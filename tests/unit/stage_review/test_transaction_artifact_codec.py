from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel
from tests.unit.stage_review.test_authorizer import (
    _abort_request,
    _authorizer,
    _context,
    _governance,
)
from tests.unit.stage_review.test_certificates import _ready_certificate
from tests.unit.stage_review.test_repo_write_lease import (
    _request,
)

from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.close_recovery_models import StageCloseRecoveryRequest
from ai_sdlc.core.stage_review.repo_write_lease import RepoWriteLeaseAuthority
from ai_sdlc.core.stage_review.transaction_artifact_codec import (
    decode_transaction_artifact,
)

ArtifactCase = tuple[type[BaseModel], BaseModel, str, str]


def test_transaction_codecs_read_current_and_previous_major(
    tmp_path: Path,
) -> None:
    for model_type, artifact, previous_version, digest_field in _artifact_cases(
        tmp_path
    ):
        current = artifact.model_dump(mode="json")
        assert decode_transaction_artifact(model_type, current) == artifact

        previous = _previous_payload(current, previous_version, digest_field)
        decoded = decode_transaction_artifact(model_type, previous)

        assert decoded.compatibility_mode == "read-only-legacy"
        assert getattr(decoded, digest_field) == previous[digest_field]
        assert decoded.extensions["source_schema_version"] == previous_version


def test_transaction_codecs_reject_future_and_tampered_previous(
    tmp_path: Path,
) -> None:
    for model_type, artifact, previous_version, digest_field in _artifact_cases(
        tmp_path
    ):
        current = artifact.model_dump(mode="json")
        with pytest.raises(ValueError, match="unknown transaction artifact schema"):
            decode_transaction_artifact(
                model_type,
                {**current, "schema_version": "future.v99"},
            )

        previous = _previous_payload(current, previous_version, digest_field)
        previous["extensions"] = {"tampered": True}
        with pytest.raises(ValueError, match="previous.*digest"):
            decode_transaction_artifact(model_type, previous)


def test_close_store_reads_previous_claim_but_refuses_to_extend_it(
    tmp_path: Path,
) -> None:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    claim = authorizer._create_claim(context)
    path = authorizer._store.claims_dir / f"{claim.certificate_id}.json"
    previous = _previous_payload(
        claim.model_dump(mode="json"),
        "close-consumption-claim.v0",
        "claim_digest",
    )
    path.write_text(json.dumps(previous), encoding="utf-8")

    legacy = authorizer._store.read_claim(claim.certificate_id)
    assert legacy is not None
    assert legacy.compatibility_mode == "read-only-legacy"
    state = authorizer._store.load_state(legacy)
    with pytest.raises(SharedStateIntegrityError, match="read-only"):
        authorizer._store.append_event(
            legacy,
            state,
            "prepared",
            occurred_at=claim.prepared_at,
            authorize_write=lambda: None,
        )

    path.write_text(
        json.dumps({**claim.model_dump(mode="json"), "schema_version": "v99"}),
        encoding="utf-8",
    )
    with pytest.raises(SharedStateIntegrityError, match="invalid"):
        authorizer._store.read_claim(claim.certificate_id)


def test_repo_store_rebuilds_previous_events_but_refuses_mixed_append(
    tmp_path: Path,
) -> None:
    authority = RepoWriteLeaseAuthority(
        tmp_path,
        project_id="project.codec-repo",
        clock=lambda: "2026-07-21T15:00:00Z",
    )
    with authority.acquire(_request(tmp_path, owner="owner.previous-repo")):
        pass
    previous_digest = ""
    for path in sorted(authority._store.events_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["previous_event_digest"] = previous_digest
        previous = _previous_payload(
            payload,
            "repo-write-lease-event.v0",
            "event_digest",
        )
        path.write_text(json.dumps(previous), encoding="utf-8")
        previous_digest = str(previous["event_digest"])
    authority._store.projection_path.unlink()

    rebuilt = authority._store.current()
    assert not rebuilt.active_leases
    with (
        pytest.raises(SharedStateIntegrityError, match="read-only"),
        authority.acquire(_request(tmp_path, owner="owner.after-previous")),
    ):
        pass


def _artifact_cases(tmp_path: Path) -> tuple[ArtifactCase, ...]:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    result = authorizer.authorize_stage_close(_context(ready, authorizer))
    assert result.receipt is not None
    events = authorizer._store._read_events(result.claim.certificate_id)
    governance = _governance(tmp_path)
    decision = governance.issue_abort(_abort_request("codec"), result.claim)
    recovery = _recovery_decision(tmp_path / "recovery")
    return (
        (
            type(result.claim),
            result.claim,
            "close-consumption-claim.v0",
            "claim_digest",
        ),
        (
            type(events[0]),
            events[0],
            "close-consumption-event.v0",
            "event_digest",
        ),
        (
            type(result.receipt),
            result.receipt,
            "stage-close-consumption-receipt.v0",
            "receipt_digest",
        ),
        *_repo_cases(tmp_path, ready.authorized.scope.project_id),
        (
            type(governance._binding),
            governance._binding,
            "stage-close-governance-authority.v0",
            "binding_digest",
        ),
        (
            type(decision),
            decision,
            "stage-close-governance-decision.v0",
            "decision_digest",
        ),
        (
            type(recovery),
            recovery,
            "stage-close-recovery-decision.v0",
            "decision_digest",
        ),
    )


def _recovery_decision(tmp_path: Path) -> BaseModel:
    ready = _ready_certificate(tmp_path)
    authorizer = _authorizer(tmp_path, ready)
    context = _context(ready, authorizer)
    aborted = authorizer.abort_stage_close(
        context,
        governance_request=_abort_request("codec-recovery"),
    )
    session = ready.fixture.service.get(ready.fixture.scope)
    request = StageCloseRecoveryRequest(
        actor_id="actor.stage-close-governor",
        idempotency_key="stage-close-recovery.codec",
        recovery_kind="supersede_session",
        reason_code="macro_rebaseline_accepted",
        reason="The codec fixture supersedes the aborted session.",
    )
    return _governance(tmp_path).issue_recovery(
        request,
        aborted.claim,
        session,
    )


def _repo_cases(
    tmp_path: Path,
    project_id: str,
) -> tuple[ArtifactCase, ArtifactCase]:
    authority = RepoWriteLeaseAuthority(
        tmp_path,
        project_id=project_id,
        clock=lambda: "2026-07-21T15:00:00Z",
    )
    with authority.acquire(_request(tmp_path, owner="owner.codec")) as guard:
        lease = guard.lease
        event = authority._store._read_events()[0]
    return (
        (type(lease), lease, "repo-write-lease.v0", "lease_digest"),
        (
            type(event),
            event,
            "repo-write-lease-event.v0",
            "event_digest",
        ),
    )


def _previous_payload(
    current: dict[str, object],
    previous_version: str,
    digest_field: str,
) -> dict[str, object]:
    payload = {**current, "schema_version": previous_version, digest_field: ""}
    protected = {key: value for key, value in payload.items() if key != digest_field}
    payload[digest_field] = canonical_digest(protected, CanonicalizationPolicy())
    return payload
