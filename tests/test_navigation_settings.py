"""Configurable navigation timeout and retry count, and navigation timing logs (offline)."""

from __future__ import annotations

import logging
import random
from pathlib import Path

import pytest

import main as cli
from scraper.browser import BrowserSession, BrowserSettings, ListingFetcher, TransientFetchError
from scraper.database import COMPLETED, FAILED, Database
from scraper.runner import RunSummary, plan_run, process_page, run_pages
from tests.fake_playwright import FakePage, FakePlaywright, ScriptedNav, connection_error, factory, success, timeout
from tests.html_fixtures import FakeSite

BASE = "https://honestpool.ru/puzzle?id=71"
PAGE2 = f"{BASE}&page=2"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class TimedPage(FakePage):
    """FakePage whose navigations take ``seconds`` on the fetcher's clock and record the timeout used."""

    def __init__(self, script: list[ScriptedNav], clock: Clock, seconds: float) -> None:
        super().__init__(script)
        self.clock, self.seconds = clock, seconds
        self.timeouts: list[int] = []

    def goto(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 30_000):
        self.timeouts.append(timeout)
        self.clock.now += self.seconds
        return super().goto(url, wait_until, timeout)


def make_fetcher(script: list[ScriptedNav], seconds: float = 1.0, **overrides: object):
    clock = Clock()
    page = TimedPage(script, clock, seconds)
    options = dict(page_delay_s=0, page_delay_jitter_s=0, backoff_base_s=1, backoff_max_s=4)
    options.update(overrides)
    fetch = ListingFetcher(page, BrowserSettings(**options), base_url=BASE,  # type: ignore[arg-type]
                           notify=lambda _: None, sleep=clock.sleep, clock=clock, rng=random.Random(0))
    return fetch, page, clock


# --- CLI -----------------------------------------------------------------------

def test_defaults_are_unchanged() -> None:
    settings = cli.live_settings(cli.build_parser().parse_args(["--start-page", "2", "--end-page", "2", "--live"]))
    assert settings == BrowserSettings()
    assert (settings.navigation_timeout_ms, settings.max_retries) == (30_000, 2)


def test_nav_timeout_and_retries_reach_the_browser_settings() -> None:
    args = cli.build_parser().parse_args(
        ["--start-page", "2", "--end-page", "2", "--live", "--nav-timeout", "60", "--retries", "0"])
    settings = cli.live_settings(args)
    assert (settings.navigation_timeout_ms, settings.max_retries) == (60_000, 0)
    assert (settings.page_delay_s, settings.page_delay_jitter_s, settings.captcha_wait_s, settings.headless) == (
        15.0, 5.0, 600.0, False)


@pytest.mark.parametrize("args", [
    ["--nav-timeout", "4"], ["--nav-timeout", "301"], ["--nav-timeout", "-1"],
    ["--retries", "-1"], ["--retries", "6"],
])
def test_invalid_navigation_options_are_rejected(db_path: Path, args: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--db", str(db_path), "--start-page", "2", "--end-page", "2", "--live", *args])
    assert exc.value.code == 2
    assert not db_path.exists()


def test_cli_passes_settings_to_the_live_run(db_path: Path, monkeypatch: pytest.MonkeyPatch,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    captured: dict[str, object] = {}

    def fake_run_live(db, start, end, settings, *, max_pages, max_attempts, **_):
        captured.update(start=start, end=end, settings=settings, max_pages=max_pages)
        return RunSummary(start, end, planned=1)

    monkeypatch.setattr(cli, "run_live", fake_run_live)
    assert cli.main(["--db", str(db_path), "--start-page", "2", "--end-page", "2", "--live", "--max-pages", "1",
                     "--nav-timeout", "60", "--retries", "0"]) == 0
    settings = captured["settings"]
    assert (captured["start"], captured["end"], captured["max_pages"]) == (2, 2, 1)
    assert (settings.navigation_timeout_ms, settings.max_retries) == (60_000, 0)  # type: ignore[attr-defined]


# --- fetcher -------------------------------------------------------------------

def test_navigation_timeout_is_applied_to_context_and_goto() -> None:
    fake = FakePlaywright()
    with BrowserSession(BrowserSettings(navigation_timeout_ms=60_000), playwright_factory=factory(fake)):
        assert fake.browser.context.timeout == 60_000
    fetch, page, _ = make_fetcher([success(2, [150])], navigation_timeout_ms=60_000)
    fetch(2)
    assert page.timeouts == [60_000]


@pytest.mark.parametrize("failure", [timeout(), connection_error("net::ERR_ABORTED at https://honestpool.ru/")])
def test_zero_retries_means_exactly_one_navigation(failure: ScriptedNav) -> None:
    fetch, page, clock = make_fetcher([failure, success(2)], seconds=60, max_retries=0)
    with pytest.raises(TransientFetchError, match=r"after 60\.0s.*gave up after 0 retries"):
        fetch(2)
    assert page.calls == [PAGE2]
    assert clock.sleeps == []


def test_default_retries_still_retry_with_backoff() -> None:
    fetch, page, clock = make_fetcher([timeout(), timeout(), success(2, [150])])
    assert "tag_search_101" in fetch(2)
    assert page.calls == [PAGE2] * 3
    assert len(clock.sleeps) == 2


def test_successful_navigation_logs_status_time_and_safe_url(caplog: pytest.LogCaptureFixture) -> None:
    script = [ScriptedNav(url=f"{PAGE2}&csrf=deadbeef", html=success(2).html)]
    fetch, _, _ = make_fetcher(script, seconds=4.25)
    with caplog.at_level(logging.INFO, logger="scraper.browser"):
        fetch(2)
    assert f"page 2: HTTP 200 in 4.2s at {PAGE2}" in caplog.text
    assert "deadbeef" not in caplog.text


# --- run level -----------------------------------------------------------------

def test_single_attempt_timeout_records_failure_and_keeps_data(db: Database) -> None:
    plan_run(db, 1, 3)
    process_page(db, 1, FakeSite(verified={1: list(range(1, 10))}))
    process_page(db, 3, FakeSite())
    fetch, page, _ = make_fetcher([timeout()], seconds=60, max_retries=0, navigation_timeout_ms=60_000)
    plan_run(db, 2, 2)
    summary = run_pages(db, 2, 2, fetch, max_pages=1)
    assert (summary.completed, summary.failed, summary.stopped) == (0, 1, None)
    assert page.calls == [PAGE2]
    assert (db.page(2)["status"], db.page(2)["attempts"]) == (FAILED, 1)
    assert "navigation timeout after 60.0s" in db.page(2)["last_error"]
    assert [db.page(p)["status"] for p in (1, 3)] == [COMPLETED, COMPLETED]
    assert db.record_count() == 9
    assert db.pages_to_process(1, 3) == [2]
