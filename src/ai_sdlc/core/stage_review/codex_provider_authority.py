"""从冻结 Reviewer Slot 重建 Codex Provider 描述符目录。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.binding_builders import (
    build_provider_binding_descriptor,
)
from ai_sdlc.core.stage_review.binding_models import ProviderBindingDescriptor
from ai_sdlc.core.stage_review.codex_provider_execution import (
    codex_reviewer_execution_route,
)
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerSlot
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderRecoveryCapabilities,
)

_PROVIDER_ID_PREFIX = "provider.openai-codex"


def _codex_provider_descriptors(
    slot: ReviewerSlot,
    release_digest: str,
) -> tuple[ProviderBindingDescriptor, ...]:
    return (
        build_provider_binding_descriptor(
            descriptor_id=f"descriptor.codex.{slot.slot_id}",
            provider_id=f"{_PROVIDER_ID_PREFIX}.{slot.slot_id}",
            equivalence_class_id="provider.openai-codex",
            model_family="model.openai-codex.default",
            role_contract_digests=(slot.role_contract_digest,),
            capability_ids=slot.capability_ids,
            provider_tags=slot.provider_constraints,
            tool_allowlist=slot.tool_permission_ids,
            recovery_capabilities=ProviderRecoveryCapabilities(
                idempotency_support=False,
                invocation_query_support=False,
                cost_metering_support=False,
            ),
            execution_route=codex_reviewer_execution_route(),
            isolation_backend="codex.permission-profile",
            network_enforcement=True,
            supported_independence_grade="session_independent",
            provider_policy_evidence_digest=release_digest,
        ),
    )


__all__: list[str] = []
