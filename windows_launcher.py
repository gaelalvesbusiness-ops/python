"""Windows 11 helper behind configurar_2captcha.bat and iniciar_honestpool.bat.

``configurar`` stores the 2Captcha settings in config/2captcha.cfg; the API key is encrypted with
Windows DPAPI, so only the same Windows user on the same PC can decrypt it. ``menu`` runs main.py
with those settings in the child environment, and only for commands that use them (live runs and
the status check); main.py removes the key from its environment before starting the browser.
Every menu action is a plain main.py command line: request pacing, retry limits, CAPTCHA limits
and database recovery are main.py's own defaults. The key is never printed, logged or put on a
command line, and any output that would contain it is redacted before it reaches the screen or
the log file. On Windows the launcher and every process it starts share a job object, so no
browser process can outlive a run or the launcher itself.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import codecs
import ctypes
import getpass
import os
import re
import subprocess
import sys
import time
import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import IO, Any

from scraper.captcha_provider import CaptchaConfigError, load_captcha_config
from scraper.config import (
    AUTO_CSV_NAME,
    CAPTCHA_API_KEY_ENV,
    CAPTCHA_PROVIDER_ENV,
    CAPTCHA_TIMEOUT_ENV,
    DB_PATH,
    TOTAL_PAGES,
    TWO_CAPTCHA_PROVIDER,
    XLSX_EXPORT_NAME,
)
from scraper.pagination import InvalidPageRangeError, validate_range

PROJECT_ROOT = Path(__file__).resolve().parent
MAIN_PY = PROJECT_ROOT / "main.py"
CONFIG_PATH = PROJECT_ROOT / "config" / "2captcha.cfg"
CONFIG_SHOWN = r"config\2captcha.cfg"
LOGS_DIR = PROJECT_ROOT / "logs"

KEY_FIELD = "CAPTCHA_API_KEY_DPAPI"
CAPTCHA_TIMEOUT_S = "120"
NAV_TIMEOUT_S = "60"
TYPICAL_KEY = re.compile(r"[0-9a-f]{32}")
REDACTED = "[CHAVE OCULTA]"
CHILD_ENV_FIXED = {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
CAPTCHA_ENV_NAMES = (CAPTCHA_PROVIDER_ENV, CAPTCHA_API_KEY_ENV, CAPTCHA_TIMEOUT_ENV)
CAPTCHA_COMMANDS = ("--live", "--captcha-status")
TREE_GRACE_S = 10.0

DPAPI_ENTROPY = b"HonestPool 2Captcha key v1"
DPAPI_DESCRIPTION = "HonestPool 2Captcha"
CRYPTPROTECT_UI_FORBIDDEN = 0x1

CONFIG_HEADER = """\
# HonestPool - configuracao 2Captcha (criada por configurar_2captcha.bat).
# A chave esta cifrada com o Windows DPAPI: so este usuario do Windows, neste PC, consegue usa-la.
# Nao envie este arquivo a ninguem. Para trocar a chave, execute configurar_2captcha.bat de novo.
"""

EXIT_MESSAGES = {
    0: "Concluído sem erros.",
    1: "O programa informou um erro (veja as mensagens acima). Se a mensagem disser 'another process is "
       "writing', feche a outra janela do HonestPool; logo depois de um travamento ou de fechar a janela "
       "pelo X, aguarde alguns segundos e tente de novo.",
    2: "O comando foi recusado pelo programa (veja as mensagens acima).",
    3: "A extração parou por segurança (veja o motivo acima). As páginas pendentes continuam salvas; "
       "verifique o motivo e use a opção 2 para retomar.",
    130: "Interrompido com Ctrl+C. A página atual voltou para pendente e os registros salvos foram "
         "mantidos; use a opção 2 para continuar.",
}


class KeyProtectionError(RuntimeError):
    """The key could not be encrypted or decrypted; messages never contain the key."""


class ConfigFileError(RuntimeError):
    """config/2captcha.cfg is unreadable or malformed; messages never contain the key."""


@dataclass(frozen=True)
class CaptchaSettings:
    provider: str = TWO_CAPTCHA_PROVIDER
    timeout_s: str = CAPTCHA_TIMEOUT_S
    api_key: str = field(default="", repr=False)


def number(value: int) -> str:
    return f"{value:,}".replace(",", ".")


# --- Windows DPAPI -------------------------------------------------------------------


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _load_dpapi() -> tuple[Any, Any]:
    if sys.platform != "win32":
        raise KeyProtectionError("a proteção da chave usa o Windows DPAPI e só funciona no Windows")
    from ctypes import wintypes

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    blob = ctypes.POINTER(_DataBlob)
    crypt32.CryptProtectData.argtypes = [blob, wintypes.LPCWSTR, blob, ctypes.c_void_p, ctypes.c_void_p,
                                         wintypes.DWORD, blob]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [blob, ctypes.c_void_p, blob, ctypes.c_void_p, ctypes.c_void_p,
                                           wintypes.DWORD, blob]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


def _blob(buffer: Any, size: int) -> _DataBlob:
    return _DataBlob(size, ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def _dpapi(data: bytes, *, encrypt: bool) -> bytes:
    crypt32, kernel32 = _load_dpapi()
    source = ctypes.create_string_buffer(data, len(data))
    entropy_buffer = ctypes.create_string_buffer(DPAPI_ENTROPY, len(DPAPI_ENTROPY))
    blob_in, entropy, blob_out = _blob(source, len(data)), _blob(entropy_buffer, len(DPAPI_ENTROPY)), _DataBlob()
    try:
        if encrypt:
            ok = crypt32.CryptProtectData(ctypes.byref(blob_in), DPAPI_DESCRIPTION, ctypes.byref(entropy),
                                          None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
        else:
            ok = crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, ctypes.byref(entropy),
                                            None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    finally:
        ctypes.memset(source, 0, len(data))
    if not ok:
        code = getattr(ctypes, "get_last_error", lambda: 0)()
        raise KeyProtectionError(f"o Windows DPAPI recusou a operação (código {code})")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def dpapi_protect(data: bytes) -> bytes:
    return _dpapi(data, encrypt=True)


def dpapi_unprotect(blob: bytes) -> bytes:
    return _dpapi(blob, encrypt=False)


# --- configuration file --------------------------------------------------------------


def save_settings(api_key: str, path: Path = CONFIG_PATH,
                  protect: Callable[[bytes], bytes] = dpapi_protect) -> None:
    encrypted = base64.b64encode(protect(api_key.encode("ascii"))).decode("ascii")
    text = (f"{CONFIG_HEADER}{CAPTCHA_PROVIDER_ENV}={TWO_CAPTCHA_PROVIDER}\n"
            f"{CAPTCHA_TIMEOUT_ENV}={CAPTCHA_TIMEOUT_S}\n{KEY_FIELD}={encrypted}\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_settings(path: Path = CONFIG_PATH,
                  unprotect: Callable[[bytes], bytes] = dpapi_unprotect) -> CaptchaSettings | None:
    """The saved settings, or None when 2Captcha was never configured."""
    if not path.exists():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigFileError(f"não foi possível ler {CONFIG_SHOWN} ({type(exc).__name__})") from None
    values: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            values[name.strip()] = value.strip()
    if not values.get(KEY_FIELD):
        raise ConfigFileError(f"{CONFIG_SHOWN} não contém a chave cifrada")
    try:
        encrypted = base64.b64decode(values[KEY_FIELD], validate=True)
    except (binascii.Error, ValueError):
        raise ConfigFileError(f"a chave cifrada em {CONFIG_SHOWN} está corrompida") from None
    try:
        api_key = unprotect(encrypted).decode("ascii")
    except UnicodeDecodeError:
        raise KeyProtectionError("a chave decifrada não é válida") from None
    return CaptchaSettings(values.get(CAPTCHA_PROVIDER_ENV, TWO_CAPTCHA_PROVIDER),
                           values.get(CAPTCHA_TIMEOUT_ENV, CAPTCHA_TIMEOUT_S), api_key)


def child_env(settings: CaptchaSettings | None, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """main.py's environment: the saved settings only, never CAPTCHA variables left in Windows."""
    env = {name: value for name, value in (os.environ if base is None else base).items()
           if name not in CAPTCHA_ENV_NAMES}
    env.update(CHILD_ENV_FIXED)
    if settings is not None:
        env[CAPTCHA_PROVIDER_ENV] = settings.provider
        env[CAPTCHA_TIMEOUT_ENV] = settings.timeout_s
        env[CAPTCHA_API_KEY_ENV] = settings.api_key
    return env


def uses_captcha_settings(args: Sequence[str]) -> bool:
    """Only live runs and the status check read the 2Captcha settings; exports and progress do not."""
    return any(arg in CAPTCHA_COMMANDS for arg in args)


# --- Windows job object: this launcher and every process it starts ---------------------

JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x0800
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MAX_LISTED_PROCESSES = 1024
LEFTOVER_EXIT_CODE = 1


class _BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class _ProcessIdList(ctypes.Structure):
    _fields_ = [("NumberOfAssignedProcesses", ctypes.c_uint32), ("NumberOfProcessIdsInList", ctypes.c_uint32),
                ("ProcessIdList", ctypes.c_size_t * MAX_LISTED_PROCESSES)]


def _load_kernel32() -> Any:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
        "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                       ctypes.c_void_p], wintypes.BOOL),
        "GetCurrentProcess": ([], wintypes.HANDLE),
        "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        "IsProcessInJob": ([wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL),
        "TerminateProcess": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
    }
    for name, (argtypes, restype) in signatures.items():
        function = getattr(kernel32, name)
        function.argtypes, function.restype = argtypes, restype
    return kernel32


class ProcessTree:
    """A Windows job object holding this launcher and, from then on, every process it starts.

    main.py, the Playwright driver, Chromium and Chromium's helper processes belong to the job
    from the moment they are created, so none can slip out of it. Windows ends all of them when
    this launcher ends, however it ends (crash, Task Manager, closing the window), and
    ``end_leftovers`` ends those still running after main.py itself has finished. Without a job
    (other systems, or if Windows refuses one) nothing changes: Playwright's driver still closes
    Chromium when main.py ends.
    """

    _shared: ProcessTree | None = None

    def __init__(self, kernel32: Any = None) -> None:
        self._kernel32 = kernel32
        self._job: Any = None

    @classmethod
    def shared(cls) -> ProcessTree:
        """The launcher's job, created on first use, before main.py is started for the first time."""
        if cls._shared is None:
            kernel32 = None
            if sys.platform == "win32":
                try:
                    kernel32 = _load_kernel32()
                except (OSError, AttributeError):
                    kernel32 = None
            cls._shared = cls(kernel32)
            cls._shared.join()
        return cls._shared

    @property
    def active(self) -> bool:
        return self._job is not None

    def join(self) -> bool:
        """Put this process in a new kill-on-close job; False (and no job) if Windows refuses."""
        kernel32 = self._kernel32
        if kernel32 is None or self._job is not None:
            return self._job is not None
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return False
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK
        if not (kernel32.SetInformationJobObject(job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits),
                                                 ctypes.sizeof(limits))
                and kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess())):
            kernel32.CloseHandle(job)
            return False
        self._job = job
        return True

    def others(self) -> list[int]:
        """Process ids in the job other than this launcher."""
        if self._job is None:
            return []
        listing = _ProcessIdList()
        if not self._kernel32.QueryInformationJobObject(self._job, JOB_OBJECT_BASIC_PROCESS_ID_LIST,
                                                        ctypes.byref(listing), ctypes.sizeof(listing), None):
            return []
        count = min(listing.NumberOfProcessIdsInList, MAX_LISTED_PROCESSES)
        own = os.getpid()
        return [int(pid) for pid in listing.ProcessIdList[:count] if int(pid) != own]

    def end_leftovers(self, grace_s: float = TREE_GRACE_S, *, sleep: Callable[[float], None] = time.sleep,
                      clock: Callable[[], float] = time.monotonic) -> int:
        """Give processes left by main.py ``grace_s`` to end by themselves, then end them; returns how many."""
        if self._job is None:
            return 0
        deadline = clock() + grace_s
        while self.others() and clock() < deadline:
            sleep(0.2)
        return sum(self._terminate(pid) for pid in self.others())

    def _terminate(self, pid: int) -> bool:
        kernel32 = self._kernel32
        handle = kernel32.OpenProcess(PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            in_job = ctypes.c_long(0)
            return bool(kernel32.IsProcessInJob(handle, self._job, ctypes.byref(in_job)) and in_job.value
                        and kernel32.TerminateProcess(handle, LEFTOVER_EXIT_CODE))
        finally:
            kernel32.CloseHandle(handle)


# --- running main.py -------------------------------------------------------------------


def session_log_path(now: datetime | None = None) -> Path:
    return LOGS_DIR / f"honestpool_{(now or datetime.now()):%Y%m%d}.log"


def redact(text: str, secret: str | None) -> str:
    return text.replace(secret, REDACTED) if secret else text


class SessionLog:
    """The daily log file; a log that cannot be written never stops the operation itself."""

    def __init__(self, path: Path, out: IO[str]) -> None:
        self.out = out
        self.file: IO[str] | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.file = open(path, "a", encoding="utf-8")
        except OSError as exc:
            self._warn(f"não foi possível abrir o registro {path.name} ({exc.strerror or type(exc).__name__})")

    def _warn(self, reason: str) -> None:
        self.out.write(f"[Aviso: {reason}; a operação continua sem registro.]\n")
        self.out.flush()

    def write(self, text: str) -> None:
        if self.file is None:
            return
        try:
            self.file.write(text)
            self.file.flush()
        except OSError as exc:
            self.close()
            self._warn(f"o registro não pode mais ser gravado ({exc.strerror or type(exc).__name__})")

    def close(self) -> None:
        if self.file is not None:
            try:
                self.file.close()
            except OSError:
                pass
            self.file = None


def run_main(args: Sequence[str], settings: CaptchaSettings | None, *, log_path: Path, out: IO[str],
             script: Path = MAIN_PY, process_tree: Callable[[], ProcessTree] = ProcessTree.shared) -> int:
    """Run ``python main.py args`` and copy its output, redacted, to ``out`` and ``log_path``.

    Ctrl+C reaches main.py directly (same console); this process keeps reading until main.py has
    saved its state and exited, so its final summary is never lost. If this process fails for any
    other reason, main.py is ended too, so no extraction keeps running unseen; the page it was on
    is then recovered on the next start like after a power cut. Browser processes still running
    after main.py has ended are ended as well.
    """
    secret = settings.api_key if settings else None
    log = SessionLog(log_path, out)

    def emit(text: str) -> None:
        text = redact(text, secret)
        out.write(text)
        out.flush()
        log.write(text)

    try:
        log.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} | python {script.name} {' '.join(args)} ===\n")
        env = child_env(settings if uses_captcha_settings(args) else None)
        tree = process_tree()
        process = subprocess.Popen([sys.executable, str(script), *args], cwd=PROJECT_ROOT,
                                   env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            code = _relay(process, emit)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            leftover = tree.end_leftovers()
        if leftover:
            emit(f"[{leftover} processo(s) do navegador ainda abertos depois do fim do programa foram encerrados.]\n")
        log.write(f"=== código de saída {code} ===\n")
    finally:
        log.close()
    return code


def _relay(process: subprocess.Popen[bytes], emit: Callable[[str], None]) -> int:
    assert process.stdout is not None
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    while True:
        try:
            while chunk := process.stdout.read1(65536):
                emit(decoder.decode(chunk))
            emit(decoder.decode(b"", final=True))
            return process.wait()
        except KeyboardInterrupt:
            emit("\n[Ctrl+C recebido: aguardando o programa salvar e encerrar...]\n")


# --- console helpers -------------------------------------------------------------------


class Console:
    def __init__(self, input_fn: Callable[[str], str] = input, out: IO[str] | None = None) -> None:
        self.input_fn = input_fn
        self.out = out or sys.stdout

    def say(self, text: str = "") -> None:
        self.out.write(text + "\n")
        self.out.flush()

    def ask(self, prompt: str) -> str:
        return self.input_fn(prompt).strip()

    def confirm(self, prompt: str) -> bool:
        while True:
            answer = self.ask(f"{prompt} (S/N): ").upper()
            if answer in ("S", "SIM"):
                return True
            if answer in ("N", "NAO", "NÃO"):
                return False
            self.say("Responda S (sim) ou N (não).")

    def ask_int(self, prompt: str, low: int, high: int) -> int | None:
        """A whole number in low..high; None when the answer is left empty."""
        while True:
            answer = self.ask(f"{prompt} ({number(low)} a {number(high)}; Enter vazio volta ao menu): ")
            if not answer:
                return None
            if answer.isdigit() and low <= int(answer) <= high:
                return int(answer)
            self.say(f"Valor inválido: digite um número inteiro de {number(low)} a {number(high)}, sem pontos.")


# --- configurar_2captcha ---------------------------------------------------------------


def configure(console: Console, *, path: Path = CONFIG_PATH, read_secret: Callable[[str], str] = getpass.getpass,
              protect: Callable[[bytes], bytes] = dpapi_protect,
              unprotect: Callable[[bytes], bytes] = dpapi_unprotect,
              runner: Callable[..., int] = run_main) -> int:
    say = console.say
    say("=" * 64)
    say(" HonestPool - configurar a chave 2Captcha")
    say("=" * 64)
    say("A chave fica somente neste computador, cifrada para o seu usuário do Windows.")
    say("Ela nunca aparece na tela, nunca é gravada nos registros (logs) e só é enviada ao")
    say("serviço 2Captcha quando um CAPTCHA precisa ser resolvido durante uma extração.")
    say("Não envie a chave a ninguém (nem ao desenvolvedor).")
    say()
    if path.exists() and not console.confirm("Já existe uma chave salva. Deseja substituí-la?"):
        say("A chave salva foi mantida.")
        return check_status(console, path=path, unprotect=unprotect, runner=runner)

    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            api_key = read_secret("Cole a chave da API 2Captcha e pressione Enter (nada aparece na tela): ").strip()
        except getpass.GetPassWarning:
            say("ERRO: esta janela não permite ocultar a digitação; nada foi salvo.")
            say("Abra configurar_2captcha.bat com dois cliques no Explorador de Arquivos.")
            return 1
    if not api_key:
        say("Nenhuma chave foi digitada; nada foi alterado.")
        return 1
    try:
        load_captcha_config({CAPTCHA_PROVIDER_ENV: TWO_CAPTCHA_PROVIDER, CAPTCHA_API_KEY_ENV: api_key})
    except CaptchaConfigError:
        say("ERRO: a chave contém espaços, acentos ou caracteres inválidos; nada foi salvo.")
        say("Copie a chave novamente no painel da 2Captcha (campo 'API Key') e tente de novo.")
        return 1
    say(f"Chave recebida ({len(api_key)} caracteres; o conteúdo não é mostrado).")
    if not TYPICAL_KEY.fullmatch(api_key) and not console.confirm(
            "Aviso: chaves 2Captcha normalmente têm 32 caracteres (números 0-9 e letras a-f). Salvar mesmo assim?"):
        say("Nada foi salvo.")
        return 1
    try:
        save_settings(api_key, path, protect)
    except (KeyProtectionError, OSError) as exc:
        say(f"ERRO: não foi possível salvar a chave: {exc}")
        return 1
    say(f"Chave salva e cifrada em {CONFIG_SHOWN}.")
    say(f"Configurado: {CAPTCHA_PROVIDER_ENV}={TWO_CAPTCHA_PROVIDER} e {CAPTCHA_TIMEOUT_ENV}={CAPTCHA_TIMEOUT_S}.")
    return check_status(console, path=path, unprotect=unprotect, runner=runner)


def check_status(console: Console, *, path: Path = CONFIG_PATH,
                 unprotect: Callable[[bytes], bytes] = dpapi_unprotect, runner: Callable[..., int] = run_main) -> int:
    """main.py --captcha-status with the saved settings; checks the configuration offline only."""
    settings = load_or_explain(console, path, unprotect)
    if settings is None:
        return 1
    console.say()
    console.say("Verificando a configuração (nenhum serviço é contatado):")
    code = runner(["--captcha-status"], settings, log_path=session_log_path(), out=console.out)
    console.say()
    if code == 0:
        console.say("CONFIGURAÇÃO OK: a chave está salva e será usada automaticamente pelo menu.")
    else:
        console.say("ERRO: a verificação falhou (veja a mensagem acima). Execute configurar_2captcha.bat de novo.")
    return code


def load_or_explain(console: Console, path: Path = CONFIG_PATH,
                    unprotect: Callable[[bytes], bytes] = dpapi_unprotect) -> CaptchaSettings | None:
    try:
        settings = load_settings(path, unprotect)
    except (ConfigFileError, KeyProtectionError) as exc:
        console.say(f"ERRO: a configuração 2Captcha não pode ser lida: {exc}.")
        console.say("Execute configurar_2captcha.bat para salvar a chave novamente.")
        return None
    if settings is None:
        console.say("A chave 2Captcha ainda não foi configurada. Execute configurar_2captcha.bat.")
    return settings


# --- iniciar_honestpool menu -----------------------------------------------------------

MENU = """\
  1) Iniciar extração supervisionada (escolher página inicial e final)
  2) Retomar extração interrompida
  3) Exportar CSV agora (durante a extração ele já é atualizado após cada página concluída)
  4) Exportar Excel (XLSX) - o XLSX só é gerado por esta opção
  5) Mostrar progresso
  6) Sair"""
CAPTCHA_MODE = {True: "configurado (chave oculta)",
                False: "não configurado - CAPTCHAs são resolvidos manualmente na janela do navegador"}


class Menu:
    def __init__(self, console: Console, *, db_path: Path = DB_PATH, config_path: Path = CONFIG_PATH,
                 unprotect: Callable[[bytes], bytes] = dpapi_unprotect,
                 runner: Callable[..., int] = run_main) -> None:
        self.console = console
        self.db_path = db_path
        self.config_path = config_path
        self.unprotect = unprotect
        self.runner = runner
        self.settings: CaptchaSettings | None = None

    def run(self) -> int:
        say = self.console.say
        if self.config_path.exists():
            self.settings = load_or_explain(self.console, self.config_path, self.unprotect)
            if self.settings is None:
                return 1
        actions = {"1": self.start_supervised, "2": self.resume, "3": lambda: self.export("csv"),
                   "4": lambda: self.export("xlsx"), "5": self.show_progress}
        while True:
            say()
            say("=" * 64)
            say(" HonestPool - menu principal")
            say("=" * 64)
            say(f" 2Captcha: {CAPTCHA_MODE[self.settings is not None]}")
            say(" Onde os arquivos são salvos:")
            for line in self.output_locations():
                say(f"   {line}")
            say(" Nenhuma extração começa sem a sua confirmação.")
            say()
            say(MENU)
            try:
                choice = self.console.ask("Escolha uma opção (1-6): ")
            except (EOFError, KeyboardInterrupt):
                say()
                return 0
            if choice == "6":
                say("Até logo.")
                return 0
            if choice not in actions:
                say("Opção inválida: digite um número de 1 a 6.")
                continue
            try:
                actions[choice]()
            except (EOFError, KeyboardInterrupt):
                say()
                say("Operação cancelada; voltando ao menu.")

    @property
    def csv_path(self) -> Path:
        return self.db_path.parent / AUTO_CSV_NAME

    @property
    def xlsx_path(self) -> Path:
        return self.db_path.parent / XLSX_EXPORT_NAME

    def output_locations(self) -> list[str]:
        return [
            f"Banco de dados (fonte principal do progresso e dos registros): {self.db_path.resolve()}",
            f"CSV (atualizado automaticamente após cada página concluída): {self.csv_path.resolve()}",
            f"Excel XLSX (gerado somente pela opção 4): {self.xlsx_path.resolve()}",
            f"Registro (log) desta sessão: {session_log_path().resolve()}",
        ]

    def execute(self, args: list[str]) -> int:
        code = self.runner(args, self.settings, log_path=session_log_path(), out=self.console.out)
        self.console.say()
        self.console.say(EXIT_MESSAGES.get(code, f"O programa terminou com o código {code}."))
        return code

    def execute_live(self, args: list[str]) -> None:
        self.execute(args)
        say = self.console.say
        say(f"Registros verificados salvos no banco de dados {self.db_path.resolve()}")
        say(f"e no CSV {self.csv_path.resolve()} (atualizado após cada página concluída).")
        say("Para gerar o Excel (XLSX), use a opção 4.")

    def has_database(self) -> bool:
        if self.db_path.exists():
            return True
        self.console.say("Ainda não existe banco de dados. Use a opção 1 para iniciar a primeira extração.")
        return False

    def ask_page_count(self, available: int) -> int | None:
        self.console.say("Quantas páginas buscar nesta sessão? Cada página pode levar 1 minuto ou mais, e a")
        self.console.say("janela do navegador deve ficar aberta durante toda a sessão.")
        return self.console.ask_int("Número de páginas", 1, available)

    def confirm_live(self) -> bool:
        say = self.console.say
        say("Durante a extração:")
        say("  - uma janela do Chromium abre; não a feche;")
        say("  - se aparecer um CAPTCHA que não for resolvido automaticamente, resolva-o nessa janela;")
        say("  - para parar com segurança, pressione Ctrl+C UMA vez nesta janela e aguarde o resumo.")
        return self.console.confirm("Iniciar agora?")

    def start_supervised(self) -> None:
        say, ask_int = self.console.say, self.console.ask_int
        say()
        say("Iniciar extração supervisionada")
        start = ask_int("Página inicial", 1, TOTAL_PAGES)
        if start is None:
            return
        end = ask_int("Página final", start, TOTAL_PAGES)
        if end is None:
            return
        try:
            validate_range(start, end)
        except InvalidPageRangeError as exc:
            say(f"Faixa inválida: {exc}")
            return
        size = end - start + 1
        pages = self.ask_page_count(size)
        if pages is None:
            return
        say()
        say(f"Resumo: páginas {number(start)} a {number(end)} ({number(size)} na faixa); "
            f"até {number(pages)} página(s) nesta sessão.")
        say("Esta faixa passa a ser a faixa salva para 'Retomar'. Páginas já concluídas nunca são")
        say("buscadas de novo e os registros já salvos nunca são apagados.")
        if not self.confirm_live():
            say("Cancelado; nada foi buscado.")
            return
        self.execute_live(["--start-page", str(start), "--end-page", str(end), "--live", "--max-pages", str(pages),
                           "--nav-timeout", NAV_TIMEOUT_S])

    def resume(self) -> None:
        say = self.console.say
        say()
        say("Retomar extração interrompida - situação da faixa salva (nada é buscado no site):")
        if not self.has_database():
            return
        if self.execute(["--resume"]) != 0:
            say("Não foi possível mostrar a faixa salva (veja a mensagem acima). Se nenhuma faixa foi")
            say("salva ainda, use a opção 1 para escolher as páginas.")
            return
        pages = self.ask_page_count(TOTAL_PAGES)
        if pages is None:
            return
        if not self.confirm_live():
            say("Cancelado; nada foi buscado.")
            return
        self.execute_live(["--resume", "--live", "--max-pages", str(pages), "--nav-timeout", NAV_TIMEOUT_S])

    def export(self, file_format: str) -> None:
        target = self.csv_path if file_format == "csv" else self.xlsx_path
        self.console.say()
        self.console.say(f"Exportar {file_format.upper()} para {target.resolve()}")
        self.console.say("(um arquivo anterior com o mesmo nome é substituído; o banco de dados não é alterado):")
        if self.has_database() and self.execute(["--export", file_format]) != 0:
            self.console.say(f"Se data\\{target.name} estiver aberto no Excel ou em outro "
                             "programa, feche-o e tente exportar de novo.")

    def show_progress(self) -> None:
        self.console.say()
        self.console.say("Progresso da faixa salva (nada é buscado no site):")
        if self.has_database():
            self.execute(["--resume"])


# --- command line ----------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="windows_launcher.py",
        description="Windows helper behind configurar_2captcha.bat and iniciar_honestpool.bat.")
    parser.add_argument("command", choices=("configurar", "status", "menu"),
                        help="configurar: save the 2Captcha key; status: check it offline; menu: interactive menu")
    return parser


def tolerate_unprintable_output() -> None:
    """A character the console cannot show becomes '?' instead of stopping the menu."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    tolerate_unprintable_output()
    console = Console()
    try:
        if args.command == "configurar":
            return configure(console)
        if args.command == "status":
            return check_status(console)
        return Menu(console).run()
    except (EOFError, KeyboardInterrupt):
        console.say()
        console.say("Cancelado.")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
