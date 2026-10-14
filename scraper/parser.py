"""HTML parsing and row extraction for the original HonestPool puzzle listing.

Only rows whose status cell holds a single ``span.badge.bg-success`` reading exactly
"Проверено" are accepted, and their displayed hex range must equal the range computed
from the segment number.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from scraper.config import HEX_WIDTH, RANGE_START, SEGMENT_COUNT, SEGMENT_SIZE

logger = logging.getLogger(__name__)

VERIFIED_TEXT = "Проверено"
VERIFIED_BADGE_CLASS = "bg-success"
EXPECTED_HEADERS = ("Number", "Start hex", "End hex", "Comment", "Status")
ROW_ID_PREFIX = "tag_search_"

NOT_VERIFIED = "not_verified"
WRONG_BADGE = "wrong_badge"
MISSING_VALUE = "missing_value"
MALFORMED_ROW = "malformed_row"
INVALID_NUMBER = "invalid_number"
ID_MISMATCH = "id_mismatch"
MASKED_HEX = "masked_hex"
INVALID_HEX = "invalid_hex"
RANGE_MISMATCH = "range_mismatch"
DUPLICATE = "duplicate"

_HEX_RE = re.compile(rf"[0-9a-f]{{{HEX_WIDTH}}}")
_NUMBER_RE = re.compile(r"[0-9]+")
_MASK_RE = re.compile(r"[*•…]|\.\.\.")
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"))


class TableNotFoundError(ValueError):
    """No unique table with a "Start hex" header exists in the page."""


class UnsupportedLayoutError(ValueError):
    """The data table does not have the original listing's column layout."""


@dataclass(frozen=True)
class SegmentRecord:
    number: int
    start_hex: str
    end_hex: str
    status: str


@dataclass(frozen=True)
class Rejection:
    row_index: int
    number: int | None
    reason: str
    detail: str


@dataclass
class ParseResult:
    rows_inspected: int = 0
    records: list[SegmentRecord] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)

    def reason_counts(self) -> dict[str, int]:
        return dict(Counter(r.reason for r in self.rejections))


def segment_range(number: int) -> tuple[str, str]:
    """Return the (start, end) hex strings for a 1-based segment number."""
    if not 1 <= number <= SEGMENT_COUNT:
        raise ValueError(f"segment number {number} outside 1..{SEGMENT_COUNT}")
    start = RANGE_START + (number - 1) * SEGMENT_SIZE
    return f"{start:0{HEX_WIDTH}x}", f"{start + SEGMENT_SIZE:0{HEX_WIDTH}x}"


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text).translate(_INVISIBLE)
    return " ".join(text.split())


def find_data_table(soup: BeautifulSoup) -> Tag:
    """Return the single table whose header row contains "Start hex"."""
    tables = [
        table for table in soup.find_all("table")
        if "Start hex" in _header_texts(table)
    ]
    if len(tables) != 1:
        raise TableNotFoundError(f"expected one table with a 'Start hex' header, found {len(tables)}")
    table = tables[0]
    headers = _header_texts(table)
    if tuple(headers[:len(EXPECTED_HEADERS)]) != EXPECTED_HEADERS:
        raise UnsupportedLayoutError(
            f"data table headers {headers} do not match the original listing {list(EXPECTED_HEADERS)}"
        )
    return table


def parse_page(html: str, seen: dict[int, SegmentRecord] | None = None) -> ParseResult:
    """Extract verified records from one listing page.

    ``seen`` carries already-accepted records across pages so duplicates are detected
    for the whole run; it is updated in place.
    """
    seen = {} if seen is None else seen
    table = find_data_table(BeautifulSoup(html, "html.parser"))
    result = ParseResult()
    body = table.find("tbody")
    rows = body.find_all("tr", recursive=False) if body else []
    for index, row in enumerate(rows, 1):
        result.rows_inspected += 1
        outcome = _parse_row(index, row)
        if isinstance(outcome, Rejection):
            _reject(result, outcome)
            continue
        previous = seen.get(outcome.number)
        if previous is not None:
            detail = "same values" if previous == outcome else f"conflicts with earlier {previous}"
            _reject(result, Rejection(index, outcome.number, DUPLICATE, detail))
            continue
        seen[outcome.number] = outcome
        result.records.append(outcome)
    return result


def parse_file(path: Path, seen: dict[int, SegmentRecord] | None = None) -> ParseResult:
    return parse_page(path.read_text(encoding="utf-8"), seen)


def _header_texts(table: Tag) -> list[str]:
    head = table.find("thead")
    header_row = head.find("tr") if head else table.find("tr")
    if header_row is None:
        return []
    return [normalize_text(c.get_text()) for c in header_row.find_all(["th", "td"], recursive=False)]


def _reject(result: ParseResult, rejection: Rejection) -> None:
    result.rejections.append(rejection)
    level = logging.DEBUG if rejection.reason == NOT_VERIFIED else logging.WARNING
    logger.log(level, "row %d (number=%s) rejected: %s - %s",
               rejection.row_index, rejection.number, rejection.reason, rejection.detail)


def _parse_row(index: int, row: Tag) -> SegmentRecord | Rejection:
    row_id = row.get("id") or ""
    if not row_id.startswith(ROW_ID_PREFIX):
        return Rejection(index, None, MALFORMED_ROW, f"row id {row_id!r} lacks prefix {ROW_ID_PREFIX!r}")

    cells = row.find_all(["th", "td"], recursive=False)
    if not cells or cells[0].name != "th":
        return Rejection(index, None, MALFORMED_ROW, "first cell is not the Number <th>")
    number_cell, tds = cells[0], cells[1:]
    if len(tds) < 4:
        return Rejection(index, None, MISSING_VALUE, f"expected at least 4 <td> cells, found {len(tds)}")

    number_text = normalize_text(number_cell.get_text())
    if not number_text:
        return Rejection(index, None, MISSING_VALUE, "empty Number")
    if not _NUMBER_RE.fullmatch(number_text):
        return Rejection(index, None, INVALID_NUMBER, f"Number {number_text!r} is not a positive integer")
    number = int(number_text)
    if not 1 <= number <= SEGMENT_COUNT:
        return Rejection(index, number, INVALID_NUMBER, f"Number outside 1..{SEGMENT_COUNT}")
    if row_id != f"{ROW_ID_PREFIX}{number}":
        return Rejection(index, number, ID_MISMATCH, f"row id {row_id!r} does not match Number")

    status = _check_status(tds[3])
    if isinstance(status, tuple):
        reason, detail = status
        return Rejection(index, number, reason, detail)

    expected = segment_range(number)
    displayed: list[str] = []
    for label, cell, want in zip(("Start hex", "End hex"), tds[:2], expected, strict=True):
        value = _check_hex(cell)
        if isinstance(value, tuple):
            reason, detail = value
            return Rejection(index, number, reason, f"{label}: {detail}")
        if value != want:
            return Rejection(index, number, RANGE_MISMATCH,
                             f"{label} displayed {value} but segment {number} expects {want}")
        displayed.append(value)
    return SegmentRecord(number, displayed[0], displayed[1], status)


def _check_status(cell: Tag) -> str | tuple[str, str]:
    badges = cell.select("span.badge")
    if not badges:
        text = normalize_text(cell.get_text())
        return (MISSING_VALUE, "no status badge") if not text else (NOT_VERIFIED, f"unbadged status {text!r}")
    if len(badges) > 1:
        return MALFORMED_ROW, f"{len(badges)} badges in status cell"
    badge = badges[0]
    text = normalize_text(badge.get_text())
    if text != VERIFIED_TEXT:
        return NOT_VERIFIED, f"status {text!r}"
    colours = [c for c in badge.get("class", []) if c.startswith("bg-")]
    if colours != [VERIFIED_BADGE_CLASS]:
        return WRONG_BADGE, f"'{VERIFIED_TEXT}' badge has colour classes {colours}"
    return text


def _check_hex(cell: Tag) -> str | tuple[str, str]:
    text = normalize_text(cell.get_text())
    if cell.find(class_="range_hex_hidden") or _MASK_RE.search(text):
        return MASKED_HEX, f"masked value {text!r}"
    if not text:
        return MISSING_VALUE, "empty value"
    if cell.find(True) is not None:
        return INVALID_HEX, "value contains nested markup"
    if not _HEX_RE.fullmatch(text):
        return INVALID_HEX, f"{text!r} is not {HEX_WIDTH} lowercase hex digits"
    return text
