"""The offline demonstration reads the database without changing it (offline)."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import pytest

import demo
from scraper.database import Database
from scraper.exporter import export_from_db
from tests.html_fixtures import record


@pytest.fixture
def stored(db_path: Path, tmp_path: Path) -> tuple[Path, Path, Path]:
    with Database(db_path) as db:
        db.claim_page(1)
        db.complete_page(1, [record(n) for n in (1, 2, 9)], 100)
        db.claim_page(2)
        db.complete_page(2, [record(101)], 100)
        csv_path, xlsx_path = tmp_path / "out.csv", tmp_path / "out.xlsx"
        export_from_db(db, csv_path)
        export_from_db(db, xlsx_path)
    return db_path, csv_path, xlsx_path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(db_path: Path, csv_path: Path, xlsx_path: Path) -> int:
    return demo.main(["--db", str(db_path), "--csv", str(csv_path), "--xlsx", str(xlsx_path)])


def test_demo_passes_and_leaves_the_database_unchanged(stored: tuple[Path, Path, Path],
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    db_path, csv_path, xlsx_path = stored
    before = digest(db_path)
    assert run(db_path, csv_path, xlsx_path) == 0
    out = capsys.readouterr().out
    assert "CSV idêntico ao SQLite" in out and "Excel idêntico ao SQLite" in out
    assert "verified records stored: 4" in out and "range 350,000..350,100" in out
    assert "FALHA" not in out
    assert digest(db_path) == before
    with Database(db_path, readonly=True) as db:
        assert db.page(350_000) is None and db.record_count() == 4


def test_demo_reports_an_export_that_differs_from_sqlite(stored: tuple[Path, Path, Path],
                                                         capsys: pytest.CaptureFixture[str]) -> None:
    db_path, csv_path, xlsx_path = stored
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerows(rows[:-1])
    assert run(db_path, csv_path, xlsx_path) == 1
    assert "[FALHA] CSV idêntico ao SQLite" in capsys.readouterr().out


def test_demo_works_from_the_example_exports_without_a_database(stored: tuple[Path, Path, Path], tmp_path: Path,
                                                                capsys: pytest.CaptureFixture[str]) -> None:
    _, csv_path, xlsx_path = stored
    assert run(tmp_path / "missing.db", csv_path, xlsx_path) == 0
    out = capsys.readouterr().out
    assert "CSV idêntico ao Excel" in out and "verified records stored: 0" in out
