"""Newly stored records on screen, the automatic CSV after every page, and CAPTCHA resumption (offline).

Every page comes from synthetic HTML or the in-memory Playwright stand-in, 2Captcha is a scripted
fake and every database and CSV lives in a pytest temporary directory.
"""

from __future__ import annotations

import csv
import logging
import os
import random
import socket
import time
from dataclasses import replace
from pathlib import Path

import pytest

import main as cli
from scraper import browser, exporter, two_captcha
from scraper.browser import BrowserSession, BrowserSettings, CaptchaRequiredError, ListingFetcher
from scraper.database import COMPLETED, Database
from scraper.exporter import CSV_COLUMNS, export_from_db
from scraper.importer import read_verified_file, store_import
from scraper.live_output import LiveOutput, auto_csv_path, format_new_records
from scraper.parser import VERIFIED_TEXT, SegmentRecord, segment_range
from scraper.runner import plan_run, run_pages
from scraper.two_captcha import CaptchaSolution, SolverError
from tests.fake_playwright import FakePage, FakePlaywright, ScriptedNav, captcha_form, factory, success
from tests.fake_two_captcha import FAKE_KEY, FakeTwoCaptchaAPI
from tests.html_fixtures import FakeSite, listing_page, record

BASE = "https://honestpool.ru/puzzle?id=71"


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def url(page: int) -> str:
    return BASE if page == 1 else f"{BASE}&page={page}"


def csv_rows(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.reader(fh))


def csv_numbers(path: Path) -> list[int]:
    rows = csv_rows(path)
    assert rows[0] == list(CSV_COLUMNS)
    return [int(row[0]) for row in rows[1:]]


def temporaries(directory: Path) -> list[Path]:
    return sorted(directory.glob(".verified_records.*"))


class Recorder:
    """LiveOutput with its messages collected instead of printed."""

    def __init__(self, db: Database) -> None:
        self.notes: list[str] = []
        self.output = LiveOutput(db, auto_csv_path(db.path), notify=self.notes.append)

    @property
    def csv(self) -> Path:
        return self.output.csv_path


# --- records on screen ---------------------------------------------------------------------------

def test_new_records_are_shown_with_number_start_end_and_status() -> None:
    text = format_new_records(7, [record(601), record(650)])
    lines = text.splitlines()
    assert lines[0] == "Page 7: 2 new verified record(s) saved:"
    assert lines[1].split() == ["Number", "Start", "hex", "End", "hex", "Status"]
    for line, number in zip(lines[2:], (601, 650), strict=True):
        assert line.split() == [str(number), *segment_range(number), VERIFIED_TEXT]
    assert format_new_records(8, []) == "Page 8: no new verified records."


def test_only_records_new_to_the_database_are_reported(db: Database, tmp_path: Path) -> None:
    source = tmp_path / "known.txt"
    source.write_text("150\n", encoding="utf-8")
    store_import(db, read_verified_file(source), "list supplied for the test")
    plan_run(db, 2, 2)
    seen: list[tuple[int, list[SegmentRecord]]] = []
    summary = run_pages(db, 2, 2, FakeSite(verified={2: [150, 160]}), on_page_saved=lambda p, r: seen.append((p, r)))
    assert seen == [(2, [record(150), record(160)])], "150 was imported, not a website record yet"
    assert summary.records_inserted == 2

    db.conn.execute("UPDATE page_progress SET status = 'pending', attempts = 0 WHERE page_number = 2")
    seen.clear()
    run_pages(db, 2, 2, FakeSite(verified={2: [150, 160, 170]}), on_page_saved=lambda p, r: seen.append((p, r)))
    assert seen == [(2, [record(170)])], "records already stored are not shown as new again"


# --- automatic CSV after every completed page ------------------------------------------------------

def test_csv_is_rewritten_after_each_completed_page(db: Database) -> None:
    rec = Recorder(db)
    site = FakeSite(verified={1: [3, 7], 2: [], 3: [250, 299]})
    seen_before_fetch: dict[int, list[int]] = {}

    def fetch(page: int) -> str:
        seen_before_fetch[page] = csv_numbers(rec.csv) if rec.csv.exists() else []
        return site(page)

    plan_run(db, 1, 3)
    rec.output.start()
    run_pages(db, 1, 3, fetch, on_page_saved=rec.output.page_saved)
    assert seen_before_fetch == {1: [], 2: [3, 7], 3: [3, 7]}
    assert csv_numbers(rec.csv) == [3, 7, 250, 299]
    assert [n for n in rec.notes if n.startswith("CSV updated")] == [
        f"CSV updated: {rec.csv} (2 verified record(s)).",
        f"CSV updated: {rec.csv} (2 verified record(s)).",
        f"CSV updated: {rec.csv} (4 verified record(s)).",
    ]
    assert "Page 2: no new verified records." in rec.notes
    assert temporaries(rec.csv.parent) == []


def test_csv_holds_only_verified_records_once_and_matches_the_manual_export(db: Database, tmp_path: Path) -> None:
    source = tmp_path / "imported.txt"
    source.write_text("5\n150\n", encoding="utf-8")
    store_import(db, read_verified_file(source), "list supplied for the test")
    rec = Recorder(db)
    plan_run(db, 1, 2)
    rec.output.start()
    run_pages(db, 1, 2, FakeSite(verified={1: [1, 5, 99], 2: [150]}), on_page_saved=rec.output.page_saved)
    rows = csv_rows(rec.csv)[1:]
    assert [int(r[0]) for r in rows] == [1, 5, 99, 150], "website and imported copies of 5 and 150 appear once"
    assert all(r[1:] == [*segment_range(int(r[0])), VERIFIED_TEXT] for r in rows)
    manual = tmp_path / "manual.csv"
    export_from_db(db, manual, "all")
    assert manual.read_bytes() == rec.csv.read_bytes(), "same content as --export csv"


def test_records_already_in_the_database_are_written_at_the_start_of_a_run(db: Database) -> None:
    plan_run(db, 1, 1)
    run_pages(db, 1, 1, FakeSite(verified={1: [10, 20]}))
    rec = Recorder(db)
    assert not rec.csv.exists()
    rec.output.start()
    assert csv_numbers(rec.csv) == [10, 20]
    assert rec.notes == [f"CSV synchronized with the database: 2 verified record(s) in {rec.csv} "
                         f"(rewritten automatically after every completed page)."]


def test_csv_is_on_disk_before_it_replaces_the_previous_file(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(exporter.os, "fsync", lambda fd: (events.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(exporter.os, "replace", lambda a, b: (events.append("replace"), real_replace(a, b))[1])
    Recorder(db).output.start()
    assert events == ["fsync", "replace"]


# --- interruptions ------------------------------------------------------------------------------

def test_ctrl_c_during_the_csv_write_keeps_the_previous_csv_and_the_next_run_catches_up(
        db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(db)
    plan_run(db, 1, 2)
    rec.output.start()
    run_pages(db, 1, 1, FakeSite(verified={1: [3, 7]}), on_page_saved=rec.output.page_saved)
    before = rec.csv.read_bytes()

    real_writer = csv.writer

    class Interrupted:
        def __init__(self, fh) -> None:
            self.inner, self.rows = real_writer(fh), 0

        def writerow(self, row) -> None:
            self.rows += 1
            if self.rows == 3:  # header and one record written, then Ctrl+C
                raise KeyboardInterrupt
            self.inner.writerow(row)

    monkeypatch.setattr(exporter.csv, "writer", Interrupted)
    with pytest.raises(KeyboardInterrupt):
        run_pages(db, 1, 2, FakeSite(verified={2: [150, 160]}), on_page_saved=rec.output.page_saved)
    assert db.page(2)["status"] == COMPLETED and db.record_count() == 4, "SQLite already holds page 2"
    assert rec.csv.read_bytes() == before, "the CSV is the previous complete file, never a partial one"
    assert temporaries(rec.csv.parent) == []

    monkeypatch.setattr(exporter.csv, "writer", real_writer)
    Recorder(db).output.start()
    assert csv_numbers(rec.csv) == [3, 7, 150, 160]


def test_temporary_files_left_by_a_killed_process_are_removed_on_start(db: Database) -> None:
    rec = Recorder(db)
    directory = rec.csv.parent
    stale = directory / ".verified_records.k1ll3d.csv"
    stale.write_text("Number,Start hex\n1,", encoding="utf-8")
    old = time.time() - 3600
    os.utime(stale, (old, old))
    recent = directory / ".verified_records.wr1t1ng.csv"  # possibly another export still writing
    recent.write_text("", encoding="utf-8")
    unrelated = directory / ".other.k1ll3d.csv"
    unrelated.write_text("", encoding="utf-8")
    os.utime(unrelated, (old, old))
    rec.output.start()
    assert not stale.exists() and recent.exists() and unrelated.exists()
    assert csv_numbers(rec.csv) == []


def test_csv_write_failure_never_fails_a_committed_page(db: Database, monkeypatch: pytest.MonkeyPatch,
                                                        caplog: pytest.LogCaptureFixture) -> None:
    rec = Recorder(db)
    real_export = exporter.export_from_db
    calls = {"n": 0}

    def locked_once(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError(13, "Permission denied", str(rec.csv))
        return real_export(*args, **kwargs)

    monkeypatch.setattr("scraper.live_output.export_from_db", locked_once)
    plan_run(db, 1, 2)
    summary = run_pages(db, 1, 2, FakeSite(verified={1: [3], 2: [150]}), on_page_saved=rec.output.page_saved)
    assert summary.completed == 2 and summary.failed == 0
    assert [db.page(p)["status"] for p in (1, 2)] == [COMPLETED, COMPLETED]
    assert any(n.startswith("WARNING: the CSV could not be updated (PermissionError") and
               "Close verified_records.csv if it is open in Excel" in n for n in rec.notes)
    assert csv_numbers(rec.csv) == [3, 150], "the next completed page writes the CSV again"
    assert rec.output.failures == 1


def test_unexpected_display_error_is_logged_and_the_page_stays_completed(
        db: Database, caplog: pytest.LogCaptureFixture) -> None:
    def broken(page: int, records: list[SegmentRecord]) -> None:
        raise ValueError("display failed")

    plan_run(db, 1, 2)
    summary = run_pages(db, 1, 2, FakeSite(verified={1: [3]}), on_page_saved=broken)
    assert summary.completed == 2 and db.record_count() == 1
    assert "page 1: saved in SQLite, but showing or exporting it failed: display failed" in caplog.text


# --- CAPTCHA: the requested page resumes after verification ----------------------------------------

class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeSolver:
    def __init__(self, answer: str = "x", on_solve=None) -> None:
        self.answer, self.on_solve = answer, on_solve
        self.images: list[bytes] = []
        self.reports: list[tuple[int, bool]] = []

    def solve_image(self, image: bytes) -> CaptchaSolution:
        self.images.append(image)
        if self.on_solve is not None:
            self.on_solve()
        return CaptchaSolution(len(self.images), self.answer)

    def report(self, solution: CaptchaSolution, *, correct: bool) -> None:
        self.reports.append((solution.task_id, correct))


def fetcher(page: FakePage, solver: object, clock: Clock, sleep, notes: list[str], **overrides: object):
    values: dict[str, object] = dict(page_delay_s=0, page_delay_jitter_s=0, captcha_wait_s=30, captcha_poll_s=1)
    settings = replace(BrowserSettings(), **{**values, **overrides})  # type: ignore[arg-type]
    return ListingFetcher(page, settings, base_url=BASE, notify=notes.append, sleep=sleep, clock=clock,
                          rng=random.Random(0), solver=solver)  # type: ignore[arg-type]


def test_page_change_during_answer_pacing_is_not_mistaken_for_a_rejection() -> None:
    """The page changed while the answer waited for its pacing slot; the answer was then accepted."""
    clock, notes = Clock(), []
    page = FakePage([captcha_form(1), success(1, [1])])
    polls_after_click = [0]

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        if not page.clicks:
            if "late-banner" not in page.html:
                page.html = page.html.replace("</body>", '<div class="late-banner"></div></body>')
            return
        polls_after_click[0] += 1
        if polls_after_click[0] == 3:  # the site needs a few seconds to accept the answer
            page.url, page.html = BASE, listing_page(1)

    solver = FakeSolver()
    fetch = fetcher(page, solver, clock, sleep, notes, page_delay_s=10)
    assert "tag_search_1" in fetch(1)
    assert solver.reports == [(1, True)] and len(solver.images) == 1
    assert not any("rejected" in n for n in notes)


@pytest.mark.parametrize("headless", [False, True])
def test_verification_completed_during_the_automatic_attempt_needs_no_manual_wait(headless: bool) -> None:
    """The operator solved the CAPTCHA in the window while 2Captcha was still failing."""
    clock, notes = Clock(), []
    page = FakePage([captcha_form(1), success(1, [1])])

    class OperatorSolvesMeanwhile(FakeSolver):
        def solve_image(self, image: bytes) -> CaptchaSolution:
            self.images.append(image)
            page.url, page.html = BASE, listing_page(1)
            raise SolverError("2Captcha could not solve the CAPTCHA (ERROR_CAPTCHA_UNSOLVABLE)")

    solver = OperatorSolvesMeanwhile()
    fetch = fetcher(page, solver, clock, clock.sleep, notes, headless=headless)
    assert "tag_search_1" in fetch(1)
    assert page.calls == [BASE, BASE] and fetch.manual_verifications == 0
    assert clock.now < 30, "no wait for a manual verification that already happened"
    assert any("Verification detected: the browser already shows the listing" in n for n in notes)


def test_captcha_still_shown_after_failed_automatic_attempt_still_needs_a_person() -> None:
    clock, notes = Clock(), []
    page = FakePage([captcha_form(1)])
    solver = FakeSolver()
    solver.solve_image = lambda image: (_ for _ in ()).throw(SolverError("unsolvable"))  # type: ignore[method-assign]
    fetch = fetcher(page, solver, clock, clock.sleep, notes, headless=True)
    with pytest.raises(CaptchaRequiredError, match="manual verification is impossible in headless mode"):
        fetch(1)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakePlaywright:
    fake = FakePlaywright()
    monkeypatch.setattr(browser, "BrowserSession", lambda settings: BrowserSession(settings, factory(fake)))
    real_run_live = cli.run_live
    monkeypatch.setattr(cli, "run_live", lambda db, start, end, s, **kw: real_run_live(
        db, start, end, replace(s, page_delay_s=0, page_delay_jitter_s=0, captcha_poll_s=0.001), **kw))
    return fake


@pytest.fixture
def two_captcha_api(monkeypatch: pytest.MonkeyPatch) -> FakeTwoCaptchaAPI:
    api = FakeTwoCaptchaAPI(["ok42"])
    monkeypatch.setattr(two_captcha, "urllib_transport", api)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    monkeypatch.setenv("CAPTCHA_PROVIDER", "2captcha")
    monkeypatch.setenv("CAPTCHA_API_KEY", FAKE_KEY)
    monkeypatch.setenv("CAPTCHA_TIMEOUT", "120")
    return api


def test_cli_automatic_captcha_resumes_the_requested_page_shows_its_records_and_saves_the_csv(
        fake: FakePlaywright, two_captcha_api: FakeTwoCaptchaAPI, db_path: Path,
        capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    page = fake.browser.context.page
    page.script = [success(4, [301]), captcha_form(1), success(5, [450, 499])]
    page.submissions = [ScriptedNav(url=BASE, html=listing_page(1))]  # the redirect drops &page=5
    argv = ["--db", str(db_path), "--start-page", "4", "--end-page", "5", "--live", "--headless", "--max-pages", "2"]
    assert cli.main(argv) == 0
    out, err = capsys.readouterr()
    csv_path = db_path.parent / "verified_records.csv"

    assert page.calls == [url(4), url(5), url(5)], "page 5 is loaded again after the accepted answer"
    assert two_captcha_api.methods == ["createTask", "getTaskResult", "reportCorrect"]
    assert out.index("Verification succeeded automatically") < out.index("Page 5: 2 new verified record(s) saved:")
    for number in (301, 450, 499):
        start, end = segment_range(number)
        assert any(line.split() == [str(number), start, end, VERIFIED_TEXT] for line in out.splitlines())
    assert f"CSV updated: {csv_path.resolve()} (3 verified record(s))." in out or \
        f"CSV updated: {csv_path} (3 verified record(s))." in out
    assert csv_numbers(csv_path) == [301, 450, 499]
    assert f"CSV (rewritten automatically after every completed page): {csv_path.resolve()}" in out
    assert "Excel XLSX (on demand only: python main.py --export xlsx" in out
    assert not (db_path.parent / "verified_records.xlsx").exists()
    assert FAKE_KEY not in out + err + caplog.text and "ok42" not in out + err + caplog.text
    with Database(db_path) as db:
        assert [db.page(p)["status"] for p in (4, 5)] == [COMPLETED, COMPLETED]


def test_cli_export_to_a_locked_file_explains_instead_of_crashing(
        db_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    with Database(db_path) as db:
        plan_run(db, 1, 1)
        run_pages(db, 1, 1, FakeSite(verified={1: [3]}))
    output = db_path.parent / "verified_records.xlsx"

    def locked(*args, **kwargs):
        raise PermissionError(13, "Permission denied", str(output))

    monkeypatch.setattr(cli, "export_from_db", locked)
    assert cli.main(["--db", str(db_path), "--export", "xlsx", "--output", str(output)]) == 1
    err = capsys.readouterr().err
    assert f"error: could not write {output} (PermissionError" in err
    assert "close the file if it is open in Excel" in err and "Traceback" not in err


def test_cli_planning_only_writes_no_csv(db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--db", str(db_path), "--start-page", "1", "--end-page", "3"]) == 0
    assert "Planning only" in capsys.readouterr().out
    assert not (db_path.parent / "verified_records.csv").exists()
