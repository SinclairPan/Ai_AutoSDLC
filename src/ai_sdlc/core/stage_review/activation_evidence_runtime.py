"""从项目本地不可变来源自动组装并评估 Activation Evidence。"""

from __future__ import annotations

from pathlib import Path

from ai_sdlc.core.loop_models import utc_now_iso
from ai_sdlc.core.stage_review.activation_artifact_codec import (
    read_activation_session_records,
)
from ai_sdlc.core.stage_review.activation_evidence_ingestor import (
    import_activation_evidence_inbox,
)
from ai_sdlc.core.stage_review.activation_fence import (
    activation_safety_mutation_fence,
)
from ai_sdlc.core.stage_review.activation_models import (
    ActivationAssessment,
    ActivationEvidence,
    ActivationSessionRecord,
    StageGateActivationPolicy,
)
from ai_sdlc.core.stage_review.activation_outcomes import (
    derive_activation_session_outcomes,
    lock_activation_outcome_sources,
    mature_activation_session_records,
    recover_activation_session_attributions,
)
from ai_sdlc.core.stage_review.activation_policy_store import (
    _advance_activation_policy_from_evidence as advance_activation_policy_from_evidence,
)
from ai_sdlc.core.stage_review.activation_policy_store import (
    current_activation_policy,
)
from ai_sdlc.core.stage_review.activation_safety import (
    activation_evaluation_cohort,
    revalidate_activation_safety_releases,
)
from ai_sdlc.core.stage_review.activation_source_models import (
    ActivationIsolationSourceRecord,
    ActivationProbeSourceRecord,
)
from ai_sdlc.core.stage_review.artifacts import (
    read_json_object,
    resolve_canonical_shared_state,
    resolve_repository_project_id,
)


def _refresh_activation_policy_from_local_evidence(
    root: Path,
    *,
    assessed_at: str | None = None,
) -> tuple[StageGateActivationPolicy, ActivationAssessment | None]:
    initial_policy = current_activation_policy(root)
    if (
        initial_policy.active_phase == 1
        and not (root / ".git").exists()
        and not (root / ".ai-sdlc").exists()
    ):
        return initial_policy, None
    project_id = resolve_repository_project_id(root)
    observed_at = assessed_at or utc_now_iso()
    with activation_safety_mutation_fence(root, project_id):
        policy = current_activation_policy(root)
        import_activation_evidence_inbox(root, policy=policy)
        revalidate_activation_safety_releases(
            root,
            policy=policy,
            assessed_at=observed_at,
        )
        evidence = _assemble_activation_evidence(
            root,
            policy=policy,
            assessed_at=observed_at,
        )
        if evidence is None:
            return policy, None
        return advance_activation_policy_from_evidence(root, evidence)


def _assemble_activation_evidence(
    root: Path,
    *,
    policy: StageGateActivationPolicy,
    assessed_at: str,
) -> ActivationEvidence | None:
    project_id = resolve_repository_project_id(root)
    shared = resolve_canonical_shared_state(root, project_id)
    mature_sessions = mature_activation_session_records(
        _canonical_session_records(_read_session_records(root, shared)),
        policy=policy,
        assessed_at=assessed_at,
    )
    sessions, cohort_boundaries = activation_evaluation_cohort(
        root,
        mature_sessions,
        policy=policy,
    )
    isolation = _read_isolation_sources(shared)
    probes = _read_probe_sources(shared)
    selected = _required_isolation(policy, isolation)
    if not sessions or selected is None or probes is None:
        return None
    recover_activation_session_attributions(root, sessions)
    with lock_activation_outcome_sources(root, sessions):
        outcomes = derive_activation_session_outcomes(
            root,
            sessions,
            policy=policy,
            assessed_at=assessed_at,
        )
    return ActivationEvidence(
        project_id=project_id,
        assessed_at=assessed_at,
        sessions=tuple(item.observation for item in sessions),
        session_record_digests=tuple(item.record_digest for item in sessions),
        isolation_matrix=tuple(item.evidence for item in selected),
        isolation_record_digests=tuple(item.record_digest for item in selected),
        probes=probes.evidence,
        probe_record_digest=probes.record_digest,
        session_outcomes=outcomes,
        cohort_boundaries=cohort_boundaries,
    )


def _canonical_session_records(
    records: tuple[ActivationSessionRecord, ...],
) -> tuple[ActivationSessionRecord, ...]:
    ordered = tuple(sorted(records, key=lambda item: item.observation.session_id))
    session_ids = tuple(item.observation.session_id for item in ordered)
    if len(session_ids) != len(set(session_ids)):
        raise ValueError("activation session source is ambiguous")
    return ordered


def _read_session_records(
    repository: Path,
    shared: Path,
) -> tuple[ActivationSessionRecord, ...]:
    root = shared / "activation" / "session-records"
    return read_activation_session_records(
        repository,
        tuple(sorted(root.glob("*.json"))),
    )


def _read_isolation_sources(
    shared: Path,
) -> tuple[ActivationIsolationSourceRecord, ...]:
    root = shared / "activation" / "evidence-sources" / "isolation"
    return tuple(
        ActivationIsolationSourceRecord.model_validate(read_json_object(path))
        for path in sorted(root.glob("*.json"))
    )


def _read_probe_sources(shared: Path) -> ActivationProbeSourceRecord | None:
    root = shared / "activation" / "evidence-sources" / "probes"
    values = tuple(
        ActivationProbeSourceRecord.model_validate(read_json_object(path))
        for path in sorted(root.glob("*.json"))
    )
    if len(values) > 1:
        raise ValueError("activation probe source is ambiguous")
    return values[0] if values else None


def _required_isolation(
    policy: StageGateActivationPolicy,
    values: tuple[ActivationIsolationSourceRecord, ...],
) -> tuple[ActivationIsolationSourceRecord, ...] | None:
    selected = []
    for platform in policy.required_isolation_platforms:
        matches = tuple(
            item for item in values if item.evidence.platform_id == platform
        )
        if len(matches) > 1:
            raise ValueError("activation isolation source is ambiguous")
        if not matches:
            return None
        selected.append(matches[0])
    return tuple(selected)
