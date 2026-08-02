"""在单一执行域内保守传播延迟对象身份。"""

from __future__ import annotations

import ast
from collections.abc import Sequence

from ai_sdlc.core.lean_code_execution_order import _start_location
from ai_sdlc.core.lean_code_flow import _pattern_bound_names
from ai_sdlc.core.lean_code_identity_definitions import _IdentityDefinitionMixin
from ai_sdlc.core.lean_code_identity_join import _deduplicate_states
from ai_sdlc.core.lean_code_identity_models import (
    _EMPTY_VALUE,
    _IdentityState,
    _IdentityTrace,
    _IdentityValue,
    _import_binding_key,
    _import_binding_prefix,
    _initial_identity_state,
)
from ai_sdlc.core.lean_code_identity_semantics import (
    _literal_items,
    _pattern_bindings,
    _pattern_matches,
)


def _trace_identity(
    scope: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    origin: ast.AST,
    mode: str,
    generator: ast.GeneratorExp | None = None,
    known_callables: tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...] = (),
    imported_callables: dict[str, _IdentityValue] | None = None,
) -> _IdentityTrace:
    runner = _IdentityRunner(origin, mode, generator, imported_callables or {})
    runner.deferred_annotations = _future_annotations(scope)
    initial = _initial_identity_state(scope)
    for node in known_callables:
        initial.values.setdefault(node.name, _IdentityValue(callable_node=node))
    runner.process([initial], scope.body)
    anchors = tuple(
        sorted(
            {id(node): node for node in runner.anchors}.values(), key=_start_location
        )
    )
    return _IdentityTrace(
        anchors=anchors,
        returned=runner.returned,
        escaped=runner.escaped,
        paths=tuple(runner.anchor_paths),
    )


def _trace_target_calls(
    scope: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    imported_callables: dict[str, _IdentityValue],
) -> tuple[tuple[tuple[str, str], ast.AST], ...]:
    runner = _IdentityRunner(ast.Pass(), "targets", None, imported_callables)
    runner.deferred_annotations = _future_annotations(scope)
    runner.process([_initial_identity_state(scope)], scope.body)
    unique = {
        (target, id(anchor)): (target, anchor)
        for target, anchor in runner.target_anchors
    }
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (item[0], _start_location(item[1])),
        )
    )


def _trace_target_references(
    scope: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    imported_callables: dict[str, _IdentityValue],
) -> tuple[tuple[tuple[str, str], ast.AST], ...]:
    runner = _IdentityRunner(ast.Pass(), "targets", None, imported_callables)
    runner.deferred_annotations = _future_annotations(scope)
    runner.process([_initial_identity_state(scope)], scope.body)
    unique = {
        (target, id(anchor)): (target, anchor)
        for target, anchor in runner.target_reference_anchors
    }
    return tuple(
        sorted(unique.values(), key=lambda item: (item[0], _start_location(item[1])))
    )


class _IdentityRunner(_IdentityDefinitionMixin):
    def __init__(
        self,
        origin: ast.AST,
        mode: str,
        generator: ast.GeneratorExp | None,
        imported_callables: dict[str, _IdentityValue],
    ) -> None:
        self.origin = origin
        self.mode = mode
        self.generator = generator
        self.anchors: list[ast.AST] = []
        self.anchor_paths: list[
            tuple[
                ast.AST,
                tuple[ast.stmt, ...],
                tuple[tuple[str, _IdentityValue], ...],
            ]
        ] = []
        self.returned = False
        self.escaped = False
        self.call_stack: dict[int, int] = {}
        self.widened_calls: set[int] = set()
        self.call_sites: list[ast.AST] = []
        self.exception_states: list[_IdentityState] = []
        self.imported_callables = imported_callables
        self.target_anchors: list[tuple[tuple[str, str], ast.AST]] = []
        self.target_reference_anchors: list[tuple[tuple[str, str], ast.AST]] = []
        self.deferred_annotations = False

    def _record_target_reference(self, value: _IdentityValue, node: ast.AST) -> None:
        if value.target_key is not None:
            self.target_reference_anchors.append((value.target_key, node))

    def process(
        self,
        states: list[_IdentityState],
        statements: Sequence[ast.stmt],
    ) -> list[_IdentityState]:
        current = states
        for statement in statements:
            next_states: list[_IdentityState] = []
            for state in current:
                if state.completion != "normal":
                    next_states.append(state)
                    continue
                state.path = (*state.path, statement)
                next_states.extend(self._statement(state, statement))
            current = _deduplicate_states(next_states)
        return current

    def _statement(self, state: _IdentityState, node: ast.stmt) -> list[_IdentityState]:
        if isinstance(node, ast.If):
            return self._if(state, node)
        if isinstance(node, (ast.For, ast.AsyncFor)):
            return self._for(state, node)
        if isinstance(node, ast.While):
            return self._while(state, node)
        if isinstance(node, ast.Try):
            return self._try(state, node)
        if isinstance(node, ast.Match):
            return self._match(state, node)
        if isinstance(node, (ast.With, ast.AsyncWith)):
            return self._with(state, node)
        return self._simple_statement(state, node)

    def _data_statement(
        self,
        state: _IdentityState,
        node: ast.stmt,
    ) -> list[_IdentityState]:
        if isinstance(node, ast.Assign):
            value = self._expression(state, node.value)
            for target in node.targets:
                self._bind(state, target, value)
            return [state]
        if isinstance(node, ast.AnnAssign):
            value = (
                self._expression(state, node.value)
                if node.value is not None
                else _EMPTY_VALUE
            )
            if node.value is not None:
                self._bind(state, node.target, value)
            if not self.deferred_annotations:
                self._expression(state, node.annotation)
            return [state]
        if isinstance(node, ast.AugAssign):
            self._expression(state, node.value)
            self._bind(state, node.target, _EMPTY_VALUE)
            return [state]
        if isinstance(node, ast.Expr):
            self._expression(state, node.value)
            return [state]
        if isinstance(node, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
            return self._completion_statement(state, node)
        return self._binding_statement(state, node)

    def _completion_statement(
        self,
        state: _IdentityState,
        node: ast.Return | ast.Raise | ast.Break | ast.Continue,
    ) -> list[_IdentityState]:
        if isinstance(node, ast.Return):
            value = (
                self._expression(state, node.value)
                if node.value is not None
                else _EMPTY_VALUE
            )
            self.returned = self.returned or value.tracked
            state.completion = "return"
            state.result = value
            return [state]
        if isinstance(node, ast.Raise):
            if node.exc is not None:
                self._expression(state, node.exc)
            state.completion = "raise"
            state.exception = self._exception_name(node.exc)
            return [state]
        if isinstance(node, ast.Break):
            state.completion = "break"
            return [state]
        if isinstance(node, ast.Continue):
            state.completion = "continue"
            return [state]
        return [state]

    def _binding_statement(
        self,
        state: _IdentityState,
        node: ast.stmt,
    ) -> list[_IdentityState]:
        if isinstance(node, ast.Import):
            self._apply_import(state, node)
            return [state]
        if isinstance(node, ast.ImportFrom):
            self._apply_import_from(state, node)
            return [state]
        if isinstance(node, ast.Delete):
            for target in node.targets:
                self._bind(state, target, _EMPTY_VALUE)
            return [state]
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                self._expression(state, child)
        return [state]

    def _apply_import(self, state: _IdentityState, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.asname or alias.name.split(".", 1)[0]
            imported = self.imported_callables.get(
                _import_binding_key(node, name), self.imported_callables.get(name)
            )
            if alias.name == "functools" and imported is None:
                imported = _IdentityValue(
                    "module",
                    entries=(
                        ("partial", _IdentityValue("functools-partial")),
                        ("wraps", _IdentityValue("functools-wraps")),
                    ),
                    truth=True,
                )
            if alias.name == "asyncio" and imported is None:
                imported = _IdentityValue("asyncio-module", truth=True)
            fallback = "importlib-module" if alias.name == "importlib" else ""
            state.write(name, imported or _IdentityValue(fallback))

    def _apply_import_from(self, state: _IdentityState, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                prefix = _import_binding_prefix(node)
                for key, value in self.imported_callables.items():
                    if key.startswith(prefix):
                        state.write(key.removeprefix(prefix), value)
                continue
            name = alias.asname or alias.name
            imported = self.imported_callables.get(
                _import_binding_key(node, name), self.imported_callables.get(name)
            )
            if node.module == "importlib" and alias.name == "import_module":
                imported = _IdentityValue("importlib-function")
            elif node.module == "functools" and alias.name in {"partial", "wraps"}:
                imported = _IdentityValue(f"functools-{alias.name}")
            state.write(name, imported or _EMPTY_VALUE)

    def _if(self, state: _IdentityState, node: ast.If) -> list[_IdentityState]:
        value = self._expression(state, node.test)
        truth = self._truth(node.test, value)
        if truth is not None:
            return self.process([state], node.body if truth else node.orelse)
        return self.process([state.clone()], node.body) + self.process(
            [state], node.orelse
        )

    def _while(self, state: _IdentityState, node: ast.While) -> list[_IdentityState]:
        value = self._expression(state, node.test)
        truth = self._truth(node.test, value)
        if truth is False:
            return self.process([state], node.orelse)
        body = self.process([state.clone()], node.body)
        breaks = self._complete_as_normal(body, "break")
        continuing = self._complete_as_normal(body, "continue")
        continuing.extend(item for item in body if item.completion == "normal")
        if truth is True:
            return breaks + continuing
        completed = self.process(continuing + [state], node.orelse)
        return breaks + completed

    def _for(
        self, state: _IdentityState, node: ast.For | ast.AsyncFor
    ) -> list[_IdentityState]:
        iterable = self._expression(state, node.iter)
        self._consume_deferred_callable(state, iterable)
        if self._consumes(iterable):
            self._record_consumption(state, node.iter)
        items = _literal_items(node.iter)
        if items == ():
            return self.process([state], node.orelse)
        if items is None and iterable.kind == "container":
            return self._known_for(state, node, iterable.items)
        if items is not None:
            actual_items = (
                iterable.items if len(iterable.items) == len(items) else items
            )
            return self._known_for(state, node, actual_items)
        return self._unknown_for(state, node)

    def _try(self, state: _IdentityState, node: ast.Try) -> list[_IdentityState]:
        outcomes = self._try_body(state.clone(), node.body)
        normal = [item for item in outcomes if item.completion == "normal"]
        terminal = [item for item in outcomes if item.completion != "normal"]
        branches = self.process(normal, node.orelse)
        for raised in (item for item in terminal if item.completion == "raise"):
            branches.extend(self._handle_raise(raised, node.handlers))
        branches.extend(item for item in terminal if item.completion != "raise")
        return self._apply_finally(branches, node.finalbody)

    def _match(self, state: _IdentityState, node: ast.Match) -> list[_IdentityState]:
        subject = self._expression(state, node.subject)
        outcomes: list[_IdentityState] = []
        for case in node.cases:
            matches = _pattern_matches(case.pattern, subject)
            if matches is False:
                continue
            branch = state.clone()
            bindings = _pattern_bindings(case.pattern, subject)
            for name in _pattern_bound_names(case.pattern):
                branch.write(name, bindings.get(name, _EMPTY_VALUE))
            if case.guard is not None:
                guard = self._expression(branch, case.guard)
                if self._truth(case.guard, guard) is False:
                    continue
            outcomes.extend(self.process([branch], case.body))
            if matches is True:
                return outcomes
        return outcomes or [state]

    def _with(
        self, state: _IdentityState, node: ast.With | ast.AsyncWith
    ) -> list[_IdentityState]:
        for item in node.items:
            self._expression(state, item.context_expr)
            if item.optional_vars is not None:
                self._bind(state, item.optional_vars, _EMPTY_VALUE)
        return self.process([state], node.body)


def _future_annotations(
    scope: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> bool:
    return isinstance(scope, ast.Module) and any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in scope.body
    )


__all__: list[str] = []
