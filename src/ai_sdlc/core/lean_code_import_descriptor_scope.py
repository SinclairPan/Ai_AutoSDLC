"""按词法作用域解析内建描述符装饰器的真实绑定。"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from ai_sdlc.core.lean_code_callback_effects import (
    _callback_summary,
    _CallbackSummary,
    _expression_callback_summary,
)
from ai_sdlc.core.lean_code_control_flow import _future_annotations_enabled
from ai_sdlc.core.lean_code_descriptor_effects import (
    _annotation_descriptor_mutations,
    _DefinitionTimeEffectFinder,
    _expression_mutations,
    _function_descriptor_mutations,
)
from ai_sdlc.core.lean_code_descriptor_state_merge import (
    _merge_descriptor_states,
)
from ai_sdlc.core.lean_code_scope import _bound_names, _local_bindings

_DESCRIPTORS = frozenset({"classmethod", "property", "staticmethod"})
_RESOLVED_MARKER = "_ai_sdlc_descriptor_decorators"


@dataclass
class _DescriptorState:
    bindings: dict[str, str] = field(
        default_factory=lambda: {name: name for name in _DESCRIPTORS}
    )
    builtin_modules: set[str] = field(default_factory=set)
    mutators: dict[str, frozenset[str]] = field(default_factory=dict)
    callback_parameters: dict[str, _CallbackSummary] = field(default_factory=dict)
    latent_mutators: dict[str, frozenset[str]] = field(default_factory=dict)
    typing_modules: set[str] = field(default_factory=set)
    type_hint_functions: set[str] = field(default_factory=set)
    evaluate_annotations: bool = True

    def fork(self) -> _DescriptorState:
        return _DescriptorState(
            dict(self.bindings),
            set(self.builtin_modules),
            dict(self.mutators),
            dict(self.callback_parameters),
            dict(self.latent_mutators),
            set(self.typing_modules),
            set(self.type_hint_functions),
            self.evaluate_annotations,
        )


def _resolve_descriptor_decorators(tree: ast.Module) -> None:
    _compile_block(
        tree.body,
        _DescriptorState(
            evaluate_annotations=not _future_annotations_enabled(tree)
        ),
    )


def _compile_block(
    statements: list[ast.stmt],
    state: _DescriptorState,
) -> None:
    for statement in statements:
        _compile_statement(statement, state)


def _compile_statement(
    statement: ast.stmt,
    state: _DescriptorState,
) -> None:
    if isinstance(statement, ast.Import):
        _apply_import(statement, state)
        return
    if isinstance(statement, ast.ImportFrom):
        _apply_import_from(statement, state)
        return
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
        _compile_function_definition(statement, state)
        return
    if isinstance(statement, ast.ClassDef):
        callable_mutations = frozenset().union(
            *(
                _function_descriptor_mutations(child, state)
                for child in statement.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and child.name == "__call__"
            )
        )
        _compile_block(statement.body, state.fork())
        _bind_name(statement.name, "", state)
        _bind_mutator(statement.name, callable_mutations, state)
        _invalidate_after_definition_time_effect(statement, state)
        return
    if isinstance(statement, (ast.Assign, ast.AnnAssign)):
        _compile_assignment(statement, state)
        return
    if isinstance(statement, ast.Delete):
        for target in statement.targets:
            for name in _bound_names(target):
                _restore_default(name, state)
        return
    branches = _nested_blocks(statement)
    if branches:
        rebound = _compound_bound_names(statement)
        outcomes = []
        for branch in branches:
            candidate = state.fork()
            for name in rebound:
                _bind_name(name, "", candidate)
            _compile_block(branch, candidate)
            outcomes.append(candidate)
        _merge_descriptor_states(state, outcomes)
        for name in rebound:
            _bind_name(name, "", state)
    _invalidate_after_definition_time_effect(statement, state)


def _compile_function_definition(
    statement: ast.FunctionDef | ast.AsyncFunctionDef,
    state: _DescriptorState,
) -> None:
    _annotate_function(statement, state)
    mutations = _function_descriptor_mutations(statement, state)
    callbacks = _callback_summary(statement, state)
    latent = (
        frozenset()
        if state.evaluate_annotations
        else _annotation_descriptor_mutations(statement, state)
    )
    nested = state.fork()
    for name in _local_bindings(statement):
        _bind_name(name, "", nested)
    _compile_block(statement.body, nested)
    _bind_name(statement.name, "", state)
    _bind_mutator(statement.name, mutations, state)
    if callbacks is not None:
        state.callback_parameters[statement.name] = callbacks
    if latent:
        state.latent_mutators[statement.name] = latent
    _invalidate_after_definition_time_effect(statement, state)


def _compile_assignment(
    statement: ast.Assign | ast.AnnAssign,
    state: _DescriptorState,
) -> None:
    value = statement.value
    mutations = _expression_mutations(value, state)
    type_hint_function = _is_type_hint_expression(value, state)
    callback = _expression_callback_summary(value, state)
    for target in _assignment_targets(statement):
        kind = _expression_kind(value, state) if value is not None else ""
        for name in _bound_names(target):
            _bind_name(name, kind, state)
            _bind_mutator(name, mutations, state)
            if callback is not None:
                state.callback_parameters[name] = callback
            if type_hint_function:
                state.type_hint_functions.add(name)
    _invalidate_after_definition_time_effect(statement, state)


def _assignment_targets(
    statement: ast.Assign | ast.AnnAssign,
) -> list[ast.expr]:
    return (
        statement.targets
        if isinstance(statement, ast.Assign)
        else [statement.target]
    )


def _annotate_function(
    statement: ast.FunctionDef | ast.AsyncFunctionDef,
    state: _DescriptorState,
) -> None:
    kinds = tuple(
        _expression_kind(decorator, state)
        for decorator in statement.decorator_list
    )
    setattr(statement, _RESOLVED_MARKER, kinds)


def _resolved_descriptor_kinds(
    statement: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[str, ...] | None:
    value = getattr(statement, _RESOLVED_MARKER, None)
    return tuple(value) if isinstance(value, tuple) else None


def _expression_kind(
    expression: ast.expr | None,
    state: _DescriptorState,
) -> str:
    if isinstance(expression, ast.Name):
        return state.bindings.get(expression.id, "")
    if (
        isinstance(expression, ast.Attribute)
        and isinstance(expression.value, ast.Name)
        and expression.value.id in state.builtin_modules
        and expression.attr in _DESCRIPTORS
    ):
        return expression.attr
    return ""


def _apply_import(node: ast.Import, state: _DescriptorState) -> None:
    for alias in node.names:
        name = alias.asname or alias.name.split(".", 1)[0]
        _bind_name(name, "", state)
        if alias.name == "builtins":
            state.builtin_modules.add(name)
        if alias.name == "typing":
            state.typing_modules.add(name)


def _apply_import_from(
    node: ast.ImportFrom,
    state: _DescriptorState,
) -> None:
    for alias in node.names:
        if alias.name == "*":
            for name in tuple(state.bindings):
                if state.bindings[name]:
                    state.bindings[name] = ""
            continue
        name = alias.asname or alias.name
        kind = alias.name if node.module == "builtins" else ""
        _bind_name(name, kind if kind in _DESCRIPTORS else "", state)
        if node.module == "typing" and alias.name == "get_type_hints":
            state.type_hint_functions.add(name)


def _bind_name(
    name: str,
    kind: str,
    state: _DescriptorState,
) -> None:
    state.bindings[name] = kind
    state.builtin_modules.discard(name)
    state.mutators.pop(name, None)
    state.callback_parameters.pop(name, None)
    state.latent_mutators.pop(name, None)
    state.typing_modules.discard(name)
    state.type_hint_functions.discard(name)


def _bind_mutator(
    name: str,
    mutations: frozenset[str],
    state: _DescriptorState,
) -> None:
    if mutations:
        state.mutators[name] = mutations


def _restore_default(name: str, state: _DescriptorState) -> None:
    _bind_name(name, name if name in _DESCRIPTORS else "", state)


def _nested_blocks(statement: ast.stmt) -> tuple[list[ast.stmt], ...]:
    if isinstance(statement, (ast.If, ast.For, ast.AsyncFor, ast.While)):
        return statement.body, statement.orelse
    if isinstance(statement, (ast.With, ast.AsyncWith)):
        return (statement.body,)
    if isinstance(statement, (ast.Try, ast.TryStar)):
        return (
            statement.body,
            *(handler.body for handler in statement.handlers),
            statement.orelse,
            statement.finalbody,
        )
    if isinstance(statement, ast.Match):
        return tuple(case.body for case in statement.cases)
    return ()


def _invalidate_after_definition_time_effect(
    statement: ast.stmt,
    state: _DescriptorState,
) -> None:
    finder = _DefinitionTimeEffectFinder(state)
    finder.visit(statement)
    for name in finder.mutations:
        state.bindings[name] = ""


def _compound_bound_names(statement: ast.stmt) -> set[str]:
    if isinstance(statement, (ast.For, ast.AsyncFor)):
        return _bound_names(statement.target)
    if isinstance(statement, (ast.With, ast.AsyncWith)):
        return set().union(
            *(
                _bound_names(item.optional_vars)
                for item in statement.items
                if item.optional_vars is not None
            )
        )
    if isinstance(statement, ast.Match):
        finder = _PatternNameFinder()
        for case in statement.cases:
            finder.visit(case.pattern)
        return finder.names
    if isinstance(statement, (ast.Try, ast.TryStar)):
        return {
            handler.name
            for handler in statement.handlers
            if handler.name is not None
        }
    return set()


def _is_type_hint_expression(
    expression: ast.expr | None,
    state: _DescriptorState,
) -> bool:
    if isinstance(expression, ast.Name):
        return expression.id in state.type_hint_functions
    if isinstance(expression, ast.NamedExpr):
        return _is_type_hint_expression(expression.value, state)
    if isinstance(expression, ast.Attribute):
        return (
            isinstance(expression.value, ast.Name)
            and expression.value.id in state.typing_modules
            and expression.attr == "get_type_hints"
        )
    if isinstance(expression, ast.Subscript):
        from ai_sdlc.core.lean_code_static_values import _constant_subscript_value

        selected = _constant_subscript_value(expression)
        return selected is not None and _is_type_hint_expression(selected, state)
    if isinstance(expression, ast.IfExp):
        return _is_type_hint_expression(
            expression.body, state
        ) or _is_type_hint_expression(expression.orelse, state)
    if isinstance(expression, ast.BoolOp):
        return any(
            _is_type_hint_expression(value, state)
            for value in expression.values
        )
    return False


class _PatternNameFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name is not None:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name is not None:
            self.names.add(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest is not None:
            self.names.add(node.rest)
        self.generic_visit(node)


__all__: list[str] = []
