"""Canonical FindingLedger 在真实 Review Session 中使用的可信适配。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import (
    create_json_exclusive,
    read_json_object,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.binding_result_models import (
    ReviewerBinding,
    ReviewerBindingSet,
)
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.finding_command_models import (
    FindingAppendCommand,
    FindingInitialBatchCommand,
    FindingLineageAdvanceCommand,
)
from ai_sdlc.core.stage_review.finding_models import (
    FindingAppendResult,
    FindingEvent,
    FindingLedger,
    FindingScope,
)
from ai_sdlc.core.stage_review.finding_trust_models import (
    FindingTrustContext,
    InitialReviewSeal,
    TrustedEvidenceDescriptor,
    TrustedFindingAuthority,
    TrustedIdentityMappingDecision,
)
from ai_sdlc.core.stage_review.findings import FindingLedgerService
from ai_sdlc.core.stage_review.optimization.attribution_runtime import (
    FindingAttributionRecorder,
)
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerPanelPlan
from ai_sdlc.core.stage_review.remote_review_models import RemoteReviewOutput
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.resource_ledger_models import ResourceReservation
from ai_sdlc.core.stage_review.session_paths import (
    _session_scope_root as session_scope_root,
)


class ExecutionFindingTrustResolver:
    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        plan: ReviewerPanelPlan,
        binding_set: ReviewerBindingSet,
        reservation: ResourceReservation,
        clock: Callable[[], str],
    ) -> None:
        self._shared = resolve_canonical_shared_state(root, project_id)
        self._project_id = project_id
        self._plan = plan
        self._binding_set = binding_set
        self._reservation = reservation
        self._clock = clock
        self._context: FindingTrustContext | None = None

    def bind_initial(self, command: FindingInitialBatchCommand) -> None:
        seal = self._read_seal(command.scope, command.initial_review_seal_digest)
        self._context = self._context_for(seal)

    def bind_latest(self, scope: FindingScope) -> None:
        directory = self._session_root(scope) / "initial-seals"
        paths = tuple(sorted(directory.glob("*.json"))) if directory.exists() else ()
        if len(paths) != 1:
            raise ValueError("review session initial seal is unavailable")
        self._context = self._context_for(
            InitialReviewSeal.model_validate(read_json_object(paths[0]))
        )

    def persist_review_evidence(
        self,
        scope: FindingScope,
        output: RemoteReviewOutput,
        binding: ReviewerBinding,
        *,
        produced_at: str,
    ) -> None:
        for finding in output.findings:
            descriptor = TrustedEvidenceDescriptor(
                scope=scope,
                evidence_bundle_digest=finding.evidence_bundle_digest,
                evidence_kind="finding",
                candidate_digest=self._binding_set.candidate_manifest_digest,
                produced_at=produced_at,
                first_visible_at=produced_at,
                initial_visibility="visible",
                confirmation_result="confirmed",
                subject_identity_digest=finding.identity.identity_digest,
                signer_actor_id=binding.actor_id,
                signer_slot_id=binding.slot_id,
                signer_slot_kind=binding.slot_kind,
                signer_authority_kind="reviewer",
                signer_capability_id=finding.capability_id,
                signer_capability_ids=binding.capability_ids,
                signer_blocking_authorities=_blocking_authorities(
                    self._plan, binding.slot_id
                ),
                signer_eligible_for_enforce_quorum=(
                    binding.eligible_for_enforce_quorum
                ),
                signer_role_contract_digest=binding.role_contract_digest,
                signer_binding_digest=binding.binding_digest,
            )
            self._persist_evidence(descriptor)

    def resolve(self, scope: FindingScope) -> FindingTrustContext:
        if self._context is None or self._context.scope != scope:
            self.bind_latest(scope)
        if self._context is None:
            raise ValueError("finding trust context is unavailable")
        return self._context

    def resolve_evidence(
        self,
        scope: FindingScope,
        evidence_bundle_digest: str,
    ) -> TrustedEvidenceDescriptor | None:
        path = self._evidence_path(evidence_bundle_digest)
        if not path.exists():
            return None
        descriptor = TrustedEvidenceDescriptor.model_validate(read_json_object(path))
        return descriptor if descriptor.scope == scope else None

    def event_is_trusted(self, event: object) -> bool:
        if not isinstance(event, FindingEvent) or event.authority_kind != "reviewer":
            return False
        context = self.resolve(event.scope)
        authority = next(
            (
                item
                for item in context.authorities
                if (item.actor_id, item.slot_id) == (event.actor_id, event.slot_id)
            ),
            None,
        )
        evidence = self.resolve_evidence(event.scope, event.evidence_bundle_digest)
        return bool(
            authority is not None
            and evidence is not None
            and event.binding_digest == authority.binding_digest
            and event.role_contract_digest == authority.role_contract_digest
        )

    def session_lineage_is_trusted(self, event: object) -> bool:
        return False

    def resolve_mapping(
        self,
        scope: FindingScope,
        decision_digest: str,
    ) -> TrustedIdentityMappingDecision | None:
        return None

    def _context_for(self, seal: InitialReviewSeal) -> FindingTrustContext:
        return FindingTrustContext(
            scope=seal.scope,
            candidate_digest=seal.initial_candidate_digest,
            policy_digest=seal.policy_digest,
            plan_digest=seal.plan_digest,
            binding_set_digest=seal.binding_set_digest,
            cohort_id=seal.initial_cohort_id,
            reviewer_engine_version="dynamic-review-gate.v1",
            initial_review_seal=seal,
            session_fencing_epoch=self._reservation.fencing_token,
            authorities=tuple(
                _authority(self._plan, binding, self._reservation.lease_expires_at)
                for binding in self._binding_set.bindings
            ),
            evaluation_at=self._clock(),
        )

    def _read_seal(self, scope: FindingScope, digest: str) -> InitialReviewSeal:
        identity = stable_id("initial-review-seal", digest)
        path = self._session_root(scope) / "initial-seals" / f"{identity}.json"
        seal = InitialReviewSeal.model_validate(read_json_object(path))
        if seal.seal_digest != digest:
            raise ValueError("initial review seal digest diverged")
        return seal

    def _session_root(self, scope: FindingScope) -> Path:
        root = self._shared / "stage-review-sessions"
        return session_scope_root(root, self._project_id, scope)

    def _evidence_path(self, digest: str) -> Path:
        identity = stable_id("finding-evidence", digest)
        return self._shared / "finding-evidence" / f"{identity}.json"

    def _persist_evidence(self, descriptor: TrustedEvidenceDescriptor) -> None:
        path = self._evidence_path(descriptor.evidence_bundle_digest)
        payload = descriptor.model_dump(mode="json")
        if not create_json_exclusive(path, payload):
            existing = TrustedEvidenceDescriptor.model_validate(read_json_object(path))
            if existing != descriptor:
                raise ValueError("finding evidence identity fork")


class CanonicalFindingMutationAuthority:
    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        resolver: ExecutionFindingTrustResolver,
    ) -> None:
        self._resolver = resolver
        self._root = root
        self._project_id = project_id
        attribution = FindingAttributionRecorder(root, project_id=project_id)

        def record_attribution(event: FindingEvent) -> None:
            attribution.record(event)

        self._service = FindingLedgerService(
            root,
            project_id=project_id,
            trust_resolver=resolver,
            event_observer=record_attribution,
        )

    def append(
        self,
        command: FindingInitialBatchCommand | FindingAppendCommand,
    ) -> FindingAppendResult:
        if isinstance(command, FindingInitialBatchCommand):
            self._resolver.bind_initial(command)
        else:
            self._resolver.bind_latest(command.scope)
        return self._service.append(command)

    def read(self, scope: FindingScope) -> FindingLedger:
        self._resolver.bind_latest(scope)
        return self._service.read(scope)

    def advance_lineage(
        self,
        command: FindingLineageAdvanceCommand,
    ) -> FindingAppendResult:
        self._resolver.bind_latest(command.scope)
        return self._service.advance_lineage(command)


CanonicalFindingLedgerWriter = CanonicalFindingMutationAuthority

def _authority(
    plan: ReviewerPanelPlan,
    binding: ReviewerBinding,
    valid_until: str,
) -> TrustedFindingAuthority:
    return TrustedFindingAuthority(
        actor_id=binding.actor_id,
        slot_id=binding.slot_id,
        slot_kind=binding.slot_kind,
        authority_kind="reviewer",
        capability_ids=binding.capability_ids,
        blocking_authorities=_blocking_authorities(plan, binding.slot_id),
        role_profile_id=binding.role_profile_id,
        role_contract_digest=binding.role_contract_digest,
        binding_digest=binding.binding_digest,
        eligible_for_enforce_quorum=binding.eligible_for_enforce_quorum,
        valid_until=valid_until,
        capability_coverage_digest=canonical_digest(
            plan.proposal.coverage_proof,
            CanonicalizationPolicy(),
        ),
    )


def _blocking_authorities(
    plan: ReviewerPanelPlan,
    slot_id: str,
) -> tuple[str, ...]:
    slot = next(
        item for item in plan.proposal.required_slots if item.slot_id == slot_id
    )
    return slot.blocking_authority


__all__ = [
    "CanonicalFindingLedgerWriter",
    "CanonicalFindingMutationAuthority",
    "ExecutionFindingTrustResolver",
]
