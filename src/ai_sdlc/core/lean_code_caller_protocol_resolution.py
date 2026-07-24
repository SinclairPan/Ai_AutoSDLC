"""Private protocol resolution responsibility for Lean caller analysis."""

from __future__ import annotations

import ast

from ai_sdlc.core.lean_code_caller_callable_index import (
    _statements_before_node,
)
from ai_sdlc.core.lean_code_caller_models import (
    _DynamicScope,
    _FunctionNode,
    _ImportedCallables,
    _ImportlibBindings,
)
from ai_sdlc.core.lean_code_caller_module_semantics import (
    _cached_lambda_bindings,
)
from ai_sdlc.core.lean_code_caller_scope_semantics import (
    _enclosing_dynamic_scope,
    _importlib_bindings,
)
from ai_sdlc.core.lean_code_caller_source_index import (
    _global_importlib_bindings,
    _identity_importlib_bindings,
    _merge_importlib_bindings,
    _without_dynamic_bindings,
)
from ai_sdlc.core.lean_code_caller_target_semantics import (
    _function_importlib_bindings_from,
)
from ai_sdlc.core.lean_code_dynamic_refs import _enclosing_function
from ai_sdlc.core.lean_code_execution_identity import (
    _deferred_generator_anchors,
    _deferred_generator_paths,
    _deferred_lambda_paths,
    _nested_execution_paths,
)
from ai_sdlc.core.lean_code_execution_order import _in_deferred_generator_body


def _class_importlib_bindings(
    node: ast.AST,
    owner: ast.ClassDef,
    tree: ast.Module,
    module_bindings: _ImportlibBindings,
    local_bindings: dict[int, set[str]],
    parents: dict[ast.AST, ast.AST],
) -> _ImportlibBindings:
    outer = _enclosing_function(owner, parents)
    if outer is not None:
        inherited = _dynamic_import_bindings_at(
            owner,
            outer,
            tree,
            module_bindings,
            local_bindings,
            parents,
        )
    else:
        inherited = _importlib_bindings(
            _statements_before_node(tree.body, owner, parents),
            (set(), set(), True),
        )
    inherited = _global_importlib_bindings(owner, inherited, module_bindings)
    return _importlib_bindings(
        _statements_before_node(owner.body, node, parents),
        inherited,
    )

def _dynamic_import_binding_candidates_at(
    node: ast.AST,
    owner: _DynamicScope | None,
    tree: ast.Module,
    module_bindings: _ImportlibBindings,
    local_bindings: dict[int, set[str]],
    parents: dict[ast.AST, ast.AST],
    imported_callables: _ImportedCallables | None = None,
) -> tuple[_ImportlibBindings, ...]:
    if _in_deferred_generator_body(node, parents):
        return tuple(
            _identity_importlib_bindings(values, _importlib_bindings(path, candidate))
            for anchor, path, values in _deferred_generator_paths(
                node, parents, imported_callables
            )
            for candidate in _dynamic_import_binding_candidates_at(
                anchor,
                _enclosing_dynamic_scope(anchor, parents),
                tree,
                module_bindings,
                local_bindings,
                parents,
                imported_callables,
            )
        )
    if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return _function_importlib_binding_candidates(
            node,
            owner,
            tree,
            module_bindings,
            local_bindings,
            parents,
            imported_callables,
        )
    if isinstance(owner, ast.Lambda):
        return _lambda_importlib_binding_candidates(
            owner,
            tree,
            module_bindings,
            local_bindings,
            parents,
            imported_callables,
        )
    return (
        _dynamic_import_bindings_at(
            node, owner, tree, module_bindings, local_bindings, parents
        ),
    )

def _dynamic_import_bindings_at(
    node: ast.AST,
    owner: _DynamicScope | None,
    tree: ast.Module,
    module_bindings: _ImportlibBindings,
    local_bindings: dict[int, set[str]],
    parents: dict[ast.AST, ast.AST],
) -> _ImportlibBindings:
    if owner is None:
        if _in_deferred_generator_body(node, parents):
            anchors = _deferred_generator_anchors(node, parents)
            return _merge_importlib_bindings(
                tuple(
                    _importlib_bindings(
                        _statements_before_node(tree.body, anchor, parents),
                        (set(), set(), True),
                    )
                    for anchor in anchors
                )
            )
        return _importlib_bindings(
            _statements_before_node(tree.body, node, parents),
            (set(), set(), True),
        )
    if isinstance(owner, ast.Lambda):
        return _lambda_importlib_bindings(
            owner,
            tree,
            module_bindings,
            local_bindings,
            parents,
        )
    if isinstance(owner, ast.ClassDef):
        return _class_importlib_bindings(
            node,
            owner,
            tree,
            module_bindings,
            local_bindings,
            parents,
        )
    return _function_importlib_bindings(
        node,
        owner,
        tree,
        module_bindings,
        local_bindings,
        parents,
    )

def _function_importlib_binding_candidates(
    node: ast.AST,
    owner: _FunctionNode,
    tree: ast.Module,
    module_bindings: _ImportlibBindings,
    local_bindings: dict[int, set[str]],
    parents: dict[ast.AST, ast.AST],
    imported_callables: _ImportedCallables | None = None,
) -> tuple[_ImportlibBindings, ...]:
    inherited = _inherited_importlib_binding_candidates(
        owner,
        tree,
        module_bindings,
        local_bindings,
        parents,
        imported_callables,
    )
    return tuple(
        _function_importlib_bindings_from(
            node,
            owner,
            candidate,
            module_bindings,
            local_bindings,
            parents,
        )
        for candidate in inherited
    )

def _function_importlib_bindings(
    node: ast.AST,
    owner: _FunctionNode,
    tree: ast.Module,
    module_bindings: _ImportlibBindings,
    local_bindings: dict[int, set[str]],
    parents: dict[ast.AST, ast.AST],
) -> _ImportlibBindings:
    return _merge_importlib_bindings(
        _function_importlib_binding_candidates(
            node,
            owner,
            tree,
            module_bindings,
            local_bindings,
            parents,
        )
    )

def _inherited_importlib_binding_candidates(
    owner: _FunctionNode,
    tree: ast.Module,
    module_bindings: _ImportlibBindings,
    local_bindings: dict[int, set[str]],
    parents: dict[ast.AST, ast.AST],
    imported_callables: _ImportedCallables | None,
) -> tuple[_ImportlibBindings, ...]:
    outer = _enclosing_function(owner, parents)
    if outer is None:
        return (module_bindings,)
    return tuple(
        _identity_importlib_bindings(
            values,
            _function_importlib_bindings_from(
                anchor,
                outer,
                candidate,
                module_bindings,
                local_bindings,
                parents,
                path,
            ),
        )
        for anchor, path, values in _nested_execution_paths(
            owner, outer, parents, imported_callables
        )
        for candidate in _dynamic_import_binding_candidates_at(
            anchor,
            outer,
            tree,
            module_bindings,
            local_bindings,
            parents,
            imported_callables,
        )
    )

def _lambda_importlib_binding_candidates(
    owner: ast.Lambda,
    tree: ast.Module,
    module_bindings: _ImportlibBindings,
    local_bindings: dict[int, set[str]],
    parents: dict[ast.AST, ast.AST],
    imported_callables: _ImportedCallables | None = None,
) -> tuple[_ImportlibBindings, ...]:
    candidates = tuple(
        _identity_importlib_bindings(values, _importlib_bindings(path, candidate))
        for anchor, path, values in _deferred_lambda_paths(
            owner, parents, imported_callables
        )
        for candidate in _dynamic_import_binding_candidates_at(
            anchor,
            _enclosing_dynamic_scope(anchor, parents),
            tree,
            module_bindings,
            local_bindings,
            parents,
            imported_callables,
        )
    )
    return tuple(
        _without_dynamic_bindings(
            candidate,
            _cached_lambda_bindings(owner, local_bindings),
        )
        for candidate in candidates
    )

def _lambda_importlib_bindings(
    owner: ast.Lambda,
    tree: ast.Module,
    module_bindings: _ImportlibBindings,
    local_bindings: dict[int, set[str]],
    parents: dict[ast.AST, ast.AST],
) -> _ImportlibBindings:
    return _merge_importlib_bindings(
        _lambda_importlib_binding_candidates(
            owner,
            tree,
            module_bindings,
            local_bindings,
            parents,
        )
    )

__all__: list[str] = []
