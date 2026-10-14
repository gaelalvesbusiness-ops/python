"""Progress reporting, resume statistics, safe shutdown and CLI compatibility (offline)."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

import main as cli
from scraper import browser
from scraper.browser import BrowserSession, BrowserSettings, ListingFetcher
from scraper.database import COMPLETED, FAILED, PENDING, Database
from scraper.progress import ProgressReporter, format_duration
from scraper.runner import RunSummary, StopRunError, plan_run, process_page, run_live, run_pages
from tests.fake_playwright import FakePage, FakePlaywright, captcha, factory, success
from tests.html_fixtures import FakeSite, listing_page


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class TimedSite(FakeSite):
    """FakeSite whose pages take ``seconds[page]`` on the clock; ``captcha[page]`` adds excluded wait."""

    def __init__(self, clock: Clock, seconds: dict[int, float], captcha_wait: dict[int, float] | None = None,
                 **kwargs) -> None:
        super().__init__(**kwargs)
        self.clock, self.seconds, self.captcha_wait = clock, seconds, captcha_wait or {}
        self.manual_wait_s = 0.0

    def __call__(self, page: int) -> str:
        wait = self.captcha_wait.get(page, 0.0)
        self.manual_wait_s += wait
        self.clock.now += self.seconds.get(page, 10.0) + wait
        return super().__call__(page)


def reporter_for(db: Database, start: int, end: int, clock: Clock, notes: list[str] | None = None,
                 **kwargs) -> ProgressReporter:
    return ProgressReporter(db, start, end, clock=clock,
                            notify=(notes if notes is not None else []).append, **kwargs)


# --- formatting and the report -------------------------------------------------

def test_format_duration() -> None:
    assert format_duration(0) == "0:00:00"
    assert format_duration(3725) == "1:02:05"
    assert format_duration(90_061) == "1 d 1:01:01"


def test_report_shows_all_fields_before_first_measurement(db: Database) -> None:
    plan_run(db, 1, 5)
    process_page(db, 1, FakeSite(verified={1: [1, 2]}))
    clock, notes = Clock(), []
    reporter = reporter_for(db, 1, 5, clock, notes)
    clock.now += 65
    reporter.page_started(2)
    report = notes[-1]
    lines = report.splitlines()
    assert lines[0] == "Progress | current page: 2 | range 1..5 (5 pages)"
    assert lines[1] == ("  pages: 1 completed, 4 pending, 0 failed | verified records stored: 2 | "
                        "last successful page: 1")
    assert lines[2] == "  session runtime: 0:01:05 | average per completed page: no completed page measured yet"
    assert "approximate" in lines[3] and "excludes CAPTCHA waits" in lines[3]
    assert "unknown until a page has been completed in this session" in lines[3]


def test_estimate_uses_measured_pages_and_excludes_captcha_waits(db: Database) -> None:
    clock = Clock()
    site = TimedSite(clock, seconds={1: 20.0, 2: 30.0}, captcha_wait={2: 600.0})
    plan_run(db, 1, 5)
    reporter = reporter_for(db, 1, 5, clock, page_limit=3)
    reporter.excluded_time = lambda: site.manual_wait_s
    run_pages(db, 1, 5, site, max_pages=2, reporter=reporter)
    snap = reporter.snapshot()
    assert reporter.durations == [20.0, 30.0]
    assert snap.average_s == 25.0
    assert (snap.remaining_range, snap.remaining_run) == (3, 1)
    assert (snap.eta_range_s, snap.eta_run_s) == (75.0, 25.0)
    assert snap.runtime_s == 650.0
    report = reporter.render()
    assert "average per completed page: 25.0 s (2 measured, CAPTCHA waits excluded)" in report
    assert "this run 0:00:25 for 1 page(s); whole range 0:01:15 for 3 page(s)" in report


def test_resume_statistics_include_earlier_sessions(db_path: Path) -> None:
    with Database(db_path) as db:
        plan_run(db, 1, 10)
        for page in (1, 2, 3, 4):
            process_page(db, page, FakeSite(verified={2: [150]}))
    with Database(db_path) as db:
        clock, notes = Clock(), []
        start, end = plan_run(db, resume=True)
        reporter = reporter_for(db, start, end, clock, notes)
        snap = reporter.snapshot()
        assert (snap.completed, snap.pending, snap.failed, snap.records) == (4, 6, 0, 1)
        assert snap.last_success == 4 and snap.average_s is None
        site = TimedSite(clock, seconds={5: 12.0})
        run_pages(db, start, end, site, max_pages=1, reporter=reporter)
        assert site.calls == [5]
        snap = reporter.snapshot()
        assert (snap.completed, snap.pending, snap.last_success, snap.measured_pages) == (5, 5, 5, 1)
        assert snap.eta_range_s == 60.0
        assert "current page: 5" in notes[0]


def test_page_range_change_reports_only_the_new_range(db: Database) -> None:
    run_pages(db, *plan_run(db, 1, 3), FakeSite(verified={1: [1, 2], 2: [101]}))
    reporter = reporter_for(db, *plan_run(db, 350_000, 350_002), Clock())
    snap = reporter.snapshot()
    assert (snap.completed, snap.pending, snap.failed) == (0, 3, 0)
    assert snap.records == 3 and snap.last_success is None
    assert "range 350,000..350,002 (3 pages)" in reporter.render()
    assert "last successful page: none yet" in reporter.render()
    assert [db.page(p)["status"] for p in (1, 2, 3)] == [COMPLETED] * 3


def test_zero_record_completed_page_counts_as_success(db: Database) -> None:
    clock = Clock()
    plan_run(db, 3, 4)
    reporter = reporter_for(db, 3, 4, clock)
    run_pages(db, 3, 4, TimedSite(clock, seconds={3: 18.0}), max_pages=1, reporter=reporter)
    snap = reporter.snapshot()
    assert db.page(3)["records_found"] == 0
    assert (snap.completed, snap.records, snap.last_success, snap.average_s) == (1, 0, 3, 18.0)


def test_failed_pages_are_reported_and_not_averaged(db: Database) -> None:
    clock = Clock()
    site = TimedSite(clock, seconds={1: 10.0, 2: 40.0, 3: 20.0}, scripted={2: [RuntimeError("boom")]})
    plan_run(db, 1, 4)
    reporter = reporter_for(db, 1, 4, clock)
    run_pages(db, 1, 4, site, max_pages=3, reporter=reporter)
    snap = reporter.snapshot()
    assert (snap.completed, snap.pending, snap.failed) == (2, 1, 1)
    assert snap.last_success == 3 and reporter.durations == [10.0, 20.0]
    assert snap.remaining_range == 2
    assert "2 completed, 1 pending, 1 failed" in reporter.render()


def test_exhausted_failed_pages_are_not_counted_as_remaining(db: Database) -> None:
    plan_run(db, 1, 2)
    for _ in range(3):
        db.claim_page(2)
        db.fail_page(2, "timeout")
    reporter = reporter_for(db, 1, 2, Clock(), max_attempts=3)
    snap = reporter.snapshot()
    assert (snap.failed, snap.remaining_range) == (1, 1)


# --- stopping safely ------------------------------------------------------------

def test_access_stop_reports_and_keeps_page_pending(db: Database) -> None:
    clock = Clock()
    site = TimedSite(clock, seconds={}, scripted={2: [StopRunError("HTTP 403")]})
    plan_run(db, 1, 3)
    reporter = reporter_for(db, 1, 3, clock)
    summary = run_pages(db, 1, 3, site, reporter=reporter)
    assert summary.stopped and site.calls == [1, 2]
    assert (db.page(2)["status"], db.page(2)["attempts"]) == (PENDING, 0)
    assert reporter.current_page is None and reporter.snapshot().completed == 1


def test_interrupt_releases_page_and_keeps_records(db: Database) -> None:
    clock = Clock()
    site = TimedSite(clock, seconds={}, verified={1: [1]}, scripted={2: [KeyboardInterrupt()]})
    plan_run(db, 1, 3)
    reporter = reporter_for(db, 1, 3, clock)
    with pytest.raises(KeyboardInterrupt):
        run_pages(db, 1, 3, site, reporter=reporter)
    assert (db.page(2)["status"], db.page(2)["attempts"]) == (PENDING, 0)
    assert db.record_count() == 1 and reporter.current_page is None


def test_cli_ctrl_c_exits_cleanly_with_resume_hint(db_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run_live", interrupted)
    assert cli.main(["--db", str(db_path), "--start-page", "1", "--end-page", "2", "--live"]) == 130
    out = capsys.readouterr().out
    assert "Progress | current page: none | range 1..2 (2 pages)" in out
    assert "Interrupted: the current page was returned to pending" in out and "--resume --live" in out


def test_manual_captcha_wait_is_measured_for_exclusion() -> None:
    clock = Clock()
    page = FakePage([captcha(), success(1, [1])])

    def user(seconds: float) -> None:
        clock.now += seconds
        if clock.now >= 1_090:
            page.url, page.html = "https://honestpool.ru/puzzle?id=71", listing_page(1, [1])

    fetch = ListingFetcher(page, BrowserSettings(page_delay_s=0, page_delay_jitter_s=0, captcha_poll_s=10),
                           notify=lambda _: None, sleep=user, clock=clock, rng=random.Random(0))
    fetch(1)
    assert fetch.manual_wait_s == 90.0


def test_run_live_wires_the_reporter_to_the_fetcher(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakePlaywright()
    fake.browser.context.page.script = [success(2, [150])]
    monkeypatch.setattr(browser, "BrowserSession", lambda settings: BrowserSession(settings, factory(fake)))
    plan_run(db, 2, 2)
    notes: list[str] = []
    reporter = ProgressReporter(db, 2, 2, notify=notes.append)
    summary = run_live(db, 2, 2, BrowserSettings(page_delay_s=0, page_delay_jitter_s=0), max_pages=1,
                       notify=lambda _: None, reporter=reporter)
    assert summary.completed == 1 and reporter.excluded_time() == 0.0
    assert reporter.snapshot().measured_pages == 1 and "current page: 2" in notes[0]


# --- CLI backward compatibility -------------------------------------------------

def capture_run_live(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    calls: dict[str, object] = {}

    def fake_run_live(db, start, end, settings, *, max_pages, max_attempts, reporter=None, solver=None,
                      on_page_saved=None):
        calls.update(start=start, end=end, max_pages=max_pages, reporter=reporter)
        return RunSummary(start, end, planned=1)

    monkeypatch.setattr(cli, "run_live", fake_run_live)
    return calls


def test_documented_start_page_command(db_path: Path, monkeypatch: pytest.MonkeyPatch,
                                       capsys: pytest.CaptureFixture[str]) -> None:
    calls = capture_run_live(monkeypatch)
    assert cli.main(["--db", str(db_path), "--start-page", "350000", "--end-page", "350100", "--live"]) == 0
    assert (calls["start"], calls["end"], calls["max_pages"]) == (350_000, 350_100, 1)
    assert isinstance(calls["reporter"], ProgressReporter)
    out = capsys.readouterr().out
    assert "Run range: pages 350,000..350,100 (101 pages)" in out
    assert "Pages to process: 101 (next: [350000, 350001" in out


def test_documented_resume_command(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with Database(db_path) as db:
        run_pages(db, *plan_run(db, 1, 3), FakeSite(), max_pages=1)
    calls = capture_run_live(monkeypatch)
    assert cli.main(["--db", str(db_path), "--resume", "--live"]) == 0
    assert (calls["start"], calls["end"], calls["max_pages"]) == (1, 3, 1)


@pytest.mark.parametrize("fmt", ["xlsx", "csv"])
def test_documented_export_commands(db_path: Path, tmp_path: Path, fmt: str,
                                    capsys: pytest.CaptureFixture[str]) -> None:
    with Database(db_path) as db:
        run_pages(db, *plan_run(db, 2, 2), FakeSite(verified={2: [101]}))
    out_file = tmp_path / f"verified_records.{fmt}"
    assert cli.main(["--db", str(db_path), "--export", fmt, "--output", str(out_file)]) == 0
    assert f"Exported 1 verified records to {out_file}" in capsys.readouterr().out
    assert out_file.stat().st_size > 0


def test_planning_output_and_help_are_backward_compatible(db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--db", str(db_path), "--start-page", "1", "--end-page", "3"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == ["Run range: pages 1..3 (3 pages)", "Recovered interrupted pages: 0",
                   "Progress in range: {'pending': 3, 'processing': 0, 'completed': 0, 'failed': 0}",
                   "Pages to process: 3 (next: [1, 2, 3])", "Verified records stored: 0",
                   "Planning only: add --live to fetch pages; no pages were fetched."]
    help_text = cli.build_parser().format_help()
    for phrase in ("Completed pages are always skipped", "There is no default range",
                   "main.py --resume --live", "main.py --export xlsx", "main.py --export csv",
                   "--start-page 350000 --end-page 350100 --live"):
        assert phrase in help_text
    assert cli.main(None) == 0


def test_live_without_range_still_refused() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--live"])
    assert exc.value.code == 2


def test_failed_status_constant_unchanged() -> None:
    assert (PENDING, FAILED, COMPLETED) == ("pending", "failed", "completed")
