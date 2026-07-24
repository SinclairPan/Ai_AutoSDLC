from __future__ import annotations

import pytest

from ai_sdlc.core.stage_review.binding_policy import BindingPolicy
from ai_sdlc.core.stage_review.binding_policy_validation import (
    validate_binding_independence,
)
from ai_sdlc.core.stage_review.binding_result_models import (
    BindingIndependenceProof,
    ReviewerBinding,
)
from ai_sdlc.core.stage_review.binding_validation import BindingRefusal


def test_binding_policy_rejects_required_pair_below_minimum_grade() -> None:
    policy = BindingPolicy(
        version="1.0.0",
        require_independent_blocking_slots=True,
        minimum_blocking_independence_grade="provider_independent",
    )
    proof = BindingIndependenceProof(
        left_slot_id="slot.delivery",
        right_slot_id="slot.evolution",
        independence_grade="session_independent",
        reason_id="binding.independence.session-only",
    )
    bindings = (
        _binding("slot.delivery", "physical.delivery", "model.shared"),
        _binding("slot.evolution", "physical.evolution", "model.shared"),
    )

    with pytest.raises(BindingRefusal, match="binding.policy-independence-unproven"):
        validate_binding_independence(
            required_slot_ids=("slot.delivery", "slot.evolution"),
            bindings=bindings,
            proofs=(proof,),
            policy=policy,
        )


def test_binding_policy_accepts_complete_pairwise_provider_independence() -> None:
    policy = BindingPolicy(
        version="1.0.0",
        require_independent_blocking_slots=True,
        minimum_blocking_independence_grade="provider_independent",
    )
    proof = BindingIndependenceProof(
        left_slot_id="slot.delivery",
        right_slot_id="slot.evolution",
        independence_grade="provider_independent",
        reason_id="binding.independence.model-diversity",
    )
    bindings = (
        _binding(
            "slot.delivery",
            "physical.delivery",
            "model.delivery",
            supported="provider_independent",
        ),
        _binding("slot.evolution", "physical.evolution", "model.evolution"),
    )

    validate_binding_independence(
        required_slot_ids=("slot.delivery", "slot.evolution"),
        bindings=bindings,
        proofs=(proof,),
        policy=policy,
    )


def _binding(
    slot_id: str,
    physical_provider_id: str,
    model_family: str,
    *,
    supported: str = "model_diversity_proven",
) -> ReviewerBinding:
    return ReviewerBinding.model_construct(
        slot_id=slot_id,
        physical_provider_id=physical_provider_id,
        physical_equivalence_class_id=physical_provider_id,
        model_family=model_family,
        supported_independence_grade=supported,
    )
