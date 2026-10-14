"""Offline reproduction of the 2026-10-14 Windows run that stopped at page 27.

1. A new CAPTCHA later in the same browser session (page 27, 19 pages after the one completed
   manually on page 6) stopped the run: only one manual verification was allowed per session,
   and the reason said "CAPTCHA shown again" without telling how to continue.
2. The CAPTCHA image was never captured although it was visible to the operator: it was only
   looked for inside the CAPTCHA <form>. The image part uses a real headless Chromium on local,
   synthetic CAPTCHA layouts: every network request is aborted and the images are data: URLs,
   so nothing is sent anywhere.
"""

from __future__ import annotations

import base64
import logging
import random
import struct
import zlib
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Error as PlaywrightError

import main as cli
from scraper import browser
from scraper.browser import BrowserSession, BrowserSettings, ListingFetcher
from scraper.database import COMPLETED, PENDING, Database
from scraper.runner import plan_run, run_pages
from tests.fake_playwright import FakePage, FakePlaywright, ScriptedNav, captcha_form, factory, success
from tests.html_fixtures import listing_page

BASE = "https://honestpool.ru/puzzle?id=71"
FORM_ONLY_SELECTOR = 'form:has(input[name="captcha"]) img'  # the selector used until 2026-10-14
IMAGE_LOADED_JS = "img => img.complete && img.naturalWidth > 0"


def url(page: int) -> str:
    return BASE if page == 1 else f"{BASE}&page={page}"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class Operator:
    """Sleep hook: the person at the browser completes every manual CAPTCHA prompt."""

    def __init__(self, page: FakePage, clock: Clock, notes: list[str]) -> None:
        self.page, self.clock, self.notes, self.solved = page, clock, notes, 0

    def __call__(self, seconds: float) -> None:
        self.clock.sleep(seconds)
        prompts = sum("Please complete it manually" in note for note in self.notes)
        if prompts > self.solved and "captcha" in self.page.url:
            self.solved += 1
            self.page.url, self.page.html = BASE, listing_page(1)


def manual_session(script: list[ScriptedNav], **overrides: Any) -> tuple[FakePage, ListingFetcher, list[str]]:
    """A visible browser session without a CAPTCHA solver, like the log's run after the image capture failed."""
    page, clock, notes = FakePage(script), Clock(), []
    settings = replace(BrowserSettings(), page_delay_s=0, page_delay_jitter_s=0, captcha_wait_s=60,
                       captcha_poll_s=1, **overrides)
    fetch = ListingFetcher(page, settings, base_url=BASE, notify=notes.append, sleep=Operator(page, clock, notes),
                           clock=clock, rng=random.Random(0))
    return page, fetch, notes


# CAPTCHA on page 1 (completed manually), pages 2-3 fine, a new CAPTCHA on page 4.
TWO_CAPTCHAS = [captcha_form(1), success(1, [1]), success(2), success(3, [250]), captcha_form(2),
                success(4), success(5), success(6)]


def stored(db: Database) -> list[tuple]:
    return [tuple(row) for row in db.conn.execute("SELECT * FROM verified_records ORDER BY number")]


# --- 1. a new CAPTCHA later in the session -------------------------------------------------------

def test_stop_at_a_later_captcha_keeps_progress_and_resume_skips_completed_pages(db: Database) -> None:
    _, fetch, _ = manual_session(TWO_CAPTCHAS, max_manual_verifications=1)
    plan_run(db, 1, 6)
    summary = run_pages(db, 1, 6, fetch)
    assert summary.stopped is not None and summary.stopped.startswith("page 4: ")
    assert (summary.completed, summary.interrupted, summary.failed) == (3, 1, 0)
    assert [(db.page(p)["status"], db.page(p)["attempts"]) for p in range(1, 7)] == \
        [(COMPLETED, 1)] * 3 + [(PENDING, 0)] * 3
    records = stored(db)
    assert [row[0] for row in records] == [1, 250]

    page, fetch, _ = manual_session([success(4), success(5), success(6)])
    start, end = plan_run(db, resume=True)
    summary = run_pages(db, start, end, fetch)
    assert (start, end) == (1, 6) and page.calls == [url(4), url(5), url(6)]
    assert summary.completed == 3 and all(db.page(p)["status"] == COMPLETED for p in range(1, 7))
    assert stored(db) == records, "records stored before the stop are never rewritten"


def test_captcha_again_right_after_a_manual_verification_still_stops(db: Database) -> None:
    _, fetch, _ = manual_session([captcha_form(1), captcha_form(2)])
    plan_run(db, 1, 1)
    summary = run_pages(db, 1, 1, fetch)
    assert summary.stopped is not None and "CAPTCHA shown again right after verification" in summary.stopped
    assert (db.page(1)["status"], db.page(1)["attempts"]) == (PENDING, 0)


def test_a_new_captcha_later_in_the_session_can_be_completed_manually(db: Database) -> None:
    _, fetch, notes = manual_session(TWO_CAPTCHAS)
    plan_run(db, 1, 6)
    summary = run_pages(db, 1, 6, fetch)
    assert summary.stopped is None and summary.completed == 6
    assert fetch.manual_verifications == 2
    prompts = [note for note in notes if "Please complete it manually" in note]
    assert len(prompts) == 2 and "manual verification 2 of 10 for this browser session" in prompts[1]


def test_stop_reason_names_the_manual_verification_limit(db: Database) -> None:
    _, fetch, _ = manual_session(TWO_CAPTCHAS, max_manual_verifications=1)
    plan_run(db, 1, 6)
    summary = run_pages(db, 1, 6, fetch)
    assert summary.stopped == ("page 4: a new CAPTCHA appeared and the limit of 1 manual verification(s) for this "
                               "browser session is reached")
    assert summary.stopped_page == 4


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakePlaywright:
    fake = FakePlaywright()
    monkeypatch.setattr(browser, "BrowserSession", lambda settings: BrowserSession(settings, factory(fake)))
    real_run_live = cli.run_live
    monkeypatch.setattr(cli, "run_live", lambda db, start, end, s, **kw: real_run_live(
        db, start, end, replace(s, page_delay_s=0, page_delay_jitter_s=0, captcha_poll_s=0.001), **kw))
    return fake


def test_cli_stop_explains_saved_progress_and_how_to_continue(fake: FakePlaywright, db_path: Path,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    fake.browser.context.page.script = [success(2), captcha_form(1)]
    argv = ["--db", str(db_path), "--start-page", "2", "--end-page", "4", "--live", "--headless", "--max-pages", "3"]
    assert cli.main(argv) == 3
    out = capsys.readouterr().out
    assert ("Run stopped: page 3: CAPTCHA shown in headless mode; manual verification is impossible\n"
            "Progress saved: 1 of 3 pages in range 2..4 completed, 2 pending; page 3 returned to pending without "
            "counting an attempt. Completed pages are not fetched again. Continue with: python main.py --resume "
            "--live (on Windows: option 2 of iniciar_honestpool.bat).\n") in out


def test_manual_verification_limit_is_an_option(fake: FakePlaywright, db_path: Path) -> None:
    assert cli.live_settings(cli.build_parser().parse_args([])).max_manual_verifications == 10
    args = cli.build_parser().parse_args(["--max-manual-verifications", "3"])
    assert cli.live_settings(args).max_manual_verifications == 3
    for bad in ("0", "51"):
        with pytest.raises(SystemExit) as stop:
            cli.main(["--db", str(db_path), "--resume", "--live", "--max-manual-verifications", bad])
        assert stop.value.code == 2


# --- 2. capturing the CAPTCHA image -------------------------------------------------------------------

def png(width: int, height: int) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    rows = b"".join(b"\x00" + bytes((200, 60, 200)) * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def png_size(data: bytes) -> tuple[int, int]:
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    return struct.unpack(">II", data[16:24])


def data_url(width: int, height: int) -> str:
    return "data:image/png;base64," + base64.b64encode(png(width, height)).decode("ascii")


CHALLENGE, ICON = data_url(120, 40), data_url(16, 16)
HEAD = ('<html><head><title>HONEST BITCOIN PUZZLE POOL | CAPTCHA</title></head><body>'
        '<div class="card"><div class="card-header">Ты не бот? Так пройди же капчу!</div><div class="card-body">')
FIELDS = ('<input type="hidden" name="csrf" value="synthetic-csrf">'
          '<input type="text" name="captcha" placeholder="Неее, ты все-таки бот...">'
          '<button type="submit" name="btn_check_captcha" value="1">Отправить</button>')
TAIL = "</div></div></body></html>"
REFRESH = f'<button type="button" class="btn btn-primary"><img src="{ICON}" alt=""></button>'
# Synthetic layouts: the real page's markup was never saved; its screenshot shows the image with a
# refresh button beside it, above the input and the submit button.
INSIDE_FORM = HEAD + f'<form method="post"><img src="{CHALLENGE}" alt="">{FIELDS}</form>' + TAIL
OUTSIDE_FORM = {
    "image before the refresh button": HEAD + f'<div><img src="{CHALLENGE}" alt="">{REFRESH}</div>'
                                              f'<form method="post">{FIELDS}</form>' + TAIL,
    "refresh button before the image": HEAD + f'<div>{REFRESH}<img src="{CHALLENGE}" alt=""></div>'
                                              f'<form method="post">{FIELDS}</form>' + TAIL,
}
NO_IMAGE = HEAD + f'<form method="post">{FIELDS}</form>' + TAIL


@pytest.fixture(scope="module")
def chromium() -> Iterator[Any]:
    """A real headless Chromium page with every network request aborted."""
    try:
        session = BrowserSession(BrowserSettings(headless=True)).__enter__()
    except PlaywrightError as exc:
        pytest.skip(f"Playwright's Chromium is not available: {exc}")
    try:
        session.page.route("**/*", lambda route: route.abort())
        yield session.page
    finally:
        session.close()


def capture(page: Any) -> bytes | None:
    clock = Clock()
    fetch = ListingFetcher(page, BrowserSettings(headless=True), base_url=BASE, notify=lambda _: None,
                           sleep=clock.sleep, clock=clock, rng=random.Random(0))
    return fetch._captcha_image()


def test_image_inside_the_form_is_captured(chromium: Any) -> None:
    chromium.set_content(INSIDE_FORM)
    assert chromium.locator(FORM_ONLY_SELECTOR).count() == 1
    image = capture(chromium)
    assert image is not None and png_size(image) == (120, 40)


@pytest.mark.parametrize("layout", sorted(OUTSIDE_FORM))
def test_image_outside_the_form_is_captured_and_not_the_refresh_icon(chromium: Any, layout: str) -> None:
    chromium.set_content(OUTSIDE_FORM[layout])
    challenge = chromium.locator(f'img[src="{CHALLENGE}"]')
    assert challenge.count() == 1 and challenge.evaluate(IMAGE_LOADED_JS), "the image itself is loaded"
    assert chromium.locator(FORM_ONLY_SELECTOR).count() == 0, "the old selector found nothing here"
    image = capture(chromium)
    assert image is not None and png_size(image) == (120, 40)


def test_warning_tells_a_missing_image_and_describes_the_page_without_tokens(
        chromium: Any, caplog: pytest.LogCaptureFixture) -> None:
    chromium.set_content(NO_IMAGE.replace("</form>", f'</form>{REFRESH}'))
    with caplog.at_level(logging.WARNING):
        assert capture(chromium) is None
    assert ("no CAPTCHA image element was found near the CAPTCHA input within 15s; page: captcha input: in a "
            "form; img elements: 1 [data:image/png 16x16 loaded in button/link]; canvas elements: 0") in caplog.text
    assert "synthetic-csrf" not in caplog.text and "iVBOR" not in caplog.text


def test_warning_tells_an_image_that_never_loads(chromium: Any, caplog: pytest.LogCaptureFixture) -> None:
    html = INSIDE_FORM.replace(CHALLENGE, "/captcha/image?token=synthetic-token")
    chromium.set_content(html.replace("<head>", '<head><base href="https://honestpool.ru/">'))  # request aborted
    with caplog.at_level(logging.WARNING):
        assert capture(chromium) is None
    assert ("the CAPTCHA image did not finish loading within 15s; page: captcha input: in a form; img elements: 1 "
            "[/captcha/image 0x0 broken in form]; canvas elements: 0") in caplog.text
    assert "synthetic-token" not in caplog.text
