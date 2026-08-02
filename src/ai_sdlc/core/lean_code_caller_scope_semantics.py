"""Private scope semantics responsibility for Lean caller analysis."""

from __future__ import annotations

import ast
from collections.abc import Sequence

from ai_sdlc.core.lean_code_caller_callable_index import (
    _captured_importlib_functions,
    _captured_importlib_modules,
    _declared_outer_effect_names,
    _importlib_attribute_assignment,
    _mutates_importlib_attribute,
    _star_assignment_states,
    _statements_before_node,
)
from ai_sdlc.core.lean_code_caller_evidence import (
    _module_callable_value,
    _resolve_import_from_callables,
)
from ai_sdlc.core.lean_code_caller_models import (
    _CallableNode,
    _CallableOrigins,
    _DynamicScope,
    _ImportedCallables,
    _ImportlibBindings,
    _SourceShape,
    _TargetExports,
)
from ai_sdlc.core.lean_code_caller_module_semantics import (
    _cached_lambda_bindings,
    _module_effect_bound_names,
    _resolve_module_statements,
    _scope_owns_runtime_node,
)
from ai_sdlc.core.lean_code_caller_primitives import (
    _imported_module_binding,
    _literal_truth,
    _merge_module_values,
    _star_lineage_key,
    _store_import_binding,
)
from ai_sdlc.core.lean_code_caller_source_index import (
    _cached_local_bindings,
)
from ai_sdlc.core.lean_code_identity_models import _IdentityValue
from ai_sdlc.core.lean_code_imports import _import_from_modules
from ai_sdlc.core.lean_code_scope import _scope_declarations


def _resolve_imported_modules(
    node: ast.Import,
    modules: dict[str, dict[str, _CallableNode]],
    globals_by_module: dict[str, dict[str, _IdentityValue]],
    origins: _CallableOrigins,
    resolved: _ImportedCallables,
) -> None:
    for alias in node.names:
        available = modules.get(alias.name)
        if available is None:
            continue
        value = _module_callable_value(
            alias.name, available, modules, globals_by_module, origins
        )
        name, value = _imported_module_binding(alias, value)
        existing = resolved.get(name)
        if existing is not None and existing.kind == value.kind == "module":
            value = _merge_module_values(existing, value)
        _store_import_binding(resolved, node, name, value)

def _target_module_names(
    nodes: Sequence[ast.stmt],
    caller_path: str,
    target_path: str,
    target_class: str,
    target_name: str,
    target_paths: frozenset[str] | None = None,
    target_exports: _TargetExports | None = None,
) -> tuple[set[str], set[str], set[str]]:
    """解析模块最终绑定；函数嵌套作用域由独立 finder 负责。"""
    state = _resolve_module_statements(
        nodes,
        (
            caller_path,
            target_path,
            target_class,
            target_name,
            target_exports or {target_path: frozenset((target_class or target_name,))},
        ),
        (set(), set(), set(), set()),
    )
    return state[0], state[1], state[2]

def _star_binding_states(
    statements: Sequence[ast.stmt],
    caller_path: str,
    target_name: str,
    target_modules: set[str],
    initial: dict[str, bool],
) -> dict[str, bool]:
    states = dict(initial)
    for statement in statements:
        if isinstance(statement, ast.If):
            states = _star_if_states(
                statement, caller_path, target_name, target_modules, states
            )
            continue
        captured = _star_assignment_states(statement, states)
        for name in _module_effect_bound_names(statement):
            states[name] = captured.get(name, False)
        states.update(
            {
                name: active
                for name, active in captured.items()
                if name.startswith("\0item:")
            }
        )
        if isinstance(statement, ast.ImportFrom):
            modules = _import_from_modules(caller_path, statement)
            if modules & target_modules:
                for alias in statement.names:
                    if alias.name == "*":
                        states[target_name] = True
                        states[_star_lineage_key(target_name)] = True
                    elif alias.name == target_name and states.get(
                        _star_lineage_key(target_name), False
                    ):
                        states[alias.asname or alias.name] = True
    return states

def _star_if_states(
    statement: ast.If,
    caller_path: str,
    target_name: str,
    target_modules: set[str],
    initial: dict[str, bool],
) -> dict[str, bool]:
    truth = _literal_truth(statement.test)
    if truth is not None:
        branch = statement.body if truth else statement.orelse
        return _star_binding_states(
            branch, caller_path, target_name, target_modules, initial
        )
    body = _star_binding_states(
        statement.body, caller_path, target_name, target_modules, initial
    )
    other = _star_binding_states(
        statement.orelse, caller_path, target_name, target_modules, initial
    )
    return {
        name: body.get(name, False) and other.get(name, False)
        for name in {*body, *other}
    }

def _global_effects_before(
    owner: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> set[str]:
    prior = _statements_before_node(owner.body, node, parents)
    globals_, _ = _scope_declarations(owner)
    direct = set().union(*(_module_effect_bound_names(item) for item in prior))
    return direct & globals_ | _declared_outer_effect_names(prior, ast.Global)

def _importlib_attribute_state_after(
    statement: ast.stmt,
    module_names: set[str],
    function_names: set[str],
    current: bool,
) -> bool:
    if isinstance(statement, ast.ClassDef):
        return _importlib_bindings(
            statement.body,
            (set(module_names), set(function_names), current),
        )[2]
    assigned = _importlib_attribute_assignment(statement, module_names)
    if assigned is not None:
        return isinstance(assigned, ast.Name) and assigned.id in function_names
    if _mutates_importlib_attribute(statement, module_names):
        return False
    return current

def _importlib_bindings(
    statements: Sequence[ast.stmt],
    initial: _ImportlibBindings,
) -> _ImportlibBindings:
    modules, functions = set(initial[0]), set(initial[1])
    attribute_intact = initial[2]
    for statement in statements:
        captured_modules = _captured_importlib_modules(statement, modules)
        captured_functions = _captured_importlib_functions(
            statement,
            modules,
            functions,
            attribute_intact,
        )
        attribute_intact = _importlib_attribute_state_after(
            statement,
            modules,
            functions,
            attribute_intact,
        )
        bound = _module_effect_bound_names(statement)
        modules.difference_update(bound)
        functions.difference_update(bound)
        modules.update(captured_modules)
        functions.update(captured_functions)
        if isinstance(statement, ast.Import):
            modules.update(
                alias.asname or alias.name
                for alias in statement.names
                if alias.name == "importlib"
            )
        if (
            isinstance(statement, ast.ImportFrom)
            and statement.module == "importlib"
            and attribute_intact
        ):
            functions.update(
                alias.asname or alias.name
                for alias in statement.names
                if alias.name == "import_module"
            )
    return modules, functions, attribute_intact

def _outer_scope_shadows(
    owner: _DynamicScope,
    target_name: str,
    parents: dict[ast.AST, ast.AST],
    local_bindings: dict[int, set[str]],
) -> bool:
    current = parents.get(owner)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            globals_, _ = _scope_declarations(current)
            if target_name in globals_:
                return False
            if target_name in _cached_local_bindings(current, local_bindings):
                return True
        elif isinstance(current, ast.Lambda):
            if target_name in _cached_lambda_bindings(current, local_bindings):
                return True
        current = parents.get(current)
    return False

def _enclosing_dynamic_scope(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> _DynamicScope | None:
    current = parents.get(node)
    while current is not None:
        if isinstance(
            current,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef),
        ) and _scope_owns_runtime_node(current, node, parents):
            return current
        current = parents.get(current)
    return None

def _resolve_imported_callables(
    path: str,
    tree: ast.Module,
    modules: dict[str, dict[str, _CallableNode]],
    globals_by_module: dict[str, dict[str, _IdentityValue]],
    origins: _CallableOrigins,
) -> _ImportedCallables:
    resolved: _ImportedCallables = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            _resolve_import_from_callables(
                path, node, modules, globals_by_module, origins, resolved
            )
        elif isinstance(node, ast.Import):
            _resolve_imported_modules(
                node, modules, globals_by_module, origins, resolved
            )
    return resolved

def _resolved_reexport_aliases(
    sources: dict[str, tuple[ast.Module, _SourceShape]],
    exports: dict[str, set[str]],
    path: str,
    target_path: str,
    target_class: str,
    target_name: str,
) -> set[str]:
    frozen = {source: frozenset(names) for source, names in exports.items()}
    tree, _ = sources[path]
    direct, _, classes = _target_module_names(
        tree.body,
        path,
        target_path,
        target_class,
        target_name,
        frozenset(frozen),
        frozen,
    )
    observed = classes if target_class else direct
    return {name for name in observed if "." not in name}

__all__: list[str] = []
