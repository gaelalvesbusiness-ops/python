# HonestPool Scraper: offline demonstration report (Step 12)

Date: 2026-10-13. Everything ran **offline**:
- HonestPool and its API were not contacted.
- No CAPTCHA was answered or bypassed.
- All simulations used temporary databases under `/tmp`.
- The real database, both real exports and the Step 10 delivery package have the same checksums before and after.

## How the demonstration works

```bash
PYTHONUNBUFFERED=1 .venv/bin/python demo_walkthrough.py > docs/demonstration_output.txt 2>&1   # about 70 s
```

- **`demo_walkthrough.py`** runs the real `main.py` commands (`cli.main(argv)`) against a new temporary database.
  - Only the browser is replaced: the Playwright stand-in from the test suite serves **synthetic listing pages**.
  - Everything else is the production code: the fetcher, the 5 s delay between page loads, the parser, SQLite saving and the progress report.
- **Synthetic verified segments:**
  - pages 1000–1005: 99901, 99950 | – | 100150 | 100201, 100300 | – | 100401
  - pages 2000–2003: 199999 | 200050 | – | 200201
- **Ctrl+C** is simulated by raising `KeyboardInterrupt` inside the mocked browser while page 1003 loads.
- **CAPTCHA scenarios** use mocked pages and a virtual clock. A "person" is simulated by changing the mocked page. No CAPTCHA logic is involved.
- **Full output:** `docs/demonstration_output.txt` (459 lines). Below, temporary paths are shortened to `<tmp>`.

## Results

| # | Feature demonstrated | Result |
|---|---|---|
| 1 | Temporary demonstration database | PASS |
| 2 | Start at page 1000, end at 1005; nothing outside the range processed | PASS |
| 3 | Progress saved after each completed page | PASS |
| 4 | Interruption, then `--resume`: completed pages skipped, no duplicates, no lost records | PASS |
| 5 | Different range selected; earlier data intact | PASS |
| 5 | Invalid listing marked failed, then retried first on resume | PASS |
| 6 | CSV and XLSX export: four columns, complete hex, hex stored as text in XLSX | PASS |
| 7 | Progress report: current page, completed/pending/failed pages, records, approximate remaining time | PASS (see limitation 2) |
| 8 | CAPTCHA detection | PASS |
| 8 | Incorrect CAPTCHA answers keep the scraper waiting | PASS |
| 8 | Valid listing detected after manual verification | PASS |
| 8 | Second CAPTCHA in a session: safe stop | PASS |
| 8 | HTTP 401 / 403: safe stop, no retry | PASS |
| 8 | HTTP 429 with Retry-After; persistent 429; Retry-After above the 600 s cap | PASS |
| 8 | HTTP 503 and timeouts: bounded retries with back-off | PASS |
| 8 | No proxy rotation or browser relaunch after a denial | PASS |
| 8 | Automatic CAPTCHA solving | **NOT IMPLEMENTED** (by design) |
| 9 | Full automated test suite | PASS: **352 passed** |
| — | Real database and delivery package untouched | PASS |

Walkthrough summary: 26 checks, 25 PASS, 1 NOT IMPLEMENTED, 0 FAIL.

## 2–3. Start at page 1000; progress saved after each page

```
$ python main.py --db <tmp>/demo.db --start-page 1000 --end-page 1005
Run range: pages 1,000..1,005 (6 pages)
Pages to process: 6 (next: [1000, 1001, 1002, 1003, 1004, 1005])
Planning only: add --live to fetch pages; no pages were fetched.

$ python main.py --db <tmp>/demo.db --start-page 1000 --end-page 1005 --live --max-pages 6 --delay 5 --jitter 0
14:13:45 INFO scraper.browser: page 1000: HTTP 200 in 0.0s at https://honestpool.ru/puzzle?id=71&page=1000; ... listing: complete (100 rows)
14:13:45 INFO scraper.runner: page 1000: completed, 2 verified, 2 new
  [saved after page 1000] 1000:completed(1x,2 rec)  1001:pending  1002:pending  1003:pending  1004:pending  1005:pending | verified records: 2
  [saved after page 1001] 1000:completed(1x,2 rec)  1001:completed(1x,0 rec)  1002:pending  ...                    | verified records: 2
  [saved after page 1002] 1000:completed  1001:completed  1002:completed(1x,1 rec)  1003:pending  ...              | verified records: 3
14:14:00 INFO scraper.browser: page 1003: navigation 4 started (timeout 30s)      <- Ctrl+C during this load
Interrupted: the current page was returned to pending and saved records are kept. Continue with --resume --live.
(exit code 130)
```

- Each `[saved after page N]` line is read from the database through a separate read-only connection
  right after the page finishes. This shows that every page is committed before the next one starts.
- Page loads started 5 s apart (14:13:45, :50, :55, 14:14:00), as configured.
- Pages requested by the mocked browser: 1000, 1001, 1002, 1003, then 1003, 1004, 1005 on resume.
  No page outside 1000–1005 was requested.

## 4. Interruption and recovery

```
  [after interruption] 1000:completed(1x,2 rec)  1001:completed(1x,0 rec)  1002:completed(1x,1 rec)  1003:pending(0x)  1004:pending(0x)  1005:pending(0x) | verified records: 3

$ python main.py --db <tmp>/demo.db --resume --live --max-pages 6 --delay 5 --jitter 0
Run range: pages 1,000..1,005 (6 pages)
Progress in range: {'pending': 3, 'processing': 0, 'completed': 3, 'failed': 0}
Pages to process: 3 (next: [1003, 1004, 1005])
...
Live run: 3 completed, 0 failed, 3 new verified records (attempted 3 of 3 unfinished pages)
(exit code 0)
  [after resume] 1000..1005 all completed | verified records: 6
```

- Page 1003 returned to `pending` with **0 attempts** counted; an interruption is not a failure.
- `--resume` requested only pages 1003, 1004 and 1005.
- Final records: `[99901, 99950, 100150, 100201, 100300, 100401]`. That is 6 unique records, exactly
  the synthetic verified segments, with no duplicates and none lost.

## 5. Changing the starting page

```
$ python main.py --db <tmp>/demo.db --start-page 2000 --end-page 2003 --live --max-pages 4 --delay 5 --jitter 0
page 2001: HTTP 200 ... listing: partial (40 of 100 rows)
WARNING scraper.runner: page 2001 failed: page 2001: incomplete or invalid listing: 40 listing rows, expected 100
Live run: 3 completed, 1 failed, 2 new verified records (attempted 4 of 4 unfinished pages)
  [range 1000-1005] all 6 completed | verified records: 8
  [range 2000-2003] 2000:completed(1x,1 rec)  2001:failed(1x)  2002:completed(1x,0 rec)  2003:completed(1x,1 rec) | verified records: 8
```

- The new range was processed. Pages 1000–1005 and their 6 records were unaffected.
- The deliberately incomplete page 2001 was **rejected and marked failed**; nothing from it was saved.

## 7. Progress reporting

The progress report is printed by `main.py` before each page and at the end of a run.

Resuming the range with the failed page:

```
$ python main.py --db <tmp>/demo.db --resume --live --max-pages 1 --delay 5 --jitter 0
Pages to process: 1 (next: [2001])
Progress | current page: 2,001 | range 2,000..2,003 (4 pages)
  pages: 3 completed, 0 pending, 1 failed | verified records stored: 8 | last successful page: 2,003
  ...
Progress | current page: none | range 2,000..2,003 (4 pages)
  pages: 4 completed, 0 pending, 0 failed | verified records stored: 9 | last successful page: 2,001
```

Extending the range to 1000–1100 and fetching 3 more pages, so the estimate uses the measured pace:

```
$ python main.py --db <tmp>/demo.db --start-page 1000 --end-page 1100 --live --max-pages 3 --delay 5 --jitter 0
Progress | current page: 1,008 | range 1,000..1,100 (101 pages)
  pages: 8 completed, 93 pending, 0 failed | verified records stored: 9 | last successful page: 1,007
  session runtime: 0:00:05 | average per completed page: 2.5 s (2 measured, CAPTCHA waits excluded)
  estimated remaining (approximate, excludes CAPTCHA waits and server slowdowns): this run 0:00:03 for 1 page(s); whole range 0:03:55 for 93 page(s)
Progress | current page: none | range 1,000..1,100 (101 pages)
  pages: 9 completed, 92 pending, 0 failed | verified records stored: 9 | last successful page: 1,008
  session runtime: 0:00:10 | average per completed page: 3.3 s (3 measured, CAPTCHA waits excluded)
  estimated remaining (approximate, excludes CAPTCHA waits and server slowdowns): this run 0:00:00 for 0 page(s); whole range 0:05:08 for 92 page(s)
```

The report shows all six required fields: current page, completed, pending and failed pages,
verified records stored, and the approximate remaining time.

## 6. CSV and XLSX export

```
$ python main.py --db <tmp>/demo.db --export csv --output <tmp>/verified_records.csv
Exported 9 verified records to <tmp>/verified_records.csv
$ python main.py --db <tmp>/demo.db --export xlsx --output <tmp>/verified_records.xlsx
Exported 9 verified records to <tmp>/verified_records.xlsx

Number,Start hex,End hex,Status
99901,401863c00000000000,401863d00000000000,Проверено
99950,401866d00000000000,401866e00000000000,Проверено
100150,401873500000000000,401873600000000000,Проверено
100201,401876800000000000,401876900000000000,Проверено
100300,40187cb00000000000,40187cc00000000000,Проверено
100401,401883000000000000,401883100000000000,Проверено
199999,4030d3e00000000000,4030d3f00000000000,Проверено
200050,4030d7100000000000,4030d7200000000000,Проверено
200201,4030e0800000000000,4030e0900000000000,Проверено

verified_records.xlsx: 9 rows; hex cell types ['s'], number formats ['@']
```

- Both files have the four required columns: Number, Start hex, End hex, Status.
- All hex values are 18 digits and equal the values computed from each segment number.
- The XLSX file stores the hex values as string cells with the Text format `@`, so Excel cannot turn
  `401863c00000000000` into a number. Its rows are identical to the CSV.

## 8. CAPTCHA and error handling (mocked browser)

```
-- 8a. headless:     CaptchaRequiredError: CAPTCHA shown in headless mode; manual verification is impossible
-- 8b. visible:      [scraper] CAPTCHA shown. Please complete it manually in the browser window (waiting up to 600s). It is never solved automatically.
                     [scraper] CAPTCHA shown again (the answer may have been rejected); still waiting ... (598s left).
                     [scraper] CAPTCHA shown again (the answer may have been rejected); still waiting ... (596s left).
                     [scraper] Verification detected: the browser reached the listing at https://honestpool.ru/puzzle?id=71; continuing with the same browser session.
                     page 2: HTTP 200 ... listing: complete (100 rows)
-- 8c. second CAPTCHA: run stopped: page 3: CAPTCHA shown again; manual verifications for this session are used up
                     [database status] 2:completed(1x,1 rec)  3:pending(0x)  4:pending(0x)
-- 8d. HTTP 403 (CLI, full pipeline):
   $ python main.py --db <tmp>/errors.db --start-page 3000 --end-page 3002 --live --max-pages 3 --delay 5 --jitter 0
   Run stopped: page 3001: HTTP 403 for https://honestpool.ru/puzzle?id=71&page=3001; access is restricted
   (exit code 3)    browser launches: [{'headless': False}]   context options: ['locale', 'viewport']
-- 8e. HTTP 401:     AccessRestrictedError: HTTP 401 ...; access is restricted (1 navigation)
-- 8f. HTTP 429:     "retry 1/2 in 45s" (Retry-After honoured), then HTTP 200
                     RateLimitedError: HTTP 429 persisted after 2 retries (3 navigations)
                     RateLimitedError: server asked to wait 3600s (refused, above the 600 s cap)
-- 8g. HTTP 503:     "retry 1/2 in 18s", then HTTP 200
       timeouts:     TransientFetchError: ... (gave up after 2 retries); back-off waits [18.4, 24.2] s;
                     "unfinished navigation stopped; tab reset to about:blank" after each timeout
-- 8h. Automatic CAPTCHA solving: NOT IMPLEMENTED
```

- After the 403, page 3002 was never requested.
- There was exactly one browser launch, with no proxy and no identity changes.
- Pages 3001 and 3002 stayed pending, with no attempt counted.

## Observations found during the demonstration

These are findings about the existing code. The scraper was not changed in this step.

1. **Run summary wording after a stop.** After the 403 stop, `main.py` printed "attempted 3 of 3 unfinished
   pages", although only 2 pages were requested. It prints the number of pages selected for the run,
   not the number actually attempted. The data is correct; only the wording is wrong. Small fix
   recommended.
2. **The early remaining-time estimate is optimistic.** The first page of each session has no delay
   before it, so it is measured as about 0 s. After 3 pages the average was 3.3 s against a real pace
   of 5 s, so the estimate was about a third too short. The error shrinks as more pages are measured,
   and with real page loads of about 24 s the effect is small. The report already labels the estimate
   as approximate. An improvement (excluding the first page, or using the time between page starts)
   can be made later.

## Remaining limitations

- The demonstration used **synthetic pages and a mocked browser**. Live behaviour has only been
  proven on pages 1–3 (Steps 7–8E).
- **Automatic CAPTCHA solving is not implemented.** In a real run, a person must solve the CAPTCHA in
  the visible browser, usually once per session. A second CAPTCHA stops the run.
- **Unattended operation does not work** and is not claimed.
- **Full extraction of 671,089 pages was not started.** It is estimated at 136 days or more even without
  CAPTCHAs, and needs authorized access from the site operator (`docs/OPERATOR_REQUEST.md`).
- HTTP 401, 403, 429 and 5xx handling has only been seen with mocked responses; the real site's limits are unknown.

## Files

- `demo_walkthrough.py`: new, the offline walkthrough script. It writes only to `/tmp`.
- `docs/demonstration_output.txt`: new, the complete terminal output of the final run.
- `docs/DEMONSTRATION_REPORT.md`: this report.
- Temporary files from the final run (`demo.db`, `errors.db`, both exports) remain in
  `/tmp/honestpool-demo-f7svww0n/` for inspection.
