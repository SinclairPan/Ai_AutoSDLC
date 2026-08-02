from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.unit.stage_review.test_authorizer import (
    _abort_request,
    _authorizer,
    _context,
    _governance,
)
from tests.unit.stage_review.test_certificates import (
    _ready_certificate,
    _ReadyCertificate,
)
from tests.unit.stage_review.test_transaction_artifact_codec import _previous_payload

from ai_sdlc.core.stage_review.artifacts import (
    ResourceLockUnavailableError,
    SharedStateIntegrityError,
)
from ai_sdlc.core.stage_review.certificate_models import (
    StageCloseCertificateRequest,
    StageCloseEvidence,
    StageCloseIntent,
)
from ai_sdlc.core.stage_review.certificates import CertificateInvalidError
from ai_sdlc.core.stage_review.close_builders import (
    _build_close_claim as build_close_claim,
)
from ai_sdlc.core.stage_review.close_builders import (
    _build_close_receipt as build_close_receipt,
)
from ai_sdlc.core.stage_review.close_builders import (
    build_close_lease_request,
)
from ai_sdlc.core.stage_review.close_governance_models import StageCloseAbortRequest
from ai_sdlc.core.stage_review.close_recovery_models import StageCloseRecoveryRequest
from ai_sdlc.core.stage_review.resource_builders import stable_id
from ai_sdlc.core.stage_review.session_contracts import (
    CloseConsumptionStartCommand,
    CloseReceiptCommitCommand,
    MacroRebaselineCommand,
    SessionIntegrityError,
)


def test_legacy_claim_rejection_does_not_mutate_session(tmp_path: Path) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    claim = authorizer._create_claim(context)
    claim_path = authorizer._store.claims_dir / f"{claim.certificate_id}.json"
    legacy = _previous_payload(
        claim.model_dump(mode="json"),
        "close-consumption-claim.v0",
        "claim_digest",
    )
    claim_path.write_text(json.dumps(legacy), encoding="utf-8")

    before = ready.fixture.service.get(ready.fixture.scope)
    with pytest.raises(SharedStateIntegrityError, match="read-only"):
        authorizer.authorize_stage_close(context)

    after = ready.fixture.service.get(ready.fixture.scope)
    assert after == before
    assert not authorizer._store._read_events(claim.certificate_id)
    assert authorizer._store.read_receipt(claim.claim_id) is None


def test_public_session_begin_rejects_unpersisted_claim(tmp_path: Path) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    claim = build_close_claim(context, prepared_at="2026-07-21T15:00:00Z")
    command_id = stable_id("session-close-start", claim.claim_digest)
    command = CloseConsumptionStartCommand(
        scope=claim.scope,
        command_id=command_id,
        idempotency_key=command_id,
        expected_revision=ready.certificate.session_revision,
        certificate=ready.certificate,
        certificate_request=ready.request,
        claim=claim,
    )

    with pytest.raises(SessionIntegrityError, match="persisted|authority"):
        ready.fixture.service.begin_close(command)

    assert ready.fixture.service.get(ready.fixture.scope).state == "authorized"
    assert authorizer._store.read_claim(claim.certificate_id) is None


def test_public_transaction_authority_binding_rejects_protocol_lookalike(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)

    class ProtocolLookalike:
        project_id = ready.authority.project_id
        shared_state_binding_id = ready.authority.shared_state_binding_id
        authority_id = "stage-close-authorizer.v1"

        def require_close_claim_current(self, _command: object) -> None:
            return None

        def require_close_receipt_current(self, _command: object) -> None:
            return None

        def require_reconciled_claim_current(self, _command: object) -> None:
            return None

        def require_aborted_claim_current(self, _claim: object) -> None:
            return None

    with pytest.raises(TypeError, match="canonical"):
        ready.authority.bind_transaction_authority(ProtocolLookalike())  # type: ignore[arg-type]

    assert ready.fixture.service.get(ready.fixture.scope).state == "authorized"


def test_public_abort_authority_binding_rejects_protocol_lookalike(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    governance = _governance(tmp_path)

    class ProtocolLookalike:
        project_id = governance.project_id
        shared_state_binding_id = governance.shared_state_binding_id
        authority_id = governance.authority_id
        authority_binding_digest = governance.authority_binding_digest

        def require_abort(self, _claim: object, _digest: str) -> object:
            return object()

        def require_recovery(self, _claim: object, _decision: object) -> object:
            return object()

    with pytest.raises(TypeError, match="canonical"):
        ready.fixture.service.bind_close_abort_authority(ProtocolLookalike())  # type: ignore[arg-type]


def test_public_recovery_authority_binding_rejects_protocol_lookalike(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    governance = _governance(tmp_path)

    class ProtocolLookalike:
        project_id = governance.project_id
        shared_state_binding_id = governance.shared_state_binding_id
        authority_id = governance.authority_id
        authority_binding_digest = governance.authority_binding_digest

        def require_recovery(self, _claim: object, _decision: object) -> object:
            return object()

    with pytest.raises(TypeError, match="canonical"):
        ready.authority.bind_recovery_authority(ProtocolLookalike())  # type: ignore[arg-type]


def test_public_session_commit_rejects_unpersisted_receipt(tmp_path: Path) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    claim = authorizer._create_claim(context)
    authorizer._session.begin(context, claim)
    receipt = build_close_receipt(
        claim,
        close_artifact_digest="sha256:unpersisted-close",
        reconciled_event_digest="sha256:unpersisted-reconciled",
        committed_at="2026-07-21T15:00:00Z",
    )
    command_id = stable_id("session-close-commit", claim.claim_digest)
    command = CloseReceiptCommitCommand(
        scope=claim.scope,
        command_id=command_id,
        idempotency_key=command_id,
        expected_revision=ready.certificate.session_revision + 1,
        claim=claim,
        receipt=receipt,
    )

    with pytest.raises(SessionIntegrityError, match="persisted|committed|authority"):
        ready.fixture.service.commit_close(command)

    assert ready.fixture.service.get(ready.fixture.scope).state == "consuming"
    assert authorizer._store.read_receipt(claim.claim_id) is None


def test_candidate_drift_after_session_begin_writes_no_close_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)

    def drift(phase: str) -> None:
        if phase != "session_consuming":
            return
        ready.context_authority.evidence = StageCloseEvidence(
            candidate_manifest_digest="sha256:candidate.changed-after-session-begin",
            test_evidence_digest=ready.request.evidence.test_evidence_digest,
            integrity_evidence_digest=(
                ready.request.evidence.integrity_evidence_digest
            ),
            protected_path_set=ready.request.evidence.protected_path_set,
        )

    monkeypatch.setattr(authorizer, "_checkpoint", drift)
    with pytest.raises(CertificateInvalidError, match="candidate|evidence"):
        authorizer.authorize_stage_close(context)

    claim = authorizer._store.read_claim(ready.certificate.certificate_id)
    assert claim is not None
    assert ready.fixture.service.get(ready.fixture.scope).state == "consuming"
    assert not authorizer._store._read_events(claim.certificate_id)
    assert authorizer._store.read_receipt(claim.claim_id) is None
    assert not (tmp_path / claim.artifact_path).exists()


def test_close_lease_covers_candidate_evidence_and_artifact_paths(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    claim = build_close_claim(context, prepared_at="2026-07-21T15:00:00Z")
    state = authorizer._store.load_state(claim)
    request = build_close_lease_request(context, claim, state)

    assert claim.protected_path_set == ready.request.evidence.protected_path_set
    assert request.protected_path_set == tuple(
        sorted({*claim.protected_path_set, claim.artifact_path})
    )


def test_preclaimed_narrowed_protected_paths_fail_before_session_or_lease(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    claim = build_close_claim(context, prepared_at="2026-07-21T15:00:00Z")
    narrowed = claim.model_copy(
        update={
            "protected_path_set": ("evidence/integrity.json",),
            "claim_digest": "",
        }
    )
    authorizer._store.create_claim(narrowed)
    before = ready.fixture.service.get(ready.fixture.scope)

    with pytest.raises(SharedStateIntegrityError, match="claim|contract"):
        authorizer.authorize_stage_close(context)

    assert ready.fixture.service.get(ready.fixture.scope) == before
    assert not authorizer._store._read_events(claim.certificate_id)
    assert authorizer._store.read_receipt(narrowed.claim_id) is None
    assert not (tmp_path / narrowed.artifact_path).exists()
    assert not authorizer._repo_leases._store._read_events()


def test_public_session_begin_rejects_narrowed_protected_paths(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    claim = build_close_claim(context, prepared_at="2026-07-21T15:00:00Z")
    narrowed = claim.model_copy(
        update={
            "protected_path_set": ("evidence/integrity.json",),
            "claim_digest": "",
        }
    )
    persisted = authorizer._store.create_claim(narrowed)
    command_id = stable_id("session-close-start", persisted.claim_digest)
    command = CloseConsumptionStartCommand(
        scope=persisted.scope,
        command_id=command_id,
        idempotency_key=command_id,
        expected_revision=ready.certificate.session_revision,
        certificate=ready.certificate,
        certificate_request=ready.request,
        claim=persisted,
    )

    with pytest.raises(SessionIntegrityError, match="binding|evidence"):
        ready.fixture.service.begin_close(command)

    assert ready.fixture.service.get(ready.fixture.scope).state == "authorized"


def test_legacy_claim_abort_writes_no_governance_decision(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    claim = authorizer._create_claim(context)
    claim_path = authorizer._store.claims_dir / f"{claim.certificate_id}.json"
    legacy = _previous_payload(
        claim.model_dump(mode="json"),
        "close-consumption-claim.v0",
        "claim_digest",
    )
    claim_path.write_text(json.dumps(legacy), encoding="utf-8")
    decision_root = _governance(tmp_path)._root / "decisions"

    with pytest.raises(SharedStateIntegrityError, match="read-only"):
        authorizer.abort_stage_close(
            context,
            governance_request=_recovery_abort_request(),
        )

    assert not list(decision_root.glob("*.json"))
    assert ready.fixture.service.get(ready.fixture.scope).state == "authorized"


def test_closed_claim_abort_writes_no_governance_decision(tmp_path: Path) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    authorizer.authorize_stage_close(context)
    decision_root = _governance(tmp_path)._root / "decisions"

    with pytest.raises(SharedStateIntegrityError, match="committed|closed"):
        authorizer.abort_stage_close(
            context,
            governance_request=_recovery_abort_request(),
        )

    assert not list(decision_root.glob("*.json"))
    assert ready.fixture.service.get(ready.fixture.scope).state == "consumed"


def test_persisted_abort_decision_fences_close_after_repo_lease_conflict(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    claim = authorizer._create_claim(context)
    state = authorizer._store.load_state(claim)
    lease_request = build_close_lease_request(context, claim, state)
    governance_request = _recovery_abort_request()

    with (
        authorizer._repo_leases.acquire(lease_request),
        pytest.raises(ResourceLockUnavailableError, match="overlapping"),
    ):
        authorizer.abort_stage_close(
            context,
            governance_request=governance_request,
        )

    decision_root = _governance(tmp_path)._root / "decisions"
    assert len(list(decision_root.glob("*.json"))) == 1
    with pytest.raises(SharedStateIntegrityError, match="abort decision|governed abort"):
        authorizer.authorize_stage_close(context)
    assert not authorizer._store._read_events(claim.certificate_id)
    assert authorizer._store.read_receipt(claim.claim_id) is None
    assert ready.fixture.service.get(ready.fixture.scope).state == "authorized"

    aborted = authorizer.abort_stage_close(
        context,
        governance_request=governance_request,
    )

    assert aborted.status == "needs_user"
    assert ready.fixture.service.get(ready.fixture.scope).state == "needs_user"


def test_aborted_close_can_only_reauthorize_with_new_certificate_and_claim(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    aborted = authorizer.abort_stage_close(
        context,
        governance_request=_recovery_abort_request(),
    )
    request = _replacement_certificate_request(ready)
    recovery_request = StageCloseRecoveryRequest(
        actor_id="actor.stage-close-governor",
        idempotency_key="stage-close-recovery.new-certificate.1",
        recovery_kind="authorize_new_certificate",
        new_command_id=request.intent.command_id,
        reason_code="manual_reconciliation_completed",
        reason="The aborted close was reconciled and requires a new certificate.",
    )

    recovered = authorizer.reauthorize_aborted_close(
        context,
        recovery_request=recovery_request,
        certificate_request=request,
    )

    assert recovered.status == "authorized"
    assert recovered.certificate.certificate_id != aborted.claim.certificate_id
    assert recovered.claim.claim_id != aborted.claim.claim_id
    session = ready.fixture.service.get(ready.fixture.scope)
    assert session.state == "authorized"
    assert session.active_close_certificate_id == recovered.certificate.certificate_id
    assert session.active_close_claim_id == recovered.claim.claim_id
    with pytest.raises(SharedStateIntegrityError, match="aborted|diverged|reused"):
        authorizer.authorize_stage_close(context)
    assert ready.fixture.service.get(ready.fixture.scope) == session
    replacement = context.model_copy(
        update={
            "certificate": recovered.certificate,
            "certificate_request": request,
            "context_digest": "",
        }
    )
    closed = authorizer.authorize_stage_close(replacement)
    assert closed.status == "closed"
    assert ready.fixture.service.get(ready.fixture.scope).state == "consumed"


def test_aborted_close_can_be_superseded_but_not_reauthorized_afterward(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    authorizer.abort_stage_close(
        context,
        governance_request=_recovery_abort_request(),
    )
    supersede = StageCloseRecoveryRequest(
        actor_id="actor.stage-close-governor",
        idempotency_key="stage-close-recovery.supersede.1",
        recovery_kind="supersede_session",
        reason_code="macro_rebaseline_accepted",
        reason="The aborted session is replaced by a new loop round.",
    )

    session = authorizer.supersede_aborted_close(
        context,
        recovery_request=supersede,
    )
    replay = authorizer.supersede_aborted_close(
        context,
        recovery_request=supersede,
    )

    assert session.state == "superseded"
    assert replay == session
    request = _replacement_certificate_request(ready)
    competing = StageCloseRecoveryRequest(
        actor_id="actor.stage-close-governor",
        idempotency_key="stage-close-recovery.competing.1",
        recovery_kind="authorize_new_certificate",
        new_command_id=request.intent.command_id,
        reason_code="manual_reconciliation_completed",
        reason="A competing terminal decision must not win.",
    )
    with pytest.raises(SharedStateIntegrityError, match="bound|terminal|decision"):
        authorizer.reauthorize_aborted_close(
            context,
            recovery_request=competing,
            certificate_request=request,
        )


def test_aborted_session_rejects_macro_request_without_event_pollution(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    authorizer.abort_stage_close(
        context,
        governance_request=_recovery_abort_request(),
    )
    session = ready.fixture.service.get(ready.fixture.scope)
    before = ready.fixture.service.events(ready.fixture.scope)

    with pytest.raises(SessionIntegrityError, match="aborted.*terminal"):
        ready.fixture.service.request_macro_rebaseline(
            MacroRebaselineCommand(
                scope=ready.fixture.scope,
                command_id="macro-rebaseline.after-abort",
                idempotency_key="macro-rebaseline-key.after-abort",
                expected_revision=session.revision,
                change_kind="architecture_change",
                evidence_digest="sha256:architecture.after-abort",
            )
        )

    assert ready.fixture.service.events(ready.fixture.scope) == before
    assert ready.fixture.service.get(ready.fixture.scope).state == "needs_user"
    superseded = authorizer.supersede_aborted_close(
        context,
        recovery_request=_supersede_recovery_request("after-invalid-macro"),
    )
    assert superseded.state == "superseded"


def test_new_certificate_recovery_replays_after_claim_persisted_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    authorizer.abort_stage_close(
        context,
        governance_request=_recovery_abort_request(),
    )
    request = _replacement_certificate_request(ready)
    recovery_request = StageCloseRecoveryRequest(
        actor_id="actor.stage-close-governor",
        idempotency_key="stage-close-recovery.crash.1",
        recovery_kind="authorize_new_certificate",
        new_command_id=request.intent.command_id,
        reason_code="manual_reconciliation_completed",
        reason="Recovery must replay after the new claim is durable.",
    )

    def interrupt(phase: str) -> None:
        if phase == "recovery_claim_created":
            raise RuntimeError("simulated recovery crash")

    monkeypatch.setattr(authorizer, "_checkpoint", interrupt)
    with pytest.raises(RuntimeError, match="simulated recovery crash"):
        authorizer.reauthorize_aborted_close(
            context,
            recovery_request=recovery_request,
            certificate_request=request,
        )
    monkeypatch.setattr(authorizer, "_checkpoint", lambda _phase: None)

    recovered = authorizer.reauthorize_aborted_close(
        context,
        recovery_request=recovery_request,
        certificate_request=request,
    )

    assert recovered.status == "authorized"
    assert ready.fixture.service.get(ready.fixture.scope).state == "authorized"


def test_new_certificate_recovery_replays_after_session_event_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    authorizer.abort_stage_close(
        context,
        governance_request=_recovery_abort_request(),
    )
    request = _replacement_certificate_request(ready)
    recovery_request = _new_certificate_recovery_request(request, "event-crash")

    def interrupt(phase: str) -> None:
        if phase == "recovery_session_authorized":
            raise RuntimeError("simulated post-event crash")

    monkeypatch.setattr(authorizer, "_checkpoint", interrupt)
    with pytest.raises(RuntimeError, match="simulated post-event crash"):
        authorizer.reauthorize_aborted_close(
            context,
            recovery_request=recovery_request,
            certificate_request=request,
        )
    monkeypatch.setattr(authorizer, "_checkpoint", lambda _phase: None)

    recovered = authorizer.reauthorize_aborted_close(
        context,
        recovery_request=recovery_request,
        certificate_request=request,
    )

    assert recovered.status == "authorized"
    assert ready.fixture.service.get(ready.fixture.scope).state == "authorized"


def test_recovery_repo_lease_expiry_blocks_session_reauthorization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    authorizer.abort_stage_close(
        context,
        governance_request=_recovery_abort_request(),
    )
    request = _replacement_certificate_request(ready)
    recovery_request = _new_certificate_recovery_request(request, "lease-expiry")

    def expire_repo_lease(phase: str) -> None:
        if phase == "recovery_claim_created":
            authorizer._repo_leases._clock = lambda: "2026-07-21T17:00:00Z"

    monkeypatch.setattr(authorizer, "_checkpoint", expire_repo_lease)
    with pytest.raises(SharedStateIntegrityError, match="lease|current"):
        authorizer.reauthorize_aborted_close(
            context,
            recovery_request=recovery_request,
            certificate_request=request,
        )

    assert ready.fixture.service.get(ready.fixture.scope).state == "needs_user"


def test_recovery_session_commit_rechecks_repo_lease_after_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    authorizer.abort_stage_close(
        context,
        governance_request=_recovery_abort_request(),
    )
    request = _replacement_certificate_request(ready)
    recovery_request = _new_certificate_recovery_request(request, "lease-toctou")
    original = authorizer._session.reauthorize

    def expire_before_session_commit(*args: object, **kwargs: object):
        authorizer._repo_leases._clock = lambda: "2026-07-21T17:00:00Z"
        return original(*args, **kwargs)

    monkeypatch.setattr(authorizer._session, "reauthorize", expire_before_session_commit)
    with pytest.raises(SharedStateIntegrityError, match="lease|current"):
        authorizer.reauthorize_aborted_close(
            context,
            recovery_request=recovery_request,
            certificate_request=request,
        )

    assert ready.fixture.service.get(ready.fixture.scope).state == "needs_user"


def test_recovery_terminal_decisions_are_idempotent_and_mutually_exclusive(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    authorizer.abort_stage_close(
        context,
        governance_request=_recovery_abort_request(),
    )
    request = _replacement_certificate_request(ready)
    recovery_request = _new_certificate_recovery_request(request, "idempotent")

    first = authorizer.reauthorize_aborted_close(
        context,
        recovery_request=recovery_request,
        certificate_request=request,
    )
    authorizer._recovery._clock = lambda: "2026-07-22T15:00:00Z"
    replay = authorizer.reauthorize_aborted_close(
        context,
        recovery_request=recovery_request,
        certificate_request=request,
    )

    assert replay == first
    with pytest.raises(SharedStateIntegrityError, match="bound|terminal|decision"):
        authorizer.supersede_aborted_close(
            context,
            recovery_request=_supersede_recovery_request("competing"),
        )


def test_unauthorized_recovery_writes_no_decision_or_session_event(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    authorizer.abort_stage_close(
        context,
        governance_request=_recovery_abort_request(),
    )
    request = _replacement_certificate_request(ready)
    before = ready.fixture.service.get(ready.fixture.scope)
    unauthorized = _new_certificate_recovery_request(request, "unauthorized")
    unauthorized = unauthorized.model_copy(update={"actor_id": "actor.untrusted"})

    with pytest.raises(SharedStateIntegrityError, match="authorized"):
        authorizer.reauthorize_aborted_close(
            context,
            recovery_request=unauthorized,
            certificate_request=request,
        )

    assert ready.fixture.service.get(ready.fixture.scope) == before
    recovery_root = _governance(tmp_path)._root / "recovery-decisions"
    assert not list(recovery_root.glob("*.json"))


def test_completed_recovery_wins_if_resource_guard_becomes_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    authorizer.abort_stage_close(
        context,
        governance_request=_recovery_abort_request(),
    )
    request = _replacement_certificate_request(ready)
    recovery_request = _new_certificate_recovery_request(request, "race")
    first = authorizer.reauthorize_aborted_close(
        context,
        recovery_request=recovery_request,
        certificate_request=request,
    )
    store = ready.fixture.service._store
    completed = store.is_operation_complete
    checks = iter((False, True))

    def operation_complete(*args: object) -> bool:
        try:
            return next(checks)
        except StopIteration:
            return completed(*args)

    monkeypatch.setattr(
        store,
        "is_operation_complete",
        operation_complete,
    )

    def unavailable(_command: object) -> object:
        raise SharedStateIntegrityError("resource snapshot changed after commit")

    monkeypatch.setattr(
        ready.authority,
        "hold_reconciled_close_current",
        unavailable,
    )
    replay = authorizer.reauthorize_aborted_close(
        context,
        recovery_request=recovery_request,
        certificate_request=request,
    )

    assert replay == first


def test_completed_recovery_replays_while_overlapping_repo_lease_is_held(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    authorizer.abort_stage_close(
        context,
        governance_request=_recovery_abort_request(),
    )
    request = _replacement_certificate_request(ready)
    recovery_request = _new_certificate_recovery_request(request, "repo-race")
    first = authorizer.reauthorize_aborted_close(
        context,
        recovery_request=recovery_request,
        certificate_request=request,
    )
    replacement = context.model_copy(
        update={
            "certificate": first.certificate,
            "certificate_request": request,
            "context_digest": "",
        }
    )
    lease_request = build_close_lease_request(
        replacement,
        first.claim,
        authorizer._store.load_state(first.claim),
    )

    with authorizer._repo_leases.acquire(lease_request):
        replay = authorizer.reauthorize_aborted_close(
            context,
            recovery_request=recovery_request,
            certificate_request=request,
        )

    assert replay == first


def _replacement_certificate_request(
    ready: _ReadyCertificate,
) -> StageCloseCertificateRequest:
    original = ready.request
    payload = original.intent.model_dump(mode="json")
    payload.update(
        command_id="stage-close.implementation.reconciled.2",
        idempotency_key="stage-close-key.implementation.reconciled.2",
        close_intent_digest="",
    )
    intent = StageCloseIntent.model_validate(payload)
    session = ready.fixture.service.get(ready.fixture.scope)
    return original.model_copy(
        update={"intent": intent, "expected_session_revision": session.revision}
    )


def _new_certificate_recovery_request(
    request: StageCloseCertificateRequest,
    suffix: str,
) -> StageCloseRecoveryRequest:
    return StageCloseRecoveryRequest(
        actor_id="actor.stage-close-governor",
        idempotency_key=f"stage-close-recovery.new-certificate.{suffix}",
        recovery_kind="authorize_new_certificate",
        new_command_id=request.intent.command_id,
        reason_code="manual_reconciliation_completed",
        reason="The aborted close was reconciled with a new certificate.",
    )


def _supersede_recovery_request(suffix: str) -> StageCloseRecoveryRequest:
    return StageCloseRecoveryRequest(
        actor_id="actor.stage-close-governor",
        idempotency_key=f"stage-close-recovery.supersede.{suffix}",
        recovery_kind="supersede_session",
        reason_code="macro_rebaseline_accepted",
        reason="The aborted session is replaced by a new loop round.",
    )


def _recovery_abort_request() -> StageCloseAbortRequest:
    return _abort_request("recovery")
