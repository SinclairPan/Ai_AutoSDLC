"""身份可调用对象的参数绑定、默认值和 range 值域。"""

from __future__ import annotations

import ast
from typing import cast

from ai_sdlc.core.lean_code_identity_callable_summary import (
    _callable_execution_kind,
)
from ai_sdlc.core.lean_code_identity_join import _join_values
from ai_sdlc.core.lean_code_identity_models import (
    _EMPTY_VALUE,
    _UNKNOWN_VALUE,
    _CallableNode,
    _IdentityState,
    _IdentityValue,
    _literal_identity,
)


class _IdentityParameterMixin:
    def _bind_parameters(
        self,
        call_state: _IdentityState,
        callee: _IdentityValue,
        arguments: list[_IdentityValue],
        keywords: dict[str, _IdentityValue],
    ) -> None:
        node = callee.callable_node
        if node is None:
            return
        positional = [*node.args.posonlyargs, *node.args.args]
        for index, parameter in enumerate(positional):
            call_state.write(
                parameter.arg,
                self._parameter_value(
                    callee, positional, parameter.arg, index, arguments, keywords
                ),
            )
        if node.args.vararg is not None:
            call_state.write(
                node.args.vararg.arg,
                _IdentityValue("container", items=tuple(arguments[len(positional) :])),
            )
        self._bind_keyword_parameters(call_state, callee, keywords)

    def _bind_keyword_parameters(
        self,
        call_state: _IdentityState,
        callee: _IdentityValue,
        keywords: dict[str, _IdentityValue],
    ) -> None:
        node = callee.callable_node
        if node is None:
            return
        consumed = {item.arg for item in (*node.args.posonlyargs, *node.args.args)}
        for index, parameter in enumerate(node.args.kwonlyargs):
            value = keywords.get(parameter.arg)
            default = (
                callee.kw_defaults[index] if index < len(callee.kw_defaults) else None
            )
            call_state.write(parameter.arg, value or default or _EMPTY_VALUE)
            consumed.add(parameter.arg)
        if node.args.kwarg is not None:
            entries = tuple(
                (name, value)
                for name, value in keywords.items()
                if name not in consumed
            )
            call_state.write(
                node.args.kwarg.arg,
                _IdentityValue("container", entries=entries, truth=bool(entries)),
            )

    @staticmethod
    def _parameter_value(
        callee: _IdentityValue,
        positional: list[ast.arg],
        name: str,
        index: int,
        arguments: list[_IdentityValue],
        keywords: dict[str, _IdentityValue],
    ) -> _IdentityValue:
        if index < len(arguments):
            return arguments[index]
        if name in keywords:
            return keywords[name]
        default_index = index - (len(positional) - len(callee.defaults))
        return callee.defaults[default_index] if default_index >= 0 else _EMPTY_VALUE

    def _range_value(
        self,
        state: _IdentityState,
        node: ast.expr,
        arguments: list[_IdentityValue],
    ) -> _IdentityValue | None:
        if (
            not isinstance(node, ast.Name)
            or node.id != "range"
            or node.id in state.values
        ):
            return None
        if not 1 <= len(arguments) <= 3 or not all(
            value.scalar_known and isinstance(value.scalar, int) for value in arguments
        ):
            return _IdentityValue("container")
        range_arguments = tuple(cast(int, value.scalar) for value in arguments)
        try:
            values = tuple(range(*range_arguments))
        except ValueError:
            self._record_exception(state, "ValueError")
            return _UNKNOWN_VALUE
        if len(values) > 256:
            return _IdentityValue("container")
        return _IdentityValue(
            "container",
            items=tuple(_literal_identity(value) for value in values),
            truth=bool(values),
        )

    @staticmethod
    def _merge_values(values: list[_IdentityValue]) -> _IdentityValue:
        return _join_values(values) if values else _EMPTY_VALUE

    def _callable_value(
        self,
        state: _IdentityState,
        node: _CallableNode,
        kind: str = "",
        defaults: tuple[_IdentityValue, ...] | None = None,
        kw_defaults: tuple[_IdentityValue | None, ...] | None = None,
    ) -> _IdentityValue:
        closure_cells = tuple(
            sorted((name, state.ensure_cell(name)) for name in state.values)
        )
        if defaults is None:
            defaults = tuple(
                self._expression(state, item) for item in node.args.defaults
            )
        if kw_defaults is None:
            kw_defaults = tuple(
                self._expression(state, item) if item is not None else None
                for item in node.args.kw_defaults
            )
        return _IdentityValue(
            kind,
            node,
            closure_cells=closure_cells,
            defaults=defaults,
            kw_defaults=kw_defaults,
            execution_kind=_callable_execution_kind(node),
            module_id=state.module_id,
        )

    def _expression(self, state: _IdentityState, node: ast.expr) -> _IdentityValue:
        raise NotImplementedError

    def _record_exception(self, state: _IdentityState, name: str | None) -> None:
        raise NotImplementedError


__all__: list[str] = []
