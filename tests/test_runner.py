"""Processing loop tests with an offline fake site: saving, recovery, retries and ranges."""

from __future__ import annotations

from pathlib import Path

import pytest

from scraper.config import DATA_DIR, TOTAL_PAGES
from scraper.database import COMPLETED, FAILED, PENDING, Database
from scraper.pagination import InvalidPageRangeError
from scraper.runner import RunAbortedError, plan_run, process_page, run_pages
from tests.html_fixtures import CAPTCHA_PAGE, FakeSite, listing_html, listing_row, record

SAMPLE = DATA_DIR / "sample_page.html"


def numbers(db: Database) -> list[int]:
    return [r.number for r in db.iter_records()]


def test_each_page_is_saved_and_completed(db: Database) -> None:
    site = FakeSite(verified={1: [1, 2], 2: [150], 3: []})
    start, end = plan_run(db, 1, 3)
    summary = run_pages(db, start, end, site)
    assert (summary.planned, summary.completed, summary.failed, summary.records_inserted) == (3, 3, 0, 3)
    assert numbers(db) == [1, 2, 150]
    assert [db.page(p)["records_found"] for p in (1, 2, 3)] == [2, 1, 0]
    assert db.record_row(150)["source_page"] == 2


def test_interrupt_keeps_completed_pages_and_resumes(db_path: Path) -> None:
    site = FakeSite(verified={1: [1], 2: [101], 3: [201]}, scripted={3: [KeyboardInterrupt()]})
    with Database(db_path) as db:
        plan_run(db, 1, 4)
        with pytest.raises(KeyboardInterrupt):
            run_pages(db, 1, 4, site)
        assert numbers(db) == [1, 101]
        assert (db.page(3)["status"], db.page(3)["attempts"]) == (PENDING, 0)
    with Database(db_path) as db:
        start, end = plan_run(db, resume=True)
        site.calls.clear()
        run_pages(db, start, end, site)
        assert site.calls == [3, 4]
        assert numbers(db) == [1, 101, 201]


def test_crash_mid_page_is_recovered_without_repeating_completed(db_path: Path) -> None:
    site = FakeSite(verified={1: [1], 2: [101]})
    with Database(db_path) as db:
        plan_run(db, 1, 3)
        process_page(db, 1, site)
        db.claim_page(2)  # process dies here, leaving page 2 in "processing"
    with Database(db_path) as db:
        site.calls.clear()
        summary = run_pages(db, 1, 3, site)
        assert site.calls == [2, 3]
        assert summary.completed == 2
        assert db.page(2)["attempts"] == 2
        assert numbers(db) == [1, 101]


def test_failed_page_is_retried_on_next_run(db: Database) -> None:
    site = FakeSite(verified={2: [120]}, scripted={2: [TimeoutError("navigation timed out")]})
    plan_run(db, 1, 3)
    first = run_pages(db, 1, 3, site)
    assert (first.completed, first.failed) == (2, 1)
    assert db.page(2)["status"] == FAILED and "TimeoutError" in db.page(2)["last_error"]
    site.calls.clear()
    second = run_pages(db, 1, 3, site)
    assert site.calls == [2]
    assert second.completed == 1 and db.page(2)["status"] == COMPLETED
    assert numbers(db) == [120]


def test_pages_exceeding_max_attempts_are_skipped(db: Database) -> None:
    site = FakeSite(scripted={2: [CAPTCHA_PAGE] * 10})
    plan_run(db, 1, 3)
    for _ in range(3):
        summary = run_pages(db, 1, 3, site, max_attempts=3)
    assert summary.exhausted == [2]
    site.calls.clear()
    assert run_pages(db, 1, 3, site, max_attempts=3).planned == 0
    assert site.calls == []


def test_consecutive_failures_abort_the_run(db: Database) -> None:
    site = FakeSite(scripted={p: [CAPTCHA_PAGE] for p in range(1, 7)})
    plan_run(db, 1, 6)
    with pytest.raises(RunAbortedError):
        run_pages(db, 1, 6, site, max_consecutive_failures=3)
    assert site.calls == [1, 2, 3]
    assert [db.page(p)["status"] for p in (1, 2, 3)] == [FAILED] * 3
    assert all(db.page(p)["attempts"] == 0 for p in (4, 5, 6))


def test_resume_from_selected_page(db: Database) -> None:
    site = FakeSite(verified={350000: [34_999_901], 350002: [35_000_200]})
    start, end = plan_run(db, 350000, 350002)
    run_pages(db, start, end, site)
    assert site.calls == [350000, 350001, 350002]
    assert numbers(db) == [34_999_901, 35_000_200]
    assert db.load_run_range() == (350000, 350002)


def test_switching_ranges_preserves_saved_data(db: Database) -> None:
    site = FakeSite(verified={1: [1], 10: [901]})
    run_pages(db, *plan_run(db, 1, 2), site)
    run_pages(db, *plan_run(db, 10, 11), site)
    assert numbers(db) == [1, 901]
    assert plan_run(db, resume=True) == (10, 11)
    site.calls.clear()
    run_pages(db, *plan_run(db, 1, 11), site)
    assert site.calls == [3, 4, 5, 6, 7, 8, 9]


def test_resume_with_explicit_range_uses_the_range(db: Database) -> None:
    plan_run(db, 1, 5)
    assert plan_run(db, 20, 25, resume=True) == (20, 25)
    assert db.load_run_range() == (20, 25)


@pytest.mark.parametrize(
    ("start", "end", "resume"),
    [(5, None, False), (None, 5, False), (None, None, False), (None, None, True),
     (0, 10, False), (10, 9, False), (1, TOTAL_PAGES + 1, False)],
)
def test_invalid_run_selection(db: Database, start: int | None, end: int | None, resume: bool) -> None:
    with pytest.raises(InvalidPageRangeError):
        plan_run(db, start, end, resume)


def test_captcha_or_empty_pages_never_erase_records(db: Database) -> None:
    site = FakeSite(verified={1: [1, 2]})
    run_pages(db, *plan_run(db, 1, 1), site)
    empty_table = listing_html([])
    site.scripted = {2: [CAPTCHA_PAGE], 3: [empty_table]}
    summary = run_pages(db, *plan_run(db, 1, 3), site)
    assert summary.failed == 2
    assert "TableNotFoundError" in db.page(2)["last_error"]
    assert "IncompletePageError" in db.page(3)["last_error"]
    assert numbers(db) == [1, 2]
    assert db.page(1)["status"] == COMPLETED


def test_page_with_masked_or_invalid_rows_is_failed(db: Database) -> None:
    rows = [listing_row(n, verified=(n == 105), start="40***0068000000000" if n == 105 else None)
            for n in range(101, 201)]
    site = FakeSite(scripted={2: [listing_html(rows)]})
    plan_run(db, 2, 2)
    assert run_pages(db, 2, 2, site).failed == 1
    assert "masked_hex" in db.page(2)["last_error"]
    assert db.record_count() == 0


@pytest.mark.skipif(not SAMPLE.exists(), reason="saved sample page not available")
def test_saved_sample_as_page_one(db: Database) -> None:
    html = SAMPLE.read_text(encoding="utf-8")
    plan_run(db, 1, 1)
    summary = run_pages(db, 1, 1, lambda page: html)
    assert summary.records_inserted == 9
    assert numbers(db) == list(range(1, 10))
    assert {db.record_row(n)["source_page"] for n in range(1, 10)} == {1}
    assert list(db.iter_records())[0] == record(1)
