"""Platform differences used by the crash-recovery tests.

``hard_kill`` ends the current process at once, like a power cut: no ``finally`` blocks, atexit
hooks, SQLite commits or buffered writes run. On Linux/macOS that is SIGKILL; on Windows,
``os.kill`` with any signal other than CTRL_C_EVENT/CTRL_BREAK_EVENT calls TerminateProcess,
which is just as abrupt.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

from scraper.database import Database, DatabaseLockedError

WINDOWS = sys.platform == "win32"
WINDOWS_KILL_CODE = 137
KILLED_RETURNCODE = WINDOWS_KILL_CODE if WINDOWS else -signal.SIGKILL


def hard_kill() -> None:
    if WINDOWS:
        os.kill(os.getpid(), WINDOWS_KILL_CODE)
    else:
        os.kill(os.getpid(), signal.SIGKILL)


def open_after_crash(path: Path, timeout_s: float = 10.0) -> Database:
    """Open the database as writer after another process was killed while holding the lock.

    Linux releases a dead process's flock immediately. Windows releases msvcrt locks of a
    terminated process too, but its documentation allows a short delay, so retry briefly there.
    """
    deadline = time.monotonic() + (timeout_s if WINDOWS else 0)
    while True:
        try:
            return Database(path)
        except DatabaseLockedError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.2)


def utf8_child_env(**extra: str) -> dict[str, str]:
    """Environment for Python child processes whose output is read as UTF-8 on every platform."""
    return {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", **extra}
