"""Multi-page interruption and recovery scenarios through the live fetcher stack, fully offline."""

from __future__ import annotations

import random
from pathlib import Path

from scraper.browser import BrowserSettings, ListingFetcher
from scraper.config import DEFAULT_BASE_URL, TOTAL_PAGES
from scraper.database import COMPLETED, FAILED, PENDING, PROCESSING, Database
from scraper.pagination import page_segments, page_url
from scraper.runner import plan_run, process_page, run_pages
from tests.fake_playwright import FakePage, captcha, success
from tests.html_fixtures import FakeSite, listing_page

PAGE_ONE = list(range(1, 10))


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def live_fetcher(page: FakePage, clock: Clock, on_sleep=None) -> ListingFetcher:
    def sleep(seconds: float) -> None:
        clock.now += seconds
        if on_sleep:
            on_sleep()

    settings = BrowserSettings(page_delay_s=15, page_delay_jitter_s=5, captcha_wait_s=600, captcha_poll_s=2)
    return ListingFetcher(page, settings, notify=lambda _: None, sleep=sleep, clock=clock, rng=random.Random(0))


def seed_page_one(db: Database) -> str:
    plan_run(db, 1, 1)
    process_page(db, 1, FakeSite(verified={1: PAGE_ONE}))
    return db.record_row(1)["extracted_at"]


def numbers(db: Database) -> list[int]:
    return [r.number for r in db.iter_records()]


def test_second_captcha_stop_then_resume_completes_remaining_pages(db_path: Path) -> None:
    url2, url3 = page_url(DEFAULT_BASE_URL, 2), page_url(DEFAULT_BASE_URL, 3)
    with Database(db_path) as db:
        page_one_saved_at = seed_page_one(db)

        page = FakePage([captcha("https://honestpool.ru/captcha?url=puzzle?id=71&page=2"),
                         captcha("https://honestpool.ru/captcha?url=puzzle?id=71&page=2")])

        def solved_redirects_to_first_page() -> None:
            if "captcha" in page.url:
                page.url, page.html = DEFAULT_BASE_URL, listing_page(1, PAGE_ONE)

        clock = Clock()
        start, end = plan_run(db, 2, 3)
        summary = run_pages(db, start, end, live_fetcher(page, clock, solved_redirects_to_first_page), max_pages=2)

        assert summary.stopped and "CAPTCHA shown again" in summary.stopped
        assert (summary.completed, summary.failed, summary.records_inserted) == (0, 0, 0)
        assert page.calls == [url2, url2]
        assert (db.page(2)["status"], db.page(2)["attempts"]) == (PENDING, 0)
        assert (db.page(3)["status"], db.page(3)["attempts"]) == (PENDING, 0)
        assert numbers(db) == PAGE_ONE and db.page(1)["status"] == COMPLETED

    with Database(db_path) as db:
        assert plan_run(db, resume=True) == (2, 3)
        assert db.pages_to_process(2, 3) == [2, 3]
        page = FakePage([success(2, [150, 199]), success(3, [])])
        clock = Clock()
        summary = run_pages(db, 2, 3, live_fetcher(page, clock), max_pages=2)

        assert page.calls == [url2, url3]
        assert clock.now >= 15
        assert (summary.completed, summary.records_inserted, summary.stopped) == (2, 2, None)
        assert [db.page(p)["status"] for p in (1, 2, 3)] == [COMPLETED] * 3
        assert [db.page(p)["records_found"] for p in (1, 2, 3)] == [9, 2, 0]
        assert numbers(db) == PAGE_ONE + [150, 199]
        assert db.record_row(1)["extracted_at"] == page_one_saved_at


def test_resume_selects_only_unfinished_pages(db_path: Path) -> None:
    site = FakeSite(verified={1: [1], 5: [401], 2: [101], 3: [250], 4: [], 6: [599]})
    with Database(db_path) as db:
        plan_run(db, 1, 6)
        process_page(db, 1, site)
        process_page(db, 5, site)
        db.claim_page(2)
        db.fail_page(2, "navigation timeout")
        db.claim_page(3)  # the process dies here, leaving page 3 in "processing"
        assert db.page(3)["status"] == PROCESSING

    with Database(db_path) as db:
        start, end = plan_run(db, resume=True)
        assert (start, end) == (1, 6)
        assert db.recover_interrupted() == 1
        assert db.pages_to_process(start, end) == [2, 3, 4, 6]
        site.calls.clear()
        summary = run_pages(db, start, end, site)
        assert site.calls == [2, 3, 4, 6]
        assert summary.completed == 4
        assert all(db.page(p)["status"] == COMPLETED for p in range(1, 7))
        assert numbers(db) == [1, 101, 250, 401, 599]
        site.calls.clear()
        assert run_pages(db, *plan_run(db, resume=True), site).planned == 0
        assert site.calls == []


def test_failed_pages_keep_their_status_until_retried(db: Database) -> None:
    site = FakeSite(scripted={2: [RuntimeError("boom")]})
    plan_run(db, 1, 3)
    run_pages(db, 1, 3, site)
    assert [db.page(p)["status"] for p in (1, 2, 3)] == [COMPLETED, FAILED, COMPLETED]
    site.calls.clear()
    run_pages(db, *plan_run(db, resume=True), site)
    assert site.calls == [2] and db.page(2)["status"] == COMPLETED


def test_start_from_any_selected_page_leaves_other_pages_untouched(db_path: Path) -> None:
    last = TOTAL_PAGES
    last_numbers = list(page_segments(last))
    site = FakeSite(verified={4: [301], last: [last_numbers[-1]]})
    with Database(db_path) as db:
        seed_page_one(db)
        run_pages(db, *plan_run(db, 4, 5), site)
        run_pages(db, *plan_run(db, last - 1, last), site)
        assert site.calls == [4, 5, last - 1, last]
        assert db.page(2) is None and db.page(3) is None
        assert db.page(last)["records_found"] == 1 and len(last_numbers) == 64
        assert numbers(db) == PAGE_ONE + [301, last_numbers[-1]]
        assert db.load_run_range() == (last - 1, last)


def test_rerunning_completed_pages_never_overwrites_records(db_path: Path) -> None:
    with Database(db_path) as db:
        saved_at = seed_page_one(db)
        site = FakeSite(verified={1: [1, 2]})
        summary = run_pages(db, *plan_run(db, 1, 1), site)
        assert site.calls == [] and summary.planned == 0
        assert numbers(db) == PAGE_ONE
        assert db.record_row(1)["extracted_at"] == saved_at
        assert db.page(1)["records_found"] == 9
