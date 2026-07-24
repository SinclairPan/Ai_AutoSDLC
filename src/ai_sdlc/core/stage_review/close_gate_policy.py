"""Stage Close 默认使用受保护的版本化激活策略重算适用性。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.activation import (
    resolve_gate_applicability,
)
from ai_sdlc.core.stage_review.activation_policy_store import (
    current_activation_policy,
)
from ai_sdlc.core.stage_review.close_gate_models import (
    GateApplicabilityDecision,
    PreparedStageClose,
)


def shadow_applicability(
    prepared: PreparedStageClose,
) -> GateApplicabilityDecision:
    """Phase 1 基线为 Shadow；调用方不能传入或覆盖 mode。"""

    return resolve_gate_applicability(
        policy=current_activation_policy(prepared.root),
        stage_key=prepared.stage_key,
        risk_level=prepared.risk_level,
        loop_id=prepared.loop_id,
        loop_created_at=prepared.loop_created_at,
        gate_contract_version=prepared.gate_contract_version,
    )
