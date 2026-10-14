"""Static checks of the Windows .bat launchers and their Portuguese guide.

cmd.exe is not available on the development machine, so these tests check structure and content
only; running the scripts on Windows 11 is a separate, manual validation step.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SETUP, CONFIGURE, START = "setup_windows.bat", "configurar_2captcha.bat", "iniciar_honestpool.bat"
SCRIPTS = (SETUP, CONFIGURE, START)
GUIDE = ROOT / "INSTALACAO_WINDOWS_11.md"
CHECKLIST = ROOT / "CHECKLIST_ACEITACAO_WINDOWS_11.md"
SCRAPING_OVERRIDES = ("--headless", "--delay", "--jitter", "--retries", "--max-attempts", "--captcha-wait")


def raw(name: str) -> bytes:
    return (ROOT / name).read_bytes()


def lines(name: str) -> list[str]:
    return raw(name).decode("ascii").split("\r\n")


def code_lines(name: str) -> list[str]:
    return [line.strip() for line in lines(name) if line.strip() and not line.strip().lower().startswith("rem ")]


def labels(name: str) -> set[str]:
    return {line[1:].lower() for line in code_lines(name) if line.startswith(":") and not line.startswith("::")}


@pytest.mark.parametrize("name", SCRIPTS)
def test_crlf_line_endings_and_ascii_only(name: str) -> None:
    data = raw(name)
    data.decode("ascii")
    assert b"\n" not in data.replace(b"\r\n", b""), "every line ends with CRLF"
    assert data.endswith(b"\r\n")


@pytest.mark.parametrize("name", SCRIPTS)
def test_standard_header(name: str) -> None:
    code = code_lines(name)
    assert lines(name)[0] == "@echo off"
    assert "setlocal EnableExtensions" in code
    assert 'pushd "%~dp0"' in code, "always runs from the program folder, even when started elsewhere or from UNC"
    assert 'set "VENV_PY=.venv\\Scripts\\python.exe"' in code
    assert 'set "SETUP_MARKER=.venv\\honestpool_setup_ok.txt"' in code


@pytest.mark.parametrize("name", SCRIPTS)
def test_every_exit_pauses_first(name: str) -> None:
    code = code_lines(name)
    exits = [i for i, line in enumerate(code) if line.lower().startswith("exit")]
    assert exits, "the script ends through explicit exits"
    for i in exits:
        assert code[i - 1].lower() == "pause", f"no pause before line {code[i]!r}"
    assert code[-1].lower().startswith("exit /b"), "nothing falls through past the last exit"


@pytest.mark.parametrize("name", SCRIPTS)
def test_goto_and_call_targets_exist(name: str) -> None:
    known = labels(name)
    for line in code_lines(name):
        for target in re.findall(r"\b(?:goto|call)\s+:(\w+)", line, flags=re.IGNORECASE):
            assert target.lower() == "eof" or target.lower() in known, f"missing label :{target}"


@pytest.mark.parametrize("name", SCRIPTS)
def test_no_parenthesized_blocks(name: str) -> None:
    """A folder such as 'honestpool (1)' breaks %CD% or %~dp0 inside ( ... ) blocks; none are used."""
    for line in code_lines(name):
        assert not line.endswith("(") and not line.startswith(")"), line


@pytest.mark.parametrize("name", SCRIPTS)
def test_echo_lines_have_no_shell_operators(name: str) -> None:
    for line in code_lines(name):
        if line.lower().startswith("echo ") and "| find" not in line:
            assert not set(line) & set("<>|&"), line


@pytest.mark.parametrize("name", SCRIPTS)
def test_no_secrets_env_file_or_scraping_overrides(name: str) -> None:
    text = raw(name).decode("ascii")
    assert "CAPTCHA_API_KEY" not in text and ".env" not in text
    assert "set /p" not in text.lower(), "nothing is typed into cmd.exe; the key is read hidden by Python"
    for flag in SCRAPING_OVERRIDES:
        assert flag not in text


@pytest.mark.parametrize("name", SCRIPTS)
def test_files_called_by_the_scripts_exist(name: str) -> None:
    text = raw(name).decode("ascii")
    for target in ("main.py", "windows_launcher.py", "requirements.txt"):
        if target in text:
            assert (ROOT / target).is_file()


def test_setup_steps() -> None:
    text = raw(SETUP).decode("ascii")
    code = code_lines(SETUP)
    assert "for %%V in (3.12 3.11) do call :try_py_launcher %%V" in code
    assert "py -%1 -c \"import sys\" >nul 2>&1" in code
    assert '%PYTHON_CMD% -m venv .venv' in code
    assert '"%VENV_PY%" -m pip install -r requirements.txt' in code
    assert '"%VENV_PY%" -m playwright install chromium' in code
    for folder in ("data", "logs", "config"):
        assert f'if not exist "{folder}" mkdir "{folder}"' in code
    assert '"%VENV_PY%" main.py' in code
    assert "INSTALACAO CONCLUIDA COM SUCESSO" in text and "A INSTALACAO NAO FOI CONCLUIDA" in text
    assert "if errorlevel 1 call :long_path_warning" in code
    assert "python.org" in text and "Add python.exe to PATH" in text
    assert "--live" not in text and "windows_launcher.py menu" not in text
    assert "windows_launcher.py configurar" not in text, "setup never asks for the key"


def test_setup_marker_is_written_only_after_every_check() -> None:
    code = code_lines(SETUP)
    removed = code.index('if exist "%SETUP_MARKER%" del "%SETUP_MARKER%"')
    written = code.index('> "%SETUP_MARKER%" echo Instalacao do HonestPool concluida.')
    checks = [i for i, line in enumerate(code) if line.startswith('"%VENV_PY%"') or line.startswith("%PYTHON_CMD%")]
    assert removed < min(i for i in checks if code[i].startswith('"%VENV_PY%"'))
    assert written > max(checks)
    assert code[written + 1] == 'if not exist "%SETUP_MARKER%" goto :check_failed'
    assert code[written + 2:].index("goto :end_ok") < code[written + 2:].index(":zip_not_extracted")


@pytest.mark.parametrize("name", [CONFIGURE, START])
def test_launchers_refuse_an_incomplete_installation(name: str) -> None:
    code = code_lines(name)
    checks = ['if not exist "windows_launcher.py" goto :missing_files', 'if not exist "%VENV_PY%" goto :not_installed',
              'if not exist "%SETUP_MARKER%" goto :setup_incomplete']
    positions = [code.index(check) for check in checks]
    launch = next(i for i, line in enumerate(code) if "windows_launcher.py" in line and line.startswith('"'))
    assert positions == sorted(positions) and positions[-1] < launch
    assert "INSTALACAO CONCLUIDA COM SUCESSO" in raw(name).decode("ascii")


@pytest.mark.parametrize("failure", ["zip_not_extracted", "missing_files", "no_python", "venv_failed", "venv_bad",
                                     "pip_failed", "chromium_failed", "folders_failed", "check_failed"])
def test_setup_failures_end_with_the_failure_banner(failure: str) -> None:
    code = code_lines(SETUP)
    start = code.index(f":{failure}")
    block = code[start + 1:]
    end = next(i for i, line in enumerate(block) if line.startswith(":"))
    assert block[end - 1] == "goto :end_fail"
    assert any(line.startswith("echo ERRO:") for line in block[:end])


def setup_snippet(variable: str) -> str:
    prefix = f'set "{variable}='
    line = next(line for line in code_lines(SETUP) if line.startswith(prefix))
    return line[len(prefix):-1]


@pytest.mark.parametrize("version, expected", [((3, 11), 0), ((3, 12), 0), ((3, 13), 1), ((3, 10), 1)])
def test_setup_python_version_check(version: tuple[int, int], expected: int) -> None:
    snippet = setup_snippet("PY_VERSION_CHECK")
    patched = snippet.replace("sys.version_info[:2]", repr(version))
    assert subprocess.run([sys.executable, "-c", patched]).returncode == expected
    assert subprocess.run([sys.executable, "-c", snippet]).returncode == (
        0 if sys.version_info[:2] in ((3, 11), (3, 12)) else 1)


@pytest.mark.parametrize("length, expected", [(20, 0), (100, 0), (101, 1), (180, 1)])
def test_setup_path_length_check(tmp_path: Path, length: int, expected: int) -> None:
    folder = tmp_path / "x"
    folder.mkdir()
    snippet = setup_snippet("PATH_LENGTH_CHECK").replace("len(os.getcwd())", str(length))
    assert subprocess.run([sys.executable, "-c", snippet], cwd=folder).returncode == expected


# --- real cmd.exe runs (Windows only; they never install anything or open the real project) ------

pytest_windows = pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe exists only on Windows")


def run_bat(script: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Start the script like Explorer does, from another folder; the newline answers every pause."""
    return subprocess.run(f'cmd.exe /d /s /c ""{script}""', cwd=cwd, input="\n\n\n", capture_output=True,
                          text=True, encoding="ascii", errors="replace", timeout=120)


@pytest.fixture
def odd_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "Pasta do Cliente (1)" / "honestpool & teste"
    folder.mkdir(parents=True)
    return folder


@pytest_windows
@pytest.mark.parametrize("name", [CONFIGURE, START])
def test_cmd_launchers_without_installation(odd_folder: Path, name: str) -> None:
    (odd_folder / name).write_bytes(raw(name))
    (odd_folder / "windows_launcher.py").write_text("raise SystemExit('must not run')\n", encoding="utf-8")
    done = run_bat(odd_folder / name, cwd=odd_folder.parent.parent)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "ERRO: o programa ainda nao foi instalado" in done.stdout


@pytest_windows
@pytest.mark.parametrize("name", [CONFIGURE, START])
def test_cmd_launchers_with_incomplete_installation(odd_folder: Path, name: str) -> None:
    (odd_folder / name).write_bytes(raw(name))
    (odd_folder / "windows_launcher.py").write_text("raise SystemExit('must not run')\n", encoding="utf-8")
    scripts = odd_folder / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_bytes(b"")
    done = run_bat(odd_folder / name, cwd=odd_folder.parent.parent)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "ERRO: a instalacao nao foi concluida" in done.stdout


@pytest_windows
def test_cmd_setup_refuses_an_unextracted_or_incomplete_folder(odd_folder: Path) -> None:
    (odd_folder / SETUP).write_bytes(raw(SETUP))
    done = run_bat(odd_folder / SETUP, cwd=odd_folder.parent.parent)
    assert done.returncode == 1, done.stdout + done.stderr
    in_temp = "\\appdata\\local\\temp" in str(odd_folder).lower()
    expected = "foi aberto de dentro do ZIP" if in_temp else "arquivos do programa nao foram encontrados"
    assert expected in done.stdout and "A INSTALACAO NAO FOI CONCLUIDA" in done.stdout
    assert not (odd_folder / ".venv").exists(), "nothing is installed when the checks fail"


def test_configure_script_delegates_to_hidden_prompt() -> None:
    code = code_lines(CONFIGURE)
    assert '"%VENV_PY%" windows_launcher.py configurar' in code
    assert "--live" not in raw(CONFIGURE).decode("ascii")


def test_start_script_only_opens_the_menu() -> None:
    code = code_lines(START)
    assert '"%VENV_PY%" windows_launcher.py menu' in code
    assert "--live" not in raw(START).decode("ascii")


def test_installation_guide() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    for name in (*SCRIPTS, "config\\2captcha.cfg", "Python 3.12", "Add python.exe to PATH", "Ctrl+C",
                 CHECKLIST.name):
        assert name in text, name
    for option in ("Iniciar extração supervisionada", "Retomar extração interrompida", "Exportar CSV",
                   "Exportar Excel", "Mostrar progresso", "Sair"):
        assert option in text, option
    assert "ainda não foram executados no Windows" in text
    assert "Validação necessária no Windows 11" in text
    assert not re.search(r"\b[0-9a-f]{32}\b", text), "no key-like value in the guide"


def test_installation_guide_short_workflow_comes_first() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    steps = ["Extraia o ZIP", SETUP, CONFIGURE, START, "teste pequeno", "Exporte"]
    quick = text[:text.index("## 1.")]
    positions = [quick.find(step) for step in steps]
    assert -1 not in positions and positions == sorted(positions), positions


def test_acceptance_checklist() -> None:
    text = CHECKLIST.read_text(encoding="utf-8")
    for topic in ("Instalação", "Configuração do 2Captcha", "Salvamento do progresso", "Retomada", "Exportação",
                  *SCRIPTS, "Ctrl+C", "config\\2captcha.cfg", "honestpool_AAAAMMDD.log"):
        assert topic in text, topic
    assert "não é garantid" in text, "automatic CAPTCHA solving and long runs are not promised"
    assert "ainda não foram executados no Windows" in text
    assert not re.search(r"\b[0-9a-f]{32}\b", text)
    items = re.findall(r"^\| (\d+) \|", text, flags=re.MULTILINE)
    assert items == [str(i) for i in range(1, len(items) + 1)] and len(items) >= 20


def test_start_here_file_for_notepad() -> None:
    data = (ROOT / "START_HERE_WINDOWS.txt").read_bytes()
    assert data.startswith(b"\xef\xbb\xbf"), "UTF-8 with BOM, so Notepad always shows the accents"
    assert b"\n" not in data.replace(b"\r\n", b"") and data.endswith(b"\r\n")
    text = data[3:].decode("utf-8")
    steps = ["PASSO 1 - Extraia o ZIP para uma pasta NOVA: C:\\HonestPool-v3", f"PASSO 2 - Dê dois cliques em {SETUP}",
             f"PASSO 3 - Dê dois cliques em {CONFIGURE}", f"PASSO 4 - Dê dois cliques em {START}",
             "PASSO 5 - Faça um teste pequeno e supervisionado", "PASSO 6 - Envie os resultados"]
    positions = [text.find(step) for step in steps]
    assert -1 not in positions and positions == sorted(positions), positions
    assert text.find("Python 3.11 ou 3.12 é OBRIGATÓRIO") < positions[0]
    assert text.find("INTERNET é OBRIGATÓRIA") < positions[0]
    for required in ("C:\\HonestPool-v3\\setup_windows.bat", "data, logs e config", CHECKLIST.name, "não é garantida",
                     "NUNCA envie", "INSTALACAO CONCLUIDA COM SUCESSO"):
        assert required in text, required
    assert "ainda não foi executado num computador com Windows" in text
    assert not re.search(r"\b[0-9a-f]{32}\b", text) and "CAPTCHA_API_KEY" not in text


def test_start_here_keeps_the_existing_installation() -> None:
    text = (ROOT / "START_HERE_WINDOWS.txt").read_bytes()[3:].decode("utf-8")
    update = text[text.index("MANTENHA A SUA INSTALAÇÃO ATUAL E O SEU BANCO DE DADOS"):text.index("PASSO 1 - ")]
    for required in ("Feche o programa antigo", "cópia de segurança", "C:\\HonestPool\\data", "C:\\HonestPool\\config",
                     "NÃO extraia a v3 dentro de C:\\HonestPool nem de C:\\HonestPool-v2", "banco de dados vazio",
                     "Nada é migrado", "Não copie o banco de dados antigo para a v3", "seção 10"):
        assert required in update, required
    for required in ("Configure a chave de novo", "SÓ 2 páginas", "página final 2", "new verified record(s) saved",
                     "CSV updated: C:\\HonestPool-v3\\data\\verified_records.csv",
                     "Verification succeeded automatically", "use a opção 4", "nunca a envie"):
        assert required in text, required


def test_start_here_presents_v3_as_a_diagnostic_test() -> None:
    text = (ROOT / "START_HERE_WINDOWS.txt").read_bytes()[3:].decode("utf-8")
    intro = text[:text.index("ANTES DE COMEÇAR")]
    for required in ("honestpool-windows11-test-v3", "VERSÃO DE DIAGNÓSTICO", "NÃO é uma",
                     "solução garantida para rodar sozinha", "CAPTCHA summary:", "42 imagens"):
        assert required in intro, required
    for required in ("CAPTCHA 1 attempt 1/3:", "C:\\HonestPool-v3\\logs", "honestpool_AAAAMMDD.log",
                     "[CHAVE OCULTA]", "as respostas do", "Não edite o arquivo"):
        assert required in text, required


def test_installation_guide_explains_the_diagnostic_log() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    assert "honestpool-windows11-test-v3" in text and "versão de diagnóstico" in text
    assert "não é uma solução garantida para rodar sozinha" in text
    log = text[text.index("## 11. Encontrar e enviar o registro (log) do teste"):]
    for required in ("C:\\HonestPool-v3\\logs\\honestpool_AAAAMMDD.log", "CAPTCHA summary", "[CHAVE OCULTA]",
                     "Não** contém as respostas do CAPTCHA", "como está"):
        assert required in log, required
    for line in ("image not found; nothing sent to 2Captcha", "website rejection confirmed",
                 "verification outcome uncertain", "manual fallback"):
        assert line in text, line
    assert "Não faça isto durante o teste de diagnóstico" in text and "42 imagens" in text


@pytest.mark.parametrize("doc", [GUIDE.name, CHECKLIST.name])
def test_requirements_are_emphasized(doc: str) -> None:
    text = (ROOT / doc).read_text(encoding="utf-8")
    required = r"Python 3\.11 ou 3\.12\**\s+\(obrigatório|\*\*obrigatório\*\* ter o \*\*Python 3\.11 ou 3\.12"
    assert re.search(required, text)
    assert "internet" in text.lower() and "durante a instalação" in text and "extraç" in text


def test_setup_creates_the_local_folders() -> None:
    code = code_lines(SETUP)
    for folder in ("data", "logs", "config"):
        assert f'if not exist "{folder}" mkdir "{folder}"' in code
        assert f'if not exist "{folder}" goto :folders_failed' in code


@pytest.mark.parametrize("doc", ["README.md", "docs/CLIENT_GUIDE.md", "docs/CLIENT_ACCEPTANCE_TEST.md"])
def test_docs_point_windows_users_to_the_installer(doc: str) -> None:
    text = (ROOT / doc).read_text(encoding="utf-8")
    assert "INSTALACAO_WINDOWS_11.md" in text and "configurar_2captcha.bat" in text
    assert "567" not in text, "the old test count is gone"
