"""按可达执行路径传播函数体内的动态导入工厂绑定。"""

from __future__ import annotations

import ast
from collections.abc import Collection, Iterable

from ai_sdlc.core.lean_code_control_flow import (
    _assignment_parts,
    _match_pattern_names,
    _must_iterate,
    _reachable_match_cases,
    _static_truth,
    _statically_empty,
)
from ai_sdlc.core.lean_code_exception_flow import _statement_exception_points
from ai_sdlc.core.lean_code_exception_handlers import _reachable_exception_handlers
from ai_sdlc.core.lean_code_import_factory_effects import (
    _apply_alias_binding,
    _apply_expression,
    _apply_generator_iteration,
    _deduplicate_states,
    _dynamic_value,
    _FlowState,
    _replace_definition,
    _visit_function_header,
)


def _returns_dynamic_callable(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    module_aliases: Collection[str],
    callable_aliases: Collection[str],
) -> bool:
    returned, _, _, _ = _scan_statements(
        function.body,
        set(module_aliases),
        set(callable_aliases),
        frozenset(callable_aliases),
    )
    return returned


def _function_uses_dynamic_dependency(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    module_aliases: Collection[str],
    callable_aliases: Collection[str],
) -> bool:
    _, called, _, _ = _scan_statements(
        function.body,
        set(module_aliases),
        set(callable_aliases),
        frozenset(callable_aliases),
    )
    return called


def _scan_statements(
    statements: Iterable[ast.stmt],
    modules: set[str],
    callables: set[str],
    known_callables: frozenset[str],
) -> tuple[bool, bool, set[str], set[str]]:
    outcomes = _run_block(
        statements,
        (_FlowState(set(modules), set(callables)),),
        known_callables,
    )
    return (
        any(state.returned for state in outcomes),
        any(state.called for state in outcomes),
        set().union(*(state.modules for state in outcomes)),
        set().union(*(state.callables for state in outcomes)),
    )


def _run_block(
    statements: Iterable[ast.stmt],
    states: Iterable[_FlowState],
    known_callables: frozenset[str],
    *,
    run_terminated: bool = False,
) -> list[_FlowState]:
    outcomes = list(states)
    for statement in statements:
        next_states: list[_FlowState] = []
        for state in outcomes:
            if state.control != "normal" and not run_terminated:
                next_states.append(state)
            else:
                next_states.extend(_run_statement(statement, state, known_callables))
        outcomes = _deduplicate_states(next_states)
    return outcomes


def _run_statement(
    statement: ast.stmt,
    state: _FlowState,
    known_callables: frozenset[str],
) -> list[_FlowState]:
    if isinstance(statement, ast.Return):
        if statement.value is not None:
            _apply_expression(statement.value, state)
            state.returned = state.returned or _dynamic_value(statement.value, state)
        state.control = "return"
        return [state]
    if isinstance(statement, ast.Raise):
        for expression in (statement.exc, statement.cause):
            if expression is not None:
                _apply_expression(expression, state)
        state.control = "raise"
        return [state]
    if isinstance(statement, ast.Break):
        state.control = "break"
        return [state]
    if isinstance(statement, ast.Continue):
        state.control = "continue"
        return [state]
    if isinstance(statement, ast.If):
        return _run_if(statement, state, known_callables)
    if isinstance(statement, (ast.Try, ast.TryStar)):
        return _run_try(statement, state, known_callables)
    if isinstance(statement, (ast.With, ast.AsyncWith)):
        return _run_with(statement, state, known_callables)
    if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
        return _run_loop(statement, state, known_callables)
    if isinstance(statement, ast.Match):
        return _run_match(statement, state, known_callables)
    _apply_statement_effects(statement, state, known_callables)
    return [state]


def _run_if(
    statement: ast.If,
    state: _FlowState,
    known_callables: frozenset[str],
) -> list[_FlowState]:
    _apply_expression(statement.test, state)
    truth = _static_truth(statement.test)
    if truth is not None:
        block = statement.body if truth else statement.orelse
        return _run_block(block, (state,), known_callables)
    return [
        *_run_block(statement.body, (state.fork(),), known_callables),
        *_run_block(statement.orelse, (state.fork(),), known_callables),
    ]


def _run_try(
    statement: ast.Try | ast.TryStar,
    state: _FlowState,
    known_callables: frozenset[str],
) -> list[_FlowState]:
    body, possible_exceptions = _run_try_body(
        statement.body,
        state,
        known_callables,
    )
    normal = _run_block(
        statement.orelse,
        (item for item in body if item.control == "normal"),
        known_callables,
    )
    carried = [item for item in body if item.control not in {"normal", "raise"}]
    handled = _run_handlers(statement, possible_exceptions, known_callables)
    outcomes = [*normal, *handled, *carried]
    finalized: list[_FlowState] = []
    for outcome in outcomes:
        previous = outcome.control
        outcome.control = "normal"
        result = _run_block(
            statement.finalbody,
            (outcome,),
            known_callables,
            run_terminated=True,
        )
        for item in result:
            if item.control == "normal":
                item.control = previous
        finalized.extend(result)
    return finalized


def _run_try_body(
    statements: Iterable[ast.stmt],
    state: _FlowState,
    known_callables: frozenset[str],
) -> tuple[list[_FlowState], list[tuple[_FlowState, str | None]]]:
    outcomes = [state.fork()]
    exceptions: list[tuple[_FlowState, str | None]] = []
    for statement in statements:
        next_outcomes: list[_FlowState] = []
        for outcome in outcomes:
            if outcome.control != "normal":
                next_outcomes.append(outcome)
                continue
            for point in _statement_exception_points(statement):
                prefixes = _run_block(
                    point.prefix,
                    (outcome.fork(),),
                    known_callables,
                )
                exceptions.extend((prefix, point.raised) for prefix in prefixes)
            next_outcomes.extend(_run_statement(statement, outcome, known_callables))
        outcomes = next_outcomes
    return outcomes, exceptions


def _run_handlers(
    statement: ast.Try | ast.TryStar,
    inputs: Iterable[tuple[_FlowState, str | None]],
    known_callables: frozenset[str],
) -> list[_FlowState]:
    return [
        outcome
        for source, raised in inputs
        for handler in _reachable_exception_handlers(
            statement.handlers,
            raised,
        )[0]
        for outcome in _run_block(
            handler.body,
            (_reset_control(source.fork()),),
            known_callables,
        )
    ]


def _reset_control(state: _FlowState) -> _FlowState:
    state.control = "normal"
    return state


def _run_with(
    statement: ast.With | ast.AsyncWith,
    state: _FlowState,
    known_callables: frozenset[str],
) -> list[_FlowState]:
    for item in statement.items:
        _apply_expression(item.context_expr, state)
        if item.optional_vars is not None:
            _apply_alias_binding(item.optional_vars, item.context_expr, state)
    return _run_block(statement.body, (state,), known_callables)


def _run_loop(
    statement: ast.For | ast.AsyncFor | ast.While,
    state: _FlowState,
    known_callables: frozenset[str],
) -> list[_FlowState]:
    if isinstance(statement, ast.While):
        _apply_expression(statement.test, state)
        truth = _static_truth(statement.test)
        if truth is False:
            return _run_block(statement.orelse, (state,), known_callables)
        body_state = state.fork()
    else:
        _apply_expression(statement.iter, state)
        _apply_generator_iteration(statement.iter, state)
        if _statically_empty(statement.iter):
            return _run_block(statement.orelse, (state,), known_callables)
        if isinstance(statement.iter, (ast.List, ast.Tuple)):
            return _run_known_for(statement, state, known_callables)
        body_state = state.fork()
        _apply_alias_binding(statement.target, statement.iter, body_state)
    body = _run_block(statement.body, (body_state,), known_callables)
    completed: list[_FlowState] = []
    for outcome in body:
        if outcome.control == "break":
            outcome.control = "normal"
            completed.append(outcome)
        elif outcome.control == "continue":
            if isinstance(statement, ast.While) and truth is True:
                outcome.control = "loop"
                completed.append(outcome)
                continue
            outcome.control = "normal"
            completed.extend(_run_block(statement.orelse, (outcome,), known_callables))
        elif outcome.control == "normal":
            if isinstance(statement, ast.While) and truth is True:
                outcome.control = "loop"
                completed.append(outcome)
                continue
            completed.extend(_run_block(statement.orelse, (outcome,), known_callables))
        else:
            completed.append(outcome)
    if (
        isinstance(statement, ast.While) and truth is True
    ) or _must_iterate(statement):
        return completed
    skipped = _run_block(statement.orelse, (state,), known_callables)
    return [*completed, *skipped]


def _run_known_for(
    statement: ast.For | ast.AsyncFor,
    state: _FlowState,
    known_callables: frozenset[str],
) -> list[_FlowState]:
    assert isinstance(statement.iter, (ast.List, ast.Tuple))
    current = [state]
    completed: list[_FlowState] = []
    for value in statement.iter.elts:
        iteration: list[_FlowState] = []
        for candidate in current:
            _apply_alias_binding(statement.target, value, candidate)
            for outcome in _run_block(
                statement.body,
                (candidate,),
                known_callables,
            ):
                if outcome.control == "break":
                    outcome.control = "normal"
                    completed.append(outcome)
                elif outcome.control == "continue":
                    outcome.control = "normal"
                    iteration.append(outcome)
                elif outcome.control == "normal":
                    iteration.append(outcome)
                else:
                    completed.append(outcome)
        current = _deduplicate_states(iteration)
    completed.extend(_run_block(statement.orelse, current, known_callables))
    return _deduplicate_states(completed)


def _run_match(
    statement: ast.Match,
    state: _FlowState,
    known_callables: frozenset[str],
) -> list[_FlowState]:
    _apply_expression(statement.subject, state)
    outcomes: list[_FlowState] = []
    cases, no_match = _reachable_match_cases(statement.subject, statement.cases)
    for case in cases:
        branch = state.fork()
        names = _match_pattern_names(case.pattern)
        if names:
            _apply_alias_binding(
                ast.Tuple(
                    elts=[ast.Name(id=name, ctx=ast.Store()) for name in names],
                    ctx=ast.Store(),
                ),
                statement.subject,
                branch,
            )
        if case.guard is not None:
            _apply_expression(case.guard, branch)
        outcomes.extend(_run_block(case.body, (branch,), known_callables))
    if no_match:
        outcomes.append(state)
    return outcomes


def _apply_statement_effects(
    statement: ast.stmt,
    state: _FlowState,
    known_callables: frozenset[str],
) -> None:
    targets, value = _assignment_parts(statement)
    if value is not None:
        _apply_expression(value, state)
    if targets and value is not None:
        for target in targets:
            _apply_alias_binding(target, value, state)
        return
    if isinstance(statement, ast.Expr):
        _apply_expression(statement.value, state)
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
        _visit_function_header(statement, state)
        _replace_definition(statement.name, state, known_callables)
    elif isinstance(statement, ast.ClassDef):
        for expression in (
            *statement.decorator_list,
            *statement.bases,
            *(keyword.value for keyword in statement.keywords),
        ):
            _apply_expression(expression, state)
        _replace_definition(statement.name, state, known_callables)


__all__: list[str] = []
