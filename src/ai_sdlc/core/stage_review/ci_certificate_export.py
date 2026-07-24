"""从 canonical shared state 导出当前 Enforce Certificate 的 CI Bundle。"""

from __future__ import annotations

from pathlib import Path

from ai_sdlc.core.source_snapshot import SourceSnapshot
from ai_sdlc.core.stage_review.artifacts import (
    create_json_exclusive,
    read_json_object,
    resolve_canonical_shared_state,
    resolve_repository_project_id,
)
from ai_sdlc.core.stage_review.candidate import (
    CandidateManifest,
    candidate_binding_digest,
)
from ai_sdlc.core.stage_review.ci_certificate import (
    CiStageCloseCertificateBundle,
    build_ci_certificate_bundle,
)
from ai_sdlc.core.stage_review.ci_certificate_evidence import (
    CiCertificateAuthorityProof,
)
from ai_sdlc.core.stage_review.resource_builders import parse_utc


def export_latest_ci_certificate_bundle(
    root: Path,
    *,
    close_kind: str,
) -> Path | None:
    resolved = root.resolve()
    project_id = resolve_repository_project_id(resolved)
    shared = resolve_canonical_shared_state(resolved, project_id)
    proof = _latest_proof(shared, close_kind)
    if proof is None:
        return None
    candidate, snapshot = _candidate_source(shared, proof)
    bundle = build_ci_certificate_bundle(
        certificate=proof.certificate,
        request=proof.certificate_request,
        authority_evidence=proof.authority_evidence,
        aborted_claim=proof.aborted_claim,
        recovery_decision=proof.recovery_decision,
        candidate=candidate,
        source_snapshot=snapshot,
        reviewed_commit=snapshot.head_commit,
    )
    path = (
        resolved
        / candidate.review_artifact_exclusion_set[0]
        / "ci-certificate-bundle.json"
    )
    _persist_bundle(path, bundle)
    return path


def _latest_proof(
    shared: Path,
    close_kind: str,
) -> CiCertificateAuthorityProof | None:
    values = []
    proof_glob = "stage-review-sessions/sessions/*/*/*/certificate-proofs/*.json"
    for path in sorted(shared.glob(proof_glob)):
        proof = CiCertificateAuthorityProof.model_validate(read_json_object(path))
        if proof.certificate.close_kind == close_kind:
            values.append(proof)
    if not values:
        return None
    ordered = sorted(
        values,
        key=lambda item: (
            parse_utc(item.certificate.issued_at),
            item.certificate.certificate_id,
        ),
    )
    latest_time = ordered[-1].certificate.issued_at
    latest = tuple(
        item for item in ordered if item.certificate.issued_at == latest_time
    )
    if len(latest) != 1:
        raise ValueError("latest CI certificate authority proof is ambiguous")
    return latest[0]


def _candidate_source(
    shared: Path,
    proof: CiCertificateAuthorityProof,
) -> tuple[CandidateManifest, SourceSnapshot]:
    session_id = proof.certificate.scope.session_id
    planning = shared / "shadow-planning" / session_id
    candidate = CandidateManifest.model_validate(
        read_json_object(planning / "candidate.json")
    )
    snapshot = SourceSnapshot.model_validate(
        read_json_object(planning / "source-snapshot.json")
    )
    checks = (
        candidate.project_id == proof.certificate.scope.project_id,
        candidate.review_session_id == session_id,
        candidate_binding_digest(candidate)
        == proof.certificate.candidate_manifest_digest,
        snapshot.source_kind == "local-git-range",
        bool(snapshot.head_commit),
    )
    if not all(checks):
        raise ValueError("CI certificate candidate source is not exportable")
    return candidate, snapshot


def _persist_bundle(path: Path, bundle: CiStageCloseCertificateBundle) -> None:
    payload = bundle.model_dump(mode="json")
    if create_json_exclusive(path, payload):
        return
    current = CiStageCloseCertificateBundle.model_validate(read_json_object(path))
    if current != bundle:
        raise ValueError("CI certificate bundle already differs")


__all__ = ["export_latest_ci_certificate_bundle"]
