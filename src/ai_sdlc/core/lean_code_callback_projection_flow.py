"""执行高阶包装器参数投影的语句级控制流。"""

from __future__ import annotations

import ast

from ai_sdlc.core.lean_code_callback_projection import (
    _bind_alias,
    _bind_projection_target,
    _imported_names,
    _literal_sequence,
    _merge_projection_states,
    _ParameterEffects,
    _ParameterProjection,
    _pattern_is_irrefutable,
    _ProjectionContext,
    _ProjectionExpressionFinder,
    _ProjectionState,
)
from ai_sdlc.core.lean_code_callback_projection_targets import (
    _bind_context_manager_target,
    _bind_pattern_projection,
    _context_enter_projection,
    _context_manager_projection,
    _iteration_projection,
    _local_context_manager_projection,
)
from ai_sdlc.core.lean_code_control_flow import _static_truth
from ai_sdlc.core.lean_code_exception_flow import _statement_exception_points
from ai_sdlc.core.lean_code_exception_handlers import (
    _reachable_exception_handlers,
)


def _parameter_effects(
    names: frozenset[str],
    body: list[ast.stmt] | list[ast.expr],
    context: _ProjectionContext,
) -> _ParameterEffects:
    state = _ProjectionState(
        aliases={name: _ParameterProjection(frozenset({name})) for name in names}
    )
    _compile_projection_block(body, state, context)
    return _ParameterEffects(
        frozenset(state.invoked),
        frozenset(state.type_hints),
        state.uncertain,
    )


def _compile_projection_block(
    nodes: list[ast.stmt] | list[ast.expr],
    state: _ProjectionState,
    context: _ProjectionContext,
) -> None:
    for node in nodes:
        if state.control != "normal":
            break
        if isinstance(node, ast.stmt):
            _compile_statement(node, state, context)
        else:
            _ProjectionExpressionFinder(state, context).visit(node)


def _compile_statement(
    node: ast.stmt,
    state: _ProjectionState,
    context: _ProjectionContext,
) -> None:
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        _compile_assignment(node, state, context)
    elif isinstance(node, ast.If):
        _compile_if(node, state, context)
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        _compile_for(node, state, context)
    elif isinstance(node, ast.While):
        _compile_while(node, state, context)
    elif isinstance(node, (ast.Try, ast.TryStar)):
        _compile_try(node, state, context)
    elif isinstance(node, (ast.With, ast.AsyncWith)):
        _compile_with(node, state, context)
    elif isinstance(node, ast.Match):
        _compile_match(node, state, context)
    else:
        _compile_simple_statement(node, state, context)


def _compile_assignment(
    node: ast.Assign | ast.AnnAssign | ast.AugAssign,
    state: _ProjectionState,
    context: _ProjectionContext,
) -> None:
    finder = _ProjectionExpressionFinder(state, context)
    if isinstance(node, ast.AugAssign):
        finder.visit(node.target)
        finder.visit(node.value)
        _bind_projection_target(node.target, None, state)
        return
    value = node.value
    if value is not None:
        finder.visit(value)
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    manager = (
        _context_manager_projection(value, state)
        if value is not None
        else None
    )
    for target in targets:
        _bind_projection_target(target, value, state)
        _bind_context_manager_target(target, manager, state)


def _compile_if(
    node: ast.If,
    state: _ProjectionState,
    context: _ProjectionContext,
) -> None:
    _ProjectionExpressionFinder(state, context).visit(node.test)
    truth = _static_truth(node.test)
    if truth is not None:
        _compile_projection_block(
            node.body if truth else node.orelse,
            state,
            context,
        )
        return
    branches = [state.fork(), state.fork()]
    _compile_projection_block(node.body, branches[0], context)
    _compile_projection_block(node.orelse, branches[1], context)
    _merge_projection_states(state, branches)


def _compile_for(
    node: ast.For | ast.AsyncFor,
    state: _ProjectionState,
    context: _ProjectionContext,
) -> None:
    _ProjectionExpressionFinder(state, context).visit(node.iter)
    values = _literal_sequence(node.iter)
    if values == ():
        _compile_projection_block(node.orelse, state, context)
        return
    if values is not None:
        _compile_known_for(node, values, state, context)
        return
    _compile_unknown_for(node, state, context)


def _compile_known_for(
    node: ast.For | ast.AsyncFor,
    values: tuple[ast.expr, ...],
    state: _ProjectionState,
    context: _ProjectionContext,
) -> None:
    active = [state.fork()]
    completed: list[_ProjectionState] = []
    propagated: list[_ProjectionState] = []
    for value in values:
        next_active: list[_ProjectionState] = []
        for branch in active:
            _bind_projection_target(node.target, value, branch)
            _compile_projection_block(node.body, branch, context)
            _route_loop_paths(branch, next_active, completed, propagated)
        active = next_active
        if not active:
            break
    for branch in active:
        _compile_projection_block(node.orelse, branch, context)
        completed.extend(_projection_paths(branch))
    _finish_loop_merge(state, completed, propagated)


def _compile_unknown_for(
    node: ast.For | ast.AsyncFor,
    state: _ProjectionState,
    context: _ProjectionContext,
) -> None:
    skipped = state.fork()
    _compile_projection_block(node.orelse, skipped, context)
    entered = state.fork()
    source = _iteration_projection(node.iter, entered, context)
    _bind_projection_target(node.target, source, entered)
    _compile_projection_block(node.body, entered, context)
    active: list[_ProjectionState] = []
    completed: list[_ProjectionState] = [skipped]
    propagated: list[_ProjectionState] = []
    _route_loop_paths(entered, active, completed, propagated)
    for branch in active:
        _compile_projection_block(node.orelse, branch, context)
        completed.extend(_projection_paths(branch))
    _finish_loop_merge(state, completed, propagated)


def _route_loop_paths(
    state: _ProjectionState,
    active: list[_ProjectionState],
    completed: list[_ProjectionState],
    propagated: list[_ProjectionState],
) -> None:
    for branch in _projection_paths(state):
        if branch.control == "break":
            branch.control = "normal"
            completed.append(branch)
        elif branch.control in {"return", "raise"}:
            propagated.append(branch)
        else:
            branch.control = "normal"
            active.append(branch)


def _finish_loop_merge(
    state: _ProjectionState,
    completed: list[_ProjectionState],
    propagated: list[_ProjectionState],
) -> None:
    outcomes = completed or propagated
    _merge_projection_states(state, outcomes)
    if not completed:
        state.control = "terminated"


def _compile_while(
    node: ast.While,
    state: _ProjectionState,
    context: _ProjectionContext,
) -> None:
    _ProjectionExpressionFinder(state, context).visit(node.test)
    truth = _static_truth(node.test)
    if truth is False:
        _compile_projection_block(node.orelse, state, context)
        return
    entered = state.fork()
    _compile_projection_block(node.body, entered, context)
    active: list[_ProjectionState] = []
    completed = [state.fork()] if truth is not True else []
    propagated: list[_ProjectionState] = []
    _route_loop_paths(entered, active, completed, propagated)
    for branch in active:
        _compile_projection_block(node.orelse, branch, context)
        completed.extend(_projection_paths(branch))
    _finish_loop_merge(state, completed, propagated)


def _compile_try(
    node: ast.Try | ast.TryStar,
    state: _ProjectionState,
    context: _ProjectionContext,
) -> None:
    normal = state.fork()
    _compile_projection_block(node.body, normal, context)
    normal_paths = [
        branch for branch in _projection_paths(normal) if branch.control == "normal"
    ]
    for branch in normal_paths:
        _compile_projection_block(node.orelse, branch, context)
    outcomes = list(normal_paths)
    for index, child in enumerate(node.body):
        for point in _statement_exception_points(child):
            handlers, _ = _reachable_exception_handlers(
                node.handlers,
                point.raised,
            )
            for handler in handlers:
                handled = state.fork()
                _compile_projection_block(node.body[:index], handled, context)
                _compile_projection_block(list(point.prefix), handled, context)
                handled.control = "normal"
                if handler.name:
                    _bind_alias(handler.name, None, handled)
                _compile_projection_block(handler.body, handled, context)
                outcomes.extend(_projection_paths(handled))
    if not outcomes:
        outcomes = _projection_paths(normal)
    for branch in outcomes:
        _compile_finally(node.finalbody, branch, context)
    _merge_projection_states(state, outcomes)


def _compile_finally(
    body: list[ast.stmt],
    state: _ProjectionState,
    context: _ProjectionContext,
) -> None:
    prior_control = "normal" if state.control == "terminated" else state.control
    state.control = "normal"
    _compile_projection_block(body, state, context)
    if state.control == "normal":
        state.control = prior_control


def _compile_with(
    node: ast.With | ast.AsyncWith,
    state: _ProjectionState,
    context: _ProjectionContext,
) -> None:
    finder = _ProjectionExpressionFinder(state, context)
    for item in node.items:
        finder.visit(item.context_expr)
        if item.optional_vars is not None:
            source = _context_enter_projection(
                item.context_expr,
                state,
                async_with=isinstance(node, ast.AsyncWith),
            )
            _bind_projection_target(item.optional_vars, source, state)
    _compile_projection_block(node.body, state, context)


def _compile_match(
    node: ast.Match,
    state: _ProjectionState,
    context: _ProjectionContext,
) -> None:
    _ProjectionExpressionFinder(state, context).visit(node.subject)
    branches = []
    exhaustive = False
    for case in node.cases:
        branch = state.fork()
        _bind_pattern_projection(case.pattern, node.subject, branch)
        if case.guard is not None:
            _ProjectionExpressionFinder(branch, context).visit(case.guard)
        _compile_projection_block(case.body, branch, context)
        branches.append(branch)
        if case.guard is None and _pattern_is_irrefutable(case.pattern):
            exhaustive = True
            break
    if not exhaustive:
        branches.append(state.fork())
    _merge_projection_states(state, branches)


def _compile_simple_statement(
    node: ast.stmt,
    state: _ProjectionState,
    context: _ProjectionContext,
) -> None:
    if isinstance(node, (ast.Break, ast.Continue)):
        state.control = "break" if isinstance(node, ast.Break) else "continue"
        return
    if isinstance(node, (ast.Return, ast.Raise)):
        finder = _ProjectionExpressionFinder(state, context)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                finder.visit(child)
        state.control = "return" if isinstance(node, ast.Return) else "raise"
        return
    if isinstance(node, ast.ClassDef):
        manager = _local_context_manager_projection(node, state)
        _bind_alias(node.name, None, state)
        state.context_managers[node.name] = manager
        return
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        _bind_alias(node.name, None, state)
        return
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        for name in _imported_names(node):
            _bind_alias(name, None, state)
        return
    if isinstance(node, ast.Delete):
        for target in node.targets:
            _bind_projection_target(target, None, state)
        return
    finder = _ProjectionExpressionFinder(state, context)
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.expr):
            finder.visit(child)


def _projection_paths(state: _ProjectionState) -> list[_ProjectionState]:
    paths = []
    if state.control != "terminated":
        paths.append(state)
    paths.extend(state.exits)
    for branch in paths:
        branch.exits = []
    return paths


__all__: list[str] = []
