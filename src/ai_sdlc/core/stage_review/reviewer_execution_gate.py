"""Journal 调用的 Reviewer 授权、隔离与可信 Egress 唯一执行门。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from ai_sdlc.core.stage_review.provider_journal_driver import (
    ProviderInvocationDriver,
)
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderInvocationRequest,
    ProviderJournalResultCode,
)


class TrustedTransportStatus(Protocol):
    @property
    def remote_provider_available(self) -> bool: ...


class ReviewerExecutionGate:
    def __init__(
        self,
        *,
        authorize: Callable[[ProviderInvocationRequest, datetime], bool],
        prepare_isolated_driver: Callable[
            [ProviderInvocationRequest, ProviderInvocationDriver, datetime],
            ProviderInvocationDriver | None,
        ],
        requires_reviewer_gate: Callable[[ProviderInvocationRequest], bool],
        trusted_egress_provider_ids: tuple[str, ...] = (),
        trusted_transport: TrustedTransportStatus | None = None,
    ) -> None:
        self._authorize = authorize
        self._prepare_isolated_driver = prepare_isolated_driver
        self._requires_reviewer_gate = requires_reviewer_gate
        self._trusted_egress_provider_ids = frozenset(trusted_egress_provider_ids)
        self._trusted_transport = trusted_transport

    def prepare(
        self,
        request: ProviderInvocationRequest,
        driver: ProviderInvocationDriver,
        now: datetime,
    ) -> tuple[ProviderInvocationDriver | None, ProviderJournalResultCode | None]:
        if not self._requires_reviewer_gate(request):
            if request.authorization_scope == "reviewer_binding":
                return None, "dispatch_unauthorized"
            return driver, None
        if not self._authorize(request, now):
            return None, "dispatch_unauthorized"
        if request.provider_id in self._trusted_egress_provider_ids:
            return self._prepare_trusted_egress()
        prepared = self._prepare_isolated_driver(request, driver, now)
        return prepared, None if prepared is not None else "needs_user"

    def _prepare_trusted_egress(
        self,
    ) -> tuple[ProviderInvocationDriver | None, ProviderJournalResultCode]:
        transport = self._trusted_transport
        if transport is None or not transport.remote_provider_available:
            return None, "needs_user"
        # T601 只证明受控传输合同；真实远程 Provider adapter 属于 T602。
        return None, "needs_user"
