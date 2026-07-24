from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from tests.unit.stage_review.test_resources import (
    _OWNER,
    _capacity,
    _final_reservation,
    _governor,
    _now,
    _policy,
    _proposal,
)
from tests.unit.stage_review.test_session import (
    CANDIDATE,
    NOW,
    PROJECT,
    SESSION,
    SNAPSHOT,
    WORK_ITEM,
    _binding_set,
    _FindingWriter,
    _Fixture,
    _pass_command,
    _plan_request,
    _Resolver,
    _review_authority,
    _risk_profile,
    _start_fixture,
)

from ai_sdlc.core.stage_review import resource_certificate_inputs
from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.binding_authority_validation import (
    _validate_binding_authority_snapshot,
)
from ai_sdlc.core.stage_review.binding_digests import (
    reviewer_binding_set_digest,
)
from ai_sdlc.core.stage_review.binding_lineage import (
    dispatch_assignment_matches_binding,
)
from ai_sdlc.core.stage_review.binding_result_models import (
    ReviewerBinding,
    ReviewerBindingSet,
    ReviewerDispatchAssignment,
)
from ai_sdlc.core.stage_review.bindings import (
    BindingAuthoritySnapshot,
    build_binding_authority_snapshot,
    build_provider_binding_descriptor,
)
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.certificate_artifact_codec import (
    _decode_certificate_artifact as decode_certificate_artifact,
)
from ai_sdlc.core.stage_review.certificate_models import StageCloseCertificate
from ai_sdlc.core.stage_review.certificate_receipt_store import (
    FilesystemReviewReceiptArtifactStore,
    ReceiptArtifactError,
)
from ai_sdlc.core.stage_review.certificate_receipt_validation import (
    _validate_review_pass_receipts as validate_review_pass_receipts,
)
from ai_sdlc.core.stage_review.certificates import (
    CertificateInvalidError,
    StageCloseCertificateAuthority,
    StageCloseCertificateRequest,
    StageCloseEvidence,
    StageCloseIntent,
    validate_certificate_inputs,
    validate_reconciled_certificate_inputs,
)
from ai_sdlc.core.stage_review.close_models import CloseConsumptionClaim
from ai_sdlc.core.stage_review.finding_digests import ledger_digest
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.isolation_execution import (
    build_isolation_execution_permit,
)
from ai_sdlc.core.stage_review.isolation_launch_models import IsolationProcessResult
from ai_sdlc.core.stage_review.isolation_models import (
    IsolationExecutionPermit,
    IsolationExecutionReceipt,
    _permit_digest,
    _receipt_digest,
)
from ai_sdlc.core.stage_review.isolation_receipts import build_execution_receipt
from ai_sdlc.core.stage_review.panel_digests import panel_proposal_digest
from ai_sdlc.core.stage_review.panel_finalization import _build_reviewer_panel_plan
from ai_sdlc.core.stage_review.panel_plan_models import ReviewerPanelProposal
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderInvocation,
    ProviderRecoveryCapabilities,
    projection_digest,
    provider_execution_evidence_root_digest,
)
from ai_sdlc.core.stage_review.provider_transport_models import (
    ProviderEgressPermit,
    ProviderEgressReceipt,
    _build_provider_execution_identity,
)
from ai_sdlc.core.stage_review.provider_transport_models import (
    _transport_artifact_digest as transport_artifact_digest,
)
from ai_sdlc.core.stage_review.provider_transport_models import (
    _transport_artifact_id as transport_artifact_id,
)
from ai_sdlc.core.stage_review.resource_builders import parse_utc, stable_id, utc_iso
from ai_sdlc.core.stage_review.resource_ledger_models import (
    ResourceReconciliation,
    ResourceReservation,
)
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts
from ai_sdlc.core.stage_review.resources import (
    ResourceGovernor,
    build_budget_envelope,
)
from ai_sdlc.core.stage_review.session import (
    PlanRevocationCommand,
    StageReviewSessionService,
)
from ai_sdlc.core.stage_review.session_artifact_models import (
    ReviewerPlanRevocation,
    ReviewPass,
)
from ai_sdlc.core.stage_review.session_contracts import SessionIntegrityError
from ai_sdlc.core.stage_review.session_models import SessionMutationResult


@dataclass
class _ResourceAuthority:
    final: ResourceReservation
    current: ResourceReservation
    reconciliation: ResourceReconciliation

    def get_reservation(self, reservation_id: str) -> ResourceReservation:
        assert reservation_id == self.current.reservation_id
        return self.current

    def get_reservation_ancestor(
        self,
        reservation_id: str,
        reservation_digest: str,
    ) -> ResourceReservation | None:
        if (
            reservation_id == self.final.reservation_id
            and reservation_digest == self.final.reservation_digest
        ):
            return self.final
        return None

    def get_reconciliation(self, reconciliation_digest: str) -> ResourceReconciliation:
        assert reconciliation_digest == self.reconciliation.reconciliation_digest
        return self.reconciliation

    @contextmanager
    def hold_certificate_inputs(
        self,
        reservation_id: str,
        final_reservation_digest: str,
        reconciliation_digest: str,
    ) -> Iterator[
        tuple[ResourceReservation, ResourceReservation, ResourceReconciliation]
    ]:
        assert reservation_id == self.final.reservation_id
        assert final_reservation_digest == self.final.reservation_digest
        assert reconciliation_digest == self.reconciliation.reconciliation_digest
        yield self.final, self.current, self.reconciliation


@dataclass
class _CloseContextAuthority:
    evidence: StageCloseEvidence

    def resolve_current(self, intent: StageCloseIntent) -> StageCloseEvidence | None:
        del intent
        return self.evidence


@dataclass
class _ReadyCertificate:
    fixture: Any
    authorized: Any
    resources: ResourceGovernor
    final: ResourceReservation
    current: ResourceReservation
    reconciliation: ResourceReconciliation
    context_authority: _CloseContextAuthority
    receipt_store: FilesystemReviewReceiptArtifactStore
    authority: StageCloseCertificateAuthority
    request: StageCloseCertificateRequest
    certificate: StageCloseCertificate


def test_certificate_inputs_freeze_exact_binding_authority_snapshot(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)

    with ready.fixture.service.hold_certificate_inputs(ready.fixture.scope) as inputs:
        assert (
            inputs.authority_snapshot.snapshot_digest
            == inputs.binding_set.authority_snapshot_digest
        )
        assert {
            item.descriptor_digest
            for item in inputs.authority_snapshot.provider_descriptors
        } == {item.provider_descriptor_digest for item in inputs.binding_set.bindings}


def test_provider_authority_rejects_prefix_matched_forged_capability(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    trusted = _certificate_binding_authority(ready)
    descriptor = trusted.provider_descriptors[0]
    forged_descriptor = build_provider_binding_descriptor(
        descriptor_id=descriptor.descriptor_id,
        provider_id=descriptor.provider_id,
        equivalence_class_id=descriptor.equivalence_class_id,
        model_family=descriptor.model_family,
        role_contract_digests=descriptor.role_contract_digests,
        capability_ids=(*descriptor.capability_ids, "capability.forged"),
        provider_tags=descriptor.provider_tags,
        tool_allowlist=descriptor.tool_allowlist,
        recovery_capabilities=descriptor.recovery_capabilities,
        execution_route=descriptor.execution_route,
        isolation_backend=descriptor.isolation_backend,
        network_enforcement=descriptor.network_enforcement,
        supported_independence_grade=descriptor.supported_independence_grade,
        provider_policy_evidence_digest=descriptor.provider_policy_evidence_digest,
    )
    forged_authority = build_binding_authority_snapshot(
        plan=ready.fixture.plan,
        risk_level=trusted.risk_level,
        enforcement_mode=trusted.enforcement_mode,
        provider_descriptors=tuple(
            forged_descriptor
            if item.descriptor_digest == descriptor.descriptor_digest
            else item
            for item in trusted.provider_descriptors
        ),
        attestor_id=trusted.attestor_id,
        attestor_version=trusted.attestor_version,
        attestation_evidence_digest=trusted.attestation_evidence_digest,
    )

    with pytest.raises(ValueError, match="provider descriptor catalog"):
        _validate_binding_authority_snapshot(
            ready.fixture.plan,
            forged_authority,
            ready.fixture.resolver.bindings[ready.authorized.active_binding_set_digest],
            tuple(ready.fixture.resolver.assignments.values()),
        )


def test_complete_same_candidate_quorum_issues_one_current_certificate(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    certificate = ready.certificate
    replay = ready.authority.issue(ready.request)

    assert replay == certificate
    assert certificate.scope == ready.fixture.scope
    assert certificate.command_id == ready.request.intent.command_id
    assert certificate.close_intent_digest == ready.request.intent.close_intent_digest
    assert certificate.session_revision == ready.authorized.revision
    assert (
        certificate.candidate_manifest_digest
        == ready.authorized.active_candidate_digest
    )
    assert certificate.task_risk_profile_digest == (
        ready.authorized.active_risk_profile_digest
    )
    assert certificate.registry_digest == ready.fixture.plan.proposal.registry_digest
    assert certificate.selection_policy_digest == (
        ready.fixture.plan.proposal.selection_policy_digest
    )
    assert certificate.budget_policy_digest == (
        ready.fixture.plan.proposal.budget_policy_digest
    )
    assert certificate.policy_digest == ready.authorized.policy_digest
    assert (
        certificate.optimization_snapshot_digest
        == ready.authorized.optimization_snapshot_digest
    )
    assert certificate.budget_revision == ready.authorized.budget_revision
    assert certificate.budget_grant_digests == ready.authorized.budget_grant_digests
    assert certificate.active_cohort_id == ready.authorized.active_cohort_id
    assert certificate.satisfied_slot_ids == (
        ready.fixture.plan.proposal.quorum.required_slot_ids
    )
    assert certificate.required_role_coverage_proof_digest.startswith("sha256:")
    assert certificate.quorum_policy_digest == (
        ready.fixture.plan.proposal.quorum.source_policy_digest
    )
    assert certificate.finding_ledger_digest == ready.authorized.finding_ledger_digest
    assert (
        certificate.test_evidence_digest == ready.request.evidence.test_evidence_digest
    )
    assert certificate.integrity_evidence_digest == (
        ready.request.evidence.integrity_evidence_digest
    )
    assert certificate.panel_plan_digest == ready.authorized.active_plan_digest
    assert certificate.binding_digest == ready.authorized.active_binding_set_digest
    assert (
        certificate.final_resource_reservation_digest == ready.final.reservation_digest
    )
    assert certificate.resource_reconciliation_digest == (
        ready.reconciliation.reconciliation_digest
    )
    assert certificate.resource_fencing_epoch == ready.reconciliation.fencing_token
    assert ready.authority.require_current(certificate, ready.request) == certificate
    assert ready.authority.certificate_path(certificate).is_file()

    ready.context_authority.evidence = StageCloseEvidence(
        candidate_manifest_digest=ready.authorized.active_candidate_digest,
        test_evidence_digest="sha256:test-evidence.changed",
        integrity_evidence_digest="sha256:integrity-evidence.changed",
        protected_path_set=ready.request.evidence.protected_path_set,
    )
    with pytest.raises(CertificateInvalidError, match="protected stage evidence"):
        ready.authority.require_current(certificate, ready.request)

    changed_request = ready.request.model_copy(
        update={"evidence": ready.context_authority.evidence}
    )
    replacement = ready.authority.issue(changed_request)

    assert replacement.certificate_id != certificate.certificate_id
    assert replacement.test_evidence_digest == "sha256:test-evidence.changed"
    assert ready.authority.require_current(replacement, changed_request) == replacement


def test_certificate_authority_composes_only_canonical_receipt_artifact_store(
    tmp_path: Path,
) -> None:
    fixture, resources = _canonical_fixture(tmp_path)
    evidence = StageCloseEvidence(
        candidate_manifest_digest=CANDIDATE,
        test_evidence_digest="sha256:test-evidence",
        integrity_evidence_digest="sha256:integrity-evidence",
        protected_path_set=("evidence/tests.json",),
    )

    StageCloseCertificateAuthority(
        fixture.service,
        resources,
        context_authority=_CloseContextAuthority(evidence),
        clock=lambda: NOW,
    )
    foreign = FilesystemReviewReceiptArtifactStore(
        tmp_path / "foreign-authority",
        project_id=PROJECT,
    )
    with pytest.raises(TypeError, match="receipt artifact resolver"):
        StageCloseCertificateAuthority(
            fixture.service,
            resources,
            context_authority=_CloseContextAuthority(evidence),
            receipt_artifact_resolver=foreign,
            clock=lambda: NOW,
        )


@pytest.mark.parametrize(
    "corruption",
    ("missing", "tampered", "cross-invocation", "response"),
)
def test_certificate_fails_closed_on_receipt_entity_corruption(
    tmp_path: Path,
    corruption: str,
) -> None:
    ready = _ready_certificate(tmp_path)
    with ready.fixture.service.hold_certificate_inputs(ready.fixture.scope) as inputs:
        passes = inputs.passes
    review_pass = passes[0]
    store = ready.receipt_store
    if corruption == "missing":
        path = store.artifact_path(
            "isolation-receipts", review_pass.isolation_receipt_digests[0]
        )
        path.unlink()
    elif corruption == "tampered":
        path = store.artifact_path(
            "isolation-receipts", review_pass.isolation_receipt_digests[0]
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["backend_epoch"] = "forged-backend-epoch"
        path.write_text(json.dumps(payload), encoding="utf-8")
    elif corruption == "cross-invocation":
        other = passes[1]
        source = store.artifact_path("invocations", other.invocation_id)
        target = store.artifact_path("invocations", review_pass.invocation_id)
        target.write_bytes(source.read_bytes())
    else:
        receipt = store.resolve_egress_receipt(review_pass.egress_receipt_digests[0])
        path = store.artifact_path("responses", receipt.response_digest)
        path.write_text(json.dumps({"status": "tampered"}), encoding="utf-8")

    with pytest.raises(CertificateInvalidError, match="receipt authority"):
        ready.authority.require_current(ready.certificate, ready.request)


@pytest.mark.parametrize("receipt_kind", ("isolation", "egress"))
def test_receipt_entity_validation_rejects_wrong_order(
    tmp_path: Path,
    receipt_kind: str,
) -> None:
    ready = _ready_certificate(tmp_path)
    review_pass = _certificate_passes(ready)[0]
    store = _copy_receipt_authority(ready, tmp_path / receipt_kind, review_pass)
    invocation = ready.receipt_store.resolve_invocation(review_pass.invocation_id)

    if receipt_kind == "isolation":
        receipt = ready.receipt_store.resolve_isolation_receipt(
            review_pass.isolation_receipt_digests[0]
        )
        permit = ready.receipt_store.resolve_isolation_permit(receipt.permit_digest)
        prior = build_execution_receipt(
            permit,
            "query",
            IsolationProcessResult(
                return_code=0,
                stdout="prior-review",
                stderr="",
                process_id=999,
                parent_process_id=1,
                boundary_results=(),
                os_native_denials=(),
                before_digest="sha256:protected-state",
                after_digest="sha256:protected-state",
                cleanup_succeeded=True,
            ),
            parse_utc(receipt.recorded_at) - timedelta(seconds=1),
        )
        store.persist_isolation_receipt(prior)
        isolation = (*review_pass.isolation_receipt_digests, prior.receipt_digest)
        egress = review_pass.egress_receipt_digests
    else:
        receipt = ready.receipt_store.resolve_egress_receipt(
            review_pass.egress_receipt_digests[0]
        )
        permit = ready.receipt_store.resolve_egress_permit(receipt.permit_digest)
        prior_permit, prior = _prior_egress_pair(permit, receipt)
        store.persist_egress_permit(prior_permit)
        store.persist_egress_receipt(prior)
        isolation = review_pass.isolation_receipt_digests
        egress = (prior.receipt_digest, *review_pass.egress_receipt_digests)

    forged_pass = _persist_forged_invocation(
        store,
        invocation,
        review_pass,
        isolation,
        egress,
    )
    binding, assignment = _certificate_pass_authority(ready, review_pass)
    with pytest.raises(ValueError, match=f"{receipt_kind} receipt order"):
        validate_review_pass_receipts(
            forged_pass,
            store,
            binding,
            assignment,
            _certificate_binding_authority(ready),
        )


def test_receipt_entity_validation_rejects_opaque_digest(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    review_pass = _certificate_passes(ready)[0]
    store = FilesystemReviewReceiptArtifactStore(
        tmp_path / "opaque",
        project_id=PROJECT,
    )
    invocation = ready.receipt_store.resolve_invocation(review_pass.invocation_id)
    forged_pass = _persist_forged_invocation(
        store,
        invocation,
        review_pass,
        ("sha256:opaque-isolation-receipt",),
        (),
    )
    binding, assignment = _certificate_pass_authority(ready, review_pass)

    with pytest.raises(ReceiptArtifactError, match="unavailable"):
        validate_review_pass_receipts(
            forged_pass,
            store,
            binding,
            assignment,
            _certificate_binding_authority(ready),
        )


def test_receipt_entity_validation_rejects_execution_identity_drift(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    review_pass = _certificate_passes(ready)[0]
    store = _copy_receipt_authority(ready, tmp_path / "identity-drift", review_pass)
    invocation = ready.receipt_store.resolve_invocation(review_pass.invocation_id)
    receipt = ready.receipt_store.resolve_egress_receipt(
        review_pass.egress_receipt_digests[0]
    )
    identity = receipt.execution_identity
    forged_identity = _build_provider_execution_identity(
        execution_scope=identity.execution_scope,
        provider_id=identity.provider_id,
        provider_descriptor_digest=identity.provider_descriptor_digest,
        equivalence_class_id=identity.equivalence_class_id,
        model_family=identity.model_family,
        capability_ids=identity.capability_ids,
        recovery_capabilities=identity.recovery_capabilities,
        provider_adapter_id=identity.provider_adapter_id,
        provider_adapter_version=identity.provider_adapter_version,
        driver_factory_id=identity.driver_factory_id,
        driver_factory_version=identity.driver_factory_version,
        broker_id=identity.broker_id,
        physical_provider_id="physical-provider.forged",
        physical_equivalence_class_id=identity.physical_equivalence_class_id,
    )
    values = receipt.model_dump(mode="json")
    values.update(execution_identity=forged_identity, receipt_digest="")
    draft = ProviderEgressReceipt.model_construct(**values)
    forged_receipt = ProviderEgressReceipt.model_validate(
        {
            **values,
            "receipt_digest": transport_artifact_digest(draft, "receipt_digest"),
        }
    )
    store.persist_egress_receipt(forged_receipt)
    forged_pass = _persist_forged_invocation(
        store,
        invocation,
        review_pass,
        review_pass.isolation_receipt_digests,
        (forged_receipt.receipt_digest,),
    )
    binding, assignment = _certificate_pass_authority(ready, review_pass)

    with pytest.raises(ValueError, match="execution identity"):
        validate_review_pass_receipts(
            forged_pass,
            store,
            binding,
            assignment,
            _certificate_binding_authority(ready),
        )


def test_receipt_entity_validation_rejects_permit_identity_drift(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    review_pass = _certificate_passes(ready)[0]
    store = _copy_receipt_authority(ready, tmp_path / "permit-drift", review_pass)
    invocation = ready.receipt_store.resolve_invocation(review_pass.invocation_id)
    receipt = ready.receipt_store.resolve_egress_receipt(
        review_pass.egress_receipt_digests[0]
    )
    permit = ready.receipt_store.resolve_egress_permit(receipt.permit_digest)
    identity = permit.execution_identity
    forged_identity = _build_provider_execution_identity(
        execution_scope=identity.execution_scope,
        provider_id=identity.provider_id,
        provider_descriptor_digest=identity.provider_descriptor_digest,
        equivalence_class_id=identity.equivalence_class_id,
        model_family=identity.model_family,
        capability_ids=identity.capability_ids,
        recovery_capabilities=identity.recovery_capabilities,
        provider_adapter_id=identity.provider_adapter_id,
        provider_adapter_version=identity.provider_adapter_version,
        driver_factory_id=identity.driver_factory_id,
        driver_factory_version=identity.driver_factory_version,
        broker_id=identity.broker_id,
        physical_provider_id="physical-provider.forged-permit",
        physical_equivalence_class_id=identity.physical_equivalence_class_id,
    )
    permit_values = permit.model_dump(mode="json")
    permit_values.update(execution_identity=forged_identity, permit_digest="")
    permit_draft = ProviderEgressPermit.model_construct(**permit_values)
    forged_permit = ProviderEgressPermit.model_validate(
        {
            **permit_values,
            "permit_digest": transport_artifact_digest(
                permit_draft,
                "permit_digest",
            ),
        }
    )
    forged_receipt = _rehashed_egress_receipt(
        receipt,
        permit_digest=forged_permit.permit_digest,
        receipt_id=transport_artifact_id(
            "provider-egress-receipt",
            forged_permit.permit_digest,
        ),
    )
    store.persist_egress_permit(forged_permit)
    store.persist_egress_receipt(forged_receipt)
    forged_pass = _persist_forged_invocation(
        store,
        invocation,
        review_pass,
        review_pass.isolation_receipt_digests,
        (forged_receipt.receipt_digest,),
    )
    binding, assignment = _certificate_pass_authority(ready, review_pass)

    with pytest.raises(ValueError, match="execution identity"):
        validate_review_pass_receipts(
            forged_pass,
            store,
            binding,
            assignment,
            _certificate_binding_authority(ready),
        )


def test_receipt_entity_validation_requires_remote_provider_execution(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    review_pass = _certificate_passes(ready)[0]
    store = _copy_receipt_authority(ready, tmp_path / "local-only", review_pass)
    invocation = ready.receipt_store.resolve_invocation(review_pass.invocation_id)
    receipt = ready.receipt_store.resolve_egress_receipt(
        review_pass.egress_receipt_digests[0]
    )
    forged_receipt = _rehashed_egress_receipt(
        receipt,
        remote_provider_exercised=False,
    )
    store.persist_egress_receipt(forged_receipt)
    forged_pass = _persist_forged_invocation(
        store,
        invocation,
        review_pass,
        review_pass.isolation_receipt_digests,
        (forged_receipt.receipt_digest,),
    )
    binding, assignment = _certificate_pass_authority(ready, review_pass)

    with pytest.raises(ValueError, match="remote provider execution"):
        validate_review_pass_receipts(
            forged_pass,
            store,
            binding,
            assignment,
            _certificate_binding_authority(ready),
        )


@pytest.mark.parametrize(
    "field",
    ("transport_contract_digest", "transport_authority_digest"),
)
def test_receipt_entity_validation_rejects_transport_authority_drift(
    tmp_path: Path,
    field: str,
) -> None:
    ready = _ready_certificate(tmp_path)
    review_pass = _certificate_passes(ready)[0]
    store = _copy_receipt_authority(ready, tmp_path / field, review_pass)
    invocation = ready.receipt_store.resolve_invocation(review_pass.invocation_id)
    receipt = ready.receipt_store.resolve_egress_receipt(
        review_pass.egress_receipt_digests[0]
    )
    forged_receipt = _rehashed_egress_receipt(
        receipt,
        **{field: f"sha256:forged-{field}"},
    )
    store.persist_egress_receipt(forged_receipt)
    forged_pass = _persist_forged_invocation(
        store,
        invocation,
        review_pass,
        review_pass.isolation_receipt_digests,
        (forged_receipt.receipt_digest,),
    )
    binding, assignment = _certificate_pass_authority(ready, review_pass)

    with pytest.raises(ValueError, match="trusted transport authority"):
        validate_review_pass_receipts(
            forged_pass,
            store,
            binding,
            assignment,
            _certificate_binding_authority(ready),
        )


def test_receipt_entity_validation_rejects_jointly_forged_transport_authority(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    review_pass = _certificate_passes(ready)[0]
    store = _copy_receipt_authority(
        ready,
        tmp_path / "joint-transport-forgery",
        review_pass,
    )
    invocation = ready.receipt_store.resolve_invocation(review_pass.invocation_id)
    receipt = ready.receipt_store.resolve_egress_receipt(
        review_pass.egress_receipt_digests[0]
    )
    permit = ready.receipt_store.resolve_egress_permit(receipt.permit_digest)
    permit_values = permit.model_dump(mode="json")
    permit_values.update(
        execution_identity=permit.execution_identity,
        transport_contract_digest="sha256:jointly-forged-contract",
        transport_authority_digest="sha256:jointly-forged-authority",
        permit_digest="",
    )
    permit_draft = ProviderEgressPermit.model_construct(**permit_values)
    forged_permit = ProviderEgressPermit.model_validate(
        {
            **permit_values,
            "permit_digest": transport_artifact_digest(
                permit_draft,
                "permit_digest",
            ),
        }
    )
    forged_receipt = _rehashed_egress_receipt(
        receipt,
        receipt_id=transport_artifact_id(
            "provider-egress-receipt",
            forged_permit.permit_digest,
        ),
        permit_digest=forged_permit.permit_digest,
        transport_contract_digest="sha256:jointly-forged-contract",
        transport_authority_digest="sha256:jointly-forged-authority",
    )
    store.persist_egress_permit(forged_permit)
    store.persist_egress_receipt(forged_receipt)
    forged_pass = _persist_forged_invocation(
        store,
        invocation,
        review_pass,
        review_pass.isolation_receipt_digests,
        (forged_receipt.receipt_digest,),
    )
    binding, assignment = _certificate_pass_authority(ready, review_pass)

    with pytest.raises(ValueError, match="trusted transport authority"):
        validate_review_pass_receipts(
            forged_pass,
            store,
            binding,
            assignment,
            _certificate_binding_authority(ready),
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        (
            "release_manifest_digest",
            "sha256:jointly-forged-release",
            "provider release",
        ),
        (
            "host_snapshot_digest",
            "sha256:jointly-forged-host",
            "host snapshot",
        ),
    ),
)
def test_receipt_entity_validation_rejects_jointly_forged_isolation_authority(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    ready = _ready_certificate(tmp_path)
    review_pass = _certificate_passes(ready)[0]
    store = _copy_receipt_authority(
        ready,
        tmp_path / f"joint-isolation-{field}",
        review_pass,
    )
    invocation = ready.receipt_store.resolve_invocation(review_pass.invocation_id)
    receipt = ready.receipt_store.resolve_isolation_receipt(
        review_pass.isolation_receipt_digests[0]
    )
    permit = ready.receipt_store.resolve_isolation_permit(receipt.permit_digest)
    forged_permit, forged_receipt = _rehashed_isolation_pair(
        permit,
        receipt,
        **{field: value},
    )
    store.persist_isolation_permit(forged_permit)
    store.persist_isolation_receipt(forged_receipt)
    forged_pass = _persist_forged_invocation(
        store,
        invocation,
        review_pass,
        (forged_receipt.receipt_digest,),
        review_pass.egress_receipt_digests,
    )
    binding, assignment = _certificate_pass_authority(ready, review_pass)

    with pytest.raises(ValueError, match=message):
        validate_review_pass_receipts(
            forged_pass,
            store,
            binding,
            assignment,
            _certificate_binding_authority(ready),
        )


@pytest.mark.parametrize("validation_mode", ("offline", "recovery"))
@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("release_manifest_digest", "sha256:jointly-forged-release"),
        ("host_snapshot_digest", "sha256:jointly-forged-host"),
    ),
)
def test_certificate_entry_points_reject_jointly_forged_isolation_authority(
    tmp_path: Path,
    validation_mode: str,
    field: str,
    value: str,
) -> None:
    ready = _ready_certificate(tmp_path)
    with ready.fixture.service.hold_certificate_inputs(ready.fixture.scope) as inputs:
        trusted_inputs = inputs
    review_pass = trusted_inputs.passes[0]
    store = _copy_all_receipt_authority(
        ready,
        tmp_path / f"{validation_mode}-{field}",
        trusted_inputs.passes,
    )
    invocation = ready.receipt_store.resolve_invocation(review_pass.invocation_id)
    receipt = ready.receipt_store.resolve_isolation_receipt(
        review_pass.isolation_receipt_digests[0]
    )
    permit = ready.receipt_store.resolve_isolation_permit(receipt.permit_digest)
    forged_permit, forged_receipt = _rehashed_isolation_pair(
        permit,
        receipt,
        **{field: value},
    )
    store.persist_isolation_permit(forged_permit)
    store.persist_isolation_receipt(forged_receipt)
    forged_pass = _persist_forged_invocation(
        store,
        invocation,
        review_pass,
        (forged_receipt.receipt_digest,),
        review_pass.egress_receipt_digests,
    )
    forged_inputs = replace(
        trusted_inputs,
        passes=(forged_pass, *trusted_inputs.passes[1:]),
    )

    with pytest.raises(CertificateInvalidError, match="receipt authority"):
        if validation_mode == "offline":
            validate_certificate_inputs(
                ready.request,
                forged_inputs,
                ready.final,
                ready.current,
                ready.reconciliation,
                store,
            )
        else:
            claim, recovery_inputs = _recovery_inputs(ready, forged_inputs)
            validate_reconciled_certificate_inputs(
                ready.request,
                recovery_inputs,
                ready.final,
                ready.current,
                ready.reconciliation,
                claim,
                store,
            )


def test_binding_set_rejects_rehashed_forged_independence_proof(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    payload = ready.fixture.binding_set.model_dump(mode="json")
    proof = ready.fixture.binding_set.independence_proofs[0]
    forged_proof = proof.model_copy(
        update={"independence_grade": "model_diversity_proven"}
    )
    payload.update(
        bindings=ready.fixture.binding_set.bindings,
        unbound_slot_ids=ready.fixture.binding_set.unbound_slot_ids,
        independence_proofs=(forged_proof,),
        binding_set_digest="",
    )
    draft = ReviewerBindingSet.model_construct(**payload)
    payload["binding_set_digest"] = reviewer_binding_set_digest(draft)

    with pytest.raises(ValueError, match="independence proof"):
        ReviewerBindingSet.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("provider_descriptor_digest", "sha256:forged-descriptor"),
        ("provider_execution_identity_digest", "sha256:forged-identity"),
        ("physical_provider_id", "physical-provider.forged"),
        ("physical_equivalence_class_id", "physical-equivalence.forged"),
        ("transport_profile_digest", "sha256:forged-transport-profile"),
        ("transport_contract_digest", "sha256:forged-transport-contract"),
        ("transport_authority_digest", "sha256:forged-transport-authority"),
        ("model_family", "model.forged"),
        (
            "recovery_capabilities",
            ProviderRecoveryCapabilities(
                idempotency_support=True,
                invocation_query_support=True,
                cost_metering_support=True,
            ),
        ),
    ),
)
def test_assignment_binding_lineage_rejects_execution_or_transport_drift(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    ready = _ready_certificate(tmp_path)
    review_pass = _certificate_passes(ready)[0]
    binding, assignment = _certificate_pass_authority(ready, review_pass)
    forged = assignment.model_copy(update={field: value})

    assert not dispatch_assignment_matches_binding(
        ready.fixture.binding_set,
        binding,
        forged,
    )


def test_explicit_plan_revocation_immediately_invalidates_certificate(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    revocation = ReviewerPlanRevocation(
        revocation_id="reviewer-plan-revocation.certificate",
        target_kind="plan",
        plan_digest=ready.authorized.active_plan_digest,
        profile_ids=(),
        capability_ids=(),
        reason_id="registry.capability-revoked",
        evidence_digest="sha256:revocation-evidence.certificate",
        issuer_id="governance.release-authority",
        issuer_authority_digest="sha256:governance-authority.1",
        replacement_version="",
        minimum_version="1.0.1",
        issued_at=NOW,
    )
    ready.fixture.resolver.revocations[revocation.revocation_digest] = revocation
    ready.fixture.service.revoke_plan(
        PlanRevocationCommand(
            scope=ready.fixture.scope,
            command_id="plan-revocation.certificate",
            idempotency_key="plan-revocation-key.certificate",
            expected_revision=ready.authorized.revision,
            revocation_digest=revocation.revocation_digest,
        )
    )

    with pytest.raises(CertificateInvalidError, match="certificate-ready"):
        ready.authority.require_current(ready.certificate, ready.request)


def test_second_close_command_cannot_rebind_existing_certificate(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    payload = ready.request.intent.model_dump(mode="json")
    payload.update(
        command_id="stage-close.implementation.2",
        idempotency_key="stage-close-key.implementation.2",
        close_intent_digest="",
    )
    intent = StageCloseIntent.model_validate(payload)
    request = ready.request.model_copy(update={"intent": intent})

    with pytest.raises(CertificateInvalidError, match="identity is already bound"):
        ready.authority.issue(request)


def test_review_pass_artifact_fork_invalidates_certificate(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    pass_ref = ready.authorized.pass_refs[0]
    original = ready.fixture.service._store.get_pass(
        ready.fixture.scope,
        pass_ref.pass_id,
    )
    payload = original.model_dump(mode="json")
    payload.update(actor_id="actor.forged", pass_digest="")
    forged = ReviewPass.model_validate(payload)
    path = (
        ready.fixture.service.projection_path(ready.fixture.scope).parent
        / "passes"
        / f"{pass_ref.pass_id}.json"
    )
    path.write_text(json.dumps(forged.model_dump(mode="json")), encoding="utf-8")

    with pytest.raises(SessionIntegrityError, match="pass"):
        ready.authority.require_current(ready.certificate, ready.request)


def test_active_cohort_artifact_fork_invalidates_certificate(tmp_path: Path) -> None:
    ready = _ready_certificate(tmp_path)
    cohort = ready.fixture.service.active_cohort(ready.fixture.scope)
    payload = cohort.model_dump(mode="json")
    payload.update(created_at="2026-07-21T14:05:00Z", cohort_digest="")
    fork = type(cohort).model_validate(payload)
    path = (
        ready.fixture.service.projection_path(ready.fixture.scope).parent
        / "cohorts"
        / f"{cohort.cohort_id}.json"
    )
    path.write_text(json.dumps(fork.model_dump(mode="json")), encoding="utf-8")

    with pytest.raises(SessionIntegrityError, match="cohort"):
        ready.authority.require_current(ready.certificate, ready.request)


def test_shared_state_binding_change_invalidates_certificate(tmp_path: Path) -> None:
    ready = _ready_certificate(tmp_path)
    binding_path = (
        ready.fixture.service._store.shared_root / "shared-state-binding.json"
    )
    binding_path.unlink()

    with pytest.raises(SessionIntegrityError, match="binding changed"):
        ready.authority.require_current(ready.certificate, ready.request)


def test_finding_ledger_change_invalidates_certificate(tmp_path: Path) -> None:
    ready = _ready_certificate(tmp_path)
    ledger = ready.fixture.finding_writer.current_ledger
    assert ledger is not None
    changed = ledger.model_copy(
        update={"revision": ledger.revision + 1, "ledger_digest": ""}
    )
    ready.fixture.finding_writer.current_ledger = changed.model_copy(
        update={"ledger_digest": ledger_digest(changed)}
    )

    with pytest.raises(CertificateInvalidError, match="ledger is not closeable"):
        ready.authority.require_current(ready.certificate, ready.request)


def test_unknown_certificate_schema_fails_closed(tmp_path: Path) -> None:
    ready = _ready_certificate(tmp_path)
    path = ready.authority.certificate_path(ready.certificate)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = "stage-close-certificate.v99"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SharedStateIntegrityError, match="artifact is invalid"):
        ready.authority.require_current(ready.certificate, ready.request)


def test_current_certificate_schema_rejects_hollow_consumable_artifact(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    payload = ready.certificate.model_dump(mode="json")
    payload["scope"] = {
        "project_id": "",
        "work_item_id": "",
        "stage_instance_id": "",
        "session_id": "",
    }
    empty_fields = {
        name
        for name, field in StageCloseCertificate.model_fields.items()
        if field.annotation is str and name not in {"issued_at", "schema_version"}
    }
    payload.update({name: "" for name in empty_fields})
    payload["satisfied_slot_ids"] = ()
    payload["certificate_id"] = stable_id(
        "stage-close-certificate",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        str(payload["loop_round_number"]),
        "",
        "",
        "",
        "",
        "",
    )
    payload["certificate_digest"] = ""

    with pytest.raises(ValueError, match="identity|digest|slot"):
        decode_certificate_artifact(payload)


def test_previous_certificate_schema_is_read_only_not_consumable(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    path = ready.authority.certificate_path(ready.certificate)
    payload = ready.certificate.model_dump(mode="json")
    payload["schema_version"] = "stage-close-certificate.v0"
    payload.pop("canonicalization_version")
    payload.pop("compatibility_mode")
    payload.pop("extensions")
    payload["certificate_digest"] = canonical_digest(
        {key: value for key, value in payload.items() if key != "certificate_digest"},
        CanonicalizationPolicy(),
    )
    path.write_text(json.dumps(payload), encoding="utf-8")
    legacy = decode_certificate_artifact(payload)

    assert legacy.compatibility_mode == "read-only-legacy"
    with pytest.raises(CertificateInvalidError, match="legacy.*read-only"):
        ready.authority.require_current(legacy, ready.request)


def test_real_resource_governor_holds_reconciled_certificate_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    governor = _governor(tmp_path)
    final = _final_reservation(governor)
    reconciled = governor.reconcile(
        final.reservation_id,
        lease_owner=_OWNER,
        expected_fencing_token=final.fencing_token,
        operation_id="operation.reconcile.certificate-inputs",
        now=_now(),
    )
    assert reconciled.reconciliation is not None
    monkeypatch.setattr(resource_certificate_inputs, "utc_now", lambda _: _now())

    with governor.hold_certificate_inputs(
        final.reservation_id,
        final.reservation_digest,
        reconciled.reconciliation.reconciliation_digest,
    ) as (held_final, held_current, held_reconciliation):
        assert held_final == final
        assert held_current == reconciled.reservation
        assert held_reconciliation == reconciled.reconciliation


def test_unbound_resource_protocol_cannot_issue_consumable_certificate(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)

    with pytest.raises(TypeError, match="canonical ResourceGovernor"):
        StageCloseCertificateAuthority(
            ready.fixture.service,
            cast(
                ResourceGovernor,
                _ResourceAuthority(
                    ready.final,
                    ready.current,
                    ready.reconciliation,
                ),
            ),
            context_authority=ready.context_authority,
            receipt_artifact_resolver=ready.receipt_store,
            clock=lambda: "2026-07-21T14:00:00Z",
        )


def _ready_certificate(tmp_path: Path) -> _ReadyCertificate:
    fixture, resources = _canonical_fixture(tmp_path)
    receipt_store = FilesystemReviewReceiptArtifactStore(
        tmp_path,
        project_id=PROJECT,
    )
    session = fixture.service.get(fixture.scope)
    slots = fixture.plan.proposal.quorum.required_slot_ids
    first = _submit_canonical(
        fixture, resources, receipt_store, session.revision, slots[0]
    )
    authorized = _submit_canonical(
        fixture,
        resources,
        receipt_store,
        first.session.revision,
        slots[1],
    ).session
    final = resources.get_reservation(authorized.resource_reservation_id)
    reconciled = resources.reconcile(
        final.reservation_id,
        lease_owner=_OWNER,
        expected_fencing_token=final.fencing_token,
        operation_id="resource-reconcile.certificate",
        now=_now(),
    )
    assert reconciled.reservation is not None
    assert reconciled.reconciliation is not None
    current = reconciled.reservation
    reconciliation = reconciled.reconciliation
    intent = StageCloseIntent(
        scope=fixture.scope,
        gate_id="stage-review.implementation",
        close_kind="implementation",
        target_status="closed",
        command_id="stage-close.implementation.1",
        idempotency_key="stage-close-key.implementation.1",
        loop_id="implementation-loop.1",
        loop_round_number=1,
    )
    evidence = StageCloseEvidence(
        candidate_manifest_digest=authorized.active_candidate_digest,
        test_evidence_digest="sha256:test-evidence.1",
        integrity_evidence_digest="sha256:integrity-evidence.1",
        protected_path_set=(
            "evidence/integrity.json",
            "evidence/tests.json",
            "specs/candidate-manifest.json",
        ),
    )
    context_authority = _CloseContextAuthority(evidence)
    authority = StageCloseCertificateAuthority(
        fixture.service,
        resources,
        context_authority=context_authority,
        clock=lambda: "2026-07-21T14:00:00Z",
    )
    request = StageCloseCertificateRequest(
        intent=intent,
        evidence=evidence,
        expected_session_revision=authorized.revision,
        resource_reconciliation_digest=reconciliation.reconciliation_digest,
    )
    return _ReadyCertificate(
        fixture=fixture,
        authorized=authorized,
        resources=resources,
        final=final,
        current=current,
        reconciliation=reconciliation,
        context_authority=context_authority,
        receipt_store=receipt_store,
        authority=authority,
        request=request,
        certificate=authority.issue(request),
    )


def _certificate_passes(ready: _ReadyCertificate) -> tuple[ReviewPass, ...]:
    with ready.fixture.service.hold_certificate_inputs(ready.fixture.scope) as inputs:
        return inputs.passes


def _certificate_pass_authority(
    ready: _ReadyCertificate,
    review_pass: ReviewPass,
) -> tuple[ReviewerBinding, ReviewerDispatchAssignment]:
    binding = next(
        item
        for item in ready.fixture.binding_set.bindings
        if item.slot_id == review_pass.slot_id
    )
    return binding, ready.fixture.resolver.assignments[review_pass.assignment_digest]


def _certificate_binding_authority(
    ready: _ReadyCertificate,
) -> BindingAuthoritySnapshot:
    with ready.fixture.service.hold_certificate_inputs(ready.fixture.scope) as inputs:
        return inputs.authority_snapshot


def _copy_receipt_authority(
    ready: _ReadyCertificate,
    root: Path,
    review_pass: ReviewPass,
) -> FilesystemReviewReceiptArtifactStore:
    source = ready.receipt_store
    target = FilesystemReviewReceiptArtifactStore(root, project_id=PROJECT)
    for digest in review_pass.isolation_receipt_digests:
        receipt = source.resolve_isolation_receipt(digest)
        target.persist_isolation_permit(
            source.resolve_isolation_permit(receipt.permit_digest)
        )
        target.persist_isolation_receipt(receipt)
    for digest in review_pass.egress_receipt_digests:
        receipt = source.resolve_egress_receipt(digest)
        target.persist_egress_permit(
            source.resolve_egress_permit(receipt.permit_digest)
        )
        assert target.persist_response(
            source.resolve_response(receipt.response_digest)
        ) == (receipt.response_digest)
        target.persist_egress_receipt(receipt)
    return target


def _copy_all_receipt_authority(
    ready: _ReadyCertificate,
    root: Path,
    passes: tuple[ReviewPass, ...],
) -> FilesystemReviewReceiptArtifactStore:
    target = FilesystemReviewReceiptArtifactStore(root, project_id=PROJECT)
    for review_pass in passes:
        target = _copy_receipt_authority(ready, root, review_pass)
    return target


def _recovery_inputs(
    ready: _ReadyCertificate,
    inputs: Any,
) -> tuple[CloseConsumptionClaim, Any]:
    certificate = ready.certificate
    claim = CloseConsumptionClaim(
        claim_id=stable_id("close-consumption-claim", certificate.certificate_id),
        scope=inputs.session.scope,
        certificate_id=certificate.certificate_id,
        certificate_digest=certificate.certificate_digest,
        certificate_revision=certificate.certificate_revision,
        session_start_revision=inputs.session.revision,
        command_id="stage-close.aborted",
        idempotency_key="stage-close-key.aborted",
        close_intent_digest="sha256:aborted-close-intent",
        candidate_manifest_digest=certificate.candidate_manifest_digest,
        protected_path_set=certificate.protected_path_set,
        artifact_path=".ai-sdlc/state/aborted-close.json",
        content_contract_digest="sha256:aborted-close-content",
        worktree_identity="worktree.aborted",
        final_resource_reservation_digest=ready.final.reservation_digest,
        resource_reconciliation_digest=ready.reconciliation.reconciliation_digest,
        fencing_epoch=ready.current.fencing_token,
        prepared_at="2026-07-21T13:00:00Z",
    )
    projection = inputs.session.projection.model_copy(
        update={
            "state": "needs_user",
            "close_failure_reason": "governed_close_abort",
            "close_governance_decision_digest": "sha256:governed-close-abort",
            "active_close_certificate_id": claim.certificate_id,
            "active_close_certificate_digest": claim.certificate_digest,
            "active_close_claim_id": claim.claim_id,
            "active_close_claim_digest": claim.claim_digest,
        }
    )
    session = inputs.session.model_copy(update={"projection": projection})
    return claim, replace(inputs, session=session)


def _persist_forged_invocation(
    store: FilesystemReviewReceiptArtifactStore,
    invocation: ProviderInvocation,
    review_pass: ReviewPass,
    isolation: tuple[str, ...],
    egress: tuple[str, ...],
) -> ReviewPass:
    root = provider_execution_evidence_root_digest(isolation, egress)
    draft = invocation.model_copy(
        update={
            "isolation_receipt_digests": isolation,
            "egress_receipt_digests": egress,
            "execution_evidence_root_digest": root,
            "projection_digest": "",
        }
    )
    forged = ProviderInvocation.model_validate(
        {
            **draft.model_dump(mode="json"),
            "projection_digest": projection_digest(draft),
        }
    )
    store.persist_invocation(forged)
    return review_pass.model_copy(
        update={
            "invocation_projection_digest": forged.projection_digest,
            "isolation_receipt_digests": isolation,
            "egress_receipt_digests": egress,
            "execution_evidence_root_digest": root,
        }
    )


def _prior_egress_pair(
    permit: ProviderEgressPermit,
    receipt: ProviderEgressReceipt,
) -> tuple[ProviderEgressPermit, ProviderEgressReceipt]:
    permit_values = permit.model_dump(mode="json")
    permit_values.update(
        permit_id=transport_artifact_id("provider-egress-permit", "prior-turn"),
        execution_identity=permit.execution_identity,
        turn_index=2,
        nonce=f"{permit.nonce}-prior",
        permit_digest="",
    )
    permit_draft = ProviderEgressPermit.model_construct(**permit_values)
    prior_permit = ProviderEgressPermit.model_validate(
        {
            **permit_values,
            "permit_digest": transport_artifact_digest(permit_draft, "permit_digest"),
        }
    )
    receipt_values = receipt.model_dump(mode="json")
    receipt_values.update(
        receipt_id=transport_artifact_id(
            "provider-egress-receipt", prior_permit.permit_digest
        ),
        permit_digest=prior_permit.permit_digest,
        execution_identity=receipt.execution_identity,
        turn_index=2,
        recorded_at=utc_iso(parse_utc(receipt.recorded_at) + timedelta(seconds=1)),
        receipt_digest="",
    )
    receipt_draft = ProviderEgressReceipt.model_construct(**receipt_values)
    prior_receipt = ProviderEgressReceipt.model_validate(
        {
            **receipt_values,
            "receipt_digest": transport_artifact_digest(
                receipt_draft, "receipt_digest"
            ),
        }
    )
    return prior_permit, prior_receipt


def _rehashed_egress_receipt(
    receipt: ProviderEgressReceipt,
    **updates: object,
) -> ProviderEgressReceipt:
    values = receipt.model_dump(mode="json")
    values["execution_identity"] = receipt.execution_identity
    values.update(updates)
    values["receipt_digest"] = ""
    draft = ProviderEgressReceipt.model_construct(**values)
    return ProviderEgressReceipt.model_validate(
        {
            **values,
            "receipt_digest": transport_artifact_digest(draft, "receipt_digest"),
        }
    )


def _rehashed_isolation_pair(
    permit: IsolationExecutionPermit,
    receipt: IsolationExecutionReceipt,
    **updates: object,
) -> tuple[IsolationExecutionPermit, IsolationExecutionReceipt]:
    permit_draft = permit.model_copy(
        update={**updates, "permit_digest": ""},
    )
    forged_permit = IsolationExecutionPermit.model_validate(
        {
            **permit_draft.model_dump(mode="json"),
            "permit_digest": _permit_digest(permit_draft),
        }
    )
    receipt_draft = receipt.model_copy(
        update={
            **updates,
            "permit_digest": forged_permit.permit_digest,
            "receipt_digest": "",
        }
    )
    forged_receipt = IsolationExecutionReceipt.model_validate(
        {
            **receipt_draft.model_dump(mode="json"),
            "receipt_digest": _receipt_digest(receipt_draft),
        }
    )
    return forged_permit, forged_receipt


def _canonical_fixture(tmp_path: Path) -> tuple[_Fixture, ResourceGovernor]:
    policy = _policy()
    envelope = build_budget_envelope(
        project_id=PROJECT,
        work_item_id=WORK_ITEM,
        stage_review_session_id=SESSION,
        risk_level="low",
        budget_policy=policy,
        pool="foreground",
    )
    resources = ResourceGovernor(
        tmp_path,
        project_id=PROJECT,
        foreground_capacity=_capacity(),
        lock_timeout_seconds=5,
    )
    admission = resources.reserve_admission(
        envelope,
        budget_policy=policy,
        lease_owner=_OWNER,
        operation_id="resource-admission.certificate",
        lease_seconds=60,
        now=_now(),
    )
    assert admission.reservation is not None
    risk = _risk_profile("certificate", ("cap.a", "cap.b"))
    request = _plan_request(
        "certificate",
        stage_instance_id="implementation",
        required_capabilities=("cap.a", "cap.b"),
        risk_profile_digest=risk.profile_digest,
    )
    proposal = _session_proposal(
        envelope.envelope_digest,
        request_digest=request.request_digest,
        planning_context_digest=request.planning_context_digest,
    )
    finalized = resources.finalize_reservation(
        admission.reservation.reservation_id,
        proposal=proposal,
        lease_owner=_OWNER,
        expected_fencing_token=admission.reservation.fencing_token,
        operation_id="resource-final.certificate",
        now=_now(),
    )
    assert finalized.reservation is not None
    final = finalized.reservation
    plan = _build_reviewer_panel_plan(proposal, final)
    binding = _binding_set(
        plan,
        final,
        CANDIDATE,
        suffix="certificate",
        trusted_catalog=True,
    )
    binding = _binding_with_budget_policy(
        binding,
        proposal.budget_policy_digest,
    )
    resolver = _Resolver(
        plan_requests={request.request_digest: request},
        plans={plan.plan_digest: plan},
        bindings={binding.binding_set_digest: binding},
        reservations={final.reservation_digest: final},
    )
    resolver.risk_profiles[risk.profile_digest] = risk
    writer = _FindingWriter()
    scope = FindingScope(
        project_id=PROJECT,
        work_item_id=WORK_ITEM,
        stage_instance_id="implementation",
        session_id=SESSION,
    )
    service = StageReviewSessionService(
        tmp_path,
        project_id=PROJECT,
        trust_resolver=resolver,
        finding_ledger_writer=writer,
        clock=lambda: NOW,
    )
    fixture = _Fixture(service, resolver, scope, plan, binding, final, writer)
    _start_fixture(fixture, risk, suffix="certificate")
    return fixture, resources


def _session_proposal(
    envelope_digest: str,
    *,
    request_digest: str,
    planning_context_digest: str,
) -> ReviewerPanelProposal:
    base = _proposal(envelope_digest=envelope_digest)
    requirement = base.resource_requirement.model_copy(
        update={
            "required_provider_calls": 4,
            "total_provider_calls": 4,
            "required_review_passes": 4,
            "total_review_passes": 4,
            "required_tokens": 4000,
            "total_tokens": 4000,
            "required_cost": 4,
            "total_cost": 4,
            "required_wall_clock": 40,
            "total_wall_clock": 40,
        }
    )
    draft = base.model_copy(
        update={
            "optimization_snapshot_digest": SNAPSHOT,
            "request_digest": request_digest,
            "planning_context_digest": planning_context_digest,
            "resource_requirement": requirement,
            "proposal_digest": "",
        }
    )
    return ReviewerPanelProposal.model_validate(
        {
            **draft.model_dump(mode="json"),
            "proposal_digest": panel_proposal_digest(draft),
        }
    )


def _binding_with_budget_policy(binding: Any, policy_digest: str) -> Any:
    draft = binding.model_copy(
        update={
            "budget_policy_digest": policy_digest,
            "binding_set_digest": "",
        }
    )
    return type(binding).model_validate(
        {
            **draft.model_dump(mode="json"),
            "binding_set_digest": reviewer_binding_set_digest(draft),
        }
    )


def _submit_canonical(
    fixture: _Fixture,
    resources: ResourceGovernor,
    receipt_store: FilesystemReviewReceiptArtifactStore,
    revision: int,
    slot_id: str,
) -> SessionMutationResult:
    command = _pass_command(fixture, revision, slot_id)
    session = fixture.service.get(fixture.scope)
    assignment, invocation, _ = _review_authority(
        fixture,
        session,
        slot_id,
        command,
    )
    binding = next(
        item for item in fixture.binding_set.bindings if item.slot_id == slot_id
    )
    result = resources.record_usage(
        session.resource_reservation_id,
        delta=ResourceAmounts(
            provider_calls=1,
            review_passes=1,
            tokens=10,
            cost=1,
            active_wall_clock=1,
        ),
        lease_owner=_OWNER,
        expected_fencing_token=session.resource_fencing_epoch,
        operation_id=f"resource-pass.{command.command_id}",
        now=_now(),
    )
    assert result.reservation is not None
    invocation = invocation.model_copy(
        update={"settlement_reservation_digest": result.reservation.reservation_digest}
    )
    authority = fixture.resolver.resolve_binding_authority(
        fixture.binding_set.authority_snapshot_digest
    )
    assert authority is not None
    descriptor = next(
        item
        for item in authority.provider_descriptors
        if item.descriptor_digest == binding.provider_descriptor_digest
    )
    invocation = _bind_isolation_artifacts(
        invocation,
        receipt_store,
        revision,
        binding,
        assignment,
        descriptor.provider_policy_evidence_digest,
    )
    invocation = ProviderInvocation.model_validate(
        {
            **invocation.model_dump(mode="json"),
            "projection_digest": projection_digest(invocation),
        }
    )
    receipt_store.persist_invocation(invocation)
    fixture.resolver.assignments[assignment.assignment_digest] = assignment
    fixture.resolver.invocations[invocation.invocation_id] = invocation
    fixture.resolver.reservations[result.reservation.reservation_digest] = (
        result.reservation
    )
    return fixture.service.submit_pass(command)


def _bind_isolation_artifacts(
    invocation: ProviderInvocation,
    store: FilesystemReviewReceiptArtifactStore,
    revision: int,
    binding: ReviewerBinding,
    assignment: ReviewerDispatchAssignment,
    release_manifest_digest: str,
) -> ProviderInvocation:
    request = invocation.request
    now = _now() + timedelta(seconds=revision)
    permit = build_isolation_execution_permit(
        allocation_digest=f"sha256:allocation.{request.assignment_digest}",
        assignment_digest=request.assignment_digest,
        candidate_digest=request.candidate_digest,
        host_snapshot_digest=assignment.host_snapshot_digest,
        backend_id="codex.permission-profile",
        contract_version="2026-07-01",
        backend_version="0.138.0",
        backend_instance_id="codex.backend.instance",
        backend_epoch="codex.backend.epoch",
        normalized_run_root="/tmp/ai-sdlc-certificate-review",
        layout_digest="sha256:layout",
        filesystem_policy_digest="sha256:filesystem-policy",
        network_policy_digest="sha256:network-policy",
        manifest_digest="sha256:isolation-manifest",
        release_manifest_digest=release_manifest_digest,
        runtime_identity_digest="sha256:runtime-identity",
        issued_at=utc_iso(now - timedelta(seconds=1)),
        expires_at=utc_iso(now + timedelta(minutes=1)),
        nonce=f"certificate-{request.invocation_id}",
    )
    result = IsolationProcessResult(
        return_code=0,
        stdout="review-complete",
        stderr="",
        process_id=100 + revision,
        parent_process_id=1,
        boundary_results=(),
        os_native_denials=(),
        before_digest="sha256:protected-state",
        after_digest="sha256:protected-state",
        cleanup_succeeded=True,
    )
    receipt = build_execution_receipt(permit, "invoke", result, now)
    store.persist_isolation_permit(permit)
    store.persist_isolation_receipt(receipt)
    isolation_receipts = (receipt.receipt_digest,)
    egress_receipts = (_persist_egress_artifacts(invocation, store, now, binding),)
    return invocation.model_copy(
        update={
            "isolation_receipt_digests": isolation_receipts,
            "egress_receipt_digests": egress_receipts,
            "execution_evidence_root_digest": (
                provider_execution_evidence_root_digest(
                    isolation_receipts,
                    egress_receipts,
                )
            ),
            "projection_digest": "",
        }
    )


def _persist_egress_artifacts(
    invocation: ProviderInvocation,
    store: FilesystemReviewReceiptArtifactStore,
    now,
    binding: ReviewerBinding,
) -> str:
    request = invocation.request
    response_digest = store.persist_response({"status": "contract-ok"})
    execution_identity = binding.execution_identity
    values = {
        "permit_id": transport_artifact_id(
            "provider-egress-permit", request.invocation_id
        ),
        "invocation_id": request.invocation_id,
        "assignment_digest": request.assignment_digest,
        "provider_id": request.provider_id,
        "execution_identity": execution_identity,
        "request_digest": request.request_digest,
        "turn_index": 1,
        "idempotency_key": request.idempotency_key,
        "credential_view_digest": "sha256:credential-view",
        "backend_epoch": "provider-broker.epoch",
        "active_wall_clock_limit": request.anticipated_usage.active_wall_clock,
        "endpoint_id": "ipc://certificate/provider",
        "transport_contract_digest": binding.transport_contract_digest,
        "transport_authority_digest": binding.transport_authority_digest,
        "issued_at": utc_iso(now - timedelta(seconds=1)),
        "expires_at": utc_iso(now + timedelta(minutes=1)),
        "nonce": f"egress-{request.invocation_id}",
    }
    draft = ProviderEgressPermit.model_construct(**values, permit_digest="")
    permit = ProviderEgressPermit.model_validate(
        {
            **values,
            "permit_digest": transport_artifact_digest(draft, "permit_digest"),
        }
    )
    receipt_values = {
        **{
            key: getattr(permit, key)
            for key in (
                "invocation_id",
                "assignment_digest",
                "provider_id",
                "execution_identity",
                "request_digest",
                "turn_index",
                "idempotency_key",
                "credential_view_digest",
                "backend_epoch",
                "endpoint_id",
                "transport_contract_digest",
                "transport_authority_digest",
            )
        },
        "receipt_id": transport_artifact_id(
            "provider-egress-receipt", permit.permit_digest
        ),
        "permit_digest": permit.permit_digest,
        "response_digest": response_digest,
        "transport_contract_attested": True,
        "remote_provider_exercised": True,
        "recorded_at": utc_iso(now),
    }
    receipt_draft = ProviderEgressReceipt.model_construct(
        **receipt_values, receipt_digest=""
    )
    receipt = ProviderEgressReceipt.model_validate(
        {
            **receipt_values,
            "receipt_digest": transport_artifact_digest(
                receipt_draft, "receipt_digest"
            ),
        }
    )
    store.persist_egress_permit(permit)
    store.persist_egress_receipt(receipt)
    return receipt.receipt_digest
