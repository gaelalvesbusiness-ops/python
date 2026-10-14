"""CAPTCHA provider configuration: selection, validation, key secrecy and manual fallback (offline)."""

from __future__ import annotations

import logging
import random
import socket
from pathlib import Path

import pytest

import main as cli
from scraper import browser
from scraper.browser import BrowserSession
from scraper.captcha_provider import CaptchaConfigError, load_captcha_config
from scraper.database import PENDING, Database
from tests.fake_playwright import FakePage, FakePlaywright, captcha, factory

FAKE_KEY = "f00dfeed" * 4  # synthetic, 32 hex characters like a provider key


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def env(**values: str) -> dict[str, str]:
    return {f"CAPTCHA_{name.upper()}": value for name, value in values.items()}


def set_env(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for name, value in env(**values).items():
        monkeypatch.setenv(name, value)


# --- provider selection ------------------------------------------------------------

@pytest.mark.parametrize("value", [None, "", "  ", "manual", "MANUAL"])
def test_manual_is_the_default_provider(value: str | None) -> None:
    config = load_captcha_config({} if value is None else env(provider=value))
    assert (config.provider, config.is_external, config.automated_submission) == ("manual", False, False)
    assert config.timeout_s == 120.0 and config.warnings == ()
    assert config.describe().startswith("CAPTCHA provider: manual (default)")


@pytest.mark.parametrize("value", ["2captcha", "2Captcha", " 2CAPTCHA "])
def test_2captcha_is_recognized(value: str) -> None:
    config = load_captcha_config(env(provider=value, api_key=FAKE_KEY, timeout="120"))
    assert (config.provider, config.is_external, config.has_api_key) == ("2captcha", True, True)
    assert config.automated_submission is True and config.timeout_s == 120.0 and config.warnings == ()
    assert config.describe() == ("CAPTCHA provider: 2captcha | API key: set (hidden) | timeout: 120 s | automatic "
                                 "solving: enabled (only the CAPTCHA image is sent to the provider; manual "
                                 "verification in the browser window remains the fallback)")


def test_timeout_accepts_decimal_seconds() -> None:
    assert load_captcha_config(env(provider="2captcha", timeout="30.5")).timeout_s == 30.5


# --- missing credentials -------------------------------------------------------------

@pytest.mark.parametrize("key", [None, "", "   "])
def test_missing_api_key_falls_back_to_manual(key: str | None) -> None:
    values = env(provider="2captcha") | ({} if key is None else env(api_key=key))
    config = load_captcha_config(values)
    assert config.provider == "2captcha" and not config.has_api_key and config.api_key is None
    assert config.warnings == ("CAPTCHA_API_KEY is not set; manual CAPTCHA verification is used",)
    assert config.automated_submission is False
    assert config.describe() == ("CAPTCHA provider: 2captcha | API key: missing (CAPTCHA_API_KEY is not set) | "
                                 "timeout: 120 s | automatic solving: disabled; CAPTCHAs are completed manually "
                                 "in the browser window")


def test_api_key_without_provider_is_ignored() -> None:
    config = load_captcha_config(env(api_key=FAKE_KEY))
    assert config.provider == "manual" and config.api_key is None
    assert "ignored because no external provider is selected" in config.warnings[0]


# --- invalid configuration -------------------------------------------------------------

@pytest.mark.parametrize(("values", "message"), [
    (env(provider="anticaptcha"), "CAPTCHA_PROVIDER 'anticaptcha' is not supported; use one of manual, 2captcha"),
    (env(provider="2capcha"), "'2capcha' is not supported"),
    (env(provider="2captcha", timeout="abc"), "CAPTCHA_TIMEOUT must be a number of seconds"),
    (env(provider="2captcha", timeout="0"), "between 10 and 600 seconds"),
    (env(provider="2captcha", timeout="5"), "between 10 and 600 seconds"),
    (env(provider="2captcha", timeout="601"), "between 10 and 600 seconds"),
    (env(provider="2captcha", timeout="-120"), "between 10 and 600 seconds"),
    (env(provider="2captcha", timeout="nan"), "between 10 and 600 seconds"),
    (env(provider="2captcha", timeout="inf"), "between 10 and 600 seconds"),
])
def test_invalid_configuration_is_rejected(values: dict[str, str], message: str) -> None:
    with pytest.raises(CaptchaConfigError, match=message.replace("(", r"\(").replace(")", r"\)")):
        load_captcha_config(values)


@pytest.mark.parametrize("key", [f"{FAKE_KEY[:16]} {FAKE_KEY[16:]}", f"{FAKE_KEY}\x07", f"{FAKE_KEY}é"])
def test_malformed_api_key_is_rejected_without_showing_it(key: str) -> None:
    with pytest.raises(CaptchaConfigError) as exc:
        load_captcha_config(env(provider="2captcha", api_key=key))
    assert "malformed" in str(exc.value) and FAKE_KEY[:8] not in str(exc.value)


def test_key_pasted_into_the_provider_variable_is_not_echoed() -> None:
    with pytest.raises(CaptchaConfigError) as exc:
        load_captcha_config(env(provider=FAKE_KEY))
    assert "(value hidden)" in str(exc.value) and FAKE_KEY not in str(exc.value)


# --- credential secrecy ------------------------------------------------------------------

def test_api_key_never_appears_in_text_forms(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    config = load_captcha_config(env(provider="2captcha", api_key=FAKE_KEY))
    for text in (repr(config), str(config), config.describe(), " ".join(config.warnings), caplog.text):
        assert FAKE_KEY not in text
    assert config.api_key == FAKE_KEY  # held in memory only, for the solver
    assert config == load_captcha_config(env(provider="2captcha", api_key="other-key"))


def test_cli_captcha_status_hides_the_key(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
                                          db_path: Path) -> None:
    set_env(monkeypatch, provider="2captcha", api_key=FAKE_KEY, timeout="120")
    assert cli.main(["--db", str(db_path), "--captcha-status"]) == 0
    captured = capsys.readouterr()
    assert "CAPTCHA provider: 2captcha | API key: set (hidden) | timeout: 120 s" in captured.out
    assert FAKE_KEY not in captured.out + captured.err and not db_path.exists()


def test_cli_captcha_status_warns_about_missing_key(monkeypatch: pytest.MonkeyPatch,
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    set_env(monkeypatch, provider="2captcha")
    assert cli.main(["--captcha-status"]) == 0
    assert "warning: CAPTCHA_API_KEY is not set; manual CAPTCHA verification is used" in capsys.readouterr().out


def test_cli_rejects_invalid_configuration_before_opening_anything(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], db_path: Path) -> None:
    set_env(monkeypatch, provider="2captcha", api_key=FAKE_KEY, timeout="9999")
    monkeypatch.setattr(cli, "run_live", lambda *a, **k: pytest.fail("run_live must not be called"))
    with pytest.raises(SystemExit) as exc:
        cli.main(["--db", str(db_path), "--start-page", "1", "--end-page", "2", "--live"])
    err = capsys.readouterr().err
    assert exc.value.code == 2 and "invalid CAPTCHA configuration: CAPTCHA_TIMEOUT" in err
    assert FAKE_KEY not in err and not db_path.exists()


def test_offline_commands_ignore_captcha_settings(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> None:
    set_env(monkeypatch, provider="not-a-provider")
    assert cli.main(["--db", str(db_path), "--start-page", "1", "--end-page", "2"]) == 0


# --- safe fallback: the manual workflow is unchanged ------------------------------------------

def test_live_run_with_provider_but_no_key_uses_manual_handling(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture,
        db_path: Path) -> None:
    set_env(monkeypatch, provider="2captcha")
    fake = FakePlaywright()
    fake.browser.context.page.script = [captcha()]
    monkeypatch.setattr(browser, "BrowserSession", lambda settings: BrowserSession(settings, factory(fake)))
    caplog.set_level(logging.DEBUG)
    code = cli.main(["--db", str(db_path), "--start-page", "1", "--end-page", "2", "--live", "--headless"])
    captured = capsys.readouterr()
    assert code == 3
    assert "automatic solving: disabled" in captured.out
    assert "Run stopped: page 1: CAPTCHA shown in headless mode; manual verification is impossible" in captured.out
    assert fake.browser.context.page.calls == ["https://honestpool.ru/puzzle?id=71"]
    with Database(db_path) as db:
        assert (db.page(1)["status"], db.page(1)["attempts"]) == (PENDING, 0)


def test_fetcher_without_solver_asks_a_person(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, provider="2captcha", api_key=FAKE_KEY)
    clock = [0.0]

    def sleep(seconds: float) -> None:
        clock[0] += seconds

    notes: list[str] = []
    page = FakePage([captcha()])
    settings = browser.BrowserSettings(page_delay_s=0, page_delay_jitter_s=0, captcha_wait_s=20, captcha_poll_s=5)
    fetch = browser.ListingFetcher(page, settings, notify=notes.append, sleep=sleep, clock=lambda: clock[0],
                                   rng=random.Random(0))
    with pytest.raises(browser.CaptchaRequiredError, match="not completed within 20s"):
        fetch(1)
    assert any("Please complete it manually in the browser window" in n for n in notes)
    assert fetch.manual_wait_s == 20.0 and page.calls == ["https://honestpool.ru/puzzle?id=71"]


def test_fetch_path_receives_a_solver_never_the_settings_or_key() -> None:
    params = browser.ListingFetcher.__init__.__code__.co_varnames
    assert "solver" in params and not {p for p in params if "provider" in p or "key" in p}
    fields = set(browser.BrowserSettings.__dataclass_fields__)
    assert not {f for f in fields if "provider" in f or "api_key" in f}
