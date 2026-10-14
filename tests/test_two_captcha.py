"""2Captcha client against a mocked API: request format, polling, errors and key secrecy (offline)."""

from __future__ import annotations

import base64
import io
import logging
import socket
import urllib.error
import urllib.request

import pytest

from scraper import two_captcha
from scraper.captcha_provider import create_solver, load_captcha_config
from scraper.two_captcha import (
    CaptchaSolution,
    SolverAccountError,
    SolverError,
    SolverTimeoutError,
    TwoCaptchaClient,
    urllib_transport,
)
from tests.fake_playwright import CAPTCHA_PNG
from tests.fake_two_captcha import FAKE_KEY, FakeTwoCaptchaAPI, error, processing, ready


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def client(api: FakeTwoCaptchaAPI, clock: Clock | None = None, timeout_s: float = 120) -> TwoCaptchaClient:
    clock = clock or Clock()
    return TwoCaptchaClient(FAKE_KEY, timeout_s=timeout_s, transport=api, sleep=clock.sleep, clock=clock)


# --- the documented Normal captcha flow -------------------------------------------------------

def test_solves_an_image_with_create_task_and_polling() -> None:
    api = FakeTwoCaptchaAPI(["x7k2p"], results=[processing(), processing()])
    clock = Clock()
    solution = client(api, clock).solve_image(CAPTCHA_PNG)
    assert solution == CaptchaSolution(7001, "x7k2p")
    assert api.methods == ["createTask", "getTaskResult", "getTaskResult", "getTaskResult"]
    assert clock.sleeps == [5.0, 5.0, 5.0]  # documented: first check after 5 s, then every 5 s


def test_create_task_sends_only_the_key_and_the_image() -> None:
    api = FakeTwoCaptchaAPI()
    client(api).solve_image(CAPTCHA_PNG)
    create = api.requests[0][1]
    assert set(create) == {"clientKey", "task"} and create["clientKey"] == FAKE_KEY
    assert create["task"] == {"type": "ImageToTextTask", "body": base64.b64encode(CAPTCHA_PNG).decode(),
                              "phrase": False, "case": False, "numeric": 0, "math": False,
                              "minLength": 0, "maxLength": 0}
    assert api.requests[1][1] == {"clientKey": FAKE_KEY, "taskId": 7001}


def test_answer_whitespace_is_stripped() -> None:
    assert client(FakeTwoCaptchaAPI(["  ab12 \n"])).solve_image(CAPTCHA_PNG).answer == "ab12"


def test_reports_whether_the_site_accepted_the_answer() -> None:
    api = FakeTwoCaptchaAPI()
    solver = client(api)
    solver.report(CaptchaSolution(7001, "x"), correct=True)
    solver.report(CaptchaSolution(7002, "y"), correct=False)
    assert api.requests == [("reportCorrect", {"clientKey": FAKE_KEY, "taskId": 7001}),
                            ("reportIncorrect", {"clientKey": FAKE_KEY, "taskId": 7002})]


def test_report_failures_are_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    api = FakeTwoCaptchaAPI(reports=[(500, b"")])
    client(api).report(CaptchaSolution(7001, "x"), correct=False)
    assert "reportIncorrect for task 7001 failed: 2Captcha API returned HTTP 500" in caplog.text


# --- timeouts -----------------------------------------------------------------------------------

def test_polling_stops_at_the_configured_timeout() -> None:
    api = FakeTwoCaptchaAPI(results=[processing()] * 100)
    clock = Clock()
    with pytest.raises(SolverTimeoutError, match=r"no answer from 2Captcha within 20s \(task 7001\)"):
        client(api, clock, timeout_s=20).solve_image(CAPTCHA_PNG)
    assert clock.now == 20.0 and sum(clock.sleeps) == 20.0
    assert api.methods.count("getTaskResult") == 3  # at 5, 10 and 15 s
    assert all(timeout <= 30 for timeout in api.timeouts)


def test_http_timeout_never_exceeds_the_remaining_time() -> None:
    api = FakeTwoCaptchaAPI(results=[processing()] * 10)
    with pytest.raises(SolverTimeoutError):
        client(api, timeout_s=12).solve_image(CAPTCHA_PNG)
    assert api.timeouts[0] == 12 and max(api.timeouts[1:]) <= 7


# --- API errors -------------------------------------------------------------------------------

@pytest.mark.parametrize(("code", "message"), [
    ("ERROR_ZERO_BALANCE", "the account balance is insufficient"),
    ("ERROR_KEY_DOES_NOT_EXIST", "the API key was rejected"),
    ("ERROR_WRONG_USER_KEY", "the API key was rejected"),
    ("ERROR_IP_NOT_ALLOWED", "trusted IP list"),
    ("ERROR_IP_BLOCKED", "blocked by 2Captcha"),
    ("ERROR_ACCOUNT_SUSPENDED", "the account is suspended"),
])
def test_account_errors_are_reported_as_account_problems(code: str, message: str) -> None:
    api = FakeTwoCaptchaAPI(create=[error(code)])
    with pytest.raises(SolverAccountError, match=message) as exc:
        client(api).solve_image(CAPTCHA_PNG)
    assert code in str(exc.value) and f"description of {code}" not in str(exc.value)
    assert api.methods == ["createTask"]


def test_insufficient_balance_while_polling_is_an_account_problem() -> None:
    api = FakeTwoCaptchaAPI(results=[processing(), error("ERROR_ZERO_BALANCE", 10)])
    with pytest.raises(SolverAccountError, match="balance is insufficient"):
        client(api).solve_image(CAPTCHA_PNG)


@pytest.mark.parametrize("code", ["ERROR_CAPTCHA_UNSOLVABLE", "ERROR_BAD_DUPLICATES", "ERROR_IMAGE_TYPE_NOT_SUPPORTED"])
def test_unsolvable_captcha_is_a_retryable_failure(code: str) -> None:
    api = FakeTwoCaptchaAPI(results=[error(code, 12)])
    with pytest.raises(SolverError, match=code) as exc:
        client(api).solve_image(CAPTCHA_PNG)
    assert not isinstance(exc.value, SolverAccountError)


@pytest.mark.parametrize("response", [(500, b""), (200, b"<html>maintenance</html>"), (200, b"[1, 2]"),
                                      error("ERROR_NO_SLOT_AVAILABLE", 2)])
def test_create_task_failures_are_retryable(response: tuple[int, bytes]) -> None:
    with pytest.raises(SolverError) as exc:
        client(FakeTwoCaptchaAPI(create=[response])).solve_image(CAPTCHA_PNG)
    assert not isinstance(exc.value, SolverAccountError)


def test_transient_poll_errors_are_tolerated() -> None:
    api = FakeTwoCaptchaAPI(["ok42"], results=[(502, b""), (200, b"not json"), processing()])
    assert client(api).solve_image(CAPTCHA_PNG).answer == "ok42"


def test_repeated_poll_errors_give_up() -> None:
    api = FakeTwoCaptchaAPI(results=[(503, b"")] * 5)
    with pytest.raises(SolverError, match=r"HTTP 503 for getTaskResult \(3 times while waiting for task 7001\)"):
        client(api).solve_image(CAPTCHA_PNG)
    assert api.methods.count("getTaskResult") == 3


@pytest.mark.parametrize("payload", [{"errorId": 0}, {"errorId": 0, "taskId": None},
                                     {"errorId": 0, "taskId": True}, {"errorId": 0, "taskId": "abc"}])
def test_missing_task_id_is_an_error(payload: dict) -> None:
    with pytest.raises(SolverError, match="did not return a task id"):
        client(FakeTwoCaptchaAPI(create=[(200, payload)])).solve_image(CAPTCHA_PNG)


@pytest.mark.parametrize("result", [ready(""), ready("   "), ready("a" * 33), ready("ab\ncd"),
                                    (200, {"errorId": 0, "status": "ready", "solution": {}}),
                                    (200, {"errorId": 0, "status": "ready"})])
def test_unusable_answers_are_rejected(result: tuple) -> None:
    with pytest.raises(SolverError, match="empty or unusable"):
        client(FakeTwoCaptchaAPI(results=[result])).solve_image(CAPTCHA_PNG)


def test_unexpected_status_is_an_error() -> None:
    with pytest.raises(SolverError, match="unexpected status"):
        client(FakeTwoCaptchaAPI(results=[(200, {"errorId": 0, "status": "queued"})])).solve_image(CAPTCHA_PNG)


@pytest.mark.parametrize("code", ["<script>alert(1)</script>", "x" * 80, None, 42])
def test_unexpected_error_codes_are_not_echoed(code: object) -> None:
    api = FakeTwoCaptchaAPI(create=[(200, {"errorId": 99, "errorCode": code})])
    with pytest.raises(SolverError, match=r"\(unknown error\)"):
        client(api).solve_image(CAPTCHA_PNG)


# --- image checks before anything is sent -----------------------------------------------------

@pytest.mark.parametrize(("image", "message"), [
    (b"\x89PNG\r\n\x1a\n", "outside 2Captcha's limits"),
    (b"\x89PNG\r\n\x1a\n" + bytes(100 * 1024), "outside 2Captcha's limits"),
    (b"<html>" + bytes(200), "not a PNG, JPEG or GIF"),
])
def test_invalid_images_are_never_sent(image: bytes, message: str) -> None:
    api = FakeTwoCaptchaAPI()
    with pytest.raises(SolverError, match=message):
        client(api).solve_image(image)
    assert api.requests == []


@pytest.mark.parametrize("signature", [b"\xff\xd8\xff\xe0", b"GIF89a", b"GIF87a"])
def test_jpeg_and_gif_images_are_accepted(signature: bytes) -> None:
    assert client(FakeTwoCaptchaAPI()).solve_image(signature + bytes(200)).answer == "x7k2p"


# --- key secrecy ----------------------------------------------------------------------------

def test_key_and_answer_never_appear_in_logs_errors_or_repr(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    api = FakeTwoCaptchaAPI(["secret-answer"], results=[processing(), (500, b"")])
    solver = client(api)
    solution = solver.solve_image(CAPTCHA_PNG)
    texts = [repr(solver), str(solver), repr(solution), caplog.text]
    for response in ([error("ERROR_ZERO_BALANCE")], [error("ERROR_CAPTCHA_UNSOLVABLE")], [(500, b"")]):
        with pytest.raises(SolverError) as exc:
            client(FakeTwoCaptchaAPI(create=response)).solve_image(CAPTCHA_PNG)
        texts.append(str(exc.value))
    texts.append(caplog.text)
    assert all(FAKE_KEY not in text and "secret-answer" not in text for text in texts)


def test_client_requires_a_key() -> None:
    with pytest.raises(ValueError, match="API key is required"):
        TwoCaptchaClient("", timeout_s=120)


# --- HTTP transport ---------------------------------------------------------------------------

class FakeHTTPResponse(io.BytesIO):
    status = 200

    def __enter__(self) -> FakeHTTPResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def test_transport_posts_json_over_https_with_the_key_only_in_the_body(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[urllib.request.Request, float]] = []

    def urlopen(request: urllib.request.Request, timeout: float) -> FakeHTTPResponse:
        seen.append((request, timeout))
        return FakeHTTPResponse(b'{"errorId": 0, "taskId": 1}')

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    body = b'{"clientKey": "%s"}' % FAKE_KEY.encode()
    assert urllib_transport("https://api.2captcha.com/createTask", body, 30) == (200, b'{"errorId": 0, "taskId": 1}')
    request, timeout = seen[0]
    assert (request.full_url, request.get_method(), request.data, timeout) == (
        "https://api.2captcha.com/createTask", "POST", body, 30)
    assert request.get_header("Content-type") == "application/json"
    assert FAKE_KEY not in request.full_url + repr(request.header_items())


def test_transport_reports_network_errors_without_details(monkeypatch: pytest.MonkeyPatch) -> None:
    def urlopen(request: urllib.request.Request, timeout: float) -> None:
        raise urllib.error.URLError(f"failure with {FAKE_KEY}")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(SolverError, match=r"^2Captcha API unreachable \(URLError\)$") as exc:
        urllib_transport("https://api.2captcha.com/createTask", b"{}", 30)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


def test_transport_returns_http_error_statuses(monkeypatch: pytest.MonkeyPatch) -> None:
    def urlopen(request: urllib.request.Request, timeout: float) -> None:
        raise urllib.error.HTTPError(request.full_url, 503, "Service Unavailable", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert urllib_transport("https://api.2captcha.com/getTaskResult", b"{}", 30) == (503, b"")


def test_client_uses_the_official_api_endpoint() -> None:
    assert two_captcha.API_BASE_URL == "https://api.2captcha.com"
    api = FakeTwoCaptchaAPI()
    client(api).solve_image(CAPTCHA_PNG)
    assert api.methods == ["createTask", "getTaskResult"]


# --- configuration -> solver -------------------------------------------------------------------

def test_create_solver_follows_the_configuration() -> None:
    assert create_solver(load_captcha_config({})) is None
    assert create_solver(load_captcha_config({"CAPTCHA_PROVIDER": "2captcha"})) is None
    assert create_solver(load_captcha_config({"CAPTCHA_API_KEY": FAKE_KEY})) is None
    solver = create_solver(load_captcha_config({"CAPTCHA_PROVIDER": "2captcha", "CAPTCHA_API_KEY": FAKE_KEY,
                                                "CAPTCHA_TIMEOUT": "45"}))
    assert isinstance(solver, TwoCaptchaClient) and solver.timeout_s == 45.0
    assert repr(solver) == "TwoCaptchaClient(timeout_s=45)"
