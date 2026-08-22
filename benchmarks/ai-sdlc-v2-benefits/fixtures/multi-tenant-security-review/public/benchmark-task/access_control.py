from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class Actor:
    user_id: str
    tenant_id: str
    roles: frozenset[str]


@dataclass
class ApprovalRequest:
    request_id: str
    tenant_id: str
    requester_id: str
    expires_at: datetime
    status: str = "pending"


@dataclass(frozen=True)
class AuditEvent:
    request_id: str
    actor_id: str
    decision: str
    reason: str
    timestamp: datetime


@dataclass(frozen=True)
class ApprovalResult:
    allowed: bool
    reason: str


def approve_request(
    request: ApprovalRequest,
    actor: Actor,
    *,
    action: str = "approve",
    now: datetime | None = None,
    audit_log: list[AuditEvent] | None = None,
) -> ApprovalResult:
    decision_time = now or datetime.now(UTC)
    if request.status != "pending":
        return ApprovalResult(False, "request_not_pending")
    if not actor.roles.intersection({"approver", "admin"}):
        return ApprovalResult(False, "role_not_allowed")
    if "admin" not in actor.roles and actor.tenant_id != request.tenant_id:
        return ApprovalResult(False, "tenant_mismatch")

    request.status = "approved"
    if audit_log is not None:
        audit_log.append(
            AuditEvent(
                request_id=request.request_id,
                actor_id=actor.user_id,
                decision=action,
                reason="authorized_role",
                timestamp=decision_time,
            )
        )
    return ApprovalResult(True, "approved")
