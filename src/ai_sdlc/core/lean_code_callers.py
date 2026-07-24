"""Snapshot-bound Python caller analysis for new public Lean symbols."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ai_sdlc.core.lean_code_caller_dynamic_resolution import (
    _resolved_target_exports,
)
from ai_sdlc.core.lean_code_caller_evaluation_support import (
    _collect_python_caller_evidence,
)
from ai_sdlc.core.lean_code_caller_evidence import _parsed_product_sources
from ai_sdlc.core.lean_code_caller_models import _TargetEvidence
from ai_sdlc.core.lean_code_caller_module_state import (
    _new_public_targets,
    _reexport_candidate_index,
    _target_export_index,
    _targets_by_name,
)
from ai_sdlc.core.lean_code_caller_target_semantics import _imported_callable_index
from ai_sdlc.core.lean_code_models import FileMetric, FunctionMetric
from ai_sdlc.core.source_snapshot import SourceSnapshot
from ai_sdlc.core.source_snapshot_view import python_sources


def _apply_python_caller_evidence(
    targets: dict[tuple[str, str], FunctionMetric],
    callers: dict[tuple[str, str], set[str]],
    evidence_by_target: dict[tuple[str, str], _TargetEvidence],
) -> None:
    for target, function in targets.items():
        evidence = evidence_by_target[target]
        function.caller_count = len(callers[target])
        function.caller_evidence = sorted(callers[target])
        function.binding_state = evidence.binding_state
        function.execution_state = evidence.execution_state
        function.invocation_evidence = sorted(evidence.invocation_evidence)
        function.reference_evidence = sorted(evidence.reference_evidence)
        function.unlinked_evidence = sorted(evidence.unlinked_evidence)
        if evidence.invocation_evidence and not function.invocation_boundary:
            function.invocation_boundary = "dynamic-reference"
        elif evidence.unlinked_evidence and not function.invocation_boundary:
            function.invocation_boundary = "dynamic-reference-unlinked"


def attach_python_callers(
    root: Path,
    snapshot: SourceSnapshot,
    files: list[FileMetric],
    source_loader: Callable[[], dict[str, bytes]] | None = None,
) -> None:
    """Count only symbol-resolved callers from the frozen snapshot after-view."""

    targets = _new_public_targets(files)
    if not targets:
        return
    callers: dict[tuple[str, str], set[str]] = {target: set() for target in targets}
    evidence_by_target = {
        target: _TargetEvidence(binding_state="exact") for target in targets
    }
    sources = (
        source_loader() if source_loader is not None else python_sources(root, snapshot)
    )
    parsed_sources = _parsed_product_sources(sources)
    imported_callables = _imported_callable_index(parsed_sources)
    reexport_candidates = _reexport_candidate_index(parsed_sources)
    target_exports = _resolved_target_exports(
        parsed_sources,
        reexport_candidates,
        targets,
    )
    targets_by_name = _targets_by_name(target_exports)
    target_export_index = _target_export_index(target_exports)
    _collect_python_caller_evidence(
        parsed_sources,
        target_exports,
        target_export_index,
        targets_by_name,
        callers,
        evidence_by_target,
        imported_callables,
    )
    _apply_python_caller_evidence(targets, callers, evidence_by_target)

__all__ = ["attach_python_callers"]
