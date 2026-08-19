"""Tests for the frozen AI-SDLC v2 benefit benchmark contract."""

import copy
import json
import multiprocessing
import subprocess
from dataclasses import fields, replace
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from ai_sdlc.benefit_benchmark import (
    AttemptCompletion as RawAttemptCompletion,
)
from ai_sdlc.benefit_benchmark import (
    AttemptRequest,
    ExecutionLock,
    canonical_protocol_digest,
    load_protocol,
    record_provider_completion,
    reserve_provider_attempt,
    validate_protocol,
    validate_provider_output_schema,
    verify_receipt,
    verify_summary,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = REPO_ROOT / "benchmarks" / "ai-sdlc-v2-benefits" / "protocol.json"


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
    path = tmp_path / ("protocol-compact.json" if compact else "protocol.json")
    path.write_text(
        json.dumps(raw, separators=(",", ":") if compact else None), encoding="utf-8"
    )
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
    assert {issue.code for issue in validate_protocol(protocol, REPO_ROOT)} == {
        "protocol.fixture-pending"
    }

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

    with pytest.raises(ValueError, match="pending"):
        reserve_provider_attempt(
            ledger,
            load_protocol(PROTOCOL_PATH),
            AttemptRequest("P:requirement-contract-ambiguity", "writer"),
        )
    with pytest.raises(ValueError, match="pending"):
        record_provider_completion(
            ledger,
            load_protocol(PROTOCOL_PATH),
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


def test_completion_evidence_schema_bump_rejects_legacy_v3_without_rewriting(
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
    raw["schema"] = "ai-sdlc-v2-benefit-attempt-ledger/v3"
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
        "governance": 4,
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
    first_reserved_at = datetime.fromisoformat(
        persisted_attempts[0]["history"][0]["recorded_at"].replace("Z", "+00:00")
    )
    evaluator_completed_at = first_reserved_at + timedelta(seconds=20)
    started_at = evaluator_completed_at - timedelta(seconds=21)
    return {
        "schema": "ai-sdlc-v2-benefit-run-receipt/v4",
        "protocol_sha256": canonical_protocol_digest(protocol),
        "run_id": run.run_id,
        "arm": run.arm,
        "fixture": run.fixture,
        "order": run.position,
        "status": receipt_status,
        "failure_classification": {
            "completed": "none",
            "failed": "writer_failure",
            "timeout": "timeout",
            "needs_operator": "expert_conflict",
            "budget_exhausted": "provider_budget_exhausted",
        }[receipt_status],
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
            "end_to_end_wall_seconds": 21,
            "verified_delivery_wall_seconds": 20,
            "setup_wall_seconds": 1,
            "framework_init_wall_seconds": 2,
            "provider_wall_seconds": 3,
            "governance_wall_seconds": 4,
            "review_wall_seconds": 5,
            "evaluation_wall_seconds": 6,
        },
        "phase_evidence": _task12_phase_evidence(started_at),
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
        "artifact_inventory": [
            {
                "path": "benchmark-task/.evidence/setup.json",
                "sha256": _digest("a"),
                "size_bytes": 1,
                "category": "setup",
                "required": True,
                "observed": True,
            },
            {
                "path": "benchmark-task/.evidence/governance.json",
                "sha256": _digest("b"),
                "size_bytes": 2,
                "category": "governance",
                "required": True,
                "observed": True,
            },
            {
                "path": "benchmark-task/result.txt",
                "sha256": _digest("c"),
                "size_bytes": 3,
                "category": "delivery",
                "required": True,
                "observed": True,
            },
        ],
        "human_events": [],
        "automated_events": [],
        "command_evidence": [
            _task12_command_evidence(attempt, protocol) for attempt in attempts
        ],
        "changed_files": ["benchmark-task/result.txt"],
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
    assert '"protocol.fixture-pending"' in result.stdout


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
    protocol = load_protocol(PROTOCOL_PATH)
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
    return subprocess.run(
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
            *arguments,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stderr == ""
        return json.loads(result.stdout)

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
    assert persisted["schema"] == "ai-sdlc-v2-benefit-attempt-ledger/v4"
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
    assert attempts[retry]["token_usage"] == {
        "input_tokens": 2,
        "cached_input_tokens": 1,
        "output_tokens": 3,
        "reasoning_output_tokens": 1,
    }


@pytest.mark.parametrize("missing_flag", ["--protocol", "--ledger"])
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
    ]
    flag_index = arguments.index(missing_flag)
    del arguments[flag_index : flag_index + 2]

    result = _run_task13_cli(*arguments)

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
        "validate", "--protocol", str(PROTOCOL_PATH)
    )
    assert pending.returncode == 0
    assert pending.stderr == ""
    pending_payload = json.loads(pending.stdout)
    assert pending_payload["structurally_valid"] is True
    assert pending_payload["execution_ready"] is False
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
        "execution_ready": True,
        "issues": [],
        "structurally_valid": True,
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
    receipt["schema"] = "ai-sdlc-v2-benefit-run-receipt/v3"
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
        ("needs_operator", "writer_failure"),
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
    assert any(
        issue.code == "receipt.measurements"
        for issue in verify_receipt(receipt, protocol, ledger)
    )


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
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    event = {
        "type": "clarification_request_event",
        "started_at": "2026-08-19T00:00:00.000000Z",
        "ended_at": "2026-08-19T00:00:00.010000Z",
        "latency_ms": 10,
    }
    event["evidence_sha256"] = sha256(
        json.dumps(
            event, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    receipt["automated_events"] = [event]
    receipt["measurements"]["clarification_request_count"] = 1
    receipt["measurements"]["intent_approval_service_latency_ms"] = 10
    assert not verify_receipt(receipt, protocol, ledger)

    receipt["automated_events"][0]["latency_ms"] = 11
    assert any(
        issue.code == "receipt.measurements"
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
        issue.code == "receipt.measurements"
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
