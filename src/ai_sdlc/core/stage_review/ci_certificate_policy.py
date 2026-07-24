"""CI 对当前 PR 的证书适用性执行独立、只读、防降级重算。"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

from pydantic import ConfigDict, model_validator

from ai_sdlc.core.source_snapshot import (
    SourceSnapshot,
    SourceSnapshotOptions,
    build_source_snapshot,
)
from ai_sdlc.core.stage_review.activation import (
    StageGateActivationPolicy,
    baseline_activation_policy,
    resolve_gate_applicability,
)
from ai_sdlc.core.stage_review.activation_policy_anchor import (
    ActivationPolicySource,
    read_ci_activation_policy,
)
from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.close_gate_models import GateApplicabilityDecision
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.risk_extractor import (
    _classify_change_surface_risk as classify_change_surface_risk,
)

_COMMIT = re.compile(r"[0-9a-f]{40}")


class CiCertificatePolicyError(ValueError):
    """CI 无法可信重算当前 Candidate 的证书要求。"""


@dataclass(frozen=True, slots=True)
class _PolicyContext:
    snapshot: SourceSnapshot
    risk: Literal["low", "medium", "high", "critical"]
    policy: StageGateActivationPolicy
    source: ActivationPolicySource
    decision: GateApplicabilityDecision


class CiCertificatePolicyVerification(ArtifactCompatibility):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ci-certificate-policy-verification.v1"] = (
        "ci-certificate-policy-verification.v1"
    )
    artifact_kind: Literal["ci-certificate-policy-verification"] = (
        "ci-certificate-policy-verification"
    )
    valid: Literal[True] = True
    stage_key: Literal["local-pr-review"] = "local-pr-review"
    risk_level: Literal["low", "medium", "high", "critical"]
    mode: Literal["shadow", "enforce"]
    certificate_required: bool
    reason_code: str
    policy_id: str
    policy_version: str
    policy_digest: str
    policy_phase: int
    policy_source: ActivationPolicySource
    base_commit: str
    tested_commit: str
    source_diff_hash: str
    checks: tuple[str, ...]
    verification_digest: str = ""

    @model_validator(mode="after")
    def _verify_result(self) -> Self:
        if self.certificate_required != (self.mode == "enforce"):
            raise ValueError("CI certificate requirement contradicts mode")
        if self.checks != tuple(sorted(set(self.checks))) or not self.checks:
            raise ValueError("CI certificate policy checks must be canonical")
        return fill_artifact_digest(self, "verification_digest")


def verify_ci_certificate_policy(
    root: Path,
    *,
    base_commit: str,
    tested_commit: str,
) -> CiCertificatePolicyVerification:
    try:
        context = _build_policy_context(root, base_commit, tested_commit)
    except CiCertificatePolicyError:
        raise
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise CiCertificatePolicyError(
            f"CI certificate policy verification failed: {exc}"
        ) from exc
    return _verification(context, base_commit, tested_commit)


def _build_policy_context(
    root: Path,
    base_commit: str,
    tested_commit: str,
) -> _PolicyContext:
    _verify_commits(root, base_commit, tested_commit)
    snapshot = build_source_snapshot(
        SourceSnapshotOptions(
            root=root,
            source_kind="local-git-range",
            base_ref=base_commit,
            head_ref=tested_commit,
        )
    )
    risk = classify_change_surface_risk("local-pr-review", snapshot.changed_files)
    policy, source = read_ci_activation_policy(
        root,
        base_commit,
        baseline_activation_policy(),
    )
    decision = resolve_gate_applicability(
        policy=policy,
        stage_key="local-pr-review",
        risk_level=risk,
        loop_id=stable_id("ci-candidate", tested_commit),
        loop_created_at=policy.effective_at,
        gate_contract_version=policy.gate_contract_version,
    )
    return _PolicyContext(snapshot, risk, policy, source, decision)


def _verification(
    context: _PolicyContext,
    base_commit: str,
    tested_commit: str,
) -> CiCertificatePolicyVerification:
    mode = context.decision.mode
    if mode == "grandfathered":
        raise CiCertificatePolicyError("current CI Candidate cannot be grandfathered")
    return CiCertificatePolicyVerification(
        risk_level=context.risk,
        mode=mode,
        certificate_required=mode == "enforce",
        reason_code=context.decision.reason_code,
        policy_id=context.policy.policy_id,
        policy_version=context.policy.policy_version,
        policy_digest=context.policy.policy_digest,
        policy_phase=context.policy.active_phase,
        policy_source=context.source,
        base_commit=base_commit,
        tested_commit=tested_commit,
        source_diff_hash=context.snapshot.diff_hash,
        checks=tuple(
            sorted(
                {
                    "activation-policy-anti-downgrade",
                    "commit-ancestry",
                    "current-candidate-risk",
                    "protected-policy-anchor",
                }
            )
        ),
    )


def _verify_commits(root: Path, base_commit: str, tested_commit: str) -> None:
    if _COMMIT.fullmatch(base_commit) is None or _COMMIT.fullmatch(tested_commit) is None:
        raise CiCertificatePolicyError("CI certificate policy commit is invalid")
    if _git(root, "rev-parse", "HEAD") != tested_commit:
        raise CiCertificatePolicyError("tested commit is not checkout HEAD")
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", base_commit, tested_commit],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise CiCertificatePolicyError("base commit is not tested ancestor")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


__all__ = [
    "CiCertificatePolicyError",
    "CiCertificatePolicyVerification",
    "verify_ci_certificate_policy",
]
