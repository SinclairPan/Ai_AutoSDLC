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
) -> tuple[set[str], set[str], set[str]]:
    modules_for_target = _module_names(target_path)
    direct: set[str] = set()
    modules: set[str] = set()
    classes: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.ImportFrom):
            imported_from = _import_from_modules(caller_path, node)
            if imported_from & modules_for_target:
                direct.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if not target_class and alias.name == target_name
                )
                classes.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if target_class and alias.name == target_class
                )
            modules.update(
                alias.asname or alias.name
                for alias in node.names
                if any(
                    f"{module}.{alias.name}" in modules_for_target
                    for module in imported_from
                )
            )
        elif isinstance(node, ast.Import):
            modules.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name in modules_for_target
            )
    if target_class:
        classes.update(f"{module}.{target_class}" for module in modules)
    call_modules = modules if not target_class else set()
    return direct, call_modules, classes


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
    return names


__all__: list[str] = []
