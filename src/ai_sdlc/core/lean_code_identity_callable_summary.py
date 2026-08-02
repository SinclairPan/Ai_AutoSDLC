"""可调用摘要的完成态、外层效应与闭包单元合并。"""

from __future__ import annotations

import ast

from ai_sdlc.core.lean_code_identity_join import _join_values
from ai_sdlc.core.lean_code_identity_models import (
    _EMPTY_VALUE,
    _CallableNode,
    _IdentityState,
    _IdentityValue,
)


def _callable_execution_kind(node: ast.AST) -> str:
    if isinstance(node, ast.Lambda):
        return "sync"
    has_yield = _scope_contains_yield(node)
    if isinstance(node, ast.AsyncFunctionDef):
        return "async-generator" if has_yield else "coroutine"
    return "generator" if has_yield else "sync"


def _scope_contains_yield(node: ast.AST) -> bool:
    pending = list(getattr(node, "body", ()))
    while pending:
        current = pending.pop()
        if isinstance(current, (ast.Yield, ast.YieldFrom)):
            return True
        if isinstance(
            current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
        ):
            continue
        pending.extend(ast.iter_child_nodes(current))
    return False


def _scope_effect_names(node: _CallableNode) -> set[str]:
    if isinstance(node, ast.Lambda):
        return set()
    finder = _EffectNameFinder()
    for statement in node.body:
        finder.visit(statement)
    return finder.names


def _summary_effects(
    outcomes: list[_IdentityState], names: set[str]
) -> tuple[tuple[str, _IdentityValue], ...]:
    return tuple(
        (name, _join_values([item.read(name) for item in outcomes]))
        for name in sorted(names)
    )


def _summary_cells(
    outcomes: list[_IdentityState],
) -> tuple[tuple[str, _IdentityValue], ...]:
    cell_ids = set().union(*(state.cells for state in outcomes))
    return tuple(
        (
            cell_id,
            _join_values(
                [state.cells.get(cell_id, _EMPTY_VALUE) for state in outcomes]
            ),
        )
        for cell_id in sorted(cell_ids)
    )


def _summary_completion(outcomes: list[_IdentityState]) -> str:
    completions = {item.completion for item in outcomes}
    return completions.pop() if len(completions) == 1 else "mixed"


def _summary_exception(outcomes: list[_IdentityState]) -> str | None:
    exceptions = {item.exception for item in outcomes if item.completion == "raise"}
    return exceptions.pop() if len(exceptions) == 1 else None


class _EffectNameFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Global(self, node: ast.Global) -> None:
        self.names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.names.update(node.names)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


__all__: list[str] = []
