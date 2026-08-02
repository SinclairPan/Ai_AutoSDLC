from __future__ import annotations

import json
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.unit.stage_review.test_authorizer import (
    _abort_request,
    _authorizer,
    _context,
    _governance,
)
from tests.unit.stage_review.test_certificates import _ready_certificate
from tests.unit.stage_review.test_resources import _OWNER, _now, _policy

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.authorizer import StageCloseAuthorizer
from ai_sdlc.core.stage_review.certificate_models import StageCloseEvidence
from ai_sdlc.core.stage_review.certificates import CertificateInvalidError
from ai_sdlc.core.stage_review.close_builders import (
    _build_close_claim as build_close_claim,
)
from ai_sdlc.core.stage_review.close_governance import (
    GovernanceDecisionInvalidError,
)
from ai_sdlc.core.stage_review.close_governance_models import StageCloseAbortRequest
from ai_sdlc.core.stage_review.close_models import (
    CloseConsumptionClaim,
    CloseConsumptionEvent,
)
from ai_sdlc.core.stage_review.close_store import (
    CloseStoreConflictError,
    StageCloseStore,
)
from ai_sdlc.core.stage_review.resources import build_budget_envelope
from ai_sdlc.core.stage_review.session_contracts import GovernedCloseAbortCommand


@pytest.mark.parametrize(
    "phase",
    (
        "prepared",
        "artifact_written",
        "close_written",
        "reconciled",
        "receipt_created",
        "committed",
        "state_materialized",
    ),
)
def test_each_persisted_close_phase_recovers_without_duplicate_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)

    def interrupt(current: str) -> None:
        if current == phase:
            raise RuntimeError(f"simulated exit after {phase}")

    monkeypatch.setattr(authorizer, "_checkpoint", interrupt)
    with pytest.raises(RuntimeError, match="simulated exit"):
        authorizer.authorize_stage_close(context)
    monkeypatch.setattr(authorizer, "_checkpoint", lambda _phase: None)

    recovered = authorizer.authorize_stage_close(context)

    assert recovered.status == "closed"
    assert recovered.state.event_kinds == (
        "prepared",
        "close_written",
        "reconciled",
        "committed",
    )
    event_paths = tuple(
        sorted(
            authorizer._store._events_dir(ready.certificate.certificate_id).glob(
                "*.json"
            )
        )
    )
    assert len(event_paths) == 4


def test_expired_repo_lease_cannot_write_formal_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = _ready_certificate(tmp_path)
    current = datetime(2026, 7, 21, 15, tzinfo=UTC)
    authorizer = StageCloseAuthorizer(
        tmp_path,
        project_id=ready.authorized.scope.project_id,
        certificate_authority=ready.authority,
        governance_authority=_governance(
            tmp_path,
            clock=lambda: current.isoformat().replace("+00:00", "Z"),
        ),
        clock=lambda: current.isoformat().replace("+00:00", "Z"),
    )
    context = _context(ready, authorizer).model_copy(
        update={"lease_seconds": 1, "context_digest": ""}
    )
    original = authorizer._store.write_artifact

    def expire_before_write(
        contract: object,
        *,
        authorize_write: object,
    ) -> str:
        nonlocal current
        current += timedelta(seconds=2)
        return original(  # type: ignore[arg-type]
            contract,
            authorize_write=authorize_write,  # type: ignore[arg-type]
        )

    monkeypatch.setattr(authorizer._store, "write_artifact", expire_before_write)

    with pytest.raises(SharedStateIntegrityError, match="lease|current"):
        authorizer.authorize_stage_close(context)

    artifact = authorizer._store.artifact_path(context.close_artifact.artifact_path)
    claim = authorizer._store.read_claim(ready.certificate.certificate_id)
    assert claim is not None
    assert not artifact.exists()
    assert authorizer._store.load_state(claim).event_kinds == ("prepared",)


def test_corrupt_held_lease_journal_blocks_close_event_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    original = authorizer._store.append_event
    tampered = False

    def tamper_before_append(*args: object, **kwargs: object):
        nonlocal tampered
        if not tampered:
            tampered = True
            lease_path = tuple(
                sorted(authorizer._repo_leases._store.events_dir.glob("*.json"))
            )[0]
            payload = json.loads(lease_path.read_text(encoding="utf-8"))
            payload["lease"]["lease_owner"] = "owner.forged"
            lease_path.write_text(json.dumps(payload), encoding="utf-8")
        return original(*args, **kwargs)

    monkeypatch.setattr(authorizer._store, "append_event", tamper_before_append)

    with pytest.raises(SharedStateIntegrityError, match="lease event"):
        authorizer.authorize_stage_close(context)

    claim = authorizer._store.read_claim(ready.certificate.certificate_id)
    assert claim is not None
    assert authorizer._store.load_state(claim).event_kinds == ()
    assert not authorizer._store.artifact_path(claim.artifact_path).exists()


def test_artifact_fork_after_close_written_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)

    def interrupt(phase: str) -> None:
        if phase == "close_written":
            raise RuntimeError("simulated exit after close_written")

    monkeypatch.setattr(authorizer, "_checkpoint", interrupt)
    with pytest.raises(RuntimeError, match="simulated exit"):
        authorizer.authorize_stage_close(context)
    artifact_path = authorizer._store.artifact_path(
        context.close_artifact.artifact_path
    )
    artifact_path.write_text('{"status":"forged"}\n', encoding="utf-8")
    monkeypatch.setattr(authorizer, "_checkpoint", lambda _phase: None)

    with pytest.raises(CloseStoreConflictError, match="artifact"):
        authorizer.authorize_stage_close(context)


def test_receipt_fork_after_committed_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)

    def interrupt(phase: str) -> None:
        if phase == "committed":
            raise RuntimeError("simulated exit after committed")

    monkeypatch.setattr(authorizer, "_checkpoint", interrupt)
    with pytest.raises(RuntimeError, match="simulated exit"):
        authorizer.authorize_stage_close(context)
    claim = authorizer._store.read_claim(ready.certificate.certificate_id)
    assert claim is not None
    receipt_path = authorizer._store.receipts_dir / f"{claim.claim_id}.json"
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["command_id"] = "stage-close.forged"
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(authorizer, "_checkpoint", lambda _phase: None)

    with pytest.raises(SharedStateIntegrityError, match="invalid"):
        authorizer.authorize_stage_close(context)


def test_receipt_must_bind_current_reconciled_event_digest(tmp_path: Path) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    closed = authorizer.authorize_stage_close(context)
    event_paths = tuple(
        sorted(authorizer._store._events_dir(closed.claim.certificate_id).glob("*.json"))
    )
    reconciled_payload = json.loads(event_paths[2].read_text(encoding="utf-8"))
    reconciled_payload.update(
        occurred_at="2026-07-21T15:00:01Z",
        event_digest="",
    )
    reconciled = CloseConsumptionEvent.model_validate(reconciled_payload)
    event_paths[2].write_text(reconciled.model_dump_json(), encoding="utf-8")
    committed_payload = json.loads(event_paths[3].read_text(encoding="utf-8"))
    committed_payload.update(
        previous_event_digest=reconciled.event_digest,
        event_digest="",
    )
    committed = CloseConsumptionEvent.model_validate(committed_payload)
    event_paths[3].write_text(committed.model_dump_json(), encoding="utf-8")
    projection = authorizer._store.projections_dir / f"{closed.claim.claim_id}.json"
    projection.unlink()

    with pytest.raises(SharedStateIntegrityError, match="four-way"):
        authorizer.authorize_stage_close(context)


def test_reconciled_event_cannot_change_close_written_artifact_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)

    def interrupt(phase: str) -> None:
        if phase == "reconciled":
            raise RuntimeError("after reconciled")

    monkeypatch.setattr(authorizer, "_checkpoint", interrupt)
    with pytest.raises(RuntimeError, match="after reconciled"):
        authorizer.authorize_stage_close(context)
    claim = authorizer._store.read_claim(ready.certificate.certificate_id)
    assert claim is not None
    event_path = tuple(
        sorted(authorizer._store._events_dir(claim.certificate_id).glob("*.json"))
    )[2]
    payload = json.loads(event_path.read_text(encoding="utf-8"))
    payload.update(
        close_artifact_digest="sha256:artifact.forged",
        event_digest="",
    )
    event_path.write_text(
        CloseConsumptionEvent.model_validate(payload).model_dump_json(),
        encoding="utf-8",
    )

    with pytest.raises(SharedStateIntegrityError, match="artifact"):
        authorizer._store.load_state(claim)


def test_committed_event_repairs_missing_closed_projection(tmp_path: Path) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    closed = authorizer.authorize_stage_close(context)
    projection = authorizer._store.projections_dir / f"{closed.claim.claim_id}.json"
    projection.unlink()

    replay = authorizer.authorize_stage_close(context)

    assert replay == closed
    assert projection.is_file()


def test_manual_abort_is_terminal_and_returns_needs_user(tmp_path: Path) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)

    aborted = authorizer.abort_stage_close(
        context,
        governance_request=_abort_request(),
    )
    replay = authorizer.authorize_stage_close(context)

    assert aborted == replay
    assert replay.status == "needs_user"
    assert replay.receipt is None
    assert replay.state.event_kinds == ("prepared", "aborted")
    session = ready.fixture.service.get(ready.fixture.scope)
    assert session.state == "needs_user"
    assert session.active_close_claim_id == replay.claim.claim_id
    assert session.close_failure_reason == "governed_close_abort"


def test_untrusted_abort_request_cannot_create_terminal_event(tmp_path: Path) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)

    request = StageCloseAbortRequest(
        actor_id="actor.untrusted",
        idempotency_key="stage-close-abort.untrusted",
        reason_code="invented",
        reason="An untrusted caller requests a terminal abort.",
    )

    with pytest.raises(GovernanceDecisionInvalidError, match="not authorized"):
        authorizer.abort_stage_close(
            context,
            governance_request=request,
        )

    assert authorizer._store.read_claim(ready.certificate.certificate_id) is None
    assert ready.fixture.service.get(ready.fixture.scope).state == "authorized"


def test_abort_decision_is_versioned_and_first_writer_wins(tmp_path: Path) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)

    authorizer.abort_stage_close(
        context,
        governance_request=_abort_request("first-writer"),
    )
    decision_path = next(
        (tmp_path / ".ai-sdlc" / "state" / "shared").rglob(
            "stage-close-governance-decision.*.json"
        )
    )
    payload = json.loads(decision_path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "stage-close-governance-decision.v1"
    assert payload["actor_id"] == "actor.stage-close-governor"
    assert payload["claim_id"]
    assert payload["certificate_id"] == ready.certificate.certificate_id

    with pytest.raises(GovernanceDecisionInvalidError, match="already bound"):
        authorizer.abort_stage_close(
            context,
            governance_request=StageCloseAbortRequest(
                actor_id="actor.stage-close-governor",
                idempotency_key="stage-close-abort.different",
                reason_code="different_reason",
                reason="A different decision cannot replace the first writer.",
            ),
        )


def test_candidate_change_after_claim_can_only_recover_through_governed_abort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    monkeypatch.setattr(
        authorizer,
        "_checkpoint",
        lambda phase: (_ for _ in ()).throw(RuntimeError("after claim"))
        if phase == "claim_created"
        else None,
    )
    with pytest.raises(RuntimeError, match="after claim"):
        authorizer.authorize_stage_close(context)
    ready.context_authority.evidence = StageCloseEvidence(
        candidate_manifest_digest="sha256:candidate.changed-before-abort",
        test_evidence_digest=context.certificate.test_evidence_digest,
        integrity_evidence_digest=context.certificate.integrity_evidence_digest,
        protected_path_set=context.certificate.protected_path_set,
    )
    monkeypatch.setattr(authorizer, "_checkpoint", lambda _phase: None)

    result = authorizer.abort_stage_close(
        context,
        governance_request=_abort_request("candidate-changed"),
    )

    assert result.status == "needs_user"
    assert ready.fixture.service.get(ready.fixture.scope).state == "needs_user"


def test_tampered_governance_decision_blocks_aborted_recovery(tmp_path: Path) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    authorizer.abort_stage_close(
        context,
        governance_request=_abort_request("tamper"),
    )
    decision_path = next(
        (tmp_path / ".ai-sdlc" / "state" / "shared").rglob(
            "stage-close-governance-decision.*.json"
        )
    )
    payload = json.loads(decision_path.read_text(encoding="utf-8"))
    payload["reason"] = "tampered"
    decision_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(GovernanceDecisionInvalidError, match="artifact"):
        authorizer.authorize_stage_close(context)


def test_session_service_rejects_unpersisted_close_abort_decision(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    claim = build_close_claim(context, prepared_at="2026-07-21T15:00:00Z")
    unpersisted = _governance(tmp_path)._build_decision(
        _abort_request("unpersisted"),
        claim,
        "2026-07-21T15:00:00Z",
    )

    with pytest.raises(GovernanceDecisionInvalidError):
        ready.fixture.service.abort_close(
            GovernedCloseAbortCommand(
                scope=claim.scope,
                command_id="session-close-abort.untrusted",
                idempotency_key="session-close-abort.untrusted",
                expected_revision=ready.authorized.revision,
                claim=claim,
                governance_decision=unpersisted,
            )
        )

    assert ready.fixture.service.get(claim.scope).state == "authorized"


def test_aborted_event_repairs_projection_write_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    original = authorizer._store.materialize

    def fail_projection(
        _state: object,
        *,
        authorize_write: object,
    ) -> None:
        del authorize_write
        raise OSError("simulated aborted projection failure")

    monkeypatch.setattr(authorizer._store, "materialize", fail_projection)
    with pytest.raises(OSError, match="aborted projection failure"):
        authorizer.abort_stage_close(
            context,
            governance_request=_abort_request("projection"),
        )
    monkeypatch.setattr(authorizer._store, "materialize", original)

    replay = authorizer.authorize_stage_close(context)
    projection = authorizer._store.projections_dir / f"{replay.claim.claim_id}.json"

    assert replay.status == "needs_user"
    assert replay.state.event_kinds == ("prepared", "aborted")
    assert projection.is_file()


def test_invalid_certificate_never_creates_close_claim(tmp_path: Path) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    ready.context_authority.evidence = ready.context_authority.evidence.model_copy(
        update={"test_evidence_digest": "sha256:test-evidence.changed"}
    )

    with pytest.raises(CertificateInvalidError):
        authorizer.authorize_stage_close(context)

    assert authorizer._store.read_claim(ready.certificate.certificate_id) is None


def test_two_authorizers_racing_same_command_return_one_consumption(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    first = _authorizer(tmp_path, ready)
    second = _authorizer(tmp_path, ready)
    context = _context(ready, first)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(
            pool.map(lambda item: item.authorize_stage_close(context), (first, second))
        )

    assert results[0] == results[1]
    assert results[0].state.closed


def test_two_processes_cannot_create_competing_certificate_claims(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    first = build_close_claim(_context(ready, authorizer), prepared_at=_now_text())
    payload = first.model_dump(mode="json")
    payload.update(
        command_id="stage-close.competing",
        idempotency_key="stage-close-key.competing",
        close_intent_digest="sha256:close-intent.competing",
        claim_digest="",
    )
    second = CloseConsumptionClaim.model_validate(payload)
    process_context = multiprocessing.get_context("spawn")
    queue = process_context.Queue()
    processes = [
        process_context.Process(
            target=_claim_worker,
            args=(
                str(tmp_path),
                ready.authorized.scope.project_id,
                claim.model_dump(mode="json"),
                queue,
            ),
        )
        for claim in (first, second)
    ]

    for process in processes:
        process.start()
    results = [queue.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    assert sorted(results) == ["conflict", "created"]


def test_store_revalidates_claim_before_persisting(tmp_path: Path) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    claim = build_close_claim(_context(ready, authorizer), prepared_at=_now_text())
    forged = claim.model_copy(update={"command_id": "stage-close.forged"})

    with pytest.raises(ValueError, match="digest"):
        authorizer._store.create_claim(forged)

    assert authorizer._store.read_claim(claim.certificate_id) is None


def test_claim_creation_replays_existing_timestamp_for_same_command(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    current = datetime(2026, 7, 21, 15, tzinfo=UTC)
    authorizer = StageCloseAuthorizer(
        tmp_path,
        project_id=ready.authorized.scope.project_id,
        certificate_authority=ready.authority,
        governance_authority=_governance(
            tmp_path,
            clock=lambda: current.isoformat().replace("+00:00", "Z"),
        ),
        clock=lambda: current.isoformat().replace("+00:00", "Z"),
    )
    context = _context(ready, authorizer)

    first = authorizer._create_claim(context)
    current += timedelta(seconds=5)
    replay = authorizer._create_claim(context)

    assert replay == first


def test_prepared_event_is_deterministic_from_persisted_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = _ready_certificate(tmp_path)
    current = datetime(2026, 7, 21, 15, tzinfo=UTC)
    authorizer = StageCloseAuthorizer(
        tmp_path,
        project_id=ready.authorized.scope.project_id,
        certificate_authority=ready.authority,
        governance_authority=_governance(
            tmp_path,
            clock=lambda: current.isoformat().replace("+00:00", "Z"),
        ),
        clock=lambda: current.isoformat().replace("+00:00", "Z"),
    )
    context = _context(ready, authorizer)

    monkeypatch.setattr(
        authorizer,
        "_checkpoint",
        lambda phase: (_ for _ in ()).throw(RuntimeError("after claim"))
        if phase == "claim_created"
        else None,
    )
    with pytest.raises(RuntimeError, match="after claim"):
        authorizer.authorize_stage_close(context)
    claim = authorizer._store.read_claim(ready.certificate.certificate_id)
    assert claim is not None
    current += timedelta(seconds=5)
    monkeypatch.setattr(authorizer, "_checkpoint", lambda _phase: None)

    recovered = authorizer.authorize_stage_close(context)
    prepared = authorizer._store.last_event(claim.certificate_id)
    assert prepared is not None

    assert recovered.status == "closed"
    assert recovered.state.event_kinds[0] == "prepared"
    first_event = tuple(
        sorted(authorizer._store._events_dir(claim.certificate_id).glob("*.json"))
    )[0]
    payload = json.loads(first_event.read_text(encoding="utf-8"))
    assert payload["occurred_at"] == claim.prepared_at


def test_every_close_event_write_holds_current_repo_write_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    original = authorizer._store.append_event
    unleased_events: list[str] = []

    def append_with_lease_check(*args: object, **kwargs: object):
        event_kind = str(args[2])
        if authorizer._repo_leases._held is None:
            unleased_events.append(event_kind)
        return original(*args, **kwargs)

    monkeypatch.setattr(authorizer._store, "append_event", append_with_lease_check)

    result = authorizer.authorize_stage_close(context)

    assert result.status == "closed"
    assert unleased_events == []


def test_receipt_created_before_exit_reuses_original_commit_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = _ready_certificate(tmp_path)
    current = datetime(2026, 7, 21, 15, tzinfo=UTC)
    authorizer = StageCloseAuthorizer(
        tmp_path,
        project_id=ready.authorized.scope.project_id,
        certificate_authority=ready.authority,
        governance_authority=_governance(
            tmp_path,
            clock=lambda: current.isoformat().replace("+00:00", "Z"),
        ),
        clock=lambda: current.isoformat().replace("+00:00", "Z"),
    )
    context = _context(ready, authorizer)

    def interrupt(phase: str) -> None:
        if phase == "receipt_created":
            raise RuntimeError("simulated exit after receipt")

    monkeypatch.setattr(authorizer, "_checkpoint", interrupt)
    with pytest.raises(RuntimeError, match="simulated exit"):
        authorizer.authorize_stage_close(context)
    current += timedelta(seconds=5)
    monkeypatch.setattr(authorizer, "_checkpoint", lambda _phase: None)

    recovered = authorizer.authorize_stage_close(context)

    assert recovered.status == "closed"
    assert recovered.receipt is not None
    assert recovered.receipt.committed_at == "2026-07-21T15:00:00Z"


def test_recovery_rejects_current_candidate_evidence_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    monkeypatch.setattr(
        authorizer,
        "_checkpoint",
        lambda phase: (_ for _ in ()).throw(RuntimeError("after claim"))
        if phase == "claim_created"
        else None,
    )
    with pytest.raises(RuntimeError, match="after claim"):
        authorizer.authorize_stage_close(context)
    ready.context_authority.evidence = StageCloseEvidence(
        candidate_manifest_digest="sha256:candidate.changed",
        test_evidence_digest=context.certificate.test_evidence_digest,
        integrity_evidence_digest=context.certificate.integrity_evidence_digest,
        protected_path_set=context.certificate.protected_path_set,
    )
    monkeypatch.setattr(authorizer, "_checkpoint", lambda _phase: None)

    with pytest.raises(CertificateInvalidError, match="evidence|candidate"):
        authorizer.authorize_stage_close(context)


def test_candidate_change_immediately_after_claim_cannot_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)

    def change_after_claim(phase: str) -> None:
        if phase == "claim_created":
            ready.context_authority.evidence = StageCloseEvidence(
                candidate_manifest_digest="sha256:candidate.changed-after-claim",
                test_evidence_digest=context.certificate.test_evidence_digest,
                integrity_evidence_digest=context.certificate.integrity_evidence_digest,
                protected_path_set=context.certificate.protected_path_set,
            )

    monkeypatch.setattr(authorizer, "_checkpoint", change_after_claim)

    with pytest.raises(CertificateInvalidError, match="evidence|candidate"):
        authorizer.authorize_stage_close(context)

    assert not authorizer._store.artifact_path(
        context.close_artifact.artifact_path
    ).exists()
    assert ready.fixture.service.get(ready.fixture.scope).state == "authorized"


def test_committed_close_recovers_pending_session_consumption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)

    monkeypatch.setattr(
        authorizer,
        "_checkpoint",
        lambda phase: (_ for _ in ()).throw(RuntimeError("after committed"))
        if phase == "committed"
        else None,
    )
    with pytest.raises(RuntimeError, match="after committed"):
        authorizer.authorize_stage_close(context)

    assert ready.fixture.service.get(ready.fixture.scope).state == "consuming"
    monkeypatch.setattr(authorizer, "_checkpoint", lambda _phase: None)

    recovered = authorizer.authorize_stage_close(context)
    session = ready.fixture.service.get(ready.fixture.scope)

    assert recovered.status == "closed"
    assert session.state == "consumed"
    assert session.close_consumption_receipt_id == recovered.receipt.receipt_id


def test_original_claim_recovers_after_project_resource_fencing_advances(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)

    monkeypatch.setattr(
        authorizer,
        "_checkpoint",
        lambda phase: (_ for _ in ()).throw(RuntimeError("after close_written"))
        if phase == "close_written"
        else None,
    )
    with pytest.raises(RuntimeError, match="after close_written"):
        authorizer.authorize_stage_close(context)

    policy = _policy()
    envelope = build_budget_envelope(
        project_id=ready.authorized.scope.project_id,
        work_item_id="work-item.after-claim",
        stage_review_session_id="session.after-claim",
        risk_level="low",
        budget_policy=policy,
        pool="foreground",
    )
    advanced = ready.resources.reserve_admission(
        envelope,
        budget_policy=policy,
        lease_owner=_OWNER,
        operation_id="resource-admission.after-close-claim",
        lease_seconds=60,
        now=_now(),
    )
    assert advanced.reservation is not None
    assert advanced.reservation.fencing_token > ready.current.fencing_token
    monkeypatch.setattr(authorizer, "_checkpoint", lambda _phase: None)

    recovered = authorizer.authorize_stage_close(context)

    assert recovered.status == "closed"
    assert recovered.receipt is not None
    assert recovered.receipt.fencing_epoch == ready.current.fencing_token


def test_recovery_rejects_persisted_certificate_fork(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    monkeypatch.setattr(
        authorizer,
        "_checkpoint",
        lambda phase: (_ for _ in ()).throw(RuntimeError("after claim"))
        if phase == "claim_created"
        else None,
    )
    with pytest.raises(RuntimeError, match="after claim"):
        authorizer.authorize_stage_close(context)
    certificate_path = ready.authority.certificate_path(ready.certificate)
    payload = json.loads(certificate_path.read_text(encoding="utf-8"))
    payload["command_id"] = "stage-close.forged"
    certificate_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(authorizer, "_checkpoint", lambda _phase: None)

    with pytest.raises(SharedStateIntegrityError, match="certificate"):
        authorizer.authorize_stage_close(context)


def test_authorizer_rejects_caller_declared_foreign_worktree(tmp_path: Path) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer).model_copy(
        update={"worktree_identity": "worktree.forged", "context_digest": ""}
    )

    with pytest.raises(ValueError, match="worktree"):
        authorizer.authorize_stage_close(context)


def _now_text() -> str:
    return "2026-07-21T15:00:00Z"


def _claim_worker(
    root: str,
    project_id: str,
    payload: dict[str, object],
    queue: object,
) -> None:
    store = StageCloseStore(
        Path(root),
        project_id=project_id,
        lock_timeout_seconds=2,
    )
    claim = CloseConsumptionClaim.model_validate(payload)
    try:
        store.create_claim(claim)
    except CloseStoreConflictError:
        queue.put("conflict")  # type: ignore[attr-defined]
    else:
        queue.put("created")  # type: ignore[attr-defined]
