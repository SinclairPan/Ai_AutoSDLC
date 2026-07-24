from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.unit.stage_review.test_certificates import _ready_certificate
from tests.unit.stage_review.test_session import PROJECT

from ai_sdlc.core.stage_review.authorizer import (
    CloseClaimConflictError,
    StageCloseAuthorizer,
)
from ai_sdlc.core.stage_review.certificate_models import StageCloseIntent
from ai_sdlc.core.stage_review.close_governance import (
    StageCloseGovernanceAuthority,
)
from ai_sdlc.core.stage_review.close_governance_models import StageCloseAbortRequest
from ai_sdlc.core.stage_review.close_models import (
    CloseArtifactContract,
    StageCloseContext,
)

_NOW = "2026-07-21T15:00:00Z"
_GOVERNANCE_ACTOR = "actor.stage-close-governor"


def _context(ready: Any, authorizer: StageCloseAuthorizer) -> StageCloseContext:
    return StageCloseContext(
        certificate=ready.certificate,
        certificate_request=ready.request,
        close_artifact=CloseArtifactContract(
            artifact_path="close/implementation.json",
            payload={
                "status": "closed",
                "stage": "implementation",
                "command_id": ready.request.intent.command_id,
            },
        ),
        worktree_identity=authorizer.worktree_identity,
        lease_owner="owner.close",
        lease_seconds=60,
    )


def _authorizer(tmp_path: Path, ready: Any) -> StageCloseAuthorizer:
    return StageCloseAuthorizer(
        tmp_path,
        project_id=PROJECT,
        certificate_authority=ready.authority,
        governance_authority=_governance(tmp_path),
        clock=lambda: _NOW,
        lock_timeout_seconds=1,
    )


def _governance(
    root: Path,
    *,
    clock: Any = lambda: _NOW,
) -> StageCloseGovernanceAuthority:
    return StageCloseGovernanceAuthority(
        root,
        project_id=PROJECT,
        authority_id="authority.stage-close-governance",
        authorized_actor_ids=(_GOVERNANCE_ACTOR,),
        clock=clock,
    )


def _abort_request(suffix: str = "1") -> StageCloseAbortRequest:
    return StageCloseAbortRequest(
        actor_id=_GOVERNANCE_ACTOR,
        idempotency_key=f"stage-close-abort.{suffix}",
        reason_code="manual_reconciliation_required",
        reason="A release governor requires manual reconciliation.",
    )


def test_authorizer_requires_canonical_certificate_authority(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="canonical"):
        StageCloseAuthorizer(
            tmp_path,
            project_id=PROJECT,
            certificate_authority=object(),  # type: ignore[arg-type]
            governance_authority=_governance(tmp_path),
            clock=lambda: _NOW,
        )


def test_first_close_claim_writes_one_artifact_receipt_and_closed_state(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)

    result = authorizer.authorize_stage_close(context)
    replay = authorizer.authorize_stage_close(context)

    assert replay == result
    assert result.status == "closed"
    assert result.claim.certificate_id == ready.certificate.certificate_id
    assert result.claim.command_id == ready.request.intent.command_id
    assert result.receipt is not None
    assert result.receipt.claim_digest == result.claim.claim_digest
    assert result.receipt.final_resource_reservation_digest == (
        ready.certificate.final_resource_reservation_digest
    )
    assert result.receipt.resource_reconciliation_digest == (
        ready.certificate.resource_reconciliation_digest
    )
    assert result.receipt.fencing_epoch == ready.certificate.resource_fencing_epoch
    assert result.state.event_kinds == (
        "prepared",
        "close_written",
        "reconciled",
        "committed",
    )
    assert result.state.schema_version == "close-consumption-state.v1"
    assert result.state.certificate_id == ready.certificate.certificate_id
    assert result.state.consumed_by_command_id == ready.request.intent.command_id
    assert result.state.closed
    assert (tmp_path / result.claim.artifact_path).is_file()
    session = ready.fixture.service.get(ready.fixture.scope)
    assert session.state == "consumed"
    assert session.active_close_certificate_id == ready.certificate.certificate_id
    assert session.active_close_claim_id == result.claim.claim_id
    assert session.close_consumption_receipt_id == result.receipt.receipt_id


def test_authorizer_runs_product_writer_before_committing_formal_close(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    calls: list[str] = []

    def product_writer() -> None:
        assert not (tmp_path / context.close_artifact.artifact_path).exists()
        calls.append("written")

    result = authorizer.authorize_stage_close(
        context,
        before_close_artifact=product_writer,
    )
    authorizer.authorize_stage_close(
        context,
        before_close_artifact=lambda: (_ for _ in ()).throw(
            AssertionError("committed close reran the product writer")
        ),
    )

    assert result.status == "closed"
    assert calls == ["written"]


def test_existing_claim_rejects_another_close_command(tmp_path: Path) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    authorizer.authorize_stage_close(context)
    payload = ready.request.intent.model_dump(mode="json")
    payload.update(
        command_id="stage-close.implementation.other",
        idempotency_key="stage-close-key.implementation.other",
        close_intent_digest="",
    )
    other_intent = StageCloseIntent.model_validate(payload)
    other_request = ready.request.model_copy(update={"intent": other_intent})
    other_context = context.model_copy(update={"certificate_request": other_request})

    with pytest.raises(CloseClaimConflictError, match="another command"):
        authorizer.authorize_stage_close(other_context)


def test_claim_created_before_prepared_recovers_without_reclaiming_resource(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)

    def interrupt(phase: str) -> None:
        if phase == "claim_created":
            raise RuntimeError("simulated exit after claim")

    monkeypatch.setattr(authorizer, "_checkpoint", interrupt)
    with pytest.raises(RuntimeError, match="simulated exit"):
        authorizer.authorize_stage_close(context)

    monkeypatch.setattr(authorizer, "_checkpoint", lambda _phase: None)
    monkeypatch.setattr(
        ready.authority,
        "hold_current",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("recovery attempted a new certificate claim")
        ),
    )

    recovered = authorizer.authorize_stage_close(context)

    assert recovered.status == "closed"
    assert recovered.state.event_kinds[0] == "prepared"
