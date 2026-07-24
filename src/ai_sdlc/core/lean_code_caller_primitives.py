"""Private primitives responsibility for Lean caller analysis."""

from __future__ import annotations

import ast

from ai_sdlc.core.lean_code_caller_models import (
    _CallableNode,
    _CallableOrigins,
    _FunctionNode,
    _ImportedCallables,
    _SourceShape,
)
from ai_sdlc.core.lean_code_execution_order import _lineage_item_key
from ai_sdlc.core.lean_code_flow import ReferenceState
from ai_sdlc.core.lean_code_identity_callable_summary import _callable_execution_kind
from ai_sdlc.core.lean_code_identity_models import _IdentityValue, _import_binding_key
from ai_sdlc.core.lean_code_imports import _import_from_modules, _module_names
from ai_sdlc.core.lean_code_scope import _bound_names, _without_local_roots


def _static_identity_value(node: ast.expr) -> _IdentityValue:
    if isinstance(node, ast.Constant) and isinstance(
        node.value, (str, bytes, int, float, bool, type(None))
    ):
        return _IdentityValue(
            "literal",
            truth=bool(node.value),
            scalar=node.value,
            scalar_known=True,
        )
    if isinstance(node, (ast.Tuple, ast.List)):
        items = tuple(_static_identity_value(item) for item in node.elts)
        return _IdentityValue("container", items=items, truth=bool(items))
    if isinstance(node, ast.Dict):
        entries = tuple(
            (key.value, _static_identity_value(value))
            for key, value in zip(node.keys, node.values, strict=True)
            if isinstance(key, ast.Constant) and isinstance(key.value, (str, int))
        )
        return _IdentityValue("container", entries=entries, truth=bool(entries))
    return _IdentityValue()

def _module_environment(
    module_id: str,
    modules: dict[str, dict[str, _CallableNode]],
    globals_by_module: dict[str, dict[str, _IdentityValue]],
    origins: _CallableOrigins,
) -> dict[str, _IdentityValue]:
    environment = dict(globals_by_module.get(module_id, {}))
    definitions = next(
        (modules[module] for module in _module_names(module_id) if module in modules),
        {},
    )
    environment.update(
        {
            name: _IdentityValue(
                callable_node=node,
                target_key=origins.get(id(node)),
                execution_kind=_callable_execution_kind(node),
                module_id=module_id,
            )
            for name, node in definitions.items()
        }
    )
    return environment

def _available_imported_callables(
    path: str,
    node: ast.ImportFrom,
    modules: dict[str, dict[str, _CallableNode]],
) -> dict[str, _CallableNode]:
    return {
        name: value
        for module in _import_from_modules(path, node)
        for name, value in modules.get(module, {}).items()
    }

def _store_import_binding(
    resolved: _ImportedCallables,
    node: ast.Import | ast.ImportFrom,
    name: str,
    value: _IdentityValue,
) -> None:
    resolved[name] = value
    resolved[_import_binding_key(node, name)] = value

def _imported_module_binding(
    alias: ast.alias, value: _IdentityValue
) -> tuple[str, _IdentityValue]:
    if alias.asname:
        return alias.asname, value
    parts = alias.name.split(".")
    nested = value
    for index in range(len(parts) - 1, 0, -1):
        nested = _IdentityValue(
            "module",
            entries=((parts[index], nested),),
            truth=True,
            module_id=".".join(parts[:index]),
        )
    return parts[0], nested

def _merge_module_values(left: _IdentityValue, right: _IdentityValue) -> _IdentityValue:
    entries = dict(left.entries)
    for name, value in right.entries:
        existing = entries.get(name)
        entries[name] = (
            _merge_module_values(existing, value)
            if existing is not None and existing.kind == value.kind == "module"
            else value
        )
    return _IdentityValue(
        "module",
        entries=tuple(sorted(entries.items())),
        truth=True,
        module_id=left.module_id or right.module_id,
    )

def _callable_modules(
    exports: dict[str, dict[str, _CallableNode]],
) -> dict[str, dict[str, _CallableNode]]:
    return {
        module: definitions
        for path, definitions in exports.items()
        for module in _module_names(path)
    }

def _callable_export_dependents(
    sources: dict[str, tuple[ast.Module, _SourceShape]],
) -> dict[str, set[str]]:
    dependents: dict[str, set[str]] = {}
    for path, (tree, _) in sources.items():
        for node in tree.body:
            if not isinstance(node, ast.ImportFrom):
                continue
            for module in _import_from_modules(path, node):
                dependents.setdefault(module, set()).add(path)
    return dependents

def _candidate_reexport_paths(
    candidates: dict[tuple[str, str], set[str]],
    source_path: str,
    names: set[str],
) -> set[str]:
    paths: set[str] = set()
    for module in _module_names(source_path):
        paths.update(candidates.get((module, "*"), ()))
        for name in names:
            paths.update(candidates.get((module, name), ()))
    return paths

def _discard_module_names(state: ReferenceState, names: set[str]) -> ReferenceState:
    return (
        state[0] - names,
        _without_local_roots(state[1], names),
        _without_local_roots(state[2], names),
        set(),
    )

def _module_bound_names(node: ast.stmt) -> set[str]:
    if isinstance(node, ast.Import):
        return {alias.asname or alias.name.split(".", 1)[0] for alias in node.names}
    if isinstance(node, ast.ImportFrom):
        return {alias.asname or alias.name for alias in node.names if alias.name != "*"}
    if isinstance(node, ast.Assign):
        return {name for target in node.targets for name in _bound_names(target)}
    if isinstance(node, ast.AnnAssign):
        return _bound_names(node.target) if node.value is not None else set()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, ast.Delete):
        return set().union(*(_bound_names(target) for target in node.targets))
    return set()

def _add_target_definition(
    node: ast.stmt,
    direct: set[str],
    classes: set[str],
    target_class: str,
    target_name: str,
) -> None:
    if target_class and isinstance(node, ast.ClassDef) and node.name == target_class:
        classes.add(node.name)
    if (
        not target_class
        and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == target_name
    ):
        direct.add(node.name)

def _class_base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _class_base_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Subscript):
        return _class_base_name(node.value)
    return ""

def _star_lineage_key(target_name: str) -> str:
    return f"\0star:{target_name}"

def _star_value_active(node: ast.expr, states: dict[str, bool]) -> bool:
    if isinstance(node, ast.Name):
        return states.get(node.id, False)
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, int)
    ):
        return states.get(_lineage_item_key(node.value.id, node.slice.value), False)
    return False

def _literal_truth(node: ast.AST) -> bool | None:
    if not isinstance(node, ast.Constant):
        return None
    try:
        return bool(node.value)
    except (TypeError, ValueError):
        return None

class _OuterDeclarationFinder(ast.NodeVisitor):
    def __init__(
        self,
        declaration_type: type[ast.Global] | type[ast.Nonlocal],
    ) -> None:
        self._declaration_type = declaration_type
        self.names: set[str] = set()

    def visit_Global(self, node: ast.Global) -> None:
        if self._declaration_type is ast.Global:
            self.names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        if self._declaration_type is ast.Nonlocal:
            self.names.update(node.names)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

def _enclosing_class_node(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> ast.ClassDef | None:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.ClassDef):
            return current
        current = parents.get(current)
    return None

def _enclosing_function_or_lambda(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> _FunctionNode | ast.Lambda | None:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return current
        current = parents.get(current)
    return None

def _is_ancestor(
    ancestor: ast.AST,
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    current: ast.AST | None = node
    while current is not None:
        if current is ancestor:
            return True
        current = parents.get(current)
    return False

def _assignment_value_targets(
    statement: ast.stmt,
) -> tuple[ast.expr | None, tuple[ast.expr, ...]]:
    if isinstance(statement, ast.Assign):
        return statement.value, tuple(statement.targets)
    if isinstance(statement, ast.AnnAssign) and statement.value is not None:
        return statement.value, (statement.target,)
    return None, ()

__all__: list[str] = []
