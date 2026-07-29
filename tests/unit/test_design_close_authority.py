"""Design Close enforce authority 的可信绑定回归测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_sdlc.core.design_close_authority_store import _build_anchor
from ai_sdlc.core.design_close_enforce_authority import (
    _DesignCloseProof,
    _enforce_record_matches,
)
from ai_sdlc.core.design_close_enforce_evidence import _trusted_enforce_receipt
from ai_sdlc.core.design_contract_models import (
    DesignContractClose,
    DesignContractInput,
    DesignContractReport,
)
from ai_sdlc.core.design_contract_store import DesignContractArtifacts
from ai_sdlc.core.scope_authority_store import ScopeAuthorityIntegrityError
from ai_sdlc.core.stage_review.activation_models import (
    ActivationSessionObservation,
    ActivationSessionRecord,
)
from ai_sdlc.core.stage_review.close_models import StageCloseConsumptionReceipt
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.resource_builders import stable_id


def test_enforce_record_must_belong_to_current_project() -> None:
    scope = FindingScope(
        project_id="project.foreign",
        work_item_id="demo",
        stage_instance_id="dc-demo",
        session_id="session.foreign",
    )
    record = ActivationSessionRecord(
        record_id="record.foreign",
        project_id=scope.project_id,
        close_proof_kind="enforce-certificate",
        close_proof_id="certificate.foreign",
        close_proof_digest=_digest(b"certificate"),
        candidate_manifest_digest=_digest(b"candidate"),
        panel_plan_digest=_digest(b"panel"),
        review_session_digest=_digest(b"session"),
        review_completion_digest=_digest(b"completion"),
        scope=scope,
        observation=ActivationSessionObservation(
            session_id=scope.session_id,
            stage_key="design-contract",
            risk_level="medium",
            mode="enforce",
            completed_at="2026-07-27T12:00:00Z",
        ),
    )

    assert not _enforce_record_matches(
        record,
        project_id="project.current",
        stage_key="design-contract",
        work_item_id=scope.work_item_id,
        stage_instance_id=scope.stage_instance_id,
        candidate_manifest_digest=record.candidate_manifest_digest,
        expected=None,
    )


def test_anchor_rejects_close_artifact_not_bound_by_enforce_receipt(
    tmp_path: Path,
) -> None:
    loop_dir = tmp_path / ".ai-sdlc" / "loops" / "design-contract" / "dc-demo"
    loop_dir.mkdir(parents=True)
    input_path = loop_dir / "design-contract-input.json"
    report_path = loop_dir / "design-contract-report.json"
    close_path = loop_dir / "design-contract-close.json"
    contract_input = DesignContractInput(
        loop_id="dc-demo",
        work_item_id="demo",
        work_item_path="specs/demo",
        spec_path="specs/demo/spec.md",
        spec_digest=_digest(b"spec"),
        plan_path="specs/demo/plan.md",
        plan_digest=_digest(b"plan"),
        tasks_path="specs/demo/tasks.md",
        tasks_digest=_digest(b"tasks"),
    )
    report = DesignContractReport(
        loop_id="dc-demo",
        work_item_id="demo",
        work_item_path="specs/demo",
    )
    close = DesignContractClose(
        loop_id="dc-demo",
        report_path=report_path.relative_to(tmp_path).as_posix(),
    )
    _write_model(input_path, contract_input)
    _write_model(report_path, report)
    _write_model(close_path, close)
    receipt_close_digest = _digest(close_path.read_bytes())
    close_payload = close.model_dump(mode="json")
    close_payload["closed_by"] = "tampered-after-receipt"
    close_path.write_text(json.dumps(close_payload), encoding="utf-8")
    artifacts = DesignContractArtifacts(
        loop_dir=loop_dir,
        loop_run_path=loop_dir / "loop-run.json",
        input_path=input_path,
        coverage_matrix_path=loop_dir / "coverage-matrix.json",
        report_json_path=report_path,
        report_md_path=loop_dir / "design-contract-report.md",
        close_path=close_path,
        pointer_path=tmp_path / ".ai-sdlc" / "loops" / "current.json",
    )
    input_digest = _digest(input_path.read_bytes())
    report_digest = _digest(report_path.read_bytes())
    proof = _DesignCloseProof(
        kind="enforce-certificate",
        proof_id="certificate.demo",
        proof_digest=_digest(b"certificate"),
        operation_id="operation.demo",
        stage_input_digest=_digest(b"stage-input"),
        stage_key="design-contract",
        close_kind="closed",
        target_status="closed",
        candidate_manifest_digest=_digest(b"candidate"),
        candidate_artifact_digests=(
            (input_path.relative_to(tmp_path).as_posix(), input_digest),
            (report_path.relative_to(tmp_path).as_posix(), report_digest),
        ),
        marker_digest=_digest(b"marker"),
        close_artifact_digest=receipt_close_digest,
    )

    with pytest.raises(ScopeAuthorityIntegrityError):
        _build_anchor(
            tmp_path,
            contract_input,
            artifacts,
            proof,
            expected_artifact_digests=(
                (input_path.relative_to(tmp_path).as_posix(), input_digest),
                (report_path.relative_to(tmp_path).as_posix(), report_digest),
            ),
        )


def test_enforce_receipt_comes_from_consumed_event_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.core import design_close_enforce_evidence as authority

    record = _enforce_record()
    claim_digest = _digest(b"claim")
    receipt = StageCloseConsumptionReceipt(
        receipt_id=stable_id("stage-close-consumption-receipt", claim_digest),
        claim_id="claim.demo",
        claim_digest=claim_digest,
        certificate_id=record.close_proof_id,
        certificate_digest=record.close_proof_digest,
        command_id="command.demo",
        close_intent_digest=_digest(b"intent"),
        close_artifact_digest=_digest(b"close"),
        reconciled_event_digest=_digest(b"event"),
        final_resource_reservation_digest=_digest(b"reservation"),
        resource_reconciliation_digest=_digest(b"reconciliation"),
        fencing_epoch=1,
        committed_at=record.observation.completed_at,
    )
    shared_root = tmp_path / "shared"
    receipts_dir = shared_root / "receipts"
    receipts_dir.mkdir(parents=True)
    _write_model(receipts_dir / "claim.demo.json", receipt)
    session = SimpleNamespace(
        state="consumed",
        active_close_certificate_id=record.close_proof_id,
        active_close_certificate_digest=record.close_proof_digest,
        active_close_claim_id=receipt.claim_id,
        active_close_claim_digest=receipt.claim_digest,
        close_consumption_receipt_id=receipt.receipt_id,
        projection=SimpleNamespace(
            close_consumption_receipt_digest=receipt.receipt_digest,
        ),
    )

    class FakeSessionStore:
        def __init__(self, _root: Path, *, project_id: str) -> None:
            assert project_id == record.project_id

        def load_events(self, scope: FindingScope) -> tuple[object, ...]:
            assert scope == record.scope
            return (object(),)

    class FakeCloseStore:
        def __init__(
            self,
            _root: Path,
            *,
            project_id: str,
            lock_timeout_seconds: float,
        ) -> None:
            assert project_id == record.project_id
            assert lock_timeout_seconds == 2
            self.shared_root = shared_root
            self.receipts_dir = receipts_dir

    monkeypatch.setattr(authority, "SessionEventStore", FakeSessionStore)
    monkeypatch.setattr(authority, "StageCloseStore", FakeCloseStore)
    monkeypatch.setattr(
        authority,
        "reduce_session_events",
        lambda scope, events: session,
    )

    assert (
        _trusted_enforce_receipt(tmp_path, record, record.project_id)
        == receipt.close_artifact_digest
    )
    session.projection.close_consumption_receipt_digest = _digest(b"forged")
    with pytest.raises(ScopeAuthorityIntegrityError, match="identity diverged"):
        _trusted_enforce_receipt(tmp_path, record, record.project_id)


def _enforce_record() -> ActivationSessionRecord:
    scope = FindingScope(
        project_id="project.current",
        work_item_id="demo",
        stage_instance_id="dc-demo",
        session_id="session.current",
    )
    return ActivationSessionRecord(
        record_id="record.current",
        project_id=scope.project_id,
        close_proof_kind="enforce-certificate",
        close_proof_id="certificate.current",
        close_proof_digest=_digest(b"certificate"),
        candidate_manifest_digest=_digest(b"candidate"),
        panel_plan_digest=_digest(b"panel"),
        review_session_digest=_digest(b"session"),
        review_completion_digest=_digest(b"completion"),
        scope=scope,
        observation=ActivationSessionObservation(
            session_id=scope.session_id,
            stage_key="design-contract",
            risk_level="medium",
            mode="enforce",
            completed_at="2026-07-27T12:00:00Z",
        ),
    )


def _write_model(path: Path, model: object) -> None:
    payload = model.model_dump(mode="json")  # type: ignore[attr-defined]
    path.write_text(json.dumps(payload), encoding="utf-8")


def _digest(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"
