"""Project-path and execution-environment bindings for Lean receipts."""

from __future__ import annotations

import hashlib
import importlib.metadata
import os
import platform
import shutil
import sys
from pathlib import Path

from ai_sdlc.core.lean_code_runner import _probe_distributions, _runner_python


def safe_project_path(root: Path, reference: str) -> Path:
    """Resolve a reference inside the project boundary."""

    path = (root / reference).resolve()
    path.relative_to(root.resolve())
    return path


def payload_digest(payload: bytes) -> str:
    """Return the canonical receipt digest format."""

    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def optional_file_digest(root: Path, reference: str) -> str:
    """Digest an optional project-local file reference."""

    return (
        payload_digest(safe_project_path(root, reference).read_bytes())
        if reference
        else ""
    )


_PYTHON_MODULE_RUNNERS = {"py_compile", "pytest"}
_TARGET_RUNNERS = {"py.test", "pytest"}
_RUNNER_VALUE_OPTIONS = {
    "-c",
    "-k",
    "--basetemp",
    "--confcutdir",
    "--deselect",
    "--ignore",
    "--ignore-glob",
    "--rootdir",
}
_PYTEST_SAFE_FLAGS = {
    "-q",
    "--quiet",
    "-s",
    "--disable-warnings",
    "--strict-config",
    "--strict-markers",
    "-v",
    "--verbose",
    "-vv",
    "-x",
    "--exitfirst",
}
_PYTEST_SAFE_PREFIXES = ("--capture=", "--color=", "--maxfail=", "--tb=")
_PYTEST_OVERRIDE = ("-o", "addopts=")


def resolve_execution_adapter(
    root: Path,
    argv: tuple[str, ...],
    reference: str,
) -> str:
    """Recognize command shapes that consume the declared test source."""

    targets = _target_indexes(root, argv, reference)
    if not targets:
        return ""
    executable = _command_name(argv[0])
    if executable.startswith(("python", "pypy")):
        return _python_adapter(argv, targets)
    if executable in _TARGET_RUNNERS and _pytest_target(argv, targets, 1):
        return f"target-runner:{executable}"
    return ""


def _target_indexes(root: Path, argv: tuple[str, ...], reference: str) -> set[int]:
    expected = _lexical_project_path(root, reference)
    indexes: set[int] = set()
    for index, item in enumerate(argv[1:], start=1):
        target = item.replace("\\", "/").split("::", 1)[0]
        resolved = _lexical_project_path(root, target)
        if resolved == expected:
            indexes.add(index)
    return indexes


def _lexical_project_path(root: Path, reference: str) -> str:
    candidate = Path(reference)
    path = candidate if candidate.is_absolute() else root / candidate
    return os.path.normcase(os.path.abspath(path))


def _python_adapter(argv: tuple[str, ...], targets: set[int]) -> str:
    if 1 in targets:
        return "python-script"
    if len(argv) > 3 and argv[1] == "-m":
        module = argv[2].lower()
        valid_target = (
            _pytest_target(argv, targets, 3)
            if module == "pytest"
            else _positional_target(argv, targets, 3)
        )
        if module in _PYTHON_MODULE_RUNNERS and valid_target:
            return f"python-module:{module}"
    return ""


def _pytest_target(argv: tuple[str, ...], targets: set[int], start: int) -> bool:
    target_indexes = {index for index in targets if index >= start}
    if not target_indexes or any(
        argv[index].startswith("-") for index in target_indexes
    ):
        return False
    index = start
    while index < len(argv):
        item = argv[index]
        if index in targets or item in _PYTEST_SAFE_FLAGS:
            index += 1
            continue
        if item.startswith(_PYTEST_SAFE_PREFIXES):
            index += 1
            continue
        if tuple(argv[index : index + 2]) == _PYTEST_OVERRIDE:
            index += 2
            continue
        return False
    return sum(index in targets for index in range(start, len(argv))) == 1


def effective_command_argv(
    adapter: str,
    argv: tuple[str, ...],
    root: Path | None = None,
) -> tuple[str, ...]:
    """Execute through the selected Python and neutralize implicit pytest addopts."""

    executable = _resolved_executable((root or Path.cwd()).resolve(), argv[0])
    runner = _runner_python(executable)
    command = (
        (str(runner), "-m", "pytest", *argv[1:])
        if adapter.startswith("target-runner:")
        else (str(runner), *argv[1:])
    )
    return (*command, *_PYTEST_OVERRIDE) if "pytest" in adapter else command


def controlled_execution_environment(
    adapter: str,
    execution_root: Path | None = None,
    project_root: Path | None = None,
) -> dict[str, str]:
    """Bind Python imports to the selected source view and normalize runner inputs."""

    execution_root = (execution_root or Path.cwd()).resolve()
    environment = os.environ.copy()
    inherited_python_path = environment.get("PYTHONPATH", "")
    for name in tuple(environment):
        if name.upper().startswith("PYTHON"):
            environment.pop(name)
    environment["PYTHONPATH"] = _selected_python_path(
        inherited_python_path,
        (project_root or execution_root).resolve(),
        execution_root,
    )
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    if "pytest" in adapter:
        environment["PYTEST_ADDOPTS"] = ""
        environment["PYTEST_PLUGINS"] = ""
    return environment


def _selected_python_path(
    inherited: str,
    project_root: Path,
    execution_root: Path,
) -> str:
    selected = [
        execution_root / item for item in _project_python_path(inherited, project_root)
    ]
    return os.pathsep.join(dict.fromkeys(str(item) for item in selected))


def _project_python_path(inherited: str, project_root: Path) -> list[Path]:
    project_root = project_root.resolve()
    selected: list[Path] = []
    for raw in inherited.split(os.pathsep):
        if not raw:
            continue
        source = Path(raw) if Path(raw).is_absolute() else project_root / raw
        try:
            selected.append(source.resolve().relative_to(project_root))
        except ValueError:
            continue
    selected.append(Path("."))
    return list(dict.fromkeys(selected))


def _positional_target(
    argv: tuple[str, ...],
    targets: set[int],
    start: int,
) -> bool:
    for index in sorted(targets):
        if index < start:
            continue
        previous = argv[index - 1] if index else ""
        if previous in _RUNNER_VALUE_OPTIONS:
            continue
        prefix = argv[start:index]
        if "--" in prefix or not any(not item.startswith("-") for item in prefix):
            return True
    return False


def execution_toolchain(root: Path, command: str) -> tuple[str, str, str, str]:
    """Fingerprint the executable bytes and common dependency lock inputs."""

    executable = _resolved_executable(root, command)
    resolved = str(executable)
    executable_digest = (
        payload_digest(executable.read_bytes()) if executable.is_file() else ""
    )
    environment = _environment_fingerprint(root, executable)
    payload = "|".join(
        (platform.platform(), sys.version, resolved, executable_digest, environment)
    )
    return (
        payload_digest(payload.encode("utf-8")),
        resolved,
        executable_digest,
        environment,
    )


def _environment_fingerprint(root: Path, executable: Path) -> str:
    root = root.resolve()
    names = (
        "uv.lock",
        "poetry.lock",
        "requirements.txt",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "pytest.ini",
        "pyproject.toml",
        "setup.cfg",
        "tox.ini",
    )
    tokens = [
        f"{name}:{payload_digest((root / name).read_bytes())}"
        for name in names
        if (root / name).is_file()
    ]
    project_path = ",".join(
        item.as_posix()
        for item in _project_python_path(os.environ.get("PYTHONPATH", ""), root)
    )
    tokens.append(f"pythonpath:{project_path}")
    tokens.append(f"installed:{_installed_distributions_fingerprint(executable)}")
    return payload_digest("|".join(tokens).encode("utf-8"))


def _installed_distributions_fingerprint(executable: Path) -> str:
    runner = _runner_python(executable)
    if os.path.normcase(os.path.abspath(runner)) != os.path.normcase(
        os.path.abspath(sys.executable)
    ):
        return _probe_distributions(runner)
    tokens = sorted(
        _distribution_token(item) for item in importlib.metadata.distributions()
    )
    payload = "|".join((sys.executable, sys.prefix, sys.base_prefix, *tokens))
    return payload_digest(payload.encode("utf-8"))


def _distribution_token(distribution: importlib.metadata.Distribution) -> str:
    raw_name = distribution.metadata["Name"]
    name = raw_name.casefold() if raw_name else ""
    version = distribution.version
    record = distribution.read_text("RECORD") or ""
    direct_url = distribution.read_text("direct_url.json") or ""
    metadata = distribution.read_text("METADATA") or ""
    binding = payload_digest("|".join((record, direct_url, metadata)).encode("utf-8"))
    return f"{name}=={version}:{binding}"


def _resolved_executable(root: Path, command: str) -> Path:
    located = shutil.which(command)
    candidate = Path(located) if located else Path(command)
    if not candidate.is_absolute():
        candidate = root / candidate
    return Path(os.path.abspath(candidate))


def _command_name(command: str) -> str:
    name = Path(command).name.lower()
    return Path(name).stem if Path(name).suffix in {".bat", ".cmd", ".exe"} else name


__all__ = [
    "controlled_execution_environment",
    "effective_command_argv",
    "execution_toolchain",
    "optional_file_digest",
    "payload_digest",
    "resolve_execution_adapter",
    "safe_project_path",
]
