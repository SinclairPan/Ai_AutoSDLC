"""Lean caller 身份的可达执行与纯引用索引。"""

from __future__ import annotations

import ast

from ai_sdlc.core.lean_code_caller_models import _ImportedCallables
from ai_sdlc.core.lean_code_caller_scope_semantics import _enclosing_dynamic_scope
from ai_sdlc.core.lean_code_caller_source_index import _dynamic_location
from ai_sdlc.core.lean_code_execution_identity import (
    _target_execution_anchors,
    _target_reference_anchors,
)


def _target_execution_index(
    tree: ast.Module,
    parents: dict[ast.AST, ast.AST],
    imported_callables: _ImportedCallables,
) -> dict[tuple[str, str], set[str]]:
    index: dict[tuple[str, str], set[str]] = {}
    for target, anchor in _target_execution_anchors(tree, imported_callables):
        index.setdefault(target, set()).add(
            _dynamic_location(anchor, _enclosing_dynamic_scope(anchor, parents))
        )
    return index


def _target_reference_index(
    tree: ast.Module,
    parents: dict[ast.AST, ast.AST],
    imported_callables: _ImportedCallables,
) -> dict[tuple[str, str], set[str]]:
    index: dict[tuple[str, str], set[str]] = {}
    source_node_ids = {id(node) for node in ast.walk(tree)}
    for target, anchor in _target_reference_anchors(tree, imported_callables):
        if id(anchor) not in source_node_ids or not isinstance(
            anchor, (ast.Name, ast.Attribute)
        ):
            continue
        if _reference_is_direct_call(anchor, parents):
            continue
        index.setdefault(target, set()).add(
            _dynamic_location(anchor, _enclosing_dynamic_scope(anchor, parents))
        )
    return index


def _reference_is_direct_call(
    anchor: ast.Name | ast.Attribute,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    current: ast.AST = anchor
    while (parent := parents.get(current)) is not None:
        if isinstance(parent, ast.Call):
            return current is parent.func
        if isinstance(parent, (ast.stmt, ast.Lambda)):
            return False
        current = parent
    return False


__all__: list[str] = []
