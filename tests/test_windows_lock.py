"""The Windows branch of the single-writer lock (msvcrt.locking), exercised with a fake msvcrt."""

from __future__ import annotations

import errno
import os
import subprocess
import sys
from pathlib import Path

import pytest

import scraper.database as database
from scraper.config import PROJECT_ROOT
from scraper.database import Database, DatabaseLockedError
from tests.platform_support import KILLED_RETURNCODE, WINDOWS, open_after_crash, utf8_child_env


class FakeMsvcrt:
    """Byte-range locks keyed by file identity, like LK_NBLCK/LK_UNLCK on one byte at offset 0."""

    LK_UNLCK, LK_NBLCK = 0, 2

    def __init__(self) -> None:
        self.owners: dict[tuple[int, int], int] = {}
        self.calls: list[tuple[int, int]] = []
        self.error: OSError | None = None

    def locking(self, fd: int, mode: int, nbytes: int) -> None:
        assert nbytes == 1
        assert os.lseek(fd, 0, os.SEEK_CUR) == 0, "the lock byte must be at offset 0"
        self.calls.append((mode, fd))
        if self.error is not None:
            raise self.error
        stat = os.fstat(fd)
        key = (stat.st_dev, stat.st_ino)
        if mode == self.LK_NBLCK:
            if key in self.owners and self.owners[key] != fd:
                raise OSError(errno.EACCES, "Permission denied")
            self.owners[key] = fd
        elif mode == self.LK_UNLCK:
            assert self.owners.pop(key) == fd


@pytest.fixture
def fake_msvcrt(monkeypatch: pytest.MonkeyPatch) -> FakeMsvcrt:
    fake = FakeMsvcrt()
    monkeypatch.setattr(database, "fcntl", None)
    monkeypatch.setattr(database, "msvcrt", fake, raising=False)
    return fake


def test_second_writer_is_refused_and_lock_is_released_on_close(fake_msvcrt: FakeMsvcrt, tmp_path: Path) -> None:
    path = tmp_path / "honestpool.db"
    first = Database(path)
    with pytest.raises(DatabaseLockedError, match="another process is writing"):
        Database(path)
    first.close()
    assert fake_msvcrt.owners == {}
    with Database(path) as second:
        assert second.record_count() == 0
    modes = [mode for mode, _ in fake_msvcrt.calls]
    assert modes == [FakeMsvcrt.LK_NBLCK, FakeMsvcrt.LK_NBLCK, FakeMsvcrt.LK_UNLCK,
                     FakeMsvcrt.LK_NBLCK, FakeMsvcrt.LK_UNLCK]


@pytest.mark.parametrize("code", [errno.EACCES, errno.EDEADLK])
def test_lock_conflict_errnos_map_to_database_locked(fake_msvcrt: FakeMsvcrt, tmp_path: Path, code: int) -> None:
    fake_msvcrt.error = OSError(code, "locked")
    with pytest.raises(DatabaseLockedError):
        Database(tmp_path / "honestpool.db")


def test_other_lock_errors_are_not_hidden(fake_msvcrt: FakeMsvcrt, tmp_path: Path) -> None:
    fake_msvcrt.error = OSError(errno.EIO, "I/O error")
    with pytest.raises(OSError) as raised:
        Database(tmp_path / "honestpool.db")
    assert not isinstance(raised.value, (DatabaseLockedError, BlockingIOError))


def test_readonly_open_takes_no_lock(fake_msvcrt: FakeMsvcrt, tmp_path: Path) -> None:
    path = tmp_path / "honestpool.db"
    Database(path).close()
    fake_msvcrt.calls.clear()
    with Database(path), Database(path, readonly=True) as reader:
        assert reader.record_count() == 0
    assert [mode for mode, _ in fake_msvcrt.calls] == [FakeMsvcrt.LK_NBLCK, FakeMsvcrt.LK_UNLCK]


@pytest.mark.skipif(WINDOWS, reason="fcntl.flock exists only on Linux and macOS")
def test_posix_branch_still_uses_flock() -> None:
    assert database.fcntl is not None, "Linux and macOS keep the original flock lock"


@pytest.mark.skipif(not WINDOWS, reason="msvcrt exists only on Windows")
def test_windows_uses_msvcrt() -> None:
    assert database.fcntl is None and database.msvcrt.LK_NBLCK is not None


# --- the real platform lock, across processes (runs on Linux and on Windows) ------------------

HOLDER = """\
import sys
from pathlib import Path
from scraper.database import Database
from tests.platform_support import hard_kill
db = Database(Path(sys.argv[1]))
print("LOCKED", flush=True)
if sys.argv[2] == "kill":
    hard_kill()
sys.stdin.readline()
db.close()
"""


def start_holder(path: Path, mode: str) -> subprocess.Popen[str]:
    holder = subprocess.Popen([sys.executable, "-c", HOLDER, str(path), mode], cwd=PROJECT_ROOT,
                              env=utf8_child_env(PYTHONPATH=str(PROJECT_ROOT)), stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "LOCKED", holder.stderr.read() if holder.stderr else ""
    return holder


def test_second_process_is_refused_while_the_first_writes(tmp_path: Path) -> None:
    path = tmp_path / "honest pool (1)" / "honestpool.db"
    holder = start_holder(path, "wait")
    try:
        with pytest.raises(DatabaseLockedError):
            Database(path)
        with Database(path, readonly=True) as reader:
            assert reader.record_count() == 0
    finally:
        holder.communicate("\n", timeout=30)
    assert holder.returncode == 0
    with Database(path) as again:
        assert again.record_count() == 0


def test_lock_of_a_killed_process_is_released(tmp_path: Path) -> None:
    path = tmp_path / "honestpool.db"
    holder = start_holder(path, "kill")
    holder.communicate(timeout=30)
    assert holder.returncode == KILLED_RETURNCODE
    with open_after_crash(path) as db:
        assert db.record_count() == 0
