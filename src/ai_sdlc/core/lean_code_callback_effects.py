"""编译并合并高阶回调的描述符副作用摘要。"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Protocol

from ai_sdlc.core.lean_code_callback_projection_flow import _parameter_effects
from ai_sdlc.core.lean_code_control_flow import _static_truth
from ai_sdlc.core.lean_code_static_values import _constant_subscript_value


class _CallbackEffectState(Protocol):
    callback_parameters: dict[str, _CallbackSummary]
    typing_modules: set[str]
    type_hint_functions: set[str]


@dataclass(frozen=True)
class _CallbackSummary:
    arguments: ast.arguments
    invoked_parameters: frozenset[str]
    type_hint_parameters: frozenset[str] = frozenset()
    uncertain: bool = False


def _callback_summary(
    statement: ast.FunctionDef | ast.AsyncFunctionDef,
    state: _CallbackEffectState,
) -> _CallbackSummary | None:
    return _callable_summary(statement.args, statement.body, state)


def _lambda_callback_summary(
    expression: ast.Lambda,
    state: _CallbackEffectState,
) -> _CallbackSummary | None:
    return _callable_summary(expression.args, [expression.body], state)


def _expression_callback_summary(
    expression: ast.expr | None,
    state: _CallbackEffectState,
) -> _CallbackSummary | None:
    if isinstance(expression, ast.Name):
        return state.callback_parameters.get(expression.id)
    if isinstance(expression, ast.NamedExpr):
        return _expression_callback_summary(expression.value, state)
    if isinstance(expression, ast.Lambda):
        return _lambda_callback_summary(expression, state)
    if isinstance(expression, ast.Subscript):
        selected = _constant_subscript_value(expression)
        return (
            _expression_callback_summary(selected, state)
            if selected is not None
            else None
        )
    if isinstance(expression, ast.IfExp):
        truth = _static_truth(expression.test)
        if truth is not None:
            selected = expression.body if truth else expression.orelse
            return _expression_callback_summary(selected, state)
        return _merge_expression_callback_summaries(
            (expression.body, expression.orelse),
            state,
        )
    if isinstance(expression, ast.BoolOp):
        return _merge_expression_callback_summaries(
            _possible_bool_values(expression),
            state,
        )
    return None


def _merge_expression_callback_summaries(
    expressions: tuple[ast.expr, ...],
    state: _CallbackEffectState,
) -> _CallbackSummary | None:
    summaries = [
        summary
        for expression in expressions
        if (summary := _expression_callback_summary(expression, state)) is not None
    ]
    return (
        _merge_callback_summaries(
            summaries,
            missing=len(summaries) != len(expressions),
        )
        if summaries
        else None
    )


def _merge_callback_summaries(
    summaries: list[_CallbackSummary],
    *,
    missing: bool,
) -> _CallbackSummary:
    first = summaries[0]
    compatible = all(
        ast.dump(summary.arguments) == ast.dump(first.arguments)
        for summary in summaries[1:]
    )
    return _CallbackSummary(
        first.arguments,
        frozenset().union(*(summary.invoked_parameters for summary in summaries)),
        frozenset().union(*(summary.type_hint_parameters for summary in summaries)),
        missing or not compatible or any(summary.uncertain for summary in summaries),
    )


def _callable_summary(
    arguments: ast.arguments,
    body: list[ast.stmt] | list[ast.expr],
    state: _CallbackEffectState,
) -> _CallbackSummary | None:
    names = frozenset(
        argument.arg
        for argument in (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
            *((arguments.vararg,) if arguments.vararg is not None else ()),
            *((arguments.kwarg,) if arguments.kwarg is not None else ()),
        )
    )
    effects = _parameter_effects(names, body, state)
    return (
        _CallbackSummary(
            arguments,
            effects.invoked,
            effects.type_hints,
            effects.uncertain,
        )
        if effects.invoked or effects.type_hints
        else None
    )


def _possible_bool_values(expression: ast.BoolOp) -> tuple[ast.expr, ...]:
    possible: list[ast.expr] = []
    is_or = isinstance(expression.op, ast.Or)
    for index, value in enumerate(expression.values):
        truth = _static_truth(value)
        is_last = index == len(expression.values) - 1
        if (
            is_last
            or (is_or and truth is not False)
            or (not is_or and truth is not True)
        ):
            possible.append(value)
        if (is_or and truth is True) or (not is_or and truth is False):
            break
    return tuple(possible)


__all__: list[str] = []
