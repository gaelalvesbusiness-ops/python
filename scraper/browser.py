"""Playwright browser session and the live listing-page fetcher.

The fetcher navigates one listing page at a time in a single, long-lived browser
context, paces navigations, retries transient failures with exponential backoff and
jitter, and never returns anything but the requested listing page:

* With a CAPTCHA solver configured, a CAPTCHA page is first solved automatically: only the
  CAPTCHA image element is captured and sent, the answer is typed into the site's own form,
  and verification is confirmed before scraping resumes. Rejected answers get a new attempt
  (a limited number per CAPTCHA, and a limited number of automatic verifications per session).
  An answer only counts as rejected (and is reported to 2Captcha as incorrect) once the site
  shows a CAPTCHA with a different image; any other result is uncertain and never reported.
  Each attempt is logged step by step, and the run's CAPTCHA counts are summarized at the end.
* Without a solver, or when automatic solving fails, each new CAPTCHA may be completed
  manually in the visible browser, up to a limited number per session; beyond it (and in
  headless mode) the run stops. A CAPTCHA shown again right after a verification always stops
  the run. Verification only counts once the browser shows a complete listing page of the
  puzzle; rejected answers, redirects, blank or partial pages keep the wait going until the
  timeout.
* HTTP 401/403 stop the run. HTTP 429 honours Retry-After, and stops the run when the
  requested wait is too long or the limit persists.
* Timeouts, connection errors and HTTP 5xx are retried a limited number of times.
* A navigation returns once DOMContentLoaded fires, or earlier once the complete
  server-rendered listing (or a CAPTCHA) is in the DOM while page scripts still load.
* An unfinished navigation is stopped (tab reset to about:blank, or replaced within the
  same context) before anything else is loaded, so navigations never overlap.
* CAPTCHA answer submissions are paced like navigations.
"""

from __future__ import annotations

import hashlib
import logging
import os
import random
import re
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from scraper.captcha_provider import withdraw_api_key
from scraper.config import DEFAULT_BASE_URL, TOTAL_PAGES
from scraper.pagination import page_segments, page_url
from scraper.parser import (
    ID_MISMATCH,
    INVALID_NUMBER,
    TableNotFoundError,
    UnsupportedLayoutError,
    parse_page,
)
from scraper.runner import StopRunError
from scraper.two_captcha import CaptchaSolution, InvalidImageError, SolverAccountError, SolverError

logger = logging.getLogger(__name__)

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_CAPTCHA_INPUT_RE = re.compile(r"""<input\b[^>]*\bname\s*=\s*["']captcha["']""", re.I)
_FIRST_ROW_RE = re.compile(r"""<tr\b[^>]*\bid\s*=\s*["']tag_search_(\d+)["']""", re.I)
_CONNECTION_ERROR_RE = re.compile(r"net::ERR_|ECONNRESET|ECONNREFUSED|socket hang up|Connection closed", re.I)
_NET_ERROR_RE = re.compile(r"net::(ERR_[A-Z0-9_]+)")
BLANK_URL = "about:blank"
LOAD_POLL_S = 1.0
CLEANUP_TIMEOUT_MS = 5_000
# The site's CAPTCHA form posts csrf, captcha and btn_check_captcha.
CAPTCHA_INPUT_SELECTOR = 'input[name="captcha"]'
CAPTCHA_SUBMIT_SELECTOR = '[name="btn_check_captcha"]'
CAPTCHA_IMAGE_SELECTOR = "img"
CAPTCHA_IMAGE_WAIT_S = 15.0
# Picks the CAPTCHA image among document.images: the first image in the closest element around the
# CAPTCHA input that has one (its form, then the card around it, ...), skipping images inside buttons
# or links and loaded images smaller than 40x15 (refresh icons). The summary describes the page
# without attribute values or query strings, so no token reaches the log.
FIND_CAPTCHA_IMAGE_JS = """() => {
  const input = document.querySelector('input[name="captcha"]');
  const images = Array.from(document.images);
  const loaded = img => img.complete && img.naturalWidth > 0;
  const usable = img => !img.closest('button, a, [role="button"]')
    && !(loaded(img) && (img.naturalWidth < 40 || img.naturalHeight < 15));
  let index = -1;
  for (let node = input && input.parentElement; node && index < 0; node = node.parentElement) {
    index = images.findIndex(img => node.contains(img) && usable(img));
  }
  const where = src => {
    if (src.startsWith('data:')) return src.split(/[;,]/)[0];
    try { return new URL(src, location.href).pathname; } catch (error) { return '(no address)'; }
  };
  const described = images.slice(0, 5).map(img => `${where(img.currentSrc || img.src || '')} `
    + `${img.naturalWidth}x${img.naturalHeight} ${loaded(img) ? 'loaded' : img.complete ? 'broken' : 'loading'}`
    + `${img.closest('form') ? ' in form' : ''}${img.closest('button, a, [role="button"]') ? ' in button/link' : ''}`);
  const summary = `captcha input: ${input ? (input.closest('form') ? 'in a form' : 'outside any form') : 'none'}; `
    + `img elements: ${images.length}${described.length ? ' [' + described.join('; ') + ']' : ''}; `
    + `canvas elements: ${document.querySelectorAll('canvas').length}`;
  return {index, loaded: index >= 0 && loaded(images[index]), summary};
}"""
CAPTCHA_ACCEPTED = "answer accepted"
CAPTCHA_REJECTED = "answer rejected"


@dataclass(frozen=True)
class BrowserSettings:
    headless: bool = False
    locale: str = "ru-RU"
    viewport_width: int = 1400
    viewport_height: int = 900
    navigation_timeout_ms: int = 30_000
    page_delay_s: float = 15.0
    page_delay_jitter_s: float = 5.0
    max_retries: int = 2
    backoff_base_s: float = 10.0
    backoff_max_s: float = 300.0
    max_retry_after_s: float = 600.0
    max_manual_verifications: int = 10
    captcha_wait_s: float = 600.0
    captcha_poll_s: float = 2.0
    captcha_auto_attempts: int = 3
    max_auto_verifications: int = 3

    @property
    def max_captcha_images(self) -> int:
        """Images one run may send to the solver: every CAPTCHA that can get automatic attempts
        (each confirmed automatic verification, each manual fallback and the one that stops the
        run) times the attempts per CAPTCHA."""
        return self.captcha_auto_attempts * (self.max_auto_verifications + self.max_manual_verifications + 1)


@dataclass(frozen=True)
class CaptchaSummary:
    """CAPTCHA counts for one run; images not found and uncertain outcomes were never reported."""

    shown: int = 0
    automatic_verifications: int = 0
    images_sent: int = 0
    image_limit: int = 0
    rejections: int = 0
    uncertain: int = 0
    solver_errors: int = 0
    images_not_found: int = 0
    manual_fallbacks: int = 0
    manual_verifications: int = 0

    def describe(self) -> str:
        return (f"CAPTCHA summary: {self.shown} shown; {self.automatic_verifications} automatic verification(s) "
                f"confirmed; {self.images_sent} image(s) sent to 2Captcha (limit {self.image_limit} per run); "
                f"{self.rejections} answer(s) rejected by the website; {self.uncertain} uncertain outcome(s) "
                f"(not reported to 2Captcha); {self.solver_errors} 2Captcha error(s); {self.images_not_found} "
                f"image(s) not found; {self.manual_fallbacks} manual fallback(s); {self.manual_verifications} "
                f"manual verification(s)")


class CaptchaSolver(Protocol):
    def solve_image(self, image: bytes) -> CaptchaSolution: ...

    def report(self, solution: CaptchaSolution, *, correct: bool) -> None: ...


class FetchError(RuntimeError):
    """The page could not be fetched; the page is marked failed and may be retried later."""


class TransientFetchError(FetchError):
    """Timeout, connection error or HTTP 5xx that persisted through the allowed retries."""


class AccessRestrictedError(StopRunError):
    """The site refused access (HTTP 401/403); the run stops."""


class RateLimitedError(AccessRestrictedError):
    """HTTP 429 persisted or asked for a longer wait than allowed; the run stops."""


class CaptchaRequiredError(AccessRestrictedError):
    """A CAPTCHA appeared that may not be completed manually in this session; the run stops."""


class NavigationCleanupError(StopRunError):
    """An unfinished navigation could not be stopped, so no further navigation is safe; the run stops."""


def ensure_browsers_path(env: dict[str, str] | os._Environ[str] = os.environ, home: Path | None = None) -> None:
    """Point Playwright at the user's installed browsers when PLAYWRIGHT_BROWSERS_PATH is stale.

    Some shells export a sandbox cache path without browsers; Step 2X only worked once
    the installed ``~/.cache/ms-playwright`` browsers were used.
    """
    default = (home or Path.home()) / ".cache" / "ms-playwright"
    current = env.get("PLAYWRIGHT_BROWSERS_PATH")
    if current and current != "0" and not _has_chromium(Path(current)) and _has_chromium(default):
        logger.info("PLAYWRIGHT_BROWSERS_PATH has no Chromium; using %s", default)
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(default)


def _has_chromium(path: Path) -> bool:
    return path.is_dir() and any(path.glob("chromium-*"))


def browser_environment(env: Mapping[str, str] = os.environ) -> dict[str, str]:
    """Chromium's environment: everything except the CAPTCHA settings, the API key above all."""
    return {name: value for name, value in env.items() if not name.upper().startswith("CAPTCHA_")}


def is_captcha_page(url: str, html: str) -> bool:
    if urlparse(url).path.rstrip("/").lower().endswith("/captcha"):
        return True
    title = _TITLE_RE.search(html)
    if title and "captcha" in title.group(1).lower():
        return True
    return bool(_CAPTCHA_INPUT_RE.search(html))


CAPTCHA_SHOWN = "CAPTCHA page"


def listing_location_problem(url: str, html: str, base_url: str = DEFAULT_BASE_URL) -> str | None:
    """Why the browser is not on a listing page of the puzzle (ignoring its content), or None."""
    final, base = urlparse(url), urlparse(base_url)
    if final.scheme != base.scheme or (final.hostname or "").lower() != (base.hostname or "").lower():
        return "not on the HonestPool site"
    if is_captcha_page(url, html):
        return CAPTCHA_SHOWN
    query = parse_qs(final.query)
    base_id = parse_qs(base.query).get("id", [None])[-1]
    if final.path.rstrip("/") != base.path.rstrip("/") or query.get("id", [None])[-1] != base_id:
        return "not the puzzle listing"
    page_text = query.get("page", ["1"])[-1]
    if not page_text.isdigit() or not 1 <= int(page_text) <= TOTAL_PAGES:
        return "invalid page parameter"
    return None


def listing_problem(url: str, html: str, base_url: str = DEFAULT_BASE_URL) -> str | None:
    """Why the browser is not on a complete listing page of the puzzle, or None when it is.

    Any page of the puzzle's original listing qualifies, because the CAPTCHA redirect
    target (``captcha?url=puzzle?id=71&page=N``) can lose its ``&page=N`` part.
    """
    problem = listing_location_problem(url, html, base_url)
    if problem is not None:
        return problem
    expected = page_segments(int(parse_qs(urlparse(url).query).get("page", ["1"])[-1]))
    try:
        result = parse_page(html)
    except (TableNotFoundError, UnsupportedLayoutError) as exc:
        return f"no valid data table: {exc}"
    if result.rows_inspected != len(expected):
        return f"{result.rows_inspected} listing rows, expected {len(expected)}"
    numbers = {r.number for r in result.records}
    numbers |= {r.number for r in result.rejections
                if r.number is not None and r.reason not in (INVALID_NUMBER, ID_MISMATCH)}
    if numbers != set(expected):
        return "listing rows do not match the page's segments"
    return None


def safe_url(url: str) -> str:
    """The URL reduced to scheme, host, path and the listing's ``id``/``page`` parameters."""
    parts = urlparse(url)
    query = parse_qs(parts.query)
    kept = "&".join(f"{k}={query[k][-1]}" for k in ("id", "page") if k in query)
    return f"{parts.scheme}://{parts.hostname or ''}{parts.path}" + (f"?{kept}" if kept else "")


def listing_state(html: str, page_number: int) -> str:
    rows, expected = len(_FIRST_ROW_RE.findall(html)), len(page_segments(page_number))
    if rows >= expected:
        return f"complete ({rows} rows)"
    return f"partial ({rows} of {expected} rows)" if rows else "absent"


class NavigationMonitor:
    """Sanitized diagnostics for the current navigation, collected from Playwright page events.

    Page lifecycle events are not tied to a document, and the previous document may still be
    loading when the next navigation starts. Events therefore count only once this navigation's
    document has committed, responses only for its own document request (and its redirects),
    and requests started before that commit are left to the previous document.

    Only lifecycle timings, main-document HTTP statuses, resource types, request counts and
    ``net::ERR_*`` categories are kept: never URLs of other resources, headers, cookies or bodies.
    """

    def __init__(self, clock: Callable[[], float], site_host: str) -> None:
        self._clock = clock
        self._site_host = site_host.lower()
        self.start()

    def attach(self, page: Any) -> None:
        on = getattr(page, "on", None)
        if on is None:
            return
        for event, handler in (("request", self._on_request), ("requestfinished", self._on_finished),
                               ("requestfailed", self._on_failed), ("response", self._on_response),
                               ("framenavigated", self._on_frame_navigated),
                               ("domcontentloaded", lambda _: self.mark_document_event("domcontentloaded")),
                               ("load", lambda _: self.mark_document_event("load"))):
            on(event, _diagnostic_handler(handler))

    def start(self, target_url: str | None = None) -> None:
        self.started = self._clock()
        self.target_url = target_url
        self.events: dict[str, float] = {}
        self.document_statuses: list[int] = []
        self.pending: dict[int, tuple[str, bool]] = {}
        self.failures: Counter[str] = Counter()
        self.listing = "not checked"
        self._documents: set[int] = set()

    def mark(self, event: str) -> None:
        self.events.setdefault(event, self._clock() - self.started)

    def mark_document_event(self, event: str) -> None:
        """Record DOMContentLoaded/load only for the document this navigation committed."""
        if "commit" in self.events:
            self.mark(event)

    def note_document(self, status: int) -> None:
        """Fallback when no response event was seen: the status is known, its arrival time is not."""
        if not self.document_statuses:
            self.document_statuses.append(status)

    def summary(self) -> str:
        commit = self.events.get("commit")

        def at(event: str) -> str:
            seconds = self.events.get(event)
            if seconds is None:
                return "no"
            if event in ("domcontentloaded", "load") and (commit is None or seconds < commit):
                return "unknown"
            return f"{seconds:.1f}s"

        response = f"{self.events['response']:.1f}s" if "response" in self.events else "unknown"
        document = ("->".join(map(str, self.document_statuses)) + f" at {response}"
                    if self.document_statuses else "not received")
        types = Counter(kind for kind, _ in self.pending.values())
        same_site = sum(1 for _, local in self.pending.values() if local)
        outstanding = str(len(self.pending))
        if self.pending:
            outstanding += f" ({', '.join(f'{k} {n}' for k, n in sorted(types.items()))}; {same_site} same-site)"
        failed = ", ".join(f"{k} x{n}" for k, n in sorted(self.failures.items())) or "none"
        return (f"main document: {document}; committed: {at('commit')}; DOMContentLoaded: {at('domcontentloaded')}; "
                f"load: {at('load')}; listing: {self.listing}; outstanding requests: {outstanding}; "
                f"failed requests: {failed}")

    def _on_request(self, request: Any) -> None:
        if self._is_own_document(request):
            self._documents.add(id(request))
        elif "commit" not in self.events:
            return  # issued by the previous document before this navigation committed
        host = (urlparse(request.url).hostname or "").lower()
        local = host == self._site_host or host.endswith(f".{self._site_host}")
        self.pending[id(request)] = (request.resource_type, local)

    def _is_own_document(self, request: Any) -> bool:
        if self.target_url is None or not request.is_navigation_request() or request.frame.parent_frame is not None:
            return False
        redirected_from = getattr(request, "redirected_from", None)
        return request.url == self.target_url or (redirected_from is not None
                                                  and id(redirected_from) in self._documents)

    def _on_frame_navigated(self, frame: Any) -> None:
        if frame.parent_frame is None and self.document_statuses:
            self.mark("commit")

    def _on_finished(self, request: Any) -> None:
        self.pending.pop(id(request), None)

    def _on_failed(self, request: Any) -> None:
        if self.pending.pop(id(request), None) is None:
            return  # started before this navigation, e.g. cancelled by the cleanup
        match = _NET_ERROR_RE.search(request.failure or "")
        self.failures[match.group(1) if match else "other"] += 1

    def _on_response(self, response: Any) -> None:
        if id(response.request) in self._documents:
            self.document_statuses.append(response.status)
            self.events["response"] = self._clock() - self.started


def _diagnostic_handler(handler: Callable[[Any], None]) -> Callable[[Any], None]:
    def guarded(arg: Any) -> None:
        try:
            handler(arg)
        except Exception as exc:  # noqa: BLE001 - diagnostics must never break a navigation
            logger.debug("navigation diagnostics skipped an event: %s", type(exc).__name__)
    return guarded


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Seconds to wait from a Retry-After header (delta-seconds or HTTP-date)."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - (now or datetime.now(timezone.utc))).total_seconds())


class RequestPacer:
    """Keeps at least ``delay`` (+ random jitter) seconds between navigations."""

    def __init__(self, delay: float, jitter: float, *, sleep: Callable[[float], None],
                 clock: Callable[[], float], rng: random.Random) -> None:
        self.delay, self.jitter = delay, jitter
        self._sleep, self._clock, self._rng = sleep, clock, rng
        self._last: float | None = None

    def wait(self) -> None:
        if self._last is not None:
            remaining = self._last + self.delay + self._rng.uniform(0, self.jitter) - self._clock()
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()


class BrowserSession:
    """Owns Playwright, the Chromium browser, one context and one page for the whole run.

    The Playwright driver copies this process's environment when it starts, and Chromium gets
    an explicit copy without the CAPTCHA settings, so neither receives the 2Captcha key (create
    the solver before entering the session). Ctrl+C is left to this process: the interrupted
    page is returned to pending first, and only then is the browser closed by ``close``.
    """

    def __init__(self, settings: BrowserSettings, playwright_factory: Callable[[], Any] = sync_playwright) -> None:
        self.settings = settings
        self._factory = playwright_factory
        self._playwright: Any = None
        self.browser: Any = None
        self.context: Any = None
        self.page: Any = None

    def __enter__(self) -> BrowserSession:
        ensure_browsers_path()
        withdraw_api_key()
        s = self.settings
        self._playwright = self._factory().start()
        try:
            self.browser = self._playwright.chromium.launch(headless=s.headless, env=browser_environment(),
                                                            handle_sigint=False)
            self.context = self.browser.new_context(
                locale=s.locale, viewport={"width": s.viewport_width, "height": s.viewport_height})
            self.context.set_default_navigation_timeout(s.navigation_timeout_ms)
            self.page = self.context.new_page()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        for obj, method in ((self.context, "close"), (self.browser, "close"), (self._playwright, "stop")):
            if obj is None:
                continue
            try:
                getattr(obj, method)()
            except Exception as exc:  # noqa: BLE001 - best-effort shutdown
                logger.debug("ignoring error during browser shutdown: %s", exc)
        self.context = self.browser = self._playwright = self.page = None


class ListingFetcher:
    """Callable ``page_number -> html`` for the runner, reusing one Playwright page."""

    def __init__(self, page: Any, settings: BrowserSettings, *, base_url: str = DEFAULT_BASE_URL,
                 notify: Callable[[str], None] = print, sleep: Callable[[float], None] | None = None,
                 clock: Callable[[], float] | None = None, rng: random.Random | None = None,
                 solver: CaptchaSolver | None = None) -> None:
        self.page = page
        self.settings = settings
        self.base_url = base_url
        self.notify = notify
        self.solver = solver
        self.solver_disabled: str | None = None
        self.auto_verifications = 0
        self.captcha_submissions = 0
        self.captchas_shown = 0
        self.images_sent = 0
        self.rejections = 0
        self.uncertain_outcomes = 0
        self.solver_errors = 0
        self.images_not_found = 0
        self.manual_fallbacks = 0
        self.auto_wait_s = 0.0
        sleep, clock = sleep or time.sleep, clock or time.monotonic
        self._sleep, self._clock = sleep, clock
        self._rng = rng or random.Random()
        self.pacer = RequestPacer(settings.page_delay_s, settings.page_delay_jitter_s,
                                  sleep=sleep, clock=clock, rng=self._rng)
        self.manual_verifications = 0
        self.manual_wait_s = 0.0
        self.navigations = 0
        self.monitor = NavigationMonitor(clock, urlparse(base_url).hostname or "")
        self.monitor.attach(page)

    def __call__(self, page_number: int) -> str:
        url = page_url(self.base_url, page_number)
        retries = 0
        captcha_completed = False
        while True:
            try:
                html = self._navigate(url, page_number)
            except _CaptchaShown:
                if captcha_completed:
                    raise CaptchaRequiredError(f"CAPTCHA shown again right after verification for {url}") from None
                self.captchas_shown += 1
                logger.info("CAPTCHA %d shown for page %d", self.captchas_shown, page_number)
                self._complete_captcha()
                captcha_completed = True
                continue
            except _Retryable as exc:
                if exc.retry_after is not None and exc.retry_after > self.settings.max_retry_after_s:
                    raise RateLimitedError(f"{exc}: server asked to wait {exc.retry_after:.0f}s for {url}") from None
                if retries >= self.settings.max_retries:
                    if exc.rate_limited:
                        raise RateLimitedError(f"HTTP 429 persisted after {retries} retries for {url}") from None
                    raise TransientFetchError(f"{exc} (gave up after {retries} retries)") from None
                retries += 1
                wait = max(self._backoff(retries), exc.retry_after or 0.0)
                logger.warning("page %d: %s; retry %d/%d in %.0fs",
                               page_number, exc, retries, self.settings.max_retries, wait)
                self._sleep(wait)
                continue
            return html

    @property
    def captcha_time_s(self) -> float:
        """Time spent on CAPTCHAs (automatic and manual), excluded from progress estimates."""
        return self.auto_wait_s + self.manual_wait_s

    def captcha_summary(self) -> CaptchaSummary:
        return CaptchaSummary(
            shown=self.captchas_shown, automatic_verifications=self.auto_verifications,
            images_sent=self.images_sent, image_limit=self.settings.max_captcha_images,
            rejections=self.rejections, uncertain=self.uncertain_outcomes, solver_errors=self.solver_errors,
            images_not_found=self.images_not_found, manual_fallbacks=self.manual_fallbacks,
            manual_verifications=self.manual_verifications)

    # --- navigation ----------------------------------------------------------

    def _navigate(self, url: str, page_number: int) -> str:
        self.pacer.wait()
        self.navigations += 1
        timeout_ms = self.settings.navigation_timeout_ms
        self.monitor.start(url)
        started = self.monitor.started
        deadline = started + timeout_ms / 1000
        logger.info("page %d: navigation %d started (timeout %.0fs)", page_number, self.navigations, timeout_ms / 1000)
        try:
            response = self.page.goto(url, wait_until="commit", timeout=timeout_ms)
        except PlaywrightTimeoutError as exc:
            raise self._abandon(page_number, f"navigation timeout after {self._clock() - started:.1f}s: "
                                             f"{_first_line(exc)}") from None
        except PlaywrightError as exc:
            if _CONNECTION_ERROR_RE.search(str(exc)):
                raise self._abandon(page_number, f"connection error after {self._clock() - started:.1f}s: "
                                                 f"{_first_line(exc)}") from None
            self._abandon(page_number, f"browser error: {_first_line(exc)}")
            raise FetchError(f"browser error: {_first_line(exc)}") from None
        if response is None:
            raise FetchError(f"no response for {url}")
        self.monitor.mark("commit")
        self.monitor.note_document(response.status)

        html = self._wait_for_document(page_number, deadline)
        if html is None:
            raise self._abandon(page_number, f"navigation timeout after {self._clock() - started:.1f}s: "
                                             f"HTTP {response.status} received but the page did not finish loading")
        self.monitor.listing = listing_state(html, page_number)
        status = response.status
        logger.info("page %d: HTTP %d in %.1fs at %s; %s", page_number, status, self._clock() - started,
                    safe_url(self.page.url), self.monitor.summary())
        if is_captcha_page(self.page.url, html):
            raise _CaptchaShown()
        if status in (401, 403):
            raise AccessRestrictedError(f"HTTP {status} for {url}; access is restricted")
        if status == 429:
            raise _Retryable("HTTP 429 rate limited", rate_limited=True,
                             retry_after=parse_retry_after(_header(response, "retry-after")))
        if 500 <= status < 600:
            raise _Retryable(f"HTTP {status}", retry_after=parse_retry_after(_header(response, "retry-after")))
        if status != 200:
            raise FetchError(f"unexpected HTTP {status} for {url}")
        self._check_location(page_number)
        self._check_first_row(html, page_number)
        problem = listing_problem(self.page.url, html, self.base_url)
        if problem is not None:
            raise FetchError(f"page {page_number}: incomplete or invalid listing: {problem}")
        return html

    def _wait_for_document(self, page_number: int, deadline: float) -> str | None:
        """HTML once DOMContentLoaded fires, or earlier once a complete listing or a CAPTCHA is in the DOM.

        The listing is server-rendered, so the complete table is enough even while the page's
        scripts are still downloading. Returns None when neither happens before ``deadline``.
        """
        expected_rows = len(page_segments(page_number))
        while (remaining := deadline - self._clock()) > 0:
            try:
                poll_ms = max(1, round(min(remaining, LOAD_POLL_S) * 1000))
                self.page.wait_for_load_state("domcontentloaded", timeout=poll_ms)
            except PlaywrightTimeoutError:
                pass
            except PlaywrightError:
                self._sleep(min(remaining, LOAD_POLL_S))
            else:
                self.monitor.mark_document_event("domcontentloaded")
                snapshot = self._snapshot()
                if snapshot is not None:
                    return snapshot[1]
                continue
            snapshot = self._snapshot()
            if snapshot is None:
                continue
            url, html = snapshot
            if is_captcha_page(url, html):
                return html
            if len(_FIRST_ROW_RE.findall(html)) >= expected_rows and listing_problem(url, html, self.base_url) is None:
                self.monitor.mark("listing complete")
                return html
        return None

    def _abandon(self, page_number: int, message: str) -> _Retryable:
        """Log diagnostics for an unfinished navigation, stop it, and return the retryable error."""
        if "commit" in self.monitor.events:
            snapshot = self._snapshot()
            if snapshot is not None:
                self.monitor.listing = listing_state(snapshot[1], page_number)
        else:
            self.monitor.listing = "absent (no document committed)"
        logger.warning("page %d: %s; %s", page_number, message, self.monitor.summary())
        self._stop_pending_navigation(page_number)
        return _Retryable(message)

    def _stop_pending_navigation(self, page_number: int) -> None:
        """Make sure an unfinished navigation cannot overlap the next one.

        Playwright stops waiting on a timeout but Chromium keeps loading. Navigating the tab
        to about:blank (no network request) cancels that load. If it fails, the tab is
        replaced by a new one in the same browser context, which keeps the cookies and the
        verified session. If neither works, the run stops.
        """
        try:
            self.page.goto(BLANK_URL, wait_until="commit", timeout=CLEANUP_TIMEOUT_MS)
            if self.page.url == BLANK_URL:
                logger.info("page %d: unfinished navigation stopped; tab reset to %s", page_number, BLANK_URL)
                return
            reason = f"tab stayed at {safe_url(self.page.url)}"
        except PlaywrightError as exc:
            reason = _first_line(exc)
        context = getattr(self.page, "context", None)
        if context is None:
            raise NavigationCleanupError(f"could not stop the unfinished navigation for page {page_number}: {reason}")
        old = self.page
        try:
            self.page = context.new_page()
        except PlaywrightError as exc:
            raise NavigationCleanupError(f"could not replace the tab after page {page_number}: "
                                         f"{_first_line(exc)}") from None
        try:
            old.close()
        except PlaywrightError as exc:
            logger.debug("ignoring error while closing the abandoned tab: %s", _first_line(exc))
        self.monitor.attach(self.page)
        logger.warning("page %d: replaced the tab within the same browser context (%s)", page_number, reason)

    def _check_location(self, page_number: int) -> None:
        final, base = urlparse(self.page.url), urlparse(self.base_url)
        query = {k: v[-1] for k, v in parse_qs(final.query).items()}
        base_id = parse_qs(base.query).get("id", [None])[-1]
        expected_page = None if page_number == 1 else str(page_number)
        if (final.path.rstrip("/") != base.path or query.get("id") != base_id
                or query.get("page") != expected_page):
            raise FetchError(f"expected listing page {page_number}, browser is at {final.path}?{final.query}")

    @staticmethod
    def _check_first_row(html: str, page_number: int) -> None:
        match = _FIRST_ROW_RE.search(html)
        expected = page_segments(page_number)[0]
        if match and int(match.group(1)) != expected:
            raise FetchError(f"page {page_number} starts at segment {match.group(1)}, expected {expected}")

    # --- CAPTCHA -------------------------------------------------------------

    def _complete_captcha(self) -> None:
        """Solve the CAPTCHA automatically when a solver is configured, else (or on failure) manually."""
        if self.solver is not None:
            started = self._clock()
            try:
                failure = self._solve_automatically(self.solver)
            finally:
                self.auto_wait_s += self._clock() - started
            if failure is None or self._already_verified():
                return
            if self.settings.headless:
                logger.warning("CAPTCHA %d: automatic solving failed (%s); no manual fallback in headless mode",
                               self.captchas_shown, failure)
                raise CaptchaRequiredError(f"automatic CAPTCHA solving failed ({failure}); manual verification "
                                           f"is impossible in headless mode")
            self.manual_fallbacks += 1
            logger.warning("CAPTCHA %d: manual fallback (%s)", self.captchas_shown, failure)
            self.notify(f"Automatic CAPTCHA solving failed ({failure}); falling back to manual verification.")
        started = self._clock()
        try:
            self._wait_for_manual_verification()
        finally:
            self.manual_wait_s += self._clock() - started

    def _solve_automatically(self, solver: CaptchaSolver) -> str | None:
        """None once the site accepted an answer and shows the listing, else why it did not work."""
        s = self.settings
        if self.solver_disabled is not None:
            return self.solver_disabled
        if self.auto_verifications >= s.max_auto_verifications:
            return f"the limit of {s.max_auto_verifications} automatic verifications for this session is reached"
        last = "no attempt was made"
        image: bytes | None = None  # the new CAPTCHA's image, once a rejection was confirmed with it
        for attempt in range(1, s.captcha_auto_attempts + 1):
            tag = f"CAPTCHA {self.captchas_shown} attempt {attempt}/{s.captcha_auto_attempts}"
            if self.images_sent >= s.max_captcha_images:
                logger.warning("%s: the limit of %d images per run is reached; nothing sent", tag,
                               s.max_captcha_images)
                return f"the limit of {s.max_captcha_images} CAPTCHA images per run is reached"
            image = image or self._captcha_image()
            if image is None:
                self.images_not_found += 1
                logger.warning("%s: image not found; nothing sent to 2Captcha", tag)
                return "the CAPTCHA image was not found on the page or could not be captured"
            logger.info("%s: image captured (%d bytes, hash %s)", tag, len(image), _image_hash(image))
            self.notify(f"CAPTCHA shown. Sending the CAPTCHA image to the CAPTCHA service "
                        f"(attempt {attempt} of {s.captcha_auto_attempts}).")
            started = self._clock()
            try:
                self.images_sent += 1
                solution = solver.solve_image(image)
            except InvalidImageError as exc:
                self.images_sent -= 1
                self.solver_errors += 1
                last = str(exc)
                logger.warning("%s: image not sent to 2Captcha: %s", tag, exc)
                image = None
                continue
            except SolverAccountError as exc:
                self.solver_errors += 1
                logger.warning("%s: 2Captcha account problem, automatic solving is off for this run: %s", tag, exc)
                self.solver_disabled = f"automatic solving is off for this run: {exc}"
                return self.solver_disabled
            except SolverError as exc:
                self.solver_errors += 1
                last = str(exc)
                logger.warning("%s: no answer from 2Captcha: %s", tag, exc)
                image = None
                continue
            logger.info("%s: answer received from 2Captcha (task %s, %d characters, after %.0fs)", tag,
                        solution.task_id, len(solution.answer), self._clock() - started)
            outcome, image = self._submit_answer(solution.answer, image, tag)
            if outcome == CAPTCHA_ACCEPTED:
                solver.report(solution, correct=True)
                self.auto_verifications += 1
                destination = safe_url(self.page.url)
                logger.info("%s: website acceptance confirmed at %s; reported to 2Captcha as correct", tag,
                            destination)
                self.notify(f"Verification succeeded automatically: the browser reached the listing at "
                            f"{destination}; continuing with the same browser session.")
                return None
            if outcome != CAPTCHA_REJECTED:
                self.uncertain_outcomes += 1
                logger.warning("%s: verification outcome uncertain (%s); not reported to 2Captcha", tag, outcome)
                return outcome
            self.rejections += 1
            solver.report(solution, correct=False)
            logger.warning("%s: website rejection confirmed (a new CAPTCHA image was shown, hash %s); "
                           "reported to 2Captcha as incorrect", tag, _image_hash(image))
            last = "the site rejected the answer"
            self.notify(f"The site rejected the automatic answer (attempt {attempt} of {s.captcha_auto_attempts}).")
        return f"{s.captcha_auto_attempts} attempt(s) failed; last: {last}"

    def _captcha_image(self) -> bytes | None:
        """PNG of the CAPTCHA image element only, captured once it is in the page and loaded (no extra request).

        A CAPTCHA is detected as soon as its URL commits, so the form and image may still be loading.
        The image may sit inside the CAPTCHA form or beside it (FIND_CAPTCHA_IMAGE_JS).
        """
        deadline = self._clock() + CAPTCHA_IMAGE_WAIT_S
        try:
            while not (found := self.page.evaluate(FIND_CAPTCHA_IMAGE_JS))["loaded"]:
                if self._clock() >= deadline:
                    problem = ("the CAPTCHA image did not finish loading" if found["index"] >= 0
                               else "no CAPTCHA image element was found near the CAPTCHA input")
                    logger.warning("%s within %.0fs; page: %s", problem, CAPTCHA_IMAGE_WAIT_S, found["summary"])
                    return None
                self._sleep(LOAD_POLL_S)
            image = self.page.locator(CAPTCHA_IMAGE_SELECTOR).nth(found["index"])
            return image.screenshot(type="png", timeout=CLEANUP_TIMEOUT_MS)
        except PlaywrightError as exc:
            logger.warning("the CAPTCHA image could not be captured: %s", _first_line(exc))
            return None

    def _submit_answer(self, answer: str, image: bytes, tag: str) -> tuple[str, bytes | None]:
        """Type the answer to ``image`` into the site's form and classify the site's response.

        CAPTCHA_ACCEPTED once the site redirects from the CAPTCHA to the puzzle listing (it may
        still be loading: the requested page is fetched and fully validated next). CAPTCHA_REJECTED,
        with the new image, only once a CAPTCHA with a different image is shown: the site issued a
        new challenge. Anything else is uncertain, including a changed page that still shows the
        same image or an image that cannot be compared; then the reason is returned.
        """
        s = self.settings
        self.pacer.wait()
        before = self._snapshot()  # taken after the pacing wait, so page changes during it are not a result
        self.captcha_submissions += 1
        try:
            self.page.fill(CAPTCHA_INPUT_SELECTOR, answer, timeout=CLEANUP_TIMEOUT_MS)
            self.page.click(CAPTCHA_SUBMIT_SELECTOR, timeout=CLEANUP_TIMEOUT_MS)
        except PlaywrightError as exc:
            return f"the answer could not be entered: {_first_line(exc)}", None
        logger.info("%s: answer submitted to the website", tag)
        deadline = self._clock() + s.navigation_timeout_ms / 1000
        checked, last_seen = before, None
        while self._clock() < deadline:
            self._sleep(s.captcha_poll_s)
            current = self._snapshot()
            if current is None or current == checked:
                continue  # the submission is still being processed, or this page was already checked
            checked = current
            url, html = current
            problem = listing_location_problem(url, html, self.base_url)
            if problem is None:
                return CAPTCHA_ACCEPTED, None
            if problem == CAPTCHA_SHOWN:
                new_image = self._captcha_image()
                if new_image is not None and new_image != image:
                    return CAPTCHA_REJECTED, new_image
                last_seen = ("a CAPTCHA with the same image" if new_image is not None
                             else "a CAPTCHA whose image could not be compared")
            else:
                last_seen = f"{problem} at {safe_url(url)}"
            logger.info("%s: not confirmed yet: %s", tag, last_seen)
        reason = f"the result of the submitted answer was not confirmed within {s.navigation_timeout_ms / 1000:.0f}s"
        return (f"{reason} (last seen: {last_seen})" if last_seen else reason), None

    def _already_verified(self) -> bool:
        """True when the browser already shows the listing, e.g. an answer the site accepted late."""
        current = self._snapshot()
        if current is None or listing_problem(*current, self.base_url) is not None:
            return False
        destination = safe_url(current[0])
        logger.info("verification already completed at %s; no manual verification needed", destination)
        self.notify(f"Verification detected: the browser already shows the listing at {destination}; "
                    f"continuing with the same browser session.")
        return True

    def _wait_for_manual_verification(self) -> None:
        s = self.settings
        if s.headless:
            raise CaptchaRequiredError("CAPTCHA shown in headless mode; manual verification is impossible")
        if self.manual_verifications >= s.max_manual_verifications:
            raise CaptchaRequiredError(f"a new CAPTCHA appeared and the limit of {s.max_manual_verifications} "
                                       f"manual verification(s) for this browser session is reached")
        self.manual_verifications += 1
        self.notify(f"CAPTCHA shown. Please complete it manually in the browser window "
                    f"(waiting up to {s.captcha_wait_s:.0f}s; manual verification {self.manual_verifications} "
                    f"of {s.max_manual_verifications} for this browser session).")
        deadline = self._clock() + s.captcha_wait_s
        last_seen = self._snapshot()
        while self._clock() < deadline:
            self._sleep(s.captcha_poll_s)
            current = self._snapshot()
            if current is None or current == last_seen:
                continue  # navigating after a submission, or nothing changed since the last check
            last_seen = current
            url, html = current
            problem = listing_problem(url, html, self.base_url)
            if problem is None:
                destination = safe_url(url)
                logger.info("manual verification confirmed at %s", destination)
                self.notify(f"Verification detected: the browser reached the listing at {destination}; "
                            f"continuing with the same browser session.")
                return
            remaining = max(0.0, deadline - self._clock())
            if problem == CAPTCHA_SHOWN:
                self.notify(f"CAPTCHA shown again (the answer may have been rejected); still waiting "
                            f"for manual verification ({remaining:.0f}s left).")
            else:
                logger.info("not verified yet: %s at %s (%.0fs left)", problem, safe_url(url), remaining)
        raise CaptchaRequiredError(f"CAPTCHA was not completed within {s.captcha_wait_s:.0f}s")

    def _snapshot(self) -> tuple[str, str] | None:
        try:
            return self.page.url, self.page.content()
        except PlaywrightError:
            return None

    def _backoff(self, retry: int) -> float:
        s = self.settings
        return min(s.backoff_base_s * 2 ** (retry - 1), s.backoff_max_s) + self._rng.uniform(0, s.backoff_base_s)


class _CaptchaShown(Exception):
    pass


class _Retryable(Exception):
    def __init__(self, message: str, retry_after: float | None = None, rate_limited: bool = False) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.rate_limited = rate_limited


def _header(response: Any, name: str) -> str | None:
    headers = response.headers or {}
    return next((v for k, v in headers.items() if k.lower() == name), None)


def _first_line(exc: BaseException) -> str:
    return str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__


def _image_hash(image: bytes | None) -> str:
    """Short SHA-256 of a CAPTCHA image, to tell in the log whether two attempts saw the same image."""
    return hashlib.sha256(image or b"").hexdigest()[:12]
