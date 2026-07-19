"""Resolve the Python interpreter that actually executes Lean evidence."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

_DISTRIBUTION_PROBE = """
import hashlib
import importlib.metadata
import sys

tokens = []
for distribution in importlib.metadata.distributions():
    name = distribution.metadata.get("Name", "").casefold()
    version = distribution.version
    record = distribution.read_text("RECORD") or ""
    direct_url = distribution.read_text("direct_url.json") or ""
    metadata = distribution.read_text("METADATA") or ""
    binding = hashlib.sha256("|".join((record, direct_url, metadata)).encode()).hexdigest()
    tokens.append(f"{name}=={version}:sha256:{binding}")
payload = "|".join((sys.executable, sys.prefix, sys.base_prefix, *sorted(tokens)))
print("sha256:" + hashlib.sha256(payload.encode()).hexdigest())
"""


def _runner_python(executable: Path) -> Path:
    name = _command_name(executable)
    if name.startswith(("python", "pypy")):
        return (
            executable
            if _is_native_executable(executable)
            else _reported_python(executable)
        )
    for candidate in _runner_python_candidates(executable):
        if candidate.is_file():
            lexical = Path(os.path.abspath(candidate))
            return (
                lexical if _is_native_executable(lexical) else _reported_python(lexical)
            )
    raise ValueError(f"Python environment for runner is unavailable: {executable}")


def _is_native_executable(executable: Path) -> bool:
    try:
        prefix = executable.read_bytes()[:4]
    except OSError:
        return False
    return (
        prefix[:2] == b"MZ"
        or prefix == b"\x7fELF"
        or prefix
        in {
            b"\xca\xfe\xba\xbe",
            b"\xce\xfa\xed\xfe",
            b"\xcf\xfa\xed\xfe",
            b"\xfe\xed\xfa\xce",
            b"\xfe\xed\xfa\xcf",
        }
    )


def _reported_python(executable: Path) -> Path:
    environment = _sanitized_environment()
    try:
        completed = subprocess.run(
            [str(executable), "-I", "-c", "import sys; print(sys.executable)"],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=10,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"Python runner identity timed out: {executable}") from exc
    output = completed.stdout.strip().splitlines()
    candidate = Path(output[-1]) if output else Path()
    if completed.returncode or not candidate.is_absolute() or not candidate.is_file():
        raise ValueError(f"Python runner identity is unavailable: {executable}")
    lexical = Path(os.path.abspath(candidate))
    if not _is_native_executable(lexical):
        raise ValueError(f"Python runner identity is not executable: {executable}")
    return lexical


def _runner_python_candidates(executable: Path) -> tuple[Path, ...]:
    if os.name == "nt":
        return (
            executable.parent.parent / "python.exe",
            executable.parent / "python.exe",
        )
    try:
        first_line = executable.read_text("utf-8").splitlines()[0]
        parts = shlex.split(first_line.removeprefix("#!"))
    except (OSError, UnicodeError, IndexError, ValueError):
        return ()
    interpreter = _shebang_interpreter(parts)
    if interpreter and _command_name(interpreter).startswith(("python", "pypy")):
        located = shutil.which(interpreter)
        return (Path(located),) if located else (Path(interpreter),)
    return _path_python_candidates(executable)


def _shebang_interpreter(parts: list[str]) -> str:
    if not parts:
        return ""
    if Path(parts[0]).name != "env":
        return parts[0]
    arguments = parts[1:]
    if arguments[:1] == ["-S"]:
        arguments = arguments[1:]
    return arguments[0] if arguments else ""


def _path_python_candidates(executable: Path) -> tuple[Path, ...]:
    candidates = [executable.parent / "python", executable.parent / "python3"]
    for name in ("python", "python3", "pypy3"):
        located = shutil.which(name)
        if located:
            candidates.append(Path(located))
    return tuple(dict.fromkeys(candidates))


def _command_name(executable: str | Path) -> str:
    name = Path(executable).name.lower()
    return Path(name).stem if Path(name).suffix in {".bat", ".cmd", ".exe"} else name


def _probe_distributions(runner: Path) -> str:
    try:
        completed = subprocess.run(
            [str(runner), "-I", "-c", _DISTRIBUTION_PROBE],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_sanitized_environment(),
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"Runner dependency probe timed out: {runner}") from exc
    fingerprint = completed.stdout.strip().splitlines()[-1] if completed.stdout else ""
    if (
        completed.returncode
        or len(fingerprint) != 71
        or not fingerprint.startswith("sha256:")
    ):
        raise ValueError(f"Runner dependency environment is unavailable: {runner}")
    return fingerprint


def _sanitized_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.upper().startswith("PYTHON"):
            environment.pop(name)
    return environment


__all__: list[str] = []
