"""Private target semantics responsibility for Lean caller analysis."""

from __future__ import annotations

import ast
from collections.abc import Sequence

from ai_sdlc.core.lean_code_caller_callable_index import (
    _statements_before_node,
)
from ai_sdlc.core.lean_code_caller_evidence import (
    _module_global_values,
)
from ai_sdlc.core.lean_code_caller_models import (
    _CallableNode,
    _DynamicScope,
    _FunctionNode,
    _ImportedCallables,
    _ImportlibBindings,
    _SourceShape,
    _TargetCallContext,
    _TargetExports,
)
from ai_sdlc.core.lean_code_caller_module_semantics import (
    _cached_lambda_bindings,
    _expand_callable_exports,
    _module_effect_bound_names,
    _tree_binds_types,
)
from ai_sdlc.core.lean_code_caller_primitives import (
    _class_base_name,
    _enclosing_class_node,
    _star_lineage_key,
)
from ai_sdlc.core.lean_code_caller_scope_semantics import (
    _global_effects_before,
    _importlib_bindings,
    _outer_scope_shadows,
    _resolve_imported_callables,
    _star_binding_states,
    _target_module_names,
)
from ai_sdlc.core.lean_code_caller_source_index import (
    _cached_local_bindings,
    _global_importlib_bindings,
)
from ai_sdlc.core.lean_code_dynamic_refs import _enclosing_function
from ai_sdlc.core.lean_code_execution_identity import _nested_execution_anchors
from ai_sdlc.core.lean_code_scope import _scope_declarations


def _protocol_binding_exists(
    sources: dict[str, tuple[ast.Module, _SourceShape]],
    target_path: str,
    target_class: str,
    target_name: str,
    target_exports: _TargetExports,
    protocol_path: str,
    protocol_class: str,
    protocol_exports: _TargetExports,
) -> bool:
    for path, (tree, _) in sources.items():
        _, _, concrete_names = _target_module_names(
            tree.body,
            path,
            target_path,
            target_class,
            target_name,
            frozenset(target_exports),
            target_exports,
        )
        _, _, protocol_names = _target_module_names(
            tree.body,
            path,
            protocol_path,
            protocol_class,
            target_name,
            frozenset(protocol_exports),
            protocol_exports,
        )
        if _tree_binds_types(tree, concrete_names, protocol_names):
            return True
    return False


def _target_class_family(
    sources: dict[str, tuple[ast.Module, _SourceShape]],
    exports: _TargetExports,
    target: tuple[str, str],
) -> _TargetExports:
    target_path, target_symbol = target
    target_class, _, target_name = target_symbol.rpartition(".")
    if not target_class:
        return exports
    resolved = {path: set(names) for path, names in exports.items()}
    changed = True
    while changed:
        changed = False
        frozen = {path: frozenset(names) for path, names in resolved.items()}
        for path, (tree, _) in sources.items():
            _, _, class_names = _target_module_names(
                tree.body,
                path,
                target_path,
                target_class,
                target_name,
                frozenset(frozen),
                frozen,
            )
            subclasses = {
                node.name
                for node in tree.body
                if isinstance(node, ast.ClassDef)
                and any(_class_base_name(base) in class_names for base in node.bases)
                and not any(
                    isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and child.name == target_name
                    for child in node.body
                )
            }
            additions = subclasses - resolved.get(path, set())
            if additions:
                resolved.setdefault(path, set()).update(additions)
                changed = True
    return {path: frozenset(names) for path, names in resolved.items()}


def _target_call_context(
    path: str,
    tree: ast.Module,
    target: tuple[str, str],
    target_exports: _TargetExports,
) -> _TargetCallContext:
    target_path, target_symbol = target
    target_class, _, target_name = target_symbol.rpartition(".")
    direct_names, module_names, class_names = _target_module_names(
        tree.body,
        path,
        target_path,
        target_class,
        target_name,
        frozenset(target_exports),
        target_exports,
    )
    module_target = (path, target_path, target_class, target_name, target_exports)
    return module_target, direct_names, module_names, class_names


def _star_import_active(
    statements: Sequence[ast.stmt],
    caller_path: str,
    target_name: str,
    target_modules: set[str],
    initial: bool = False,
) -> bool:
    lineage_key = _star_lineage_key(target_name)
    states = _star_binding_states(
        statements,
        caller_path,
        target_name,
        target_modules,
        {target_name: initial, lineage_key: initial},
    )
    return states.get(target_name, False)


def _star_states_before_call(
    node: ast.AST,
    owner: _DynamicScope | None,
    tree: ast.Module,
    caller_path: str,
    target_name: str,
    target_modules: set[str],
    parents: dict[ast.AST, ast.AST],
) -> dict[str, bool]:
    initial = {target_name: False, _star_lineage_key(target_name): False}
    if not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return _star_binding_states(
            _statements_before_node(tree.body, node, parents),
            caller_path,
            target_name,
            target_modules,
            initial,
        )
    module_states = _star_binding_states(
        _statements_before_node(tree.body, owner, parents),
        caller_path,
        target_name,
        target_modules,
        initial,
    )
    return _star_binding_states(
        _statements_before_node(owner.body, node, parents),
        caller_path,
        target_name,
        target_modules,
        module_states,
    )


def _enclosing_global_effects(
    owner: _DynamicScope,
    parents: dict[ast.AST, ast.AST],
) -> set[str]:
    effects: set[str] = set()
    outer_function = _enclosing_function(owner, parents)
    if outer_function is not None:
        anchors = (
            _nested_execution_anchors(owner, outer_function, parents)
            if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef))
            else (owner,)
        )
        anchor_effects = [
            _global_effects_before(outer_function, anchor, parents)
            for anchor in anchors
        ]
        if anchor_effects:
            effects.update(set.intersection(*anchor_effects))
    outer_class = _enclosing_class_node(owner, parents)
    if outer_class is not None:
        effects.update(_global_effects_before(outer_class, owner, parents))
    return effects


def _function_importlib_bindings_from(
    node: ast.AST,
    owner: _FunctionNode,
    inherited: _ImportlibBindings,
    module_bindings: _ImportlibBindings,
    local_bindings: dict[int, set[str]],
    parents: dict[ast.AST, ast.AST],
    path: Sequence[ast.stmt] | None = None,
) -> _ImportlibBindings:
    inherited = _global_importlib_bindings(owner, inherited, module_bindings)
    local_names = _cached_local_bindings(owner, local_bindings)
    initial = (
        inherited[0] - local_names,
        inherited[1] - local_names,
        inherited[2],
    )
    statements = (
        list(path)
        if path is not None
        else _statements_before_node(owner.body, node, parents)
    )
    return _importlib_bindings(statements, initial)


def _dynamic_scope_shadows(
    owner: _DynamicScope,
    node: ast.AST,
    target_name: str,
    parents: dict[ast.AST, ast.AST],
    local_bindings: dict[int, set[str]],
) -> bool:
    if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
        own_bindings = _cached_local_bindings(owner, local_bindings)
        global_names, nonlocal_names = _scope_declarations(owner)
    elif isinstance(owner, ast.Lambda):
        own_bindings = _cached_lambda_bindings(owner, local_bindings)
        global_names, nonlocal_names = set(), set()
    else:
        global_names, nonlocal_names = _scope_declarations(owner)
        own_bindings = set().union(
            *(
                _module_effect_bound_names(statement)
                for statement in _statements_before_node(owner.body, node, parents)
            )
        )
        own_bindings.difference_update(global_names | nonlocal_names)
    if target_name in global_names:
        return False
    outer_shadowed = _outer_scope_shadows(
        owner,
        target_name,
        parents,
        local_bindings,
    )
    return target_name in own_bindings or outer_shadowed


def _imported_callable_index(
    sources: dict[str, tuple[ast.Module, _SourceShape]],
) -> dict[str, _ImportedCallables]:
    exports: dict[str, dict[str, _CallableNode]] = {
        path: {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for path, (tree, _) in sources.items()
    }
    origins = {
        id(node): (path, name)
        for path, definitions in exports.items()
        for name, node in definitions.items()
    }
    modules = _expand_callable_exports(sources, exports)
    globals_by_module = _module_global_values(sources)
    return {
        path: _resolve_imported_callables(
            path, tree, modules, globals_by_module, origins
        )
        for path, (tree, _) in sources.items()
    }


__all__: list[str] = []
