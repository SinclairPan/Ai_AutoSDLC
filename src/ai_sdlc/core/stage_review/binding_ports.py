"""Reviewer Binding 对可信 Resolver、Host 与隔离运行时的最小端口。"""

from __future__ import annotations

from typing import Protocol

from ai_sdlc.core.stage_review.binding_availability_models import (
    ProviderAvailabilityAttestation,
)
from ai_sdlc.core.stage_review.binding_models import (
    BindingAuthoritySnapshot,
    HostCapabilitySnapshot,
    IsolationExecutionEvidence,
    ReviewerRuntimeAllocation,
)
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerPanelPlan


class BindingAuthorityResolver(Protocol):
    def resolve(self, plan: ReviewerPanelPlan) -> BindingAuthoritySnapshot: ...


class ProviderAvailabilityResolver(Protocol):
    def resolve(
        self,
        plan: ReviewerPanelPlan,
        previous_binding_set_digest: str,
    ) -> ProviderAvailabilityAttestation | None: ...


class HostCapabilityProbe(Protocol):
    def probe(self, previous_snapshot_digest: str = "") -> HostCapabilitySnapshot: ...


class ReviewerRuntimeBroker(Protocol):
    def allocate(
        self,
        operation_id: str,
        plan: ReviewerPanelPlan,
        authority: BindingAuthoritySnapshot,
    ) -> tuple[ReviewerRuntimeAllocation, ...]: ...


class IsolationEvidenceAdapter(Protocol):
    def prepare(
        self,
        operation_id: str,
        allocations: tuple[ReviewerRuntimeAllocation, ...],
        host_snapshot: HostCapabilitySnapshot,
        visibility_barrier_id: str,
    ) -> tuple[IsolationExecutionEvidence, ...]: ...
