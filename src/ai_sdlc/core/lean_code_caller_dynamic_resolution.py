"""Private dynamic resolution responsibility for Lean caller analysis."""

from __future__ import annotations

import ast

from ai_sdlc.core.lean_code_caller_binding_resolution import (
    _bound_protocol_receivers,
    _module_star_binding_at,
    _nested_star_binding_active,
    _star_expression_target_active,
    _target_reexports,
)
from ai_sdlc.core.lean_code_caller_callable_index import (
    _comprehension_shadows,
)
from ai_sdlc.core.lean_code_caller_models import (
    _DynamicScope,
    _ImportedCallables,
    _ImportlibBindings,
    _SourceShape,
    _TargetExports,
)
from ai_sdlc.core.lean_code_caller_module_semantics import (
    _candidate_import_call_is_linked,
)
from ai_sdlc.core.lean_code_caller_primitives import (
    _class_base_name,
)
from ai_sdlc.core.lean_code_caller_protocol_resolution import (
    _dynamic_import_binding_candidates_at,
)
from ai_sdlc.core.lean_code_caller_scope_semantics import (
    _global_effects_before,
)
from ai_sdlc.core.lean_code_caller_source_index import (
    _dynamic_import_call_shape,
)
from ai_sdlc.core.lean_code_caller_target_semantics import (
    _dynamic_scope_shadows,
    _enclosing_global_effects,
    _target_class_family,
)
from ai_sdlc.core.lean_code_execution_order import _expression_binding_events_before
from ai_sdlc.core.lean_code_models import FunctionMetric


def _protocol_exports_by_method(
    sources: dict[str, tuple[ast.Module, _SourceShape]],
    candidates: dict[tuple[str, str], set[str]],
) -> dict[str, tuple[tuple[str, str, _TargetExports], ...]]:
    protocols: dict[str, list[tuple[str, str, _TargetExports]]] = {}
    for path, (tree, _) in sources.items():
        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or not any(
                _class_base_name(base).rsplit(".", 1)[-1] == "Protocol"
                for base in node.bases
            ):
                continue
            for child in node.body:
                if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                target = (path, f"{node.name}.{child.name}")
                exports = _target_reexports(sources, candidates, target)
                protocols.setdefault(child.name, []).append(
                    (path, node.name, exports)
                )
    return {
        name: tuple(entries)
        for name, entries in protocols.items()
    }

def _star_owner_call_is_linked(
    node: ast.Call,
    owner: _DynamicScope,
    tree: ast.Module,
    caller_path: str,
    target_name: str,
    target_modules: set[str],
    active: bool,
    expression_active: bool | None,
    local_bindings: dict[int, set[str]],
    parents: dict[ast.AST, ast.AST],
    imported_callables: _ImportedCallables,
) -> bool:
    nested_active = _nested_star_binding_active(
        owner,
        tree,
        caller_path,
        target_name,
        target_modules,
        active,
        parents,
        imported_callables,
    )
    if nested_active is not None:
        active = nested_active
    if _dynamic_scope_shadows(owner, node, target_name, parents, local_bindings):
        return False
    if (
        expression_active is None
        and nested_active is None
        and target_name in _enclosing_global_effects(owner, parents)
    ):
        return False
    if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        if expression_active is not None:
            return active
        return (
            target_name not in _global_effects_before(owner, node, parents) and active
        )
    return active

def _star_call_initial_state(
    node: ast.Call,
    owner: _DynamicScope | None,
    tree: ast.Module,
    caller_path: str,
    target_name: str,
    target_modules: set[str],
    module_star_active: bool,
    expression_active: bool | None,
    parents: dict[ast.AST, ast.AST],
    imported_callables: _ImportedCallables,
) -> bool:
    if expression_active is not None:
        return expression_active
    return _module_star_binding_at(
        node,
        owner,
        tree,
        caller_path,
        target_name,
        target_modules,
        module_star_active,
        parents,
        imported_callables,
    )

def _star_expression_state(
    node: ast.Call,
    owner: _DynamicScope | None,
    tree: ast.Module,
    caller_path: str,
    target_name: str,
    target_modules: set[str],
    parents: dict[ast.AST, ast.AST],
) -> tuple[bool | None, bool]:
    events = _expression_binding_events_before(node, parents)
    active = _star_expression_target_active(
        node,
        owner,
        tree,
        caller_path,
        target_name,
        target_modules,
        events,
        parents,
    )
    shadowed = any(name == target_name for name, _ in events) and active is None
    return active, shadowed

def _resolved_target_exports(
    sources: dict[str, tuple[ast.Module, _SourceShape]],
    candidates: dict[tuple[str, str], set[str]],
    targets: dict[tuple[str, str], FunctionMetric],
) -> dict[tuple[str, str], _TargetExports]:
    protocol_exports = _protocol_exports_by_method(sources, candidates)
    resolved: dict[tuple[str, str], _TargetExports] = {}
    for target in targets:
        exports = _target_reexports(sources, candidates, target)
        exports = _target_class_family(sources, exports, target)
        resolved[target] = _bound_protocol_receivers(
            sources,
            exports,
            target,
            protocol_exports,
        )
    return resolved

def _linked_star_call(
    node: ast.Call,
    owner: _DynamicScope | None,
    tree: ast.Module,
    caller_path: str,
    target_name: str,
    target_modules: set[str],
    module_star_active: bool,
    local_bindings: dict[int, set[str]],
    parents: dict[ast.AST, ast.AST],
    imported_callables: _ImportedCallables,
) -> bool:
    expression_active, expression_shadowed = _star_expression_state(
        node,
        owner,
        tree,
        caller_path,
        target_name,
        target_modules,
        parents,
    )
    if expression_shadowed:
        return False
    active = _star_call_initial_state(
        node,
        owner,
        tree,
        caller_path,
        target_name,
        target_modules,
        module_star_active,
        expression_active,
        parents,
        imported_callables,
    )
    if owner is None:
        return active
    return _star_owner_call_is_linked(
        node,
        owner,
        tree,
        caller_path,
        target_name,
        target_modules,
        active,
        expression_active,
        local_bindings,
        parents,
        imported_callables,
    )

def _star_import_call_is_linked(
    node: ast.Call,
    owner: _DynamicScope | None,
    tree: ast.Module,
    caller_path: str,
    target_modules: set[str],
    module_star_active: bool,
    local_bindings: dict[int, set[str]],
    parents: dict[ast.AST, ast.AST],
    imported_callables: _ImportedCallables,
) -> bool:
    if not isinstance(node.func, ast.Name) or not target_modules:
        return False
    if _comprehension_shadows(node, node.func.id, parents):
        return False
    return _linked_star_call(
        node,
        owner,
        tree,
        caller_path,
        node.func.id,
        target_modules,
        module_star_active,
        local_bindings,
        parents,
        imported_callables,
    )

def _dynamic_call_is_linked(
    node: ast.Call,
    owner: _DynamicScope | None,
    tree: ast.Module,
    path: str,
    exports_by_module: dict[str, frozenset[str]],
    modules_by_export: dict[str, set[str]],
    module_bindings: _ImportlibBindings,
    module_star_bindings: dict[str, bool],
    local_bindings: dict[int, set[str]],
    parents: dict[ast.AST, ast.AST],
    imported_callables: _ImportedCallables,
) -> bool:
    call_name = node.func.id if isinstance(node.func, ast.Name) else ""
    if _star_import_call_is_linked(
        node,
        owner,
        tree,
        path,
        modules_by_export.get(call_name, set()),
        module_star_bindings.get(call_name, False),
        local_bindings,
        parents,
        imported_callables,
    ):
        return True
    if not _dynamic_import_call_shape(node, frozenset(modules_by_export)):
        return False
    candidates = _dynamic_import_binding_candidates_at(
        node,
        owner,
        tree,
        module_bindings,
        local_bindings,
        parents,
        imported_callables,
    )
    events = _expression_binding_events_before(node, parents)
    return _candidate_import_call_is_linked(
        node, exports_by_module, candidates, events, parents
    )

__all__: list[str] = []
