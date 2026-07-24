"""在仓库中保存可由干净 CI 读取的受保护 Activation Policy 锚点。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Literal

from ai_sdlc.core.stage_review.activation_artifact_codec import (
    decode_activation_policy,
)
from ai_sdlc.core.stage_review.activation_models import (
    ACTIVATION_RISKS,
    ACTIVATION_STAGES,
    StageGateActivationPolicy,
)
from ai_sdlc.core.stage_review.artifacts import atomic_write_json, read_json_object

ACTIVATION_POLICY_ANCHOR = Path(
    ".ai-sdlc/policies/stage-gate-activation-policy.json"
)
ActivationPolicySource = Literal[
    "bundled-baseline",
    "protected-base-anchor",
    "protected-candidate-anchor",
]


def read_activation_policy_anchor(
    root: Path,
) -> StageGateActivationPolicy | None:
    path = root.resolve() / ACTIVATION_POLICY_ANCHOR
    if not path.is_file():
        return None
    return _validated_policy(read_json_object(path))


def write_activation_policy_anchor(
    root: Path,
    policy: StageGateActivationPolicy,
) -> Path:
    trusted = _validated_policy(policy.model_dump(mode="json"))
    path = root.resolve() / ACTIVATION_POLICY_ANCHOR
    atomic_write_json(path, trusted.model_dump(mode="json"))
    return path


def select_local_activation_policy(
    pointer_policy: StageGateActivationPolicy | None,
    anchor_policy: StageGateActivationPolicy | None,
    baseline: StageGateActivationPolicy,
) -> StageGateActivationPolicy:
    if pointer_policy is None:
        return anchor_policy or baseline
    if anchor_policy is None or pointer_policy.policy_digest == anchor_policy.policy_digest:
        return pointer_policy
    return _newer_policy(pointer_policy, anchor_policy)


def read_ci_activation_policy(
    root: Path,
    base_commit: str,
    baseline: StageGateActivationPolicy,
) -> tuple[StageGateActivationPolicy, ActivationPolicySource]:
    base = _read_git_anchor(root, base_commit)
    candidate = read_activation_policy_anchor(root)
    if candidate is None:
        if base is None:
            raise ValueError("protected activation policy anchor is missing")
        return base, "protected-base-anchor"
    if base is None:
        _require_not_weaker(candidate, baseline)
    else:
        _require_not_weaker(candidate, base)
    return candidate, "protected-candidate-anchor"


def _newer_policy(
    first: StageGateActivationPolicy,
    second: StageGateActivationPolicy,
) -> StageGateActivationPolicy:
    if first.active_phase == second.active_phase:
        if _is_strict_schema_upgrade(first, second):
            return first
        if _is_strict_schema_upgrade(second, first):
            return second
        raise ValueError("activation policy anchor and pointer diverged")
    newer, older = (
        (first, second)
        if first.active_phase > second.active_phase
        else (second, first)
    )
    _require_not_weaker(newer, older)
    return newer


def _require_not_weaker(
    candidate: StageGateActivationPolicy,
    base: StageGateActivationPolicy,
) -> None:
    if candidate.policy_digest == base.policy_digest:
        return
    if candidate.active_phase < base.active_phase:
        raise ValueError("activation policy candidate downgrades protected base")
    if candidate.active_phase == base.active_phase:
        if _is_strict_schema_upgrade(candidate, base):
            return
        raise ValueError("activation policy changed without a phase transition")
    if not set(base.enabled_stages).issubset(candidate.enabled_stages):
        raise ValueError("activation policy candidate removes an enabled stage")
    if not set(base.enabled_risk_levels).issubset(candidate.enabled_risk_levels):
        raise ValueError("activation policy candidate removes an enabled risk")
    trust_root = (
        candidate.trusted_evidence_workflow_paths
        == base.trusted_evidence_workflow_paths
        and candidate.evidence_predicate_type == base.evidence_predicate_type
        and candidate.evidence_purpose == base.evidence_purpose
    )
    if not trust_root:
        raise ValueError("activation policy candidate changes the evidence trust root")


def _is_strict_schema_upgrade(
    candidate: StageGateActivationPolicy,
    base: StageGateActivationPolicy,
) -> bool:
    if (
        base.compatibility_mode != "read-only-legacy"
        or candidate.compatibility_mode != "strict"
    ):
        return False
    excluded = {"compatibility_mode", "extensions", "policy_digest"}
    return candidate.model_dump(mode="json", exclude=excluded) == base.model_dump(
        mode="json",
        exclude=excluded,
    )


def _validated_policy(payload: object) -> StageGateActivationPolicy:
    policy = decode_activation_policy(payload)
    expected_risks = () if policy.active_phase == 1 else (
        ("low",) if policy.active_phase == 2 else ACTIVATION_RISKS
    )
    if policy.enabled_stages != tuple(sorted(ACTIVATION_STAGES)):
        raise ValueError("activation policy stage coverage is not canonical")
    if policy.enabled_risk_levels != tuple(sorted(expected_risks)):
        raise ValueError("activation policy risk coverage is not canonical")
    return policy


def _read_git_anchor(
    root: Path,
    commit: str,
) -> StageGateActivationPolicy | None:
    result = subprocess.run(
        ["git", "show", f"{commit}:{ACTIVATION_POLICY_ANCHOR.as_posix()}"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        return None
    try:
        return _validated_policy(json.loads(result.stdout))
    except json.JSONDecodeError as exc:
        raise ValueError("protected activation policy anchor is invalid") from exc


__all__ = [
    "ACTIVATION_POLICY_ANCHOR",
    "read_activation_policy_anchor",
    "read_ci_activation_policy",
    "select_local_activation_policy",
    "write_activation_policy_anchor",
]
