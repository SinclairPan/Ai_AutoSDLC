"""Private module semantics responsibility for Lean caller analysis."""

from __future__ import annotations

import ast
from collections import deque
from collections.abc import Sequence

from ai_sdlc.core.lean_code_caller_callable_index import (
    _comprehension_shadows,
    _in_comprehension_body,
    _ModuleEffectBindingFinder,
)
from ai_sdlc.core.lean_code_caller_evidence import (
    _collect_function_callers,
    _exact_dynamic_import_call,
    _reference_is_modeled_argument,
    _target_linked_dynamic_evidence,
)
from ai_sdlc.core.lean_code_caller_models import (
    _CallableNode,
    _DynamicScope,
    _ExceptionStateStacks,
    _ImportedCallables,
    _ImportlibBindings,
    _ModuleTarget,
    _SourceEvidenceIndex,
    _SourceShape,
    _TargetCallContext,
)
from ai_sdlc.core.lean_code_caller_module_state import (
    _annotation_class_name,
    _apply_module_statement,
    _imported_expression_value,
    _module_reexported_callables,
)
from ai_sdlc.core.lean_code_caller_primitives import (
    _callable_export_dependents,
    _callable_modules,
    _discard_module_names,
    _is_ancestor,
)
from ai_sdlc.core.lean_code_caller_source_index import (
    _apply_expression_importlib_bindings,
    _without_dynamic_bindings,
)
from ai_sdlc.core.lean_code_flow import (
    ReferenceState,
    _constructs_target,
    _merge_reference_states,
    _pattern_bound_names,
    _rebind_named_expressions,
)
from ai_sdlc.core.lean_code_imports import _module_names
from ai_sdlc.core.lean_code_scope import _bound_names


def _expand_callable_exports(
    sources: dict[str, tuple[ast.Module, _SourceShape]],
    exports: dict[str, dict[str, _CallableNode]],
) -> dict[str, dict[str, _CallableNode]]:
    modules = _callable_modules(exports)
    dependents = _callable_export_dependents(sources)
    pending = deque(sources)
    queued = set(sources)
    while pending:
        path = pending.popleft()
        queued.discard(path)
        tree, _ = sources[path]
        additions = _module_reexported_callables(path, tree, modules)
        before = len(exports[path])
        exports[path].update(additions)
        if len(exports[path]) == before:
            continue
        for module in _module_names(path):
            for dependent in dependents.get(module, ()):
                if dependent not in queued:
                    pending.append(dependent)
                    queued.add(dependent)
    return modules


def _resolve_module_statement(
    node: ast.stmt,
    target: _ModuleTarget,
    state: ReferenceState,
    exception_stacks: _ExceptionStateStacks,
) -> ReferenceState:
    if isinstance(node, ast.If):
        state = _rebind_named_expressions(node.test, state)
        return _merge_reference_states(
            _resolve_module_statements(node.body, target, state, exception_stacks),
            _resolve_module_statements(node.orelse, target, state, exception_stacks),
        )
    if isinstance(node, (ast.Try, ast.TryStar)):
        return _resolve_module_try(node, target, state, exception_stacks)
    if isinstance(node, ast.Match):
        cases = [
            _resolve_module_statements(
                case.body,
                target,
                _discard_module_names(state, _pattern_bound_names(case.pattern)),
                exception_stacks,
            )
            for case in node.cases
        ]
        return _merge_reference_states(state, *cases)
    if isinstance(node, (ast.For, ast.AsyncFor)):
        iteration = _discard_module_names(state, _bound_names(node.target))
        return _merge_reference_states(
            _resolve_module_statements(node.orelse, target, state, exception_stacks),
            _resolve_module_statements(
                [*node.body, *node.orelse], target, iteration, exception_stacks
            ),
        )
    if isinstance(node, ast.While):
        state = _rebind_named_expressions(node.test, state)
        return _merge_reference_states(
            _resolve_module_statements(node.orelse, target, state, exception_stacks),
            _resolve_module_statements(
                [*node.body, *node.orelse], target, state, exception_stacks
            ),
        )
    if isinstance(node, (ast.With, ast.AsyncWith)):
        for item in node.items:
            state = _rebind_named_expressions(item.context_expr, state)
            state = _discard_module_names(state, _bound_names(item.optional_vars))
        return _resolve_module_statements(node.body, target, state, exception_stacks)
    return _apply_module_statement(node, target, state)


def _resolve_module_statements(
    nodes: Sequence[ast.stmt],
    target: _ModuleTarget,
    state: ReferenceState,
    exception_stacks: _ExceptionStateStacks = (),
) -> ReferenceState:
    for node in nodes:
        for states in exception_stacks:
            states.append(state)
        state = _resolve_module_statement(node, target, state, exception_stacks)
    return state


def _resolve_module_try(
    node: ast.Try | ast.TryStar,
    target: _ModuleTarget,
    state: ReferenceState,
    exception_stacks: _ExceptionStateStacks,
) -> ReferenceState:
    prefixes = [state]
    current = _resolve_module_statements(
        node.body, target, state, (*exception_stacks, prefixes)
    )
    success = _resolve_module_statements(node.orelse, target, current, exception_stacks)
    handler_entry = _merge_reference_states(*prefixes)
    handlers = []
    for handler in node.handlers:
        entry = (
            _discard_module_names(handler_entry, {handler.name})
            if handler.name
            else handler_entry
        )
        handlers.append(
            _resolve_module_statements(handler.body, target, entry, exception_stacks)
        )
    merged = _merge_reference_states(success, *handlers)
    return _resolve_module_statements(node.finalbody, target, merged, exception_stacks)


def _tree_binds_types(
    tree: ast.Module,
    concrete_names: set[str],
    protocol_names: set[str],
) -> bool:
    if not concrete_names or not protocol_names:
        return False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AnnAssign)
            and node.value is not None
            and _annotation_class_name(node.annotation) in protocol_names
            and _constructs_target(node.value, concrete_names)
        ):
            return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _annotation_class_name(node.returns) not in protocol_names:
                continue
            if any(
                isinstance(child, ast.Return)
                and _constructs_target(child.value, concrete_names)
                for child in ast.walk(node)
            ):
                return True
    return False


def _module_effect_bound_names(node: ast.stmt) -> set[str]:
    finder = _ModuleEffectBindingFinder()
    finder.visit(node)
    return finder.names


def _cached_lambda_bindings(
    owner: ast.Lambda,
    cache: dict[int, set[str]],
) -> set[str]:
    existing = cache.get(id(owner))
    if existing is not None:
        return existing
    names = {
        argument.arg
        for argument in (
            *owner.args.posonlyargs,
            *owner.args.args,
            *owner.args.kwonlyargs,
            owner.args.vararg,
            owner.args.kwarg,
        )
        if argument is not None
    }
    finder = _ModuleEffectBindingFinder()
    finder.visit(owner.body)
    names.update(finder.names)
    cache[id(owner)] = names
    return names


def _scope_owns_runtime_node(
    scope: _DynamicScope,
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    if isinstance(scope, ast.Lambda):
        return _is_ancestor(scope.body, node, parents)
    if isinstance(scope, ast.ClassDef) and _in_comprehension_body(node, parents):
        return False
    return any(_is_ancestor(statement, node, parents) for statement in scope.body)


def _candidate_import_call_is_linked(
    node: ast.Call,
    exports_by_module: dict[str, frozenset[str]],
    candidates: tuple[_ImportlibBindings, ...],
    events: tuple[tuple[str, str | None], ...],
    parents: dict[ast.AST, ast.AST],
) -> bool:
    return any(
        _exact_dynamic_import_call(
            node,
            exports_by_module,
            _without_dynamic_bindings(
                _apply_expression_importlib_bindings(bindings, events),
                {
                    name
                    for name in (*bindings[0], *bindings[1])
                    if _comprehension_shadows(node, name, parents)
                },
            ),
        )
        for bindings in candidates
    )


def _target_linked_evidence(
    tree: ast.Module,
    shape: _SourceShape,
    context: _TargetCallContext,
    class_instances: dict[str, set[str]],
    exact_dynamic_locations: set[str],
    source_evidence: _SourceEvidenceIndex,
    imported_callables: _ImportedCallables,
) -> set[str]:
    module_target, direct_names, module_names, class_names = context
    path, target_path, target_class, target_name, target_exports = module_target
    parents, functions, scope_imports, _, _ = shape
    linked_evidence = _target_linked_dynamic_evidence(
        path,
        tree,
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
        exact_dynamic_locations,
        source_evidence,
        imported_callables,
    )
    return linked_evidence


def _collect_static_target_callers(
    shape: _SourceShape,
    context: _TargetCallContext,
    class_instances: dict[str, set[str]],
    callers: set[str],
) -> None:
    parents, functions, scope_imports, _, _ = shape
    module_target, direct_names, module_names, class_names = context
    _collect_function_callers(
        parents,
        functions,
        scope_imports,
        module_target,
        direct_names,
        module_names,
        class_names,
        class_instances,
        callers,
    )


def _modeled_only_target_references(
    nodes: Sequence[ast.AST],
    parents: dict[ast.AST, ast.AST],
    environment: _ImportedCallables,
) -> set[tuple[str, str]]:
    usage: dict[tuple[str, str], list[bool]] = {}
    for node in nodes:
        if not isinstance(node, (ast.Name, ast.Attribute)):
            continue
        if not isinstance(getattr(node, "ctx", None), ast.Load):
            continue
        target = _imported_expression_value(node, environment).target_key
        if target is None:
            continue
        usage.setdefault(target, []).append(
            _reference_is_modeled_argument(node, parents, environment)
        )
    return {target for target, references in usage.items() if all(references)}


__all__: list[str] = []
