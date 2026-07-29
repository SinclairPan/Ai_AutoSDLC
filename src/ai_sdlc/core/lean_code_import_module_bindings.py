"""解析模块级静态导入、动态入口与重绑定状态。"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass

from ai_sdlc.core.lean_code_control_flow import _function_annotation_expressions
from ai_sdlc.core.lean_code_import_binding_flow import (
    _assigned_dynamic_aliases,
    _dynamic_alias_bindings,
    _module_rebound_names,
    _nested_binding_uncertainty,
)
from ai_sdlc.core.lean_code_import_class_aliases import (
    _assigned_dynamic_class_callables,
    _discard_callable_binding,
)
from ai_sdlc.core.lean_code_import_expression_flow import (
    _named_expression_binding_paths,
)
from ai_sdlc.core.lean_code_import_factories import (
    _dynamic_factory_bindings,
    _dynamic_import_aliases,
    _DynamicFactoryBindings,
)
from ai_sdlc.core.lean_code_module_execution import _ModuleExecutionState
from ai_sdlc.core.lean_code_scope import _bound_names


@dataclass(frozen=True)
class _ImportBindings:
    values: dict[str, str]
    uncertain: bool = False
    uncertain_names: frozenset[str] = frozenset()
    dynamic_modules: frozenset[str] = frozenset()
    dynamic_callables: frozenset[str] = frozenset()
    historically_dynamic_function_nodes: frozenset[int] = frozenset()


def _module_import_bindings(tree: ast.Module | None) -> _ImportBindings:
    if tree is None:
        return _ImportBindings({})
    imports = [
        node
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    bindings = _import_bindings(imports)
    dynamic_modules, dynamic_callables = _dynamic_import_aliases(imports)
    dynamic_factories = _dynamic_factory_bindings(
        tree,
        dynamic_modules,
        dynamic_callables,
    )
    dynamic_modules.update({"__builtins__", "__loader__"})
    if "__builtins__" in bindings:
        dynamic_modules.discard("__builtins__")
    uncertain = _has_star_import(imports)
    uncertain_names: set[str] = set()
    execution = _ModuleExecutionState()
    evaluate_annotations = not _postpones_annotations(tree)
    for statement in tree.body:
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            continue
        uncertain = (
            _apply_module_binding_statement(
                statement,
                bindings,
                dynamic_modules,
                dynamic_callables,
                dynamic_factories,
                uncertain_names,
                execution,
                evaluate_annotations=evaluate_annotations,
            )
            or uncertain
        )
    return _ImportBindings(
        bindings,
        uncertain,
        frozenset(uncertain_names),
        frozenset(dynamic_modules),
        frozenset(dynamic_callables),
        frozenset(execution.historically_dynamic_nodes),
    )


def _apply_module_binding_statement(
    statement: ast.stmt,
    bindings: dict[str, str],
    dynamic_modules: set[str],
    dynamic_callables: set[str],
    dynamic_factories: _DynamicFactoryBindings,
    uncertain_names: set[str],
    execution: _ModuleExecutionState,
    *,
    evaluate_annotations: bool,
) -> bool:
    execution.observe(
        statement,
        dynamic_modules,
        dynamic_callables,
        evaluate_annotations=evaluate_annotations,
    )
    eager_rebound, eager_modules, eager_callables = _eager_expression_aliases(
        statement,
        dynamic_modules,
        dynamic_callables,
        evaluate_annotations=evaluate_annotations,
    )
    rebound = _module_rebound_names(statement) | eager_rebound
    _invalidate_rebound_imports(rebound, bindings, uncertain_names)
    nested_names, unbounded = _nested_binding_uncertainty(
        statement,
        dynamic_modules,
        dynamic_callables,
    )
    uncertain_names.update(nested_names)
    assigned_modules, assigned_callables = _assigned_dynamic_bindings(
        statement,
        dynamic_modules,
        dynamic_callables,
        eager_modules,
        eager_callables,
    )
    for name in rebound:
        dynamic_modules.discard(name)
        _discard_callable_binding(dynamic_callables, name)
    _apply_dynamic_factory_definition(
        statement,
        dynamic_factories,
        dynamic_callables,
    )
    dynamic_modules.update(assigned_modules)
    dynamic_callables.update(assigned_callables)
    execution.apply_binding(statement)
    return unbounded


def _assigned_dynamic_bindings(
    statement: ast.stmt,
    dynamic_modules: set[str],
    dynamic_callables: set[str],
    eager_modules: set[str],
    eager_callables: set[str],
) -> tuple[set[str], set[str]]:
    assigned_modules, assigned_callables = _assigned_dynamic_aliases(
        statement,
        dynamic_modules,
        dynamic_callables,
    )
    assigned_modules.update(eager_modules)
    assigned_callables.update(eager_callables)
    assigned_callables.update(
        _assigned_dynamic_class_callables(statement, dynamic_callables)
    )
    return assigned_modules, assigned_callables


def _apply_dynamic_factory_definition(
    statement: ast.stmt,
    dynamic_factories: _DynamicFactoryBindings,
    dynamic_callables: set[str],
) -> None:
    if (
        isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
        and dynamic_factories.is_function(statement)
    ):
        dynamic_callables.add(statement.name)
    elif isinstance(statement, ast.ClassDef):
        dynamic_callables.update(
            dynamic_factories.callables_for_class(statement)
        )
    else:
        dynamic_callables.update(
            _branch_dynamic_factory_callables(
                statement,
                dynamic_factories,
                dynamic_callables,
            )
        )


def _eager_expression_aliases(
    statement: ast.stmt,
    dynamic_modules: set[str],
    dynamic_callables: set[str],
    *,
    evaluate_annotations: bool,
) -> tuple[set[str], set[str], set[str]]:
    bindings = tuple(
        binding
        for candidate in (statement, *_reachable_branch_statements(statement))
        for expression in _definition_time_expressions(
            candidate,
            evaluate_annotations=evaluate_annotations,
        )
        for path in _named_expression_binding_paths(expression)
        for binding in path
    )
    bindings += tuple(
        binding
        for expression in _compound_header_expressions(
            statement,
            evaluate_annotations=evaluate_annotations,
        )
        for path in _named_expression_binding_paths(expression)
        for binding in path
    )
    rebound: set[str] = set()
    modules: set[str] = set()
    callables: set[str] = set()
    for target, value in bindings:
        rebound.update(_bound_names(target))
        bound_modules, bound_callables = _dynamic_alias_bindings(
            target,
            value,
            dynamic_modules,
            dynamic_callables,
        )
        modules.update(bound_modules)
        callables.update(bound_callables)
    return rebound, modules, callables


def _definition_time_expressions(
    statement: ast.stmt,
    *,
    evaluate_annotations: bool,
) -> tuple[ast.expr, ...]:
    if isinstance(statement, ast.Expr):
        return (statement.value,)
    if isinstance(statement, ast.Assign):
        return (statement.value,)
    if isinstance(statement, ast.AnnAssign):
        value = (statement.value,) if statement.value is not None else ()
        annotation = (
            (statement.annotation,)
            if evaluate_annotations and statement.simple
            else ()
        )
        return (*value, *annotation)
    if isinstance(statement, ast.AugAssign):
        return (statement.value,)
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return (
            *statement.decorator_list,
            *getattr(statement, "type_params", ()),
            *statement.args.defaults,
            *(item for item in statement.args.kw_defaults if item is not None),
            *(
                _function_annotation_expressions(statement)
                if evaluate_annotations
                else ()
            ),
        )
    if isinstance(statement, ast.ClassDef):
        return (
            *statement.decorator_list,
            *statement.bases,
            *(keyword.value for keyword in statement.keywords),
            *getattr(statement, "type_params", ()),
        )
    return ()


def _compound_header_expressions(
    statement: ast.stmt,
    *,
    evaluate_annotations: bool,
) -> tuple[ast.expr, ...]:
    del evaluate_annotations
    if isinstance(statement, ast.If):
        return (statement.test,)
    if isinstance(statement, (ast.For, ast.AsyncFor)):
        return (statement.iter,)
    if isinstance(statement, ast.While):
        return (statement.test,)
    if isinstance(statement, (ast.With, ast.AsyncWith)):
        return tuple(item.context_expr for item in statement.items)
    if isinstance(statement, ast.Match):
        return (
            statement.subject,
            *(
                case.guard
                for case in statement.cases
                if case.guard is not None
            ),
        )
    return ()


def _postpones_annotations(tree: ast.Module) -> bool:
    return any(
        isinstance(statement, ast.ImportFrom)
        and statement.module == "__future__"
        and any(alias.name == "annotations" for alias in statement.names)
        for statement in tree.body
    )


def _invalidate_rebound_imports(
    rebound: set[str],
    bindings: dict[str, str],
    uncertain_names: set[str],
) -> None:
    rebound_imports = rebound & bindings.keys()
    uncertain_names.update(rebound_imports)
    for name in rebound_imports:
        bindings.pop(name, None)


def _branch_dynamic_factory_callables(
    statement: ast.stmt,
    dynamic_factories: _DynamicFactoryBindings,
    visible_callables: set[str],
) -> set[str]:
    callables = set(visible_callables)
    additions: set[str] = set()
    for child in _reachable_branch_statements(statement):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if dynamic_factories.is_function(child):
                callables.add(child.name)
                additions.add(child.name)
            continue
        if isinstance(child, ast.ClassDef):
            discovered = dynamic_factories.callables_for_class(child)
            callables.update(discovered)
            additions.update(discovered)
            continue
        _, aliases = _assigned_dynamic_aliases(child, set(), callables)
        aliases.update(_assigned_dynamic_class_callables(child, callables))
        callables.update(aliases)
        additions.update(aliases)
    return additions


def _reachable_branch_statements(
    statement: ast.stmt,
) -> Iterable[ast.stmt]:
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return
    blocks: tuple[list[ast.stmt], ...] = ()
    if isinstance(statement, ast.If):
        if isinstance(statement.test, ast.Constant):
            blocks = (statement.body if bool(statement.test.value) else statement.orelse,)
        else:
            blocks = statement.body, statement.orelse
    elif isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
        blocks = statement.body, statement.orelse
    elif isinstance(statement, (ast.With, ast.AsyncWith)):
        blocks = (statement.body,)
    elif isinstance(statement, (ast.Try, ast.TryStar)):
        blocks = (
            statement.body,
            *(handler.body for handler in statement.handlers),
            statement.orelse,
            statement.finalbody,
        )
    elif isinstance(statement, ast.Match):
        blocks = tuple(case.body for case in statement.cases)
    for block in blocks:
        for child in block:
            yield child
            yield from _reachable_branch_statements(child)


def _import_bindings(
    nodes: Iterable[ast.Import | ast.ImportFrom],
) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".", 1)[0]
                bindings[local_name] = alias.name.split(".", 1)[0]
        elif isinstance(node, ast.ImportFrom) and node.module:
            module = node.module.split(".", 1)[0]
            for alias in node.names:
                if alias.name != "*":
                    bindings[alias.asname or alias.name] = module
    return bindings


def _has_star_import(nodes: Iterable[ast.Import | ast.ImportFrom]) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and any(alias.name == "*" for alias in node.names)
        for node in nodes
    )


__all__: list[str] = []
