"""Provider Adapter 能力校验与可恢复调用决策。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from ai_sdlc.core.stage_review.provider_execution_evidence import (
    ProviderExecutionOutcome,
    build_provider_execution_outcome,
)
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderInvocationRequest,
    ProviderJournalResultCode,
    ProviderQueryResult,
    ProviderRecoveryCapabilities,
    ProviderSubmission,
)
from ai_sdlc.core.stage_review.provider_usage_models import AccountedProviderUsage

ProviderOutputValidator = Callable[[ProviderSubmission], str]


class ProviderDriverRefused(RuntimeError):  # noqa: N818
    """Provider 执行边界在产生可信 Submission 前拒绝本次调用。"""

    def __init__(
        self,
        message: str,
        *,
        accounted_usage: AccountedProviderUsage | None = None,
        outcome: ProviderExecutionOutcome | None = None,
    ) -> None:
        super().__init__(message)
        if outcome is not None and accounted_usage not in {
            None,
            outcome.accounted_usage,
        }:
            raise ValueError("provider refusal usage diverges from execution outcome")
        self.outcome = outcome or build_provider_execution_outcome(accounted_usage)

    @property
    def accounted_usage(self) -> AccountedProviderUsage | None:
        return self.outcome.accounted_usage


class ProviderInvocationDriver(Protocol):
    provider_id: str
    capabilities: ProviderRecoveryCapabilities

    def invoke(self, request: ProviderInvocationRequest) -> ProviderSubmission: ...

    def query(self, request: ProviderInvocationRequest) -> ProviderQueryResult: ...


def recover_provider_submission(
    request: ProviderInvocationRequest,
    driver: ProviderInvocationDriver,
    *,
    fresh_dispatch: bool,
) -> tuple[ProviderSubmission | None, ProviderJournalResultCode | None]:
    if fresh_dispatch:
        return _trusted_submission(driver.invoke(request)), None
    if request.capabilities.invocation_query_support:
        raw_query = driver.query(request)
        query = ProviderQueryResult.model_validate(raw_query.model_dump(mode="json"))
        if query.query_status == "submitted":
            return query.submission, None
        if query.query_status == "in_progress":
            return None, "retry_wait"
        return _trusted_submission(driver.invoke(request)), None
    if request.capabilities.idempotency_support:
        return _trusted_submission(driver.invoke(request)), None
    return None, "needs_user"


def driver_matches(
    request: ProviderInvocationRequest,
    driver: ProviderInvocationDriver,
) -> bool:
    return (
        driver.provider_id == request.provider_id
        and driver.capabilities == request.capabilities
    )


def _trusted_submission(value: ProviderSubmission) -> ProviderSubmission:
    return ProviderSubmission.model_validate(value.model_dump(mode="json"))
