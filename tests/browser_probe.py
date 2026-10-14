"""Child process for test_process_safety: one page held open in a real headless Chromium, offline.

It follows main.py's order (2Captcha client first, then the key leaves the environment), opens
a local HTML string (no network), prints READY and then waits inside the page's processing, so
the parent can press Ctrl+C or kill it there. Usage: python -m tests.browser_probe DB_PATH
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from scraper.browser import BrowserSession, BrowserSettings
from scraper.captcha_provider import create_solver, load_captcha_config, withdraw_api_key
from scraper.config import CAPTCHA_API_KEY_ENV
from scraper.database import Database
from scraper.runner import process_page

PAGE = 1


def run(db_path: Path) -> None:
    solver = create_solver(load_captcha_config())
    withdraw_api_key()
    with Database(db_path) as db:
        db.register_pages(PAGE, PAGE)
        with BrowserSession(BrowserSettings(headless=True)) as session:
            def fetch(page: int) -> str:
                session.page.set_content("<html><body>offline probe</body></html>")
                print(f"READY solver={'yes' if solver else 'no'} "
                      f"key_in_environment={'yes' if CAPTCHA_API_KEY_ENV in os.environ else 'no'}", flush=True)
                time.sleep(120)
                raise TimeoutError("the parent never stopped the probe")

            process_page(db, PAGE, fetch)


def main() -> int:
    try:
        run(Path(sys.argv[1]))
    except KeyboardInterrupt:
        print("STOPPED", flush=True)
        return 130
    return 1


if __name__ == "__main__":
    sys.exit(main())
