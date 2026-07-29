"""传播模块定义期复合语句产生的持久绑定。"""

from __future__ import annotations

import ast
from typing import Protocol, Self

from ai_sdlc.core.lean_code_control_flow import (
    _match_pattern_names,
    _reachable_match_cases,
    _static_truth,
    _statically_empty,
    _statically_nonempty,
)
from ai_sdlc.core.lean_code_exception_flow import _statement_exception_points
from ai_sdlc.core.lean_code_exception_handlers import _reachable_exception_handlers
from ai_sdlc.core.lean_code_import_expression_flow import (
    _named_expression_bindings,
)


class _CompoundState(Protocol):
    def apply_binding(self, statement: ast.stmt) -> None: ...

    def fork(self) -> Self: ...

    def merge(self, states: tuple[Self, ...]) -> None: ...

    def _bind_target(self, target: ast.expr, value: ast.expr) -> None: ...


def _apply_compound_binding(state: _CompoundState, statement: ast.stmt) -> bool:
    if isinstance(statement, ast.Expr):
        _apply_expression_bindings(state, statement.value)
        return True
    if isinstance(statement, ast.If):
        _apply_if(state, statement)
        return True
    if isinstance(statement, ast.Match):
        _apply_match(state, statement)
        return True
    if isinstance(statement, (ast.Try, ast.TryStar)):
        _apply_try(state, statement)
        return True
    if isinstance(statement, (ast.For, ast.AsyncFor)):
        _apply_for(state, statement)
        return True
    if isinstance(statement, ast.While):
        _apply_while(state, statement)
        return True
    if isinstance(statement, (ast.With, ast.AsyncWith)):
        for item in statement.items:
            _apply_expression_bindings(state, item.context_expr)
            if item.optional_vars is not None:
                state._bind_target(item.optional_vars, item.context_expr)
        _apply_block(state, statement.body)
        return True
    return False


def _apply_if(state: _CompoundState, statement: ast.If) -> None:
    _apply_expression_bindings(state, statement.test)
    truth = _static_truth(statement.test)
    if truth is not None:
        _apply_block(state, statement.body if truth else statement.orelse)
    else:
        _merge_blocks(state, (statement.body, statement.orelse))


def _apply_match(state: _CompoundState, statement: ast.Match) -> None:
    _apply_expression_bindings(state, statement.subject)
    cases, no_match = _reachable_match_cases(statement.subject, statement.cases)
    branches: list[_CompoundState] = []
    for case in cases:
        branch = state.fork()
        names = _match_pattern_names(case.pattern)
        if names:
            branch._bind_target(
                ast.Tuple(
                    elts=[
                        ast.Name(id=name, ctx=ast.Store())
                        for name in sorted(names)
                    ],
                    ctx=ast.Store(),
                ),
                statement.subject,
            )
        if case.guard is not None:
            _apply_expression_bindings(branch, case.guard)
        _apply_block(branch, case.body)
        branches.append(branch)
    if no_match:
        branches.append(state.fork())
    state.merge(tuple(branches))


def _apply_try(
    state: _CompoundState,
    statement: ast.Try | ast.TryStar,
) -> None:
    normal = state.fork()
    normal_control = _apply_block(normal, statement.body)
    if normal_control == "normal":
        _apply_block(normal, statement.orelse)
    branches: list[_CompoundState] = [normal]
    for index, child in enumerate(statement.body):
        for point in _statement_exception_points(child):
            for handler in _reachable_exception_handlers(
                statement.handlers,
                point.raised,
            )[0]:
                branch = state.fork()
                _apply_block(branch, statement.body[:index])
                _apply_block(branch, list(point.prefix))
                _apply_block(branch, handler.body)
                branches.append(branch)
    for branch in branches:
        _apply_block(branch, statement.finalbody)
    state.merge(tuple(branches))


def _apply_for(
    state: _CompoundState,
    statement: ast.For | ast.AsyncFor,
) -> None:
    _apply_expression_bindings(state, statement.iter)
    if _statically_empty(statement.iter):
        _apply_block(state, statement.orelse)
        return
    if isinstance(statement.iter, (ast.List, ast.Tuple)) and statement.iter.elts:
        iteration = state.fork()
        broke = False
        for value in statement.iter.elts:
            iteration._bind_target(statement.target, value)
            control = _apply_block(iteration, statement.body)
            if control == "break":
                broke = True
                break
            if control not in {"normal", "continue"}:
                break
        if not broke:
            _apply_block(iteration, statement.orelse)
    else:
        iteration = state.fork()
        iteration._bind_target(statement.target, statement.iter)
        control = _apply_block(iteration, statement.body)
        if control != "break":
            _apply_block(iteration, statement.orelse)
    if _statically_nonempty(statement.iter):
        state.merge((iteration,))
    else:
        state.merge((iteration, _branch(state, statement.orelse)))


def _apply_while(state: _CompoundState, statement: ast.While) -> None:
    _apply_expression_bindings(state, statement.test)
    truth = _static_truth(statement.test)
    if truth is False:
        _apply_block(state, statement.orelse)
    elif truth is True:
        _apply_block(state, statement.body)
    else:
        _merge_blocks(state, (statement.body, statement.orelse))


def _apply_block(state: _CompoundState, statements: list[ast.stmt]) -> str:
    for statement in statements:
        if isinstance(statement, ast.Break):
            return "break"
        if isinstance(statement, ast.Continue):
            return "continue"
        if isinstance(statement, ast.Return):
            return "return"
        if isinstance(statement, ast.Raise):
            return "raise"
        state.apply_binding(statement)
    return "normal"


def _branch(state: _CompoundState, statements: list[ast.stmt]) -> _CompoundState:
    branch = state.fork()
    _apply_block(branch, statements)
    return branch


def _merge_blocks(
    state: _CompoundState,
    blocks: tuple[list[ast.stmt], ...],
) -> None:
    state.merge(tuple(_branch(state, block) for block in blocks))


def _first_item(expression: ast.expr) -> ast.expr | None:
    if isinstance(expression, (ast.List, ast.Tuple)) and expression.elts:
        return expression.elts[0]
    return None


def _apply_expression_bindings(
    state: _CompoundState,
    expression: ast.expr,
) -> None:
    for target, value in _named_expression_bindings(expression):
        state._bind_target(target, value)


__all__: list[str] = []
