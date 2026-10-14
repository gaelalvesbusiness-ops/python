"""Manual CAPTCHA verification needs positive evidence of a real listing page (offline)."""

from __future__ import annotations

import logging
import random

import pytest
from playwright.sync_api import Error as PlaywrightError

from scraper.browser import (
    CAPTCHA_SHOWN,
    BrowserSettings,
    CaptchaRequiredError,
    ListingFetcher,
    listing_problem,
    safe_url,
)
from scraper.database import COMPLETED, PENDING, Database
from scraper.pagination import page_segments
from scraper.runner import plan_run, run_pages
from tests.fake_playwright import FakePage, captcha, success
from tests.html_fixtures import CAPTCHA_PAGE, listing_html, listing_page, listing_row

BASE = "https://honestpool.ru/puzzle?id=71"
PAGE2 = f"{BASE}&page=2"
CAPTCHA_URL = "https://honestpool.ru/captcha?url=puzzle?id=71&page=2"
NAVIGATING = object()


def new_challenge(n: int) -> tuple[str, str]:
    return CAPTCHA_URL, CAPTCHA_PAGE.replace("</body>", f"<img src='/captcha/img?{n}'></body>")


LISTING = (BASE, listing_page(1, [1, 2]))
BLANK = ("about:blank", "")
HOME_REDIRECT = ("https://honestpool.ru/", "<html><head><title>HONEST BITCOIN PUZZLE POOL</title></head></html>")
NO_TABLE = (BASE, "<html><head><title>HONEST BITCOIN PUZZLE POOL</title></head><body><p>Pool</p></body></html>")
ERROR_PAGE = (BASE, "<html><head><title>502 Bad Gateway</title></head><body>nginx</body></html>")
PARTIAL = (BASE, listing_html(listing_row(n) for n in list(page_segments(1))[:40]))
WRONG_HEADERS = (BASE, listing_page(1).replace("<th>End hex</th>", "<th>Hex end</th>"))
WRONG_PAGE = (f"{BASE}&page=3", listing_page(2))
WRONG_PUZZLE = ("https://honestpool.ru/puzzle?id=70", listing_page(1))
CHECKED_LISTING = ("https://honestpool.ru/puzzlePoolChecked?id=71", listing_page(1))
OTHER_HOST = ("https://honestpool.example/puzzle?id=71", listing_page(1))
PLAIN_HTTP = ("http://honestpool.ru/puzzle?id=71", listing_page(1))


class NavigatingPage(FakePage):
    def __init__(self, script) -> None:
        super().__init__(script)
        self.navigating = False

    def content(self) -> str:
        if self.navigating:
            raise PlaywrightError("Execution context was destroyed, most likely because of a navigation")
        return super().content()


class ManualUser:
    """Clock whose sleeps stand in for the person working in the browser between CAPTCHA polls."""

    def __init__(self, page: NavigatingPage, states: list) -> None:
        self.page, self.states = page, list(states)
        self.now = 0.0
        self.polls = 0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        self.polls += 1
        if self.states:
            state = self.states.pop(0)
            self.page.navigating = state is NAVIGATING
            if state is not NAVIGATING:
                self.page.url, self.page.html = state


def make_fetcher(states: list, script: list | None = None, notes: list[str] | None = None,
                 **overrides: object) -> tuple[ListingFetcher, NavigatingPage, ManualUser]:
    page = NavigatingPage(script if script is not None else [captcha(CAPTCHA_URL), success(2, [150])])
    user = ManualUser(page, states)
    options = dict(page_delay_s=0, page_delay_jitter_s=0, captcha_wait_s=60, captcha_poll_s=2)
    options.update(overrides)
    fetcher = ListingFetcher(page, BrowserSettings(**options), base_url=BASE,  # type: ignore[arg-type]
                             notify=(notes if notes is not None else []).append,
                             sleep=user.sleep, clock=user, rng=random.Random(0))
    return fetcher, page, user


# --- listing evidence ----------------------------------------------------------

def test_complete_listing_pages_are_accepted() -> None:
    assert listing_problem(*LISTING) is None
    assert listing_problem(PAGE2, listing_page(2)) is None
    assert listing_problem(f"{BASE}&page=1", listing_page(1)) is None


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (new_challenge(1), CAPTCHA_SHOWN),
        (BLANK, "not on the HonestPool site"),
        (OTHER_HOST, "not on the HonestPool site"),
        (PLAIN_HTTP, "not on the HonestPool site"),
        (HOME_REDIRECT, "not the puzzle listing"),
        (WRONG_PUZZLE, "not the puzzle listing"),
        (CHECKED_LISTING, "not the puzzle listing"),
        ((f"{BASE}&page=0", listing_page(1)), "invalid page parameter"),
        ((f"{BASE}&page=x", listing_page(1)), "invalid page parameter"),
        (NO_TABLE, "no valid data table"),
        (ERROR_PAGE, "no valid data table"),
        (WRONG_HEADERS, "no valid data table"),
        (PARTIAL, "40 listing rows, expected 100"),
        (WRONG_PAGE, "listing rows do not match"),
    ],
)
def test_non_listing_pages_are_rejected_with_a_reason(state: tuple[str, str], reason: str) -> None:
    problem = listing_problem(*state)
    assert problem is not None and problem.startswith(reason)


def test_safe_url_keeps_only_listing_parameters() -> None:
    assert safe_url(f"{PAGE2}&csrf=abc&token=xyz#frag") == PAGE2
    assert safe_url("https://honestpool.ru/captcha?url=puzzle?id=71&page=2&captcha=Xy7Qz") == (
        "https://honestpool.ru/captcha?page=2")


# --- the manual wait -------------------------------------------------------------

def test_correct_submission_leading_to_listing_is_verified() -> None:
    notes: list[str] = []
    fetch, page, user = make_fetcher([LISTING], notes=notes)
    html = fetch(2)
    assert "tag_search_101" in html
    assert page.calls == [PAGE2, PAGE2]
    assert fetch.manual_verifications == 1 and user.polls == 1
    assert any(n.startswith("Verification detected") and f"listing at {BASE}" in n for n in notes)


def test_incorrect_submission_returning_to_captcha_keeps_waiting() -> None:
    notes: list[str] = []
    fetch, page, _ = make_fetcher([new_challenge(1)], notes=notes, captcha_wait_s=20)
    with pytest.raises(CaptchaRequiredError, match="not completed within 20s"):
        fetch(2)
    assert page.calls == [PAGE2]
    assert sum("answer may have been rejected" in n for n in notes) == 1
    assert not any(n.startswith("Verification detected") for n in notes)


def test_temporary_redirect_before_captcha_reappears_is_not_verification() -> None:
    notes: list[str] = []
    fetch, page, _ = make_fetcher([HOME_REDIRECT, new_challenge(1)], notes=notes, captcha_wait_s=20)
    with pytest.raises(CaptchaRequiredError):
        fetch(2)
    assert page.calls == [PAGE2]
    assert not any(n.startswith("Verification detected") for n in notes)


def test_blank_and_navigating_pages_are_not_verification() -> None:
    fetch, page, user = make_fetcher([BLANK, NAVIGATING, BLANK, LISTING])
    fetch(2)
    assert user.polls == 4
    assert page.calls == [PAGE2, PAGE2]


@pytest.mark.parametrize(
    "state",
    [BLANK, HOME_REDIRECT, NO_TABLE, ERROR_PAGE, PARTIAL, WRONG_HEADERS, WRONG_PAGE, WRONG_PUZZLE,
     CHECKED_LISTING, OTHER_HOST],
    ids=["blank", "redirect", "no-table", "error-page", "partial", "wrong-headers", "wrong-page",
         "wrong-puzzle", "checked-listing", "other-host"],
)
def test_pages_without_listing_evidence_never_end_the_wait(state: tuple[str, str]) -> None:
    fetch, page, user = make_fetcher([state], captcha_wait_s=10)
    with pytest.raises(CaptchaRequiredError, match="not completed"):
        fetch(2)
    assert page.calls == [PAGE2]
    assert user.now >= 10


def test_partially_loaded_listing_is_accepted_once_complete() -> None:
    fetch, _, user = make_fetcher([PARTIAL, PARTIAL, LISTING])
    fetch(2)
    assert user.polls == 3


def test_verification_after_multiple_manual_attempts() -> None:
    notes: list[str] = []
    states = [new_challenge(1), NAVIGATING, HOME_REDIRECT, new_challenge(2), BLANK, PARTIAL, LISTING]
    fetch, page, user = make_fetcher(states, notes=notes)
    html = fetch(2)
    assert "tag_search_101" in html
    assert user.polls == len(states)
    assert sum("answer may have been rejected" in n for n in notes) == 2
    assert sum(n.startswith("Verification detected") for n in notes) == 1
    assert fetch.manual_verifications == 1 and page.calls == [PAGE2, PAGE2]


def test_verification_timeout_is_configurable() -> None:
    fetch, page, user = make_fetcher([], captcha_wait_s=7, captcha_poll_s=1)
    with pytest.raises(CaptchaRequiredError, match="not completed within 7s"):
        fetch(2)
    assert user.polls == 7 and page.calls == [PAGE2]
    assert BrowserSettings().captcha_wait_s == 600


def test_destination_is_logged_without_sensitive_parameters(caplog: pytest.LogCaptureFixture) -> None:
    notes: list[str] = []
    noisy = (f"{BASE}&csrf=deadbeef&session=s3cr3t", listing_page(1))
    fetch, _, _ = make_fetcher([new_challenge(1), noisy], notes=notes)
    with caplog.at_level(logging.INFO, logger="scraper.browser"):
        fetch(2)
    text = caplog.text + "\n".join(notes)
    assert f"verification confirmed at {BASE}" in caplog.text
    assert "deadbeef" not in text and "s3cr3t" not in text and "img?" not in text


# --- run-level behaviour ---------------------------------------------------------

def test_captcha_later_in_the_run_beyond_the_limit_stops_cleanly(db: Database) -> None:
    script = [captcha(CAPTCHA_URL), success(2, [150]), captcha(CAPTCHA_URL.replace("page=2", "page=3"))]
    fetch, page, _ = make_fetcher([LISTING], script=script, max_manual_verifications=1)
    plan_run(db, 2, 4)
    summary = run_pages(db, 2, 4, fetch)
    assert summary.stopped and "the limit of 1 manual verification(s) for this browser session" in summary.stopped
    assert (summary.completed, summary.records_inserted) == (1, 1)
    assert db.page(2)["status"] == COMPLETED
    assert (db.page(3)["status"], db.page(3)["attempts"]) == (PENDING, 0)
    assert (db.page(4)["status"], db.page(4)["attempts"]) == (PENDING, 0)
    assert len(page.calls) == 3


def test_failed_verification_leaves_database_untouched(db: Database) -> None:
    fetch, _, _ = make_fetcher([HOME_REDIRECT, new_challenge(1)], captcha_wait_s=10)
    plan_run(db, 2, 3)
    summary = run_pages(db, 2, 3, fetch)
    assert summary.stopped and "not completed" in summary.stopped
    assert db.record_count() == 0
    assert [(db.page(p)["status"], db.page(p)["attempts"]) for p in (2, 3)] == [(PENDING, 0), (PENDING, 0)]
