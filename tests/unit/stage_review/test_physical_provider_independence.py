from ai_sdlc.core.stage_review.binding_result_builders import (
    build_independence_proofs,
)
from ai_sdlc.core.stage_review.binding_result_models import ReviewerBinding


def test_independence_uses_physical_provider_equivalence() -> None:
    left = ReviewerBinding.model_construct(
        slot_id="reviewer-a",
        provider_id="provider.logical-a",
        model_family="model.logical-a",
        physical_provider_id="provider.openai-codex",
        physical_equivalence_class_id="provider.openai-codex",
        supported_independence_grade="model_diversity_proven",
    )
    right = ReviewerBinding.model_construct(
        slot_id="reviewer-b",
        provider_id="provider.logical-b",
        model_family="model.logical-b",
        physical_provider_id="provider.openai-codex",
        physical_equivalence_class_id="provider.openai-codex",
        supported_independence_grade="model_diversity_proven",
    )

    proof = build_independence_proofs((left, right))[0]

    assert proof.independence_grade == "session_independent"
    assert proof.reason_id == "binding.independence.physical-session-only"
