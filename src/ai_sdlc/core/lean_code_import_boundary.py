"""Selected-source import roots and project-package fallback boundaries."""

from __future__ import annotations

import importlib.machinery
import os
from pathlib import Path

_IMPORT_SUFFIXES = tuple(
    sorted(
        {*(suffix.casefold() for suffix in importlib.machinery.all_suffixes()), ".pyd"},
        key=len,
        reverse=True,
    )
)


def _selected_python_path(
    inherited: str,
    project_root: Path,
    execution_root: Path,
) -> str:
    project_paths = _project_python_path(inherited, project_root)
    if (execution_root / "src").is_dir():
        project_paths.insert(0, Path("src"))
    selected = [execution_root / item for item in project_paths]
    return os.pathsep.join(dict.fromkeys(str(item) for item in selected))


def _validate_selected_python_path(execution_root: Path, selected: str) -> None:
    boundary = execution_root.resolve()
    for raw in selected.split(os.pathsep):
        if raw:
            _inside_selected_view(Path(raw), boundary)


def _missing_project_imports(
    execution_root: Path,
    project_root: Path,
    inherited: str,
    removed_files: tuple[str, ...],
) -> tuple[str, ...]:
    roots = _import_roots(execution_root, project_root, inherited, removed_files)
    owned = set().union(*(_import_names(project_root / root) for root in roots))
    selected = set().union(*(_import_names(execution_root / root) for root in roots))
    owned.update(_removed_import_names(roots, removed_files))
    return tuple(sorted(owned - selected))


def _uses_src_layout(
    execution_root: Path,
    project_root: Path,
    removed_files: tuple[str, ...],
) -> bool:
    return (
        (project_root / "src").is_dir()
        or (execution_root / "src").is_dir()
        or any(Path(path).parts[:1] == ("src",) for path in removed_files)
    )


def _import_names(root: Path) -> set[str]:
    if not root.is_dir():
        return set()
    names: set[str] = set()
    for path in root.iterdir():
        name = _import_name(path)
        if name.isidentifier() and (path.is_dir() or path.is_symlink() or path.suffix):
            names.add(name)
    return names


def _removed_import_names(
    roots: list[Path], removed_files: tuple[str, ...]
) -> set[str]:
    names: set[str] = set()
    for reference in removed_files:
        path = Path(reference)
        for root in roots:
            try:
                first = path.relative_to(root).parts[0]
            except (IndexError, ValueError):
                continue
            name = _import_name(Path(first))
            if name.isidentifier():
                names.add(name)
    return names


def _import_shield(
    execution_root: Path,
    names: tuple[str, ...],
    namespaces: dict[str, tuple[str, ...]] | None = None,
    blocked_members: dict[str, tuple[Path, ...]] | None = None,
) -> Path | None:
    return _write_import_shield(
        execution_root,
        names,
        namespaces or {},
        blocked_members,
    )


def _selected_namespace_imports(
    execution_root: Path,
    project_root: Path,
    inherited: str,
    removed_files: tuple[str, ...],
    dependencies: tuple[Path, ...],
) -> dict[str, tuple[str, ...]]:
    roots = _import_roots(execution_root, project_root, inherited, removed_files)
    candidates: dict[str, list[str]] = {}
    regular: set[str] = set()
    for relative in roots:
        _collect_namespace_imports(execution_root / relative, candidates, regular)
    for name in candidates.keys() - regular:
        candidates[name].extend(_dependency_namespace_paths(name, dependencies))
    return {
        name: tuple(dict.fromkeys(paths))
        for name, paths in candidates.items()
        if name not in regular
    }


def _collect_namespace_imports(
    root: Path,
    candidates: dict[str, list[str]],
    regular: set[str],
) -> None:
    if not root.is_dir():
        return
    for path in root.iterdir():
        name = _import_name(path)
        if not name.isidentifier():
            continue
        if path.is_file() or (path / "__init__.py").is_file():
            regular.add(name)
        elif path.is_dir():
            candidates.setdefault(name, []).append(str(path.resolve()))


def _dependency_namespace_paths(
    name: str,
    dependencies: tuple[Path, ...],
) -> list[str]:
    paths: list[str] = []
    for root in dependencies:
        candidate = root / name
        if candidate.is_dir() and not (candidate / "__init__.py").is_file():
            paths.append(str(candidate.resolve()))
    return paths


def _removed_namespace_members(
    execution_root: Path,
    project_root: Path,
    inherited: str,
    removed_files: tuple[str, ...],
    namespaces: dict[str, tuple[str, ...]],
) -> dict[str, tuple[Path, ...]]:
    roots = _import_roots(execution_root, project_root, inherited, removed_files)
    members: dict[str, list[Path]] = {}
    for reference in removed_files:
        _collect_removed_member(Path(reference), roots, namespaces, members)
    return {name: tuple(dict.fromkeys(paths)) for name, paths in members.items()}


def _collect_removed_member(
    path: Path,
    roots: list[Path],
    namespaces: dict[str, tuple[str, ...]],
    members: dict[str, list[Path]],
) -> None:
    for root in roots:
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if len(relative.parts) < 2:
            continue
        name = _import_name(Path(relative.parts[0]))
        member = _sentinel_member(Path(*relative.parts[1:]))
        if name in namespaces and member is not None:
            members.setdefault(name, []).append(member)


def _sentinel_member(path: Path) -> Path | None:
    name = _import_name(Path(path.name))
    if name == path.name or not name.isidentifier():
        return None
    return path.parent / f"{name}.py"


def _write_import_shield(
    execution_root: Path,
    missing: tuple[str, ...],
    namespaces: dict[str, tuple[str, ...]],
    blocked_members: dict[str, tuple[Path, ...]] | None = None,
) -> Path | None:
    if not missing and not namespaces:
        return None
    shield = execution_root / ".ai-sdlc-import-shield"
    shield.mkdir(exist_ok=True)
    for name in missing:
        package = shield / name
        package.mkdir(exist_ok=True)
        (package / "__init__.py").write_text(
            f"raise ModuleNotFoundError({name!r})\n",
            encoding="utf-8",
        )
    for name, paths in namespaces.items():
        package = shield / name
        package.mkdir(exist_ok=True)
        for relative in (blocked_members or {}).get(name, ()):
            sentinel = package / relative
            sentinel.parent.mkdir(parents=True, exist_ok=True)
            sentinel.write_text(
                f"raise ModuleNotFoundError({name!r})\n",
                encoding="utf-8",
            )
        (package / "__init__.py").write_text(
            f"__path__ = {[str(package), *paths]!r}\n",
            encoding="utf-8",
        )
    return shield


def _validated_source_root(
    execution_root: Path,
    relative: Path,
    allowed_links: set[str] | None = None,
) -> Path | None:
    candidate = execution_root / relative
    if not candidate.is_symlink() and not candidate.is_dir():
        return None
    boundary = execution_root.resolve()
    resolved = _inside_selected_view(candidate, boundary)
    if not resolved.is_dir():
        return None
    _validate_import_tree(resolved, boundary, allowed_links or set())
    return relative


def _validate_import_tree(root: Path, boundary: Path, allowed_links: set[str]) -> None:
    pending = [root]
    visited: set[Path] = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        _walk_import_tree(current, boundary, pending, allowed_links)


def _walk_import_tree(
    current: Path,
    boundary: Path,
    pending: list[Path],
    allowed_links: set[str],
) -> None:
    for directory, directories, files in os.walk(current, followlinks=False):
        _inside_selected_view(Path(directory), boundary)
        _validate_directories(Path(directory), directories, boundary, pending)
        _validate_linked_files(Path(directory), files, boundary, allowed_links)


def _validate_directories(
    directory: Path,
    directories: list[str],
    boundary: Path,
    pending: list[Path],
) -> None:
    for name in directories[:]:
        path = directory / name
        target = _inside_selected_view(path, boundary)
        lexical = os.path.normcase(os.path.abspath(path))
        if target.is_dir() and lexical != os.path.normcase(str(target)):
            # 目录链接的后代同样可能被 import，需继续验证且避免循环。
            directories.remove(name)
            pending.append(target)


def _validate_linked_files(
    directory: Path,
    files: list[str],
    boundary: Path,
    allowed_links: set[str],
) -> None:
    for name in files:
        path = directory / name
        if path.is_symlink() and _lexical_key(path) not in allowed_links:
            _inside_selected_view(path, boundary)


def _lexical_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _inside_selected_view(path: Path, boundary: Path) -> Path:
    try:
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError:
            resolved = path.resolve(strict=False)
        resolved.relative_to(boundary)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(f"Path escapes the selected source view: {path}") from exc
    return resolved


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


def _import_roots(
    execution_root: Path,
    project_root: Path,
    inherited: str,
    removed_files: tuple[str, ...],
) -> list[Path]:
    roots = _project_python_path(inherited, project_root)
    if Path("src") not in roots and _uses_src_layout(
        execution_root, project_root, removed_files
    ):
        roots.insert(0, Path("src"))
    return roots


def _import_name(path: Path) -> str:
    folded = path.name.casefold()
    for suffix in _IMPORT_SUFFIXES:
        if folded.endswith(suffix):
            name = path.name[: -len(suffix)]
            return name.split(".", 1)[0] if suffix.endswith((".so", ".pyd")) else name
    return path.name


__all__: list[str] = []
