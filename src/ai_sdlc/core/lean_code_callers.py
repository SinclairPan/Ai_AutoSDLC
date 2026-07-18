"""Snapshot-bound Python caller analysis for new public Lean Code symbols."""

from __future__ import annotations

import ast
from pathlib import Path

from ai_sdlc.core.lean_code_classification import classify_file
from ai_sdlc.core.lean_code_models import (
    FileClassification,
    FileMetric,
    MetricCapability,
)
from ai_sdlc.core.source_snapshot import SourceSnapshot
from ai_sdlc.core.source_snapshot_view import python_sources


def attach_python_callers(
    root: Path, snapshot: SourceSnapshot, files: list[FileMetric]
) -> None:
    """Count only symbol-resolved callers from the frozen snapshot after-view."""

    targets = {
        (file.path, function.symbol): function
        for file in files
        if file.capability == MetricCapability.EXACT
        for function in file.functions
        if function.public and function.is_new
    }
    if not targets:
        return
    callers: dict[tuple[str, str], set[str]] = {target: set() for target in targets}
    for path, payload in python_sources(root, snapshot).items():
        if (
            classify_file(path, payload, False)
            != FileClassification.HANDWRITTEN_PRODUCT
        ):
            continue
        try:
            tree = ast.parse(payload.decode("utf-8", errors="strict"), filename=path)
        except (SyntaxError, UnicodeDecodeError):
            continue
        for target in targets:
            _collect_target_callers(path, tree, target, callers[target])
    for target, function in targets.items():
        function.caller_count = len(callers[target])


def _collect_target_callers(
    path: str,
    tree: ast.Module,
    target: tuple[str, str],
    callers: set[str],
) -> None:
    target_path, target_symbol = target
    target_name = target_symbol.split(".")[-1]
    direct_names, module_names = _target_import_names(tree, target_path, target_name)
    if path == target_path:
        direct_names.add(target_name)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        caller = f"{path}:{node.name}:{node.lineno}"
        if _function_calls_target(
            node, direct_names, module_names, target_name
        ) and not (path == target_path and node.name == target_name):
            callers.add(caller)


def _function_calls_target(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    direct_names: set[str],
    module_names: set[str],
    target_name: str,
) -> bool:
    finder = _DirectFunctionCallFinder(direct_names, module_names, target_name)
    for statement in node.body:
        finder.visit(statement)
    return finder.found


class _DirectFunctionCallFinder(ast.NodeVisitor):
    """Find calls owned by one function without descending into nested scopes."""

    def __init__(
        self,
        direct_names: set[str],
        module_names: set[str],
        target_name: str,
    ) -> None:
        self.direct_names = direct_names
        self.module_names = module_names
        self.target_name = target_name
        self.found = False

    def visit_Call(self, node: ast.Call) -> None:
        if _calls_target(
            node.func, self.direct_names, self.module_names, self.target_name
        ):
            self.found = True
            return
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return


def _target_import_names(
    tree: ast.Module, target_path: str, target_name: str
) -> tuple[set[str], set[str]]:
    modules_for_target = _module_names(target_path)
    direct: set[str] = set()
    modules: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module in modules_for_target:
            direct.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == target_name
            )
        elif isinstance(node, ast.Import):
            modules.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name in modules_for_target
            )
    return direct, modules


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


def _calls_target(
    node: ast.expr,
    direct_names: set[str],
    module_names: set[str],
    target_name: str,
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in direct_names
    if not isinstance(node, ast.Attribute) or node.attr != target_name:
        return False
    return _dotted_name(node.value) in module_names


def _dotted_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


__all__ = ["attach_python_callers"]
