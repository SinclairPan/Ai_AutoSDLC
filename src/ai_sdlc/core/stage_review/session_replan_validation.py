"""Session 角色重规划计数的确定性校验。"""

from ai_sdlc.core.stage_review.session_contracts import SessionIntegrityError
from ai_sdlc.core.stage_review.session_models import SessionProjectionData


def _validate_replan_delta(
    before: SessionProjectionData,
    after: SessionProjectionData,
) -> None:
    before_total = sum(item.count for item in before.role_replan_counts)
    after_total = sum(item.count for item in after.role_replan_counts)
    if after_total != before_total + 1:
        raise SessionIntegrityError("session role replan counter is invalid")
