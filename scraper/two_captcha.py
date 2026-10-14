"""2Captcha client for image CAPTCHAs (API v2 "Normal captcha", task type ImageToTextTask).

Only the CAPTCHA image is sent, through ``createTask``; the answer is polled with
``getTaskResult`` until the configured timeout, and ``reportCorrect``/``reportIncorrect``
tell 2Captcha whether the site accepted it. No page URL, cookie or other session data is
sent. The API key travels only in the JSON body of HTTPS requests to api.2captcha.com and
never appears in logs, error messages or ``repr``; answers are never logged either.

Errors are split into account problems (rejected key, insufficient balance, IP not allowed or
blocked, suspended account), which disable automatic solving for the rest of the run, and
everything else (unsolvable image, timeout, API or network errors), after which another
attempt or manual verification may follow.
"""

from __future__ import annotations

import base64
import http.client
import json
import logging
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

API_BASE_URL = "https://api.2captcha.com"
TASK_TYPE = "ImageToTextTask"
MIN_IMAGE_BYTES, MAX_IMAGE_BYTES = 100, 100 * 1024
INITIAL_WAIT_S = 5.0
POLL_INTERVAL_S = 5.0
REQUEST_TIMEOUT_S = 30.0
MAX_POLL_FAILURES = 3
MAX_ANSWER_LENGTH = 32
MAX_RESPONSE_BYTES = 64 * 1024

ACCOUNT_ERRORS = {
    "ERROR_KEY_DOES_NOT_EXIST": "the API key was rejected",
    "ERROR_WRONG_USER_KEY": "the API key was rejected",
    "ERROR_ZERO_BALANCE": "the account balance is insufficient",
    "ERROR_IP_NOT_ALLOWED": "this computer's IP address is not on the account's trusted IP list",
    "ERROR_IP_BLOCKED": "this IP address is blocked by 2Captcha",
    "IP_BANNED": "this IP address is blocked by 2Captcha",
    "ERROR_ACCOUNT_SUSPENDED": "the account is suspended",
}
TRANSIENT_ERRORS = {"ERROR_NO_SLOT_AVAILABLE"}
_ERROR_CODE = re.compile(r"[A-Z0-9_]{1,40}")
_IMAGE_SIGNATURES = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")

# url, JSON body, timeout in seconds -> (HTTP status, response body)
Transport = Callable[[str, bytes, float], tuple[int, bytes]]


class SolverError(RuntimeError):
    """The CAPTCHA was not solved this time; another attempt or manual verification may follow."""


class SolverAccountError(SolverError):
    """The account cannot be used; automatic solving stays off for the rest of the run."""


class SolverTimeoutError(SolverError):
    """No answer arrived within the configured timeout."""


class InvalidImageError(SolverError):
    """The image failed the local checks and was not sent to 2Captcha."""


class _TransientError(SolverError):
    pass


@dataclass(frozen=True)
class CaptchaSolution:
    task_id: int | str
    answer: str

    def __repr__(self) -> str:
        return f"CaptchaSolution(task_id={self.task_id!r}, answer=<hidden>)"


def urllib_transport(url: str, body: bytes, timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
        raise _TransientError(f"2Captcha API unreachable ({type(exc).__name__})") from None


class TwoCaptchaClient:
    """Solves one image CAPTCHA per ``solve_image`` call within ``timeout_s`` seconds."""

    def __init__(self, api_key: str, *, timeout_s: float, transport: Transport | None = None,
                 sleep: Callable[[float], None] | None = None, clock: Callable[[], float] | None = None,
                 initial_wait_s: float = INITIAL_WAIT_S, poll_interval_s: float = POLL_INTERVAL_S) -> None:
        if not api_key:
            raise ValueError("a 2Captcha API key is required")
        self._api_key = api_key
        self.timeout_s = timeout_s
        self._transport = transport or urllib_transport
        self._sleep, self._clock = sleep or time.sleep, clock or time.monotonic
        self.initial_wait_s, self.poll_interval_s = initial_wait_s, poll_interval_s

    def __repr__(self) -> str:
        return f"TwoCaptchaClient(timeout_s={self.timeout_s:g})"

    def solve_image(self, image: bytes) -> CaptchaSolution:
        _check_image(image)
        deadline = self._clock() + self.timeout_s
        task = {"type": TASK_TYPE, "body": base64.b64encode(image).decode("ascii"), "phrase": False,
                "case": False, "numeric": 0, "math": False, "minLength": 0, "maxLength": 0}
        created = self._call("createTask", {"task": task}, deadline)
        task_id = created.get("taskId")
        if isinstance(task_id, bool) or not isinstance(task_id, (int, str)) or not str(task_id).isdigit():
            raise SolverError("2Captcha did not return a task id")
        logger.info("2Captcha task %s submitted; waiting up to %.0fs for the answer", task_id, self.timeout_s)
        self._wait(self.initial_wait_s, deadline)
        failures = 0
        while self._clock() < deadline:
            try:
                result = self._call("getTaskResult", {"taskId": task_id}, deadline)
            except _TransientError as exc:
                failures += 1
                if failures >= MAX_POLL_FAILURES:
                    raise SolverError(f"{exc} ({failures} times while waiting for task {task_id})") from None
                logger.warning("2Captcha task %s: %s; polling again", task_id, exc)
                self._wait(self.poll_interval_s, deadline)
                continue
            failures = 0
            status = result.get("status")
            if status == "ready":
                logger.info("2Captcha task %s: answer received", task_id)
                return CaptchaSolution(task_id, _answer(result, task_id))
            if status != "processing":
                raise SolverError(f"2Captcha task {task_id}: unexpected status")
            self._wait(self.poll_interval_s, deadline)
        raise SolverTimeoutError(f"no answer from 2Captcha within {self.timeout_s:g}s (task {task_id})")

    def report(self, solution: CaptchaSolution, *, correct: bool) -> None:
        """Tell 2Captcha whether the site accepted the answer; failures are only logged."""
        method = "reportCorrect" if correct else "reportIncorrect"
        try:
            self._call(method, {"taskId": solution.task_id}, self._clock() + REQUEST_TIMEOUT_S)
        except SolverError as exc:
            logger.warning("2Captcha %s for task %s failed: %s", method, solution.task_id, exc)

    def _wait(self, seconds: float, deadline: float) -> None:
        remaining = deadline - self._clock()
        if remaining > 0:
            self._sleep(min(seconds, remaining))

    def _call(self, method: str, payload: dict[str, Any], deadline: float) -> dict[str, Any]:
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise SolverTimeoutError(f"no answer from 2Captcha within {self.timeout_s:g}s")
        body = json.dumps({"clientKey": self._api_key, **payload}).encode("utf-8")
        status, raw = self._transport(f"{API_BASE_URL}/{method}", body, min(REQUEST_TIMEOUT_S, remaining))
        if status != 200:
            raise _TransientError(f"2Captcha API returned HTTP {status} for {method}")
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise _TransientError(f"2Captcha API returned an unreadable response for {method}")
        if data.get("errorId", 0) in (0, None):
            return data
        code = data.get("errorCode")
        code = code if isinstance(code, str) and _ERROR_CODE.fullmatch(code) else "unknown error"
        if code in ACCOUNT_ERRORS:
            raise SolverAccountError(f"2Captcha refused the request: {ACCOUNT_ERRORS[code]} ({code})")
        if code in TRANSIENT_ERRORS:
            raise _TransientError(f"2Captcha is temporarily not accepting tasks ({code})")
        raise SolverError(f"2Captcha could not process the CAPTCHA ({code})")


def _check_image(image: bytes) -> None:
    if not MIN_IMAGE_BYTES <= len(image) <= MAX_IMAGE_BYTES:
        raise InvalidImageError(f"CAPTCHA image of {len(image)} bytes is outside 2Captcha's limits "
                                f"({MIN_IMAGE_BYTES} bytes to {MAX_IMAGE_BYTES // 1024} kB)")
    if not image.startswith(_IMAGE_SIGNATURES):
        raise InvalidImageError("CAPTCHA image is not a PNG, JPEG or GIF")


def _answer(result: dict[str, Any], task_id: int | str) -> str:
    solution = result.get("solution")
    text = solution.get("text") if isinstance(solution, dict) else None
    text = text.strip() if isinstance(text, str) else ""
    if not text or len(text) > MAX_ANSWER_LENGTH or not text.isprintable():
        raise SolverError(f"2Captcha task {task_id}: the answer is empty or unusable")
    return text
