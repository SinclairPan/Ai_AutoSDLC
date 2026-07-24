"""Session 不可变工件的路径约束、持久化与可信读取。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ai_sdlc.core.stage_review.artifacts import create_json_exclusive, read_json_object
from ai_sdlc.core.stage_review.binding_result_models import ReviewerBindingSet
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.finding_support_models import ProgressSnapshot
from ai_sdlc.core.stage_review.finding_trust_models import InitialReviewSeal
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerPanelPlan
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.resource_ledger_models import ResourceReservation
from ai_sdlc.core.stage_review.session_artifact_models import (
    ReviewCohort,
    ReviewerPlanRevocation,
    ReviewPass,
)
from ai_sdlc.core.stage_review.session_budget_approval_models import BudgetGrantApproval
from ai_sdlc.core.stage_review.session_budget_grant_models import (
    BudgetGrantResourceApplication,
)
from ai_sdlc.core.stage_review.session_budget_grant_operation import (
    SessionBudgetGrantOperation,
)
from ai_sdlc.core.stage_review.session_budget_grant_request import (
    BudgetGrantRequestProof,
)
from ai_sdlc.core.stage_review.session_budget_reconciliation_models import (
    BudgetGrantResourceReconciliation,
)
from ai_sdlc.core.stage_review.session_contracts import SessionIntegrityError

_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class _SessionArtifactStoreMixin:
    """复用 SessionEventStore 的 canonical session root 管理不可变工件。"""

    def persist_cohort(self, cohort: ReviewCohort) -> None:
        self._persist_model(
            self._artifact_path(cohort.scope, "cohorts", cohort.cohort_id),
            cohort,
            ReviewCohort,
            cohort.cohort_digest,
        )

    def get_cohort(self, scope: FindingScope, cohort_id: str) -> ReviewCohort:
        return self._require_model(
            self._artifact_path(scope, "cohorts", cohort_id),
            ReviewCohort,
            "review cohort",
        )

    def persist_pass(self, review_pass: ReviewPass) -> None:
        self._persist_model(
            self._artifact_path(review_pass.scope, "passes", review_pass.pass_id),
            review_pass,
            ReviewPass,
            review_pass.pass_digest,
        )

    def get_pass(self, scope: FindingScope, pass_id: str) -> ReviewPass:
        return self._require_model(
            self._artifact_path(scope, "passes", pass_id),
            ReviewPass,
            "review pass",
        )

    def persist_initial_seal(self, seal: InitialReviewSeal) -> str:
        artifact_id = stable_id("initial-review-seal", seal.seal_digest)
        self._persist_model(
            self._artifact_path(seal.scope, "initial-seals", artifact_id),
            seal,
            InitialReviewSeal,
            seal.seal_digest,
        )
        return artifact_id

    def get_initial_seal(
        self,
        scope: FindingScope,
        artifact_id: str,
    ) -> InitialReviewSeal:
        return self._require_model(
            self._artifact_path(scope, "initial-seals", artifact_id),
            InitialReviewSeal,
            "initial review seal",
        )

    def persist_progress(self, scope: FindingScope, snapshot: ProgressSnapshot) -> str:
        artifact_id = stable_id("progress-snapshot", snapshot.snapshot_digest)
        self._persist_model(
            self._artifact_path(scope, "progress", artifact_id),
            snapshot,
            ProgressSnapshot,
            snapshot.snapshot_digest,
        )
        return artifact_id

    def get_progress(
        self,
        scope: FindingScope,
        snapshot_digest: str,
    ) -> ProgressSnapshot:
        artifact_id = stable_id("progress-snapshot", snapshot_digest)
        return self._require_model(
            self._artifact_path(scope, "progress", artifact_id),
            ProgressSnapshot,
            "progress snapshot",
        )

    def persist_revocation(
        self,
        scope: FindingScope,
        revocation: ReviewerPlanRevocation,
    ) -> None:
        self._persist_model(
            self._artifact_path(
                scope,
                "plan-revocations",
                revocation.revocation_id,
            ),
            revocation,
            ReviewerPlanRevocation,
            revocation.revocation_digest,
        )

    def persist_authority(
        self,
        scope: FindingScope,
        plan: ReviewerPanelPlan,
        binding_set: ReviewerBindingSet,
        reservation: ResourceReservation,
    ) -> None:
        snapshots: tuple[tuple[str, str, BaseModel, type[BaseModel]], ...] = (
            ("plans", plan.plan_digest, plan, ReviewerPanelPlan),
            (
                "bindings",
                binding_set.binding_set_digest,
                binding_set,
                ReviewerBindingSet,
            ),
            (
                "reservations",
                reservation.reservation_digest,
                reservation,
                ResourceReservation,
            ),
        )
        for directory, digest, model, model_type in snapshots:
            artifact_id = stable_id(f"session-{directory[:-1]}", digest)
            self._persist_model(
                self._artifact_path(scope, directory, artifact_id),
                model,
                model_type,
                digest,
            )

    def persist_budget_grant_application(
        self,
        scope: FindingScope,
        application: BudgetGrantResourceApplication,
    ) -> None:
        self._persist_model(
            self._artifact_path(
                scope,
                "budget-grant-applications",
                application.grant.grant_id,
            ),
            application,
            BudgetGrantResourceApplication,
            application.application_digest,
        )

    def persist_budget_grant_approval(self, approval: BudgetGrantApproval) -> None:
        self._persist_model(
            self._artifact_path(
                approval.scope,
                "budget-grant-approvals",
                approval.approval_id,
            ),
            approval,
            BudgetGrantApproval,
            approval.approval_digest,
        )

    def get_budget_grant_approval(
        self,
        scope: FindingScope,
        approval_id: str,
    ) -> BudgetGrantApproval:
        return self._require_model(
            self._artifact_path(scope, "budget-grant-approvals", approval_id),
            BudgetGrantApproval,
            "budget grant approval",
        )

    def persist_budget_grant_request_proof(
        self,
        proof: BudgetGrantRequestProof,
    ) -> None:
        self._persist_model(
            self._artifact_path(
                proof.approval.scope,
                "budget-grant-request-proofs",
                stable_id(
                    "budget-grant-request-proof",
                    proof.request_operation.command_id,
                ),
            ),
            proof,
            BudgetGrantRequestProof,
            proof.proof_digest,
        )

    def get_budget_grant_request_proof(
        self,
        proof: BudgetGrantRequestProof,
    ) -> BudgetGrantRequestProof:
        proof_id = stable_id(
            "budget-grant-request-proof",
            proof.request_operation.command_id,
        )
        return self._require_model(
            self._artifact_path(
                proof.approval.scope,
                "budget-grant-request-proofs",
                proof_id,
            ),
            BudgetGrantRequestProof,
            "budget grant request proof",
        )

    def persist_budget_grant_reconciliation(
        self,
        scope: FindingScope,
        reconciliation: BudgetGrantResourceReconciliation,
    ) -> None:
        self._persist_model(
            self._artifact_path(
                scope,
                "budget-grant-reconciliations",
                reconciliation.application.grant.grant_id,
            ),
            reconciliation,
            BudgetGrantResourceReconciliation,
            reconciliation.reconciliation_digest,
        )

    def get_budget_grant_application(
        self,
        scope: FindingScope,
        grant_id: str,
    ) -> BudgetGrantResourceApplication | None:
        path = self._artifact_path(scope, "budget-grant-applications", grant_id)
        if not path.exists():
            return None
        return self._require_model(
            path,
            BudgetGrantResourceApplication,
            "budget grant resource application",
        )

    def persist_session_budget_grant_operation(
        self,
        scope: FindingScope,
        operation: SessionBudgetGrantOperation,
    ) -> None:
        self._persist_model(
            self._artifact_path(
                scope,
                "budget-grant-operations",
                operation.operation_id,
            ),
            operation,
            SessionBudgetGrantOperation,
            operation.operation_digest,
        )

    def get_session_budget_grant_operation(
        self,
        scope: FindingScope,
        operation_id: str,
    ) -> SessionBudgetGrantOperation:
        return self._require_model(
            self._artifact_path(
                scope,
                "budget-grant-operations",
                operation_id,
            ),
            SessionBudgetGrantOperation,
            "session budget grant operation",
        )

    def _persist_model(
        self,
        path: Path,
        model: BaseModel,
        model_type: type[_ModelT],
        digest: str,
    ) -> None:
        trusted = model_type.model_validate(model.model_dump(mode="json"))
        payload = trusted.model_dump(mode="json")
        if create_json_exclusive(path, payload):
            return
        existing = self._require_model(path, model_type, path.parent.name)
        if existing.model_dump(mode="json") != payload or not digest:
            raise SessionIntegrityError("session immutable artifact fork")

    def _require_model(
        self,
        path: Path,
        model_type: type[_ModelT],
        label: str,
    ) -> _ModelT:
        try:
            from ai_sdlc.core.stage_review.session_artifact_codec import (
                decode_session_artifact,
            )

            return decode_session_artifact(model_type, read_json_object(path))
        except SessionIntegrityError:
            raise
        except (
            FileNotFoundError,
            json.JSONDecodeError,
            ValidationError,
            ValueError,
        ) as exc:
            raise SessionIntegrityError(f"{label} artifact is invalid") from exc

    def _artifact_path(
        self,
        scope: FindingScope,
        directory: str,
        artifact_id: str,
    ) -> Path:
        if _IDENTITY.fullmatch(artifact_id) is None:
            raise ValueError("session artifact identity is invalid")
        parent = (self._session_root(scope) / directory).resolve()
        path = (parent / f"{artifact_id}.json").resolve()
        if path.parent != parent:
            raise ValueError("session artifact path escapes store")
        return path

    def _session_root(self, scope: FindingScope) -> Path:
        raise NotImplementedError
