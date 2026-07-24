"""激活工件的显式版本解码、旧摘要验签和只读迁移。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from ai_sdlc.core.stage_review.activation_fence import (
    activation_safety_mutation_fence,
)
from ai_sdlc.core.stage_review.activation_models import (
    ActivationEvidence,
    ActivationSessionRecord,
    StageGateActivationPolicy,
)
from ai_sdlc.core.stage_review.activation_source_models import (
    ActivationEvidencePackage,
)
from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    create_json_exclusive,
    read_json_object,
    resolve_canonical_shared_state,
    resolve_repository_project_id,
)
from ai_sdlc.core.stage_review.candidate import (
    CandidateManifest,
    candidate_binding_digest,
)
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.close_gate_models import StageCloseGateAttestation
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.optimization.attribution import AttributionPolicy

_DIGEST_POLICY = CanonicalizationPolicy()


class LegacyActivationArtifactUnavailableError(ValueError):
    """旧工件已验签，但不能安全参与当前激活决策。"""


def decode_activation_policy(payload: object) -> StageGateActivationPolicy:
    data = _mapping(payload)
    if data.get("schema_version") == "stage-gate-activation-policy.v2":
        return StageGateActivationPolicy.model_validate(data)
    legacy = _verified_legacy(
        data,
        schema_version="stage-gate-activation-policy.v1",
        digest_field="policy_digest",
    )
    attribution_policy = AttributionPolicy.baseline()
    migrated = _legacy_envelope(
        legacy,
        schema_version="stage-gate-activation-policy.v2",
        digest_field="policy_digest",
    )
    migrated.update(
        {
            "outcome_maturity_window_days": 14,
            "maximum_reversal_rate_upper": 0.1,
            "maximum_late_critical_rate_upper": 0.1,
            "maximum_escape_rate_upper": 0.1,
            "activation_escape_cause_ids": list(
                attribution_policy.non_optimizable_causes
            ),
            "attribution_policy_digest": attribution_policy.policy_digest,
        }
    )
    return StageGateActivationPolicy.model_validate(migrated)


def decode_activation_session_record(
    root: Path,
    payload: object,
) -> ActivationSessionRecord:
    data = _mapping(payload)
    if data.get("schema_version") == "stage-gate-activation-session-record.v2":
        return ActivationSessionRecord.model_validate(data)
    legacy = _verified_legacy(
        data,
        schema_version="stage-gate-activation-session-record.v1",
        digest_field="record_digest",
    )
    scope = _scope_for_legacy_candidate(
        root,
        project_id=str(legacy.get("project_id", "")),
        candidate_digest=str(legacy.get("candidate_manifest_digest", "")),
        session_id=str(_mapping(legacy.get("observation")).get("session_id", "")),
        stage_key=str(_mapping(legacy.get("observation")).get("stage_key", "")),
    )
    migrated = _legacy_envelope(
        legacy,
        schema_version="stage-gate-activation-session-record.v2",
        digest_field="record_digest",
    )
    migrated["close_proof_kind"] = "shadow-attestation"
    migrated["close_proof_id"] = migrated.pop("attestation_id")
    migrated["close_proof_digest"] = migrated.pop("attestation_digest")
    migrated["scope"] = scope.model_dump(mode="json")
    return ActivationSessionRecord.model_validate(migrated)


def read_activation_session_records(
    root: Path,
    paths: tuple[Path, ...],
) -> tuple[ActivationSessionRecord, ...]:
    records = []
    for path in paths:
        payload = read_json_object(path)
        try:
            records.append(decode_activation_session_record(root, payload))
        except LegacyActivationArtifactUnavailableError as error:
            _quarantine_legacy_artifact_safely(
                root,
                path,
                payload,
                error,
                digest_field="record_digest",
                reason_code="legacy-scope-unavailable",
            )
    return tuple(records)


def read_stage_close_gate_attestations(
    root: Path,
    paths: tuple[Path, ...],
) -> tuple[StageCloseGateAttestation, ...]:
    attestations = []
    for path in paths:
        payload = read_json_object(path)
        try:
            attestations.append(
                decode_stage_close_gate_attestation(root, payload)
            )
        except LegacyActivationArtifactUnavailableError as error:
            _quarantine_legacy_artifact_safely(
                root,
                path,
                payload,
                error,
                digest_field="attestation_digest",
                reason_code="legacy-attestation-scope-unavailable",
            )
    return tuple(attestations)


def quarantined_stage_close_attestation_ids(root: Path) -> frozenset[str]:
    return frozenset(
        child
        for children in quarantined_stage_close_attestation_children(root).values()
        for child in children
    )


def quarantined_stage_close_attestation_children(
    root: Path,
) -> dict[str, tuple[str, ...]]:
    project_id = resolve_repository_project_id(root)
    shared = resolve_canonical_shared_state(root, project_id)
    quarantine_root = shared / "activation/compatibility-quarantine"
    children: dict[str, list[str]] = {}
    for path in sorted(quarantine_root.glob("*.json")):
        payload = read_json_object(path)
        if payload.get("reason_code") == "legacy-attestation-scope-unavailable":
            child = str(payload.get("attestation_id", ""))
            parent = str(payload.get("supersedes_attestation_id", ""))
            if child:
                children.setdefault(parent, []).append(child)
    return {
        parent: tuple(sorted(set(values)))
        for parent, values in children.items()
    }


def decode_stage_close_gate_attestation(
    root: Path,
    payload: object,
) -> StageCloseGateAttestation:
    data = _mapping(payload)
    if data.get("schema_version") == "stage-close-gate-attestation.v2":
        return StageCloseGateAttestation.model_validate(data)
    legacy = _verified_legacy(
        data,
        schema_version="stage-close-gate-attestation.v1",
        digest_field="attestation_digest",
    )
    migrated = _legacy_envelope(
        legacy,
        schema_version="stage-close-gate-attestation.v2",
        digest_field="attestation_digest",
    )
    review_session_digest = str(legacy.get("review_session_digest", ""))
    if review_session_digest:
        candidate = _mapping(legacy.get("candidate"))
        migrated["review_scope"] = _scope_for_legacy_candidate(
            root,
            project_id=resolve_repository_project_id(root),
            candidate_digest=str(candidate.get("candidate_manifest_digest", "")),
            session_id="",
            stage_key=str(legacy.get("stage_key", "")),
        ).model_dump(mode="json")
    else:
        migrated["review_scope"] = None
    return StageCloseGateAttestation.model_validate(migrated)


def decode_activation_evidence(payload: object) -> ActivationEvidence:
    data = _mapping(payload)
    if data.get("schema_version") == "stage-gate-activation-evidence.v2":
        return ActivationEvidence.model_validate(data)
    _verified_legacy(
        data,
        schema_version="stage-gate-activation-evidence.v1",
        digest_field="evidence_digest",
    )
    raise LegacyActivationArtifactUnavailableError(
        "v1 activation evidence uses untrusted aggregate outcome counters"
    )


def decode_activation_evidence_package(payload: object) -> ActivationEvidencePackage:
    data = _mapping(payload)
    if data.get("schema_version") == "activation-evidence-package.v2":
        return ActivationEvidencePackage.model_validate(data)
    _verified_legacy(
        data,
        schema_version="activation-evidence-package.v1",
        digest_field="package_digest",
    )
    raise LegacyActivationArtifactUnavailableError(
        "v1 activation evidence package must be replaced by a v2 probe package"
    )


def _scope_for_legacy_candidate(
    root: Path,
    *,
    project_id: str,
    candidate_digest: str,
    session_id: str,
    stage_key: str,
) -> FindingScope:
    shared = resolve_canonical_shared_state(root, project_id)
    candidates = sorted((shared / "shadow-planning").glob("*/candidate.json"))
    if len(candidates) > 10_000:
        raise LegacyActivationArtifactUnavailableError(
            "legacy activation candidate scan budget exceeded"
        )
    matches = []
    for path in candidates:
        candidate = CandidateManifest.model_validate(read_json_object(path))
        if candidate_binding_digest(candidate) == candidate_digest:
            matches.append(candidate)
    if len(matches) != 1:
        raise LegacyActivationArtifactUnavailableError(
            "legacy activation scope cannot be reconstructed uniquely"
        )
    candidate = matches[0]
    if (
        candidate.project_id != project_id
        or (session_id and candidate.review_session_id != session_id)
        or candidate.stage_key != stage_key
    ):
        raise SharedStateIntegrityError(
            "legacy activation candidate lineage diverged"
        )
    return FindingScope(
        project_id=candidate.project_id,
        work_item_id=candidate.work_item_id,
        stage_instance_id=candidate.stage_instance_id,
        session_id=candidate.review_session_id,
    )

def _verified_legacy(
    payload: dict[str, object],
    *,
    schema_version: str,
    digest_field: str,
) -> dict[str, object]:
    if payload.get("schema_version") != schema_version:
        raise ValueError(f"unsupported activation schema: {payload.get('schema_version')}")
    source_digest = payload.get(digest_field)
    if not isinstance(source_digest, str) or not source_digest:
        raise SharedStateIntegrityError("legacy activation digest is missing")
    expected = canonical_digest(
        {key: value for key, value in payload.items() if key != digest_field},
        _DIGEST_POLICY,
    )
    if source_digest != expected:
        raise SharedStateIntegrityError("legacy activation digest does not match content")
    return dict(payload)


def _legacy_envelope(
    payload: dict[str, object],
    *,
    schema_version: str,
    digest_field: str,
) -> dict[str, object]:
    migrated = dict(payload)
    source_version = str(migrated["schema_version"])
    source_digest = str(migrated[digest_field])
    extensions = dict(_mapping(migrated.get("extensions", {})))
    extensions.update(
        {
            "migrated_from_schema_version": source_version,
            "source_digest": source_digest,
        }
    )
    migrated.update(
        {
            "schema_version": schema_version,
            "compatibility_mode": "read-only-legacy",
            "extensions": extensions,
        }
    )
    return migrated


def _quarantine_legacy_artifact(
    root: Path,
    path: Path,
    payload: object,
    error: LegacyActivationArtifactUnavailableError,
    *,
    digest_field: str,
    reason_code: str,
) -> None:
    data = _mapping(payload)
    source_digest = str(data.get(digest_field, ""))
    identity = canonical_digest(
        {
            "source_digest": source_digest,
            "reason_code": reason_code,
        },
        _DIGEST_POLICY,
    ).removeprefix("sha256:")
    project_id = resolve_repository_project_id(root)
    shared = resolve_canonical_shared_state(root, project_id)
    record = {
        "schema_version": "activation-compatibility-quarantine.v1",
        "artifact_kind": "activation-compatibility-quarantine",
        "project_id": project_id,
        "source_artifact": path.name,
        "source_digest": source_digest,
        "reason_code": reason_code,
        "detail": str(error),
    }
    if reason_code == "legacy-attestation-scope-unavailable":
        record.update(
            {
                "attestation_id": str(data.get("attestation_id", "")),
                "supersedes_attestation_id": str(
                    data.get("supersedes_attestation_id", "")
                ),
            }
        )
    create_json_exclusive(
        shared / "activation/compatibility-quarantine" / f"{identity}.json",
        record,
    )


def _quarantine_legacy_artifact_safely(
    root: Path,
    path: Path,
    payload: object,
    error: LegacyActivationArtifactUnavailableError,
    *,
    digest_field: str,
    reason_code: str,
) -> None:
    project_id = resolve_repository_project_id(root)
    with activation_safety_mutation_fence(root, project_id):
        _quarantine_legacy_artifact(
            root,
            path,
            payload,
            error,
            digest_field=digest_field,
            reason_code=reason_code,
        )


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("activation artifact must be a JSON object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError("activation artifact keys must be strings")
    return dict(value)
