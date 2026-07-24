"""Finding façade的可信证据与 waiver 查询辅助。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.finding_command_models import FindingAppendCommand
from ai_sdlc.core.stage_review.finding_trust_models import (
    FindingTrustContext,
    FindingTrustResolver,
    FindingWaiver,
    TrustedEvidenceDescriptor,
)


def trusted_command_evidence(
    command: FindingAppendCommand,
    trust: FindingTrustContext,
    resolver: FindingTrustResolver,
) -> TrustedEvidenceDescriptor:
    is_receipt = command.event_type == "cross_scope_handoff_resolved"
    scope = command.target_scope if is_receipt else command.scope
    if scope is None:
        raise PermissionError("finding evidence scope is unavailable")
    evidence = resolver.resolve_evidence(scope, command.evidence_bundle_digest)
    if (
        evidence is None
        or evidence.scope != scope
        or evidence.evidence_bundle_digest != command.evidence_bundle_digest
    ):
        raise PermissionError("finding evidence is not trusted")
    if not is_receipt and evidence.candidate_digest != trust.candidate_digest:
        raise PermissionError("finding evidence is not trusted")
    return TrustedEvidenceDescriptor.model_validate(evidence.model_dump(mode="json"))


def command_waiver(
    command: FindingAppendCommand,
    trust: FindingTrustContext,
) -> FindingWaiver | None:
    if command.event_type != "waived":
        return None
    waiver = next(
        (item for item in trust.waivers if item.waiver_digest == command.waiver_digest),
        None,
    )
    if waiver is None:
        raise PermissionError("finding waiver artifact is unavailable")
    return waiver


def historical_replay_trust(
    trust: FindingTrustContext,
    waivers: tuple[FindingWaiver, ...],
) -> FindingTrustContext:
    """重放只补入事件实际引用的历史 waiver，不放宽当前写权限。"""
    return trust.model_copy(update={"waivers": waivers})
