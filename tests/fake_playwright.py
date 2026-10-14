"""In-memory Playwright stand-ins for offline browser tests."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from scraper.browser import FIND_CAPTCHA_IMAGE_JS
from tests.html_fixtures import CAPTCHA_PAGE, captcha_form_page, listing_page

BLANK_URL = "about:blank"
CAPTCHA_URL = "https://honestpool.ru/captcha?url=puzzle?id=71"
# Synthetic PNG bytes standing in for the rendered CAPTCHA image element (challenge 1).
CAPTCHA_PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256))
_CHALLENGE_RE = re.compile(r"captcha_image\?challenge=(\d+)")


def challenge_png(challenge: int) -> bytes:
    """The rendered image of one CAPTCHA challenge; every challenge looks different."""
    return CAPTCHA_PNG if challenge == 1 else CAPTCHA_PNG + challenge.to_bytes(4, "big")


@dataclass
class FakeResponse:
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    request: Any = None

    @property
    def frame(self) -> Any:
        return self.request.frame if self.request is not None else None


class FakeFrame:
    def __init__(self, parent_frame: Any = None) -> None:
        self.parent_frame = parent_frame


@dataclass
class FakeRequest:
    url: str
    resource_type: str = "document"
    navigation: bool = False
    frame: Any = None
    failure: str | None = None
    redirected_from: Any = None

    def is_navigation_request(self) -> bool:
        return self.navigation


@dataclass
class ScriptedNav:
    """One scripted page.goto outcome."""
    url: str | None = None
    html: str | None = None
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    error: BaseException | None = None


class FakeLocator:
    """The CAPTCHA image element of the current page (found by FakePage.evaluate)."""

    def __init__(self, page: FakePage, selector: str) -> None:
        self._page, self.selector = page, selector

    def nth(self, index: int) -> FakeLocator:
        return self

    def screenshot(self, type: str = "png", timeout: float | None = None) -> bytes:
        self._page.screenshots.append(self.selector)
        if self._page.screenshot_error is not None:
            raise self._page.screenshot_error
        if self._page.captcha_image is not None:
            return self._page.captcha_image
        return challenge_png(int(m.group(1)) if (m := _CHALLENGE_RE.search(self._page.html)) else 1)


class FakePage:
    """Scripted page. Navigating to about:blank needs no network and never consumes the script.

    ``submissions`` holds the outcome of each CAPTCHA form submission (the page shown after the
    click); with none left, the click changes nothing, like a submission that never completes.
    """

    def __init__(self, script: list[ScriptedNav] | None = None,
                 submissions: list[ScriptedNav] | None = None) -> None:
        self.submissions = list(submissions or [])
        self.fills: list[tuple[str, str]] = []
        self.clicks: list[str] = []
        self.screenshots: list[str] = []
        self.screenshot_error: BaseException | None = None
        self.captcha_image: bytes | None = None  # None: each challenge of captcha_form has its own image
        self.image_loaded = [True]
        self.script = list(script or [])
        self.url = BLANK_URL
        self.html = ""
        self.calls: list[str] = []
        self.resets = 0
        self.content_reads = 0
        self.handlers: dict[str, list[Callable[[Any], None]]] = {}
        self.main_frame = FakeFrame()
        self.context: Any = None
        self.closed = False

    def on(self, event: str, handler: Callable[[Any], None]) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def emit(self, event: str, arg: Any = None) -> None:
        for handler in self.handlers.get(event, []):
            handler(self if arg is None else arg)

    def wait_for_load_state(self, state: str = "load", timeout: float | None = None) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def goto(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 30_000) -> FakeResponse | None:
        if url == BLANK_URL:
            self.resets += 1
            self.url, self.html = BLANK_URL, ""
            return None
        self.calls.append(url)
        if not self.script:
            raise AssertionError(f"unexpected navigation to {url}")
        step = self.script.pop(0)
        if step.error is not None:
            raise step.error
        self.url = step.url if step.url is not None else url
        self.html = step.html if step.html is not None else ""
        return FakeResponse(step.status, dict(step.headers))

    def content(self) -> str:
        self.content_reads += 1
        return self.html

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    def evaluate(self, expression: str) -> dict[str, Any]:
        """The CAPTCHA image search: an image exists while the page has the CAPTCHA form with an
        image; whether it has loaded comes from ``image_loaded`` (a queue; the last answer repeats)."""
        if expression != FIND_CAPTCHA_IMAGE_JS:
            raise AssertionError(f"unexpected script: {expression[:60]}")
        if not ('name="captcha"' in self.html and "<img" in self.html):
            return {"index": -1, "loaded": False, "summary": "fake page without a CAPTCHA image"}
        loaded = self.image_loaded.pop(0) if len(self.image_loaded) > 1 else self.image_loaded[0]
        return {"index": 0, "loaded": loaded, "summary": "fake page with a CAPTCHA image"}

    def fill(self, selector: str, value: str, timeout: float | None = None) -> None:
        if 'name="captcha"' not in self.html:
            raise PlaywrightTimeoutError(f"waiting for locator('{selector}')")
        self.fills.append((selector, value))

    def click(self, selector: str, timeout: float | None = None) -> None:
        self.clicks.append(selector)
        if self.submissions:
            step = self.submissions.pop(0)
            self.url = step.url if step.url is not None else self.url
            self.html = step.html if step.html is not None else ""


class FakeContext:
    def __init__(self) -> None:
        self.page = FakePage()
        self.closed = False
        self.timeout: int | None = None

    def set_default_navigation_timeout(self, timeout: int) -> None:
        self.timeout = timeout

    def new_page(self) -> FakePage:
        self.page.context = self
        return self.page

    def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self) -> None:
        self.context = FakeContext()
        self.closed = False

    def new_context(self, **kwargs: Any) -> FakeContext:
        self.context.kwargs = kwargs
        return self.context

    def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self, browser: FakeBrowser) -> None:
        self._browser = browser
        self.launches: list[dict[str, Any]] = []
        self.envs: list[dict[str, Any] | None] = []

    def launch(self, **kwargs: Any) -> FakeBrowser:
        self.envs.append(kwargs.pop("env", None))
        self.launches.append(kwargs)
        return self._browser


class FakePlaywright:
    def __init__(self) -> None:
        self.browser = FakeBrowser()
        self.chromium = FakeChromium(self.browser)
        self.stopped = False
        self.driver_env: dict[str, str] | None = None

    def start(self) -> FakePlaywright:
        self.driver_env = dict(os.environ)  # the real driver is started with a copy of os.environ
        return self

    def stop(self) -> None:
        self.stopped = True


def success(page: int, verified: list[int] | None = None) -> ScriptedNav:
    return ScriptedNav(html=listing_page(page, verified or []))


def captcha(return_url: str = CAPTCHA_URL) -> ScriptedNav:
    return ScriptedNav(url=return_url, html=CAPTCHA_PAGE)


def captcha_form(challenge: int = 1) -> ScriptedNav:
    return ScriptedNav(url=CAPTCHA_URL, html=captcha_form_page(challenge))


def http(status: int, page: int = 1, headers: dict[str, str] | None = None, html: str = "") -> ScriptedNav:
    return ScriptedNav(status=status, headers=headers or {}, html=html or listing_page(page))


def timeout(message: str = "Timeout 30000ms exceeded.") -> ScriptedNav:
    return ScriptedNav(error=PlaywrightTimeoutError(message))


def connection_error(message: str = "net::ERR_CONNECTION_RESET at https://honestpool.ru/") -> ScriptedNav:
    return ScriptedNav(error=PlaywrightError(message))


def factory(fake: FakePlaywright) -> Callable[[], FakePlaywright]:
    return lambda: fake
