"""收敛已提交产品关闭与 canonical Review Session。"""

from __future__ import annotations

from collections.abc import Callable

from ai_sdlc.core.stage_review.authorizer import StageCloseAuthorizer
from ai_sdlc.core.stage_review.close_models import (
    StageCloseAuthorization,
    StageCloseContext,
)
from ai_sdlc.core.stage_review.session import StageReviewSessionService


def _finalize_recovered_product_close(
    recovered: tuple[StageCloseAuthorization, object],
    sessions: StageReviewSessionService,
    authority_context: Callable[
        [], tuple[StageCloseAuthorizer, StageCloseContext]
    ],
    on_closed: Callable[[StageCloseAuthorization], None] | None,
) -> object:
    authorization, result = recovered
    if not _session_already_consumed(sessions, authorization):
        authorizer, context = authority_context()
        authorization = authorizer._reconcile_committed_stage_close(
            context,
            authorization,
        )
    if on_closed is not None:
        on_closed(authorization)
    return result


def _session_already_consumed(
    sessions: StageReviewSessionService,
    authorization: StageCloseAuthorization,
) -> bool:
    session = sessions.get(authorization.claim.scope)
    if session.state == "consuming":
        return False
    projection = session.projection
    receipt = authorization.receipt
    if session.state != "consumed" or not all(
        (
            projection.active_close_certificate_id
            == authorization.claim.certificate_id,
            projection.active_close_certificate_digest
            == authorization.claim.certificate_digest,
            projection.active_close_claim_id == authorization.claim.claim_id,
            projection.active_close_claim_digest == authorization.claim.claim_digest,
            receipt is not None,
            receipt is not None
            and projection.close_consumption_receipt_id == receipt.receipt_id,
            receipt is not None
            and projection.close_consumption_receipt_digest
            == receipt.receipt_digest,
        )
    ):
        raise ValueError("recovered product close session diverged")
    return True
