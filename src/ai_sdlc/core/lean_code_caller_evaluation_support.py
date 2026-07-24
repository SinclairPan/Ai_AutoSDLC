"""Private evaluation support responsibility for Lean caller analysis."""

from __future__ import annotations

import ast
from collections.abc import Sequence

from ai_sdlc.core.lean_code_binding_state import _deferred_annotation_call_ids
from ai_sdlc.core.lean_code_caller_dynamic_resolution import (
    _dynamic_call_is_linked,
)
from ai_sdlc.core.lean_code_caller_evidence import (
    _context_reference_names,
    _evidence_caller_key,
    _relevant_source_targets,
)
from ai_sdlc.core.lean_code_caller_execution import (
    _target_execution_index,
    _target_reference_index,
)
from ai_sdlc.core.lean_code_caller_models import (
    _ExecutionState,
    _ImportedCallables,
    _SourceEvidenceIndex,
    _SourceShape,
    _TargetCallContext,
    _TargetEvidence,
    _TargetExports,
)
from ai_sdlc.core.lean_code_caller_module_semantics import (
    _collect_static_target_callers,
    _modeled_only_target_references,
    _target_linked_evidence,
)
from ai_sdlc.core.lean_code_caller_module_state import (
    _exposed_import_target_keys,
    _source_evidence_index,
)
from ai_sdlc.core.lean_code_caller_scope_semantics import (
    _enclosing_dynamic_scope,
    _importlib_bindings,
)
from ai_sdlc.core.lean_code_caller_source_index import (
    _dynamic_import_target_modules,
    _dynamic_location,
)
from ai_sdlc.core.lean_code_caller_target_semantics import (
    _star_import_active,
    _target_call_context,
)
from ai_sdlc.core.lean_code_dynamic_refs import (
    _unresolved_member_reference_locations,
)
from ai_sdlc.core.lean_code_flow import _class_target_instances


def _exact_dynamic_import_locations(
    path: str,
    tree: ast.Module,
    parents: dict[ast.AST, ast.AST],
    target_exports: _TargetExports,
    deferred_annotation_calls: set[int] | None = None,
    imported_callables: _ImportedCallables | None = None,
    call_nodes: Sequence[ast.Call] | None = None,
) -> set[str]:
    exports_by_module, modules_by_export = _dynamic_import_target_modules(
        target_exports
    )
    locations: set[str] = set()
    module_bindings = _importlib_bindings(tree.body, (set(), set(), True))
    local_bindings: dict[int, set[str]] = {}
    annotation_calls = (
        deferred_annotation_calls
        if deferred_annotation_calls is not None
        else _deferred_annotation_call_ids(tree)
    )
    module_star_bindings = {
        name: _star_import_active(tree.body, path, name, modules)
        for name, modules in modules_by_export.items()
    }
    calls = (
        call_nodes
        if call_nodes is not None
        else tuple(node for node in ast.walk(tree) if isinstance(node, ast.Call))
    )
    for node in calls:
        if id(node) in annotation_calls:
            continue
        owner = _enclosing_dynamic_scope(node, parents)
        if not _dynamic_call_is_linked(
            node,
            owner,
            tree,
            path,
            exports_by_module,
            modules_by_export,
            module_bindings,
            module_star_bindings,
            local_bindings,
            parents,
            imported_callables or {},
        ):
            continue
        locations.add(_dynamic_location(node, owner))
    return locations


def _dynamic_target_evidence(
    path: str,
    tree: ast.Module,
    parents: dict[ast.AST, ast.AST],
    dynamic_names: dict[str, set[str]],
    target_class: str,
    target_name: str,
    target_exports: _TargetExports,
    reference_names: set[str],
    class_instances: dict[str, set[str]],
    deferred_annotation_calls: set[int],
    imported_callables: _ImportedCallables,
    source_evidence: _SourceEvidenceIndex,
) -> tuple[set[str], set[str]]:
    locations = {
        location
        for name in reference_names
        for location in dynamic_names.get(name, set())
    }
    locations.update(
        _unresolved_member_reference_locations(
            tree, parents, target_name, class_instances, source_evidence.calls
        )
    )
    dynamic_exports = (
        target_exports
        if not target_class
        else {export_path: frozenset((target_name,)) for export_path in target_exports}
    )
    exact_locations = _exact_dynamic_import_locations(
        path,
        tree,
        parents,
        dynamic_exports,
        deferred_annotation_calls,
        imported_callables,
        source_evidence.calls,
    )
    locations.update(exact_locations)
    return {f"{path}:{location}" for location in locations}, exact_locations


def _collect_target_evidence(
    tree: ast.Module,
    shape: _SourceShape,
    context: _TargetCallContext,
    callers: set[str],
    imported_callables: _ImportedCallables,
    source_evidence: _SourceEvidenceIndex,
) -> _TargetEvidence:
    module_target, _, _, class_names = context
    path, _, target_class, target_name, target_exports = module_target
    parents, _, scope_imports, dynamic_names, deferred_annotation_calls = shape
    class_instances = _class_target_instances(tree, class_names)
    caller_count = len(callers)
    _collect_static_target_callers(shape, context, class_instances, callers)
    reference_names = _context_reference_names(context, scope_imports)
    dynamic_evidence, exact_dynamic_locations = _dynamic_target_evidence(
        path,
        tree,
        parents,
        dynamic_names,
        target_class,
        target_name,
        target_exports,
        reference_names,
        class_instances,
        deferred_annotation_calls,
        imported_callables,
        source_evidence,
    )
    linked_evidence = _target_linked_evidence(
        tree,
        shape,
        context,
        class_instances,
        exact_dynamic_locations,
        source_evidence,
        imported_callables,
    )
    linked_evidence = {
        item for item in linked_evidence if _evidence_caller_key(item) not in callers
    }
    return _partition_target_evidence(
        path,
        callers,
        caller_count,
        dynamic_evidence,
        exact_dynamic_locations,
        linked_evidence,
    )


def _partition_target_evidence(
    path: str,
    callers: set[str],
    caller_count: int,
    dynamic_evidence: set[str],
    exact_dynamic_locations: set[str],
    linked_evidence: set[str],
) -> _TargetEvidence:
    exact_evidence = frozenset(
        f"{path}:{location}" for location in exact_dynamic_locations
    )
    invocation_evidence = frozenset(linked_evidence) & exact_evidence
    reference_evidence = frozenset(linked_evidence) - exact_evidence
    unlinked_evidence = frozenset(dynamic_evidence - linked_evidence)
    executed = len(callers) > caller_count or bool(invocation_evidence)
    execution_state: _ExecutionState
    if executed:
        execution_state = "executed"
    elif reference_evidence:
        execution_state = "referenced_only"
    else:
        execution_state = "unknown"
    return _TargetEvidence(
        binding_state="exact" if executed or reference_evidence else "plausible",
        execution_state=execution_state,
        invocation_evidence=invocation_evidence,
        reference_evidence=reference_evidence,
        unlinked_evidence=unlinked_evidence,
    )


def _collect_target_callers(
    path: str,
    tree: ast.Module,
    shape: _SourceShape,
    target: tuple[str, str],
    target_exports: _TargetExports,
    callers: set[str],
    imported_callables: _ImportedCallables | None = None,
    source_evidence: _SourceEvidenceIndex | None = None,
) -> _TargetEvidence:
    context = _target_call_context(path, tree, target, target_exports)
    return _collect_target_evidence(
        tree,
        shape,
        context,
        callers,
        imported_callables or {},
        source_evidence or _source_evidence_index(tree),
    )


def _collect_python_caller_evidence(
    parsed_sources: dict[str, tuple[ast.Module, _SourceShape]],
    target_exports: dict[tuple[str, str], _TargetExports],
    target_export_index: dict[tuple[str, str], set[tuple[str, str]]],
    targets_by_name: dict[str, list[tuple[str, str]]],
    callers: dict[tuple[str, str], set[str]],
    evidence_by_target: dict[tuple[str, str], _TargetEvidence],
    imported_callables: dict[str, _ImportedCallables],
) -> None:
    for path, (tree, shape) in parsed_sources.items():
        _collect_source_caller_evidence(
            path,
            tree,
            shape,
            target_exports,
            target_export_index,
            targets_by_name,
            callers,
            evidence_by_target,
            imported_callables.get(path, {}),
        )


def _collect_source_caller_evidence(
    path: str,
    tree: ast.Module,
    shape: _SourceShape,
    target_exports: dict[tuple[str, str], _TargetExports],
    target_export_index: dict[tuple[str, str], set[tuple[str, str]]],
    targets_by_name: dict[str, list[tuple[str, str]]],
    callers: dict[tuple[str, str], set[str]],
    evidence_by_target: dict[tuple[str, str], _TargetEvidence],
    imported_environment: _ImportedCallables,
) -> None:
    source_evidence = _source_evidence_index(tree)
    modeled_only_targets = _modeled_only_target_references(
        source_evidence.nodes, shape[0], imported_environment
    )
    relevant_targets = _relevant_source_targets(
        path, tree, shape, targets_by_name, target_export_index
    )
    exposed_targets = _exposed_import_target_keys(imported_environment)
    target_execution, target_escapes = _target_identity_indexes(
        tree, shape, relevant_targets, exposed_targets, imported_environment
    )
    for target in relevant_targets:
        evidence = _collect_target_callers(
            path,
            tree,
            shape,
            target,
            target_exports[target],
            callers[target],
            imported_environment,
            source_evidence,
        )
        evidence_by_target[target] = _merge_identity_evidence(
            path,
            target,
            evidence_by_target[target],
            evidence,
            target_execution,
            target_escapes,
            exposed_targets,
            modeled_only_targets,
        )


def _merge_identity_evidence(
    path: str,
    target: tuple[str, str],
    accumulated: _TargetEvidence,
    evidence: _TargetEvidence,
    target_execution: dict[tuple[str, str], set[str]],
    target_escapes: dict[tuple[str, str], set[str]],
    exposed_targets: set[tuple[str, str]],
    modeled_only_targets: set[tuple[str, str]],
) -> _TargetEvidence:
    identity = _identity_evidence(path, target, target_execution, target_escapes)
    if identity.reference_evidence and not identity.invocation_evidence:
        evidence = _demote_reference_only(evidence)
    if target in exposed_targets:
        evidence = evidence.merge(_TargetEvidence(binding_state="exact"))
    if target in modeled_only_targets and not (
        identity.invocation_evidence or identity.reference_evidence
    ):
        identity = _TargetEvidence(binding_state="exact")
    return accumulated.merge(evidence.merge(identity))


def _demote_reference_only(evidence: _TargetEvidence) -> _TargetEvidence:
    return _TargetEvidence(
        binding_state=evidence.binding_state,
        execution_state="referenced_only",
        reference_evidence=(
            evidence.reference_evidence | evidence.invocation_evidence
        ),
        unlinked_evidence=evidence.unlinked_evidence,
    )


def _target_identity_indexes(
    tree: ast.Module,
    shape: _SourceShape,
    relevant_targets: set[tuple[str, str]],
    exposed_targets: set[tuple[str, str]],
    imported_environment: _ImportedCallables,
) -> tuple[
    dict[tuple[str, str], set[str]],
    dict[tuple[str, str], set[str]],
]:
    if not relevant_targets & exposed_targets:
        return {}, {}
    parents = shape[0]
    return (
        _target_execution_index(tree, parents, imported_environment),
        _target_reference_index(tree, parents, imported_environment),
    )


def _identity_evidence(
    path: str,
    target: tuple[str, str],
    execution: dict[tuple[str, str], set[str]],
    escapes: dict[tuple[str, str], set[str]],
) -> _TargetEvidence:
    invocation = frozenset(
        f"{path}:{item}" for item in execution.get(target, set())
    )
    references = frozenset(f"{path}:{item}" for item in escapes.get(target, set()))
    state: _ExecutionState
    if invocation:
        state = "executed"
    elif references:
        state = "referenced_only"
    else:
        state = "unreachable"
    return _TargetEvidence(
        binding_state="exact" if invocation or references else "disproven",
        execution_state=state,
        invocation_evidence=invocation,
        reference_evidence=references,
    )


__all__: list[str] = []
