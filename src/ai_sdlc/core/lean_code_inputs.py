"""Freeze and cross-bind every input consumed by one Lean evaluation round."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ai_sdlc.core.implementation_models import ImplementationInput
from ai_sdlc.core.implementation_store import (
    implementation_artifacts,
    implementation_task_items_digest,
    read_tasks,
)
from ai_sdlc.core.lean_code_artifacts import (
    read_current_report,
    read_lean_exceptions,
    read_regression_evidence,
)
from ai_sdlc.core.lean_code_evaluator import LeanEvaluationOptions, evaluate_lean_code
from ai_sdlc.core.lean_code_evidence import implementation_verification_artifacts
from ai_sdlc.core.lean_code_models import (
    LeanEvaluationInput,
    LeanEvaluationReport,
    LeanException,
    LeanPolicy,
    RegressionEvidence,
    evaluation_profile_for,
)
from ai_sdlc.core.lean_code_policy import stable_artifact_digest
from ai_sdlc.core.source_snapshot import (
    SourceSnapshot,
    SourceSnapshotOptions,
    build_source_snapshot,
)


@dataclass(frozen=True)
class LeanEvaluationSource:
    """CLI-selected source view and optional structured evidence paths."""

    source_kind: str = "local-unstaged"
    base_ref: str = ""
    head_ref: str = "HEAD"
    patch_file: str = ""
    regression_evidence_paths: tuple[str, ...] = ()
    exception_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class _LoadedEvaluation:
    snapshot: SourceSnapshot
    previous: LeanEvaluationReport | None
    regression: tuple[RegressionEvidence, ...]
    exceptions: tuple[LeanException, ...]
    task_refs: tuple[str, ...]
    tasks_digest: str
    acceptance_digests: dict[str, str]
    verification_tokens: tuple[str, ...]
    verification_refs: tuple[str, ...]
    verification_digests: dict[str, str]
    regression_paths: tuple[str, ...]
    exception_paths: tuple[str, ...]


def prepare_lean_evaluation(
    root: Path,
    loop_run,
    impl_input: ImplementationInput,
    policy: LeanPolicy,
    evaluation_round: int,
    source: LeanEvaluationSource,
) -> tuple[SourceSnapshot, LeanEvaluationReport, LeanEvaluationInput]:
    """Build one frozen input, deterministic report, and persisted input model."""

    loaded = _load_inputs(root, loop_run.loop_id, impl_input, source)
    report = _evaluate(loop_run, impl_input, policy, evaluation_round, loaded, root)
    return (
        loaded.snapshot,
        report,
        _evaluation_input(impl_input, policy, report, loaded),
    )


def _load_inputs(root, loop_id, impl_input, source) -> _LoadedEvaluation:
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(
            root=root,
            source_kind=source.source_kind,
            base_ref=source.base_ref,
            head_ref=source.head_ref,
            patch_file=source.patch_file,
        )
    )
    refs, digests, tokens = implementation_verification_artifacts(
        root, impl_input, snapshot.diff_hash
    )
    task_refs, tasks_digest = implementation_task_bindings(root, impl_input)
    return _LoadedEvaluation(
        snapshot=snapshot,
        previous=read_current_report(root, loop_id),
        regression=read_regression_evidence(root, source.regression_evidence_paths),
        exceptions=read_lean_exceptions(root, source.exception_paths),
        task_refs=task_refs,
        tasks_digest=tasks_digest,
        acceptance_digests=acceptance_evidence_digests(root, impl_input),
        verification_tokens=tokens,
        verification_refs=refs,
        verification_digests=digests,
        regression_paths=source.regression_evidence_paths,
        exception_paths=source.exception_paths,
    )


def _evaluate(loop_run, impl_input, policy, evaluation_round, loaded, root):
    previous = loaded.previous
    return evaluate_lean_code(
        LeanEvaluationOptions(
            root=root,
            loop_id=loop_run.loop_id,
            work_item_id=impl_input.work_item_id,
            work_type=impl_input.work_type,
            source_snapshot=loaded.snapshot,
            policy=policy,
            declared_scope=tuple(impl_input.declared_scope),
            task_refs=loaded.task_refs,
            acceptance_refs=(impl_input.spec_path,),
            regression_evidence=loaded.regression,
            exceptions=loaded.exceptions,
            verification_refs=loaded.verification_tokens,
            evaluation_round=evaluation_round,
            previous_findings=tuple(previous.findings) if previous else (),
            previous_report_digest=(
                stable_artifact_digest(previous) if previous is not None else ""
            ),
            previous_verification_digest=(
                previous.verification_digest if previous is not None else ""
            ),
            previous_had_actionable_findings=bool(
                previous.blocking_findings if previous is not None else False
            ),
        )
    )


def _evaluation_input(impl_input, policy, report, loaded) -> LeanEvaluationInput:
    snapshot = loaded.snapshot
    return LeanEvaluationInput(
        loop_id=impl_input.loop_id,
        work_item_id=impl_input.work_item_id,
        work_type=impl_input.work_type,
        evaluation_profile=evaluation_profile_for(impl_input.work_type),
        policy_version=policy.policy_version,
        policy_digest=report.policy_digest,
        base_ref=snapshot.base_ref,
        head_ref=snapshot.head_ref,
        base_commit=snapshot.base_commit,
        head_commit=snapshot.head_commit,
        diff_hash=snapshot.diff_hash,
        declared_scope=impl_input.declared_scope,
        changed_files=snapshot.changed_files,
        tasks_refs=list(loaded.task_refs),
        tasks_digest=loaded.tasks_digest,
        acceptance_refs=[impl_input.spec_path],
        acceptance_digests=loaded.acceptance_digests,
        verification_evidence_refs=list(loaded.verification_refs),
        verification_evidence_digests=loaded.verification_digests,
        regression_evidence_refs=list(loaded.regression_paths),
        regression_evidence_digests=_artifact_digests(
            loaded.regression_paths, loaded.regression
        ),
        exception_refs=list(loaded.exception_paths),
        exception_digests=_artifact_digests(loaded.exception_paths, loaded.exceptions),
        evaluation_round=report.evaluation_round,
    )


def _artifact_digests(paths, artifacts) -> dict[str, str]:
    return {
        path: stable_artifact_digest(item)
        for path, item in zip(paths, artifacts, strict=True)
    }


def implementation_task_bindings(
    root: Path,
    impl_input: ImplementationInput,
) -> tuple[tuple[str, ...], str]:
    """Return task identities plus the exact artifact bytes consumed by evaluation."""

    path = implementation_artifacts(root, impl_input.loop_id).tasks_path
    if not path.is_file():
        return (impl_input.tasks_path,), ""
    tasks = read_tasks(path)
    current_digest = implementation_task_items_digest(tasks.items)
    if impl_input.tasks_digest and current_digest != impl_input.tasks_digest:
        raise ValueError(
            "Implementation tasks snapshot does not match the digest frozen at "
            "implementation start."
        )
    return tuple(item.task_id for item in tasks.items), _bytes_digest(path)


def acceptance_evidence_digests(
    root: Path,
    impl_input: ImplementationInput,
) -> dict[str, str]:
    """Bind the acceptance document bytes when the canonical spec exists."""

    path = _safe_path(root, impl_input.spec_path)
    return {impl_input.spec_path: _bytes_digest(path)} if path.is_file() else {}


def _bytes_digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _safe_path(root: Path, reference: str) -> Path:
    path = (root / reference).resolve()
    path.relative_to(root.resolve())
    return path


__all__ = [
    "LeanEvaluationSource",
    "acceptance_evidence_digests",
    "implementation_task_bindings",
    "prepare_lean_evaluation",
]
