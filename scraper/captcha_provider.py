"""CAPTCHA provider configuration: which provider solves image CAPTCHAs, validated up front.

Reads CAPTCHA_PROVIDER, CAPTCHA_API_KEY and CAPTCHA_TIMEOUT from the environment. With the
2captcha provider and an API key, ``create_solver`` returns the 2Captcha client used by the
live fetcher; otherwise CAPTCHAs are completed manually in the browser window (or stop the run),
which also remains the fallback whenever automatic solving fails. The API key is never printed,
logged or stored; only whether it is set is reported. Once the solver holds the key,
``withdraw_api_key`` removes it from the environment, so the Playwright driver and Chromium,
which are started later, never inherit it.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from typing import Any

from scraper.config import (
    CAPTCHA_API_KEY_ENV,
    CAPTCHA_PROVIDER_ENV,
    CAPTCHA_PROVIDERS,
    CAPTCHA_TIMEOUT_ENV,
    DEFAULT_CAPTCHA_TIMEOUT_S,
    MANUAL_PROVIDER,
    MAX_CAPTCHA_TIMEOUT_S,
    MIN_CAPTCHA_TIMEOUT_S,
    TWO_CAPTCHA_PROVIDER,
)
from scraper.two_captcha import TwoCaptchaClient

AUTOMATIC_NOTE = ("automatic solving: enabled (only the CAPTCHA image is sent to the provider; manual "
                  "verification in the browser window remains the fallback)")
MANUAL_NOTE = "automatic solving: disabled; CAPTCHAs are completed manually in the browser window"
_SAFE_ECHO = re.compile(r"[A-Za-z0-9_-]{1,15}")


class CaptchaConfigError(ValueError):
    """The CAPTCHA provider environment variables are invalid; messages never contain the API key."""


@dataclass(frozen=True)
class CaptchaProviderConfig:
    provider: str = MANUAL_PROVIDER
    timeout_s: float = DEFAULT_CAPTCHA_TIMEOUT_S
    api_key: str | None = field(default=None, repr=False, compare=False)
    warnings: tuple[str, ...] = ()

    @property
    def is_external(self) -> bool:
        return self.provider != MANUAL_PROVIDER

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    @property
    def automated_submission(self) -> bool:
        return self.provider == TWO_CAPTCHA_PROVIDER and self.has_api_key

    def __str__(self) -> str:
        return self.describe()

    def describe(self) -> str:
        """Status for the terminal; shows only whether the key is set."""
        if not self.is_external:
            return "CAPTCHA provider: manual (default); CAPTCHAs are completed manually in the browser window"
        key = "set (hidden)" if self.has_api_key else f"missing ({CAPTCHA_API_KEY_ENV} is not set)"
        note = AUTOMATIC_NOTE if self.automated_submission else MANUAL_NOTE
        return f"CAPTCHA provider: {self.provider} | API key: {key} | timeout: {self.timeout_s:g} s | {note}"


def create_solver(config: CaptchaProviderConfig, **options: Any) -> TwoCaptchaClient | None:
    """The configured solver, or None when CAPTCHAs are completed manually."""
    if not config.automated_submission or config.api_key is None:
        return None
    return TwoCaptchaClient(config.api_key, timeout_s=config.timeout_s, **options)


def withdraw_api_key(environ: MutableMapping[str, str] | None = None) -> None:
    """Remove CAPTCHA_API_KEY from the environment so processes started afterwards never inherit it.

    Call it only after ``load_captcha_config`` and ``create_solver``: from then on the key exists
    only inside the configuration and the 2Captcha client of this process.
    """
    (os.environ if environ is None else environ).pop(CAPTCHA_API_KEY_ENV, None)


def load_captcha_config(environ: Mapping[str, str] | None = None) -> CaptchaProviderConfig:
    """Validate the CAPTCHA environment variables; raises CaptchaConfigError on invalid values."""
    env = os.environ if environ is None else environ
    provider = _provider(env.get(CAPTCHA_PROVIDER_ENV, ""))
    timeout_s = _timeout(env.get(CAPTCHA_TIMEOUT_ENV, ""))
    api_key = _api_key(env.get(CAPTCHA_API_KEY_ENV, ""))
    warnings = []
    if provider == MANUAL_PROVIDER:
        if api_key:
            warnings.append(f"{CAPTCHA_API_KEY_ENV} is set but ignored because no external provider is selected")
        api_key = None
    elif not api_key:
        warnings.append(f"{CAPTCHA_API_KEY_ENV} is not set; manual CAPTCHA verification is used")
    return CaptchaProviderConfig(provider, timeout_s, api_key, tuple(warnings))


def _provider(raw: str) -> str:
    value = raw.strip().lower()
    if not value:
        return MANUAL_PROVIDER
    if value not in CAPTCHA_PROVIDERS:
        shown = repr(raw.strip()) if _SAFE_ECHO.fullmatch(raw.strip()) else "(value hidden)"
        raise CaptchaConfigError(f"{CAPTCHA_PROVIDER_ENV} {shown} is not supported; use one of "
                                 f"{', '.join(CAPTCHA_PROVIDERS)} or leave it empty")
    return value


def _timeout(raw: str) -> float:
    value = raw.strip()
    if not value:
        return DEFAULT_CAPTCHA_TIMEOUT_S
    try:
        seconds = float(value)
    except ValueError:
        raise CaptchaConfigError(f"{CAPTCHA_TIMEOUT_ENV} must be a number of seconds") from None
    if not math.isfinite(seconds) or not MIN_CAPTCHA_TIMEOUT_S <= seconds <= MAX_CAPTCHA_TIMEOUT_S:
        raise CaptchaConfigError(f"{CAPTCHA_TIMEOUT_ENV} must be between {MIN_CAPTCHA_TIMEOUT_S:g} and "
                                 f"{MAX_CAPTCHA_TIMEOUT_S:g} seconds")
    return seconds


def _api_key(raw: str) -> str | None:
    value = raw.strip()
    if not value:
        return None
    if not value.isascii() or not value.isprintable() or any(c.isspace() for c in value):
        raise CaptchaConfigError(f"{CAPTCHA_API_KEY_ENV} is malformed (it contains spaces, control or non-ASCII "
                                 f"characters); the value is not shown")
    return value
