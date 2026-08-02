"""Private source index responsibility for Lean caller analysis."""

from __future__ import annotations

import ast

from ai_sdlc.core.lean_code_call_resolution import (
    _enclosing_target_instances,
    _function_target_reference_locations,
)
from ai_sdlc.core.lean_code_caller_models import (
    _DynamicScope,
    _FunctionNode,
    _ImportlibBindings,
    _TargetExports,
)
from ai_sdlc.core.lean_code_dynamic_refs import _expression_location
from ai_sdlc.core.lean_code_identity_models import _IMPORTLIB_INTACT_KEY, _IdentityValue
from ai_sdlc.core.lean_code_imports import _module_names, _target_import_names
from ai_sdlc.core.lean_code_scope import (
    _enclosing_class,
    _local_bindings,
    _scope_declarations,
)


class _ImportlibAttributeMutationFinder(ast.NodeVisitor):
    def __init__(self, module_names: set[str]) -> None:
        self._module_names = module_names
        self.mutated = False

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (
            node.attr == "import_module"
            and isinstance(node.value, ast.Name)
            and node.value.id in self._module_names
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ):
            self.mutated = True
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is None:
            self.visit(node.annotation)
            return
        self.visit(node.value)
        self.visit(node.target)
        self.visit(node.annotation)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function_header(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_header(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def _visit_function_header(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        for argument in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ):
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if node.returns is not None:
            self.visit(node.returns)

def _cached_local_bindings(
    owner: _FunctionNode,
    cache: dict[int, set[str]],
) -> set[str]:
    existing = cache.get(id(owner))
    if existing is not None:
        return existing
    names = _local_bindings(owner)
    cache[id(owner)] = names
    return names

def _dynamic_import_call_shape(
    node: ast.Call,
    exported_names: frozenset[str],
) -> bool:
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr in exported_names
        and isinstance(node.func.value, ast.Call)
    )

def _global_importlib_bindings(
    owner: _FunctionNode | ast.ClassDef,
    inherited: _ImportlibBindings,
    module_bindings: _ImportlibBindings,
) -> _ImportlibBindings:
    global_names, _ = _scope_declarations(owner)
    modules = inherited[0] - global_names | module_bindings[0] & global_names
    functions = inherited[1] - global_names | module_bindings[1] & global_names
    relevant = global_names & {
        *inherited[0],
        *inherited[1],
        *module_bindings[0],
        *module_bindings[1],
    }
    intact = module_bindings[2] if relevant else inherited[2]
    return modules, functions, intact

def _identity_importlib_bindings(
    values: tuple[tuple[str, _IdentityValue], ...],
    fallback: _ImportlibBindings,
) -> _ImportlibBindings:
    modules, functions = set(fallback[0]), set(fallback[1])
    intact = fallback[2]
    for name, value in values:
        if name == _IMPORTLIB_INTACT_KEY:
            intact = value.truth if value.truth is not None else intact
            continue
        modules.discard(name)
        functions.discard(name)
        if value.kind == "importlib-module":
            modules.add(name)
        elif value.kind == "importlib-function":
            functions.add(name)
    return modules, functions, intact

def _without_dynamic_bindings(
    bindings: _ImportlibBindings,
    shadowed: set[str],
) -> _ImportlibBindings:
    return bindings[0] - shadowed, bindings[1] - shadowed, bindings[2]

def _merge_importlib_bindings(
    candidates: tuple[_ImportlibBindings, ...],
) -> _ImportlibBindings:
    return (
        set().union(*(item[0] for item in candidates)),
        set().union(*(item[1] for item in candidates)),
        any(item[2] for item in candidates),
    )

def _dynamic_import_callable(node: ast.expr) -> tuple[str, str]:
    if (
        isinstance(node, ast.Attribute)
        and node.attr == "import_module"
        and isinstance(node.value, ast.Name)
    ):
        return "module", node.value.id
    if isinstance(node, ast.Name):
        return "function", node.id
    return "", ""

def _apply_expression_importlib_bindings(
    bindings: _ImportlibBindings,
    events: tuple[tuple[str, str | None], ...],
) -> _ImportlibBindings:
    modules, functions = set(bindings[0]), set(bindings[1])
    for target, source in events:
        source_is_module = source in modules if source is not None else False
        source_is_function = source in functions if source is not None else False
        modules.discard(target)
        functions.discard(target)
        if source_is_module:
            modules.add(target)
        if source_is_function:
            functions.add(target)
    return modules, functions, bindings[2]

def _dynamic_location(node: ast.AST, owner: _DynamicScope | None) -> str:
    return _expression_location(node, owner)

def _dynamic_import_target_modules(
    target_exports: _TargetExports,
) -> tuple[dict[str, frozenset[str]], dict[str, set[str]]]:
    exports_by_module = {
        module: names
        for export_path, names in target_exports.items()
        for module in _module_names(export_path)
    }
    modules_by_export: dict[str, set[str]] = {}
    for module, names in exports_by_module.items():
        for name in names:
            modules_by_export.setdefault(name, set()).add(module)
    return exports_by_module, modules_by_export

def _target_reference_names(
    path: str,
    target_path: str,
    target_class: str,
    target_name: str,
    target_exports: _TargetExports,
    direct_names: set[str],
    scope_imports: dict[int, tuple[ast.Import | ast.ImportFrom, ...]],
) -> set[str]:
    names = {target_name, *(name for name in direct_names if "." not in name)}
    if not target_class:
        names.update(name for exported in target_exports.values() for name in exported)
    for import_nodes in scope_imports.values():
        for import_node in import_nodes:
            local_direct, _, _ = _target_import_names(
                [import_node],
                path,
                target_path,
                target_class,
                target_name,
                frozenset(target_exports),
                target_exports,
            )
            names.update(name for name in local_direct if "." not in name)
    return names

def _function_linked_reference_locations(
    path: str,
    parents: dict[ast.AST, ast.AST],
    functions: tuple[_FunctionNode, ...],
    scope_imports: dict[int, tuple[ast.Import | ast.ImportFrom, ...]],
    target_path: str,
    target_class: str,
    target_name: str,
    target_exports: _TargetExports,
    direct_names: set[str],
    module_names: set[str],
    class_names: set[str],
    class_instances: dict[str, set[str]],
) -> set[str]:
    evidence: set[str] = set()
    for node in functions:
        owner_class = _enclosing_class(node, parents)
        inherited_instances = _enclosing_target_instances(
            node, parents, class_names, target_class
        )
        inherited_instances.update(class_instances.get(owner_class, set()))
        local_imports = {
            id(import_node): _target_import_names(
                [import_node],
                path,
                target_path,
                target_class,
                target_name,
                frozenset(target_exports),
                target_exports,
            )
            for import_node in scope_imports[id(node)]
        }
        evidence.update(
            _function_target_reference_locations(
                node,
                direct_names,
                module_names,
                class_names,
                target_name,
                owner_class == target_class and bool(target_class),
                inherited_instances,
                local_imports,
                parents,
            )
        )
    return evidence

__all__: list[str] = []
