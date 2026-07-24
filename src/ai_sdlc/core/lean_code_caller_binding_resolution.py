"""Private binding resolution responsibility for Lean caller analysis."""

from __future__ import annotations

import ast
from collections import deque

from ai_sdlc.core.lean_code_caller_callable_index import (
    _generator_inherits_outer,
    _statements_before_node,
)
from ai_sdlc.core.lean_code_caller_models import (
    _DynamicScope,
    _ImportedCallables,
    _SourceShape,
    _TargetExports,
)
from ai_sdlc.core.lean_code_caller_primitives import (
    _candidate_reexport_paths,
    _enclosing_class_node,
    _enclosing_function_or_lambda,
)
from ai_sdlc.core.lean_code_caller_scope_semantics import (
    _resolved_reexport_aliases,
)
from ai_sdlc.core.lean_code_caller_target_semantics import (
    _protocol_binding_exists,
    _star_import_active,
    _star_states_before_call,
)
from ai_sdlc.core.lean_code_dynamic_refs import _enclosing_function
from ai_sdlc.core.lean_code_execution_identity import (
    _deferred_generator_paths,
    _deferred_lambda_paths,
    _nested_execution_paths,
)
from ai_sdlc.core.lean_code_execution_order import _in_deferred_generator_body


def _target_reexports(
    sources: dict[str, tuple[ast.Module, _SourceShape]],
    candidates: dict[tuple[str, str], set[str]],
    target: tuple[str, str],
) -> _TargetExports:
    target_path, target_symbol = target
    target_class, _, target_name = target_symbol.rpartition(".")
    exported_name = target_class or target_name
    exports: dict[str, set[str]] = {target_path: {exported_name}}
    pending = deque(_candidate_reexport_paths(candidates, target_path, {exported_name}))
    queued = set(pending)
    while pending:
        path = pending.popleft()
        queued.discard(path)
        aliases = _resolved_reexport_aliases(
            sources, exports, path, target_path, target_class, target_name
        )
        additions = aliases - exports.get(path, set())
        if not additions:
            continue
        exports.setdefault(path, set()).update(additions)
        for candidate in _candidate_reexport_paths(candidates, path, additions):
            if candidate not in queued:
                pending.append(candidate)
                queued.add(candidate)
    return {path: frozenset(names) for path, names in exports.items()}

def _bound_protocol_receivers(
    sources: dict[str, tuple[ast.Module, _SourceShape]],
    exports: _TargetExports,
    target: tuple[str, str],
    protocols: dict[str, tuple[tuple[str, str, _TargetExports], ...]],
) -> _TargetExports:
    target_path, target_symbol = target
    target_class, _, target_name = target_symbol.rpartition(".")
    if not target_class:
        return exports
    resolved = {path: set(names) for path, names in exports.items()}
    for protocol_path, protocol_class, protocol_exports in protocols.get(
        target_name, ()
    ):
        if not _protocol_binding_exists(
            sources,
            target_path,
            target_class,
            target_name,
            exports,
            protocol_path,
            protocol_class,
            protocol_exports,
        ):
            continue
        for path, names in protocol_exports.items():
            resolved.setdefault(path, set()).update(names)
    return {path: frozenset(names) for path, names in resolved.items()}

def _nested_star_binding_active(
    owner: _DynamicScope,
    tree: ast.Module,
    caller_path: str,
    target_name: str,
    target_modules: set[str],
    initial: bool,
    parents: dict[ast.AST, ast.AST],
    imported_callables: _ImportedCallables,
) -> bool | None:
    if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
        outer = _enclosing_function(owner, parents)
        if outer is None:
            return None
        paths = tuple(
            path
            for _, path, _ in _nested_execution_paths(
                owner, outer, parents, imported_callables
            )
        )
    elif isinstance(owner, ast.Lambda):
        outer = _enclosing_function(owner, parents)
        paths = tuple(
            path
            for _, path, _ in _deferred_lambda_paths(owner, parents, imported_callables)
        )
        if not paths:
            return False
        if outer is None:
            initial = False
    else:
        return None
    return any(
        _star_import_active(
            path,
            caller_path,
            target_name,
            target_modules,
            initial,
        )
        for path in paths
    )

def _module_star_binding_at(
    node: ast.AST,
    owner: _DynamicScope | None,
    tree: ast.Module,
    caller_path: str,
    target_name: str,
    target_modules: set[str],
    final_binding: bool,
    parents: dict[ast.AST, ast.AST],
    imported_callables: _ImportedCallables,
) -> bool:
    if _in_deferred_generator_body(node, parents):
        paths = _deferred_generator_paths(node, parents, imported_callables)
        if (
            _enclosing_class_node(node, parents) is not None
            and _enclosing_function_or_lambda(node, parents) is None
        ):
            return bool(paths) and final_binding
        inherited = final_binding if _generator_inherits_outer(node, parents) else False
        return any(
            _star_import_active(
                path,
                caller_path,
                target_name,
                target_modules,
                inherited,
            )
            for _, path, _ in paths
        )
    if owner is not None and not (
        isinstance(owner, ast.ClassDef)
        and _enclosing_function_or_lambda(owner, parents) is None
    ):
        return final_binding
    return _star_import_active(
        _statements_before_node(tree.body, node, parents),
        caller_path,
        target_name,
        target_modules,
    )

def _star_expression_target_active(
    node: ast.AST,
    owner: _DynamicScope | None,
    tree: ast.Module,
    caller_path: str,
    target_name: str,
    target_modules: set[str],
    events: tuple[tuple[str, str | None], ...],
    parents: dict[ast.AST, ast.AST],
) -> bool | None:
    if not any(name == target_name for name, _ in events):
        return None
    states = _star_states_before_call(
        node, owner, tree, caller_path, target_name, target_modules, parents
    )
    for target, source in events:
        active = source is not None and states.get(source, False)
        states[target] = active
    return states.get(target_name, False)

__all__: list[str] = []
