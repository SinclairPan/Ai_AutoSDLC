"""维护动态工厂控制流中的表达式与绑定效果。"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass, field

from ai_sdlc.core.lean_code_control_flow import _function_annotation_expressions
from ai_sdlc.core.lean_code_import_binding_flow import (
    _assigned_dynamic_aliases,
    _contained_dynamic_kinds,
)
from ai_sdlc.core.lean_code_import_class_aliases import (
    _assigned_dynamic_class_callables,
    _discard_callable_binding,
)
from ai_sdlc.core.lean_code_import_expression_flow import (
    _expression_calls_dynamic,
    _generator_consumption_paths,
    _named_expression_binding_paths,
)
from ai_sdlc.core.lean_code_path_summary import _binding_path_overflowed
from ai_sdlc.core.lean_code_scope import _bound_names


@dataclass
class _FlowState:
    modules: set[str]
    callables: set[str]
    returned: bool = False
    called: bool = False
    control: str = "normal"
    deferred_generators: dict[str, ast.GeneratorExp] = field(default_factory=dict)

    def fork(self) -> _FlowState:
        return _FlowState(
            set(self.modules),
            set(self.callables),
            self.returned,
            self.called,
            self.control,
            dict(self.deferred_generators),
        )


def _apply_expression(expression: ast.expr, state: _FlowState) -> None:
    state.called = state.called or _expression_calls_dynamic(
        expression,
        state.modules,
        state.callables,
    )
    paths = _named_expression_binding_paths(expression)
    if not paths:
        return
    branches: list[_FlowState] = []
    for path in paths:
        branch = state.fork()
        branch.called = branch.called or _binding_path_overflowed(path)
        for target, value in path:
            _apply_alias_binding(target, value, branch)
        branches.append(branch)
    state.modules = set().union(*(branch.modules for branch in branches))
    state.callables = set().union(*(branch.callables for branch in branches))
    state.called = any(branch.called for branch in branches)


def _apply_alias_binding(
    target: ast.expr,
    value: ast.expr,
    state: _FlowState,
) -> None:
    assignment = ast.Assign(targets=[target], value=value)
    assigned_modules, assigned_callables = _assigned_dynamic_aliases(
        assignment,
        state.modules,
        state.callables,
    )
    class_callables = _assigned_dynamic_class_callables(
        assignment,
        state.callables,
    )
    for name in _bound_names(target):
        state.modules.discard(name)
        _discard_callable_binding(state.callables, name)
        deferred = _deferred_generator_value(value, state)
        if deferred is None:
            state.deferred_generators.pop(name, None)
        else:
            state.deferred_generators[name] = deferred
    state.modules.update(assigned_modules)
    state.callables.update({*assigned_callables, *class_callables})


def _apply_generator_iteration(expression: ast.expr, state: _FlowState) -> None:
    generator = (
        expression
        if isinstance(expression, ast.GeneratorExp)
        else state.deferred_generators.get(expression.id)
        if isinstance(expression, ast.Name)
        else None
    )
    if generator is None:
        return
    paths = _generator_consumption_paths(generator)
    branches: list[_FlowState] = []
    for path in paths:
        branch = state.fork()
        branch.called = branch.called or _binding_path_overflowed(path)
        for target, value in path:
            _apply_alias_binding(target, value, branch)
        branches.append(branch)
    state.modules = set().union(*(branch.modules for branch in branches))
    state.callables = set().union(*(branch.callables for branch in branches))
    state.called = any(branch.called for branch in branches)


def _deferred_generator_value(
    value: ast.expr,
    state: _FlowState,
) -> ast.GeneratorExp | None:
    if isinstance(value, ast.GeneratorExp):
        return value
    if isinstance(value, ast.Name):
        return state.deferred_generators.get(value.id)
    return None


def _replace_definition(
    name: str,
    state: _FlowState,
    known_callables: frozenset[str],
) -> None:
    state.modules.discard(name)
    _discard_callable_binding(state.callables, name)
    state.deferred_generators.pop(name, None)
    state.callables.update(
        alias
        for alias in known_callables
        if alias == name or alias.startswith(f"{name}.")
    )


def _visit_function_header(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    state: _FlowState,
) -> None:
    for expression in (
        *node.decorator_list,
        *getattr(node, "type_params", ()),
        *node.args.defaults,
        *(item for item in node.args.kw_defaults if item is not None),
        *_function_annotation_expressions(node),
    ):
        _apply_expression(expression, state)


def _dynamic_value(value: ast.expr, state: _FlowState) -> bool:
    return "callable" in _contained_dynamic_kinds(
        value,
        state.modules,
        state.callables,
    )


def _deduplicate_states(states: Iterable[_FlowState]) -> list[_FlowState]:
    unique: dict[
        tuple[
            tuple[str, ...],
            tuple[str, ...],
            bool,
            bool,
            str,
            tuple[tuple[str, str], ...],
        ],
        _FlowState,
    ] = {}
    for state in states:
        key = (
            tuple(sorted(state.modules)),
            tuple(sorted(state.callables)),
            state.returned,
            state.called,
            state.control,
            tuple(
                sorted(
                    (name, ast.dump(generator))
                    for name, generator in state.deferred_generators.items()
                )
            ),
        )
        unique.setdefault(key, state)
    if len(unique) <= 128:
        return list(unique.values())
    overflow = _FlowState(
        modules=set().union(*(state.modules for state in unique.values())),
        callables=set().union(*(state.callables for state in unique.values())),
        returned=any(state.returned for state in unique.values()),
        called=any(state.called for state in unique.values()),
        deferred_generators={
            name: generator
            for state in unique.values()
            for name, generator in state.deferred_generators.items()
        },
    )
    return [overflow]


__all__: list[str] = []
