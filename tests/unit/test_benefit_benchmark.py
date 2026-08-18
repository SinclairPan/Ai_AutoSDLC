"""Tests for the frozen AI-SDLC v2 benefit benchmark contract."""

import copy
import json
import multiprocessing
import subprocess
from dataclasses import fields, replace
from pathlib import Path

import pytest

from ai_sdlc.benefit_benchmark import (
    AttemptCompletion,
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
            AttemptCompletion(attempt_id, "failed", False),
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
            AttemptCompletion("attempt-001", "failed", False),
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
            AttemptCompletion(writer.attempt_id, "failed", False),
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
        AttemptCompletion(previous.attempt_id, "technical_failure", False),
    )
    for _ in range(3):
        previous = reserve_provider_attempt(
            ledger, protocol, retry_request(previous.attempt_id)
        )
        record_provider_completion(
            ledger,
            protocol,
            AttemptCompletion(previous.attempt_id, "technical_failure", False),
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
        AttemptCompletion(writer.attempt_id, "technical_failure", False),
    )

    retry = _reserve_technical_retry(ledger, protocol, writer.attempt_id)
    for status in ("candidate_ready", "review_pending"):
        record_provider_completion(
            ledger,
            protocol,
            AttemptCompletion(
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
        AttemptCompletion(expert.attempt_id, "completed", True),
    )
    record_provider_completion(
        ledger,
        protocol,
        AttemptCompletion(
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
        AttemptCompletion(expert.attempt_id, "technical_failure", False),
    )

    retry = _reserve_technical_retry(ledger, protocol, expert.attempt_id)
    record_provider_completion(
        ledger,
        protocol,
        AttemptCompletion(retry.attempt_id, "completed", True),
    )
    record_provider_completion(
        ledger,
        protocol,
        AttemptCompletion(
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
        AttemptCompletion(
            expert.attempt_id,
            "completed",
            True,
            finding_digest=_digest("d"),
        ),
    )
    record_provider_completion(
        ledger,
        protocol,
        AttemptCompletion(
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
        AttemptCompletion(
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
        AttemptCompletion(rereview.attempt_id, "technical_failure", False),
    )

    retry = _reserve_technical_retry(ledger, protocol, rereview.attempt_id)
    record_provider_completion(
        ledger,
        protocol,
        AttemptCompletion(retry.attempt_id, "completed", True),
    )
    record_provider_completion(
        ledger,
        protocol,
        AttemptCompletion(
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
            AttemptCompletion(writer.attempt_id, "failed", True),
        )
    else:
        retry = _reserve_technical_retry(ledger, protocol, retried.attempt_id)
        record_provider_completion(
            ledger,
            protocol,
            AttemptCompletion(writer.attempt_id, "failed", True),
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
        writer_attempt["sequence"] = writer_failure["sequence"]
        retry_attempt["sequence"] = retry_reservation["sequence"]
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
            AttemptCompletion("attempt-999", "failed", False),
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


def test_event_sequence_schema_bump_rejects_legacy_v2_without_rewriting(
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
    raw["schema"] = "ai-sdlc-v2-benefit-attempt-ledger/v2"
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
        AttemptCompletion(writer.attempt_id, "failed", True),
    )
    raw = json.loads(ledger.read_text(encoding="utf-8"))
    writer_failure = raw["attempts"][0]["history"][-1]
    expert_reservation = raw["attempts"][1]["history"][0]
    writer_failure["sequence"], expert_reservation["sequence"] = (
        expert_reservation["sequence"],
        writer_failure["sequence"],
    )
    raw["attempts"][0]["sequence"] = writer_failure["sequence"]
    raw["attempts"][1]["sequence"] = expert_reservation["sequence"]
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
            AttemptCompletion(writer.attempt_id, "failed", True),
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
        AttemptCompletion(
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
        AttemptCompletion(
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
            AttemptCompletion(
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
            AttemptCompletion(expert.attempt_id, "completed", False),
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
                AttemptCompletion(expert.attempt_id, "completed", True),
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
            AttemptCompletion(
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
        AttemptCompletion(expert.attempt_id, "completed", True),
    )
    record_provider_completion(
        ledger,
        protocol,
        AttemptCompletion(
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
    incomplete = AttemptCompletion(
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
        AttemptCompletion(
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
        AttemptCompletion(
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
        AttemptCompletion(
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
        AttemptCompletion(rereview.attempt_id, "completed", True),
    )
    record_provider_completion(
        ledger,
        protocol,
        AttemptCompletion(
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
            AttemptCompletion(
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
            AttemptCompletion(
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
            AttemptCompletion(
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
        AttemptCompletion(
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
            AttemptCompletion(rereview.attempt_id, "completed", True),
        )
    record_provider_completion(
        ledger,
        protocol,
        AttemptCompletion(
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
        AttemptCompletion(
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
            AttemptCompletion(
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
            AttemptCompletion(
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
        AttemptCompletion(
            experts[0].attempt_id,
            "completed",
            True,
            finding_digest=_digest("d"),
        ),
    )
    record_provider_completion(
        ledger,
        protocol,
        AttemptCompletion(experts[1].attempt_id, "completed", True),
    )
    record_provider_completion(
        ledger,
        protocol,
        AttemptCompletion(
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
        AttemptCompletion(
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
            AttemptCompletion(
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
        AttemptCompletion(
            experts[0].attempt_id,
            "completed",
            True,
            finding_digest=_digest("d"),
        ),
    )
    record_provider_completion(
        ledger,
        protocol,
        AttemptCompletion(experts[1].attempt_id, "completed", True),
    )
    record_provider_completion(
        ledger,
        protocol,
        AttemptCompletion(
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
    raw["attempts"][2]["sequence"] = cross_completion["sequence"]
    raw["attempts"][0]["sequence"] = writer_repair["sequence"]
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
        AttemptCompletion(writer.attempt_id, "failed", True),
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
            AttemptCompletion(
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
            AttemptCompletion(
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
            AttemptCompletion(expert.attempt_id, "technical_failure", False),
        )
        return writer, expert
    record_provider_completion(
        ledger,
        protocol,
        AttemptCompletion(
            expert.attempt_id,
            "completed",
            True,
            finding_digest=_digest("d"),
        ),
    )
    record_provider_completion(
        ledger,
        protocol,
        AttemptCompletion(
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
        AttemptCompletion(
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
        AttemptCompletion(rereview.attempt_id, "technical_failure", False),
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
            AttemptCompletion(
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


def _task12_provider_attempt(
    attempt: dict[str, object], *, session: str, token_seed: int = 1
) -> dict[str, object]:
    return {
        "attempt_id": attempt["attempt_id"],
        "kind": attempt["kind"],
        "effective_kind": attempt["effective_kind"],
        "status": attempt["status"],
        "content_produced": attempt["content_produced"],
        "terminal": attempt["terminal"],
        "child_session": session,
        "token_usage": {
            "input_tokens": token_seed,
            "cached_input_tokens": token_seed + 1,
            "output_tokens": token_seed + 2,
            "reasoning_output_tokens": token_seed + 3,
        },
    }


def _task12_receipt(
    protocol,
    ledger: Path,
    *,
    run_id: str = "P:requirement-contract-ambiguity",
) -> dict[str, object]:
    run = next(row for row in protocol.run_matrix if row.run_id == run_id)
    raw_ledger = json.loads(ledger.read_text(encoding="utf-8"))
    attempts = [
        _task12_provider_attempt(
            attempt, session=f"session-{index}", token_seed=index
        )
        for index, attempt in enumerate(
            (item for item in raw_ledger["attempts"] if item["run_id"] == run_id),
            start=1,
        )
    ]
    token_usage = {
        key: sum(attempt["token_usage"][key] for attempt in attempts)
        for key in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
        )
    }
    candidate = raw_ledger["attempts"][0].get("candidate_digest") or _digest("b")
    return {
        "schema": "ai-sdlc-v2-benefit-run-receipt/v2",
        "protocol_sha256": canonical_protocol_digest(protocol),
        "run_id": run.run_id,
        "arm": run.arm,
        "fixture": run.fixture,
        "order": run.position,
        "status": "completed",
        "failure_classification": "none",
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
            "started_at": "2026-08-18T00:00:00Z",
            "ended_at": "2026-08-18T00:00:21Z",
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
            "needs_operator": False,
            "evidence_completeness": 1.0,
            "setup_artifact_bytes": 1,
            "governance_artifact_bytes": 2,
            "total_artifact_bytes": 3,
        },
        "human_events": [],
        "automated_events": [],
        "command_evidence": [
            {
                "command": "pytest",
                "exit_code": 0,
                "stdout_sha256": _digest("7"),
                "stderr_sha256": _digest("8"),
            }
        ],
        "changed_files": ["benchmark-task/result.txt"],
        "final_candidate_tree_sha256": candidate,
        "loop": {
            "close": {
                "state": "not_applicable",
                "command": "not_applicable",
                "exit_code": 0,
                "review_digest": _digest("0"),
                "close_digest": _digest("0"),
            },
            "expert_callbacks": [],
        },
        "external_evaluator": {
            "candidate_tree_sha256": candidate,
            "result_sha256": _digest("6"),
            "external_verified_delivery": True,
            "weighted_ac_coverage": 1.0,
            "severe_defect_escape_count": 0,
            "invalid_completion": False,
        },
    }


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
        AttemptCompletion(
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
        AttemptCompletion(
            expert.attempt_id, "completed", True, finding_digest=_digest("d")
        ),
    )
    record_provider_completion(
        ledger,
        protocol,
        AttemptCompletion(
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
        AttemptCompletion(
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
        AttemptCompletion(rereview.attempt_id, "completed", True),
    )
    record_provider_completion(
        ledger,
        protocol,
        AttemptCompletion(
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
    receipt["loop"] = {
        "close": {
            "state": "closed",
            "command": "ai-sdlc loop close --expect-review-digest digest",
            "exit_code": 0,
            "review_digest": _digest("a"),
            "close_digest": _digest("9"),
        },
        "expert_callbacks": [
            {
                "role": "primary",
                "reason": "design-contract risk",
                "status": "pass",
                "expert_attempt_id": expert.attempt_id,
                "rereview_attempt_id": rereview.attempt_id,
                "parent_digest": _digest("a"),
                "candidate_digest": _digest("b"),
                "child_session": "session-2",
                "finding_count": 1,
                "severe_finding_count": 1,
                "finding_digest": _digest("d"),
                "repair_digest": _digest("e"),
                "repaired_candidate_digest": _digest("c"),
                "rereview_digest": _digest("c"),
                "review_command": "ai-sdlc loop review --expect-digest x --read-path y",
                "review_exit_code": 0,
                "rereview_command": "ai-sdlc loop review --expect-digest x --read-path y",
                "rereview_exit_code": 0,
                "rereview_raw_output_sha256": _digest("a"),
                "snapshot_sha256": _digest("1"),
                "input_sha256": _digest("2"),
                "raw_output_sha256": _digest("3"),
                "parent_tree_before_sha256": _digest("4"),
                "parent_tree_after_sha256": _digest("4"),
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
        AttemptCompletion(writer.attempt_id, "technical_failure", False),
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
        AttemptCompletion(
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
        AttemptCompletion(other.attempt_id, "failed", False),
    )
    raw = json.loads(ledger.read_text(encoding="utf-8"))
    receipt["provider_attempts"].append(
        _task12_provider_attempt(raw["attempts"][-1], session="cross-run")
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
        lambda receipt: receipt["loop"]["expert_callbacks"][0].pop(
            "review_command"
        ),
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
        lambda receipt: receipt["loop"]["expert_callbacks"][0].update(
            {"rereview_attempt_id": "attempt-999"}
        ),
        lambda receipt: receipt["loop"]["expert_callbacks"][0].pop(
            "rereview_raw_output_sha256"
        ),
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
        AttemptCompletion(expert.attempt_id, "completed", True),
    )
    record_provider_completion(
        ledger,
        protocol,
        AttemptCompletion(
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
    receipt["loop"] = {
        "close": {
            "state": "closed",
            "command": "ai-sdlc loop close --expect-review-digest digest",
            "exit_code": 0,
            "review_digest": _digest("a"),
            "close_digest": _digest("9"),
        },
        "expert_callbacks": [
            {
                "role": "primary",
                "reason": "design-contract risk",
                "status": "pass",
                "expert_attempt_id": expert.attempt_id,
                "parent_digest": _digest("a"),
                "candidate_digest": _digest("b"),
                "child_session": "session-2",
                "finding_count": 0,
                "severe_finding_count": 0,
                "review_command": "ai-sdlc loop review --expect-digest x --read-path y",
                "review_exit_code": 0,
                "snapshot_sha256": _digest("1"),
                "input_sha256": _digest("2"),
                "raw_output_sha256": _digest("3"),
                "parent_tree_before_sha256": _digest("4"),
                "parent_tree_after_sha256": _digest("4"),
            }
        ],
    }
    assert not verify_receipt(receipt, protocol, ledger)


def _task12_summary(protocol) -> dict[str, object]:
    return {
        "schema": "ai-sdlc-v2-benefit-summary/v2",
        "protocol_sha256": canonical_protocol_digest(protocol),
        "runs": [
            {
                "run_id": run.run_id,
                "arm": run.arm,
                "fixture": run.fixture,
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
    receipt["command_evidence"][0]["command"] = value
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
    protocol, ledger, receipt = _task12_completed_p_run(tmp_path)
    receipt["command_evidence"][0]["command"] = value
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
        AttemptCompletion(
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
