"""Navigation timings belong to the navigation's own document, never to the previous page (offline)."""

from __future__ import annotations

import logging
import random
import re

import pytest

from scraper.browser import BrowserSettings, ListingFetcher, NavigationMonitor, TransientFetchError
from tests.fake_playwright import BLANK_URL, FakePage, FakeRequest, FakeResponse, success
from tests.html_fixtures import CAPTCHA_PAGE, listing_page
from tests.test_navigation_reliability import BASE, CAPTCHA_URL, PAGE2, Clock, EventPage, Nav

TIMES_RE = re.compile(r"main document: [\d>-]+ at (?P<response>[\d.]+)s; committed: (?P<commit>[\d.]+)s; "
                      r"DOMContentLoaded: (?P<dcl>[\d.]+)s")


class StalePage(EventPage):
    """The previous document keeps loading: its events fire while the next navigation is pending."""

    def __init__(self, navs: list[Nav], clock: Clock, stale: dict[int, list[tuple[float, str]]]) -> None:
        super().__init__(navs, clock)
        self.stale = stale
        self.real_navigations = 0

    def goto(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 30_000):
        if url == BLANK_URL:
            return super().goto(url, wait_until, timeout)
        self.real_navigations += 1
        started = self.clock.now
        for at, event in self.stale.get(self.real_navigations, []):
            self.clock.now = started + at
            if event == "request":
                old = FakeRequest("https://honestpool.ru/assets/plugin/chartjs/chart.js?v=t0k3n", "script",
                                  frame=self.main_frame)
                self.emit("request", old)
                old.failure = "net::ERR_ABORTED"
                self.emit("requestfailed", old)
            elif event == "navigation":
                own = FakeRequest(BASE, "document", navigation=True, frame=self.main_frame)
                self.emit("request", own)
                self.emit("response", FakeResponse(200, request=own))
            else:
                self.emit(event)
        return super().goto(url, wait_until, timeout)


def fetcher(page: FakePage, clock: Clock, **overrides: object) -> ListingFetcher:
    options = dict(page_delay_s=0, page_delay_jitter_s=0, navigation_timeout_ms=60_000, max_retries=0,
                   backoff_base_s=1, captcha_poll_s=2)
    options.update(overrides)
    return ListingFetcher(page, BrowserSettings(**options), base_url=BASE,  # type: ignore[arg-type]
                          notify=lambda _: None, sleep=clock.sleep, clock=clock, rng=random.Random(0))


@pytest.fixture
def logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.INFO, logger="scraper.browser")
    return caplog


def summary_for(logs: pytest.LogCaptureFixture, page: int, nth: int = -1) -> str:
    lines = [r.getMessage() for r in logs.records if r.getMessage().startswith(f"page {page}: ")
             and "main document:" in r.getMessage()]
    return lines[nth]


def times(summary: str) -> tuple[float, float, float]:
    match = TIMES_RE.search(summary)
    assert match, summary
    return float(match["response"]), float(match["commit"]), float(match["dcl"])


def test_previous_page_domcontentloaded_is_not_attributed(logs: pytest.LogCaptureFixture) -> None:
    clock = Clock()
    page = StalePage([Nav(commit_after=14.4, html=listing_page(2, [101]), dcl_after=0.2)], clock,
                     stale={1: [(9.5, "domcontentloaded")]})
    assert "tag_search_101" in fetcher(page, clock)(2)
    summary = summary_for(logs, 2)
    assert "DOMContentLoaded: 9.5s" not in summary
    assert "main document: 200 at 23.9s; committed: 23.9s; DOMContentLoaded: 24.1s" in summary


def test_previous_page_load_event_is_not_attributed(logs: pytest.LogCaptureFixture) -> None:
    clock = Clock()
    page = StalePage([Nav(commit_after=3.0, html=listing_page(2), dcl_after=0.5)], clock,
                     stale={1: [(1.0, "domcontentloaded"), (2.0, "load")]})
    fetcher(page, clock)(2)
    summary = summary_for(logs, 2)
    assert "load: no" in summary
    assert "DOMContentLoaded: 5.5s" in summary


def test_previous_document_requests_and_navigations_are_not_counted(logs: pytest.LogCaptureFixture) -> None:
    clock = Clock()
    page = StalePage([Nav(commit_after=4.0, html=listing_page(2), dcl_after=0.5)], clock,
                     stale={1: [(0.5, "request"), (1.0, "navigation")]})
    fetcher(page, clock)(2)
    summary = summary_for(logs, 2)
    assert "main document: 200 at 5.0s" in summary and "200->200" not in summary
    assert "failed requests: none" in summary and "outstanding requests: 0" in summary


def test_valid_navigation_has_ordered_timings(logs: pytest.LogCaptureFixture) -> None:
    clock = Clock()
    page = StalePage([Nav(commit_after=2.0, html=listing_page(2), dcl_after=1.3)], clock,
                     stale={1: [(0.4, "domcontentloaded"), (0.6, "load")]})
    fetcher(page, clock)(2)
    response, commit, dcl = times(summary_for(logs, 2))
    assert response <= commit <= dcl
    assert (response, commit, dcl) == (2.6, 2.6, 3.9)


def test_timeout_before_commit_reports_no_document_events(logs: pytest.LogCaptureFixture) -> None:
    clock = Clock()
    page = StalePage([Nav(commit_after=None)], clock,
                     stale={1: [(3.0, "domcontentloaded"), (4.0, "load"), (5.0, "request")]})
    with pytest.raises(TransientFetchError, match="navigation timeout"):
        fetcher(page, clock)(2)
    summary = summary_for(logs, 2)
    for part in ("main document: not received", "committed: no", "DOMContentLoaded: no", "load: no",
                 "outstanding requests: 1 (document 1; 1 same-site)", "failed requests: none"):
        assert part in summary


def test_cleanup_then_next_navigation_starts_fresh(logs: pytest.LogCaptureFixture) -> None:
    clock = Clock()
    page = StalePage([Nav(commit_after=None), Nav(commit_after=0.5, html=listing_page(2), dcl_after=0.5)], clock,
                     stale={2: [(0.1, "load")]})
    assert "tag_search_101" in fetcher(page, clock, max_retries=1)(2)
    assert page.actions == [f"goto {PAGE2}", "reset", f"goto {PAGE2}"]
    first, second = summary_for(logs, 2, 0), summary_for(logs, 2, 1)
    assert "main document: not received" in first
    assert "main document: 200 at 0.6s; committed: 0.6s; DOMContentLoaded: 1.1s; load: no" in second
    assert "failed requests: none" in second


def test_captcha_redirect_then_listing_uses_each_documents_own_timings(logs: pytest.LogCaptureFixture) -> None:
    clock = Clock()
    captcha_nav = Nav(commit_after=7.0, statuses=[302, 200], url=CAPTCHA_URL, html=CAPTCHA_PAGE,
                      early_html=CAPTCHA_PAGE, dcl_after=None)
    listing_nav = Nav(commit_after=14.0, html=listing_page(2, [101]), dcl_after=0.2)
    page = StalePage([captcha_nav, listing_nav], clock, stale={2: [(3.5, "domcontentloaded"), (9.5, "load")]})

    def user_solves(seconds: float) -> None:
        clock.sleep(seconds)
        if "captcha" in page.url:
            page.url, page.html, page.current = BASE, listing_page(1, list(range(1, 10))), None

    fetch = ListingFetcher(page, BrowserSettings(page_delay_s=0, page_delay_jitter_s=0, max_retries=0,
                                                 navigation_timeout_ms=60_000, captcha_poll_s=2),
                           base_url=BASE, notify=lambda _: None, sleep=user_solves, clock=clock,
                           rng=random.Random(0))
    assert "tag_search_101" in fetch(2)
    first, second = summary_for(logs, 2, 0), summary_for(logs, 2, 1)
    assert "main document: 302->200 at 7.0s" in first and "listing: absent" in first
    assert "DOMContentLoaded: 3.5s" not in second and "load: 9.5s" not in second
    assert "main document: 200 at 23.5s; committed: 23.5s; DOMContentLoaded: 23.7s; load: no" in second
    assert "listing: complete (100 rows)" in second


def test_missing_timing_events_are_reported_as_unknown_or_no(logs: pytest.LogCaptureFixture) -> None:
    clock = Clock()
    page = FakePage([success(2)])  # emits no events at all
    fetcher(page, clock)(2)
    summary = summary_for(logs, 2)
    assert "main document: 200 at unknown; committed: 0.0s; DOMContentLoaded: 0.0s; load: no" in summary


def test_monitor_never_reports_document_events_before_commit() -> None:
    clock = Clock()
    monitor = NavigationMonitor(clock, "honestpool.ru")
    monitor.start(PAGE2)
    monitor.mark_document_event("domcontentloaded")
    monitor.mark_document_event("load")
    assert "domcontentloaded" not in monitor.events and "load" not in monitor.events
    monitor.events.update(commit=5.0, domcontentloaded=3.0)
    assert "DOMContentLoaded: unknown" in monitor.summary()
    assert "committed: 5.0s" in monitor.summary()


def test_diagnostics_stay_sanitized(logs: pytest.LogCaptureFixture) -> None:
    clock = Clock()
    nav = Nav(commit_after=1.0, url=f"{PAGE2}&csrf=deadbeef&token=abc123", html=listing_page(2),
              resources=[("https://honestpool.ru/assets/app.js?session=s3cr3t", "script", "finished")])
    page = StalePage([nav], clock, stale={1: [(0.2, "request"), (0.5, "domcontentloaded")]})
    fetcher(page, clock)(2)
    text = logs.text
    assert f"at {PAGE2};" in text
    for secret in ("deadbeef", "abc123", "s3cr3t", "t0k3n", "csrf", "token=", "session=", "chart.js", "app.js"):
        assert secret not in text
