"""windows_launcher.py: 2Captcha key storage, redacted main.py runs and the beginner menu (offline)."""

from __future__ import annotations

import ctypes
import getpass
import io
import subprocess
import sys
import textwrap
import warnings
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

import windows_launcher as wl
from scraper.config import TOTAL_PAGES
from tests.platform_support import utf8_child_env

FAKE_KEY = "abcdef0123456789abcdef0123456789"
FORBIDDEN_FLAGS = ("--headless", "--delay", "--jitter", "--retries", "--max-attempts", "--captcha-wait", "--db")


@pytest.fixture(autouse=True)
def no_job_object(monkeypatch: pytest.MonkeyPatch) -> None:
    """The test process itself never joins a Windows job object."""
    monkeypatch.setattr(wl.ProcessTree, "_shared", wl.ProcessTree())


def fake_protect(data: bytes) -> bytes:
    return b"FAKE" + bytes(b ^ 0x5A for b in data)


def fake_unprotect(blob: bytes) -> bytes:
    if not blob.startswith(b"FAKE"):
        raise wl.KeyProtectionError("o Windows DPAPI recusou a operação (código 13)")
    return bytes(b ^ 0x5A for b in blob[4:])


class Script:
    """Console input from a list; EOFError once it runs out, like a closed console."""

    def __init__(self, answers: Iterable[str | BaseException]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


class FakeRunner:
    def __init__(self, codes: Iterable[int] = ()) -> None:
        self.codes = list(codes)
        self.calls: list[tuple[list[str], wl.CaptchaSettings | None]] = []

    def __call__(self, args: list[str], settings: wl.CaptchaSettings | None, *, log_path: Path, out: Any) -> int:
        assert log_path.parent == wl.LOGS_DIR
        self.calls.append((list(args), settings))
        return self.codes.pop(0) if self.codes else 0

    @property
    def args(self) -> list[list[str]]:
        return [args for args, _ in self.calls]


def console(answers: Iterable[str | BaseException]) -> tuple[wl.Console, io.StringIO, Script]:
    out = io.StringIO()
    script = Script(answers)
    return wl.Console(script, out), out, script


def assert_safe_commands(runner: FakeRunner) -> None:
    for args in runner.args:
        assert not set(FORBIDDEN_FLAGS) & set(args), args
        assert FAKE_KEY not in " ".join(args)


# --- configuration file -------------------------------------------------------------


def test_settings_round_trip_never_store_the_plain_key(tmp_path: Path) -> None:
    path = tmp_path / "config" / "2captcha.cfg"
    wl.save_settings(FAKE_KEY, path, fake_protect)
    text = path.read_text(encoding="utf-8")
    assert FAKE_KEY not in text
    assert "CAPTCHA_PROVIDER=2captcha\n" in text and "CAPTCHA_TIMEOUT=120\n" in text
    assert "CAPTCHA_API_KEY_DPAPI=" in text and "\nCAPTCHA_API_KEY=" not in text
    assert list(path.parent.iterdir()) == [path], "the temporary file is removed"
    settings = wl.load_settings(path, fake_unprotect)
    assert settings == wl.CaptchaSettings("2captcha", "120", FAKE_KEY)
    assert FAKE_KEY not in repr(settings)


def test_missing_config_means_not_configured(tmp_path: Path) -> None:
    assert wl.load_settings(tmp_path / "2captcha.cfg", fake_unprotect) is None


@pytest.mark.parametrize("content, error", [
    ("CAPTCHA_PROVIDER=2captcha\n", wl.ConfigFileError),
    ("CAPTCHA_API_KEY_DPAPI=not*base64\n", wl.ConfigFileError),
    ("CAPTCHA_API_KEY_DPAPI=T1RIRVI=\n", wl.KeyProtectionError),
])
def test_broken_config_is_reported_without_the_key(tmp_path: Path, content: str, error: type) -> None:
    path = tmp_path / "2captcha.cfg"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(error):
        wl.load_settings(path, fake_unprotect)


def test_child_env_uses_saved_settings_only() -> None:
    base = {"PATH": "x", "CAPTCHA_PROVIDER": "manual", "CAPTCHA_API_KEY": "stray", "CAPTCHA_TIMEOUT": "9"}
    env = wl.child_env(wl.CaptchaSettings(api_key=FAKE_KEY), base)
    assert env["CAPTCHA_PROVIDER"] == "2captcha" and env["CAPTCHA_TIMEOUT"] == "120"
    assert env["CAPTCHA_API_KEY"] == FAKE_KEY and env["PATH"] == "x"
    assert env["PYTHONUTF8"] == "1" and env["PYTHONIOENCODING"] == "utf-8" and env["PYTHONUNBUFFERED"] == "1"
    manual = wl.child_env(None, base)
    assert not {"CAPTCHA_PROVIDER", "CAPTCHA_API_KEY", "CAPTCHA_TIMEOUT"} & set(manual)


# --- Windows DPAPI (call flow with fake crypt32/kernel32) --------------------------------


class FakeCrypt32:
    def __init__(self) -> None:
        self.buffers: list[Any] = []
        self.fail = False

    def _answer(self, blob_in: Any, entropy: Any, blob_out: Any, transform: Any) -> int:
        if self.fail:
            return 0
        data = ctypes.string_at(blob_in._obj.pbData, blob_in._obj.cbData)
        salt = ctypes.string_at(entropy._obj.pbData, entropy._obj.cbData)
        assert salt == wl.DPAPI_ENTROPY
        result = transform(data)
        buffer = ctypes.create_string_buffer(result, len(result))
        self.buffers.append(buffer)
        blob_out._obj.cbData = len(result)
        blob_out._obj.pbData = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))
        return 1

    def CryptProtectData(self, blob_in, description, entropy, reserved, prompt, flags, blob_out):  # noqa: N802
        assert description == wl.DPAPI_DESCRIPTION and reserved is None and prompt is None
        assert flags == wl.CRYPTPROTECT_UI_FORBIDDEN
        return self._answer(blob_in, entropy, blob_out, lambda data: b"DPAPI" + data[::-1])

    def CryptUnprotectData(self, blob_in, description, entropy, reserved, prompt, flags, blob_out):  # noqa: N802
        assert description is None and flags == wl.CRYPTPROTECT_UI_FORBIDDEN

        def decrypt(data: bytes) -> bytes:
            assert data.startswith(b"DPAPI")
            return data[5:][::-1]
        return self._answer(blob_in, entropy, blob_out, decrypt)


class FakeKernel32:
    def __init__(self) -> None:
        self.freed = 0

    def LocalFree(self, pointer: Any) -> None:  # noqa: N802
        assert isinstance(pointer, ctypes.c_void_p) and pointer.value
        self.freed += 1


def test_dpapi_call_flow_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    crypt32, kernel32 = FakeCrypt32(), FakeKernel32()
    monkeypatch.setattr(wl, "_load_dpapi", lambda: (crypt32, kernel32))
    encrypted = wl.dpapi_protect(FAKE_KEY.encode())
    assert FAKE_KEY.encode() not in encrypted
    assert wl.dpapi_unprotect(encrypted) == FAKE_KEY.encode()
    assert kernel32.freed == 2


def test_dpapi_failure_raises_without_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    crypt32, kernel32 = FakeCrypt32(), FakeKernel32()
    crypt32.fail = True
    monkeypatch.setattr(wl, "_load_dpapi", lambda: (crypt32, kernel32))
    with pytest.raises(wl.KeyProtectionError) as raised:
        wl.dpapi_protect(FAKE_KEY.encode())
    assert FAKE_KEY not in str(raised.value)
    assert kernel32.freed == 0


@pytest.mark.skipif(sys.platform == "win32", reason="checks the non-Windows refusal")
def test_dpapi_refuses_outside_windows() -> None:
    with pytest.raises(wl.KeyProtectionError, match="só funciona no Windows"):
        wl.dpapi_protect(b"x")


@pytest.mark.skipif(sys.platform != "win32", reason="the real Windows DPAPI exists only on Windows")
def test_real_dpapi_round_trip_and_tamper_detection(tmp_path: Path) -> None:
    encrypted = wl.dpapi_protect(FAKE_KEY.encode())
    assert FAKE_KEY.encode() not in encrypted and len(encrypted) > len(FAKE_KEY)
    assert wl.dpapi_unprotect(encrypted) == FAKE_KEY.encode()
    with pytest.raises(wl.KeyProtectionError):
        wl.dpapi_unprotect(encrypted[:-1] + bytes([encrypted[-1] ^ 1]))
    path = tmp_path / "config" / "2captcha.cfg"
    wl.save_settings(FAKE_KEY, path)
    assert FAKE_KEY not in path.read_text(encoding="utf-8")
    assert wl.load_settings(path) == wl.CaptchaSettings("2captcha", "120", FAKE_KEY)


# --- running main.py ---------------------------------------------------------------------


def test_captcha_status_through_real_main_never_shows_the_key(tmp_path: Path) -> None:
    out, log = io.StringIO(), tmp_path / "logs" / "session.log"
    code = wl.run_main(["--captcha-status"], wl.CaptchaSettings(api_key=FAKE_KEY), log_path=log, out=out)
    assert code == 0
    assert "CAPTCHA provider: 2captcha | API key: set (hidden) | timeout: 120 s" in out.getvalue()
    logged = log.read_text(encoding="utf-8")
    assert "python main.py --captcha-status" in logged and "código de saída 0" in logged
    assert FAKE_KEY not in out.getvalue() + logged


def test_captcha_status_without_settings_is_manual(tmp_path: Path) -> None:
    out = io.StringIO()
    assert wl.run_main(["--captcha-status"], None, log_path=tmp_path / "s.log", out=out) == 0
    assert "CAPTCHA provider: manual (default)" in out.getvalue()


def test_output_containing_the_key_is_redacted(tmp_path: Path) -> None:
    script = tmp_path / "leak.py"
    script.write_text(textwrap.dedent("""\
        import os, sys
        print("key=" + os.environ["CAPTCHA_API_KEY"])
        print("status: Проверено")
        sys.stdout.write("partial line without newline")
        sys.exit(3)
    """), encoding="utf-8")
    out, log = io.StringIO(), tmp_path / "s.log"
    code = wl.run_main(["--live"], wl.CaptchaSettings(api_key=FAKE_KEY), log_path=log, out=out, script=script)
    assert code == 3
    for text in (out.getvalue(), log.read_text(encoding="utf-8")):
        assert FAKE_KEY not in text
        assert "key=[CHAVE OCULTA]" in text and "status: Проверено" in text
        assert "partial line without newline" in text


def test_key_travels_in_the_environment_not_the_command_line(monkeypatch: pytest.MonkeyPatch,
                                                              tmp_path: Path) -> None:
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
    args = ["--resume", "--live", "--max-pages", "2", "--nav-timeout", "60"]
    assert wl.run_main(args, wl.CaptchaSettings(api_key=FAKE_KEY), log_path=tmp_path / "s.log",
                       out=io.StringIO()) == 0
    assert seen["command"] == [sys.executable, str(wl.MAIN_PY), *args]
    assert FAKE_KEY not in " ".join(seen["command"])
    assert seen["env"]["CAPTCHA_API_KEY"] == FAKE_KEY
    assert seen["cwd"] == wl.PROJECT_ROOT and "stdin" not in seen


class ScriptedPopen:
    """A child process whose output reads follow a script of byte chunks and exceptions."""

    instances: list[ScriptedPopen] = []

    def __init__(self, steps: list[bytes | BaseException], returncode: int) -> None:
        self.steps, self.returncode, self.killed, self.running = list(steps), returncode, False, True
        self.stdout = self
        ScriptedPopen.instances.append(self)

    def read1(self, size: int) -> bytes:
        step = self.steps.pop(0) if self.steps else b""
        if isinstance(step, BaseException):
            raise step
        if step == b"":
            self.running = False
        return step

    def poll(self) -> int | None:
        return None if self.running else self.returncode

    def wait(self) -> int:
        self.running = False
        return self.returncode

    def kill(self) -> None:
        self.killed = True


def scripted(monkeypatch: pytest.MonkeyPatch, steps: list[bytes | BaseException], returncode: int) -> None:
    ScriptedPopen.instances.clear()
    monkeypatch.setattr(wl.subprocess, "Popen", lambda *a, **k: ScriptedPopen(steps, returncode))


def test_ctrl_c_keeps_relaying_until_main_has_saved_and_exited(monkeypatch: pytest.MonkeyPatch,
                                                                tmp_path: Path) -> None:
    summary = "Interrupted: the current page was returned to pending and saved records are kept.\n"
    scripted(monkeypatch, [b"Progress | page 4\n", KeyboardInterrupt(), summary.encode(), KeyboardInterrupt(), b""],
             130)
    out, log = io.StringIO(), tmp_path / "s.log"
    assert wl.run_main(["--resume", "--live"], None, log_path=log, out=out) == 130
    text = out.getvalue()
    assert text.count("[Ctrl+C recebido: aguardando o programa salvar e encerrar...]") == 2
    assert summary in text and summary in log.read_text(encoding="utf-8")
    assert "código de saída 130" in log.read_text(encoding="utf-8")
    assert not ScriptedPopen.instances[0].killed, "Ctrl+C never kills main.py; it stops by itself"


def test_child_is_ended_when_the_launcher_itself_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    scripted(monkeypatch, [b"Progress | page 4\n", RuntimeError("console vanished")], 0)
    with pytest.raises(RuntimeError):
        wl.run_main(["--resume", "--live"], None, log_path=tmp_path / "s.log", out=io.StringIO())
    assert ScriptedPopen.instances[0].killed


def test_unwritable_log_never_stops_the_operation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    scripted(monkeypatch, [b"Exported 3 verified records\n", b""], 0)
    blocked = tmp_path / "logs"
    blocked.write_text("a file where the logs folder should be", encoding="utf-8")
    out = io.StringIO()
    assert wl.run_main(["--export", "csv"], None, log_path=blocked / "s.log", out=out) == 0
    assert "a operação continua sem registro" in out.getvalue() and "Exported 3" in out.getvalue()


def test_log_that_fails_midway_is_dropped_with_a_warning(tmp_path: Path) -> None:
    out = io.StringIO()
    log = wl.SessionLog(tmp_path / "s.log", out)

    class FullDisk(io.StringIO):
        def write(self, text: str) -> int:
            raise OSError(28, "No space left on device")

    log.file = FullDisk()
    log.write("line\n")
    log.write("next line\n")
    assert log.file is None and out.getvalue().count("não pode mais ser gravado") == 1


# --- configurar_2captcha -----------------------------------------------------------------


def configure(tmp_path: Path, answers: Iterable[str], secret: str | BaseException = FAKE_KEY,
              runner: FakeRunner | None = None, protect: Any = fake_protect) -> tuple[int, str, FakeRunner, Path]:
    con, out, script = console(answers)
    runner = runner or FakeRunner()
    path = tmp_path / "config" / "2captcha.cfg"

    def read_secret(prompt: str) -> str:
        assert "nada aparece na tela" in prompt
        if isinstance(secret, BaseException):
            raise secret
        return secret

    code = wl.configure(con, path=path, read_secret=read_secret, protect=protect, unprotect=fake_unprotect,
                        runner=runner)
    assert all("chave da API" not in prompt for prompt in script.prompts), "the key is never read with input()"
    assert FAKE_KEY not in out.getvalue()
    return code, out.getvalue(), runner, path


def test_configure_saves_encrypted_key_and_checks_status(tmp_path: Path) -> None:
    code, out, runner, path = configure(tmp_path, [])
    assert code == 0
    assert runner.args == [["--captcha-status"]]
    assert runner.calls[0][1] == wl.CaptchaSettings("2captcha", "120", FAKE_KEY)
    assert FAKE_KEY not in path.read_text(encoding="utf-8")
    assert "Chave recebida (32 caracteres" in out and "CONFIGURAÇÃO OK" in out


def test_configure_reports_failed_status_check(tmp_path: Path) -> None:
    code, out, _, _ = configure(tmp_path, [], runner=FakeRunner([2]))
    assert code == 2 and "ERRO: a verificação falhou" in out


@pytest.mark.parametrize("secret, message", [
    ("   ", "Nenhuma chave foi digitada"),
    ("abc def0123456789abcdef0123456789", "caracteres inválidos"),
    ("chave-com-ç-acentuada", "caracteres inválidos"),
])
def test_configure_rejects_empty_or_malformed_keys(tmp_path: Path, secret: str, message: str) -> None:
    code, out, runner, path = configure(tmp_path, [], secret=secret)
    assert code == 1 and message in out
    assert not path.exists() and runner.calls == []


def test_configure_asks_before_saving_an_unusual_key(tmp_path: Path) -> None:
    code, out, runner, path = configure(tmp_path, ["N"], secret="ABCDEF")
    assert code == 1 and "Nada foi salvo" in out and not path.exists()
    code, _, runner, path = configure(tmp_path, ["S"], secret="ABCDEF")
    assert code == 0 and path.exists() and runner.calls[0][1].api_key == "ABCDEF"


def test_configure_keeps_existing_key_unless_confirmed(tmp_path: Path) -> None:
    configure(tmp_path, [])
    path = tmp_path / "config" / "2captcha.cfg"
    before = path.read_bytes()
    code, out, runner, _ = configure(tmp_path, ["N"], secret="0" * 32)
    assert code == 0 and "A chave salva foi mantida" in out
    assert path.read_bytes() == before and runner.calls[0][1].api_key == FAKE_KEY
    code, _, runner, _ = configure(tmp_path, ["S"], secret="0" * 32)
    assert code == 0 and runner.calls[0][1].api_key == "0" * 32


def test_configure_refuses_when_input_cannot_be_hidden(tmp_path: Path) -> None:
    def echoing_getpass(prompt: str) -> str:
        warnings.warn("Can not control echo on the terminal.", getpass.GetPassWarning, stacklevel=1)
        return FAKE_KEY

    con, out, _ = console([])
    path = tmp_path / "2captcha.cfg"
    code = wl.configure(con, path=path, read_secret=echoing_getpass, protect=fake_protect,
                        unprotect=fake_unprotect, runner=FakeRunner())
    assert code == 1 and not path.exists() and "não permite ocultar" in out.getvalue()


def test_configure_reports_protection_failure(tmp_path: Path) -> None:
    def broken_protect(data: bytes) -> bytes:
        raise wl.KeyProtectionError("o Windows DPAPI recusou a operação (código 5)")

    code, out, runner, path = configure(tmp_path, [], protect=broken_protect)
    assert code == 1 and "não foi possível salvar" in out and not path.exists() and runner.calls == []


# --- menu --------------------------------------------------------------------------------------


def run_menu(tmp_path: Path, answers: Iterable[str | BaseException], *, codes: Iterable[int] = (),
             database: bool = True, key: str | None = None) -> tuple[int, str, FakeRunner]:
    db_path, config_path = tmp_path / "data" / "honestpool.db", tmp_path / "config" / "2captcha.cfg"
    if database:
        db_path.parent.mkdir(parents=True)
        db_path.write_bytes(b"")
    if key is not None:
        wl.save_settings(key, config_path, fake_protect)
    con, out, _ = console(answers)
    runner = FakeRunner(codes)
    code = wl.Menu(con, db_path=db_path, config_path=config_path, unprotect=fake_unprotect, runner=runner).run()
    assert_safe_commands(runner)
    assert key is None or key not in out.getvalue()
    return code, out.getvalue(), runner


def test_menu_lists_the_six_options_and_exits(tmp_path: Path) -> None:
    code, out, runner = run_menu(tmp_path, ["6"])
    assert code == 0 and runner.calls == []
    for option in ("1) Iniciar extração supervisionada", "2) Retomar extração interrompida", "3) Exportar CSV",
                   "4) Exportar Excel (XLSX)", "5) Mostrar progresso", "6) Sair"):
        assert option in out
    assert "não configurado" in out


@pytest.mark.parametrize("ending", [[], [KeyboardInterrupt()]])
def test_menu_closes_cleanly_on_eof_or_ctrl_c(tmp_path: Path, ending: list[BaseException]) -> None:
    assert run_menu(tmp_path, ending)[0] == 0


def test_supervised_extraction_runs_only_after_confirmation(tmp_path: Path) -> None:
    code, out, runner = run_menu(tmp_path, ["1", "10", "12", "2", "S", "6"], key=FAKE_KEY)
    assert code == 0
    assert runner.args == [["--start-page", "10", "--end-page", "12", "--live", "--max-pages", "2",
                            "--nav-timeout", "60"]]
    assert runner.calls[0][1].api_key == FAKE_KEY
    assert "configurado (chave oculta)" in out and "páginas 10 a 12 (3 na faixa); até 2 página(s)" in out


def test_supervised_extraction_cancelled_fetches_nothing(tmp_path: Path) -> None:
    code, out, runner = run_menu(tmp_path, ["1", "10", "12", "1", "N", "6"])
    assert runner.calls == [] and "Cancelado; nada foi buscado." in out


def test_supervised_extraction_rejects_invalid_numbers(tmp_path: Path) -> None:
    answers = ["1", "abc", "0", str(TOTAL_PAGES + 1), "1.000", "-5", "500",
               "499", "502", "0", "4", "3", "talvez", "S", "6"]
    code, out, runner = run_menu(tmp_path, answers)
    assert runner.args == [["--start-page", "500", "--end-page", "502", "--live", "--max-pages", "3",
                            "--nav-timeout", "60"]]
    assert out.count("Valor inválido") == 8 and "Responda S (sim) ou N (não)." in out
    assert f"1 a {TOTAL_PAGES:,}".replace(",", ".") in out


@pytest.mark.parametrize("answers", [["1", ""], ["1", "5", ""], ["1", "5", "9", ""]])
def test_empty_answer_returns_to_menu(tmp_path: Path, answers: list[str]) -> None:
    code, _, runner = run_menu(tmp_path, [*answers, "6"])
    assert code == 0 and runner.calls == []


def test_resume_shows_progress_then_asks_before_fetching(tmp_path: Path) -> None:
    code, _, runner = run_menu(tmp_path, ["2", "5", "S", "6"])
    assert runner.args == [["--resume"], ["--resume", "--live", "--max-pages", "5", "--nav-timeout", "60"]]


def test_resume_without_saved_range_fetches_nothing(tmp_path: Path) -> None:
    code, out, runner = run_menu(tmp_path, ["2", "6"], codes=[2])
    assert runner.args == [["--resume"]] and "use a opção 1" in out


def test_resume_cancelled_fetches_nothing(tmp_path: Path) -> None:
    _, _, runner = run_menu(tmp_path, ["2", "3", "N", "6"])
    assert runner.args == [["--resume"]]


@pytest.mark.parametrize("choice, args", [("3", ["--export", "csv"]), ("4", ["--export", "xlsx"]),
                                          ("5", ["--resume"])])
def test_export_and_progress_commands(tmp_path: Path, choice: str, args: list[str]) -> None:
    code, _, runner = run_menu(tmp_path, [choice, "6"])
    assert code == 0 and runner.args == [args]


@pytest.mark.parametrize("choice, fmt", [("3", "csv"), ("4", "xlsx")])
def test_failed_export_suggests_closing_the_file(tmp_path: Path, choice: str, fmt: str) -> None:
    _, out, _ = run_menu(tmp_path, [choice, "6"], codes=[1])
    assert f"Se data\\verified_records.{fmt} estiver aberto no Excel" in out
    assert "another process is writing" in out


def test_menu_shows_where_output_files_are_saved(tmp_path: Path) -> None:
    _, out, _ = run_menu(tmp_path, ["6"])
    data = (tmp_path / "data").resolve()
    assert "Onde os arquivos são salvos:" in out
    assert f"Banco de dados (fonte principal do progresso e dos registros): {data / 'honestpool.db'}" in out
    assert f"CSV (atualizado automaticamente após cada página concluída): {data / 'verified_records.csv'}" in out
    assert f"Excel XLSX (gerado somente pela opção 4): {data / 'verified_records.xlsx'}" in out
    assert f"Registro (log) desta sessão: {wl.session_log_path().resolve()}" in out
    assert "4) Exportar Excel (XLSX) - o XLSX só é gerado por esta opção" in out


def test_live_runs_and_exports_name_the_output_files(tmp_path: Path) -> None:
    _, out, runner = run_menu(tmp_path, ["1", "10", "12", "2", "S", "3", "4", "6"])
    data = (tmp_path / "data").resolve()
    assert [args[0] for args in runner.args] == ["--start-page", "--export", "--export"]
    assert (f"Registros verificados salvos no banco de dados {data / 'honestpool.db'}\n"
            f"e no CSV {data / 'verified_records.csv'} (atualizado após cada página concluída).\n"
            "Para gerar o Excel (XLSX), use a opção 4.") in out
    assert f"Exportar CSV para {data / 'verified_records.csv'}" in out
    assert f"Exportar XLSX para {data / 'verified_records.xlsx'}" in out


@pytest.mark.parametrize("choice", ["2", "3", "4", "5"])
def test_actions_needing_a_database_explain_when_it_is_missing(tmp_path: Path, choice: str) -> None:
    _, out, runner = run_menu(tmp_path, [choice, "6"], database=False)
    assert runner.calls == [] and "Ainda não existe banco de dados" in out


def test_exit_codes_are_explained(tmp_path: Path) -> None:
    _, out, _ = run_menu(tmp_path, ["1", "1", "1", "1", "S", "1", "1", "1", "1", "S", "6"], codes=[130, 3])
    assert "Interrompido com Ctrl+C" in out and "parou por segurança" in out


def test_ctrl_c_while_answering_returns_to_menu(tmp_path: Path) -> None:
    _, out, runner = run_menu(tmp_path, ["1", KeyboardInterrupt(), "6"])
    assert runner.calls == [] and "Operação cancelada; voltando ao menu." in out


def test_invalid_menu_option(tmp_path: Path) -> None:
    _, out, runner = run_menu(tmp_path, ["7", "x", "6"])
    assert out.count("Opção inválida") == 2 and runner.calls == []


def test_menu_stops_when_saved_key_cannot_be_decrypted(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "2captcha.cfg"
    config_path.parent.mkdir()
    config_path.write_text("CAPTCHA_API_KEY_DPAPI=T1RIRVI=\n", encoding="utf-8")
    con, out, _ = console(["1"])
    runner = FakeRunner()
    code = wl.Menu(con, db_path=tmp_path / "x.db", config_path=config_path, unprotect=fake_unprotect,
                   runner=runner).run()
    assert code == 1 and runner.calls == [] and "configurar_2captcha.bat" in out.getvalue()


def test_cli_help_and_unknown_command() -> None:
    root = wl.PROJECT_ROOT
    options = {"cwd": root, "capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace",
               "env": utf8_child_env()}
    ok = subprocess.run([sys.executable, "windows_launcher.py", "--help"], **options)
    assert ok.returncode == 0 and "configurar" in ok.stdout and "menu" in ok.stdout
    bad = subprocess.run([sys.executable, "windows_launcher.py", "scrape"], **options)
    assert bad.returncode == 2
