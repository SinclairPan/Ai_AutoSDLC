"""Review Session 执行期间解析既有持久化权威工件。"""

from __future__ import annotations

from dataclasses import dataclass

from ai_sdlc.core.stage_review.binding_models import BindingAuthoritySnapshot
from ai_sdlc.core.stage_review.binding_result_models import (
    RebindDirective,
    ReviewerBindingSet,
    ReviewerDispatchAssignment,
)
from ai_sdlc.core.stage_review.bindings import ReviewerBindingService
from ai_sdlc.core.stage_review.contracts import TaskRiskProfile
from ai_sdlc.core.stage_review.panel_models import ReviewerPlanRequest
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerPanelPlan
from ai_sdlc.core.stage_review.provider_journal import ProviderInvocationJournal
from ai_sdlc.core.stage_review.provider_journal_models import ProviderInvocation
from ai_sdlc.core.stage_review.resource_ledger_models import ResourceReservation
from ai_sdlc.core.stage_review.resources import ResourceGovernor
from ai_sdlc.core.stage_review.session_artifact_models import ReviewerPlanRevocation


@dataclass(frozen=True, slots=True)
class ExecutionTrustResolver:
    request: ReviewerPlanRequest
    plan: ReviewerPanelPlan
    risk_profile: TaskRiskProfile
    binding_set: ReviewerBindingSet
    binding_authority: BindingAuthoritySnapshot
    resources: ResourceGovernor
    bindings: ReviewerBindingService
    journal: ProviderInvocationJournal

    def resolve_plan_request(self, digest: str) -> ReviewerPlanRequest | None:
        return self.request if digest == self.request.request_digest else None

    def resolve_plan(self, digest: str) -> ReviewerPanelPlan | None:
        return self.plan if digest == self.plan.plan_digest else None

    def resolve_binding_set(self, digest: str) -> ReviewerBindingSet | None:
        if digest != self.binding_set.binding_set_digest:
            return None
        persisted = self.bindings.get_binding_set(self.binding_set.binding_set_id)
        return persisted if persisted == self.binding_set else None

    def resolve_binding_authority(
        self,
        digest: str,
    ) -> BindingAuthoritySnapshot | None:
        if (
            digest != self.binding_authority.snapshot_digest
            or digest != self.binding_set.authority_snapshot_digest
        ):
            return None
        return BindingAuthoritySnapshot.model_validate(
            self.binding_authority.model_dump(mode="json")
        )

    def resolve_reservation(self, digest: str) -> ResourceReservation | None:
        return self.resources.get_reservation_ancestor(
            self.plan.final_reservation_id,
            digest,
        )

    def resolve_assignment(
        self,
        digest: str,
    ) -> ReviewerDispatchAssignment | None:
        return self.bindings.get_dispatch_assignment(digest)

    def resolve_invocation(self, invocation_id: str) -> ProviderInvocation | None:
        return self.journal.get(invocation_id)

    def resolve_risk_profile(self, digest: str) -> TaskRiskProfile | None:
        return self.risk_profile if digest == self.risk_profile.profile_digest else None

    def resolve_rebind_directive(self, digest: str) -> RebindDirective | None:
        return None

    def resolve_plan_revocation(
        self,
        digest: str,
    ) -> ReviewerPlanRevocation | None:
        return None

    def macro_evidence_is_trusted(
        self,
        profile_digest: str,
        change_kind: str,
        evidence_digest: str,
    ) -> bool:
        return False


__all__ = ["ExecutionTrustResolver"]
