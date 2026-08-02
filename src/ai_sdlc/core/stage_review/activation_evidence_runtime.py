"""从项目本地不可变来源自动组装并评估 Activation Evidence。"""

from __future__ import annotations

import subprocess
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
    ActivationEvidenceImportReceipt,
    ActivationIsolationSourceRecord,
    ActivationProbeSourceRecord,
    ActiveActivationEvidenceSourceSet,
)
from ai_sdlc.core.stage_review.artifacts import (
    create_json_exclusive,
    portable_content_digest_name,
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
    source_set = _select_active_evidence_source_set(root, shared, policy)
    if source_set is None:
        return None
    isolation = _read_isolation_sources(shared, policy, source_set)
    probes = _read_probe_sources(shared, policy, source_set)
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
        evidence_source_set_digest=source_set.source_set_digest,
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
    policy: StageGateActivationPolicy,
    source_set: ActiveActivationEvidenceSourceSet,
) -> tuple[ActivationIsolationSourceRecord, ...]:
    root = shared / "activation" / "evidence-sources" / "isolation"
    values = tuple(
        ActivationIsolationSourceRecord.model_validate(
            read_json_object(
                root / f"{portable_content_digest_name(digest)}.json"
            )
        )
        for digest in source_set.isolation_record_digests
    )
    if any(
        item.record_digest not in source_set.isolation_record_digests
        or item.import_receipt_digest != source_set.import_receipt_digest
        for item in values
    ):
        raise ValueError("activation isolation source set lineage diverged")
    _require_source_set_policy(shared, source_set, policy)
    return values


def _read_probe_sources(
    shared: Path,
    policy: StageGateActivationPolicy,
    source_set: ActiveActivationEvidenceSourceSet,
) -> ActivationProbeSourceRecord:
    root = shared / "activation" / "evidence-sources" / "probes"
    value = ActivationProbeSourceRecord.model_validate(
        read_json_object(
            root
            / f"{portable_content_digest_name(source_set.probe_record_digest)}.json"
        )
    )
    if (
        value.record_digest != source_set.probe_record_digest
        or value.import_receipt_digest != source_set.import_receipt_digest
    ):
        raise ValueError("activation probe source set lineage diverged")
    _require_source_set_policy(shared, source_set, policy)
    return value


def _select_active_evidence_source_set(
    repository: Path,
    shared: Path,
    policy: StageGateActivationPolicy,
) -> ActiveActivationEvidenceSourceSet | None:
    isolation = tuple(
        ActivationIsolationSourceRecord.model_validate(read_json_object(path))
        for path in sorted(
            (shared / "activation/evidence-sources/isolation").glob("*.json")
        )
    )
    probes = tuple(
        ActivationProbeSourceRecord.model_validate(read_json_object(path))
        for path in sorted(
            (shared / "activation/evidence-sources/probes").glob("*.json")
        )
    )
    receipt_digests = tuple(
        sorted(
            {
                *(item.import_receipt_digest for item in isolation),
                *(item.import_receipt_digest for item in probes),
            }
        )
    )
    candidates: list[
        tuple[ActiveActivationEvidenceSourceSet, ActivationEvidenceImportReceipt]
    ] = []
    for receipt_digest in receipt_digests:
        receipt = _read_import_receipt(shared, receipt_digest)
        if receipt.activation_policy_digest != policy.policy_digest:
            continue
        receipt_isolation = tuple(
            item for item in isolation if item.import_receipt_digest == receipt_digest
        )
        receipt_probes = tuple(
            item for item in probes if item.import_receipt_digest == receipt_digest
        )
        selected_isolation = _required_isolation(policy, receipt_isolation)
        if selected_isolation is None or len(receipt_probes) != 1:
            continue
        source_set = ActiveActivationEvidenceSourceSet(
            project_id=receipt.project_id,
            activation_policy_digest=policy.policy_digest,
            import_receipt_digest=receipt.receipt_digest,
            tested_commit=receipt.tested_commit,
            isolation_record_digests=tuple(
                sorted(item.record_digest for item in selected_isolation)
            ),
            probe_record_digest=receipt_probes[0].record_digest,
        )
        candidates.append((source_set, receipt))
    if not candidates:
        return None
    candidates = _deduplicate_same_commit_candidates(candidates)
    maximal = tuple(
        item
        for item in candidates
        if all(
            _commit_is_ancestor(repository, other[0].tested_commit, item[0].tested_commit)
            for other in candidates
        )
    )
    if len(maximal) != 1:
        raise ValueError("activation evidence source commits are incomparable")
    selected = maximal[0][0]
    path = (
        shared
        / "activation/evidence-source-sets"
        / f"{portable_content_digest_name(selected.source_set_digest)}.json"
    )
    if not create_json_exclusive(path, selected.model_dump(mode="json")):
        current = ActiveActivationEvidenceSourceSet.model_validate(read_json_object(path))
        if current != selected:
            raise ValueError("activation evidence source set persistence diverged")
    return selected


def _deduplicate_same_commit_candidates(
    candidates: list[
        tuple[ActiveActivationEvidenceSourceSet, ActivationEvidenceImportReceipt]
    ],
) -> list[tuple[ActiveActivationEvidenceSourceSet, ActivationEvidenceImportReceipt]]:
    by_commit: dict[
        str,
        list[tuple[ActiveActivationEvidenceSourceSet, ActivationEvidenceImportReceipt]],
    ] = {}
    for candidate in candidates:
        by_commit.setdefault(candidate[0].tested_commit, []).append(candidate)
    selected = []
    for tested_commit in sorted(by_commit):
        values = by_commit[tested_commit]
        if len({item[1].package_digest for item in values}) != 1:
            raise ValueError("activation evidence packages conflict at one commit")
        selected.append(min(values, key=lambda item: item[0].source_set_digest))
    return selected


def _commit_is_ancestor(repository: Path, ancestor: str, descendant: str) -> bool:
    if ancestor == descendant:
        return True
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repository,
        check=False,
        capture_output=True,
    )
    if result.returncode not in (0, 1):
        raise ValueError("activation evidence commit ancestry is unavailable")
    return result.returncode == 0


def _read_import_receipt(
    shared: Path,
    receipt_digest: str,
) -> ActivationEvidenceImportReceipt:
    name = portable_content_digest_name(receipt_digest)
    path = shared / "activation/evidence-imports/receipts" / f"{name}.json"
    receipt = ActivationEvidenceImportReceipt.model_validate(read_json_object(path))
    if receipt.receipt_digest != receipt_digest:
        raise ValueError("activation evidence import receipt digest diverged")
    return receipt


def _require_source_set_policy(
    shared: Path,
    source_set: ActiveActivationEvidenceSourceSet,
    policy: StageGateActivationPolicy,
) -> None:
    receipt = _read_import_receipt(shared, source_set.import_receipt_digest)
    if (
        source_set.activation_policy_digest != policy.policy_digest
        or receipt.activation_policy_digest != policy.policy_digest
        or receipt.project_id != source_set.project_id
        or receipt.tested_commit != source_set.tested_commit
    ):
        raise ValueError("activation evidence source set policy lineage diverged")


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
