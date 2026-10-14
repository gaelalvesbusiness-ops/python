"""Offline stand-in for the 2Captcha API v2: scripted JSON responses, no network."""

from __future__ import annotations

import json
from typing import Any

FAKE_KEY = "f00dfeed" * 4  # synthetic, 32 hex characters like a 2Captcha key
API = "https://api.2captcha.com/"

Response = tuple[int, Any]


def processing() -> Response:
    return 200, {"errorId": 0, "status": "processing"}


def ready(text: str) -> Response:
    return 200, {"errorId": 0, "status": "ready", "solution": {"text": text}, "cost": "0.00025",
                 "ip": "1.2.3.4", "createTime": 1692808229, "endTime": 1692808326, "solveCount": 1}


def error(code: str, error_id: int = 1) -> Response:
    return 200, {"errorId": error_id, "errorCode": code, "errorDescription": f"description of {code}"}


class FakeTwoCaptchaAPI:
    """Transport for TwoCaptchaClient; records every request.

    ``create`` and ``results`` script the createTask and getTaskResult responses in order; once
    they run out, tasks are created normally and each poll returns the next of ``answers``.
    """

    def __init__(self, answers: list[str] | None = None, *, create: list[Response] | None = None,
                 results: list[Response] | None = None, reports: list[Response] | None = None) -> None:
        self.answers = list(answers or ["x7k2p"])
        self.create, self.results, self.reports = list(create or []), list(results or []), list(reports or [])
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.timeouts: list[float] = []
        self._task = 7000

    @property
    def methods(self) -> list[str]:
        return [method for method, _ in self.requests]

    def __call__(self, url: str, body: bytes, timeout: float) -> tuple[int, bytes]:
        assert url.startswith(API), url
        method = url[len(API):]
        self.requests.append((method, json.loads(body)))
        self.timeouts.append(timeout)
        if method == "createTask":
            if self.create:
                return self._encode(self.create.pop(0))
            self._task += 1
            return self._encode((200, {"errorId": 0, "taskId": self._task}))
        if method == "getTaskResult":
            if self.results:
                return self._encode(self.results.pop(0))
            return self._encode(ready(self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]))
        if method in ("reportCorrect", "reportIncorrect"):
            if self.reports:
                return self._encode(self.reports.pop(0))
            return self._encode((200, {"errorId": 0, "status": "success"}))
        return self._encode(error("ERROR_NO_SUCH_METHOD", 14))

    @staticmethod
    def _encode(response: Response) -> tuple[int, bytes]:
        status, payload = response
        return status, payload if isinstance(payload, bytes) else json.dumps(payload).encode()
