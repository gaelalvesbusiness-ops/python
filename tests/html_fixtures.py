"""Synthetic HonestPool listing pages and an offline fake site for tests."""

from __future__ import annotations

from collections.abc import Iterable

from scraper.pagination import page_segments
from scraper.parser import VERIFIED_TEXT, SegmentRecord, segment_range

VERIFIED_BADGE = f'<span class="badge bg-success">{VERIFIED_TEXT}</span>'
UNVERIFIED_BADGE = '<span class="badge bg-secondary">Не проверено</span>'
HEADERS = "<th>Number</th><th>Start hex</th><th>End hex</th><th>Comment</th><th>Status</th><th></th>"
CAPTCHA_PAGE = ("<html><head><title>HONEST BITCOIN PUZZLE POOL | CAPTCHA</title></head>"
                "<body>Ты не бот? Так пройди же капчу!</body></html>")


def captcha_form_page(challenge: int = 1) -> str:
    """CAPTCHA page with the site's form; each ``challenge`` has its own image and csrf token."""
    return ("<html><head><title>HONEST BITCOIN PUZZLE POOL | CAPTCHA</title></head><body>"
            "<div>Ты не бот? Так пройди же капчу!</div><form method=\"post\">"
            f'<input type="hidden" name="csrf" value="synthetic-csrf-{challenge}">'
            f'<img src="captcha_image?challenge={challenge}" alt="">'
            '<input type="text" name="captcha" placeholder="Неее, ты все-таки бот...">'
            '<button type="submit" name="btn_check_captcha" value="1">Отправить</button>'
            "</form></body></html>")


def record(number: int) -> SegmentRecord:
    return SegmentRecord(number, *segment_range(number), VERIFIED_TEXT)


def listing_row(number: int, verified: bool = False, start: str | None = None, end: str | None = None) -> str:
    exp_start, exp_end = segment_range(number)
    badge = VERIFIED_BADGE if verified else UNVERIFIED_BADGE
    return (f'<tr class="tr_{number}" id="tag_search_{number}"><th>{number}</th>'
            f"<td>{start or exp_start}</td><td>{end or exp_end}</td>"
            f'<td class="comment">c</td><td>{badge}</td><td>13.10.2026</td></tr>')


def listing_html(rows: Iterable[str]) -> str:
    return ('<html><body><table class="table table-bordered statustic-table"><tbody>'
            "<tr><th>Проверено</th><th>4</th></tr></tbody></table>"
            f'<table class="table"><thead><tr>{HEADERS}</tr></thead><tbody>{"".join(rows)}</tbody></table>'
            "</body></html>")


def listing_page(page: int, verified: Iterable[int] = ()) -> str:
    """A complete listing page; ``verified`` holds the segment numbers shown as Проверено."""
    verified = set(verified)
    return listing_html(listing_row(n, n in verified) for n in page_segments(page))


class FakeSite:
    """Offline page source: serves synthetic pages, or scripted responses/exceptions per page."""

    def __init__(self, verified: dict[int, Iterable[int]] | None = None,
                 scripted: dict[int, list[str | BaseException]] | None = None) -> None:
        self.verified = {page: set(nums) for page, nums in (verified or {}).items()}
        self.scripted = {page: list(items) for page, items in (scripted or {}).items()}
        self.calls: list[int] = []

    def __call__(self, page: int) -> str:
        self.calls.append(page)
        queue = self.scripted.get(page)
        if queue:
            item = queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        return listing_page(page, self.verified.get(page, ()))
