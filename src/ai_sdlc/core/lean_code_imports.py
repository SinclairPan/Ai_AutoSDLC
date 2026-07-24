"""解析冻结 Python 源码中的目标模块导入别名。"""

from __future__ import annotations

import ast
from collections.abc import Sequence


def _target_import_names(
    nodes: Sequence[ast.stmt],
    caller_path: str,
    target_path: str,
    target_class: str,
    target_name: str,
    target_paths: frozenset[str] | None = None,
    target_exports: dict[str, frozenset[str]] | None = None,
) -> tuple[set[str], set[str], set[str]]:
    provenance_paths = target_paths or frozenset((target_path,))
    exports = target_exports or {
        path: frozenset((target_class or target_name,)) for path in provenance_paths
    }
    exports_by_module = {
        module: exported_names
        for path, exported_names in exports.items()
        for module in _module_names(path)
    }
    modules_for_target = set(exports_by_module)
    direct: set[str] = set()
    modules: set[str] = set()
    classes: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.ImportFrom):
            _merge_import_from_names(
                node,
                caller_path,
                target_class,
                modules_for_target,
                exports_by_module,
                direct,
                modules,
                classes,
            )
        elif isinstance(node, ast.Import):
            _merge_import_names(
                node,
                target_class,
                modules_for_target,
                exports_by_module,
                direct,
                modules,
                classes,
            )
    call_modules = modules if not target_class else set()
    return direct, call_modules, classes


def _merge_import_from_names(
    node: ast.ImportFrom,
    caller_path: str,
    target_class: str,
    modules_for_target: set[str],
    exports_by_module: dict[str, frozenset[str]],
    direct: set[str],
    modules: set[str],
    classes: set[str],
) -> None:
    imported_from = _import_from_modules(caller_path, node)
    imported_exports = set().union(
        *(exports_by_module.get(module, frozenset()) for module in imported_from)
    )
    imported_names = {
        alias.asname or alias.name
        for alias in node.names
        if alias.name in imported_exports
    }
    (classes if target_class else direct).update(imported_names)
    for alias in node.names:
        imported_modules = {
            f"{module}.{alias.name}"
            for module in imported_from
            if f"{module}.{alias.name}" in modules_for_target
        }
        if not imported_modules:
            continue
        local_module = alias.asname or alias.name
        modules.add(local_module)
        submodule_exports = set().union(
            *(exports_by_module[module] for module in imported_modules)
        )
        destination = classes if target_class else direct
        destination.update(f"{local_module}.{export}" for export in submodule_exports)


def _merge_import_names(
    node: ast.Import,
    target_class: str,
    modules_for_target: set[str],
    exports_by_module: dict[str, frozenset[str]],
    direct: set[str],
    modules: set[str],
    classes: set[str],
) -> None:
    for alias in node.names:
        if alias.name not in modules_for_target:
            continue
        local_module = alias.asname or alias.name
        exported_names = exports_by_module[alias.name]
        destination = classes if target_class else direct
        destination.update(f"{local_module}.{exported}" for exported in exported_names)
        modules.add(local_module)


def _import_from_modules(path: str, node: ast.ImportFrom) -> set[str]:
    if node.level == 0:
        return {node.module or ""}
    resolved: set[str] = set()
    for caller_module in _module_names(path):
        package = caller_module.split(".")
        if not path.replace("\\", "/").endswith("/__init__.py"):
            package = package[:-1]
        keep = max(0, len(package) - node.level + 1)
        parts = [*package[:keep]]
        if node.module:
            parts.extend(node.module.split("."))
        resolved.add(".".join(parts))
    return resolved


def _module_names(path: str) -> set[str]:
    normalized = path.replace("\\", "/")
    if normalized.endswith("/__init__.py"):
        normalized = normalized[: -len("/__init__.py")]
    elif normalized.endswith(".py"):
        normalized = normalized[:-3]
    module = normalized.strip("/").replace("/", ".")
    names = {module}
    if module.startswith("src."):
        names.add(module.removeprefix("src."))
    if module.startswith("scripts."):
        names.add(module.rsplit(".", 1)[-1])
    return names


__all__: list[str] = []
