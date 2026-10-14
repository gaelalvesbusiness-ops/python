# Production readiness (Step 9)

Status as of 2026-10-13. Nothing in this document was produced by contacting HonestPool;
it relies on the saved run logs, the SQLite database, the test suite and the information
gathered during Steps 2–8.

## 1. Running the scraper

```bash
python main.py --start-page 350000 --end-page 350100          # plan and save the range, fetch nothing
python main.py --start-page 350000 --end-page 350100 --live   # fetch 1 page of that range
python main.py --resume --live                                # continue the saved range (1 page)
python main.py --resume --live --max-pages 50 --nav-timeout 60
python main.py --export xlsx                                  # data/verified_records.xlsx
python main.py --export csv                                   # data/verified_records.csv
```

- There is no default range. A range is only ever processed after it was given explicitly
  with `--start-page`/`--end-page`; `--live` without a range (and without `--resume`) is refused.
- Selecting a new range replaces the saved run range only. Pages and records of earlier
  ranges stay in the database, and completed pages are never fetched again, whatever range
  they are part of.
- `--max-pages` (default 1) caps the pages fetched per invocation. Fetching a whole range needs
  an explicit, sufficiently large value.
- SQLite is the authoritative copy. After each page is committed, its new verified records are
  printed and `verified_records.csv` next to the database is rewritten from SQLite by atomic
  replacement (about 0.25 s per page at 100,000 stored records, 3 s at 1,000,000 on the
  development machine; a warning is logged above 5 s). The XLSX file is only written by
  `--export xlsx`.
- Before each page, and when the run ends, a progress report is printed:

```
Progress | current page: 350042 | range 350000..350100 (101 pages)
  pages: 41 completed, 60 pending, 0 failed | verified records stored: 3 | last successful page: 350041
  session runtime: 0:17:05 | average per completed page: 24.6 s (41 measured, CAPTCHA waits excluded)
  estimated remaining (approximate, not guaranteed; excludes CAPTCHA waits and server slowdowns): this run 0:24:36 for 60 page(s); whole range 0:24:36 for 60 page(s)
  estimate basis: 24.6 s per page; retries, slow server responses and CAPTCHA waits can make the real time longer
```

  "verified records stored" counts all records in the database (every range). The estimate
  uses only pages completed in the current session, so it shows "unknown" until one page has
  been completed, and it leaves out the time spent waiting for a manual CAPTCHA. The first
  page of a session has no delay before it, so it is counted as at least the average configured
  delay (`--delay` + `--jitter`/2); later pages already include their delay.
- The final summary separates planned, attempted, completed, failed, interrupted and
  not-attempted pages, and reports page requests separately: retries and reloads after a
  CAPTCHA are extra requests within one page attempt, and the database counts one attempt
  per page. A page stopped by a CAPTCHA, 401/403/429 or Ctrl+C was attempted but returns to
  pending without counting an attempt.
- Exit codes: 0 success, 1 another instance holds the database lock, 2 invalid arguments,
  3 run stopped (CAPTCHA again, access restricted, persistent rate limit), 4 offline import
  refused (invalid file or no authorized source stated), 130 Ctrl+C.
  An interrupted or stopped page returns to pending and is continued with `--resume --live`.

## 2. Execution safety (verified offline)

| # | Requirement | How it is enforced | Tests |
|---|---|---|---|
| 1 | Atomic save per page | Records and the `completed` status are written in one `BEGIN IMMEDIATE` transaction (`Database.complete_page`) | `test_page_completion_is_atomic`, `test_each_page_is_saved_and_completed` |
| 2 | Completed pages skipped | `pages_to_process` never returns completed pages; claiming a completed page is refused | `test_completed_page_cannot_be_claimed_again`, `test_resume_uses_saved_range_and_skips_completed`, `test_rerunning_completed_pages_never_overwrites_records` |
| 3 | New range keeps old records | Ranges are only added with `INSERT OR IGNORE`; nothing deletes pages or records | `test_registering_ranges_never_resets_progress`, `test_switching_ranges_preserves_saved_data`, `test_page_range_change_reports_only_the_new_range` |
| 4 | Safe stop on CAPTCHA / restriction | Second CAPTCHA, CAPTCHA in headless mode, 401/403 and persistent 429 raise `StopRunError`; the page is released without counting an attempt | `test_second_captcha_stops_the_run`, `test_access_restricted_stops_the_run`, `test_access_denial_stops_without_retry_or_relaunch`, `test_second_captcha_stop_then_resume_completes_remaining_pages` |
| 5 | Pacing and retry limits | 15 s + 0–5 s jitter between navigations (minimum 5 s), at most `--retries` (0–5) retries with exponential backoff, `Retry-After` honoured up to 600 s, no overlapping navigations | `test_session_reuses_page_and_paces_between_navigations`, `test_backoff_grows_with_jitter`, `test_http_429_respects_retry_after_then_succeeds`, `test_persistent_rate_limit_stops_after_limited_retries`, `test_retry_never_overlaps_the_previous_navigation`, `test_zero_retries_means_exactly_one_navigation` |
| 6 | Interrupted runs resumable | Pages left in `processing` are returned to pending on every start; Ctrl+C releases the current page | `test_recovery_after_killed_process`, `test_crash_mid_page_is_recovered_without_repeating_completed`, `test_interrupt_releases_page_and_keeps_records`, `test_cli_ctrl_c_exits_cleanly_with_resume_hint` |
| 7 | Single writer | Exclusive `flock` on a lock file next to the database; a second instance exits with code 1 | `test_only_one_writer_at_a_time`, `test_second_writer_is_refused` |
| 8 | No secrets in logs | The scraper never reads cookies, form fields or CAPTCHA input; logged URLs keep only `id` and `page`; inspection output is redacted | `test_safe_url_keeps_only_listing_parameters`, `test_successful_navigation_logs_status_time_and_safe_url`, `tests/test_redaction.py` |
| 9 | No accidental bulk extraction | No default range, `--live` requires a range, `--max-pages` defaults to 1 | `test_live_requires_a_page_range`, `test_live_without_range_still_refused`, `test_documented_start_page_command` |
| 10 | No evasion after a limit or denial | One browser launch with only `headless`; context sets only locale and viewport; no proxy, user-agent or fingerprint code exists; since Step 17, CAPTCHA-service calls exist only in `scraper/two_captcha.py` | `test_browser_launch_has_no_proxy_or_identity_overrides`, `test_source_contains_no_proxy_rotation_or_identity_spoofing`, `test_captcha_service_calls_exist_only_in_the_solver_module`, `test_access_denial_stops_without_retry_or_relaunch` |

## 3. Performance estimates

### Measured data (all saved logs)

| Observation | Value |
|---|---|
| Page 2 listing, Step 8E (after CAPTCHA, 60 s timeout) | 24.2 s navigation (server response 23.9 s); parse + commit 0.12 s |
| Page 3 listing retry, Step 8B | about 5 s |
| CAPTCHA redirect, Step 8E | 302 → CAPTCHA page in 8.6 s |
| Manual CAPTCHA completion | about 2 minutes in Step 8E (several attempts); about 1 minute in Step 7 |
| Failed navigations | Step 8B: three 30 s timeouts and one `ERR_ABORTED`; Step 8C: one 60 s timeout |

Only 2 of the 7 listing navigations since Step 8B succeeded on the first try. Most of the failures happened before the Step 8D navigation clean-up, so the current failure rate is unknown. The sample is far too small for statistics.

### Assumptions

- One browser, pages strictly one after another, running 24/7 with no pauses.
- Configured pacing: 15 s plus 0–5 s random jitter between navigation starts (17.5 s on average). Navigations slower than the pacing interval are not paced further.
- Navigation timeout 60 s, as used in Step 8E. A first retry waits 10 s plus 0–10 s jitter (15 s on average).
- No CAPTCHA waits, no rate limiting and no blocking. None of these can be measured in advance, so all of them are excluded.
- The server is **not** assumed to sustain any of these rates for days or months.

| Scenario | Per page | Pages/hour | 1,000 pages | 100,000 pages | 671,089 pages |
|---|---|---|---|---|---|
| Best: every page loads within the pacing interval (as page 3 did) | 17.5 s | about 206 | 4.9 h | 20.3 days | 135.9 days |
| Typical: every page takes the measured 24.2 s + 0.2 s | 24.4 s | about 148 | 6.8 h | 28.2 days | 189.5 days |
| Slow: every page times out once (60 s), backs off (15 s), then loads (24.4 s) | about 100 s | about 36 | 27.9 h | 116.2 days | 779.8 days |
| Reference: a 60 requests/hour limit, as documented for the official API | 60 s | 60 | 16.7 h | 69.4 days | 466.0 days |

These durations can grow considerably:
- Every new browser session has so far needed a manual CAPTCHA. Each one takes at least a minute and needs a person at the screen.
- A CAPTCHA within a session, any 401 or 403, or a persistent 429 stops the run until someone restarts it.
- The official API allows 60 requests per hour and blocks an IP for 24 hours after repeated excess. The best and typical scenarios send 2.5 to 3.4 times that many requests. The website's own limits are unknown and could be similar or stricter.
- The page-2 response took 23.9 s on the server side, which suggests that deep pages are expensive to generate.

## 4. Unattended operation and authorized access

### Unresolved requirement

> **Update (Step 17):** at the client's request, and on their confirmation that they are
> authorized to use 2Captcha with HonestPool, the image CAPTCHA can now be solved through the
> 2Captcha API (`scraper/two_captcha.py`; see section 9A of `docs/CLIENT_GUIDE.md`), with manual
> verification as the fallback. It has only been tested offline with mocked API responses. The
> rest of this section describes the state before Step 17.
>
> **Update (Step 22):** the client's screenshots show a real CAPTCHA solved automatically through
> 2Captcha and the scraper continuing on pages 7020 and 7021. That run's database has not been
> inspected. Crash recovery was validated offline end to end (Step 21). See section 14 of
> `docs/CLIENT_GUIDE.md` for what is validated, demonstrated and not yet verified.

Fully automatic CAPTCHA handling is **not implemented and must not be implemented** by
solving or bypassing the CAPTCHA. Today:

- A visible browser and a person are needed whenever a CAPTCHA appears, which has happened at the start of most sessions.
- A CAPTCHA beyond the manual-verification limit of the session (10 since 2026-10-14, previously 1),
  a CAPTCHA shown again right after a verification, or any CAPTCHA in `--headless` mode that
  2Captcha does not solve, stops the run safely.
- How long a verified session lasts, and how many pages it allows, has not been measured.

Unattended extraction is only possible through an access method the operator authorizes.

### What is known about authorized access (from Steps 2–8, no new requests)

- **Official API** (github.com/homlol/honestpool):
  - `GET /api/v1/getRange` hands out a range to scan, and `POST /api/v1/setRange` submits a result.
  - There is no endpoint that lists verification status and no bulk export.
  - The limit is 60 requests per hour. Exceeding it more than 10 times in 7 days blocks the IP for 24 hours.
  - It cannot deliver the verified-segment list.
- **`/puzzlePoolChecked?id=71`** lists only verified segments, about 234 pages, so it is far smaller than the full 671,089-page listing. However, part of each hex value is masked by the server, and the segment number column is disabled. It is usable only with the operator's permission, for example as an unmasked export.
- **Operator contact:** Telegram @homlol_official (questions and offers), group @homlol_pool.
- No documented export, bulk endpoint or unattended access method was found.

### Draft request to the operator

> Hello, I am building a tool that collects the list of verified segments for puzzle 71
> on honestpool.ru for analysis. To avoid putting load on the site and to respect your rules,
> I would like to ask:
>
> 1. Is authorized bulk access or a data export of verified segments for puzzle 71 available
>    (for example CSV/JSON with segment number, start and end hex)?
> 2. Could you provide the verified segment numbers for puzzle 71 directly, or allow access to
>    the unmasked `puzzlePoolChecked?id=71` list?
> 3. Is there an approved mechanism for unattended access (API key, token or allow-listed
>    client) so the tool does not need to pass a CAPTCHA for each session?
> 4. What request frequency is permitted for automated reading of the listing pages?
> 5. Are there conditions of use (attribution, non-redistribution, time windows) we should
>    follow?
>
> We will not attempt to bypass the CAPTCHA or your limits. Thank you.

## 5. Production-readiness checklist

| Item | Status |
|---|---|
| Atomic per-page persistence, resume, skip completed | Ready (tested, including simulated SIGKILL during load and commit) |
| Automatic CAPTCHA solving (2Captcha) | Tested offline; shown in client screenshots; not independently verified (saving of that run's records unconfirmed) |
| Range selection, no implicit full run, `--max-pages` cap | Ready (tested) |
| Safe stop on CAPTCHA / 401 / 403 / persistent 429 | Ready (tested); live-confirmed for the second-CAPTCHA case |
| Pacing, bounded retries, no overlapping navigations | Ready (tested) |
| Single writer, no secrets in logs | Ready (tested) |
| Progress report and ETA | Ready (tested offline); not yet seen in a live run |
| CSV / XLSX export | Ready (10 records verified in Step 8F) |
| Navigation reliability on slow pages | Partly: 2 live successes, current failure rate unknown |
| Unattended operation | **Not supported**: runs need supervision; CAPTCHA frequency and site limits in long runs are unknown |
| Permitted request rate | **Unknown**: needs the operator's answer |
| Full 671,089-page run | **Not ready**: duration of 4.5 months to more than 2 years even without CAPTCHAs |
