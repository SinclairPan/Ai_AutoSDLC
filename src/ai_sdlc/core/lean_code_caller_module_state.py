"""Private module state responsibility for Lean caller analysis."""

from __future__ import annotations

import ast
from collections.abc import Sequence

from ai_sdlc.core.lean_code_binding_state import _deferred_annotation_call_ids
from ai_sdlc.core.lean_code_caller_models import (
    _CallableNode,
    _CallableOrigins,
    _ImportedCallables,
    _ModuleTarget,
    _SourceEvidenceIndex,
    _SourceShape,
    _TargetExports,
)
from ai_sdlc.core.lean_code_caller_primitives import (
    _add_target_definition,
    _available_imported_callables,
    _class_base_name,
    _module_bound_names,
    _module_environment,
    _static_identity_value,
)
from ai_sdlc.core.lean_code_dynamic_refs import (
    _enclosing_function,
    _expression_location,
    _potential_dynamic_references,
)
from ai_sdlc.core.lean_code_flow import (
    ReferenceState,
    _calls_target,
    _references_target_class,
)
from ai_sdlc.core.lean_code_identity_callable_summary import _callable_execution_kind
from ai_sdlc.core.lean_code_identity_models import _IdentityValue
from ai_sdlc.core.lean_code_imports import (
    _import_from_modules,
    _module_names,
    _target_import_names,
)
from ai_sdlc.core.lean_code_models import FileMetric, FunctionMetric, MetricCapability
from ai_sdlc.core.lean_code_scope import _scope_imports, _without_local_roots


def _module_target_reference_locations(
    tree: ast.Module,
    parents: dict[ast.AST, ast.AST],
    direct_names: set[str],
    module_names: set[str],
    class_names: set[str],
    target_name: str,
    nodes: Sequence[ast.AST],
) -> set[str]:
    locations: set[str] = set()
    for node in nodes:
        if not isinstance(node, (ast.Name, ast.Attribute)):
            continue
        if _enclosing_function(node, parents) is not None:
            continue
        parent = parents.get(node)
        if isinstance(parent, ast.Call) and parent.func is node:
            continue
        if _calls_target(
            node,
            direct_names,
            module_names,
            class_names,
            set(),
            target_name,
        ):
            locations.add(_expression_location(node, None, parents))
    return locations

def _is_target_definition(
    path: str,
    target_path: str,
    node_name: str,
    target_name: str,
    owner_class: str,
    target_class: str,
) -> bool:
    return (
        path == target_path and node_name == target_name and owner_class == target_class
    )

def _source_evidence_index(tree: ast.Module) -> _SourceEvidenceIndex:
    nodes = tuple(ast.walk(tree))
    return _SourceEvidenceIndex(
        nodes=nodes,
        calls=tuple(node for node in nodes if isinstance(node, ast.Call)),
    )

def _imported_expression_value(
    node: ast.expr, environment: _ImportedCallables
) -> _IdentityValue:
    if isinstance(node, ast.Name):
        return environment.get(node.id, _IdentityValue())
    if isinstance(node, ast.Attribute):
        owner = _imported_expression_value(node.value, environment)
        if owner.kind == "module":
            return dict(owner.entries).get(node.attr, _IdentityValue())
    return _IdentityValue()

def _import_alias_targets(
    path: str,
    tree: ast.Module,
    referenced_names: set[str],
    export_index: dict[tuple[str, str], set[tuple[str, str]]],
) -> set[tuple[str, str]]:
    relevant: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        modules = _import_from_modules(path, node)
        for alias in node.names:
            local_name = alias.asname or alias.name
            if local_name not in referenced_names:
                continue
            for module in modules:
                relevant.update(export_index.get((module, alias.name), set()))
    return relevant

def _exposed_import_target_keys(
    environment: _ImportedCallables,
) -> set[tuple[str, str]]:
    targets: set[tuple[str, str]] = set()
    pending = list(environment.values())
    visited: set[int] = set()
    while pending:
        value = pending.pop()
        if id(value) in visited:
            continue
        visited.add(id(value))
        if value.target_key is not None:
            targets.add(value.target_key)
        if value.kind == "module":
            pending.extend(item for _, item in value.entries)
    return targets

def _target_export_index(
    target_exports: dict[tuple[str, str], _TargetExports],
) -> dict[tuple[str, str], set[tuple[str, str]]]:
    index: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for target, exports in target_exports.items():
        for path, names in exports.items():
            for module in _module_names(path):
                for name in names:
                    index.setdefault((module, name), set()).add(target)
    return index

def _new_public_targets(
    files: list[FileMetric],
) -> dict[tuple[str, str], FunctionMetric]:
    return {
        (file.path, function.symbol): function
        for file in files
        if file.capability == MetricCapability.EXACT
        for function in file.functions
        if function.public and function.is_new
    }

def _reexport_candidate_index(
    sources: dict[str, tuple[ast.Module, _SourceShape]],
) -> dict[tuple[str, str], set[str]]:
    candidates: dict[tuple[str, str], set[str]] = {}
    for path, (tree, _) in sources.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            for module in _import_from_modules(path, node):
                for alias in node.names:
                    candidates.setdefault((module, alias.name), set()).add(path)
    return candidates

def _targets_by_name(
    target_exports: dict[tuple[str, str], _TargetExports],
) -> dict[str, list[tuple[str, str]]]:
    index: dict[str, list[tuple[str, str]]] = {}
    for target, exports in target_exports.items():
        names = {target[1].rpartition(".")[2]}
        names.update(name for exported in exports.values() for name in exported)
        for name in names:
            index.setdefault(name, []).append(target)
    return index

def _source_shape(tree: ast.Module) -> _SourceShape:
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    functions = tuple(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    scope_imports = {id(node): tuple(_scope_imports(node)) for node in functions}
    dynamic_names = _potential_dynamic_references(tree, parents)
    deferred_annotation_calls = _deferred_annotation_call_ids(tree)
    return parents, functions, scope_imports, dynamic_names, deferred_annotation_calls

def _static_module_assignments(tree: ast.Module) -> dict[str, _IdentityValue]:
    values: dict[str, _IdentityValue] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            value = _static_identity_value(statement.value)
            for target in statement.targets:
                if isinstance(target, ast.Name):
                    values[target.id] = value
        elif (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.value is not None
        ):
            values[statement.target.id] = _static_identity_value(statement.value)
    return values

def _callable_value(
    node: _CallableNode,
    modules: dict[str, dict[str, _CallableNode]],
    globals_by_module: dict[str, dict[str, _IdentityValue]],
    origins: _CallableOrigins,
) -> _IdentityValue:
    origin = origins.get(id(node))
    module_id = origin[0] if origin is not None else ""
    environment = _module_environment(module_id, modules, globals_by_module, origins)
    return _IdentityValue(
        callable_node=node,
        closure=tuple(sorted(globals_by_module.get(module_id, {}).items())),
        target_key=origin,
        execution_kind=_callable_execution_kind(node),
        module_id=module_id,
        module_entries=tuple(sorted(environment.items())),
    )

def _module_reexported_callables(
    path: str,
    tree: ast.Module,
    modules: dict[str, dict[str, _CallableNode]],
) -> dict[str, _CallableNode]:
    resolved: dict[str, _CallableNode] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        available = _available_imported_callables(path, node, modules)
        for alias in node.names:
            if alias.name == "*":
                resolved.update(
                    {
                        name: value
                        for name, value in available.items()
                        if not name.startswith("_")
                    }
                )
            elif alias.name in available:
                resolved[alias.asname or alias.name] = available[alias.name]
    return resolved

def _apply_module_statement(
    node: ast.stmt,
    target: _ModuleTarget,
    state: ReferenceState,
) -> ReferenceState:
    caller_path, target_path, target_class, target_name, target_exports = target
    direct, modules, classes = (set(state[0]), set(state[1]), set(state[2]))
    bound = _module_bound_names(node)
    value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
    class_alias = value is not None and _references_target_class(value, classes)
    direct.difference_update(bound)
    modules = _without_local_roots(modules, bound)
    classes = _without_local_roots(classes, bound)
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        additions = _target_import_names(
            [node],
            caller_path,
            target_path,
            target_class,
            target_name,
            frozenset(target_exports),
            target_exports,
        )
        direct.update(additions[0])
        modules.update(additions[1])
        classes.update(additions[2])
    elif class_alias:
        classes.update(bound)
    elif caller_path == target_path:
        _add_target_definition(node, direct, classes, target_class, target_name)
    return direct, modules, classes, set()

def _annotation_class_name(node: ast.expr | None) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return _class_base_name(node) if node is not None else ""

__all__: list[str] = []
