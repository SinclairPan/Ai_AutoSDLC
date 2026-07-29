"""把复合语句展开为互斥的可达类绑定路径。"""

from __future__ import annotations

import ast

from ai_sdlc.core.lean_code_control_flow import (
    _reachable_match_cases,
    _static_truth,
    _statically_empty,
    _statically_nonempty,
)
from ai_sdlc.core.lean_code_exception_flow import _statement_exception_points
from ai_sdlc.core.lean_code_exception_handlers import _reachable_exception_handlers

_Path = tuple[list[ast.stmt], ...]
_ControlPath = tuple[list[ast.stmt], str]


def _statement_paths(statement: ast.stmt) -> tuple[_Path, ...] | None:
    if isinstance(statement, ast.If):
        return _if_paths(statement)
    if isinstance(statement, (ast.Try, ast.TryStar)):
        return _try_paths(statement)
    if isinstance(statement, ast.Match):
        return _match_paths(statement)
    if isinstance(statement, (ast.For, ast.AsyncFor)):
        return _for_paths(statement)
    if isinstance(statement, ast.While):
        return _while_paths(statement)
    if isinstance(statement, (ast.With, ast.AsyncWith)):
        with_header = _with_header(statement)
        return ((with_header, statement.body),)
    return None


def _if_paths(statement: ast.If) -> tuple[_Path, ...]:
    header = _expression_block(statement.test)
    truth = _static_truth(statement.test)
    if truth is not None:
        return ((header, statement.body if truth else statement.orelse),)
    return ((header, statement.body), (header, statement.orelse))


def _try_paths(statement: ast.Try | ast.TryStar) -> tuple[_Path, ...]:
    normal = (statement.body, statement.orelse, statement.finalbody)
    handlers = tuple(
        (
            statement.body[:index],
            list(point.prefix),
            handler.body,
            statement.finalbody,
        )
        for index, child in enumerate(statement.body)
        for point in _statement_exception_points(child)
        for handler in _reachable_exception_handlers(
            statement.handlers,
            point.raised,
        )[0]
    )
    return (normal, *handlers)


def _match_paths(statement: ast.Match) -> tuple[_Path, ...]:
    header = _expression_block(statement.subject)
    cases, no_match = _reachable_match_cases(statement.subject, statement.cases)
    paths = tuple(
        (
            header,
            *((_expression_block(case.guard),) if case.guard is not None else ()),
            case.body,
        )
        for case in cases
    )
    return (*paths, (header, [])) if no_match else paths


def _for_paths(statement: ast.For | ast.AsyncFor) -> tuple[_Path, ...]:
    header = _loop_header(statement)
    if _statically_empty(statement.iter):
        return ((header, statement.orelse),)
    executed = tuple(
        (header, *path) for path in _loop_paths(statement.body, statement.orelse)
    )
    if _statically_nonempty(statement.iter):
        return executed
    return (*executed, (header, statement.orelse))


def _while_paths(statement: ast.While) -> tuple[_Path, ...]:
    header = _expression_block(statement.test)
    truth = _static_truth(statement.test)
    if truth is False:
        return ((header, statement.orelse),)
    executed = tuple(
        (header, *path) for path in _loop_paths(statement.body, statement.orelse)
    )
    return executed if truth is True else (*executed, (header, statement.orelse))


def _loop_paths(body: list[ast.stmt], orelse: list[ast.stmt]) -> tuple[_Path, ...]:
    paths: list[_Path] = []
    for statements, control in _block_control_paths(body):
        if control == "break":
            paths.append((statements,))
        elif control in {"normal", "continue"}:
            paths.append((statements, orelse))
        else:
            paths.append((statements,))
    return tuple(paths)


def _block_control_paths(statements: list[ast.stmt]) -> tuple[_ControlPath, ...]:
    outcomes: list[_ControlPath] = [([], "normal")]
    for statement in statements:
        next_outcomes: list[_ControlPath] = []
        for prefix, control in outcomes:
            if control != "normal":
                next_outcomes.append((prefix, control))
                continue
            terminal = _terminal_control(statement)
            if terminal is not None:
                next_outcomes.append((prefix, terminal))
                continue
            if isinstance(statement, (ast.Try, ast.TryStar)):
                for suffix, child_control in _try_control_paths(statement):
                    next_outcomes.append(([*prefix, *suffix], child_control))
                continue
            nested = _statement_paths(statement)
            if nested is None:
                next_outcomes.append(([*prefix, statement], "normal"))
                continue
            for path in nested:
                flattened = [child for block in path for child in block]
                for suffix, child_control in _block_control_paths(flattened):
                    next_outcomes.append(([*prefix, *suffix], child_control))
        outcomes = next_outcomes
    return tuple(outcomes)


def _try_control_paths(
    statement: ast.Try | ast.TryStar,
) -> tuple[_ControlPath, ...]:
    paths: list[tuple[list[ast.stmt], ...]] = [
        (statement.body, statement.orelse)
    ]
    paths.extend(
        (
            statement.body[:index],
            list(point.prefix),
            handler.body,
        )
        for index, child in enumerate(statement.body)
        for point in _statement_exception_points(child)
        for handler in _reachable_exception_handlers(
            statement.handlers,
            point.raised,
        )[0]
    )
    outcomes: list[_ControlPath] = []
    for path in paths:
        flattened = [child for block in path for child in block]
        for prefix, previous in _block_control_paths(flattened):
            final_paths = _block_control_paths(statement.finalbody)
            for suffix, final_control in final_paths:
                control = previous if final_control == "normal" else final_control
                outcomes.append(([*prefix, *suffix], control))
    return tuple(outcomes)


def _loop_header(statement: ast.For | ast.AsyncFor) -> list[ast.stmt]:
    header: list[ast.stmt] = [ast.Expr(value=statement.iter)]
    if isinstance(statement.iter, (ast.List, ast.Tuple)) and statement.iter.elts:
        header.append(
            ast.Assign(
                targets=[statement.target],
                value=statement.iter.elts[0],
            )
        )
    return header


def _with_header(statement: ast.With | ast.AsyncWith) -> list[ast.stmt]:
    header: list[ast.stmt] = []
    for item in statement.items:
        header.append(ast.Expr(value=item.context_expr))
        if item.optional_vars is not None:
            header.append(
                ast.Assign(
                    targets=[item.optional_vars],
                    value=item.context_expr,
                )
            )
    return header


def _expression_block(expression: ast.expr) -> list[ast.stmt]:
    return [ast.Expr(value=expression)]


def _terminal_control(statement: ast.stmt) -> str | None:
    if isinstance(statement, ast.Break):
        return "break"
    if isinstance(statement, ast.Continue):
        return "continue"
    if isinstance(statement, ast.Return):
        return "return"
    if isinstance(statement, ast.Raise):
        return "raise"
    return None


__all__: list[str] = []
