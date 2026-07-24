"""Resolved call and inherited-instance queries for Lean caller analysis."""

from __future__ import annotations

import ast
from typing import cast

from ai_sdlc.core.lean_code_call_finder import _DirectFunctionCallFinder
from ai_sdlc.core.lean_code_scope import _enclosing_class


def _function_calls_target(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    direct_names: set[str],
    module_names: set[str],
    class_names: set[str],
    target_name: str,
    allow_self_or_cls: bool,
    inherited_instances: set[str],
    local_imports: dict[int, tuple[set[str], set[str], set[str]]],
) -> bool:
    finder = _DirectFunctionCallFinder(
        direct_names,
        module_names,
        class_names,
        target_name,
        allow_self_or_cls,
        inherited_instances,
        node,
        local_imports,
    )
    for statement in node.body:
        finder.visit(statement)
    return cast(bool, finder.found)


def _function_target_reference_locations(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    direct_names: set[str],
    module_names: set[str],
    class_names: set[str],
    target_name: str,
    allow_self_or_cls: bool,
    inherited_instances: set[str],
    local_imports: dict[int, tuple[set[str], set[str], set[str]]],
    parents: dict[ast.AST, ast.AST],
) -> set[str]:
    finder = _DirectFunctionCallFinder(
        direct_names,
        module_names,
        class_names,
        target_name,
        allow_self_or_cls,
        inherited_instances,
        node,
        local_imports,
        capture_references=True,
        parents=parents,
    )
    for statement in node.body:
        finder.visit(statement)
    return cast(set[str], finder.reference_locations)


def _enclosing_target_instances(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    parents: dict[ast.AST, ast.AST],
    class_names: set[str],
    target_class: str,
) -> set[str]:
    ancestors: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ancestors.append(current)
        current = parents.get(current)
    ordered = list(reversed(ancestors))
    instances: set[str] = set()
    for index, ancestor in enumerate(ordered):
        child = ordered[index + 1] if index + 1 < len(ordered) else node
        instances = _function_instances_at_call(
            ancestor,
            class_names,
            _enclosing_class(ancestor, parents) == target_class,
            instances,
            child.name,
        )
    return instances


def _function_instances_at_call(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    class_names: set[str],
    allow_self_or_cls: bool,
    inherited_instances: set[str],
    child_name: str,
) -> set[str]:
    finder = _DirectFunctionCallFinder(
        set(),
        set(),
        class_names,
        "",
        allow_self_or_cls,
        inherited_instances,
        node,
        watched_call=child_name,
    )
    for statement in node.body:
        finder.visit(statement)
    return cast(set[str], finder.captured_instances)


__all__: list[str] = []
