"""Configuration and constants (to be implemented in Step 2+)."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"

DEFAULT_BASE_URL = "https://honestpool.ru/puzzle?id=71"

# Puzzle 71 segment layout, as published by the site and its API (bits=44).
PUZZLE_ID = 71
RANGE_START = 0x400000000000000000
SEGMENT_BITS = 44
SEGMENT_SIZE = 1 << SEGMENT_BITS
SEGMENT_COUNT = 67_108_864
HEX_WIDTH = 18

# Original listing pagination: 100 segments per page, page N holds numbers (N-1)*100+1 .. N*100.
PAGE_SIZE = 100
TOTAL_PAGES = -(-SEGMENT_COUNT // PAGE_SIZE)  # 671,089; the last page holds 64 rows

DB_PATH = DATA_DIR / "honestpool.db"
MAX_PAGE_ATTEMPTS = 3

# Live runs rewrite this CSV (next to the database) from SQLite after every completed page.
# The XLSX export is only produced on demand (--export xlsx).
AUTO_CSV_NAME = "verified_records.csv"
XLSX_EXPORT_NAME = "verified_records.xlsx"

# CAPTCHA provider configuration (read from the environment by scraper.captcha_provider).
# With CAPTCHA_PROVIDER=2captcha and an API key, image CAPTCHAs are sent to 2Captcha
# (scraper.two_captcha); otherwise, and whenever that fails, they are completed manually.
CAPTCHA_PROVIDER_ENV = "CAPTCHA_PROVIDER"
CAPTCHA_API_KEY_ENV = "CAPTCHA_API_KEY"
CAPTCHA_TIMEOUT_ENV = "CAPTCHA_TIMEOUT"
MANUAL_PROVIDER = "manual"
TWO_CAPTCHA_PROVIDER = "2captcha"
CAPTCHA_PROVIDERS = (MANUAL_PROVIDER, TWO_CAPTCHA_PROVIDER)
DEFAULT_CAPTCHA_TIMEOUT_S = 120.0
MIN_CAPTCHA_TIMEOUT_S, MAX_CAPTCHA_TIMEOUT_S = 10.0, 600.0
