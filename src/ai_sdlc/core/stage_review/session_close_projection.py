"""Session 关闭消费投影的结构一致性校验。"""

from __future__ import annotations

from typing import Protocol, cast

from ai_sdlc.core.stage_review.session_contracts import SessionState


class SessionCloseProjectionView(Protocol):
    state: SessionState
    active_close_certificate_id: str
    active_close_certificate_digest: str
    active_close_claim_id: str
    active_close_claim_digest: str
    close_consumption_receipt_id: str
    close_consumption_receipt_digest: str
    close_governance_decision_digest: str
    close_failure_reason: str


class _SessionProjectionContainer(Protocol):
    projection: SessionCloseProjectionView


class SessionCloseProjectionAccessors:
    @property
    def active_close_certificate_id(self) -> str:
        return _projection(self).active_close_certificate_id

    @property
    def active_close_certificate_digest(self) -> str:
        return _projection(self).active_close_certificate_digest

    @property
    def active_close_claim_id(self) -> str:
        return _projection(self).active_close_claim_id

    @property
    def active_close_claim_digest(self) -> str:
        return _projection(self).active_close_claim_digest

    @property
    def close_consumption_receipt_id(self) -> str:
        return _projection(self).close_consumption_receipt_id

    @property
    def close_failure_reason(self) -> str:
        return _projection(self).close_failure_reason


def _validate_session_close_projection(value: SessionCloseProjectionView) -> None:
    identity = (
        value.active_close_certificate_id,
        value.active_close_certificate_digest,
        value.active_close_claim_id,
        value.active_close_claim_digest,
    )
    receipt = (
        value.close_consumption_receipt_id,
        value.close_consumption_receipt_digest,
    )
    if any(identity) != all(identity) or any(receipt) != all(receipt):
        raise ValueError("session close projection has a partial binding")
    if value.state == "consuming":
        valid = all(identity) and not any(receipt) and not _has_failure(value)
    elif value.state == "authorized" and all(identity):
        valid = not any(receipt) and not _has_failure(value)
    elif value.state == "consumed":
        valid = all(identity) and all(receipt) and not _has_failure(value)
    elif value.state == "needs_user" and _has_failure(value):
        valid = (
            all(identity)
            and not any(receipt)
            and value.close_failure_reason == "governed_close_abort"
            and bool(value.close_governance_decision_digest)
        )
    else:
        valid = not any((*identity, *receipt)) and not _has_failure(value)
    if not valid:
        raise ValueError("session close projection is inconsistent with state")


def _has_failure(value: SessionCloseProjectionView) -> bool:
    return bool(
        value.close_governance_decision_digest or value.close_failure_reason
    )


def _projection(value: object) -> SessionCloseProjectionView:
    return cast(_SessionProjectionContainer, value).projection
