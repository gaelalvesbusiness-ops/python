"""The 2Captcha key never reaches the Playwright driver or Chromium, and no browser process
outlives a run (offline; the real-browser tests only open a local HTML string)."""

from __future__ import annotations

import ctypes
import io
import os
import select
import signal
import subprocess
import sys
import textwrap
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import main as cli
import windows_launcher as wl
from scraper import browser, two_captcha
from scraper.browser import BrowserSession, BrowserSettings
from scraper.captcha_provider import withdraw_api_key
from scraper.config import PROJECT_ROOT
from scraper.database import COMPLETED, PENDING, PROCESSING, Database
from tests.fake_playwright import FakePlaywright, ScriptedNav, captcha_form, factory, success
from tests.fake_two_captcha import FAKE_KEY, FakeTwoCaptchaAPI
from tests.html_fixtures import listing_page
from tests.platform_support import KILLED_RETURNCODE, WINDOWS, open_after_crash, utf8_child_env

CAPTCHA_ENV = {"CAPTCHA_PROVIDER": "2captcha", "CAPTCHA_API_KEY": FAKE_KEY, "CAPTCHA_TIMEOUT": "120"}
KEY = "CAPTCHA_API_KEY"
LIVE = ["--start-page", "2", "--end-page", "2", "--live", "--headless"]


@pytest.fixture(autouse=True)
def no_job_object(monkeypatch: pytest.MonkeyPatch) -> None:
    """The test process itself never joins a Windows job object."""
    monkeypatch.setattr(wl.ProcessTree, "_shared", wl.ProcessTree())


def set_captcha_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in CAPTCHA_ENV.items():
        monkeypatch.setenv(name, value)


class DriverProbe(FakePlaywright):
    """Records the environment Playwright's own code would give its driver process."""

    def start(self) -> DriverProbe:
        from playwright._impl._driver import get_driver_env

        self.driver_env = get_driver_env()
        return self


# --- credential isolation ----------------------------------------------------------------------


def test_driver_and_chromium_never_receive_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    set_captcha_env(monkeypatch)
    monkeypatch.setenv("HONESTPOOL_PROBE", "kept")
    fake = DriverProbe()
    with BrowserSession(BrowserSettings(), factory(fake)):
        pass
    driver_env, chromium_env = fake.driver_env, fake.chromium.envs[0]
    assert driver_env is not None and chromium_env is not None
    for env in (driver_env, chromium_env):
        assert KEY not in env and all(FAKE_KEY not in str(value) for value in env.values())
        assert env["HONESTPOOL_PROBE"] == "kept", "everything else is passed on unchanged"
    assert not [name for name in chromium_env if name.startswith("CAPTCHA_")]
    assert fake.chromium.launches == [{"headless": False, "handle_sigint": False}]


def test_browser_environment_drops_captcha_settings_in_any_letter_case() -> None:
    env = {"PATH": "p", "captcha_api_key": "x", "Captcha_Provider": "2captcha", "CAPTCHA_TIMEOUT": "1", "LANG": "ru"}
    assert browser.browser_environment(env) == {"PATH": "p", "LANG": "ru"}


def test_withdraw_removes_only_the_key_and_can_repeat() -> None:
    env = dict(CAPTCHA_ENV, PATH="p")
    withdraw_api_key(env)
    withdraw_api_key(env)
    assert env == {"CAPTCHA_PROVIDER": "2captcha", "CAPTCHA_TIMEOUT": "120", "PATH": "p"}


@pytest.fixture
def cli_live(monkeypatch: pytest.MonkeyPatch) -> tuple[DriverProbe, FakeTwoCaptchaAPI]:
    fake = DriverProbe()
    monkeypatch.setattr(browser, "BrowserSession", lambda settings: BrowserSession(settings, factory(fake)))
    api = FakeTwoCaptchaAPI(["ok42"])
    monkeypatch.setattr(two_captcha, "urllib_transport", api)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    real_run_live = cli.run_live
    monkeypatch.setattr(cli, "run_live", lambda db, start, end, s, **kw: real_run_live(
        db, start, end, replace(s, page_delay_s=0, page_delay_jitter_s=0, captcha_poll_s=0.001), **kw))
    set_captcha_env(monkeypatch)
    return fake, api


def test_cli_solver_keeps_the_key_while_the_browser_never_gets_it(
        cli_live: tuple[DriverProbe, FakeTwoCaptchaAPI], db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fake, api = cli_live
    page = fake.browser.context.page
    page.script = [captcha_form(1), success(2, [150])]
    page.submissions = [ScriptedNav(url="https://honestpool.ru/puzzle?id=71", html=listing_page(1))]
    assert cli.main(["--db", str(db_path), *LIVE]) == 0
    assert api.methods == ["createTask", "getTaskResult", "reportCorrect"]
    assert [body["clientKey"] for _, body in api.requests] == [FAKE_KEY] * 3, "the 2Captcha client still has it"
    assert KEY not in os.environ
    assert fake.driver_env is not None and KEY not in fake.driver_env and KEY not in fake.chromium.envs[0]
    out, err = capsys.readouterr()
    assert FAKE_KEY not in out + err
    with Database(db_path) as db:
        assert db.page(2)["status"] == COMPLETED


def test_key_leaves_the_environment_only_after_the_solver_has_it(
        cli_live: tuple[DriverProbe, FakeTwoCaptchaAPI], db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _ = cli_live
    fake.browser.context.page.script = [success(2, [150])]
    seen: dict[str, Any] = {}
    real_create, real_run_live = cli.create_solver, cli.run_live

    def create_solver(config: Any) -> Any:
        seen["key_in_env_at_solver"] = KEY in os.environ
        seen["solver"] = real_create(config)
        return seen["solver"]

    def run_live(*args: Any, **kwargs: Any) -> Any:
        seen["key_in_env_at_browser"] = KEY in os.environ
        seen["solver_used"] = kwargs["solver"]
        return real_run_live(*args, **kwargs)

    monkeypatch.setattr(cli, "create_solver", create_solver)
    monkeypatch.setattr(cli, "run_live", run_live)
    assert cli.main(["--db", str(db_path), *LIVE]) == 0
    assert seen["key_in_env_at_solver"] and not seen["key_in_env_at_browser"]
    assert seen["solver_used"] is seen["solver"] and seen["solver"]._api_key == FAKE_KEY
    assert FAKE_KEY not in repr(seen["solver"])


def test_status_check_also_withdraws_the_key(monkeypatch: pytest.MonkeyPatch,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    set_captcha_env(monkeypatch)
    assert cli.main(["--captcha-status"]) == 0
    assert KEY not in os.environ and FAKE_KEY not in capsys.readouterr().out


@pytest.mark.parametrize(("args", "gets_settings"), [
    (["--start-page", "1", "--end-page", "5", "--live", "--max-pages", "2", "--nav-timeout", "60"], True),
    (["--resume", "--live", "--max-pages", "2", "--nav-timeout", "60"], True),
    (["--captcha-status"], True),
    (["--resume"], False),
    (["--export", "csv"], False),
    (["--export", "xlsx"], False),
])
def test_launcher_gives_the_key_only_to_commands_that_use_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                             args: list[str], gets_settings: bool) -> None:
    seen: dict[str, Any] = {}

    class FakePopen:
        def __init__(self, command: list[str], **kwargs: Any) -> None:
            seen.update(command=command, **kwargs)
            self.stdout = io.BufferedReader(io.BytesIO(b"ok\n"))

        def wait(self) -> int:
            return 0

        def poll(self) -> int:
            return 0

    monkeypatch.setattr(wl.subprocess, "Popen", FakePopen)
    assert wl.run_main(args, wl.CaptchaSettings(api_key=FAKE_KEY), log_path=tmp_path / "s.log",
                       out=io.StringIO()) == 0
    captcha_names = {name for name in seen["env"] if name.startswith("CAPTCHA_")}
    assert captcha_names == (set(CAPTCHA_ENV) if gets_settings else set())
    assert FAKE_KEY not in " ".join(seen["command"]) + (tmp_path / "s.log").read_text(encoding="utf-8")


# --- Windows job object (simulated kernel32) ----------------------------------------------------


class FakeKernel32:
    JOB, SELF = 0x10, -1

    def __init__(self, *, refuse: tuple[str, ...] = (), listings: list[list[int]] | None = None,
                 outside: tuple[int, ...] = ()) -> None:
        self.refuse, self.listings, self.outside = set(refuse), list(listings or [[]]), set(outside)
        self.calls: list[str] = []
        self.flags: int | None = None
        self.terminated: list[tuple[int, int]] = []
        self.closed: list[int] = []

    def CreateJobObjectW(self, attributes: Any, name: Any) -> int:
        self.calls.append("CreateJobObjectW")
        return 0 if "CreateJobObjectW" in self.refuse else self.JOB

    def SetInformationJobObject(self, job: int, info_class: int, info: Any, size: int) -> bool:
        self.calls.append("SetInformationJobObject")
        assert (job, info_class, size) == (self.JOB, 9, ctypes.sizeof(wl._ExtendedLimits))
        self.flags = info._obj.BasicLimitInformation.LimitFlags
        return "SetInformationJobObject" not in self.refuse

    def GetCurrentProcess(self) -> int:
        return self.SELF

    def AssignProcessToJobObject(self, job: int, process: int) -> bool:
        self.calls.append("AssignProcessToJobObject")
        assert (job, process) == (self.JOB, self.SELF)
        return "AssignProcessToJobObject" not in self.refuse

    def QueryInformationJobObject(self, job: int, info_class: int, info: Any, size: int, returned: Any) -> bool:
        assert (job, info_class, size) == (self.JOB, 3, ctypes.sizeof(wl._ProcessIdList))
        pids = self.listings.pop(0) if len(self.listings) > 1 else self.listings[0]
        listing = info._obj
        listing.NumberOfAssignedProcesses = listing.NumberOfProcessIdsInList = len(pids)
        for index, pid in enumerate(pids):
            listing.ProcessIdList[index] = pid
        return True

    def OpenProcess(self, access: int, inherit: bool, pid: int) -> int:
        assert access == wl.PROCESS_TERMINATE | wl.PROCESS_QUERY_LIMITED_INFORMATION and not inherit
        return 1000 + pid

    def IsProcessInJob(self, handle: int, job: int, result: Any) -> bool:
        result._obj.value = int(handle - 1000 not in self.outside)
        return True

    def TerminateProcess(self, handle: int, code: int) -> bool:
        self.terminated.append((handle - 1000, code))
        return True

    def CloseHandle(self, handle: int) -> bool:
        self.closed.append(handle)
        return True


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_launcher_joins_one_kill_on_close_job() -> None:
    kernel32 = FakeKernel32()
    tree = wl.ProcessTree(kernel32)
    assert tree.join() and tree.active
    assert kernel32.calls == ["CreateJobObjectW", "SetInformationJobObject", "AssignProcessToJobObject"]
    assert kernel32.flags == wl.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | wl.JOB_OBJECT_LIMIT_BREAKAWAY_OK
    assert tree.join() and len(kernel32.calls) == 3, "joining again creates no second job"
    assert kernel32.closed == [], "the job handle stays open until the launcher ends"


@pytest.mark.parametrize("refused", ["CreateJobObjectW", "SetInformationJobObject", "AssignProcessToJobObject"])
def test_a_refused_job_changes_nothing(refused: str) -> None:
    kernel32 = FakeKernel32(refuse=(refused,))
    tree = wl.ProcessTree(kernel32)
    assert not tree.join() and not tree.active
    assert kernel32.closed == ([] if refused == "CreateJobObjectW" else [FakeKernel32.JOB])
    assert tree.end_leftovers(grace_s=0) == 0 and tree.others() == []


def test_leftovers_get_time_to_close_by_themselves_and_are_then_ended() -> None:
    own = os.getpid()
    kernel32 = FakeKernel32(listings=[[own, 501, 502, 503], [own, 502, 503]], outside=(503,))
    tree, clock = wl.ProcessTree(kernel32), Clock()
    tree.join()
    assert tree.end_leftovers(grace_s=1.0, sleep=clock.sleep, clock=clock) == 1
    assert clock.now >= 1.0, "the driver gets the grace period to close Chromium itself"
    assert kernel32.terminated == [(502, wl.LEFTOVER_EXIT_CODE)], "never this launcher, never a process outside the job"
    assert sorted(kernel32.closed) == [1502, 1503]


def test_nothing_is_ended_when_everything_closed_by_itself() -> None:
    own = os.getpid()
    kernel32 = FakeKernel32(listings=[[own, 501], [own]])
    tree, clock = wl.ProcessTree(kernel32), Clock()
    tree.join()
    assert tree.end_leftovers(grace_s=10.0, sleep=clock.sleep, clock=clock) == 0
    assert kernel32.terminated == [] and clock.now < 1.0


def test_job_structures_match_the_windows_layout() -> None:
    pointer = ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(wl._BasicLimits) == (64 if pointer == 8 else 48)
    assert ctypes.sizeof(wl._ExtendedLimits) == (144 if pointer == 8 else 112)
    assert wl._ProcessIdList.ProcessIdList.offset == 8


@pytest.mark.skipif(WINDOWS, reason="checks the non-Windows fallback")
def test_no_job_object_outside_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wl.ProcessTree, "_shared", None)
    tree = wl.ProcessTree.shared()
    assert not tree.active and tree.end_leftovers() == 0 and wl.ProcessTree.shared() is tree


class StepPopen:
    """main.py stand-in whose output reads follow a script; records kill() in ``events``."""

    def __init__(self, steps: list[bytes | BaseException], returncode: int, events: list[str]) -> None:
        self.steps, self.returncode, self.events, self.running = list(steps), returncode, events, True
        self.stdout = self

    def read1(self, size: int) -> bytes:
        step = self.steps.pop(0) if self.steps else b""
        if isinstance(step, BaseException):
            raise step
        self.running = bool(step)
        return step

    def poll(self) -> int | None:
        return None if self.running else self.returncode

    def wait(self) -> int:
        self.running = False
        return self.returncode

    def kill(self) -> None:
        self.events.append("kill main.py")


class RecordingTree:
    def __init__(self, events: list[str], leftovers: int = 0) -> None:
        self.events, self.leftovers = events, leftovers

    def end_leftovers(self) -> int:
        self.events.append("end leftovers")
        return self.leftovers


def test_browser_processes_left_after_main_are_ended_and_reported(monkeypatch: pytest.MonkeyPatch,
                                                                   tmp_path: Path) -> None:
    events: list[str] = []
    monkeypatch.setattr(wl.subprocess, "Popen", lambda *a, **k: StepPopen([b"Live run: 2 completed\n", b""], 0, events))
    out = io.StringIO()
    code = wl.run_main(["--resume", "--live"], None, log_path=tmp_path / "s.log", out=out,
                       process_tree=lambda: RecordingTree(events, leftovers=2))
    assert code == 0 and events == ["end leftovers"]
    assert "[2 processo(s) do navegador ainda abertos depois do fim do programa foram encerrados.]" in out.getvalue()


def test_launcher_failure_ends_main_first_and_then_its_browser(monkeypatch: pytest.MonkeyPatch,
                                                                 tmp_path: Path) -> None:
    events: list[str] = []
    monkeypatch.setattr(wl.subprocess, "Popen",
                        lambda *a, **k: StepPopen([b"Progress\n", RuntimeError("console vanished")], 0, events))
    with pytest.raises(RuntimeError):
        wl.run_main(["--resume", "--live"], None, log_path=tmp_path / "s.log", out=io.StringIO(),
                    process_tree=lambda: RecordingTree(events))
    assert events == ["kill main.py", "end leftovers"]


def test_ctrl_c_never_ends_main_or_its_browser_early(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    events: list[str] = []
    steps: list[bytes | BaseException] = [b"Progress\n", KeyboardInterrupt(), b"Interrupted: ...\n", b""]
    monkeypatch.setattr(wl.subprocess, "Popen", lambda *a, **k: StepPopen(steps, 130, events))
    code = wl.run_main(["--resume", "--live"], None, log_path=tmp_path / "s.log", out=io.StringIO(),
                       process_tree=lambda: RecordingTree(events))
    assert code == 130 and events == ["end leftovers"], "main.py is never killed; cleanup runs after it exited"


# --- real processes ------------------------------------------------------------------------------


def playwright_chromium_installed() -> bool:
    roots = [Path.home() / ".cache" / "ms-playwright"]
    configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if configured and configured != "0":
        roots.append(Path(configured))
    return any(root.is_dir() and any(root.glob("chromium_headless_shell-*")) for root in roots)


REAL_BROWSER = sys.platform.startswith("linux") and playwright_chromium_installed()


def process_table() -> dict[int, tuple[int, str]]:
    """pid -> (parent pid, state) from /proc."""
    table = {}
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                stat = (entry / "stat").read_text()
            except OSError:
                continue
            state, parent = stat.rsplit(")", 1)[1].split()[:2]
            table[int(entry.name)] = (int(parent), state)
    return table


def descendants(pid: int) -> list[int]:
    table, found, frontier = process_table(), [], [pid]
    while frontier:
        parent = frontier.pop()
        children = [child for child, (ppid, _) in table.items() if ppid == parent]
        found += children
        frontier += children
    return found


def still_running(pids: list[int]) -> list[int]:
    table = process_table()
    return [pid for pid in pids if pid in table and table[pid][1] != "Z"]


def executable(pid: int) -> str:
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return ""


def read_until(process: subprocess.Popen[bytes], marker: bytes, timeout_s: float) -> str:
    assert process.stdout is not None
    fd, buffer, deadline = process.stdout.fileno(), b"", time.monotonic() + timeout_s
    while marker not in buffer and time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], max(0.0, deadline - time.monotonic()))
        if not ready:
            break
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        buffer += chunk
    assert marker in buffer, buffer.decode("utf-8", "replace")
    return buffer.decode("utf-8", "replace")


@pytest.mark.skipif(not REAL_BROWSER, reason="needs Linux /proc and Playwright's Chromium (opens a local page only)")
@pytest.mark.parametrize("stop", ["ctrl-c", "kill"])
def test_real_browser_never_gets_the_key_and_never_outlives_main(tmp_path: Path, stop: str) -> None:
    db_path = tmp_path / "probe.db"
    env = utf8_child_env(**CAPTCHA_ENV)
    process = subprocess.Popen([sys.executable, "-m", "tests.browser_probe", str(db_path)], cwd=PROJECT_ROOT,
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        ready = read_until(process, b"READY", 90)
        assert "solver=yes key_in_environment=no" in ready
        tree = descendants(process.pid)
        drivers = [pid for pid in tree if Path(executable(pid)).name == "node"]
        assert len(drivers) == 1 and len(tree) >= 2, "the Playwright driver and Chromium are running"
        driver_env = Path(f"/proc/{drivers[0]}/environ").read_bytes()
        assert b"PATH=" in driver_env, "the driver's environment is readable"
        assert FAKE_KEY.encode() not in driver_env and b"CAPTCHA_API_KEY=" not in driver_env
        if stop == "ctrl-c":
            os.killpg(process.pid, signal.SIGINT)  # what Ctrl+C in a terminal does
        else:
            process.kill()
        rest, _ = process.communicate(timeout=60)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
    deadline = time.monotonic() + 20
    while still_running(tree) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert still_running(tree) == [], "no driver or Chromium process outlives main"
    with open_after_crash(db_path) as db:
        page = db.page(1)
        if stop == "ctrl-c":
            assert process.returncode == 130 and b"STOPPED" in rest
            assert (page["status"], page["attempts"]) == (PENDING, 0), "the interrupted page went back to pending"
        else:
            assert process.returncode == KILLED_RETURNCODE
            assert (page["status"], page["attempts"]) == (PROCESSING, 1)
            assert db.recover_interrupted() == 1 and db.page(1)["status"] == PENDING


JOB_PROBE = textwrap.dedent("""\
    import subprocess, sys
    import windows_launcher as wl
    tree = wl.ProcessTree.shared()
    print("joined" if tree.active else "refused", flush=True)
    maker = "import subprocess, sys; n = subprocess.DEVNULL; " \\
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], " \\
            "stdin=n, stdout=n, stderr=n); print(p.pid, flush=True)"
    orphan = subprocess.run([sys.executable, "-c", maker], capture_output=True, text=True, check=True).stdout.split()[0]
    print("orphan", orphan, flush=True)
    if sys.argv[1] == "end-leftovers":
        print("ended", tree.end_leftovers(grace_s=1.0), flush=True)
""")


def windows_process_ended(pid: int, timeout_s: float = 15.0) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        return True
    try:
        return kernel32.WaitForSingleObject(ctypes.c_void_p(handle), int(timeout_s * 1000)) == 0
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


@pytest.mark.skipif(not WINDOWS, reason="job objects exist only on Windows")
@pytest.mark.parametrize("mode", ["end-leftovers", "launcher-exits"])
def test_windows_job_ends_processes_left_behind(tmp_path: Path, mode: str) -> None:
    done = subprocess.run([sys.executable, "-c", JOB_PROBE, mode], cwd=PROJECT_ROOT, capture_output=True,
                          encoding="utf-8", env=utf8_child_env(PYTHONPATH=str(PROJECT_ROOT)), timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    lines = dict(line.split(" ", 1) if " " in line else (line, "") for line in done.stdout.splitlines())
    assert "joined" in lines, done.stdout
    if mode == "end-leftovers":
        assert int(lines["ended"]) >= 1
    assert windows_process_ended(int(lines["orphan"])), "the orphaned process was ended with the job"
