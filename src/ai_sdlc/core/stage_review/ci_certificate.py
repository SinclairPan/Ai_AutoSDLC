"""CI 对关闭证书、当前候选与 Git 祖先关系执行纯只读验证。"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Literal, Self

from pydantic import ConfigDict, field_validator, model_validator

from ai_sdlc.core.source_snapshot import (
    SourceSnapshot,
    SourceSnapshotOptions,
    build_source_snapshot,
)
from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.artifacts import read_json_object
from ai_sdlc.core.stage_review.candidate import (
    CandidateBuildContext,
    CandidateManifest,
    build_candidate_manifest,
    candidate_binding_digest,
)
from ai_sdlc.core.stage_review.certificate_input_guard import (
    certificate_matches_request,
)
from ai_sdlc.core.stage_review.certificate_models import (
    StageCloseCertificate,
    StageCloseCertificateRequest,
)
from ai_sdlc.core.stage_review.ci_certificate_evidence import (
    CiCertificateAuthorityEvidence,
    CiCertificateAuthorityProof,
)
from ai_sdlc.core.stage_review.close_models import CloseConsumptionClaim
from ai_sdlc.core.stage_review.close_recovery_models import StageCloseRecoveryDecision
from ai_sdlc.core.stage_review.source_binding import (
    _review_artifact_path_allowed as review_artifact_path_allowed,
)
from ai_sdlc.core.stage_review.source_binding import (
    candidate_source_binding,
)

_COMMIT = re.compile(r"[0-9a-f]{40}")


class CiCertificateVerificationError(ValueError):
    """证书包不能证明当前 CI Candidate。"""


class CiStageCloseCertificateBundle(ArtifactCompatibility):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ci-stage-close-certificate-bundle.v1"] = (
        "ci-stage-close-certificate-bundle.v1"
    )
    artifact_kind: Literal["ci-stage-close-certificate-bundle"] = (
        "ci-stage-close-certificate-bundle"
    )
    certificate: StageCloseCertificate
    certificate_request: StageCloseCertificateRequest
    authority_evidence: CiCertificateAuthorityEvidence
    candidate: CandidateManifest
    source_snapshot: SourceSnapshot
    reviewed_commit: str
    aborted_claim: CloseConsumptionClaim | None = None
    recovery_decision: StageCloseRecoveryDecision | None = None
    bundle_digest: str = ""

    @field_validator("reviewed_commit")
    @classmethod
    def _commit_is_canonical(cls, value: str) -> str:
        if _COMMIT.fullmatch(value) is None:
            raise ValueError("reviewed commit is invalid")
        return value

    @model_validator(mode="after")
    def _verify_bundle(self) -> Self:
        if (
            self.source_snapshot.source_kind != "local-git-range"
            or self.source_snapshot.head_commit != self.reviewed_commit
            or not self.source_snapshot.base_commit
        ):
            raise ValueError("CI certificate source commit lineage is invalid")
        return fill_artifact_digest(self, "bundle_digest")


class CiCertificateVerification(ArtifactCompatibility):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ci-certificate-verification.v1"] = (
        "ci-certificate-verification.v1"
    )
    artifact_kind: Literal["ci-certificate-verification"] = (
        "ci-certificate-verification"
    )
    valid: Literal[True] = True
    tested_commit: str
    reviewed_commit: str
    base_commit: str
    bundle_digest: str
    certificate_digest: str
    candidate_manifest_digest: str
    source_tree_digest: str
    stage_key: str
    close_kind: str
    activation_policy_digest: str
    mode: Literal["enforce"] = "enforce"
    checks: tuple[str, ...]
    verification_digest: str = ""

    @model_validator(mode="after")
    def _verify_result(self) -> Self:
        if self.checks != tuple(sorted(set(self.checks))) or not self.checks:
            raise ValueError("CI certificate checks must be canonical")
        return fill_artifact_digest(self, "verification_digest")


def build_ci_certificate_bundle(
    *,
    certificate: StageCloseCertificate,
    request: StageCloseCertificateRequest,
    authority_evidence: CiCertificateAuthorityEvidence,
    candidate: CandidateManifest,
    source_snapshot: SourceSnapshot,
    reviewed_commit: str,
    aborted_claim: CloseConsumptionClaim | None = None,
    recovery_decision: StageCloseRecoveryDecision | None = None,
) -> CiStageCloseCertificateBundle:
    return CiStageCloseCertificateBundle(
        certificate=certificate,
        certificate_request=request,
        authority_evidence=authority_evidence,
        aborted_claim=aborted_claim,
        recovery_decision=recovery_decision,
        candidate=candidate,
        source_snapshot=source_snapshot,
        reviewed_commit=reviewed_commit,
    )


def read_ci_certificate_bundle(path: Path) -> CiStageCloseCertificateBundle:
    try:
        return CiStageCloseCertificateBundle.model_validate(read_json_object(path))
    except (OSError, ValueError) as exc:
        raise CiCertificateVerificationError("CI certificate bundle is invalid") from exc


def verify_ci_certificate_bundle(
    root: Path,
    bundle: CiStageCloseCertificateBundle,
    *,
    tested_commit: str,
    expected_stage_key: str,
    expected_close_kind: str,
    expected_policy_digest: str,
    expected_mode: str,
) -> CiCertificateVerification:
    try:
        trusted = CiStageCloseCertificateBundle.model_validate(
            bundle.model_dump(mode="json")
        )
        _verify_commits(root, trusted, tested_commit)
        _verify_candidate(root, trusted)
        _verify_certificate(trusted)
        _verify_purpose(
            trusted,
            expected_stage_key=expected_stage_key,
            expected_close_kind=expected_close_kind,
            expected_policy_digest=expected_policy_digest,
            expected_mode=expected_mode,
        )
        _verify_post_review_changes(root, trusted, tested_commit)
    except CiCertificateVerificationError:
        raise
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise CiCertificateVerificationError(
            "CI certificate verification failed"
        ) from exc
    return _verification(
        trusted,
        tested_commit,
        expected_policy_digest=expected_policy_digest,
    )


def _verify_commits(
    root: Path,
    bundle: CiStageCloseCertificateBundle,
    tested_commit: str,
) -> None:
    if _COMMIT.fullmatch(tested_commit) is None:
        raise CiCertificateVerificationError("tested commit is invalid")
    if _git(root, "rev-parse", "HEAD") != tested_commit:
        raise CiCertificateVerificationError("tested commit is not checkout HEAD")
    base = bundle.source_snapshot.base_commit
    if not _is_ancestor(root, base, bundle.reviewed_commit):
        raise CiCertificateVerificationError("base commit is not reviewed ancestor")
    if not _is_ancestor(root, bundle.reviewed_commit, tested_commit):
        raise CiCertificateVerificationError("reviewed commit is not tested ancestor")


def _verify_candidate(root: Path, bundle: CiStageCloseCertificateBundle) -> None:
    candidate = bundle.candidate
    rebuilt_snapshot = build_source_snapshot(
        SourceSnapshotOptions(
            root=root,
            source_kind="local-git-range",
            base_ref=bundle.source_snapshot.base_commit,
            head_ref=bundle.reviewed_commit,
        )
    )
    expected = candidate_source_binding(
        rebuilt_snapshot,
        candidate.review_artifact_exclusion_set,
        candidate.protected_source_set,
        candidate.policy_digests,
    )
    lineage = (
        candidate.source_snapshot_digest == expected.snapshot_digest,
        candidate.source_tree_digest == expected.source_tree_digest,
        candidate.change_surface_digest == expected.change_surface_digest,
        candidate.change_surface == expected.change_surface,
    )
    rebuilt = build_candidate_manifest(
        root=root,
        source_snapshot=rebuilt_snapshot,
        context=_candidate_context(candidate),
    )
    if not all(lineage) or candidate_binding_digest(
        rebuilt
    ) != candidate_binding_digest(candidate):
        raise CiCertificateVerificationError("candidate source tree digest diverged")


def _candidate_context(candidate: CandidateManifest) -> CandidateBuildContext:
    return CandidateBuildContext(
        work_item_id=candidate.work_item_id,
        project_id=candidate.project_id,
        loop_id=candidate.loop_id,
        loop_round_number=candidate.loop_round_number,
        stage_key=candidate.stage_key,
        stage_instance_id=candidate.stage_instance_id,
        review_session_id=candidate.review_session_id,
        adapter_id=candidate.adapter_id,
        adapter_version=candidate.adapter_version,
        adapter_contract_digest=candidate.adapter_contract_digest,
        input_artifacts=tuple(candidate.input_artifacts),
        output_artifacts=tuple(candidate.output_artifacts),
        test_evidence_digests=tuple(candidate.test_evidence_digests),
        policy_digests=tuple(candidate.policy_digests),
        toolchain_ids=tuple(candidate.toolchain_ids),
        target_platform_ids=tuple(candidate.target_platform_ids),
        protected_source_set=tuple(candidate.protected_source_set),
        extensions=candidate.extensions,
    )


def _verify_certificate(bundle: CiStageCloseCertificateBundle) -> None:
    certificate = bundle.certificate
    request = bundle.certificate_request
    candidate = bundle.candidate
    scope = certificate.scope
    checks = (
        certificate.compatibility_mode == "strict",
        certificate_matches_request(certificate, request),
        certificate.candidate_manifest_digest == candidate_binding_digest(candidate),
        set(candidate.protected_source_set).issubset(certificate.protected_path_set),
        certificate.test_evidence_digest == request.evidence.test_evidence_digest,
        certificate.integrity_evidence_digest
        == request.evidence.integrity_evidence_digest,
        scope.project_id == candidate.project_id,
        scope.work_item_id == candidate.work_item_id,
        scope.stage_instance_id == candidate.stage_instance_id,
        scope.session_id == candidate.review_session_id,
        certificate.loop_id == candidate.loop_id,
        certificate.loop_round_number == candidate.loop_round_number,
    )
    if not all(checks):
        raise CiCertificateVerificationError("certificate candidate lineage diverged")
    CiCertificateAuthorityProof(
        certificate=certificate,
        certificate_request=request,
        authority_evidence=bundle.authority_evidence,
        aborted_claim=bundle.aborted_claim,
        recovery_decision=bundle.recovery_decision,
    )


def _verify_purpose(
    bundle: CiStageCloseCertificateBundle,
    *,
    expected_stage_key: str,
    expected_close_kind: str,
    expected_policy_digest: str,
    expected_mode: str,
) -> None:
    checks = (
        expected_mode == "enforce",
        bundle.candidate.stage_key == expected_stage_key,
        bundle.certificate.close_kind == expected_close_kind,
        bundle.certificate_request.intent.close_kind == expected_close_kind,
        tuple(bundle.candidate.policy_digests) == (expected_policy_digest,),
    )
    if not all(checks):
        raise CiCertificateVerificationError("certificate purpose diverged")


def _verify_post_review_changes(
    root: Path,
    bundle: CiStageCloseCertificateBundle,
    tested_commit: str,
) -> None:
    output = _git_bytes(
        root,
        "diff",
        "--name-only",
        "--no-renames",
        "-z",
        bundle.reviewed_commit,
        tested_commit,
    )
    paths = tuple(item.decode("utf-8") for item in output.split(b"\0") if item)
    exclusion = bundle.candidate.review_artifact_exclusion_set[0]
    if any(not review_artifact_path_allowed(path, exclusion) for path in paths):
        raise CiCertificateVerificationError("post-review protected change detected")


def _verification(
    bundle: CiStageCloseCertificateBundle,
    tested_commit: str,
    *,
    expected_policy_digest: str,
) -> CiCertificateVerification:
    candidate_digest = candidate_binding_digest(bundle.candidate)
    return CiCertificateVerification(
        tested_commit=tested_commit,
        reviewed_commit=bundle.reviewed_commit,
        base_commit=bundle.source_snapshot.base_commit,
        bundle_digest=bundle.bundle_digest,
        certificate_digest=bundle.certificate.certificate_digest,
        candidate_manifest_digest=candidate_digest,
        source_tree_digest=bundle.candidate.source_tree_digest,
        stage_key=bundle.candidate.stage_key,
        close_kind=bundle.certificate.close_kind,
        activation_policy_digest=expected_policy_digest,
        checks=tuple(
            sorted(
                {
                    "candidate-binding",
                    "activation-policy",
                    "certificate-artifact-digest",
                    "certificate-purpose",
                    "certificate-request-lineage",
                    "commit-ancestry",
                    "gate-mode",
                    "post-review-change-boundary",
                    "source-tree-digest",
                }
            )
        ),
    )


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if result.returncode not in {0, 1}:
        raise subprocess.CalledProcessError(result.returncode, result.args)
    return result.returncode == 0


def _git(root: Path, *args: str) -> str:
    return _git_bytes(root, *args).decode("utf-8").strip()


def _git_bytes(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout


__all__ = [
    "CiCertificateVerification", "CiCertificateVerificationError",
    "CiStageCloseCertificateBundle", "build_ci_certificate_bundle",
    "read_ci_certificate_bundle", "verify_ci_certificate_bundle",
]
