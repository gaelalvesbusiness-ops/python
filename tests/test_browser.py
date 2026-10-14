"""Offline tests for Playwright integration: no network, no real browser."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import pytest

from scraper.browser import (
    AccessRestrictedError,
    BrowserSession,
    BrowserSettings,
    CaptchaRequiredError,
    FetchError,
    ListingFetcher,
    RateLimitedError,
    TransientFetchError,
    ensure_browsers_path,
    is_captcha_page,
    parse_retry_after,
)
from scraper.config import DATA_DIR
from scraper.database import COMPLETED, PENDING, Database
from scraper.runner import StopRunError, plan_run, run_live, run_pages
from tests.fake_playwright import (
    FakePage,
    FakePlaywright,
    ScriptedNav,
    captcha,
    connection_error,
    factory,
    http,
    success,
    timeout,
)
from tests.html_fixtures import CAPTCHA_PAGE, listing_page

SAMPLE = DATA_DIR / "sample_page.html"
BASE = "https://honestpool.ru/puzzle?id=71"


def settings(**overrides: object) -> BrowserSettings:
    defaults = dict(page_delay_s=0, page_delay_jitter_s=0, backoff_base_s=1, backoff_max_s=4,
                    captcha_wait_s=30, captcha_poll_s=1, max_retries=2)
    defaults.update(overrides)
    return BrowserSettings(**defaults)  # type: ignore[arg-type]


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def fetcher(page: FakePage, clock: Clock | None = None, notes: list[str] | None = None,
            **overrides: object) -> ListingFetcher:
    clock = clock or Clock()
    return ListingFetcher(page, settings(**overrides), base_url=BASE,
                          notify=(notes if notes is not None else []).append,
                          sleep=clock.sleep, clock=clock, rng=random.Random(0))


# --- helpers -----------------------------------------------------------------

@pytest.mark.parametrize(
    ("url", "html", "expected"),
    [
        ("https://honestpool.ru/captcha?url=puzzle?id=71", CAPTCHA_PAGE, True),
        ("https://honestpool.ru/CAPTCHA", "<html></html>", True),
        ("https://honestpool.ru/puzzle?id=71", CAPTCHA_PAGE, True),
        ("https://honestpool.ru/puzzle?id=71",
         '<html><title>POOL</title><input name="captcha" value=""></html>', True),
        ("https://honestpool.ru/puzzle?id=71", listing_page(1, [1]), False),
        ("https://honestpool.ru/puzzle?id=71&page=2", listing_page(2), False),
    ],
)
def test_captcha_detection(url: str, html: str, expected: bool) -> None:
    assert is_captcha_page(url, html) is expected


def test_parse_retry_after_delta_and_http_date() -> None:
    now = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
    assert parse_retry_after("120") == 120.0
    assert parse_retry_after(format_datetime(now + timedelta(seconds=45)), now) == 45.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("not-a-date") is None


def test_ensure_browsers_path_falls_back_to_user_cache(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cache = home / ".cache" / "ms-playwright" / "chromium-1243"
    cache.mkdir(parents=True)
    stale = tmp_path / "stale"
    stale.mkdir()
    env = {"PLAYWRIGHT_BROWSERS_PATH": str(stale)}
    ensure_browsers_path(env, home)
    assert env["PLAYWRIGHT_BROWSERS_PATH"] == str(home / ".cache" / "ms-playwright")


# --- session -----------------------------------------------------------------

def test_browser_session_reuses_one_context_and_closes_cleanly() -> None:
    fake = FakePlaywright()
    with BrowserSession(settings(headless=False), playwright_factory=factory(fake)) as session:
        assert fake.chromium.launches == [{"headless": False, "handle_sigint": False}]
        assert fake.browser.context.kwargs["locale"] == "ru-RU"
        assert fake.browser.context.timeout == 30_000
        assert session.page is fake.browser.context.page
    assert fake.browser.closed and fake.browser.context.closed and fake.stopped


# --- successful navigation and session reuse ---------------------------------

def test_successful_navigation_returns_html() -> None:
    page = FakePage([success(1, [1, 2])])
    clock = Clock()
    html = fetcher(page, clock, page_delay_s=15, page_delay_jitter_s=0)(1)
    assert "tag_search_1" in html and page.calls == [f"{BASE}"]
    assert clock.sleeps == []  # first page has no pacing wait


def test_session_reuses_page_and_paces_between_navigations() -> None:
    page = FakePage([success(1, [1]), success(2, [101]), success(3)])
    clock = Clock()
    fetch = fetcher(page, clock, page_delay_s=10, page_delay_jitter_s=0)
    assert [fetch(n)[:20] for n in (1, 2, 3)]
    assert page.calls == [BASE, f"{BASE}&page=2", f"{BASE}&page=3"]
    assert fetch.navigations == 3
    assert clock.sleeps == [10.0, 10.0]


def test_wrong_listing_page_is_rejected() -> None:
    page = FakePage([success(5)])  # HTML for page 5, but we asked for page 1
    with pytest.raises(FetchError, match="starts at segment"):
        fetcher(page)(1)


# --- CAPTCHA -----------------------------------------------------------------

def test_manual_captcha_then_success() -> None:
    page = FakePage([captcha(), success(1, [1])])
    clock = Clock()
    notes: list[str] = []

    def poll_then_solve(seconds: float) -> None:
        clock.sleeps.append(seconds)
        clock.now += seconds
        if len(clock.sleeps) == 2:  # after the first CAPTCHA poll
            page.url, page.html = BASE, listing_page(1, [1])

    fetch = ListingFetcher(page, settings(captcha_wait_s=30, captcha_poll_s=5), base_url=BASE,
                           notify=notes.append, sleep=poll_then_solve, clock=clock, rng=random.Random(0))
    html = fetch(1)
    assert "tag_search_1" in html
    assert fetch.manual_verifications == 1
    assert any("CAPTCHA shown" in n for n in notes)
    assert any("Verification detected" in n for n in notes)


def test_captcha_beyond_the_manual_verification_limit_stops_the_run() -> None:
    page = FakePage([captcha(), success(1), captcha()])
    clock = Clock()

    def auto_solve(seconds: float) -> None:
        clock.sleeps.append(seconds)
        clock.now += seconds
        if "captcha" in page.url.lower():
            page.url, page.html = BASE, listing_page(1)

    fetch = ListingFetcher(page, settings(captcha_poll_s=1, max_manual_verifications=1), base_url=BASE,
                           notify=lambda _: None, sleep=auto_solve, clock=clock, rng=random.Random(0))
    fetch(1)
    with pytest.raises(CaptchaRequiredError, match=r"a new CAPTCHA appeared and the limit of 1 manual "
                                                   r"verification\(s\) for this browser session is reached"):
        fetch(2)
    assert BrowserSettings().max_manual_verifications == 10


def test_captcha_in_headless_mode_stops_immediately() -> None:
    page = FakePage([captcha()])
    with pytest.raises(CaptchaRequiredError, match="headless"):
        fetcher(page, headless=True)(1)
    assert page.calls == [BASE]


def test_captcha_timeout_stops_the_run() -> None:
    page = FakePage([captcha()])
    with pytest.raises(CaptchaRequiredError, match="not completed"):
        fetcher(page, captcha_wait_s=3, captcha_poll_s=1)(1)


def test_captcha_page_is_never_parsed_as_data(db: Database) -> None:
    page = FakePage([captcha()])
    plan_run(db, 1, 1)
    summary = run_pages(db, 1, 1, fetcher(page, headless=True))
    assert summary.stopped and "CAPTCHA" in summary.stopped
    assert db.page(1)["status"] == PENDING and db.record_count() == 0


# --- HTTP errors and timeouts ------------------------------------------------

@pytest.mark.parametrize("status", [401, 403])
def test_access_restricted_stops_the_run(status: int) -> None:
    page = FakePage([http(status)])
    with pytest.raises(AccessRestrictedError, match=str(status)):
        fetcher(page)(1)


def test_http_429_respects_retry_after_then_succeeds() -> None:
    page = FakePage([http(429, headers={"Retry-After": "12"}), success(1, [1])])
    clock = Clock()
    html = fetcher(page, clock, backoff_base_s=1)(1)
    assert "tag_search_1" in html
    assert 12.0 in clock.sleeps


def test_http_429_with_long_retry_after_stops() -> None:
    page = FakePage([http(429, headers={"Retry-After": "9999"})])
    with pytest.raises(RateLimitedError, match="9999"):
        fetcher(page, max_retry_after_s=600)(1)


def test_persistent_429_stops_after_retries() -> None:
    page = FakePage([http(429), http(429), http(429)])
    with pytest.raises(RateLimitedError, match="persisted"):
        fetcher(page, max_retries=2, backoff_base_s=1)(1)
    assert len(page.calls) == 3


def test_http_5xx_is_retried_then_succeeds() -> None:
    page = FakePage([http(503, headers={"retry-after": "2"}), success(1, [1])])
    clock = Clock()
    assert "tag_search_1" in fetcher(page, clock, backoff_base_s=1)(1)
    assert any(s >= 2 for s in clock.sleeps)


def test_timeout_is_retried_then_fails() -> None:
    page = FakePage([timeout(), timeout(), timeout()])
    with pytest.raises(TransientFetchError, match="timeout"):
        fetcher(page, max_retries=2, backoff_base_s=1)(1)
    assert len(page.calls) == 3


def test_connection_error_is_retried_then_succeeds() -> None:
    page = FakePage([connection_error(), success(1, [1])])
    assert "tag_search_1" in fetcher(page, backoff_base_s=1)(1)


def test_unexpected_http_status_fails_without_retry() -> None:
    page = FakePage([http(404)])
    with pytest.raises(FetchError, match="404"):
        fetcher(page)(1)
    assert len(page.calls) == 1


def test_backoff_grows_with_jitter() -> None:
    page = FakePage([timeout(), timeout(), timeout()])
    clock = Clock()
    with pytest.raises(TransientFetchError):
        fetcher(page, clock, max_retries=2, backoff_base_s=10, backoff_max_s=100,
                page_delay_s=0, page_delay_jitter_s=0)(1)
    # first retry ~10..20s, second ~20..30s (base * 2^(n-1) + jitter[0, base])
    assert 10 <= clock.sleeps[0] < 20
    assert 20 <= clock.sleeps[1] < 30


# --- runner / database integration -------------------------------------------

def test_fetcher_integrates_with_parser_and_database(db: Database) -> None:
    page = FakePage([success(1, [1, 2]), success(2, [150])])
    plan_run(db, 1, 2)
    summary = run_pages(db, 1, 2, fetcher(page, page_delay_s=0))
    assert (summary.completed, summary.records_inserted) == (2, 3)
    assert [r.number for r in db.iter_records()] == [1, 2, 150]
    assert page.calls == [BASE, f"{BASE}&page=2"]


def test_stop_run_keeps_page_pending_and_preserves_prior_records(db: Database) -> None:
    page = FakePage([success(1, [1]), captcha()])
    plan_run(db, 1, 3)
    summary = run_pages(db, 1, 3, fetcher(page, headless=True))
    assert summary.completed == 1 and summary.stopped
    assert db.page(1)["status"] == COMPLETED and db.page(2)["status"] == PENDING
    assert db.page(2)["attempts"] == 0
    assert [r.number for r in db.iter_records()] == [1]


def test_transient_failure_marks_page_failed_and_is_retried(db: Database) -> None:
    page = FakePage([timeout(), timeout(), timeout(), success(1, [1])])
    plan_run(db, 1, 1)
    first = run_pages(db, 1, 1, fetcher(page, max_retries=2, backoff_base_s=0.1))
    assert first.failed == 1 and db.page(1)["status"] != COMPLETED
    second = run_pages(db, 1, 1, fetcher(page, max_retries=2, backoff_base_s=0.1))
    assert second.completed == 1 and [r.number for r in db.iter_records()] == [1]


def test_recovery_after_interruption_with_live_fetcher(db_path: Path) -> None:
    page = FakePage([success(1, [1]), success(2, [101])])
    with Database(db_path) as db:
        plan_run(db, 1, 2)
        run_pages(db, 1, 2, fetcher(page), max_pages=1)
        db.claim_page(2)  # simulate crash mid-page-2
    with Database(db_path) as db:
        page.calls.clear()
        summary = run_pages(db, 1, 2, fetcher(page))
        assert page.calls == [f"{BASE}&page=2"]
        assert summary.completed == 1
        assert [r.number for r in db.iter_records()] == [1, 101]


def test_run_live_uses_injected_playwright(db: Database) -> None:
    fake = FakePlaywright()
    fake.browser.context.page.script = [success(1, [1, 2])]
    original = BrowserSession.__init__

    def patched(self: BrowserSession, settings: BrowserSettings, playwright_factory=None) -> None:
        original(self, settings, playwright_factory=factory(fake))

    monkey = pytest.MonkeyPatch()
    monkey.setattr(BrowserSession, "__init__", patched)
    try:
        plan_run(db, 1, 1)
        summary = run_live(db, 1, 1, settings(), max_pages=1)
    finally:
        monkey.undo()
    assert summary.records_inserted == 2
    assert fake.browser.closed and fake.stopped


@pytest.mark.skipif(not SAMPLE.exists(), reason="saved sample page not available")
def test_saved_sample_served_through_fetcher(db: Database) -> None:
    page = FakePage([ScriptedNav(html=SAMPLE.read_text(encoding="utf-8"), url=BASE)])
    plan_run(db, 1, 1)
    summary = run_pages(db, 1, 1, fetcher(page))
    assert summary.records_inserted == 9
    assert [r.number for r in db.iter_records()] == list(range(1, 10))


def test_stop_run_error_is_a_stop_run_error() -> None:
    assert issubclass(AccessRestrictedError, StopRunError)
    assert issubclass(CaptchaRequiredError, AccessRestrictedError)
    assert issubclass(RateLimitedError, AccessRestrictedError)
