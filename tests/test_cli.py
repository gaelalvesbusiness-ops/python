"""Command-line page selection and export tests (no fetching happens in Step 5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from main import main
from scraper.config import TOTAL_PAGES
from scraper.database import PENDING, Database
from tests.html_fixtures import record


def run(db_path: Path, *args: str) -> int:
    return main([*args, "--db", str(db_path)])


def seed(db_path: Path, page: int, numbers: list[int]) -> None:
    with Database(db_path) as db:
        db.claim_page(page)
        db.complete_page(page, [record(n) for n in numbers], 100)


def test_range_is_planned_and_saved(db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(db_path, "--start-page", "1", "--end-page", "100") == 0
    out = capsys.readouterr().out
    assert "pages 1..100 (100 pages)" in out
    assert "Pages to process: 100" in out
    assert "no pages were fetched" in out
    with Database(db_path) as db:
        assert db.load_run_range() == (1, 100)
        assert db.progress_counts(1, 100)[PENDING] == 100


def test_selected_high_range(db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(db_path, "--start-page", "350000", "--end-page", "350500") == 0
    assert "Pages to process: 501 (next: [350000, 350001" in capsys.readouterr().out


def test_resume_uses_saved_range_and_skips_completed(db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run(db_path, "--start-page", "1", "--end-page", "5")
    seed(db_path, 2, [150])
    capsys.readouterr()
    assert run(db_path, "--resume") == 0
    out = capsys.readouterr().out
    assert "pages 1..5" in out
    assert "Pages to process: 4 (next: [1, 3, 4, 5])" in out
    assert "Verified records stored: 1" in out


def test_resume_reports_interrupted_page(db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run(db_path, "--start-page", "1", "--end-page", "3")
    with Database(db_path) as db:
        db.claim_page(3)
    capsys.readouterr()
    run(db_path, "--resume")
    out = capsys.readouterr().out
    assert "Recovered interrupted pages: 1" in out
    assert "next: [3, 1, 2]" in out


@pytest.mark.parametrize(
    "args",
    [
        ["--start-page", "0", "--end-page", "5"],
        ["--start-page", "10", "--end-page", "5"],
        ["--start-page", "1", "--end-page", str(TOTAL_PAGES + 1)],
        ["--start-page", "5"],
        ["--end-page", "5"],
        ["--start-page", "abc", "--end-page", "5"],
        ["--start-page", "1", "--end-page", "2", "--max-attempts", "0"],
    ],
)
def test_invalid_page_ranges_exit_without_creating_database(db_path: Path, args: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        run(db_path, *args)
    assert exc.value.code == 2
    assert not db_path.exists()


def test_resume_without_saved_range_is_an_error(db_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        run(db_path, "--resume")
    assert exc.value.code == 2


def test_second_writer_is_refused(db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with Database(db_path):
        assert run(db_path, "--start-page", "1", "--end-page", "2") == 1
    assert "another process is writing" in capsys.readouterr().err


@pytest.mark.parametrize("fmt", ["csv", "xlsx"])
def test_export_from_database(db_path: Path, tmp_path: Path, fmt: str, capsys: pytest.CaptureFixture[str]) -> None:
    seed(db_path, 1, [1, 2])
    output = tmp_path / f"out.{fmt}"
    assert run(db_path, "--export", fmt, "--output", str(output)) == 0
    assert output.exists()
    assert "Exported 2 verified records" in capsys.readouterr().out


def test_export_suffix_must_match_format(db_path: Path, tmp_path: Path) -> None:
    seed(db_path, 1, [1])
    with pytest.raises(SystemExit):
        run(db_path, "--export", "xlsx", "--output", str(tmp_path / "out.csv"))


def test_export_without_database_fails_cleanly(db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(db_path, "--export", "csv") == 1
    assert "does not exist" in capsys.readouterr().err


def test_planning_without_live_fetches_nothing(db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(db_path, "--start-page", "1", "--end-page", "3") == 0
    assert "Planning only" in capsys.readouterr().out
    with Database(db_path) as db:
        assert db.progress_counts(1, 3)[PENDING] == 3
        assert db.record_count() == 0


def test_live_requires_a_page_range() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--live"])
    assert exc.value.code == 2


@pytest.mark.parametrize("args", [
    ["--start-page", "1", "--end-page", "2", "--live", "--max-pages", "0"],
    ["--start-page", "1", "--end-page", "2", "--live", "--delay", "1"],
    ["--start-page", "1", "--end-page", "2", "--live", "--jitter", "-1"],
])
def test_live_flag_validation(db_path: Path, args: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        run(db_path, *args)
    assert exc.value.code == 2
