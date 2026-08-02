"""身份流的可调用对象、参数和有界递归摘要。"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from dataclasses import replace

from ai_sdlc.core.lean_code_identity_callable_summary import (
    _scope_effect_names,
    _summary_cells,
    _summary_completion,
    _summary_effects,
    _summary_exception,
)
from ai_sdlc.core.lean_code_identity_models import (
    _EMPTY_VALUE,
    _UNKNOWN_VALUE,
    _CallableSummary,
    _IdentityState,
    _IdentityValue,
)
from ai_sdlc.core.lean_code_identity_parameters import _IdentityParameterMixin
from ai_sdlc.core.lean_code_identity_semantics import (
    _builtin_consumer,
    _function_arguments,
)
from ai_sdlc.core.lean_code_scope import _local_bindings


class _IdentityCallMixin(_IdentityParameterMixin):
    origin: ast.AST
    mode: str
    escaped: bool
    call_stack: dict[int, int]
    call_sites: list[ast.AST]
    widened_calls: set[int]

    def _call(self, state: _IdentityState, node: ast.Call) -> _IdentityValue:
        callee = self._expression(state, node.func)
        arguments = self._call_arguments(state, node.args)
        keywords = self._call_keywords(state, node.keywords)
        range_value = self._range_value(state, node.func, arguments)
        if range_value is not None:
            return range_value
        if self._is_builtin_object(node.func, state):
            return _IdentityValue("literal", truth=True)
        if _builtin_consumer(node.func, state):
            self._consume_builtin_arguments(state, node, arguments, keywords)
            return _EMPTY_VALUE
        if callee.kind == "asyncio-run" and arguments:
            self._consume_deferred_callable(state, arguments[0])
            return _EMPTY_VALUE
        if callee.kind == "class":
            return _IdentityValue("instance", entries=callee.entries, truth=True)
        if callee.kind == "instance":
            method = dict(callee.entries).get("__call__", _EMPTY_VALUE)
            return self._invoke_callable(
                state, method, [callee, *arguments], keywords, call_site=node
            )
        if callee.kind == "partial" and callee.items:
            return self._invoke_callable(
                state,
                callee.items[0],
                [*callee.bound_arguments, *arguments],
                {**dict(callee.bound_keywords), **keywords},
                call_site=node,
            )
        if callee.kind == "functools-partial" and arguments:
            return _IdentityValue(
                "partial",
                items=(arguments[0],),
                bound_arguments=tuple(arguments[1:]),
                bound_keywords=tuple(keywords.items()),
                truth=True,
            )
        if callee.kind == "functools-wraps":
            return _IdentityValue("wraps-decorator", truth=True)
        if callee.callable_node is not None or callee.kind == "object":
            return self._invoke_callable(
                state, callee, arguments, keywords, call_site=node
            )
        if any(self._consumes(value) for value in (*arguments, *keywords.values())):
            self.escaped = True
        return _EMPTY_VALUE

    @staticmethod
    def _is_builtin_object(node: ast.expr, state: _IdentityState) -> bool:
        return (
            isinstance(node, ast.Name)
            and node.id == "object"
            and node.id not in state.values
        )

    def _call_arguments(
        self, state: _IdentityState, nodes: list[ast.expr]
    ) -> list[_IdentityValue]:
        arguments: list[_IdentityValue] = []
        for node in nodes:
            value = self._expression(state, node)
            arguments.extend(value.items if isinstance(node, ast.Starred) else (value,))
        return arguments

    def _call_keywords(
        self, state: _IdentityState, nodes: list[ast.keyword]
    ) -> dict[str, _IdentityValue]:
        keywords: dict[str, _IdentityValue] = {}
        for keyword in nodes:
            value = self._expression(state, keyword.value)
            if keyword.arg is not None:
                keywords[keyword.arg] = value
                continue
            keywords.update(
                {
                    str(name): item
                    for name, item in value.entries
                    if isinstance(name, str)
                }
            )
        return keywords

    def _consume_builtin_arguments(
        self,
        state: _IdentityState,
        node: ast.Call,
        arguments: list[_IdentityValue],
        keywords: dict[str, _IdentityValue],
    ) -> None:
        selected = arguments[:1]
        if isinstance(node.func, ast.Name) and node.func.id in {"max", "min"}:
            selected = arguments if len(arguments) == 1 else []
        for value in selected:
            self._consume_deferred_callable(state, value)
            self._consume_deferred_generator(state, value)
            if self.mode != "callable" and self._consumes(value):
                self._record_consumption(state, node)

    def _consume_deferred_callable(
        self, state: _IdentityState, value: _IdentityValue
    ) -> None:
        for alternative in value.alternatives:
            self._consume_deferred_callable(state, alternative)
        if value.kind != "deferred-call":
            return
        self._invoke_callable(
            state,
            value,
            list(value.bound_arguments),
            dict(value.bound_keywords),
            force=True,
        )

    def _consume_deferred_generator(
        self,
        state: _IdentityState,
        value: _IdentityValue,
    ) -> None:
        for alternative in value.alternatives:
            self._consume_deferred_generator(state, alternative)
        node = value.generator_node
        if node is None or value.generator_truth is False:
            return
        generator_state = state.clone()
        for name, captured in value.closure:
            if name not in generator_state.values:
                generator_state.write(name, captured)
        for name, cell_id in value.closure_cells:
            generator_state.bindings[name] = cell_id
            generator_state.values[name] = generator_state.cells.get(
                cell_id, _EMPTY_VALUE
            )
        for index, generator in enumerate(node.generators):
            iterable = (
                value
                if index == 0
                else self._expression(generator_state, generator.iter)
            )
            if index > 0 and iterable.truth is False:
                return
            item = iterable.items[0] if iterable.items else _EMPTY_VALUE
            self._bind(generator_state, generator.target, item)
            for condition in generator.ifs:
                predicate = self._expression(generator_state, condition)
                if self._truth(condition, predicate) is False:
                    return
        self._expression(generator_state, node.elt)

    def _invoke_callable(
        self,
        state: _IdentityState,
        callee: _IdentityValue,
        arguments: list[_IdentityValue],
        keywords: dict[str, _IdentityValue],
        call_site: ast.AST | None = None,
        force: bool = False,
    ) -> _IdentityValue:
        if callee.kind == "wraps-decorator":
            return arguments[0] if arguments else _EMPTY_VALUE
        if callee.target_key is not None:
            self._record_target(state, callee.target_key, call_site or self.origin)
        if callee.execution_kind != "sync" and not force:
            return replace(
                callee,
                kind="deferred-call",
                bound_arguments=tuple(arguments),
                bound_keywords=tuple(keywords.items()),
            )
        if self.mode == "callable" and callee.kind == "object":
            anchor_state = self._call_state(state, callee, arguments, keywords)
            self._record_anchor(
                anchor_state, call_site or callee.callable_node or self.origin
            )
            return _EMPTY_VALUE
        node = callee.callable_node
        if node is None:
            return _EMPTY_VALUE
        depth = self.call_stack.get(id(node), 0)
        if depth >= 8:
            return self._widen_recursive_call(state, callee, arguments, keywords)
        self.call_stack[id(node)] = depth + 1
        if call_site is not None:
            self.call_sites.append(call_site)
        try:
            summary = self._callable_summary(state, callee, arguments, keywords)
            return self._apply_callable_summary(state, callee, summary)
        finally:
            if call_site is not None:
                self.call_sites.pop()
            if depth:
                self.call_stack[id(node)] = depth
            else:
                self.call_stack.pop(id(node), None)

    def _apply_callable_summary(
        self,
        state: _IdentityState,
        callee: _IdentityValue,
        summary: _CallableSummary,
    ) -> _IdentityValue:
        state.cells.update(summary.cells)
        state.frame_serial = max(state.frame_serial, summary.frame_serial)
        state.sync_cells()
        for name, value in summary.effects:
            if not callee.module_id or callee.module_id == state.module_id:
                state.write(name, value)
        if summary.completion == "raise":
            self._record_exception(state, summary.exception)
            state.completion = "raise"
            state.exception = summary.exception
        self.escaped = self.escaped or summary.escaped
        return summary.result

    def _widen_recursive_call(
        self,
        state: _IdentityState,
        callee: _IdentityValue,
        arguments: list[_IdentityValue],
        keywords: dict[str, _IdentityValue],
    ) -> _IdentityValue:
        node = callee.callable_node
        if node is None or id(node) in self.widened_calls:
            self.escaped = True
            return _UNKNOWN_VALUE
        self.widened_calls.add(id(node))
        try:
            widened = [
                _UNKNOWN_VALUE if value.scalar_known else value for value in arguments
            ]
            summary = self._callable_summary(state, callee, widened, keywords)
            return self._apply_callable_summary(state, callee, summary)
        finally:
            self.widened_calls.remove(id(node))

    def _callable_summary(
        self,
        state: _IdentityState,
        callee: _IdentityValue,
        arguments: list[_IdentityValue],
        keywords: dict[str, _IdentityValue],
    ) -> _CallableSummary:
        node = callee.callable_node
        if node is None:
            return _CallableSummary(result=_UNKNOWN_VALUE, escaped=True)
        call_state = self._call_state(state, callee, arguments, keywords)
        escaped_before = self.escaped
        if isinstance(node, ast.Lambda):
            result = self._expression(call_state, node.body)
            outcomes = [call_state]
        else:
            outcomes = self.process([call_state], node.body)
            result = self._merge_values(
                [item.result for item in outcomes if item.completion == "return"]
            )
        effects = _summary_effects(outcomes, _scope_effect_names(node))
        completion = _summary_completion(outcomes)
        return _CallableSummary(
            result=result,
            effects=effects,
            completion=completion,
            exception=_summary_exception(outcomes),
            escaped=self.escaped and not escaped_before,
            cells=_summary_cells(outcomes),
            frame_serial=max((item.frame_serial for item in outcomes), default=0),
        )

    def _call_state(
        self,
        state: _IdentityState,
        callee: _IdentityValue,
        arguments: list[_IdentityValue],
        keywords: dict[str, _IdentityValue],
    ) -> _IdentityState:
        node = callee.callable_node
        if node is None:
            return state.clone()
        module_entries = callee.module_entries
        if not module_entries and callee.module_id == state.module_id:
            module_entries = state.module_entries
        call_state = _IdentityState(
            cells=dict(state.cells),
            path=state.path,
            frame_serial=state.frame_serial + 1,
            module_id=callee.module_id or state.module_id,
            module_entries=module_entries,
        )
        for name, value in module_entries:
            call_state.write(name, value)
        for name, value in callee.closure:
            call_state.write(name, value)
        for name, cell_id in callee.closure_cells:
            call_state.bindings[name] = cell_id
            call_state.values[name] = call_state.cells.get(cell_id, _EMPTY_VALUE)
        local_names = (
            {argument.arg for argument in _function_arguments(node.args)}
            if isinstance(node, ast.Lambda)
            else _local_bindings(node)
        )
        for name in local_names:
            call_state.values.setdefault(name, _EMPTY_VALUE)
            call_state.bindings.pop(name, None)
            call_state.ensure_cell(name)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            call_state.write(node.name, callee)
        self._bind_parameters(call_state, callee, arguments, keywords)
        return call_state

    def _expression(self, state: _IdentityState, node: ast.expr) -> _IdentityValue:
        raise NotImplementedError

    def _consumes(self, value: _IdentityValue) -> bool:
        raise NotImplementedError

    def _record_anchor(self, state: _IdentityState, node: ast.AST) -> None:
        raise NotImplementedError

    def _record_consumption(self, state: _IdentityState, node: ast.AST) -> None:
        raise NotImplementedError

    def _record_target(
        self, state: _IdentityState, target: tuple[str, str], node: ast.AST
    ) -> None:
        raise NotImplementedError

    def _record_exception(self, state: _IdentityState, name: str | None) -> None:
        raise NotImplementedError

    def _bind(
        self, state: _IdentityState, target: ast.expr, value: _IdentityValue
    ) -> None:
        raise NotImplementedError

    def _truth(self, node: ast.AST, value: _IdentityValue) -> bool | None:
        raise NotImplementedError

    def process(
        self, states: list[_IdentityState], statements: Sequence[ast.stmt]
    ) -> list[_IdentityState]:
        raise NotImplementedError
