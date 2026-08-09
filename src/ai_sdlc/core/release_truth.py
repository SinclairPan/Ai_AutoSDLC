"""Permanent Release Truth 的 Proof 构建与 Publish CAS 校验。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from ai_sdlc.core.release_truth_models import (
    PublishedReleaseSnapshot,
    ReleaseAssetBinding,
    ReleaseCandidateSnapshot,
    ReleaseCertificate,
    ReleaseRevocationReceipt,
    ReleaseSatisfactionProof,
    ReleaseTrustDecision,
    RequiredGateBinding,
    RevocationSignal,
)

RELEASE_TRUTH_FRESHNESS_TTL = timedelta(minutes=15)
MAX_REVOCATION_GENERATION_DIGITS = 19


class ReleaseTruthError(ValueError):
    """Release Truth 输入无法证明安全发布时的统一 fail-closed 错误。"""


def parse_canonical_revocation_generation(tag: str, prefix: str) -> int | None:
    """仅接受固定成本的 ASCII 正十进制 generation tag。"""

    if not tag.startswith(prefix):
        return None
    generation = tag.removeprefix(prefix)
    if (
        not generation.isascii()
        or not generation.isdigit()
        or generation.startswith("0")
        or len(generation) > MAX_REVOCATION_GENERATION_DIGITS
    ):
        return None
    return int(generation)


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")


def _is_canonical(values: tuple[object, ...], key) -> bool:  # noqa: ANN001
    keys = tuple(key(value) for value in values)
    return bool(keys) and keys == tuple(sorted(set(keys)))


def _validate_gate(
    gate: RequiredGateBinding,
    snapshot: ReleaseCandidateSnapshot,
) -> None:
    if gate.conclusion != "success":
        raise ReleaseTruthError("required gate must be successful")
    if not gate.required:
        raise ReleaseTruthError("gate is not a required check")
    if not gate.protected:
        raise ReleaseTruthError("required gate must come from a protected workflow")
    if gate.authority_repository != snapshot.repository:
        raise ReleaseTruthError("required gate authority repository differs")
    expected_prefix = f"{snapshot.repository}/.github/workflows/"
    if not gate.workflow_ref.startswith(
        expected_prefix
    ) or not gate.workflow_ref.endswith("@refs/heads/main"):
        raise ReleaseTruthError("required gate is not bound to protected mainline")
    if gate.head_sha != snapshot.commit_sha:
        raise ReleaseTruthError("required gate head SHA differs")
    if gate.workflow_run_id != snapshot.workflow_run_id:
        raise ReleaseTruthError("required gate workflow run differs")
    if gate.workflow_run_attempt != snapshot.workflow_run_attempt:
        raise ReleaseTruthError("required gate run attempt differs")
    cutoff = _parse_utc(snapshot.evidence_cutoff_at)
    if _parse_utc(gate.completed_at) > cutoff or cutoff > _parse_utc(gate.valid_until):
        raise ReleaseTruthError("required gate evidence is stale")


def _validate_assets(
    expected: tuple[ReleaseAssetBinding, ...],
    actual: tuple[ReleaseAssetBinding, ...],
) -> None:
    if not _is_canonical(expected, lambda value: value.name) or not _is_canonical(
        actual, lambda value: value.name
    ):
        raise ReleaseTruthError("release asset collection is not canonical")
    if expected != actual:
        raise ReleaseTruthError("release asset set or digest differs")


def build_release_satisfaction_proof(
    snapshot: ReleaseCandidateSnapshot,
) -> ReleaseSatisfactionProof:
    """从同一 run attempt 的冻结候选快照生成唯一 Proof。"""

    if not snapshot.draft:
        raise ReleaseTruthError("candidate release must remain draft")
    if not _is_canonical(snapshot.required_gate_names, lambda value: value):
        raise ReleaseTruthError("required gate name collection is not canonical")
    if not _is_canonical(snapshot.required_gates, lambda value: value.name):
        raise ReleaseTruthError("required gate collection is not canonical")
    if (
        tuple(gate.name for gate in snapshot.required_gates)
        != snapshot.required_gate_names
    ):
        raise ReleaseTruthError("required gate set differs from policy")
    for gate in snapshot.required_gates:
        _validate_gate(gate, snapshot)
    _validate_assets(snapshot.expected_assets, snapshot.assets)
    return ReleaseSatisfactionProof(
        repository=snapshot.repository,
        admission_id=snapshot.admission_id,
        admission_digest=snapshot.admission_digest,
        draft_release_id=snapshot.draft_release_id,
        upload_url=snapshot.upload_url,
        release_user_agent=snapshot.release_user_agent,
        draft_release_updated_at=snapshot.draft_release_updated_at,
        tag_name=snapshot.tag_name,
        tag_object_sha=snapshot.tag_object_sha,
        commit_sha=snapshot.commit_sha,
        tree_sha=snapshot.tree_sha,
        required_policy_digest=snapshot.required_policy_digest,
        required_gates=snapshot.required_gates,
        workflow_run_id=snapshot.workflow_run_id,
        workflow_run_attempt=snapshot.workflow_run_attempt,
        assets=snapshot.assets,
        release_settings_digest=snapshot.release_settings_digest,
        publish_workflow_ref=snapshot.publish_workflow_ref,
        evidence_cutoff_at=snapshot.evidence_cutoff_at,
    )


def validate_publish_claim(
    proof: ReleaseSatisfactionProof,
    current: ReleaseCandidateSnapshot,
    *,
    caller_workflow_ref: str,
    caller_run_id: int,
    caller_run_attempt: int,
    observed_at: str,
) -> None:
    """在 Publish 线性化点前重建 Proof，并拒绝任一身份或调用者漂移。"""

    caller = (caller_workflow_ref, caller_run_id, caller_run_attempt)
    expected_caller = (
        proof.publish_workflow_ref,
        proof.workflow_run_id,
        proof.workflow_run_attempt,
    )
    if caller != expected_caller:
        raise ReleaseTruthError("publish caller is not bound to proof")
    if not observed_at.endswith("Z"):
        raise ReleaseTruthError("publish observation must use canonical UTC")
    observation = _parse_utc(observed_at)
    if observation < _parse_utc(proof.evidence_cutoff_at):
        raise ReleaseTruthError("publish observation predates frozen evidence cutoff")
    for gate in proof.required_gates:
        if observation < _parse_utc(gate.completed_at):
            raise ReleaseTruthError("publish observation predates required gate")
        if observation > _parse_utc(gate.valid_until):
            raise ReleaseTruthError("required gate expired before publish")
    rebuilt = build_release_satisfaction_proof(current)
    if rebuilt != proof:
        raise ReleaseTruthError("release proof identity differs from current candidate")


def build_release_certificate(
    proof: ReleaseSatisfactionProof,
    published: PublishedReleaseSnapshot,
    *,
    release_attestation_digest: str,
    issued_at: str,
) -> ReleaseCertificate:
    """验证 GitHub 权威、不可变保护和最终资产后生成唯一 Certificate。"""

    try:
        proof = ReleaseSatisfactionProof.model_validate(proof.model_dump(mode="json"))
    except (ValidationError, ValueError) as exc:
        raise ReleaseTruthError("release proof is invalid") from exc
    if not published.published or published.draft:
        raise ReleaseTruthError("release is not in published state")
    if not published.immutable:
        raise ReleaseTruthError("published release is not immutable")
    if (
        not published.release_attestation_verified
        or published.release_attestation_digest != release_attestation_digest
    ):
        raise ReleaseTruthError("release attestation is not verified")
    if published.revocation_generation != 0:
        raise ReleaseTruthError("certificate generation condition differs")
    proof_identity = (
        proof.repository,
        proof.draft_release_id,
        proof.tag_name,
        proof.commit_sha,
        proof.tree_sha,
    )
    published_identity = (
        published.repository,
        published.github_release_id,
        published.tag_name,
        published.commit_sha,
        published.tree_sha,
    )
    if published_identity != proof_identity:
        raise ReleaseTruthError("published release identity differs from proof")
    _validate_assets(proof.assets, published.assets)
    return ReleaseCertificate(
        repository=published.repository,
        admission_id=proof.admission_id,
        admission_digest=proof.admission_digest,
        github_release_id=published.github_release_id,
        upload_url=proof.upload_url,
        release_user_agent=proof.release_user_agent,
        github_release_url=published.github_release_url,
        tag_name=published.tag_name,
        tag_object_sha=proof.tag_object_sha,
        commit_sha=published.commit_sha,
        tree_sha=published.tree_sha,
        workflow_run_id=proof.workflow_run_id,
        workflow_run_attempt=proof.workflow_run_attempt,
        proof_digest=proof.proof_digest,
        release_attestation_digest=release_attestation_digest,
        assets=published.assets,
        issued_at=issued_at,
    )


def build_revocation_receipt(
    certificate: ReleaseCertificate,
    latest: ReleaseRevocationReceipt | None,
    signal: RevocationSignal,
    *,
    expected_generation: int,
) -> ReleaseRevocationReceipt:
    """以 latest generation 为 CAS 前置条件追加唯一 Receipt。"""

    try:
        certificate = ReleaseCertificate.model_validate(
            certificate.model_dump(mode="json")
        )
    except (ValidationError, ValueError) as exc:
        raise ReleaseTruthError("release certificate is invalid") from exc
    if latest is None:
        next_generation = 1
        predecessor_digest = ""
    else:
        if latest.certificate_digest != certificate.certificate_digest:
            raise ReleaseTruthError("latest receipt belongs to another certificate")
        if (
            latest.repository != certificate.repository
            or latest.tag_name != certificate.tag_name
        ):
            raise ReleaseTruthError("latest receipt release identity differs")
        try:
            latest = ReleaseRevocationReceipt.model_validate(
                latest.model_dump(mode="json")
            )
        except (ValidationError, ValueError) as exc:
            raise ReleaseTruthError("latest receipt is invalid") from exc
        next_generation = latest.generation + 1
        predecessor_digest = latest.receipt_digest
    if expected_generation != next_generation:
        raise ReleaseTruthError("revocation generation CAS differs")
    if _parse_utc(signal.observed_at) < _parse_utc(certificate.issued_at):
        raise ReleaseTruthError("revocation signal predates certificate")
    return ReleaseRevocationReceipt(
        repository=certificate.repository,
        tag_name=certificate.tag_name,
        certificate_digest=certificate.certificate_digest,
        generation=next_generation,
        predecessor_receipt_digest=predecessor_digest,
        reason_code=signal.reason_code,
        evidence_digest=signal.evidence_digest,
        work_item_id=signal.work_item_id,
        observed_at=signal.observed_at,
    )


def _decision(
    status: str,
    reason_code: str,
    observed_at: str,
    *,
    certificate_digest: str = "",
    revocation_generation: int = 0,
) -> ReleaseTrustDecision:
    return ReleaseTrustDecision(
        status=status,
        reason_code=reason_code,
        certificate_digest=certificate_digest,
        revocation_generation=revocation_generation,
        observed_at=observed_at,
    )


def _certificate_matches_release(
    published: PublishedReleaseSnapshot,
    certificate: ReleaseCertificate,
) -> bool:
    return (
        certificate.repository == published.repository
        and certificate.github_release_id == published.github_release_id
        and certificate.github_release_url == published.github_release_url
        and certificate.tag_name == published.tag_name
        and certificate.commit_sha == published.commit_sha
        and certificate.tree_sha == published.tree_sha
        and certificate.release_attestation_digest
        == published.release_attestation_digest
        and certificate.assets == published.assets
        and certificate.immutable
        and certificate.revocation_generation == 0
    )


def _receipt_chain_is_valid(
    published: PublishedReleaseSnapshot,
    certificate: ReleaseCertificate,
    receipts: tuple[ReleaseRevocationReceipt, ...],
) -> bool:
    if len(receipts) != published.revocation_generation:
        return False
    by_generation: dict[int, ReleaseRevocationReceipt] = {}
    for receipt in receipts:
        try:
            trusted = ReleaseRevocationReceipt.model_validate(
                receipt.model_dump(mode="json")
            )
        except (ValidationError, ValueError):
            return False
        if trusted.generation in by_generation:
            return False
        by_generation[trusted.generation] = trusted
    predecessor = ""
    for generation in range(1, published.revocation_generation + 1):
        receipt = by_generation.get(generation)
        if receipt is None:
            return False
        if (
            receipt.repository != published.repository
            or receipt.tag_name != published.tag_name
            or receipt.certificate_digest != certificate.certificate_digest
            or receipt.predecessor_receipt_digest != predecessor
        ):
            return False
        predecessor = receipt.receipt_digest
    return True


def evaluate_release_trust(
    published: PublishedReleaseSnapshot,
    certificate: ReleaseCertificate | None,
    receipts: tuple[ReleaseRevocationReceipt, ...],
    *,
    observed_at: str,
    now: datetime,
) -> ReleaseTrustDecision:
    """每次从 GitHub、Certificate 与完整 Receipt 链重建当前推荐资格。"""

    observed = _parse_utc(observed_at)
    if now.tzinfo is None:
        return _decision("unknown", "invalid_clock", observed_at)
    age = now.astimezone(UTC) - observed
    if age < timedelta(0) or age > RELEASE_TRUTH_FRESHNESS_TTL:
        return _decision("unknown", "stale_projection", observed_at)
    if (
        not published.published
        or published.draft
        or not published.immutable
        or not published.release_attestation_verified
    ):
        return _decision("unknown", "release_authority_unverified", observed_at)
    if certificate is None:
        return _decision("untrusted", "certificate_missing", observed_at)
    try:
        trusted_certificate = ReleaseCertificate.model_validate(
            certificate.model_dump(mode="json")
        )
    except (ValidationError, ValueError):
        return _decision("unknown", "certificate_mismatch", observed_at)
    if not _certificate_matches_release(published, trusted_certificate):
        return _decision("unknown", "certificate_mismatch", observed_at)
    if not _receipt_chain_is_valid(published, trusted_certificate, receipts):
        return _decision(
            "unknown",
            "receipt_chain_invalid",
            observed_at,
            certificate_digest=trusted_certificate.certificate_digest,
        )
    if receipts:
        return _decision(
            "untrusted",
            "revoked",
            observed_at,
            certificate_digest=trusted_certificate.certificate_digest,
            revocation_generation=published.revocation_generation,
        )
    return _decision(
        "trusted",
        "certificate_current",
        observed_at,
        certificate_digest=trusted_certificate.certificate_digest,
    )


__all__ = [
    "MAX_REVOCATION_GENERATION_DIGITS",
    "ReleaseTruthError",
    "build_release_certificate",
    "build_revocation_receipt",
    "build_release_satisfaction_proof",
    "evaluate_release_trust",
    "parse_canonical_revocation_generation",
    "validate_publish_claim",
]
