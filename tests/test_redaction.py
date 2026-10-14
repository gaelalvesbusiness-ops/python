"""Regression tests for credential redaction in inspect_site outputs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from inspect_site import REDACTED, audit_outputs, fmt, redact, write_redacted

CSRF = "deadbeef" * 8
ANSWER = "Xy7Qz"
CAPTCHA_POST = f"csrf={CSRF}&captcha={ANSWER}&btn_check_captcha=%D0%9E%D1%82%D0%BF"


@pytest.mark.parametrize(
    "text",
    [
        CAPTCHA_POST,
        f"post='{CAPTCHA_POST}'",
        f"[original:load] POST 302 document https://honestpool.ru/captcha  post='{CAPTCHA_POST}'",
        f"captcha={ANSWER}&csrf={CSRF}",
        f"a=1&csrf_token={CSRF}",
        f"https://example.test/form?session_id={CSRF}&x=1",
    ],
)
def test_post_body_secrets_are_redacted(text: str) -> None:
    out = redact(text)
    assert CSRF not in out
    assert ANSWER not in out
    assert REDACTED in out


def test_post_body_redacted_in_json_log_stays_valid_json() -> None:
    details = {"network": [{"method": "POST", "postData": CAPTCHA_POST, "status": 302}]}
    out = redact(fmt(details))
    assert CSRF not in out and ANSWER not in out
    entry = json.loads(out)["network"][0]
    assert entry["postData"].startswith(f"csrf={REDACTED}&captcha={REDACTED}")
    assert entry["status"] == 302


def test_hidden_inputs_redacted_in_html_and_json_embedded_html() -> None:
    html = (f'<form><input type="hidden" name="csrf" value="{CSRF}">'
            f'<input name="captcha" value="{ANSWER}"></form>')
    assert CSRF not in redact(html) and ANSWER not in redact(html)
    out = redact(fmt({"bodyHead": html}))
    assert CSRF not in out and ANSWER not in out
    assert json.loads(out)["bodyHead"].count(REDACTED) == 2


@pytest.mark.parametrize(
    "text",
    [
        f"Cookie: PHPSESSID={CSRF}",
        f"set-cookie: PHPSESSID={CSRF}; path=/",
        f"Authorization: Bearer {CSRF}",
        json.dumps({"cookie": f"PHPSESSID={CSRF}"}),
        json.dumps({"authorization": f"Bearer {CSRF}"}),
        json.dumps({"x-csrf-token": CSRF}),
    ],
)
def test_headers_and_session_values_are_redacted(text: str) -> None:
    assert CSRF not in redact(text)


@pytest.mark.parametrize(
    "text",
    [
        "https://honestpool.ru/captcha?url=puzzle?id=71",
        "hex_or_number=400000000000000000&search=1",
        '"captchaScreenshot": "logs/captcha_original.png"',
        "keyword hits: ['captcha', 'капч']",
        '<meta name="author" content="https://t.me/homlol_official" />',
        "<td>400000000000000000</td><td>400000100000000000</td>",
        "https://honestpool.ru/puzzle?id=71&page=2",
    ],
)
def test_non_sensitive_content_is_preserved(text: str) -> None:
    assert redact(text) == text


def test_audit_reports_location_without_secret(tmp_path: Path) -> None:
    leaked = tmp_path / "leaked.txt"
    leaked.write_text(f"ok line\npost='{CAPTCHA_POST}'\n", encoding="utf-8")
    findings = audit_outputs([leaked])
    assert findings and all(f.startswith("leaked.txt:2:") for f in findings)
    assert not any(CSRF in f or ANSWER in f for f in findings)


def test_written_outputs_pass_audit(tmp_path: Path) -> None:
    txt, js = tmp_path / "report.txt", tmp_path / "details.json"
    write_redacted(txt, f"[original:load] POST 302 document  post='{CAPTCHA_POST}'\n")
    write_redacted(js, fmt({"network": [{"postData": CAPTCHA_POST}],
                            "bodyHead": f'<input name="csrf" value="{CSRF}">'}))
    assert audit_outputs([txt, js]) == []
    for path in (txt, js):
        content = path.read_text(encoding="utf-8")
        assert CSRF not in content and ANSWER not in content
