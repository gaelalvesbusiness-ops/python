# HonestPool Scraper

Professional Python scraper for [honestpool.ru](https://honestpool.ru/puzzle?id=71) puzzle data extraction.

## Requirements

- Python 3.11 or 3.12 (the Windows installer accepts only these two versions)
- Windows 11, Linux, or macOS

## Setup

### Windows 11 (client installation, recommended)

Follow `START_HERE_WINDOWS.txt` (short version) or `INSTALACAO_WINDOWS_11.md` (Portuguese).
Python 3.11 or 3.12 and internet access (during installation and extraction) are required. After
extracting the ZIP to a new folder (`C:\HonestPool-v3` for the v3 diagnostic test package, never
over an earlier installation, whose database stays untouched), the client only double-clicks three
files:

1. `setup_windows.bat` checks for Python 3.11/3.12, creates `.venv`, installs `requirements.txt`
   and Playwright's Chromium, and creates `data`, `logs` and `config`;
2. `configurar_2captcha.bat` stores the 2Captcha key, read with hidden input and encrypted with
   Windows DPAPI in `config\2captcha.cfg` (no `.env` file on Windows);
3. `iniciar_honestpool.bat` opens a Portuguese menu (supervised extraction, resume, CSV/Excel
   export, progress) that runs `main.py` with its default pacing and limits and writes
   `logs\honestpool_YYYYMMDD.log`.

`CHECKLIST_ACEITACAO_WINDOWS_11.md` is the acceptance checklist for the Windows installation.
A v2 installation starts with an empty database; `INSTALACAO_WINDOWS_11.md`, section 10,
describes the optional manual copy of a v1 `honestpool.db` (nothing is migrated automatically). The scripts were checked on Ubuntu only (static checks and offline tests); they
have not been run on Windows yet.

### Windows (PowerShell, manual, for developers)

```powershell
cd honestpool-scraper
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
python main.py
```

### Windows (Command Prompt, manual, for developers)

```cmd
cd honestpool-scraper
python -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
python main.py
```

### Linux / macOS

```bash
cd honestpool-scraper
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
python main.py
```

On Debian/Ubuntu without the `python3-venv` package, either `sudo apt install python3-venv`
or use [uv](https://docs.astral.sh/uv/): `uv venv .venv && uv pip install --python .venv pip -r requirements.txt`.
If Chromium fails to launch on Linux, run `sudo .venv/bin/python -m playwright install-deps chromium`.

If `playwright install chromium` is slow or fails (CDN timeouts), Playwright can drive an
installed Google Chrome instead — useful on both Windows and Linux:

```python
browser = playwright.chromium.launch(channel="chrome", headless=True)
```

Run the tests with (all of them run offline):

```bash
python -m pytest
```

On Linux: 765 passed and 9 skipped (the skipped ones need `cmd.exe`, `msvcrt`, the Windows
DPAPI or a Windows job object). On Windows the same suite runs those 9 and skips 5 Linux-only
checks (two of them start a real headless Chromium on a local page and inspect `/proc`); the
crash-recovery tests use `TerminateProcess` there instead of SIGKILL. The suite has not been run
on Windows yet.

### 2Captcha API key (optional)

**Windows 11:** run `configurar_2captcha.bat` (see `INSTALACAO_WINDOWS_11.md`, section 5). The key
is typed with hidden input, encrypted with Windows DPAPI for the current Windows user in
`config\2captcha.cfg`, passed to `main.py` only through its environment by
`iniciar_honestpool.bat`, and redacted from the screen and the log. No `.env` file is used.

**Linux / macOS (or manual runs):** the program reads `CAPTCHA_PROVIDER`, `CAPTCHA_API_KEY` and
`CAPTCHA_TIMEOUT` from the environment only; it never opens a `.env` file by itself and never
prints the key.

1. Copy `.env.example` to `.env` (gitignored) and paste the key after `CAPTCHA_API_KEY=`, with no
   quotes or spaces. On Linux, `chmod 600 .env`.
2. Load it into the terminal before each session:

```bash
set -a; . ./.env; set +a                     # Linux / macOS
python main.py --captcha-status              # must show "API key: set (hidden)"
```

Alternatively, type the key into the terminal without a file (`read -rsp` on Linux; see
`docs/CLIENT_GUIDE.md`, section 9A). Never commit `.env` or `config/`, send them by message or
put them in a delivery package. Without a key, CAPTCHAs are completed manually.

**Who receives the key:** only the Python 2Captcha client inside `main.py`. As soon as that
client has been created, `main.py` removes `CAPTCHA_API_KEY` from its environment, before the
Playwright driver (Node.js) is started; Chromium is launched with an explicit copy of the
environment without any `CAPTCHA_*` variable. On Windows the launcher passes the key only to live
runs and the status check, not to exports or the progress display.

## Usage

```bash
python main.py --start-page 350000 --end-page 350100          # plan and save the range, fetch nothing
python main.py --start-page 350000 --end-page 350100 --live   # fetch 1 page of that range
python main.py --resume --live                                # continue the saved range (1 page)
python main.py --resume --live --max-pages 50 --nav-timeout 60
python main.py --export xlsx                                  # data/verified_records.xlsx
python main.py --export csv                                   # data/verified_records.csv
python main.py --import-verified verified_numbers.txt         # optional offline import (see below)
```

- There is no default range; every range must be selected explicitly with `--start-page` and
  `--end-page`. A new range replaces the saved run range, but records and completed pages of
  earlier ranges are kept, and completed pages are never fetched again.
- `--live` fetches at most `--max-pages` pages per invocation (default 1) in a visible Chromium
  window. With `CAPTCHA_PROVIDER=2captcha` and `CAPTCHA_API_KEY` set, the image CAPTCHA is
  solved through the 2Captcha API (only the CAPTCHA image is sent; `CAPTCHA_TIMEOUT` limits
  each attempt; check the settings with `--captcha-status`). Otherwise, or when that fails, the
  CAPTCHA is completed manually (up to `--max-manual-verifications` per browser session, default
  10). An unresolved CAPTCHA, HTTP 401/403 or a persistent 429 stops the run, and Ctrl+C stops it
  cleanly. Continue with `--resume --live`.
- **Automatic CAPTCHA workflow:** only the CAPTCHA image element is captured from the open page
  and sent to 2Captcha; the answer is typed into the site's form and submitted with the same
  pacing as page navigations; the verification is accepted only when the site redirects to the
  puzzle listing, and the requested page is then reloaded and validated like any other page. A
  rejected answer is reported to 2Captcha and retried with the new image.
- **Resume:** completed pages are always skipped; interrupted and failed pages come first. Ctrl+C,
  an unresolved CAPTCHA, 401/403 and a persistent 429 return the current page to pending without
  counting an attempt. After a crash or power loss the page is found in `processing`, returned
  to pending on the next start (`Recovered interrupted pages: 1`) and that attempt counts.
- A progress report (pages completed, pending and failed, records, runtime, average time per
  page and an approximate remaining time without CAPTCHA waits) is printed before each page.
- **Records on screen and automatic CSV:** SQLite (`--db`) is the authoritative copy; each page's
  records and its completed status are committed together. Right after that, the page's newly
  stored records are printed (`Number`, `Start hex`, `End hex`, `Status`) and
  `verified_records.csv` next to the database (`data/verified_records.csv` by default) is
  rewritten from SQLite: written to a temporary file in the same folder, flushed to disk and
  renamed over the previous file. It therefore always holds every stored verified record exactly
  once (the same content as `--export csv`), and an interruption leaves the previous complete
  file. Every live run first removes leftover temporary files and re-synchronizes the CSV. If the
  CSV cannot be replaced (for example while Excel has it open on Windows), a warning is printed,
  the run continues and the next page writes it again. Rewriting takes about 0.25 s for 100,000
  records and 3 s for 1,000,000 (measured on the development machine).
- **Export:** `--export csv|xlsx [--output FILE]` reads the database only and never contacts the
  site. CSV is UTF-8 with BOM; in XLSX the hex columns are text cells, so all 18 digits are kept.
  The XLSX file is only produced on demand (`--export xlsx`, option 4 of the Windows menu).
- `python main.py --help` lists every option. See `docs/PRODUCTION_READINESS.md` for the safety
  checks, time estimates and the outstanding access requirements.
- `python demo.py` shows the stored results offline: page statuses, records, a comparison of
  the CSV/XLSX exports with SQLite and a simulated progress display. The database is only read.
- **Pacing and costs:** every page navigation and CAPTCHA answer submission waits `--delay`
  (default 15 s) plus 0..`--jitter` s (default 5), about 205 requests per hour at most on
  average. 2Captcha charges per image submitted (see its current price list). Each CAPTCHA gets
  at most 3 images. Automatic solving stops being tried once 3 automatic verifications have been
  confirmed in a run, but CAPTCHAs that fail automatically do not count toward that: they go to
  manual verification (at most 10 per run; the run stops at the 11th). So with the default
  settings a run sends **at most 42 images** (3 × (3 + 10 + 1)), a hard limit the program
  enforces, and a `--headless` run at most 12 in practice (it stops at the first CAPTCHA that fails
  automatically). Each new browser session has so far needed a CAPTCHA, so fewer runs with a larger
  `--max-pages` cost less. The real success rate and cost per accepted CAPTCHA have not been
  measured. Every run ends with a `CAPTCHA summary:` line (CAPTCHAs shown, automatic
  verifications, images sent to 2Captcha, rejected answers, uncertain outcomes, manual fallbacks).

### Failed pages

```bash
python main.py --resume                               # counts, next pages, pages skipped after 3 attempts
python -c "import sqlite3; c = sqlite3.connect('file:data/honestpool.db?mode=ro', uri=True); [print(r) for r in c.execute('SELECT page_number, status, attempts, last_error FROM page_progress WHERE attempts > 0 AND status <> ? ORDER BY page_number', ['completed'])]"
python main.py --resume --live --max-pages 5          # failed pages are retried first
python main.py --resume --live --max-pages 5 --max-attempts 4   # one more attempt for skipped pages
```

The second command opens the database read-only and prints each failed or interrupted page with
its attempts and last error. A failed page never stores records.

### The three-attempt limits and recovery limitations

| Limit | Scope | When reached | Change with |
|---|---|---|---|
| 3 attempts per page | Across runs, stored in SQLite | Page skipped and listed as `Skipped after 3 failed attempts` | `--max-attempts K` |
| 3 attempts per CAPTCHA | 2Captcha answers for one CAPTCHA | Manual verification; `--headless` stops the run | Fixed |
| 3 automatic verifications per session | One `main.py` run | Manual verification, otherwise the run stops | Fixed |
| 10 manual verifications per session | One `main.py` run | The run stops; progress is saved and `--resume --live` continues | `--max-manual-verifications V` (1..50) |

A CAPTCHA shown again right after a verification, for the same page, always stops the run.

`--retries` (default 2) repeats a navigation after a timeout, connection error, 429 or 5xx within
the same page attempt.

Recovery limitations:

- Everything already committed is kept, but nothing restarts by itself: after any stop, run
  `python main.py --resume --live` again.
- The page in progress is fetched again from the start; repeated crashes on the same page can
  use up its attempts.
- `--resume` only continues the last saved range.
- The scraper cannot recover a deleted or corrupted database or a full disk. Back up
  `data/honestpool.db` together with its `-wal` and `-shm` files while the scraper is stopped,
  or export regularly.
- Recovery was validated with simulated interruptions (a hard kill while loading and while saving
  a page, and Ctrl+C), not with a real crash during a live run. The hard kill is SIGKILL on Linux
  and TerminateProcess on Windows; only the Linux variant has been run so far.
- On Windows, the lock of a process that was killed is released by the OS, possibly after a short
  delay: if `another process is writing` appears right after a crash, wait a few seconds.

### Offline import of verified numbers (optional)

Verified segment numbers supplied by a **trusted, authorized data source** (for example a list
provided by the site operator) can be imported without contacting the website:

```bash
python main.py --import-verified verified_numbers.txt                  # asks who supplied the file
python main.py --import-verified verified_numbers.csv --source "list supplied by the operator, 2026-10"
python main.py --import-verified verified_numbers.csv --source "..." --export xlsx
python main.py --export csv --export-source imported                   # only imported records
```

- **Formats** (UTF-8, optional BOM): `.txt` with one segment number per line (blank lines are
  ignored); `.csv` with a header row containing a column named exactly `Number`. Other columns
  are allowed; if the CSV has `Start hex`, `End hex` or `Status` columns (as written by
  `--export`), they must match the computed range and `Проверено`.
- **Validation:** only whole decimal numbers from 1 to 67,108,864 are accepted. Signs,
  decimals, thousands separators, hex, empty values, malformed CSV, a missing `Number` column,
  rows with the wrong field count, non-UTF-8 or binary files and empty files are rejected.
  A file with any invalid value is rejected as a whole and nothing is written; the error lists
  the line numbers. Duplicate numbers are removed.
- **Hex values:** for each number N, `start = 0x400000000000000000 + (N - 1) * 2**44` and
  `end = start + 2**44`, written as 18 lowercase hex digits; Status is `Проверено`.
- **Trust:** the program cannot check where a file comes from. Imported numbers are stored as
  verified, so the operator must state the authorized source with `--source`, or answer the
  prompt; without it (or in a non-interactive shell) the import is refused. Never import guessed
  or unverified numbers.
- **Provenance:** imported records are stored in their own tables (`imported_records`, linked to
  `import_batches` with the source, file name, SHA-256, format, counts and time), created on the
  first import. Website records (`verified_records`) and page progress are never changed:
  numbers already stored are skipped, never overwritten, and no website page is marked
  completed. Pages are still fetched normally later; a number then found on the website is
  exported once.
- **Export:** `--export csv|xlsx` includes website and imported records with the same columns
  (hex stored as text in XLSX); `--export-source website|imported` selects one of them.
- Exit code 4 means the import was refused. The import does not solve the CAPTCHA requirement
  and does not replace authorized access for live fetching.

Documentation: `docs/CLIENT_GUIDE.md` (client guide, Portuguese), `docs/OPERATOR_REQUEST.md`
(draft request to the site operator, Russian and English), `examples/CONFIG_EXAMPLES.md`.

## Project layout

```
honestpool-scraper/
├── main.py              # Entry point
├── setup_windows.bat    # Windows 11 installer (double-click)
├── configurar_2captcha.bat  # Windows: store the 2Captcha key (DPAPI)
├── iniciar_honestpool.bat   # Windows: Portuguese menu
├── windows_launcher.py  # Helper behind the two Windows launchers
├── START_HERE_WINDOWS.txt          # Windows: six steps for the client (Portuguese, Notepad)
├── INSTALACAO_WINDOWS_11.md         # Windows installation guide (Portuguese)
├── CHECKLIST_ACEITACAO_WINDOWS_11.md  # Windows acceptance checklist (Portuguese)
├── demo.py              # Offline demonstration (read-only)
├── demo_import.py       # Offline import demonstration (synthetic data, temporary database)
├── scraper/             # Core package (Step 2+)
├── tests/               # Offline test suite and synthetic fixtures
├── data/                # SQLite DB and exports
├── docs/                # Client guide, operator request, readiness notes
├── examples/            # Example exports, configuration profiles, synthetic import files
├── logs/
├── config/              # Windows only: encrypted 2Captcha key (created on the client PC, never shipped)
├── requirements.txt
├── .env.example         # 2Captcha settings template for Linux/macOS (placeholders only)
└── README.md
```

## Step 2 inspection on Windows (Chrome)

Use this when the site opens in Windows Chrome but not inside the Ubuntu VM.
Do **not** copy the Linux `.venv` folder — create a fresh Windows venv.

```powershell
# Adjust the path if your project lives elsewhere on Windows
cd $env:USERPROFILE\Documents\projects\Projects\leandro-python\honestpool-scraper

py -3 --version
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
.\.venv\Scripts\python.exe inspect_site.py --channel chrome
```

`--channel chrome` drives your installed Google Chrome (visible window, temporary
Playwright profile — not your everyday Chrome profile). If a CAPTCHA appears,
solve it manually in that window within 5 minutes. The script loads at most four
pages and writes redacted reports under `logs\` and `data\`.

Optional: extend the CAPTCHA wait with `--manual-timeout 600`.

After it finishes, review outputs before sharing:

```powershell
Select-String -Path logs\*.txt,logs\*.json,data\*.html -Pattern "cookie|token|session|authorization" |
  Where-Object { $_ -notmatch "REDACTED" }
```

## Status

Two workflows are available, both writing to the same SQLite database and exporting the same
four columns:

- **Workflow A — website extraction:** verified rows are read from the original listing pages
  with Playwright (`--live`). The CAPTCHA (at the start of a browser session and again from time
  to time, e.g. after 19 pages in a run on 2026-10-14) is solved through 2Captcha when
  configured, otherwise by a person in the browser window.
- **Workflow B — authorized import:** verified segment numbers from an authorized TXT or CSV
  file are imported offline and their hex ranges are calculated locally (`--import-verified`).
  `python demo_import.py` demonstrates it with synthetic data on a temporary database.

In the database we inspected, pages 1–3 have been fetched live (10 verified records). The
project is suitable for limited, supervised extraction and client acceptance testing; it is not
a finished bulk-extraction solution, and the full 671,089-page extraction has not been done.

## Validation status

**Validated by the offline test suite on Ubuntu (765 passed, 9 Windows-only skipped, no
network):** extraction of rows with the
exact green `Проверено` badge only (look-alike statuses are never saved); hex validation against
the formula; atomic per-page commits; a 10-page end-to-end run through the real CLI, runner and
SQLite layer with an interruption on page 6 (hard kill while loading, hard kill inside the page's
transaction, hard kill while the automatic CSV is rewritten, and Ctrl+C), resume without
repeating completed pages, recovery of the interrupted page, a CAPTCHA solved through the mocked
2Captcha API, the automatic CSV after every page, and CSV/XLSX exports compared with SQLite; retries, attempt limits and safe stops on 401/403/429; the 2Captcha client and error
handling; the offline import; no API key or CAPTCHA answer in logs or messages; the single-writer
lock across processes, including release after a killed process; the Windows launcher logic
(key storage, redaction, menu commands, Ctrl+C relay, job-object cleanup with a simulated
kernel32) and static checks of the `.bat` files; with a real headless Chromium on a local page
(Linux): the Playwright driver's environment does not contain the key, and after Ctrl+C or a
hard kill no driver or Chromium process remains and the page is pending or recoverable.

**Windows 11:** the installer, launchers, DPAPI key storage, `msvcrt` lock, job object and
Ctrl+C behaviour have not been run on Windows yet; `CHECKLIST_ACEITACAO_WINDOWS_11.md` covers
that first test.

**Verified in our own supervised live runs (before 2Captcha):** pages 1–3 fetched with manual
CAPTCHA verification (10 records, exported to CSV/XLSX); the safe stop on a second CAPTCHA in
the same session (the limit was then 1 manual verification per session; it is 10 since
2026-10-14). Numbers 9 and 101 were independently confirmed against the client's data.

**Demonstrated in the client's screenshots:** a real HonestPool CAPTCHA solved automatically
through 2Captcha, with the scraper continuing on pages 7020 and 7021. We did not observe that run
and its database has not been inspected, so the screenshots do not prove that records were saved.

**Not yet independently verified against the live website:** record saving during 2Captcha
runs (needs the client's database); the page shown after a rejected answer; the 2Captcha success
rate and real cost; how often CAPTCHAs appear in long runs; the site's rate limits over hundreds
of pages; recovery from a real crash during a live run; live `--headless` operation; unattended
operation (not supported).

## Limitations

- **CAPTCHA:** Workflow A solves the image CAPTCHA through 2Captcha when configured, with
  manual verification as the fallback. See "Validation status" for what has and has not been
  confirmed against the live site.
- **No unattended operation:** website extraction requires supervision.
- **Full extraction incomplete:** in the database we inspected, only pages 1–3 of 671,089 have
  been fetched. A full run would take an estimated 136–780 days of continuous operation,
  excluding CAPTCHAs and blocks.
- **Unknown site limits:** the permitted request rate is not published; large ranges need
  authorized access from the site operator (see `docs/PRODUCTION_READINESS.md` and
  `docs/OPERATOR_REQUEST.md`).
- **Official API:** it hands out scan ranges and accepts results, but does not list verified
  segments, so it cannot replace either workflow.
- **Workflow B trust:** the program cannot verify where an import file comes from; the operator
  must state an authorized source. No authorized list has been obtained yet.
- **Imports:** TXT and CSV only; there is no command to undo an import batch.

## License

Proprietary — client project.
