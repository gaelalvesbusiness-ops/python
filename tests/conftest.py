from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from scraper.database import Database


@pytest.fixture(autouse=True)
def no_captcha_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never see a real CAPTCHA provider configuration from the developer's shell."""
    for name in ("CAPTCHA_PROVIDER", "CAPTCHA_API_KEY", "CAPTCHA_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "honestpool.db"


@pytest.fixture
def db(db_path: Path) -> Iterator[Database]:
    database = Database(db_path)
    try:
        yield database
    finally:
        database.close()
