"""Run planning and the page-by-page processing loop.

Range selection rules (also shown in ``main.py --help``):

* ``--start-page N --end-page M`` selects pages N..M and saves that as the run range.
  Completed pages inside it are always skipped, so re-running a range resumes it.
* ``--resume`` without a range reuses the last saved run range.
* ``--resume`` together with a range behaves exactly like the range alone.
* Pages left in "processing" by a crash are returned to "pending" on every start.
* Within the range, previously attempted pages (interrupted or failed) are processed
  first, then untouched pages in ascending order. Pages that already used
  ``max_attempts`` are skipped and reported.

The page fetcher is injected, so this module never talks to the network itself.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from scraper.config import MAX_PAGE_ATTEMPTS
from scraper.database import Database
from scraper.pagination import InvalidPageRangeError, validate_range
from scraper.parser import NOT_VERIFIED, SegmentRecord, parse_page

if TYPE_CHECKING:
    from scraper.browser import BrowserSettings, CaptchaSolver, CaptchaSummary
    from scraper.progress import ProgressReporter

logger = logging.getLogger(__name__)

FetchPage = Callable[[int], str]
PageSaved = Callable[[int, list[SegmentRecord]], None]


class PageValidationError(ValueError):
    """A page parsed, but some rows were rejected for reasons other than being unverified."""


class RunAbortedError(RuntimeError):
    """Too many consecutive page failures; the run stopped to avoid burning retries."""


class StopRunError(RuntimeError):
    """Raised by a fetcher when the whole run must stop (e.g. access restricted).

    The current page returns to pending without counting the attempt, because the
    failure says nothing about the page itself.
    """


@dataclass
class RunSummary:
    """Page counts for one run.

    ``planned`` pages were selected (at most ``max_pages``); ``attempted`` pages were
    actually fetched. Each attempted page ends completed, failed, or interrupted (a stop
    returned it to pending). Retries and reloads after a CAPTCHA are extra requests within
    the same page attempt: they add to ``requests`` but never to ``attempted``.
    """

    start_page: int
    end_page: int
    planned: int = 0
    attempted: int = 0
    completed: int = 0
    failed: int = 0
    interrupted: int = 0
    records_inserted: int = 0
    requests: int | None = None
    captcha: CaptchaSummary | None = None
    exhausted: list[int] = field(default_factory=list)
    stopped: str | None = None
    stopped_page: int | None = None

    @property
    def not_attempted(self) -> int:
        return self.planned - self.attempted


def plan_run(db: Database, start: int | None = None, end: int | None = None,
             resume: bool = False) -> tuple[int, int]:
    """Resolve the page range for this run, register its pages and save it as the run range."""
    if start is not None or end is not None:
        if start is None or end is None:
            raise InvalidPageRangeError("--start-page and --end-page must be given together")
        validate_range(start, end)
    elif resume:
        saved = db.load_run_range()
        if saved is None:
            raise InvalidPageRangeError("no saved run range to resume; give --start-page and --end-page")
        start, end = saved
    else:
        raise InvalidPageRangeError("select pages with --start-page/--end-page or --resume")
    db.register_pages(start, end)
    db.save_run_range(start, end)
    return start, end


def process_page(db: Database, page: int, fetch: FetchPage) -> list[SegmentRecord]:
    """Fetch, parse and commit one page; returns the records it newly stored.

    Any failure marks the page failed and re-raises. A deliberate stop (Ctrl+C or
    StopRunError) returns the page to pending without counting the attempt.
    """
    attempt = db.claim_page(page)
    logger.info("page %d: attempt %d", page, attempt)
    try:
        result = parse_page(fetch(page))
        anomalies = {k: v for k, v in result.reason_counts().items() if k != NOT_VERIFIED}
        if anomalies:
            raise PageValidationError(f"unexpected row rejections {anomalies}")
        known = db.stored_numbers(r.number for r in result.records)
        inserted = db.complete_page(page, result.records, result.rows_inspected)
    except (KeyboardInterrupt, SystemExit, StopRunError):
        db.release_page(page)
        raise
    except Exception as exc:
        db.fail_page(page, f"{type(exc).__name__}: {exc}")
        raise
    new = [r for r in result.records if r.number not in known]
    if len(new) != inserted:
        logger.warning("page %d: %d records reported as inserted, %d expected", page, inserted, len(new))
    logger.info("page %d: completed, %d verified, %d new", page, len(result.records), inserted)
    return new


def run_pages(db: Database, start: int, end: int, fetch: FetchPage, *,
              max_attempts: int = MAX_PAGE_ATTEMPTS, max_consecutive_failures: int = 3,
              max_pages: int | None = None, reporter: ProgressReporter | None = None,
              on_page_saved: PageSaved | None = None) -> RunSummary:
    """Process unfinished pages in the range; ``max_pages`` caps how many are attempted this run.

    ``on_page_saved(page, new_records)`` runs after each page is committed to SQLite. Its
    errors are logged and never undo or fail the committed page.
    """
    db.recover_interrupted()
    pages = db.pages_to_process(start, end, max_attempts)
    if max_pages is not None:
        pages = pages[:max_pages]
    summary = RunSummary(start, end, planned=len(pages))
    consecutive = 0
    for page in pages:
        if reporter:
            reporter.page_started(page)
        summary.attempted += 1
        try:
            new_records = process_page(db, page, fetch)
        except StopRunError as exc:
            if reporter:
                reporter.page_stopped(page)
            summary.interrupted += 1
            summary.stopped, summary.stopped_page = f"page {page}: {exc}", page
            logger.warning("run stopped at page %d: %s", page, exc)
            break
        except Exception as exc:  # noqa: BLE001 - page is already marked failed
            if reporter:
                reporter.page_finished(page, completed=False)
            summary.failed += 1
            consecutive += 1
            logger.warning("page %d failed: %s", page, exc)
            if consecutive >= max_consecutive_failures:
                if reporter:
                    reporter.run_ended()
                raise RunAbortedError(f"{consecutive} consecutive page failures; last on page {page}") from exc
            continue
        except BaseException:
            if reporter:
                reporter.page_stopped(page)
            raise
        summary.records_inserted += len(new_records)
        if reporter:
            reporter.page_finished(page, completed=True)
        summary.completed += 1
        consecutive = 0
        if on_page_saved:
            try:
                on_page_saved(page, new_records)
            except Exception as exc:  # noqa: BLE001 - the page is already safely committed
                logger.warning("page %d: saved in SQLite, but showing or exporting it failed: %s", page, exc)
    summary.exhausted = db.exhausted_pages(start, end, max_attempts)
    return summary


def run_live(db: Database, start: int, end: int, settings: BrowserSettings | None = None, *,
             max_pages: int, max_attempts: int = MAX_PAGE_ATTEMPTS,
             notify: Callable[[str], None] = print, reporter: ProgressReporter | None = None,
             solver: CaptchaSolver | None = None, on_page_saved: PageSaved | None = None) -> RunSummary:
    """Process pages with one Playwright browser context kept open for the whole run.

    ``solver`` solves image CAPTCHAs automatically; without it they are completed manually.
    After a CAPTCHA the requested page is loaded again and processed like any other page.
    The run's CAPTCHA counts end up in ``summary.captcha``, or in the log when the run is
    interrupted or aborted.
    """
    from scraper import browser  # browser imports StopRunError from this module

    settings = settings or browser.BrowserSettings()
    with browser.BrowserSession(settings) as session:
        fetcher = browser.ListingFetcher(session.page, settings, notify=notify, solver=solver)
        if reporter:
            reporter.excluded_time = lambda: fetcher.captcha_time_s
        try:
            summary = run_pages(db, start, end, fetcher, max_attempts=max_attempts, max_pages=max_pages,
                                reporter=reporter, on_page_saved=on_page_saved)
        except BaseException:
            logger.warning("run ended early; %s", fetcher.captcha_summary().describe())
            raise
        summary.requests = fetcher.navigations + fetcher.captcha_submissions
        summary.captcha = fetcher.captcha_summary()
        return summary
