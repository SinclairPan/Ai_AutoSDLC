"""在轻量导入分析前编译生成器调用点的可调用身份。"""

from __future__ import annotations

import ast

from ai_sdlc.core.lean_code_call_arguments import _expanded_call_arguments
from ai_sdlc.core.lean_code_consumer_proof import (
    _lambda_ignores_arguments,
    _proven_non_consumer,
)
from ai_sdlc.core.lean_code_context_manager_lineage import (
    _context_manager_expression_protocols,
)
from ai_sdlc.core.lean_code_context_manager_methods import (
    _RETURN_LINEAGE_ATTRIBUTE,
    _context_manager_generator_lineage,
)
from ai_sdlc.core.lean_code_control_flow import (
    _function_annotation_expressions,
    _future_annotations_enabled,
    _static_truth,
)
from ai_sdlc.core.lean_code_generator_compound import (
    _block_always_exits,
    _compile_compound,
)
from ai_sdlc.core.lean_code_generator_identity import (
    _generator_lineage,
    _merge_lineages,
    _set_consumer_metadata,
)
from ai_sdlc.core.lean_code_generator_imports import (
    _apply_generator_import,
    _apply_generator_import_from,
    _builtin_consumer_identity,
    _is_builtin_consumer,
)
from ai_sdlc.core.lean_code_generator_state import (
    _advance_generator_offsets,
    _bind_context_manager_names,
    _bind_generator_names,
    _bind_names,
    _ConsumerState,
    _invalidate_non_consumers,
    _merge_consumer_states,
)
from ai_sdlc.core.lean_code_scope import (
    _bound_names,
    _local_bindings,
    _scope_declarations,
)
from ai_sdlc.core.lean_code_static_values import _constant_subscript_value


def _annotate_generator_consumers(tree: ast.Module) -> None:
    state = _ConsumerState(evaluate_annotations=not _future_annotations_enabled(tree))
    _compile_block(tree.body, state)


def _compile_block(statements: list[ast.stmt], state: _ConsumerState) -> None:
    for statement in statements:
        _compile_statement(statement, state)
        if _block_always_exits([statement]):
            break


def _compile_statement(statement: ast.stmt, state: _ConsumerState) -> None:
    if isinstance(statement, ast.Import):
        _apply_generator_import(statement, state)
        return
    if isinstance(statement, ast.ImportFrom):
        _apply_generator_import_from(statement, state)
        return
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
        _compile_function(statement, state)
        return
    if isinstance(statement, ast.ClassDef):
        _compile_class(statement, state)
        return
    if isinstance(statement, (ast.Assign, ast.AnnAssign)):
        _compile_assignment(statement, state)
        return
    if isinstance(statement, ast.Return):
        _compile_return(statement, state)
        return
    if _compile_compound(
        statement,
        state,
        _compile_expression,
        _compile_block,
    ):
        return
    for child in ast.iter_child_nodes(statement):
        if isinstance(child, ast.expr):
            _compile_expression(child, state)
    for block in _nested_blocks(statement):
        _compile_block(block, state.fork())
    _invalidate_rebound_names(statement, state)


def _compile_class(node: ast.ClassDef, state: _ConsumerState) -> None:
    _compile_expressions(
        (
            *node.decorator_list,
            *node.bases,
            *(keyword.value for keyword in node.keywords),
        ),
        state,
    )
    _invalidate_non_consumers(state)
    enter_protocols = _context_manager_generator_lineage(
        node,
        state,
        _compile_function_body,
    )
    _bind_names({node.name}, "unknown", state)
    _bind_context_manager_names({node.name}, enter_protocols, state)
    _compile_block(node.body, state.fork())
    if any(isinstance(child, (ast.Global, ast.Nonlocal)) for child in ast.walk(node)):
        _invalidate_non_consumers(state)


def _compile_assignment(
    node: ast.Assign | ast.AnnAssign,
    state: _ConsumerState,
) -> None:
    value = node.value
    if value is None:
        return
    _compile_expression(value, state)
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    identity = _callable_identity(value, state)
    lineage = _generator_lineage(value, state.generators)
    manager_protocols = _context_manager_expression_protocols(value, state)
    for target in targets:
        names = _bound_names(target)
        _bind_names(names, identity, state)
        _bind_generator_names(names, lineage, state)
        _bind_context_manager_names(names, manager_protocols, state)


def _compile_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    outer: _ConsumerState,
) -> None:
    header = (
        *node.decorator_list,
        *node.args.defaults,
        *(item for item in node.args.kw_defaults if item is not None),
        *(_function_annotation_expressions(node) if outer.evaluate_annotations else ()),
    )
    _compile_expressions(header, outer)
    if node.decorator_list:
        _invalidate_non_consumers(outer)
    identity = (
        "no-consume"
        if not node.decorator_list
        and _proven_non_consumer(node)
        and not any(_scope_declarations(node))
        else "unknown"
    )
    _bind_names({node.name}, identity, outer)
    _compile_function_body(node, outer)


def _compile_function_body(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    outer: _ConsumerState,
) -> _ConsumerState:
    local = outer.fork()
    for name in _local_bindings(node):
        local.identities[name] = "unknown"
        local.builtin_modules.discard(name)
        local.generators.pop(name, None)
        local.context_manager_protocols.pop(name, None)
    _compile_block(node.body, local)
    return local


def _compile_return(node: ast.Return, state: _ConsumerState) -> None:
    if node.value is None:
        return
    _compile_expression(node.value, state)
    lineage = _generator_lineage(node.value, state.generators)
    previous = getattr(node, _RETURN_LINEAGE_ATTRIBUTE, ())
    setattr(node, _RETURN_LINEAGE_ATTRIBUTE, _merge_lineages(previous, lineage))


def _compile_expression(expression: ast.expr, state: _ConsumerState) -> None:
    _ExpressionCompiler(state).visit(expression)


def _compile_expressions(
    expressions: tuple[ast.expr, ...],
    state: _ConsumerState,
) -> None:
    for expression in expressions:
        _compile_expression(expression, state)


def _callable_identity(expression: ast.expr, state: _ConsumerState) -> str:
    if isinstance(expression, ast.Name):
        if expression.id in state.identities:
            return state.identities[expression.id]
        return _builtin_consumer_identity(expression.id)
    if isinstance(expression, ast.Attribute):
        if (
            isinstance(expression.value, ast.Name)
            and expression.value.id in state.builtin_modules
            and _is_builtin_consumer(expression.attr)
        ):
            return _builtin_consumer_identity(expression.attr)
        return "unknown"
    if isinstance(expression, ast.Subscript):
        selected = _constant_subscript_value(expression)
        if selected is not None:
            return _callable_identity(selected, state)
    if isinstance(expression, ast.IfExp):
        body = _callable_identity(expression.body, state)
        other = _callable_identity(expression.orelse, state)
        return body if body == other else "unknown"
    if isinstance(expression, ast.Lambda):
        return "no-consume" if _lambda_ignores_arguments(expression) else "unknown"
    return "unknown"


class _ExpressionCompiler(ast.NodeVisitor):
    def __init__(self, state: _ConsumerState) -> None:
        self.state = state

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        names = _bound_names(node.target)
        manager_protocols = _context_manager_expression_protocols(
            node.value,
            self.state,
        )
        _bind_names(
            names,
            _callable_identity(node.value, self.state),
            self.state,
        )
        _bind_generator_names(
            names,
            _generator_lineage(node.value, self.state.generators),
            self.state,
        )
        _bind_context_manager_names(names, manager_protocols, self.state)

    def visit_Call(self, node: ast.Call) -> None:
        identity = _callable_identity(node.func, self.state)
        self.visit(node.func)
        for argument in node.args:
            self.visit(argument)
        for keyword in node.keywords:
            self.visit(keyword.value)
        all_generators = _merge_lineages(
            *(
                _generator_lineage(argument, self.state.generators)
                for argument in node.args
            ),
            *(
                _generator_lineage(keyword.value, self.state.generators)
                for keyword in node.keywords
            ),
        )
        positional, _, arguments_complete = _expanded_call_arguments(node)
        generators = all_generators
        if identity == "consume-one" and positional:
            generators = _generator_lineage(
                positional[0],
                self.state.generators,
            )
        elif identity == "consume-one" and arguments_complete:
            generators = ()
        mode = _consumer_mode(identity, generators)
        offsets = tuple(
            self.state.generator_offsets.get(id(generator), 0)
            for generator in generators
        )
        _set_consumer_metadata(
            node,
            f"consume-{mode}" if identity.startswith("consume-") else identity,
            generators,
            offsets,
        )
        _advance_generator_offsets(generators, mode, self.state)
        if identity == "unknown":
            _invalidate_non_consumers(self.state)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        if node.generators:
            self.visit(node.generators[0].iter)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.visit(node.test)
        truth = _static_truth(node.test)
        if truth is not None:
            self.visit(node.body if truth else node.orelse)
            return
        branches = []
        for expression in (node.body, node.orelse):
            branch = self.state.fork()
            _ExpressionCompiler(branch).visit(expression)
            branches.append(branch)
        _merge_consumer_states(self.state, branches)


def _invalidate_rebound_names(
    statement: ast.stmt,
    state: _ConsumerState,
) -> None:
    finder = _ReboundNameFinder()
    for block in _nested_blocks(statement):
        for child in block:
            finder.visit(child)
    _bind_names(finder.names, "unknown", state)


def _consumer_mode(
    identity: str,
    generators: tuple[ast.GeneratorExp, ...],
) -> str:
    if identity == "consume-one":
        if any(
            len(generator.generators) != 1
            or any(item.ifs for item in generator.generators)
            for generator in generators
        ):
            return "unknown"
        return "one"
    return identity.removeprefix("consume-")


class _ReboundNameFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.names.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


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


__all__: list[str] = []
