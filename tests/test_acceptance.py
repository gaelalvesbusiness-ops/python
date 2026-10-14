"""Offline acceptance scenarios, numbered after the client requirements (Step 11).

Every scenario uses temporary databases, synthetic or saved HTML and mocked browsers;
nothing here contacts the website.
"""

from __future__ import annotations

import random
import sqlite3
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

import main as cli
from scraper import browser
from scraper.browser import (
    AccessRestrictedError,
    BrowserSession,
    BrowserSettings,
    CaptchaRequiredError,
    ListingFetcher,
    RateLimitedError,
    TransientFetchError,
    is_captcha_page,
)
from scraper.config import DATA_DIR, RANGE_START, SEGMENT_SIZE, TOTAL_PAGES
from scraper.database import COMPLETED, PENDING, Database
from scraper.pagination import page_segments
from scraper.parser import (
    DUPLICATE,
    ID_MISMATCH,
    INVALID_HEX,
    INVALID_NUMBER,
    MALFORMED_ROW,
    MASKED_HEX,
    MISSING_VALUE,
    NOT_VERIFIED,
    RANGE_MISMATCH,
    VERIFIED_TEXT,
    WRONG_BADGE,
    parse_page,
    segment_range,
)
from scraper.runner import StopRunError, plan_run, run_live, run_pages
from tests.fake_playwright import (
    FakePage,
    FakePlaywright,
    captcha,
    factory,
    http,
    success,
    timeout,
)
from tests.html_fixtures import (
    CAPTCHA_PAGE,
    UNVERIFIED_BADGE,
    VERIFIED_BADGE,
    FakeSite,
    listing_html,
    listing_page,
    listing_row,
)
from tests.test_captcha_verification import (
    BLANK,
    LISTING,
    PAGE2,
    PARTIAL,
    make_fetcher,
    new_challenge,
)

SAMPLE = DATA_DIR / "sample_page.html"
BASE = "https://honestpool.ru/puzzle?id=71"


def row(number: int, status: str = VERIFIED_BADGE, start: str | None = None, end: str | None = None,
        row_id: str | None = None, number_text: str | None = None) -> str:
    exp_start, exp_end = segment_range(number)
    return (f'<tr id="{row_id or f"tag_search_{number}"}"><th>{number_text or number}</th>'
            f"<td>{exp_start if start is None else start}</td><td>{exp_end if end is None else end}</td>"
            f"<td>c</td><td>{status}</td><td></td></tr>")


def page_with(page: int, special: dict[int, str]) -> str:
    """A complete listing page where the rows in ``special`` replace the default unverified rows."""
    return listing_html(special.get(n, listing_row(n)) for n in page_segments(page))


def numbers(db: Database) -> list[int]:
    return [r.number for r in db.iter_records()]


def formula(number: int) -> tuple[str, str]:
    start = RANGE_START + (number - 1) * SEGMENT_SIZE
    return format(start, "018x"), format(start + SEGMENT_SIZE, "018x")


def fake_live(site: FakeSite):
    """Stand-in for run_live that serves synthetic pages through the real processing loop."""
    def run(db, start, end, settings, *, max_pages, max_attempts, reporter=None, solver=None, on_page_saved=None):
        return run_pages(db, start, end, site, max_pages=max_pages, max_attempts=max_attempts, reporter=reporter,
                         on_page_saved=on_page_saved)
    return run


def fetcher(page: FakePage, clock: list[float] | None = None, **overrides: object) -> ListingFetcher:
    clock = clock if clock is not None else [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    options = dict(page_delay_s=0, page_delay_jitter_s=0, backoff_base_s=1, backoff_max_s=4,
                   captcha_wait_s=30, captcha_poll_s=1, max_retries=2)
    options.update(overrides)
    fetch = ListingFetcher(page, BrowserSettings(**options), base_url=BASE,  # type: ignore[arg-type]
                           notify=lambda _: None, sleep=sleep, clock=lambda: clock[0], rng=random.Random(0))
    fetch.sleeps = sleeps  # type: ignore[attr-defined]
    return fetch


# --- Part B: data extraction ---------------------------------------------------------------

def test_b1_b2_only_exact_green_verified_rows_are_extracted() -> None:
    html = page_with(1, {
        1: row(1),                                                                   # green Проверено
        2: row(2, UNVERIFIED_BADGE),                                                 # Не проверено
        3: row(3, '<span class="badge bg-warning">Проверено</span>'),                # wrong colour
        4: row(4, f"{VERIFIED_TEXT}"),                                               # no badge
        5: row(5, '<span class="badge bg-success">Проверено!</span>'),               # not exact text
        6: row(6, VERIFIED_BADGE * 2),                                               # two badges
        7: row(7, '<span class="badge bg-success bg-warning">Проверено</span>'),     # mixed colours
        8: row(8, '<span class="badge bg-success"> Проверено </span>'),              # whitespace only
    })
    result = parse_page(html)
    assert [r.number for r in result.records] == [1, 8]
    reasons = {r.number: r.reason for r in result.rejections if r.number in range(2, 8)}
    assert reasons == {2: NOT_VERIFIED, 3: WRONG_BADGE, 4: NOT_VERIFIED, 5: NOT_VERIFIED,
                       6: MALFORMED_ROW, 7: WRONG_BADGE}
    assert result.rows_inspected == 100


@pytest.mark.skipif(not SAMPLE.exists(), reason="saved sample page not available")
def test_b1_b2_saved_page_matches_an_independent_count_of_green_badges() -> None:
    html = SAMPLE.read_text(encoding="utf-8")
    rows = BeautifulSoup(html, "html.parser").select("tr[id^=tag_search_]")
    green = [int(r["id"].removeprefix("tag_search_")) for r in rows
             if any(b.get_text(strip=True) == VERIFIED_TEXT and "bg-success" in b.get("class", [])
                    for b in r.select("span.badge"))]
    result = parse_page(html)
    assert [r.number for r in result.records] == green == list(range(1, 10))
    assert result.reason_counts() == {NOT_VERIFIED: 91}


def test_b3_b4_fields_are_complete_and_match_the_segment_formula() -> None:
    chosen = [99_901, 99_950, 100_000]
    result = parse_page(listing_page(1000, chosen))
    assert [r.number for r in result.records] == chosen
    for r in result.records:
        assert (r.start_hex, r.end_hex) == formula(r.number)
        assert len(r.start_hex) == len(r.end_hex) == 18 and r.status == VERIFIED_TEXT
    assert segment_range(1) == ("400000000000000000", "400000100000000000")
    assert segment_range(67_108_864) == ("7ffffff00000000000", "800000000000000000")


@pytest.mark.parametrize(
    ("bad_row", "reason"),
    [
        (row(1, start="40000***0000000000"), MASKED_HEX),
        (row(1, start="400000...000000000"), MASKED_HEX),
        (row(1, start='<span class="range_hex_hidden">400000000000000000</span>'), MASKED_HEX),
        (row(1, start="40000000000000000"), INVALID_HEX),                     # 17 digits
        (row(1, start="40000000000000000G"), INVALID_HEX),                    # not hex
        (row(1, start="400000000000000000".upper().replace("4", "A")), INVALID_HEX),  # upper case
        (row(1, start="<b>400000000000000000</b>"), INVALID_HEX),             # nested markup
        (row(1, start=""), MISSING_VALUE),
        (row(1, start="400000100000000000"), RANGE_MISMATCH),                 # belongs to segment 2
        (row(1, number_text=" "), MISSING_VALUE),
        (row(1, number_text="1a"), INVALID_NUMBER),
        (row(1, row_id="tag_search_2"), ID_MISMATCH),
        (row(1, row_id="other_1"), MALFORMED_ROW),
        ('<tr id="tag_search_1"><th>1</th><td>400000000000000000</td></tr>', MISSING_VALUE),
    ],
)
def test_b5_masked_malformed_or_incomplete_rows_are_rejected(bad_row: str, reason: str) -> None:
    result = parse_page(page_with(1, {1: bad_row}))
    assert result.records == []
    assert [r.reason for r in result.rejections if r.reason != NOT_VERIFIED] == [reason]


def test_b5_a_page_with_an_invalid_verified_row_is_failed_and_saves_nothing(db: Database) -> None:
    html = page_with(1000, {99_901: row(99_901), 99_902: row(99_902, start="40***0000000000000")})
    plan_run(db, 1000, 1000)
    summary = run_pages(db, 1000, 1000, FakeSite(scripted={1000: [html]}))
    assert summary.failed == 1 and db.record_count() == 0
    assert db.page(1000)["status"] != COMPLETED and MASKED_HEX in db.page(1000)["last_error"]


def test_b6_duplicates_are_never_saved(db: Database) -> None:
    within_page = parse_page(page_with(1, {1: row(1), 2: row(1, row_id="tag_search_1")}))
    assert [r.number for r in within_page.records] == [1]
    seen: dict = {}
    parse_page(listing_page(1, [5]), seen)
    again = parse_page(listing_page(1, [5]), seen)
    assert again.records == [] and [(r.number, r.reason) for r in again.rejections if r.reason == DUPLICATE] == [
        (5, DUPLICATE)]
    site = FakeSite(verified={1000: [99_901]})
    for _ in range(3):
        run_pages(db, *plan_run(db, 1000, 1000), site)
    assert site.calls == [1000] and numbers(db) == [99_901]
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute("INSERT INTO verified_records (number, start_hex, end_hex, status, source_page) "
                        "VALUES (99901, ?, ?, ?, 1000)", (*segment_range(99_901), VERIFIED_TEXT))


# --- Part C: page selection -------------------------------------------------------------------

VERIFIED_1000 = {1000: [99_901, 100_000], 1002: [100_150], 1005: [100_401]}


def test_c1_c3_cli_processes_exactly_pages_1000_to_1005(db_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                         capsys: pytest.CaptureFixture[str]) -> None:
    site = FakeSite(verified=VERIFIED_1000)
    monkeypatch.setattr(cli, "run_live", fake_live(site))
    argv = ["--db", str(db_path), "--start-page", "1000", "--end-page", "1005", "--live", "--max-pages", "10"]
    assert cli.main(argv) == 0
    assert site.calls == [1000, 1001, 1002, 1003, 1004, 1005]
    assert "Live run: 6 completed, 0 failed, 4 new verified records" in capsys.readouterr().out
    with Database(db_path, readonly=True) as db:
        assert tuple(db.conn.execute("SELECT MIN(page_number), MAX(page_number), COUNT(*) FROM page_progress"
                                     ).fetchone()) == (1000, 1005, 6)
        assert db.progress_counts(1000, 1005)[COMPLETED] == 6
        assert db.page(999) is None and db.page(1006) is None
        assert numbers(db) == [99_901, 100_000, 100_150, 100_401]
        assert {db.record_row(n)["source_page"] for n in (99_901, 100_000)} == {1000}


def test_c1_max_pages_keeps_the_run_inside_the_range(db: Database) -> None:
    site = FakeSite(verified=VERIFIED_1000)
    run_pages(db, *plan_run(db, 1000, 1005), site, max_pages=2)
    assert site.calls == [1000, 1001]
    assert db.progress_counts(1000, 1005) == {PENDING: 4, "processing": 0, COMPLETED: 2, "failed": 0}


def test_c4_c5_selecting_a_different_range_preserves_saved_records(db_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    site = FakeSite(verified={**VERIFIED_1000, 2001: [200_050]})
    monkeypatch.setattr(cli, "run_live", fake_live(site))
    base = ["--db", str(db_path), "--live", "--max-pages", "10"]
    assert cli.main([*base, "--start-page", "1000", "--end-page", "1005"]) == 0
    site.calls.clear()
    assert cli.main([*base, "--start-page", "2000", "--end-page", "2002"]) == 0
    assert site.calls == [2000, 2001, 2002]
    site.calls.clear()
    assert cli.main([*base, "--start-page", "1000", "--end-page", "1005"]) == 0
    assert site.calls == []
    assert cli.main(["--db", str(db_path), "--resume"]) == 0
    with Database(db_path, readonly=True) as db:
        assert numbers(db) == [99_901, 100_000, 100_150, 100_401, 200_050]
        assert db.load_run_range() == (1000, 1005)
        assert db.progress_counts()[COMPLETED] == 9


@pytest.mark.parametrize(
    "argv",
    [["--start-page", "0", "--end-page", "5"], ["--start-page", "-3", "--end-page", "5"],
     ["--start-page", "10", "--end-page", "9"], ["--start-page", "1", "--end-page", str(TOTAL_PAGES + 1)],
     ["--start-page", "abc", "--end-page", "5"], ["--start-page", "1000"], ["--end-page", "1005"],
     ["--start-page", "1.5", "--end-page", "5"]],
)
def test_c6_invalid_page_numbers_are_rejected(argv: list[str], tmp_path: Path) -> None:
    db_path = tmp_path / "never.db"
    with pytest.raises(SystemExit) as exc:
        cli.main(["--db", str(db_path), *argv, "--live"])
    assert exc.value.code == 2 and not db_path.exists()


# --- Part D: progress saving and recovery -------------------------------------------------

def test_d1_d2_each_page_is_committed_before_the_next_one_starts(db_path: Path) -> None:
    observed: list[tuple[int, list[int], int]] = []

    class CheckingSite(FakeSite):
        def __call__(self, page: int) -> str:
            reader = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
            done = [r[0] for r in reader.execute(
                "SELECT page_number FROM page_progress WHERE status = 'completed' ORDER BY 1")]
            observed.append((page, done, reader.execute("SELECT COUNT(*) FROM verified_records").fetchone()[0]))
            reader.close()
            return super().__call__(page)

    with Database(db_path) as db:
        run_pages(db, *plan_run(db, 1000, 1003), CheckingSite(verified=VERIFIED_1000))
    assert observed == [(1000, [], 0), (1001, [1000], 2), (1002, [1000, 1001], 2), (1003, [1000, 1001, 1002], 3)]


def test_d3_d7_stop_during_processing_then_resume(db_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    site = FakeSite(verified=VERIFIED_1000, scripted={1003: [KeyboardInterrupt()]})
    monkeypatch.setattr(cli, "run_live", fake_live(site))
    argv = ["--db", str(db_path), "--start-page", "1000", "--end-page", "1005", "--live", "--max-pages", "10"]
    assert cli.main(argv) == 130
    assert "Continue with --resume --live" in capsys.readouterr().out
    with Database(db_path, readonly=True) as db:
        assert [db.page(p)["status"] for p in range(1000, 1006)] == [COMPLETED] * 3 + [PENDING] * 3
        assert db.page(1003)["attempts"] == 0
        assert numbers(db) == [99_901, 100_000, 100_150]
    site.calls.clear()
    assert cli.main(["--db", str(db_path), "--resume", "--live", "--max-pages", "10"]) == 0
    assert site.calls == [1003, 1004, 1005]
    with Database(db_path, readonly=True) as db:
        assert db.progress_counts(1000, 1005)[COMPLETED] == 6
        stored = numbers(db)
        assert stored == [99_901, 100_000, 100_150, 100_401] and len(stored) == len(set(stored))


def test_d6_failed_and_interrupted_pages_are_processed_again_first(db_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    site = FakeSite(verified=VERIFIED_1000, scripted={1001: [TimeoutError("navigation timed out")]})
    monkeypatch.setattr(cli, "run_live", fake_live(site))
    assert cli.main(["--db", str(db_path), "--start-page", "1000", "--end-page", "1005", "--live",
                     "--max-pages", "3"]) == 0
    assert site.calls == [1000, 1001, 1002]
    site.calls.clear()
    assert cli.main(["--db", str(db_path), "--resume", "--live", "--max-pages", "10"]) == 0
    assert site.calls == [1001, 1003, 1004, 1005]
    with Database(db_path, readonly=True) as db:
        assert db.page(1001)["attempts"] == 2 and db.progress_counts(1000, 1005)[COMPLETED] == 6


def test_d8_unexpected_termination_mid_page_is_recovered_on_resume(db_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                   capsys: pytest.CaptureFixture[str]) -> None:
    site = FakeSite(verified=VERIFIED_1000)
    with Database(db_path) as db:
        run_pages(db, *plan_run(db, 1000, 1005), site, max_pages=2)
        db.claim_page(1002)  # the process dies here: page 1002 stays "processing", nothing is released
    monkeypatch.setattr(cli, "run_live", fake_live(site))
    site.calls.clear()
    assert cli.main(["--db", str(db_path), "--resume", "--live", "--max-pages", "10"]) == 0
    assert "Recovered interrupted pages: 1" in capsys.readouterr().out
    assert site.calls == [1002, 1003, 1004, 1005]
    with Database(db_path, readonly=True) as db:
        assert numbers(db) == [99_901, 100_000, 100_150, 100_401]


# --- Part E: CAPTCHA and error handling (mocked browser) ----------------------------------

def test_e1_captcha_detection() -> None:
    assert is_captcha_page("https://honestpool.ru/captcha?url=puzzle?id=71", CAPTCHA_PAGE)
    assert is_captcha_page(BASE, CAPTCHA_PAGE)
    assert not is_captcha_page(BASE, listing_page(1, [1]))
    with pytest.raises(CaptchaRequiredError, match="headless"):
        fetcher(FakePage([captcha()]), headless=True)(1)


def test_e2_e3_wrong_answers_then_a_valid_listing_after_manual_verification(db: Database) -> None:
    notes: list[str] = []
    states = [new_challenge(1), new_challenge(2), BLANK, PARTIAL, LISTING]
    fetch, page, _ = make_fetcher(states, notes=notes)
    plan_run(db, 2, 2)
    summary = run_pages(db, 2, 2, fetch)
    assert summary.completed == 1 and numbers(db) == [150]
    assert sum("answer may have been rejected" in n for n in notes) == 2
    assert sum(n.startswith("Verification detected") for n in notes) == 1
    assert fetch.manual_verifications == 1 and page.calls == [PAGE2, PAGE2]


def test_e2_wrong_answers_until_the_timeout_save_nothing(db: Database) -> None:
    fetch, page, _ = make_fetcher([new_challenge(1), new_challenge(2)], captcha_wait_s=10)
    plan_run(db, 2, 3)
    summary = run_pages(db, 2, 3, fetch)
    assert summary.stopped and "not completed" in summary.stopped
    assert db.record_count() == 0 and (db.page(2)["status"], db.page(2)["attempts"]) == (PENDING, 0)
    assert page.calls == [PAGE2]


def test_e4_second_captcha_stops_safely_and_keeps_earlier_pages(db: Database) -> None:
    script = [captcha(), success(2, [150]), captcha()]
    fetch, page, _ = make_fetcher([LISTING], script=script)
    plan_run(db, 2, 4)
    summary = run_pages(db, 2, 4, fetch)
    assert summary.stopped and summary.completed == 1
    assert db.page(2)["status"] == COMPLETED and numbers(db) == [150]
    assert [(db.page(p)["status"], db.page(p)["attempts"]) for p in (3, 4)] == [(PENDING, 0), (PENDING, 0)]
    assert len(page.calls) == 3


@pytest.mark.parametrize("status", [401, 403])
def test_e5_access_denied_stops_without_retry(status: int, db: Database) -> None:
    page = FakePage([http(status)])
    with pytest.raises(AccessRestrictedError, match=str(status)):
        fetcher(page)(1)
    assert len(page.calls) == 1
    plan_run(db, 1, 2)
    summary = run_pages(db, 1, 2, fetcher(FakePage([http(status)])))
    assert summary.stopped and (db.page(1)["status"], db.page(1)["attempts"]) == (PENDING, 0)


def test_e5_e7_http_429_honours_retry_after_and_stops_when_persistent() -> None:
    clock = [0.0]
    fetch = fetcher(FakePage([http(429, headers={"Retry-After": "45"}), success(1, [1])]), clock)
    assert "tag_search_1" in fetch(1)
    assert fetch.sleeps[0] >= 45  # type: ignore[attr-defined]
    with pytest.raises(RateLimitedError, match="persisted"):
        fetcher(FakePage([http(429)] * 3), max_retries=2)(1)
    with pytest.raises(RateLimitedError):
        fetcher(FakePage([http(429, headers={"Retry-After": "3600"})]), max_retry_after_s=600)(1)


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_e5_server_errors_are_retried_a_bounded_number_of_times(status: int) -> None:
    assert "tag_search_1" in fetcher(FakePage([http(status), success(1, [1])]))(1)
    page = FakePage([http(status)] * 5)
    with pytest.raises(TransientFetchError):
        fetcher(page, max_retries=2)(1)
    assert len(page.calls) == 3


@pytest.mark.parametrize("retries", [0, 1, 2, 5])
def test_e6_timeouts_are_retried_at_most_the_configured_number_of_times(retries: int) -> None:
    page = FakePage([timeout()] * 10)
    with pytest.raises(TransientFetchError, match="timeout"):
        fetcher(page, max_retries=retries)(1)
    assert len(page.calls) == retries + 1


def test_e7_pacing_between_pages_with_jitter() -> None:
    clock = [0.0]
    fetch = fetcher(FakePage([success(p) for p in range(1, 6)]), clock, page_delay_s=15, page_delay_jitter_s=5)
    for p in range(1, 6):
        fetch(p)
    gaps = fetch.sleeps  # type: ignore[attr-defined]
    assert len(gaps) == 4 and all(15 <= g <= 20 for g in gaps)


def test_e8_access_restriction_never_triggers_proxy_relaunch_or_bypass(db: Database,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakePlaywright()
    fake.browser.context.page.script = [success(1, [1]), http(403, page=2)]
    monkeypatch.setattr(browser, "BrowserSession", lambda settings: BrowserSession(settings, factory(fake)))
    plan_run(db, 1, 5)
    summary = run_live(db, 1, 5, BrowserSettings(page_delay_s=0, page_delay_jitter_s=0), max_pages=5,
                       notify=lambda _: None)
    assert summary.stopped and "403" in summary.stopped
    assert fake.chromium.launches == [{"headless": False, "handle_sigint": False}]
    assert set(fake.browser.context.kwargs) == {"locale", "viewport"}
    assert len(fake.browser.context.page.calls) == 2 and numbers(db) == [1]


def test_e9_automatic_captcha_solving_is_not_implemented() -> None:
    """NOT IMPLEMENTED by design: the scraper only waits for a person and never touches the form."""
    class NoTouchPage(FakePage):
        def __getattr__(self, name: str):
            if name in {"fill", "click", "type", "press", "locator", "evaluate", "keyboard", "mouse"}:
                raise AssertionError(f"scraper tried to interact with the CAPTCHA via {name}")
            raise AttributeError(name)

    page = NoTouchPage([captcha()])
    with pytest.raises(CaptchaRequiredError, match="not completed"):
        fetcher(page, captcha_wait_s=5, captcha_poll_s=1)(1)
    assert page.calls == [BASE]
    assert issubclass(CaptchaRequiredError, StopRunError)
