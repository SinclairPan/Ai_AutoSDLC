"""受保护 Activation Policy 的跨 Worktree CAS 与自动推进。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from ai_sdlc.core.stage_review.activation import (
    ActivationAssessment,
    ActivationEvaluationCohortBoundary,
    ActivationEvidence,
    ActivationSessionRecord,
    StageGateActivationPolicy,
    advance_activation_policy,
    assess_activation,
    baseline_activation_policy,
)
from ai_sdlc.core.stage_review.activation_artifact_codec import (
    decode_activation_policy,
    read_activation_session_records,
)
from ai_sdlc.core.stage_review.activation_evidence_ingestor import (
    verify_activation_source_records,
)
from ai_sdlc.core.stage_review.activation_fence import (
    activation_safety_mutation_fence,
)
from ai_sdlc.core.stage_review.activation_outcomes import (
    derive_activation_session_outcomes,
    lock_activation_outcome_sources,
    mature_activation_session_records,
)
from ai_sdlc.core.stage_review.activation_policy_anchor import (
    read_activation_policy_anchor,
    select_local_activation_policy,
    write_activation_policy_anchor,
)
from ai_sdlc.core.stage_review.activation_rollback import (
    require_activation_rollback_idle,
)
from ai_sdlc.core.stage_review.activation_safety import (
    activation_evaluation_cohort,
    persist_activation_safety_hold,
)
from ai_sdlc.core.stage_review.activation_source_models import (
    ActivationIsolationSourceRecord,
    ActivationProbeSourceRecord,
)
from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.artifacts import (
    ShortFileLock,
    atomic_write_json,
    bind_repository_project,
    create_json_exclusive,
    read_json_object,
    resolve_canonical_shared_state,
    resolve_repository_project_id,
)


class ActivationPolicyPointer(ArtifactCompatibility):
    schema_version: Literal["stage-gate-activation-policy-pointer.v1"] = (
        "stage-gate-activation-policy-pointer.v1"
    )
    artifact_kind: Literal["stage-gate-activation-policy-pointer"] = (
        "stage-gate-activation-policy-pointer"
    )
    project_id: str
    revision: int = Field(ge=1)
    policy_digest: str
    previous_policy_digest: str
    pointer_digest: str = ""

    @model_validator(mode="after")
    def _verify_pointer(self) -> Self:
        if not all(
            value.strip()
            for value in (
                self.project_id,
                self.policy_digest,
                self.previous_policy_digest,
            )
        ):
            raise ValueError("activation policy pointer identity is incomplete")
        return fill_artifact_digest(self, "pointer_digest")


@dataclass(frozen=True, slots=True)
class _ActivationPaths:
    repository: Path
    root: Path
    policies: Path
    evidences: Path
    assessments: Path
    session_records: Path
    isolation_sources: Path
    probe_sources: Path
    pointer: Path
    lock: Path
    project_id: str


def current_activation_policy(root: Path) -> StageGateActivationPolicy:
    require_activation_rollback_idle(root)
    paths = _activation_paths(root)
    pointer_policy = _read_pointer_policy(paths)
    anchor_policy = read_activation_policy_anchor(root)
    return select_local_activation_policy(
        pointer_policy,
        anchor_policy,
        baseline_activation_policy(),
    )


def _advance_activation_policy_from_evidence(
    root: Path,
    evidence: ActivationEvidence,
    *,
    grandfathered_loop_ids: tuple[str, ...] = (),
) -> tuple[StageGateActivationPolicy, ActivationAssessment]:
    require_activation_rollback_idle(root)
    paths = _activation_paths(root)
    with (
        activation_safety_mutation_fence(root, paths.project_id),
        ShortFileLock(paths.lock, timeout_seconds=5),
    ):
            bind_repository_project(paths.root, paths.project_id)
            current, revision = _read_current_locked(paths)
            records, cohort_boundaries = _mature_session_records(
                paths,
                current,
                evidence.assessed_at,
            )
            with lock_activation_outcome_sources(root, records):
                _verify_evidence_sources(
                    paths,
                    evidence,
                    current,
                    session_records=records,
                    cohort_boundaries=cohort_boundaries,
                )
                _persist_evidence(paths, evidence)
                assessment = assess_activation(current, evidence)
                _persist_assessment(paths, assessment)
                persist_activation_safety_hold(
                    root,
                    policy=current,
                    evidence=evidence,
                    assessment=assessment,
                )
                promoted = advance_activation_policy(
                    current,
                    assessment,
                    grandfathered_loop_ids=grandfathered_loop_ids,
                )
                if promoted is None:
                    return current, assessment
                _persist_policy(paths, promoted)
                write_activation_policy_anchor(root, promoted)
                pointer = ActivationPolicyPointer(
                    project_id=paths.project_id,
                    revision=revision + 1,
                    policy_digest=promoted.policy_digest,
                    previous_policy_digest=current.policy_digest,
                )
                atomic_write_json(paths.pointer, pointer.model_dump(mode="json"))
                return promoted, assessment


def _read_current_locked(
    paths: _ActivationPaths,
) -> tuple[StageGateActivationPolicy, int]:
    pointer_policy = _read_pointer_policy(paths)
    anchor_policy = read_activation_policy_anchor(paths.repository)
    current = select_local_activation_policy(
        pointer_policy,
        anchor_policy,
        baseline_activation_policy(),
    )
    if not paths.pointer.is_file():
        return current, 0
    pointer = ActivationPolicyPointer.model_validate(read_json_object(paths.pointer))
    return current, pointer.revision


def _read_pointer_policy(
    paths: _ActivationPaths,
) -> StageGateActivationPolicy | None:
    if not paths.pointer.is_file():
        return None
    pointer = ActivationPolicyPointer.model_validate(read_json_object(paths.pointer))
    if pointer.project_id != paths.project_id:
        raise ValueError("activation policy pointer project mismatch")
    return _read_policy(paths, pointer.policy_digest)


def _read_policy(
    paths: _ActivationPaths,
    policy_digest: str,
) -> StageGateActivationPolicy:
    path = paths.policies / f"{_digest_name(policy_digest)}.json"
    policy = decode_activation_policy(read_json_object(path))
    if policy.policy_digest != policy_digest:
        raise ValueError("activation policy pointer digest mismatch")
    return policy


def _verify_evidence_sources(
    paths: _ActivationPaths,
    evidence: ActivationEvidence,
    policy: StageGateActivationPolicy,
    *,
    session_records: tuple[ActivationSessionRecord, ...],
    cohort_boundaries: tuple[ActivationEvaluationCohortBoundary, ...],
) -> None:
    if evidence.project_id != paths.project_id:
        raise ValueError("activation evidence project mismatch")
    selected = session_records
    if (
        tuple(item.record_digest for item in selected)
        != evidence.session_record_digests
    ):
        raise ValueError("activation mature session population is incomplete")
    if any(item.project_id != paths.project_id for item in selected):
        raise ValueError("activation session source project mismatch")
    if tuple(item.observation for item in selected) != evidence.sessions:
        raise ValueError("activation session source lineage diverged")
    if tuple(cohort_boundaries) != evidence.cohort_boundaries:
        raise ValueError("activation evaluation cohort lineage diverged")
    isolation = _verify_isolation_sources(paths, evidence)
    expected_outcomes = derive_activation_session_outcomes(
        paths.repository,
        selected,
        policy=policy,
        assessed_at=evidence.assessed_at,
    )
    if expected_outcomes != evidence.session_outcomes:
        raise ValueError("activation session outcome lineage diverged")
    probes = _read_probe_source(paths, evidence.probe_record_digest)
    if probes.project_id != paths.project_id or probes.evidence != evidence.probes:
        raise ValueError("activation probe source lineage diverged")
    verify_activation_source_records(
        paths.repository,
        isolation,
        probes,
        policy=policy,
    )


def _mature_session_records(
    paths: _ActivationPaths,
    policy: StageGateActivationPolicy,
    assessed_at: str,
) -> tuple[
    tuple[ActivationSessionRecord, ...],
    tuple[ActivationEvaluationCohortBoundary, ...],
]:
    files = sorted(paths.session_records.glob("*.json"))
    records = read_activation_session_records(paths.repository, tuple(files))
    ordered = tuple(sorted(records, key=lambda item: item.observation.session_id))
    if len({item.observation.session_id for item in ordered}) != len(ordered):
        raise ValueError("activation session source is ambiguous")
    mature = mature_activation_session_records(
        ordered,
        policy=policy,
        assessed_at=assessed_at,
    )
    return activation_evaluation_cohort(
        paths.repository,
        mature,
        policy=policy,
    )


def _verify_isolation_sources(
    paths: _ActivationPaths,
    evidence: ActivationEvidence,
) -> tuple[ActivationIsolationSourceRecord, ...]:
    selected = tuple(
        _read_isolation_source(paths, digest)
        for digest in evidence.isolation_record_digests
    )
    if any(item.project_id != paths.project_id for item in selected):
        raise ValueError("activation isolation source project mismatch")
    if tuple(item.evidence for item in selected) != evidence.isolation_matrix:
        raise ValueError("activation isolation source lineage diverged")
    return selected


def _read_isolation_source(
    paths: _ActivationPaths,
    digest: str,
) -> ActivationIsolationSourceRecord:
    path = paths.isolation_sources / f"{_digest_name(digest)}.json"
    record = ActivationIsolationSourceRecord.model_validate(read_json_object(path))
    if record.record_digest != digest:
        raise ValueError("activation isolation source digest mismatch")
    return record


def _read_probe_source(
    paths: _ActivationPaths,
    digest: str,
) -> ActivationProbeSourceRecord:
    path = paths.probe_sources / f"{_digest_name(digest)}.json"
    record = ActivationProbeSourceRecord.model_validate(read_json_object(path))
    if record.record_digest != digest:
        raise ValueError("activation probe source digest mismatch")
    return record


def _persist_policy(
    paths: _ActivationPaths,
    policy: StageGateActivationPolicy,
) -> None:
    path = paths.policies / f"{_digest_name(policy.policy_digest)}.json"
    payload = policy.model_dump(mode="json")
    if create_json_exclusive(path, payload):
        return
    current = StageGateActivationPolicy.model_validate(read_json_object(path))
    if current != policy:
        raise ValueError("activation policy content address diverged")


def _persist_assessment(
    paths: _ActivationPaths,
    assessment: ActivationAssessment,
) -> None:
    path = paths.assessments / f"{_digest_name(assessment.assessment_digest)}.json"
    payload = assessment.model_dump(mode="json")
    if create_json_exclusive(path, payload):
        return
    current = ActivationAssessment.model_validate(read_json_object(path))
    if current != assessment:
        raise ValueError("activation assessment content address diverged")


def _persist_evidence(paths: _ActivationPaths, evidence: ActivationEvidence) -> None:
    path = paths.evidences / f"{_digest_name(evidence.evidence_digest)}.json"
    payload = evidence.model_dump(mode="json")
    if create_json_exclusive(path, payload):
        return
    current = ActivationEvidence.model_validate(read_json_object(path))
    if current != evidence:
        raise ValueError("activation evidence content address diverged")


def _activation_paths(root: Path) -> _ActivationPaths:
    project_id = resolve_repository_project_id(root)
    shared = resolve_canonical_shared_state(root, project_id)
    base = shared / "activation"
    return _ActivationPaths(
        repository=root.resolve(),
        root=shared,
        policies=base / "policies",
        evidences=base / "evidences",
        assessments=base / "assessments",
        session_records=base / "session-records",
        isolation_sources=base / "evidence-sources" / "isolation",
        probe_sources=base / "evidence-sources" / "probes",
        pointer=base / "active-policy.json",
        lock=base / "policy.lock",
        project_id=project_id,
    )


def _digest_name(value: str) -> str:
    if not value.startswith("sha256:") or len(value) != 71:
        raise ValueError("activation content digest is invalid")
    return value.removeprefix("sha256:")
