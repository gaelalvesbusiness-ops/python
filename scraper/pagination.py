"""Page range validation and page-to-segment mapping for the original listing."""

from __future__ import annotations

from scraper.config import PAGE_SIZE, SEGMENT_COUNT, TOTAL_PAGES


class InvalidPageRangeError(ValueError):
    """A requested page or page range lies outside 1..TOTAL_PAGES or is reversed."""


def validate_page(page: int) -> int:
    if isinstance(page, bool) or not isinstance(page, int):
        raise InvalidPageRangeError(f"page number must be an integer, got {page!r}")
    if not 1 <= page <= TOTAL_PAGES:
        raise InvalidPageRangeError(f"page {page} outside 1..{TOTAL_PAGES}")
    return page


def validate_range(start: int, end: int) -> tuple[int, int]:
    validate_page(start)
    validate_page(end)
    if end < start:
        raise InvalidPageRangeError(f"end page {end} is before start page {start}")
    return start, end


def page_segments(page: int) -> range:
    """Segment numbers listed on ``page``."""
    validate_page(page)
    first = (page - 1) * PAGE_SIZE + 1
    return range(first, min(page * PAGE_SIZE, SEGMENT_COUNT) + 1)


def page_for_segment(number: int) -> int:
    if not 1 <= number <= SEGMENT_COUNT:
        raise ValueError(f"segment number {number} outside 1..{SEGMENT_COUNT}")
    return (number - 1) // PAGE_SIZE + 1


def page_url(base_url: str, page: int) -> str:
    """Listing URL for ``page``; page 1 has no page parameter, as on the live site."""
    validate_page(page)
    return base_url if page == 1 else f"{base_url}&page={page}"
