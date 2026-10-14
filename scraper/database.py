"""SQLite persistence for verified records and per-page progress.

A page moves pending -> processing -> completed | failed. Its records and its
"completed" mark are written in one transaction, so a crash can never leave a page
marked completed without its records (or records without the mark). Pages found in
"processing" at startup were interrupted and are returned to "pending".

Only one writer may open the database at a time; this is enforced with an exclusive
``flock`` on ``<db>.lock`` (``msvcrt.locking`` on Windows), which the OS releases
automatically if the process dies.
"""

from __future__ import annotations

import errno
import logging
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

from scraper.config import DB_PATH, HEX_WIDTH, MAX_PAGE_ATTEMPTS, SEGMENT_COUNT, TOTAL_PAGES
from scraper.pagination import page_for_segment, page_segments, validate_page, validate_range
from scraper.parser import VERIFIED_TEXT, SegmentRecord, segment_range

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt

logger = logging.getLogger(__name__)

_WINDOWS_LOCK_ERRNOS = (errno.EACCES, errno.EDEADLK)


def _lock_exclusive(handle: IO[str]) -> None:
    """Take the writer lock without waiting; raises BlockingIOError when another process holds it."""
    if fcntl is not None:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        if exc.errno in _WINDOWS_LOCK_ERRNOS:
            raise BlockingIOError(exc.errno, "lock is held by another process") from None
        raise


def _unlock(handle: IO[str]) -> None:
    if fcntl is not None:
        fcntl.flock(handle, fcntl.LOCK_UN)
        return
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

SCHEMA_VERSION = 1
PENDING, PROCESSING, COMPLETED, FAILED = "pending", "processing", "completed", "failed"

SCHEMA_STATEMENTS = (
    f"""CREATE TABLE IF NOT EXISTS verified_records (
        number       INTEGER PRIMARY KEY CHECK (number BETWEEN 1 AND {SEGMENT_COUNT}),
        start_hex    TEXT NOT NULL CHECK (length(start_hex) = {HEX_WIDTH}),
        end_hex      TEXT NOT NULL CHECK (length(end_hex) = {HEX_WIDTH}),
        status       TEXT NOT NULL CHECK (status = '{VERIFIED_TEXT}'),
        source_page  INTEGER NOT NULL CHECK (source_page BETWEEN 1 AND {TOTAL_PAGES}),
        extracted_at TEXT
    )""",
    f"""CREATE TABLE IF NOT EXISTS page_progress (
        page_number   INTEGER PRIMARY KEY CHECK (page_number BETWEEN 1 AND {TOTAL_PAGES}),
        status        TEXT NOT NULL DEFAULT '{PENDING}'
                      CHECK (status IN ('{PENDING}', '{PROCESSING}', '{COMPLETED}', '{FAILED}')),
        attempts      INTEGER NOT NULL DEFAULT 0,
        records_found INTEGER,
        last_error    TEXT,
        updated_at    TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_page_progress_status ON page_progress (status, page_number)",
    "CREATE TABLE IF NOT EXISTS run_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
)

# Optional offline import from an authorized source. Created on the first import only, kept apart
# from website data (verified_records/page_progress), and ignored by code that does not know it.
IMPORT_FORMATS = ("txt", "csv")
IMPORT_SCHEMA_STATEMENTS = (
    f"""CREATE TABLE IF NOT EXISTS import_batches (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        source           TEXT NOT NULL CHECK (length(trim(source)) > 0),
        file_name        TEXT NOT NULL,
        file_sha256      TEXT NOT NULL CHECK (length(file_sha256) = 64),
        file_format      TEXT NOT NULL CHECK (file_format IN ({", ".join(f"'{f}'" for f in IMPORT_FORMATS)})),
        numbers_in_file  INTEGER NOT NULL,
        duplicates       INTEGER NOT NULL,
        inserted         INTEGER NOT NULL,
        already_scraped  INTEGER NOT NULL,
        already_imported INTEGER NOT NULL,
        imported_at      TEXT NOT NULL
    )""",
    f"""CREATE TABLE IF NOT EXISTS imported_records (
        number      INTEGER PRIMARY KEY CHECK (number BETWEEN 1 AND {SEGMENT_COUNT}),
        start_hex   TEXT NOT NULL CHECK (length(start_hex) = {HEX_WIDTH}),
        end_hex     TEXT NOT NULL CHECK (length(end_hex) = {HEX_WIDTH}),
        status      TEXT NOT NULL CHECK (status = '{VERIFIED_TEXT}'),
        import_id   INTEGER NOT NULL REFERENCES import_batches (id),
        imported_at TEXT NOT NULL
    )""",
)
WEBSITE, IMPORTED = "website", "import"


@dataclass(frozen=True)
class ImportOutcome:
    batch_id: int
    inserted: int
    already_scraped: int
    already_imported: int


class DatabaseLockedError(RuntimeError):
    """Another process already has the database open for writing."""


class PageStateError(RuntimeError):
    """A page lifecycle transition was requested from the wrong state."""


class InvalidRecordError(ValueError):
    """A record failed validation and was not stored."""


class IncompletePageError(ValueError):
    """A parsed page did not contain the expected number of listing rows."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path = DB_PATH, *, readonly: bool = False, busy_timeout: float = 30.0) -> None:
        self.path = Path(path)
        self.readonly = readonly
        self._lock_file: IO[str] | None = None
        if readonly:
            if not self.path.exists():
                raise FileNotFoundError(f"database {self.path} does not exist")
            uri = f"{self.path.resolve().as_uri()}?mode=ro"
            self.conn = sqlite3.connect(uri, uri=True, timeout=busy_timeout, isolation_level=None)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._acquire_writer_lock()
            try:
                self.conn = sqlite3.connect(self.path, timeout=busy_timeout, isolation_level=None)
                self.conn.execute("PRAGMA journal_mode=WAL")
                self.conn.execute("PRAGMA synchronous=FULL")
                self._init_schema()
            except BaseException:
                self._release_writer_lock()
                raise
        self.conn.row_factory = sqlite3.Row

    # --- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        try:
            self.conn.close()
        finally:
            self._release_writer_lock()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _acquire_writer_lock(self) -> None:
        lock_path = self.path.with_name(self.path.name + ".lock")
        handle = open(lock_path, "a+", encoding="utf-8")
        try:
            _lock_exclusive(handle)
        except BlockingIOError:
            handle.close()
            raise DatabaseLockedError(f"another process is writing to {self.path} (lock {lock_path})") from None
        self._lock_file = handle

    def _release_writer_lock(self) -> None:
        if self._lock_file is not None:
            _unlock(self._lock_file)
            self._lock_file.close()
            self._lock_file = None

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        if self.readonly:
            raise PageStateError("database was opened read-only")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
            self.conn.execute("COMMIT")
        except BaseException:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    def _init_schema(self) -> None:
        with self._write() as c:
            version = c.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"database schema v{version} is newer than supported v{SCHEMA_VERSION}")
            for statement in SCHEMA_STATEMENTS:
                c.execute(statement)
            c.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    # --- run range -----------------------------------------------------------

    def save_run_range(self, start: int, end: int) -> None:
        validate_range(start, end)
        with self._write() as c:
            c.executemany("INSERT INTO run_state (key, value) VALUES (?, ?) "
                          "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                          (("run_start_page", str(start)), ("run_end_page", str(end))))

    def load_run_range(self) -> tuple[int, int] | None:
        rows = dict(self.conn.execute(
            "SELECT key, value FROM run_state WHERE key IN ('run_start_page', 'run_end_page')").fetchall())
        if len(rows) != 2:
            return None
        return int(rows["run_start_page"]), int(rows["run_end_page"])

    # --- page lifecycle ------------------------------------------------------

    def register_pages(self, start: int, end: int) -> int:
        """Add missing pages in the range as pending; existing progress is left untouched."""
        validate_range(start, end)
        now = utc_now()
        with self._write() as c:
            before = c.total_changes
            c.executemany(
                f"INSERT OR IGNORE INTO page_progress (page_number, status, attempts, updated_at) "
                f"VALUES (?, '{PENDING}', 0, ?)",
                ((page, now) for page in range(start, end + 1)),
            )
            return c.total_changes - before

    def recover_interrupted(self) -> int:
        """Return pages left in "processing" by a crash to "pending"; the attempt stays counted."""
        with self._write() as c:
            cur = c.execute(
                f"UPDATE page_progress SET status = '{PENDING}', last_error = ?, updated_at = ? "
                f"WHERE status = '{PROCESSING}'",
                ("interrupted before completion", utc_now()),
            )
        if cur.rowcount:
            logger.warning("recovered %d interrupted page(s)", cur.rowcount)
        return cur.rowcount

    def claim_page(self, page: int) -> int:
        """Mark a page as processing and return its attempt number."""
        validate_page(page)
        now = utc_now()
        with self._write() as c:
            status = self._page_status(c, page)
            if status is None:
                c.execute(f"INSERT INTO page_progress (page_number, status, attempts, updated_at) "
                          f"VALUES (?, '{PROCESSING}', 1, ?)", (page, now))
                return 1
            if status in (COMPLETED, PROCESSING):
                raise PageStateError(f"page {page} is {status} and cannot be claimed")
            c.execute(f"UPDATE page_progress SET status = '{PROCESSING}', attempts = attempts + 1, "
                      f"updated_at = ? WHERE page_number = ?", (now, page))
            return c.execute("SELECT attempts FROM page_progress WHERE page_number = ?", (page,)).fetchone()[0]

    def complete_page(self, page: int, records: Iterable[SegmentRecord], rows_inspected: int) -> int:
        """Store a page's verified records and mark it completed atomically.

        Returns the number of newly inserted records. Existing records are never
        overwritten or deleted.
        """
        validate_page(page)
        records = list(records)
        expected_rows = len(page_segments(page))
        if rows_inspected != expected_rows:
            raise IncompletePageError(f"page {page}: {rows_inspected} listing rows, expected {expected_rows}")
        for record in records:
            self._validate_record(page, record)
        now = utc_now()
        with self._write() as c:
            status = self._page_status(c, page)
            if status != PROCESSING:
                raise PageStateError(f"page {page} is {status}, expected {PROCESSING}")
            before = c.total_changes
            c.executemany(
                "INSERT INTO verified_records (number, start_hex, end_hex, status, source_page, extracted_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(number) DO NOTHING",
                ((r.number, r.start_hex, r.end_hex, r.status, page, now) for r in records),
            )
            inserted = c.total_changes - before
            self._mark_completed(c, page, len({r.number for r in records}), now)
        return inserted

    def fail_page(self, page: int, error: str) -> None:
        with self._write() as c:
            cur = c.execute(
                f"UPDATE page_progress SET status = '{FAILED}', last_error = ?, updated_at = ? "
                f"WHERE page_number = ? AND status = '{PROCESSING}'",
                (error[:1000], utc_now(), page),
            )
            if cur.rowcount != 1:
                raise PageStateError(f"page {page} is not processing; cannot mark failed")

    def release_page(self, page: int) -> None:
        """Return a processing page to pending after a deliberate stop, without counting the attempt."""
        with self._write() as c:
            c.execute(
                f"UPDATE page_progress SET status = '{PENDING}', attempts = MAX(attempts - 1, 0), "
                f"last_error = ?, updated_at = ? WHERE page_number = ? AND status = '{PROCESSING}'",
                ("stopped before completion", utc_now(), page),
            )

    def pages_to_process(self, start: int, end: int, max_attempts: int = MAX_PAGE_ATTEMPTS) -> list[int]:
        """Unfinished pages in the range: previously attempted ones first, then untouched pages, ascending."""
        validate_range(start, end)
        rows = self.conn.execute(
            f"SELECT page_number FROM page_progress WHERE page_number BETWEEN ? AND ? "
            f"AND status IN ('{PENDING}', '{FAILED}') AND attempts < ? "
            f"ORDER BY attempts = 0, page_number",
            (start, end, max_attempts),
        ).fetchall()
        return [r[0] for r in rows]

    def exhausted_pages(self, start: int, end: int, max_attempts: int = MAX_PAGE_ATTEMPTS) -> list[int]:
        validate_range(start, end)
        rows = self.conn.execute(
            f"SELECT page_number FROM page_progress WHERE page_number BETWEEN ? AND ? "
            f"AND status IN ('{PENDING}', '{FAILED}') AND attempts >= ? ORDER BY page_number",
            (start, end, max_attempts),
        ).fetchall()
        return [r[0] for r in rows]

    # --- queries -------------------------------------------------------------

    def page(self, page: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM page_progress WHERE page_number = ?", (page,)).fetchone()
        return dict(row) if row else None

    def progress_counts(self, start: int | None = None, end: int | None = None) -> dict[str, int]:
        sql, params = "SELECT status, COUNT(*) FROM page_progress", ()
        if start is not None and end is not None:
            sql, params = sql + " WHERE page_number BETWEEN ? AND ?", (start, end)
        counts = {PENDING: 0, PROCESSING: 0, COMPLETED: 0, FAILED: 0}
        counts.update(dict(self.conn.execute(sql + " GROUP BY status", params).fetchall()))
        return counts

    def remaining_count(self, start: int, end: int, max_attempts: int = MAX_PAGE_ATTEMPTS) -> int:
        """Number of pages ``pages_to_process`` would return, without materialising the list."""
        validate_range(start, end)
        return self.conn.execute(
            f"SELECT COUNT(*) FROM page_progress WHERE page_number BETWEEN ? AND ? "
            f"AND status IN ('{PENDING}', '{FAILED}') AND attempts < ?",
            (start, end, max_attempts),
        ).fetchone()[0]

    def last_completed_page(self, start: int, end: int) -> int | None:
        """The most recently completed page in the range, or None."""
        validate_range(start, end)
        row = self.conn.execute(
            f"SELECT page_number FROM page_progress WHERE page_number BETWEEN ? AND ? "
            f"AND status = '{COMPLETED}' ORDER BY updated_at DESC, page_number DESC LIMIT 1",
            (start, end),
        ).fetchone()
        return row[0] if row else None

    def record_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM verified_records").fetchone()[0]

    def stored_numbers(self, numbers: Iterable[int]) -> set[int]:
        """Which of ``numbers`` already have a website record (read-only)."""
        numbers = list(numbers)
        if not numbers:
            return set()
        placeholders = ",".join("?" * len(numbers))
        cur = self.conn.execute(f"SELECT number FROM verified_records WHERE number IN ({placeholders})", numbers)
        return {row[0] for row in cur}

    def iter_records(self) -> Iterator[SegmentRecord]:
        cur = self.conn.execute("SELECT number, start_hex, end_hex, status FROM verified_records ORDER BY number")
        for row in cur:
            yield SegmentRecord(row["number"], row["start_hex"], row["end_hex"], row["status"])

    def record_row(self, number: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM verified_records WHERE number = ?", (number,)).fetchone()
        return dict(row) if row else None

    # --- offline import (authorized source) -----------------------------------

    def import_verified(self, records: Iterable[SegmentRecord], *, source: str, file_name: str,
                        file_sha256: str, file_format: str, duplicates: int) -> ImportOutcome:
        """Store imported verified records and their provenance in one transaction.

        Numbers already stored from the website or by an earlier import are skipped, never
        overwritten. Website records and page progress are not touched.
        """
        records = list(records)
        for record in records:
            self._validate_values(record)
        if len({r.number for r in records}) != len(records):
            raise InvalidRecordError("an import batch must not contain the same number twice")
        if not source.strip():
            raise InvalidRecordError("the authorized import source must be described")
        now = utc_now()
        with self._write() as c:
            for statement in IMPORT_SCHEMA_STATEMENTS:
                c.execute(statement)
            new, scraped, imported = [], 0, 0
            for r in records:
                if c.execute("SELECT 1 FROM verified_records WHERE number = ?", (r.number,)).fetchone():
                    scraped += 1
                elif c.execute("SELECT 1 FROM imported_records WHERE number = ?", (r.number,)).fetchone():
                    imported += 1
                else:
                    new.append(r)
            batch_id = c.execute(
                "INSERT INTO import_batches (source, file_name, file_sha256, file_format, numbers_in_file, "
                "duplicates, inserted, already_scraped, already_imported, imported_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (source.strip(), file_name, file_sha256, file_format, len(records), duplicates, len(new),
                 scraped, imported, now),
            ).lastrowid
            c.executemany(
                "INSERT INTO imported_records (number, start_hex, end_hex, status, import_id, imported_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ((r.number, r.start_hex, r.end_hex, r.status, batch_id, now) for r in new),
            )
        return ImportOutcome(batch_id, len(new), scraped, imported)

    def has_imports(self) -> bool:
        return self.conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
            "AND name IN ('import_batches', 'imported_records')").fetchone()[0] == 2

    def imported_count(self) -> int:
        if not self.has_imports():
            return 0
        return self.conn.execute("SELECT COUNT(*) FROM imported_records").fetchone()[0]

    def iter_imported_records(self) -> Iterator[SegmentRecord]:
        if not self.has_imports():
            return
        cur = self.conn.execute("SELECT number, start_hex, end_hex, status FROM imported_records ORDER BY number")
        for row in cur:
            yield SegmentRecord(row["number"], row["start_hex"], row["end_hex"], row["status"])

    def iter_all_records(self) -> Iterator[SegmentRecord]:
        """Website and imported records by Number; a number stored from the website is listed once, as such."""
        if not self.has_imports():
            yield from self.iter_records()
            return
        cur = self.conn.execute(
            "SELECT number, start_hex, end_hex, status FROM verified_records UNION ALL "
            "SELECT number, start_hex, end_hex, status FROM imported_records "
            "WHERE number NOT IN (SELECT number FROM verified_records) ORDER BY number")
        for row in cur:
            yield SegmentRecord(row["number"], row["start_hex"], row["end_hex"], row["status"])

    def provenance(self, number: int) -> str | None:
        """WEBSITE when the number was scraped, IMPORTED when it only came from an import, else None."""
        if self.record_row(number):
            return WEBSITE
        return IMPORTED if self.imported_row(number) else None

    def imported_row(self, number: int) -> dict[str, Any] | None:
        if not self.has_imports():
            return None
        row = self.conn.execute("SELECT * FROM imported_records WHERE number = ?", (number,)).fetchone()
        return dict(row) if row else None

    def import_batches(self) -> list[dict[str, Any]]:
        if not self.has_imports():
            return []
        return [dict(r) for r in self.conn.execute("SELECT * FROM import_batches ORDER BY id")]

    # --- internals -----------------------------------------------------------

    @staticmethod
    def _page_status(c: sqlite3.Connection, page: int) -> str | None:
        row = c.execute("SELECT status FROM page_progress WHERE page_number = ?", (page,)).fetchone()
        return row[0] if row else None

    @staticmethod
    def _mark_completed(c: sqlite3.Connection, page: int, records_found: int, now: str) -> None:
        c.execute(
            f"UPDATE page_progress SET status = '{COMPLETED}', records_found = ?, last_error = NULL, "
            f"updated_at = ? WHERE page_number = ?",
            (records_found, now, page),
        )

    @staticmethod
    def _validate_record(page: int, record: SegmentRecord) -> None:
        Database._validate_values(record)
        if page_for_segment(record.number) != page:
            raise InvalidRecordError(f"segment {record.number} does not belong to page {page}")

    @staticmethod
    def _validate_values(record: SegmentRecord) -> None:
        if not isinstance(record, SegmentRecord):
            raise InvalidRecordError(f"expected SegmentRecord, got {type(record).__name__}")
        if record.status != VERIFIED_TEXT:
            raise InvalidRecordError(f"segment {record.number}: status {record.status!r} is not verified")
        try:
            expected = segment_range(record.number)
        except (TypeError, ValueError) as exc:
            raise InvalidRecordError(f"invalid segment number {record.number!r}: {exc}") from None
        if (record.start_hex, record.end_hex) != expected:
            raise InvalidRecordError(f"segment {record.number}: hex range does not match {expected}")
