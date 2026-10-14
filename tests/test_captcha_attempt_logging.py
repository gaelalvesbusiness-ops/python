"""Per-attempt CAPTCHA logging, rejection confirmation and the end-of-run CAPTCHA summary (offline)."""

from __future__ import annotations

import logging
import random
import re
import socket
import time
from dataclasses import replace
from pathlib import Path

import pytest

import main as cli
from scraper import browser, two_captcha
from scraper.browser import BrowserSession, BrowserSettings, CaptchaRequiredError, CaptchaSummary, ListingFetcher
from scraper.database import COMPLETED, PENDING, Database
from scraper.runner import plan_run, run_live
from scraper.two_captcha import SolverAccountError, SolverError, TwoCaptchaClient
from tests.fake_playwright import (
    CAPTCHA_URL,
    FakePage,
    FakePlaywright,
    ScriptedNav,
    captcha,
    captcha_form,
    challenge_png,
    factory,
    success,
)
from tests.fake_two_captcha import FAKE_KEY, FakeTwoCaptchaAPI
from tests.html_fixtures import captcha_form_page
from tests.test_captcha_auto import BASE, Clock, FakeSolver, fetcher, listing, person_solves_after_manual_prompt

ROOT = Path(__file__).resolve().parents[1]
SAME_IMAGE_PAGE = ScriptedNav(url=CAPTCHA_URL, html=captcha_form_page(1).replace("</form>", "<p>...</p></form>"))


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def browser_log(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name in ("scraper.browser", "scraper.two_captcha")]


def real_client(api: FakeTwoCaptchaAPI, clock: Clock) -> TwoCaptchaClient:
    return TwoCaptchaClient(FAKE_KEY, timeout_s=120, transport=api, sleep=clock.sleep, clock=clock)


# --- each step of an attempt is logged ----------------------------------------------------------------------

def test_accepted_attempt_logs_every_step_in_order(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    page = FakePage([captcha_form(1), success(1)], submissions=[listing(1)])
    clock, api = Clock(), FakeTwoCaptchaAPI(["k3y9z"])
    fetcher(page, real_client(api, clock), clock=clock)(1)
    steps = ["CAPTCHA 1 shown for page 1",
             "CAPTCHA 1 attempt 1/3: image captured (264 bytes, hash ",
             "2Captcha task 7001 submitted; waiting up to 120s for the answer",
             "2Captcha task 7001: answer received",
             "CAPTCHA 1 attempt 1/3: answer received from 2Captcha (task 7001, 5 characters, after ",
             "CAPTCHA 1 attempt 1/3: answer submitted to the website",
             "CAPTCHA 1 attempt 1/3: website acceptance confirmed at https://honestpool.ru/puzzle?id=71; "
             "reported to 2Captcha as correct"]
    log = browser_log(caplog)
    positions = [next(i for i, line in enumerate(log) if line.startswith(step)) for step in steps]
    assert positions == sorted(positions)
    assert api.methods == ["createTask", "getTaskResult", "reportCorrect"]
    assert "k3y9z" not in caplog.text and FAKE_KEY not in caplog.text


def test_image_not_found_is_logged_and_nothing_is_sent(caplog: pytest.LogCaptureFixture) -> None:
    solver = FakeSolver(["x"])
    fetch = fetcher(FakePage([captcha()]), solver, headless=True)
    with pytest.raises(CaptchaRequiredError, match="the CAPTCHA image was not found"):
        fetch(1)
    assert "CAPTCHA 1 attempt 1/3: image not found; nothing sent to 2Captcha" in caplog.text
    assert solver.images == [] and (fetch.images_not_found, fetch.images_sent) == (1, 0)


def test_solver_errors_are_logged_as_no_answer_and_counted(caplog: pytest.LogCaptureFixture) -> None:
    page = FakePage([captcha_form(1), success(1)], submissions=[listing(1)])
    solver = FakeSolver([SolverError("2Captcha could not process the CAPTCHA (ERROR_CAPTCHA_UNSOLVABLE)"), "ok"])
    fetch = fetcher(page, solver, headless=True)
    fetch(1)
    assert ("CAPTCHA 1 attempt 1/3: no answer from 2Captcha: 2Captcha could not process the CAPTCHA "
            "(ERROR_CAPTCHA_UNSOLVABLE)") in caplog.text
    assert (fetch.solver_errors, fetch.images_sent, fetch.auto_verifications) == (1, 2, 1)


def test_account_problem_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    fetch = fetcher(FakePage([captcha_form(1)]), FakeSolver([SolverAccountError("balance is insufficient")]),
                    headless=True)
    with pytest.raises(CaptchaRequiredError):
        fetch(1)
    assert ("CAPTCHA 1 attempt 1/3: 2Captcha account problem, automatic solving is off for this run: "
            "balance is insufficient") in caplog.text
    assert "CAPTCHA 1: automatic solving failed" in caplog.text and "no manual fallback in headless mode" in caplog.text


def test_image_refused_by_the_local_checks_is_not_counted_as_sent(caplog: pytest.LogCaptureFixture) -> None:
    page = FakePage([captcha_form(1)])
    page.captcha_image = b"\x89PNG\r\n\x1a\n"  # below 2Captcha's minimum size
    clock, api = Clock(), FakeTwoCaptchaAPI()
    fetch = fetcher(page, real_client(api, clock), clock=clock, headless=True)
    with pytest.raises(CaptchaRequiredError, match="outside 2Captcha's limits"):
        fetch(1)
    assert api.requests == [] and fetch.images_sent == 0 and fetch.solver_errors == 3
    assert "CAPTCHA 1 attempt 1/3: image not sent to 2Captcha: CAPTCHA image of 8 bytes" in caplog.text


def test_manual_fallback_is_logged_and_counted(caplog: pytest.LogCaptureFixture) -> None:
    page = FakePage([captcha_form(1), success(1)], submissions=[captcha_form(2), captcha_form(3), captcha_form(4)])
    solver, notes, clock = FakeSolver(["a", "b", "c"]), [], Clock()
    fetch = ListingFetcher(page, BrowserSettings(page_delay_s=0, page_delay_jitter_s=0, captcha_poll_s=1),
                           base_url=BASE, notify=notes.append, clock=clock, rng=random.Random(0),
                           sleep=person_solves_after_manual_prompt(page, clock, notes), solver=solver)
    fetch(1)
    assert ("CAPTCHA 1: manual fallback (3 attempt(s) failed; last: the site rejected the answer)"
            in caplog.text)
    assert fetch.captcha_summary() == CaptchaSummary(shown=1, automatic_verifications=0, images_sent=3,
                                                     image_limit=42, rejections=3, manual_fallbacks=1,
                                                     manual_verifications=1)


# --- a rejection needs a new CAPTCHA image; anything else is uncertain and never reported --------------------

def test_rejection_is_confirmed_by_a_new_image_and_that_image_is_answered_next(
        caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    page = FakePage([captcha_form(1), success(1)], submissions=[captcha_form(2), listing(1)])
    solver = FakeSolver(["wrong", "right"])
    fetch = fetcher(page, solver)
    fetch(1)
    assert solver.reports == [(1, False), (2, True)]
    assert solver.images == [challenge_png(1), challenge_png(2)] and len(page.screenshots) == 2
    hashes = re.findall(r"image captured \(\d+ bytes, hash ([0-9a-f]{12})\)", caplog.text)
    assert len(hashes) == 2 and hashes[0] != hashes[1]
    assert (f"CAPTCHA 1 attempt 1/3: website rejection confirmed (a new CAPTCHA image was shown, hash {hashes[1]}); "
            f"reported to 2Captcha as incorrect") in caplog.text
    assert (fetch.rejections, fetch.uncertain_outcomes, fetch.auto_verifications) == (1, 0, 1)


def test_same_image_after_submission_is_uncertain_and_not_reported(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    page = FakePage([captcha_form(1)], submissions=[SAME_IMAGE_PAGE])
    solver = FakeSolver(["x"])
    fetch = fetcher(page, solver, headless=True)
    with pytest.raises(CaptchaRequiredError, match=r"not confirmed within 30s \(last seen: a CAPTCHA with the same "
                                                   r"image\)"):
        fetch(1)
    assert solver.reports == [] and len(solver.images) == 1
    assert (fetch.rejections, fetch.uncertain_outcomes) == (0, 1)
    assert "CAPTCHA 1 attempt 1/3: not confirmed yet: a CAPTCHA with the same image" in caplog.text
    assert "CAPTCHA 1 attempt 1/3: verification outcome uncertain (" in caplog.text
    assert "not reported to 2Captcha" in caplog.text and "website rejection confirmed" not in caplog.text


def test_captcha_whose_image_cannot_be_compared_is_uncertain_and_not_reported() -> None:
    page = FakePage([captcha_form(1)], submissions=[captcha()])  # CAPTCHA page without the image
    solver = FakeSolver(["x"])
    fetch = fetcher(page, solver, headless=True)
    with pytest.raises(CaptchaRequiredError, match="last seen: a CAPTCHA whose image could not be compared"):
        fetch(1)
    assert solver.reports == [] and (fetch.rejections, fetch.uncertain_outcomes) == (0, 1)


def test_page_change_before_the_new_challenge_is_not_a_rejection() -> None:
    """A changed page with the old image (e.g. a disabled button) is skipped; the new image confirms."""
    page = FakePage([captcha_form(1), success(1)], submissions=[SAME_IMAGE_PAGE, listing(1)])
    clock, solver = Clock(), FakeSolver(["wrong", "right"])

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        if len(page.clicks) == 1 and page.html == SAME_IMAGE_PAGE.html and clock.now >= 5:
            page.url, page.html = CAPTCHA_URL, captcha_form_page(2)

    fetch = ListingFetcher(page, BrowserSettings(page_delay_s=0, page_delay_jitter_s=0, captcha_poll_s=1,
                                                 headless=True), base_url=BASE, notify=lambda _: None,
                           sleep=sleep, clock=clock, rng=random.Random(0), solver=solver)
    fetch(1)
    assert solver.reports == [(1, False), (2, True)] and solver.images == [challenge_png(1), challenge_png(2)]
    assert (fetch.rejections, fetch.uncertain_outcomes) == (1, 0)


@pytest.mark.parametrize("submission", [None, ScriptedNav(url="https://honestpool.ru/", html="<html></html>"),
                                        SAME_IMAGE_PAGE, captcha()],
                         ids=["no change", "wrong destination", "same image", "image not comparable"])
def test_uncertain_outcomes_are_never_reported_as_incorrect(submission: ScriptedNav | None,
                                                            caplog: pytest.LogCaptureFixture) -> None:
    page = FakePage([captcha_form(1)], submissions=[submission] if submission else [])
    solver = FakeSolver(["x"])
    fetch = fetcher(page, solver, headless=True)
    with pytest.raises(CaptchaRequiredError, match="not confirmed within"):
        fetch(1)
    assert solver.reports == [] and fetch.uncertain_outcomes == 1 and fetch.rejections == 0
    assert "reported to 2Captcha as incorrect" not in caplog.text


# --- limits are unchanged; the image ceiling is derived from them ----------------------------------------------

def test_limits_and_the_derived_image_ceiling() -> None:
    s = BrowserSettings()
    assert (s.captcha_auto_attempts, s.max_auto_verifications, s.max_manual_verifications) == (3, 3, 10)
    assert s.max_captcha_images == 42 == 3 * (3 + 10 + 1)
    assert replace(s, max_manual_verifications=1).max_captcha_images == 15


def test_no_image_is_sent_once_the_ceiling_is_reached(caplog: pytest.LogCaptureFixture) -> None:
    solver = FakeSolver(["x"])
    fetch = fetcher(FakePage([captcha_form(1)]), solver, headless=True)
    fetch.images_sent = fetch.settings.max_captcha_images
    with pytest.raises(CaptchaRequiredError, match=r"the limit of 42 CAPTCHA images per run is reached"):
        fetch(1)
    assert solver.images == [] and "attempt 1/3: the limit of 42 images per run is reached" in caplog.text


# --- end-of-run summary --------------------------------------------------------------------------------------

FAST = dict(page_delay_s=0, page_delay_jitter_s=0, backoff_base_s=0.01, backoff_max_s=0.02, captcha_poll_s=0.001)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakePlaywright:
    fake = FakePlaywright()
    monkeypatch.setattr(browser, "BrowserSession", lambda settings: BrowserSession(settings, factory(fake)))
    return fake


def test_run_summary_counts_the_run_captchas(fake: FakePlaywright, db: Database) -> None:
    page = fake.browser.context.page
    page.script, page.submissions = [captcha_form(1), success(2, [150])], [captcha_form(2), listing(1)]
    plan_run(db, 2, 2)
    summary = run_live(db, 2, 2, BrowserSettings(headless=True, **FAST), max_pages=1, notify=lambda _: None,
                       solver=FakeSolver(["wrong", "right"]))
    assert summary.completed == 1 and db.page(2)["status"] == COMPLETED
    assert summary.captcha == CaptchaSummary(shown=1, automatic_verifications=1, images_sent=2, image_limit=42,
                                             rejections=1)


def test_interrupted_run_logs_the_captcha_summary(fake: FakePlaywright, db: Database,
                                                  caplog: pytest.LogCaptureFixture) -> None:
    fake.browser.context.page.script = [captcha_form(1)]
    plan_run(db, 2, 2)
    with pytest.raises(KeyboardInterrupt):
        run_live(db, 2, 2, BrowserSettings(headless=True, **FAST), max_pages=1, notify=lambda _: None,
                 solver=FakeSolver([KeyboardInterrupt()]))
    assert "run ended early; CAPTCHA summary: 1 shown; 0 automatic verification(s) confirmed; 1 image(s) sent" \
        in caplog.text
    assert (db.page(2)["status"], db.page(2)["attempts"]) == (PENDING, 0)


def test_cli_prints_the_captcha_summary_without_the_answer(
        monkeypatch: pytest.MonkeyPatch, fake: FakePlaywright, db_path: Path,
        capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture) -> None:
    api = FakeTwoCaptchaAPI(["bad111", "good22"])
    monkeypatch.setattr(two_captcha, "urllib_transport", api)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    real_run_live = cli.run_live
    monkeypatch.setattr(cli, "run_live", lambda db, start, end, s, **kw: real_run_live(
        db, start, end, replace(s, page_delay_s=0, page_delay_jitter_s=0, captcha_poll_s=0.001), **kw))
    monkeypatch.setenv("CAPTCHA_PROVIDER", "2captcha")
    monkeypatch.setenv("CAPTCHA_API_KEY", FAKE_KEY)
    page = fake.browser.context.page
    page.script, page.submissions = [captcha_form(1), success(2, [150])], [captcha_form(2), listing(1)]
    assert cli.main(["--db", str(db_path), "--start-page", "2", "--end-page", "2", "--live", "--headless"]) == 0
    out, err = capsys.readouterr()
    assert ("CAPTCHA summary: 1 shown; 1 automatic verification(s) confirmed; 2 image(s) sent to 2Captcha "
            "(limit 42 per run); 1 answer(s) rejected by the website; 0 uncertain outcome(s) (not reported to "
            "2Captcha); 0 2Captcha error(s); 0 image(s) not found; 0 manual fallback(s); 0 manual "
            "verification(s)") in out
    assert api.methods == ["createTask", "getTaskResult", "reportIncorrect",
                           "createTask", "getTaskResult", "reportCorrect"]
    text = out + err + caplog.text
    assert all(secret not in text for secret in ("bad111", "good22", FAKE_KEY))


# --- documentation ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("doc", ["README.md", "docs/CLIENT_GUIDE.md", "docs/CLIENT_ACCEPTANCE_TEST.md"])
def test_docs_state_the_real_image_ceiling(doc: str) -> None:
    text = (ROOT / doc).read_text(encoding="utf-8")
    assert not re.search(r"at most\s+9 images|9 imagens|27 imagens", text)
    assert "42" in text
