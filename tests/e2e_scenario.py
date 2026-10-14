"""Shared 10-page scenario for the end-to-end recovery test, and the run that is interrupted on page 6.

Run as ``python -m tests.e2e_scenario DB MODE``: the real CLI processes pages 1..10 of a fake
site in a fake browser (no network) and is stopped while page 6 is in progress. MODE is
``kill-fetch`` (hard kill while page 6 loads), ``kill-commit`` (hard kill inside page 6's SQLite
transaction, after its records were inserted), ``ctrl-c`` (KeyboardInterrupt while page 6 loads) or
``kill-csv`` (hard kill while the automatic CSV is rewritten after page 6 was committed).
The hard kill is SIGKILL on Linux/macOS and TerminateProcess on Windows (tests.platform_support).
Before every listing navigation a ``SNAPSHOT {json}`` line reports what another connection sees
as committed in the database.
"""

from __future__ import annotations

import json
import socket
import sqlite3
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from scraper.pagination import page_segments
from scraper.parser import segment_range
from tests.html_fixtures import listing_html
from tests.platform_support import hard_kill

PAGES = range(1, 11)
INTERRUPTED_PAGE = 6
VERIFIED = {1: [9], 2: [101, 150], 3: [], 4: [301, 333, 399], 5: [450], 6: [501, 600], 7: [],
            8: [777], 9: [801, 850, 900], 10: [1000]}
# Status badges that must never be saved: wrong text, or the right word in the wrong form.
DECOYS = (
    ("bg-secondary", "Не проверено"),
    ("bg-warning", "Проверяется"),
    ("bg-success", "проверено"),
    ("bg-success", "Проверено частично"),
    ("bg-success", "Не проверено"),
)
MODES = ("kill-fetch", "kill-commit", "ctrl-c", "kill-csv")
SNAPSHOT = "SNAPSHOT "


def row(number: int, colour: str, text: str) -> str:
    start, end = segment_range(number)
    return (f'<tr class="tr_{number}" id="tag_search_{number}"><th>{number}</th><td>{start}</td><td>{end}</td>'
            f'<td class="comment">c</td><td><span class="badge {colour}">{text}</span></td>'
            f"<td>13.10.2026</td></tr>")


def scenario_page(page: int) -> str:
    """Listing page with the green "Проверено" badge on VERIFIED rows and a decoy status elsewhere."""
    verified = set(VERIFIED[page])
    return listing_html(row(n, "bg-success", "Проверено") if n in verified else row(n, *DECOYS[n % len(DECOYS)])
                        for n in page_segments(page))


def page_of(url: str) -> int:
    return int(url.rsplit("&page=", 1)[1]) if "&page=" in url else 1


def committed_state(db: Path) -> dict[str, Any]:
    """Page progress and records as seen by a separate read-only connection."""
    con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    try:
        pages = con.execute("SELECT page_number, status, attempts FROM page_progress ORDER BY page_number").fetchall()
        records = con.execute("SELECT number, source_page FROM verified_records ORDER BY number").fetchall()
    finally:
        con.close()
    return {"pages": [list(p) for p in pages], "records": [list(r) for r in records]}


def fast(run_live):
    """Wrap runner.run_live so the CLI's minimum delays do not slow the offline run down."""
    return lambda db, start, end, s, **kw: run_live(
        db, start, end, replace(s, page_delay_s=0, page_delay_jitter_s=0, captcha_poll_s=0.001), **kw)


def _refuse(*args: object, **kwargs: object) -> None:
    raise AssertionError("network access attempted")


def run_until_interrupted(db: Path, mode: str) -> int:
    import main as cli
    from scraper import browser
    from scraper.database import Database
    from tests.fake_playwright import BLANK_URL, FakePlaywright, ScriptedNav, factory

    socket.socket.connect = _refuse  # type: ignore[method-assign]
    socket.create_connection = _refuse  # type: ignore[assignment]
    time.sleep = lambda seconds: None

    fake = FakePlaywright()
    page = fake.browser.context.page
    page.script = [ScriptedNav(html=scenario_page(p)) for p in range(1, INTERRUPTED_PAGE + 1)]
    goto = page.goto

    def watched_goto(url: str, **kwargs: Any):
        if url != BLANK_URL:
            print(SNAPSHOT + json.dumps({"navigating_to": page_of(url), **committed_state(db)}), flush=True)
            if page_of(url) == INTERRUPTED_PAGE and mode == "kill-fetch":
                hard_kill()
            if page_of(url) == INTERRUPTED_PAGE and mode == "ctrl-c":
                raise KeyboardInterrupt
        return goto(url, **kwargs)

    page.goto = watched_goto  # type: ignore[method-assign]

    mark_completed = Database._mark_completed

    def dying_mark_completed(c: sqlite3.Connection, page_number: int, records_found: int, now: str) -> None:
        if page_number == INTERRUPTED_PAGE and mode == "kill-commit":
            hard_kill()
        mark_completed(c, page_number, records_found, now)

    Database._mark_completed = staticmethod(dying_mark_completed)  # type: ignore[method-assign]

    if mode == "kill-csv":
        from scraper import exporter

        real_writer = exporter.csv.writer

        class DyingWriter:
            def __init__(self, fh: Any) -> None:
                self.inner = real_writer(fh)

            def writerow(self, values: Any) -> None:
                if values[0] == VERIFIED[INTERRUPTED_PAGE][0]:
                    hard_kill()
                self.inner.writerow(values)

        exporter.csv.writer = DyingWriter  # type: ignore[assignment]
    session = browser.BrowserSession
    browser.BrowserSession = lambda settings: session(settings, factory(fake))  # type: ignore[misc]
    cli.run_live = fast(cli.run_live)
    return cli.main(["--db", str(db), "--start-page", str(PAGES[0]), "--end-page", str(PAGES[-1]),
                     "--live", "--headless", "--max-pages", str(len(PAGES))])


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[2] not in MODES:
        sys.exit(f"usage: python -m tests.e2e_scenario DB {{{','.join(MODES)}}}")
    sys.exit(run_until_interrupted(Path(sys.argv[1]), sys.argv[2]))
