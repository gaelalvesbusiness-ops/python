# Release notes

## Windows 11 test package v3, diagnostic version (2026-10-14, not yet run on Windows)

Packaged as `honestpool-windows11-test-v3.zip` for a short, supervised diagnostic test. It is not
an unattended solution. The client reported wrong 2Captcha answers, but the only available log
(Windows v1) shows that no image was ever sent to 2Captcha: all four automatic attempts failed
before image capture. These changes make the next log show exactly where each attempt ends.
Attempt limits, pacing, safe stops and extraction rules are unchanged. The v1 and v2 packages are
kept unchanged.

- v3 is installed into a new folder (`C:\HonestPool-v3`). `START_HERE_WINDOWS.txt`,
  `INSTALACAO_WINDOWS_11.md` and the checklist tell the client to keep the current installation
  (`C:\HonestPool` or `C:\HonestPool-v2`) and its database untouched, to back up `data` and
  `config`, to run the diagnostic test on v3's empty database, and not to copy the old database
  during the test. Section 10 of the guide (manual database copy) is marked as a step for after
  the diagnosis; nothing is copied, deleted or overwritten automatically.
- New section 11 of the guide and checklist part H explain where the sanitized log is
  (`logs\honestpool_AAAAMMDD.log`), what it contains and how to send it unedited.
- `setup_windows.bat` suggests `C:\HonestPool-v3` when the folder has to be moved.
- Every automatic attempt is logged as `CAPTCHA N attempt M/3: ...`: image not found, image
  captured (size and a short hash of the image), image not sent (failed local checks), no answer
  from 2Captcha, 2Captcha account problem, answer received (task id, answer length, solve time),
  answer submitted to the website, website acceptance confirmed, website rejection confirmed,
  verification outcome uncertain. The 2Captcha client logs `task N submitted`. A manual fallback
  is logged as `CAPTCHA N: manual fallback (...)`. Answers and the API key are never logged.
- A rejection is only confirmed, and reported to 2Captcha as incorrect, when the site shows a
  CAPTCHA with a different image than the one answered. A changed page that still shows the same
  image, or an image that cannot be compared, is an uncertain outcome: it is never reported to
  2Captcha and leads to manual verification (or a safe stop in headless mode), as before.
- Each run prints a `CAPTCHA summary:` line (CAPTCHAs shown, automatic verifications, images sent
  to 2Captcha, rejections, uncertain outcomes, 2Captcha errors, images not found, manual
  fallbacks and verifications); an interrupted or aborted run logs it.
- The documentation said a run sends at most 9 paid images. That was wrong, because CAPTCHAs that
  fail automatically do not count toward the 3 automatic verifications. The real bound with the
  default settings is 42 images per run (3 × (3 + 10 + 1)). The program now enforces it as a hard
  limit, so an edge case (an answer accepted late) cannot exceed it.
- Tests (offline, Ubuntu): 765 passed, 9 Windows-only skipped, including 22 new tests in
  `tests/test_captcha_attempt_logging.py` and two for the v3 instructions.

## Windows 11 test package v2 (2026-10-14, not yet run on Windows)

Packaged as `honestpool-windows11-test-v2.zip` to address the client's two complaints after the
v1 test: automatic CAPTCHA solving did not work, and extracted hexadecimal keys were neither
shown on screen nor saved automatically. The v1 package is kept unchanged. The changes are
listed in the three sections below; none of them has been run on Windows or against the live
site yet.

- v2 is installed into a new folder (`C:\HonestPool-v2`), never over v1. `START_HERE_WINDOWS.txt`,
  `INSTALACAO_WINDOWS_11.md` and the checklist explain closing v1, backing up `data` and
  `config`, configuring the 2Captcha key again and a supervised two-page test.
- A v2 installation starts with an empty database. Section 10 of the installation guide
  describes an optional, manual copy of the v1 `honestpool.db` (with its `-wal`/`-shm` files);
  nothing is migrated, overwritten or deleted automatically. The database schema is unchanged,
  so a v1 database opens in v2 without conversion.
- `setup_windows.bat` suggests `C:\HonestPool-v2` instead of `C:\HonestPool` when the folder has
  to be moved.
- A manual export onto a file that is open in Excel now prints a short error instead of a
  Python traceback.
- Tests (offline, Ubuntu): 741 passed, 9 Windows-only skipped, including two new tests for the
  v2 update instructions and the export error.

## CAPTCHA handling after the Windows run of 2026-10-14

A supervised run of pages 1..50 on Windows stopped at page 27: the site showed a new CAPTCHA 19
pages after the one completed manually on page 6, and only one manual verification was allowed
per browser session. The CAPTCHA image was never captured for 2Captcha, although the operator
saw it. Extraction rules, pacing and stored records are unchanged.

- Each new CAPTCHA may be completed manually, up to 10 per browser session
  (`--max-manual-verifications`, 1..50). A CAPTCHA shown again right after a verification, a
  CAPTCHA in `--headless` mode that 2Captcha does not solve, and a manual wait longer than
  `--captcha-wait` still stop the run. The prompt shows the count ("manual verification 2 of 10").
- The stop reason now says what happened ("a new CAPTCHA appeared and the limit of 1 manual
  verification(s) for this browser session is reached" instead of "CAPTCHA shown again"), and
  every stop prints the saved progress and the resume command (`Progress saved: 26 of 50 pages
  in range 1..50 completed, 24 pending; page 27 returned to pending ... Continue with: python
  main.py --resume --live`).
- The CAPTCHA image was only looked for inside the CAPTCHA `<form>`. It is now looked for from the
  CAPTCHA input outwards (its form, then the enclosing card), skipping images in buttons or links
  and small icons; still only that image element is captured. The real page's markup has not
  been inspected, so this is the most likely cause, not a confirmed one. When no image is found,
  the log now says whether it was missing or did not load and describes the page (image
  addresses without query strings, sizes, canvas count; no tokens or field values).
- Tests (offline): the Windows run reproduced with fakes (stop, saved progress, resume without
  repeating pages, second manual verification), and image capture with a real headless Chromium
  on local synthetic layouts with every request aborted.

### Records on screen and automatic CSV (client report: keys not shown or saved automatically)

Until now verified records were saved only in SQLite (atomically with each page's completed
status); the terminal showed only "page N: completed, X verified, Y new" in the log line, and the
CSV/XLSX files were written only by `--export` (menu options 3 and 4).

- After every completed page the newly stored records are printed as a table (`Number`,
  `Start hex`, `End hex`, `Status`), or `Page N: no new verified records.`
- `verified_records.csv` next to the database (`data\verified_records.csv` from the Windows menu)
  is rewritten from SQLite after every completed page: temporary file in the same folder, flushed
  to disk (`fsync`, now also for `--export csv`), then renamed over the previous file. It holds
  every stored verified record exactly once, as `--export csv` would. SQLite stays the
  authoritative copy: the CSV is written only after the page is committed, and a CSV that cannot
  be written (e.g. open in Excel) prints a warning without failing the page. Each live run first
  removes temporary files left by a killed process (older than 60 s) and re-synchronizes the CSV.
- The XLSX file is still generated only on demand (`--export xlsx`, menu option 4). The end of
  every live run and the Windows menu header show the full paths of the database, the automatic
  CSV, the on-demand XLSX and the session log.

### CAPTCHA verification robustness

- The page snapshot used to recognise a rejected answer was taken before the pacing wait; a page
  change during that wait could make a pending answer look rejected (reported to 2Captcha as
  incorrect and retried with a new image). It is now taken right before the answer is typed.
- When automatic solving failed but the browser already showed the listing (an answer accepted
  late, or the operator completed the CAPTCHA meanwhile), the run waited for a manual
  verification that had already happened (600 s, then a stop) or stopped in `--headless`
  mode. That state is now recognised and the requested page is loaded again.
- The log records the size of each captured CAPTCHA image and the attempt number.
- Tests: 19 new offline tests (records table, only new records reported, CSV after each page,
  only verified records without duplicates and identical to `--export csv`, Ctrl+C during the
  CSV write, hard kill of the real CLI process during the CSV write, leftover temporary files,
  CSV locked, both CAPTCHA cases, the Windows menu paths). Linux: 739 passed, 9 skipped.
  Automatic CAPTCHA solving has still not been verified against the real site.

## Windows 11 test package v1 (2026-10-13, not yet run on Windows)

Packaged as `honestpool-windows11-test-v1.zip` for a first supervised installation test on
Windows 11; versions 1, 2 and 3 are kept unchanged. Scraper behaviour, pacing and limits are
unchanged. None of this has been run on Windows yet.

### Test package

- Files sit directly at the ZIP root, so extracting to `C:\HonestPool` gives
  `C:\HonestPool\setup_windows.bat`. `START_HERE_WINDOWS.txt` lists the six steps in Portuguese
  and states that Python 3.11 or 3.12 and internet access (installation and extraction) are
  required.
- `MANIFEST.sha256` inside the ZIP lists the SHA-256 of every other file.
- Contains the source code, offline tests, documentation and synthetic examples only: no `.env`,
  `config/`, database, exports with real records, logs, network traces, browser data, virtual
  environment or API key. The installer creates `data/`, `logs/` and `config/` locally.

### New

- `setup_windows.bat`, `configurar_2captcha.bat` and `iniciar_honestpool.bat` with
  `windows_launcher.py`: installation, protected 2Captcha key storage (Windows DPAPI, per user
  and computer, never displayed) and a menu for supervised extraction, resume, CSV/Excel export
  and progress. No `.env` is needed on Windows.
- `INSTALACAO_WINDOWS_11.md` (six-step workflow) and `CHECKLIST_ACEITACAO_WINDOWS_11.md`
  (Portuguese acceptance checklist for installation, CAPTCHA settings, progress saving, resume
  and exports).
- The database lock uses `msvcrt` on Windows (`fcntl` elsewhere).

### Windows preparation

- The installer writes a completion marker only after every check passed; the launchers refuse
  to run after an incomplete installation. Paths with spaces and parentheses are supported, and
  long project paths, Temp (ZIP not extracted) and OneDrive folders are warned about.
- The launcher keeps relaying output after Ctrl+C until `main.py` has saved and exited, ends the
  child process if the launcher itself fails, continues without a log file if it cannot be
  written, and suggests closing Excel when an export fails.
- Tests: the hard-kill recovery tests use `TerminateProcess` on Windows instead of `SIGKILL`,
  child processes run in UTF-8, and Windows-only tests (msvcrt, real DPAPI, `cmd.exe` runs of
  the `.bat` files) are skipped elsewhere.
- `docs/CLIENT_ACCEPTANCE_TEST.md` is now the Linux / command-line variant; the untested
  PowerShell `.env` instructions were removed (`Read-Host -MaskInput` does not exist in the
  Windows PowerShell 5.1 shipped with Windows 11).

### API key isolation and process safety

- `main.py` creates the 2Captcha client and then removes `CAPTCHA_API_KEY` from its environment,
  before the Playwright driver is started (the driver copies the environment); Chromium is
  launched with an explicit environment without any `CAPTCHA_*` variable. Before this change
  the driver and Chromium inherited the key.
- The Windows launcher passes the 2Captcha settings only to live runs and `--captcha-status`.
- Ctrl+C is handled by `main.py` alone (`handle_sigint=False`): the interrupted page is returned
  to pending first, then the browser is closed, instead of the driver closing Chromium at the
  same moment.
- Windows: the launcher runs in a kill-on-close job object, so every process it starts (main.py,
  driver, Chromium) is ended when the launcher ends; processes still running 10 s after main.py
  has finished are ended and reported.
- Tests: credential isolation (fakes plus a real headless Chromium on a local page, Linux),
  Ctrl+C and hard kill without leftover processes, job-object logic with a simulated kernel32.
  Linux: 709 passed, 9 skipped (Windows-only).

Automatic CAPTCHA solving and long unattended operation are still not guaranteed.

## Version 3 (2026-10-13)

Replaces version 2 (`honestpool-scraper-v2-20261013.zip`); versions 1 and 2 are kept unchanged.

### New

- **Automatic image-CAPTCHA solving through 2Captcha** (`scraper/two_captcha.py`,
  `scraper/captcha_provider.py`), enabled with `CAPTCHA_PROVIDER=2captcha` and
  `CAPTCHA_API_KEY`. Only the CAPTCHA image element is sent; the answer is submitted with the
  same pacing as page navigations; at most 3 attempts per CAPTCHA and 3 automatic verifications
  per browser session; account errors (balance, key, IP) turn automatic solving off for the run.
  Manual verification in the browser window remains the fallback; `--headless` stops safely.
  The CAPTCHA image is captured only once it has loaded, and an answer is accepted as soon as
  the site redirects to the puzzle listing; the requested page is then reloaded and validated.
- `--captcha-status` checks the settings without contacting any service; the key is never shown.
- `.env.example` template (placeholders only) and instructions for loading a local `.env` into
  the terminal (`docs/CLIENT_GUIDE.md`, section 9A).
- End-to-end offline test of 10 pages through the real CLI, runner and SQLite layer: interruption
  on page 6 (SIGKILL while loading, SIGKILL inside the page's transaction, Ctrl+C), resume,
  recovery, a CAPTCHA solved through the mocked 2Captcha API, and CSV/XLSX export checks.
- Documentation: failed pages (section 5A), the three-attempt limits and recovery limitations
  (section 9B), pacing and API costs, and what has been validated, demonstrated or not yet
  verified (section 14).

### Fixed

- Client guide: removed references to non-existent `--plan`/`--status` options.

### Validation status

- Offline: 567 automated tests pass, without network access.
- Client screenshots show a real CAPTCHA solved automatically through 2Captcha and the scraper
  continuing on pages 7020 and 7021. That run's database has not been inspected, so saving of
  those records is not confirmed.
- Not yet independently verified live: record saving during 2Captcha runs, the page shown after
  a rejected answer, the 2Captcha success rate and cost, CAPTCHA frequency in long runs, the
  site's rate limits, recovery from a real crash, live `--headless` operation.

### Unchanged limitations

- Unattended operation is not supported. In the database we inspected, only pages 1–3 of
  671,089 have been extracted; the full extraction has not been done.
- The official API does not list verified segments; Workflow B needs a file from an authorized
  source, which has not been obtained yet.

### Package contents

Source code, the offline test suite and its synthetic fixtures, documentation, configuration
examples, `.env.example`, the example exports of the 10 extracted records and the synthetic
import files. Not included: `.env` or any API key, SQLite databases, virtual environments,
browser profiles, cookies or session data, NetLogs, inspection logs, screenshots and temporary
files.

## Version 2 (2026-10-13)

Replaces the Step 10 demonstration package (`honestpool-scraper-demo-20261013.zip`), which is
kept unchanged.

### New

- **Workflow B — offline import** of verified segment numbers from an authorized TXT or CSV
  file (`--import-verified FILE --source "..."`). Hex ranges are calculated locally; a file
  with any invalid value is rejected as a whole; duplicates are removed; imports are recorded
  in separate tables with their source, file name and SHA-256; website records and page
  progress are never changed. See `docs/CLIENT_GUIDE.md`, section 7A.
- `--export-source all|website|imported` for CSV/XLSX exports (default `all`).
- `demo_import.py`: offline end-to-end demonstration with synthetic data on a temporary
  database (`examples/import/`). Its output is in `docs/import_demonstration_output.txt`.
- Exit code 4: import refused.

### Fixed

- The end-of-run summary counts only pages actually fetched ("attempted X of Y planned pages"),
  and reports pages stopped before completion, pages not attempted and page requests separately.
- The remaining-time estimate includes the configured `--delay`/`--jitter` for the first page
  of a session, is labelled "approximate, not guaranteed" and explains its basis.

### Unchanged limitations

- Workflow A (website extraction) still requires manual CAPTCHA verification; nothing solves or
  bypasses it, and Workflow B does not change that.
- Unattended operation is not supported. Only pages 1–3 of 671,089 have been extracted.
- The official API does not list verified segments; Workflow B needs a file from an authorized
  source, which has not been obtained yet.

### Package contents

Source code, the offline test suite and its synthetic fixtures, documentation, configuration
examples, the example exports of the 10 extracted records and the synthetic import files.
Not included: the SQLite database, virtual environments, browser profiles, cookies or session
data, NetLogs, inspection logs, screenshots and temporary databases.
