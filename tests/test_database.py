"""SQLite storage, page lifecycle, recovery and single-writer tests."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from scraper.config import PROJECT_ROOT, TOTAL_PAGES
from scraper.database import (
    COMPLETED,
    FAILED,
    PENDING,
    PROCESSING,
    Database,
    DatabaseLockedError,
    IncompletePageError,
    InvalidRecordError,
    PageStateError,
)
from scraper.pagination import InvalidPageRangeError
from scraper.parser import SegmentRecord, segment_range
from tests.html_fixtures import record
from tests.platform_support import KILLED_RETURNCODE, open_after_crash, utf8_child_env


def columns(db: Database, table: str) -> list[str]:
    return [row[1] for row in db.conn.execute(f"PRAGMA table_info({table})")]


def complete(db: Database, page: int, numbers: list[int], rows: int = 100) -> int:
    db.claim_page(page)
    return db.complete_page(page, [record(n) for n in numbers], rows)


# --- initialization ----------------------------------------------------------

def test_initialization_creates_schema(db: Database, db_path: Path) -> None:
    assert db_path.exists()
    assert columns(db, "verified_records") == [
        "number", "start_hex", "end_hex", "status", "source_page", "extracted_at"]
    assert columns(db, "page_progress") == [
        "page_number", "status", "attempts", "records_found", "last_error", "updated_at"]
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert db.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db.record_count() == 0
    assert db.progress_counts() == {PENDING: 0, PROCESSING: 0, COMPLETED: 0, FAILED: 0}


def test_reopening_is_idempotent_and_keeps_data(db_path: Path) -> None:
    with Database(db_path) as first:
        complete(first, 1, [1, 2])
    with Database(db_path) as second:
        assert second.record_count() == 2
        assert second.page(1)["status"] == COMPLETED


def test_schema_constraints_reject_bad_rows(db: Database) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute("INSERT INTO verified_records VALUES (1, 'short', 'short', 'Проверено', 1, NULL)")
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute("INSERT INTO page_progress (page_number, status) VALUES (1, 'done')")
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute("INSERT INTO page_progress (page_number) VALUES (?)", (TOTAL_PAGES + 1,))


# --- insertion and duplicates ------------------------------------------------

def test_record_insertion_stores_source_page_and_timestamp(db: Database) -> None:
    db.register_pages(1, 1)
    assert db.claim_page(1) == 1
    assert db.complete_page(1, [record(1), record(5)], 100) == 2
    row = db.record_row(5)
    assert (row["start_hex"], row["end_hex"]) == segment_range(5)
    assert row["status"] == "Проверено" and row["source_page"] == 1 and row["extracted_at"]
    page = db.page(1)
    assert (page["status"], page["attempts"], page["records_found"], page["last_error"]) == (COMPLETED, 1, 2, None)
    assert list(db.iter_records()) == [record(1), record(5)]


def test_duplicate_numbers_are_stored_once(db: Database) -> None:
    assert complete(db, 1, [7, 7, 8]) == 2
    assert db.record_count() == 2
    assert db.page(1)["records_found"] == 2
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute("INSERT INTO verified_records VALUES (7, ?, ?, 'Проверено', 1, NULL)", segment_range(7))


def test_completed_page_cannot_be_claimed_again(db: Database) -> None:
    complete(db, 1, [1])
    with pytest.raises(PageStateError):
        db.claim_page(1)


@pytest.mark.parametrize(
    "bad",
    [
        SegmentRecord(1, *segment_range(2), "Проверено"),
        SegmentRecord(1, *segment_range(1), "Не проверено"),
        SegmentRecord(101, *segment_range(101), "Проверено"),
        SegmentRecord(0, "400000000000000000", "400000100000000000", "Проверено"),
    ],
)
def test_invalid_records_are_rejected_before_writing(db: Database, bad: SegmentRecord) -> None:
    db.claim_page(1)
    with pytest.raises(InvalidRecordError):
        db.complete_page(1, [record(2), bad], 100)
    assert db.record_count() == 0
    assert db.page(1)["status"] == PROCESSING


# --- atomic completion -------------------------------------------------------

def test_page_completion_is_atomic(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_after_inserts(*args: object) -> None:
        raise sqlite3.OperationalError("simulated failure between insert and status update")

    db.claim_page(1)
    monkeypatch.setattr(Database, "_mark_completed", staticmethod(fail_after_inserts))
    with pytest.raises(sqlite3.OperationalError):
        db.complete_page(1, [record(1), record(2)], 100)
    assert db.record_count() == 0
    assert db.page(1)["status"] == PROCESSING
    assert not db.conn.in_transaction


def test_page_must_be_claimed_before_completion(db: Database) -> None:
    db.register_pages(1, 1)
    with pytest.raises(PageStateError):
        db.complete_page(1, [record(1)], 100)
    assert db.record_count() == 0


# --- empty or partial pages never erase data ---------------------------------

def test_empty_or_partial_pages_are_refused(db: Database) -> None:
    complete(db, 1, [1, 2, 3])
    db.claim_page(2)
    with pytest.raises(IncompletePageError):
        db.complete_page(2, [], 0)
    with pytest.raises(IncompletePageError):
        db.complete_page(2, [record(150)], 57)
    assert db.page(2)["status"] == PROCESSING
    assert [r.number for r in db.iter_records()] == [1, 2, 3]


def test_last_page_expects_64_rows(db: Database) -> None:
    last_number = (TOTAL_PAGES - 1) * 100 + 64
    db.claim_page(TOTAL_PAGES)
    with pytest.raises(IncompletePageError):
        db.complete_page(TOTAL_PAGES, [], 100)
    assert db.complete_page(TOTAL_PAGES, [record(last_number)], 64) == 1


def test_registering_ranges_never_resets_progress(db: Database) -> None:
    complete(db, 2, [101])
    db.claim_page(3)
    db.fail_page(3, "boom")
    assert db.register_pages(1, 5) == 3
    assert db.page(2)["status"] == COMPLETED and db.page(3)["status"] == FAILED
    assert db.record_count() == 1


# --- recovery and retries ----------------------------------------------------

def test_recovery_after_simulated_interruption(db_path: Path) -> None:
    with Database(db_path) as first:
        first.register_pages(1, 3)
        complete(first, 1, [1])
        first.claim_page(2)
    with Database(db_path) as second:
        assert second.page(2)["status"] == PROCESSING
        assert second.recover_interrupted() == 1
        page = second.page(2)
        assert (page["status"], page["attempts"], page["last_error"]) == (PENDING, 1, "interrupted before completion")
        assert second.pages_to_process(1, 3) == [2, 3]
        assert second.record_count() == 1


def test_recovery_after_killed_process(db_path: Path) -> None:
    script = (
        "from pathlib import Path\n"
        "from scraper.database import Database\n"
        "from tests.html_fixtures import record\n"
        "from tests.platform_support import hard_kill\n"
        f"db = Database(Path({str(db_path)!r}))\n"
        "db.register_pages(1, 3)\n"
        "db.claim_page(1); db.complete_page(1, [record(1), record(2)], 100)\n"
        "db.claim_page(2)\n"
        "hard_kill()\n"
    )
    proc = subprocess.run([sys.executable, "-c", script], cwd=PROJECT_ROOT, capture_output=True, timeout=60,
                          env=utf8_child_env(PYTHONPATH=str(PROJECT_ROOT)))
    assert proc.returncode == KILLED_RETURNCODE, proc.stderr.decode("utf-8", "replace")
    with open_after_crash(db_path) as db:
        assert db.record_count() == 2
        assert db.page(1)["status"] == COMPLETED
        assert db.recover_interrupted() == 1
        assert db.pages_to_process(1, 3) == [2, 3]


def test_failed_page_is_retried_until_max_attempts(db: Database) -> None:
    db.register_pages(1, 2)
    for attempt in (1, 2, 3):
        assert db.claim_page(2) == attempt
        db.fail_page(2, f"error {attempt}")
        expected = [2, 1] if attempt < 3 else [1]
        assert db.pages_to_process(1, 2, max_attempts=3) == expected
    assert db.page(2)["last_error"] == "error 3"
    assert db.exhausted_pages(1, 2, max_attempts=3) == [2]
    assert db.pages_to_process(1, 2, max_attempts=4) == [2, 1]


def test_released_page_does_not_count_an_attempt(db: Database) -> None:
    db.register_pages(1, 1)
    db.claim_page(1)
    db.release_page(1)
    assert (db.page(1)["status"], db.page(1)["attempts"]) == (PENDING, 0)


def test_pages_to_process_prioritises_started_pages(db: Database) -> None:
    db.register_pages(1, 6)
    complete(db, 2, [101])
    db.claim_page(5)
    db.fail_page(5, "x")
    db.claim_page(3)
    db.recover_interrupted()
    assert db.pages_to_process(1, 6) == [3, 5, 1, 4, 6]
    assert db.pages_to_process(4, 6) == [5, 4, 6]


def test_fail_page_requires_processing(db: Database) -> None:
    db.register_pages(1, 1)
    with pytest.raises(PageStateError):
        db.fail_page(1, "not claimed")


# --- run range, ranges and locking -------------------------------------------

def test_run_range_persists(db_path: Path) -> None:
    with Database(db_path) as db:
        assert db.load_run_range() is None
        db.save_run_range(350000, 350500)
    with Database(db_path) as db:
        assert db.load_run_range() == (350000, 350500)


@pytest.mark.parametrize(("start", "end"), [(0, 5), (5, 4), (1, TOTAL_PAGES + 1), (-3, 2)])
def test_invalid_ranges_are_rejected(db: Database, start: int, end: int) -> None:
    with pytest.raises(InvalidPageRangeError):
        db.register_pages(start, end)
    with pytest.raises(InvalidPageRangeError):
        db.save_run_range(start, end)


def test_only_one_writer_at_a_time(db: Database, db_path: Path) -> None:
    with pytest.raises(DatabaseLockedError):
        Database(db_path)
    complete(db, 1, [1])
    with Database(db_path, readonly=True) as reader:
        assert reader.record_count() == 1
        with pytest.raises(PageStateError):
            reader.claim_page(2)
    db.close()
    with Database(db_path) as again:
        assert again.record_count() == 1


def test_readonly_requires_existing_database(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Database(tmp_path / "missing.db", readonly=True)
