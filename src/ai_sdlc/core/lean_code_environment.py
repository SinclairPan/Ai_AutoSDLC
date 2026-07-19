"""Project-path and execution-environment bindings for Lean receipts."""

from __future__ import annotations

import hashlib
import importlib.metadata
import os
import platform
import shutil
import sys
from pathlib import Path

from ai_sdlc.core.lean_code_import_boundary import (
    _import_shield,
    _missing_project_imports,
    _project_python_path,
    _removed_namespace_members,
    _selected_namespace_imports,
    _selected_python_path,
    _validate_selected_python_path,
    _validated_source_root,
)
from ai_sdlc.core.lean_code_runner import (
    _probe_distributions,
    _runner_import_paths,
    _runner_python,
)


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
    start = 2 if len(argv) > 1 and argv[1] == "-S" else 1
    if start in targets:
        return "python-script"
    if len(argv) > start + 2 and argv[start] == "-m":
        module = argv[start + 1].lower()
        valid_target = (
            _pytest_target(argv, targets, start + 2)
            if module == "pytest"
            else _positional_target(argv, targets, start + 2)
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
    arguments = argv[2:] if argv[1:2] == ("-S",) else argv[1:]
    command = (
        (str(runner), "-S", "-m", "pytest", *arguments)
        if adapter.startswith("target-runner:")
        else (str(runner), "-S", *arguments)
    )
    return (*command, *_PYTEST_OVERRIDE) if "pytest" in adapter else command


def controlled_execution_environment(
    adapter: str,
    execution_root: Path | None = None,
    project_root: Path | None = None,
    runner: Path | None = None,
    removed_files: tuple[str, ...] = (),
) -> dict[str, str]:
    """Bind Python imports to the selected source view and normalize runner inputs."""

    validate_selected_view = execution_root is not None
    execution_root = (execution_root or Path.cwd()).resolve()
    project_root = (project_root or execution_root).resolve()
    environment = os.environ.copy()
    inherited_python_path = environment.get("PYTHONPATH", "")
    for name in tuple(environment):
        if name.upper().startswith("PYTHON"):
            environment.pop(name)
    if validate_selected_view:
        _validated_source_root(
            execution_root,
            Path("."),
            _runner_view_links(runner, project_root, execution_root),
        )
    environment["PYTHONPATH"] = _controlled_python_path(
        inherited_python_path,
        project_root,
        execution_root,
        runner,
        removed_files,
        validate_selected_view,
    )
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    if "pytest" in adapter:
        environment["PYTEST_ADDOPTS"] = ""
        environment["PYTEST_PLUGINS"] = ""
    return environment


def _runner_view_links(
    runner: Path | None,
    project_root: Path,
    execution_root: Path,
) -> set[str]:
    if runner is None:
        return set()
    try:
        relative = Path(os.path.abspath(runner)).relative_to(project_root)
    except ValueError:
        return set()
    candidate = execution_root / relative
    if not candidate.is_symlink():
        return set()
    # 显式 runner 已由 toolchain 摘要约束，不再把同一链接当作源码逃逸。
    return {os.path.normcase(os.path.abspath(candidate))}


def _controlled_python_path(
    inherited: str,
    project_root: Path,
    execution_root: Path,
    runner: Path | None,
    removed_files: tuple[str, ...],
    validate_selected_view: bool,
) -> str:
    selected = _selected_python_path(inherited, project_root, execution_root)
    if validate_selected_view:
        _validate_selected_python_path(execution_root, selected)
    if runner is None:
        return selected
    dependencies = _runner_import_paths(runner, project_root)
    namespaces = _selected_namespace_imports(
        execution_root,
        project_root,
        inherited,
        removed_files,
        dependencies,
    )
    shield = _import_shield(
        execution_root,
        _missing_project_imports(
            execution_root,
            project_root,
            inherited,
            removed_files,
        ),
        namespaces,
        _removed_namespace_members(
            execution_root,
            project_root,
            inherited,
            removed_files,
            namespaces,
        ),
    )
    return os.pathsep.join(
        dict.fromkeys(
            (
                selected,
                *(str(path) for path in (shield,) if path is not None),
                *(str(path) for path in dependencies),
            )
        )
    )


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
