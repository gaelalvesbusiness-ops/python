"""The offline import demonstration passes and leaves the real data untouched."""

from __future__ import annotations

import pytest

import demo_import


def test_import_demonstration_passes(capsys: pytest.CaptureFixture[str]) -> None:
    before = demo_import.protected_state()
    assert demo_import.main([]) == 0
    out = capsys.readouterr().out
    assert "FAIL" not in out and "Result: all checks passed" in out
    assert "--source 'synthetic demonstration data'" in out
    assert demo_import.protected_state() == before


def test_synthetic_examples_are_valid_import_files() -> None:
    from scraper.importer import read_verified_file

    txt = read_verified_file(demo_import.EXAMPLES / demo_import.TXT_NAME)
    csv_file = read_verified_file(demo_import.EXAMPLES / demo_import.CSV_NAME)
    assert (txt.values_read, len(txt.numbers), csv_file.values_read, len(csv_file.numbers)) == (6, 5, 5, 4)
