"""Access denials and rate limits stop the run; nothing tries to evade them (offline)."""

from __future__ import annotations

import re

import pytest

from scraper import browser
from scraper.browser import BrowserSession, BrowserSettings
from scraper.config import PROJECT_ROOT
from scraper.database import COMPLETED, PENDING, Database
from scraper.runner import plan_run, run_live
from tests.fake_playwright import FakePlaywright, factory, http, success

PAGE2 = "https://honestpool.ru/puzzle?id=71&page=2"
FAST = dict(page_delay_s=0, page_delay_jitter_s=0, backoff_base_s=0.01, backoff_max_s=0.02)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakePlaywright:
    fake = FakePlaywright()
    monkeypatch.setattr(browser, "BrowserSession", lambda settings: BrowserSession(settings, factory(fake)))
    return fake


def test_browser_launch_has_no_proxy_or_identity_overrides(fake: FakePlaywright) -> None:
    with BrowserSession(BrowserSettings(), factory(fake)):
        pass
    assert fake.chromium.launches == [{"headless": False, "handle_sigint": False}]
    assert fake.chromium.envs == [browser.browser_environment()], "the environment is passed on, not altered"
    assert fake.browser.context.kwargs == {"locale": "ru-RU", "viewport": {"width": 1400, "height": 900}}


def test_access_denial_stops_without_retry_or_relaunch(fake: FakePlaywright, db: Database) -> None:
    fake.browser.context.page.script = [http(403, page=2)]
    plan_run(db, 2, 3)
    summary = run_live(db, 2, 3, BrowserSettings(**FAST), max_pages=2, notify=lambda _: None)
    assert summary.stopped and "403" in summary.stopped
    assert fake.browser.context.page.calls == [PAGE2]
    assert len(fake.chromium.launches) == 1
    assert [(db.page(p)["status"], db.page(p)["attempts"]) for p in (2, 3)] == [(PENDING, 0), (PENDING, 0)]


def test_persistent_rate_limit_stops_after_limited_retries(fake: FakePlaywright, db: Database) -> None:
    fake.browser.context.page.script = [http(429, page=2)] * 3 + [success(3)]
    plan_run(db, 2, 3)
    summary = run_live(db, 2, 3, BrowserSettings(max_retries=2, **FAST), max_pages=2, notify=lambda _: None)
    assert summary.stopped and "429" in summary.stopped
    assert fake.browser.context.page.calls == [PAGE2] * 3
    assert len(fake.chromium.launches) == 1
    assert db.page(2)["status"] == PENDING and db.page(3)["status"] == PENDING


def test_successful_page_after_stop_needs_an_explicit_new_run(fake: FakePlaywright, db: Database) -> None:
    fake.browser.context.page.script = [success(2, [150]), http(403, page=3)]
    plan_run(db, 2, 3)
    summary = run_live(db, 2, 3, BrowserSettings(**FAST), max_pages=2, notify=lambda _: None)
    assert summary.completed == 1 and summary.stopped
    assert db.page(2)["status"] == COMPLETED and db.record_count() == 1


SOURCES = [PROJECT_ROOT / "main.py", *sorted((PROJECT_ROOT / "scraper").glob("*.py"))]
SOLVER_MODULE = "two_captcha.py"


def source_lines():
    for path in SOURCES:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            yield path.name, number, line


def test_source_contains_no_proxy_rotation_or_identity_spoofing() -> None:
    pattern = re.compile(r"proxy|user_agent|user-agent|stealth|fingerprint|anticaptcha|websiteURL", re.I)
    assert [f"{name}:{n}" for name, n, line in source_lines() if pattern.search(line)] == []


def test_captcha_service_calls_exist_only_in_the_solver_module() -> None:
    pattern = re.compile(r"api\.2captcha|createTask|getTaskResult|reportCorrect|reportIncorrect|in\.php|res\.php|"
                         r"ImageToText|clientKey|base64", re.I)
    offenders = {name for name, _, line in source_lines() if pattern.search(line)}
    assert offenders == {SOLVER_MODULE}


def test_only_the_solver_module_opens_network_connections_outside_the_browser() -> None:
    pattern = re.compile(r"^\s*(import|from)\s+(httpx|requests|urllib\.request|urllib\.error|socket|http\b|"
                         r"http\.client|aiohttp)", re.M)
    offenders = {path.name for path in SOURCES if pattern.search(path.read_text(encoding="utf-8"))}
    assert offenders == {SOLVER_MODULE}
    solver = (PROJECT_ROOT / "scraper" / SOLVER_MODULE).read_text(encoding="utf-8")
    assert re.findall(r"https?://[^\s\"']+", solver) == ["https://api.2captcha.com"]
    assert "honestpool" not in solver.lower() and "playwright" not in solver.lower()


def test_only_the_captcha_image_element_is_captured() -> None:
    lines = [(name, line.strip()) for name, _, line in source_lines() if "screenshot" in line]
    assert lines == [("browser.py", 'return image.screenshot(type="png", timeout=CLEANUP_TIMEOUT_MS)')]
    assert browser.CAPTCHA_IMAGE_SELECTOR == "img" and "document.images" in browser.FIND_CAPTCHA_IMAGE_JS


def test_api_key_never_reaches_the_fetch_path() -> None:
    withdrawal = ("from scraper.captcha_provider import withdraw_api_key\n", "        withdraw_api_key()\n")
    for module in ("browser.py", "runner.py", "parser.py", "database.py", "progress.py"):
        text = (PROJECT_ROOT / "scraper" / module).read_text(encoding="utf-8")
        if module == "browser.py":
            assert all(text.count(line) == 1 for line in withdrawal), "the browser only removes the key"
            for line in withdrawal:
                text = text.replace(line, "")
        assert "captcha_provider" not in text and "CAPTCHA_API_KEY" not in text and "api_key" not in text
