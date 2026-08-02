"""按同步和异步协议解析上下文管理器进入值的生成器血缘。"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Protocol

from ai_sdlc.core.lean_code_control_flow import _static_truth
from ai_sdlc.core.lean_code_generator_identity import _merge_lineages
from ai_sdlc.core.lean_code_static_values import _constant_subscript_value


@dataclass(frozen=True)
class _ProtocolLineage:
    defined: bool = False
    generators: tuple[ast.GeneratorExp, ...] = ()
    uncertain: bool = False


@dataclass(frozen=True)
class _ContextManagerProtocols:
    sync: _ProtocolLineage = _ProtocolLineage()
    async_: _ProtocolLineage = _ProtocolLineage()


class _GeneratorBindings(Protocol):
    context_manager_protocols: dict[str, _ContextManagerProtocols]


def _context_manager_expression_protocols(
    expression: ast.expr,
    state: _GeneratorBindings,
) -> _ContextManagerProtocols:
    if isinstance(expression, ast.Name):
        if expression.id == "object":
            return _ContextManagerProtocols()
        return state.context_manager_protocols.get(
            expression.id,
            _unknown_protocols(),
        )
    if isinstance(expression, ast.NamedExpr):
        return _context_manager_expression_protocols(expression.value, state)
    if isinstance(expression, ast.Call):
        return _context_manager_expression_protocols(expression.func, state)
    if isinstance(expression, ast.Subscript):
        selected = _constant_subscript_value(expression)
        if selected is not None:
            return _context_manager_expression_protocols(selected, state)
        return _unknown_protocols()
    if isinstance(expression, ast.IfExp):
        truth = _static_truth(expression.test)
        if truth is not None:
            selected = expression.body if truth else expression.orelse
            return _context_manager_expression_protocols(selected, state)
        return _merge_protocols(
            (
                _context_manager_expression_protocols(expression.body, state),
                _context_manager_expression_protocols(expression.orelse, state),
            )
        )
    if isinstance(expression, ast.BoolOp):
        return _merge_protocols(
            tuple(
                _context_manager_expression_protocols(value, state)
                for value in _possible_bool_values(expression)
            )
        )
    return _unknown_protocols()


def _context_manager_expression_lineage(
    expression: ast.expr,
    state: _GeneratorBindings,
    *,
    async_with: bool = False,
) -> tuple[ast.GeneratorExp, ...]:
    protocol = _selected_protocol(
        _context_manager_expression_protocols(expression, state),
        async_with=async_with,
    )
    return protocol.generators


def _context_manager_expression_uncertain(
    expression: ast.expr,
    state: _GeneratorBindings,
    *,
    async_with: bool = False,
) -> bool:
    protocol = _selected_protocol(
        _context_manager_expression_protocols(expression, state),
        async_with=async_with,
    )
    return protocol.uncertain


def _selected_protocol(
    protocols: _ContextManagerProtocols,
    *,
    async_with: bool,
) -> _ProtocolLineage:
    return protocols.async_ if async_with else protocols.sync


def _merge_protocols(
    values: tuple[_ContextManagerProtocols, ...],
) -> _ContextManagerProtocols:
    return _ContextManagerProtocols(
        sync=_merge_protocol_lineages(tuple(value.sync for value in values)),
        async_=_merge_protocol_lineages(tuple(value.async_ for value in values)),
    )


def _merge_protocol_lineages(
    values: tuple[_ProtocolLineage, ...],
) -> _ProtocolLineage:
    if not values:
        return _ProtocolLineage()
    signatures = {
        (value.defined, tuple(id(item) for item in value.generators))
        for value in values
    }
    return _ProtocolLineage(
        defined=any(value.defined for value in values),
        generators=_merge_lineages(*(value.generators for value in values)),
        uncertain=any(value.uncertain for value in values) or len(signatures) > 1,
    )


def _unknown_protocols() -> _ContextManagerProtocols:
    unknown = _ProtocolLineage(uncertain=True)
    return _ContextManagerProtocols(sync=unknown, async_=unknown)


def _possible_bool_values(expression: ast.BoolOp) -> tuple[ast.expr, ...]:
    possible: list[ast.expr] = []
    is_or = isinstance(expression.op, ast.Or)
    for index, value in enumerate(expression.values):
        truth = _static_truth(value)
        is_last = index == len(expression.values) - 1
        if is_last or (is_or and truth is not False) or (not is_or and truth is not True):
            possible.append(value)
        if (is_or and truth is True) or (not is_or and truth is False):
            break
    return tuple(possible)


__all__: list[str] = []
