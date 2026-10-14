"""Build and verify the Windows 11 test package (developer tool; not part of the package).

    python packaging/build_windows_test_package.py build
    python packaging/build_windows_test_package.py verify "<folder the ZIP was extracted to>"

The package is built from an explicit list of files, never from the whole project folder, so
local settings, databases, exports, logs and browser data cannot end up in it. Files sit at the
ZIP root: extracting to C:\\HonestPool gives C:\\HonestPool\\setup_windows.bat. Detected values
that look like secrets are reported masked only.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_NAME = "honestpool-windows11-test-v3"
ZIP_PATH = ROOT / "delivery" / f"{PACKAGE_NAME}.zip"
MANIFEST = "MANIFEST.sha256"

FILES = (
    "START_HERE_WINDOWS.txt",
    "setup_windows.bat",
    "configurar_2captcha.bat",
    "iniciar_honestpool.bat",
    "windows_launcher.py",
    "INSTALACAO_WINDOWS_11.md",
    "CHECKLIST_ACEITACAO_WINDOWS_11.md",
    "main.py",
    "requirements.txt",
    "README.md",
    "pytest.ini",
    ".env.example",
    ".gitignore",
    ".gitattributes",
    "demo.py",
    "demo_import.py",
    "inspect_site.py",
    "data/.gitkeep",
    "data/sample_page.html",
    "data/sample_page_checked.html",
    "docs/CLIENT_GUIDE.md",
    "docs/CLIENT_ACCEPTANCE_TEST.md",
    "docs/RELEASE_NOTES.md",
    "docs/OPERATOR_REQUEST.md",
    "docs/PRODUCTION_READINESS.md",
    "docs/import_demonstration_output.txt",
    "examples/CONFIG_EXAMPLES.md",
    "examples/import/README.md",
    "examples/import/synthetic_verified_numbers.csv",
    "examples/import/synthetic_verified_numbers.txt",
    "scraper/browser.py",
    "scraper/captcha_provider.py",
    "scraper/two_captcha.py",
    "scraper/live_output.py",
    "scraper/exporter.py",
    "scraper/database.py",
    "tests/test_live_output.py",
    "tests/test_captcha_auto.py",
    "tests/test_captcha_attempt_logging.py",
    "tests/test_end_to_end_recovery.py",
)
PATTERNS = ("scraper/*.py", "tests/*.py", "tests/fixtures/import/*")

CRLF_FILES = ("setup_windows.bat", "configurar_2captcha.bat", "iniciar_honestpool.bat", "START_HERE_WINDOWS.txt")
SYNTHETIC_DATA_DIRS = ("examples/import", "tests/fixtures/import")
FORBIDDEN_DIRS = {"config", "logs", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".tools", ".git",
                  "delivery", "packaging"}
FORBIDDEN_SUFFIXES = (".db", ".db-wal", ".db-shm", ".db-journal", ".lock", ".sqlite", ".sqlite3", ".json", ".har",
                      ".log", ".png", ".jpg", ".jpeg", ".xlsx", ".cfg", ".pyc", ".zip")
FORBIDDEN_NAME_PARTS = ("verified_records", "network", "cookie", "profile", "local state", "secret", "credential")
DATA_SUFFIXES = (".csv",)

HEX_KEY = re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{32}(?![0-9A-Za-z])")
KNOWN_HEX = {hashlib.md5(b"").hexdigest(), "abcdef0123456789" * 2}
SECRET_PATTERNS = {
    "private key": re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
    "service account": re.compile(r'"type"\s*:\s*"service_account"|"private_key"\s*:'),
    "cookie header": re.compile(r"(?im)^\s*(?:set-)?cookie\s*:"),
    "session cookie": re.compile(r"(?:PHPSESSID|sessionid|cf_clearance|__cf_bm)=[0-9A-Za-z._-]{16,}"),
    "captcha key value": re.compile(r"(?m)^\s*(?:set\s+)?CAPTCHA_API_KEY\s*=\s*[0-9A-Za-z]{16,}"),
}


def package_files() -> list[str]:
    names = set(FILES)
    for pattern in PATTERNS:
        names.update(p.relative_to(ROOT).as_posix() for p in ROOT.glob(pattern) if p.is_file())
    return sorted(names)


def path_problems(names: list[str]) -> list[str]:
    problems = []
    for name in names:
        path = PurePosixPath(name)
        lower = name.lower()
        if path.name.startswith(".env") and path.name != ".env.example":
            problems.append(f"{name}: environment file")
        if FORBIDDEN_DIRS.intersection(path.parts[:-1]):
            problems.append(f"{name}: forbidden folder")
        if lower.endswith(FORBIDDEN_SUFFIXES):
            problems.append(f"{name}: forbidden file type")
        if any(part in path.name.lower() for part in FORBIDDEN_NAME_PARTS):
            problems.append(f"{name}: forbidden file name")
        if lower.endswith(DATA_SUFFIXES) and not name.startswith(SYNTHETIC_DATA_DIRS):
            problems.append(f"{name}: data export outside the synthetic examples")
    return problems


def mask(value: str) -> str:
    return f"{value[:3]}...{value[-2:]} ({len(value)} characters)"


def content_problems(base: Path, names: list[str]) -> list[str]:
    problems = []
    for name in names:
        text = (base / name).read_bytes().decode("utf-8", errors="replace")
        for token in sorted(set(HEX_KEY.findall(text)) - KNOWN_HEX):
            problems.append(f"{name}: possible API key {mask(token)}")
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                problems.append(f"{name}: {label}")
    return problems


def line_ending_problems(base: Path) -> list[str]:
    problems = []
    for name in CRLF_FILES:
        data = (base / name).read_bytes()
        if b"\n" in data.replace(b"\r\n", b"") or not data.endswith(b"\r\n"):
            problems.append(f"{name}: not CRLF only")
    return problems


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fail(title: str, problems: list[str]) -> int:
    print(f"FAILED: {title}")
    for problem in problems:
        print(f"  - {problem}")
    return 1


def build() -> int:
    if ZIP_PATH.exists():
        return fail("refusing to overwrite an existing package", [str(ZIP_PATH.relative_to(ROOT))])
    names = package_files()
    missing = [name for name in names if not (ROOT / name).is_file()]
    if missing:
        return fail("files missing from the project", missing)
    problems = path_problems(names) + content_problems(ROOT, names) + line_ending_problems(ROOT)
    if problems:
        return fail("pre-flight checks", problems)

    manifest = "".join(f"{sha256(ROOT / name)}  {name}\n" for name in names)
    partial = ZIP_PATH.with_name(ZIP_PATH.name + ".partial")
    with zipfile.ZipFile(partial, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in names:
            info = zipfile.ZipInfo.from_file(ROOT / name, name)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (ROOT / name).read_bytes(), compresslevel=9)
        archive.writestr(zipfile.ZipInfo(MANIFEST, date_time=time.localtime()[:6]), manifest,
                         compress_type=zipfile.ZIP_DEFLATED)
    partial.rename(ZIP_PATH)
    digest = sha256(ZIP_PATH)
    ZIP_PATH.with_name(ZIP_PATH.name + ".sha256").write_text(f"{digest}  {ZIP_PATH.name}\n", encoding="ascii")
    print(f"Created {ZIP_PATH.relative_to(ROOT)}: {len(names) + 1} files, {ZIP_PATH.stat().st_size} bytes")
    print(f"SHA-256 {digest}")
    return 0


def verify(base: Path) -> int:
    found = sorted(p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file())
    problems = [f"{name}: missing" for name in (*FILES, MANIFEST) if name not in found]
    listed = {}
    for line in (base / MANIFEST).read_text(encoding="ascii").splitlines():
        digest, name = line.split("  ", 1)
        listed[name] = digest
    problems += [f"{name}: checksum does not match" for name, digest in listed.items()
                 if not (base / name).is_file() or sha256(base / name) != digest]
    problems += [f"{name}: not in {MANIFEST}" for name in found if name != MANIFEST and name not in listed]
    contents = [name for name in found if name != MANIFEST]
    problems += path_problems(contents) + content_problems(base, contents) + line_ending_problems(base)
    if problems:
        return fail(f"verification of {base}", problems)
    print(f"OK: {len(found)} files, {len(listed)} checksums match, no forbidden files or secrets, CRLF scripts")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build")
    check = sub.add_parser("verify")
    check.add_argument("folder", type=Path)
    args = parser.parse_args(argv)
    return build() if args.command == "build" else verify(args.folder)


if __name__ == "__main__":
    sys.exit(main())
