"""Private evidence responsibility for Lean caller analysis."""

from __future__ import annotations

import ast

from ai_sdlc.core.lean_code_call_resolution import (
    _enclosing_target_instances,
    _function_calls_target,
)
from ai_sdlc.core.lean_code_caller_models import (
    _CallableNode,
    _CallableOrigins,
    _FunctionNode,
    _ImportedCallables,
    _ImportlibBindings,
    _ModuleTarget,
    _SourceEvidenceIndex,
    _SourceShape,
    _TargetCallContext,
    _TargetExports,
)
from ai_sdlc.core.lean_code_caller_module_state import (
    _callable_value,
    _import_alias_targets,
    _imported_expression_value,
    _is_target_definition,
    _source_shape,
    _static_module_assignments,
)
from ai_sdlc.core.lean_code_caller_primitives import (
    _available_imported_callables,
    _store_import_binding,
)
from ai_sdlc.core.lean_code_caller_source_index import (
    _dynamic_import_callable,
    _function_linked_reference_locations,
    _target_reference_names,
)
from ai_sdlc.core.lean_code_classification import classify_file
from ai_sdlc.core.lean_code_dynamic_refs import (
    _enclosing_function,
    _expression_location,
    _referenced_symbol_names,
)
from ai_sdlc.core.lean_code_identity_models import _IdentityValue
from ai_sdlc.core.lean_code_imports import _module_names, _target_import_names
from ai_sdlc.core.lean_code_models import FileClassification
from ai_sdlc.core.lean_code_scope import _enclosing_class


def _evidence_caller_key(evidence: str) -> str:
    owner, owner_line, *_ = evidence.rsplit(":", 5)
    return f"{owner}:{owner_line}"


def _exact_dynamic_import_call(
    node: ast.AST,
    exports_by_module: dict[str, frozenset[str]],
    bindings: _ImportlibBindings,
) -> bool:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    receiver = node.func.value
    if not isinstance(receiver, ast.Call):
        return False
    if not receiver.args:
        return False
    module_name = receiver.args[0]
    function_kind, import_name = _dynamic_import_callable(receiver.func)
    return (
        (
            function_kind == "module"
            and import_name in bindings[0]
            and bindings[2]
            or function_kind == "function"
            and import_name in bindings[1]
        )
        and isinstance(module_name, ast.Constant)
        and isinstance(module_name.value, str)
        and node.func.attr in exports_by_module.get(module_name.value, frozenset())
    )


def _context_reference_names(
    context: _TargetCallContext,
    scope_imports: dict[int, tuple[ast.Import | ast.ImportFrom, ...]],
) -> set[str]:
    target, direct_names, _, _ = context
    path, target_path, target_class, target_name, target_exports = target
    return _target_reference_names(
        path,
        target_path,
        target_class,
        target_name,
        target_exports,
        direct_names,
        scope_imports,
    )


def _target_linked_dynamic_evidence(
    path: str,
    tree: ast.Module,
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
    exact_dynamic_locations: set[str],
    source_evidence: _SourceEvidenceIndex,
    imported_callables: _ImportedCallables,
) -> set[str]:
    evidence: set[str] = set()
    evidence.update(
        _function_linked_reference_locations(
            path,
            parents,
            functions,
            scope_imports,
            target_path,
            target_class,
            target_name,
            target_exports,
            direct_names,
            module_names,
            class_names,
            class_instances,
        )
    )
    evidence.update(exact_dynamic_locations)
    evidence.difference_update(
        _modeled_target_reference_locations(
            source_evidence.nodes,
            parents,
            imported_callables,
            (
                target_path,
                f"{target_class}.{target_name}" if target_class else target_name,
            ),
        )
    )
    return {f"{path}:{location}" for location in evidence}


def _modeled_target_reference_locations(
    nodes: tuple[ast.AST, ...],
    parents: dict[ast.AST, ast.AST],
    environment: _ImportedCallables,
    target: tuple[str, str],
) -> set[str]:
    return {
        _expression_location(node, _enclosing_function(node, parents), parents)
        for node in nodes
        if isinstance(node, (ast.Name, ast.Attribute))
        and _imported_expression_value(node, environment).target_key == target
        and _reference_is_modeled_argument(node, parents, environment)
    }


def _collect_function_callers(
    parents: dict[ast.AST, ast.AST],
    functions: tuple[_FunctionNode, ...],
    scope_imports: dict[int, tuple[ast.Import | ast.ImportFrom, ...]],
    target: _ModuleTarget,
    direct_names: set[str],
    module_names: set[str],
    class_names: set[str],
    class_instances: dict[str, set[str]],
    callers: set[str],
) -> None:
    path, target_path, target_class, target_name, target_exports = target
    # 嵌套函数作为独立 caller 评估，避免把同一次调用重复计入外层函数。
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
        if _function_calls_target(
            node,
            direct_names,
            module_names,
            class_names,
            target_name,
            owner_class == target_class and bool(target_class),
            inherited_instances,
            local_imports,
        ) and not _is_target_definition(
            path, target_path, node.name, target_name, owner_class, target_class
        ):
            callers.add(f"{path}:{node.name}:{node.lineno}")


def _reference_is_modeled_argument(
    node: ast.Name | ast.Attribute,
    parents: dict[ast.AST, ast.AST],
    environment: _ImportedCallables,
) -> bool:
    current: ast.AST = node
    while (parent := parents.get(current)) is not None:
        if isinstance(parent, ast.Call) and current is not parent.func:
            return (
                _imported_expression_value(parent.func, environment).callable_node
                is not None
            )
        if isinstance(parent, (ast.stmt, ast.Lambda)):
            return False
        current = parent
    return False


def _relevant_source_targets(
    path: str,
    tree: ast.Module,
    shape: _SourceShape,
    targets_by_name: dict[str, list[tuple[str, str]]],
    target_export_index: dict[tuple[str, str], set[tuple[str, str]]],
) -> set[tuple[str, str]]:
    referenced_names = _referenced_symbol_names(tree, shape[3])
    relevant = {
        target for name in referenced_names for target in targets_by_name.get(name, ())
    }
    relevant.update(
        _import_alias_targets(
            path,
            tree,
            referenced_names,
            target_export_index,
        )
    )
    return relevant


def _parsed_product_sources(
    sources: dict[str, bytes],
) -> dict[str, tuple[ast.Module, _SourceShape]]:
    parsed: dict[str, tuple[ast.Module, _SourceShape]] = {}
    for path, payload in sources.items():
        if (
            classify_file(path, payload, False)
            != FileClassification.HANDWRITTEN_PRODUCT
        ):
            continue
        try:
            tree = ast.parse(payload.decode("utf-8", errors="strict"), filename=path)
        except (SyntaxError, UnicodeDecodeError):
            continue
        parsed[path] = tree, _source_shape(tree)
    return parsed


def _module_global_values(
    sources: dict[str, tuple[ast.Module, _SourceShape]],
) -> dict[str, dict[str, _IdentityValue]]:
    values: dict[str, dict[str, _IdentityValue]] = {}
    for path, (tree, _) in sources.items():
        module_values = _static_module_assignments(tree)
        values[path] = module_values
        for module in _module_names(path):
            values[module] = module_values
    return values


def _resolve_import_from_callables(
    path: str,
    node: ast.ImportFrom,
    modules: dict[str, dict[str, _CallableNode]],
    globals_by_module: dict[str, dict[str, _IdentityValue]],
    origins: _CallableOrigins,
    resolved: _ImportedCallables,
) -> None:
    available = _available_imported_callables(path, node, modules)
    for alias in node.names:
        if alias.name == "*":
            for name, value in available.items():
                if not name.startswith("_"):
                    _store_import_binding(
                        resolved,
                        node,
                        name,
                        _callable_value(value, modules, globals_by_module, origins),
                    )
        elif alias.name in available:
            name = alias.asname or alias.name
            _store_import_binding(
                resolved,
                node,
                name,
                _callable_value(
                    available[alias.name], modules, globals_by_module, origins
                ),
            )


def _module_callable_value(
    module_name: str,
    environment: dict[str, _CallableNode],
    modules: dict[str, dict[str, _CallableNode]],
    globals_by_module: dict[str, dict[str, _IdentityValue]],
    origins: _CallableOrigins,
) -> _IdentityValue:
    globals_for_module = globals_by_module.get(module_name, {})
    entries = tuple(
        sorted(
            (
                name,
                _callable_value(node, modules, globals_by_module, origins),
            )
            for name, node in environment.items()
        )
        + sorted(globals_for_module.items())
    )
    return _IdentityValue("module", entries=entries, truth=True, module_id=module_name)


__all__: list[str] = []
