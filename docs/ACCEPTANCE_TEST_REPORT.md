# HonestPool Scraper: client acceptance test report (Step 11)

Date: 2026-10-13. All checks in this report ran **offline**. HonestPool and its API were not
contacted, no live scraping was run, and the real SQLite database was opened read-only. Its
checksum, the checksums of both exports and the checksum of the delivery package were identical
before and after the checks.

## Summary

| Item | Result |
|---|---|
| Automated tests | **352 passed, 0 failed** (300 existing + 52 new acceptance scenarios in `tests/test_acceptance.py`) |
| Data integrity | **PASS**: `PRAGMA integrity_check` and `quick_check` return `ok` |
| Extraction progress | Pages 1, 2 and 3 completed (3 of 671,089, 0.0004 %); 10 unique verified records (Numbers 1–9 and 101) |
| Exports | CSV and XLSX identical to SQLite; hex stored as text in Excel |
| Supervised demonstration | **Ready** |
| Full unattended extraction (client requirement) | **Not met**: manual CAPTCHA and missing authorized access (see "Remaining access requirements") |

Status legend: **PASS** works as required. **PARTIAL** works with limitations. **NOT IMPLEMENTED**
not available. Evidence: **Live** was observed against the real website in Steps 7–8E. **Offline**
is proven only with saved HTML, synthetic fixtures, mocked browsers and temporary databases.

## Part A: environment and code

| Check | Result | Evidence |
|---|---|---|
| Python | PASS | Python 3.12.3 in `.venv` (project requires 3.11+) |
| Dependencies | PASS | playwright 1.63.0, beautifulsoup4 4.15.0, httpx 0.28.1, pandas 2.3.3, openpyxl 3.1.5, pytest 8.4.2. `pip check` reports no broken requirements. Chromium 1243 is installed for Playwright |
| `main.py` launches | PASS | `python main.py` prints "Environment Ready" (exit 0). `--help` exits 0 |
| Automated tests | PASS | 352 passed in about 5 s |
| Lint | PASS | ruff correctness rules (pyflakes, syntax): no findings. Standard style set (pycodestyle, bugbear, isort; line length 120): 2 advisory `zip(strict=)` notes in completed modules (`scraper/parser.py`, `tests/test_exporter.py`), no errors. IDE lint: no findings |
| Errors found | Fixed | Running `pytest` from the project root also collected the copied tests inside `delivery/` and stopped with an import-path conflict. This was introduced when the delivery folder was created in Step 10. Fixed with `pytest.ini` (`testpaths = tests`, `norecursedirs = delivery …`). The delivery package was not changed |

## Requirements table

| # | Requirement | Test performed | Status | Evidence | Remaining work |
|---|---|---|---|---|---|
| B1 | Extract only rows with the exact green "Проверено" badge | Synthetic page with 8 badge variants (wrong colour, no badge, extra text, two badges, mixed colours, whitespace). Saved page 1 compared with an independent count of green badges | PASS | **Live**: page 1 gave 9 records, page 2 gave 1 (Number 101), page 3 gave 0. **Offline**: `test_b1_b2_*`, `tests/test_parser.py` | None |
| B2 | Exclude unverified rows | Same as B1. 91 "Не проверено" rows on the saved page | PASS | **Live** + **Offline** (`test_b1_b2_saved_page_matches_an_independent_count_of_green_badges`) | None |
| B3 | Validate Number, Start hex, End hex, Status | Field checks on page 1000 fixtures and on the real data | PASS | **Offline**: `test_b3_b4_*`. **Live data**: F5 below | None |
| B4 | Hex complete and matching the segment formula | Independent formula `0x400000000000000000 + (N−1)·2^44`, including the last segment 67,108,864 | PASS | **Offline**: `test_b3_b4_*`. **Live data**: all 10 records match | None |
| B5 | Reject masked, malformed or incomplete records | 14 bad-row cases: `***`, `...`, hidden-hex span, 17 digits, non-hex, upper case, nested markup, empty, wrong range, empty/invalid Number, id mismatch, bad row id, missing cells. A page containing one masked verified row | PASS | **Offline**: `test_b5_*` (the whole page fails and nothing is saved) | None |
| B6 | No duplicate records | Duplicates within a page and across pages; the same page run 3 times; a direct duplicate INSERT is refused by the primary key | PASS | **Offline**: `test_b6_duplicates_are_never_saved`. **Live data**: 10 rows, 10 unique | None |
| C1 | Start from page 1000 | CLI `--start-page 1000 --end-page 1005 --live` with a synthetic site and a temporary database | PASS | **Offline**: `test_c1_c3_cli_processes_exactly_pages_1000_to_1005`. **Live**: explicit ranges 2..3 and 2..2 were used in Steps 8B–8E | None |
| C2 | Process pages 1000–1005 | Same. 6 pages completed, 4 verified records with the correct source pages | PASS | **Offline** | None |
| C3 | Pages outside the range are not processed | Pages 999 and 1006 never touched. `--max-pages 2` stops after 1000–1001 | PASS | **Offline**: `test_c1_*` | None |
| C4 | Select a different range | Range 2000–2002 after 1000–1005, then back to 1000–1005 (no pages refetched) | PASS | **Offline**: `test_c4_c5_*` | None |
| C5 | Previously saved records preserved | All 5 records kept across range changes | PASS | **Offline**: `test_c4_c5_*`, `test_registering_ranges_never_resets_progress` | None |
| C6 | Invalid page numbers rejected | 0, −3, end < start, 671,090, "abc", "1.5", start or end missing. Exit code 2 and no database file created | PASS | **Offline**: `test_c6_*` (8 cases) | None |
| D1–D2 | Records and completion saved after each page | Before each page, a separate read-only connection confirms that all earlier pages and their records are committed | PASS | **Offline**: `test_d1_d2_*`. **Live**: pages 1–3 saved one at a time | None |
| D3–D5 | Stop during processing, restart with `--resume`, completed pages skipped | Ctrl+C at page 1003 (exit 130), then `--resume --live`. Only pages 1003–1005 are fetched | PASS | **Offline**: `test_d3_d7_*`. **Live**: resumed runs never refetched pages 1 and 3 | None |
| D6 | Unfinished pages processed again | A failed page is retried first on resume, then the untouched pages | PASS | **Offline**: `test_d6_*`. **Live**: page 2 failed in Steps 8B and 8C and completed in Step 8E | None |
| D7 | No records lost or duplicated | Record set and uniqueness checked after each restart | PASS | **Offline**: `test_d3_d7_*`, `test_d8_*` | None |
| D8 | Unexpected termination | A page left in "processing" is recovered on start ("Recovered interrupted pages: 1"). A real process killed with SIGKILL leaves a consistent database | PASS | **Offline**: `test_d8_*`, `test_recovery_after_killed_process` (real `os.kill`) | None |
| E1 | CAPTCHA detection | CAPTCHA by URL and by content; a listing is not mistaken for a CAPTCHA; headless mode stops at once | PASS | **Live**: the CAPTCHA was detected in Steps 7, 8 and 8E. **Offline**: `test_e1_*` | None |
| E2 | Incorrect CAPTCHA submissions | Two wrong answers keep the scraper waiting; if the wait times out, nothing is saved and the page stays pending | PASS | **Live**: Step 8E, several rejected attempts, then success. **Offline**: `test_e2_*` | None |
| E3 | Valid listing detected after manual verification | Blank and partial pages are not accepted; only the complete listing is; the page is then saved | PASS | **Live**: Step 8E "Verification detected", then page 2 saved. **Offline**: `test_e2_e3_*`, 36 tests in `test_captcha_verification.py` | None |
| E4 | Second CAPTCHA stops safely | Earlier pages kept; later pages stay pending with 0 attempts | PASS | **Live**: Step 8 stopped safely on a second CAPTCHA. **Offline**: `test_e4_*` | None |
| E5 | HTTP 401, 403, 429, 5xx | 401/403 stop with no retry; 429 honours Retry-After or stops when persistent; 500/502/503/504 get bounded retries | PASS | **Offline only** (never received live): `test_e5_*`, `tests/test_browser.py` | Observe real limits only if the operator authorizes them |
| E6 | Timeouts and bounded retries | `--retries` 0, 1, 2 and 5 give exactly 1, 2, 3 and 6 navigations | PASS | **Live**: 30 s and 60 s timeouts in Steps 8B and 8C, with bounded retries. **Offline**: `test_e6_*` | None |
| E7 | Request pacing and Retry-After | Gaps of 15–20 s between pages (delay plus jitter); Retry-After 45 s honoured; above 600 s the run stops | PASS | **Live**: pacing was active in all runs. **Offline**: `test_e7_*`, `test_e5_e7_*` | None |
| E8 | No proxy rotation or bypass on restriction | 403 during a run: one browser launch, only locale and viewport set, no relaunch, stop. The source code contains no proxy, user-agent or solver code | PASS | **Offline**: `test_e8_*`, `tests/test_safety.py` | None |
| E9 | Automatic CAPTCHA solving | The scraper never fills or clicks anything on the CAPTCHA page; it only waits for a person | **NOT IMPLEMENTED** (by design: solving or bypassing the CAPTCHA is not permitted) | **Offline**: `test_e9_*` | Needs authorized unattended access from the operator |
| F1 | SQLite integrity (real database) | `integrity_check`, `quick_check` (read-only) | PASS | Both `ok` | None |
| F2 | Pages 1–3 completed | Read-only query | PASS | Page 1: completed, 1 attempt, 9 records. Page 2: completed, 3 attempts, 1 record. Page 3: completed, 1 attempt, 0 records | None |
| F3–F4 | Exactly 10 unique records; Numbers 1–9 and 101 | Read-only query | PASS | `[1, 2, 3, 4, 5, 6, 7, 8, 9, 101]` | None |
| F5 | Stored hex valid | 18 lowercase hex digits, formula, status, source page, records per page | PASS | All checks pass | None |
| F6 | CSV and XLSX match SQLite | Row-by-row comparison including headers | PASS | Both identical (10 rows) | None |
| F7 | Excel stores hex as text | Cell type and format, plus the raw sheet XML | PASS | 20 hex cells are strings with format `@`; the XML has 22 B/C cells (20 values + 2 headers), all `inlineStr` | None |
| — | CSV / Excel export | Export from SQLite | PASS | **Live data** exported in Step 8F; **Offline**: `tests/test_exporter.py` | None |
| — | Progress report with time estimate | Simulated runs | PASS | **Offline only**: `tests/test_progress.py`, `demo.py` | To be observed in the next authorized live run |
| — | Full extraction of 671,089 pages | Estimate only; not run | **PARTIAL** | The code accepts any range, but the run is estimated at 136–780 days without CAPTCHAs (`docs/PRODUCTION_READINESS.md`) | Authorized bulk access or export |
| — | Unattended operation | — | **NOT IMPLEMENTED** | A manual CAPTCHA has been needed in most browser sessions | Operator authorization (`docs/OPERATOR_REQUEST.md`) |

## Evidence by type

**Proven through live website tests (Steps 7–8E):**
- Extraction of verified rows only: pages 1, 2 and 3, giving 9 + 1 + 0 records.
- Exact badge filtering on real pages.
- Atomic per-page saving.
- Explicit page ranges.
- Failed-page retry across runs (page 2).
- Manual CAPTCHA with rejected attempts, then verification detection.
- Safe stop on a second CAPTCHA.
- Navigation timeouts with bounded retries.
- Request pacing.
- CSV and XLSX export of the live records.

**Verified only through offline tests:**
- HTTP 401, 403, 429 and 5xx handling, and Retry-After.
- Masked, malformed and incomplete row rejection.
- Duplicate protection.
- Starting from page 1000 and other far ranges.
- Range switching.
- Ctrl+C and crash recovery, including SIGKILL.
- Clean-up of stuck navigations.
- The progress report and time estimate.
- The no-proxy, no-relaunch guarantees.
- The offline demo.

**Not implemented:**
- Automatic CAPTCHA solving (deliberately).
- Unattended operation.
- Authorized bulk or export access.
- A full 671,089-page extraction.

## Known limitations

- A person must solve a CAPTCHA in a visible browser, typically once per browser session. A second CAPTCHA in the same session stops the run.
- How long a verified session lasts, and how many pages it allows, has not been measured.
- Only two live page loads have succeeded, in 5 s and 24.2 s. Earlier runs had several timeouts, so the current failure rate is unknown.
- The website's request limits are not published. The official API allows 60 requests per hour and blocks an IP for 24 hours after repeated excess.
- Duration estimates for all 671,089 pages, with no CAPTCHA waits included: 136 days at best, about 190 days typical and about 780 days on slow pages.
- Only about 1 in 2,900 segments is verified, so almost all fetched pages contain no records.

## Remaining access requirements

Draft request: `docs/OPERATOR_REQUEST.md`, in Russian and English, not sent. It asks the operator for:
1. An authorized export of the verified segment numbers for puzzle 71.
2. A documented endpoint that returns verified segment identifiers.
3. Permission and supported authentication for unattended collection.
4. The permitted request frequency and limits.
5. Any usage conditions.

The segment numbers alone are enough, because the full hex values are calculated from them.

## Conclusion

- **Ready for a supervised demonstration: YES.** All implemented features pass, the real data is
  intact and consistent, and the offline demo (`python demo.py`) runs without network access.
- **Meets the client's full unattended-extraction requirement: NO.** Unattended operation and
  full-scale extraction depend on authorized access from the site operator. Automatic CAPTCHA
  solving is not implemented and will not be implemented as a bypass.
