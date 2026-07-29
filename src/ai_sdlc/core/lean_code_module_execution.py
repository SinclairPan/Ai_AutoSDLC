"""追踪模块定义期可达调用及其调用时身份。"""

from __future__ import annotations

import ast
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field

from ai_sdlc.core.lean_code_control_flow import _parallel_sequence
from ai_sdlc.core.lean_code_import_binding_flow import _DYNAMIC_CALLABLE_NAMES
from ai_sdlc.core.lean_code_import_factory_flow import _function_uses_dynamic_dependency
from ai_sdlc.core.lean_code_module_compound import _apply_compound_binding
from ai_sdlc.core.lean_code_module_execution_aliases import (
    _function_call_dynamic_aliases,
)
from ai_sdlc.core.lean_code_module_execution_merge import (
    _class_bound_names,
    _copy_members,
    _copy_sets,
    _merge_class_maps,
    _merge_containers,
    _merge_mapping_states,
    _merge_members,
    _merge_set_maps,
    _ordered_members,
)
from ai_sdlc.core.lean_code_module_invocation import _DefinitionTimeCallFinder
from ai_sdlc.core.lean_code_module_parameters import (
    _invocation_key,
    _invocation_parameter_refs,
)
from ai_sdlc.core.lean_code_module_values import (
    _CallableValue,
    _expression_value,
    _registered_callable_returns_dynamic,
)
from ai_sdlc.core.lean_code_scope import _bound_names
from ai_sdlc.core.lean_code_static_values import (
    _UNKNOWN,
    _constant_subscript_value,
    _constant_value,
)

_FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef
_CallableNode = _FunctionNode | ast.Lambda


@dataclass
class _ModuleExecutionState:
    definitions: dict[int, _CallableNode] = field(default_factory=dict)
    default_refs: dict[int, dict[str, _CallableValue]] = field(default_factory=dict)
    aliases: dict[str, set[int]] = field(default_factory=dict)
    containers: dict[str, tuple[set[int], ...]] = field(default_factory=dict)
    mappings: dict[str, dict[object, set[int]]] = field(default_factory=dict)
    classes: dict[str, dict[str, set[int]]] = field(default_factory=dict)
    historically_dynamic_nodes: set[int] = field(default_factory=set)
    shadowed_dynamic_names: set[str] = field(default_factory=set)

    def observe(
        self,
        statement: ast.stmt,
        modules: set[str],
        callables: set[str],
        *,
        evaluate_annotations: bool = True,
    ) -> None:
        finder = _DefinitionTimeCallFinder(
            self,
            dynamic_modules=modules,
            dynamic_names=callables,
            evaluate_annotations=evaluate_annotations,
        )
        finder.visit(statement)
        pending = list(finder.invocations)
        observed: set[
            tuple[int, tuple[tuple[str, tuple[object, ...]], ...]]
        ] = set()
        while pending:
            invocation = pending.pop()
            function = self.definitions.get(invocation.function_id)
            if function is None:
                continue
            parameters = _invocation_parameter_refs(
                function.args,
                invocation,
                self.default_refs.get(invocation.function_id, {}),
            )
            key = _invocation_key(invocation, parameters)
            if key in observed:
                continue
            observed.add(key)
            nested = _DefinitionTimeCallFinder(
                self,
                parameters,
                modules,
                callables,
                evaluate_annotations=evaluate_annotations,
            )
            if isinstance(function, ast.Lambda):
                nested.visit(function.body)
            else:
                nested._visit_statements(function.body)
                self._observe_function(function, modules, callables)
                if nested.dynamic_invoked:
                    self.historically_dynamic_nodes.add(id(function))
            pending.extend(nested.invocations)

    def _observe_function(
        self,
        function: _FunctionNode,
        modules: set[str],
        callables: set[str],
    ) -> None:
        visible_modules, visible_callables = _function_call_dynamic_aliases(
            function,
            modules,
            callables,
        )
        if _function_uses_dynamic_dependency(
            function,
            visible_modules,
            visible_callables,
        ):
            self.historically_dynamic_nodes.add(id(function))

    def apply_binding(self, statement: ast.stmt) -> None:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._kill(statement.name)
            function_id = self.register_callable(statement)
            self.aliases[statement.name] = {function_id}
            return
        if isinstance(statement, ast.ClassDef):
            members = self._class_definition_members(statement)
            self._kill(statement.name)
            self.classes[statement.name] = members
            return
        if _apply_compound_binding(self, statement):
            return
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                self._bind_target(target, statement.value)
        elif isinstance(statement, ast.AnnAssign):
            if statement.value is not None:
                self._bind_target(statement.target, statement.value)
            else:
                for name in _bound_names(statement.target):
                    self._kill(name)

    def fork(self) -> _ModuleExecutionState:
        return _ModuleExecutionState(
            definitions=dict(self.definitions),
            default_refs={
                function_id: dict(values)
                for function_id, values in self.default_refs.items()
            },
            aliases=_copy_sets(self.aliases),
            containers={
                name: tuple(set(refs) for refs in values)
                for name, values in self.containers.items()
            },
            mappings={
                name: _copy_sets(values) for name, values in self.mappings.items()
            },
            classes={
                name: _copy_members(values) for name, values in self.classes.items()
            },
            historically_dynamic_nodes=set(self.historically_dynamic_nodes),
            shadowed_dynamic_names=set(self.shadowed_dynamic_names),
        )

    def merge(self, states: tuple[_ModuleExecutionState, ...]) -> None:
        self.definitions.update(
            {
                function_id: node
                for state in states
                for function_id, node in state.definitions.items()
            }
        )
        self.default_refs.update(
            {
                function_id: values
                for state in states
                for function_id, values in state.default_refs.items()
            }
        )
        self.aliases = _merge_set_maps(tuple(state.aliases for state in states))
        self.classes = _merge_class_maps(tuple(state.classes for state in states))
        self.containers = _merge_containers(
            tuple(state.containers for state in states)
        )
        self.mappings = _merge_mapping_states(
            tuple(state.mappings for state in states)
        )
        self.historically_dynamic_nodes = set().union(
            *(state.historically_dynamic_nodes for state in states)
        )
        self.shadowed_dynamic_names = set.intersection(
            *(set(state.shadowed_dynamic_names) for state in states)
        )

    def register_callable(self, node: _CallableNode) -> int:
        function_id = id(node)
        if function_id not in self.definitions:
            self.definitions[function_id] = node
            self.default_refs[function_id] = _default_parameter_refs(node.args, self)
        return function_id

    def all_callable_refs(self) -> set[int]:
        return set(self.definitions)

    def callable_returns_dynamic(
        self,
        function_id: int,
        modules: Collection[str],
        callables: Collection[str],
    ) -> bool:
        function = self.definitions.get(function_id)
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return False
        return _registered_callable_returns_dynamic(
            self,
            function,
            modules,
            callables,
            self.shadowed_dynamic_names,
        )

    def refs(
        self,
        expression: ast.expr,
        overrides: Mapping[str, set[int]] | None = None,
    ) -> set[int]:
        if isinstance(expression, ast.Name):
            if overrides is not None and expression.id in overrides:
                return set(overrides[expression.id])
            return set(self.aliases.get(expression.id, set()))
        if isinstance(expression, ast.NamedExpr):
            return self.refs(expression.value, overrides)
        if isinstance(expression, ast.Lambda):
            return {self.register_callable(expression)}
        if (
            isinstance(expression, ast.Call)
            and isinstance(expression.func, ast.Name)
            and expression.func.id in {"staticmethod", "classmethod"}
            and expression.args
        ):
            return self.refs(expression.args[0], overrides)
        if isinstance(expression, ast.IfExp):
            return {
                *self.refs(expression.body, overrides),
                *self.refs(expression.orelse, overrides),
            }
        if isinstance(expression, ast.Attribute):
            members = self._class_members(expression.value)
            return set(members.get(expression.attr, set())) if members else set()
        if isinstance(expression, ast.Subscript):
            return self._subscript_refs(expression, overrides)
        return set()

    def _subscript_refs(
        self,
        expression: ast.Subscript,
        overrides: Mapping[str, set[int]] | None,
    ) -> set[int]:
        selected = _constant_subscript_value(expression)
        if selected is not None:
            return self.refs(selected, overrides)
        key = _constant_value(expression.slice)
        if not isinstance(expression.value, ast.Name) or key is _UNKNOWN:
            return set()
        items = self.containers.get(expression.value.id, ())
        if isinstance(key, int) and -len(items) <= key < len(items):
            return set(items[key])
        return set(self.mappings.get(expression.value.id, {}).get(key, set()))

    def _bind_target(self, target: ast.expr, value: ast.expr) -> None:
        if _parallel_sequence(target, value):
            assert isinstance(target, (ast.Tuple, ast.List))
            assert isinstance(value, (ast.Tuple, ast.List))
            for child_target, child_value in zip(
                target.elts,
                value.elts,
                strict=True,
            ):
                self._bind_target(child_target, child_value)
            return
        refs = self.refs(value)
        container = _container_refs(value, self)
        mapping = _mapping_refs(value, self)
        class_members = self._class_members(value)
        for name in _bound_names(target):
            self._kill(name)
            if refs:
                self.aliases[name] = set(refs)
            if container:
                self.containers[name] = container
            if mapping:
                self.mappings[name] = mapping
            if class_members is not None:
                self.classes[name] = _copy_members(class_members)

    def _class_members(self, expression: ast.expr) -> dict[str, set[int]] | None:
        if isinstance(expression, ast.Name):
            members = self.classes.get(expression.id)
            return _copy_members(members) if members is not None else None
        if isinstance(expression, ast.Call):
            return self._class_members(expression.func)
        if isinstance(expression, ast.IfExp):
            candidates = [
                self._class_members(expression.body),
                self._class_members(expression.orelse),
            ]
            present = [members for members in candidates if members is not None]
            return _merge_members(present) if present else None
        return None

    def _class_definition_members(self, node: ast.ClassDef) -> dict[str, set[int]]:
        inherited = [
            members
            for base in node.bases
            if (members := self._class_members(base)) is not None
        ]
        members = _ordered_members(inherited)
        local = self.fork()
        for statement in node.body:
            local.apply_binding(statement)
        for name in _class_bound_names(node.body):
            members[name] = set(local.aliases.get(name, set()))
        return members

    def _kill(self, name: str) -> None:
        self.aliases.pop(name, None)
        self.containers.pop(name, None)
        self.mappings.pop(name, None)
        self.classes.pop(name, None)
        if name in _DYNAMIC_CALLABLE_NAMES:
            self.shadowed_dynamic_names.add(name)


def _container_refs(
    expression: ast.expr,
    state: _ModuleExecutionState,
) -> tuple[set[int], ...]:
    if isinstance(expression, (ast.Tuple, ast.List)):
        return tuple(state.refs(item) for item in expression.elts)
    return ()


def _mapping_refs(
    expression: ast.expr,
    state: _ModuleExecutionState,
) -> dict[object, set[int]]:
    if not isinstance(expression, ast.Dict):
        return {}
    values: dict[object, set[int]] = {}
    for key_node, value in zip(expression.keys, expression.values, strict=True):
        if key_node is None:
            nested = _mapping_refs(value, state)
            if not nested:
                return {}
            values.update(nested)
            continue
        key = _constant_value(key_node)
        if key is _UNKNOWN:
            return {}
        values[key] = state.refs(value)
    return values


def _default_parameter_refs(
    arguments: ast.arguments,
    state: _ModuleExecutionState,
) -> dict[str, _CallableValue]:
    positional = [*arguments.posonlyargs, *arguments.args]
    pairs = list(
        zip(
            positional[-len(arguments.defaults) :],
            arguments.defaults,
            strict=True,
        )
        if arguments.defaults
        else ()
    )
    pairs.extend(
        (argument, default)
        for argument, default in zip(
            arguments.kwonlyargs,
            arguments.kw_defaults,
            strict=True,
        )
        if default is not None
    )
    return {
        argument.arg: _expression_value(state, default)
        for argument, default in pairs
    }


__all__: list[str] = []
