"""Tests for the frozen AI-SDLC v2 benefit benchmark contract."""

import copy
import json
import multiprocessing
import os
import subprocess
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

import ai_sdlc.benefit_benchmark as benchmark_core
from ai_sdlc.benefit_benchmark import (
    AttemptCompletion as RawAttemptCompletion,
)
from ai_sdlc.benefit_benchmark import (
    AttemptRequest,
    ExecutionLock,
    canonical_protocol_digest,
    load_protocol,
    validate_protocol,
    validate_provider_output_schema,
    verify_summary,
)
from ai_sdlc.benefit_benchmark import (
    record_provider_completion as _core_record_provider_completion,
)
from ai_sdlc.benefit_benchmark import (
    record_service_transaction as _core_record_service_transaction,
)
from ai_sdlc.benefit_benchmark import (
    reserve_provider_attempt as _core_reserve_provider_attempt,
)
from ai_sdlc.benefit_benchmark import seal_run_evidence as _core_seal_run_evidence
from ai_sdlc.benefit_benchmark import start_run as _core_start_run
from ai_sdlc.benefit_benchmark import (
    start_service_transaction as _core_start_service_transaction,
)
from ai_sdlc.benefit_benchmark import (
    transition_run_phase as _core_transition_run_phase,
)
from ai_sdlc.benefit_benchmark import verify_receipt as _verify_receipt

REPO_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = REPO_ROOT / "benchmarks" / "ai-sdlc-v2-benefits" / "protocol.json"


def _write_execution_authorization(
    path: Path,
    protocol,
    *,
    valid_from: datetime | None = None,
    expires_at: datetime | None = None,
) -> Path:
    now = datetime.now(UTC)
    payload = {
        "schema": "ai-sdlc-v2-benefit-execution-authorization/v1",
        "protocol_sha256": canonical_protocol_digest(protocol),
        "execution_identity": {
            field.name: getattr(protocol.execution_lock, field.name)
            for field in fields(ExecutionLock)
        },
        "attempt_budget": {
            field.name: getattr(protocol.attempt_budget, field.name)
            for field in fields(protocol.attempt_budget)
        },
        "valid_from": (valid_from or now - timedelta(minutes=1)).isoformat().replace(
            "+00:00", "Z"
        ),
        "expires_at": (expires_at or now + timedelta(hours=1)).isoformat().replace(
            "+00:00", "Z"
        ),
        "scope": {
            "mode": "synthetic-unit-mutation",
            "run_ids": [run.run_id for run in protocol.run_matrix],
            "operations": [
                "start_run",
                "transition_run_phase",
                "reserve_provider_attempt",
                "record_provider_completion",
                "start_service_transaction",
                "record_service_transaction",
                "seal_run_evidence",
            ],
        },
    }
    path.write_bytes(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    path.chmod(0o600)
    return path


def _authorization_path(ledger: Path, protocol) -> Path:
    return ledger.parent / (
        f"execution-authorization-{canonical_protocol_digest(protocol)}.json"
    )


def start_run(ledger: Path, protocol, contract: Path, *, run_id: str) -> None:
    _core_start_run(
        ledger,
        protocol,
        contract,
        run_id=run_id,
        authorization_path=_authorization_path(ledger, protocol),
    )


def transition_run_phase(
    ledger: Path, protocol, contract: Path, *, run_id: str, next_phase: str
) -> None:
    _core_transition_run_phase(
        ledger,
        protocol,
        contract,
        run_id=run_id,
        next_phase=next_phase,
        authorization_path=_authorization_path(ledger, protocol),
    )


def _reserve_provider_attempt(ledger: Path, protocol, request, contract: Path):
    return _core_reserve_provider_attempt(
        ledger,
        protocol,
        request,
        contract,
        authorization_path=_authorization_path(ledger, protocol),
    )


def _record_provider_completion(
    ledger: Path, protocol, completion, contract: Path
) -> None:
    _core_record_provider_completion(
        ledger,
        protocol,
        completion,
        contract,
        authorization_path=_authorization_path(ledger, protocol),
    )


def start_service_transaction(
    ledger: Path,
    protocol,
    contract: Path,
    *,
    attempt_id: str,
    event_type: str,
    transaction_id: str,
) -> None:
    _core_start_service_transaction(
        ledger,
        protocol,
        contract,
        attempt_id=attempt_id,
        event_type=event_type,
        transaction_id=transaction_id,
        authorization_path=_authorization_path(ledger, protocol),
    )


def record_service_transaction(
    ledger: Path,
    protocol,
    contract: Path,
    *,
    attempt_id: str,
    event_type: str,
    transaction_id: str,
    evidence,
) -> None:
    _core_record_service_transaction(
        ledger,
        protocol,
        contract,
        attempt_id=attempt_id,
        event_type=event_type,
        transaction_id=transaction_id,
        evidence=evidence,
        authorization_path=_authorization_path(ledger, protocol),
    )


def _seal_run_evidence(
    ledger: Path,
    protocol,
    contract: Path,
    *,
    run_id: str,
    workspace_root: Path,
) -> None:
    _core_seal_run_evidence(
        ledger,
        protocol,
        contract,
        run_id=run_id,
        workspace_root=workspace_root,
        authorization_path=_authorization_path(ledger, protocol),
    )


def _ensure_run_provider(ledger: Path, protocol, run_id: str) -> None:
    contract = ledger.parent / "evidence-contract.json"
    raw = json.loads(ledger.read_text(encoding="utf-8")) if ledger.is_file() else {}
    if run_id not in raw.get("runs", {}):
        start_run(ledger, protocol, contract, run_id=run_id)
    raw = json.loads(ledger.read_text(encoding="utf-8"))
    current = raw["runs"][run_id]["current_phase"]
    if current == "setup":
        transition_run_phase(
            ledger, protocol, contract, run_id=run_id, next_phase="framework_init"
        )
        current = "framework_init"
    if current == "framework_init":
        transition_run_phase(
            ledger, protocol, contract, run_id=run_id, next_phase="provider"
        )


def reserve_provider_attempt(ledger: Path, protocol, request: AttemptRequest):
    _ensure_run_provider(ledger, protocol, request.run_id)
    return _reserve_provider_attempt(
        ledger, protocol, request, ledger.parent / "evidence-contract.json"
    )


def record_provider_completion(
    ledger: Path, protocol, completion: RawAttemptCompletion
) -> None:
    _record_provider_completion(
        ledger, protocol, completion, ledger.parent / "evidence-contract.json"
    )


def seal_run_evidence(
    ledger: Path,
    protocol,
    contract: Path,
    *,
    run_id: str,
    workspace_root: Path,
) -> None:
    raw = json.loads(ledger.read_text(encoding="utf-8"))
    current = raw["runs"][run_id]["current_phase"]
    remaining = ("post_provider", "review", "evaluation")
    if current in ("provider", *remaining):
        start = ("provider", *remaining).index(current)
        for next_phase in remaining[start:]:
            transition_run_phase(
                ledger,
                protocol,
                contract,
                run_id=run_id,
                next_phase=next_phase,
            )
    _seal_run_evidence(
        ledger,
        protocol,
        contract,
        run_id=run_id,
        workspace_root=workspace_root,
    )


def verify_receipt(receipt, protocol, ledger: Path):
    return _verify_receipt(
        receipt,
        protocol,
        ledger,
        ledger.parent / "evidence-contract.json",
        ledger.parent / "workspace",
    )


def _completion(
    attempt_id: str,
    status: str,
    content_produced: bool = False,
    **kwargs,
) -> RawAttemptCompletion:
    terminal = status in {
        "completed",
        "technical_failure",
        "failed",
        "timeout",
        "needs_operator",
        "budget_exhausted",
    }
    if terminal:
        kwargs.update(
            {
                "child_session": f"session-{attempt_id}",
                "token_usage": {
                    "input_tokens": 1 if content_produced else 0,
                    "cached_input_tokens": 0,
                    "output_tokens": 1 if content_produced else 0,
                    "reasoning_output_tokens": 0,
                },
                "raw_provider_output_sha256": _digest("9"),
            }
        )
    return RawAttemptCompletion(attempt_id, status, content_produced, **kwargs)


def _reserve_in_process(
    ledger: str, protocol_path: str, run_id: str, queue: multiprocessing.Queue
) -> None:
    try:
        result = reserve_provider_attempt(
            Path(ledger),
            load_protocol(Path(protocol_path)),
            AttemptRequest(run_id, "writer"),
        )
        queue.put(result.attempt_id)
    except ValueError as error:
        queue.put(str(error))


def _complete_in_process(
    ledger: str,
    protocol_path: str,
    attempt_id: str,
    queue: multiprocessing.Queue,
) -> None:
    try:
        record_provider_completion(
            Path(ledger),
            load_protocol(Path(protocol_path)),
            _completion(attempt_id, "failed", False),
        )
        queue.put("completed")
    except ValueError as error:
        queue.put(str(error))


def _bound_protocol_path(tmp_path: Path, *, compact: bool = False) -> Path:
    raw = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    fixture_digest = _digest("f")
    raw["execution_lock"]["fixture_tree_sha256"] = fixture_digest
    raw["execution_lock"]["fixture_commitment"] = fixture_digest
    contract_path = tmp_path / "evidence-contract.json"
    contract = {
        "schema": "ai-sdlc-v2-benefit-evidence-contract/v1",
        "runs": [
            {
                "run_id": row["run_id"],
                "artifact_slots": [
                    {
                        "path": "benchmark-task/.evidence/setup.json",
                        "category": "setup",
                        "required": True,
                        "applicable": True,
                    },
                    {
                        "path": "benchmark-task/.evidence/governance.json",
                        "category": "governance",
                        "required": True,
                        "applicable": True,
                    },
                    {
                        "path": "benchmark-task/result.txt",
                        "category": "delivery",
                        "required": True,
                        "applicable": True,
                    },
                ],
                "changed_files_scope": {
                    "baseline_root": "baseline",
                    "candidate_root": "benchmark-task",
                    "include_paths": ["result.txt"],
                },
                "allowed_automated_event_types": [
                    "intent_service_event",
                    "clarification_request_event",
                    "approval_service_event",
                ],
            }
            for row in raw["run_matrix"]
        ],
    }
    contract_path.write_text(
        json.dumps(contract, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    contract_digest = sha256(contract_path.read_bytes()).hexdigest()
    raw["execution_lock"]["evidence_contract_sha256"] = contract_digest
    raw["execution_lock"]["evidence_contract_commitment"] = contract_digest
    path = tmp_path / ("protocol-compact.json" if compact else "protocol.json")
    path.write_text(
        json.dumps(raw, separators=(",", ":") if compact else None), encoding="utf-8"
    )
    protocol = load_protocol(path)
    _write_execution_authorization(
        tmp_path
        / f"execution-authorization-{canonical_protocol_digest(protocol)}.json",
        protocol,
    )
    return path


def _pending_protocol_path(tmp_path: Path) -> Path:
    raw = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    for field in (
        "fixture_tree_sha256",
        "fixture_commitment",
        "evidence_contract_sha256",
        "evidence_contract_commitment",
    ):
        raw["execution_lock"][field] = "pending-unbound"
    path = tmp_path / "protocol-pending.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def _bound_protocol(tmp_path: Path):
    return load_protocol(_bound_protocol_path(tmp_path))


def test_protocol_freezes_arms_fixtures_matrix_and_balanced_schedule() -> None:
    """The preregistration only permits the exact 5-by-3 balanced matrix."""
    protocol = load_protocol(PROTOCOL_PATH)

    assert protocol.arms == ("P", "S", "A00", "A10", "A11")
    assert protocol.fixtures == (
        "requirement-contract-ambiguity",
        "frontend-recovery-delivery",
        "multi-tenant-security-review",
    )
    assert len(protocol.run_matrix) == 15
    assert len({(run.arm, run.fixture) for run in protocol.run_matrix}) == 15
    assert validate_protocol(protocol, REPO_ROOT) == []

    positions_by_arm: dict[str, list[int]] = {arm: [] for arm in protocol.arms}
    for run in protocol.run_matrix:
        positions_by_arm[run.arm].append(run.position)
    assert {
        arm: sum(positions) / len(positions)
        for arm, positions in positions_by_arm.items()
    } == {arm: 3 for arm in protocol.arms}


@pytest.mark.parametrize(
    ("change", "expected_code"),
    [
        (lambda protocol: replace(protocol, arms=("P",)), "protocol.arms"),
        (
            lambda protocol: replace(protocol, fixtures=("fixture",)),
            "protocol.fixtures",
        ),
        (
            lambda protocol: replace(protocol, run_matrix=protocol.run_matrix[:-1]),
            "protocol.matrix",
        ),
        (
            lambda protocol: replace(
                protocol,
                run_matrix=tuple(
                    replace(run, position=1) if run.arm == "P" else run
                    for run in protocol.run_matrix
                ),
            ),
            "protocol.schedule",
        ),
        (
            lambda protocol: replace(
                protocol, attempt_budget=replace(protocol.attempt_budget, limit=32)
            ),
            "protocol.budget",
        ),
    ],
)
def test_protocol_rejects_any_preregistration_drift(change, expected_code) -> None:
    protocol = change(load_protocol(PROTOCOL_PATH))
    assert expected_code in {
        issue.code for issue in validate_protocol(protocol, REPO_ROOT)
    }


def test_protocol_rejects_every_execution_lock_field_drift() -> None:
    protocol = load_protocol(PROTOCOL_PATH)

    for field in fields(ExecutionLock):
        original = getattr(protocol.execution_lock, field.name)
        changed = original + 1 if isinstance(original, int) else f"drift-{original}"
        drifted = replace(
            protocol,
            execution_lock=replace(protocol.execution_lock, **{field.name: changed}),
        )
        assert "protocol.lock" in {
            issue.code for issue in validate_protocol(drifted, REPO_ROOT)
        }, field.name


def test_pending_fixture_protocol_reaches_reservation_api_and_does_not_mutate_ledger(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.json"
    pending = load_protocol(_pending_protocol_path(tmp_path))

    with pytest.raises(ValueError, match="pending"):
        reserve_provider_attempt(
            ledger,
            pending,
            AttemptRequest("P:requirement-contract-ambiguity", "writer"),
        )
    with pytest.raises(ValueError, match="pending"):
        record_provider_completion(
            ledger,
            pending,
            _completion("attempt-001", "failed", False),
        )

    assert not ledger.exists()


def test_ledger_binds_exact_protocol_digest_for_reservation_and_completion(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.json"
    protocol = _bound_protocol(tmp_path)
    same_values_different_bytes = load_protocol(
        _bound_protocol_path(tmp_path, compact=True)
    )
    writer = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest("P:requirement-contract-ambiguity", "writer"),
    )

    with pytest.raises(ValueError, match="protocol digest"):
        reserve_provider_attempt(
            ledger,
            same_values_different_bytes,
            AttemptRequest("S:requirement-contract-ambiguity", "writer"),
        )
    with pytest.raises(ValueError, match="protocol digest"):
        record_provider_completion(
            ledger,
            same_values_different_bytes,
            _completion(writer.attempt_id, "failed", False),
        )


def test_reservation_rejects_protocol_object_that_does_not_match_its_exact_bytes(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.json"
    forged = replace(_bound_protocol(tmp_path), canonical_bytes=b"{}")

    with pytest.raises(ValueError, match="canonical bytes"):
        reserve_provider_attempt(
            ledger,
            forged,
            AttemptRequest("P:requirement-contract-ambiguity", "writer"),
        )

    assert not ledger.exists()


@pytest.mark.parametrize(
    "attempt_request",
    [
        AttemptRequest(
            "P:requirement-contract-ambiguity",
            "technical_retry",
            retry_reason="content",
        ),
        AttemptRequest("P:requirement-contract-ambiguity", "content_retry"),
    ],
)
def test_reservation_still_rejects_content_retries(
    tmp_path: Path, attempt_request: AttemptRequest
) -> None:
    with pytest.raises(ValueError, match="content"):
        reserve_provider_attempt(
            tmp_path / "ledger.json", _bound_protocol(tmp_path), attempt_request
        )


def test_technical_retry_requires_pre_output_failure_and_stops_after_three(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.json"
    protocol = _bound_protocol(tmp_path)
    previous = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest("P:requirement-contract-ambiguity", "writer"),
    )

    def retry_request(attempt_id: str) -> AttemptRequest:
        return AttemptRequest(
            "P:requirement-contract-ambiguity",
            "technical_retry",
            retry_reason="transport",
            retry_of_attempt_id=attempt_id,
        )

    with pytest.raises(ValueError, match="terminated pre-output"):
        reserve_provider_attempt(ledger, protocol, retry_request(previous.attempt_id))
    record_provider_completion(
        ledger,
        protocol,
        _completion(previous.attempt_id, "technical_failure", False),
    )
    for _ in range(3):
        previous = reserve_provider_attempt(
            ledger, protocol, retry_request(previous.attempt_id)
        )
        record_provider_completion(
            ledger,
            protocol,
            _completion(previous.attempt_id, "technical_failure", False),
        )
    with pytest.raises(ValueError, match="budget"):
        reserve_provider_attempt(ledger, protocol, retry_request(previous.attempt_id))


def test_writer_technical_retry_inherits_writer_state_machine_through_close(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.json"
    protocol = _bound_protocol(tmp_path)
    parent_digest = _digest("a")
    candidate_digest = _digest("b")
    writer = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest(
            "A11:requirement-contract-ambiguity",
            "writer",
            parent_digest=parent_digest,
        ),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(writer.attempt_id, "technical_failure", False),
    )

    retry = _reserve_technical_retry(ledger, protocol, writer.attempt_id)
    for status in ("candidate_ready", "review_pending"):
        record_provider_completion(
            ledger,
            protocol,
            _completion(
                retry.attempt_id,
                status,
                True,
                candidate_digest=candidate_digest,
            ),
        )
    expert = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest(
            "A11:requirement-contract-ambiguity",
            "primary_expert",
            parent_attempt_id=retry.attempt_id,
            role="primary",
            parent_digest=parent_digest,
            candidate_digest=candidate_digest,
        ),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(expert.attempt_id, "completed", True),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            retry.attempt_id,
            "completed",
            True,
            candidate_digest=candidate_digest,
            close_digest=_digest("c"),
        ),
    )

    persisted = json.loads(ledger.read_text(encoding="utf-8"))
    assert persisted["attempts"][1]["effective_kind"] == "writer"
    assert persisted["attempts"][1]["status"] == "completed"


def test_expert_technical_retry_inherits_role_and_lineage_through_close(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.json"
    protocol = _bound_protocol(tmp_path)
    writer, expert = _writer_at_review_with_expert(ledger, protocol)
    record_provider_completion(
        ledger,
        protocol,
        _completion(expert.attempt_id, "technical_failure", False),
    )

    retry = _reserve_technical_retry(ledger, protocol, expert.attempt_id)
    record_provider_completion(
        ledger,
        protocol,
        _completion(retry.attempt_id, "completed", True),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "completed",
            True,
            candidate_digest=_digest("b"),
            close_digest=_digest("c"),
        ),
    )

    persisted = json.loads(ledger.read_text(encoding="utf-8"))
    assert persisted["attempts"][2]["effective_kind"] == "primary_expert"
    assert persisted["attempts"][2]["role"] == "primary"


def test_rereview_technical_retry_inherits_repair_lineage_through_close(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.json"
    protocol = _bound_protocol(tmp_path)
    writer, expert = _writer_at_review_with_expert(ledger, protocol)
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            expert.attempt_id,
            "completed",
            True,
            finding_digest=_digest("d"),
        ),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "candidate_ready",
            True,
            candidate_digest=_digest("c"),
            finding_digest=_digest("d"),
            repair_digest=_digest("e"),
        ),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "review_pending",
            True,
            candidate_digest=_digest("c"),
        ),
    )
    rereview = reserve_provider_attempt(
        ledger,
        protocol,
        _rereview_request(expert.attempt_id, _digest("c")),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(rereview.attempt_id, "technical_failure", False),
    )

    retry = _reserve_technical_retry(ledger, protocol, rereview.attempt_id)
    record_provider_completion(
        ledger,
        protocol,
        _completion(retry.attempt_id, "completed", True),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "completed",
            True,
            candidate_digest=_digest("c"),
            close_digest=_digest("8"),
        ),
    )

    persisted = json.loads(ledger.read_text(encoding="utf-8"))
    assert persisted["attempts"][3]["effective_kind"] == "expert_rereview"
    assert persisted["attempts"][3]["finding_digest"] == _digest("d")
    assert persisted["attempts"][3]["repair_digest"] == _digest("e")


@pytest.mark.parametrize("effective_role", ["expert", "rereview"])
@pytest.mark.parametrize("path", ["online", "reload"])
def test_technical_retry_replays_effective_role_active_parent_requirement(
    tmp_path: Path, effective_role: str, path: str
) -> None:
    ledger = tmp_path / "ledger.json"
    protocol = _bound_protocol(tmp_path)
    writer, retried = _failed_effective_role_attempt(
        ledger, protocol, effective_role
    )

    if path == "online":
        record_provider_completion(
            ledger,
            protocol,
            _completion(writer.attempt_id, "failed", True),
        )
    else:
        retry = _reserve_technical_retry(ledger, protocol, retried.attempt_id)
        record_provider_completion(
            ledger,
            protocol,
            _completion(writer.attempt_id, "failed", True),
        )
        raw = json.loads(ledger.read_text(encoding="utf-8"))
        writer_attempt = next(
            attempt
            for attempt in raw["attempts"]
            if attempt["attempt_id"] == writer.attempt_id
        )
        retry_attempt = next(
            attempt
            for attempt in raw["attempts"]
            if attempt["attempt_id"] == retry.attempt_id
        )
        writer_failure = writer_attempt["history"][-1]
        retry_reservation = retry_attempt["history"][0]
        writer_failure["sequence"], retry_reservation["sequence"] = (
            retry_reservation["sequence"],
            writer_failure["sequence"],
        )
        writer_failure["recorded_at"], retry_reservation["recorded_at"] = (
            retry_reservation["recorded_at"],
            writer_failure["recorded_at"],
        )
        writer_attempt["sequence"] = writer_failure["sequence"]
        writer_attempt["recorded_at"] = writer_failure["recorded_at"]
        retry_attempt["sequence"] = retry_reservation["sequence"]
        retry_attempt["recorded_at"] = retry_reservation["recorded_at"]
        ledger.write_text(json.dumps(raw), encoding="utf-8")

    unchanged = ledger.read_bytes()
    with pytest.raises(ValueError, match="active parent"):
        if path == "online":
            _reserve_technical_retry(ledger, protocol, retried.attempt_id)
        else:
            reserve_provider_attempt(
                ledger,
                protocol,
                AttemptRequest("S:requirement-contract-ambiguity", "writer"),
            )
    assert ledger.read_bytes() == unchanged


def test_duplicate_writer_and_unreserved_completion_stay_rejected(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.json"
    protocol = _bound_protocol(tmp_path)
    request = AttemptRequest("P:requirement-contract-ambiguity", "writer")
    reserve_provider_attempt(ledger, protocol, request)

    with pytest.raises(ValueError, match="replacement"):
        reserve_provider_attempt(ledger, protocol, request)
    with pytest.raises(ValueError, match="reservation"):
        record_provider_completion(
            ledger,
            protocol,
            _completion("attempt-999", "failed", False),
        )


@pytest.mark.parametrize(
    "corruption",
    [
        "missing",
        "unknown",
        "invalid",
        "reserved_close",
        "reserved_repair",
        "bool_count",
    ],
)
def test_ledger_load_rejects_any_corrupt_attempt_shape(
    tmp_path: Path, corruption: str
) -> None:
    ledger = tmp_path / "ledger.json"
    protocol = _bound_protocol(tmp_path)
    reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest("P:requirement-contract-ambiguity", "writer"),
    )
    raw = json.loads(ledger.read_text(encoding="utf-8"))
    if corruption == "missing":
        raw["attempts"][0].pop("role")
    elif corruption == "unknown":
        raw["attempts"][0]["surprise"] = True
    elif corruption == "reserved_close":
        raw["attempts"][0]["close_digest"] = _digest("c")
        raw["attempts"][0]["history"][0]["close_digest"] = _digest("c")
    elif corruption == "reserved_repair":
        raw["attempts"][0]["finding_digest"] = _digest("d")
        raw["attempts"][0]["repair_digest"] = _digest("e")
        raw["attempts"][0]["history"][0]["finding_digest"] = _digest("d")
        raw["attempts"][0]["history"][0]["repair_digest"] = _digest("e")
    elif corruption == "bool_count":
        raw["attempts_started"] = True
    else:
        raw["attempts"][0]["status"] = "invented"
    ledger.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="attempt"):
        reserve_provider_attempt(
            ledger,
            protocol,
            AttemptRequest("S:requirement-contract-ambiguity", "writer"),
        )


def test_completion_evidence_schema_bump_rejects_legacy_v5_without_rewriting(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.json"
    protocol = _bound_protocol(tmp_path)
    reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest("P:requirement-contract-ambiguity", "writer"),
    )
    raw = json.loads(ledger.read_text(encoding="utf-8"))
    raw["schema"] = "ai-sdlc-v2-benefit-attempt-ledger/v5"
    legacy_bytes = json.dumps(raw).encode()
    ledger.write_bytes(legacy_bytes)

    with pytest.raises(ValueError, match="unexpected schema"):
        reserve_provider_attempt(
            ledger,
            protocol,
            AttemptRequest("S:requirement-contract-ambiguity", "writer"),
        )

    assert ledger.read_bytes() == legacy_bytes


def test_ledger_load_rejects_kind_specific_topology_corruption(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.json"
    protocol = _bound_protocol(tmp_path)
    _writer_at_review_with_expert(ledger, protocol)
    raw = json.loads(ledger.read_text(encoding="utf-8"))
    raw["attempts"][1]["kind"] = "cross_risk_expert"
    raw["attempts"][1]["effective_kind"] = "cross_risk_expert"
    raw["attempts"][1]["role"] = "cross-risk"
    ledger.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="cross-risk"):
        reserve_provider_attempt(
            ledger,
            protocol,
            AttemptRequest("S:requirement-contract-ambiguity", "writer"),
        )


def test_ledger_reload_requires_parent_current_state_at_expert_reservation(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.json"
    protocol = _bound_protocol(tmp_path)
    writer, _expert = _writer_at_review_with_expert(ledger, protocol)
    record_provider_completion(
        ledger,
        protocol,
        _completion(writer.attempt_id, "failed", True),
    )
    raw = json.loads(ledger.read_text(encoding="utf-8"))
    writer_failure = raw["attempts"][0]["history"][-1]
    expert_reservation = raw["attempts"][1]["history"][0]
    writer_failure["sequence"], expert_reservation["sequence"] = (
        expert_reservation["sequence"],
        writer_failure["sequence"],
    )
    writer_failure["recorded_at"], expert_reservation["recorded_at"] = (
        expert_reservation["recorded_at"],
        writer_failure["recorded_at"],
    )
    raw["attempts"][0]["sequence"] = writer_failure["sequence"]
    raw["attempts"][0]["recorded_at"] = writer_failure["recorded_at"]
    raw["attempts"][1]["sequence"] = expert_reservation["sequence"]
    raw["attempts"][1]["recorded_at"] = expert_reservation["recorded_at"]
    ledger.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="expert parent chain"):
        reserve_provider_attempt(
            ledger,
            protocol,
            AttemptRequest("S:requirement-contract-ambiguity", "writer"),
        )


@pytest.mark.parametrize("corruption", ["close", "repair", "duplicate", "budget"])
def test_ledger_reload_replays_all_online_invariants(
    tmp_path: Path, corruption: str
) -> None:
    ledger = tmp_path / "ledger.json"
    protocol = _bound_protocol(tmp_path)
    writer = _writer_at_review(ledger, protocol)
    raw = json.loads(ledger.read_text(encoding="utf-8"))
    attempt = raw["attempts"][0]
    if corruption == "close":
        _append_raw_event(
            attempt,
            status="completed",
            candidate_digest=_digest("b"),
            close_digest=_digest("c"),
            terminal=True,
        )
    elif corruption == "repair":
        _append_raw_event(
            attempt,
            status="candidate_ready",
            candidate_digest=_digest("c"),
            finding_digest=_digest("d"),
            repair_digest=_digest("e"),
            terminal=False,
        )
    else:
        copies = 2 if corruption == "duplicate" else 34
        raw["attempts"] = []
        for index in range(1, copies + 1):
            cloned = copy.deepcopy(attempt)
            cloned["attempt_id"] = f"attempt-{index:03d}"
            raw["attempts"].append(cloned)
        raw["attempts_started"] = copies
    ledger.write_text(json.dumps(raw), encoding="utf-8")

    expected = "budget" if corruption == "budget" else "ledger invariant"
    with pytest.raises(ValueError, match=expected):
        record_provider_completion(
            ledger,
            protocol,
            _completion(writer.attempt_id, "failed", True),
        )


@pytest.mark.parametrize(
    ("kind", "role"),
    [("primary_expert", "primary"), ("cross_risk_expert", "cross-risk")],
)
def test_expert_requires_writer_review_pending_candidate_checkpoint(
    tmp_path: Path, kind: str, role: str
) -> None:
    ledger = tmp_path / "ledger.json"
    protocol = _bound_protocol(tmp_path)
    parent_digest = _digest("a")
    candidate_digest = _digest("b")
    writer = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest(
            "A11:multi-tenant-security-review",
            "writer",
            parent_digest=parent_digest,
        ),
    )
    expert_request = AttemptRequest(
        "A11:multi-tenant-security-review",
        kind,
        parent_attempt_id=writer.attempt_id,
        role=role,
        parent_digest=parent_digest,
        candidate_digest=candidate_digest,
    )

    with pytest.raises(ValueError, match="review_pending"):
        reserve_provider_attempt(ledger, protocol, expert_request)
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "candidate_ready",
            True,
            candidate_digest=candidate_digest,
        ),
    )
    with pytest.raises(ValueError, match="review_pending"):
        reserve_provider_attempt(ledger, protocol, expert_request)
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "review_pending",
            True,
            candidate_digest=candidate_digest,
        ),
    )

    expert = reserve_provider_attempt(ledger, protocol, expert_request)
    assert expert.attempt_id == "attempt-002"


def test_completed_requires_content_for_writer_and_expert(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.json"
    protocol = _bound_protocol(tmp_path)
    writer = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest("P:requirement-contract-ambiguity", "writer"),
    )

    with pytest.raises(ValueError, match="content"):
        record_provider_completion(
            ledger,
            protocol,
            _completion(
                writer.attempt_id,
                "completed",
                False,
                candidate_digest=_digest("b"),
            ),
        )

    a11_writer, expert = _writer_at_review_with_expert(ledger, protocol)
    assert a11_writer.attempt_id == "attempt-002"
    with pytest.raises(ValueError, match="content"):
        record_provider_completion(
            ledger,
            protocol,
            _completion(expert.attempt_id, "completed", False),
        )


@pytest.mark.parametrize("field", ["repair_digest", "close_digest"])
def test_expert_completion_rejects_writer_only_fields(
    tmp_path: Path, field: str
) -> None:
    ledger = tmp_path / "ledger.json"
    protocol = _bound_protocol(tmp_path)
    _, expert = _writer_at_review_with_expert(ledger, protocol)

    with pytest.raises(ValueError, match="expert"):
        record_provider_completion(
            ledger,
            protocol,
            replace(
                _completion(expert.attempt_id, "completed", True),
                **{field: _digest("c")},
            ),
        )


def test_writer_checkpoint_is_nonterminal_until_required_expert_and_close(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.json"
    protocol = _bound_protocol(tmp_path)
    writer, expert = _writer_at_review_with_expert(ledger, protocol)

    persisted = json.loads(ledger.read_text(encoding="utf-8"))
    assert persisted["attempts"][0]["status"] == "review_pending"
    assert persisted["attempts"][0]["terminal"] is False
    with pytest.raises(ValueError, match="expert"):
        record_provider_completion(
            ledger,
            protocol,
            _completion(
                writer.attempt_id,
                "completed",
                True,
                candidate_digest=_digest("b"),
                close_digest=_digest("c"),
            ),
        )

    record_provider_completion(
        ledger,
        protocol,
        _completion(expert.attempt_id, "completed", True),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "completed",
            True,
            candidate_digest=_digest("b"),
            close_digest=_digest("c"),
        ),
    )
    persisted = json.loads(ledger.read_text(encoding="utf-8"))
    assert persisted["attempts"][0]["status"] == "completed"
    assert persisted["attempts"][0]["terminal"] is True


def test_a10_writer_requires_close_digest_before_terminal_completion(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.json"
    protocol = _bound_protocol(tmp_path)
    writer = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest("A10:requirement-contract-ambiguity", "writer"),
    )
    incomplete = _completion(
        writer.attempt_id,
        "completed",
        True,
        candidate_digest=_digest("b"),
    )

    with pytest.raises(ValueError, match="Close"):
        record_provider_completion(ledger, protocol, incomplete)
    record_provider_completion(
        ledger,
        protocol,
        replace(incomplete, close_digest=_digest("c")),
    )


def test_rereview_binds_finding_same_writer_repair_and_new_candidate(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.json"
    protocol = _bound_protocol(tmp_path)
    writer, expert = _writer_at_review_with_expert(ledger, protocol)
    finding_digest = _digest("d")
    repair_digest = _digest("e")
    old_candidate = _digest("b")
    new_candidate = _digest("c")
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            expert.attempt_id,
            "completed",
            True,
            finding_digest=finding_digest,
        ),
    )

    with pytest.raises(ValueError, match="new Candidate"):
        reserve_provider_attempt(
            ledger,
            protocol,
            _rereview_request(expert.attempt_id, old_candidate),
        )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "candidate_ready",
            True,
            candidate_digest=new_candidate,
            finding_digest=finding_digest,
            repair_digest=repair_digest,
        ),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "review_pending",
            True,
            candidate_digest=new_candidate,
        ),
    )

    with pytest.raises(ValueError, match="new Candidate"):
        reserve_provider_attempt(
            ledger,
            protocol,
            _rereview_request(expert.attempt_id, old_candidate),
        )
    with pytest.raises(ValueError, match="parent"):
        reserve_provider_attempt(
            ledger,
            protocol,
            replace(
                _rereview_request(expert.attempt_id, new_candidate),
                parent_digest=_digest("9"),
            ),
        )

    rereview = reserve_provider_attempt(
        ledger,
        protocol,
        _rereview_request(expert.attempt_id, new_candidate),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(rereview.attempt_id, "completed", True),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "completed",
            True,
            candidate_digest=new_candidate,
            close_digest=_digest("8"),
        ),
    )


def test_security_writer_binds_both_expert_findings_to_one_repaired_candidate(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.json"
    protocol = _bound_protocol(tmp_path)
    parent_digest = _digest("a")
    old_candidate = _digest("b")
    new_candidate = _digest("c")
    writer = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest(
            "A11:multi-tenant-security-review",
            "writer",
            parent_digest=parent_digest,
        ),
    )
    for status in ("candidate_ready", "review_pending"):
        record_provider_completion(
            ledger,
            protocol,
            _completion(
                writer.attempt_id,
                status,
                True,
                candidate_digest=old_candidate,
            ),
        )
    experts = []
    for kind, role in (
        ("primary_expert", "primary"),
        ("cross_risk_expert", "cross-risk"),
    ):
        experts.append(
            reserve_provider_attempt(
                ledger,
                protocol,
                AttemptRequest(
                    "A11:multi-tenant-security-review",
                    kind,
                    parent_attempt_id=writer.attempt_id,
                    role=role,
                    parent_digest=parent_digest,
                    candidate_digest=old_candidate,
                ),
            )
        )
    bindings = [(_digest("d"), _digest("f")), (_digest("e"), _digest("7"))]
    for expert, (finding_digest, _) in zip(experts, bindings, strict=True):
        record_provider_completion(
            ledger,
            protocol,
            _completion(
                expert.attempt_id,
                "completed",
                True,
                finding_digest=finding_digest,
            ),
        )
    for finding_digest, repair_digest in bindings:
        record_provider_completion(
            ledger,
            protocol,
            _completion(
                writer.attempt_id,
                "candidate_ready",
                True,
                candidate_digest=new_candidate,
                finding_digest=finding_digest,
                repair_digest=repair_digest,
            ),
        )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "review_pending",
            True,
            candidate_digest=new_candidate,
        ),
    )
    for expert, (finding_digest, repair_digest) in zip(
        experts, bindings, strict=True
    ):
        rereview = reserve_provider_attempt(
            ledger,
            protocol,
            AttemptRequest(
                "A11:multi-tenant-security-review",
                "expert_rereview",
                parent_attempt_id=expert.attempt_id,
                role="primary"
                if expert is experts[0]
                else "cross-risk",
                parent_digest=parent_digest,
                candidate_digest=new_candidate,
                finding_digest=finding_digest,
                repair_digest=repair_digest,
            ),
        )
        record_provider_completion(
            ledger,
            protocol,
            _completion(rereview.attempt_id, "completed", True),
        )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "completed",
            True,
            candidate_digest=new_candidate,
            close_digest=_digest("8"),
        ),
    )


def test_security_repair_waits_for_all_required_first_review_roles(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.json"
    protocol = _bound_protocol(tmp_path)
    writer, primary = _security_writer_with_expert(
        ledger, protocol, "primary_expert", "primary"
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            primary.attempt_id,
            "completed",
            True,
            finding_digest=_digest("d"),
        ),
    )

    with pytest.raises(ValueError, match="first-review"):
        record_provider_completion(
            ledger,
            protocol,
            _completion(
                writer.attempt_id,
                "candidate_ready",
                True,
                candidate_digest=_digest("c"),
                finding_digest=_digest("d"),
                repair_digest=_digest("e"),
            ),
        )

    persisted = json.loads(ledger.read_text(encoding="utf-8"))
    assert persisted["attempts"][0]["status"] == "review_pending"
    assert persisted["attempts"][0]["candidate_digest"] == _digest("b")


def test_security_reload_rejects_first_review_candidate_baseline_drift(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.json"
    protocol = _bound_protocol(tmp_path)
    parent_digest = _digest("a")
    old_candidate = _digest("b")
    new_candidate = _digest("c")
    writer = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest(
            "A11:multi-tenant-security-review",
            "writer",
            parent_digest=parent_digest,
        ),
    )
    for status in ("candidate_ready", "review_pending"):
        record_provider_completion(
            ledger,
            protocol,
            _completion(
                writer.attempt_id,
                status,
                True,
                candidate_digest=old_candidate,
            ),
        )
    experts = [
        reserve_provider_attempt(
            ledger,
            protocol,
            AttemptRequest(
                "A11:multi-tenant-security-review",
                kind,
                parent_attempt_id=writer.attempt_id,
                role=role,
                parent_digest=parent_digest,
                candidate_digest=old_candidate,
            ),
        )
        for kind, role in (
            ("primary_expert", "primary"),
            ("cross_risk_expert", "cross-risk"),
        )
    ]
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            experts[0].attempt_id,
            "completed",
            True,
            finding_digest=_digest("d"),
        ),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(experts[1].attempt_id, "completed", True),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "candidate_ready",
            True,
            candidate_digest=new_candidate,
            finding_digest=_digest("d"),
            repair_digest=_digest("e"),
        ),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "review_pending",
            True,
            candidate_digest=new_candidate,
        ),
    )
    raw = json.loads(ledger.read_text(encoding="utf-8"))
    cross = raw["attempts"][2]
    cross["candidate_digest"] = new_candidate
    cross["history"][0]["candidate_digest"] = new_candidate
    cross["history"][1]["candidate_digest"] = new_candidate
    ledger.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="first-review Candidate baseline"):
        reserve_provider_attempt(
            ledger,
            protocol,
            AttemptRequest("S:requirement-contract-ambiguity", "writer"),
        )


def test_security_reload_replays_first_review_completion_before_repair(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.json"
    protocol = _bound_protocol(tmp_path)
    parent_digest = _digest("a")
    writer = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest(
            "A11:multi-tenant-security-review",
            "writer",
            parent_digest=parent_digest,
        ),
    )
    for status in ("candidate_ready", "review_pending"):
        record_provider_completion(
            ledger,
            protocol,
            _completion(
                writer.attempt_id,
                status,
                True,
                candidate_digest=_digest("b"),
            ),
        )
    experts = [
        reserve_provider_attempt(
            ledger,
            protocol,
            AttemptRequest(
                "A11:multi-tenant-security-review",
                kind,
                parent_attempt_id=writer.attempt_id,
                role=role,
                parent_digest=parent_digest,
                candidate_digest=_digest("b"),
            ),
        )
        for kind, role in (
            ("primary_expert", "primary"),
            ("cross_risk_expert", "cross-risk"),
        )
    ]
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            experts[0].attempt_id,
            "completed",
            True,
            finding_digest=_digest("d"),
        ),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(experts[1].attempt_id, "completed", True),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "candidate_ready",
            True,
            candidate_digest=_digest("c"),
            finding_digest=_digest("d"),
            repair_digest=_digest("e"),
        ),
    )
    raw = json.loads(ledger.read_text(encoding="utf-8"))
    call_order = (
        (0, 0),
        (0, 1),
        (0, 2),
        (1, 0),
        (2, 0),
        (1, 1),
        (2, 1),
        (0, 3),
    )
    for sequence, (attempt_index, event_index) in enumerate(call_order, start=1):
        raw["attempts"][attempt_index]["history"][event_index].setdefault(
            "sequence", sequence
        )
    for attempt in raw["attempts"]:
        attempt.setdefault("sequence", attempt["history"][-1]["sequence"])
    cross_completion = raw["attempts"][2]["history"][1]
    writer_repair = raw["attempts"][0]["history"][3]
    cross_completion["sequence"], writer_repair["sequence"] = (
        writer_repair["sequence"],
        cross_completion["sequence"],
    )
    cross_completion["recorded_at"], writer_repair["recorded_at"] = (
        writer_repair["recorded_at"],
        cross_completion["recorded_at"],
    )
    raw["attempts"][2]["sequence"] = cross_completion["sequence"]
    raw["attempts"][2]["recorded_at"] = cross_completion["recorded_at"]
    raw["attempts"][0]["sequence"] = writer_repair["sequence"]
    raw["attempts"][0]["recorded_at"] = writer_repair["recorded_at"]
    ledger.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="first-review completion"):
        reserve_provider_attempt(
            ledger,
            protocol,
            AttemptRequest("S:requirement-contract-ambiguity", "writer"),
        )


def test_terminal_failure_ends_writer_without_close(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.json"
    protocol = _bound_protocol(tmp_path)
    writer = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest(
            "A11:requirement-contract-ambiguity",
            "writer",
            parent_digest=_digest(),
        ),
    )

    record_provider_completion(
        ledger,
        protocol,
        _completion(writer.attempt_id, "failed", True),
    )

    persisted = json.loads(ledger.read_text(encoding="utf-8"))
    assert persisted["attempts"][0]["terminal"] is True


def _writer_at_review_with_expert(ledger: Path, protocol):
    parent_digest = _digest("a")
    candidate_digest = _digest("b")
    writer = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest(
            "A11:requirement-contract-ambiguity",
            "writer",
            parent_digest=parent_digest,
        ),
    )
    for status in ("candidate_ready", "review_pending"):
        record_provider_completion(
            ledger,
            protocol,
            _completion(
                writer.attempt_id,
                status,
                True,
                candidate_digest=candidate_digest,
            ),
        )
    expert = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest(
            "A11:requirement-contract-ambiguity",
            "primary_expert",
            parent_attempt_id=writer.attempt_id,
            role="primary",
            parent_digest=parent_digest,
            candidate_digest=candidate_digest,
        ),
    )
    return writer, expert


def _writer_at_review(ledger: Path, protocol):
    writer = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest(
            "A11:requirement-contract-ambiguity",
            "writer",
            parent_digest=_digest("a"),
        ),
    )
    for status in ("candidate_ready", "review_pending"):
        record_provider_completion(
            ledger,
            protocol,
            _completion(
                writer.attempt_id,
                status,
                True,
                candidate_digest=_digest("b"),
            ),
        )
    return writer


def _failed_effective_role_attempt(ledger: Path, protocol, effective_role: str):
    writer, expert = _writer_at_review_with_expert(ledger, protocol)
    if effective_role == "expert":
        record_provider_completion(
            ledger,
            protocol,
            _completion(expert.attempt_id, "technical_failure", False),
        )
        return writer, expert
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            expert.attempt_id,
            "completed",
            True,
            finding_digest=_digest("d"),
        ),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "candidate_ready",
            True,
            candidate_digest=_digest("c"),
            finding_digest=_digest("d"),
            repair_digest=_digest("e"),
        ),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "review_pending",
            True,
            candidate_digest=_digest("c"),
        ),
    )
    rereview = reserve_provider_attempt(
        ledger,
        protocol,
        _rereview_request(expert.attempt_id, _digest("c")),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(rereview.attempt_id, "technical_failure", False),
    )
    return writer, rereview


def _security_writer_with_expert(ledger: Path, protocol, kind: str, role: str):
    writer = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest(
            "A11:multi-tenant-security-review",
            "writer",
            parent_digest=_digest("a"),
        ),
    )
    for status in ("candidate_ready", "review_pending"):
        record_provider_completion(
            ledger,
            protocol,
            _completion(
                writer.attempt_id,
                status,
                True,
                candidate_digest=_digest("b"),
            ),
        )
    expert = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest(
            "A11:multi-tenant-security-review",
            kind,
            parent_attempt_id=writer.attempt_id,
            role=role,
            parent_digest=_digest("a"),
            candidate_digest=_digest("b"),
        ),
    )
    return writer, expert


def _append_raw_event(
    attempt: dict[str, object],
    *,
    status: str,
    candidate_digest: str,
    finding_digest: str | None = None,
    repair_digest: str | None = None,
    close_digest: str | None = None,
    terminal: bool,
) -> None:
    event = {
        "sequence": attempt["sequence"] + 1,
        "status": status,
        "content_produced": True,
        "candidate_digest": candidate_digest,
        "finding_digest": finding_digest,
        "repair_digest": repair_digest,
        "close_digest": close_digest,
        "terminal": terminal,
        "recorded_at": attempt["history"][-1]["recorded_at"],
        "child_session": f"session-{attempt['attempt_id']}" if terminal else None,
        "token_usage": {
            "input_tokens": 1,
            "cached_input_tokens": 0,
            "output_tokens": 1,
            "reasoning_output_tokens": 0,
        }
        if terminal
        else None,
        "raw_provider_output_sha256": _digest("9") if terminal else None,
    }
    attempt["history"].append(event)
    attempt.update(event)


def _rereview_request(parent_attempt_id: str, candidate_digest: str) -> AttemptRequest:
    return AttemptRequest(
        "A11:requirement-contract-ambiguity",
        "expert_rereview",
        parent_attempt_id=parent_attempt_id,
        role="primary",
        parent_digest=_digest("a"),
        candidate_digest=candidate_digest,
        finding_digest=_digest("d"),
        repair_digest=_digest("e"),
    )


def _reserve_technical_retry(ledger: Path, protocol, attempt_id: str):
    return reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest(
            "A11:requirement-contract-ambiguity",
            "technical_retry",
            retry_reason="transport",
            retry_of_attempt_id=attempt_id,
        ),
    )


def test_provider_output_schema_recursively_requires_closed_typed_surfaces() -> None:
    schema = {
        "type": "object",
        "properties": {
            "answer": {"const": "yes"},
            "labels": {"type": "array", "items": {"minLength": 1}},
            "details": {"properties": {"count": {"minimum": 0}}},
        },
        "additionalProperties": True,
        "oneOf": [],
    }
    codes = {issue.code for issue in validate_provider_output_schema(schema)}
    assert "provider-schema.type" in codes
    assert "provider-schema.additional-properties" in codes
    assert "provider-schema.keyword" in codes


def test_provider_output_schema_accepts_a_closed_recursive_schema() -> None:
    schema = {
        "type": "object",
        "properties": {
            "answer": {"type": "string", "const": "yes"},
            "labels": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
        },
        "required": ["answer", "labels"],
        "additionalProperties": False,
    }
    assert not validate_provider_output_schema(schema)


def _digest(char: str = "a") -> str:
    return char * 64


def test_receipt_verification_accepts_a_complete_public_receipt(
    tmp_path: Path,
) -> None:
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    assert not verify_receipt(receipt, protocol, ledger)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda receipt: receipt.pop("failure_classification"),
        lambda receipt: receipt["changed_files"].append("/private/secret.txt"),
        lambda receipt: receipt["digests"].update({"fixture_sha256": "missing"}),
        lambda receipt: receipt.update({"final_candidate_tree_sha256": _digest("d")}),
        lambda receipt: receipt["token_usage"].update({"output_tokens": "two"}),
        lambda receipt: receipt["timings"].pop("review_wall_seconds"),
        lambda receipt: receipt["human_events"].append(
            {"type": "approval_service_event", "seconds": 1}
        ),
        lambda receipt: receipt["timings"].update({"end_to_end_wall_seconds": 20}),
    ],
)
def test_receipt_verification_rejects_incomplete_or_misclassified_evidence(
    tmp_path: Path, mutate,
) -> None:
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    mutate(receipt)
    assert verify_receipt(receipt, protocol, ledger)


def test_a11_receipt_requires_complete_expert_callback_evidence_before_close(
    tmp_path: Path,
) -> None:
    protocol, ledger, receipt = _task12_completed_a11_run(tmp_path)
    receipt["loop"]["expert_callbacks"] = []
    assert any(
        issue.code.startswith("receipt.a11")
        for issue in verify_receipt(receipt, protocol, ledger)
    )


def test_receipt_verification_applies_the_frozen_closed_json_schema(
    tmp_path: Path,
) -> None:
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    receipt["unregistered_field"] = "must fail closed"
    assert any(
        issue.code == "receipt.schema"
        for issue in verify_receipt(receipt, protocol, ledger)
    )


def test_summary_verification_keeps_summary_and_receipt_boundaries_closed(
    tmp_path: Path,
) -> None:
    protocol = _bound_protocol(tmp_path)
    summary = _task12_summary(protocol)
    assert not verify_summary(summary, protocol)
    summary["raw_provider_jsonl"] = "/private/provider.jsonl"
    assert verify_summary(summary, protocol)


def _task12_provider_attempt(attempt: dict[str, object]) -> dict[str, object]:
    return {
        "attempt_id": attempt["attempt_id"],
        "kind": attempt["kind"],
        "effective_kind": attempt["effective_kind"],
        "status": attempt["status"],
        "content_produced": attempt["content_produced"],
        "terminal": attempt["terminal"],
        "child_session": attempt["child_session"],
        "token_usage": copy.deepcopy(attempt["token_usage"]),
        "raw_provider_output_sha256": attempt["raw_provider_output_sha256"],
    }


def _task12_evidence_id(evidence: dict[str, object]) -> str:
    bound = {
        key: evidence[key]
        for key in (
            "attempt_id",
            "argv",
            "exit_code",
            "stdout_sha256",
            "stderr_sha256",
            "raw_provider_output_sha256",
        )
    }
    return sha256(
        json.dumps(
            bound, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _task12_command_evidence(
    attempt: dict[str, object], protocol
) -> dict[str, object]:
    sandbox = (
        "workspace-write"
        if attempt["effective_kind"] == "writer"
        else "read-only"
    )
    evidence = {
        "attempt_id": attempt["attempt_id"],
        "argv": [
            "codex",
            "exec",
            "--ephemeral",
            "--json",
            "--ignore-user-config",
            "--strict-config",
            "--model",
            protocol.execution_lock.model,
            "-c",
            f'model_reasoning_effort="{protocol.execution_lock.reasoning_effort}"',
            "--sandbox",
            sandbox,
            "-C",
            "benchmark-task/",
        ],
        "exit_code": 0 if attempt["status"] == "completed" else 1,
        "stdout_sha256": _digest("7"),
        "stderr_sha256": _digest("8"),
        "raw_provider_output_sha256": attempt["raw_provider_output_sha256"],
    }
    evidence["evidence_id"] = _task12_evidence_id(evidence)
    return evidence


def _task12_timestamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _task12_phase_evidence(started_at: datetime) -> dict[str, object]:
    durations = {
        "setup": 1,
        "framework_init": 2,
        "provider": 3,
        "post_provider": 4,
        "review": 5,
        "evaluation": 6,
    }
    cursor = started_at
    result = {}
    for phase, seconds in durations.items():
        ended_at = cursor + timedelta(seconds=seconds)
        payload = {
            "phase": phase,
            "started_at": _task12_timestamp(cursor),
            "ended_at": _task12_timestamp(ended_at),
        }
        result[phase] = {
            "started_at": payload["started_at"],
            "ended_at": payload["ended_at"],
            "evidence_sha256": sha256(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        }
        cursor = ended_at
    return result


def _task12_receipt(
    protocol,
    ledger: Path,
    *,
    run_id: str = "P:requirement-contract-ambiguity",
    seal: bool = True,
) -> dict[str, object]:
    run = next(row for row in protocol.run_matrix if row.run_id == run_id)
    raw_ledger = json.loads(ledger.read_text(encoding="utf-8"))
    persisted_attempts = [
        item for item in raw_ledger["attempts"] if item["run_id"] == run_id
    ]
    attempts = [_task12_provider_attempt(attempt) for attempt in persisted_attempts]
    token_usage = {
        key: sum(attempt["token_usage"][key] for attempt in attempts)
        for key in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
        )
    }
    candidate = next(
        (
            attempt["candidate_digest"]
            for attempt in reversed(persisted_attempts)
            if attempt.get("candidate_digest") is not None
        ),
        _digest("b"),
    )
    writer = next(
        (
            attempt
            for attempt in reversed(persisted_attempts)
            if attempt["effective_kind"] == "writer"
            and attempt["status"] != "technical_failure"
        ),
        next(
            attempt
            for attempt in reversed(persisted_attempts)
            if attempt["effective_kind"] == "writer"
        ),
    )
    receipt_status = {
        "completed": "completed",
        "technical_failure": "failed",
        "failed": "failed",
        "timeout": "timeout",
        "needs_operator": "needs_operator",
        "budget_exhausted": "budget_exhausted",
    }[writer["status"]]
    failure_classification = {
        "completed": "none",
        "timeout": "timeout",
        "needs_operator": "expert_conflict",
        "budget_exhausted": "provider_budget_exhausted",
    }.get(receipt_status)
    if failure_classification is None:
        if any(
            attempt["effective_kind"]
            in {"primary_expert", "cross_risk_expert", "expert_rereview"}
            and attempt["status"]
            in {"failed", "timeout", "needs_operator", "budget_exhausted"}
            for attempt in persisted_attempts
        ):
            failure_classification = "expert_failure"
        elif writer["status"] == "technical_failure":
            failure_classification = "provider_pre_output_failure"
        else:
            failure_classification = "writer_failure"
    workspace = ledger.parent / "workspace"
    if seal:
        for relative, payload in (
            ("benchmark-task/.evidence/setup.json", b"a"),
            ("benchmark-task/.evidence/governance.json", b"bb"),
            ("benchmark-task/result.txt", b"ccc"),
            ("baseline/result.txt", b"old"),
        ):
            target = workspace.joinpath(*relative.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        seal_run_evidence(
            ledger,
            protocol,
            ledger.parent / "evidence-contract.json",
            run_id=run_id,
            workspace_root=workspace,
        )
    raw_ledger = json.loads(ledger.read_text(encoding="utf-8"))
    sealed = raw_ledger["runs"][run_id]["sealed_evidence"]
    phases = sealed["phase_evidence"]
    started_at = datetime.fromisoformat(
        phases["setup"]["started_at"].replace("Z", "+00:00")
    )
    evaluator_completed_at = datetime.fromisoformat(
        phases["evaluation"]["ended_at"].replace("Z", "+00:00")
    )
    first_reserved_at = datetime.fromisoformat(
        persisted_attempts[0]["history"][0]["recorded_at"].replace("Z", "+00:00")
    )
    phase_seconds = {
        name: (
            datetime.fromisoformat(phase["ended_at"].replace("Z", "+00:00"))
            - datetime.fromisoformat(phase["started_at"].replace("Z", "+00:00"))
        ).total_seconds()
        for name, phase in phases.items()
    }
    end_to_end_seconds = (evaluator_completed_at - started_at).total_seconds()
    verified_seconds = (evaluator_completed_at - first_reserved_at).total_seconds()
    receipt = {
        "schema": "ai-sdlc-v2-benefit-run-receipt/v5",
        "protocol_sha256": canonical_protocol_digest(protocol),
        "run_id": run.run_id,
        "arm": run.arm,
        "fixture": run.fixture,
        "order": run.position,
        "status": receipt_status,
        "failure_classification": failure_classification,
        "identity": {
            field.name: getattr(protocol.execution_lock, field.name)
            for field in fields(ExecutionLock)
        },
        "digests": {
            "fixture_sha256": _digest("f"),
            "public_input_sha256": _digest("1"),
            "sealed_commitment_sha256": _digest("2"),
            "arm_config_sha256": _digest("3"),
            "provider_cwd_tree_sha256": _digest("4"),
            "instruction_chain_sha256": _digest("5"),
            "candidate_tree_sha256": candidate,
            "evaluator_result_sha256": _digest("6"),
        },
        "timestamps": {
            "started_at": _task12_timestamp(started_at),
            "ended_at": _task12_timestamp(evaluator_completed_at),
        },
        "provider_cwd": "benchmark-task/",
        "provider_attempts": attempts,
        "timings": {
            "end_to_end_wall_seconds": end_to_end_seconds,
            "verified_delivery_wall_seconds": verified_seconds,
            "setup_wall_seconds": phase_seconds["setup"],
            "framework_init_wall_seconds": phase_seconds["framework_init"],
            "provider_wall_seconds": phase_seconds["provider"],
            "governance_wall_seconds": phase_seconds["post_provider"],
            "review_wall_seconds": phase_seconds["review"],
            "evaluation_wall_seconds": phase_seconds["evaluation"],
        },
        "phase_evidence": copy.deepcopy(phases),
        "token_usage": token_usage,
        "measurements": {
            "provider_attempt_count": len(attempts),
            "clarification_request_count": 0,
            "intent_service_event_count": 0,
            "approval_service_event_count": 0,
            "intent_approval_service_latency_ms": 0,
            "human_event_count": 0,
            "human_active_seconds": 0,
            "human_efficiency_not_measured": True,
            "needs_operator": receipt_status == "needs_operator",
            "evidence_completeness": 1.0,
            "setup_artifact_bytes": 1,
            "governance_artifact_bytes": 2,
            "total_artifact_bytes": 6,
        },
        "artifact_inventory": copy.deepcopy(sealed["artifact_inventory"]),
        "human_events": copy.deepcopy(sealed["human_events"]),
        "automated_events": copy.deepcopy(sealed["automated_events"]),
        "command_evidence": [
            _task12_command_evidence(attempt, protocol) for attempt in attempts
        ],
        "changed_files": copy.deepcopy(sealed["changed_files"]),
        "changed_scope_tree_digests": copy.deepcopy(
            sealed["changed_scope_tree_digests"]
        ),
        "final_candidate_tree_sha256": candidate,
        "loop": _task12_default_loop(run, receipt_status, writer),
        "external_evaluator": {
            "completed_at": _task12_timestamp(evaluator_completed_at),
            "candidate_tree_sha256": candidate,
            "result_sha256": _digest("6"),
            "external_verified_delivery": receipt_status == "completed",
            "weighted_ac_coverage": 1.0,
            "severe_defect_escape_count": 0,
            "invalid_completion": False,
        },
    }
    return receipt


def _task12_default_loop(run, status: str, writer: dict[str, object]) -> dict[str, object]:
    arm = run.arm
    if arm in {"P", "S", "A00"}:
        state = "not_applicable"
    elif status == "completed":
        loop_type = (
            "design-contract"
            if run.fixture == "requirement-contract-ambiguity"
            else "implementation"
        )
        loop_id = f"benefit-{arm.lower()}-{run.fixture}"
        return {
            "close": {
                "state": "closed",
                "argv": [
                    "ai-sdlc",
                    "loop",
                    loop_type,
                    "close",
                    "--loop-id",
                    loop_id,
                    "--expect-review-digest",
                    writer["candidate_digest"],
                    "--yes",
                    "--json",
                ],
                "exit_code": 0,
                "review_digest": writer["candidate_digest"],
                "close_digest": writer["close_digest"],
            },
            "expert_callbacks": [],
        }
    else:
        state = "open"
    return {
        "close": {
            "state": state,
            "argv": None,
            "exit_code": None,
            "review_digest": None,
            "close_digest": None,
        },
        "expert_callbacks": [],
    }


def _task12_loop_identity(
    run_id: str,
) -> tuple[str, str]:
    arm, fixture = run_id.split(":", 1)
    loop_type = (
        "design-contract"
        if fixture == "requirement-contract-ambiguity"
        else "implementation"
    )
    return loop_type, f"benefit-{arm.lower()}-{fixture}"


def _task12_review_argv(run_id: str, expected_digest: str) -> list[str]:
    loop_type, loop_id = _task12_loop_identity(run_id)
    return [
        "ai-sdlc",
        "loop",
        "review",
        "--type",
        loop_type,
        "--loop-id",
        loop_id,
        "--expect-digest",
        expected_digest,
        "--read-path",
        f".ai-sdlc/loops/{loop_type}/{loop_id}/{loop_type}-input.json",
        "--json",
    ]


def _task12_close_argv(run_id: str, review_digest: str) -> list[str]:
    loop_type, loop_id = _task12_loop_identity(run_id)
    return [
        "ai-sdlc",
        "loop",
        loop_type,
        "close",
        "--loop-id",
        loop_id,
        "--expect-review-digest",
        review_digest,
        "--yes",
        "--json",
    ]


def _task12_completed_p_run(tmp_path: Path):
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    writer = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest("P:requirement-contract-ambiguity", "writer"),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id, "completed", True, candidate_digest=_digest("b")
        ),
    )
    return protocol, ledger, _task12_receipt(protocol, ledger)


def _task12_completed_a11_run(tmp_path: Path):
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    writer, expert = _writer_at_review_with_expert(ledger, protocol)
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            expert.attempt_id, "completed", True, finding_digest=_digest("d")
        ),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "candidate_ready",
            True,
            candidate_digest=_digest("c"),
            finding_digest=_digest("d"),
            repair_digest=_digest("e"),
        ),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "review_pending",
            True,
            candidate_digest=_digest("c"),
        ),
    )
    rereview = reserve_provider_attempt(
        ledger, protocol, _rereview_request(expert.attempt_id, _digest("c"))
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(rereview.attempt_id, "completed", True),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "completed",
            True,
            candidate_digest=_digest("c"),
            close_digest=_digest("9"),
        ),
    )
    receipt = _task12_receipt(
        protocol, ledger, run_id="A11:requirement-contract-ambiguity"
    )
    attempts_by_id = {
        attempt["attempt_id"]: attempt for attempt in receipt["provider_attempts"]
    }
    run_id = "A11:requirement-contract-ambiguity"
    receipt["loop"] = {
        "close": {
            "state": "closed",
            "argv": _task12_close_argv(run_id, _digest("c")),
            "exit_code": 0,
            "review_digest": _digest("c"),
            "close_digest": _digest("9"),
        },
        "expert_callbacks": [
            {
                "role": "primary",
                "reason": "design-contract risk",
                "status": "pass",
                "expert_attempt_id": expert.attempt_id,
                "expert_attempt_status": "completed",
                "parent_digest": _digest("a"),
                "candidate_digest": _digest("b"),
                "child_session": attempts_by_id[expert.attempt_id]["child_session"],
                "token_usage": copy.deepcopy(
                    attempts_by_id[expert.attempt_id]["token_usage"]
                ),
                "finding_count": 1,
                "severe_finding_count": 1,
                "finding_digest": _digest("d"),
                "repair_digest": _digest("e"),
                "repaired_candidate_digest": _digest("c"),
                "review_argv": _task12_review_argv(run_id, _digest("a")),
                "review_exit_code": 0,
                "snapshot_sha256": _digest("1"),
                "input_sha256": _digest("2"),
                "raw_output_sha256": attempts_by_id[expert.attempt_id][
                    "raw_provider_output_sha256"
                ],
                "parent_tree_before_sha256": _digest("4"),
                "parent_tree_after_sha256": _digest("4"),
                "rereviews": [
                    {
                        "attempt_id": rereview.attempt_id,
                        "status": "completed",
                        "child_session": attempts_by_id[rereview.attempt_id][
                            "child_session"
                        ],
                        "token_usage": copy.deepcopy(
                            attempts_by_id[rereview.attempt_id]["token_usage"]
                        ),
                        "raw_output_sha256": attempts_by_id[rereview.attempt_id][
                            "raw_provider_output_sha256"
                        ],
                        "finding_digest": _digest("d"),
                        "repair_digest": _digest("e"),
                        "candidate_digest": _digest("c"),
                        "argv": _task12_review_argv(run_id, _digest("c")),
                        "exit_code": 0,
                        "snapshot_sha256": _digest("1"),
                        "input_sha256": _digest("2"),
                        "parent_tree_before_sha256": _digest("4"),
                        "parent_tree_after_sha256": _digest("4"),
                    }
                ],
            }
        ],
    }
    return protocol, ledger, receipt


@pytest.mark.parametrize(
    "mutation",
    [
        lambda receipt: receipt.update({"unknown": True}),
        lambda receipt: receipt["digests"].update(
            {"instruction_chain_sha256": "bad"}
        ),
        lambda receipt: receipt.update({"run_id": "S:requirement-contract-ambiguity"}),
    ],
)
def test_task12_receipt_fails_closed_before_ledger_binding(
    tmp_path: Path, mutation
) -> None:
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    mutation(receipt)
    assert verify_receipt(receipt, protocol, ledger)


@pytest.mark.parametrize("ledger_case", ["missing", "corrupt"])
def test_task12_receipt_requires_a_valid_real_ledger(
    tmp_path: Path, ledger_case: str
) -> None:
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    if ledger_case == "missing":
        ledger.unlink()
    else:
        ledger.write_text("{}", encoding="utf-8")
    assert any(
        issue.code == "receipt.ledger"
        for issue in verify_receipt(receipt, protocol, ledger)
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda receipt: receipt["provider_attempts"].clear(),
        lambda receipt: receipt["provider_attempts"].append(
            copy.deepcopy(receipt["provider_attempts"][0])
        ),
        lambda receipt: receipt["provider_attempts"][0].update(
            {"attempt_id": "attempt-999"}
        ),
        lambda receipt: receipt["provider_attempts"][0].update(
            {"status": "failed"}
        ),
        lambda receipt: receipt["provider_attempts"][0].update(
            {"content_produced": False}
        ),
        lambda receipt: receipt["provider_attempts"][0].update(
            {"child_session": ""}
        ),
        lambda receipt: receipt["token_usage"].update({"output_tokens": 999}),
        lambda receipt: receipt["measurements"].update(
            {"provider_attempt_count": 99}
        ),
        lambda receipt: receipt["measurements"].update({"human_event_count": 1}),
        lambda receipt: receipt["measurements"].update({"total_artifact_bytes": 2}),
        lambda receipt: receipt["external_evaluator"].update(
            {"invalid_completion": True}
        ),
    ],
)
def test_task12_receipt_attempts_close_exactly_over_ledger_and_cost(
    tmp_path: Path, mutation
) -> None:
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    mutation(receipt)
    assert verify_receipt(receipt, protocol, ledger)


def test_task12_receipt_accepts_a_successful_writer_technical_retry(
    tmp_path: Path,
) -> None:
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    writer = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest("P:requirement-contract-ambiguity", "writer"),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(writer.attempt_id, "technical_failure", False),
    )
    retry = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest(
            "P:requirement-contract-ambiguity",
            "technical_retry",
            retry_reason="transport",
            retry_of_attempt_id=writer.attempt_id,
        ),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            retry.attempt_id, "completed", True, candidate_digest=_digest("b")
        ),
    )
    receipt = _task12_receipt(protocol, ledger)
    assert not verify_receipt(receipt, protocol, ledger)


def test_task12_receipt_binds_evaluator_result_digest(tmp_path: Path) -> None:
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    receipt["external_evaluator"]["result_sha256"] = _digest("9")
    assert verify_receipt(receipt, protocol, ledger)


def test_task12_receipt_rejects_a_cross_run_attempt(tmp_path: Path) -> None:
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    other = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest("S:requirement-contract-ambiguity", "writer"),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(other.attempt_id, "failed", False),
    )
    raw = json.loads(ledger.read_text(encoding="utf-8"))
    receipt["provider_attempts"].append(
        _task12_provider_attempt(raw["attempts"][-1])
    )
    assert verify_receipt(receipt, protocol, ledger)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda receipt: receipt["loop"]["expert_callbacks"].clear(),
        lambda receipt: receipt["loop"]["expert_callbacks"][0].update(
            {"child_session": ""}
        ),
        lambda receipt: receipt["loop"]["expert_callbacks"][0].update(
            {"child_session": "placeholder"}
        ),
        lambda receipt: receipt["loop"]["expert_callbacks"][0].update(
            {"reason": "TBD"}
        ),
        lambda receipt: receipt["loop"]["expert_callbacks"][0].pop("review_argv"),
        lambda receipt: receipt["loop"]["expert_callbacks"][0].update(
            {"review_exit_code": 1}
        ),
        lambda receipt: receipt["loop"]["expert_callbacks"][0].pop(
            "snapshot_sha256"
        ),
        lambda receipt: receipt["loop"]["expert_callbacks"][0].pop("input_sha256"),
        lambda receipt: receipt["loop"]["expert_callbacks"][0].pop(
            "raw_output_sha256"
        ),
        lambda receipt: receipt["loop"]["expert_callbacks"][0].update(
            {"raw_output_sha256": _digest("0")}
        ),
        lambda receipt: receipt["loop"]["expert_callbacks"][0].update(
            {"parent_tree_after_sha256": _digest("5")}
        ),
        lambda receipt: receipt["loop"]["expert_callbacks"][0]["rereviews"][
            0
        ].update({"attempt_id": "attempt-999"}),
        lambda receipt: receipt["loop"]["expert_callbacks"][0]["rereviews"][
            0
        ].pop("raw_output_sha256"),
        lambda receipt: receipt["loop"]["close"].update({"exit_code": 1}),
        lambda receipt: receipt["loop"]["close"].update(
            {"close_digest": _digest("8")}
        ),
    ],
)
def test_task12_a11_completed_requires_real_ordered_review_evidence(
    tmp_path: Path, mutation
) -> None:
    protocol, ledger, receipt = _task12_completed_a11_run(tmp_path)
    mutation(receipt)
    assert verify_receipt(receipt, protocol, ledger)


def test_task12_a11_no_finding_pass_closes_without_fake_repair(
    tmp_path: Path,
) -> None:
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    writer, expert = _writer_at_review_with_expert(ledger, protocol)
    record_provider_completion(
        ledger,
        protocol,
        _completion(expert.attempt_id, "completed", True),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "completed",
            True,
            candidate_digest=_digest("b"),
            close_digest=_digest("9"),
        ),
    )
    receipt = _task12_receipt(
        protocol, ledger, run_id="A11:requirement-contract-ambiguity"
    )
    attempts_by_id = {
        attempt["attempt_id"]: attempt for attempt in receipt["provider_attempts"]
    }
    run_id = "A11:requirement-contract-ambiguity"
    receipt["loop"] = {
        "close": {
            "state": "closed",
            "argv": _task12_close_argv(run_id, _digest("b")),
            "exit_code": 0,
            "review_digest": _digest("b"),
            "close_digest": _digest("9"),
        },
        "expert_callbacks": [
            _task12_partial_callback(
                {
                    **attempts_by_id[expert.attempt_id],
                    "role": "primary",
                    "parent_digest": _digest("a"),
                    "candidate_digest": _digest("b"),
                },
                loop_id="benefit-a11-requirement-contract-ambiguity",
                loop_type="design-contract",
            )
        ],
    }
    assert not verify_receipt(receipt, protocol, ledger)


def _task12_summary(protocol) -> dict[str, object]:
    return {
        "schema": "ai-sdlc-v2-benefit-summary/v3",
        "protocol_sha256": canonical_protocol_digest(protocol),
        "runs": [
            {
                "run_id": run.run_id,
                "arm": run.arm,
                "fixture": run.fixture,
                "position": run.position,
                "order": run.position,
                "receipt_sha256": _digest(f"{index:x}"[-1]),
            }
            for index, run in enumerate(protocol.run_matrix, start=1)
        ],
        "metrics": {
            "external_verified_delivery_count": {
                "arms": {"P": 1, "S": 2, "A11": 3},
                "n_per_arm": 3,
                "direction": "higher_is_better",
                "signed_deltas": {"S_minus_P": 1, "A11_minus_P": 2},
            },
            "median_weighted_ac_coverage": {
                "arms": {"A00": 0.5, "A10": 0.75},
                "n_per_arm": 3,
                "direction": "higher_is_better",
                "signed_delta": {
                    "comparison": "A10_minus_A00",
                    "percentage_points": 25.0,
                },
            },
            "sum_severe_defect_escape_count": {
                "arms": {"A10": 4, "A11": 1},
                "n_per_arm": 3,
                "direction": "lower_is_better",
                "signed_delta": {
                    "comparison": "A11_minus_A10",
                    "value": -3,
                },
            },
        },
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda summary: summary.update({"metrics": {}}),
        lambda summary: summary["metrics"].pop("median_weighted_ac_coverage"),
        lambda summary: summary["metrics"][
            "external_verified_delivery_count"
        ].update({"n_per_arm": 15}),
        lambda summary: summary["metrics"][
            "median_weighted_ac_coverage"
        ]["arms"].update({"A10": 2}),
        lambda summary: summary["metrics"][
            "median_weighted_ac_coverage"
        ]["signed_delta"].update({"percentage_points": -25}),
        lambda summary: summary["metrics"][
            "sum_severe_defect_escape_count"
        ].update({"direction": "higher_is_better"}),
        lambda summary: summary["runs"].__setitem__(
            1, copy.deepcopy(summary["runs"][0])
        ),
        lambda summary: summary["runs"][1].update(
            {"receipt_sha256": summary["runs"][0]["receipt_sha256"]}
        ),
    ],
)
def test_task12_summary_requires_all_preregistered_metrics_and_exact_parity(
    tmp_path: Path, mutation
) -> None:
    protocol = _bound_protocol(tmp_path)
    summary = _task12_summary(protocol)
    mutation(summary)
    assert verify_summary(summary, protocol)


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "string", "pattern": 7},
        {"type": "string", "pattern": "["},
        {"type": "number", "minimum": True},
        {"type": "number", "minimum": float("nan")},
        {"type": "number", "const": float("inf")},
        {"type": "number", "enum": [1, float("-inf")]},
        {"type": "array", "items": {}},
    ],
)
def test_task12_provider_schema_rejects_invalid_operands(schema) -> None:
    assert validate_provider_output_schema(schema)


@pytest.mark.parametrize(
    "value",
    [
        "/Users/private/result.json",
        "C:\\Users\\private\\result.json",
        "\\\\server\\share\\result.json",
        "FiLe:///Users/private/result.json",
        "artifact at /mnt/benchmark-private/result.json",
        "curl -H 'Authorization: Bearer token-value' https://example.test",
        "OPENAI_API_KEY=actual-secret",
        "GH_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz123456",
    ],
)
def test_task12_public_evidence_rejects_paths_and_secrets(
    tmp_path: Path, value: str
) -> None:
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    receipt["changed_files"] = [value]
    assert verify_receipt(receipt, protocol, ledger)


@pytest.mark.parametrize(
    "value",
    [
        "OPENAI_API_KEY=REDACTED",
        "Authorization: REDACTED",
        "token=REDACTED",
    ],
)
def test_task12_public_evidence_allows_explicit_redaction(
    tmp_path: Path, value: str
) -> None:
    protocol, ledger, receipt = _task12_completed_a11_run(tmp_path)
    receipt["loop"]["expert_callbacks"][0]["reason"] = value
    assert not verify_receipt(receipt, protocol, ledger)


def test_task12_verify_receipt_cli_uses_the_required_ledger(tmp_path: Path) -> None:
    protocol_path = _bound_protocol_path(tmp_path)
    protocol = load_protocol(protocol_path)
    ledger = tmp_path / "ledger.json"
    writer = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest("P:requirement-contract-ambiguity", "writer"),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id, "completed", True, candidate_digest=_digest("b")
        ),
    )
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(
        json.dumps(_task12_receipt(protocol, ledger)), encoding="utf-8"
    )
    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "scripts/ai_sdlc_v2_benefit_benchmark.py",
            "verify-receipt",
            "--protocol",
            str(protocol_path),
            "--ledger",
            str(ledger),
            "--receipt",
            str(receipt_path),
            "--contract",
            str(tmp_path / "evidence-contract.json"),
            "--workspace-root",
            str(tmp_path / "workspace"),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"issues": []}


@pytest.mark.parametrize("case", ["fabricated_session", "all_tokens_zero"])
def test_fix_round_one_receipt_binds_completion_evidence_to_ledger(
    tmp_path: Path, case: str
) -> None:
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    attempt = receipt["provider_attempts"][0]
    if case == "fabricated_session":
        attempt["child_session"] = "fabricated-session"
    else:
        attempt["token_usage"] = {
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_output_tokens": 0,
        }
        receipt["token_usage"] = copy.deepcopy(attempt["token_usage"])
    assert verify_receipt(receipt, protocol, ledger)


def test_fix_round_one_p_arm_rejects_fake_close_and_expert_callback(
    tmp_path: Path,
) -> None:
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    a11_root = tmp_path / "a11"
    a11_root.mkdir()
    _, _, a11 = _task12_completed_a11_run(a11_root)
    receipt["loop"] = copy.deepcopy(a11["loop"])
    assert verify_receipt(receipt, protocol, ledger)


def test_fix_round_one_a11_needs_operator_requires_open_real_conflict(
    tmp_path: Path,
) -> None:
    protocol, ledger, receipt = _task12_completed_a11_run(tmp_path)
    receipt["status"] = "needs_operator"
    receipt["measurements"]["needs_operator"] = True
    assert verify_receipt(receipt, protocol, ledger)


def test_fix_round_one_accepts_a11_real_conflict_open_without_close(
    tmp_path: Path,
) -> None:
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    run_id = "A11:multi-tenant-security-review"
    writer = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest(run_id, "writer", parent_digest=_digest("a")),
    )
    for status in ("candidate_ready", "review_pending"):
        record_provider_completion(
            ledger,
            protocol,
            _completion(
                writer.attempt_id, status, True, candidate_digest=_digest("b")
            ),
        )
    experts = []
    for kind, role, finding in (
        ("primary_expert", "primary", _digest("d")),
        ("cross_risk_expert", "cross-risk", _digest("e")),
    ):
        expert = reserve_provider_attempt(
            ledger,
            protocol,
            AttemptRequest(
                run_id,
                kind,
                parent_attempt_id=writer.attempt_id,
                role=role,
                parent_digest=_digest("a"),
                candidate_digest=_digest("b"),
            ),
        )
        record_provider_completion(
            ledger,
            protocol,
            _completion(
                expert.attempt_id,
                "completed",
                True,
                finding_digest=finding,
            ),
        )
        experts.append((expert, role, finding))
    record_provider_completion(
        ledger,
        protocol,
        _completion(writer.attempt_id, "needs_operator", True),
    )
    receipt = _task12_receipt(protocol, ledger, run_id=run_id)
    attempts_by_id = {
        attempt["attempt_id"]: attempt for attempt in receipt["provider_attempts"]
    }
    receipt["loop"]["expert_callbacks"] = []
    for expert, role, finding in experts:
        callback = _task12_partial_callback(
            {
                **attempts_by_id[expert.attempt_id],
                "role": role,
                "parent_digest": _digest("a"),
                "candidate_digest": _digest("b"),
                "finding_digest": finding,
            },
            loop_id="benefit-a11-multi-tenant-security-review",
            loop_type="implementation",
        )
        callback.update(
            {
                "reason": f"{role} produced an incompatible repair boundary",
                "status": "conflict",
                "finding_count": 1,
                "severe_finding_count": 1,
                "finding_digest": finding,
            }
        )
        receipt["loop"]["expert_callbacks"].append(callback)

    assert not verify_receipt(receipt, protocol, ledger)


@pytest.mark.parametrize("arm", ["P", "S", "A00"])
def test_fix_round_one_non_loop_arms_require_null_not_applicable_evidence(
    tmp_path: Path, arm: str
) -> None:
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    run_id = f"{arm}:requirement-contract-ambiguity"
    writer = reserve_provider_attempt(
        ledger, protocol, AttemptRequest(run_id, "writer")
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id, "completed", True, candidate_digest=_digest("b")
        ),
    )
    assert not verify_receipt(
        _task12_receipt(protocol, ledger, run_id=run_id), protocol, ledger
    )


def test_fix_round_one_a10_closes_without_expert_callbacks(tmp_path: Path) -> None:
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    run_id = "A10:requirement-contract-ambiguity"
    writer = reserve_provider_attempt(
        ledger, protocol, AttemptRequest(run_id, "writer")
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "completed",
            True,
            candidate_digest=_digest("b"),
            close_digest=_digest("c"),
        ),
    )
    receipt = _task12_receipt(protocol, ledger, run_id=run_id)
    assert not verify_receipt(receipt, protocol, ledger)
    a11_root = tmp_path / "a11"
    a11_root.mkdir()
    receipt["loop"]["expert_callbacks"].append(
        copy.deepcopy(
            _task12_completed_a11_run(a11_root)[2]["loop"]["expert_callbacks"][0]
        )
    )
    assert verify_receipt(receipt, protocol, ledger)


@pytest.mark.parametrize("arm", ["P", "A10", "A11"])
def test_fix_round_one_pre_output_writer_failure_keeps_arm_loop_state_closed(
    tmp_path: Path, arm: str
) -> None:
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    run_id = f"{arm}:requirement-contract-ambiguity"
    writer = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest(
            run_id,
            "writer",
            parent_digest=_digest("a") if arm == "A11" else None,
        ),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(writer.attempt_id, "technical_failure", False),
    )
    receipt = _task12_receipt(protocol, ledger, run_id=run_id)
    assert receipt["status"] == "failed"
    assert receipt["loop"]["close"]["state"] == (
        "not_applicable" if arm == "P" else "open"
    )
    assert not verify_receipt(receipt, protocol, ledger)


@pytest.mark.parametrize("terminal_status", ["failed", "timeout"])
def test_fix_round_two_content_produced_is_monotonic_online(
    tmp_path: Path, terminal_status: str
) -> None:
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    writer = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest("P:requirement-contract-ambiguity", "writer"),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "candidate_ready",
            True,
            candidate_digest=_digest("b"),
        ),
    )

    with pytest.raises(ValueError, match="content"):
        record_provider_completion(
            ledger,
            protocol,
            _completion(writer.attempt_id, terminal_status, False),
        )


def test_fix_round_two_content_produced_is_monotonic_on_reload_and_receipt(
    tmp_path: Path,
) -> None:
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    writer = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest("P:requirement-contract-ambiguity", "writer"),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "candidate_ready",
            True,
            candidate_digest=_digest("b"),
        ),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(writer.attempt_id, "failed", True),
    )
    receipt = _task12_receipt(protocol, ledger)
    raw = json.loads(ledger.read_text(encoding="utf-8"))
    forged = raw["attempts"][0]
    forged["content_produced"] = False
    forged["token_usage"] = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
    }
    forged["history"][-1]["content_produced"] = False
    forged["history"][-1]["token_usage"] = copy.deepcopy(forged["token_usage"])
    ledger.write_text(json.dumps(raw), encoding="utf-8")
    receipt["provider_attempts"][0]["content_produced"] = False
    receipt["provider_attempts"][0]["token_usage"] = copy.deepcopy(
        forged["token_usage"]
    )
    receipt["token_usage"] = copy.deepcopy(forged["token_usage"])

    assert any(
        issue.code == "receipt.ledger"
        for issue in verify_receipt(receipt, protocol, ledger)
    )
    with pytest.raises(ValueError, match="content"):
        reserve_provider_attempt(
            ledger,
            protocol,
            AttemptRequest("S:requirement-contract-ambiguity", "writer"),
        )


@pytest.mark.parametrize("case", ["echo_provider", "completed_exit_37"])
def test_fix_round_two_provider_command_must_bind_frozen_execution_contract(
    tmp_path: Path, case: str
) -> None:
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    evidence = receipt["command_evidence"][0]
    if case == "echo_provider":
        evidence["argv"] = ["echo", *evidence["argv"]]
    else:
        evidence["exit_code"] = 37
    evidence["evidence_id"] = _task12_evidence_id(evidence)
    assert verify_receipt(receipt, protocol, ledger)


@pytest.mark.parametrize(
    "case", ["wrong_parent_digest", "wrong_read_path", "wrong_loop_id", "wrong_close_id"]
)
def test_fix_round_two_loop_argv_values_bind_real_review_and_close_evidence(
    tmp_path: Path, case: str
) -> None:
    protocol, ledger, receipt = _task12_completed_a11_run(tmp_path)
    callback = receipt["loop"]["expert_callbacks"][0]
    target = callback["review_argv"]
    if case == "wrong_parent_digest":
        target[target.index("--expect-digest") + 1] = _digest("f")
    elif case == "wrong_read_path":
        target[target.index("--read-path") + 1] = "other-loop/result.json"
    elif case == "wrong_loop_id":
        target[target.index("--loop-id") + 1] = "foreign-loop"
    else:
        close_argv = receipt["loop"]["close"]["argv"]
        close_argv[close_argv.index("--loop-id") + 1] = "foreign-loop"
    assert verify_receipt(receipt, protocol, ledger)


def _task12_a11_partial_terminal_run(tmp_path: Path, status: str):
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    run_id = "A11:requirement-contract-ambiguity"
    writer, expert = _writer_at_review_with_expert(ledger, protocol)
    record_provider_completion(
        ledger,
        protocol,
        _completion(expert.attempt_id, "completed", True),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(writer.attempt_id, status, True),
    )
    receipt = _task12_receipt(protocol, ledger, run_id=run_id)
    persisted = json.loads(ledger.read_text(encoding="utf-8"))
    expert_state = next(
        attempt
        for attempt in persisted["attempts"]
        if attempt["attempt_id"] == expert.attempt_id
    )
    receipt["loop"]["expert_callbacks"] = [
        _task12_partial_callback(
            expert_state,
            loop_id="benefit-a11-requirement-contract-ambiguity",
            loop_type="design-contract",
        )
    ]
    return protocol, ledger, receipt


def _task12_partial_callback(
    expert: dict[str, object], *, loop_id: str, loop_type: str
) -> dict[str, object]:
    return {
        "role": expert["role"],
        "reason": "bounded review completed before writer terminal failure",
        "status": "pass",
        "expert_attempt_id": expert["attempt_id"],
        "expert_attempt_status": expert["status"],
        "parent_digest": expert["parent_digest"],
        "candidate_digest": expert["candidate_digest"],
        "child_session": expert["child_session"],
        "token_usage": copy.deepcopy(expert["token_usage"]),
        "finding_count": 0,
        "severe_finding_count": 0,
        "finding_digest": None,
        "repair_digest": None,
        "repaired_candidate_digest": None,
        "review_argv": [
            "ai-sdlc",
            "loop",
            "review",
            "--type",
            loop_type,
            "--loop-id",
            loop_id,
            "--expect-digest",
            expert["parent_digest"],
            "--read-path",
            f".ai-sdlc/loops/{loop_type}/{loop_id}/{loop_type}-input.json",
            "--json",
        ],
        "review_exit_code": 0,
        "snapshot_sha256": _digest("1"),
        "input_sha256": _digest("2"),
        "raw_output_sha256": expert["raw_provider_output_sha256"],
        "parent_tree_before_sha256": _digest("4"),
        "parent_tree_after_sha256": _digest("4"),
        "rereviews": [],
    }


@pytest.mark.parametrize("status", ["failed", "timeout", "budget_exhausted"])
def test_fix_round_two_a11_partial_failure_rejects_cross_run_callback(
    tmp_path: Path, status: str
) -> None:
    protocol, ledger, receipt = _task12_a11_partial_terminal_run(tmp_path, status)
    assert not verify_receipt(receipt, protocol, ledger)

    other_run = "A11:frontend-recovery-delivery"
    other_writer = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest(other_run, "writer", parent_digest=_digest("6")),
    )
    for checkpoint in ("candidate_ready", "review_pending"):
        record_provider_completion(
            ledger,
            protocol,
            _completion(
                other_writer.attempt_id,
                checkpoint,
                True,
                candidate_digest=_digest("7"),
            ),
        )
    other_expert = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest(
            other_run,
            "primary_expert",
            parent_attempt_id=other_writer.attempt_id,
            role="primary",
            parent_digest=_digest("6"),
            candidate_digest=_digest("7"),
        ),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(other_expert.attempt_id, "completed", True),
    )
    raw = json.loads(ledger.read_text(encoding="utf-8"))
    other_state = next(
        attempt
        for attempt in raw["attempts"]
        if attempt["attempt_id"] == other_expert.attempt_id
    )
    receipt["loop"]["expert_callbacks"].append(
        _task12_partial_callback(
            other_state,
            loop_id="benefit-a11-frontend-recovery-delivery",
            loop_type="implementation",
        )
    )
    assert verify_receipt(receipt, protocol, ledger)


def _task12_a11_terminal_after_completed_rereview(tmp_path: Path, status: str):
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    run_id = "A11:requirement-contract-ambiguity"
    writer, expert = _writer_at_review_with_expert(ledger, protocol)
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            expert.attempt_id,
            "completed",
            True,
            finding_digest=_digest("d"),
        ),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "candidate_ready",
            True,
            candidate_digest=_digest("c"),
            finding_digest=_digest("d"),
            repair_digest=_digest("e"),
        ),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "review_pending",
            True,
            candidate_digest=_digest("c"),
        ),
    )
    rereview = reserve_provider_attempt(
        ledger,
        protocol,
        _rereview_request(expert.attempt_id, _digest("c")),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(rereview.attempt_id, "completed", True),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(writer.attempt_id, status, True),
    )
    receipt = _task12_receipt(protocol, ledger, run_id=run_id)
    persisted = json.loads(ledger.read_text(encoding="utf-8"))
    attempts = {attempt["attempt_id"]: attempt for attempt in persisted["attempts"]}
    callback = _task12_partial_callback(
        attempts[expert.attempt_id],
        loop_id="benefit-a11-requirement-contract-ambiguity",
        loop_type="design-contract",
    )
    callback.update(
        {
            "finding_count": 1,
            "severe_finding_count": 1,
            "finding_digest": _digest("d"),
            "repair_digest": _digest("e"),
            "repaired_candidate_digest": _digest("c"),
            "rereviews": [
                {
                    "attempt_id": rereview.attempt_id,
                    "status": "completed",
                    "child_session": attempts[rereview.attempt_id]["child_session"],
                    "token_usage": copy.deepcopy(
                        attempts[rereview.attempt_id]["token_usage"]
                    ),
                    "raw_output_sha256": attempts[rereview.attempt_id][
                        "raw_provider_output_sha256"
                    ],
                    "finding_digest": _digest("d"),
                    "repair_digest": _digest("e"),
                    "candidate_digest": _digest("c"),
                    "argv": _task12_review_argv(run_id, _digest("c")),
                    "exit_code": 0,
                    "snapshot_sha256": _digest("1"),
                    "input_sha256": _digest("2"),
                    "parent_tree_before_sha256": _digest("4"),
                    "parent_tree_after_sha256": _digest("4"),
                }
            ],
        }
    )
    receipt["loop"]["expert_callbacks"] = [callback]
    return protocol, ledger, receipt


@pytest.mark.parametrize("status", ["failed", "timeout", "budget_exhausted"])
def test_fix_round_three_completed_rereview_cannot_be_omitted_from_terminal_receipt(
    tmp_path: Path, status: str
) -> None:
    protocol, ledger, receipt = _task12_a11_terminal_after_completed_rereview(
        tmp_path, status
    )
    assert not verify_receipt(receipt, protocol, ledger)

    callback = receipt["loop"]["expert_callbacks"][0]
    for key in ("repair_digest", "repaired_candidate_digest"):
        callback[key] = None
    callback["rereviews"] = []
    assert verify_receipt(receipt, protocol, ledger)


def test_fix_round_two_ledger_event_time_is_global_sequence_monotonic(
    tmp_path: Path,
) -> None:
    protocol, ledger, _receipt = _task12_completed_p_run(tmp_path)
    reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest("S:requirement-contract-ambiguity", "writer"),
    )
    raw = json.loads(ledger.read_text(encoding="utf-8"))
    rolled_back = "2000-01-01T00:00:00Z"
    raw["attempts"][1]["recorded_at"] = rolled_back
    raw["attempts"][1]["history"][0]["recorded_at"] = rolled_back
    ledger.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="time"):
        reserve_provider_attempt(
            ledger,
            protocol,
            AttemptRequest("A00:requirement-contract-ambiguity", "writer"),
        )


@pytest.mark.parametrize("case", ["evaluator_before_terminal", "start_after_reservation"])
def test_fix_round_two_receipt_time_causality_covers_full_ledger(
    tmp_path: Path, case: str
) -> None:
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    raw = json.loads(ledger.read_text(encoding="utf-8"))
    reserved = datetime.fromisoformat(
        raw["attempts"][0]["history"][0]["recorded_at"].replace("Z", "+00:00")
    )
    if case == "evaluator_before_terminal":
        ended = reserved
        started = ended - timedelta(seconds=21)
        receipt["timings"]["verified_delivery_wall_seconds"] = 0
    else:
        started = reserved + timedelta(seconds=1)
        ended = started + timedelta(seconds=21)
        receipt["timings"]["verified_delivery_wall_seconds"] = 22
    receipt["timestamps"] = {
        "started_at": _task12_timestamp(started),
        "ended_at": _task12_timestamp(ended),
    }
    receipt["external_evaluator"]["completed_at"] = _task12_timestamp(ended)
    assert verify_receipt(receipt, protocol, ledger)


@pytest.mark.parametrize("case", ["echo_loop", "zero_digest"])
def test_fix_round_one_command_evidence_is_structured_and_non_placeholder(
    tmp_path: Path, case: str
) -> None:
    protocol, ledger, receipt = _task12_completed_a11_run(tmp_path)
    if case == "echo_loop":
        receipt["loop"]["expert_callbacks"][0]["review_argv"] = [
            "echo",
            "ai-sdlc",
            "loop",
            "review",
            "--expect-digest",
            "x",
            "--read-path",
            "y",
        ]
    else:
        receipt["command_evidence"][0]["stdout_sha256"] = _digest("0")
    assert verify_receipt(receipt, protocol, ledger)


def test_fix_round_one_summary_rows_zip_every_canonical_field(tmp_path: Path) -> None:
    protocol = _bound_protocol(tmp_path)
    summary = _task12_summary(protocol)
    summary["runs"][0]["arm"], summary["runs"][1]["arm"] = (
        summary["runs"][1]["arm"],
        summary["runs"][0]["arm"],
    )
    assert verify_summary(summary, protocol)


@pytest.mark.parametrize("case", ["start_after_end", "verified_999999"])
def test_fix_round_one_timestamps_and_elapsed_are_reproducible(
    tmp_path: Path, case: str
) -> None:
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    if case == "start_after_end":
        receipt["timestamps"] = {
            "started_at": "2026-08-18T00:01:00Z",
            "ended_at": "2026-08-18T00:00:00Z",
        }
    else:
        receipt["timings"]["verified_delivery_wall_seconds"] = 999999
    assert verify_receipt(receipt, protocol, ledger)


@pytest.mark.parametrize(
    ("value", "expected_issue"),
    [
        ("https://example.test/private/result.json", False),
        ("mount0:/Users/private/result.json", True),
        ("OPENAI_API_KEY='REDACTED'", False),
        ('Authorization: "REDACTED"', False),
    ],
)
def test_fix_round_one_path_secret_boundaries(
    tmp_path: Path, value: str, expected_issue: bool
) -> None:
    protocol, ledger, receipt = _task12_completed_a11_run(tmp_path)
    receipt["loop"]["expert_callbacks"][0]["reason"] = value
    assert bool(verify_receipt(receipt, protocol, ledger)) is expected_issue


def test_static_schemas_and_offline_cli_validation_are_available() -> None:
    benchmark_root = PROTOCOL_PATH.parent
    for schema_name in ("run-receipt.schema.json", "summary.schema.json"):
        schema = json.loads(
            (benchmark_root / "schemas" / schema_name).read_text(encoding="utf-8")
        )
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["additionalProperties"] is False

    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "scripts/ai_sdlc_v2_benefit_benchmark.py",
            "validate",
            "--protocol",
            str(PROTOCOL_PATH),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["execution_ready"] is False
    assert payload["task2_commitment_bound"] is True
    assert payload["provider_authorized"] is False
    assert payload["experiment_authorized"] is False


def test_fix_round_protocol_freezes_exact_rows_and_execution_locks() -> None:
    protocol = load_protocol(PROTOCOL_PATH)
    assert protocol.fixtures == (
        "requirement-contract-ambiguity",
        "frontend-recovery-delivery",
        "multi-tenant-security-review",
    )
    assert protocol.run_matrix[0].run_id == "P:requirement-contract-ambiguity"
    assert protocol.run_matrix[-1].run_id == "A00:multi-tenant-security-review"
    assert protocol.execution_lock.model == "gpt-5.6-sol"
    assert protocol.execution_lock.writer_timeout_seconds == 1800
    assert len(canonical_protocol_digest(protocol)) == 64


def test_fix_round_two_rejects_pending_fixture_lock_before_reservation(
    tmp_path: Path,
) -> None:
    protocol = load_protocol(_pending_protocol_path(tmp_path))
    assert any(
        issue.code == "protocol.fixture-pending"
        for issue in validate_protocol(protocol, REPO_ROOT)
    )


def test_cross_process_reservations_are_unique_and_not_lost(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.json"
    protocol_path = _bound_protocol_path(tmp_path)
    queue: multiprocessing.Queue = multiprocessing.Queue()
    processes = [
        multiprocessing.Process(
            target=_reserve_in_process,
            args=(
                str(ledger),
                str(protocol_path),
                "P:requirement-contract-ambiguity",
                queue,
            ),
        ),
        multiprocessing.Process(
            target=_reserve_in_process,
            args=(
                str(ledger),
                str(protocol_path),
                "S:requirement-contract-ambiguity",
                queue,
            ),
        ),
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    assert {queue.get(timeout=1), queue.get(timeout=1)} == {
        "attempt-001",
        "attempt-002",
    }
    persisted = json.loads(ledger.read_text(encoding="utf-8"))
    assert persisted["attempts_started"] == 2
    assert len(persisted["attempts"]) == 2


def test_completion_and_reservation_share_one_cross_process_transaction_lock(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.json"
    protocol_path = _bound_protocol_path(tmp_path)
    protocol = load_protocol(protocol_path)
    writer = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest("P:requirement-contract-ambiguity", "writer"),
    )
    queue: multiprocessing.Queue = multiprocessing.Queue()
    processes = [
        multiprocessing.Process(
            target=_complete_in_process,
            args=(str(ledger), str(protocol_path), writer.attempt_id, queue),
        ),
        multiprocessing.Process(
            target=_reserve_in_process,
            args=(
                str(ledger),
                str(protocol_path),
                "S:requirement-contract-ambiguity",
                queue,
            ),
        ),
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0

    assert {queue.get(timeout=1), queue.get(timeout=1)} == {
        "completed",
        "attempt-002",
    }
    persisted = json.loads(ledger.read_text(encoding="utf-8"))
    assert persisted["attempts_started"] == 2
    assert persisted["attempts"][0]["status"] == "failed"
    assert persisted["attempts"][1]["attempt_id"] == "attempt-002"


def _run_task13_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    resolved = list(arguments)
    if (
        resolved
        and resolved[0]
        in {
            "start-run",
            "transition-phase",
            "reserve-attempt",
            "complete-attempt",
            "start-service-transaction",
            "complete-service-transaction",
            "seal-run-evidence",
        }
        and "--protocol" in resolved
        and "--authorization" not in resolved
    ):
        protocol_path = Path(resolved[resolved.index("--protocol") + 1])
        try:
            protocol = load_protocol(protocol_path)
        except (OSError, ValueError, json.JSONDecodeError):
            protocol = None
        if protocol is not None:
            authorization = protocol_path.parent / (
                f"execution-authorization-{canonical_protocol_digest(protocol)}.json"
            )
            resolved.extend(["--authorization", str(authorization)])
    if (
        resolved
        and resolved[0] == "verify-receipt"
        and "--protocol" in resolved
        and "--ledger" in resolved
        and "--contract" not in resolved
    ):
        protocol_path = Path(resolved[resolved.index("--protocol") + 1])
        ledger_path = Path(resolved[resolved.index("--ledger") + 1])
        resolved.extend(
            [
                "--contract",
                str(protocol_path.parent / "evidence-contract.json"),
                "--workspace-root",
                str(ledger_path.parent / "workspace"),
            ]
        )
    return subprocess.run(
        [
            "uv",
            "run",
            "python",
            "scripts/ai_sdlc_v2_benefit_benchmark.py",
            *resolved,
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _assert_task13_json_error(
    result: subprocess.CompletedProcess[str], expected_code: str
) -> None:
    assert result.returncode == 2
    assert result.stderr == ""
    assert "Traceback" not in result.stdout
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == expected_code
    assert set(payload["error"]) == {"code", "message"}


@pytest.mark.parametrize("command", ["reserve-attempt", "complete-attempt"])
def test_task13_attempt_commands_require_protocol_as_json_usage_error(
    tmp_path: Path, command: str
) -> None:
    ledger = tmp_path / "ledger.json"
    arguments = [command, "--ledger", str(ledger)]
    if command == "reserve-attempt":
        arguments.extend(
            [
                "--run-id",
                "P:requirement-contract-ambiguity",
                "--kind",
                "writer",
            ]
        )
    else:
        arguments.extend(
            ["--attempt-id", "attempt-001", "--status", "failed"]
        )

    result = _run_task13_cli(*arguments)

    _assert_task13_json_error(result, "cli.usage")
    assert not ledger.exists()


@pytest.mark.parametrize("command", ["reserve-attempt", "complete-attempt"])
def test_task13_pending_protocol_blocks_attempt_without_ledger_mutation(
    tmp_path: Path, command: str
) -> None:
    ledger = tmp_path / "ledger.json"
    original = b'{"sentinel":"unchanged"}'
    if command == "complete-attempt":
        ledger.write_bytes(original)
    arguments = [
        command,
        "--ledger",
        str(ledger),
        "--protocol",
        str(PROTOCOL_PATH),
        "--contract",
        str(tmp_path / "evidence-contract.json"),
    ]
    if command == "reserve-attempt":
        arguments.extend(
            [
                "--run-id",
                "P:requirement-contract-ambiguity",
                "--kind",
                "writer",
            ]
        )
    else:
        arguments.extend(
            ["--attempt-id", "attempt-001", "--status", "failed"]
        )

    result = _run_task13_cli(*arguments)

    _assert_task13_json_error(result, "cli.input")
    if command == "reserve-attempt":
        assert not ledger.exists()
    else:
        assert ledger.read_bytes() == original


def _task13_terminal_cli_arguments(attempt_id: str) -> list[str]:
    return [
        "--attempt-id",
        attempt_id,
        "--status",
        "completed",
        "--content-produced",
        "--child-session",
        f"session-{attempt_id}",
        "--input-tokens",
        "2",
        "--cached-input-tokens",
        "1",
        "--output-tokens",
        "3",
        "--reasoning-output-tokens",
        "1",
        "--raw-provider-output-sha256",
        _digest("9"),
    ]


def test_task13_cli_round_trips_writer_expert_retry_and_rereview_fields(
    tmp_path: Path,
) -> None:
    protocol = _bound_protocol_path(tmp_path)
    ledger = tmp_path / "ledger.json"

    def invoke(command: str, *arguments: str) -> dict[str, object]:
        result = _run_task13_cli(
            command,
            "--ledger",
            str(ledger),
            "--protocol",
            str(protocol),
            "--contract",
            str(tmp_path / "evidence-contract.json"),
            *arguments,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stderr == ""
        return json.loads(result.stdout)

    invoke("start-run", "--run-id", "A11:requirement-contract-ambiguity")
    invoke(
        "transition-phase",
        "--run-id",
        "A11:requirement-contract-ambiguity",
        "--next-phase",
        "framework_init",
    )
    invoke(
        "transition-phase",
        "--run-id",
        "A11:requirement-contract-ambiguity",
        "--next-phase",
        "provider",
    )
    writer = invoke(
        "reserve-attempt",
        "--run-id",
        "A11:requirement-contract-ambiguity",
        "--kind",
        "writer",
        "--arm",
        "A11",
        "--parent-digest",
        _digest("a"),
    )["attempt_id"]
    assert isinstance(writer, str)
    for status in ("candidate_ready", "review_pending"):
        invoke(
            "complete-attempt",
            "--attempt-id",
            writer,
            "--status",
            status,
            "--content-produced",
            "--candidate-digest",
            _digest("b"),
        )
    expert = invoke(
        "reserve-attempt",
        "--run-id",
        "A11:requirement-contract-ambiguity",
        "--kind",
        "primary_expert",
        "--arm",
        "A11",
        "--parent-attempt-id",
        writer,
        "--role",
        "primary",
        "--parent-digest",
        _digest("a"),
        "--candidate-digest",
        _digest("b"),
    )["attempt_id"]
    assert isinstance(expert, str)
    invoke(
        "complete-attempt",
        *_task13_terminal_cli_arguments(expert),
        "--finding-digest",
        _digest("d"),
    )
    invoke(
        "complete-attempt",
        "--attempt-id",
        writer,
        "--status",
        "candidate_ready",
        "--content-produced",
        "--candidate-digest",
        _digest("c"),
        "--finding-digest",
        _digest("d"),
        "--repair-digest",
        _digest("e"),
    )
    invoke(
        "complete-attempt",
        "--attempt-id",
        writer,
        "--status",
        "review_pending",
        "--content-produced",
        "--candidate-digest",
        _digest("c"),
    )
    rereview = invoke(
        "reserve-attempt",
        "--run-id",
        "A11:requirement-contract-ambiguity",
        "--kind",
        "expert_rereview",
        "--arm",
        "A11",
        "--parent-attempt-id",
        expert,
        "--role",
        "primary",
        "--parent-digest",
        _digest("a"),
        "--candidate-digest",
        _digest("c"),
        "--finding-digest",
        _digest("d"),
        "--repair-digest",
        _digest("e"),
    )["attempt_id"]
    assert isinstance(rereview, str)
    invoke("complete-attempt", *_task13_terminal_cli_arguments(rereview))
    invoke(
        "complete-attempt",
        *_task13_terminal_cli_arguments(writer),
        "--candidate-digest",
        _digest("c"),
        "--close-digest",
        _digest("f"),
    )

    invoke("start-run", "--run-id", "P:frontend-recovery-delivery")
    invoke(
        "transition-phase",
        "--run-id",
        "P:frontend-recovery-delivery",
        "--next-phase",
        "framework_init",
    )
    invoke(
        "transition-phase",
        "--run-id",
        "P:frontend-recovery-delivery",
        "--next-phase",
        "provider",
    )
    failed_writer = invoke(
        "reserve-attempt",
        "--run-id",
        "P:frontend-recovery-delivery",
        "--kind",
        "writer",
        "--arm",
        "P",
    )["attempt_id"]
    assert isinstance(failed_writer, str)
    service_evidence = tmp_path / "service-evidence.json"
    service_evidence.write_text(json.dumps({"closed": True}), encoding="utf-8")
    invoke(
        "start-service-transaction",
        "--attempt-id",
        failed_writer,
        "--event-type",
        "intent_service_event",
        "--transaction-id",
        "cli-intent-001",
    )
    invoke(
        "complete-service-transaction",
        "--attempt-id",
        failed_writer,
        "--event-type",
        "intent_service_event",
        "--transaction-id",
        "cli-intent-001",
        "--evidence",
        str(service_evidence),
    )
    invoke(
        "complete-attempt",
        "--attempt-id",
        failed_writer,
        "--status",
        "technical_failure",
        "--child-session",
        f"session-{failed_writer}",
        "--input-tokens",
        "0",
        "--cached-input-tokens",
        "0",
        "--output-tokens",
        "0",
        "--reasoning-output-tokens",
        "0",
        "--raw-provider-output-sha256",
        _digest("8"),
    )
    retry = invoke(
        "reserve-attempt",
        "--run-id",
        "P:frontend-recovery-delivery",
        "--kind",
        "technical_retry",
        "--arm",
        "P",
        "--retry-reason",
        "transport",
        "--retry-of-attempt-id",
        failed_writer,
    )["attempt_id"]
    assert isinstance(retry, str)
    invoke(
        "complete-attempt",
        *_task13_terminal_cli_arguments(retry),
        "--candidate-digest",
        _digest("7"),
    )

    persisted = json.loads(ledger.read_text(encoding="utf-8"))
    assert persisted["schema"] == "ai-sdlc-v2-benefit-attempt-ledger/v6"
    attempts = {attempt["attempt_id"]: attempt for attempt in persisted["attempts"]}
    assert attempts[writer]["arm"] == "A11"
    assert attempts[writer]["parent_digest"] == _digest("a")
    assert attempts[writer]["finding_digest"] == _digest("d")
    assert attempts[writer]["repair_digest"] == _digest("e")
    assert attempts[writer]["close_digest"] == _digest("f")
    assert attempts[expert]["role"] == "primary"
    assert attempts[expert]["parent_attempt_id"] == writer
    assert attempts[expert]["candidate_digest"] == _digest("b")
    assert attempts[expert]["finding_digest"] == _digest("d")
    assert attempts[rereview]["parent_attempt_id"] == expert
    assert attempts[rereview]["candidate_digest"] == _digest("c")
    assert attempts[rereview]["finding_digest"] == _digest("d")
    assert attempts[rereview]["repair_digest"] == _digest("e")
    assert attempts[retry]["retry_reason"] == "transport"
    assert attempts[retry]["retry_of_attempt_id"] == failed_writer
    assert attempts[retry]["effective_kind"] == "writer"
    assert attempts[failed_writer]["service_events"][0]["status"] == "completed"
    assert attempts[retry]["token_usage"] == {
        "input_tokens": 2,
        "cached_input_tokens": 1,
        "output_tokens": 3,
        "reasoning_output_tokens": 1,
    }


@pytest.mark.parametrize(
    "missing_flag", ["--protocol", "--ledger", "--contract", "--workspace-root"]
)
def test_task13_verify_receipt_requires_protocol_and_ledger_as_json_usage_error(
    tmp_path: Path, missing_flag: str
) -> None:
    receipt = tmp_path / "receipt.json"
    receipt.write_text("{}", encoding="utf-8")
    protocol = _bound_protocol_path(tmp_path)
    ledger = tmp_path / "ledger.json"
    arguments = [
        "verify-receipt",
        "--receipt",
        str(receipt),
        "--protocol",
        str(protocol),
        "--ledger",
        str(ledger),
        "--contract",
        str(tmp_path / "evidence-contract.json"),
        "--workspace-root",
        str(tmp_path / "workspace"),
    ]
    flag_index = arguments.index(missing_flag)
    del arguments[flag_index : flag_index + 2]

    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "scripts/ai_sdlc_v2_benefit_benchmark.py",
            *arguments,
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    _assert_task13_json_error(result, "cli.usage")


@pytest.mark.parametrize("case", ["missing", "corrupt", "mismatched"])
def test_task13_verify_receipt_actually_loads_ledger(
    tmp_path: Path, case: str
) -> None:
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    protocol_path = tmp_path / "protocol.json"
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    if case == "missing":
        ledger.unlink()
    elif case == "corrupt":
        ledger.write_text("not-json", encoding="utf-8")
    else:
        raw = json.loads(ledger.read_text(encoding="utf-8"))
        raw["protocol_sha256"] = _digest("0")
        ledger.write_text(json.dumps(raw), encoding="utf-8")

    result = _run_task13_cli(
        "verify-receipt",
        "--receipt",
        str(receipt_path),
        "--protocol",
        str(protocol_path),
        "--ledger",
        str(ledger),
    )

    assert result.returncode != 0
    assert result.stderr == ""
    assert "Traceback" not in result.stdout
    payload = json.loads(result.stdout)
    assert any(issue["code"] == "receipt.ledger" for issue in payload["issues"])
    assert str(tmp_path) not in result.stdout


def test_task13_verify_receipt_rejects_corrupt_protocol_without_private_path(
    tmp_path: Path,
) -> None:
    protocol = tmp_path / "private-protocol.json"
    protocol.write_text("not-json", encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    receipt.write_text("{}", encoding="utf-8")

    result = _run_task13_cli(
        "verify-receipt",
        "--receipt",
        str(receipt),
        "--protocol",
        str(protocol),
        "--ledger",
        str(tmp_path / "ledger.json"),
    )

    _assert_task13_json_error(result, "cli.input")
    assert str(tmp_path) not in result.stdout


@pytest.mark.parametrize("case", ["empty_metrics", "false_protocol_digest"])
def test_task13_verify_summary_rejects_empty_metrics_and_false_digest(
    tmp_path: Path, case: str
) -> None:
    protocol_path = _bound_protocol_path(tmp_path)
    protocol = load_protocol(protocol_path)
    summary = _task12_summary(protocol)
    if case == "empty_metrics":
        summary["metrics"] = {}
    else:
        summary["protocol_sha256"] = _digest("0")
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    result = _run_task13_cli(
        "verify-summary",
        "--summary",
        str(summary_path),
        "--protocol",
        str(protocol_path),
    )

    assert result.returncode == 1
    assert result.stderr == ""
    issues = json.loads(result.stdout)["issues"]
    expected = "summary.schema" if case == "empty_metrics" else "summary.digest"
    assert any(issue["code"] == expected for issue in issues)


def test_task13_validate_reports_structural_validity_separately_from_execution_ready(
    tmp_path: Path,
) -> None:
    pending = _run_task13_cli(
        "validate", "--protocol", str(_pending_protocol_path(tmp_path))
    )
    assert pending.returncode == 0
    assert pending.stderr == ""
    pending_payload = json.loads(pending.stdout)
    assert pending_payload["structurally_valid"] is True
    assert pending_payload["execution_ready"] is False
    assert pending_payload["task2_commitment_bound"] is False
    assert pending_payload["provider_authorized"] is False
    assert pending_payload["experiment_authorized"] is False
    assert any(
        issue["code"] == "protocol.fixture-pending"
        for issue in pending_payload["issues"]
    )

    bound = _run_task13_cli(
        "validate", "--protocol", str(_bound_protocol_path(tmp_path))
    )
    assert bound.returncode == 0
    bound_payload = json.loads(bound.stdout)
    assert bound_payload == {
        "execution_ready": False,
        "experiment_authorized": False,
        "issues": [],
        "provider_authorized": False,
        "structurally_valid": True,
        "task2_commitment_bound": True,
    }

    raw = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    raw["arms"] = ["P"]
    invalid_path = tmp_path / "invalid-protocol.json"
    invalid_path.write_text(json.dumps(raw), encoding="utf-8")
    invalid = _run_task13_cli("validate", "--protocol", str(invalid_path))
    assert invalid.returncode == 1
    invalid_payload = json.loads(invalid.stdout)
    assert invalid_payload["structurally_valid"] is False
    assert invalid_payload["execution_ready"] is False


def test_task2_bound_protocol_cannot_start_provider_or_reserve_without_authorization(
    tmp_path: Path,
) -> None:
    protocol = load_protocol(PROTOCOL_PATH)
    contract = (
        REPO_ROOT
        / "benchmarks"
        / "ai-sdlc-v2-benefits"
        / "fixtures"
        / "evidence-contract.template.json"
    )
    ledger = tmp_path / "ledger.json"
    run_id = "P:requirement-contract-ambiguity"
    authorization = _write_execution_authorization(
        tmp_path / "synthetic-authorization.json", protocol
    )
    benchmark_core.start_run(
        ledger, protocol, contract, run_id=run_id, authorization_path=authorization
    )
    for next_phase in ("framework_init", "provider"):
        benchmark_core.transition_run_phase(
            ledger,
            protocol,
            contract,
            run_id=run_id,
            next_phase=next_phase,
            authorization_path=authorization,
        )
    before = ledger.read_bytes()

    with pytest.raises(ValueError, match="authorization"):
        benchmark_core.reserve_provider_attempt(
            ledger,
            protocol,
            AttemptRequest(run_id, "writer"),
            contract,
            authorization_path=None,
        )
    assert ledger.read_bytes() == before


@pytest.mark.parametrize(
    "mutation",
    [
        "start_run",
        "transition_run_phase",
        "reserve_provider_attempt",
        "record_provider_completion",
        "start_service_transaction",
        "record_service_transaction",
        "seal_run_evidence",
    ],
)
def test_every_mutation_api_has_one_fail_closed_authorization_gate(
    tmp_path: Path, mutation: str
) -> None:
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    sentinel = b'{"sentinel":"unchanged"}'
    ledger.write_bytes(sentinel)
    contract = tmp_path / "evidence-contract.json"
    run_id = "P:requirement-contract-ambiguity"
    calls = {
        "start_run": lambda: benchmark_core.start_run(
            ledger, protocol, contract, run_id=run_id, authorization_path=None
        ),
        "transition_run_phase": lambda: benchmark_core.transition_run_phase(
            ledger,
            protocol,
            contract,
            run_id=run_id,
            next_phase="framework_init",
            authorization_path=None,
        ),
        "reserve_provider_attempt": lambda: benchmark_core.reserve_provider_attempt(
            ledger,
            protocol,
            AttemptRequest(run_id, "writer"),
            contract,
            authorization_path=None,
        ),
        "record_provider_completion": lambda: benchmark_core.record_provider_completion(
            ledger,
            protocol,
            _completion("attempt-001", "failed", False),
            contract,
            authorization_path=None,
        ),
        "start_service_transaction": lambda: benchmark_core.start_service_transaction(
            ledger,
            protocol,
            contract,
            attempt_id="attempt-001",
            event_type="intent_service_event",
            transaction_id="tx-001",
            authorization_path=None,
        ),
        "record_service_transaction": lambda: benchmark_core.record_service_transaction(
            ledger,
            protocol,
            contract,
            attempt_id="attempt-001",
            event_type="intent_service_event",
            transaction_id="tx-001",
            evidence={"closed": True},
            authorization_path=None,
        ),
        "seal_run_evidence": lambda: benchmark_core.seal_run_evidence(
            ledger,
            protocol,
            contract,
            run_id=run_id,
            workspace_root=tmp_path / "workspace",
            authorization_path=None,
        ),
    }

    with pytest.raises(ValueError, match="authorization"):
        calls[mutation]()
    assert ledger.read_bytes() == sentinel


@pytest.mark.parametrize("case", ["expired", "protocol", "budget", "identity", "scope"])
def test_execution_authorization_rejects_expiry_and_every_frozen_binding(
    tmp_path: Path, case: str
) -> None:
    protocol = _bound_protocol(tmp_path)
    authorization = _write_execution_authorization(
        tmp_path / "synthetic-authorization.json",
        protocol,
        expires_at=(
            datetime.now(UTC) - timedelta(minutes=1) if case == "expired" else None
        ),
    )
    payload = json.loads(authorization.read_text())
    if case == "protocol":
        payload["protocol_sha256"] = _digest("0")
    elif case == "budget":
        payload["attempt_budget"]["limit"] += 1
    elif case == "identity":
        payload["execution_identity"]["model"] = "drift"
    elif case == "scope":
        payload["scope"]["run_ids"] = payload["scope"]["run_ids"][:-1]
    authorization.write_bytes(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    )
    ledger = tmp_path / "ledger.json"

    with pytest.raises(ValueError, match="authorization"):
        benchmark_core.start_run(
            ledger,
            protocol,
            tmp_path / "evidence-contract.json",
            run_id="P:requirement-contract-ambiguity",
            authorization_path=authorization,
        )
    assert not ledger.exists()


@pytest.mark.parametrize("case", ["extra", "missing", "mode", "hardlink", "symlink"])
def test_execution_authorization_is_closed_and_metadata_protected(
    tmp_path: Path, case: str
) -> None:
    protocol = _bound_protocol(tmp_path)
    authorization = _write_execution_authorization(
        tmp_path / "synthetic-authorization.json", protocol
    )
    if case in {"extra", "missing"}:
        payload = json.loads(authorization.read_text())
        if case == "extra":
            payload["permission"] = True
        else:
            payload.pop("expires_at")
        authorization.write_bytes(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        )
    elif case == "mode":
        authorization.chmod(0o644)
    elif case == "hardlink":
        os.link(authorization, tmp_path / "authorization-alias.json")
    else:
        target = tmp_path / "authorization-target.json"
        authorization.rename(target)
        authorization.symlink_to(target)
    ledger = tmp_path / "ledger.json"

    with pytest.raises(ValueError, match="authorization"):
        benchmark_core.start_run(
            ledger,
            protocol,
            tmp_path / "evidence-contract.json",
            run_id="P:requirement-contract-ambiguity",
            authorization_path=authorization,
        )
    assert not ledger.exists()


def test_v1_synthetic_authorization_never_makes_formal_matrix_execution_ready(
    tmp_path: Path,
) -> None:
    protocol_path = _bound_protocol_path(tmp_path)
    protocol = load_protocol(protocol_path)
    authorization = _write_execution_authorization(
        tmp_path / "synthetic-authorization.json", protocol
    )
    default = _run_task13_cli("validate", "--protocol", str(protocol_path))
    authorized = _run_task13_cli(
        "validate", "--protocol", str(protocol_path), "--authorization", str(authorization)
    )

    assert json.loads(default.stdout) == {
        "execution_ready": False,
        "experiment_authorized": False,
        "issues": [],
        "provider_authorized": False,
        "structurally_valid": True,
        "task2_commitment_bound": True,
    }
    assert json.loads(authorized.stdout) == {
        "execution_ready": False,
        "experiment_authorized": False,
        "issues": [],
        "provider_authorized": False,
        "structurally_valid": True,
        "task2_commitment_bound": True,
    }


def test_task3_rejects_legacy_v1_formal_mode_and_has_no_formal_v2_lock(
    tmp_path: Path,
) -> None:
    protocol = load_protocol(PROTOCOL_PATH)
    authorization = _write_execution_authorization(
        tmp_path / "legacy-v1-authorization.json", protocol
    )
    raw = json.loads(authorization.read_text())
    raw["scope"]["mode"] = "single-frozen-matrix"
    authorization.write_bytes(
        json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    )

    assert benchmark_core.validate_execution_authorization(protocol, authorization)
    assert not (
        REPO_ROOT
        / "benchmarks"
        / "ai-sdlc-v2-benefits"
        / "evidence"
        / "preflight-receipt.json"
    ).exists()
    assert not list(
        (REPO_ROOT / "benchmarks" / "ai-sdlc-v2-benefits").glob(
            "execution-authorization-*.json"
        )
    )


@pytest.mark.parametrize(
    ("command", "specific"),
    [
        ("start-run", ["--run-id", "P:requirement-contract-ambiguity"]),
        (
            "transition-phase",
            [
                "--run-id",
                "P:requirement-contract-ambiguity",
                "--next-phase",
                "framework_init",
            ],
        ),
        (
            "reserve-attempt",
            ["--run-id", "P:requirement-contract-ambiguity", "--kind", "writer"],
        ),
        ("complete-attempt", ["--attempt-id", "attempt-001", "--status", "failed"]),
        (
            "start-service-transaction",
            [
                "--attempt-id",
                "attempt-001",
                "--event-type",
                "intent_service_event",
                "--transaction-id",
                "tx-001",
            ],
        ),
        (
            "complete-service-transaction",
            [
                "--attempt-id",
                "attempt-001",
                "--event-type",
                "intent_service_event",
                "--transaction-id",
                "tx-001",
                "--evidence",
                "service-evidence.json",
            ],
        ),
        (
            "seal-run-evidence",
            [
                "--run-id",
                "P:requirement-contract-ambiguity",
                "--workspace-root",
                "workspace",
            ],
        ),
    ],
)
def test_every_mutation_cli_requires_independent_authorization_before_write(
    tmp_path: Path, command: str, specific: list[str]
) -> None:
    protocol = _bound_protocol_path(tmp_path)
    ledger = tmp_path / "ledger.json"
    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "scripts/ai_sdlc_v2_benefit_benchmark.py",
            command,
            "--ledger",
            str(ledger),
            "--protocol",
            str(protocol),
            "--contract",
            str(tmp_path / "evidence-contract.json"),
            *specific,
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    _assert_task13_json_error(result, "cli.usage")
    assert not ledger.exists()


def test_task13_cli_missing_private_file_error_is_json_and_path_redacted() -> None:
    private_path = "/Users/private-owner/secret/protocol.json"
    result = _run_task13_cli("validate", "--protocol", private_path)

    _assert_task13_json_error(result, "cli.input")
    assert private_path not in result.stdout
    assert "/Users/private-owner" not in result.stdout


@pytest.mark.parametrize("surface", ["receipt", "ledger", "summary"])
@pytest.mark.parametrize(
    "private_value",
    [
        "/Users/private-owner/secret/result.json",
        r"C:\Users\private-owner\secret\result.json",
        r"\\private-server\share\secret\result.json",
        "file:///Users/private-owner/secret/result.json",
    ],
    ids=["posix", "windows", "unc", "file-uri"],
)
def test_task13_cli_redacts_private_paths_from_all_issue_messages(
    tmp_path: Path, surface: str, private_value: str
) -> None:
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    protocol_path = tmp_path / "protocol.json"
    if surface == "receipt":
        receipt[private_value] = "unexpected"
        artifact = tmp_path / "receipt.json"
        artifact.write_text(json.dumps(receipt), encoding="utf-8")
        result = _run_task13_cli(
            "verify-receipt",
            "--receipt",
            str(artifact),
            "--protocol",
            str(protocol_path),
            "--ledger",
            str(ledger),
        )
    elif surface == "ledger":
        raw_ledger = json.loads(ledger.read_text(encoding="utf-8"))
        raw_ledger[private_value] = "unexpected"
        ledger.write_text(json.dumps(raw_ledger), encoding="utf-8")
        artifact = tmp_path / "receipt.json"
        artifact.write_text(json.dumps(receipt), encoding="utf-8")
        result = _run_task13_cli(
            "verify-receipt",
            "--receipt",
            str(artifact),
            "--protocol",
            str(protocol_path),
            "--ledger",
            str(ledger),
        )
    else:
        summary = _task12_summary(protocol)
        summary[private_value] = "unexpected"
        artifact = tmp_path / "summary.json"
        artifact.write_text(json.dumps(summary), encoding="utf-8")
        result = _run_task13_cli(
            "verify-summary",
            "--summary",
            str(artifact),
            "--protocol",
            str(protocol_path),
        )

    assert result.returncode == 1
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["issues"]
    messages = "\n".join(issue["message"] for issue in payload["issues"])
    assert private_value not in messages
    assert "private-owner" not in messages
    assert "private-server" not in messages
    assert "<redacted-path>" in messages


# Final core/evidence audit: every test below was introduced against BASE 7277ef0.


def test_final_core_union_schema_rejects_bool_as_nullable_integer(
    tmp_path: Path,
) -> None:
    protocol, ledger, receipt = _task12_completed_a11_run(tmp_path)
    receipt["loop"]["close"]["exit_code"] = False
    assert any(
        issue.code == "receipt.schema"
        for issue in verify_receipt(receipt, protocol, ledger)
    )


def test_final_core_receipt_v3_fails_closed_without_implicit_migration(
    tmp_path: Path,
) -> None:
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    receipt["schema"] = "ai-sdlc-v2-benefit-run-receipt/v4"
    assert any(
        issue.code == "receipt.schema"
        for issue in verify_receipt(receipt, protocol, ledger)
    )


@pytest.mark.parametrize(
    "schema",
    [
        {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer", "answer"],
            "additionalProperties": False,
        },
        {"type": "string", "enum": ["yes", "yes"]},
        {"type": ["string", "string"]},
        {"type": ["string", "null"], "minimum": 0},
        {"type": ["integer", "null"], "const": False},
    ],
)
def test_final_core_provider_schema_rejects_duplicate_or_open_union_operands(
    schema,
) -> None:
    assert validate_provider_output_schema(schema)


def test_final_core_provider_schema_accepts_closed_nullable_union() -> None:
    schema = {
        "type": "object",
        "properties": {
            "count": {"type": ["integer", "null"], "minimum": 0},
            "label": {"type": ["string", "null"], "minLength": 1},
        },
        "required": ["count", "label"],
        "additionalProperties": False,
    }
    assert not validate_provider_output_schema(schema)


@pytest.mark.parametrize(
    "value",
    [
        "https://example.test/public?next=/Users/private/result.json",
        "https://example.test/public?next=C:\\Users\\private\\result.json",
        "https://example.test/public?next=\\\\server\\share\\result.json",
        "https://example.test/public#next=file:///Users/private/result.json",
    ],
)
def test_final_core_http_query_and_fragment_do_not_hide_private_paths(
    tmp_path: Path, value: str
) -> None:
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    receipt["changed_files"] = [value]
    assert any(
        issue.code == "receipt.absolute-path"
        for issue in verify_receipt(receipt, protocol, ledger)
    )


def test_final_core_allows_independent_public_http_uri(tmp_path: Path) -> None:
    protocol, ledger, receipt = _task12_completed_a11_run(tmp_path)
    receipt["loop"]["expert_callbacks"][0]["reason"] = (
        "https://example.test/public/result.json?q=ok#section"
    )
    assert not verify_receipt(receipt, protocol, ledger)


@pytest.mark.parametrize(
    ("status", "classification"),
    [
        ("completed", "timeout"),
        ("timeout", "none"),
        ("budget_exhausted", "timeout"),
    ],
)
def test_final_core_failure_classification_is_frozen_by_status(
    tmp_path: Path, status: str, classification: str
) -> None:
    if status == "completed":
        protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    else:
        protocol = _bound_protocol(tmp_path)
        ledger = tmp_path / "ledger.json"
        writer = reserve_provider_attempt(
            ledger,
            protocol,
            AttemptRequest("P:requirement-contract-ambiguity", "writer"),
        )
        record_provider_completion(
            ledger, protocol, _completion(writer.attempt_id, status, False)
        )
        receipt = _task12_receipt(protocol, ledger)
    receipt["failure_classification"] = classification
    assert any(
        issue.code == "receipt.failure-classification"
        for issue in verify_receipt(receipt, protocol, ledger)
    )


@pytest.mark.parametrize(
    "attack",
    [
        "timing_reallocation",
        "artifact_999999",
        "completeness_point_zero_one",
        "clarification_999",
    ],
)
def test_final_core_measurements_are_recomputed_from_closed_evidence(
    tmp_path: Path, attack: str
) -> None:
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    if attack == "timing_reallocation":
        receipt["timings"]["provider_wall_seconds"] += 1
        receipt["timings"]["governance_wall_seconds"] -= 1
    elif attack == "artifact_999999":
        receipt["measurements"]["total_artifact_bytes"] = 999999
    elif attack == "completeness_point_zero_one":
        receipt["measurements"]["evidence_completeness"] = 0.01
    else:
        receipt["measurements"]["clarification_request_count"] = 999
    assert verify_receipt(receipt, protocol, ledger)


def test_final_core_rejects_zero_applicable_receipt_and_summary_digests(
    tmp_path: Path,
) -> None:
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    receipt["digests"]["evaluator_result_sha256"] = _digest("0")
    receipt["external_evaluator"]["result_sha256"] = _digest("0")
    assert any(
        issue.code == "receipt.digest"
        for issue in verify_receipt(receipt, protocol, ledger)
    )

    summary = _task12_summary(protocol)
    summary["runs"][0]["receipt_sha256"] = _digest("0")
    assert any(
        issue.code == "summary.digest"
        for issue in verify_summary(summary, protocol)
    )


def _final_core_callback_v4(expert: dict[str, object]) -> dict[str, object]:
    callback = _task12_partial_callback(
        expert,
        loop_id="benefit-a11-requirement-contract-ambiguity",
        loop_type="design-contract",
    )
    return callback


def test_final_core_failed_expert_is_disclosed_and_accepted(tmp_path: Path) -> None:
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    writer, expert = _writer_at_review_with_expert(ledger, protocol)
    record_provider_completion(
        ledger, protocol, _completion(expert.attempt_id, "failed", True)
    )
    record_provider_completion(
        ledger, protocol, _completion(writer.attempt_id, "failed", True)
    )
    receipt = _task12_receipt(
        protocol, ledger, run_id="A11:requirement-contract-ambiguity"
    )
    persisted = json.loads(ledger.read_text(encoding="utf-8"))
    expert_state = next(
        item for item in persisted["attempts"] if item["attempt_id"] == expert.attempt_id
    )
    callback = _final_core_callback_v4(expert_state)
    callback["status"] = "fail"
    callback["review_exit_code"] = 1
    receipt["loop"]["expert_callbacks"] = [callback]
    assert not verify_receipt(receipt, protocol, ledger)


@pytest.mark.parametrize("with_failed_rereview", [False, True])
def test_final_core_writer_repair_and_failed_rereview_are_never_hidden(
    tmp_path: Path, with_failed_rereview: bool
) -> None:
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    run_id = "A11:requirement-contract-ambiguity"
    writer, expert = _writer_at_review_with_expert(ledger, protocol)
    record_provider_completion(
        ledger,
        protocol,
        _completion(expert.attempt_id, "completed", True, finding_digest=_digest("d")),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "candidate_ready",
            True,
            candidate_digest=_digest("c"),
            finding_digest=_digest("d"),
            repair_digest=_digest("e"),
        ),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "review_pending",
            True,
            candidate_digest=_digest("c"),
        ),
    )
    rereview = None
    if with_failed_rereview:
        rereview = reserve_provider_attempt(
            ledger, protocol, _rereview_request(expert.attempt_id, _digest("c"))
        )
        record_provider_completion(
            ledger, protocol, _completion(rereview.attempt_id, "failed", True)
        )
    record_provider_completion(
        ledger, protocol, _completion(writer.attempt_id, "failed", True)
    )
    receipt = _task12_receipt(protocol, ledger, run_id=run_id)
    persisted = json.loads(ledger.read_text(encoding="utf-8"))
    attempts = {item["attempt_id"]: item for item in persisted["attempts"]}
    callback = _final_core_callback_v4(attempts[expert.attempt_id])
    callback.update(
        {
            "status": "fail",
            "finding_count": 1,
            "severe_finding_count": 1,
            "finding_digest": _digest("d"),
            "repair_digest": _digest("e"),
            "repaired_candidate_digest": _digest("c"),
        }
    )
    if rereview is not None:
        state = attempts[rereview.attempt_id]
        callback["rereviews"] = [
            {
                "attempt_id": state["attempt_id"],
                "status": state["status"],
                "child_session": state["child_session"],
                "token_usage": copy.deepcopy(state["token_usage"]),
                "raw_output_sha256": state["raw_provider_output_sha256"],
                "finding_digest": state["finding_digest"],
                "repair_digest": state["repair_digest"],
                "candidate_digest": state["candidate_digest"],
                "argv": _task12_review_argv(run_id, _digest("c")),
                "exit_code": 1,
                "snapshot_sha256": _digest("1"),
                "input_sha256": _digest("2"),
                "parent_tree_before_sha256": _digest("4"),
                "parent_tree_after_sha256": _digest("4"),
            }
        ]
    receipt["loop"]["expert_callbacks"] = [callback]
    assert not verify_receipt(receipt, protocol, ledger)


def test_final_core_needs_operator_rejects_duplicate_callback_attempt(
    tmp_path: Path,
) -> None:
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    run_id = "A11:multi-tenant-security-review"
    writer = reserve_provider_attempt(
        ledger, protocol, AttemptRequest(run_id, "writer", parent_digest=_digest("a"))
    )
    for status in ("candidate_ready", "review_pending"):
        record_provider_completion(
            ledger,
            protocol,
            _completion(writer.attempt_id, status, True, candidate_digest=_digest("b")),
        )
    experts = []
    for kind, role, finding in (
        ("primary_expert", "primary", _digest("d")),
        ("cross_risk_expert", "cross-risk", _digest("e")),
    ):
        expert = reserve_provider_attempt(
            ledger,
            protocol,
            AttemptRequest(
                run_id,
                kind,
                parent_attempt_id=writer.attempt_id,
                role=role,
                parent_digest=_digest("a"),
                candidate_digest=_digest("b"),
            ),
        )
        record_provider_completion(
            ledger,
            protocol,
            _completion(expert.attempt_id, "completed", True, finding_digest=finding),
        )
        experts.append(expert)
    record_provider_completion(
        ledger, protocol, _completion(writer.attempt_id, "needs_operator", True)
    )
    receipt = _task12_receipt(protocol, ledger, run_id=run_id)
    persisted = json.loads(ledger.read_text(encoding="utf-8"))
    states = {item["attempt_id"]: item for item in persisted["attempts"]}
    callbacks = []
    for expert in experts:
        callback = _final_core_callback_v4(states[expert.attempt_id])
        callback.update(
            {
                "status": "conflict",
                "finding_count": 1,
                "severe_finding_count": 1,
                "finding_digest": states[expert.attempt_id]["finding_digest"],
                "reason": "incompatible security repair boundaries",
            }
        )
        callback["review_argv"] = _task12_review_argv(run_id, _digest("a"))
        callbacks.append(callback)
    receipt["loop"]["expert_callbacks"] = callbacks
    assert not verify_receipt(receipt, protocol, ledger)
    receipt["loop"]["expert_callbacks"].append(copy.deepcopy(callbacks[0]))
    assert any(
        issue.code == "receipt.a11.conflict"
        for issue in verify_receipt(receipt, protocol, ledger)
    )


def test_final_core_clarification_events_recompute_count_latency_and_digest(
    tmp_path: Path,
) -> None:
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    writer = reserve_provider_attempt(
        ledger, protocol, AttemptRequest("P:requirement-contract-ambiguity", "writer")
    )
    start_service_transaction(
        ledger,
        protocol,
        tmp_path / "evidence-contract.json",
        attempt_id=writer.attempt_id,
        event_type="clarification_request_event",
        transaction_id="clarification-001",
    )
    record_service_transaction(
        ledger,
        protocol,
        tmp_path / "evidence-contract.json",
        attempt_id=writer.attempt_id,
        event_type="clarification_request_event",
        transaction_id="clarification-001",
        evidence={"closed": True},
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id, "completed", True, candidate_digest=_digest("b")
        ),
    )
    receipt = _task12_receipt(protocol, ledger)
    recorded_event = receipt["automated_events"][0]
    receipt["measurements"]["clarification_request_count"] = 1
    receipt["measurements"]["intent_approval_service_latency_ms"] = recorded_event[
        "latency_ms"
    ]
    assert not verify_receipt(receipt, protocol, ledger)

    receipt["automated_events"][0]["latency_ms"] = 11
    assert any(
        issue.code in {"receipt.measurements", "receipt.ledger-evidence"}
        for issue in verify_receipt(receipt, protocol, ledger)
    )


def test_final_core_artifact_inventory_rejects_duplicate_or_unobserved_binding(
    tmp_path: Path,
) -> None:
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    receipt["artifact_inventory"][1]["path"] = receipt["artifact_inventory"][0][
        "path"
    ]
    assert any(
        issue.code in {"receipt.measurements", "receipt.ledger-evidence"}
        for issue in verify_receipt(receipt, protocol, ledger)
    )


def test_final_core_started_expert_retry_has_exact_callback_closure(
    tmp_path: Path,
) -> None:
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    run_id = "A11:requirement-contract-ambiguity"
    writer, expert = _writer_at_review_with_expert(ledger, protocol)
    record_provider_completion(
        ledger, protocol, _completion(expert.attempt_id, "technical_failure", False)
    )
    retry = _reserve_technical_retry(ledger, protocol, expert.attempt_id)
    record_provider_completion(
        ledger, protocol, _completion(retry.attempt_id, "completed", True)
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "completed",
            True,
            candidate_digest=_digest("b"),
            close_digest=_digest("9"),
        ),
    )
    receipt = _task12_receipt(protocol, ledger, run_id=run_id)
    persisted = json.loads(ledger.read_text(encoding="utf-8"))
    states = {item["attempt_id"]: item for item in persisted["attempts"]}
    callbacks = []
    for attempt_id, callback_status, exit_code in (
        (expert.attempt_id, "fail", 1),
        (retry.attempt_id, "pass", 0),
    ):
        callback = _final_core_callback_v4(states[attempt_id])
        callback["status"] = callback_status
        callback["review_exit_code"] = exit_code
        callbacks.append(callback)
    receipt["loop"]["expert_callbacks"] = callbacks
    assert not verify_receipt(receipt, protocol, ledger)

    receipt["loop"]["expert_callbacks"].pop(0)
    assert any(
        issue.code == "receipt.a11.evidence"
        for issue in verify_receipt(receipt, protocol, ledger)
    )


def test_final_core_started_rereview_retry_has_exact_nested_closure(
    tmp_path: Path,
) -> None:
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    run_id = "A11:requirement-contract-ambiguity"
    writer, expert = _writer_at_review_with_expert(ledger, protocol)
    record_provider_completion(
        ledger,
        protocol,
        _completion(expert.attempt_id, "completed", True, finding_digest=_digest("d")),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "candidate_ready",
            True,
            candidate_digest=_digest("c"),
            finding_digest=_digest("d"),
            repair_digest=_digest("e"),
        ),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id, "review_pending", True, candidate_digest=_digest("c")
        ),
    )
    rereview = reserve_provider_attempt(
        ledger, protocol, _rereview_request(expert.attempt_id, _digest("c"))
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(rereview.attempt_id, "technical_failure", False),
    )
    retry = _reserve_technical_retry(ledger, protocol, rereview.attempt_id)
    record_provider_completion(
        ledger, protocol, _completion(retry.attempt_id, "completed", True)
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "completed",
            True,
            candidate_digest=_digest("c"),
            close_digest=_digest("9"),
        ),
    )
    receipt = _task12_receipt(protocol, ledger, run_id=run_id)
    persisted = json.loads(ledger.read_text(encoding="utf-8"))
    states = {item["attempt_id"]: item for item in persisted["attempts"]}
    callback = _final_core_callback_v4(states[expert.attempt_id])
    callback.update(
        {
            "status": "pass",
            "finding_count": 1,
            "severe_finding_count": 1,
            "finding_digest": _digest("d"),
            "repair_digest": _digest("e"),
            "repaired_candidate_digest": _digest("c"),
        }
    )
    callback["rereviews"] = []
    for attempt_id, exit_code in (
        (rereview.attempt_id, 1),
        (retry.attempt_id, 0),
    ):
        state = states[attempt_id]
        callback["rereviews"].append(
            {
                "attempt_id": attempt_id,
                "status": state["status"],
                "child_session": state["child_session"],
                "token_usage": copy.deepcopy(state["token_usage"]),
                "raw_output_sha256": state["raw_provider_output_sha256"],
                "finding_digest": _digest("d"),
                "repair_digest": _digest("e"),
                "candidate_digest": _digest("c"),
                "argv": _task12_review_argv(run_id, _digest("c")),
                "exit_code": exit_code,
                "snapshot_sha256": _digest("1"),
                "input_sha256": _digest("2"),
                "parent_tree_before_sha256": _digest("4"),
                "parent_tree_after_sha256": _digest("4"),
            }
        )
    receipt["loop"]["expert_callbacks"] = [callback]
    assert not verify_receipt(receipt, protocol, ledger)

    receipt["loop"]["expert_callbacks"][0]["rereviews"].pop(0)
    assert any(
        issue.code == "receipt.a11.evidence"
        for issue in verify_receipt(receipt, protocol, ledger)
    )


def test_task13_cli_preserves_public_https_uri_while_sanitizing_input_error(
    tmp_path: Path,
) -> None:
    public_uri = "https://example.test/public/benchmark.json"
    raw = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    raw[public_uri] = "unexpected"
    protocol_path = tmp_path / "protocol-with-public-uri.json"
    protocol_path.write_text(json.dumps(raw), encoding="utf-8")

    result = _run_task13_cli("validate", "--protocol", str(protocol_path))

    _assert_task13_json_error(result, "cli.input")
    assert json.loads(result.stdout)["error"]["message"].endswith(public_uri)


@pytest.mark.parametrize("surface", ["validation", "exception"])
@pytest.mark.parametrize("context", ["comma", "semicolon", "parentheses", "query"])
@pytest.mark.parametrize(
    "private_value",
    [
        "/Users/private-owner/secret/result.json",
        r"C:\Users\private-owner\secret\result.json",
        r"\\private-server\share\secret\result.json",
        "file:///Users/private-owner/secret/result.json",
    ],
    ids=["posix", "windows", "unc", "file-uri"],
)
def test_task13_cli_only_protects_strict_public_http_components(
    tmp_path: Path, surface: str, context: str, private_value: str
) -> None:
    public_uri = "https://example.test/public"
    composite = {
        "comma": f"{public_uri},{private_value}",
        "semicolon": f"{public_uri};{private_value}",
        "parentheses": f"{public_uri}({private_value})",
        "query": f"{public_uri}?next={private_value}",
    }[context]
    if surface == "validation":
        _, ledger, receipt = _task12_completed_p_run(tmp_path)
        receipt[composite] = "unexpected"
        receipt_path = tmp_path / "receipt.json"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        result = _run_task13_cli(
            "verify-receipt",
            "--receipt",
            str(receipt_path),
            "--protocol",
            str(tmp_path / "protocol.json"),
            "--ledger",
            str(ledger),
        )
        assert result.returncode == 1
        messages = "\n".join(
            issue["message"] for issue in json.loads(result.stdout)["issues"]
        )
    else:
        raw = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
        raw[composite] = "unexpected"
        protocol_path = tmp_path / "invalid-protocol.json"
        protocol_path.write_text(json.dumps(raw), encoding="utf-8")
        result = _run_task13_cli("validate", "--protocol", str(protocol_path))
        _assert_task13_json_error(result, "cli.input")
        messages = json.loads(result.stdout)["error"]["message"]

    assert result.stderr == ""
    assert public_uri in messages
    assert private_value not in messages
    assert "private-owner" not in messages
    assert "private-server" not in messages
    assert "<redacted-path>" in messages


@pytest.mark.parametrize("surface", ["validation", "exception"])
@pytest.mark.parametrize("location", ["query", "fragment"])
@pytest.mark.parametrize(
    "secret_parameter",
    [
        "api_key=sk-privatecredential123456",
        'token="ghp_123456789012345678901234"',
        "access%5Ftoken%3Dsk%2Dprivatecredential123456",
        "authorization=%27Bearer%20privatecredential123456%27",
        "auth%5Ftoken%3D%22sk%2Dprivatecredential123456%22",
    ],
    ids=[
        "api-key",
        "quoted-token",
        "encoded-access-token",
        "authorization",
        "encoded-quoted-auth-token",
    ],
)
def test_task13_cli_redacts_secrets_inside_public_http_query_and_fragment(
    tmp_path: Path, surface: str, location: str, secret_parameter: str
) -> None:
    public_uri = "https://example.test/public/result.json"
    if location == "query":
        composite = f"{public_uri}?keep=ok&{secret_parameter}&mode=fast#section"
    else:
        composite = f"{public_uri}?keep=ok#section&{secret_parameter}&visible=yes"
    if surface == "validation":
        _, ledger, receipt = _task12_completed_p_run(tmp_path)
        receipt[composite] = "unexpected"
        receipt_path = tmp_path / "receipt.json"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        result = _run_task13_cli(
            "verify-receipt",
            "--receipt",
            str(receipt_path),
            "--protocol",
            str(tmp_path / "protocol.json"),
            "--ledger",
            str(ledger),
        )
        assert result.returncode == 1
        message = "\n".join(
            issue["message"] for issue in json.loads(result.stdout)["issues"]
        )
    else:
        raw = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
        raw[composite] = "unexpected"
        protocol_path = tmp_path / "invalid-protocol.json"
        protocol_path.write_text(json.dumps(raw), encoding="utf-8")
        result = _run_task13_cli("validate", "--protocol", str(protocol_path))
        _assert_task13_json_error(result, "cli.input")
        message = json.loads(result.stdout)["error"]["message"]

    assert result.stderr == ""
    assert public_uri in message
    assert "keep=ok" in message
    assert "privatecredential" not in message
    assert "ghp_123456789012345678901234" not in message
    assert "REDACTED" in message
    if location == "query":
        assert "mode=fast#section" in message
    else:
        assert "section" in message
        assert "visible=yes" in message


@pytest.mark.parametrize("surface", ["validation", "exception"])
def test_task13_cli_preserves_non_sensitive_http_query_and_fragment(
    tmp_path: Path, surface: str
) -> None:
    public_uri = "https://example.test/public/result.json?keep=ok&mode=fast#section"
    if surface == "validation":
        _, ledger, receipt = _task12_completed_p_run(tmp_path)
        receipt[public_uri] = "unexpected"
        receipt_path = tmp_path / "receipt.json"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        result = _run_task13_cli(
            "verify-receipt",
            "--receipt",
            str(receipt_path),
            "--protocol",
            str(tmp_path / "protocol.json"),
            "--ledger",
            str(ledger),
        )
        assert result.returncode == 1
        message = "\n".join(
            issue["message"] for issue in json.loads(result.stdout)["issues"]
        )
    else:
        raw = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
        raw[public_uri] = "unexpected"
        protocol_path = tmp_path / "invalid-protocol.json"
        protocol_path.write_text(json.dumps(raw), encoding="utf-8")
        result = _run_task13_cli("validate", "--protocol", str(protocol_path))
        _assert_task13_json_error(result, "cli.input")
        message = json.loads(result.stdout)["error"]["message"]

    assert result.stderr == ""
    assert public_uri in message
    assert "REDACTED" not in message


@pytest.mark.parametrize(
    "private_value",
    [
        "/Users/private-owner/secret/result.json",
        r"C:\Users\private-owner\secret\result.json",
        r"\\private-server\share\secret\result.json",
        "file:///Users/private-owner/secret/result.json",
    ],
    ids=["posix", "windows", "unc", "file-uri"],
)
def test_task13_cli_surfaces_core_http_query_private_path_finding(
    tmp_path: Path, private_value: str
) -> None:
    _, ledger, receipt = _task12_completed_p_run(tmp_path)
    receipt["changed_files"] = [
        f"https://example.test/public/result.json?next={private_value}"
    ]
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    result = _run_task13_cli(
        "verify-receipt",
        "--receipt",
        str(receipt_path),
        "--protocol",
        str(tmp_path / "protocol.json"),
        "--ledger",
        str(ledger),
    )

    assert result.returncode == 1
    assert result.stderr == ""
    assert any(
        issue["code"] == "receipt.absolute-path"
        for issue in json.loads(result.stdout)["issues"]
    )


def test_release_gate_protocol_rejects_zero_fixture_commitment(tmp_path: Path) -> None:
    raw = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    raw["execution_lock"]["fixture_tree_sha256"] = "0" * 64
    raw["execution_lock"]["fixture_commitment"] = "0" * 64
    path = tmp_path / "zero-bound-protocol.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    assert "protocol.lock" in {
        issue.code for issue in validate_protocol(load_protocol(path), REPO_ROOT)
    }


def test_release_gate_ledger_rejects_global_duplicate_child_session(
    tmp_path: Path,
) -> None:
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    for index, (run_id, candidate) in enumerate((
        ("P:requirement-contract-ambiguity", _digest("a")),
        ("S:requirement-contract-ambiguity", _digest("b")),
    )):
        writer = reserve_provider_attempt(
            ledger, protocol, AttemptRequest(run_id, "writer")
        )
        completion = RawAttemptCompletion(
            writer.attempt_id,
            "completed",
            True,
            candidate_digest=candidate,
            child_session="duplicate-logical-session",
            token_usage={
                "input_tokens": 1,
                "cached_input_tokens": 0,
                "output_tokens": 1,
                "reasoning_output_tokens": 0,
            },
            raw_provider_output_sha256=_digest("9"),
        )
        if index == 0:
            record_provider_completion(ledger, protocol, completion)
        else:
            with pytest.raises(ValueError, match="child session"):
                record_provider_completion(ledger, protocol, completion)


@pytest.mark.parametrize(
    "run_id",
    [
        "P:requirement-contract-ambiguity",
        "S:requirement-contract-ambiguity",
        "A00:requirement-contract-ambiguity",
        "A10:requirement-contract-ambiguity",
        "A11:requirement-contract-ambiguity",
    ],
)
def test_release_gate_needs_operator_is_security_a11_only(
    tmp_path: Path, run_id: str
) -> None:
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    writer = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest(
            run_id,
            "writer",
            parent_digest=_digest("a") if run_id.startswith("A11:") else None,
        ),
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id,
            "candidate_ready",
            True,
            candidate_digest=_digest("b"),
        ),
    )

    with pytest.raises(ValueError, match="security"):
        record_provider_completion(
            ledger,
            protocol,
            _completion(writer.attempt_id, "needs_operator", True),
        )


def test_release_gate_receipt_cannot_self_attest_phase_or_artifact_measurements(
    tmp_path: Path,
) -> None:
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    receipt["phase_evidence"]["setup"]["ended_at"] = receipt["phase_evidence"][
        "framework_init"
    ]["ended_at"]
    receipt["phase_evidence"]["framework_init"]["started_at"] = receipt[
        "phase_evidence"
    ]["setup"]["ended_at"]
    for phase_name in ("setup", "framework_init"):
        phase = receipt["phase_evidence"][phase_name]
        payload = {
            "phase": phase_name,
            "started_at": phase["started_at"],
            "ended_at": phase["ended_at"],
        }
        phase["evidence_sha256"] = sha256(
            json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
    receipt["timings"]["setup_wall_seconds"] = 3
    receipt["timings"]["framework_init_wall_seconds"] = 0
    receipt["artifact_inventory"][0].update(
        {"sha256": _digest("e"), "size_bytes": 4}
    )
    receipt["measurements"]["setup_artifact_bytes"] = 4
    receipt["measurements"]["total_artifact_bytes"] = 9

    assert any(
        issue.code == "receipt.ledger-evidence"
        for issue in verify_receipt(receipt, protocol, ledger)
    )


def test_release_gate_failure_classification_is_uniquely_derived(
    tmp_path: Path,
) -> None:
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    writer = reserve_provider_attempt(
        ledger,
        protocol,
        AttemptRequest("P:requirement-contract-ambiguity", "writer"),
    )
    record_provider_completion(
        ledger, protocol, _completion(writer.attempt_id, "failed", True)
    )
    receipt = _task12_receipt(protocol, ledger)
    receipt["failure_classification"] = "evaluation_failure"

    assert any(
        issue.code == "receipt.failure-classification"
        for issue in verify_receipt(receipt, protocol, ledger)
    )


@pytest.mark.parametrize(
    "value",
    [
        "https://example.test/x?api%5Fkey%5B%5D=privatecredential",
        "https://example.test/x#next=%2FUsers%2Fprivate-owner%2Fsecret.txt",
        "https://example.test/x?next=C%3A%5CUsers%5Cprivate-owner%5Csecret.txt",
        "https://example.test/x?next=%5C%5Cprivate-server%5Cshare%5Csecret.txt",
        "https://example.test/x#next=file%3A%2F%2F%2FUsers%2Fprivate-owner%2Fx",
        "https://example.test/x?%61%70%69%5F%6B%65%79%5B%5D=%73%6B%2Dprivatecredential123",
        "https://example.test/x?keep=B%65arer%20privatecredential123",
        "https://example.test/x?keep=%67%68%70%5F123456789012345678901234",
        "https://example.test/x?keep=%41%4B%49%411234567890ABCDEF",
    ],
)
def test_release_gate_core_single_decode_uri_privacy(value: str, tmp_path: Path) -> None:
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    receipt["changed_files"] = [value]

    assert any(
        issue.code in {"receipt.absolute-path", "receipt.secret"}
        for issue in verify_receipt(receipt, protocol, ledger)
    )


@pytest.mark.parametrize("surface", ["validation", "exception"])
@pytest.mark.parametrize(
    "ordinary",
    [
        "https://example.test/x?notoken=visible",
        "https://example.test/x?nosecret=visible",
        "https://example.test/x?mypassword=visible",
        "https://example.test/x?myapikey=visible",
    ],
)
def test_release_gate_cli_preserves_non_secret_substring_keys(
    ordinary: str, surface: str, tmp_path: Path
) -> None:
    if surface == "exception":
        raw = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
        raw[ordinary] = "unexpected"
        protocol_path = tmp_path / "ordinary-key-protocol.json"
        protocol_path.write_text(json.dumps(raw), encoding="utf-8")
        result = _run_task13_cli("validate", "--protocol", str(protocol_path))
        _assert_task13_json_error(result, "cli.input")
        message = json.loads(result.stdout)["error"]["message"]
    else:
        _, ledger, receipt = _task12_completed_p_run(tmp_path)
        receipt[ordinary] = "unexpected"
        receipt_path = tmp_path / "receipt.json"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        result = _run_task13_cli(
            "verify-receipt",
            "--receipt",
            str(receipt_path),
            "--protocol",
            str(tmp_path / "protocol.json"),
            "--ledger",
            str(ledger),
        )
        assert result.returncode == 1
        message = "\n".join(
            issue["message"] for issue in json.loads(result.stdout)["issues"]
        )
    assert ordinary in message


@pytest.mark.parametrize("surface", ["validation", "exception"])
@pytest.mark.parametrize(
    ("private_uri", "expected_marker"),
    [
        (
            "https://example.test/x?api%5Fkey%5B%5D=privatecredential",
            "REDACTED",
        ),
        (
            "https://example.test/x#next=%2FUsers%2Fprivate-owner%2Fsecret.txt",
            "<redacted-path>",
        ),
        (
            "https://example.test/x?next=C%3A%5CUsers%5Cprivate-owner%5Cx",
            "<redacted-path>",
        ),
        (
            "https://example.test/x?next=%5C%5Cprivate-server%5Cshare%5Cx",
            "<redacted-path>",
        ),
        (
            "https://example.test/x#next=file%3A%2F%2F%2FUsers%2Fprivate-owner%2Fx",
            "<redacted-path>",
        ),
    ],
)
def test_release_gate_cli_single_decode_uri_privacy_on_all_surfaces(
    tmp_path: Path, surface: str, private_uri: str, expected_marker: str
) -> None:
    if surface == "exception":
        raw = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
        raw[private_uri] = "unexpected"
        path = tmp_path / "private-protocol.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        result = _run_task13_cli("validate", "--protocol", str(path))
        _assert_task13_json_error(result, "cli.input")
        message = json.loads(result.stdout)["error"]["message"]
    else:
        _, ledger, receipt = _task12_completed_p_run(tmp_path)
        receipt[private_uri] = "unexpected"
        path = tmp_path / "private-receipt.json"
        path.write_text(json.dumps(receipt), encoding="utf-8")
        result = _run_task13_cli(
            "verify-receipt",
            "--receipt",
            str(path),
            "--protocol",
            str(tmp_path / "protocol.json"),
            "--ledger",
            str(ledger),
        )
        assert result.returncode == 1
        message = "\n".join(
            issue["message"] for issue in json.loads(result.stdout)["issues"]
        )
    assert "privatecredential" not in message
    assert "private-owner" not in message
    assert "private-server" not in message
    assert expected_marker in message


def test_release_gate_cli_atomically_snapshots_real_run_evidence(
    tmp_path: Path,
) -> None:
    protocol = _bound_protocol(tmp_path)
    protocol_path = tmp_path / "protocol.json"
    ledger = tmp_path / "ledger.json"
    run_id = "P:requirement-contract-ambiguity"
    writer = reserve_provider_attempt(
        ledger, protocol, AttemptRequest(run_id, "writer")
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(
            writer.attempt_id, "completed", True, candidate_digest=_digest("b")
        ),
    )
    workspace = tmp_path / "real-workspace"
    for relative, payload in (
        ("benchmark-task/.evidence/setup.json", b"setup"),
        ("benchmark-task/.evidence/governance.json", b"governance"),
        ("benchmark-task/result.txt", b"real-result"),
        ("baseline/result.txt", b"prior-result"),
    ):
        artifact = workspace.joinpath(*relative.split("/"))
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(payload)

    for next_phase in ("post_provider", "review", "evaluation"):
        transitioned = _run_task13_cli(
            "transition-phase",
            "--protocol",
            str(protocol_path),
            "--ledger",
            str(ledger),
            "--contract",
            str(tmp_path / "evidence-contract.json"),
            "--run-id",
            run_id,
            "--next-phase",
            next_phase,
        )
        assert transitioned.returncode == 0, transitioned.stdout

    result = _run_task13_cli(
        "seal-run-evidence",
        "--protocol",
        str(protocol_path),
        "--ledger",
        str(ledger),
        "--contract",
        str(tmp_path / "evidence-contract.json"),
        "--run-id",
        run_id,
        "--workspace-root",
        str(workspace),
    )

    assert result.returncode == 0
    assert result.stderr == ""
    snapshot = json.loads(ledger.read_text(encoding="utf-8"))["runs"][run_id][
        "sealed_evidence"
    ]
    delivery = snapshot["artifact_inventory"][2]
    assert delivery["path"] == "benchmark-task/result.txt"
    assert delivery["size_bytes"] == len(b"real-result")
    assert delivery["sha256"] == sha256(b"real-result").hexdigest()
    assert snapshot["changed_files"] == ["benchmark-task/result.txt"]
    ledger_bytes = ledger.read_bytes()
    (workspace / "benchmark-task" / "result.txt").write_bytes(b"later-tamper")
    repeated = _run_task13_cli(
        "seal-run-evidence",
        "--protocol",
        str(protocol_path),
        "--ledger",
        str(ledger),
        "--contract",
        str(tmp_path / "evidence-contract.json"),
        "--run-id",
        run_id,
        "--workspace-root",
        str(workspace),
    )
    assert repeated.returncode == 2
    assert ledger.read_bytes() == ledger_bytes


def test_release_gate_round2_protocol_tracks_bound_evidence_contract() -> None:
    protocol = load_protocol(PROTOCOL_PATH)
    assert validate_protocol(protocol, REPO_ROOT) == []
    assert (
        protocol.execution_lock.evidence_contract_sha256
        == protocol.execution_lock.evidence_contract_commitment
        == "7b32d614533e4c51438415bbcbb9cc885177d0752b814d95c344c8925382060c"
    )


def test_release_gate_round2_removes_bulk_self_attested_run_evidence_api() -> None:
    assert not hasattr(benchmark_core, "RunEvidenceRequest")


def test_release_gate_round2_rejects_human_events_at_authority_boundary(
    tmp_path: Path,
) -> None:
    _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    manifest = tmp_path / "caller-evidence.json"
    manifest.write_text(
        json.dumps({"human_events": [{"type": "operator_confirmation", "seconds": 1}]}),
        encoding="utf-8",
    )
    before = ledger.read_bytes() if ledger.exists() else None
    rejected = _run_task13_cli(
        "record-run-evidence",
        "--protocol",
        str(tmp_path / "protocol.json"),
        "--ledger",
        str(ledger),
        "--manifest",
        str(manifest),
    )
    assert rejected.returncode == 2
    if before is None:
        assert not ledger.exists()
    else:
        assert ledger.read_bytes() == before


@pytest.mark.parametrize("surface", ["validation", "exception"])
@pytest.mark.parametrize(
    "uri",
    [
        "https://example.test/x?%61%70%69%5F%6B%65%79%5B%5D=%73%6B%2Dprivatecredential123",
        "https://example.test/x?keep=%42%65%61%72%65%72%20privatecredential123",
        "https://example.test/x?keep=%67%68%70%5F123456789012345678901234",
        "https://example.test/x?keep=%41%4B%49%411234567890ABCDEF",
    ],
)
def test_release_gate_round2_cli_redacts_fully_encoded_secret_spans(
    tmp_path: Path, uri: str, surface: str
) -> None:
    if surface == "exception":
        raw = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
        raw[uri] = "unexpected"
        path = tmp_path / "encoded-secret-protocol.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        result = _run_task13_cli("validate", "--protocol", str(path))
        message = json.loads(result.stdout)["error"]["message"]
    else:
        _, ledger, receipt = _task12_completed_p_run(tmp_path)
        receipt[uri] = "unexpected"
        path = tmp_path / "encoded-secret-receipt.json"
        path.write_text(json.dumps(receipt), encoding="utf-8")
        result = _run_task13_cli(
            "verify-receipt",
            "--receipt",
            str(path),
            "--protocol",
            str(tmp_path / "protocol.json"),
            "--ledger",
            str(ledger),
        )
        message = "\n".join(
            issue["message"] for issue in json.loads(result.stdout)["issues"]
        )
    assert "privatecredential" not in message
    assert "123456789012345678901234" not in message
    assert "1234567890ABCDEF" not in message
    assert "REDACTED" in message


def _round2_workspace(tmp_path: Path, name: str = "workspace") -> Path:
    workspace = tmp_path / name
    for relative, payload in (
        ("benchmark-task/.evidence/setup.json", b"setup"),
        ("benchmark-task/.evidence/governance.json", b"governance"),
        ("benchmark-task/result.txt", b"candidate"),
        ("baseline/result.txt", b"baseline"),
    ):
        path = workspace.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return workspace


def test_release_gate_round2_contract_tamper_fails_before_ledger_write(
    tmp_path: Path,
) -> None:
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    writer = reserve_provider_attempt(
        ledger, protocol, AttemptRequest("P:requirement-contract-ambiguity", "writer")
    )
    record_provider_completion(
        ledger,
        protocol,
        _completion(writer.attempt_id, "completed", True, candidate_digest=_digest("b")),
    )
    before = ledger.read_bytes()
    contract = tmp_path / "evidence-contract.json"
    contract.write_bytes(contract.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="contract bytes"):
        seal_run_evidence(
            ledger,
            protocol,
            contract,
            run_id="P:requirement-contract-ambiguity",
            workspace_root=_round2_workspace(tmp_path),
        )

    assert ledger.read_bytes() == before


@pytest.mark.parametrize("stage", ["reserve", "complete"])
def test_release_gate_round2_attempt_api_requires_exact_contract_bytes(
    tmp_path: Path, stage: str
) -> None:
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    writer = None
    if stage == "complete":
        writer = reserve_provider_attempt(
            ledger,
            protocol,
            AttemptRequest("P:requirement-contract-ambiguity", "writer"),
        )
    before = ledger.read_bytes() if ledger.exists() else None
    contract = tmp_path / "evidence-contract.json"
    contract.write_bytes(contract.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="contract bytes"):
        if stage == "reserve":
            reserve_provider_attempt(
                ledger,
                protocol,
                AttemptRequest("P:requirement-contract-ambiguity", "writer"),
            )
        else:
            assert writer is not None
            record_provider_completion(
                ledger,
                protocol,
                _completion(writer.attempt_id, "failed", False),
            )

    if before is None:
        assert not ledger.exists()
    else:
        assert ledger.read_bytes() == before


@pytest.mark.parametrize("command", ["reserve-attempt", "complete-attempt"])
def test_release_gate_round2_attempt_cli_requires_contract(
    tmp_path: Path, command: str
) -> None:
    protocol_path = _bound_protocol_path(tmp_path)
    ledger = tmp_path / "ledger.json"
    arguments = [
        command,
        "--ledger",
        str(ledger),
        "--protocol",
        str(protocol_path),
    ]
    if command == "reserve-attempt":
        arguments.extend(
            ["--run-id", "P:requirement-contract-ambiguity", "--kind", "writer"]
        )
    else:
        arguments.extend(["--attempt-id", "attempt-001", "--status", "failed"])

    result = _run_task13_cli(*arguments)

    _assert_task13_json_error(result, "cli.usage")
    assert not ledger.exists()


def test_release_gate_round2_seal_rejects_late_events_and_retry(
    tmp_path: Path,
) -> None:
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    run_id = "P:requirement-contract-ambiguity"
    writer = reserve_provider_attempt(ledger, protocol, AttemptRequest(run_id, "writer"))
    record_provider_completion(
        ledger, protocol, _completion(writer.attempt_id, "technical_failure", False)
    )
    seal_run_evidence(
        ledger,
        protocol,
        tmp_path / "evidence-contract.json",
        run_id=run_id,
        workspace_root=_round2_workspace(tmp_path),
    )
    before = ledger.read_bytes()

    actions = (
        lambda: reserve_provider_attempt(
            ledger,
            protocol,
            AttemptRequest(
                run_id,
                "technical_retry",
                retry_reason="transport",
                retry_of_attempt_id=writer.attempt_id,
            ),
        ),
        lambda: record_provider_completion(
            ledger, protocol, _completion(writer.attempt_id, "failed", False)
        ),
        lambda: transition_run_phase(
            ledger,
            protocol,
            tmp_path / "evidence-contract.json",
            run_id=run_id,
            next_phase="post_provider",
        ),
        lambda: start_service_transaction(
            ledger,
            protocol,
            tmp_path / "evidence-contract.json",
            attempt_id=writer.attempt_id,
            event_type="clarification_request_event",
            transaction_id="late-transaction",
        ),
    )
    for action in actions:
        with pytest.raises(ValueError):
            action()
        assert ledger.read_bytes() == before


@pytest.mark.parametrize(
    "attack",
    [
        "cross_run_transplant",
        "attempt_change",
        "end_after_seal",
        "authority_manifest",
    ],
)
def test_release_gate_round2_reload_rejects_sealed_binding_tamper(
    tmp_path: Path, attack: str
) -> None:
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    workspace = _round2_workspace(tmp_path)
    run_ids = ["P:requirement-contract-ambiguity"]
    if attack == "cross_run_transplant":
        run_ids.append("S:requirement-contract-ambiguity")
    for run_id in run_ids:
        writer = reserve_provider_attempt(
            ledger, protocol, AttemptRequest(run_id, "writer")
        )
        record_provider_completion(
            ledger,
            protocol,
            _completion(
                writer.attempt_id, "completed", True, candidate_digest=_digest("b")
            ),
        )
        seal_run_evidence(
            ledger,
            protocol,
            tmp_path / "evidence-contract.json",
            run_id=run_id,
            workspace_root=workspace,
        )
    raw = json.loads(ledger.read_text(encoding="utf-8"))
    if attack == "cross_run_transplant":
        raw["runs"][run_ids[1]]["sealed_evidence"] = copy.deepcopy(
            raw["runs"][run_ids[0]]["sealed_evidence"]
        )
    elif attack == "attempt_change":
        raw["attempts"][0]["child_session"] = "session-mutated"
        raw["attempts"][0]["history"][-1]["child_session"] = "session-mutated"
    elif attack == "end_after_seal":
        sealed = raw["runs"][run_ids[0]]["sealed_evidence"]
        recorded = datetime.fromisoformat(
            sealed["recorded_at"].replace("Z", "+00:00")
        )
        sealed["phase_evidence"]["evaluation"]["ended_at"] = _task12_timestamp(
            recorded + timedelta(seconds=1)
        )
    else:
        raw["runs"][run_ids[0]]["sealed_evidence"]["artifact_inventory"][0][
            "required"
        ] = False
    ledger.write_text(json.dumps(raw), encoding="utf-8")
    tampered = ledger.read_bytes()

    with pytest.raises(ValueError, match="sealed|binding|phase"):
        reserve_provider_attempt(
            ledger,
            protocol,
            AttemptRequest("A00:requirement-contract-ambiguity", "writer"),
        )

    assert ledger.read_bytes() == tampered


def test_release_gate_round2_reload_rejects_future_core_timestamp(
    tmp_path: Path,
) -> None:
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    writer = reserve_provider_attempt(
        ledger, protocol, AttemptRequest("P:requirement-contract-ambiguity", "writer")
    )
    raw = json.loads(ledger.read_text(encoding="utf-8"))
    future = _task12_timestamp(datetime.now().astimezone() + timedelta(days=1))
    raw["runs"][writer.request.run_id]["run_started_at"] = future
    raw["attempts"][0]["recorded_at"] = future
    raw["attempts"][0]["history"][0]["recorded_at"] = future
    ledger.write_text(json.dumps(raw), encoding="utf-8")
    before = ledger.read_bytes()

    with pytest.raises(ValueError, match="time|start"):
        record_provider_completion(
            ledger, protocol, _completion(writer.attempt_id, "failed", False)
        )

    assert ledger.read_bytes() == before


def test_release_gate_round2_core_records_controller_phase_and_actual_file_evidence(
    tmp_path: Path,
) -> None:
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    run_id = "P:requirement-contract-ambiguity"
    writer = reserve_provider_attempt(ledger, protocol, AttemptRequest(run_id, "writer"))
    record_provider_completion(
        ledger,
        protocol,
        _completion(writer.attempt_id, "completed", True, candidate_digest=_digest("b")),
    )
    workspace = _round2_workspace(tmp_path)
    seal_run_evidence(
        ledger,
        protocol,
        tmp_path / "evidence-contract.json",
        run_id=run_id,
        workspace_root=workspace,
    )
    raw = json.loads(ledger.read_text(encoding="utf-8"))
    run = raw["runs"][run_id]
    sealed = run["sealed_evidence"]

    assert [
        sealed["phase_evidence"]["evaluation"][key]
        for key in ("started_at", "ended_at")
    ] == [
        run["phase_events"][-1]["started_at"],
        run["phase_events"][-1]["ended_at"],
    ]
    assert sealed["artifact_inventory"][2]["sha256"] == sha256(
        (workspace / "benchmark-task" / "result.txt").read_bytes()
    ).hexdigest()
    assert sealed["changed_file_evidence"] == [
        {
            "path": "benchmark-task/result.txt",
            "baseline_sha256": sha256(b"baseline").hexdigest(),
            "candidate_sha256": sha256(b"candidate").hexdigest(),
        }
    ]


def test_release_gate_round2_private_service_event_never_reaches_ledger(
    tmp_path: Path,
) -> None:
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    writer = reserve_provider_attempt(
        ledger, protocol, AttemptRequest("P:requirement-contract-ambiguity", "writer")
    )
    start_service_transaction(
        ledger,
        protocol,
        tmp_path / "evidence-contract.json",
        attempt_id=writer.attempt_id,
        event_type="intent_service_event",
        transaction_id="intent-001",
    )
    before = ledger.read_bytes()

    with pytest.raises(ValueError, match="public"):
        record_service_transaction(
            ledger,
            protocol,
            tmp_path / "evidence-contract.json",
            attempt_id=writer.attempt_id,
            event_type="intent_service_event",
            transaction_id="intent-001",
            evidence={
                "uri": "https://example.test/x?keep=%73%6B%2Dprivatecredential123"
            },
        )

    assert ledger.read_bytes() == before
    with pytest.raises(ValueError, match="open service transaction"):
        record_provider_completion(
            ledger, protocol, _completion(writer.attempt_id, "failed", False)
        )
    assert ledger.read_bytes() == before
    evidence = {"closed": True, "result": "accepted"}
    start_service_transaction(
        ledger,
        protocol,
        tmp_path / "evidence-contract.json",
        attempt_id=writer.attempt_id,
        event_type="intent_service_event",
        transaction_id="intent-002",
    )
    record_service_transaction(
        ledger,
        protocol,
        tmp_path / "evidence-contract.json",
        attempt_id=writer.attempt_id,
        event_type="intent_service_event",
        transaction_id="intent-002",
        evidence=evidence,
    )
    event = json.loads(ledger.read_text(encoding="utf-8"))["attempts"][0][
        "service_events"
    ][1]
    assert event["service_evidence_sha256"] == sha256(
        json.dumps(
            evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def test_release_gate_round2_service_transaction_identity_is_global(
    tmp_path: Path,
) -> None:
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    attempts = [
        reserve_provider_attempt(ledger, protocol, AttemptRequest(run_id, "writer"))
        for run_id in (
            "P:requirement-contract-ambiguity",
            "S:requirement-contract-ambiguity",
        )
    ]
    start_service_transaction(
        ledger,
        protocol,
        tmp_path / "evidence-contract.json",
        attempt_id=attempts[0].attempt_id,
        event_type="intent_service_event",
        transaction_id="global-transaction-001",
    )
    before = ledger.read_bytes()

    with pytest.raises(ValueError, match="globally unique"):
        start_service_transaction(
            ledger,
            protocol,
            tmp_path / "evidence-contract.json",
            attempt_id=attempts[1].attempt_id,
            event_type="intent_service_event",
            transaction_id="global-transaction-001",
        )

    assert ledger.read_bytes() == before


def test_release_gate_round3_rejects_resigned_contract_authority_tamper(
    tmp_path: Path,
) -> None:
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    raw = json.loads(ledger.read_text(encoding="utf-8"))
    sealed = raw["runs"][receipt["run_id"]]["sealed_evidence"]
    sealed["artifact_inventory"][0]["required"] = False
    sealed["seal_binding_sha256"] = benchmark_core._seal_binding_digest(sealed)
    ledger.write_text(json.dumps(raw), encoding="utf-8")
    receipt["artifact_inventory"][0]["required"] = False

    assert verify_receipt(receipt, protocol, ledger)


def test_release_gate_round3_rejects_artifact_drift_after_seal(tmp_path: Path) -> None:
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    (tmp_path / "workspace" / "benchmark-task" / "result.txt").write_text(
        "mutated-after-seal", encoding="utf-8"
    )

    assert verify_receipt(receipt, protocol, ledger)


def test_release_gate_round3_requires_atomic_phase_state_machine_api() -> None:
    assert hasattr(benchmark_core, "start_run")
    assert hasattr(benchmark_core, "transition_run_phase")
    assert not hasattr(benchmark_core, "record_phase_event")


@pytest.mark.parametrize("missing", ["contract", "workspace"])
def test_release_gate_round3_verify_requires_external_authority_inputs(
    tmp_path: Path, missing: str
) -> None:
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    contract = tmp_path / "evidence-contract.json"
    workspace = tmp_path / "workspace"
    target = contract if missing == "contract" else workspace
    if target.is_file():
        target.unlink()
    else:
        target.rename(tmp_path / "workspace-away")

    issues = _verify_receipt(receipt, protocol, ledger, contract, workspace)

    assert issues
    assert any(
        issue.code in {"receipt.evidence-contract", "receipt.workspace-evidence"}
        for issue in issues
    )


@pytest.mark.parametrize("relative", ["baseline/result.txt", "benchmark-task/result.txt"])
def test_release_gate_round3_recomputes_changed_scope_tree_digests(
    tmp_path: Path, relative: str
) -> None:
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    (tmp_path / "workspace" / relative).write_text("root-drift", encoding="utf-8")

    issues = verify_receipt(receipt, protocol, ledger)

    assert any(issue.code == "receipt.workspace-evidence" for issue in issues)


@pytest.mark.parametrize("case", ["skip", "prestart", "after-provider"])
def test_release_gate_round3_phase_machine_rejects_illegal_writer_order(
    tmp_path: Path, case: str
) -> None:
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    contract = tmp_path / "evidence-contract.json"
    run_id = "P:requirement-contract-ambiguity"
    if case == "prestart":
        before = None
        action = lambda: _reserve_provider_attempt(  # noqa: E731
            ledger, protocol, AttemptRequest(run_id, "writer"), contract
        )
    else:
        start_run(ledger, protocol, contract, run_id=run_id)
        if case == "skip":
            before = ledger.read_bytes()
            action = lambda: transition_run_phase(  # noqa: E731
                ledger, protocol, contract, run_id=run_id, next_phase="provider"
            )
        else:
            transition_run_phase(
                ledger,
                protocol,
                contract,
                run_id=run_id,
                next_phase="framework_init",
            )
            transition_run_phase(
                ledger,
                protocol,
                contract,
                run_id=run_id,
                next_phase="provider",
            )
            writer = _reserve_provider_attempt(
                ledger, protocol, AttemptRequest(run_id, "writer"), contract
            )
            _record_provider_completion(
                ledger,
                protocol,
                _completion(
                    writer.attempt_id,
                    "completed",
                    True,
                    candidate_digest=_digest("b"),
                ),
                contract,
            )
            transition_run_phase(
                ledger,
                protocol,
                contract,
                run_id=run_id,
                next_phase="post_provider",
            )
            before = ledger.read_bytes()
            action = lambda: _reserve_provider_attempt(  # noqa: E731
                ledger, protocol, AttemptRequest(run_id, "writer"), contract
            )

    with pytest.raises(ValueError):
        action()
    assert (ledger.read_bytes() if ledger.exists() else None) == before


@pytest.mark.parametrize("case", ["future", "gap", "orphan"])
def test_release_gate_round3_rejects_poisoned_phase_ledger_without_rewrite(
    tmp_path: Path, case: str
) -> None:
    protocol = _bound_protocol(tmp_path)
    ledger = tmp_path / "ledger.json"
    contract = tmp_path / "evidence-contract.json"
    run_id = "P:requirement-contract-ambiguity"
    writer = reserve_provider_attempt(ledger, protocol, AttemptRequest(run_id, "writer"))
    raw = json.loads(ledger.read_text(encoding="utf-8"))
    events = raw["runs"][run_id]["phase_events"]
    if case == "future":
        future = _task12_timestamp(datetime.now().astimezone() + timedelta(days=1))
        events[-1]["started_at"] = future
    elif case == "gap":
        events[-1]["started_at"] = events[0]["started_at"]
    else:
        raw["runs"].pop(run_id)
    ledger.write_text(json.dumps(raw), encoding="utf-8")
    before = ledger.read_bytes()

    with pytest.raises(ValueError):
        _record_provider_completion(
            ledger,
            protocol,
            _completion(writer.attempt_id, "failed", False),
            contract,
        )

    assert ledger.read_bytes() == before


def test_release_gate_round3_sealed_ledger_contains_no_private_workspace_root(
    tmp_path: Path,
) -> None:
    _protocol, ledger, _receipt = _task12_completed_p_run(tmp_path)

    assert str(tmp_path) not in ledger.read_text(encoding="utf-8")


def test_release_gate_round3_cli_full_state_machine_seal_and_verify(
    tmp_path: Path,
) -> None:
    protocol_path = _bound_protocol_path(tmp_path)
    protocol = load_protocol(protocol_path)
    ledger = tmp_path / "ledger.json"
    contract = tmp_path / "evidence-contract.json"
    run_id = "P:requirement-contract-ambiguity"

    def invoke(command: str, *arguments: str) -> subprocess.CompletedProcess[str]:
        return _run_task13_cli(
            command,
            "--ledger",
            str(ledger),
            "--protocol",
            str(protocol_path),
            "--contract",
            str(contract),
            *arguments,
        )

    assert invoke("start-run", "--run-id", run_id).returncode == 0
    for next_phase in ("framework_init", "provider"):
        assert (
            invoke(
                "transition-phase",
                "--run-id",
                run_id,
                "--next-phase",
                next_phase,
            ).returncode
            == 0
        )
    reserved = invoke("reserve-attempt", "--run-id", run_id, "--kind", "writer")
    attempt_id = json.loads(reserved.stdout)["attempt_id"]
    completed = invoke(
        "complete-attempt",
        *_task13_terminal_cli_arguments(attempt_id),
        "--candidate-digest",
        _digest("b"),
    )
    assert completed.returncode == 0, completed.stdout
    for next_phase in ("post_provider", "review", "evaluation"):
        assert (
            invoke(
                "transition-phase",
                "--run-id",
                run_id,
                "--next-phase",
                next_phase,
            ).returncode
            == 0
        )
    workspace = tmp_path / "workspace"
    for relative, payload in (
        ("benchmark-task/.evidence/setup.json", b"a"),
        ("benchmark-task/.evidence/governance.json", b"bb"),
        ("benchmark-task/result.txt", b"ccc"),
        ("baseline/result.txt", b"old"),
    ):
        target = workspace.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    sealed = invoke(
        "seal-run-evidence",
        "--run-id",
        run_id,
        "--workspace-root",
        str(workspace),
    )
    assert sealed.returncode == 0, sealed.stdout
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(
        json.dumps(_task12_receipt(protocol, ledger, seal=False)), encoding="utf-8"
    )

    verified = _run_task13_cli(
        "verify-receipt",
        "--receipt",
        str(receipt_path),
        "--protocol",
        str(protocol_path),
        "--ledger",
        str(ledger),
        "--contract",
        str(contract),
        "--workspace-root",
        str(workspace),
    )

    assert verified.returncode == 0, verified.stdout
    assert json.loads(verified.stdout) == {"issues": []}


@pytest.mark.parametrize("command", ["start-run", "transition-phase"])
def test_release_gate_round3_pending_protocol_phase_cli_does_not_write(
    tmp_path: Path, command: str
) -> None:
    ledger = tmp_path / "ledger.json"
    arguments = [
        command,
        "--ledger",
        str(ledger),
        "--protocol",
        str(PROTOCOL_PATH),
        "--contract",
        str(tmp_path / "missing-contract.json"),
        "--run-id",
        "P:requirement-contract-ambiguity",
    ]
    if command == "transition-phase":
        arguments.extend(["--next-phase", "framework_init"])

    result = _run_task13_cli(*arguments)

    _assert_task13_json_error(result, "cli.input")
    assert not ledger.exists()
