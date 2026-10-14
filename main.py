"""HonestPool Scraper — application entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from scraper.browser import BrowserSettings
from scraper.captcha_provider import (
    CaptchaConfigError,
    CaptchaProviderConfig,
    create_solver,
    load_captcha_config,
    withdraw_api_key,
)
from scraper.config import DATA_DIR, DB_PATH, MAX_PAGE_ATTEMPTS, TOTAL_PAGES
from scraper.database import Database, DatabaseLockedError, ImportOutcome
from scraper.exporter import EXPORT_SOURCES, SUPPORTED_FORMATS, export_from_db
from scraper.importer import ImportFileError, VerifiedFile, read_verified_file, store_import
from scraper.live_output import LiveOutput, auto_csv_path, xlsx_export_path
from scraper.pagination import InvalidPageRangeError, validate_range
from scraper.progress import ProgressReporter
from scraper.runner import RunAbortedError, RunSummary, plan_run, run_live

RANGE_RULES = f"""\
page selection:
  --start-page N --end-page M   process pages N..M (1..{TOTAL_PAGES:,}) and save it as the run range
  --resume                      continue the last saved run range
  --resume with a range         same as the range alone
  Completed pages are always skipped, saved records are never removed, and pages
  interrupted by a crash are returned to pending on every start. Within the range,
  interrupted/failed pages come first, then untouched pages in ascending order.
  There is no default range: a full {TOTAL_PAGES:,}-page run is never started implicitly.
  When the saved range is finished, select the next one explicitly with --start-page and
  --end-page; it replaces the saved run range, while pages and records of earlier ranges
  are kept and their completed pages are never fetched again.

live fetching:
  Pages are only fetched with --live, at most --max-pages per invocation (default 1),
  in one visible Chromium window. A CAPTCHA is sent to 2Captcha when configured and is
  otherwise completed manually (up to --max-manual-verifications per browser session);
  past that limit, or if the site answers 401/403/persistent 429, the run stops cleanly
  and the current page stays pending. Ctrl+C also stops cleanly; resume with --resume.
  A progress report is printed before each page; its time estimate is approximate, not
  guaranteed, includes the configured --delay/--jitter and excludes CAPTCHA waits.
  The final summary counts a page as attempted once it was fetched; retries and reloads
  after a CAPTCHA are extra requests within that same page attempt.

output files:
  The SQLite database (--db) is the authoritative copy of progress and records. After
  every completed page the new verified records are printed (Number, Start hex, End hex,
  Status) and verified_records.csv next to the database is rewritten from it by atomic
  replacement. The .xlsx file is only written on demand with --export xlsx.

examples:
  main.py --start-page 350000 --end-page 350100           plan the range, fetch nothing
  main.py --start-page 350000 --end-page 350100 --live    fetch 1 page of that range
  main.py --start-page 350000 --end-page 350100 --live --max-pages 101 --nav-timeout 60
                                                          fetch up to the whole range
  main.py --resume --live                                 continue the saved range (1 page)
  main.py --export xlsx                                   data/verified_records.xlsx
  main.py --export csv                                    data/verified_records.csv

offline import (optional, never contacts the website):
  Only for files from a trusted, authorized source. Imported numbers are stored as
  verified, so the operator must state where the file comes from (--source, or the prompt).
  .txt: one segment number per line. .csv: a column named Number (optional Start hex,
  End hex and Status columns must match). A file with any invalid value is rejected as a
  whole; duplicates are removed; numbers already stored are skipped, never overwritten.
  Imports are recorded separately and never mark website pages as completed.
  main.py --import-verified verified_numbers.txt
  main.py --import-verified verified_numbers.csv --source "list supplied by the operator"
"""

IMPORT_REFUSED = 4

MIN_PAGE_DELAY_S = 5.0
MIN_NAV_TIMEOUT_S = 5.0
MAX_NAV_TIMEOUT_S = 300.0
MAX_RETRIES = 5
MAX_MANUAL_VERIFICATIONS = 50


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="HonestPool verified-segment scraper.",
        epilog=RANGE_RULES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--start-page", type=int, metavar="N", help="first page to process")
    parser.add_argument("--end-page", type=int, metavar="M", help="last page to process (inclusive)")
    parser.add_argument("--resume", action="store_true", help="continue the last saved run range")
    parser.add_argument("--max-attempts", type=int, default=MAX_PAGE_ATTEMPTS, metavar="K",
                        help=f"skip pages that already failed K times (default {MAX_PAGE_ATTEMPTS})")
    parser.add_argument("--export", choices=SUPPORTED_FORMATS, help="export verified records from the database")
    parser.add_argument("--output", type=Path, help="export file path (default data/verified_records.<format>)")
    parser.add_argument("--export-source", choices=EXPORT_SOURCES, default="all",
                        help="records to export: website and imported (default), website only, or imported only")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite database (default {DB_PATH})")
    live = parser.add_argument_group("live fetching")
    live.add_argument("--live", action="store_true", help="fetch pages from the website with Playwright")
    live.add_argument("--max-pages", type=int, default=1, metavar="P",
                      help="maximum pages to fetch in this invocation (default 1)")
    live.add_argument("--delay", type=float, default=BrowserSettings.page_delay_s, metavar="S",
                      help=f"seconds between page navigations, minimum {MIN_PAGE_DELAY_S:g} "
                           f"(default {BrowserSettings.page_delay_s:g})")
    live.add_argument("--jitter", type=float, default=BrowserSettings.page_delay_jitter_s, metavar="S",
                      help=f"random extra delay 0..S seconds (default {BrowserSettings.page_delay_jitter_s:g})")
    live.add_argument("--captcha-wait", type=float, default=BrowserSettings.captcha_wait_s, metavar="S",
                      help="seconds to wait for manual CAPTCHA completion "
                           f"(default {BrowserSettings.captcha_wait_s:g})")
    live.add_argument("--max-manual-verifications", type=int, default=BrowserSettings.max_manual_verifications,
                      metavar="V", help="new CAPTCHAs that may be completed manually in one browser session, "
                                        f"1..{MAX_MANUAL_VERIFICATIONS} (default "
                                        f"{BrowserSettings.max_manual_verifications}); the run stops beyond that")
    live.add_argument("--nav-timeout", type=float, default=BrowserSettings.navigation_timeout_ms / 1000, metavar="S",
                      help=f"seconds to wait for each page navigation, {MIN_NAV_TIMEOUT_S:g}..{MAX_NAV_TIMEOUT_S:g} "
                           f"(default {BrowserSettings.navigation_timeout_ms / 1000:g})")
    live.add_argument("--retries", type=int, default=BrowserSettings.max_retries, metavar="R",
                      help=f"automatic retries per page after a timeout, connection error, 429 or 5xx, "
                           f"0..{MAX_RETRIES} (default {BrowserSettings.max_retries})")
    live.add_argument("--headless", action="store_true",
                      help="run without a window (a CAPTCHA then stops the run unless the configured "
                           "CAPTCHA provider solves it)")
    live.add_argument("--captcha-status", action="store_true",
                      help="check the CAPTCHA_PROVIDER, CAPTCHA_API_KEY and CAPTCHA_TIMEOUT settings and exit "
                           "without contacting any service; the key is never shown")
    offline = parser.add_argument_group("offline import")
    offline.add_argument("--import-verified", type=Path, metavar="FILE",
                         help="import verified segment numbers from a .txt or .csv file of an authorized source")
    offline.add_argument("--source", metavar="TEXT",
                         help="who supplied the import file; stating it confirms the source is trusted and "
                              "authorized (asked interactively when omitted)")
    return parser


def print_captcha_status(captcha: CaptchaProviderConfig) -> None:
    print(captcha.describe())
    for warning in captcha.warnings:
        print(f"warning: {warning}")


def ask_source(prompt: str) -> str | None:
    """Ask the operator to describe the authorized source; None when nobody can answer."""
    if not sys.stdin.isatty():
        return None
    try:
        return input(prompt)
    except EOFError:
        return None


def prepare_import(path: Path, source: str | None) -> tuple[VerifiedFile, str] | None:
    """Validate the file and obtain the authorized source before any database is opened."""
    try:
        verified = read_verified_file(path)
    except ImportFileError as exc:
        print(f"error: import refused, nothing was written: {exc}", file=sys.stderr)
        return None
    print(f"Import file: {verified.path.name} ({verified.file_format}, SHA-256 {verified.sha256})")
    print(f"Segment numbers: {verified.values_read:,} valid, {verified.duplicates:,} duplicate(s) removed, "
          f"{len(verified.numbers):,} unique")
    if source is None:
        print("Only import files from a trusted, authorized source: imported numbers are stored as verified.")
        source = ask_source("Describe the authorized source of this file (leave empty to cancel): ")
        if source is None:
            print("error: import refused, nothing was written: no source stated; use --source \"...\" "
                  "to name the authorized source", file=sys.stderr)
            return None
    if not source.strip():
        print("error: import refused, nothing was written: no authorized source was stated", file=sys.stderr)
        return None
    return verified, source.strip()


def report_import(db: Database, source: str, outcome: ImportOutcome) -> None:
    print(f"Imported {outcome.inserted:,} new verified record(s) from {source!r} (import batch "
          f"{outcome.batch_id}); skipped {outcome.already_scraped:,} already stored from the website and "
          f"{outcome.already_imported:,} already imported")
    print("Website page progress was not changed; imported records are stored separately from scraped "
          "records and are included in --export.")
    print(f"Verified records stored: {db.record_count():,} from the website, {db.imported_count():,} imported")


REQUESTS_NOTE = ("retries, CAPTCHA answer submissions and reloads after a CAPTCHA are extra requests within "
                 "the same page attempt; the database counts one attempt per page")


def page_breakdown(attempted: int, planned: int, interrupted: int) -> str:
    text = f"attempted {attempted} of {planned} planned pages"
    if interrupted:
        text += f"; {interrupted} stopped before completion and returned to pending"
    if planned > attempted:
        text += f"; {planned - attempted} not attempted"
    return text


def describe_run(summary: RunSummary, unfinished: int) -> str:
    return (f"Live run: {summary.completed} completed, {summary.failed} failed, "
            f"{summary.records_inserted} new verified records "
            f"({page_breakdown(summary.attempted, summary.planned, summary.interrupted)}; "
            f"{unfinished:,} unfinished pages in range at start)")


def describe_session(reporter: ProgressReporter, planned: int) -> str:
    return (f"This run: {reporter.completed} completed, {reporter.failed} failed "
            f"({page_breakdown(reporter.started, planned, reporter.interrupted)})")


def live_settings(args: argparse.Namespace) -> BrowserSettings:
    return replace(BrowserSettings(), headless=args.headless, page_delay_s=args.delay,
                   page_delay_jitter_s=args.jitter, captcha_wait_s=args.captcha_wait,
                   navigation_timeout_ms=round(args.nav_timeout * 1000), max_retries=args.retries,
                   max_manual_verifications=args.max_manual_verifications)


def describe_saved_progress(db: Database, start: int, end: int, stopped_page: int | None) -> str:
    counts = db.progress_counts(start, end)
    text = (f"Progress saved: {counts['completed']:,} of {end - start + 1:,} pages in range {start:,}..{end:,} "
            f"completed, {counts['pending']:,} pending")
    if counts["failed"]:
        text += f", {counts['failed']:,} failed (retried on resume)"
    if stopped_page is not None:
        text += f"; page {stopped_page:,} returned to pending without counting an attempt"
    return (text + ". Completed pages are not fetched again. Continue with: python main.py --resume --live "
            "(on Windows: option 2 of iniciar_honestpool.bat).")


def describe_outputs(db_path: Path) -> str:
    return "\n".join([
        f"Database (authoritative progress and records): {Path(db_path).resolve()}",
        f"CSV (rewritten automatically after every completed page): {auto_csv_path(db_path).resolve()}",
        f"Excel XLSX (on demand only: python main.py --export xlsx, or option 4 of iniciar_honestpool.bat): "
        f"{xlsx_export_path(db_path).resolve()}",
    ])


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; ``argv=None`` means no arguments (the environment check)."""
    parser = build_parser()
    args = parser.parse_args([] if argv is None else list(argv))
    wants_run = args.start_page is not None or args.end_page is not None or args.resume

    if args.live and not wants_run:
        parser.error("--live requires --start-page/--end-page or --resume")
    if args.max_attempts < 1:
        parser.error("--max-attempts must be at least 1")
    if args.output and not args.export:
        parser.error("--output requires --export")
    if args.export_source != "all" and not args.export:
        parser.error("--export-source requires --export")
    if args.source is not None and args.import_verified is None:
        parser.error("--source requires --import-verified")
    if args.import_verified is not None and (wants_run or args.live):
        parser.error("--import-verified is an offline step; run it without --live, --start-page/--end-page "
                     "or --resume")
    if args.max_pages < 1:
        parser.error("--max-pages must be at least 1")
    if args.delay < MIN_PAGE_DELAY_S or args.jitter < 0 or args.captcha_wait <= 0:
        parser.error(f"--delay must be >= {MIN_PAGE_DELAY_S:g}, --jitter >= 0 and --captcha-wait > 0")
    if not MIN_NAV_TIMEOUT_S <= args.nav_timeout <= MAX_NAV_TIMEOUT_S:
        parser.error(f"--nav-timeout must be between {MIN_NAV_TIMEOUT_S:g} and {MAX_NAV_TIMEOUT_S:g} seconds")
    if not 0 <= args.retries <= MAX_RETRIES:
        parser.error(f"--retries must be between 0 and {MAX_RETRIES}")
    if not 1 <= args.max_manual_verifications <= MAX_MANUAL_VERIFICATIONS:
        parser.error(f"--max-manual-verifications must be between 1 and {MAX_MANUAL_VERIFICATIONS}")
    if args.start_page is not None or args.end_page is not None:
        if args.start_page is None or args.end_page is None:
            parser.error("--start-page and --end-page must be given together")
        try:
            validate_range(args.start_page, args.end_page)
        except InvalidPageRangeError as exc:
            parser.error(str(exc))
    captcha = solver = None
    if args.live or args.captcha_status:
        try:
            captcha = load_captcha_config()
        except CaptchaConfigError as exc:
            parser.error(f"invalid CAPTCHA configuration: {exc}")
        solver = create_solver(captcha) if args.live else None
        withdraw_api_key()
    if args.captcha_status:
        print_captcha_status(captcha)
        if not (wants_run or args.export or args.import_verified):
            return 0
    if not wants_run and not args.export and args.import_verified is None:
        print("HonestPool Scraper - Environment Ready")
        print("Use --help for page selection and export options.")
        return 0

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    prepared = None
    if args.import_verified is not None:
        prepared = prepare_import(args.import_verified, args.source)
        if prepared is None:
            return IMPORT_REFUSED
    try:
        db = Database(args.db, readonly=not (wants_run or prepared is not None))
    except (DatabaseLockedError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    exit_code = 0
    with db:
        if prepared:
            verified, source = prepared
            report_import(db, source, store_import(db, verified, source))
        if wants_run:
            try:
                recovered = db.recover_interrupted()
                start, end = plan_run(db, args.start_page, args.end_page, args.resume)
            except InvalidPageRangeError as exc:
                parser.error(str(exc))
            pending = db.pages_to_process(start, end, args.max_attempts)
            exhausted = db.exhausted_pages(start, end, args.max_attempts)
            counts = db.progress_counts(start, end)
            print(f"Run range: pages {start:,}..{end:,} ({end - start + 1:,} pages)")
            print(f"Recovered interrupted pages: {recovered}")
            print(f"Progress in range: {counts}")
            print(f"Pages to process: {len(pending):,}" + (f" (next: {pending[:10]})" if pending else ""))
            if exhausted:
                print(f"Skipped after {args.max_attempts} failed attempts: {len(exhausted):,} {exhausted[:10]}")
            print(f"Verified records stored: {db.record_count():,}")
            if not args.live:
                print("Planning only: add --live to fetch pages; no pages were fetched.")
            elif pending:
                if not args.captcha_status:
                    print_captcha_status(captcha)
                reporter = ProgressReporter(db, start, end, max_attempts=args.max_attempts,
                                            page_limit=args.max_pages, delay_s=args.delay, jitter_s=args.jitter)
                planned = min(len(pending), args.max_pages)
                output = LiveOutput(db, auto_csv_path(args.db))
                try:
                    output.start()
                    summary = run_live(db, start, end, live_settings(args), max_pages=args.max_pages,
                                       max_attempts=args.max_attempts, reporter=reporter,
                                       solver=solver, on_page_saved=output.page_saved)
                except RunAbortedError as exc:
                    print(reporter.render())
                    print(describe_session(reporter, planned))
                    print(f"Run aborted: {exc}. Failed pages stay recoverable with --resume.")
                    print(describe_saved_progress(db, start, end, None))
                    print(describe_outputs(args.db))
                    return 3
                except KeyboardInterrupt:
                    print(reporter.render())
                    print(describe_session(reporter, planned))
                    print("Interrupted: the current page was returned to pending and saved records are kept. "
                          "Continue with --resume --live.")
                    print(describe_outputs(args.db))
                    return 130
                print(reporter.render())
                print(describe_run(summary, len(pending)))
                if summary.requests is not None:
                    print(f"Page requests sent: {summary.requests} ({REQUESTS_NOTE})")
                if summary.captcha is not None:
                    print(summary.captcha.describe())
                print(f"Verified records stored: {db.record_count():,}")
                if summary.stopped:
                    print(f"Run stopped: {summary.stopped}")
                    print(describe_saved_progress(db, start, end, summary.stopped_page))
                    exit_code = 3
                print(describe_outputs(args.db))

        if args.export:
            output = args.output or DATA_DIR / f"verified_records.{args.export}"
            if output.suffix.lower() != f".{args.export}":
                parser.error(f"--output must end in .{args.export}")
            try:
                count = export_from_db(db, output, args.export_source)
            except OSError as exc:
                print(f"error: could not write {output} ({type(exc).__name__}: {exc}); close the file if it is "
                      f"open in Excel or another program and export again. The database was not changed.",
                      file=sys.stderr)
                return 1
            print(f"Exported {count:,} verified records to {output}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
