"""Smoke tests for project environment."""

from __future__ import annotations

import importlib


def test_import_scraper_package() -> None:
    pkg = importlib.import_module("scraper")
    assert hasattr(pkg, "__version__")


def test_main_message(capsys) -> None:
    from main import main

    main()
    captured = capsys.readouterr()
    assert "HonestPool Scraper - Environment Ready" in captured.out
