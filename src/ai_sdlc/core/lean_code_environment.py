"""Project-path and execution-environment bindings for Lean receipts."""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import sys
from pathlib import Path


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
    executable = Path(argv[0]).name.lower().removesuffix(".exe")
    if executable.startswith(("python", "pypy")):
        return _python_adapter(argv, targets)
    if executable in _TARGET_RUNNERS and _pytest_target(argv, targets, 1):
        return f"target-runner:{executable}"
    return ""


def _target_indexes(root: Path, argv: tuple[str, ...], reference: str) -> set[int]:
    expected = safe_project_path(root, reference)
    indexes: set[int] = set()
    for index, item in enumerate(argv[1:], start=1):
        target = item.replace("\\", "/").split("::", 1)[0]
        candidate = Path(target)
        resolved = (
            candidate.resolve()
            if candidate.is_absolute()
            else (root / candidate).resolve()
        )
        if resolved == expected:
            indexes.add(index)
    return indexes


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


def effective_command_argv(adapter: str, argv: tuple[str, ...]) -> tuple[str, ...]:
    """Add a recorded override that neutralizes implicit pytest addopts."""

    return (*argv, *_PYTEST_OVERRIDE) if "pytest" in adapter else argv


def controlled_execution_environment(adapter: str) -> dict[str, str]:
    """Return the inherited environment with implicit pytest selectors disabled."""

    environment = os.environ.copy()
    if "pytest" in adapter:
        environment["PYTEST_ADDOPTS"] = ""
        environment["PYTEST_PLUGINS"] = ""
    return environment


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

    resolved = str(Path(shutil.which(command) or command).resolve())
    executable = Path(resolved)
    executable_digest = (
        payload_digest(executable.read_bytes()) if executable.is_file() else ""
    )
    environment = _environment_fingerprint(root)
    payload = "|".join(
        (platform.platform(), sys.version, resolved, executable_digest, environment)
    )
    return (
        payload_digest(payload.encode("utf-8")),
        resolved,
        executable_digest,
        environment,
    )


def _environment_fingerprint(root: Path) -> str:
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
    return payload_digest("|".join(tokens).encode("utf-8"))


__all__ = [
    "controlled_execution_environment",
    "effective_command_argv",
    "execution_toolchain",
    "optional_file_digest",
    "payload_digest",
    "resolve_execution_adapter",
    "safe_project_path",
]
