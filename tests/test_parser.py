"""Tests for the original-listing parser, using saved samples and synthetic pages."""

from __future__ import annotations

import csv
import logging
from pathlib import Path

import pytest

from scraper.config import DATA_DIR, SEGMENT_COUNT
from scraper.exporter import CSV_COLUMNS, export_csv
from scraper.parser import (
    DUPLICATE,
    ID_MISMATCH,
    INVALID_HEX,
    INVALID_NUMBER,
    MALFORMED_ROW,
    MASKED_HEX,
    MISSING_VALUE,
    NOT_VERIFIED,
    RANGE_MISMATCH,
    WRONG_BADGE,
    SegmentRecord,
    TableNotFoundError,
    UnsupportedLayoutError,
    parse_file,
    parse_page,
    segment_range,
)

SAMPLE = DATA_DIR / "sample_page.html"
SAMPLE_CHECKED = DATA_DIR / "sample_page_checked.html"

VERIFIED = '<span class="badge bg-success">Проверено</span>'
UNVERIFIED = '<span class="badge bg-secondary">Не проверено</span>'
HEADERS = "<th>Number</th><th>Start hex</th><th>End hex</th><th>Comment</th><th>Status</th><th></th>"
DECOYS = """
<table class="table table-bordered statustic-table"><tbody>
  <tr><th>Проверено</th><th>4</th></tr></tbody></table>
<p>пометить ее как "<span class="badge bg-success">Проверено</span>".</p>
"""


def row(number: int, start: str | None = None, end: str | None = None, status: str = VERIFIED,
        row_id: str | None = None, number_cell: str | None = None, extra_tds: str = "<td>13.10.2026</td>") -> str:
    if start is None or end is None:
        exp_start, exp_end = segment_range(number)
        start = exp_start if start is None else start
        end = exp_end if end is None else end
    row_id = f"tag_search_{number}" if row_id is None else row_id
    number_cell = f"<th>{number}</th>" if number_cell is None else number_cell
    return (f'<tr class="tr_{number}" id="{row_id}">{number_cell}<td>{start}</td><td>{end}</td>'
            f'<td class="comment">c</td><td>{status}</td>{extra_tds}</tr>')


def page(*rows: str, headers: str = HEADERS, decoys: str = DECOYS) -> str:
    return (f"<html><body>{decoys}<table class=\"table\"><thead><tr>{headers}</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></body></html>")


def only_rejection(html: str):
    result = parse_page(html)
    assert result.records == []
    assert len(result.rejections) == 1
    return result.rejections[0]


# --- hexadecimal calculation -------------------------------------------------

@pytest.mark.parametrize(
    ("number", "start", "end"),
    [
        (1, "400000000000000000", "400000100000000000"),
        (10, "400000900000000000", "400000a00000000000"),
        (101, "400006400000000000", "400006500000000000"),
        (838640, "40ccbef00000000000", "40ccbf000000000000"),  # official API documentation example
        (SEGMENT_COUNT, "7ffffff00000000000", "800000000000000000"),
    ],
)
def test_segment_range(number: int, start: str, end: str) -> None:
    assert segment_range(number) == (start, end)
    assert len(start) == len(end) == 18


@pytest.mark.parametrize("number", [0, -1, SEGMENT_COUNT + 1])
def test_segment_range_rejects_out_of_bounds(number: int) -> None:
    with pytest.raises(ValueError):
        segment_range(number)


def test_consecutive_segments_are_contiguous() -> None:
    for n in (1, 2, 999, 671088):
        assert segment_range(n)[1] == segment_range(n + 1)[0]


# --- saved samples -----------------------------------------------------------

@pytest.mark.skipif(not SAMPLE.exists(), reason="saved sample page not available")
def test_sample_page_extracts_only_verified_rows() -> None:
    result = parse_file(SAMPLE)
    assert result.rows_inspected == 100
    assert result.reason_counts() == {NOT_VERIFIED: 91}
    assert [r.number for r in result.records] == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    for r in result.records:
        assert r.status == "Проверено"
        assert (r.start_hex, r.end_hex) == segment_range(r.number)
    assert result.records[0] == SegmentRecord(1, "400000000000000000", "400000100000000000", "Проверено")


@pytest.mark.skipif(not SAMPLE_CHECKED.exists(), reason="saved checked sample not available")
def test_verified_only_listing_layout_is_refused() -> None:
    with pytest.raises(UnsupportedLayoutError):
        parse_file(SAMPLE_CHECKED)


# --- table identification ----------------------------------------------------

def test_status_indicators_outside_data_table_are_ignored() -> None:
    result = parse_page(page(row(1), row(2, status=UNVERIFIED)))
    assert result.rows_inspected == 2
    assert [r.number for r in result.records] == [1]


def test_missing_data_table_raises() -> None:
    with pytest.raises(TableNotFoundError):
        parse_page(f"<html><body>{DECOYS}<table><tr><th>Number</th></tr></table></body></html>")


def test_ambiguous_data_tables_raise() -> None:
    with pytest.raises(TableNotFoundError):
        parse_page(page(row(1)) + page(row(2)))


def test_table_without_number_column_is_refused() -> None:
    headers = "<th>Start hex</th><th>End hex</th><th>Comment</th><th>Status</th><th></th>"
    with pytest.raises(UnsupportedLayoutError):
        parse_page(page(row(1), headers=headers))


# --- status filter -----------------------------------------------------------

def test_verified_row_is_accepted() -> None:
    result = parse_page(page(row(42)))
    assert result.records == [SegmentRecord(42, *segment_range(42), "Проверено")]
    assert result.rejections == []


def test_verified_status_text_is_whitespace_normalized() -> None:
    result = parse_page(page(row(3, status='<span class="badge bg-success">\n  Проверено\u200b </span>')))
    assert [r.number for r in result.records] == [3]


@pytest.mark.parametrize(
    "status",
    [UNVERIFIED, '<span class="badge bg-success">Не проверено</span>',
     '<span class="badge bg-success">Проверено!</span>', "Проверено"],
)
def test_unverified_rows_are_rejected(status: str) -> None:
    assert only_rejection(page(row(5, status=status))).reason == NOT_VERIFIED


@pytest.mark.parametrize(
    "badge_class",
    ["badge bg-secondary", "badge bg-warning", "badge bg-danger", "badge", "badge bg-success bg-danger"],
)
def test_incorrect_badge_colour_is_rejected(badge_class: str) -> None:
    status = f'<span class="{badge_class}">Проверено</span>'
    assert only_rejection(page(row(5, status=status))).reason == WRONG_BADGE


def test_multiple_badges_in_status_cell_are_rejected() -> None:
    assert only_rejection(page(row(5, status=VERIFIED + VERIFIED))).reason == MALFORMED_ROW


# --- missing and malformed values -------------------------------------------

@pytest.mark.parametrize(
    "html",
    [
        page(row(7, start="")),
        page(row(7, end="  ")),
        page(row(7, status="")),
        page(row(7, number_cell="<th> </th>")),
        page('<tr id="tag_search_7"><th>7</th><td>400000600000000000</td><td>400000700000000000</td></tr>'),
    ],
)
def test_missing_values_are_rejected(html: str) -> None:
    assert only_rejection(html).reason == MISSING_VALUE


@pytest.mark.parametrize(
    ("html", "reason"),
    [
        (page(row(7, row_id="other_7")), MALFORMED_ROW),
        (page(row(7, number_cell="<td>7</td>")), MALFORMED_ROW),
        (page(row(7, row_id="tag_search_8")), ID_MISMATCH),
        (page(row(7, number_cell="<th>7a</th>")), INVALID_NUMBER),
        (page(row(7, number_cell=f"<th>{SEGMENT_COUNT + 1}</th>", row_id=f"tag_search_{SEGMENT_COUNT + 1}",
                  start="800000000000000000", end="800000100000000000")), INVALID_NUMBER),
    ],
)
def test_malformed_rows_are_rejected(html: str, reason: str) -> None:
    assert only_rejection(html).reason == reason


# --- hexadecimal validation --------------------------------------------------

@pytest.mark.parametrize(
    "start",
    [
        '<div class="range_hex_hidden">40<div>*</div><div>*</div><div>*</div>0040000000000</div>',
        "40***0040000000000",
        "4000004…000000000",
        "4000004...00000000",
    ],
)
def test_masked_hex_is_rejected(start: str) -> None:
    assert only_rejection(page(row(5, start=start))).reason == MASKED_HEX


@pytest.mark.parametrize(
    "end",
    ["40000050000000000", "0x400000500000000000", "40000050000000000g", "<b>400000500000000000</b>"],
)
def test_invalid_hex_is_rejected(end: str) -> None:
    assert only_rejection(page(row(5, end=end))).reason == INVALID_HEX


def test_uppercase_hex_is_rejected() -> None:
    assert segment_range(10)[1] == "400000a00000000000"
    assert only_rejection(page(row(10, end="400000A00000000000"))).reason == INVALID_HEX


def test_range_mismatch_is_rejected_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    wrong_start, wrong_end = segment_range(6)
    with caplog.at_level(logging.WARNING, logger="scraper.parser"):
        rejection = only_rejection(page(row(5, start=wrong_start, end=wrong_end)))
    assert rejection.reason == RANGE_MISMATCH
    assert wrong_start in rejection.detail and segment_range(5)[0] in rejection.detail
    assert any("range_mismatch" in m for m in caplog.messages)


def test_end_hex_mismatch_is_rejected() -> None:
    rejection = only_rejection(page(row(5, end=segment_range(5)[0])))
    assert rejection.reason == RANGE_MISMATCH and rejection.detail.startswith("End hex")


# --- duplicates --------------------------------------------------------------

def test_duplicate_number_within_page_is_rejected() -> None:
    result = parse_page(page(row(9), row(9)))
    assert [r.number for r in result.records] == [9]
    assert [(r.reason, r.number) for r in result.rejections] == [(DUPLICATE, 9)]


def test_duplicates_are_detected_across_pages() -> None:
    seen: dict[int, SegmentRecord] = {}
    first = parse_page(page(row(1), row(2)), seen)
    second = parse_page(page(row(2), row(3)), seen)
    assert [r.number for r in first.records] == [1, 2]
    assert [r.number for r in second.records] == [3]
    assert [(r.reason, r.number) for r in second.rejections] == [(DUPLICATE, 2)]
    assert sorted(seen) == [1, 2, 3]


# --- CSV export --------------------------------------------------------------

def test_csv_export_preserves_exact_hex_strings(tmp_path: Path) -> None:
    records = [SegmentRecord(10, *segment_range(10), "Проверено"),
               SegmentRecord(1, *segment_range(1), "Проверено")]
    out = tmp_path / "verified.csv"
    assert export_csv(records, out) == 2
    with out.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    assert tuple(rows[0]) == CSV_COLUMNS
    assert rows[1] == ["1", "400000000000000000", "400000100000000000", "Проверено"]
    assert rows[2] == ["10", "400000900000000000", "400000a00000000000", "Проверено"]
