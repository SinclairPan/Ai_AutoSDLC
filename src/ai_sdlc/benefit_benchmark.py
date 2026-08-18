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


_PROTOCOL_KEYS = {"schema", "arms", "fixtures", "run_matrix", "attempt_budget", "execution_lock"}
_RUN_KEYS = {"run_id", "arm", "fixture", "position"}
_LOCK_KEYS = {"ai_sdlc_version", "ai_sdlc_commit", "source_tree_sha", "superpowers_commit", "benchmark_commit", "fixture_tree_sha256", "codex_version", "model", "reasoning_effort", "runner_script_sha256", "writer_timeout_seconds", "expert_timeout_seconds", "fixture_commitment"}
_EXPECTED_LOCK = {"ai_sdlc_version":"2.0.0","ai_sdlc_commit":"737bda39e05c53450e180a20581b7b7a70db9cf0","source_tree_sha":"3db58121e228a7a1c4c6b760c535d6df1ffdbe84","superpowers_commit":"b36e0829c6d0140e93cfef2ca599b1b07d4a7797","codex_version":"0.147.0","model":"gpt-5.6-sol","reasoning_effort":"high","runner_script_sha256":"134063e133f0b4244fa3b251acf973d4fe4b4aeeacbdc135211bf480f59f1477","writer_timeout_seconds":1800,"expert_timeout_seconds":900,"fixture_commitment":"pending-unbound"}
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
_SCHEDULE = (
    ("P", _FIXTURES[0], 1), ("S", _FIXTURES[0], 2), ("A00", _FIXTURES[0], 3), ("A10", _FIXTURES[0], 4), ("A11", _FIXTURES[0], 5),
    ("A00", _FIXTURES[1], 1), ("A10", _FIXTURES[1], 2), ("A11", _FIXTURES[1], 3), ("P", _FIXTURES[1], 4), ("S", _FIXTURES[1], 5),
    ("A11", _FIXTURES[2], 1), ("S", _FIXTURES[2], 2), ("A10", _FIXTURES[2], 3), ("P", _FIXTURES[2], 4), ("A00", _FIXTURES[2], 5),
)
_LEDGER_SCHEMA = "ai-sdlc-v2-benefit-attempt-ledger/v1"
_LEDGER_KEYS = {"schema", "attempts_started", "attempts"}
_ATTEMPT_KINDS = {
    "writer",
    "primary_expert",
    "cross_risk_expert",
    "expert_rereview",
    "technical_retry",
}
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_SECRET_KEY = re.compile(
    r"(?:api[_-]?key|secret|password|authorization|access[_-]?token|auth[_-]?token)",
    re.I,
)
_SECRET_VALUE = re.compile(r"(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{20,}|Bearer\s+\S+|AKIA[0-9A-Z]{16}|(?:API_KEY|TOKEN|SECRET)\s*=\s*(?!REDACTED)[^\s]+)", re.I)
_BENCHMARK_ROOT = Path(__file__).resolve().parents[2] / "benchmarks" / "ai-sdlc-v2-benefits"


def canonical_protocol_digest(protocol: BenchmarkProtocol) -> str:
    return sha256(protocol.canonical_bytes).hexdigest()


def reserve_provider_attempt(ledger_path: Path, request: AttemptRequest) -> AttemptReservation:
    """Atomically reserve an allowed logical Provider attempt before it starts."""
    with _ledger_lock(ledger_path):
        ledger = _load_ledger(ledger_path)
        if ledger["attempts_started"] >= 33:
            raise ValueError("Provider attempt budget of 33 is exhausted")
        _validate_reservation_request(ledger["attempts"], request)
        attempts_started = ledger["attempts_started"] + 1
        attempt_id = f"attempt-{attempts_started:03d}"
        ledger["attempts_started"] = attempts_started
        ledger["attempts"].append({"attempt_id": attempt_id, "run_id": request.run_id, "kind": request.kind, "arm": request.arm, "retry_reason": request.retry_reason, "retry_of_attempt_id": request.retry_of_attempt_id, "parent_attempt_id": request.parent_attempt_id, "role": request.role, "parent_digest": request.parent_digest, "candidate_digest": request.candidate_digest, "status": "reserved"})
        _atomic_write_json(ledger_path, ledger)
        return AttemptReservation(attempt_id, attempts_started, request)


def record_provider_completion(ledger_path: Path, completion: AttemptCompletion) -> None:
    """Record a terminal result only for an already-reserved attempt."""
    if completion.status not in {"completed", "technical_failure", "failed", "timeout", "needs_operator", "budget_exhausted"}:
        raise ValueError("Provider completion status is not allowed")
    with _ledger_lock(ledger_path):
        ledger = _load_ledger(ledger_path)
        for attempt in ledger["attempts"]:
            if attempt["attempt_id"] == completion.attempt_id:
                if attempt["status"] != "reserved":
                    raise ValueError("Provider attempt completion was already recorded")
                attempt["status"] = completion.status
                attempt["content_produced"] = completion.content_produced
                _atomic_write_json(ledger_path, ledger)
                return
        raise ValueError("Provider completion requires a prior reservation")


def validate_provider_output_schema(schema: Mapping[str, object]) -> list[BenchmarkIssue]:
    """Reject non-deterministic or open Provider structured-output schemas."""
    issues: list[BenchmarkIssue] = []
    _validate_schema_node(schema, "$", issues)
    return issues


def verify_receipt(
    receipt: Mapping[str, object], protocol: BenchmarkProtocol | None = None
) -> list[BenchmarkIssue]:
    """Verify public receipt semantics after JSON Schema validation has passed."""
    issues: list[BenchmarkIssue] = []
    if protocol is None:
        _validate_json_schema(receipt, _load_frozen_schema("run-receipt.schema.json"), "$", "receipt", issues)
    required = {
        "schema",
        "run_id",
        "arm",
        "fixture",
        "order",
        "status",
        "failure_classification",
        "digests",
        "timings",
        "token_usage",
        "human_events",
        "command_evidence",
        "changed_files",
        "final_candidate_tree_sha256",
        "loop",
        "external_evaluator",
    }
    _missing_issues(receipt, required, "receipt", issues)
    if issues:
        return issues
    _scan_public_value(receipt, "$", issues)
    digests = _mapping(receipt.get("digests"))
    evaluator = _mapping(receipt.get("external_evaluator"))
    candidate = receipt.get("final_candidate_tree_sha256")
    if not isinstance(candidate, str) or not _is_digest(candidate):
        issues.append(BenchmarkIssue("receipt.digest", "final candidate tree digest is missing or malformed"))
    for name, value in digests.items():
        if name.endswith("sha256") and (not isinstance(value, str) or not _is_digest(value)):
            issues.append(BenchmarkIssue("receipt.digest", f"{name} is not a SHA-256 digest"))
    evaluator_candidate = evaluator.get("candidate_tree_sha256")
    if evaluator_candidate != candidate or digests.get("candidate_tree_sha256") != candidate:
        issues.append(BenchmarkIssue("receipt.candidate-tree", "candidate tree digests must match"))
    _validate_receipt_timing(receipt.get("timings"), issues)
    _validate_token_usage(receipt.get("token_usage"), issues)
    _validate_human_events(receipt.get("human_events"), issues)
    if receipt.get("arm") == "A11":
        _validate_a11_close(receipt.get("loop"), issues)
    if protocol is not None:
        _validate_receipt_protocol_binding(receipt, protocol, issues)
    return issues


def verify_summary(
    summary: Mapping[str, object], protocol: BenchmarkProtocol
) -> list[BenchmarkIssue]:
    """Validate that the public summary only indexes every frozen receipt once."""
    issues: list[BenchmarkIssue] = []
    required = {"schema", "protocol_sha256", "runs", "metrics"}
    _missing_issues(summary, required, "summary", issues)
    unknown = set(summary) - required
    if unknown:
        issues.append(BenchmarkIssue("summary.unknown", "summary contains receipt-only fields"))
    if summary.get("schema") != "ai-sdlc-v2-benefit-summary/v1":
        issues.append(BenchmarkIssue("summary.schema", "unexpected summary schema"))
    digest = summary.get("protocol_sha256")
    if not isinstance(digest, str) or not _is_digest(digest):
        issues.append(BenchmarkIssue("summary.digest", "protocol_sha256 must be a SHA-256 digest"))
    runs = summary.get("runs")
    if not isinstance(runs, list):
        return issues + [BenchmarkIssue("summary.runs", "runs must be a list")]
    pairs: set[tuple[str, str]] = set()
    for run in runs:
        if not isinstance(run, Mapping):
            issues.append(BenchmarkIssue("summary.runs", "run entry must be an object"))
            continue
        arm, fixture, receipt_digest = run.get("arm"), run.get("fixture"), run.get("receipt_sha256")
        if not isinstance(arm, str) or not isinstance(fixture, str):
            issues.append(BenchmarkIssue("summary.runs", "run arm and fixture are required"))
            continue
        pairs.add((arm, fixture))
        if not isinstance(receipt_digest, str) or not _is_digest(receipt_digest):
            issues.append(BenchmarkIssue("summary.digest", "receipt digest must be SHA-256"))
    expected = {(run.arm, run.fixture) for run in protocol.run_matrix}
    if len(runs) != 15 or pairs != expected:
        issues.append(BenchmarkIssue("summary.matrix", "summary must index the frozen 15-run matrix"))
    _scan_public_value(summary, "$", issues)
    if digest != canonical_protocol_digest(protocol):
        issues.append(BenchmarkIssue("summary.protocol-digest", "summary must digest canonical protocol bytes"))
    run_ids = [run.get("run_id") for run in runs if isinstance(run, Mapping)]
    if run_ids != [run.run_id for run in protocol.run_matrix] or len(set(run_ids)) != 15:
        issues.append(BenchmarkIssue("summary.run-id", "summary run IDs must match canonical rows once"))
    receipt_digests = [run.get("receipt_sha256") for run in runs if isinstance(run, Mapping)]
    if len(set(receipt_digests)) != len(receipt_digests):
        issues.append(BenchmarkIssue("summary.receipt-digest", "receipt digests must be unique"))
    return issues


def _validate_receipt_protocol_binding(
    receipt: Mapping[str, object], protocol: BenchmarkProtocol, issues: list[BenchmarkIssue]
) -> None:
    matching = next((run for run in protocol.run_matrix if run.run_id == receipt.get("run_id")), None)
    if matching is None or (receipt.get("arm"), receipt.get("fixture"), receipt.get("order")) != (matching.arm, matching.fixture, matching.position):
        issues.append(BenchmarkIssue("receipt.protocol", "receipt must match one canonical run row"))
        return
    required = {"attempt_id", "provider_cwd", "instruction_chain_sha256", "timestamps", "identity"}
    _missing_issues(receipt, required, "receipt", issues)
    if receipt.get("provider_cwd") != "benchmark-task/":
        issues.append(BenchmarkIssue("receipt.provider-cwd", "provider cwd must be relative benchmark-task/"))
    identity = _mapping(receipt.get("identity"))
    expected_identity = protocol.execution_lock
    for key in _LOCK_KEYS:
        if identity.get(key) != getattr(expected_identity, key):
            issues.append(BenchmarkIssue("receipt.identity", f"identity lock mismatch: {key}"))
            break
    timestamps = _mapping(receipt.get("timestamps"))
    if not isinstance(timestamps.get("started_at"), str) or not isinstance(timestamps.get("ended_at"), str):
        issues.append(BenchmarkIssue("receipt.timestamps", "receipt requires start and end timestamps"))
    if receipt.get("arm") == "A11" and receipt.get("status") == "completed":
        loop = _mapping(receipt.get("loop"))
        if _mapping(loop.get("close")).get("state") != "closed":
            issues.append(BenchmarkIssue("receipt.a11.close", "completed A11 receipt must be closed"))
            return
        required_roles = {"primary"}
        if receipt.get("fixture") == "multi-tenant-security-review":
            required_roles.add("cross-risk")
        callbacks = loop.get("expert_callbacks")
        if not isinstance(callbacks, list) or {callback.get("role") for callback in callbacks if isinstance(callback, Mapping)} != required_roles:
            issues.append(BenchmarkIssue("receipt.a11.roles", "A11 callback roles do not match fixture"))


def load_protocol(path: Path) -> BenchmarkProtocol:
    """Load the protocol with a closed JSON object surface."""
    canonical_bytes = path.read_bytes()
    raw = json.loads(canonical_bytes)
    if not isinstance(raw, dict):
        raise ValueError("protocol must be a JSON object")
    _reject_unknown(raw, _PROTOCOL_KEYS, "protocol")
    _require_keys(raw, _PROTOCOL_KEYS, "protocol")
    runs = raw["run_matrix"]
    budget = raw["attempt_budget"]
    lock = raw["execution_lock"]
    if not isinstance(runs, list) or not isinstance(budget, dict) or not isinstance(lock, dict):
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
        attempt_budget=AttemptBudget(**{key: _integer(budget[key], key) for key in _BUDGET_KEYS}),
        execution_lock=ExecutionLock(**{key: _lock_value(lock[key], key) for key in _LOCK_KEYS}),
        canonical_bytes=canonical_bytes,
    )


def validate_protocol(protocol: BenchmarkProtocol, repo_root: Path) -> list[BenchmarkIssue]:
    """Validate immutable preregistration invariants without touching Providers."""
    _ = repo_root
    issues: list[BenchmarkIssue] = []
    if protocol.schema != "ai-sdlc-v2-benefit-protocol/v1":
        issues.append(BenchmarkIssue("protocol.schema", "unexpected protocol schema"))
    if protocol.arms != _ARMS:
        issues.append(BenchmarkIssue("protocol.arms", "arms must equal P,S,A00,A10,A11"))
    if protocol.fixtures != _FIXTURES:
        issues.append(BenchmarkIssue("protocol.fixtures", "fixtures must equal the frozen IDs"))
    pairs = {(run.arm, run.fixture) for run in protocol.run_matrix}
    if len(protocol.run_matrix) != 15 or len(pairs) != 15:
        issues.append(BenchmarkIssue("protocol.matrix", "run matrix must contain 15 unique pairs"))
    expected_pairs = {(arm, fixture) for arm in _ARMS for fixture in _FIXTURES}
    if pairs != expected_pairs:
        issues.append(BenchmarkIssue("protocol.matrix", "run matrix must cover every arm and fixture"))
    actual_schedule = tuple((run.arm, run.fixture, run.position) for run in protocol.run_matrix)
    if actual_schedule != _SCHEDULE or any(run.run_id != f"{run.arm}:{run.fixture}" for run in protocol.run_matrix):
        issues.append(BenchmarkIssue("protocol.schedule", "run schedule must match each frozen row"))
    for arm in _ARMS:
        positions = [run.position for run in protocol.run_matrix if run.arm == arm]
        if len(positions) != 3 or sum(positions) != 9:
            issues.append(BenchmarkIssue("protocol.schedule", f"{arm} mean position must equal 3"))
    if protocol.attempt_budget != AttemptBudget(33, 19, 4, 3, 7):
        issues.append(BenchmarkIssue("protocol.budget", "attempt budget must equal 33/19/4/3/7"))
    for key, expected in _EXPECTED_LOCK.items():
        if getattr(protocol.execution_lock, key) != expected:
            issues.append(BenchmarkIssue("protocol.lock", f"execution lock drift: {key}"))
    if protocol.execution_lock.fixture_commitment == "pending-unbound":
        issues.append(BenchmarkIssue("protocol.fixture-pending", "fixture commitment is pending Task 2"))
    return issues


def _load_ledger(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"schema": _LEDGER_SCHEMA, "attempts_started": 0, "attempts": []}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("attempt ledger must be an object")
    _reject_unknown(raw, _LEDGER_KEYS, "attempt ledger")
    _require_keys(raw, _LEDGER_KEYS, "attempt ledger")
    if raw["schema"] != _LEDGER_SCHEMA:
        raise ValueError("attempt ledger has an unexpected schema")
    if not isinstance(raw["attempts_started"], int) or raw["attempts_started"] < 0:
        raise ValueError("attempt ledger has invalid attempts_started")
    if not isinstance(raw["attempts"], list):
        raise ValueError("attempt ledger has invalid attempts")
    attempts = raw["attempts"]
    if raw["attempts_started"] != len(attempts):
        raise ValueError("attempt ledger count does not match attempts")
    expected_ids = [f"attempt-{index:03d}" for index in range(1, len(attempts) + 1)]
    if any(not isinstance(item, Mapping) or item.get("attempt_id") != expected for item, expected in zip(attempts, expected_ids, strict=True)):
        raise ValueError("attempt ledger attempt IDs are corrupt")
    return raw


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
        issues.append(BenchmarkIssue(f"{scope}.schema", f"{path} must be {expected_type}"))
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
                    issues.append(BenchmarkIssue(f"{scope}.schema", f"{path}.{key} is required"))
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in property_schemas:
                    issues.append(BenchmarkIssue(f"{scope}.schema", f"{path}.{key} is not allowed"))
        for key, child_schema in property_schemas.items():
            if key in value and isinstance(key, str) and isinstance(child_schema, Mapping):
                _validate_json_schema(value[key], child_schema, f"{path}.{key}", scope, issues)
    elif isinstance(value, list):
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
            issues.append(BenchmarkIssue(f"{scope}.schema", f"{path} does not match pattern"))
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
        return isinstance(value, (int, float)) and not isinstance(value, bool)
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
        if request.retry_reason not in {"transport", "schema", "provider_pre_output"}:
            raise ValueError("technical retry must be pre-output")
        if _count_kind(attempts, "technical_retry") >= 3:
            raise ValueError("technical retry budget is exhausted")
        prior = _attempt_by_id(attempts, request.retry_of_attempt_id)
        if prior is None or prior.get("run_id") != request.run_id:
            raise ValueError("technical retry requires a prior attempt")
        if prior.get("status") != "technical_failure" or prior.get("content_produced") is not False:
            raise ValueError("technical retry requires terminated pre-output failure")
        return
    if request.kind == "writer":
        if _count_kind(attempts, "writer") >= 15:
            raise ValueError("normal writer topology is exhausted")
        if any(item.get("kind") == "writer" and item.get("run_id") == request.run_id for item in attempts):
            raise ValueError("duplicate run replacement is forbidden")
        return
    if arm != "A11":
        raise ValueError("expert roles are only permitted for A11")
    if request.kind == "primary_expert":
        if _count_kind(attempts, "primary_expert") >= 3:
            raise ValueError("primary expert topology is exhausted")
        if any(item.get("kind") == "primary_expert" and item.get("run_id") == request.run_id for item in attempts):
            raise ValueError("duplicate primary expert is forbidden")
        if request.role != "primary" or not _is_digest_or_none(request.parent_digest) or not _is_digest_or_none(request.candidate_digest):
            raise ValueError("expert requires role and parent/candidate digests")
        return
    if request.kind == "cross_risk_expert":
        if request.run_id != "A11:multi-tenant-security-review" or _count_kind(attempts, "cross_risk_expert") >= 1:
            raise ValueError("cross-risk expert topology is exhausted")
        if request.role != "cross-risk" or not _is_digest_or_none(request.parent_digest) or not _is_digest_or_none(request.candidate_digest):
            raise ValueError("expert requires role and parent/candidate digests")
        return
    if _count_kind(attempts, "expert_rereview") >= 4:
        raise ValueError("expert rereview topology is exhausted")
    parent = _attempt_by_id(attempts, request.parent_attempt_id)
    if parent is None or parent.get("run_id") != request.run_id or parent.get("kind") not in {"primary_expert", "cross_risk_expert"}:
        raise ValueError("expert rereview requires an existing expert reservation")
    if parent.get("status") != "completed" or parent.get("role") != request.role:
        raise ValueError("expert rereview requires completed matching expert")
    if any(item.get("kind") == "expert_rereview" and item.get("parent_attempt_id") == request.parent_attempt_id for item in attempts):
        raise ValueError("expert can have at most one rereview")
    if request.parent_digest != parent.get("parent_digest") or request.candidate_digest != parent.get("candidate_digest"):
        raise ValueError("expert rereview digests must match parent")


def _count_kind(attempts: list[object], kind: str) -> int:
    return sum(isinstance(item, Mapping) and item.get("kind") == kind for item in attempts)


def _attempt_by_id(attempts: list[object], attempt_id: str | None) -> Mapping[str, object] | None:
    if not attempt_id:
        return None
    return next((item for item in attempts if isinstance(item, Mapping) and item.get("attempt_id") == attempt_id), None)


def _is_digest_or_none(value: str | None) -> bool:
    return isinstance(value, str) and _is_digest(value)


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
        "$schema", "$id", "title", "description", "type", "properties", "required",
        "additionalProperties", "items", "enum", "const", "minLength", "maxLength",
        "pattern", "minimum", "maximum", "minItems", "maxItems",
    }
    for key in node:
        if key not in supported:
            issues.append(BenchmarkIssue("provider-schema.keyword", f"{path}: unsupported {key}"))
    declared_type = node.get("type")
    if declared_type not in {"object", "array", "string", "number", "integer", "boolean", "null"}:
        issues.append(BenchmarkIssue("provider-schema.type", f"{path}: explicit supported type is required"))
    if "const" in node and not _value_matches_type(node["const"], declared_type):
        issues.append(BenchmarkIssue("provider-schema.type", f"{path}: const must match type"))
    if "enum" in node:
        enum = node["enum"]
        if not isinstance(enum, list) or not enum or any(not _value_matches_type(value, declared_type) for value in enum):
            issues.append(BenchmarkIssue("provider-schema.type", f"{path}: enum must match type"))
    if "properties" in node:
        if declared_type != "object" or not isinstance(node["properties"], Mapping):
            issues.append(BenchmarkIssue("provider-schema.type", f"{path}: properties require object type"))
        else:
            for name, child in node["properties"].items():
                if not isinstance(name, str) or not isinstance(child, Mapping):
                    issues.append(BenchmarkIssue("provider-schema.node", f"{path}: invalid property"))
                else:
                    _validate_schema_node(child, f"{path}.properties.{name}", issues)
    if "required" in node:
        required = node["required"]
        properties = node.get("properties")
        if declared_type != "object" or not isinstance(required, list) or not all(isinstance(name, str) for name in required) or not isinstance(properties, Mapping) or not set(required) <= set(properties):
            issues.append(BenchmarkIssue("provider-schema.required", f"{path}: required must name declared properties"))
    if declared_type == "object" and node.get("additionalProperties") is not False:
        issues.append(BenchmarkIssue("provider-schema.additional-properties", f"{path}: object must be closed"))
    if declared_type == "array" and "items" not in node:
        issues.append(BenchmarkIssue("provider-schema.items", f"{path}: array requires typed items"))
    if "items" in node:
        if declared_type != "array" or not isinstance(node["items"], Mapping):
            issues.append(BenchmarkIssue("provider-schema.type", f"{path}: items require array type"))
        else:
            _validate_schema_node(node["items"], f"{path}.items", issues)
    if any(key in node for key in {"minLength", "maxLength", "pattern"}) and declared_type != "string":
        issues.append(BenchmarkIssue("provider-schema.type", f"{path}: string constraint needs string type"))
    if any(key in node for key in {"minimum", "maximum"}) and declared_type not in {"number", "integer"}:
        issues.append(BenchmarkIssue("provider-schema.type", f"{path}: numeric constraint needs number type"))
    if any(key in node for key in {"minItems", "maxItems"}) and declared_type != "array":
        issues.append(BenchmarkIssue("provider-schema.type", f"{path}: array constraint needs array type"))
    for minimum, maximum in (("minLength", "maxLength"), ("minItems", "maxItems")):
        if minimum in node and (not _non_bool_int(node[minimum]) or node[minimum] < 0):
            issues.append(BenchmarkIssue("provider-schema.operand", f"{path}: {minimum} must be non-negative integer"))
        if maximum in node and (not _non_bool_int(node[maximum]) or node[maximum] < 0):
            issues.append(BenchmarkIssue("provider-schema.operand", f"{path}: {maximum} must be non-negative integer"))
        if _non_bool_int(node.get(minimum)) and _non_bool_int(node.get(maximum)) and node[minimum] > node[maximum]:
            issues.append(BenchmarkIssue("provider-schema.range", f"{path}: invalid {minimum}/{maximum}"))
    if "minimum" in node and (not _finite_number(node["minimum"])):
        issues.append(BenchmarkIssue("provider-schema.operand", f"{path}: minimum must be numeric"))
    if "maximum" in node and (not _finite_number(node["maximum"])):
        issues.append(BenchmarkIssue("provider-schema.operand", f"{path}: maximum must be numeric"))
    if _finite_number(node.get("minimum")) and _finite_number(node.get("maximum")) and node["minimum"] > node["maximum"]:
        issues.append(BenchmarkIssue("provider-schema.range", f"{path}: invalid minimum/maximum"))
    if "pattern" in node and isinstance(node["pattern"], str):
        try:
            re.compile(node["pattern"])
        except re.error:
            issues.append(BenchmarkIssue("provider-schema.pattern", f"{path}: invalid regex"))


def _non_bool_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _value_matches_type(value: object, declared_type: object) -> bool:
    if declared_type == "object":
        return isinstance(value, Mapping)
    if declared_type == "array":
        return isinstance(value, list)
    if declared_type == "string":
        return isinstance(value, str)
    if declared_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if declared_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if declared_type == "boolean":
        return isinstance(value, bool)
    return value is None and declared_type == "null"


def _missing_issues(
    value: Mapping[str, object], required: set[str], prefix: str, issues: list[BenchmarkIssue]
) -> None:
    for key in sorted(required - set(value)):
        issues.append(BenchmarkIssue(f"{prefix}.missing", f"missing required field {key}"))


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _is_digest(value: str) -> bool:
    return bool(_SHA256.fullmatch(value))


def _scan_public_value(value: object, path: str, issues: list[BenchmarkIssue]) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _SECRET_KEY.search(str(key)):
                issues.append(BenchmarkIssue("receipt.secret", f"{path}.{key} is a secret-like field"))
            _scan_public_value(child, f"{path}.{key}", issues)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_public_value(child, f"{path}[{index}]", issues)
    elif isinstance(value, str):
        if _is_private_path(value):
            issues.append(BenchmarkIssue("receipt.absolute-path", f"{path} contains an absolute path"))
        if _SECRET_VALUE.search(value):
            issues.append(BenchmarkIssue("receipt.secret", f"{path} contains a secret-like value"))


def _is_private_path(value: str) -> bool:
    return value.startswith(("/", "\\\\", "//", "file:")) or bool(re.match(r"^[A-Za-z]:[\\/]", value))


def _validate_receipt_timing(value: object, issues: list[BenchmarkIssue]) -> None:
    timings = _mapping(value)
    components = (
        "setup_wall_seconds", "framework_init_wall_seconds", "provider_wall_seconds",
        "governance_wall_seconds", "review_wall_seconds", "evaluation_wall_seconds",
    )
    if any(not isinstance(timings.get(name), (int, float)) for name in components):
        issues.append(BenchmarkIssue("receipt.timing", "all additive timing components are required"))
        return
    total = sum(timings[name] for name in components)
    if timings.get("end_to_end_wall_seconds") != total:
        issues.append(BenchmarkIssue("receipt.timing", "end-to-end timing must equal additive components"))


def _validate_token_usage(value: object, issues: list[BenchmarkIssue]) -> None:
    tokens = _mapping(value)
    required = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")
    if any(not isinstance(tokens.get(name), int) or tokens[name] < 0 for name in required):
        issues.append(BenchmarkIssue("receipt.tokens", "token usage must contain non-negative integers"))


def _validate_human_events(value: object, issues: list[BenchmarkIssue]) -> None:
    if not isinstance(value, list):
        issues.append(BenchmarkIssue("receipt.human-events", "human_events must be a list"))
        return
    allowed = {"operator_authorization", "operator_confirmation", "operator_adjudication"}
    for event in value:
        if not isinstance(event, Mapping) or event.get("type") not in allowed:
            issues.append(BenchmarkIssue("receipt.human-events", "automated events are not human events"))


def _validate_a11_close(value: object, issues: list[BenchmarkIssue]) -> None:
    loop = _mapping(value)
    close = _mapping(loop.get("close"))
    callbacks = loop.get("expert_callbacks")
    if close.get("state") != "closed":
        return
    if not isinstance(callbacks, list) or not callbacks:
        issues.append(BenchmarkIssue("receipt.a11.close", "A11 Close requires expert callbacks"))
        return
    for callback in callbacks:
        if not isinstance(callback, Mapping) or callback.get("status") != "pass":
            issues.append(BenchmarkIssue("receipt.a11.close", "A11 callbacks must all pass"))
            return
        required = {"role", "parent_digest", "child_session", "finding_digest", "repair_digest", "rereview_digest"}
        if required - set(callback):
            issues.append(BenchmarkIssue("receipt.a11.close", "A11 callback evidence is incomplete"))
            return


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
