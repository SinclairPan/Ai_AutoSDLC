"""隔离执行器的 Provider driver 与执行观察包装。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from ai_sdlc.core.stage_review.isolation_execution import IsolationPermitStore
from ai_sdlc.core.stage_review.isolation_launch_models import (
    IsolatedCommandProviderDriver,
    IsolationLaunchContext,
    IsolationProcessResult,
)
from ai_sdlc.core.stage_review.isolation_models import IsolationExecutionPermit
from ai_sdlc.core.stage_review.isolation_receipts import build_execution_observation
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderInvocationRequest,
    ProviderQueryResult,
    ProviderSubmission,
)

if TYPE_CHECKING:
    from ai_sdlc.core.stage_review.isolation_launcher import ReviewerIsolationLauncher


class _IsolatedDriver:
    def __init__(
        self,
        launcher: ReviewerIsolationLauncher,
        driver: IsolatedCommandProviderDriver,
        context: IsolationLaunchContext,
        now: datetime,
    ) -> None:
        self.provider_id = driver.provider_id
        self.capabilities = driver.capabilities
        self._launcher = launcher
        self._driver = driver
        self._context = context
        self._now = now

    def invoke(self, request: ProviderInvocationRequest) -> ProviderSubmission:
        value = self._launcher._execute(
            self._driver, request, self._context, "invoke", self._now
        )
        return ProviderSubmission.model_validate(value.model_dump(mode="json"))

    def query(self, request: ProviderInvocationRequest) -> ProviderQueryResult:
        value = self._launcher._execute(
            self._driver, request, self._context, "query", self._now
        )
        return ProviderQueryResult.model_validate(value.model_dump(mode="json"))


class _PermitExecutionRecorder:
    def __init__(
        self,
        store: IsolationPermitStore,
        permit: IsolationExecutionPermit,
        now: datetime,
    ) -> None:
        self._store = store
        self._permit = permit
        self._now = now
        self._completed_digest = ""

    def record_completed(self, result: IsolationProcessResult) -> None:
        observation = build_execution_observation(
            self._permit,
            result,
            stage="completed",
            previous_observation_digest="",
            now=self._now,
        )
        self._store.persist_observation(observation)
        self._completed_digest = observation.observation_digest

    def record_cleanup(self, result: IsolationProcessResult) -> None:
        if not self._completed_digest:
            raise RuntimeError("isolation completion evidence is missing")
        observation = build_execution_observation(
            self._permit,
            result,
            stage="cleaned" if result.cleanup_succeeded else "cleanup_failed",
            previous_observation_digest=self._completed_digest,
            now=self._now,
        )
        self._store.persist_observation(observation)


__all__: list[str] = []
