"""Lean 例外所需的独立 reviewer 决策模型。"""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, model_validator

from ai_sdlc.core.loop_models import LoopArtifactModel


def _reviewer_decision_payload_digest(
    diff_hash: str,
    policy_digest: str,
    evaluation_digest: str,
    decisions: list[LeanReviewerFindingDecision] | list[dict[str, object]],
) -> str:
    normalized = [
        item.model_dump(mode="json")
        if isinstance(item, LeanReviewerFindingDecision)
        else item
        for item in decisions
    ]
    payload = {
        "diff_hash": diff_hash,
        "policy_digest": policy_digest,
        "evaluation_digest": evaluation_digest,
        "decisions": normalized,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(encoded.encode()).hexdigest()}"


class LeanReviewerFindingDecision(BaseModel):
    """One reviewer's evidence-bound decision for one exact Lean finding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stable_signature: str
    rule_id: str
    path: str
    symbol: str = ""
    verdict: str
    rationale: str
    contract_kind: str
    contract_path: str
    contract_digest: str
    contract_symbol: str
    exact_locators: list[str]
    exact_locator_digests: dict[str, str]
    verification_evidence_refs: list[str]
    verification_evidence_digests: dict[str, str]

    @model_validator(mode="after")
    def _require_semantic_evidence(self) -> LeanReviewerFindingDecision:
        required = (
            self.stable_signature,
            self.rule_id,
            self.path,
            self.verdict,
            self.rationale,
            self.contract_kind,
            self.contract_path,
            self.contract_digest,
            self.contract_symbol,
        )
        if not all(item.strip() for item in required):
            raise ValueError("Lean reviewer decision is incomplete")
        if self.verdict not in {"approved", "rejected"}:
            raise ValueError("Lean reviewer verdict is invalid")
        if not self.exact_locators or not self.verification_evidence_refs:
            raise ValueError("Lean reviewer semantic evidence is missing")
        if set(self.exact_locators) != set(self.exact_locator_digests):
            raise ValueError("Lean reviewer exact locator evidence is incomplete")
        if set(self.verification_evidence_refs) != set(
            self.verification_evidence_digests
        ):
            raise ValueError("Lean reviewer verification evidence is incomplete")
        return self


class LeanReviewerDecisionArtifact(LoopArtifactModel):
    """Independent reviewer decisions bound to one frozen Lean evaluation."""

    artifact_kind: str = "lean-reviewer-decision"
    decision_id: str
    reviewer_id: str
    reviewer_role: str
    review_project_id: str
    review_work_item_id: str
    review_stage_instance_id: str
    review_session_id: str
    review_pass_id: str
    review_pass_digest: str
    review_assignment_digest: str
    decision_payload_digest: str
    diff_hash: str
    policy_digest: str
    evaluation_digest: str
    decisions: list[LeanReviewerFindingDecision]

    @model_validator(mode="after")
    def _require_independent_decisions(self) -> LeanReviewerDecisionArtifact:
        required = (
            self.decision_id,
            self.reviewer_id,
            self.reviewer_role,
            self.review_project_id,
            self.review_work_item_id,
            self.review_stage_instance_id,
            self.review_session_id,
            self.review_pass_id,
            self.review_pass_digest,
            self.review_assignment_digest,
            self.decision_payload_digest,
            self.diff_hash,
            self.policy_digest,
            self.evaluation_digest,
        )
        if not all(item.strip() for item in required) or not self.decisions:
            raise ValueError("Lean reviewer decision artifact is incomplete")
        signatures = [item.stable_signature for item in self.decisions]
        if len(signatures) != len(set(signatures)):
            raise ValueError("Lean reviewer decisions must be unique by signature")
        expected = _reviewer_decision_payload_digest(
            self.diff_hash,
            self.policy_digest,
            self.evaluation_digest,
            self.decisions,
        )
        if self.decision_payload_digest != expected:
            raise ValueError("Lean reviewer decision payload digest is invalid")
        return self


__all__ = [
    "LeanReviewerDecisionArtifact",
    "LeanReviewerFindingDecision",
]
