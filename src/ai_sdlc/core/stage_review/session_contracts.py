"""StageReviewSession 的命令、状态枚举与可信解析端口。"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_sdlc.core.stage_review.binding_models import BindingAuthoritySnapshot
from ai_sdlc.core.stage_review.binding_result_models import (
    RebindDirective,
    ReviewerBindingSet,
    ReviewerDispatchAssignment,
)
from ai_sdlc.core.stage_review.certificate_models import (
    StageCloseCertificate,
    StageCloseCertificateRequest,
)
from ai_sdlc.core.stage_review.close_governance_models import (
    StageCloseGovernanceDecision,
)
from ai_sdlc.core.stage_review.close_models import (
    CloseConsumptionClaim,
    StageCloseConsumptionReceipt,
)
from ai_sdlc.core.stage_review.close_recovery_models import (
    StageCloseRecoveryDecision,
)
from ai_sdlc.core.stage_review.contracts import TaskRiskProfile
from ai_sdlc.core.stage_review.finding_command_models import (
    FindingInitialBatchCommand,
    FindingInitialDraft,
    FindingLineageAdvanceCommand,
)
from ai_sdlc.core.stage_review.finding_models import (
    FindingAppendResult,
    FindingLedger,
    FindingScope,
)
from ai_sdlc.core.stage_review.finding_support_models import ProgressSnapshot
from ai_sdlc.core.stage_review.panel_models import ReviewerPlanRequest
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerPanelPlan
from ai_sdlc.core.stage_review.provider_journal_models import ProviderInvocation
from ai_sdlc.core.stage_review.resource_grant_models import BudgetGrantDecisionClaim
from ai_sdlc.core.stage_review.resource_ledger_models import ResourceReservation
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts
from ai_sdlc.core.stage_review.session_artifact_models import (
    CoverageDeclaration,
    ReviewerPlanRevocation,
)
from ai_sdlc.core.stage_review.session_budget_grant_models import (
    BudgetGrantResourceApplication,
)
from ai_sdlc.core.stage_review.session_budget_reconciliation_models import (
    BudgetGrantResourceReconciliation,
)

_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

SessionState = Literal[
    "planning",
    "binding",
    "collecting_initial_reviews",
    "remediation_required",
    "awaiting_verification",
    "replanning",
    "authorized",
    "consuming",
    "consumed",
    "needs_user",
    "blocked",
    "superseded",
]
SessionEventKind = Literal[
    "session_started",
    "review_pass_committed",
    "initial_reviews_sealed",
    "cohort_reviews_sealed",
    "progress_recorded",
    "candidate_updated",
    "risk_fact_enriched",
    "provider_rebind_required",
    "role_gap_detected",
    "cohort_superseded",
    "old_passes_invalidated",
    "plan_resolution_requested",
    "panel_plan_frozen",
    "reviewer_bindings_validated",
    "new_cohort_activated",
    "macro_rebaseline_requested",
    "reviewer_plan_revoked",
    "user_decision_required",
    "budget_grant_requested",
    "budget_grant_applied",
    "budget_grant_reconciled",
    "budget_grant_failed",
    "close_consumption_started",
    "close_receipt_committed",
    "governed_close_abort",
    "reconciled_new_certificate_issued",
    "macro_rebaseline_accepted",
]
ReviewVerdict = Literal["passed", "findings"]
ProgressOutcome = Literal["improved", "same", "regressed", "uncomparable"]
MacroChangeKind = Literal[
    "requirements_change",
    "architecture_change",
    "technical_route_change",
    "acceptance_baseline_change",
    "risk_profile_change",
]


class SessionIntegrityError(RuntimeError):
    """Session 的可信工件、事件链或状态血缘不一致。"""


class SessionCasConflictError(RuntimeError):
    """命令基于过期 Session revision。"""


class BudgetGrantApprovalChangedError(SessionCasConflictError):
    """BudgetGrant 决策绑定的审批治理代次已变化。"""


class SessionCommand(BaseModel):
    model_config = _MODEL_CONFIG

    scope: FindingScope
    command_id: str
    idempotency_key: str
    expected_revision: int = Field(ge=0)


class SessionStartCommand(SessionCommand):
    candidate_digest: str
    risk_profile_digest: str
    risk_profile_lineage_id: str
    policy_digest: str
    optimization_snapshot_digest: str
    plan_digest: str
    binding_set_digest: str


class SubmitReviewPassCommand(SessionCommand):
    cohort_id: str
    slot_id: str
    assignment_digest: str
    invocation_id: str
    verdict: ReviewVerdict
    coverage: CoverageDeclaration
    findings: tuple[FindingInitialDraft, ...] = ()
    evidence_digests: tuple[str, ...]
    observed_peer_pass_ids: tuple[str, ...] = ()


class ProgressCommand(SessionCommand):
    snapshot: ProgressSnapshot


class CandidateUpdateCommand(SessionCommand):
    candidate_digest: str
    binding_set_digest: str


class RoleGapCommand(SessionCommand):
    missing_capability_ids: tuple[str, ...]
    plan_digest: str
    binding_set_digest: str

    @field_validator("missing_capability_ids")
    @classmethod
    def _canonical_gap(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or value != tuple(sorted(set(value))):
            raise ValueError("role gap capabilities must be unique and sorted")
        return value


class ProviderRebindCommand(SessionCommand):
    binding_set_digest: str
    rebind_directive_digest: str


class RiskEnrichmentCommand(SessionCommand):
    risk_profile_digest: str
    plan_digest: str = ""
    binding_set_digest: str = ""
    macro_change_kind: MacroChangeKind | None = None
    evidence_digest: str = ""


class MacroRebaselineCommand(SessionCommand):
    change_kind: MacroChangeKind
    evidence_digest: str


class PlanRevocationCommand(SessionCommand):
    revocation_digest: str


class CloseConsumptionStartCommand(SessionCommand):
    certificate: StageCloseCertificate
    certificate_request: StageCloseCertificateRequest
    claim: CloseConsumptionClaim


class CloseReceiptCommitCommand(SessionCommand):
    claim: CloseConsumptionClaim
    receipt: StageCloseConsumptionReceipt


class GovernedCloseAbortCommand(SessionCommand):
    claim: CloseConsumptionClaim
    governance_decision: StageCloseGovernanceDecision


class ReconciledCloseCertificateCommand(SessionCommand):
    aborted_claim: CloseConsumptionClaim
    recovery_decision: StageCloseRecoveryDecision
    certificate: StageCloseCertificate
    certificate_request: StageCloseCertificateRequest
    claim: CloseConsumptionClaim


class CloseAbortSupersedeCommand(SessionCommand):
    aborted_claim: CloseConsumptionClaim
    recovery_decision: StageCloseRecoveryDecision


class BudgetGrantRequestCommand(SessionCommand):
    expected_budget_revision: int = Field(ge=0)
    increment: ResourceAmounts
    approval_digest: str

    @field_validator("approval_digest")
    @classmethod
    def _approval_is_present(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("budget grant approval digest is required")
        return value

    @field_validator("increment")
    @classmethod
    def _increment_is_positive(cls, value: ResourceAmounts) -> ResourceAmounts:
        if not value.any_positive():
            raise ValueError("budget grant increment must be positive")
        return value


class BudgetGrantApplyCommand(SessionCommand):
    request_command_id: str
    request_event_digest: str
    application: BudgetGrantResourceApplication
    decision: BudgetGrantDecisionClaim


class BudgetGrantReconcileCommand(SessionCommand):
    request_command_id: str
    request_event_digest: str
    reconciliation: BudgetGrantResourceReconciliation


class BudgetGrantFailureCommand(SessionCommand):
    request_command_id: str
    request_event_digest: str
    failure_code: str
    integrity_failure: bool = False


class FindingInitialBatchWriter(Protocol):
    def append(self, command: FindingInitialBatchCommand) -> FindingAppendResult: ...

    def read(self, scope: FindingScope) -> FindingLedger: ...

    def advance_lineage(
        self, command: FindingLineageAdvanceCommand
    ) -> FindingAppendResult: ...


class SessionTrustResolver(Protocol):
    def resolve_plan_request(self, digest: str) -> ReviewerPlanRequest | None: ...

    def resolve_plan(self, digest: str) -> ReviewerPanelPlan | None: ...

    def resolve_binding_set(self, digest: str) -> ReviewerBindingSet | None: ...

    def resolve_binding_authority(
        self,
        digest: str,
    ) -> BindingAuthoritySnapshot | None: ...

    def resolve_reservation(self, digest: str) -> ResourceReservation | None: ...

    def resolve_assignment(self, digest: str) -> ReviewerDispatchAssignment | None: ...

    def resolve_invocation(self, invocation_id: str) -> ProviderInvocation | None: ...

    def resolve_risk_profile(self, digest: str) -> TaskRiskProfile | None: ...

    def resolve_rebind_directive(self, digest: str) -> RebindDirective | None: ...

    def resolve_plan_revocation(
        self,
        digest: str,
    ) -> ReviewerPlanRevocation | None: ...

    def macro_evidence_is_trusted(
        self,
        profile_digest: str,
        change_kind: str,
        evidence_digest: str,
    ) -> bool: ...


_COMMAND_TYPES: tuple[type[SessionCommand], ...] = (
    SessionStartCommand,
    SubmitReviewPassCommand,
    ProgressCommand,
    CandidateUpdateCommand,
    RoleGapCommand,
    ProviderRebindCommand,
    RiskEnrichmentCommand,
    MacroRebaselineCommand,
    PlanRevocationCommand,
    CloseConsumptionStartCommand,
    CloseReceiptCommitCommand,
    GovernedCloseAbortCommand,
    ReconciledCloseCertificateCommand,
    CloseAbortSupersedeCommand,
    BudgetGrantRequestCommand,
    BudgetGrantApplyCommand,
    BudgetGrantReconcileCommand,
    BudgetGrantFailureCommand,
)
_COMMAND_BY_NAME = {item.__name__: item for item in _COMMAND_TYPES}


def session_command_type(command: SessionCommand) -> str:
    name = type(command).__name__
    if name not in _COMMAND_BY_NAME:
        raise TypeError(f"unsupported session command: {name}")
    return name


def parse_session_command(name: str, payload: object) -> SessionCommand:
    model = _COMMAND_BY_NAME.get(name)
    if model is None:
        raise SessionIntegrityError("session operation command type is unknown")
    return model.model_validate(payload)
