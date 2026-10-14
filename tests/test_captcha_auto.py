"""Automatic CAPTCHA handling in the fetcher, the run loop and the CLI, with mocked 2Captcha (offline)."""

from __future__ import annotations

import logging
import random
import socket
import time
from dataclasses import replace
from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError

import main as cli
from scraper import browser, two_captcha
from scraper.browser import (
    CAPTCHA_IMAGE_SELECTOR,
    CAPTCHA_INPUT_SELECTOR,
    CAPTCHA_SUBMIT_SELECTOR,
    BrowserSession,
    BrowserSettings,
    CaptchaRequiredError,
    ListingFetcher,
)
from scraper.database import COMPLETED, PENDING, Database
from scraper.runner import plan_run, run_live
from scraper.two_captcha import CaptchaSolution, SolverAccountError, SolverError, SolverTimeoutError, TwoCaptchaClient
from tests.fake_playwright import (
    CAPTCHA_PNG,
    FakePage,
    FakePlaywright,
    ScriptedNav,
    captcha,
    captcha_form,
    factory,
    success,
)
from tests.fake_two_captcha import FAKE_KEY, FakeTwoCaptchaAPI, error
from tests.html_fixtures import captcha_form_page, listing_html, listing_page, listing_row

BASE = "https://honestpool.ru/puzzle?id=71"
PAGE2 = f"{BASE}&page=2"
ZERO_BALANCE = "2Captcha refused the request: the account balance is insufficient (ERROR_ZERO_BALANCE)"


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


class FakeSolver:
    """Returns scripted answers or raises scripted errors; records images and reports."""

    def __init__(self, outcomes: list[str | BaseException]) -> None:
        self.outcomes = list(outcomes)
        self.images: list[bytes] = []
        self.reports: list[tuple[int | str, bool]] = []

    def solve_image(self, image: bytes) -> CaptchaSolution:
        self.images.append(image)
        item = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(item, BaseException):
            raise item
        return CaptchaSolution(len(self.images), item)

    def report(self, solution: CaptchaSolution, *, correct: bool) -> None:
        self.reports.append((solution.task_id, correct))


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def settings(**overrides: object) -> BrowserSettings:
    defaults = dict(page_delay_s=0, page_delay_jitter_s=0, backoff_base_s=1, backoff_max_s=4,
                    captcha_wait_s=30, captcha_poll_s=1, max_retries=2)
    defaults.update(overrides)
    return BrowserSettings(**defaults)  # type: ignore[arg-type]


def fetcher(page: FakePage, solver: object, *, clock: Clock | None = None, notes: list[str] | None = None,
            **overrides: object) -> ListingFetcher:
    clock = clock or Clock()
    return ListingFetcher(page, settings(**overrides), base_url=BASE,
                          notify=(notes if notes is not None else []).append, sleep=clock.sleep, clock=clock,
                          rng=random.Random(0), solver=solver)  # type: ignore[arg-type]


def listing(page: int = 1, verified: list[int] | None = None) -> ScriptedNav:
    """The page the site shows after an accepted answer (the CAPTCHA redirect drops &page=N)."""
    return ScriptedNav(url=BASE if page == 1 else f"{BASE}&page={page}", html=listing_page(page, verified or []))


def person_solves_after_manual_prompt(page: FakePage, clock: Clock, notes: list[str]):
    """Sleep hook: once the manual prompt was shown, a person completes the CAPTCHA."""
    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        if any("Please complete it manually" in n for n in notes) and "captcha" in page.url:
            page.url, page.html = BASE, listing_page(1)
    return sleep


# --- automatic verification -------------------------------------------------------------------

@pytest.mark.parametrize("headless", [False, True])
def test_captcha_is_solved_automatically_and_scraping_resumes(headless: bool) -> None:
    page = FakePage([captcha_form(1), success(1, [1])], submissions=[listing(1, [1])])
    solver, notes = FakeSolver(["x7k2p"]), []
    fetch = fetcher(page, solver, notes=notes, headless=headless)
    html = fetch(1)
    assert "tag_search_1" in html and page.calls == [BASE, BASE]
    assert page.screenshots == [CAPTCHA_IMAGE_SELECTOR] and solver.images == [CAPTCHA_PNG]
    assert page.fills == [(CAPTCHA_INPUT_SELECTOR, "x7k2p")] and page.clicks == [CAPTCHA_SUBMIT_SELECTOR]
    assert solver.reports == [(1, True)]
    assert (fetch.auto_verifications, fetch.manual_verifications, fetch.captcha_submissions) == (1, 0, 1)
    assert any("Verification succeeded automatically: the browser reached the listing at "
               "https://honestpool.ru/puzzle?id=71" in n for n in notes)
    assert not any("manually" in n for n in notes)


def test_image_search_starts_at_the_captcha_input() -> None:
    assert CAPTCHA_IMAGE_SELECTOR == "img"
    assert "document.querySelector('input[name=\"captcha\"]')" in browser.FIND_CAPTCHA_IMAGE_JS
    assert "img.closest('button, a, [role=\"button\"]')" in browser.FIND_CAPTCHA_IMAGE_JS
    assert (CAPTCHA_INPUT_SELECTOR, CAPTCHA_SUBMIT_SELECTOR) == ('input[name="captcha"]', '[name="btn_check_captcha"]')


def test_rejected_answer_is_reported_and_retried_with_the_new_image() -> None:
    page = FakePage([captcha_form(1), success(1)], submissions=[captcha_form(2), listing(1)])
    solver, notes = FakeSolver(["wrong", "right"]), []
    fetcher(page, solver, notes=notes)(1)
    assert [value for _, value in page.fills] == ["wrong", "right"]
    assert solver.reports == [(1, False), (2, True)] and len(page.screenshots) == 2
    assert any("The site rejected the automatic answer (attempt 1 of 3)" in n for n in notes)


def test_all_attempts_rejected_fall_back_to_manual_verification() -> None:
    page = FakePage([captcha_form(1), success(1)], submissions=[captcha_form(2), captcha_form(3), captcha_form(4)])
    solver, notes, clock = FakeSolver(["a", "b", "c"]), [], Clock()
    fetch = ListingFetcher(page, settings(), base_url=BASE, notify=notes.append, clock=clock, rng=random.Random(0),
                           sleep=person_solves_after_manual_prompt(page, clock, notes), solver=solver)
    assert "tag_search_1" in fetch(1)
    assert solver.reports == [(1, False), (2, False), (3, False)] and fetch.captcha_submissions == 3
    assert fetch.manual_verifications == 1 and fetch.auto_verifications == 0
    assert ("Automatic CAPTCHA solving failed (3 attempt(s) failed; last: the site rejected the answer); "
            "falling back to manual verification.") in notes
    assert fetch.auto_wait_s > 0 and fetch.manual_wait_s > 0
    assert fetch.captcha_time_s == fetch.auto_wait_s + fetch.manual_wait_s


def test_all_attempts_rejected_stop_the_run_in_headless_mode() -> None:
    page = FakePage([captcha_form(1)], submissions=[captcha_form(2), captcha_form(3), captcha_form(4)])
    with pytest.raises(CaptchaRequiredError, match=r"automatic CAPTCHA solving failed \(3 attempt\(s\) failed; last: "
                                                   r"the site rejected the answer\); manual verification is "
                                                   r"impossible in headless mode"):
        fetcher(page, FakeSolver(["a", "b", "c"]), headless=True)(1)
    assert page.calls == [BASE]


def test_attempts_per_captcha_are_configurable() -> None:
    page = FakePage([captcha_form(1)], submissions=[captcha_form(2)])
    solver = FakeSolver(["a"])
    with pytest.raises(CaptchaRequiredError, match=r"1 attempt\(s\) failed"):
        fetcher(page, solver, headless=True, captcha_auto_attempts=1)(1)
    assert len(solver.images) == 1


# --- solver errors ----------------------------------------------------------------------------

def test_solver_errors_are_retried_without_submitting_anything() -> None:
    solver = FakeSolver([SolverTimeoutError("no answer from 2Captcha within 120s (task 1)"),
                         SolverError("2Captcha could not process the CAPTCHA (ERROR_CAPTCHA_UNSOLVABLE)")])
    page = FakePage([captcha_form(1)])
    with pytest.raises(CaptchaRequiredError, match=r"3 attempt\(s\) failed; last: 2Captcha could not process "
                                                   r"the CAPTCHA \(ERROR_CAPTCHA_UNSOLVABLE\)"):
        fetcher(page, solver, headless=True)(1)
    assert len(solver.images) == 3 and page.fills == [] and page.clicks == [] and solver.reports == []


def test_solver_error_then_success() -> None:
    page = FakePage([captcha_form(1), success(1)], submissions=[listing(1)])
    solver = FakeSolver([SolverError("2Captcha API returned HTTP 502 for createTask"), "good"])
    fetch = fetcher(page, solver, headless=True)
    fetch(1)
    assert page.fills == [(CAPTCHA_INPUT_SELECTOR, "good")] and fetch.auto_verifications == 1


def test_account_error_turns_automatic_solving_off_for_the_session() -> None:
    page = FakePage([captcha_form(1), success(1), captcha_form(1)])
    solver, notes, clock = FakeSolver([SolverAccountError(ZERO_BALANCE)]), [], Clock()
    fetch = ListingFetcher(page, settings(max_manual_verifications=1), base_url=BASE, notify=notes.append,
                           clock=clock, rng=random.Random(0),
                           sleep=person_solves_after_manual_prompt(page, clock, notes), solver=solver)
    fetch(1)
    assert len(solver.images) == 1 and fetch.manual_verifications == 1
    assert f"Automatic CAPTCHA solving failed (automatic solving is off for this run: {ZERO_BALANCE}); " \
           f"falling back to manual verification." in notes
    with pytest.raises(CaptchaRequiredError, match=r"the limit of 1 manual verification\(s\)"):
        fetch(2)
    assert len(solver.images) == 1  # never asked again after the account problem


def test_insufficient_balance_stops_a_headless_run() -> None:
    with pytest.raises(CaptchaRequiredError, match=r"balance is insufficient \(ERROR_ZERO_BALANCE\)\); manual "
                                                   r"verification is impossible in headless mode"):
        fetcher(FakePage([captcha_form(1)]), FakeSolver([SolverAccountError(ZERO_BALANCE)]), headless=True)(1)


# --- page problems ------------------------------------------------------------------------------

def test_missing_captcha_image_falls_back_without_calling_the_solver() -> None:
    solver = FakeSolver(["x"])
    with pytest.raises(CaptchaRequiredError, match="the CAPTCHA image was not found on the page"):
        fetcher(FakePage([captcha()]), solver, headless=True)(1)
    assert solver.images == []


def test_image_capture_errors_fall_back(caplog: pytest.LogCaptureFixture) -> None:
    page = FakePage([captcha_form(1)])
    page.screenshot_error = PlaywrightError("Element is not visible")
    solver = FakeSolver(["x"])
    with pytest.raises(CaptchaRequiredError, match="the CAPTCHA image was not found"):
        fetcher(page, solver, headless=True)(1)
    assert solver.images == [] and "the CAPTCHA image could not be captured: Element is not visible" in caplog.text


def test_image_is_captured_only_once_it_has_loaded() -> None:
    page = FakePage([captcha_form(1), success(1)], submissions=[listing(1)])
    page.image_loaded = [False, False, True]
    clock = Clock()
    fetcher(page, FakeSolver(["x"]), clock=clock)(1)
    assert page.screenshots == [CAPTCHA_IMAGE_SELECTOR] and clock.sleeps[:2] == [1.0, 1.0]


def test_image_that_never_loads_is_not_sent(caplog: pytest.LogCaptureFixture) -> None:
    page = FakePage([captcha_form(1)])
    page.image_loaded = [False]
    solver = FakeSolver(["x"])
    with pytest.raises(CaptchaRequiredError, match="the CAPTCHA image was not found on the page or could not be "
                                                   "captured"):
        fetcher(page, solver, headless=True)(1)
    assert solver.images == [] and page.screenshots == []
    assert "the CAPTCHA image did not finish loading within 15s; page: fake page with a CAPTCHA image" in caplog.text


def test_image_is_awaited_when_the_captcha_is_detected_before_the_form_loads() -> None:
    page = FakePage([captcha(), success(1)], submissions=[listing(1)])  # the URL commits before the form
    clock, solver = Clock(), FakeSolver(["x"])

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        if len(clock.sleeps) == 2:
            page.html = captcha_form_page(1)

    fetch = ListingFetcher(page, settings(headless=True), base_url=BASE, notify=lambda _: None, sleep=sleep,
                           clock=clock, rng=random.Random(0), solver=solver)
    assert "tag_search_1" in fetch(1)
    assert solver.images == [CAPTCHA_PNG] and fetch.auto_verifications == 1


def test_redirect_to_a_listing_that_is_still_loading_counts_as_accepted() -> None:
    still_loading = ScriptedNav(url=BASE, html=listing_html([listing_row(1)]))  # 1 of 100 rows so far
    page = FakePage([captcha_form(1), success(4, [301])], submissions=[still_loading])
    solver, notes = FakeSolver(["x"]), []
    html = fetcher(page, solver, notes=notes, headless=True)(4)
    assert "tag_search_301" in html and page.calls == [f"{BASE}&page=4"] * 2
    assert solver.reports == [(1, True)] and any("Verification succeeded automatically" in n for n in notes)


def test_interruption_while_solving_returns_the_page_to_pending(fake: FakePlaywright, db: Database) -> None:
    fake.browser.context.page.script = [captcha_form(1)]
    plan_run(db, 2, 2)
    with pytest.raises(KeyboardInterrupt):
        run_live(db, 2, 2, BrowserSettings(headless=True, **FAST), max_pages=1, notify=lambda _: None,
                 solver=FakeSolver([KeyboardInterrupt()]))
    assert (db.page(2)["status"], db.page(2)["attempts"]) == (PENDING, 0) and db.record_count() == 0


def test_answer_that_cannot_be_entered_is_not_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage([captcha_form(1)])

    def broken_fill(*args, **kwargs):
        raise PlaywrightError("element is detached")

    monkeypatch.setattr(page, "fill", broken_fill)
    solver = FakeSolver(["x"])
    with pytest.raises(CaptchaRequiredError, match="the answer could not be entered: element is detached"):
        fetcher(page, solver, headless=True)(1)
    assert solver.reports == []


def test_unconfirmed_submission_is_not_reported() -> None:
    page = FakePage([captcha_form(1)])  # the click changes nothing
    solver = FakeSolver(["x"])
    with pytest.raises(CaptchaRequiredError, match="the result of the submitted answer was not confirmed within 30s"):
        fetcher(page, solver, headless=True)(1)
    assert solver.reports == [] and page.clicks == [CAPTCHA_SUBMIT_SELECTOR]


def test_wrong_destination_after_submission_is_not_accepted() -> None:
    page = FakePage([captcha_form(1)], submissions=[ScriptedNav(url="https://honestpool.ru/", html="<html></html>")])
    solver = FakeSolver(["x"])
    with pytest.raises(CaptchaRequiredError, match="not confirmed"):
        fetcher(page, solver, headless=True)(1)
    assert solver.reports == []


# --- session handling is preserved -----------------------------------------------------------------

def test_captcha_again_right_after_automatic_verification_stops_the_run() -> None:
    page = FakePage([captcha_form(1), captcha_form(2)], submissions=[listing(1)])
    solver = FakeSolver(["x"])
    with pytest.raises(CaptchaRequiredError, match="CAPTCHA shown again right after verification"):
        fetcher(page, solver)(1)
    assert len(solver.images) == 1


def test_automatic_verifications_per_session_are_limited() -> None:
    page = FakePage([captcha_form(1), success(1), captcha_form(2)], submissions=[listing(1)])
    solver = FakeSolver(["x"])
    fetch = fetcher(page, solver, headless=True, max_auto_verifications=1)
    fetch(1)
    with pytest.raises(CaptchaRequiredError, match="the limit of 1 automatic verifications for this session"):
        fetch(2)
    assert len(solver.images) == 1


def test_answer_submission_is_paced_like_a_navigation() -> None:
    clock = Clock()
    page = FakePage([captcha_form(1), success(1)], submissions=[listing(1)])
    events: list[tuple[str, float]] = []
    original_click, original_goto = page.click, page.goto
    page.click = lambda *a, **k: (events.append(("submit", clock.now)), original_click(*a, **k))[1]
    page.goto = lambda url, **k: (events.append(("goto", clock.now)), original_goto(url, **k))[1]
    fetcher(page, FakeSolver(["x"]), clock=clock, page_delay_s=10)(1)
    assert [kind for kind, _ in events] == ["goto", "submit", "goto"]
    times = [at for _, at in events]
    assert times[1] - times[0] >= 10 and times[2] - times[1] >= 10


def test_answer_and_key_never_reach_logs_or_messages(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    page = FakePage([captcha_form(1), success(1)], submissions=[captcha_form(2), listing(1)])
    clock, notes = Clock(), []
    solver = TwoCaptchaClient(FAKE_KEY, timeout_s=120, transport=FakeTwoCaptchaAPI(["wrong-secret", "right-secret"]),
                              sleep=clock.sleep, clock=clock)
    fetcher(page, solver, clock=clock, notes=notes)(1)
    text = caplog.text + "\n".join(notes)
    assert all(secret not in text for secret in (FAKE_KEY, "wrong-secret", "right-secret"))
    assert [value for _, value in page.fills] == ["wrong-secret", "right-secret"]


# --- run loop: SQLite checkpoints and resume ----------------------------------------------------------

FAST = dict(page_delay_s=0, page_delay_jitter_s=0, backoff_base_s=0.01, backoff_max_s=0.02, captcha_poll_s=0.001)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakePlaywright:
    fake = FakePlaywright()
    monkeypatch.setattr(browser, "BrowserSession", lambda settings: BrowserSession(settings, factory(fake)))
    return fake


def test_run_live_solves_automatically_and_saves_the_page(fake: FakePlaywright, db: Database) -> None:
    page = fake.browser.context.page
    page.script, page.submissions = [captcha_form(1), success(2, [150])], [listing(1)]
    solver = FakeSolver(["ok"])
    plan_run(db, 2, 2)
    summary = run_live(db, 2, 2, BrowserSettings(headless=True, **FAST), max_pages=1, notify=lambda _: None,
                       solver=solver)
    assert summary.completed == 1 and summary.stopped is None
    assert db.page(2)["status"] == COMPLETED and db.record_count() == 1
    assert summary.requests == 3  # two navigations and one answer submission
    assert page.calls == [PAGE2, PAGE2] and solver.reports == [(1, True)]


def test_run_live_stops_safely_when_automatic_solving_fails(fake: FakePlaywright, db: Database) -> None:
    fake.browser.context.page.script = [captcha_form(1)]
    plan_run(db, 2, 3)
    summary = run_live(db, 2, 3, BrowserSettings(headless=True, **FAST), max_pages=2, notify=lambda _: None,
                       solver=FakeSolver([SolverAccountError(ZERO_BALANCE)]))
    assert summary.stopped and "balance is insufficient" in summary.stopped
    assert [(db.page(p)["status"], db.page(p)["attempts"]) for p in (2, 3)] == [(PENDING, 0), (PENDING, 0)]
    assert db.record_count() == 0


# --- CLI end to end with the real client and a mocked API ---------------------------------------------

@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch, fake: FakePlaywright) -> FakeTwoCaptchaAPI:
    api = FakeTwoCaptchaAPI(["ok42"])
    monkeypatch.setattr(two_captcha, "urllib_transport", api)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    real_run_live = cli.run_live
    monkeypatch.setattr(cli, "run_live", lambda db, start, end, s, **kw: real_run_live(
        db, start, end, replace(s, page_delay_s=0, page_delay_jitter_s=0, captcha_poll_s=0.001), **kw))
    monkeypatch.setenv("CAPTCHA_PROVIDER", "2captcha")
    monkeypatch.setenv("CAPTCHA_API_KEY", FAKE_KEY)
    monkeypatch.setenv("CAPTCHA_TIMEOUT", "120")
    return api


LIVE = ["--start-page", "2", "--end-page", "2", "--live", "--headless"]


def test_cli_live_run_solves_the_captcha_automatically(
        cli_env: FakeTwoCaptchaAPI, fake: FakePlaywright, db_path: Path, capsys: pytest.CaptureFixture[str],
        caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    page = fake.browser.context.page
    page.script, page.submissions = [captcha_form(1), success(2, [150])], [listing(1)]
    assert cli.main(["--db", str(db_path), *LIVE]) == 0
    out, err = capsys.readouterr()
    assert "CAPTCHA provider: 2captcha | API key: set (hidden) | timeout: 120 s | automatic solving: enabled" in out
    assert "Verification succeeded automatically" in out and "Page requests sent: 3" in out
    assert cli_env.methods == ["createTask", "getTaskResult", "reportCorrect"]
    assert page.fills == [(CAPTCHA_INPUT_SELECTOR, "ok42")]
    assert FAKE_KEY not in out + err + caplog.text and "ok42" not in out + err + caplog.text
    with Database(db_path) as db:
        assert db.page(2)["status"] == COMPLETED and db.record_count() == 1


def test_cli_insufficient_balance_stops_and_keeps_the_page_pending(
        cli_env: FakeTwoCaptchaAPI, fake: FakePlaywright, db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli_env.create = [error("ERROR_ZERO_BALANCE", 10)]
    fake.browser.context.page.script = [captcha_form(1)]
    assert cli.main(["--db", str(db_path), *LIVE]) == 3
    out = capsys.readouterr().out
    assert (f"Run stopped: page 2: automatic CAPTCHA solving failed (automatic solving is off for this run: "
            f"{ZERO_BALANCE}); manual verification is impossible in headless mode") in out
    assert cli_env.methods == ["createTask"] and FAKE_KEY not in out
    with Database(db_path) as db:
        assert (db.page(2)["status"], db.page(2)["attempts"]) == (PENDING, 0)


def test_cli_without_api_key_keeps_manual_handling(
        cli_env: FakeTwoCaptchaAPI, fake: FakePlaywright, db_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("CAPTCHA_API_KEY")
    fake.browser.context.page.script = [captcha_form(1)]
    assert cli.main(["--db", str(db_path), *LIVE]) == 3
    out = capsys.readouterr().out
    assert "automatic solving: disabled" in out
    assert "Run stopped: page 2: CAPTCHA shown in headless mode; manual verification is impossible" in out
    assert cli_env.requests == [] and fake.browser.context.page.screenshots == []


def test_captcha_status_never_contacts_the_service(cli_env: FakeTwoCaptchaAPI,
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--captcha-status"]) == 0
    assert "automatic solving: enabled" in capsys.readouterr().out and cli_env.requests == []
