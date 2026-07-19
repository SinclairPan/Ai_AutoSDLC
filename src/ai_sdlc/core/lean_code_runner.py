"""Resolve the Python interpreter that actually executes Lean evidence."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

_DISTRIBUTION_PROBE = """
import hashlib
import importlib.metadata
import json
import sys

paths = json.loads(sys.argv[1])
tokens = []
for distribution in importlib.metadata.distributions(path=paths):
    name = distribution.metadata.get("Name", "").casefold()
    version = distribution.version
    record = distribution.read_text("RECORD") or ""
    direct_url = distribution.read_text("direct_url.json") or ""
    metadata = distribution.read_text("METADATA") or ""
    binding = hashlib.sha256("|".join((record, direct_url, metadata)).encode()).hexdigest()
    tokens.append(f"{name}=={version}:sha256:{binding}")
payload = "|".join((sys.executable, *paths, *sorted(tokens)))
print("sha256:" + hashlib.sha256(payload.encode()).hexdigest())
"""

_SITE_PACKAGES_PROBE = """
import json
import os
import sys
import sysconfig
from pathlib import Path

executable = Path(sys.executable)
environment = executable.parent.parent
if (environment / "pyvenv.cfg").is_file():
    if os.name == "nt":
        roots = [environment / "Lib" / "site-packages"]
    else:
        version = f"python{sys.version_info.major}.{sys.version_info.minor}"
        roots = [environment / "lib" / version / "site-packages"]
    config = {}
    for line in (environment / "pyvenv.cfg").read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            config[key.strip().casefold()] = value.strip().casefold()
    if config.get("include-system-site-packages") == "true":
        variables = {"base": sys.base_prefix, "platbase": sys.base_prefix}
        paths = sysconfig.get_paths(vars=variables)
        roots.extend(Path(paths[name]) for name in ("purelib", "platlib"))
else:
    paths = sysconfig.get_paths()
    roots = [Path(paths[name]) for name in ("purelib", "platlib")]
print(json.dumps(list(dict.fromkeys(str(root) for root in roots))))
"""

_IMPORT_PATHS_PROBE = """
import importlib.metadata
import importlib.util
import json
import sys
from pathlib import Path

paths = list(sys.path)
for name in importlib.metadata.packages_distributions():
    if not name.isidentifier():
        continue
    try:
        spec = importlib.util.find_spec(name)
    except Exception:
        continue
    if spec is None:
        continue
    locations = list(spec.submodule_search_locations or ())
    origin = getattr(spec, "origin", "") or ""
    if origin and origin not in {"built-in", "frozen"}:
        path = Path(origin)
        root = path.parent.parent if path.name.startswith("__init__.") else path.parent
        locations.append(str(root))
    for location in locations:
        path = Path(location)
        root = path.parent if path.name == name else path
        paths.append(str(root))
print(json.dumps(list(dict.fromkeys(paths))))
"""

_BASE_IMPORT_PATHS_PROBE = """
import json
import sys
print(json.dumps(sys.path))
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
    site_packages = _runner_site_packages(runner)
    try:
        completed = subprocess.run(
            [
                str(runner),
                "-I",
                "-S",
                "-c",
                _DISTRIBUTION_PROBE,
                json.dumps([str(path) for path in site_packages]),
            ],
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


def _runner_site_packages(runner: Path) -> tuple[Path, ...]:
    try:
        completed = subprocess.run(
            [str(runner), "-I", "-S", "-c", _SITE_PACKAGES_PROBE],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_sanitized_environment(),
            timeout=10,
        )
        raw = json.loads(completed.stdout.strip().splitlines()[-1])
        paths = tuple(Path(item) for item in raw)
    except (IndexError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"Runner import paths are unavailable: {runner}") from exc
    if (
        completed.returncode
        or not paths
        or any(not path.is_absolute() for path in paths)
    ):
        raise ValueError(f"Runner import paths are unavailable: {runner}")
    return tuple(dict.fromkeys(paths))


def _runner_import_paths(runner: Path, project_root: Path) -> tuple[Path, ...]:
    baseline = set(_probe_import_paths(runner, with_site=False))
    expanded = _probe_import_paths(runner, with_site=True)
    site_packages = set(_runner_site_packages(runner))
    paths = (
        path
        for path in expanded
        if path not in baseline
        and (
            path in site_packages
            or _inside_runner_environment(path, runner)
            or _outside_project(path, project_root)
        )
    )
    return tuple(dict.fromkeys(paths))


def _probe_import_paths(runner: Path, *, with_site: bool) -> tuple[Path, ...]:
    flags = ["-I"] if with_site else ["-I", "-S"]
    probe = _IMPORT_PATHS_PROBE if with_site else _BASE_IMPORT_PATHS_PROBE
    try:
        completed = subprocess.run(
            [str(runner), *flags, "-c", probe],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_sanitized_environment(),
            timeout=30,
        )
        raw = json.loads(completed.stdout.strip().splitlines()[-1])
        paths = tuple(Path(item) for item in raw if item)
    except (IndexError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"Runner import environment is unavailable: {runner}") from exc
    if completed.returncode or any(not path.is_absolute() for path in paths):
        raise ValueError(f"Runner import environment is unavailable: {runner}")
    return tuple(dict.fromkeys(paths))


def _outside_project(path: Path, project_root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(project_root.resolve())
    except (OSError, RuntimeError, ValueError):
        return True
    return False


def _inside_runner_environment(path: Path, runner: Path) -> bool:
    environment = runner.parent.parent
    if not (environment / "pyvenv.cfg").is_file():
        return False
    try:
        path.resolve(strict=False).relative_to(environment.resolve())
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def _sanitized_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.upper().startswith("PYTHON"):
            environment.pop(name)
    return environment


__all__: list[str] = []
