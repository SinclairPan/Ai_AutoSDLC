"""按真实语句控制流投影高阶包装器的参数副作用。"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Protocol

from ai_sdlc.core.lean_code_call_arguments import _bound_call_arguments
from ai_sdlc.core.lean_code_control_flow import _static_truth
from ai_sdlc.core.lean_code_static_values import _constant_subscript_value


class _ProjectionContext(Protocol):
    typing_modules: set[str]
    type_hint_functions: set[str]


@dataclass(frozen=True)
class _ParameterProjection:
    names: frozenset[str]
    uncertain: bool = False


_ContextManagerProjection = tuple[
    _ParameterProjection | None,
    _ParameterProjection | None,
]


@dataclass(frozen=True)
class _ParameterEffects:
    invoked: frozenset[str]
    type_hints: frozenset[str]
    uncertain: bool


@dataclass
class _ProjectionState:
    aliases: dict[str, _ParameterProjection]
    invoked: set[str] = field(default_factory=set)
    type_hints: set[str] = field(default_factory=set)
    uncertain: bool = False
    context_managers: dict[str, _ContextManagerProjection] = field(
        default_factory=dict
    )
    control: str = "normal"
    exits: list[_ProjectionState] = field(default_factory=list)

    def fork(self) -> _ProjectionState:
        return _ProjectionState(
            aliases=dict(self.aliases),
            invoked=set(self.invoked),
            type_hints=set(self.type_hints),
            uncertain=self.uncertain,
            context_managers=dict(self.context_managers),
        )


class _ProjectionExpressionFinder(ast.NodeVisitor):
    def __init__(
        self,
        state: _ProjectionState,
        context: _ProjectionContext,
    ) -> None:
        self.state = state
        self.context = context

    def visit_Call(self, node: ast.Call) -> None:
        projection = _parameter_projection(node.func, self.state)
        if projection is not None:
            self.state.invoked.update(projection.names)
            self.state.uncertain |= projection.uncertain
        if _is_type_hints_call(node, self.context):
            projection = _parameter_projection(
                _type_hint_argument(node),
                self.state,
            )
            if projection is not None:
                self.state.type_hints.update(projection.names)
                self.state.uncertain |= projection.uncertain
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        _bind_projection_target(node.target, node.value, self.state)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.visit(node.test)
        truth = _static_truth(node.test)
        if truth is not None:
            self.visit(node.body if truth else node.orelse)
            return
        branches = [self.state.fork(), self.state.fork()]
        _ProjectionExpressionFinder(branches[0], self.context).visit(node.body)
        _ProjectionExpressionFinder(branches[1], self.context).visit(node.orelse)
        _merge_projection_states(self.state, branches)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        branches = []
        for value in _possible_bool_values(node):
            branch = self.state.fork()
            _ProjectionExpressionFinder(branch, self.context).visit(value)
            branches.append(branch)
        _merge_projection_states(self.state, branches)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


def _bind_projection_target(
    target: ast.expr,
    value: ast.expr | _ParameterProjection | None,
    state: _ProjectionState,
) -> None:
    if isinstance(target, ast.Name):
        projection = (
            value
            if isinstance(value, _ParameterProjection)
            else _parameter_projection(value, state)
        )
        _bind_alias(target.id, projection, state)
        return
    if isinstance(target, ast.Starred):
        projection = _uncertain_projection(value, state)
        _bind_projection_target(target.value, projection, state)
        return
    if not isinstance(target, (ast.Tuple, ast.List)):
        return
    values = _literal_sequence(value) if isinstance(value, ast.expr) else None
    if values is not None and len(values) == len(target.elts):
        for child, child_value in zip(target.elts, values, strict=True):
            _bind_projection_target(child, child_value, state)
        return
    projection = _uncertain_projection(value, state)
    for name in _target_names(target):
        _bind_alias(name, projection, state)


def _parameter_projection(
    expression: ast.expr | None,
    state: _ProjectionState,
) -> _ParameterProjection | None:
    if isinstance(expression, ast.Name):
        return state.aliases.get(expression.id)
    if isinstance(expression, ast.NamedExpr):
        return _parameter_projection(expression.value, state)
    if isinstance(expression, ast.Subscript):
        selected = _constant_subscript_value(expression)
        return _parameter_projection(selected or expression.value, state)
    if isinstance(expression, ast.IfExp):
        truth = _static_truth(expression.test)
        if truth is not None:
            selected = expression.body if truth else expression.orelse
            return _parameter_projection(selected, state)
        return _merge_projection_values(
            [
                _parameter_projection(expression.body, state),
                _parameter_projection(expression.orelse, state),
            ]
        )
    if isinstance(expression, ast.BoolOp):
        return _merge_projection_values(
            [
                _parameter_projection(value, state)
                for value in _possible_bool_values(expression)
            ]
        )
    if isinstance(expression, ast.Call):
        projection = _merge_projection_values(
            [
                *(_parameter_projection(value, state) for value in expression.args),
                *(
                    _parameter_projection(keyword.value, state)
                    for keyword in expression.keywords
                ),
            ]
        )
        return (
            _ParameterProjection(projection.names, True)
            if projection is not None
            else None
        )
    return None


def _merge_projection_states(
    target: _ProjectionState,
    branches: list[_ProjectionState],
) -> None:
    active = [branch for branch in branches if branch.control == "normal"]
    alias_sources = active or branches
    terminal = [
        branch.fork()
        for branch in branches
        if branch.control not in {"normal", "terminated"}
    ]
    nested_exits = [item for branch in branches for item in branch.exits]
    effect_sources = [*branches, *nested_exits]
    names = set().union(*(branch.aliases for branch in alias_sources))
    target.aliases = {
        name: projection
        for name in names
        if (
            projection := _merge_projection_values(
                [branch.aliases.get(name) for branch in alias_sources]
            )
        )
    }
    target.context_managers = _merge_context_manager_projections(
        alias_sources
    )
    invoked_sets = {frozenset(branch.invoked) for branch in effect_sources}
    hint_sets = {frozenset(branch.type_hints) for branch in effect_sources}
    target.invoked = set().union(*(branch.invoked for branch in effect_sources))
    target.type_hints = set().union(*(branch.type_hints for branch in effect_sources))
    target.uncertain = (
        any(branch.uncertain for branch in effect_sources)
        or len(invoked_sets) > 1
        or len(hint_sets) > 1
    )
    target.control = "normal" if active else "terminated"
    target.exits.extend([*terminal, *nested_exits])


def _merge_context_manager_projections(
    branches: list[_ProjectionState],
) -> dict[str, _ContextManagerProjection]:
    names = set().union(*(branch.context_managers for branch in branches))
    return {
        name: (
            _merge_projection_values(
                [
                    projection[0] if projection is not None else None
                    for branch in branches
                    if (projection := branch.context_managers.get(name))
                    is not None
                ]
            ),
            _merge_projection_values(
                [
                    projection[1] if projection is not None else None
                    for branch in branches
                    if (projection := branch.context_managers.get(name))
                    is not None
                ]
            ),
        )
        for name in names
    }


def _merge_projection_values(
    values: list[_ParameterProjection | None],
) -> _ParameterProjection | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    names = frozenset().union(*(value.names for value in present))
    return _ParameterProjection(
        names,
        len(present) != len(values)
        or any(value.names != present[0].names for value in present[1:])
        or any(value.uncertain for value in present),
    )


def _bind_alias(
    name: str,
    projection: _ParameterProjection | None,
    state: _ProjectionState,
) -> None:
    state.context_managers.pop(name, None)
    if projection is None:
        state.aliases.pop(name, None)
    else:
        state.aliases[name] = projection


def _uncertain_expression_projection(
    expression: ast.expr,
    state: _ProjectionState,
) -> _ParameterProjection | None:
    return _uncertain_projection(expression, state)


def _uncertain_projection(
    value: ast.expr | _ParameterProjection | None,
    state: _ProjectionState,
) -> _ParameterProjection | None:
    projection = (
        value
        if isinstance(value, _ParameterProjection)
        else _parameter_or_sequence_projection(value, state)
    )
    return (
        _ParameterProjection(projection.names, True) if projection is not None else None
    )


def _parameter_or_sequence_projection(
    value: ast.expr | None,
    state: _ProjectionState,
) -> _ParameterProjection | None:
    values = _literal_sequence(value)
    if values is None:
        return _parameter_projection(value, state)
    return _merge_projection_values(
        [_parameter_projection(item, state) for item in values]
    )


def _is_type_hints_call(
    node: ast.Call,
    context: _ProjectionContext,
) -> bool:
    return (
        isinstance(node.func, ast.Name) and node.func.id in context.type_hint_functions
    ) or (
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in context.typing_modules
        and node.func.attr == "get_type_hints"
    )


def _type_hint_argument(node: ast.Call) -> ast.expr | None:
    signature = ast.arguments(
        posonlyargs=[],
        args=[ast.arg(arg="obj")],
        vararg=None,
        kwonlyargs=[],
        kw_defaults=[],
        kwarg=None,
        defaults=[],
    )
    bound, _ = _bound_call_arguments(signature, node)
    return bound.get("obj")


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


def _literal_sequence(expression: ast.expr | None) -> tuple[ast.expr, ...] | None:
    if isinstance(expression, (ast.List, ast.Tuple)):
        return tuple(expression.elts)
    return None


def _target_names(target: ast.expr) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    if isinstance(target, (ast.Tuple, ast.List)):
        return set().union(*(_target_names(child) for child in target.elts))
    return set()


def _pattern_names(pattern: ast.pattern) -> set[str]:
    return {
        child.name
        for child in ast.walk(pattern)
        if isinstance(child, (ast.MatchAs, ast.MatchStar)) and child.name is not None
    }


def _pattern_is_irrefutable(pattern: ast.pattern) -> bool:
    if isinstance(pattern, ast.MatchAs):
        return pattern.pattern is None or _pattern_is_irrefutable(pattern.pattern)
    if isinstance(pattern, ast.MatchOr):
        return any(_pattern_is_irrefutable(child) for child in pattern.patterns)
    return False


def _imported_names(node: ast.Import | ast.ImportFrom) -> set[str]:
    return {
        alias.asname or alias.name.split(".", 1)[0]
        for alias in node.names
        if alias.name != "*"
    }


__all__: list[str] = []
