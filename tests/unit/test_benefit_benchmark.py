"""Tests for the frozen AI-SDLC v2 benefit benchmark contract."""

import json
import multiprocessing
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from ai_sdlc.benefit_benchmark import (
    AttemptCompletion,
    AttemptRequest,
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


def _reserve_in_process(ledger: str, run_id: str, queue: multiprocessing.Queue) -> None:
    try:
        result = reserve_provider_attempt(Path(ledger), AttemptRequest(run_id, "writer"))
        queue.put(result.attempt_id)
    except ValueError as error:
        queue.put(str(error))


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
    assert not validate_protocol(protocol, REPO_ROOT)

    positions_by_arm: dict[str, list[int]] = {arm: [] for arm in protocol.arms}
    for run in protocol.run_matrix:
        positions_by_arm[run.arm].append(run.position)
    assert {arm: sum(positions) / len(positions) for arm, positions in positions_by_arm.items()} == {
        arm: 3 for arm in protocol.arms
    }


@pytest.mark.parametrize(
    ("change", "expected_code"),
    [
        (lambda protocol: replace(protocol, arms=("P",)), "protocol.arms"),
        (lambda protocol: replace(protocol, fixtures=("fixture",)), "protocol.fixtures"),
        (lambda protocol: replace(protocol, run_matrix=protocol.run_matrix[:-1]), "protocol.matrix"),
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
    assert expected_code in {issue.code for issue in validate_protocol(protocol, REPO_ROOT)}


def test_reservation_fails_closed_at_thirty_three_attempts(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        json.dumps({"schema": "ai-sdlc-v2-benefit-attempt-ledger/v1", "attempts_started": 33, "attempts": [{"attempt_id": f"attempt-{index:03d}"} for index in range(1, 34)]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="33"):
        reserve_provider_attempt(ledger, AttemptRequest(run_id="overflow", kind="technical_retry"))


@pytest.mark.parametrize(
    "attempt_request",
    [
        AttemptRequest(run_id="P:requirement-contract-ambiguity", kind="technical_retry", retry_reason="content"),
        AttemptRequest(run_id="P:requirement-contract-ambiguity", kind="content_retry"),
    ],
)
def test_reservation_rejects_content_retries(
    tmp_path: Path, attempt_request: AttemptRequest
) -> None:
    with pytest.raises(ValueError, match="content"):
        reserve_provider_attempt(tmp_path / "ledger.json", attempt_request)


def test_reservation_rejects_a_fourth_technical_retry(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"schema": "ai-sdlc-v2-benefit-attempt-ledger/v1", "attempts_started": 3, "attempts": [{"attempt_id": f"attempt-{index:03d}", "kind": "technical_retry"} for index in range(1, 4)]}), encoding="utf-8")
    with pytest.raises(ValueError, match="technical retry"):
        reserve_provider_attempt(ledger, AttemptRequest(run_id="P:requirement-contract-ambiguity", kind="technical_retry", retry_reason="transport", retry_of_attempt_id="attempt-001"))


def test_reservation_rejects_duplicate_writer_run_replacement(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.json"
    request = AttemptRequest(run_id="A11:multi-tenant-security-review", kind="writer", arm="A11")
    reserve_provider_attempt(ledger, request)
    with pytest.raises(ValueError, match="replacement"):
        reserve_provider_attempt(ledger, request)


def test_completion_requires_a_prior_reservation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="reservation"):
        record_provider_completion(
            tmp_path / "ledger.json", AttemptCompletion(attempt_id="missing", status="failed")
        )


def test_topology_rejects_extra_roles_rereviews_and_non_a11_experts(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.json"
    expert = reserve_provider_attempt(
        ledger, AttemptRequest(run_id="A11:multi-tenant-security-review", kind="writer", arm="A11")
    )
    reserve_provider_attempt(
        ledger,
        AttemptRequest(run_id="A11:multi-tenant-security-review", kind="primary_expert", arm="A11", role="primary", parent_digest=_digest(), candidate_digest=_digest("b")),
    )
    with pytest.raises(ValueError, match="expert"):
        reserve_provider_attempt(
            ledger,
            AttemptRequest(run_id="A11:multi-tenant-security-review", kind="expert_rereview", arm="A11", parent_attempt_id=expert.attempt_id, role="primary", parent_digest=_digest(), candidate_digest=_digest("b")),
        )
    with pytest.raises(ValueError, match="role"):
        reserve_provider_attempt(
            ledger, AttemptRequest(run_id="A11:multi-tenant-security-review", kind="replacement_writer", arm="A11")
        )
    with pytest.raises(ValueError, match="A11"):
        reserve_provider_attempt(
            ledger, AttemptRequest(run_id="P:multi-tenant-security-review", kind="primary_expert", arm="P")
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


def _receipt() -> dict[str, object]:
    return {
        "schema": "ai-sdlc-v2-benefit-run-receipt/v1",
        "run_id": "P:requirement-contract-ambiguity",
        "arm": "P",
        "fixture": "requirement-contract-ambiguity",
        "order": 1,
        "status": "completed",
        "failure_classification": "none",
        "digests": {"fixture_sha256": _digest(), "candidate_tree_sha256": _digest("b")},
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
        "token_usage": {
            "input_tokens": 1,
            "cached_input_tokens": 0,
            "output_tokens": 2,
            "reasoning_output_tokens": 3,
        },
        "human_events": [],
        "command_evidence": [
            {"command": "pytest", "exit_code": 0, "stdout_sha256": _digest(), "stderr_sha256": _digest()}
        ],
        "changed_files": ["benchmark-task/result.txt"],
        "final_candidate_tree_sha256": _digest("b"),
        "loop": {"close": {"state": "not_applicable"}},
        "external_evaluator": {"candidate_tree_sha256": _digest("b"), "result_sha256": _digest("c")},
    }


def test_receipt_verification_accepts_a_complete_public_receipt() -> None:
    assert not verify_receipt(_receipt())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda receipt: receipt.pop("failure_classification"),
        lambda receipt: receipt["changed_files"].append("/private/secret.txt"),
        lambda receipt: receipt["digests"].update({"fixture_sha256": "missing"}),
        lambda receipt: receipt.update({"final_candidate_tree_sha256": _digest("d")}),
        lambda receipt: receipt["token_usage"].update({"output_tokens": "two"}),
        lambda receipt: receipt["timings"].pop("review_wall_seconds"),
        lambda receipt: receipt["human_events"].append({"type": "approval_service_event", "seconds": 1}),
        lambda receipt: receipt["timings"].update({"end_to_end_wall_seconds": 20}),
    ],
)
def test_receipt_verification_rejects_incomplete_or_misclassified_evidence(mutate) -> None:
    receipt = _receipt()
    mutate(receipt)
    assert verify_receipt(receipt)


def test_a11_receipt_requires_complete_expert_callback_evidence_before_close() -> None:
    receipt = _receipt()
    receipt.update({"run_id": "A11:multi-tenant-security-review", "arm": "A11", "fixture": "multi-tenant-security-review"})
    receipt["loop"] = {"close": {"state": "closed"}, "expert_callbacks": []}
    assert any(issue.code == "receipt.a11.close" for issue in verify_receipt(receipt))


def test_receipt_verification_applies_the_frozen_closed_json_schema() -> None:
    receipt = _receipt()
    receipt["unregistered_field"] = "must fail closed"
    assert any(issue.code == "receipt.schema" for issue in verify_receipt(receipt))


def test_summary_verification_keeps_summary_and_receipt_boundaries_closed() -> None:
    protocol = load_protocol(PROTOCOL_PATH)
    summary = {
        "schema": "ai-sdlc-v2-benefit-summary/v1",
        "protocol_sha256": canonical_protocol_digest(protocol),
        "runs": [
            {"run_id": run.run_id, "arm": run.arm, "fixture": run.fixture, "receipt_sha256": _digest(f"{index:x}"[-1])}
            for index, run in enumerate(protocol.run_matrix)
        ],
        "metrics": {"external_verified_delivery_count": {"P": 0, "S": 0, "A11": 0}},
    }
    assert not verify_summary(summary, protocol)
    summary["raw_provider_jsonl"] = "/private/provider.jsonl"
    assert verify_summary(summary, protocol)


def test_static_schemas_and_offline_cli_validation_are_available() -> None:
    benchmark_root = PROTOCOL_PATH.parent
    for schema_name in ("run-receipt.schema.json", "summary.schema.json"):
        schema = json.loads((benchmark_root / "schemas" / schema_name).read_text(encoding="utf-8"))
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
    assert '"issues": []' in result.stdout


def test_fix_round_protocol_freezes_exact_rows_and_execution_locks() -> None:
    protocol = load_protocol(PROTOCOL_PATH)
    assert protocol.fixtures == (
        "requirement-contract-ambiguity",
        "frontend-recovery-delivery",
        "multi-tenant-security-review",
    )
    assert protocol.run_matrix[0].run_id == "P:requirement-contract-ambiguity"
    assert protocol.run_matrix[-1].run_id == "A00:multi-tenant-security-review"
    assert protocol.execution_lock.codex_version == "0.147.0"
    assert protocol.execution_lock.writer_timeout_seconds == 1800
    assert len(canonical_protocol_digest(protocol)) == 64


def test_fix_round_cross_process_reservations_are_unique_and_not_lost(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.json"
    queue: multiprocessing.Queue = multiprocessing.Queue()
    processes = [
        multiprocessing.Process(target=_reserve_in_process, args=(str(ledger), "P:requirement-contract-ambiguity", queue)),
        multiprocessing.Process(target=_reserve_in_process, args=(str(ledger), "S:requirement-contract-ambiguity", queue)),
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    assert {queue.get(timeout=1), queue.get(timeout=1)} == {"attempt-001", "attempt-002"}
    persisted = json.loads(ledger.read_text(encoding="utf-8"))
    assert persisted["attempts_started"] == 2
    assert len(persisted["attempts"]) == 2


def test_fix_round_retry_and_rereview_require_canonical_parent_state(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.json"
    writer = reserve_provider_attempt(ledger, AttemptRequest("A11:multi-tenant-security-review", "writer"))
    with pytest.raises(ValueError, match="terminated"):
        reserve_provider_attempt(
            ledger,
            AttemptRequest("A11:multi-tenant-security-review", "technical_retry", retry_of_attempt_id=writer.attempt_id, retry_reason="transport"),
        )
    record_provider_completion(ledger, AttemptCompletion(writer.attempt_id, "technical_failure", False))
    retry = reserve_provider_attempt(
        ledger,
        AttemptRequest("A11:multi-tenant-security-review", "technical_retry", retry_of_attempt_id=writer.attempt_id, retry_reason="transport"),
    )
    assert retry.attempt_id == "attempt-002"
    expert = reserve_provider_attempt(
        ledger,
        AttemptRequest("A11:multi-tenant-security-review", "primary_expert", role="primary", parent_digest=_digest(), candidate_digest=_digest("b")),
    )
    record_provider_completion(ledger, AttemptCompletion(expert.attempt_id, "completed"))
    reserve_provider_attempt(
        ledger,
        AttemptRequest("A11:multi-tenant-security-review", "expert_rereview", role="primary", parent_attempt_id=expert.attempt_id, parent_digest=_digest(), candidate_digest=_digest("b")),
    )
    with pytest.raises(ValueError, match="rereview"):
        reserve_provider_attempt(
            ledger,
            AttemptRequest("A11:multi-tenant-security-review", "expert_rereview", role="primary", parent_attempt_id=expert.attempt_id, parent_digest=_digest(), candidate_digest=_digest("b")),
        )
