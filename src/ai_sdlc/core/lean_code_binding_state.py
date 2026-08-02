"""Python 动态调用血缘所需的注解求值边界。"""

from __future__ import annotations

import ast


def _deferred_annotation_call_ids(tree: ast.Module) -> set[int]:
    if not _future_annotations_enabled(tree):
        return set()
    calls: set[int] = set()
    for root in _annotation_roots(tree):
        calls.update(id(node) for node in ast.walk(root) if isinstance(node, ast.Call))
    return calls


def _future_annotations_enabled(tree: ast.Module) -> bool:
    return any(
        isinstance(statement, ast.ImportFrom)
        and statement.module == "__future__"
        and any(alias.name == "annotations" for alias in statement.names)
        for statement in tree.body
    )


def _annotation_roots(tree: ast.Module) -> list[ast.expr]:
    roots: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.annotation is not None:
            roots.append(node.annotation)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns is not None:
                roots.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            roots.append(node.annotation)
    return roots


__all__: list[str] = []
