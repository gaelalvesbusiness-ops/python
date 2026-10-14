"""What a live run shows and saves after every completed page.

SQLite is the authoritative copy: a page's records and its completed status are
committed together before anything here runs. Afterwards the newly stored records
are printed as a table and the CSV export is rewritten from SQLite with an atomic
file replacement, so it always holds every stored verified record exactly once and
an interruption leaves either the previous or the new complete file, never a partial
one. If the CSV cannot be written (for example while Excel has it open on Windows),
the run continues and the next completed page, the next run or ``--export csv``
writes it again. The XLSX export is only produced on demand (``--export xlsx``).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from scraper.config import AUTO_CSV_NAME, XLSX_EXPORT_NAME
from scraper.database import Database
from scraper.exporter import export_from_db, remove_stale_temporaries
from scraper.parser import SegmentRecord

logger = logging.getLogger(__name__)

SLOW_CSV_WRITE_S = 5.0


def auto_csv_path(db_path: Path) -> Path:
    """The CSV that live runs keep up to date, next to the database."""
    return Path(db_path).parent / AUTO_CSV_NAME


def xlsx_export_path(db_path: Path) -> Path:
    """Where ``--export xlsx`` writes by default, next to the database."""
    return Path(db_path).parent / XLSX_EXPORT_NAME


def format_new_records(page: int, records: Sequence[SegmentRecord]) -> str:
    """A table of the records a page newly stored, in the export's column order."""
    if not records:
        return f"Page {page:,}: no new verified records."
    width = max(len("Number"), *(len(str(r.number)) for r in records))
    hex_width = max(len("Start hex"), *(len(r.start_hex) for r in records))
    lines = [f"Page {page:,}: {len(records)} new verified record(s) saved:",
             f"  {'Number':>{width}}  {'Start hex':<{hex_width}}  {'End hex':<{hex_width}}  Status"]
    lines += [f"  {r.number:>{width}}  {r.start_hex:<{hex_width}}  {r.end_hex:<{hex_width}}  {r.status}"
              for r in records]
    return "\n".join(lines)


class LiveOutput:
    """Prints newly stored records and keeps ``csv_path`` in step with SQLite."""

    def __init__(self, db: Database, csv_path: Path, *, notify: Callable[[str], None] = print,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.db, self.csv_path = db, Path(csv_path)
        self.notify, self._clock = notify, clock
        self.written = 0
        self.failures = 0

    def start(self) -> None:
        """Before the first page: remove leftovers of an interrupted write and catch up with SQLite."""
        removed = remove_stale_temporaries(self.csv_path)
        if removed:
            logger.info("removed %d temporary file(s) left by an interrupted CSV write", removed)
        count = self._write()
        if count is not None:
            self.notify(f"CSV synchronized with the database: {count:,} verified record(s) in {self.csv_path} "
                        f"(rewritten automatically after every completed page).")

    def page_saved(self, page: int, records: list[SegmentRecord]) -> None:
        self.notify(format_new_records(page, records))
        count = self._write()
        if count is not None:
            self.notify(f"CSV updated: {self.csv_path} ({count:,} verified record(s)).")

    def _write(self) -> int | None:
        started = self._clock()
        try:
            count = export_from_db(self.db, self.csv_path, "all")
        except OSError as exc:
            self.failures += 1
            logger.warning("CSV not updated (%s): %s", self.csv_path, exc)
            self.notify(f"WARNING: the CSV could not be updated ({type(exc).__name__}: {exc}). The records are safe "
                        f"in the database; the CSV is written again after the next completed page or with the "
                        f"CSV export. Close {self.csv_path.name} if it is open in Excel or another program.")
            return None
        elapsed = self._clock() - started
        if elapsed >= SLOW_CSV_WRITE_S:
            logger.warning("rewriting the CSV with %d records took %.1fs", count, elapsed)
        self.written += 1
        return count
