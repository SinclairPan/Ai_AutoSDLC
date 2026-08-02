"""编译会改变生成器 lineage 或游标的复合控制流。"""

from __future__ import annotations

import ast
from collections.abc import Callable

from ai_sdlc.core.lean_code_comprehension_values import _known_iteration_values
from ai_sdlc.core.lean_code_context_manager_lineage import (
    _context_manager_expression_lineage,
    _context_manager_expression_protocols,
    _context_manager_expression_uncertain,
)
from ai_sdlc.core.lean_code_context_manager_metadata import (
    _CONTEXT_MANAGER_UNCERTAIN_ATTRIBUTE,
)
from ai_sdlc.core.lean_code_control_flow import _static_truth
from ai_sdlc.core.lean_code_exception_flow import _statement_exception_points
from ai_sdlc.core.lean_code_exception_handlers import (
    _reachable_exception_handlers,
)
from ai_sdlc.core.lean_code_generator_identity import (
    _consumer_generator_lineage,
    _generator_lineage,
    _set_consumer_metadata,
)
from ai_sdlc.core.lean_code_generator_state import (
    _bind_context_manager_names,
    _bind_generator_names,
    _bind_names,
    _ConsumerState,
    _merge_consumer_states,
)
from ai_sdlc.core.lean_code_scope import _bound_names

_BlockCompiler = Callable[[list[ast.stmt], _ConsumerState], None]
_ExpressionCompiler = Callable[[ast.expr, _ConsumerState], None]


def _compile_compound(
    statement: ast.stmt,
    state: _ConsumerState,
    compile_expression: _ExpressionCompiler,
    compile_block: _BlockCompiler,
) -> bool:
    if isinstance(statement, ast.If):
        _compile_if(statement, state, compile_expression, compile_block)
    elif isinstance(statement, (ast.For, ast.AsyncFor)):
        _compile_for(statement, state, compile_expression, compile_block)
    elif isinstance(statement, ast.While):
        _compile_while(statement, state, compile_expression, compile_block)
        if _static_truth(statement.test) is not False and not _block_always_break(
            statement.body
        ):
            _mark_loop_consumers_unknown(statement.body)
    elif isinstance(statement, (ast.With, ast.AsyncWith)):
        _compile_with(statement, state, compile_expression, compile_block)
    elif isinstance(statement, (ast.Try, ast.TryStar)):
        _compile_try(statement, state, compile_block)
    else:
        return False
    return True


def _compile_if(
    statement: ast.If,
    state: _ConsumerState,
    compile_expression: _ExpressionCompiler,
    compile_block: _BlockCompiler,
) -> None:
    compile_expression(statement.test, state)
    truth = _static_truth(statement.test)
    if truth is not None:
        compile_block(statement.body if truth else statement.orelse, state)
        return
    branches = []
    for block in (statement.body, statement.orelse):
        branch = state.fork()
        compile_block(block, branch)
        branches.append(branch)
    _merge_consumer_states(state, branches)


def _compile_for(
    statement: ast.For | ast.AsyncFor,
    state: _ConsumerState,
    compile_expression: _ExpressionCompiler,
    compile_block: _BlockCompiler,
) -> None:
    compile_expression(statement.iter, state)
    values = _known_iteration_values(statement.iter)
    if values == ():
        compile_block(statement.orelse, state)
        return
    entered = state.fork()
    iterations = []
    _bind_for_target(statement, values[0] if values else None, entered)
    compile_block(statement.body, entered)
    iterations.append(entered.fork())
    if values is not None:
        if _block_always_break(statement.body):
            _merge_consumer_states(state, [entered])
            return
        for value in values[1:]:
            _bind_for_target(statement, value, entered)
            compile_block(statement.body, entered)
            iterations.append(entered.fork())
            if _block_always_break(statement.body):
                break
        normal = entered.fork()
        compile_block(statement.orelse, normal)
        outcomes = [normal]
        if _block_may_break(statement.body):
            outcomes.extend(iterations)
        _merge_consumer_states(state, outcomes)
        return
    if not _block_always_break(statement.body):
        _mark_loop_consumers_unknown(statement.body)
    compile_block(statement.orelse, entered)
    _merge_consumer_states(state, [state.fork(), entered])


def _compile_while(
    statement: ast.While,
    state: _ConsumerState,
    compile_expression: _ExpressionCompiler,
    compile_block: _BlockCompiler,
) -> None:
    compile_expression(statement.test, state)
    truth = _static_truth(statement.test)
    if truth is False:
        compile_block(statement.orelse, state)
        return
    entered = state.fork()
    compile_block(statement.body, entered)
    if _block_always_break(statement.body):
        _merge_consumer_states(state, [entered])
        return
    compile_block(statement.orelse, entered)
    _merge_consumer_states(state, [state.fork(), entered])


def _compile_with(
    statement: ast.With | ast.AsyncWith,
    state: _ConsumerState,
    compile_expression: _ExpressionCompiler,
    compile_block: _BlockCompiler,
) -> None:
    async_with = isinstance(statement, ast.AsyncWith)
    for item in statement.items:
        compile_expression(item.context_expr, state)
        if _context_manager_expression_uncertain(
            item.context_expr,
            state,
            async_with=async_with,
        ):
            setattr(statement, _CONTEXT_MANAGER_UNCERTAIN_ATTRIBUTE, True)
        if item.optional_vars is not None:
            names = _bound_names(item.optional_vars)
            lineage = _context_manager_expression_lineage(
                item.context_expr,
                state,
                async_with=async_with,
            )
            _bind_names(names, "unknown", state)
            _bind_generator_names(names, lineage, state)
    compile_block(statement.body, state)


def _compile_try(
    statement: ast.Try | ast.TryStar,
    state: _ConsumerState,
    compile_block: _BlockCompiler,
) -> None:
    normal = state.fork()
    compile_block(statement.body, normal)
    compile_block(statement.orelse, normal)
    outcomes = [normal]
    for index, child in enumerate(statement.body):
        for point in _statement_exception_points(child):
            handlers, _ = _reachable_exception_handlers(
                statement.handlers,
                point.raised,
            )
            for handler in handlers:
                handled = state.fork()
                compile_block(statement.body[:index], handled)
                compile_block(list(point.prefix), handled)
                compile_block(handler.body, handled)
                outcomes.append(handled)
    _merge_consumer_states(state, outcomes)
    compile_block(statement.finalbody, state)


def _block_may_break(statements: list[ast.stmt]) -> bool:
    finder = _LoopBreakFinder()
    for statement in statements:
        finder.visit(statement)
    return finder.found


def _block_always_break(statements: list[ast.stmt]) -> bool:
    return _block_always_exits(statements, (ast.Break,))


def _block_always_exits(
    statements: list[ast.stmt],
    exits: tuple[type[ast.stmt], ...] = (
        ast.Break,
        ast.Continue,
        ast.Return,
        ast.Raise,
    ),
) -> bool:
    for statement in statements:
        if isinstance(statement, exits):
            return True
        if (
            isinstance(statement, ast.If)
            and statement.orelse
            and _block_always_exits(statement.body, exits)
            and _block_always_exits(statement.orelse, exits)
        ):
            return True
    return False


def _bind_for_target(
    statement: ast.For | ast.AsyncFor,
    value: ast.expr | None,
    state: _ConsumerState,
) -> None:
    names = _bound_names(statement.target)
    lineage = _generator_lineage(value, state.generators) if value is not None else ()
    manager_protocols = (
        _context_manager_expression_protocols(value, state)
        if value is not None
        else None
    )
    _bind_names(names, "unknown", state)
    _bind_generator_names(names, lineage, state)
    if manager_protocols is not None:
        _bind_context_manager_names(names, manager_protocols, state)


def _mark_loop_consumers_unknown(statements: list[ast.stmt]) -> None:
    for statement in statements:
        for node in ast.walk(statement):
            if not isinstance(node, ast.Call):
                continue
            generators = _consumer_generator_lineage(node)
            if generators:
                _set_consumer_metadata(
                    node,
                    "consume-unknown",
                    generators,
                    tuple(None for _ in generators),
                )


class _LoopBreakFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.found = False

    def visit_Break(self, node: ast.Break) -> None:
        self.found = True

    def visit_For(self, node: ast.For) -> None:
        return

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        return

    def visit_While(self, node: ast.While) -> None:
        return

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


__all__: list[str] = []
