"""Deterministic validation primitives for the AI-SDLC v2 benefit benchmark."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from urllib.parse import unquote, urlsplit


@dataclass(frozen=True)
class BenchmarkIssue:
    code: str
    message: str


@dataclass(frozen=True)
class BenchmarkRun:
    run_id: str
    arm: str
    fixture: str
    position: int


@dataclass(frozen=True)
class AttemptBudget:
    limit: int
    normal_sessions: int
    max_expert_rereviews: int
    max_pre_output_retries: int
    reserved_security_slots: int


@dataclass(frozen=True)
class BenchmarkProtocol:
    schema: str
    arms: tuple[str, ...]
    fixtures: tuple[str, ...]
    run_matrix: tuple[BenchmarkRun, ...]
    attempt_budget: AttemptBudget
    execution_lock: ExecutionLock
    canonical_bytes: bytes


@dataclass(frozen=True)
class ExecutionLock:
    ai_sdlc_version: str
    ai_sdlc_commit: str
    source_tree_sha: str
    superpowers_commit: str
    benchmark_commit: str
    fixture_tree_sha256: str
    codex_version: str
    model: str
    reasoning_effort: str
    runner_script_sha256: str
    writer_timeout_seconds: int
    expert_timeout_seconds: int
    fixture_commitment: str
    evidence_contract_sha256: str
    evidence_contract_commitment: str


@dataclass(frozen=True)
class AttemptRequest:
    """A logical Provider session reservation requested by the offline runner."""

    run_id: str
    kind: str
    arm: str | None = None
    retry_reason: str | None = None
    retry_of_attempt_id: str | None = None
    parent_attempt_id: str | None = None
    role: str | None = None
    parent_digest: str | None = None
    candidate_digest: str | None = None
    finding_digest: str | None = None
    repair_digest: str | None = None


@dataclass(frozen=True)
class AttemptReservation:
    attempt_id: str
    attempts_started: int
    request: AttemptRequest


@dataclass(frozen=True)
class AttemptCompletion:
    attempt_id: str
    status: str
    content_produced: bool = False
    candidate_digest: str | None = None
    finding_digest: str | None = None
    repair_digest: str | None = None
    close_digest: str | None = None
    child_session: str | None = None
    token_usage: Mapping[str, int] | None = None
    raw_provider_output_sha256: str | None = None


_PROTOCOL_KEYS = {
    "schema",
    "arms",
    "fixtures",
    "run_matrix",
    "attempt_budget",
    "execution_lock",
}
_RUN_KEYS = {"run_id", "arm", "fixture", "position"}
_LOCK_KEYS = {
    "ai_sdlc_version",
    "ai_sdlc_commit",
    "source_tree_sha",
    "superpowers_commit",
    "benchmark_commit",
    "fixture_tree_sha256",
    "codex_version",
    "model",
    "reasoning_effort",
    "runner_script_sha256",
    "writer_timeout_seconds",
    "expert_timeout_seconds",
    "fixture_commitment",
    "evidence_contract_sha256",
    "evidence_contract_commitment",
}
_EXPECTED_LOCK = {
    "ai_sdlc_version": "2.0.0",
    "ai_sdlc_commit": "737bda39e05c53450e180a20581b7b7a70db9cf0",
    "source_tree_sha": "3db58121e228a7a1c4c6b760c535d6df1ffdbe84",
    "superpowers_commit": "b36e0829c6d0140e93cfef2ca599b1b07d4a7797",
    "benchmark_commit": "7fc2366b8530265d58b1874e781b0b7274615d94",
    "codex_version": "0.147.0",
    "model": "gpt-5.6-sol",
    "reasoning_effort": "high",
    "runner_script_sha256": "134063e133f0b4244fa3b251acf973d4fe4b4aeeacbdc135211bf480f59f1477",
    "writer_timeout_seconds": 1800,
    "expert_timeout_seconds": 900,
}
_BUDGET_KEYS = {
    "limit",
    "normal_sessions",
    "max_expert_rereviews",
    "max_pre_output_retries",
    "reserved_security_slots",
}
_ARMS = ("P", "S", "A00", "A10", "A11")
_FIXTURES = (
    "requirement-contract-ambiguity",
    "frontend-recovery-delivery",
    "multi-tenant-security-review",
)
_FIXTURE_LOOP_TYPES = {
    "requirement-contract-ambiguity": "design-contract",
    "frontend-recovery-delivery": "implementation",
    "multi-tenant-security-review": "implementation",
}
_SCHEDULE = (
    ("P", _FIXTURES[0], 1),
    ("S", _FIXTURES[0], 2),
    ("A00", _FIXTURES[0], 3),
    ("A10", _FIXTURES[0], 4),
    ("A11", _FIXTURES[0], 5),
    ("A00", _FIXTURES[1], 1),
    ("A10", _FIXTURES[1], 2),
    ("A11", _FIXTURES[1], 3),
    ("P", _FIXTURES[1], 4),
    ("S", _FIXTURES[1], 5),
    ("A11", _FIXTURES[2], 1),
    ("S", _FIXTURES[2], 2),
    ("A10", _FIXTURES[2], 3),
    ("P", _FIXTURES[2], 4),
    ("A00", _FIXTURES[2], 5),
)
_LEDGER_SCHEMA = "ai-sdlc-v2-benefit-attempt-ledger/v6"
_LEDGER_KEYS = {
    "schema",
    "protocol_sha256",
    "attempts_started",
    "attempts",
    "runs",
}
_ATTEMPT_KEYS = {
    "attempt_id",
    "run_id",
    "kind",
    "effective_kind",
    "sequence",
    "arm",
    "retry_reason",
    "retry_of_attempt_id",
    "parent_attempt_id",
    "role",
    "parent_digest",
    "candidate_digest",
    "finding_digest",
    "repair_digest",
    "close_digest",
    "status",
    "content_produced",
    "terminal",
    "recorded_at",
    "child_session",
    "token_usage",
    "raw_provider_output_sha256",
    "history",
    "service_events",
}
_EVENT_KEYS = {
    "sequence",
    "status",
    "content_produced",
    "candidate_digest",
    "finding_digest",
    "repair_digest",
    "close_digest",
    "terminal",
    "recorded_at",
    "child_session",
    "token_usage",
    "raw_provider_output_sha256",
}
_ATTEMPT_KINDS = {
    "writer",
    "primary_expert",
    "cross_risk_expert",
    "expert_rereview",
    "technical_retry",
}
_TERMINAL_STATUSES = {
    "completed",
    "technical_failure",
    "failed",
    "timeout",
    "needs_operator",
    "budget_exhausted",
}
_RUN_PHASES = (
    "setup",
    "framework_init",
    "provider",
    "post_provider",
    "review",
    "evaluation",
)
_POST_CONTENT_TERMINAL_STATUSES = _TERMINAL_STATUSES - {"technical_failure"}
_TRANSITIONS = {
    "writer": {
        "reserved": {"candidate_ready", *_TERMINAL_STATUSES},
        "candidate_ready": {
            "candidate_ready",
            "review_pending",
            *_POST_CONTENT_TERMINAL_STATUSES,
        },
        "review_pending": {"candidate_ready", *_POST_CONTENT_TERMINAL_STATUSES},
    },
    "primary_expert": {"reserved": _TERMINAL_STATUSES},
    "cross_risk_expert": {"reserved": _TERMINAL_STATUSES},
    "expert_rereview": {"reserved": _TERMINAL_STATUSES},
    "technical_retry": {"reserved": _TERMINAL_STATUSES},
}
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_SECRET_FIELD_NAMES = {
    "api_key",
    "api-key",
    "secret",
    "password",
    "authorization",
    "access_token",
    "access-token",
    "auth_token",
    "auth-token",
    "gh_token",
    "gh-token",
    "token",
}
_SECRET_TOKEN = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"AKIA[0-9A-Z]{16}|Bearer(?:\s+|%20|\+)+(?!REDACTED(?:\b|$))[^&#\s,;)]+)",
    re.I,
)
_PRIVATE_PATH = re.compile(
    r"(?:file://[^\s'\"]+|[A-Za-z]:[\\/][^\s'\"]+|"
    r"(?<!:)(?:\\\\|//)[A-Za-z0-9._-]+[\\/][^\s'\"]+|"
    r"(?<![A-Za-z0-9_/-])/(?!/)[^\s'\"]+)",
    re.I,
)
_HTTP_URI = re.compile(r"https?://[^\s'\";,()]+", re.I)
_JSON_TYPES = {
    "object",
    "array",
    "string",
    "number",
    "integer",
    "boolean",
    "null",
}
_FAILURE_CLASSIFICATIONS = {
    "completed": {"none"},
    "failed": {
        "provider_pre_output_failure",
        "writer_failure",
        "expert_failure",
        "evaluation_failure",
        "evidence_failure",
    },
    "timeout": {"timeout"},
    "needs_operator": {"expert_conflict"},
    "budget_exhausted": {"provider_budget_exhausted"},
}
_BENCHMARK_ROOT = (
    Path(__file__).resolve().parents[2] / "benchmarks" / "ai-sdlc-v2-benefits"
)


def canonical_protocol_digest(protocol: BenchmarkProtocol) -> str:
    return sha256(protocol.canonical_bytes).hexdigest()


def start_run(
    ledger_path: Path,
    protocol: BenchmarkProtocol,
    evidence_contract_path: Path,
    *,
    run_id: str,
) -> None:
    """Create a run in setup before any Provider reservation."""
    protocol_digest = _require_executable_protocol(protocol)
    contract = _load_evidence_contract(evidence_contract_path, protocol)
    _contract_run(contract, run_id)
    with _ledger_lock(ledger_path):
        ledger = _load_ledger(
            ledger_path,
            protocol_digest,
            protocol.attempt_budget,
            protocol.execution_lock.evidence_contract_sha256,
        )
        runs = ledger["runs"]
        if not isinstance(runs, dict) or run_id in runs:
            raise ValueError("run start requires a new frozen run identity")
        now = _now_rfc3339()
        runs[run_id] = {
            "run_id": run_id,
            "run_started_at": now,
            "current_phase": "setup",
            "phase_events": [
                {"phase": "setup", "started_at": now, "ended_at": None}
            ],
            "sealed_evidence": None,
        }
        _validate_run_registry(
            runs,
            ledger["attempts"],
            evidence_contract_sha256=protocol.execution_lock.evidence_contract_sha256,
        )
        _atomic_write_json(ledger_path, ledger)


def transition_run_phase(
    ledger_path: Path,
    protocol: BenchmarkProtocol,
    evidence_contract_path: Path,
    *,
    run_id: str,
    next_phase: str,
) -> None:
    """Atomically close the current phase and open its fixed successor."""
    protocol_digest = _require_executable_protocol(protocol)
    _load_evidence_contract(evidence_contract_path, protocol)
    with _ledger_lock(ledger_path):
        ledger = _load_ledger(
            ledger_path,
            protocol_digest,
            protocol.attempt_budget,
            protocol.execution_lock.evidence_contract_sha256,
        )
        run = _active_run_record(ledger, run_id)
        current = run.get("current_phase")
        try:
            expected = _RUN_PHASES[_RUN_PHASES.index(str(current)) + 1]
        except (ValueError, IndexError) as error:
            raise ValueError("run phase has no transition successor") from error
        if next_phase != expected:
            raise ValueError("run phase transition violates the fixed order")
        run_attempts = [
            attempt
            for attempt in ledger["attempts"]
            if isinstance(attempt, Mapping) and attempt.get("run_id") == run_id
        ]
        if current == "provider" and (
            not run_attempts
            or any(attempt.get("terminal") is not True for attempt in run_attempts)
        ):
            raise ValueError("provider phase can close only after all attempts terminate")
        events = run.get("phase_events")
        if not isinstance(events, list) or not events:
            raise ValueError("run phase state is invalid")
        now = _now_rfc3339()
        closed = {**events[-1], "ended_at": now}
        opened = {"phase": next_phase, "started_at": now, "ended_at": None}
        events[-1] = closed
        events.append(opened)
        run["current_phase"] = next_phase
        _validate_run_registry(
            ledger["runs"],
            ledger["attempts"],
            evidence_contract_sha256=protocol.execution_lock.evidence_contract_sha256,
        )
        _atomic_write_json(ledger_path, ledger)


def reserve_provider_attempt(
    ledger_path: Path,
    protocol: BenchmarkProtocol,
    request: AttemptRequest,
    evidence_contract_path: Path,
) -> AttemptReservation:
    """Atomically reserve an allowed logical Provider attempt before it starts."""
    protocol_digest = _require_executable_protocol(protocol)
    _load_evidence_contract(evidence_contract_path, protocol)
    with _ledger_lock(ledger_path):
        ledger = _load_ledger(
            ledger_path,
            protocol_digest,
            protocol.attempt_budget,
            protocol.execution_lock.evidence_contract_sha256,
        )
        _validate_reservation_request(ledger["attempts"], request)
        runs = ledger["runs"]
        if not isinstance(runs, dict):
            raise ValueError("attempt ledger has invalid run registry")
        run = runs.get(request.run_id)
        if not isinstance(run, Mapping):
            raise ValueError("run must be started before Provider reservation")
        if run.get("sealed_evidence") is not None:
            raise ValueError("sealed run rejects late attempts and events")
        if run.get("current_phase") != "provider":
            raise ValueError("Provider reservation is allowed only in provider phase")
        if ledger["attempts_started"] >= protocol.attempt_budget.limit:
            raise ValueError(
                f"Provider attempt budget of {protocol.attempt_budget.limit} is exhausted"
            )
        attempts_started = ledger["attempts_started"] + 1
        attempt_id = f"attempt-{attempts_started:03d}"
        ledger["attempts_started"] = attempts_started
        new_attempt = _new_attempt(attempt_id, request, ledger["attempts"])
        _require_public_ledger_value(new_attempt, "Provider reservation")
        ledger["attempts"].append(new_attempt)
        _validate_attempt_ledger_invariants(
            ledger["attempts"], protocol.attempt_budget
        )
        _validate_run_registry(
            runs,
            ledger["attempts"],
            evidence_contract_sha256=protocol.execution_lock.evidence_contract_sha256,
        )
        _atomic_write_json(ledger_path, ledger)
        return AttemptReservation(attempt_id, attempts_started, request)


def record_provider_completion(
    ledger_path: Path,
    protocol: BenchmarkProtocol,
    completion: AttemptCompletion,
    evidence_contract_path: Path,
) -> None:
    """Atomically record one allowed Provider attempt state transition."""
    protocol_digest = _require_executable_protocol(protocol)
    _load_evidence_contract(evidence_contract_path, protocol)
    with _ledger_lock(ledger_path):
        ledger = _load_ledger(
            ledger_path,
            protocol_digest,
            protocol.attempt_budget,
            protocol.execution_lock.evidence_contract_sha256,
        )
        for attempt in ledger["attempts"]:
            if attempt["attempt_id"] == completion.attempt_id:
                run = _active_run_record(ledger, str(attempt["run_id"]))
                if run.get("current_phase") != "provider":
                    raise ValueError(
                        "Provider completion is allowed only in provider phase"
                    )
                _apply_attempt_transition(ledger["attempts"], attempt, completion)
                _validate_persisted_attempt(attempt, completion.attempt_id)
                _validate_attempt_ledger_invariants(
                    ledger["attempts"], protocol.attempt_budget
                )
                _validate_run_registry(
                    ledger["runs"],
                    ledger["attempts"],
                    evidence_contract_sha256=protocol.execution_lock.evidence_contract_sha256,
                )
                _atomic_write_json(ledger_path, ledger)
                return
        raise ValueError("Provider completion requires a prior reservation")


def start_service_transaction(
    ledger_path: Path,
    protocol: BenchmarkProtocol,
    evidence_contract_path: Path,
    *,
    attempt_id: str,
    event_type: str,
    transaction_id: str,
) -> None:
    """Start one service transaction using only the core clock."""
    protocol_digest = _require_executable_protocol(protocol)
    contract = _load_evidence_contract(evidence_contract_path, protocol)
    with _ledger_lock(ledger_path):
        ledger = _load_ledger(
            ledger_path,
            protocol_digest,
            protocol.attempt_budget,
            protocol.execution_lock.evidence_contract_sha256,
        )
        attempt = _attempt_by_id(ledger["attempts"], attempt_id)
        if attempt is None or attempt.get("terminal") is True:
            raise ValueError("service transaction requires an active attempt")
        run = _active_run_record(ledger, str(attempt.get("run_id")))
        if run.get("current_phase") != "provider":
            raise ValueError("service transaction is allowed only in provider phase")
        allowed = _contract_run(contract, str(attempt.get("run_id")))[
            "allowed_automated_event_types"
        ]
        if event_type not in allowed:
            raise ValueError("service transaction type is not allowed by contract")
        service_events = attempt.get("service_events")
        if not isinstance(service_events, list) or any(
            isinstance(item, Mapping)
            and item.get("transaction_id") == transaction_id
            for item in service_events
        ):
            raise ValueError("service transaction identity is duplicated")
        started_at = _now_rfc3339()
        event = {
            "type": event_type,
            "transaction_id": transaction_id,
            "attempt_id": attempt_id,
            "status": "started",
            "started_at": started_at,
            "ended_at": None,
            "latency_ms": None,
            "service_evidence_sha256": None,
            "evidence_sha256": None,
        }
        _validate_service_event(event)
        _require_public_ledger_value(event, "service transaction")
        service_events.append(event)
        _validate_attempt_ledger_invariants(
            ledger["attempts"], protocol.attempt_budget
        )
        _validate_run_registry(
            ledger["runs"],
            ledger["attempts"],
            evidence_contract_sha256=protocol.execution_lock.evidence_contract_sha256,
        )
        # Referencing the run here makes the active/sealed guard explicit.
        _ = run
        _atomic_write_json(ledger_path, ledger)


def record_service_transaction(
    ledger_path: Path,
    protocol: BenchmarkProtocol,
    evidence_contract_path: Path,
    *,
    attempt_id: str,
    event_type: str,
    transaction_id: str,
    evidence: Mapping[str, object],
) -> None:
    """Close one started service transaction using only the core clock."""
    protocol_digest = _require_executable_protocol(protocol)
    contract = _load_evidence_contract(evidence_contract_path, protocol)
    privacy_issues: list[BenchmarkIssue] = []
    _scan_public_value(evidence, "$", privacy_issues)
    if (
        privacy_issues
        or not transaction_id
        or not isinstance(evidence, Mapping)
        or not evidence
        or not _is_closed_json_value(evidence)
    ):
        raise ValueError("service transaction evidence is not public and closed")
    with _ledger_lock(ledger_path):
        ledger = _load_ledger(
            ledger_path,
            protocol_digest,
            protocol.attempt_budget,
            protocol.execution_lock.evidence_contract_sha256,
        )
        attempt = _attempt_by_id(ledger["attempts"], attempt_id)
        if attempt is None or attempt.get("terminal") is True:
            raise ValueError("service transaction requires an active attempt")
        run = _active_run_record(ledger, str(attempt.get("run_id")))
        if run.get("current_phase") != "provider":
            raise ValueError("service transaction is allowed only in provider phase")
        allowed = _contract_run(contract, str(attempt.get("run_id")))[
            "allowed_automated_event_types"
        ]
        if event_type not in allowed:
            raise ValueError("service transaction type is not allowed by contract")
        service_events = attempt.get("service_events")
        matches = [
            (index, event)
            for index, event in enumerate(service_events)
            if isinstance(event, Mapping)
            and event.get("transaction_id") == transaction_id
        ] if isinstance(service_events, list) else []
        if len(matches) != 1:
            raise ValueError("service transaction requires one prior start")
        event_index, started_event = matches[0]
        if (
            started_event.get("status") != "started"
            or started_event.get("type") != event_type
            or started_event.get("attempt_id") != attempt_id
        ):
            raise ValueError("service transaction start binding is invalid")
        started_at = started_event.get("started_at")
        ended_at = _now_rfc3339()
        started = _parse_rfc3339(started_at)
        ended = _parse_rfc3339(ended_at)
        if started is None or ended is None or ended < started:
            raise ValueError("service transaction clock is invalid")
        latency_ms = (ended - started).total_seconds() * 1000
        public_event = {
            "type": event_type,
            "started_at": started_at,
            "ended_at": ended_at,
            "latency_ms": latency_ms,
        }
        event = {
            "type": event_type,
            "transaction_id": transaction_id,
            "attempt_id": attempt_id,
            "status": "completed",
            "started_at": started_at,
            "ended_at": ended_at,
            "latency_ms": latency_ms,
            "service_evidence_sha256": sha256(
                json.dumps(
                    evidence,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "evidence_sha256": sha256(
                json.dumps(
                    public_event,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        }
        _validate_service_event(event)
        _require_public_ledger_value(event, "service transaction")
        service_events[event_index] = event
        _validate_attempt_ledger_invariants(
            ledger["attempts"], protocol.attempt_budget
        )
        _validate_run_registry(
            ledger["runs"],
            ledger["attempts"],
            evidence_contract_sha256=protocol.execution_lock.evidence_contract_sha256,
        )
        _atomic_write_json(ledger_path, ledger)


def seal_run_evidence(
    ledger_path: Path,
    protocol: BenchmarkProtocol,
    evidence_contract_path: Path,
    *,
    run_id: str,
    workspace_root: Path,
) -> None:
    """Seal one immutable run from contract, ledger clocks and actual files."""
    protocol_digest = _require_executable_protocol(protocol)
    contract = _load_evidence_contract(evidence_contract_path, protocol)
    with _ledger_lock(ledger_path):
        ledger = _load_ledger(
            ledger_path,
            protocol_digest,
            protocol.attempt_budget,
            protocol.execution_lock.evidence_contract_sha256,
        )
        run = _active_run_record(ledger, run_id)
        if run.get("current_phase") != "evaluation":
            raise ValueError("run evidence can be sealed only in evaluation phase")
        attempts = [
            item
            for item in ledger["attempts"]
            if isinstance(item, Mapping) and item.get("run_id") == run_id
        ]
        if not attempts or any(item.get("terminal") is not True for item in attempts):
            raise ValueError("run evidence requires every started run attempt terminal")
        recorded_at = _now_rfc3339()
        phase_events = run.get("phase_events")
        if (
            not isinstance(phase_events, list)
            or not phase_events
            or phase_events[-1].get("phase") != "evaluation"
            or phase_events[-1].get("ended_at") is not None
        ):
            raise ValueError("run evidence requires one open evaluation phase")
        phase_events[-1] = {**phase_events[-1], "ended_at": recorded_at}
        snapshot = _build_sealed_run_evidence(
            run_id=run_id,
            run=run,
            attempts=attempts,
            contract=contract,
            workspace_root=workspace_root,
            recorded_at=recorded_at,
            contract_sha256=sha256(contract["canonical_bytes"]).hexdigest(),
        )
        _validated_phase_durations(
            snapshot["phase_evidence"],
            str(run.get("run_started_at")),
            recorded_at,
        )
        run["sealed_evidence"] = snapshot
        _validate_run_registry(
            ledger["runs"],
            ledger["attempts"],
            evidence_contract_sha256=protocol.execution_lock.evidence_contract_sha256,
        )
        _atomic_write_json(ledger_path, ledger)


def validate_provider_output_schema(
    schema: Mapping[str, object],
) -> list[BenchmarkIssue]:
    """Reject non-deterministic or open Provider structured-output schemas."""
    issues: list[BenchmarkIssue] = []
    _validate_schema_node(schema, "$", issues)
    return issues


def verify_receipt(
    receipt: Mapping[str, object],
    protocol: BenchmarkProtocol,
    ledger_path: Path,
    evidence_contract_path: Path,
    workspace_root: Path,
) -> list[BenchmarkIssue]:
    """Verify one receipt in the fixed schema, protocol and ledger order."""
    issues: list[BenchmarkIssue] = []
    _validate_json_schema(
        receipt,
        _load_frozen_schema("run-receipt.schema.json"),
        "$",
        "receipt",
        issues,
    )
    if issues:
        return issues
    _scan_non_placeholder_digests(receipt, "$", "receipt", issues)
    if issues:
        return issues
    _validate_receipt_protocol_binding(receipt, protocol, issues)
    if issues:
        return issues
    try:
        contract = _load_evidence_contract(evidence_contract_path, protocol)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return [
            BenchmarkIssue(
                "receipt.evidence-contract",
                f"evidence contract is invalid: {error}",
            )
        ]
    _scan_public_value(receipt, "$", issues)
    _validate_receipt_ledger_binding(
        receipt,
        protocol,
        ledger_path,
        contract,
        workspace_root,
        issues,
    )
    if issues:
        return issues
    digests = _mapping(receipt.get("digests"))
    evaluator = _mapping(receipt.get("external_evaluator"))
    candidate = receipt.get("final_candidate_tree_sha256")
    if not isinstance(candidate, str) or not _is_digest(candidate):
        issues.append(
            BenchmarkIssue(
                "receipt.digest", "final candidate tree digest is missing or malformed"
            )
        )
    for name, value in digests.items():
        if name.endswith("sha256") and not _is_non_placeholder_digest(value):
            issues.append(
                BenchmarkIssue("receipt.digest", f"{name} is not a SHA-256 digest")
            )
    evaluator_candidate = evaluator.get("candidate_tree_sha256")
    if (
        evaluator_candidate != candidate
        or digests.get("candidate_tree_sha256") != candidate
    ):
        issues.append(
            BenchmarkIssue(
                "receipt.candidate-tree", "candidate tree digests must match"
            )
        )
    if evaluator.get("result_sha256") != digests.get("evaluator_result_sha256"):
        issues.append(
            BenchmarkIssue(
                "receipt.evaluator", "evaluator result digest must match receipt digest"
            )
        )
    _validate_receipt_timing(receipt, issues)
    _validate_token_usage(receipt.get("token_usage"), issues)
    _validate_human_events(receipt.get("human_events"), issues)
    _validate_receipt_measurements(receipt, issues)
    allowed_classifications = _FAILURE_CLASSIFICATIONS.get(receipt.get("status"), set())
    if receipt.get("failure_classification") not in allowed_classifications:
        issues.append(
            BenchmarkIssue(
                "receipt.failure-classification",
                "failure classification is not allowed for the run status",
            )
        )
    return issues


def verify_summary(
    summary: Mapping[str, object], protocol: BenchmarkProtocol
) -> list[BenchmarkIssue]:
    """Validate that the public summary only indexes every frozen receipt once."""
    issues: list[BenchmarkIssue] = []
    _validate_json_schema(
        summary,
        _load_frozen_schema("summary.schema.json"),
        "$",
        "summary",
        issues,
    )
    if issues:
        return issues
    _scan_non_placeholder_digests(summary, "$", "summary", issues)
    if issues:
        return issues
    try:
        protocol_digest = _require_executable_protocol(protocol)
    except ValueError as error:
        return [BenchmarkIssue("summary.protocol", str(error))]
    digest = summary.get("protocol_sha256")
    if not isinstance(digest, str) or not _is_digest(digest):
        issues.append(
            BenchmarkIssue("summary.digest", "protocol_sha256 must be a SHA-256 digest")
        )
    runs = summary.get("runs")
    if not isinstance(runs, list):
        return issues + [BenchmarkIssue("summary.runs", "runs must be a list")]
    for index, (run, canonical) in enumerate(
        zip(runs, protocol.run_matrix, strict=True), start=1
    ):
        if not isinstance(run, Mapping):
            issues.append(BenchmarkIssue("summary.runs", "run entry must be an object"))
            continue
        receipt_digest = run.get("receipt_sha256")
        actual_row = (
            run.get("run_id"),
            run.get("arm"),
            run.get("fixture"),
            run.get("position"),
            run.get("order"),
        )
        expected_row = (
            canonical.run_id,
            canonical.arm,
            canonical.fixture,
            canonical.position,
            canonical.position,
        )
        if actual_row != expected_row:
            issues.append(
                BenchmarkIssue(
                    "summary.matrix",
                    f"summary row {index} must match the canonical protocol row",
                )
            )
        if not _is_non_placeholder_digest(receipt_digest):
            issues.append(
                BenchmarkIssue("summary.digest", "receipt digest must be SHA-256")
            )
    _scan_public_value(summary, "$", issues)
    if digest != protocol_digest:
        issues.append(
            BenchmarkIssue(
                "summary.protocol-digest",
                "summary must digest canonical protocol bytes",
            )
        )
    receipt_digests = [
        run.get("receipt_sha256") for run in runs if isinstance(run, Mapping)
    ]
    if len(set(receipt_digests)) != len(receipt_digests):
        issues.append(
            BenchmarkIssue("summary.receipt-digest", "receipt digests must be unique")
        )
    _validate_summary_metrics(summary.get("metrics"), issues)
    return issues


def _validate_receipt_protocol_binding(
    receipt: Mapping[str, object],
    protocol: BenchmarkProtocol,
    issues: list[BenchmarkIssue],
) -> None:
    try:
        protocol_digest = _require_executable_protocol(protocol)
    except ValueError as error:
        issues.append(BenchmarkIssue("receipt.protocol", str(error)))
        return
    matching = next(
        (run for run in protocol.run_matrix if run.run_id == receipt.get("run_id")),
        None,
    )
    if matching is None or (
        receipt.get("arm"),
        receipt.get("fixture"),
        receipt.get("order"),
    ) != (matching.arm, matching.fixture, matching.position):
        issues.append(
            BenchmarkIssue(
                "receipt.protocol", "receipt must match one canonical run row"
            )
        )
        return
    if receipt.get("protocol_sha256") != protocol_digest:
        issues.append(
            BenchmarkIssue(
                "receipt.protocol-digest",
                "receipt must digest the canonical protocol bytes",
            )
        )
    if receipt.get("provider_cwd") != "benchmark-task/":
        issues.append(
            BenchmarkIssue(
                "receipt.provider-cwd", "provider cwd must be relative benchmark-task/"
            )
        )
    identity = _mapping(receipt.get("identity"))
    expected_identity = protocol.execution_lock
    for key in _LOCK_KEYS:
        if identity.get(key) != getattr(expected_identity, key):
            issues.append(
                BenchmarkIssue("receipt.identity", f"identity lock mismatch: {key}")
            )
            break
    if receipt.get("arm") == "A11" and receipt.get("status") == "completed":
        loop = _mapping(receipt.get("loop"))
        if _mapping(loop.get("close")).get("state") != "closed":
            issues.append(
                BenchmarkIssue(
                    "receipt.a11.close", "completed A11 receipt must be closed"
                )
            )
            return
        required_roles = {"primary"}
        if receipt.get("fixture") == "multi-tenant-security-review":
            required_roles.add("cross-risk")
        callbacks = loop.get("expert_callbacks")
        if (
            not isinstance(callbacks, list)
            or {
                callback.get("role")
                for callback in callbacks
                if isinstance(callback, Mapping)
            }
            != required_roles
        ):
            issues.append(
                BenchmarkIssue(
                    "receipt.a11.roles", "A11 callback roles do not match fixture"
                )
            )


def _validate_receipt_ledger_binding(
    receipt: Mapping[str, object],
    protocol: BenchmarkProtocol,
    ledger_path: Path,
    contract: Mapping[str, object],
    workspace_root: Path,
    issues: list[BenchmarkIssue],
) -> None:
    """Bind receipt attempts and runner evidence to the validated persisted v6 ledger."""
    if not ledger_path.is_file():
        issues.append(BenchmarkIssue("receipt.ledger", "attempt ledger is missing"))
        return
    try:
        with _ledger_lock(ledger_path):
            ledger = _load_ledger(
                ledger_path,
                canonical_protocol_digest(protocol),
                protocol.attempt_budget,
                protocol.execution_lock.evidence_contract_sha256,
            )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        issues.append(
            BenchmarkIssue("receipt.ledger", f"attempt ledger is invalid: {error}")
        )
        return

    run_id = receipt.get("run_id")
    run_registry = ledger.get("runs")
    run_record = (
        run_registry.get(run_id)
        if isinstance(run_registry, Mapping) and isinstance(run_id, str)
        else None
    )
    authoritative_evidence = (
        run_record.get("sealed_evidence")
        if isinstance(run_record, Mapping)
        else None
    )
    if not isinstance(authoritative_evidence, Mapping):
        issues.append(
            BenchmarkIssue(
                "receipt.ledger-evidence",
                "runner-owned run evidence is missing from the attempt ledger",
            )
        )
        return
    if (
        authoritative_evidence.get("evidence_contract_sha256")
        != protocol.execution_lock.evidence_contract_sha256
    ):
        issues.append(
            BenchmarkIssue(
                "receipt.ledger-evidence",
                "sealed evidence contract does not match protocol",
            )
        )
    try:
        (
            actual_inventory,
            actual_changed_files,
            actual_changed_file_evidence,
            actual_tree_digests,
        ) = _build_authoritative_file_evidence(contract, str(run_id), workspace_root)
    except (OSError, ValueError) as error:
        issues.append(
            BenchmarkIssue(
                "receipt.workspace-evidence",
                f"workspace evidence is invalid: {error}",
            )
        )
        return
    actual_surfaces = {
        "artifact_inventory": actual_inventory,
        "changed_files": actual_changed_files,
        "changed_file_evidence": actual_changed_file_evidence,
        "changed_scope_tree_digests": actual_tree_digests,
    }
    if any(
        authoritative_evidence.get(key) != value
        for key, value in actual_surfaces.items()
    ):
        issues.append(
            BenchmarkIssue(
                "receipt.workspace-evidence",
                "sealed evidence does not match the contract-bound workspace",
            )
        )
    _validate_receipt_run_evidence_projection(
        receipt, authoritative_evidence, issues
    )
    expected = [
        attempt
        for attempt in ledger["attempts"]
        if isinstance(attempt, Mapping) and attempt.get("run_id") == run_id
    ]
    observed = receipt.get("provider_attempts")
    if not isinstance(observed, list) or not observed:
        issues.append(
            BenchmarkIssue("receipt.ledger", "receipt has no Provider attempts")
        )
        return
    if len(observed) != len(expected) or any(
        not isinstance(item, Mapping) for item in observed
    ):
        issues.append(
            BenchmarkIssue(
                "receipt.ledger", "receipt attempt set does not close over its run"
            )
        )
        return
    observed_ids = [item.get("attempt_id") for item in observed]
    expected_ids = [item.get("attempt_id") for item in expected]
    if observed_ids != expected_ids or len(set(observed_ids)) != len(observed_ids):
        issues.append(
            BenchmarkIssue(
                "receipt.ledger", "receipt attempts must match ledger order exactly"
            )
        )
        return

    comparable = (
        "kind",
        "effective_kind",
        "status",
        "content_produced",
        "terminal",
        "child_session",
        "token_usage",
        "raw_provider_output_sha256",
    )
    sessions: list[object] = []
    token_keys = (
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
    )
    totals = dict.fromkeys(token_keys, 0)
    for actual, persisted in zip(observed, expected, strict=True):
        if any(actual.get(key) != persisted.get(key) for key in comparable):
            issues.append(
                BenchmarkIssue(
                    "receipt.ledger",
                    f"attempt {actual.get('attempt_id')} state differs from ledger",
                )
            )
        if actual.get("terminal") is not True:
            issues.append(
                BenchmarkIssue(
                    "receipt.ledger", "all published attempts must be terminal"
                )
            )
        sessions.append(actual.get("child_session"))
        usage = _mapping(actual.get("token_usage"))
        for key in token_keys:
            value = usage.get(key)
            if not _non_bool_int(value) or value < 0:
                issues.append(
                    BenchmarkIssue(
                        "receipt.tokens", "per-attempt token usage is invalid"
                    )
                )
            else:
                totals[key] += value
    if any(not isinstance(value, str) or not value for value in sessions) or len(
        set(sessions)
    ) != len(sessions):
        issues.append(
            BenchmarkIssue(
                "receipt.session", "attempt child sessions must be non-empty and unique"
            )
        )
    if dict(_mapping(receipt.get("token_usage"))) != totals:
        issues.append(
            BenchmarkIssue(
                "receipt.tokens",
                "aggregate token usage must equal every attempt exactly",
            )
        )

    writer_lineage = [
        attempt for attempt in expected if attempt.get("effective_kind") == "writer"
    ]
    writers = [
        attempt
        for attempt in writer_lineage
        if attempt.get("status") != "technical_failure"
    ]
    if not writers and writer_lineage and all(
        attempt.get("status") == "technical_failure" for attempt in writer_lineage
    ):
        writers = [writer_lineage[-1]]
    if len(writers) != 1:
        issues.append(
            BenchmarkIssue("receipt.ledger", "run must bind exactly one effective writer")
        )
        return
    writer = writers[0]
    expected_receipt_status = {
        "completed": "completed",
        "technical_failure": "failed",
        "failed": "failed",
        "timeout": "timeout",
        "needs_operator": "needs_operator",
        "budget_exhausted": "budget_exhausted",
    }.get(writer.get("status"))
    if receipt.get("status") != expected_receipt_status:
        issues.append(
            BenchmarkIssue(
                "receipt.ledger", "receipt status must be derived from the terminal writer"
            )
        )
    expected_classification = _derive_failure_classification(
        receipt, expected, writer
    )
    if receipt.get("failure_classification") != expected_classification:
        issues.append(
            BenchmarkIssue(
                "receipt.failure-classification",
                "failure classification must be uniquely derived from ledger and evaluator state",
            )
        )
    _validate_command_evidence(receipt, protocol, observed, issues)
    _validate_receipt_verified_delivery_timing(receipt, expected, issues)
    close = _mapping(_mapping(receipt.get("loop")).get("close"))
    callbacks = _mapping(receipt.get("loop")).get("expert_callbacks")
    arm = receipt.get("arm")
    status = receipt.get("status")
    if arm in {"P", "S", "A00"}:
        if not _is_empty_loop_evidence(close, "not_applicable") or callbacks != []:
            issues.append(
                BenchmarkIssue(
                    "receipt.loop", "non-Loop arms cannot publish Close or expert evidence"
                )
            )
        return
    if arm == "A10" and callbacks != []:
        issues.append(BenchmarkIssue("receipt.loop", "A10 cannot publish expert evidence"))
    expected_loop_state = "closed" if status == "completed" else "open"
    if close.get("state") != expected_loop_state:
        issues.append(
            BenchmarkIssue("receipt.close", "Loop state does not match the run terminal state")
        )
    loop_type, loop_id = _expected_loop_identity(receipt)
    if status == "completed" and (
        close.get("exit_code") != 0
        or not _is_loop_close_command(
            close.get("argv"),
            loop_type=loop_type,
            loop_id=loop_id,
            review_digest=close.get("review_digest"),
        )
        or not _is_non_placeholder_digest(close.get("review_digest"))
        or close.get("review_digest") != writer.get("candidate_digest")
        or close.get("close_digest") != writer.get("close_digest")
    ):
        issues.append(
            BenchmarkIssue(
                "receipt.close", "Loop Close must match the writer ledger event"
            )
        )
    if status != "completed" and not _is_empty_loop_evidence(close, "open"):
        issues.append(
            BenchmarkIssue("receipt.close", "an unclosed Loop cannot carry Close evidence")
        )
    if arm == "A11":
        _validate_a11_attempt_closure(receipt, expected, writer, issues)


def _validate_receipt_run_evidence_projection(
    receipt: Mapping[str, object],
    authoritative: Mapping[str, object],
    issues: list[BenchmarkIssue],
) -> None:
    bindings = {
        "phase_evidence": "phase_evidence",
        "artifact_inventory": "artifact_inventory",
        "changed_files": "changed_files",
        "changed_scope_tree_digests": "changed_scope_tree_digests",
        "automated_events": "automated_events",
        "human_events": "human_events",
    }
    for receipt_key, ledger_key in bindings.items():
        if receipt.get(receipt_key) != authoritative.get(ledger_key):
            issues.append(
                BenchmarkIssue(
                    "receipt.ledger-evidence",
                    f"{receipt_key} must exactly project runner-owned ledger evidence",
                )
            )


def _derive_failure_classification(
    receipt: Mapping[str, object],
    attempts: list[Mapping[str, object]],
    writer: Mapping[str, object],
) -> str:
    status = receipt.get("status")
    fixed = {
        "completed": "none",
        "timeout": "timeout",
        "needs_operator": "expert_conflict",
        "budget_exhausted": "provider_budget_exhausted",
    }
    if status in fixed:
        return fixed[status]
    if status != "failed":
        return "evidence_failure"
    expert_failures = {
        "failed",
        "timeout",
        "needs_operator",
        "budget_exhausted",
    }
    if any(
        attempt.get("effective_kind")
        in {"primary_expert", "cross_risk_expert", "expert_rereview"}
        and attempt.get("status") in expert_failures
        for attempt in attempts
    ):
        return "expert_failure"
    if writer.get("status") == "technical_failure" and writer.get(
        "content_produced"
    ) is False:
        return "provider_pre_output_failure"
    if writer.get("status") == "failed":
        return "writer_failure"
    measurements = _mapping(receipt.get("measurements"))
    if measurements.get("evidence_completeness") != 1:
        return "evidence_failure"
    if _mapping(receipt.get("external_evaluator")).get(
        "external_verified_delivery"
    ) is False:
        return "evaluation_failure"
    return "writer_failure"


def _validate_a11_attempt_closure(
    receipt: Mapping[str, object],
    attempts: list[Mapping[str, object]],
    writer: Mapping[str, object],
    issues: list[BenchmarkIssue],
) -> None:
    callbacks = _mapping(receipt.get("loop")).get("expert_callbacks")
    if not isinstance(callbacks, list):
        issues.append(BenchmarkIssue("receipt.a11.evidence", "callbacks are missing"))
        return
    providers = {
        item.get("attempt_id"): item
        for item in receipt.get("provider_attempts", [])
        if isinstance(item, Mapping)
    }
    first_reviews = {
        item.get("attempt_id"): item
        for item in attempts
        if item.get("effective_kind") in {"primary_expert", "cross_risk_expert"}
    }
    callback_ids = [
        item.get("expert_attempt_id")
        for item in callbacks
        if isinstance(item, Mapping)
    ]
    issue_code = (
        "receipt.a11.conflict"
        if receipt.get("status") == "needs_operator"
        else "receipt.a11.evidence"
    )
    if (
        len(callback_ids) != len(callbacks)
        or len(callback_ids) != len(set(callback_ids))
        or set(callback_ids) != set(first_reviews)
    ):
        issues.append(
            BenchmarkIssue(
                issue_code,
                "expert callbacks must close over every started first-review attempt exactly once",
            )
        )
        return
    rereviews_by_parent: dict[object, list[Mapping[str, object]]] = {}
    for attempt in attempts:
        if attempt.get("effective_kind") == "expert_rereview":
            rereviews_by_parent.setdefault(attempt.get("parent_attempt_id"), []).append(
                attempt
            )
    loop_type, loop_id = _expected_loop_identity(receipt)
    writer_history = writer.get("history")
    repairs = (
        [
            event
            for event in writer_history
            if isinstance(event, Mapping) and event.get("repair_digest") is not None
        ]
        if isinstance(writer_history, list)
        else []
    )
    conflict_roles: list[object] = []
    completed_roles: set[object] = set()
    for callback in callbacks:
        if not isinstance(callback, Mapping):
            continue
        expert = first_reviews[callback.get("expert_attempt_id")]
        provider = providers.get(expert.get("attempt_id"))
        reason = callback.get("reason")
        review_exit = callback.get("review_exit_code")
        expected_exit_success = expert.get("status") == "completed"
        proof_keys = (
            "snapshot_sha256",
            "input_sha256",
            "raw_output_sha256",
            "parent_tree_before_sha256",
            "parent_tree_after_sha256",
        )
        if (
            not isinstance(reason, str)
            or reason.strip().lower()
            in {"", "tbd", "todo", "placeholder", "unknown", "not_applicable"}
            or not isinstance(provider, Mapping)
            or callback.get("role") != expert.get("role")
            or callback.get("expert_attempt_status") != expert.get("status")
            or callback.get("parent_digest") != expert.get("parent_digest")
            or callback.get("candidate_digest") != expert.get("candidate_digest")
            or callback.get("child_session") != provider.get("child_session")
            or callback.get("token_usage") != provider.get("token_usage")
            or callback.get("raw_output_sha256")
            != provider.get("raw_provider_output_sha256")
            or not _is_loop_review_command(
                callback.get("review_argv"),
                loop_type=loop_type,
                loop_id=loop_id,
                expected_digest=callback.get("parent_digest"),
            )
            or not _non_bool_int(review_exit)
            or (expected_exit_success and review_exit != 0)
            or (not expected_exit_success and review_exit == 0)
            or any(
                not _is_non_placeholder_digest(callback.get(key))
                for key in proof_keys
            )
            or callback.get("parent_tree_before_sha256")
            != callback.get("parent_tree_after_sha256")
        ):
            issues.append(
                BenchmarkIssue(
                    issue_code,
                    "callback does not bind its started expert attempt and immutable evidence",
                )
            )
            continue
        if expert.get("status") == "completed":
            completed_roles.add(expert.get("role"))
        finding = expert.get("finding_digest")
        finding_count = callback.get("finding_count")
        severe_count = callback.get("severe_finding_count")
        if (
            not _non_bool_int(finding_count)
            or not _non_bool_int(severe_count)
            or severe_count > finding_count
            or (finding is None and (finding_count != 0 or severe_count != 0))
            or (finding is not None and finding_count < 1)
            or callback.get("finding_digest") != finding
        ):
            issues.append(
                BenchmarkIssue(issue_code, "callback Finding evidence is not reproducible")
            )
        matching_repairs = [
            event for event in repairs if event.get("finding_digest") == finding
        ]
        repair = matching_repairs[-1] if matching_repairs else None
        expected_repair = repair.get("repair_digest") if repair else None
        expected_candidate = repair.get("candidate_digest") if repair else None
        if (
            callback.get("repair_digest") != expected_repair
            or callback.get("repaired_candidate_digest") != expected_candidate
        ):
            issues.append(
                BenchmarkIssue(
                    issue_code,
                    "callback must disclose the writer repair and new Candidate exactly",
                )
            )
        published_rereviews = callback.get("rereviews")
        expected_rereviews = rereviews_by_parent.get(expert.get("attempt_id"), [])
        if not isinstance(published_rereviews, list):
            issues.append(BenchmarkIssue(issue_code, "rereviews must be an array"))
            continue
        published_ids = [
            item.get("attempt_id")
            for item in published_rereviews
            if isinstance(item, Mapping)
        ]
        expected_ids = [item.get("attempt_id") for item in expected_rereviews]
        if (
            len(published_ids) != len(published_rereviews)
            or len(published_ids) != len(set(published_ids))
            or published_ids != expected_ids
        ):
            issues.append(
                BenchmarkIssue(
                    issue_code,
                    "callback must disclose every started rereview attempt exactly once",
                )
            )
            continue
        completed_rereview = False
        for published, rereview in zip(
            published_rereviews, expected_rereviews, strict=True
        ):
            rereview_provider = providers.get(rereview.get("attempt_id"))
            rereview_exit = published.get("exit_code")
            rereview_completed = rereview.get("status") == "completed"
            completed_rereview = completed_rereview or rereview_completed
            rereview_proofs = (
                "snapshot_sha256",
                "input_sha256",
                "raw_output_sha256",
                "parent_tree_before_sha256",
                "parent_tree_after_sha256",
            )
            if (
                not isinstance(rereview_provider, Mapping)
                or published.get("status") != rereview.get("status")
                or published.get("child_session") != rereview_provider.get("child_session")
                or published.get("token_usage") != rereview_provider.get("token_usage")
                or published.get("raw_output_sha256")
                != rereview_provider.get("raw_provider_output_sha256")
                or published.get("finding_digest") != rereview.get("finding_digest")
                or published.get("repair_digest") != rereview.get("repair_digest")
                or published.get("candidate_digest") != rereview.get("candidate_digest")
                or not _is_loop_review_command(
                    published.get("argv"),
                    loop_type=loop_type,
                    loop_id=loop_id,
                    expected_digest=published.get("candidate_digest"),
                )
                or not _non_bool_int(rereview_exit)
                or (rereview_completed and rereview_exit != 0)
                or (not rereview_completed and rereview_exit == 0)
                or any(
                    not _is_non_placeholder_digest(published.get(key))
                    for key in rereview_proofs
                )
                or published.get("parent_tree_before_sha256")
                != published.get("parent_tree_after_sha256")
            ):
                issues.append(
                    BenchmarkIssue(
                        issue_code,
                        "rereview disclosure does not bind its Provider attempt, lineage, command, and evidence",
                    )
                )
        expected_callback_status = "fail"
        if receipt.get("status") == "needs_operator" and finding is not None:
            expected_callback_status = "conflict"
            conflict_roles.append(expert.get("role"))
        elif expert.get("status") == "completed" and (
            finding is None or completed_rereview
        ):
            expected_callback_status = "pass"
        if callback.get("status") != expected_callback_status:
            issues.append(
                BenchmarkIssue(
                    issue_code,
                    "callback status does not follow the actual attempt and repair closure",
                )
            )
    required_roles = {"primary"}
    if receipt.get("fixture") == "multi-tenant-security-review":
        required_roles.add("cross-risk")
    if receipt.get("status") == "completed" and completed_roles != required_roles:
        issues.append(
            BenchmarkIssue(
                "receipt.a11.roles",
                "completed A11 run lacks the required completed expert roles",
            )
        )
    if receipt.get("status") == "needs_operator" and (
        len(conflict_roles) < 2
        or len(conflict_roles) != len(set(conflict_roles))
        or set(conflict_roles) != required_roles
        or repairs
        or any(rereviews_by_parent.values())
    ):
        issues.append(
            BenchmarkIssue(
                "receipt.a11.conflict",
                "needs_operator requires a unique immutable conflict multiset without repair",
            )
        )


def _validate_command_evidence(
    receipt: Mapping[str, object],
    protocol: BenchmarkProtocol,
    attempts: list[Mapping[str, object]],
    issues: list[BenchmarkIssue],
) -> None:
    evidence = receipt.get("command_evidence")
    if not isinstance(evidence, list):
        issues.append(BenchmarkIssue("receipt.command", "command evidence is missing"))
        return
    attempts_by_id = {item.get("attempt_id"): item for item in attempts}
    seen: set[object] = set()
    for item in evidence:
        if not isinstance(item, Mapping):
            issues.append(
                BenchmarkIssue("receipt.command", "command evidence must be an object")
            )
            continue
        attempt_id = item.get("attempt_id")
        attempt = attempts_by_id.get(attempt_id)
        if attempt is None or attempt_id in seen:
            issues.append(
                BenchmarkIssue(
                    "receipt.command",
                    "command evidence attempt binding is missing or duplicated",
                )
            )
            continue
        seen.add(attempt_id)
        argv = item.get("argv")
        if (
            not isinstance(argv, list)
            or len(argv) < 2
            or any(not isinstance(argument, str) or not argument for argument in argv)
        ):
            issues.append(
                BenchmarkIssue("receipt.command", "command argv is not closed")
            )
        elif not _is_provider_command(
            argv,
            protocol=protocol,
            provider_cwd=receipt.get("provider_cwd"),
            effective_kind=attempt.get("effective_kind"),
        ):
            issues.append(
                BenchmarkIssue(
                    "receipt.command",
                    "command argv does not match the frozen Provider execution contract",
                )
            )
        expected_exit = 0 if attempt.get("status") == "completed" else None
        exit_code = item.get("exit_code")
        if (
            not _non_bool_int(exit_code)
            or (expected_exit == 0 and exit_code != 0)
            or (expected_exit is None and exit_code == 0)
        ):
            issues.append(
                BenchmarkIssue(
                    "receipt.command",
                    "command exit code does not match the terminal Provider status",
                )
            )
        raw_digest = item.get("raw_provider_output_sha256")
        if (
            raw_digest != attempt.get("raw_provider_output_sha256")
            or not _is_non_placeholder_digest(raw_digest)
        ):
            issues.append(
                BenchmarkIssue(
                    "receipt.command",
                    "command raw output digest does not bind its Provider attempt",
                )
            )
        if any(
            not _is_non_placeholder_digest(item.get(key))
            for key in ("stdout_sha256", "stderr_sha256")
        ):
            issues.append(
                BenchmarkIssue(
                    "receipt.command", "command stdout/stderr digests are placeholders"
                )
            )
        if item.get("evidence_id") != _command_evidence_id(item):
            issues.append(
                BenchmarkIssue(
                    "receipt.command", "command evidence ID does not bind its record"
                )
            )
    if seen != set(attempts_by_id):
        issues.append(
            BenchmarkIssue(
                "receipt.command",
                "command evidence must close over every Provider attempt exactly once",
            )
        )


def _command_evidence_id(value: Mapping[str, object]) -> str:
    bound = {
        key: value.get(key)
        for key in (
            "attempt_id",
            "argv",
            "exit_code",
            "stdout_sha256",
            "stderr_sha256",
            "raw_provider_output_sha256",
        )
    }
    canonical = json.dumps(
        bound, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def _is_provider_command(
    value: list[str],
    *,
    protocol: BenchmarkProtocol,
    provider_cwd: object,
    effective_kind: object,
) -> bool:
    if not isinstance(provider_cwd, str) or effective_kind not in {
        "writer",
        "primary_expert",
        "cross_risk_expert",
        "expert_rereview",
    }:
        return False
    expected_sandbox = (
        "workspace-write" if effective_kind == "writer" else "read-only"
    )
    expected_config = f'model_reasoning_effort="{protocol.execution_lock.reasoning_effort}"'
    return value == [
        "codex",
        "exec",
        "--ephemeral",
        "--json",
        "--ignore-user-config",
        "--strict-config",
        "--model",
        protocol.execution_lock.model,
        "-c",
        expected_config,
        "--sandbox",
        expected_sandbox,
        "-C",
        provider_cwd,
    ]


def _loop_prefix(value: object, *parts: str) -> int | None:
    if not isinstance(value, list) or any(
        not isinstance(argument, str) or not argument for argument in value
    ):
        return None
    prefixes = (
        ["ai-sdlc", "loop", *parts],
        ["python", "-m", "ai_sdlc", "loop", *parts],
        ["uv", "run", "ai-sdlc", "loop", *parts],
    )
    return next(
        (len(prefix) for prefix in prefixes if value[: len(prefix)] == prefix),
        None,
    )


def _parse_closed_options(
    value: object,
    start: int | None,
    *,
    valued: set[str],
    flags: set[str],
) -> dict[str, str | bool] | None:
    if not isinstance(value, list) or start is None:
        return None
    parsed: dict[str, str | bool] = {}
    index = start
    while index < len(value):
        option = value[index]
        if option in parsed or option not in valued | flags:
            return None
        if option in flags:
            parsed[option] = True
            index += 1
            continue
        if index + 1 >= len(value):
            return None
        option_value = value[index + 1]
        if not isinstance(option_value, str) or not option_value or option_value.startswith("--"):
            return None
        parsed[option] = option_value
        index += 2
    if set(parsed) != valued | flags:
        return None
    return parsed


def _expected_loop_identity(receipt: Mapping[str, object]) -> tuple[str, str]:
    fixture = receipt.get("fixture")
    arm = receipt.get("arm")
    loop_type = _FIXTURE_LOOP_TYPES.get(fixture, "")
    loop_id = (
        f"benefit-{arm.lower()}-{fixture}"
        if isinstance(arm, str) and isinstance(fixture, str)
        else ""
    )
    return loop_type, loop_id


def _expected_loop_read_path(loop_type: str, loop_id: str) -> str:
    return f".ai-sdlc/loops/{loop_type}/{loop_id}/{loop_type}-input.json"


def _is_loop_review_command(
    value: object,
    *,
    loop_type: str,
    loop_id: str,
    expected_digest: object,
) -> bool:
    parsed = _parse_closed_options(
        value,
        _loop_prefix(value, "review"),
        valued={"--type", "--loop-id", "--expect-digest", "--read-path"},
        flags={"--json"},
    )
    return parsed == {
        "--type": loop_type,
        "--loop-id": loop_id,
        "--expect-digest": expected_digest,
        "--read-path": _expected_loop_read_path(loop_type, loop_id),
        "--json": True,
    }


def _is_loop_close_command(
    value: object,
    *,
    loop_type: str,
    loop_id: str,
    review_digest: object,
) -> bool:
    parsed = _parse_closed_options(
        value,
        _loop_prefix(value, loop_type, "close"),
        valued={"--loop-id", "--expect-review-digest"},
        flags={"--yes", "--json"},
    )
    return parsed == {
        "--loop-id": loop_id,
        "--expect-review-digest": review_digest,
        "--yes": True,
        "--json": True,
    }


def _is_empty_loop_evidence(close: Mapping[str, object], state: str) -> bool:
    return close.get("state") == state and all(
        close.get(key) is None
        for key in ("argv", "exit_code", "review_digest", "close_digest")
    )


def _validate_receipt_verified_delivery_timing(
    receipt: Mapping[str, object],
    attempts: list[Mapping[str, object]],
    issues: list[BenchmarkIssue],
) -> None:
    reservations: list[datetime] = []
    terminal_completions: list[datetime] = []
    for attempt in attempts:
        history = attempt.get("history")
        if isinstance(history, list) and history and isinstance(history[0], Mapping):
            reserved_at = _parse_rfc3339(history[0].get("recorded_at"))
            if reserved_at is not None:
                reservations.append(reserved_at)
            terminal = history[-1]
            if isinstance(terminal, Mapping) and terminal.get("terminal") is True:
                terminal_at = _parse_rfc3339(terminal.get("recorded_at"))
                if terminal_at is not None:
                    terminal_completions.append(terminal_at)
    timestamps = _mapping(receipt.get("timestamps"))
    started_at = _parse_rfc3339(timestamps.get("started_at"))
    ended_at = _parse_rfc3339(timestamps.get("ended_at"))
    completed_at = _parse_rfc3339(
        _mapping(receipt.get("external_evaluator")).get("completed_at")
    )
    first_reservation = min(reservations) if reservations else None
    last_terminal = max(terminal_completions) if terminal_completions else None
    if (
        started_at is None
        or ended_at is None
        or first_reservation is None
        or last_terminal is None
        or completed_at is None
        or not (
            started_at
            <= first_reservation
            <= last_terminal
            <= completed_at
            == ended_at
        )
    ):
        issues.append(
            BenchmarkIssue(
                "receipt.timing",
                "verified delivery timestamps do not bind start, reservation, terminal completion, and evaluator",
            )
        )
        return
    expected = (completed_at - first_reservation).total_seconds()
    actual = _mapping(receipt.get("timings")).get(
        "verified_delivery_wall_seconds"
    )
    if not _finite_number(actual) or not math.isclose(
        actual, expected, rel_tol=0, abs_tol=1e-6
    ):
        issues.append(
            BenchmarkIssue(
                "receipt.timing",
                "verified delivery timing must equal first reservation to evaluator",
            )
        )


def _validate_summary_metrics(
    value: object, issues: list[BenchmarkIssue]
) -> None:
    metrics = _mapping(value)
    delivery = _mapping(metrics.get("external_verified_delivery_count"))
    delivery_arms = _mapping(delivery.get("arms"))
    delivery_deltas = _mapping(delivery.get("signed_deltas"))
    if (
        delivery_deltas.get("S_minus_P")
        != delivery_arms.get("S") - delivery_arms.get("P")
        or delivery_deltas.get("A11_minus_P")
        != delivery_arms.get("A11") - delivery_arms.get("P")
    ):
        issues.append(
            BenchmarkIssue(
                "summary.metric-delta", "delivery signed deltas are not reproducible"
            )
        )
    coverage = _mapping(metrics.get("median_weighted_ac_coverage"))
    coverage_arms = _mapping(coverage.get("arms"))
    coverage_delta = _mapping(coverage.get("signed_delta"))
    expected_coverage_delta = (
        coverage_arms.get("A10") - coverage_arms.get("A00")
    ) * 100
    if not math.isclose(
        coverage_delta.get("percentage_points"),
        expected_coverage_delta,
        rel_tol=0,
        abs_tol=1e-9,
    ):
        issues.append(
            BenchmarkIssue(
                "summary.metric-delta", "Loop signed delta is not reproducible"
            )
        )
    defects = _mapping(metrics.get("sum_severe_defect_escape_count"))
    defect_arms = _mapping(defects.get("arms"))
    defect_delta = _mapping(defects.get("signed_delta"))
    if defect_delta.get("value") != defect_arms.get("A11") - defect_arms.get("A10"):
        issues.append(
            BenchmarkIssue(
                "summary.metric-delta", "expert signed delta is not reproducible"
            )
        )


def load_protocol(path: Path) -> BenchmarkProtocol:
    """Load the protocol with a closed JSON object surface."""
    return _parse_protocol_bytes(path.read_bytes())


def _load_evidence_contract(
    path: Path, protocol: BenchmarkProtocol
) -> dict[str, object]:
    canonical_bytes = path.read_bytes()
    digest = sha256(canonical_bytes).hexdigest()
    lock = protocol.execution_lock
    if (
        digest != lock.evidence_contract_sha256
        or digest != lock.evidence_contract_commitment
        or not _is_non_placeholder_digest(digest)
    ):
        raise ValueError("evidence contract bytes do not match protocol commitment")
    raw = json.loads(canonical_bytes)
    if not isinstance(raw, dict) or set(raw) != {"schema", "runs"}:
        raise ValueError("evidence contract surface is invalid")
    if raw.get("schema") != "ai-sdlc-v2-benefit-evidence-contract/v1":
        raise ValueError("evidence contract schema is invalid")
    runs = raw.get("runs")
    if not isinstance(runs, list) or len(runs) != len(protocol.run_matrix):
        raise ValueError("evidence contract run matrix is invalid")
    expected_ids = [run.run_id for run in protocol.run_matrix]
    actual_ids: list[object] = []
    for run in runs:
        _validate_contract_run(run)
        actual_ids.append(run["run_id"])
    if actual_ids != expected_ids or len(set(actual_ids)) != len(actual_ids):
        raise ValueError("evidence contract run order must match protocol")
    privacy_issues: list[BenchmarkIssue] = []
    _scan_public_value(raw, "$", privacy_issues)
    if privacy_issues:
        raise ValueError("evidence contract contains non-public values")
    return {**raw, "canonical_bytes": canonical_bytes}


def _validate_contract_run(value: object) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "run_id",
        "artifact_slots",
        "changed_files_scope",
        "allowed_automated_event_types",
    }:
        raise ValueError("evidence contract run is invalid")
    run_id = value.get("run_id")
    if run_id not in {f"{arm}:{fixture}" for arm, fixture, _ in _SCHEDULE}:
        raise ValueError("evidence contract run id is invalid")
    slots = value.get("artifact_slots")
    if not isinstance(slots, list) or not slots:
        raise ValueError("evidence contract artifact slots are invalid")
    paths: list[object] = []
    for slot in slots:
        if not isinstance(slot, Mapping) or set(slot) != {
            "path",
            "category",
            "required",
            "applicable",
        }:
            raise ValueError("evidence contract artifact slot is invalid")
        if (
            not _is_safe_relative_path(slot.get("path"))
            or slot.get("category") not in {"setup", "governance", "delivery"}
            or not isinstance(slot.get("required"), bool)
            or not isinstance(slot.get("applicable"), bool)
            or (slot.get("required") and not slot.get("applicable"))
        ):
            raise ValueError("evidence contract artifact slot is invalid")
        paths.append(slot.get("path"))
    if len(paths) != len(set(paths)):
        raise ValueError("evidence contract artifact paths must be unique")
    scope = value.get("changed_files_scope")
    if not isinstance(scope, Mapping) or set(scope) != {
        "baseline_root",
        "candidate_root",
        "include_paths",
    }:
        raise ValueError("evidence contract changed-files scope is invalid")
    include = scope.get("include_paths")
    if (
        not _is_safe_relative_path(scope.get("baseline_root"))
        or not _is_safe_relative_path(scope.get("candidate_root"))
        or not isinstance(include, list)
        or not include
        or any(not _is_safe_relative_path(item) for item in include)
        or len(include) != len(set(include))
    ):
        raise ValueError("evidence contract changed-files scope is invalid")
    allowed = value.get("allowed_automated_event_types")
    if (
        not isinstance(allowed, list)
        or len(allowed) != len(set(allowed))
        or not set(allowed)
        <= {
            "intent_service_event",
            "clarification_request_event",
            "approval_service_event",
        }
    ):
        raise ValueError("evidence contract automated event types are invalid")


def _contract_run(
    contract: Mapping[str, object], run_id: str
) -> Mapping[str, object]:
    runs = contract.get("runs")
    if not isinstance(runs, list):
        raise ValueError("evidence contract run matrix is invalid")
    match = next(
        (
            run
            for run in runs
            if isinstance(run, Mapping) and run.get("run_id") == run_id
        ),
        None,
    )
    if not isinstance(match, Mapping):
        raise ValueError("evidence contract does not cover run")
    return match


def _parse_protocol_bytes(canonical_bytes: bytes) -> BenchmarkProtocol:
    raw = json.loads(canonical_bytes)
    if not isinstance(raw, dict):
        raise ValueError("protocol must be a JSON object")
    _reject_unknown(raw, _PROTOCOL_KEYS, "protocol")
    _require_keys(raw, _PROTOCOL_KEYS, "protocol")
    runs = raw["run_matrix"]
    budget = raw["attempt_budget"]
    lock = raw["execution_lock"]
    if (
        not isinstance(runs, list)
        or not isinstance(budget, dict)
        or not isinstance(lock, dict)
    ):
        raise ValueError("protocol has invalid run_matrix or attempt_budget")
    _reject_unknown(budget, _BUDGET_KEYS, "attempt_budget")
    _require_keys(budget, _BUDGET_KEYS, "attempt_budget")
    _reject_unknown(lock, _LOCK_KEYS, "execution_lock")
    _require_keys(lock, _LOCK_KEYS, "execution_lock")
    parsed_runs = tuple(_parse_run(item) for item in runs)
    return BenchmarkProtocol(
        schema=_string(raw["schema"], "schema"),
        arms=_string_tuple(raw["arms"], "arms"),
        fixtures=_string_tuple(raw["fixtures"], "fixtures"),
        run_matrix=parsed_runs,
        attempt_budget=AttemptBudget(
            **{key: _integer(budget[key], key) for key in _BUDGET_KEYS}
        ),
        execution_lock=ExecutionLock(
            **{key: _lock_value(lock[key], key) for key in _LOCK_KEYS}
        ),
        canonical_bytes=canonical_bytes,
    )


def validate_protocol(
    protocol: BenchmarkProtocol, repo_root: Path
) -> list[BenchmarkIssue]:
    """Validate immutable preregistration invariants without touching Providers."""
    _ = repo_root
    issues: list[BenchmarkIssue] = []
    if protocol.schema != "ai-sdlc-v2-benefit-protocol/v1":
        issues.append(BenchmarkIssue("protocol.schema", "unexpected protocol schema"))
    if protocol.arms != _ARMS:
        issues.append(
            BenchmarkIssue("protocol.arms", "arms must equal P,S,A00,A10,A11")
        )
    if protocol.fixtures != _FIXTURES:
        issues.append(
            BenchmarkIssue("protocol.fixtures", "fixtures must equal the frozen IDs")
        )
    pairs = {(run.arm, run.fixture) for run in protocol.run_matrix}
    if len(protocol.run_matrix) != 15 or len(pairs) != 15:
        issues.append(
            BenchmarkIssue("protocol.matrix", "run matrix must contain 15 unique pairs")
        )
    expected_pairs = {(arm, fixture) for arm in _ARMS for fixture in _FIXTURES}
    if pairs != expected_pairs:
        issues.append(
            BenchmarkIssue(
                "protocol.matrix", "run matrix must cover every arm and fixture"
            )
        )
    actual_schedule = tuple(
        (run.arm, run.fixture, run.position) for run in protocol.run_matrix
    )
    if actual_schedule != _SCHEDULE or any(
        run.run_id != f"{run.arm}:{run.fixture}" for run in protocol.run_matrix
    ):
        issues.append(
            BenchmarkIssue(
                "protocol.schedule", "run schedule must match each frozen row"
            )
        )
    for arm in _ARMS:
        positions = [run.position for run in protocol.run_matrix if run.arm == arm]
        if len(positions) != 3 or sum(positions) != 9:
            issues.append(
                BenchmarkIssue("protocol.schedule", f"{arm} mean position must equal 3")
            )
    if protocol.attempt_budget != AttemptBudget(33, 19, 4, 3, 7):
        issues.append(
            BenchmarkIssue("protocol.budget", "attempt budget must equal 33/19/4/3/7")
        )
    for key, expected in _EXPECTED_LOCK.items():
        if getattr(protocol.execution_lock, key) != expected:
            issues.append(
                BenchmarkIssue("protocol.lock", f"execution lock drift: {key}")
            )
    fixture_tree = protocol.execution_lock.fixture_tree_sha256
    fixture_commitment = protocol.execution_lock.fixture_commitment
    if fixture_tree == fixture_commitment == "pending-unbound":
        issues.append(
            BenchmarkIssue(
                "protocol.fixture-pending", "fixture commitment is pending Task 2"
            )
        )
    elif (
        fixture_tree != fixture_commitment
        or not _is_non_placeholder_digest(fixture_tree)
        or not _is_non_placeholder_digest(fixture_commitment)
    ):
        issues.append(
            BenchmarkIssue(
                "protocol.lock",
                "fixture tree and commitment must be the same SHA-256 digest",
            )
        )
    evidence_sha = protocol.execution_lock.evidence_contract_sha256
    evidence_commitment = protocol.execution_lock.evidence_contract_commitment
    if evidence_sha == evidence_commitment == "pending-unbound":
        issues.append(
            BenchmarkIssue(
                "protocol.evidence-contract-pending",
                "evidence contract commitment is pending Task 2",
            )
        )
    elif (
        evidence_sha != evidence_commitment
        or not _is_non_placeholder_digest(evidence_sha)
        or not _is_non_placeholder_digest(evidence_commitment)
    ):
        issues.append(
            BenchmarkIssue(
                "protocol.lock",
                "evidence contract and commitment must be the same SHA-256 digest",
            )
        )
    return issues


def _require_executable_protocol(protocol: BenchmarkProtocol) -> str:
    try:
        reparsed = _parse_protocol_bytes(protocol.canonical_bytes)
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("benchmark protocol canonical bytes are invalid") from error
    if reparsed != protocol:
        raise ValueError("benchmark protocol object does not match its canonical bytes")
    issues = validate_protocol(protocol, Path.cwd())
    if issues:
        details = ", ".join(issue.message for issue in issues)
        raise ValueError(f"benchmark protocol is not executable: {details}")
    return canonical_protocol_digest(protocol)


def _load_ledger(
    path: Path,
    protocol_digest: str,
    attempt_budget: AttemptBudget,
    evidence_contract_sha256: str,
) -> dict[str, object]:
    if not path.exists():
        return {
            "schema": _LEDGER_SCHEMA,
            "protocol_sha256": protocol_digest,
            "attempts_started": 0,
            "attempts": [],
            "runs": {},
        }
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("attempt ledger must be an object")
    _reject_unknown(raw, _LEDGER_KEYS, "attempt ledger")
    _require_keys(raw, _LEDGER_KEYS, "attempt ledger")
    if raw["schema"] != _LEDGER_SCHEMA:
        raise ValueError("attempt ledger has an unexpected schema")
    if raw["protocol_sha256"] != protocol_digest:
        raise ValueError("attempt ledger protocol digest does not match")
    if (
        not isinstance(raw["attempts_started"], int)
        or isinstance(raw["attempts_started"], bool)
        or raw["attempts_started"] < 0
    ):
        raise ValueError("attempt ledger has invalid attempts_started")
    if not isinstance(raw["attempts"], list):
        raise ValueError("attempt ledger has invalid attempts")
    if not isinstance(raw["runs"], dict):
        raise ValueError("attempt ledger has invalid run registry")
    attempts = raw["attempts"]
    if raw["attempts_started"] != len(attempts):
        raise ValueError("attempt ledger count does not match attempts")
    expected_ids = [f"attempt-{index:03d}" for index in range(1, len(attempts) + 1)]
    for item, expected in zip(attempts, expected_ids, strict=True):
        if not isinstance(item, dict):
            raise ValueError("attempt ledger attempt must be an object")
        _validate_persisted_attempt(item, expected)
    _validate_attempt_ledger_invariants(attempts, attempt_budget)
    _validate_run_registry(
        raw["runs"], attempts, evidence_contract_sha256=evidence_contract_sha256
    )
    return raw


def _build_sealed_run_evidence(
    *,
    run_id: str,
    run: Mapping[str, object],
    attempts: list[Mapping[str, object]],
    contract: Mapping[str, object],
    workspace_root: Path,
    recorded_at: str,
    contract_sha256: str,
) -> dict[str, object]:
    (
        artifacts,
        changed_files,
        changed_file_evidence,
        changed_scope_tree_digests,
    ) = _build_authoritative_file_evidence(contract, run_id, workspace_root)
    phase_evidence = _derive_authoritative_phases(run, attempts, recorded_at)
    automated_events = [
        {
            key: event[key]
            for key in (
                "type",
                "started_at",
                "ended_at",
                "latency_ms",
                "evidence_sha256",
            )
        }
        for attempt in attempts
        for event in attempt["service_events"]
    ]
    attempt_ids = [str(attempt["attempt_id"]) for attempt in attempts]
    terminal_sequence = max(int(attempt["sequence"]) for attempt in attempts)
    attempt_binding = _attempt_binding_digest(attempts)
    snapshot = {
        "run_id": run_id,
        "evidence_contract_sha256": contract_sha256,
        "attempt_ids": attempt_ids,
        "terminal_sequence": terminal_sequence,
        "attempt_binding_sha256": attempt_binding,
        "recorded_at": recorded_at,
        "phase_evidence": phase_evidence,
        "artifact_inventory": artifacts,
        "changed_files": changed_files,
        "changed_file_evidence": changed_file_evidence,
        "changed_scope_tree_digests": changed_scope_tree_digests,
        "automated_events": automated_events,
        "human_events": [],
    }
    snapshot["seal_binding_sha256"] = _seal_binding_digest(snapshot)
    return snapshot


def _build_authoritative_file_evidence(
    contract: Mapping[str, object], run_id: str, workspace_root: Path
) -> tuple[
    list[dict[str, object]],
    list[str],
    list[dict[str, object]],
    dict[str, str],
]:
    """Re-read the portable workspace under the exact contract authority."""
    workspace_root = workspace_root.resolve()
    if not workspace_root.is_dir():
        raise ValueError("run evidence workspace root is unavailable")
    rule = _contract_run(contract, run_id)
    artifacts: list[dict[str, object]] = []
    for slot in rule["artifact_slots"]:
        path = slot["path"]
        artifact_path = workspace_root.joinpath(*path.split("/"))
        try:
            resolved = artifact_path.resolve(strict=False)
            resolved.relative_to(workspace_root)
        except (OSError, ValueError) as error:
            raise ValueError("run evidence artifact escapes the workspace") from error
        observed = slot["applicable"] and artifact_path.is_file()
        digest: str | None = None
        size = 0
        if observed:
            with artifact_path.open("rb") as handle:
                payload = handle.read()
                size = os.fstat(handle.fileno()).st_size
            if size != len(payload):
                raise ValueError("run evidence artifact changed while being read")
            digest = sha256(payload).hexdigest()
            if size == 0:
                raise ValueError("observed run evidence artifacts cannot be empty")
        artifacts.append(
            {
                "path": path,
                "sha256": digest,
                "size_bytes": size,
                "category": slot["category"],
                "required": slot["required"],
                "applicable": slot["applicable"],
                "observed": observed,
            }
        )
    (
        changed_files,
        changed_file_evidence,
        changed_scope_tree_digests,
    ) = _derive_changed_files(
        workspace_root, rule["changed_files_scope"]
    )
    return (
        artifacts,
        changed_files,
        changed_file_evidence,
        changed_scope_tree_digests,
    )


def _derive_authoritative_phases(
    run: Mapping[str, object],
    attempts: list[Mapping[str, object]],
    recorded_at: str,
) -> dict[str, object]:
    del attempts
    controller_events = run.get("phase_events")
    if (
        not isinstance(controller_events, list)
        or [event.get("phase") for event in controller_events] != list(_RUN_PHASES)
        or any(event.get("ended_at") is None for event in controller_events)
        or controller_events[-1].get("ended_at") != recorded_at
    ):
        raise ValueError("run evidence requires one closed adjacent phase state")
    result: dict[str, object] = {}
    for event in controller_events:
        name = str(event["phase"])
        start_text = str(event["started_at"])
        end_text = str(event["ended_at"])
        payload = {"phase": name, "started_at": start_text, "ended_at": end_text}
        result[name] = {
            "started_at": start_text,
            "ended_at": end_text,
            "evidence_sha256": sha256(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        }
    return result


def _format_rfc3339(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validate_run_registry(
    value: Mapping[str, object],
    attempts: list[object],
    *,
    evidence_contract_sha256: str,
) -> None:
    registry = {f"{arm}:{fixture}" for arm, fixture, _position in _SCHEDULE}
    now = datetime.now(UTC)
    attempt_run_ids = {
        item.get("run_id") for item in attempts if isinstance(item, Mapping)
    }
    if not attempt_run_ids <= set(value):
        raise ValueError("attempt ledger run registry does not close over attempts")
    for run_id, run in value.items():
        if run_id not in registry or not isinstance(run, Mapping) or set(run) != {
            "run_id",
            "run_started_at",
            "current_phase",
            "phase_events",
            "sealed_evidence",
        }:
            raise ValueError("attempt ledger run record is invalid")
        started = _parse_rfc3339(run.get("run_started_at"))
        if run.get("run_id") != run_id or started is None or started > now:
            raise ValueError("attempt ledger run start is invalid")
        phase_events = run.get("phase_events")
        sealed = run.get("sealed_evidence")
        if (
            not isinstance(phase_events, list)
            or not 1 <= len(phase_events) <= len(_RUN_PHASES)
            or [event.get("phase") for event in phase_events]
            != list(_RUN_PHASES[: len(phase_events)])
            or run.get("current_phase") != phase_events[-1].get("phase")
        ):
            raise ValueError("attempt ledger phase events are invalid")
        cursor = started
        for index, event in enumerate(phase_events):
            if not isinstance(event, Mapping) or set(event) != {
                "phase",
                "started_at",
                "ended_at",
            }:
                raise ValueError("attempt ledger phase event is invalid")
            event_start = _parse_rfc3339(event.get("started_at"))
            event_end = _parse_rfc3339(event.get("ended_at"))
            is_last = index == len(phase_events) - 1
            if (
                event_start is None
                or event_start != cursor
                or event_start > now
                or (not is_last and (event_end is None or event_end < event_start))
                or (is_last and sealed is None and event.get("ended_at") is not None)
                or (is_last and sealed is not None and event_end is None)
                or (event_end is not None and event_end > now)
            ):
                raise ValueError("attempt ledger phase event time is invalid")
            if event_end is not None:
                cursor = event_end
        if phase_events[0].get("started_at") != run.get("run_started_at"):
            raise ValueError("attempt ledger phase start is not bound to run start")
        run_attempts = [
            item
            for item in attempts
            if isinstance(item, Mapping) and item.get("run_id") == run_id
        ]
        phase_index = _RUN_PHASES.index(str(run.get("current_phase")))
        if run_attempts and phase_index < _RUN_PHASES.index("provider"):
            raise ValueError("attempt ledger has a pre-provider orphan attempt")
        if phase_index > _RUN_PHASES.index("provider") and (
            not run_attempts
            or any(attempt.get("terminal") is not True for attempt in run_attempts)
        ):
            raise ValueError("post-provider run does not close over terminal attempts")
        provider_event = next(
            (event for event in phase_events if event.get("phase") == "provider"), None
        )
        for attempt in run_attempts:
            for attempt_event in attempt.get("history", []):
                attempt_at = _parse_rfc3339(attempt_event.get("recorded_at"))
                provider_start = _parse_rfc3339(
                    provider_event.get("started_at")
                    if isinstance(provider_event, Mapping)
                    else None
                )
                provider_end = _parse_rfc3339(
                    provider_event.get("ended_at")
                    if isinstance(provider_event, Mapping)
                    else None
                )
                if (
                    attempt_at is None
                    or provider_start is None
                    or attempt_at < provider_start
                    or attempt_at > now
                    or (provider_end is not None and attempt_at > provider_end)
                ):
                    raise ValueError("attempt ledger attempt event time is invalid")
            for service_event in attempt.get("service_events", []):
                service_start = _parse_rfc3339(service_event.get("started_at"))
                service_end = _parse_rfc3339(service_event.get("ended_at"))
                provider_start = _parse_rfc3339(
                    provider_event.get("started_at")
                    if isinstance(provider_event, Mapping)
                    else None
                )
                provider_end = _parse_rfc3339(
                    provider_event.get("ended_at")
                    if isinstance(provider_event, Mapping)
                    else None
                )
                if (
                    service_start is None
                    or provider_start is None
                    or service_start < provider_start
                    or service_start > now
                    or (provider_end is not None and service_start > provider_end)
                    or (
                        service_event.get("status") == "completed"
                        and (
                            service_end is None
                            or service_end > now
                            or service_end < service_start
                            or (
                                provider_end is not None
                                and service_end > provider_end
                            )
                        )
                    )
                ):
                    raise ValueError("attempt ledger service event time is invalid")
        if sealed is not None:
            if (
                run.get("current_phase") != "evaluation"
                or len(phase_events) != len(_RUN_PHASES)
            ):
                raise ValueError("sealed run is not in the final phase")
            _validate_sealed_run_evidence(
                run,
                sealed,
                run_attempts,
                now,
                evidence_contract_sha256=evidence_contract_sha256,
            )
        _require_public_ledger_value(run, "run registry")


def _validate_sealed_run_evidence(
    run: Mapping[str, object],
    sealed: object,
    attempts: list[Mapping[str, object]],
    now: datetime,
    *,
    evidence_contract_sha256: str,
) -> None:
    run_id = str(run.get("run_id"))
    keys = {
        "run_id",
        "evidence_contract_sha256",
        "attempt_ids",
        "terminal_sequence",
        "attempt_binding_sha256",
        "seal_binding_sha256",
        "recorded_at",
        "phase_evidence",
        "artifact_inventory",
        "changed_files",
        "changed_file_evidence",
        "changed_scope_tree_digests",
        "automated_events",
        "human_events",
    }
    if not isinstance(sealed, Mapping) or set(sealed) != keys or not attempts:
        raise ValueError("attempt ledger sealed run evidence is invalid")
    recorded = _parse_rfc3339(sealed.get("recorded_at"))
    attempt_ids = [attempt.get("attempt_id") for attempt in attempts]
    if (
        sealed.get("run_id") != run_id
        or sealed.get("evidence_contract_sha256") != evidence_contract_sha256
        or sealed.get("attempt_ids") != attempt_ids
        or any(attempt.get("terminal") is not True for attempt in attempts)
        or sealed.get("terminal_sequence")
        != max(attempt.get("sequence") for attempt in attempts)
        or sealed.get("attempt_binding_sha256") != _attempt_binding_digest(attempts)
        or sealed.get("seal_binding_sha256") != _seal_binding_digest(sealed)
        or recorded is None
        or recorded > now
        or any(
            (terminal_at := _parse_rfc3339(attempt["history"][-1].get("recorded_at")))
            is None
            or terminal_at > recorded
            for attempt in attempts
        )
        or sealed.get("human_events") != []
    ):
        raise ValueError("attempt ledger sealed run binding is invalid")
    expected_phases = _derive_authoritative_phases(
        run, attempts, str(sealed.get("recorded_at"))
    )
    if sealed.get("phase_evidence") != expected_phases:
        raise ValueError("attempt ledger sealed phase evidence is invalid")
    _validate_sealed_artifact_inventory(sealed.get("artifact_inventory"))
    _validate_sealed_changed_files(
        sealed.get("changed_files"), sealed.get("changed_file_evidence")
    )
    _validate_changed_scope_tree_digests(sealed.get("changed_scope_tree_digests"))
    expected_events = [
        {
            key: event[key]
            for key in (
                "type",
                "started_at",
                "ended_at",
                "latency_ms",
                "evidence_sha256",
            )
        }
        for attempt in attempts
        for event in attempt.get("service_events", [])
    ]
    if sealed.get("automated_events") != expected_events:
        raise ValueError("attempt ledger sealed service events are invalid")
    for attempt in attempts:
        for event in attempt.get("service_events", []):
            event_end = _parse_rfc3339(event.get("ended_at"))
            if event_end is None or event_end > recorded:
                raise ValueError("attempt ledger service event is after seal")


def _validate_sealed_artifact_inventory(value: object) -> None:
    if not isinstance(value, list) or not value:
        raise ValueError("attempt ledger sealed artifact inventory is invalid")
    paths: list[object] = []
    keys = {
        "path",
        "sha256",
        "size_bytes",
        "category",
        "required",
        "applicable",
        "observed",
    }
    for item in value:
        if not isinstance(item, Mapping) or set(item) != keys:
            raise ValueError("attempt ledger sealed artifact inventory is invalid")
        observed = item.get("observed")
        applicable = item.get("applicable")
        required = item.get("required")
        size = item.get("size_bytes")
        digest = item.get("sha256")
        if (
            not _is_safe_relative_path(item.get("path"))
            or item.get("category") not in {"setup", "governance", "delivery"}
            or not isinstance(required, bool)
            or not isinstance(applicable, bool)
            or not isinstance(observed, bool)
            or not _non_bool_int(size)
            or size < 0
            or (required and not applicable)
            or (observed and not applicable)
            or (observed and (size == 0 or not _is_non_placeholder_digest(digest)))
            or (not observed and (size != 0 or digest is not None))
        ):
            raise ValueError("attempt ledger sealed artifact inventory is invalid")
        paths.append(item.get("path"))
    if len(paths) != len(set(paths)):
        raise ValueError("attempt ledger sealed artifact paths are duplicated")


def _validate_sealed_changed_files(files: object, evidence: object) -> None:
    if (
        not isinstance(files, list)
        or not isinstance(evidence, list)
        or len(files) != len(evidence)
        or any(not _is_safe_relative_path(path) for path in files)
        or len(files) != len(set(files))
    ):
        raise ValueError("attempt ledger sealed changed files are invalid")
    for path, item in zip(files, evidence, strict=True):
        if not isinstance(item, Mapping) or set(item) != {
            "path",
            "baseline_sha256",
            "candidate_sha256",
        }:
            raise ValueError("attempt ledger sealed changed-file evidence is invalid")
        baseline = item.get("baseline_sha256")
        candidate = item.get("candidate_sha256")
        if (
            item.get("path") != path
            or (baseline is not None and not _is_non_placeholder_digest(baseline))
            or (candidate is not None and not _is_non_placeholder_digest(candidate))
            or baseline == candidate
        ):
            raise ValueError("attempt ledger sealed changed-file evidence is invalid")


def _attempt_binding_digest(attempts: list[Mapping[str, object]]) -> str:
    return sha256(
        json.dumps(
            attempts, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _seal_binding_digest(sealed: Mapping[str, object]) -> str:
    payload = {key: value for key, value in sealed.items() if key != "seal_binding_sha256"}
    return sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _derive_changed_files(
    workspace_root: Path, scope: Mapping[str, object]
) -> tuple[list[str], list[dict[str, object]], dict[str, str]]:
    baseline_root = scope["baseline_root"]
    candidate_root = scope["candidate_root"]
    include_paths = scope["include_paths"]
    changed: list[str] = []
    evidence: list[dict[str, object]] = []
    baseline_tree: list[dict[str, object]] = []
    candidate_tree: list[dict[str, object]] = []
    for relative in include_paths:
        baseline_bytes = _read_scoped_workspace_file(
            workspace_root, f"{baseline_root}/{relative}"
        )
        candidate_bytes = _read_scoped_workspace_file(
            workspace_root, f"{candidate_root}/{relative}"
        )
        baseline_digest = (
            sha256(baseline_bytes).hexdigest() if baseline_bytes is not None else None
        )
        candidate_digest = (
            sha256(candidate_bytes).hexdigest() if candidate_bytes is not None else None
        )
        baseline_tree.append({"path": relative, "sha256": baseline_digest})
        candidate_tree.append({"path": relative, "sha256": candidate_digest})
        if baseline_bytes == candidate_bytes:
            continue
        receipt_path = f"{candidate_root.rstrip('/')}/{relative}"
        changed.append(receipt_path)
        evidence.append(
            {
                "path": receipt_path,
                "baseline_sha256": baseline_digest,
                "candidate_sha256": candidate_digest,
            }
        )
    return (
        changed,
        evidence,
        {
            "baseline_sha256": _canonical_json_digest(baseline_tree),
            "candidate_sha256": _canonical_json_digest(candidate_tree),
        },
    )


def _canonical_json_digest(value: object) -> str:
    return sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _validate_changed_scope_tree_digests(value: object) -> None:
    if (
        not isinstance(value, Mapping)
        or set(value) != {"baseline_sha256", "candidate_sha256"}
        or any(not _is_non_placeholder_digest(digest) for digest in value.values())
    ):
        raise ValueError("attempt ledger changed-scope tree digests are invalid")


def _read_scoped_workspace_file(workspace_root: Path, relative: str) -> bytes | None:
    path = workspace_root.joinpath(*relative.split("/"))
    try:
        path.resolve(strict=False).relative_to(workspace_root)
    except (OSError, ValueError) as error:
        raise ValueError("changed-files scope escapes the workspace") from error
    if not path.is_file():
        return None
    with path.open("rb") as handle:
        payload = handle.read()
        size = os.fstat(handle.fileno()).st_size
    if size != len(payload):
        raise ValueError("changed-files input changed while being read")
    return payload


def _validate_service_event(event: Mapping[str, object]) -> None:
    if set(event) != {
        "type",
        "transaction_id",
        "attempt_id",
        "status",
        "started_at",
        "ended_at",
        "latency_ms",
        "service_evidence_sha256",
        "evidence_sha256",
    }:
        raise ValueError("service transaction event fields are invalid")
    started = _parse_rfc3339(event.get("started_at"))
    if (
        event.get("type")
        not in {
            "intent_service_event",
            "clarification_request_event",
            "approval_service_event",
        }
        or not isinstance(event.get("transaction_id"), str)
        or not event.get("transaction_id")
        or not isinstance(event.get("attempt_id"), str)
        or started is None
        or event.get("status") not in {"started", "completed"}
    ):
        raise ValueError("service transaction event is invalid")
    if event.get("status") == "started":
        if any(
            event.get(key) is not None
            for key in (
                "ended_at",
                "latency_ms",
                "service_evidence_sha256",
                "evidence_sha256",
            )
        ):
            raise ValueError("started service transaction must remain open")
        return
    ended = _parse_rfc3339(event.get("ended_at"))
    if (
        ended is None
        or ended < started
        or not _finite_number(event.get("latency_ms"))
        or not math.isclose(
            event["latency_ms"],
            (ended - started).total_seconds() * 1000,
            rel_tol=0,
            abs_tol=1e-6,
        )
        or not _is_non_placeholder_digest(event.get("evidence_sha256"))
        or not _is_non_placeholder_digest(event.get("service_evidence_sha256"))
    ):
        raise ValueError("service transaction event is invalid")


def _active_run_record(ledger: Mapping[str, object], run_id: str) -> dict[str, object]:
    runs = ledger.get("runs")
    run = runs.get(run_id) if isinstance(runs, Mapping) else None
    if not isinstance(run, dict):
        raise ValueError("run must be established before recording evidence")
    if run.get("sealed_evidence") is not None:
        raise ValueError("sealed run rejects late attempts and events")
    return run


def _is_safe_relative_path(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and not value.startswith(("/", "\\"))
        and re.match(r"^[A-Za-z]:[\\/]", value) is None
        and "\\" not in value
        and all(part not in {"", ".", ".."} for part in value.split("/"))
    )


def _new_attempt(
    attempt_id: str, request: AttemptRequest, attempts: list[object]
) -> dict[str, object]:
    arm = request.run_id.split(":", 1)[0]
    retried = (
        _attempt_by_id(attempts, request.retry_of_attempt_id)
        if request.kind == "technical_retry"
        else None
    )
    effective_kind = (
        retried["effective_kind"] if retried is not None else request.kind
    )
    retried_reserved = retried["history"][0] if retried is not None else None
    parent_attempt_id = (
        retried["parent_attempt_id"] if retried is not None else request.parent_attempt_id
    )
    role = retried["role"] if retried is not None else request.role
    parent_digest = (
        retried["parent_digest"] if retried is not None else request.parent_digest
    )
    candidate_digest = (
        retried_reserved["candidate_digest"]
        if retried_reserved is not None
        else request.candidate_digest
    )
    finding_digest = (
        retried_reserved["finding_digest"]
        if retried_reserved is not None
        else request.finding_digest
    )
    repair_digest = (
        retried_reserved["repair_digest"]
        if retried_reserved is not None
        else request.repair_digest
    )
    sequence = _next_event_sequence(attempts)
    reserved = {
        "sequence": sequence,
        "status": "reserved",
        "content_produced": False,
        "candidate_digest": candidate_digest,
        "finding_digest": finding_digest,
        "repair_digest": repair_digest,
        "close_digest": None,
        "terminal": False,
        "recorded_at": _now_rfc3339(),
        "child_session": None,
        "token_usage": None,
        "raw_provider_output_sha256": None,
    }
    return {
        "attempt_id": attempt_id,
        "run_id": request.run_id,
        "kind": request.kind,
        "effective_kind": effective_kind,
        "sequence": sequence,
        "arm": arm,
        "retry_reason": request.retry_reason,
        "retry_of_attempt_id": request.retry_of_attempt_id,
        "parent_attempt_id": parent_attempt_id,
        "role": role,
        "parent_digest": parent_digest,
        "candidate_digest": candidate_digest,
        "finding_digest": finding_digest,
        "repair_digest": repair_digest,
        "close_digest": None,
        "status": "reserved",
        "content_produced": False,
        "terminal": False,
        "recorded_at": reserved["recorded_at"],
        "child_session": None,
        "token_usage": None,
        "raw_provider_output_sha256": None,
        "service_events": [],
        "history": [reserved],
    }


def _validate_persisted_attempt(attempt: dict[str, object], expected_id: str) -> None:
    _require_public_ledger_value(attempt, "Provider attempt")
    _reject_unknown(attempt, _ATTEMPT_KEYS, "attempt ledger attempt")
    _require_keys(attempt, _ATTEMPT_KEYS, "attempt ledger attempt")
    if attempt["attempt_id"] != expected_id:
        raise ValueError("attempt ledger attempt IDs are corrupt")
    run_id = attempt["run_id"]
    registry = {f"{arm}:{fixture}": arm for arm, fixture, _position in _SCHEDULE}
    if not isinstance(run_id, str) or run_id not in registry:
        raise ValueError("attempt ledger attempt has an invalid run")
    if attempt["arm"] != registry[run_id]:
        raise ValueError("attempt ledger attempt has an invalid arm")
    kind = attempt["kind"]
    if not isinstance(kind, str) or kind not in _ATTEMPT_KINDS:
        raise ValueError("attempt ledger attempt has an invalid kind")
    effective_kind = attempt["effective_kind"]
    if (
        not isinstance(effective_kind, str)
        or effective_kind not in _ATTEMPT_KINDS - {"technical_retry"}
    ):
        raise ValueError("attempt ledger attempt has an invalid effective kind")
    if (
        not isinstance(attempt["sequence"], int)
        or isinstance(attempt["sequence"], bool)
        or attempt["sequence"] < 1
    ):
        raise ValueError("attempt ledger attempt has an invalid sequence")
    if not isinstance(attempt["content_produced"], bool) or not isinstance(
        attempt["terminal"], bool
    ):
        raise ValueError("attempt ledger attempt has invalid state flags")
    for key in (
        "retry_reason",
        "retry_of_attempt_id",
        "parent_attempt_id",
        "role",
    ):
        if attempt[key] is not None and not isinstance(attempt[key], str):
            raise ValueError(f"attempt ledger attempt has invalid {key}")
    for key in (
        "parent_digest",
        "candidate_digest",
        "finding_digest",
        "repair_digest",
        "close_digest",
    ):
        if attempt[key] is not None and (
            not isinstance(attempt[key], str) or not _is_digest(attempt[key])
        ):
            raise ValueError(f"attempt ledger attempt has invalid {key}")
    history = attempt["history"]
    if not isinstance(history, list) or not history:
        raise ValueError("attempt ledger attempt has invalid history")
    previous: Mapping[str, object] | None = None
    for event in history:
        if not isinstance(event, dict):
            raise ValueError("attempt ledger attempt event must be an object")
        _reject_unknown(event, _EVENT_KEYS, "attempt ledger attempt event")
        _require_keys(event, _EVENT_KEYS, "attempt ledger attempt event")
        _validate_event(effective_kind, run_id, previous, event)
        previous = event
    if any(attempt[key] != history[-1][key] for key in _EVENT_KEYS):
        raise ValueError("attempt ledger attempt state does not match its history")
    service_events = attempt["service_events"]
    if not isinstance(service_events, list):
        raise ValueError("attempt ledger service events are invalid")
    transaction_ids: list[object] = []
    for service_event in service_events:
        if not isinstance(service_event, Mapping):
            raise ValueError("attempt ledger service event is invalid")
        _validate_service_event(service_event)
        if service_event.get("attempt_id") != attempt["attempt_id"]:
            raise ValueError("attempt ledger service event attempt binding is invalid")
        transaction_ids.append(service_event.get("transaction_id"))
    if len(transaction_ids) != len(set(transaction_ids)):
        raise ValueError("attempt ledger service transaction is duplicated")
    if attempt["terminal"] is True and any(
        event.get("status") != "completed" for event in service_events
    ):
        raise ValueError("terminal attempt has an open service transaction")
    _validate_kind_shape(attempt, history[0])


def _validate_event(
    kind: str,
    run_id: str,
    previous: Mapping[str, object] | None,
    event: Mapping[str, object],
) -> None:
    status = event["status"]
    sequence = event["sequence"]
    if (
        not isinstance(sequence, int)
        or isinstance(sequence, bool)
        or sequence < 1
    ):
        raise ValueError("attempt ledger attempt event has invalid sequence")
    if not isinstance(status, str):
        raise ValueError("attempt ledger attempt event has invalid status")
    if not isinstance(event["content_produced"], bool) or not isinstance(
        event["terminal"], bool
    ):
        raise ValueError("attempt ledger attempt event has invalid state flags")
    recorded_at = _parse_rfc3339(event.get("recorded_at"))
    if recorded_at is None:
        raise ValueError("attempt ledger attempt event has invalid recorded_at")
    _validate_completion_evidence(event)
    for key in (
        "candidate_digest",
        "finding_digest",
        "repair_digest",
        "close_digest",
    ):
        if event[key] is not None and (
            not isinstance(event[key], str) or not _is_digest(event[key])
        ):
            raise ValueError(f"attempt ledger attempt event has invalid {key}")
    if previous is None:
        if (
            status != "reserved"
            or event["content_produced"]
            or event["terminal"]
            or event["close_digest"] is not None
            or event["child_session"] is not None
            or event["token_usage"] is not None
            or event["raw_provider_output_sha256"] is not None
        ):
            raise ValueError("attempt ledger attempt must begin reserved")
        return
    previous_status = previous["status"]
    previous_at = _parse_rfc3339(previous.get("recorded_at"))
    if previous_at is None or recorded_at < previous_at:
        raise ValueError("attempt ledger attempt event time is invalid")
    if previous["content_produced"] is True and event["content_produced"] is not True:
        raise ValueError("attempt ledger content_produced cannot regress")
    if sequence <= previous["sequence"]:
        raise ValueError("attempt ledger attempt event sequence is invalid")
    allowed = _TRANSITIONS.get(kind, {}).get(previous_status, set())
    if status not in allowed:
        raise ValueError(
            f"attempt ledger {kind} transition {previous_status}->{status} is invalid"
        )
    if event["terminal"] is not (status in _TERMINAL_STATUSES):
        raise ValueError("attempt ledger attempt terminal flag is invalid")
    if status == "completed" and event["content_produced"] is not True:
        raise ValueError("completed Provider attempt must have produced content")
    if status == "technical_failure" and event["content_produced"] is not False:
        raise ValueError("technical failure must be pre-output")
    if kind in {"primary_expert", "cross_risk_expert"} and (
        event["candidate_digest"] != previous["candidate_digest"]
        or event["repair_digest"] is not None
        or event["close_digest"] is not None
        or (
            status != "completed"
            and event["finding_digest"] != previous["finding_digest"]
        )
    ):
        raise ValueError("expert completion fields are invalid")
    if kind == "expert_rereview" and (
        event["candidate_digest"] != previous["candidate_digest"]
        or event["finding_digest"] != previous["finding_digest"]
        or event["repair_digest"] != previous["repair_digest"]
        or event["close_digest"] is not None
    ):
        raise ValueError("expert rereview completion fields are invalid")
    if kind == "technical_retry" and any(
        event[key] is not None
        for key in (
            "candidate_digest",
            "finding_digest",
            "repair_digest",
            "close_digest",
        )
    ):
        raise ValueError("technical retry completion fields are invalid")
    if status in {"candidate_ready", "review_pending"} and (
        kind != "writer"
        or not event["content_produced"]
        or not _is_digest(event["candidate_digest"])
    ):
        raise ValueError("writer Candidate checkpoint is invalid")
    if kind == "writer" and status == "candidate_ready":
        if previous_status == "reserved":
            if (
                event["finding_digest"] is not None
                or event["repair_digest"] is not None
            ):
                raise ValueError("initial Candidate cannot claim a repair")
        elif previous_status == "review_pending" and (
            not _is_digest(event["finding_digest"])
            or not _is_digest(event["repair_digest"])
            or event["candidate_digest"] == previous["candidate_digest"]
        ):
            raise ValueError("writer repair must bind a Finding and new Candidate")
        elif previous_status == "candidate_ready" and (
            not _is_digest(previous["finding_digest"])
            or not _is_digest(previous["repair_digest"])
            or not _is_digest(event["finding_digest"])
            or not _is_digest(event["repair_digest"])
            or event["finding_digest"] == previous["finding_digest"]
            or event["candidate_digest"] != previous["candidate_digest"]
        ):
            raise ValueError(
                "writer repair batch must bind a distinct Finding to the same Candidate"
            )
    if (
        kind == "writer"
        and status == "review_pending"
        and (
            event["candidate_digest"] != previous["candidate_digest"]
            or event["finding_digest"] != previous["finding_digest"]
            or event["repair_digest"] != previous["repair_digest"]
            or event["close_digest"] is not None
        )
    ):
        raise ValueError("review_pending must preserve the Candidate repair state")
    if kind == "writer" and status == "completed":
        if not _is_digest(event["candidate_digest"]):
            raise ValueError("completed writer requires a Candidate digest")
        if run_id.startswith("A11:") and previous_status != "review_pending":
            raise ValueError("A11 writer can Close only from review_pending")
        if run_id.startswith(("A10:", "A11:")) and not _is_digest(
            event["close_digest"]
        ):
            raise ValueError("completed A10/A11 writer requires a Close digest")
    if (
        kind == "writer"
        and status in _TERMINAL_STATUSES - {"completed"}
        and (
            event["candidate_digest"] != previous["candidate_digest"]
            or event["finding_digest"] != previous["finding_digest"]
            or event["repair_digest"] != previous["repair_digest"]
            or event["close_digest"] is not None
        )
    ):
        raise ValueError("terminal writer failure cannot introduce result bindings")


def _validate_kind_shape(
    attempt: Mapping[str, object], reserved: Mapping[str, object]
) -> None:
    kind = attempt["kind"]
    effective_kind = attempt["effective_kind"]
    if kind == "technical_retry":
        if attempt["retry_reason"] not in {
            "transport",
            "schema",
            "provider_pre_output",
        } or not isinstance(attempt["retry_of_attempt_id"], str):
            raise ValueError("attempt ledger technical retry is invalid")
    elif effective_kind != kind:
        raise ValueError("attempt ledger effective kind does not match kind")
    elif attempt["retry_reason"] is not None or attempt["retry_of_attempt_id"] is not None:
        raise ValueError("attempt ledger non-retry has retry fields")
    if effective_kind == "writer":
        if any(
            attempt[key] is not None
            for key in (
                "parent_attempt_id",
                "role",
            )
        ):
            raise ValueError("attempt ledger writer has invalid reservation fields")
        if attempt["arm"] == "A11" and not _is_digest(attempt["parent_digest"]):
            raise ValueError("attempt ledger A11 writer requires a parent digest")
        if any(
            reserved[key] is not None
            for key in ("candidate_digest", "finding_digest", "repair_digest")
        ):
            raise ValueError("attempt ledger writer cannot reserve result bindings")
        return
    if not isinstance(attempt["parent_attempt_id"], str):
        raise ValueError("attempt ledger expert requires a parent attempt")
    if not _is_digest(attempt["parent_digest"]) or not _is_digest(
        reserved["candidate_digest"]
    ):
        raise ValueError("attempt ledger expert requires parent and Candidate digests")
    if effective_kind == "primary_expert" and attempt["role"] != "primary":
        raise ValueError("attempt ledger primary expert role is invalid")
    if effective_kind == "cross_risk_expert" and attempt["role"] != "cross-risk":
        raise ValueError("attempt ledger cross-risk expert role is invalid")
    if effective_kind in {"primary_expert", "cross_risk_expert"} and (
        reserved["finding_digest"] is not None
        or reserved["repair_digest"] is not None
    ):
        raise ValueError("attempt ledger primary expert has repair bindings")
    if effective_kind == "expert_rereview" and (
        attempt["role"] not in {"primary", "cross-risk"}
        or not _is_digest(reserved["finding_digest"])
        or not _is_digest(reserved["repair_digest"])
    ):
        raise ValueError("attempt ledger rereview binding is invalid")


def _validate_attempt_ledger_invariants(
    attempts: list[object], attempt_budget: AttemptBudget
) -> None:
    if len(attempts) > attempt_budget.limit:
        raise ValueError("attempt ledger invariant: Provider attempt budget exceeded")
    child_sessions = [
        attempt.get("child_session")
        for attempt in attempts
        if isinstance(attempt, Mapping) and attempt.get("terminal") is True
    ]
    if len(child_sessions) != len(set(child_sessions)):
        raise ValueError(
            "attempt ledger invariant: child session must be globally unique"
        )
    transaction_ids = [
        event.get("transaction_id")
        for attempt in attempts
        if isinstance(attempt, Mapping)
        for event in attempt.get("service_events", [])
        if isinstance(event, Mapping)
    ]
    if len(transaction_ids) != len(set(transaction_ids)):
        raise ValueError(
            "attempt ledger invariant: service transaction must be globally unique"
        )
    kind_limits = {
        "writer": 15,
        "primary_expert": 3,
        "cross_risk_expert": 1,
        "expert_rereview": attempt_budget.max_expert_rereviews,
        "technical_retry": attempt_budget.max_pre_output_retries,
    }
    for kind, limit in kind_limits.items():
        if _count_kind(attempts, kind) > limit:
            raise ValueError(
                f"attempt ledger invariant: {kind} topology budget exceeded"
            )
    unique_keys = {
        "writer": "run_id",
        "primary_expert": "run_id",
        "cross_risk_expert": "run_id",
        "expert_rereview": "parent_attempt_id",
        "technical_retry": "retry_of_attempt_id",
    }
    for kind, key in unique_keys.items():
        values = [
            attempt.get(key)
            for attempt in attempts
            if isinstance(attempt, Mapping) and attempt.get("kind") == kind
        ]
        if len(values) != len(set(values)):
            raise ValueError(
                f"attempt ledger invariant: duplicate {kind} topology is forbidden"
            )
    sequences = [
        event.get("sequence")
        for attempt in attempts
        if isinstance(attempt, Mapping)
        for event in attempt.get("history", [])
        if isinstance(event, Mapping)
    ]
    if sorted(sequences) != list(range(1, len(sequences) + 1)):
        raise ValueError("attempt ledger invariant: event sequence is corrupt")
    event_timeline = sorted(
        (
            event
            for attempt in attempts
            if isinstance(attempt, Mapping)
            for event in attempt.get("history", [])
            if isinstance(event, Mapping)
        ),
        key=lambda event: event["sequence"],
    )
    previous_at: datetime | None = None
    current_time = datetime.now(UTC)
    for event in event_timeline:
        recorded_at = _parse_rfc3339(event.get("recorded_at"))
        if recorded_at is None or (
            previous_at is not None and recorded_at < previous_at
        ) or recorded_at > current_time:
            raise ValueError("attempt ledger invariant: event time moved backwards")
        previous_at = recorded_at
    reservation_sequences = [
        attempt["history"][0]["sequence"]
        for attempt in attempts
        if isinstance(attempt, Mapping)
    ]
    if reservation_sequences != sorted(reservation_sequences):
        raise ValueError("attempt ledger invariant: reservation sequence is corrupt")
    _validate_security_first_review_baselines(attempts)
    prior: list[Mapping[str, object]] = []
    for raw_attempt in attempts:
        if not isinstance(raw_attempt, Mapping):
            raise ValueError("attempt ledger attempt must be an object")
        kind = raw_attempt["kind"]
        if kind == "technical_retry":
            retried = _attempt_by_id(prior, raw_attempt["retry_of_attempt_id"])
            retried_reserved = (
                retried["history"][0] if retried is not None else None
            )
            reserved = raw_attempt["history"][0]
            if (
                retried is None
                or retried["run_id"] != raw_attempt["run_id"]
                or retried["status"] != "technical_failure"
                or retried["content_produced"] is not False
                or retried["sequence"] >= reserved["sequence"]
                or raw_attempt["effective_kind"] != retried["effective_kind"]
                or raw_attempt["parent_attempt_id"]
                != retried["parent_attempt_id"]
                or raw_attempt["role"] != retried["role"]
                or raw_attempt["parent_digest"] != retried["parent_digest"]
                or any(
                    reserved[key] != retried_reserved[key]
                    for key in (
                        "candidate_digest",
                        "finding_digest",
                        "repair_digest",
                    )
                )
            ):
                raise ValueError(
                    "attempt ledger technical retry effective lineage is invalid"
                )
            _validate_retry_effective_role_active_parent(
                prior, retried, reserved["sequence"]
            )
        elif kind in {"primary_expert", "cross_risk_expert"}:
            if raw_attempt["arm"] != "A11":
                raise ValueError("attempt ledger expert must belong to A11")
            if (
                kind == "cross_risk_expert"
                and raw_attempt["run_id"] != "A11:multi-tenant-security-review"
            ):
                raise ValueError(
                    "attempt ledger cross-risk expert must use the security run"
                )
            writer = _attempt_by_id(prior, raw_attempt["parent_attempt_id"])
            reserved = raw_attempt["history"][0]
            writer_state = (
                _state_before_sequence(writer, reserved["sequence"])
                if writer is not None
                else None
            )
            if (
                writer is None
                or writer_state is None
                or writer["effective_kind"] != "writer"
                or writer["run_id"] != raw_attempt["run_id"]
                or writer["parent_digest"] != raw_attempt["parent_digest"]
                or writer_state["status"] != "review_pending"
                or writer_state["candidate_digest"] != reserved["candidate_digest"]
            ):
                raise ValueError("attempt ledger expert parent chain is invalid")
        elif kind == "expert_rereview":
            expert = _attempt_by_id(prior, raw_attempt["parent_attempt_id"])
            writer = (
                _attempt_by_id(prior, expert["parent_attempt_id"])
                if expert is not None
                else None
            )
            reserved = raw_attempt["history"][0]
            writer_state = (
                _state_before_sequence(writer, reserved["sequence"])
                if writer is not None
                else None
            )
            if (
                expert is None
                or writer is None
                or writer_state is None
                or raw_attempt["arm"] != "A11"
                or expert["run_id"] != raw_attempt["run_id"]
                or writer["run_id"] != raw_attempt["run_id"]
                or expert["effective_kind"]
                not in {"primary_expert", "cross_risk_expert"}
                or expert["status"] != "completed"
                or expert["sequence"] >= reserved["sequence"]
                or expert["role"] != raw_attempt["role"]
                or expert["parent_digest"] != raw_attempt["parent_digest"]
                or expert["finding_digest"] != reserved["finding_digest"]
                or expert["candidate_digest"] == reserved["candidate_digest"]
                or writer_state["status"] != "review_pending"
                or writer_state["candidate_digest"]
                != reserved["candidate_digest"]
                or (
                    repair := _find_repair_event(
                        writer,
                        reserved["finding_digest"],
                        reserved["repair_digest"],
                        reserved["candidate_digest"],
                    )
                )
                is None
                or repair["sequence"] >= reserved["sequence"]
            ):
                raise ValueError("attempt ledger rereview parent chain is invalid")
        prior.append(raw_attempt)
    for raw_attempt in attempts:
        if (
            not isinstance(raw_attempt, Mapping)
            or raw_attempt.get("effective_kind") != "writer"
        ):
            continue
        required_roles = _required_first_review_roles(raw_attempt)
        history = raw_attempt.get("history")
        if not isinstance(history, list):
            raise ValueError("attempt ledger invariant: writer history is invalid")
        previous: Mapping[str, object] | None = None
        for event in history:
            if not isinstance(event, Mapping):
                raise ValueError("attempt ledger invariant: writer event is invalid")
            if (
                previous is not None
                and event.get("status") == "candidate_ready"
                and previous.get("status") != "reserved"
            ):
                completed_first_reviews = [
                    attempt
                    for attempt in attempts
                    if isinstance(attempt, Mapping)
                    and attempt.get("parent_attempt_id")
                    == raw_attempt.get("attempt_id")
                    and attempt.get("effective_kind")
                    in {"primary_expert", "cross_risk_expert"}
                    and attempt.get("status") == "completed"
                    and attempt.get("sequence") < event.get("sequence")
                ]
                completed_first_review_roles = {
                    attempt.get("role") for attempt in completed_first_reviews
                }
                if not required_roles.issubset(completed_first_review_roles):
                    raise ValueError(
                        "attempt ledger invariant: writer repair requires every required first-review completion"
                    )
                expert = next(
                    (
                        attempt
                        for attempt in completed_first_reviews
                        if attempt.get("finding_digest")
                        == event.get("finding_digest")
                    ),
                    None,
                )
                if expert is None:
                    raise ValueError(
                        "attempt ledger invariant: writer repair has no owning expert Finding"
                    )
            previous = event
        if raw_attempt.get("status") == "completed" and raw_attempt.get("arm") == "A11":
            try:
                _validate_writer_close(attempts, raw_attempt, history[-1])
            except ValueError as error:
                raise ValueError(f"attempt ledger invariant: {error}") from error
        if raw_attempt.get("status") == "needs_operator":
            _validate_writer_conflict(attempts, raw_attempt)


def _state_before_sequence(
    attempt: Mapping[str, object], sequence: object
) -> Mapping[str, object] | None:
    if not isinstance(sequence, int):
        return None
    states = [
        event
        for event in attempt["history"]
        if isinstance(event, Mapping)
        and isinstance(event.get("sequence"), int)
        and event["sequence"] < sequence
    ]
    return max(states, key=lambda event: event["sequence"], default=None)


def _validate_retry_effective_role_active_parent(
    attempts: list[object], retried: Mapping[str, object], sequence: object
) -> None:
    effective_kind = retried.get("effective_kind")
    if effective_kind == "writer":
        return
    reserved = retried["history"][0]
    if effective_kind in {"primary_expert", "cross_risk_expert"}:
        writer = _attempt_by_id(attempts, retried.get("parent_attempt_id"))
        writer_state = (
            _state_before_sequence(writer, sequence) if writer is not None else None
        )
        if (
            writer is None
            or writer_state is None
            or writer.get("effective_kind") != "writer"
            or writer.get("run_id") != retried.get("run_id")
            or writer.get("parent_digest") != retried.get("parent_digest")
            or writer_state.get("status") != "review_pending"
            or writer_state.get("candidate_digest")
            != reserved["candidate_digest"]
        ):
            raise ValueError("technical retry effective role requires an active parent")
        return
    expert = _attempt_by_id(attempts, retried.get("parent_attempt_id"))
    writer = (
        _attempt_by_id(attempts, expert.get("parent_attempt_id"))
        if expert is not None
        else None
    )
    writer_state = (
        _state_before_sequence(writer, sequence) if writer is not None else None
    )
    repair = (
        _find_repair_event(
            writer,
            reserved["finding_digest"],
            reserved["repair_digest"],
            reserved["candidate_digest"],
        )
        if writer is not None
        else None
    )
    if (
        effective_kind != "expert_rereview"
        or expert is None
        or writer is None
        or writer_state is None
        or repair is None
        or expert.get("effective_kind")
        not in {"primary_expert", "cross_risk_expert"}
        or expert.get("status") != "completed"
        or expert.get("sequence") >= sequence
        or expert.get("run_id") != retried.get("run_id")
        or writer.get("run_id") != retried.get("run_id")
        or expert.get("role") != retried.get("role")
        or expert.get("parent_digest") != retried.get("parent_digest")
        or expert.get("finding_digest") != reserved["finding_digest"]
        or expert.get("candidate_digest") == reserved["candidate_digest"]
        or writer_state.get("status") != "review_pending"
        or writer_state.get("candidate_digest") != reserved["candidate_digest"]
        or repair.get("sequence") >= sequence
    ):
        raise ValueError("technical retry effective role requires an active parent")


def _required_first_review_roles(writer: Mapping[str, object]) -> set[str]:
    if writer.get("arm") != "A11":
        return set()
    roles = {"primary"}
    if writer.get("run_id") == "A11:multi-tenant-security-review":
        roles.add("cross-risk")
    return roles


def _validate_writer_conflict(
    attempts: list[object], writer: Mapping[str, object]
) -> None:
    if (
        writer.get("effective_kind") != "writer"
        or writer.get("run_id") != "A11:multi-tenant-security-review"
    ):
        raise ValueError("needs_operator is restricted to the A11 security run")
    history = writer.get("history")
    if (
        not isinstance(history, list)
        or len(history) < 2
        or not isinstance(history[-2], Mapping)
        or history[-2].get("status") != "review_pending"
    ):
        raise ValueError("security needs_operator requires a review-pending Candidate")
    experts = [
        attempt
        for attempt in attempts
        if isinstance(attempt, Mapping)
        and attempt.get("parent_attempt_id") == writer.get("attempt_id")
        and attempt.get("effective_kind") in {"primary_expert", "cross_risk_expert"}
        and attempt.get("status") == "completed"
        and attempt.get("sequence") < writer.get("sequence")
    ]
    role_findings = {
        attempt.get("role"): attempt.get("finding_digest") for attempt in experts
    }
    if (
        set(role_findings) != {"primary", "cross-risk"}
        or any(not _is_non_placeholder_digest(value) for value in role_findings.values())
        or len(set(role_findings.values())) != 2
        or any(
            isinstance(attempt, Mapping)
            and attempt.get("run_id") == writer.get("run_id")
            and attempt.get("effective_kind") == "expert_rereview"
            for attempt in attempts
        )
        or any(
            isinstance(event, Mapping) and event.get("repair_digest") is not None
            for event in history
        )
    ):
        raise ValueError(
            "security needs_operator requires two distinct immutable required expert Findings"
        )


def _validate_security_first_review_baselines(attempts: list[object]) -> None:
    for writer in attempts:
        if (
            not isinstance(writer, Mapping)
            or writer.get("effective_kind") != "writer"
            or writer.get("run_id") != "A11:multi-tenant-security-review"
        ):
            continue
        first_reviews = [
            attempt
            for attempt in attempts
            if isinstance(attempt, Mapping)
            and attempt.get("parent_attempt_id") == writer.get("attempt_id")
            and attempt.get("kind")
            in {"primary_expert", "cross_risk_expert"}
        ]
        if not first_reviews:
            continue
        first_reviews.sort(key=lambda attempt: attempt["history"][0]["sequence"])
        baseline = first_reviews[0]["history"][0]["candidate_digest"]
        if any(
            attempt["history"][0]["candidate_digest"] != baseline
            for attempt in first_reviews
        ):
            raise ValueError(
                "attempt ledger invariant: security first-review Candidate baseline changed"
            )


@contextmanager
def _ledger_lock(path: Path):
    """Hold an advisory cross-process lock for the whole read/validate/write transaction."""
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _load_frozen_schema(name: str) -> Mapping[str, object]:
    raw = json.loads((_BENCHMARK_ROOT / "schemas" / name).read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError(f"frozen schema {name} must be a JSON object")
    return raw


def _validate_json_schema(
    value: object,
    schema: Mapping[str, object],
    path: str,
    scope: str,
    issues: list[BenchmarkIssue],
) -> None:
    """Validate the frozen, deliberately small JSON Schema subset deterministically."""
    expected_type = schema.get("type")
    expected_types = (
        [expected_type]
        if isinstance(expected_type, str)
        else expected_type
        if isinstance(expected_type, list)
        else []
    )
    if expected_types and not any(
        isinstance(item, str) and _matches_json_type(value, item)
        for item in expected_types
    ):
        label = " or ".join(str(item) for item in expected_types)
        issues.append(
            BenchmarkIssue(f"{scope}.schema", f"{path} must be {label}")
        )
        return
    if "const" in schema and value != schema["const"]:
        issues.append(BenchmarkIssue(f"{scope}.schema", f"{path} does not match const"))
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        issues.append(BenchmarkIssue(f"{scope}.schema", f"{path} is outside enum"))
    if isinstance(value, Mapping):
        properties = schema.get("properties")
        property_schemas = properties if isinstance(properties, Mapping) else {}
        required = schema.get("required")
        if isinstance(required, list):
            for key in required:
                if isinstance(key, str) and key not in value:
                    issues.append(
                        BenchmarkIssue(f"{scope}.schema", f"{path}.{key} is required")
                    )
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in property_schemas:
                    issues.append(
                        BenchmarkIssue(
                            f"{scope}.schema", f"{path}.{key} is not allowed"
                        )
                    )
        for key, child_schema in property_schemas.items():
            if (
                key in value
                and isinstance(key, str)
                and isinstance(child_schema, Mapping)
            ):
                _validate_json_schema(
                    value[key], child_schema, f"{path}.{key}", scope, issues
                )
    elif isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            issues.append(BenchmarkIssue(f"{scope}.schema", f"{path} has too few items"))
        if isinstance(maximum, int) and len(value) > maximum:
            issues.append(
                BenchmarkIssue(f"{scope}.schema", f"{path} has too many items")
            )
        items = schema.get("items")
        if isinstance(items, Mapping):
            for index, child in enumerate(value):
                _validate_json_schema(child, items, f"{path}[{index}]", scope, issues)
    elif isinstance(value, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        pattern = schema.get("pattern")
        if isinstance(minimum, int) and len(value) < minimum:
            issues.append(BenchmarkIssue(f"{scope}.schema", f"{path} is too short"))
        if isinstance(maximum, int) and len(value) > maximum:
            issues.append(BenchmarkIssue(f"{scope}.schema", f"{path} is too long"))
        if isinstance(pattern, str) and re.fullmatch(pattern, value) is None:
            issues.append(
                BenchmarkIssue(f"{scope}.schema", f"{path} does not match pattern")
            )
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            issues.append(BenchmarkIssue(f"{scope}.schema", f"{path} is below minimum"))
        if isinstance(maximum, (int, float)) and value > maximum:
            issues.append(BenchmarkIssue(f"{scope}.schema", f"{path} is above maximum"))


def _matches_json_type(value: object, expected_type: str) -> bool:
    if expected_type == "object":
        return isinstance(value, Mapping)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return _finite_number(value)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "null":
        return value is None
    return False


def _validate_reservation_request(attempts: object, request: AttemptRequest) -> None:
    if not isinstance(attempts, list):
        raise ValueError("attempt ledger has invalid attempts")
    registry = {f"{arm}:{fixture}": arm for arm, fixture, _position in _SCHEDULE}
    if request.run_id not in registry:
        raise ValueError("run is not in the canonical registry")
    arm = registry[request.run_id]
    if request.arm is not None and request.arm != arm:
        raise ValueError("attempt arm does not match canonical run")
    if request.kind == "content_retry" or request.retry_reason == "content":
        raise ValueError("content retries are forbidden")
    if request.kind not in _ATTEMPT_KINDS:
        raise ValueError("attempt role is not preregistered")
    if request.kind == "technical_retry":
        if any(
            value is not None
            for value in (
                request.parent_attempt_id,
                request.role,
                request.parent_digest,
                request.candidate_digest,
                request.finding_digest,
                request.repair_digest,
            )
        ):
            raise ValueError("technical retry cannot carry expert bindings")
        if request.retry_reason not in {"transport", "schema", "provider_pre_output"}:
            raise ValueError("technical retry must be pre-output")
        if _count_kind(attempts, "technical_retry") >= 3:
            raise ValueError("technical retry budget is exhausted")
        if any(
            item.get("kind") == "technical_retry"
            and item.get("retry_of_attempt_id") == request.retry_of_attempt_id
            for item in attempts
        ):
            raise ValueError("failed attempt already has a technical retry")
        prior = _attempt_by_id(attempts, request.retry_of_attempt_id)
        if prior is None or prior.get("run_id") != request.run_id:
            raise ValueError("technical retry requires a prior attempt")
        if (
            prior.get("status") != "technical_failure"
            or prior.get("content_produced") is not False
        ):
            raise ValueError("technical retry requires terminated pre-output failure")
        _validate_retry_effective_role_active_parent(
            attempts, prior, _next_event_sequence(attempts)
        )
        return
    if request.kind == "writer":
        if any(
            value is not None
            for value in (
                request.retry_reason,
                request.retry_of_attempt_id,
                request.parent_attempt_id,
                request.role,
                request.candidate_digest,
                request.finding_digest,
                request.repair_digest,
            )
        ):
            raise ValueError("writer reservation fields are invalid")
        if request.parent_digest is not None and not _is_digest(request.parent_digest):
            raise ValueError("writer parent digest is invalid")
        if arm == "A11" and not _is_digest(request.parent_digest):
            raise ValueError("A11 writer requires a parent digest")
        if _count_kind(attempts, "writer") >= 15:
            raise ValueError("normal writer topology is exhausted")
        if any(
            item.get("kind") == "writer" and item.get("run_id") == request.run_id
            for item in attempts
        ):
            raise ValueError("duplicate run replacement is forbidden")
        return
    if arm != "A11":
        raise ValueError("expert roles are only permitted for A11")
    if request.retry_reason is not None or request.retry_of_attempt_id is not None:
        raise ValueError("expert cannot carry retry fields")
    if request.kind == "primary_expert":
        if _count_kind(attempts, "primary_expert") >= 3:
            raise ValueError("primary expert topology is exhausted")
        if any(
            item.get("kind") == "primary_expert"
            and item.get("run_id") == request.run_id
            for item in attempts
        ):
            raise ValueError("duplicate primary expert is forbidden")
        if request.role != "primary":
            raise ValueError("expert requires role and parent/candidate digests")
    elif request.kind == "cross_risk_expert":
        if (
            request.run_id != "A11:multi-tenant-security-review"
            or _count_kind(attempts, "cross_risk_expert") >= 1
        ):
            raise ValueError("cross-risk expert topology is exhausted")
        if request.role != "cross-risk":
            raise ValueError("expert requires role and parent/candidate digests")
    else:
        _validate_rereview_reservation(attempts, request)
        return
    if request.finding_digest is not None or request.repair_digest is not None:
        raise ValueError("primary expert cannot reserve repair bindings")
    writer = _attempt_by_id(attempts, request.parent_attempt_id)
    if (
        writer is None
        or writer.get("effective_kind") != "writer"
        or writer.get("run_id") != request.run_id
        or writer.get("status") != "review_pending"
    ):
        raise ValueError("expert requires a writer review_pending Candidate checkpoint")
    if (
        not _is_digest(request.parent_digest)
        or not _is_digest(request.candidate_digest)
        or request.parent_digest != writer.get("parent_digest")
        or request.candidate_digest != writer.get("candidate_digest")
    ):
        raise ValueError("expert parent/Candidate chain does not match the writer")


def _validate_rereview_reservation(
    attempts: list[object], request: AttemptRequest
) -> None:
    if _count_kind(attempts, "expert_rereview") >= 4:
        raise ValueError("expert rereview topology is exhausted")
    expert = _attempt_by_id(attempts, request.parent_attempt_id)
    if (
        expert is None
        or expert.get("run_id") != request.run_id
        or expert.get("effective_kind")
        not in {"primary_expert", "cross_risk_expert"}
    ):
        raise ValueError("expert rereview requires an existing expert reservation")
    if expert.get("status") != "completed" or expert.get("role") != request.role:
        raise ValueError("expert rereview requires completed matching expert")
    if any(
        item.get("kind") == "expert_rereview"
        and item.get("parent_attempt_id") == request.parent_attempt_id
        for item in attempts
    ):
        raise ValueError("expert can have at most one rereview")
    writer = _attempt_by_id(attempts, expert.get("parent_attempt_id"))
    if writer is None or writer.get("status") != "review_pending":
        raise ValueError("expert rereview requires the same writer review_pending")
    if request.parent_digest != expert.get("parent_digest"):
        raise ValueError("expert rereview parent chain changed")
    if request.finding_digest != expert.get("finding_digest") or not _is_digest(
        request.finding_digest
    ):
        raise ValueError("expert rereview must bind the first Finding")
    if request.candidate_digest == expert.get("candidate_digest"):
        raise ValueError("expert rereview requires a new Candidate")
    if request.candidate_digest != writer.get("candidate_digest"):
        raise ValueError("expert rereview Candidate does not match the same writer")
    if (
        not _is_digest(request.repair_digest)
        or _find_repair_event(
            writer,
            request.finding_digest,
            request.repair_digest,
            request.candidate_digest,
        )
        is None
    ):
        raise ValueError("expert rereview requires the same-writer repair")


def _apply_attempt_transition(
    attempts: list[object],
    attempt: dict[str, object],
    completion: AttemptCompletion,
) -> None:
    current_status = attempt["status"]
    if current_status in _TERMINAL_STATUSES:
        raise ValueError("Provider attempt completion was already recorded")
    event = {
        "sequence": _next_event_sequence(attempts),
        "status": completion.status,
        "content_produced": completion.content_produced,
        "candidate_digest": completion.candidate_digest
        if completion.candidate_digest is not None
        else attempt["candidate_digest"],
        "finding_digest": completion.finding_digest
        if completion.finding_digest is not None
        else attempt["finding_digest"],
        "repair_digest": completion.repair_digest
        if completion.repair_digest is not None
        else attempt["repair_digest"],
        "close_digest": completion.close_digest
        if completion.close_digest is not None
        else attempt["close_digest"],
        "terminal": completion.status in _TERMINAL_STATUSES,
        "recorded_at": _now_rfc3339(),
        "child_session": completion.child_session,
        "token_usage": dict(completion.token_usage)
        if completion.token_usage is not None
        else None,
        "raw_provider_output_sha256": completion.raw_provider_output_sha256,
    }
    history = attempt["history"]
    if not isinstance(history, list) or not history:
        raise ValueError("attempt ledger attempt has invalid history")
    _validate_event(attempt["effective_kind"], attempt["run_id"], history[-1], event)
    _require_public_ledger_value(event, "Provider completion")
    history.append(event)
    for key in _EVENT_KEYS:
        attempt[key] = event[key]


def _validate_writer_close(
    attempts: list[object],
    writer: Mapping[str, object],
    event: Mapping[str, object],
) -> None:
    fixture = str(writer["run_id"]).split(":", 1)[1]
    required = {"primary"}
    if fixture == "multi-tenant-security-review":
        required.add("cross-risk")
    experts = {
        item.get("role"): item
        for item in attempts
        if isinstance(item, Mapping)
        and item.get("parent_attempt_id") == writer["attempt_id"]
        and item.get("effective_kind")
        in {"primary_expert", "cross_risk_expert"}
        and item.get("status") == "completed"
        and item.get("sequence") < event.get("sequence")
    }
    if not required.issubset(experts):
        raise ValueError("writer Close requires every required expert completion")
    for role in required:
        expert = experts[role]
        finding_digest = expert.get("finding_digest")
        if finding_digest is None:
            continue
        repair = _find_repair_event(
            writer,
            finding_digest,
            None,
            event["candidate_digest"],
        )
        if repair is None:
            raise ValueError("writer Close requires a same-writer repair")
        rereview = next(
            (
                item
                for item in attempts
                if isinstance(item, Mapping)
                and item.get("effective_kind") == "expert_rereview"
                and item.get("parent_attempt_id") == expert["attempt_id"]
                and item.get("status") == "completed"
                and item.get("sequence") < event.get("sequence")
                and item.get("finding_digest") == finding_digest
                and item.get("repair_digest") == repair["repair_digest"]
                and item.get("candidate_digest") == event["candidate_digest"]
            ),
            None,
        )
        if rereview is None:
            raise ValueError("writer Close requires the bound expert rereview")


def _find_repair_event(
    writer: Mapping[str, object],
    finding_digest: object,
    repair_digest: object,
    candidate_digest: object,
) -> Mapping[str, object] | None:
    history = writer.get("history")
    if not isinstance(history, list):
        return None
    return next(
        (
            event
            for event in history
            if isinstance(event, Mapping)
            and event.get("status") == "candidate_ready"
            and event.get("finding_digest") == finding_digest
            and (repair_digest is None or event.get("repair_digest") == repair_digest)
            and event.get("candidate_digest") == candidate_digest
        ),
        None,
    )


def _count_kind(attempts: list[object], kind: str) -> int:
    return sum(
        isinstance(item, Mapping) and item.get("kind") == kind for item in attempts
    )


def _next_event_sequence(attempts: list[object]) -> int:
    return (
        sum(
            len(history)
            for attempt in attempts
            if isinstance(attempt, Mapping)
            and isinstance((history := attempt.get("history")), list)
        )
        + 1
    )


def _attempt_by_id(
    attempts: list[object], attempt_id: str | None
) -> Mapping[str, object] | None:
    if not attempt_id:
        return None
    return next(
        (
            item
            for item in attempts
            if isinstance(item, Mapping) and item.get("attempt_id") == attempt_id
        ),
        None,
    )


def _atomic_write_json(path: Path, data: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def _validate_schema_node(
    node: Mapping[str, object], path: str, issues: list[BenchmarkIssue]
) -> None:
    supported = {
        "$schema",
        "$id",
        "title",
        "description",
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "minLength",
        "maxLength",
        "pattern",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
    }
    for key in node:
        if key not in supported:
            issues.append(
                BenchmarkIssue("provider-schema.keyword", f"{path}: unsupported {key}")
            )
    declared_type = node.get("type")
    if isinstance(declared_type, str):
        declared_types = [declared_type]
    elif isinstance(declared_type, list):
        declared_types = declared_type
    else:
        declared_types = []
    type_set = {
        item for item in declared_types if isinstance(item, str) and item in _JSON_TYPES
    }
    if (
        not declared_types
        or len(type_set) != len(declared_types)
        or len(set(declared_types)) != len(declared_types)
    ):
        issues.append(
            BenchmarkIssue(
                "provider-schema.type", f"{path}: explicit supported type is required"
            )
        )
    if "const" in node and (
        not any(_value_matches_type(node["const"], item) for item in type_set)
        or not _is_finite_json_value(node["const"])
    ):
        issues.append(
            BenchmarkIssue("provider-schema.type", f"{path}: const must match type")
        )
    if "enum" in node:
        enum = node["enum"]
        if (
            not isinstance(enum, list)
            or not enum
            or len({_canonical_json_value(value) for value in enum}) != len(enum)
            or any(
                not any(_value_matches_type(value, item) for item in type_set)
                or not _is_finite_json_value(value)
                for value in enum
            )
        ):
            issues.append(
                BenchmarkIssue("provider-schema.type", f"{path}: enum must match type")
            )
    if "properties" in node:
        if "object" not in type_set or not isinstance(node["properties"], Mapping):
            issues.append(
                BenchmarkIssue(
                    "provider-schema.type", f"{path}: properties require object type"
                )
            )
        else:
            for name, child in node["properties"].items():
                if not isinstance(name, str) or not isinstance(child, Mapping):
                    issues.append(
                        BenchmarkIssue(
                            "provider-schema.node", f"{path}: invalid property"
                        )
                    )
                else:
                    _validate_schema_node(child, f"{path}.properties.{name}", issues)
    if "required" in node:
        required = node["required"]
        properties = node.get("properties")
        if (
            "object" not in type_set
            or not isinstance(required, list)
            or not all(isinstance(name, str) for name in required)
            or len(set(required)) != len(required)
            or not isinstance(properties, Mapping)
            or not set(required) <= set(properties)
        ):
            issues.append(
                BenchmarkIssue(
                    "provider-schema.required",
                    f"{path}: required must name declared properties",
                )
            )
    if "object" in type_set and node.get("additionalProperties") is not False:
        issues.append(
            BenchmarkIssue(
                "provider-schema.additional-properties",
                f"{path}: object must be closed",
            )
        )
    if "array" in type_set and "items" not in node:
        issues.append(
            BenchmarkIssue(
                "provider-schema.items", f"{path}: array requires typed items"
            )
        )
    if "items" in node:
        if "array" not in type_set or not isinstance(node["items"], Mapping):
            issues.append(
                BenchmarkIssue(
                    "provider-schema.type", f"{path}: items require array type"
                )
            )
        else:
            _validate_schema_node(node["items"], f"{path}.items", issues)
    if (
        any(key in node for key in {"minLength", "maxLength", "pattern"})
        and ("string" not in type_set or not type_set <= {"string", "null"})
    ):
        issues.append(
            BenchmarkIssue(
                "provider-schema.type", f"{path}: string constraint needs string type"
            )
        )
    if any(key in node for key in {"minimum", "maximum"}) and (
        not type_set & {"number", "integer"}
        or not type_set <= {"number", "integer", "null"}
    ):
        issues.append(
            BenchmarkIssue(
                "provider-schema.type", f"{path}: numeric constraint needs number type"
            )
        )
    if (
        any(key in node for key in {"minItems", "maxItems"})
        and ("array" not in type_set or not type_set <= {"array", "null"})
    ):
        issues.append(
            BenchmarkIssue(
                "provider-schema.type", f"{path}: array constraint needs array type"
            )
        )
    for minimum, maximum in (("minLength", "maxLength"), ("minItems", "maxItems")):
        if minimum in node and (not _non_bool_int(node[minimum]) or node[minimum] < 0):
            issues.append(
                BenchmarkIssue(
                    "provider-schema.operand",
                    f"{path}: {minimum} must be non-negative integer",
                )
            )
        if maximum in node and (not _non_bool_int(node[maximum]) or node[maximum] < 0):
            issues.append(
                BenchmarkIssue(
                    "provider-schema.operand",
                    f"{path}: {maximum} must be non-negative integer",
                )
            )
        if (
            _non_bool_int(node.get(minimum))
            and _non_bool_int(node.get(maximum))
            and node[minimum] > node[maximum]
        ):
            issues.append(
                BenchmarkIssue(
                    "provider-schema.range", f"{path}: invalid {minimum}/{maximum}"
                )
            )
    if "minimum" in node and (not _finite_number(node["minimum"])):
        issues.append(
            BenchmarkIssue(
                "provider-schema.operand", f"{path}: minimum must be numeric"
            )
        )
    if "maximum" in node and (not _finite_number(node["maximum"])):
        issues.append(
            BenchmarkIssue(
                "provider-schema.operand", f"{path}: maximum must be numeric"
            )
        )
    if (
        _finite_number(node.get("minimum"))
        and _finite_number(node.get("maximum"))
        and node["minimum"] > node["maximum"]
    ):
        issues.append(
            BenchmarkIssue("provider-schema.range", f"{path}: invalid minimum/maximum")
        )
    if "pattern" in node:
        if not isinstance(node["pattern"], str):
            issues.append(
                BenchmarkIssue(
                    "provider-schema.operand", f"{path}: pattern must be a string"
                )
            )
        else:
            try:
                re.compile(node["pattern"])
            except re.error:
                issues.append(
                    BenchmarkIssue("provider-schema.pattern", f"{path}: invalid regex")
                )


def _non_bool_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _now_rfc3339() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_rfc3339(value: object) -> datetime | None:
    if not isinstance(value, str) or re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
        value,
    ) is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _validate_token_usage_object(value: object, *, allow_all_zero: bool) -> None:
    required = {
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
    }
    if not isinstance(value, Mapping) or set(value) != required or any(
        not _non_bool_int(value.get(key)) or value[key] < 0 for key in required
    ):
        raise ValueError("Provider completion token usage is invalid")
    if not allow_all_zero and sum(value.values()) == 0:
        raise ValueError("content-producing Provider completion has zero token usage")


def _validate_completion_evidence(event: Mapping[str, object]) -> None:
    if event.get("terminal") is not True:
        if any(
            event.get(key) is not None
            for key in (
                "child_session",
                "token_usage",
                "raw_provider_output_sha256",
            )
        ):
            raise ValueError("non-terminal event cannot carry completion evidence")
        return
    session = event.get("child_session")
    if not isinstance(session, str) or session.strip().lower() in {
        "",
        "placeholder",
        "unknown",
        "tbd",
    }:
        raise ValueError("Provider completion child session is invalid")
    _validate_token_usage_object(
        event.get("token_usage"), allow_all_zero=event.get("content_produced") is False
    )
    if not _is_non_placeholder_digest(event.get("raw_provider_output_sha256")):
        raise ValueError("Provider completion raw output digest is invalid")


def _finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _value_matches_type(value: object, declared_type: object) -> bool:
    if declared_type == "object":
        return isinstance(value, Mapping)
    if declared_type == "array":
        return isinstance(value, list)
    if declared_type == "string":
        return isinstance(value, str)
    if declared_type == "number":
        return _finite_number(value)
    if declared_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if declared_type == "boolean":
        return isinstance(value, bool)
    return value is None and declared_type == "null"


def _is_finite_json_value(value: object) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, Mapping):
        return all(_is_finite_json_value(child) for child in value.values())
    if isinstance(value, list):
        return all(_is_finite_json_value(child) for child in value)
    return True


def _is_closed_json_value(value: object) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, Mapping):
        return all(
            isinstance(key, str) and _is_closed_json_value(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return all(_is_closed_json_value(child) for child in value)
    return False


def _canonical_json_value(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _missing_issues(
    value: Mapping[str, object],
    required: set[str],
    prefix: str,
    issues: list[BenchmarkIssue],
) -> None:
    for key in sorted(required - set(value)):
        issues.append(
            BenchmarkIssue(f"{prefix}.missing", f"missing required field {key}")
        )


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA256.fullmatch(value))


def _is_non_placeholder_digest(value: object) -> bool:
    return _is_digest(value) and value != "0" * 64


def _scan_public_value(value: object, path: str, issues: list[BenchmarkIssue]) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _is_secret_field_name(str(key)) and not (
                isinstance(child, str) and child.strip().upper() == "REDACTED"
            ):
                issues.append(
                    BenchmarkIssue(
                        "receipt.secret", f"{path}.{key} is a secret-like field"
                    )
                )
            _scan_public_value(child, f"{path}.{key}", issues)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_public_value(child, f"{path}[{index}]", issues)
    elif isinstance(value, str):
        if _is_private_path(value):
            issues.append(
                BenchmarkIssue(
                    "receipt.absolute-path", f"{path} contains an absolute path"
                )
            )
        if _contains_secret_value(value):
            issues.append(
                BenchmarkIssue("receipt.secret", f"{path} contains a secret-like value")
            )


def _require_public_ledger_value(value: object, label: str) -> None:
    issues: list[BenchmarkIssue] = []
    _scan_public_value(value, "$", issues)
    if issues:
        raise ValueError(f"{label} contains non-public data")


def _scan_non_placeholder_digests(
    value: object, path: str, scope: str, issues: list[BenchmarkIssue]
) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            is_digest_field = (
                str(key).endswith(("_sha256", "_digest"))
                or key
                in {"evidence_id", "receipt_sha256", "sha256", "fixture_commitment"}
            )
            if is_digest_field and child is not None and not _is_non_placeholder_digest(
                child
            ):
                issues.append(
                    BenchmarkIssue(
                        f"{scope}.digest",
                        f"{child_path} must be a non-placeholder SHA-256 digest",
                    )
                )
            _scan_non_placeholder_digests(child, child_path, scope, issues)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_non_placeholder_digests(
                child, f"{path}[{index}]", scope, issues
            )


def _is_private_path(value: str) -> bool:
    scrubbed = value
    for match in reversed(list(_HTTP_URI.finditer(value))):
        candidate = match.group(0)
        parsed = _strict_public_http(candidate)
        if parsed is None:
            continue
        retained = ""
        if parsed.query:
            retained += f"?{_percent_decode_once(parsed.query)}"
        if parsed.fragment:
            retained += f"#{_percent_decode_once(parsed.fragment)}"
        scrubbed = scrubbed[: match.start()] + f"http-uri{retained}" + scrubbed[match.end() :]
    return bool(_PRIVATE_PATH.search(scrubbed))


def redact_public_message(message: str) -> str:
    """Redact secrets/private paths with one shared, bounded URI classifier."""
    message = _redact_secret_values(message)
    protected: list[str] = []

    def protect(match: re.Match[str]) -> str:
        candidate = match.group(0)
        parsed = _strict_public_http(candidate)
        if parsed is None:
            return candidate
        redacted = _redact_uri_parameters(candidate)
        suffix_start = min(
            (
                offset
                for offset in (redacted.find("?"), redacted.find("#"))
                if offset >= 0
            ),
            default=len(redacted),
        )
        suffix = redacted[suffix_start:]
        decoded_suffix = _percent_decode_once(suffix)
        private_match = _PRIVATE_PATH.search(decoded_suffix)
        safe_end = suffix_start
        if private_match is None:
            safe_end = len(redacted)
        else:
            # Protect only the stable public prefix. The original encoded private
            # suffix remains outside the marker and is redacted below.
            original_prefix = _encoded_prefix_for_decoded_offset(
                suffix, private_match.start()
            )
            safe_end += original_prefix
            redacted = f"{redacted[:safe_end]}<redacted-path>"
            safe_end = len(redacted)
        marker = f"__AI_SDLC_PUBLIC_URI_{len(protected)}__::"
        protected.append(redacted[:safe_end])
        return marker + redacted[safe_end:]

    message = _HTTP_URI.sub(protect, message)
    message = _PRIVATE_PATH.sub("<redacted-path>", message)
    for index, uri in enumerate(protected):
        message = message.replace(f"__AI_SDLC_PUBLIC_URI_{index}__::", uri)
    return message


def _strict_public_http(value: str):
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or "\\" in parsed.netloc
    ):
        return None
    return parsed


def _percent_decode_once(value: str) -> str:
    return unquote(value)


def _is_secret_field_name(value: str) -> bool:
    decoded = _percent_decode_once(value).strip().lower()
    while decoded.endswith("[]"):
        decoded = decoded[:-2]
    return decoded in _SECRET_FIELD_NAMES


def _contains_secret_value(value: str) -> bool:
    normalized = re.sub(
        r"(['\"])REDACTED\1", "REDACTED", value, flags=re.IGNORECASE
    )
    if _decoded_has_secret_token(normalized):
        return True
    for candidate in _HTTP_URI.findall(normalized):
        parsed = _strict_public_http(candidate)
        if parsed is not None and _uri_has_secret_parameter(parsed.query, parsed.fragment):
            return True
    decoded = _percent_decode_once(normalized)
    assignment = re.compile(
        r"(?<![A-Za-z0-9_.%-])(?P<key>[A-Za-z][A-Za-z0-9_%\-]*(?:\[\])*)"
        r"\s*(?::|=)\s*(?P<value>[^&#\s,;)]+)",
        re.I,
    )
    return any(
        _is_secret_field_name(match.group("key"))
        and match.group("value").strip("\"'").upper() != "REDACTED"
        for match in assignment.finditer(decoded)
    )


def _uri_has_secret_parameter(*parts: str) -> bool:
    for raw in parts:
        decoded = _percent_decode_once(raw)
        for component in re.split(r"[&;]", decoded):
            key, separator, value = component.partition("=")
            if not separator:
                key, separator, value = component.partition(":")
            if separator and value.strip("\"'").upper() != "REDACTED" and (
                _is_secret_field_name(key) or _decoded_has_secret_token(value)
            ):
                return True
    return False


def _decoded_has_secret_token(value: str) -> bool:
    """Classify a secret token after exactly one bounded percent decode."""
    return bool(_SECRET_TOKEN.search(_percent_decode_once(value)))


def _redact_secret_values(value: str) -> str:
    assignment = re.compile(
        r"(?<![A-Za-z0-9_.%-])"
        r"(?P<key>(?:(?:[A-Za-z0-9_.\-])|(?:%[0-9A-Fa-f]{2}))+"
        r"(?:(?:\[\])|(?:%5[bB]%5[dD]))*)"
        r"(?P<space>(?:\s|\+|%20)*)"
        r"(?P<separator>=|:|%3[dD]|%3[aA])"
        r"(?P<after>(?:\s|\+|%20)*(?:[\"']|%22|%27)?)"
        r"(?P<value>.+?)(?=(?:%22|%27|[\"'&#\s,;)]|$))",
        re.I,
    )

    def replace(match: re.Match[str]) -> str:
        if not (
            _is_secret_field_name(match.group("key"))
            or _decoded_has_secret_token(match.group("value"))
        ):
            return match.group(0)
        return (
            f"{match.group('key')}{match.group('space')}"
            f"{match.group('separator')}{match.group('after')}REDACTED"
        )

    value = _HTTP_URI.sub(
        lambda match: _redact_uri_parameters(match.group(0)), value
    )
    return _redact_encoded_secret_tokens(assignment.sub(replace, value))


def _redact_uri_parameters(value: str) -> str:
    start = min(
        (offset for offset in (value.find("?"), value.find("#")) if offset >= 0),
        default=len(value),
    )
    if start == len(value):
        return value
    prefix = value[:start]
    components = re.split(r"([&#;])", value[start:])
    assignment = re.compile(
        r"^(?P<head>[?#]?)(?P<key>(?:(?:[A-Za-z0-9_.\-])|(?:%[0-9A-Fa-f]{2}))+"
        r"(?:(?:\[\])|(?:%5[bB]%5[dD]))*)"
        r"(?P<separator>=|:|%3[dD]|%3[aA])(?P<value>.*)$",
        re.I,
    )
    for index in range(0, len(components), 2):
        component = components[index]
        match = assignment.fullmatch(component)
        if match is None or not (
            _is_secret_field_name(match.group("key"))
            or _decoded_has_secret_token(match.group("value"))
        ):
            continue
        raw_value = match.group("value")
        quote = next(
            (
                marker
                for marker in ('"', "'", "%22", "%27")
                if raw_value.lower().startswith(marker.lower())
            ),
            "",
        )
        components[index] = (
            f"{match.group('head')}{match.group('key')}"
            f"{match.group('separator')}{quote}REDACTED"
        )
    return prefix + "".join(components)


def _redact_encoded_secret_tokens(value: str) -> str:
    """Redact decoded ASCII token spans while preserving unrelated raw bytes."""
    decoded: list[str] = []
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(value):
        if (
            value[index] == "%"
            and index + 2 < len(value)
            and re.fullmatch(r"[0-9A-Fa-f]{2}", value[index + 1 : index + 3])
        ):
            decoded.append(chr(int(value[index + 1 : index + 3], 16)))
            spans.append((index, index + 3))
            index += 3
        else:
            decoded.append(value[index])
            spans.append((index, index + 1))
            index += 1
    matches = list(_SECRET_TOKEN.finditer("".join(decoded)))
    for match in reversed(matches):
        raw_start = spans[match.start()][0]
        raw_end = spans[match.end() - 1][1]
        value = f"{value[:raw_start]}REDACTED{value[raw_end:]}"
    return value


def _encoded_prefix_for_decoded_offset(value: str, decoded_offset: int) -> int:
    if decoded_offset <= 0:
        return 0
    for index in range(1, len(value) + 1):
        if len(_percent_decode_once(value[:index])) >= decoded_offset:
            return index - 1
    return len(value)


def _validate_receipt_timing(value: object, issues: list[BenchmarkIssue]) -> None:
    receipt = _mapping(value)
    timings = _mapping(receipt.get("timings"))
    components = (
        "setup_wall_seconds",
        "framework_init_wall_seconds",
        "provider_wall_seconds",
        "governance_wall_seconds",
        "review_wall_seconds",
        "evaluation_wall_seconds",
    )
    if any(not _finite_number(timings.get(name)) for name in components):
        issues.append(
            BenchmarkIssue(
                "receipt.timing", "all additive timing components are required"
            )
        )
        return
    total = sum(timings[name] for name in components)
    end_to_end = timings.get("end_to_end_wall_seconds")
    timestamps = _mapping(receipt.get("timestamps"))
    started_at = _parse_rfc3339(timestamps.get("started_at"))
    ended_at = _parse_rfc3339(timestamps.get("ended_at"))
    evaluator_at = _parse_rfc3339(
        _mapping(receipt.get("external_evaluator")).get("completed_at")
    )
    if (
        started_at is None
        or ended_at is None
        or evaluator_at is None
        or ended_at < started_at
        or evaluator_at != ended_at
    ):
        issues.append(
            BenchmarkIssue(
                "receipt.timing",
                "start/end/evaluator timestamps must be ordered and bound",
            )
        )
        return
    elapsed = (ended_at - started_at).total_seconds()
    if (
        not _finite_number(end_to_end)
        or not math.isclose(end_to_end, total, rel_tol=0, abs_tol=1e-6)
        or not math.isclose(end_to_end, elapsed, rel_tol=0, abs_tol=1e-6)
    ):
        issues.append(
            BenchmarkIssue(
                "receipt.timing",
                "end-to-end timing must equal timestamps and additive components",
            )
        )


def _validate_token_usage(value: object, issues: list[BenchmarkIssue]) -> None:
    tokens = _mapping(value)
    required = (
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
    )
    if any(
        not _non_bool_int(tokens.get(name)) or tokens[name] < 0 for name in required
    ):
        issues.append(
            BenchmarkIssue(
                "receipt.tokens", "token usage must contain non-negative integers"
            )
        )


def _validate_human_events(value: object, issues: list[BenchmarkIssue]) -> None:
    if not isinstance(value, list):
        issues.append(
            BenchmarkIssue("receipt.human-events", "human_events must be a list")
        )
        return
    allowed = {
        "operator_authorization",
        "operator_confirmation",
        "operator_adjudication",
    }
    for event in value:
        if not isinstance(event, Mapping) or event.get("type") not in allowed:
            issues.append(
                BenchmarkIssue(
                    "receipt.human-events", "automated events are not human events"
                )
            )


def _validate_receipt_measurements(
    receipt: Mapping[str, object], issues: list[BenchmarkIssue]
) -> None:
    measurements = _mapping(receipt.get("measurements"))
    provider_attempts = receipt.get("provider_attempts")
    human_events = receipt.get("human_events")
    automated_events = receipt.get("automated_events")
    _validate_phase_measurement_evidence(receipt, issues)
    if (
        isinstance(provider_attempts, list)
        and measurements.get("provider_attempt_count") != len(provider_attempts)
    ):
        issues.append(
            BenchmarkIssue(
                "receipt.measurements", "Provider attempt count is not reproducible"
            )
        )
    if isinstance(human_events, list):
        human_seconds = sum(
            event.get("seconds", 0)
            for event in human_events
            if isinstance(event, Mapping)
        )
        if measurements.get("human_event_count") != len(human_events) or not math.isclose(
            measurements.get("human_active_seconds"),
            human_seconds,
            rel_tol=0,
            abs_tol=1e-9,
        ):
            issues.append(
                BenchmarkIssue(
                    "receipt.measurements", "human event totals are not reproducible"
                )
            )
    if isinstance(automated_events, list):
        intent_count = sum(
            1
            for event in automated_events
            if isinstance(event, Mapping)
            and event.get("type") == "intent_service_event"
        )
        approval_count = sum(
            1
            for event in automated_events
            if isinstance(event, Mapping)
            and event.get("type") == "approval_service_event"
        )
        clarification_count = sum(
            1
            for event in automated_events
            if isinstance(event, Mapping)
            and event.get("type") == "clarification_request_event"
        )
        latency = sum(
            event.get("latency_ms", 0)
            for event in automated_events
            if isinstance(event, Mapping)
        )
        if (
            measurements.get("clarification_request_count") != clarification_count
            or measurements.get("intent_service_event_count") != intent_count
            or measurements.get("approval_service_event_count") != approval_count
            or not math.isclose(
                measurements.get("intent_approval_service_latency_ms"),
                latency,
                rel_tol=0,
                abs_tol=1e-9,
            )
        ):
            issues.append(
                BenchmarkIssue(
                    "receipt.measurements",
                    "automated service totals are not reproducible",
                )
            )
        for event in automated_events:
            if not isinstance(event, Mapping):
                continue
            started_at = _parse_rfc3339(event.get("started_at"))
            ended_at = _parse_rfc3339(event.get("ended_at"))
            latency_ms = event.get("latency_ms")
            payload = {
                "type": event.get("type"),
                "started_at": event.get("started_at"),
                "ended_at": event.get("ended_at"),
                "latency_ms": latency_ms,
            }
            expected_digest = sha256(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if (
                started_at is None
                or ended_at is None
                or ended_at < started_at
                or not _finite_number(latency_ms)
                or not math.isclose(
                    latency_ms,
                    (ended_at - started_at).total_seconds() * 1000,
                    rel_tol=0,
                    abs_tol=1e-6,
                )
                or event.get("evidence_sha256") != expected_digest
            ):
                issues.append(
                    BenchmarkIssue(
                        "receipt.measurements",
                        "automated event timing and digest are not reproducible",
                    )
                )
    if measurements.get("needs_operator") is not (
        receipt.get("status") == "needs_operator"
    ):
        issues.append(
            BenchmarkIssue(
                "receipt.measurements", "needs_operator does not match run status"
            )
        )
    _validate_artifact_inventory(receipt, issues)
    evaluator = _mapping(receipt.get("external_evaluator"))
    expected_invalid = receipt.get("status") == "completed" and not evaluator.get(
        "external_verified_delivery"
    )
    if evaluator.get("invalid_completion") is not expected_invalid:
        issues.append(
            BenchmarkIssue(
                "receipt.measurements", "invalid completion is not reproducible"
            )
        )


def _validate_phase_measurement_evidence(
    receipt: Mapping[str, object], issues: list[BenchmarkIssue]
) -> None:
    phases = _mapping(receipt.get("phase_evidence"))
    timings = _mapping(receipt.get("timings"))
    timestamps = _mapping(receipt.get("timestamps"))
    try:
        durations = _validated_phase_durations(
            phases,
            timestamps.get("started_at"),
            timestamps.get("ended_at"),
        )
    except ValueError:
        issues.append(
            BenchmarkIssue(
                "receipt.measurements",
                "phase evidence must be one reproducible adjacent start-to-end partition",
            )
        )
        return
    timing_names = {
        "setup": "setup_wall_seconds",
        "framework_init": "framework_init_wall_seconds",
        "provider": "provider_wall_seconds",
        "post_provider": "governance_wall_seconds",
        "review": "review_wall_seconds",
        "evaluation": "evaluation_wall_seconds",
    }
    for phase_name, timing_name in timing_names.items():
        if (
            not _finite_number(timings.get(timing_name))
            or not math.isclose(
                timings[timing_name],
                durations[phase_name],
                rel_tol=0,
                abs_tol=1e-6,
            )
        ):
            issues.append(
                BenchmarkIssue(
                    "receipt.measurements",
                    f"{phase_name} phase timing is not independently reproducible",
                )
            )


def _validated_phase_durations(
    phases: object, started_text: object, ended_text: object
) -> dict[str, float]:
    if not isinstance(phases, Mapping) or set(phases) != set(_RUN_PHASES):
        raise ValueError("phase evidence surface is incomplete")
    cursor = _parse_rfc3339(started_text)
    final_end = _parse_rfc3339(ended_text)
    if cursor is None or final_end is None or final_end < cursor:
        raise ValueError("phase evidence boundary is invalid")
    durations: dict[str, float] = {}
    for phase_name in _RUN_PHASES:
        phase = phases.get(phase_name)
        if not isinstance(phase, Mapping) or set(phase) != {
            "started_at",
            "ended_at",
            "evidence_sha256",
        }:
            raise ValueError("phase evidence entry is invalid")
        started_at = _parse_rfc3339(phase.get("started_at"))
        ended_at = _parse_rfc3339(phase.get("ended_at"))
        payload = {
            "phase": phase_name,
            "started_at": phase.get("started_at"),
            "ended_at": phase.get("ended_at"),
        }
        if (
            started_at is None
            or ended_at is None
            or started_at != cursor
            or ended_at < started_at
            or phase.get("evidence_sha256") != _canonical_json_digest(payload)
        ):
            raise ValueError("phase evidence is not reproducible")
        durations[phase_name] = (ended_at - started_at).total_seconds()
        cursor = ended_at
    if cursor != final_end:
        raise ValueError("phase evidence is not a complete adjacent partition")
    return durations


def _validate_artifact_inventory(
    receipt: Mapping[str, object], issues: list[BenchmarkIssue]
) -> None:
    inventory = receipt.get("artifact_inventory")
    measurements = _mapping(receipt.get("measurements"))
    if not isinstance(inventory, list) or not inventory:
        issues.append(
            BenchmarkIssue("receipt.measurements", "artifact inventory is missing")
        )
        return
    paths: list[str] = []
    totals = {"setup": 0, "governance": 0, "delivery": 0}
    required_count = 0
    observed_required = 0
    observed_delivery: set[str] = set()
    for artifact in inventory:
        if not isinstance(artifact, Mapping):
            continue
        path = artifact.get("path")
        digest = artifact.get("sha256")
        size = artifact.get("size_bytes")
        category = artifact.get("category")
        required = artifact.get("required")
        applicable = artifact.get("applicable")
        observed = artifact.get("observed")
        if (
            not _is_safe_relative_path(path)
            or not _non_bool_int(size)
            or category not in totals
            or not isinstance(required, bool)
            or not isinstance(applicable, bool)
            or not isinstance(observed, bool)
            or (required and not applicable)
            or (
                observed
                and (
                    not applicable
                    or not _is_non_placeholder_digest(digest)
                    or size < 1
                )
            )
            or (not observed and (digest is not None or size != 0))
        ):
            issues.append(
                BenchmarkIssue(
                    "receipt.measurements", "artifact inventory item is invalid"
                )
            )
            continue
        paths.append(path)
        if observed:
            totals[category] += size
            if category == "delivery":
                observed_delivery.add(path)
        if required and applicable:
            required_count += 1
            observed_required += int(observed)
    if len(paths) != len(set(paths)):
        issues.append(
            BenchmarkIssue(
                "receipt.measurements", "artifact inventory paths must be unique"
            )
        )
    expected_completeness = (
        observed_required / required_count if required_count else 1.0
    )
    changed_files = receipt.get("changed_files")
    if (
        required_count == 0
        or measurements.get("setup_artifact_bytes") != totals["setup"]
        or measurements.get("governance_artifact_bytes") != totals["governance"]
        or measurements.get("total_artifact_bytes") != sum(totals.values())
        or not _finite_number(measurements.get("evidence_completeness"))
        or not math.isclose(
            measurements["evidence_completeness"],
            expected_completeness,
            rel_tol=0,
            abs_tol=1e-9,
        )
        or not isinstance(changed_files, list)
        or set(changed_files) - observed_delivery
    ):
        issues.append(
            BenchmarkIssue(
                "receipt.measurements",
                "artifact totals, completeness, or changed-file bindings are not reproducible",
            )
        )


def _parse_run(value: object) -> BenchmarkRun:
    if not isinstance(value, dict):
        raise ValueError("run_matrix item must be an object")
    _reject_unknown(value, _RUN_KEYS, "run_matrix item")
    _require_keys(value, _RUN_KEYS, "run_matrix item")
    return BenchmarkRun(
        run_id=_string(value["run_id"], "run id"),
        arm=_string(value["arm"], "run arm"),
        fixture=_string(value["fixture"], "run fixture"),
        position=_integer(value["position"], "run position"),
    )


def _lock_value(value: object, key: str) -> str | int:
    if key.endswith("seconds"):
        return _integer(value, key)
    return _string(value, key)


def _require_keys(value: dict[str, object], keys: set[str], label: str) -> None:
    missing = keys - value.keys()
    if missing:
        raise ValueError(f"{label} missing fields: {', '.join(sorted(missing))}")


def _reject_unknown(value: dict[str, object], keys: set[str], label: str) -> None:
    unknown = value.keys() - keys
    if unknown:
        raise ValueError(f"{label} unknown fields: {', '.join(sorted(unknown))}")


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    return value


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a string list")
    return tuple(value)


def _integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    return value
