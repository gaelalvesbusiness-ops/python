"""Terminal progress report for live runs, based only on measured page timings.

Averages use the wall-clock time of pages completed in this session, including request
pacing and retries but excluding time spent waiting for a manual CAPTCHA, which cannot
be predicted. The pacer waits before every navigation except the first of a session, so
every completed page already contains its delay except the session's first page; when
the configured delay is known, that one page is counted as at least the average delay
(never measured time plus delay, which would count the delay twice). Estimates are
therefore approximate, assume the current pace continues and are never guaranteed.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from scraper.config import MAX_PAGE_ATTEMPTS
from scraper.database import COMPLETED, FAILED, PENDING, PROCESSING, Database


def format_duration(seconds: float) -> str:
    seconds = max(0, round(seconds))
    days, rest = divmod(seconds, 86_400)
    hours, rest = divmod(rest, 3_600)
    minutes, secs = divmod(rest, 60)
    clock = f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{days} d {clock}" if days else clock


@dataclass(frozen=True)
class ProgressSnapshot:
    current_page: int | None
    start: int
    end: int
    completed: int
    pending: int
    failed: int
    records: int
    last_success: int | None
    runtime_s: float
    measured_pages: int
    average_s: float | None
    remaining_range: int
    remaining_run: int
    per_page_s: float | None = None
    first_page_raised_to_s: float | None = None
    min_delay_s: float | None = None

    @property
    def estimate_basis_s(self) -> float | None:
        return self.average_s if self.per_page_s is None else self.per_page_s

    @property
    def eta_range_s(self) -> float | None:
        basis = self.estimate_basis_s
        return None if basis is None else basis * self.remaining_range

    @property
    def eta_run_s(self) -> float | None:
        basis = self.estimate_basis_s
        return None if basis is None else basis * self.remaining_run

    @property
    def delay_floor_run_s(self) -> float | None:
        """Lower bound from configured delays alone: every page after the next waits at least the delay."""
        if not self.min_delay_s or self.remaining_run < 2:
            return None
        return self.min_delay_s * (self.remaining_run - 1)


class ProgressReporter:
    """Tracks one live session and renders its progress for the terminal.

    ``delay_s``/``jitter_s`` are the configured pacing between page requests; without
    them the estimate uses measured durations only.
    """

    def __init__(self, db: Database, start: int, end: int, *, max_attempts: int = MAX_PAGE_ATTEMPTS,
                 page_limit: int | None = None, delay_s: float | None = None, jitter_s: float = 0.0,
                 clock: Callable[[], float] = time.monotonic, notify: Callable[[str], None] = print) -> None:
        self.db, self.start, self.end = db, start, end
        self.max_attempts, self.page_limit = max_attempts, page_limit
        self.delay_s, self.jitter_s = delay_s, jitter_s
        self._clock, self.notify = clock, notify
        self.excluded_time: Callable[[], float] = lambda: 0.0
        self.session_started = clock()
        self.durations: list[float] = []
        self.first_page_measured = False
        self.started = self.completed = self.failed = self.interrupted = 0
        self.current_page: int | None = None
        self.stopped_page: int | None = None
        self.ended = False
        self.last_success = db.last_completed_page(start, end)
        self._page_started = 0.0
        self._excluded_at_start = 0.0

    @property
    def finished(self) -> int:
        return self.completed + self.failed

    @property
    def expected_interval_s(self) -> float | None:
        return None if self.delay_s is None else self.delay_s + self.jitter_s / 2

    def page_started(self, page: int) -> None:
        self.started += 1
        self.current_page = page
        self._page_started = self._clock()
        self._excluded_at_start = self.excluded_time()
        self.notify(self.render())

    def page_finished(self, page: int, completed: bool) -> None:
        if completed:
            excluded = self.excluded_time() - self._excluded_at_start
            self.durations.append(max(0.0, self._clock() - self._page_started - excluded))
            self.first_page_measured = self.first_page_measured or self.started == 1
            self.last_success = page
            self.completed += 1
        else:
            self.failed += 1
        self.current_page = None

    def page_stopped(self, page: int) -> None:
        self.interrupted += 1
        self.stopped_page = page
        self.current_page = None
        self.run_ended()

    def run_ended(self) -> None:
        """The run stopped early: nothing more will be fetched in this run."""
        self.ended = True

    def _per_page(self) -> tuple[float | None, float | None]:
        if not self.durations:
            return None, None
        durations = list(self.durations)
        raised_to = None
        interval = self.expected_interval_s
        if self.first_page_measured and interval and durations[0] < interval:
            durations[0] = raised_to = interval
        return sum(durations) / len(durations), raised_to

    def snapshot(self) -> ProgressSnapshot:
        counts = self.db.progress_counts(self.start, self.end)
        registered = sum(counts.values())
        remaining_range = self.db.remaining_count(self.start, self.end, self.max_attempts)
        remaining_run = remaining_range
        if self.ended:
            remaining_run = 0
        elif self.page_limit is not None:
            remaining_run = min(remaining_range, max(0, self.page_limit - self.finished))
        per_page, raised_to = self._per_page()
        return ProgressSnapshot(
            current_page=self.current_page, start=self.start, end=self.end,
            completed=counts[COMPLETED],
            pending=counts[PENDING] + counts[PROCESSING] + (self.end - self.start + 1 - registered),
            failed=counts[FAILED], records=self.db.record_count(), last_success=self.last_success,
            runtime_s=self._clock() - self.session_started, measured_pages=len(self.durations),
            average_s=sum(self.durations) / len(self.durations) if self.durations else None,
            remaining_range=remaining_range, remaining_run=remaining_run,
            per_page_s=per_page, first_page_raised_to_s=raised_to, min_delay_s=self.delay_s,
        )

    def render(self) -> str:
        s = self.snapshot()
        current = f"{s.current_page:,}" if s.current_page is not None else "none"
        last = f"{s.last_success:,}" if s.last_success is not None else "none yet"
        if s.average_s is None:
            average = "no completed page measured yet"
            estimate = "unknown until a page has been completed in this session"
            if s.delay_floor_run_s is not None:
                estimate += (f" (configured delays alone add at least {format_duration(s.delay_floor_run_s)} "
                             f"for {s.remaining_run:,} page(s))")
        else:
            average = f"{s.average_s:.1f} s ({s.measured_pages} measured, CAPTCHA waits excluded)"
            run_part = (f"this run {format_duration(s.eta_run_s or 0)} for {s.remaining_run:,} page(s)"
                        if s.remaining_run else "nothing left in this run")
            estimate = (f"{run_part}; "
                        f"whole range {format_duration(s.eta_range_s or 0)} for {s.remaining_range:,} page(s)")
        lines = [
            f"Progress | current page: {current} | range {s.start:,}..{s.end:,} ({s.end - s.start + 1:,} pages)",
            f"  pages: {s.completed:,} completed, {s.pending:,} pending, {s.failed:,} failed | "
            f"verified records stored: {s.records:,} | last successful page: {last}",
            f"  session runtime: {format_duration(s.runtime_s)} | average per completed page: {average}",
            f"  estimated remaining (approximate, not guaranteed; excludes CAPTCHA waits and server slowdowns): "
            f"{estimate}",
        ]
        if s.estimate_basis_s is not None:
            basis = f"  estimate basis: {s.estimate_basis_s:.1f} s per page"
            if s.first_page_raised_to_s is not None:
                basis += (f" (first page of this session had no delay before it and is counted as the "
                          f"{s.first_page_raised_to_s:.1f} s average configured delay)")
            lines.append(basis + "; retries, slow server responses and CAPTCHA waits can make the real time longer")
        return "\n".join(lines)
