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
_LEDGER_SCHEMA = "ai-sdlc-v2-benefit-attempt-ledger/v4"
_LEDGER_KEYS = {"schema", "protocol_sha256", "attempts_started", "attempts"}
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
_SECRET_KEY = re.compile(
    r"(?:api[_-]?key|secret|password|authorization|access[_-]?token|auth[_-]?token)",
    re.I,
)
_SECRET_VALUE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|"
    r"(?:api[_-]?key|secret|password|authorization|access[_-]?token|auth[_-]?token|gh[_-]?token|token)"
    r"\s*(?::|=)\s*(?!REDACTED(?:\b|$))\S+|"
    r"--(?:api[_-]?key|secret|password|authorization|access[_-]?token|auth[_-]?token|token)"
    r"(?:=|\s+)(?!REDACTED(?:\b|$))\S+|Bearer\s+(?!REDACTED(?:\b|$))\S+)",
    re.I,
)
_PRIVATE_PATH = re.compile(
    r"(?:file://[^\s'\"]+|[A-Za-z]:[\\/][^\s'\"]+|"
    r"(?<!:)(?:\\\\|//)[A-Za-z0-9._-]+[\\/][^\s'\"]+|"
    r"(?<![A-Za-z0-9_./-])/(?!/)[^\s'\"]+)",
    re.I,
)
_HTTP_URI = re.compile(r"https?://[^\s'\"]+", re.I)
_BENCHMARK_ROOT = (
    Path(__file__).resolve().parents[2] / "benchmarks" / "ai-sdlc-v2-benefits"
)


def canonical_protocol_digest(protocol: BenchmarkProtocol) -> str:
    return sha256(protocol.canonical_bytes).hexdigest()


def reserve_provider_attempt(
    ledger_path: Path,
    protocol: BenchmarkProtocol,
    request: AttemptRequest,
) -> AttemptReservation:
    """Atomically reserve an allowed logical Provider attempt before it starts."""
    protocol_digest = _require_executable_protocol(protocol)
    with _ledger_lock(ledger_path):
        ledger = _load_ledger(ledger_path, protocol_digest, protocol.attempt_budget)
        if ledger["attempts_started"] >= protocol.attempt_budget.limit:
            raise ValueError(
                f"Provider attempt budget of {protocol.attempt_budget.limit} is exhausted"
            )
        _validate_reservation_request(ledger["attempts"], request)
        attempts_started = ledger["attempts_started"] + 1
        attempt_id = f"attempt-{attempts_started:03d}"
        ledger["attempts_started"] = attempts_started
        ledger["attempts"].append(_new_attempt(attempt_id, request, ledger["attempts"]))
        _validate_attempt_ledger_invariants(
            ledger["attempts"], protocol.attempt_budget
        )
        _atomic_write_json(ledger_path, ledger)
        return AttemptReservation(attempt_id, attempts_started, request)


def record_provider_completion(
    ledger_path: Path,
    protocol: BenchmarkProtocol,
    completion: AttemptCompletion,
) -> None:
    """Atomically record one allowed Provider attempt state transition."""
    protocol_digest = _require_executable_protocol(protocol)
    with _ledger_lock(ledger_path):
        ledger = _load_ledger(ledger_path, protocol_digest, protocol.attempt_budget)
        for attempt in ledger["attempts"]:
            if attempt["attempt_id"] == completion.attempt_id:
                _apply_attempt_transition(ledger["attempts"], attempt, completion)
                _validate_attempt_ledger_invariants(
                    ledger["attempts"], protocol.attempt_budget
                )
                _atomic_write_json(ledger_path, ledger)
                return
        raise ValueError("Provider completion requires a prior reservation")


def validate_provider_output_schema(
    schema: Mapping[str, object],
) -> list[BenchmarkIssue]:
    """Reject non-deterministic or open Provider structured-output schemas."""
    issues: list[BenchmarkIssue] = []
    _validate_schema_node(schema, "$", issues)
    return issues


def verify_receipt(
    receipt: Mapping[str, object], protocol: BenchmarkProtocol, ledger_path: Path
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
    _validate_receipt_protocol_binding(receipt, protocol, issues)
    if issues:
        return issues
    _validate_receipt_ledger_binding(receipt, protocol, ledger_path, issues)
    if issues:
        return issues
    _scan_public_value(receipt, "$", issues)
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
        if name.endswith("sha256") and (
            not isinstance(value, str) or not _is_digest(value)
        ):
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
        if not isinstance(receipt_digest, str) or not _is_digest(receipt_digest):
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
    issues: list[BenchmarkIssue],
) -> None:
    """Bind receipt attempts and A11 evidence to the validated persisted v3 ledger."""
    if not ledger_path.is_file():
        issues.append(BenchmarkIssue("receipt.ledger", "attempt ledger is missing"))
        return
    try:
        with _ledger_lock(ledger_path):
            ledger = _load_ledger(
                ledger_path,
                canonical_protocol_digest(protocol),
                protocol.attempt_budget,
            )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        issues.append(
            BenchmarkIssue("receipt.ledger", f"attempt ledger is invalid: {error}")
        )
        return

    run_id = receipt.get("run_id")
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
    if arm == "A11" and status == "completed":
        _validate_a11_receipt_evidence(receipt, expected, writer, issues)
    elif arm == "A11" and status == "needs_operator":
        _validate_a11_conflict_evidence(receipt, expected, issues)
    elif arm == "A11" and status in {"failed", "timeout", "budget_exhausted"}:
        _validate_a11_terminal_failure_evidence(receipt, expected, issues)


def _validate_a11_receipt_evidence(
    receipt: Mapping[str, object],
    attempts: list[Mapping[str, object]],
    writer: Mapping[str, object],
    issues: list[BenchmarkIssue],
) -> None:
    loop = _mapping(receipt.get("loop"))
    callbacks = loop.get("expert_callbacks")
    if not isinstance(callbacks, list):
        issues.append(BenchmarkIssue("receipt.a11.evidence", "callbacks are missing"))
        return
    provider_attempts = {
        item.get("attempt_id"): item
        for item in receipt.get("provider_attempts", [])
        if isinstance(item, Mapping)
    }
    completed_experts = {
        attempt.get("role"): attempt
        for attempt in attempts
        if attempt.get("effective_kind")
        in {"primary_expert", "cross_risk_expert"}
        and attempt.get("status") == "completed"
    }
    if len(callbacks) != len(completed_experts):
        issues.append(
            BenchmarkIssue(
                "receipt.a11.evidence",
                "callbacks must bind every completed first-review role exactly once",
            )
        )
        return
    close = _mapping(loop.get("close"))
    loop_type, loop_id = _expected_loop_identity(receipt)
    if not _is_loop_close_command(
        close.get("argv"),
        loop_type=loop_type,
        loop_id=loop_id,
        review_digest=writer.get("candidate_digest"),
    ):
        issues.append(
            BenchmarkIssue(
                "receipt.a11.evidence", "writer Close command is incomplete"
            )
        )
    writer_history = writer.get("history")
    writer_final_sequence = writer.get("sequence")
    if not isinstance(writer_history, list) or not _non_bool_int(
        writer_final_sequence
    ):
        issues.append(
            BenchmarkIssue("receipt.a11.evidence", "writer history is unavailable")
        )
        return
    seen_roles: set[object] = set()
    for callback in callbacks:
        if not isinstance(callback, Mapping):
            issues.append(
                BenchmarkIssue("receipt.a11.evidence", "callback must be an object")
            )
            continue
        role = callback.get("role")
        if role in seen_roles or role not in completed_experts:
            issues.append(
                BenchmarkIssue(
                    "receipt.a11.evidence", "callback role is duplicate or unbound"
                )
            )
            continue
        seen_roles.add(role)
        expert = completed_experts[role]
        expert_receipt = provider_attempts.get(expert.get("attempt_id"))
        reason = callback.get("reason")
        if not isinstance(reason, str) or reason.strip().lower() in {
            "tbd",
            "todo",
            "placeholder",
            "unknown",
            "not_applicable",
        }:
            issues.append(
                BenchmarkIssue(
                    "receipt.a11.evidence", f"{role} selection reason is a placeholder"
                )
            )
        if (
            callback.get("status") != "pass"
            or callback.get("expert_attempt_id") != expert.get("attempt_id")
            or callback.get("parent_digest") != expert.get("parent_digest")
            or callback.get("candidate_digest") != expert.get("candidate_digest")
            or not isinstance(expert_receipt, Mapping)
            or callback.get("child_session") != expert_receipt.get("child_session")
            or callback.get("raw_output_sha256")
            != expert_receipt.get("raw_provider_output_sha256")
        ):
            issues.append(
                BenchmarkIssue(
                    "receipt.a11.evidence",
                    f"{role} callback does not bind the completed expert",
                )
            )
        review_argv = callback.get("review_argv")
        if not _is_loop_review_command(
            review_argv,
            loop_type=loop_type,
            loop_id=loop_id,
            expected_digest=callback.get("parent_digest"),
        ):
            issues.append(
                BenchmarkIssue(
                    "receipt.a11.evidence", f"{role} review command is incomplete"
                )
            )
        if callback.get("review_exit_code") != 0:
            issues.append(
                BenchmarkIssue(
                    "receipt.a11.evidence", f"{role} review command did not pass"
                )
            )
        if callback.get("parent_tree_before_sha256") != callback.get(
            "parent_tree_after_sha256"
        ):
            issues.append(
                BenchmarkIssue(
                    "receipt.a11.tree",
                    f"{role} expert changed the frozen parent tree",
                )
            )
        proof_digests = (
            "snapshot_sha256",
            "input_sha256",
            "raw_output_sha256",
            "parent_tree_before_sha256",
            "parent_tree_after_sha256",
        )
        if any(
            not _is_non_placeholder_digest(callback.get(key))
            for key in proof_digests
        ):
            issues.append(
                BenchmarkIssue(
                    "receipt.a11.evidence", f"{role} proof digest is a placeholder"
                )
            )
        finding_count = callback.get("finding_count")
        severe_count = callback.get("severe_finding_count")
        if (
            not _non_bool_int(finding_count)
            or not _non_bool_int(severe_count)
            or severe_count > finding_count
        ):
            issues.append(
                BenchmarkIssue(
                    "receipt.a11.evidence", f"{role} finding counts are invalid"
                )
            )
            continue
        ledger_finding = expert.get("finding_digest")
        if finding_count == 0:
            if ledger_finding is not None or any(
                callback.get(key) is not None
                for key in (
                    "finding_digest",
                    "repair_digest",
                    "repaired_candidate_digest",
                    "rereview_attempt_id",
                    "rereview_digest",
                    "rereview_argv",
                    "rereview_exit_code",
                    "rereview_raw_output_sha256",
                )
            ):
                issues.append(
                    BenchmarkIssue(
                        "receipt.a11.evidence",
                        f"{role} no-finding callback carries repair evidence",
                    )
                )
            continue
        required_repair = {
            "finding_digest",
            "repair_digest",
            "repaired_candidate_digest",
            "rereview_attempt_id",
            "rereview_digest",
            "rereview_argv",
            "rereview_exit_code",
            "rereview_raw_output_sha256",
        }
        if required_repair - set(callback):
            issues.append(
                BenchmarkIssue(
                    "receipt.a11.evidence", f"{role} repair/rereview evidence is missing"
                )
            )
            continue
        if callback.get("finding_digest") != ledger_finding:
            issues.append(
                BenchmarkIssue(
                    "receipt.a11.evidence", f"{role} Finding digest is unbound"
                )
            )
        repair = _find_repair_event(
            writer,
            callback.get("finding_digest"),
            callback.get("repair_digest"),
            callback.get("repaired_candidate_digest"),
        )
        rereview = next(
            (
                attempt
                for attempt in attempts
                if attempt.get("attempt_id") == callback.get("rereview_attempt_id")
                and attempt.get("effective_kind") == "expert_rereview"
            ),
            None,
        )
        rereview_receipt = (
            provider_attempts.get(rereview.get("attempt_id"))
            if isinstance(rereview, Mapping)
            else None
        )
        rereview_argv = callback.get("rereview_argv")
        if not _is_loop_review_command(
            rereview_argv,
            loop_type=loop_type,
            loop_id=loop_id,
            expected_digest=callback.get("rereview_digest"),
        ):
            issues.append(
                BenchmarkIssue(
                    "receipt.a11.evidence", f"{role} rereview command is incomplete"
                )
            )
        if callback.get("rereview_exit_code") != 0 or not _is_non_placeholder_digest(
            callback.get("rereview_raw_output_sha256")
        ):
            issues.append(
                BenchmarkIssue(
                    "receipt.a11.evidence", f"{role} rereview output is incomplete"
                )
            )
        if (
            repair is None
            or rereview is None
            or rereview.get("status") != "completed"
            or rereview.get("role") != role
            or rereview.get("parent_attempt_id") != expert.get("attempt_id")
            or rereview.get("finding_digest") != callback.get("finding_digest")
            or rereview.get("repair_digest") != callback.get("repair_digest")
            or rereview.get("candidate_digest")
            != callback.get("repaired_candidate_digest")
            or callback.get("rereview_digest") != rereview.get("candidate_digest")
            or not isinstance(rereview_receipt, Mapping)
            or callback.get("rereview_raw_output_sha256")
            != rereview_receipt.get("raw_provider_output_sha256")
            or not (
                expert.get("sequence")
                < repair.get("sequence")
                < rereview.get("sequence")
                < writer_final_sequence
            )
        ):
            issues.append(
                BenchmarkIssue(
                    "receipt.a11.order",
                    f"{role} writer/Finding/repair/rereview/Close order is invalid",
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


def _validate_a11_conflict_evidence(
    receipt: Mapping[str, object],
    attempts: list[Mapping[str, object]],
    issues: list[BenchmarkIssue],
) -> None:
    loop_type, loop_id = _expected_loop_identity(receipt)
    callbacks = _mapping(receipt.get("loop")).get("expert_callbacks")
    if not isinstance(callbacks, list) or len(callbacks) < 2:
        issues.append(
            BenchmarkIssue(
                "receipt.a11.conflict",
                "needs_operator requires at least two conflicting expert callbacks",
            )
        )
        return
    providers = {
        item.get("attempt_id"): item
        for item in receipt.get("provider_attempts", [])
        if isinstance(item, Mapping)
    }
    experts = {
        item.get("attempt_id"): item
        for item in attempts
        if item.get("effective_kind") in {"primary_expert", "cross_risk_expert"}
        and item.get("status") == "completed"
    }
    callback_ids = {
        item.get("expert_attempt_id")
        for item in callbacks
        if isinstance(item, Mapping)
    }
    if callback_ids != set(experts):
        issues.append(
            BenchmarkIssue(
                "receipt.a11.conflict",
                "conflict callbacks must bind every completed first-review expert",
            )
        )
    for callback in callbacks:
        if not isinstance(callback, Mapping):
            issues.append(
                BenchmarkIssue("receipt.a11.conflict", "conflict callback is malformed")
            )
            continue
        expert = experts.get(callback.get("expert_attempt_id"))
        provider = providers.get(callback.get("expert_attempt_id"))
        reason = callback.get("reason")
        proof_keys = (
            "finding_digest",
            "snapshot_sha256",
            "input_sha256",
            "raw_output_sha256",
            "parent_tree_before_sha256",
            "parent_tree_after_sha256",
        )
        if (
            callback.get("status") != "conflict"
            or not isinstance(reason, str)
            or reason.strip().lower()
            in {"", "tbd", "todo", "placeholder", "unknown", "not_applicable"}
            or not isinstance(expert, Mapping)
            or not isinstance(provider, Mapping)
            or callback.get("role") != expert.get("role")
            or callback.get("child_session") != provider.get("child_session")
            or callback.get("parent_digest") != expert.get("parent_digest")
            or callback.get("candidate_digest") != expert.get("candidate_digest")
            or callback.get("finding_digest") != expert.get("finding_digest")
            or callback.get("raw_output_sha256")
            != provider.get("raw_provider_output_sha256")
            or not _is_loop_review_command(
                callback.get("review_argv"),
                loop_type=loop_type,
                loop_id=loop_id,
                expected_digest=callback.get("parent_digest"),
            )
            or callback.get("review_exit_code") != 0
            or any(
                not _is_non_placeholder_digest(callback.get(key))
                for key in proof_keys
            )
            or callback.get("parent_tree_before_sha256")
            != callback.get("parent_tree_after_sha256")
            or not _non_bool_int(callback.get("finding_count"))
            or callback.get("finding_count", 0) < 1
            or not _non_bool_int(callback.get("severe_finding_count"))
            or callback.get("severe_finding_count", 0)
            > callback.get("finding_count", 0)
            or any(
                callback.get(key) is not None
                for key in (
                    "repair_digest",
                    "repaired_candidate_digest",
                    "rereview_attempt_id",
                    "rereview_digest",
                    "rereview_argv",
                    "rereview_exit_code",
                    "rereview_raw_output_sha256",
                )
            )
        ):
            issues.append(
                BenchmarkIssue(
                    "receipt.a11.conflict",
                    "needs_operator callback lacks real immutable conflict evidence",
                )
            )


def _validate_a11_terminal_failure_evidence(
    receipt: Mapping[str, object],
    attempts: list[Mapping[str, object]],
    issues: list[BenchmarkIssue],
) -> None:
    callbacks = _mapping(receipt.get("loop")).get("expert_callbacks")
    if not isinstance(callbacks, list):
        issues.append(
            BenchmarkIssue(
                "receipt.a11.failure", "terminal A11 callbacks must be an array"
            )
        )
        return
    experts = {
        attempt.get("attempt_id"): attempt
        for attempt in attempts
        if attempt.get("effective_kind")
        in {"primary_expert", "cross_risk_expert"}
        and attempt.get("status") == "completed"
    }
    callback_ids = [
        callback.get("expert_attempt_id")
        for callback in callbacks
        if isinstance(callback, Mapping)
    ]
    if (
        len(callback_ids) != len(callbacks)
        or len(callback_ids) != len(set(callback_ids))
        or set(callback_ids) != set(experts)
    ):
        issues.append(
            BenchmarkIssue(
                "receipt.a11.failure",
                "terminal A11 callbacks must close over current-run completed experts exactly",
            )
        )
        return
    providers = {
        attempt.get("attempt_id"): attempt
        for attempt in receipt.get("provider_attempts", [])
        if isinstance(attempt, Mapping)
    }
    loop_type, loop_id = _expected_loop_identity(receipt)
    nullable_repair = (
        "repair_digest",
        "repaired_candidate_digest",
        "rereview_attempt_id",
        "rereview_digest",
        "rereview_argv",
        "rereview_exit_code",
        "rereview_raw_output_sha256",
    )
    for callback in callbacks:
        if not isinstance(callback, Mapping):
            continue
        expert = experts[callback.get("expert_attempt_id")]
        provider = providers.get(expert.get("attempt_id"))
        reason = callback.get("reason")
        finding_count = callback.get("finding_count")
        severe_count = callback.get("severe_finding_count")
        proof_digests = (
            "snapshot_sha256",
            "input_sha256",
            "raw_output_sha256",
            "parent_tree_before_sha256",
            "parent_tree_after_sha256",
        )
        if (
            callback.get("status") not in {"pass", "fail"}
            or not isinstance(reason, str)
            or reason.strip().lower()
            in {"", "tbd", "todo", "placeholder", "unknown", "not_applicable"}
            or not isinstance(provider, Mapping)
            or callback.get("role") != expert.get("role")
            or callback.get("parent_digest") != expert.get("parent_digest")
            or callback.get("candidate_digest") != expert.get("candidate_digest")
            or callback.get("child_session") != provider.get("child_session")
            or callback.get("raw_output_sha256")
            != provider.get("raw_provider_output_sha256")
            or callback.get("review_exit_code") != 0
            or not _is_loop_review_command(
                callback.get("review_argv"),
                loop_type=loop_type,
                loop_id=loop_id,
                expected_digest=callback.get("parent_digest"),
            )
            or any(
                not _is_non_placeholder_digest(callback.get(key))
                for key in proof_digests
            )
            or callback.get("parent_tree_before_sha256")
            != callback.get("parent_tree_after_sha256")
            or not _non_bool_int(finding_count)
            or not _non_bool_int(severe_count)
            or severe_count > finding_count
        ):
            issues.append(
                BenchmarkIssue(
                    "receipt.a11.failure",
                    "terminal A11 callback does not bind its completed expert",
                )
            )
            continue
        ledger_finding = expert.get("finding_digest")
        if ledger_finding is None:
            if (
                finding_count != 0
                or severe_count != 0
                or callback.get("finding_digest") is not None
                or any(callback.get(key) is not None for key in nullable_repair)
            ):
                issues.append(
                    BenchmarkIssue(
                        "receipt.a11.failure",
                        "no-finding terminal callback carries unbound repair evidence",
                    )
                )
            continue
        if finding_count < 1 or callback.get("finding_digest") != ledger_finding:
            issues.append(
                BenchmarkIssue(
                    "receipt.a11.failure",
                    "terminal callback Finding does not bind the expert ledger event",
                )
            )
        if any(callback.get(key) is not None for key in nullable_repair):
            if any(callback.get(key) is None for key in nullable_repair):
                issues.append(
                    BenchmarkIssue(
                        "receipt.a11.failure",
                        "terminal callback publishes partial repair/rereview evidence",
                    )
                )
                continue
            rereview = next(
                (
                    attempt
                    for attempt in attempts
                    if attempt.get("attempt_id")
                    == callback.get("rereview_attempt_id")
                    and attempt.get("effective_kind") == "expert_rereview"
                    and attempt.get("status") == "completed"
                ),
                None,
            )
            rereview_provider = (
                providers.get(rereview.get("attempt_id"))
                if isinstance(rereview, Mapping)
                else None
            )
            if (
                not isinstance(rereview, Mapping)
                or rereview.get("parent_attempt_id") != expert.get("attempt_id")
                or rereview.get("finding_digest") != ledger_finding
                or rereview.get("repair_digest") != callback.get("repair_digest")
                or rereview.get("candidate_digest")
                != callback.get("repaired_candidate_digest")
                or callback.get("rereview_digest")
                != rereview.get("candidate_digest")
                or not isinstance(rereview_provider, Mapping)
                or callback.get("rereview_raw_output_sha256")
                != rereview_provider.get("raw_provider_output_sha256")
                or callback.get("rereview_exit_code") != 0
                or not _is_loop_review_command(
                    callback.get("rereview_argv"),
                    loop_type=loop_type,
                    loop_id=loop_id,
                    expected_digest=callback.get("rereview_digest"),
                )
            ):
                issues.append(
                    BenchmarkIssue(
                        "receipt.a11.failure",
                        "terminal callback rereview evidence is unbound",
                    )
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
        or not _is_digest(fixture_tree)
        or not _is_digest(fixture_commitment)
    ):
        issues.append(
            BenchmarkIssue(
                "protocol.lock",
                "fixture tree and commitment must be the same SHA-256 digest",
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
    path: Path, protocol_digest: str, attempt_budget: AttemptBudget
) -> dict[str, object]:
    if not path.exists():
        return {
            "schema": _LEDGER_SCHEMA,
            "protocol_sha256": protocol_digest,
            "attempts_started": 0,
            "attempts": [],
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
    attempts = raw["attempts"]
    if raw["attempts_started"] != len(attempts):
        raise ValueError("attempt ledger count does not match attempts")
    expected_ids = [f"attempt-{index:03d}" for index in range(1, len(attempts) + 1)]
    for item, expected in zip(attempts, expected_ids, strict=True):
        if not isinstance(item, dict):
            raise ValueError("attempt ledger attempt must be an object")
        _validate_persisted_attempt(item, expected)
    _validate_attempt_ledger_invariants(attempts, attempt_budget)
    return raw


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
        "history": [reserved],
    }


def _validate_persisted_attempt(attempt: dict[str, object], expected_id: str) -> None:
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
    for event in event_timeline:
        recorded_at = _parse_rfc3339(event.get("recorded_at"))
        if recorded_at is None or (
            previous_at is not None and recorded_at < previous_at
        ):
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
    if isinstance(expected_type, str) and not _matches_json_type(value, expected_type):
        issues.append(
            BenchmarkIssue(f"{scope}.schema", f"{path} must be {expected_type}")
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
    if declared_type not in {
        "object",
        "array",
        "string",
        "number",
        "integer",
        "boolean",
        "null",
    }:
        issues.append(
            BenchmarkIssue(
                "provider-schema.type", f"{path}: explicit supported type is required"
            )
        )
    if "const" in node and (
        not _value_matches_type(node["const"], declared_type)
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
            or any(
                not _value_matches_type(value, declared_type)
                or not _is_finite_json_value(value)
                for value in enum
            )
        ):
            issues.append(
                BenchmarkIssue("provider-schema.type", f"{path}: enum must match type")
            )
    if "properties" in node:
        if declared_type != "object" or not isinstance(node["properties"], Mapping):
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
            declared_type != "object"
            or not isinstance(required, list)
            or not all(isinstance(name, str) for name in required)
            or not isinstance(properties, Mapping)
            or not set(required) <= set(properties)
        ):
            issues.append(
                BenchmarkIssue(
                    "provider-schema.required",
                    f"{path}: required must name declared properties",
                )
            )
    if declared_type == "object" and node.get("additionalProperties") is not False:
        issues.append(
            BenchmarkIssue(
                "provider-schema.additional-properties",
                f"{path}: object must be closed",
            )
        )
    if declared_type == "array" and "items" not in node:
        issues.append(
            BenchmarkIssue(
                "provider-schema.items", f"{path}: array requires typed items"
            )
        )
    if "items" in node:
        if declared_type != "array" or not isinstance(node["items"], Mapping):
            issues.append(
                BenchmarkIssue(
                    "provider-schema.type", f"{path}: items require array type"
                )
            )
        else:
            _validate_schema_node(node["items"], f"{path}.items", issues)
    if (
        any(key in node for key in {"minLength", "maxLength", "pattern"})
        and declared_type != "string"
    ):
        issues.append(
            BenchmarkIssue(
                "provider-schema.type", f"{path}: string constraint needs string type"
            )
        )
    if any(key in node for key in {"minimum", "maximum"}) and declared_type not in {
        "number",
        "integer",
    }:
        issues.append(
            BenchmarkIssue(
                "provider-schema.type", f"{path}: numeric constraint needs number type"
            )
        )
    if (
        any(key in node for key in {"minItems", "maxItems"})
        and declared_type != "array"
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
            if _SECRET_KEY.search(str(key)) and not (
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
        normalized = re.sub(
            r"(['\"])REDACTED\1", "REDACTED", value, flags=re.IGNORECASE
        )
        if _SECRET_VALUE.search(normalized):
            issues.append(
                BenchmarkIssue("receipt.secret", f"{path} contains a secret-like value")
            )


def _is_private_path(value: str) -> bool:
    return bool(_PRIVATE_PATH.search(_HTTP_URI.sub("", value)))


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
        not isinstance(tokens.get(name), int) or tokens[name] < 0 for name in required
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
        latency = sum(
            event.get("latency_ms", 0)
            for event in automated_events
            if isinstance(event, Mapping)
        )
        if (
            measurements.get("intent_service_event_count") != intent_count
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
    if measurements.get("needs_operator") is not (
        receipt.get("status") == "needs_operator"
    ):
        issues.append(
            BenchmarkIssue(
                "receipt.measurements", "needs_operator does not match run status"
            )
        )
    if measurements.get("total_artifact_bytes", 0) < (
        measurements.get("setup_artifact_bytes", 0)
        + measurements.get("governance_artifact_bytes", 0)
    ):
        issues.append(
            BenchmarkIssue(
                "receipt.measurements", "artifact byte totals are inconsistent"
            )
        )
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
