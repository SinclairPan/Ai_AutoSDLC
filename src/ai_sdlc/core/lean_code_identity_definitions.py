"""函数、类定义及其定义期求值顺序。"""

from __future__ import annotations

import ast

from ai_sdlc.core.lean_code_identity_control import _IdentityControlMixin
from ai_sdlc.core.lean_code_identity_models import _IdentityState, _IdentityValue
from ai_sdlc.core.lean_code_identity_semantics import _function_arguments


class _IdentityDefinitionMixin(_IdentityControlMixin):
    origin: ast.AST
    deferred_annotations: bool

    def _simple_statement(
        self,
        state: _IdentityState,
        node: ast.stmt,
    ) -> list[_IdentityState]:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decorators, defaults, kw_defaults = self._function_header(state, node)
            value = self._callable_value(
                state,
                node,
                "object" if node is self.origin else "",
                defaults,
                kw_defaults,
            )
            for decorator in reversed(decorators):
                value = self._invoke_callable(
                    state, decorator, [value], {}, call_site=node
                )
            state.write(node.name, value)
            return [state]
        if isinstance(node, ast.ClassDef):
            state.write(node.name, self._class_definition(state, node))
            return [state]
        return self._data_statement(state, node)

    def _function_header(
        self, state: _IdentityState, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> tuple[
        list[_IdentityValue],
        tuple[_IdentityValue, ...],
        tuple[_IdentityValue | None, ...],
    ]:
        decorators = [self._expression(state, item) for item in node.decorator_list]
        defaults = tuple(self._expression(state, item) for item in node.args.defaults)
        kw_defaults = tuple(
            self._expression(state, item) if item is not None else None
            for item in node.args.kw_defaults
        )
        for argument in _function_arguments(node.args):
            if argument.annotation is not None and not self.deferred_annotations:
                self._expression(state, argument.annotation)
        if node.returns is not None and not self.deferred_annotations:
            self._expression(state, node.returns)
        return decorators, defaults, kw_defaults

    def _class_definition(
        self, state: _IdentityState, node: ast.ClassDef
    ) -> _IdentityValue:
        for item in (
            *node.decorator_list,
            *node.bases,
            *(keyword.value for keyword in node.keywords),
        ):
            self._expression(state, item)
        outcomes = self.process([state.clone()], node.body)
        method_names = {
            item.name
            for item in node.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        entries = {
            name: value
            for outcome in outcomes
            for name, value in outcome.resolved_values().items()
            if name in method_names and value.callable_node is not None
        }
        return _IdentityValue(
            "class", entries=tuple(sorted(entries.items())), truth=True
        )

    def _data_statement(
        self, state: _IdentityState, node: ast.stmt
    ) -> list[_IdentityState]:
        raise NotImplementedError


__all__: list[str] = []
