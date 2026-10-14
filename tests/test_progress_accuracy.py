"""Attempted-page counts and delay-aware time estimates (offline, temporary databases only)."""

from __future__ import annotations

import random
from dataclasses import replace
from pathlib import Path

import pytest

import main as cli
from scraper import browser
from scraper.browser import BrowserSession, BrowserSettings, ListingFetcher
from scraper.config import DEFAULT_BASE_URL
from scraper.database import COMPLETED, FAILED, PENDING, Database
from scraper.pagination import page_url
from scraper.runner import RunAbortedError, RunSummary, StopRunError, plan_run, run_live, run_pages
from tests.fake_playwright import BLANK_URL, FakePage, FakePlaywright, captcha, factory, http, success
from tests.test_progress import Clock, TimedSite, reporter_for

FAST = dict(page_delay_s=0, page_delay_jitter_s=0, backoff_base_s=0.01, backoff_max_s=0.02)
PAGE2_CAPTCHA = "https://honestpool.ru/captcha?url=puzzle?id=71&page=2"


def quiet(_: str) -> None:
    return None


class WorkPage(FakePage):
    """FakePage whose every listing navigation takes ``work_s`` on the simulated clock."""

    def __init__(self, clock: Clock, work_s: float, script: list) -> None:
        super().__init__(script)
        self.clock, self.work_s = clock, work_s

    def goto(self, url: str, **kwargs):
        if url != BLANK_URL:
            self.clock.now += self.work_s
        return super().goto(url, **kwargs)


def live_fetcher(page: FakePage, clock: Clock, **settings) -> ListingFetcher:
    def sleep(seconds: float) -> None:
        clock.now += seconds

    return ListingFetcher(page, BrowserSettings(**settings), notify=quiet, sleep=sleep, clock=clock,
                          rng=random.Random(0))


def wired_reporter(db: Database, start: int, end: int, clock: Clock, fetcher: ListingFetcher, **kwargs):
    reporter = reporter_for(db, start, end, clock, **kwargs)
    reporter.excluded_time = lambda: fetcher.manual_wait_s
    return reporter


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakePlaywright:
    fake = FakePlaywright()
    monkeypatch.setattr(browser, "BrowserSession", lambda settings: BrowserSession(settings, factory(fake)))
    return fake


# --- PART A: attempted page counts ------------------------------------------------

def test_http_403_stop_counts_only_pages_actually_fetched(fake: FakePlaywright, db: Database) -> None:
    fake.browser.context.page.script = [success(1, [1]), http(403, page=2)]
    plan_run(db, 1, 4)
    summary = run_live(db, 1, 4, BrowserSettings(**FAST), max_pages=3, notify=quiet)
    assert summary.stopped and "403" in summary.stopped
    assert (summary.planned, summary.attempted, summary.completed, summary.failed) == (3, 2, 1, 0)
    assert (summary.interrupted, summary.not_attempted, summary.requests) == (1, 1, 2)
    assert [(db.page(p)["status"], db.page(p)["attempts"]) for p in (1, 2, 3, 4)] == [
        (COMPLETED, 1), (PENDING, 0), (PENDING, 0), (PENDING, 0)]


def test_cli_reports_corrected_counts_after_http_403(fake: FakePlaywright, db_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch,
                                                     capsys: pytest.CaptureFixture[str]) -> None:
    fake.browser.context.page.script = [success(1, [1]), http(403, page=2)]
    real_run_live = cli.run_live

    def fast_run_live(db, start, end, settings, **kwargs):
        return real_run_live(db, start, end, replace(settings, **FAST), notify=quiet, **kwargs)

    monkeypatch.setattr(cli, "run_live", fast_run_live)
    code = cli.main(["--db", str(db_path), "--start-page", "1", "--end-page", "3", "--live", "--max-pages", "3"])
    out = capsys.readouterr().out
    assert code == 3
    assert ("Live run: 1 completed, 0 failed, 1 new verified records (attempted 2 of 3 planned pages; "
            "1 stopped before completion and returned to pending; 1 not attempted; "
            "3 unfinished pages in range at start)") in out
    assert "attempted 3 of 3" not in out
    assert "Page requests sent: 2 (retries, CAPTCHA answer submissions and reloads after a CAPTCHA are extra " \
           "requests" in out
    assert "Run stopped: page 2: HTTP 403" in out


def test_captcha_stop_counts_the_attempt_and_excludes_the_wait(db: Database) -> None:
    clock = Clock()
    page = WorkPage(clock, 2.0, [success(1, [1]), captcha(PAGE2_CAPTCHA)])
    fetcher = live_fetcher(page, clock, page_delay_s=5, page_delay_jitter_s=0, captcha_wait_s=60, captcha_poll_s=2)
    plan_run(db, 1, 4)
    reporter = wired_reporter(db, 1, 4, clock, fetcher, delay_s=5, jitter_s=0, page_limit=3)
    summary = run_pages(db, 1, 4, fetcher, max_pages=3, reporter=reporter)
    assert summary.stopped and "CAPTCHA was not completed" in summary.stopped
    assert (summary.attempted, summary.completed, summary.interrupted, summary.not_attempted) == (2, 1, 1, 1)
    assert fetcher.navigations == 2 and fetcher.manual_wait_s == 60.0
    assert [(db.page(p)["status"], db.page(p)["attempts"]) for p in (2, 3)] == [(PENDING, 0), (PENDING, 0)]
    assert (reporter.started, reporter.completed, reporter.interrupted, reporter.stopped_page) == (2, 1, 1, 2)
    snap = reporter.snapshot()
    assert reporter.durations == [2.0] and snap.per_page_s == 5.0
    assert snap.eta_range_s == 15.0  # 3 pages x 5 s; the 60 s CAPTCHA wait is not part of any estimate
    assert snap.remaining_run == 0 and "nothing left in this run; whole range 0:00:15" in reporter.render()


def test_abort_after_consecutive_failures_reports_session_counts(db: Database) -> None:
    clock = Clock()
    site = TimedSite(clock, seconds={}, scripted={p: [RuntimeError("boom")] for p in (1, 2, 3)})
    plan_run(db, 1, 6)
    reporter = reporter_for(db, 1, 6, clock, page_limit=5)
    with pytest.raises(RunAbortedError):
        run_pages(db, 1, 6, site, max_pages=5, reporter=reporter)
    assert (reporter.started, reporter.failed, reporter.ended) == (3, 3, True)
    assert reporter.snapshot().remaining_run == 0
    assert cli.describe_session(reporter, 5) == (
        "This run: 0 completed, 3 failed (attempted 3 of 5 planned pages; 2 not attempted)")


def test_completed_page_with_zero_verified_records(db: Database) -> None:
    clock = Clock()
    fetcher = live_fetcher(WorkPage(clock, 2.0, [success(1, [])]), clock, page_delay_s=5, page_delay_jitter_s=0)
    plan_run(db, 1, 3)
    reporter = wired_reporter(db, 1, 3, clock, fetcher, delay_s=5, jitter_s=0)
    summary = run_pages(db, 1, 3, fetcher, max_pages=1, reporter=reporter)
    assert (summary.attempted, summary.completed, summary.records_inserted, summary.not_attempted) == (1, 1, 0, 0)
    assert db.page(1)["status"] == COMPLETED and db.page(1)["records_found"] == 0
    snap = reporter.snapshot()
    assert (snap.records, snap.last_success, snap.measured_pages, snap.per_page_s) == (0, 1, 1, 5.0)
    assert "this run" in reporter.render() and "unknown" not in reporter.render()


def test_retries_are_extra_requests_within_one_page_attempt(db: Database) -> None:
    clock = Clock()
    page = WorkPage(clock, 1.0, [http(503, page=1), http(503, page=1), success(1, [1])])
    fetcher = live_fetcher(page, clock, page_delay_s=5, page_delay_jitter_s=0, max_retries=2,
                           backoff_base_s=10, backoff_max_s=300)
    plan_run(db, 1, 2)
    reporter = wired_reporter(db, 1, 2, clock, fetcher, delay_s=5, jitter_s=0)
    summary = run_pages(db, 1, 2, fetcher, max_pages=1, reporter=reporter)
    assert (summary.attempted, summary.completed, summary.failed) == (1, 1, 0)
    assert fetcher.navigations == 3 and page.calls == [page_url(DEFAULT_BASE_URL, 1)] * 3
    assert (db.page(1)["status"], db.page(1)["attempts"]) == (COMPLETED, 1)
    assert reporter.durations == [clock.now - 1_000.0] and reporter.durations[0] > 33
    assert reporter.snapshot().per_page_s == reporter.durations[0]  # measured retry waits exceed the delay


def test_exhausted_retries_fail_one_page_attempt(db: Database) -> None:
    clock = Clock()
    fetcher = live_fetcher(WorkPage(clock, 1.0, [http(503, page=1)] * 3), clock, page_delay_s=5,
                           page_delay_jitter_s=0, max_retries=2, backoff_base_s=10)
    plan_run(db, 1, 2)
    reporter = wired_reporter(db, 1, 2, clock, fetcher, delay_s=5)
    summary = run_pages(db, 1, 2, fetcher, max_pages=1, reporter=reporter)
    assert (summary.attempted, summary.completed, summary.failed, fetcher.navigations) == (1, 0, 1, 3)
    assert (db.page(1)["status"], db.page(1)["attempts"]) == (FAILED, 1)
    assert reporter.durations == [] and reporter.snapshot().per_page_s is None


def test_run_live_counts_retry_requests(fake: FakePlaywright, db: Database) -> None:
    fake.browser.context.page.script = [http(503, page=1), http(503, page=1), success(1, [1])]
    plan_run(db, 1, 1)
    summary = run_live(db, 1, 1, BrowserSettings(max_retries=2, **FAST), max_pages=1, notify=quiet)
    assert (summary.attempted, summary.completed, summary.requests) == (1, 1, 3)
    assert db.page(1)["attempts"] == 1


def test_pages_beyond_the_page_limit_are_not_planned(db: Database) -> None:
    site = TimedSite(Clock(), seconds={})
    summary = run_pages(db, *plan_run(db, 1, 6), site, max_pages=3)
    assert (summary.planned, summary.attempted, summary.completed, summary.not_attempted) == (3, 3, 3, 0)
    assert site.calls == [1, 2, 3]
    assert [db.page(p)["attempts"] for p in (4, 5, 6)] == [0, 0, 0]


def test_stop_on_first_page_leaves_the_rest_of_the_plan_untouched(db: Database) -> None:
    site = TimedSite(Clock(), seconds={}, scripted={1: [StopRunError("HTTP 401")]})
    summary = run_pages(db, *plan_run(db, 1, 6), site, max_pages=4)
    assert (summary.planned, summary.attempted, summary.completed, summary.failed) == (4, 1, 0, 0)
    assert (summary.interrupted, summary.not_attempted) == (1, 3)
    assert site.calls == [1] and all(db.page(p)["attempts"] == 0 for p in range(1, 7))
    assert cli.describe_run(summary, 6) == (
        "Live run: 0 completed, 0 failed, 0 new verified records (attempted 1 of 4 planned pages; "
        "1 stopped before completion and returned to pending; 3 not attempted; "
        "6 unfinished pages in range at start)")


def test_full_run_text_has_no_stop_or_untouched_parts() -> None:
    summary = RunSummary(1, 3, planned=3, attempted=3, completed=2, failed=1, records_inserted=4)
    assert cli.describe_run(summary, 3) == ("Live run: 2 completed, 1 failed, 4 new verified records "
                                            "(attempted 3 of 3 planned pages; 3 unfinished pages in range at start)")


# --- PART B: delay-aware estimates ----------------------------------------------

def test_one_completed_page_with_several_remaining(db: Database) -> None:
    clock = Clock()
    plan_run(db, 1, 10)
    reporter = reporter_for(db, 1, 10, clock, delay_s=5, jitter_s=0, page_limit=5)
    run_pages(db, 1, 10, TimedSite(clock, seconds={1: 2.0}), max_pages=1, reporter=reporter)
    snap = reporter.snapshot()
    assert (snap.average_s, snap.per_page_s, snap.remaining_run, snap.remaining_range) == (2.0, 5.0, 4, 9)
    assert (snap.eta_run_s, snap.eta_range_s) == (20.0, 45.0)
    report = reporter.render()
    assert "average per completed page: 2.0 s (1 measured, CAPTCHA waits excluded)" in report
    assert "approximate, not guaranteed; excludes CAPTCHA waits" in report
    assert "this run 0:00:20 for 4 page(s); whole range 0:00:45 for 9 page(s)" in report
    assert "is counted as the 5.0 s average configured delay" in report
    assert "retries, slow server responses and CAPTCHA waits can make the real time longer" in report


@pytest.mark.parametrize(("delay", "jitter", "first_page", "expected"), [
    (5.0, 0.0, 2.0, 5.0),
    (15.0, 5.0, 2.0, 17.5),
    (30.0, 10.0, 2.0, 35.0),
    (5.0, 0.0, 40.0, 40.0),  # a slow first page keeps its real measured time
    (None, 0.0, 2.0, 2.0),  # no delay configured: measured time only, as before
])
def test_configured_delay_sets_the_first_page_floor(db: Database, delay: float | None, jitter: float,
                                                    first_page: float, expected: float) -> None:
    clock = Clock()
    plan_run(db, 1, 3)
    reporter = reporter_for(db, 1, 3, clock, delay_s=delay, jitter_s=jitter)
    run_pages(db, 1, 3, TimedSite(clock, seconds={1: first_page}), max_pages=1, reporter=reporter)
    snap = reporter.snapshot()
    assert reporter.durations == [first_page]
    assert snap.per_page_s == expected and snap.eta_range_s == expected * 2
    assert ("counted as the" in reporter.render()) == (expected != first_page)


def test_live_pacing_with_jitter_is_not_double_counted(db: Database) -> None:
    clock = Clock()
    page = WorkPage(clock, 1.0, [success(p) for p in (1, 2, 3)])
    fetcher = live_fetcher(page, clock, page_delay_s=15, page_delay_jitter_s=5)
    plan_run(db, 1, 5)
    reporter = wired_reporter(db, 1, 5, clock, fetcher, delay_s=15, jitter_s=5)
    run_pages(db, 1, 5, fetcher, max_pages=3, reporter=reporter)
    first, *paced = reporter.durations
    assert first == 1.0 and all(15.0 <= d <= 20.0 for d in paced)
    assert reporter.snapshot().per_page_s == pytest.approx((17.5 + sum(paced)) / 3)


def test_unknown_estimate_shows_only_a_delay_floor(db: Database) -> None:
    plan_run(db, 1, 10)
    lines = reporter_for(db, 1, 10, Clock(), delay_s=15, jitter_s=5, page_limit=5).render().splitlines()
    assert "unknown until a page has been completed in this session" in lines[3]
    assert "configured delays alone add at least 0:01:00 for 5 page(s)" in lines[3]
    assert len(lines) == 4
    single = reporter_for(db, 1, 10, Clock(), delay_s=15, page_limit=1).render()
    assert "at least" not in single


def test_interrupted_then_resumed_run(db_path: Path) -> None:
    with Database(db_path) as db:
        clock = Clock()
        plan_run(db, 1, 4)
        site = TimedSite(clock, seconds={1: 2.0}, verified={1: [1]}, scripted={2: [KeyboardInterrupt()]})
        reporter = reporter_for(db, 1, 4, clock, delay_s=5, jitter_s=0, page_limit=3)
        with pytest.raises(KeyboardInterrupt):
            run_pages(db, 1, 4, site, max_pages=3, reporter=reporter)
        assert (reporter.started, reporter.completed, reporter.failed, reporter.interrupted) == (2, 1, 0, 1)
        assert cli.describe_session(reporter, 3) == (
            "This run: 1 completed, 0 failed (attempted 2 of 3 planned pages; "
            "1 stopped before completion and returned to pending; 1 not attempted)")
        assert (db.page(2)["status"], db.page(2)["attempts"]) == (PENDING, 0)

    with Database(db_path) as db:
        clock = Clock()
        start, end = plan_run(db, resume=True)
        site = TimedSite(clock, seconds={2: 1.0, 3: 5.0, 4: 5.0})
        reporter = reporter_for(db, start, end, clock, delay_s=5, jitter_s=0)
        summary = run_pages(db, start, end, site, max_pages=3, reporter=reporter)
        assert site.calls == [2, 3, 4] and db.record_count() == 1
        assert (summary.planned, summary.attempted, summary.completed, summary.not_attempted) == (3, 3, 3, 0)
        assert reporter.durations == [1.0, 5.0, 5.0] and reporter.snapshot().per_page_s == 5.0


def test_cli_ctrl_c_reports_pages_attempted_this_run(db_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                     capsys: pytest.CaptureFixture[str]) -> None:
    site = TimedSite(Clock(), seconds={}, scripted={2: [KeyboardInterrupt()]})

    def interrupted_run_live(db, start, end, settings, *, max_pages, max_attempts, reporter=None, solver=None,
                             on_page_saved=None):
        return run_pages(db, start, end, site, max_pages=max_pages, max_attempts=max_attempts, reporter=reporter,
                         on_page_saved=on_page_saved)

    monkeypatch.setattr(cli, "run_live", interrupted_run_live)
    code = cli.main(["--db", str(db_path), "--start-page", "1", "--end-page", "3", "--live", "--max-pages", "2"])
    out = capsys.readouterr().out
    assert code == 130
    assert ("This run: 1 completed, 0 failed (attempted 2 of 2 planned pages; "
            "1 stopped before completion and returned to pending)") in out
    assert "Interrupted: the current page was returned to pending" in out


def test_estimate_after_multiple_completed_pages(db: Database) -> None:
    clock = Clock()
    page = WorkPage(clock, 1.0, [success(p) for p in (1, 2, 3, 4)])
    fetcher = live_fetcher(page, clock, page_delay_s=5, page_delay_jitter_s=0)
    plan_run(db, 1, 10)
    reporter = wired_reporter(db, 1, 10, clock, fetcher, delay_s=5, jitter_s=0)
    run_pages(db, 1, 10, fetcher, max_pages=4, reporter=reporter)
    snap = reporter.snapshot()
    assert reporter.durations == [1.0, 5.0, 5.0, 5.0]
    assert (snap.average_s, snap.per_page_s, snap.remaining_range) == (4.0, 5.0, 6)
    assert snap.eta_range_s == 30.0  # not 24 s (first page without delay) and not 60 s (delay counted twice)
    assert "estimate basis: 5.0 s per page (first page of this session had no delay" in reporter.render()
