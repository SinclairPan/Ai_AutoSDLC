"""Finding 终态授权的单一确定性判定。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.finding_trust_models import TrustedFindingAuthority


def required_terminal_authority(
    authority: TrustedFindingAuthority,
    capability_id: str,
) -> bool:
    return (
        authority.authority_kind in {"reviewer", "deterministic_gate"}
        and authority.slot_kind == "required"
        and authority.eligible_for_enforce_quorum
        and capability_id in authority.capability_ids
        and capability_id in authority.blocking_authorities
    )
