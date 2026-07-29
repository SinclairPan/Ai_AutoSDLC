"""把复合语句的运行时目标映射回包装器参数投影。"""

from __future__ import annotations

import ast

from ai_sdlc.core.lean_code_callback_projection import (
    _bind_alias,
    _bind_projection_target,
    _ContextManagerProjection,
    _literal_sequence,
    _merge_projection_values,
    _parameter_projection,
    _ParameterProjection,
    _possible_bool_values,
    _ProjectionContext,
    _ProjectionExpressionFinder,
    _ProjectionState,
    _target_names,
    _uncertain_projection,
)
from ai_sdlc.core.lean_code_control_flow import _static_truth
from ai_sdlc.core.lean_code_scope import _local_bindings
from ai_sdlc.core.lean_code_static_values import _constant_subscript_value


def _iteration_projection(
    expression: ast.expr,
    state: _ProjectionState,
    context: _ProjectionContext,
) -> _ParameterProjection | None:
    values = _literal_sequence(expression)
    if values is not None:
        return _merge_projection_values(
            [_parameter_projection(value, state) for value in values]
        )
    if isinstance(expression, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
        return _comprehension_projection(
            expression.elt,
            expression.generators,
            state,
            context,
        )
    if isinstance(expression, ast.DictComp):
        return _comprehension_projection(
            expression.key,
            expression.generators,
            state,
            context,
        )
    return _uncertain_projection(expression, state)


def _comprehension_projection(
    terminal: ast.expr,
    generators: list[ast.comprehension],
    state: _ProjectionState,
    context: _ProjectionContext,
) -> _ParameterProjection | None:
    local = state.fork()
    for generator in generators:
        _ProjectionExpressionFinder(local, context).visit(generator.iter)
        source = _iteration_projection(generator.iter, local, context)
        _bind_projection_target(generator.target, source, local)
        for condition in generator.ifs:
            _ProjectionExpressionFinder(local, context).visit(condition)
    projection = _parameter_projection(terminal, local)
    return (
        _ParameterProjection(projection.names, True)
        if projection is not None and len(generators) > 1
        else projection
    )


def _context_enter_projection(
    expression: ast.expr,
    state: _ProjectionState,
    *,
    async_with: bool = False,
) -> _ParameterProjection | None:
    manager = _context_manager_projection(expression, state)
    if manager is not None:
        return manager[1] if async_with else manager[0]
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
    return _uncertain_projection(expression, state)


def _context_manager_projection(
    expression: ast.expr,
    state: _ProjectionState,
) -> _ContextManagerProjection | None:
    if isinstance(expression, ast.Name):
        return state.context_managers.get(expression.id)
    if isinstance(expression, ast.NamedExpr):
        return _context_manager_projection(expression.value, state)
    if isinstance(expression, ast.Call):
        return _context_manager_projection(expression.func, state)
    if isinstance(expression, ast.Subscript):
        selected = _constant_subscript_value(expression)
        return (
            _context_manager_projection(selected, state)
            if selected is not None
            else None
        )
    if isinstance(expression, ast.IfExp):
        truth = _static_truth(expression.test)
        values = (
            [expression.body if truth else expression.orelse]
            if truth is not None
            else [expression.body, expression.orelse]
        )
        return _merge_context_manager_projection_values(values, state)
    if isinstance(expression, ast.BoolOp):
        return _merge_context_manager_projection_values(
            _possible_bool_values(expression),
            state,
        )
    return None


def _merge_context_manager_projection_values(
    values: list[ast.expr] | tuple[ast.expr, ...],
    state: _ProjectionState,
) -> _ContextManagerProjection | None:
    projections = [
        _context_manager_projection(value, state) for value in values
    ]
    present = [value for value in projections if value is not None]
    if not present:
        return None
    return (
        _merge_projection_values([value[0] for value in present]),
        _merge_projection_values([value[1] for value in present]),
    )


def _bind_context_manager_target(
    target: ast.expr,
    projection: _ContextManagerProjection | None,
    state: _ProjectionState,
) -> None:
    names = _target_names(target)
    for name in names:
        state.context_managers.pop(name, None)
    if projection is not None and isinstance(target, ast.Name):
        state.context_managers[target.id] = projection


def _local_context_manager_projection(
    node: ast.ClassDef,
    state: _ProjectionState,
) -> _ContextManagerProjection:
    sync = _class_protocol_projection(node, "__enter__", 0, state)
    async_ = _class_protocol_projection(node, "__aenter__", 1, state)
    if node.decorator_list:
        unknown = _all_parameter_projection(state)
        sync = _merge_projection_values([sync, unknown])
        async_ = _merge_projection_values([async_, unknown])
    return sync, async_


def _class_protocol_projection(
    node: ast.ClassDef,
    name: str,
    protocol_index: int,
    state: _ProjectionState,
) -> _ParameterProjection | None:
    method, mutated = _final_protocol_binding(node.body, name)
    if mutated:
        return _all_parameter_projection(state)
    if method is not None:
        return _method_return_projection(method, state)
    for base in node.bases:
        inherited = _context_manager_projection(base, state)
        if inherited is not None:
            return inherited[protocol_index]
        if not isinstance(base, ast.Name) or base.id != "object":
            return _all_parameter_projection(state)
    return None


def _final_protocol_binding(
    body: list[ast.stmt],
    name: str,
) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef | None, bool]:
    method = None
    mutated = False
    for statement in body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if statement.name == name:
                method = statement
                mutated = False
        elif _statement_targets_name(statement, name):
            method = None
            mutated = True
    return method, mutated


def _statement_targets_name(statement: ast.stmt, name: str) -> bool:
    if isinstance(statement, ast.Assign):
        return any(name in _target_names(target) for target in statement.targets)
    if isinstance(statement, ast.AnnAssign):
        return statement.value is not None and name in _target_names(statement.target)
    if isinstance(statement, ast.Delete):
        return any(name in _target_names(target) for target in statement.targets)
    return False


def _method_return_projection(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    state: _ProjectionState,
) -> _ParameterProjection | None:
    local = state.fork()
    for name in _local_bindings(node):
        _bind_alias(name, None, local)
    returns = _ImmediateReturnFinder()
    for statement in node.body:
        returns.visit(statement)
    projection = _merge_projection_values(
        [_parameter_projection(item.value, local) for item in returns.nodes]
    )
    if node.decorator_list:
        projection = _merge_projection_values(
            [projection, _all_parameter_projection(state)]
        )
    return projection


def _all_parameter_projection(
    state: _ProjectionState,
) -> _ParameterProjection | None:
    names = frozenset().union(
        *(projection.names for projection in state.aliases.values())
    )
    return _ParameterProjection(names, True) if names else None


class _ImmediateReturnFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.nodes: list[ast.Return] = []

    def visit_Return(self, node: ast.Return) -> None:
        self.nodes.append(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


def _bind_pattern_projection(
    pattern: ast.pattern,
    subject: ast.expr | _ParameterProjection,
    state: _ProjectionState,
) -> None:
    if isinstance(pattern, ast.MatchAs):
        if pattern.pattern is not None:
            _bind_pattern_projection(pattern.pattern, subject, state)
        if pattern.name is not None:
            _bind_alias(pattern.name, _projection(subject, state), state)
        return
    if isinstance(pattern, ast.MatchStar):
        if pattern.name is not None:
            _bind_alias(pattern.name, _uncertain_projection(subject, state), state)
        return
    if isinstance(pattern, ast.MatchSequence):
        _bind_sequence_pattern(pattern, subject, state)
        return
    if isinstance(pattern, ast.MatchMapping):
        projection = _uncertain_projection(subject, state)
        for child in pattern.patterns:
            _bind_pattern_projection(child, projection or subject, state)
        if pattern.rest is not None:
            _bind_alias(pattern.rest, projection, state)
        return
    if isinstance(pattern, ast.MatchClass):
        projection = _uncertain_projection(subject, state)
        for child in (*pattern.patterns, *pattern.kwd_patterns):
            _bind_pattern_projection(child, projection or subject, state)
        return
    if isinstance(pattern, ast.MatchOr):
        projection = _uncertain_projection(subject, state)
        for child in pattern.patterns:
            _bind_pattern_projection(child, projection or subject, state)


def _bind_sequence_pattern(
    pattern: ast.MatchSequence,
    subject: ast.expr | _ParameterProjection,
    state: _ProjectionState,
) -> None:
    values = _literal_sequence(subject) if isinstance(subject, ast.expr) else None
    if values is not None and len(values) == len(pattern.patterns):
        for child, value in zip(pattern.patterns, values, strict=True):
            _bind_pattern_projection(child, value, state)
        return
    projection = _uncertain_projection(subject, state)
    for child in pattern.patterns:
        _bind_pattern_projection(child, projection or subject, state)


def _projection(
    value: ast.expr | _ParameterProjection,
    state: _ProjectionState,
) -> _ParameterProjection | None:
    return (
        value
        if isinstance(value, _ParameterProjection)
        else _parameter_projection(value, state)
    )


__all__: list[str] = []
