"""Navigation waiting, cleanup of unfinished navigations, and sanitized diagnostics (offline)."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from scraper.browser import (
    BrowserSession,
    BrowserSettings,
    CaptchaRequiredError,
    FetchError,
    ListingFetcher,
    TransientFetchError,
)
from scraper.database import FAILED, PENDING, Database
from scraper.pagination import page_segments
from scraper.runner import plan_run, run_pages
from tests.fake_playwright import (
    BLANK_URL,
    FakePage,
    FakePlaywright,
    FakeRequest,
    FakeResponse,
    factory,
)
from tests.html_fixtures import CAPTCHA_PAGE, listing_html, listing_page, listing_row

BASE = "https://honestpool.ru/puzzle?id=71"
PAGE2 = f"{BASE}&page=2"
CAPTCHA_URL = "https://honestpool.ru/captcha?url=puzzle?id=71&page=2"
PARTIAL_2 = listing_html(listing_row(n) for n in list(page_segments(2))[:40])
ASSETS = [("https://honestpool.ru/assets/jquery/jquery-3.7.1.min.js", "script"),
          ("https://honestpool.ru/assets/plugin/chartjs/chart.js", "script")]


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@dataclass
class Nav:
    """One scripted navigation. ``commit_after=None``: the server never answers."""
    commit_after: float | None = 0.5
    statuses: list[int] = field(default_factory=lambda: [200])
    url: str | None = None
    html: str = ""
    early_html: str = ""
    dcl_after: float | None = 0.5
    resources: list[tuple[str, str, str]] = field(default_factory=list)  # (url, type, finished|pending|failure text)


class EventPage(FakePage):
    """Emits Playwright-like events and models a navigation that keeps loading after a timeout."""

    def __init__(self, navs: list[Nav], clock: Clock, *, blank_fails: bool = False) -> None:
        super().__init__()
        self.navs, self.clock, self.blank_fails = list(navs), clock, blank_fails
        self.uncommitted: FakeRequest | None = None
        self.current: Nav | None = None
        self.committed_at = 0.0
        self.overlaps = 0
        self.actions: list[str] = []

    def goto(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 30_000):
        if url == BLANK_URL:
            self.actions.append("reset")
            if self.blank_fails:
                raise PlaywrightTimeoutError("Timeout 5000ms exceeded.")
            if self.uncommitted is not None:
                self.uncommitted.failure = "net::ERR_ABORTED"
                self.emit("requestfailed", self.uncommitted)
                self.uncommitted = None
            self.current = None
            return super().goto(url, wait_until, timeout)
        if self.uncommitted is not None:
            self.overlaps += 1
        self.actions.append(f"goto {url}")
        self.calls.append(url)
        nav = self.navs.pop(0)
        document = FakeRequest(url, "document", navigation=True, frame=self.main_frame)
        self.emit("request", document)
        if nav.commit_after is None:
            self.clock.now += timeout / 1000
            self.uncommitted = document
            raise PlaywrightTimeoutError(f"Timeout {timeout}ms exceeded.")
        self.clock.now += nav.commit_after
        for index, status in enumerate(nav.statuses):
            if index:
                document = FakeRequest(nav.url or url, "document", navigation=True, frame=self.main_frame,
                                       redirected_from=document)
                self.emit("request", document)
            self.emit("response", FakeResponse(status, request=document))
            self.emit("requestfinished", document)
        self.url = nav.url or url
        self.current, self.committed_at = nav, self.clock.now
        self.emit("framenavigated", self.main_frame)
        self.html = nav.html if nav.dcl_after == 0 else nav.early_html
        for res_url, kind, outcome in nav.resources:
            request = FakeRequest(res_url, kind, frame=self.main_frame)
            self.emit("request", request)
            if outcome == "finished":
                self.emit("requestfinished", request)
            elif outcome != "pending":
                request.failure = outcome
                self.emit("requestfailed", request)
        return FakeResponse(nav.statuses[-1], request=document)

    def wait_for_load_state(self, state: str = "load", timeout: float | None = None) -> None:
        nav = self.current
        if nav is not None and nav.dcl_after is not None:
            ready = self.committed_at + nav.dcl_after
            if ready <= self.clock.now + (timeout or 0) / 1000:
                self.clock.now = max(self.clock.now, ready)
                self.html = nav.html
                self.emit("domcontentloaded")
                return
        self.clock.now += (timeout or 0) / 1000
        raise PlaywrightTimeoutError(f"Timeout {timeout}ms exceeded.")


class CookieContext:
    def __init__(self, replacement: FakePage) -> None:
        self.cookies = [{"name": "PHPSESSID", "value": "s3ss10n-v4lue"}]
        self.replacement = replacement
        self.new_pages = 0
        self.closed = False

    def new_page(self) -> FakePage:
        self.new_pages += 1
        self.replacement.context = self
        return self.replacement


def make(navs: list[Nav], clock: Clock | None = None, **overrides: object):
    clock = clock or Clock()
    page = EventPage(navs, clock)
    options = dict(page_delay_s=0, page_delay_jitter_s=0, backoff_base_s=1, backoff_max_s=4,
                   navigation_timeout_ms=60_000, max_retries=0)
    options.update(overrides)
    fetch = ListingFetcher(page, BrowserSettings(**options), base_url=BASE,  # type: ignore[arg-type]
                           notify=lambda _: None, sleep=clock.sleep, clock=clock, rng=random.Random(0))
    return fetch, page, clock


@pytest.fixture
def logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.INFO, logger="scraper.browser")
    return caplog


# --- waiting for the document ---------------------------------------------------

def test_successful_navigation_with_domcontentloaded(logs: pytest.LogCaptureFixture) -> None:
    nav = Nav(commit_after=0.8, html=listing_page(2, [150]), dcl_after=1.5,
              resources=[(u, t, "finished") for u, t in ASSETS])
    fetch, page, _ = make([nav])
    assert "tag_search_150" in fetch(2)
    assert "page 2: navigation 1 started (timeout 60s)" in logs.text
    assert f"page 2: HTTP 200 in 2.3s at {PAGE2}" in logs.text
    for part in ("main document: 200 at 0.8s", "committed: 0.8s", "DOMContentLoaded: 2.3s",
                 "listing: complete (100 rows)", "outstanding requests: 0;", "failed requests: none"):
        assert part in logs.text
    assert page.resets == 0


def test_html_received_while_resources_remain_pending(logs: pytest.LogCaptureFixture) -> None:
    nav = Nav(commit_after=1.0, early_html=listing_page(2, [150]), dcl_after=None,
              resources=[(u, t, "pending") for u, t in ASSETS])
    fetch, _, clock = make([nav])
    assert "tag_search_150" in fetch(2)
    assert clock.now <= 2.0
    assert "DOMContentLoaded: no" in logs.text
    assert "listing: complete (100 rows)" in logs.text
    assert "outstanding requests: 2 (script 2; 2 same-site)" in logs.text


def test_timeout_before_any_http_response(logs: pytest.LogCaptureFixture) -> None:
    fetch, page, _ = make([Nav(commit_after=None)])
    with pytest.raises(TransientFetchError, match=r"navigation timeout after 60\.0s"):
        fetch(2)
    for part in ("main document: not received", "committed: no", "listing: absent (no document committed)",
                 "outstanding requests: 1 (document 1; 1 same-site)"):
        assert part in logs.text
    assert page.resets == 1 and page.url == BLANK_URL and page.uncommitted is None
    assert "unfinished navigation stopped; tab reset to about:blank" in logs.text


def test_timeout_after_html_received(logs: pytest.LogCaptureFixture) -> None:
    nav = Nav(commit_after=1.0, early_html=PARTIAL_2, dcl_after=None,
              resources=[(ASSETS[1][0], "script", "pending")])
    fetch, page, _ = make([nav], navigation_timeout_ms=10_000)
    with pytest.raises(TransientFetchError, match="HTTP 200 received but the page did not finish loading"):
        fetch(2)
    for part in ("main document: 200 at 1.0s", "DOMContentLoaded: no", "listing: partial (40 of 100 rows)",
                 "outstanding requests: 1 (script 1; 1 same-site)"):
        assert part in logs.text
    assert page.resets == 1


# --- cleanup and retries --------------------------------------------------------

def test_retry_never_overlaps_the_previous_navigation() -> None:
    fetch, page, clock = make([Nav(commit_after=None), Nav(html=listing_page(2))], max_retries=1)
    assert "tag_search_101" in fetch(2)
    assert page.overlaps == 0
    assert page.actions == [f"goto {PAGE2}", "reset", f"goto {PAGE2}"]
    assert len(clock.sleeps) == 1


def test_fake_detects_overlap_without_cleanup() -> None:
    page = EventPage([Nav(commit_after=None), Nav(html=listing_page(2))], Clock())
    with pytest.raises(PlaywrightTimeoutError):
        page.goto(PAGE2)
    page.goto(PAGE2)
    assert page.overlaps == 1


def test_next_page_after_giving_up_starts_on_a_clean_tab(db: Database) -> None:
    fetch, page, _ = make([Nav(commit_after=None), Nav(html=listing_page(3))])
    plan_run(db, 2, 3)
    summary = run_pages(db, 2, 3, fetch)
    assert (summary.completed, summary.failed) == (1, 1)
    assert page.overlaps == 0 and page.actions[1] == "reset"
    assert db.page(2)["status"] == FAILED


def test_cleanup_replaces_a_stuck_tab_in_the_same_context(logs: pytest.LogCaptureFixture) -> None:
    clock = Clock()
    stuck = EventPage([Nav(commit_after=None)], clock, blank_fails=True)
    fresh = EventPage([Nav(html=listing_page(2, [199]))], clock)
    context = CookieContext(fresh)
    stuck.context = context
    fetch = ListingFetcher(stuck, BrowserSettings(page_delay_s=0, page_delay_jitter_s=0, max_retries=1,
                                                  backoff_base_s=1), base_url=BASE, notify=lambda _: None,
                           sleep=clock.sleep, clock=clock, rng=random.Random(0))
    assert "tag_search_199" in fetch(2)
    assert fetch.page is fresh and stuck.closed and not context.closed
    assert context.new_pages == 1
    assert context.cookies == [{"name": "PHPSESSID", "value": "s3ss10n-v4lue"}]
    assert "replaced the tab within the same browser context" in logs.text
    assert "main document: 200" in logs.text  # diagnostics follow the new tab


def test_failed_cleanup_without_a_context_stops_the_run(db: Database) -> None:
    clock = Clock()
    page = EventPage([Nav(commit_after=None)], clock, blank_fails=True)
    fetch = ListingFetcher(page, BrowserSettings(page_delay_s=0, page_delay_jitter_s=0, max_retries=2),
                           base_url=BASE, notify=lambda _: None, sleep=clock.sleep, clock=clock,
                           rng=random.Random(0))
    plan_run(db, 2, 3)
    summary = run_pages(db, 2, 3, fetch)
    assert summary.stopped and "could not stop the unfinished navigation" in summary.stopped
    assert page.calls == [PAGE2]
    assert [(db.page(p)["status"], db.page(p)["attempts"]) for p in (2, 3)] == [(PENDING, 0), (PENDING, 0)]


def test_cleanup_keeps_the_browser_session() -> None:
    fake = FakePlaywright()
    with BrowserSession(BrowserSettings(), playwright_factory=factory(fake)) as session:
        context = session.context
        page = session.page
        fetch = ListingFetcher(page, BrowserSettings(page_delay_s=0, page_delay_jitter_s=0, max_retries=0),
                               base_url=BASE, notify=lambda _: None, sleep=lambda _: None)
        page.script = []
        page.goto = _timeout_then_blank(page)  # type: ignore[method-assign]
        with pytest.raises(TransientFetchError):
            fetch(2)
        assert fetch.page is page and page.url == BLANK_URL
        assert session.context is context and not context.closed
        assert fake.chromium.launches == [{"headless": False, "handle_sigint": False}]


def _timeout_then_blank(page: FakePage):
    original = page.goto

    def goto(url: str, wait_until: str = "domcontentloaded", timeout: int = 30_000):
        if url == BLANK_URL:
            return original(url, wait_until, timeout)
        page.calls.append(url)
        raise PlaywrightTimeoutError("Timeout 30000ms exceeded.")
    return goto


# --- what counts as a loaded listing --------------------------------------------

@pytest.mark.parametrize("dcl_after", [0.5, None], ids=["after-dcl", "before-dcl"])
def test_captcha_after_redirect_is_detected(dcl_after: float | None, logs: pytest.LogCaptureFixture) -> None:
    nav = Nav(statuses=[302, 200], url=CAPTCHA_URL, html=CAPTCHA_PAGE, early_html=CAPTCHA_PAGE, dcl_after=dcl_after)
    fetch, page, _ = make([nav], headless=True)
    with pytest.raises(CaptchaRequiredError, match="headless"):
        fetch(2)
    assert "main document: 302->200" in logs.text
    assert page.calls == [PAGE2]


@pytest.mark.parametrize(
    ("html", "reason"),
    [
        (PARTIAL_2, "40 listing rows, expected 100"),
        (listing_page(2).replace("<th>End hex</th>", "<th>Hex end</th>"), "no valid data table"),
        ("", "no valid data table"),
        ("<html><head><title>HONEST BITCOIN PUZZLE POOL</title></head><body></body></html>", "no valid data table"),
    ],
    ids=["partial", "wrong-headers", "blank", "no-table"],
)
def test_incomplete_or_invalid_listing_is_rejected(db: Database, html: str, reason: str) -> None:
    fetch, _, _ = make([Nav(html=html)])
    plan_run(db, 2, 2)
    summary = run_pages(db, 2, 2, fetch)
    assert summary.failed == 1 and db.record_count() == 0
    assert db.page(2)["status"] == FAILED
    assert "incomplete or invalid listing" in db.page(2)["last_error"] and reason in db.page(2)["last_error"]


def test_listing_for_the_wrong_page_is_not_accepted_early() -> None:
    nav = Nav(early_html=listing_page(3), html=listing_page(3), dcl_after=None)
    fetch, _, _ = make([nav], navigation_timeout_ms=5_000)
    with pytest.raises(TransientFetchError, match="did not finish loading"):
        fetch(2)


def test_browser_error_cleans_up_and_fails_the_page() -> None:
    clock = Clock()
    page = EventPage([], clock)
    page.goto = _raise_then_blank(page, PlaywrightError("Navigation failed because page crashed!"))  # type: ignore[method-assign]
    fetch = ListingFetcher(page, BrowserSettings(page_delay_s=0, page_delay_jitter_s=0), base_url=BASE,
                           notify=lambda _: None, sleep=clock.sleep, clock=clock)
    with pytest.raises(FetchError, match="browser error"):
        fetch(2)
    assert page.url == BLANK_URL


def _raise_then_blank(page: EventPage, error: Exception):
    original = page.goto

    def goto(url: str, wait_until: str = "domcontentloaded", timeout: int = 30_000):
        if url == BLANK_URL:
            return original(url, wait_until, timeout)
        raise error
    return goto


# --- sanitized diagnostics ------------------------------------------------------

def test_diagnostics_are_sanitized(logs: pytest.LogCaptureFixture) -> None:
    nav = Nav(commit_after=0.5, url=f"{PAGE2}&csrf=deadbeefcafe&session=s3cr3t", html=listing_page(2),
              resources=[("https://honestpool.ru/assets/app.js?token=t0k3n", "script", "finished"),
                         ("https://tracker.example/pixel?uid=u5er", "image",
                          "net::ERR_CONNECTION_RESET at https://tracker.example/pixel?uid=u5er"),
                         ("https://honestpool.ru/captcha/img?answer=Xy7Qz", "image", "pending")])
    fetch, _, _ = make([nav])
    fetch(2)
    text = logs.text
    assert f"at {PAGE2};" in text
    assert "failed requests: ERR_CONNECTION_RESET x1" in text
    assert "outstanding requests: 1 (image 1; 1 same-site)" in text
    for secret in ("deadbeefcafe", "s3cr3t", "t0k3n", "tracker.example", "u5er", "Xy7Qz", "csrf", "session=",
                   "app.js", "assets/"):
        assert secret not in text


def test_diagnostic_handler_errors_never_break_navigation(logs: pytest.LogCaptureFixture) -> None:
    fetch, page, _ = make([Nav(html=listing_page(2))])
    page.emit("response", FakeResponse(200, request=None))  # malformed event
    page.emit("request", object())
    assert "tag_search_101" in fetch(2)
