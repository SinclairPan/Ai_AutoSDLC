"""按词法作用域识别返回动态导入入口的函数与类方法。"""

from __future__ import annotations

import ast
import copy
from collections.abc import Collection, Iterable
from dataclasses import dataclass

from ai_sdlc.core.lean_code_generator_consumers import (
    _annotate_generator_consumers,
)
from ai_sdlc.core.lean_code_import_binding_flow import _default_dynamic_aliases
from ai_sdlc.core.lean_code_import_class_flow import _dynamic_class_callable_paths
from ai_sdlc.core.lean_code_import_class_identity import _ClassCandidate
from ai_sdlc.core.lean_code_import_factory_flow import (
    _function_uses_dynamic_dependency,
    _returns_dynamic_callable,
)
from ai_sdlc.core.lean_code_scope import _local_bindings, _scope_imports


@dataclass(frozen=True)
class _FactoryCandidate:
    node: ast.FunctionDef | ast.AsyncFunctionDef
    parent_scope: tuple[int, ...]
    class_ancestors: tuple[int, ...]
    class_names: tuple[str, ...]
    class_member: bool


@dataclass(frozen=True)
class _DynamicFactoryBindings:
    function_nodes: frozenset[int]
    class_callables: dict[int, frozenset[str]]

    def is_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        return id(node) in self.function_nodes

    def callables_for_class(self, node: ast.ClassDef) -> frozenset[str]:
        return self.class_callables.get(id(node), frozenset())


def _dynamic_factory_bindings(
    tree: ast.Module,
    module_aliases: Collection[str],
    callable_aliases: Collection[str],
) -> _DynamicFactoryBindings:
    _annotate_generator_consumers(tree)
    candidates: list[_FactoryCandidate] = []
    classes: list[_ClassCandidate] = []
    _collect_candidates(
        tree.body,
        parent_scope=(),
        class_ancestors=(),
        class_names=(),
        container_kind="module",
        candidates=candidates,
        classes=classes,
    )
    dynamic_nodes: set[int] = set()
    deferred_annotations = _uses_deferred_annotations(tree)
    changed = True
    while changed:
        changed = False
        for candidate in candidates:
            if id(candidate.node) in dynamic_nodes:
                continue
            modules, callables = _visible_dynamic_aliases(
                candidate,
                candidates,
                dynamic_nodes,
                module_aliases,
                callable_aliases,
            )
            if _candidate_is_dynamic(
                candidate.node,
                modules,
                callables,
                deferred_annotations=deferred_annotations,
            ):
                dynamic_nodes.add(id(candidate.node))
                changed = True
    class_callables = _dynamic_class_callable_paths(
        tree,
        classes,
        dynamic_nodes,
        _dynamic_names_by_scope(candidates, dynamic_nodes),
    )
    return _DynamicFactoryBindings(
        function_nodes=frozenset(dynamic_nodes),
        class_callables=class_callables,
    )


def _candidate_is_dynamic(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    modules: Collection[str],
    callables: Collection[str],
    *,
    deferred_annotations: bool,
) -> bool:
    runtime_node = _runtime_function_view(node, deferred_annotations)
    return _returns_dynamic_callable(
        runtime_node,
        modules,
        callables,
    ) or _function_uses_dynamic_dependency(
        runtime_node,
        modules,
        callables,
    )


def _uses_deferred_annotations(tree: ast.Module) -> bool:
    return any(
        isinstance(statement, ast.ImportFrom)
        and statement.module == "__future__"
        and any(alias.name == "annotations" for alias in statement.names)
        for statement in tree.body
    )


def _runtime_function_view(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    deferred_annotations: bool,
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    if not deferred_annotations:
        return node
    runtime_node = copy.deepcopy(node)
    for child in ast.walk(runtime_node):
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for argument in (
            *child.args.posonlyargs,
            *child.args.args,
            *child.args.kwonlyargs,
            *((child.args.vararg,) if child.args.vararg is not None else ()),
            *((child.args.kwarg,) if child.args.kwarg is not None else ()),
        ):
            argument.annotation = None
        child.returns = None
    return runtime_node


def _collect_candidates(
    statements: Iterable[ast.stmt],
    *,
    parent_scope: tuple[int, ...],
    class_ancestors: tuple[int, ...],
    class_names: tuple[str, ...],
    container_kind: str,
    candidates: list[_FactoryCandidate],
    classes: list[_ClassCandidate],
) -> None:
    for statement in statements:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _collect_function_candidate(
                statement,
                parent_scope,
                class_ancestors,
                class_names,
                container_kind,
                candidates,
                classes,
            )
        elif isinstance(statement, ast.ClassDef):
            _collect_class_candidate(
                statement,
                parent_scope,
                class_ancestors,
                class_names,
                candidates,
                classes,
            )
        else:
            for block in _statement_blocks(statement):
                _collect_candidates(
                    block,
                    parent_scope=parent_scope,
                    class_ancestors=class_ancestors,
                    class_names=class_names,
                    container_kind=container_kind,
                    candidates=candidates,
                    classes=classes,
                )


def _collect_function_candidate(
    statement: ast.FunctionDef | ast.AsyncFunctionDef,
    parent_scope: tuple[int, ...],
    class_ancestors: tuple[int, ...],
    class_names: tuple[str, ...],
    container_kind: str,
    candidates: list[_FactoryCandidate],
    classes: list[_ClassCandidate],
) -> None:
    candidates.append(
        _FactoryCandidate(
            node=statement,
            parent_scope=parent_scope,
            class_ancestors=class_ancestors,
            class_names=class_names,
            class_member=container_kind == "class",
        )
    )
    _collect_candidates(
        statement.body,
        parent_scope=(*parent_scope, id(statement)),
        class_ancestors=class_ancestors,
        class_names=class_names,
        container_kind="function",
        candidates=candidates,
        classes=classes,
    )


def _collect_class_candidate(
    statement: ast.ClassDef,
    parent_scope: tuple[int, ...],
    class_ancestors: tuple[int, ...],
    class_names: tuple[str, ...],
    candidates: list[_FactoryCandidate],
    classes: list[_ClassCandidate],
) -> None:
    classes.append(
        _ClassCandidate(
            node=statement,
            parent_scope=parent_scope,
            class_ancestors=(*class_ancestors, id(statement)),
            class_names=(*class_names, statement.name),
        )
    )
    _collect_candidates(
        statement.body,
        parent_scope=(*parent_scope, id(statement)),
        class_ancestors=(*class_ancestors, id(statement)),
        class_names=(*class_names, statement.name),
        container_kind="class",
        candidates=candidates,
        classes=classes,
    )


def _visible_dynamic_aliases(
    candidate: _FactoryCandidate,
    candidates: Collection[_FactoryCandidate],
    dynamic_nodes: Collection[int],
    module_aliases: Collection[str],
    callable_aliases: Collection[str],
) -> tuple[set[str], set[str]]:
    local_names = _local_bindings(candidate.node)
    outer_modules = set(module_aliases)
    outer_callables = set(callable_aliases)
    outer_callables.update(
        _active_sibling_factories(
            candidate.parent_scope,
            candidates,
            dynamic_nodes,
        )
    )
    outer_callables.update(
        _visible_class_factory_paths(candidate, candidates, dynamic_nodes)
    )
    modules = outer_modules - local_names
    callables = outer_callables - local_names
    local_modules, local_callables = _dynamic_import_aliases(
        _scope_imports(candidate.node)
    )
    default_modules, default_callables = _default_dynamic_aliases(
        candidate.node.args,
        outer_modules,
        outer_callables,
    )
    modules.update(local_modules)
    modules.update(default_modules)
    callables.update(local_callables)
    callables.update(default_callables)
    callables.update(
        _active_sibling_factories(
            (*candidate.parent_scope, id(candidate.node)),
            candidates,
            dynamic_nodes,
        )
    )
    return modules, callables


def _visible_class_factory_paths(
    candidate: _FactoryCandidate,
    candidates: Collection[_FactoryCandidate],
    dynamic_nodes: Collection[int],
) -> set[str]:
    visible_scopes = {
        (),
        candidate.parent_scope,
        (*candidate.parent_scope, id(candidate.node)),
    }
    paths: set[str] = set()
    for sibling in candidates:
        if not sibling.class_member or id(sibling.node) not in dynamic_nodes:
            continue
        outer_scope = sibling.parent_scope[: -len(sibling.class_ancestors)]
        if outer_scope in visible_scopes:
            paths.add(".".join((*sibling.class_names, sibling.node.name)))
    return paths


def _active_sibling_factories(
    parent_scope: tuple[int, ...],
    candidates: Collection[_FactoryCandidate],
    dynamic_nodes: Collection[int],
) -> set[str]:
    latest: dict[str, _FactoryCandidate] = {}
    for candidate in candidates:
        if candidate.parent_scope == parent_scope:
            current = latest.get(candidate.node.name)
            if current is None or _definition_position(candidate.node) > _definition_position(
                current.node
            ):
                latest[candidate.node.name] = candidate
    return {
        name
        for name, candidate in latest.items()
        if id(candidate.node) in dynamic_nodes
    }


def _definition_position(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[int, int]:
    return node.lineno, node.col_offset


def _dynamic_names_by_scope(
    candidates: Collection[_FactoryCandidate],
    dynamic_nodes: Collection[int],
) -> dict[tuple[int, ...], frozenset[str]]:
    scopes = {candidate.parent_scope for candidate in candidates}
    return {
        scope: frozenset(
            _active_sibling_factories(scope, candidates, dynamic_nodes)
        )
        for scope in scopes
    }


def _statement_blocks(statement: ast.stmt) -> tuple[list[ast.stmt], ...]:
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


def _dynamic_import_aliases(
    nodes: Iterable[ast.Import | ast.ImportFrom],
) -> tuple[set[str], set[str]]:
    modules: set[str] = set()
    callables: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            modules.update(
                alias.asname or alias.name.split(".", 1)[0]
                for alias in node.names
                if alias.name.split(".", 1)[0] in {"builtins", "importlib"}
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "importlib":
            callables.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "import_module"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "builtins":
            callables.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name
                in {"__import__", "eval", "exec", "globals", "locals"}
            )
    return modules, callables


__all__: list[str] = []
