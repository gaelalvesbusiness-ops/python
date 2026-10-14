"""Offline import of verified segment numbers supplied by an authorized, trusted data source.

Nothing here contacts the website. Accepted files (UTF-8, optional BOM):
  .txt  one segment number per line; blank lines are ignored.
  .csv  a header row with a column named exactly "Number". Optional "Start hex", "End hex" and
        "Status" columns (as written by --export) must match the computed range and "Проверено".
A file with any invalid value is rejected as a whole, so nothing is guessed or partly imported.
Duplicate numbers are removed. Imported records keep their own provenance and never mark a
website page as completed.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path

from scraper.config import SEGMENT_COUNT
from scraper.database import IMPORT_FORMATS, Database, ImportOutcome
from scraper.parser import VERIFIED_TEXT, SegmentRecord, normalize_text, segment_range

NUMBER_COLUMN = "Number"
CHECKED_COLUMNS = ("Start hex", "End hex")
STATUS_COLUMN = "Status"
MAX_REPORTED_PROBLEMS = 20
_DIGITS = re.compile(r"[0-9]+")


class ImportFileError(ValueError):
    """The import file was rejected; nothing was written."""

    def __init__(self, path: Path, problems: list[str]) -> None:
        self.path, self.problems = path, problems
        shown = "; ".join(problems[:MAX_REPORTED_PROBLEMS])
        more = len(problems) - MAX_REPORTED_PROBLEMS
        super().__init__(f"{path.name}: {shown}" + (f"; ... and {more} more problem(s)" if more > 0 else ""))


@dataclass(frozen=True)
class VerifiedFile:
    path: Path
    file_format: str
    sha256: str
    values_read: int
    numbers: tuple[int, ...]

    @property
    def duplicates(self) -> int:
        return self.values_read - len(self.numbers)

    def records(self) -> list[SegmentRecord]:
        return [SegmentRecord(n, *segment_range(n), VERIFIED_TEXT) for n in self.numbers]


def parse_segment_number(text: str) -> int:
    """A whole decimal number in 1..SEGMENT_COUNT; signs, decimals, separators and hex are rejected."""
    value = text.strip()
    if not value:
        raise ValueError("empty value")
    if not _DIGITS.fullmatch(value):
        raise ValueError(f"{value[:40]!r} is not a whole decimal number")
    if len(value.lstrip("0")) > len(str(SEGMENT_COUNT)) or not 1 <= int(value) <= SEGMENT_COUNT:
        raise ValueError(f"{value[:40]} is outside 1..{SEGMENT_COUNT:,}")
    return int(value)


def read_verified_file(path: Path) -> VerifiedFile:
    """Read and validate an import file; raises ImportFileError without touching any database."""
    path = Path(path)
    file_format = path.suffix.lower().lstrip(".")
    if file_format not in IMPORT_FORMATS:
        raise ImportFileError(path, [f"unsupported file type {path.suffix or '(none)'!r}; use .txt or .csv"])
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ImportFileError(path, [f"cannot read file: {exc.strerror or exc}"]) from None
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportFileError(path, [f"not UTF-8 text (invalid byte at offset {exc.start})"]) from None
    if "\x00" in text:
        raise ImportFileError(path, ["binary data (NUL bytes) found; expected a text file"])
    numbers, problems = (_read_txt if file_format == "txt" else _read_csv)(text)
    if problems:
        raise ImportFileError(path, problems)
    if not numbers:
        raise ImportFileError(path, ["no segment numbers found (the file is empty)"])
    return VerifiedFile(path, file_format, hashlib.sha256(data).hexdigest(), len(numbers),
                        tuple(sorted(set(numbers))))


def store_import(db: Database, verified: VerifiedFile, source: str) -> ImportOutcome:
    """Store a validated file's numbers as imported verified records attributed to ``source``."""
    return db.import_verified(verified.records(), source=source, file_name=verified.path.name,
                              file_sha256=verified.sha256, file_format=verified.file_format,
                              duplicates=verified.duplicates)


def import_file(db: Database, path: Path, source: str) -> tuple[VerifiedFile, ImportOutcome]:
    """Validate ``path`` and store its numbers; raises ImportFileError before writing anything."""
    verified = read_verified_file(path)
    return verified, store_import(db, verified, source)


def _read_txt(text: str) -> tuple[list[int], list[str]]:
    numbers, problems = [], []
    for line_number, line in enumerate(text.split("\n"), 1):
        if not line.strip():
            continue
        try:
            numbers.append(parse_segment_number(line))
        except ValueError as exc:
            problems.append(f"line {line_number}: {exc}")
    return numbers, problems


def _read_csv(text: str) -> tuple[list[int], list[str]]:
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    numbers: list[int] = []
    problems: list[str] = []
    header: list[str] | None = None
    try:
        for row in reader:
            line = reader.line_num
            if not row:
                continue
            if header is None:
                header = [normalize_text(cell) for cell in row]
                problems += _header_problems(header)
                if problems:
                    return [], problems
                continue
            if len(row) != len(header):
                problems.append(f"line {line}: {len(row)} field(s), the header has {len(header)}")
                continue
            values = dict(zip(header, row, strict=True))
            try:
                number = parse_segment_number(values[NUMBER_COLUMN])
            except ValueError as exc:
                problems.append(f"line {line}: Number {exc}")
                continue
            problem = _row_problem(number, values)
            if problem:
                problems.append(f"line {line}: {problem}")
                continue
            numbers.append(number)
    except csv.Error as exc:
        problems.append(f"malformed CSV at line {reader.line_num}: {exc}")
    return numbers, problems


def _header_problems(header: list[str]) -> list[str]:
    if NUMBER_COLUMN not in header:
        return [f"no column named {NUMBER_COLUMN!r} in the header {header[:10]}"]
    repeated = sorted({name for name in header if header.count(name) > 1})
    return [f"repeated column(s) {repeated} in the header"] if repeated else []


def _row_problem(number: int, values: dict[str, str]) -> str | None:
    expected = dict(zip(CHECKED_COLUMNS, segment_range(number), strict=True))
    for column, want in expected.items():
        if column in values and values[column].strip() != want:
            return f"{column} {values[column].strip()[:40]!r} does not match segment {number} ({want})"
    if STATUS_COLUMN in values and normalize_text(values[STATUS_COLUMN]) != VERIFIED_TEXT:
        return (f"Status {values[STATUS_COLUMN].strip()[:40]!r} is not {VERIFIED_TEXT!r}; "
                f"only verified numbers can be imported")
    return None
