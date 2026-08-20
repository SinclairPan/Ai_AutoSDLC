"""Fail-closed, lightweight directional benefit experiment.

This module is deliberately separate from the formal benchmark authority.  It can
prepare and rehearse the frozen matrix without starting a Provider session.  A
future real runner must consume the preflight and a separate human budget approval.
"""

from __future__ import annotations

import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from ai_sdlc.benefit_benchmark_arms import (
    ARM_IDS,
    PreparedArm,
    prepare_arm,
    verify_method_instruction_immutability,
    verify_prepared_arm_identity,
)
from ai_sdlc.benefit_benchmark_fixtures import (
    FIXTURE_IDS,
    PreparedFixture,
    ProviderIsolationProfile,
    build_provider_isolation_profile,
    prepare_fixture,
    run_provider_isolated,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - formal execution is macOS-only.
    fcntl = None  # type: ignore[assignment]


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DIRECTIONAL_ROOT = _REPO_ROOT / "benchmarks" / "ai-sdlc-v2-directional"
_FORMAL_ROOT = _REPO_ROOT / "benchmarks" / "ai-sdlc-v2-benefits"
_SEALED_BASE = Path("/private/tmp/ai-sdlc-v2-benefit-evaluator")
_SEALED_R1_ROOT = _SEALED_BASE / "v2-benefits-20260819-r1"
_SEALED_R2_ROOT = _SEALED_BASE / "v2-benefits-20260819-r2"
_SEALED_R3_ROOT = _SEALED_BASE / "v2-benefits-20260819-r3"
_SOURCE_BASE = Path("/private/tmp/ai-sdlc-v2-benefit-source")
_RUN_ID = re.compile(r"^run-[a-f0-9]{24}$")
_SESSION_ID = re.compile(r"^session-[a-f0-9]{24}$")
_LABELS = (
    "directional engineering observation",
    "n=3 per arm",
    "single run per task",
    "not statistically significant",
    "not production SLA",
    "no generalization",
)
_MANIFEST_KEYS = {
    "schema",
    "study_label",
    "fixture_ids",
    "arm_ids",
    "model",
    "reasoning_effort",
    "writer_output_token_cap",
    "expert_output_token_cap",
    "max_provider_sessions",
    "technical_retries",
    "expert_rereviews",
    "common_prompt_path",
    "common_prompt_sha256",
    "arm_manifest_path",
    "arm_manifest_sha256",
    "fixture_manifest_path",
    "fixture_manifest_sha256",
    "evaluator_classification",
    "actual_r2_authority_status",
    "runs",
    "sessions",
}
_RUN_KEYS = {"run_id", "fixture_id", "arm_id", "ordinal"}
_SESSION_KEYS = {
    "session_id",
    "run_id",
    "fixture_id",
    "arm_id",
    "kind",
    "role",
    "ordinal",
    "retry",
    "rereview",
}
_METRIC_KEYS = {
    "schema",
    "run_id",
    "external_verified_delivery",
    "weighted_acceptance_coverage",
    "severe_defect_escape_count",
    "wall_time_seconds",
    "provider_sessions",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "currency_cost",
}
_RECEIPT_KEYS = {
    "schema",
    "writer",
    "run_id",
    "status",
    "metric",
    "matrix_action",
    "winner",
}
_BLIND_EVALUATOR_KEYS = {
    "schema",
    "opaque_run_id",
    "candidate_snapshot_sha256",
}
_PRESENTATION_KEYS = {
    "schema",
    "homepage_tracks",
    "research_controls",
    "show_raw_paired_values",
    "show_losses",
    "quality_cost_frontier",
    "winner_cherry_pick",
    "required_labels",
}
_WEBSITE_DATA_KEYS = {
    "schema",
    "status",
    "homepage_tracks",
    "research_controls",
    "comparisons",
    "raw_paired_values",
    "losses",
    "quality_cost_frontier",
    "winner",
    "labels",
}
_PREFLIGHT_KEYS = {
    "schema",
    "manifest_sha256",
    "execution_order",
    "output_root",
    "provider_calls_started",
    "formal_authority_status",
    "evaluator_classification",
    "model",
    "reasoning_effort",
    "writer_sessions",
    "expert_sessions",
    "hard_session_cap",
    "technical_retries",
    "input_tokens",
    "output_tokens",
    "currency_cost",
}
_MODEL_FAILURES = {"model-timeout", "model-nonzero", "invalid-output"}
_INFRA_FAILURES = {
    "provider-5xx",
    "network",
    "rate-limit",
    "host",
    "isolation",
    "ledger-corruption",
}


@dataclass(frozen=True)
class DirectionalRun:
    run_id: str
    fixture_id: str
    arm_id: str
    ordinal: int


@dataclass(frozen=True)
class DirectionalSession:
    session_id: str
    run_id: str
    fixture_id: str
    arm_id: str
    kind: str
    role: str
    ordinal: int
    retry: bool
    rereview: bool


@dataclass(frozen=True)
class DirectionalManifest:
    schema: str
    study_label: str
    fixture_ids: tuple[str, ...]
    arm_ids: tuple[str, ...]
    model: str
    reasoning_effort: str
    writer_output_token_cap: int
    expert_output_token_cap: int
    max_provider_sessions: int
    technical_retries: int
    expert_rereviews: int
    common_prompt_path: str
    common_prompt_sha256: str
    arm_manifest_path: str
    arm_manifest_sha256: str
    fixture_manifest_path: str
    fixture_manifest_sha256: str
    evaluator_classification: str
    actual_r2_authority_status: str
    runs: tuple[DirectionalRun, ...]
    sessions: tuple[DirectionalSession, ...]
    canonical_sha256: str


@dataclass(frozen=True)
class FailureSemantics:
    category: str
    matrix_status: str
    continue_matrix: bool


@dataclass(frozen=True)
class DirectionalMetric:
    schema: str
    run_id: str
    external_verified_delivery: bool
    weighted_acceptance_coverage: float
    severe_defect_escape_count: int
    wall_time_seconds: float
    provider_sessions: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    currency_cost: float | None


@dataclass(frozen=True)
class DirectionalSummary:
    labels: tuple[str, ...]
    publishable: bool
    evaluator_inputs: tuple[str, ...]
    main_quality_fields: tuple[str, ...]
    raw_receipts: tuple[Mapping[str, object], ...]
    paired_deltas: tuple[Mapping[str, object], ...]


@dataclass(frozen=True)
class DirectionalPreflight:
    schema: str
    manifest_sha256: str
    execution_order: tuple[str, ...]
    output_root: str
    provider_calls_started: int
    formal_authority_status: str
    evaluator_classification: str
    model: str
    reasoning_effort: str
    writer_sessions: int
    expert_sessions: int
    hard_session_cap: int
    technical_retries: int
    input_tokens: int | None
    output_tokens: int | None
    currency_cost: float | None


@dataclass(frozen=True)
class ProtectedRoot:
    label: str
    path: Path
    access: str = "deny-read-write"


@dataclass(frozen=True)
class FakeRehearsalResult:
    prepared_workspaces: int
    simulated_sessions: int
    external_provider_calls: int
    input_tokens: int | None
    output_tokens: int | None
    currency_cost: float | None
    ledger_path: Path
    prepared_arms: tuple[PreparedArm, ...]


@dataclass(frozen=True)
class DirectionalIsolationCanary:
    baseline_exec_allowed: bool
    candidate_input_read_allowed: bool
    direct_reads_denied: bool
    directory_lists_denied: bool
    parent_escape_denied: bool
    environment_leak_denied: bool
    add_dir_denied: bool
    output_append_denied: bool
    output_create_denied: bool
    output_rename_denied: bool
    method_chmod_denied: bool
    nested_provider_denied: bool
    residue_free: bool

    @property
    def passed(self) -> bool:
        return all(
            (
                self.baseline_exec_allowed,
                self.candidate_input_read_allowed,
                self.direct_reads_denied,
                self.directory_lists_denied,
                self.parent_escape_denied,
                self.environment_leak_denied,
                self.add_dir_denied,
                self.output_append_denied,
                self.output_create_denied,
                self.output_rename_denied,
                self.method_chmod_denied,
                self.nested_provider_denied,
                self.residue_free,
            )
        )


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _closed(raw: object, keys: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != keys:
        raise ValueError(f"{label} must be a closed object")
    return raw


def _sha_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def directional_manifest_path() -> Path:
    return _DIRECTIONAL_ROOT / "manifest.json"


def _resolved_bound_file(manifest_path: Path, relative: str, expected: str) -> Path:
    try:
        candidate = (manifest_path.parent / relative).resolve(strict=True)
        candidate.relative_to(_REPO_ROOT)
    except (OSError, ValueError) as error:
        raise ValueError(
            "manifest bound path is unavailable or escapes the repository"
        ) from error
    if _sha_file(candidate) != expected:
        raise ValueError("manifest bound file digest changed")
    return candidate


def load_directional_manifest(
    path: Path = _DIRECTIONAL_ROOT / "manifest.json",
) -> DirectionalManifest:
    canonical = path.read_bytes()
    raw = _closed(json.loads(canonical), _MANIFEST_KEYS, "directional manifest")
    runs_raw = raw["runs"]
    sessions_raw = raw["sessions"]
    if not isinstance(runs_raw, list) or not isinstance(sessions_raw, list):
        raise ValueError("directional matrix must be lists")
    runs = tuple(
        DirectionalRun(**_closed(item, _RUN_KEYS, "directional run"))
        for item in runs_raw
    )
    sessions = tuple(
        DirectionalSession(**_closed(item, _SESSION_KEYS, "directional session"))
        for item in sessions_raw
    )
    manifest = DirectionalManifest(
        schema=str(raw["schema"]),
        study_label=str(raw["study_label"]),
        fixture_ids=tuple(raw["fixture_ids"]),
        arm_ids=tuple(raw["arm_ids"]),
        model=str(raw["model"]),
        reasoning_effort=str(raw["reasoning_effort"]),
        writer_output_token_cap=int(raw["writer_output_token_cap"]),
        expert_output_token_cap=int(raw["expert_output_token_cap"]),
        max_provider_sessions=int(raw["max_provider_sessions"]),
        technical_retries=int(raw["technical_retries"]),
        expert_rereviews=int(raw["expert_rereviews"]),
        common_prompt_path=str(raw["common_prompt_path"]),
        common_prompt_sha256=str(raw["common_prompt_sha256"]),
        arm_manifest_path=str(raw["arm_manifest_path"]),
        arm_manifest_sha256=str(raw["arm_manifest_sha256"]),
        fixture_manifest_path=str(raw["fixture_manifest_path"]),
        fixture_manifest_sha256=str(raw["fixture_manifest_sha256"]),
        evaluator_classification=str(raw["evaluator_classification"]),
        actual_r2_authority_status=str(raw["actual_r2_authority_status"]),
        runs=runs,
        sessions=sessions,
        canonical_sha256=sha256(canonical).hexdigest(),
    )
    _validate_directional_manifest(manifest, path)
    return manifest


def _validate_directional_manifest(
    manifest: DirectionalManifest, manifest_path: Path
) -> None:
    if (
        manifest.schema != "ai-sdlc-v2-directional-manifest/v1"
        or manifest.study_label != _LABELS[0]
        or manifest.fixture_ids != FIXTURE_IDS
        or manifest.arm_ids != ARM_IDS
        or manifest.model != "gpt-5.6-sol"
        or manifest.reasoning_effort != "high"
        or manifest.writer_output_token_cap != 1800
        or manifest.expert_output_token_cap != 900
        or manifest.max_provider_sessions != 19
        or manifest.technical_retries != 0
        or manifest.expert_rereviews != 0
        or manifest.evaluator_classification != "legacy-directional-evaluator"
        or manifest.actual_r2_authority_status != "NO-GO"
    ):
        raise ValueError("directional manifest constants changed")
    _resolved_bound_file(
        manifest_path, manifest.common_prompt_path, manifest.common_prompt_sha256
    )
    _resolved_bound_file(
        manifest_path, manifest.arm_manifest_path, manifest.arm_manifest_sha256
    )
    _resolved_bound_file(
        manifest_path, manifest.fixture_manifest_path, manifest.fixture_manifest_sha256
    )
    if len(manifest.runs) != 15 or tuple(run.ordinal for run in manifest.runs) != tuple(
        range(1, 16)
    ):
        raise ValueError("directional run matrix is not exact")
    if len({run.run_id for run in manifest.runs}) != 15 or any(
        not _RUN_ID.fullmatch(run.run_id)
        or run.fixture_id not in FIXTURE_IDS
        or run.arm_id not in ARM_IDS
        or run.arm_id in run.run_id
        for run in manifest.runs
    ):
        raise ValueError("directional run identity is invalid")
    for fixture_id in FIXTURE_IDS:
        block = tuple(run for run in manifest.runs if run.fixture_id == fixture_id)
        if len(block) != 5 or frozenset(run.arm_id for run in block) != frozenset(
            ARM_IDS
        ):
            raise ValueError("directional fixture block is not a complete rotation")
    rotations = {
        tuple(run.arm_id for run in manifest.runs if run.fixture_id == fixture_id)
        for fixture_id in FIXTURE_IDS
    }
    if len(rotations) != 3:
        raise ValueError("directional arm order is not rotated")
    if len(manifest.sessions) != 19 or tuple(
        session.ordinal for session in manifest.sessions
    ) != tuple(range(1, 20)):
        raise ValueError("directional session table is not exact")
    run_by_id = {run.run_id: run for run in manifest.runs}
    if len({item.session_id for item in manifest.sessions}) != 19:
        raise ValueError("directional session identifiers are duplicated")
    for session in manifest.sessions:
        run = run_by_id.get(session.run_id)
        if (
            not _SESSION_ID.fullmatch(session.session_id)
            or run is None
            or (run.fixture_id, run.arm_id) != (session.fixture_id, session.arm_id)
            or session.retry
            or session.rereview
        ):
            raise ValueError("directional session binding is invalid")
    writers = tuple(item for item in manifest.sessions if item.kind == "writer")
    experts = tuple(item for item in manifest.sessions if item.kind != "writer")
    if (
        len(writers) != 15
        or tuple(item.run_id for item in writers)
        != tuple(item.run_id for item in manifest.runs)
        or any(item.role != "Writer" for item in writers)
        or len(experts) != 4
        or any(item.arm_id != "A11" for item in experts)
    ):
        raise ValueError("directional writer/expert topology is invalid")
    primary = {
        (item.fixture_id, item.role)
        for item in experts
        if item.kind == "primary_expert"
    }
    cross = {
        (item.fixture_id, item.role)
        for item in experts
        if item.kind == "cross_risk_expert"
    }
    if primary != {(fixture, "Primary") for fixture in FIXTURE_IDS} or cross != {
        ("multi-tenant-security-review", "Cross-risk")
    }:
        raise ValueError("directional expert topology is invalid")


def classify_failure(category: str) -> FailureSemantics:
    if category in _MODEL_FAILURES:
        return FailureSemantics(category, "cell-terminal-failure", True)
    if category in _INFRA_FAILURES:
        return FailureSemantics(category, "matrix-abort-incomplete", False)
    if category == "budget-exhausted":
        return FailureSemantics(category, "matrix-incomplete", False)
    raise ValueError("failure category is not closed")


def initialize_attempt_ledger(path: Path, manifest: DirectionalManifest) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    header = {
        "schema": "ai-sdlc-v2-directional-attempt-ledger/v1",
        "kind": "header",
        "manifest_sha256": manifest.canonical_sha256,
        "max_provider_sessions": 19,
        "technical_retries": 0,
        "execution_order": [session.session_id for session in manifest.sessions],
    }
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        _write_all(descriptor, _canonical_bytes(header) + b"\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return path


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("short write")
        offset += written


def _ledger_rows_from_descriptor(descriptor: int) -> list[Mapping[str, Any]]:
    info = os.fstat(descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
        or info.st_uid != os.getuid()
    ):
        raise ValueError("ledger identity is invalid")
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 64 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    try:
        rows = [
            json.loads(line) for line in b"".join(chunks).decode("utf-8").splitlines()
        ]
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("ledger is corrupt") from error
    if not rows:
        raise ValueError("ledger is empty")
    header = _closed(
        rows[0],
        {
            "schema",
            "kind",
            "manifest_sha256",
            "max_provider_sessions",
            "technical_retries",
            "execution_order",
        },
        "ledger header",
    )
    order = header["execution_order"]
    if (
        header["schema"] != "ai-sdlc-v2-directional-attempt-ledger/v1"
        or header["kind"] != "header"
        or not isinstance(header["manifest_sha256"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", header["manifest_sha256"])
        or header["max_provider_sessions"] != 19
        or header["technical_retries"] != 0
        or not isinstance(order, list)
        or len(order) != 19
        or any(
            not isinstance(item, str) or not _SESSION_ID.fullmatch(item)
            for item in order
        )
        or len(set(order)) != 19
    ):
        raise ValueError("ledger header is corrupt")
    reservations: list[str] = []
    launches_started: set[str] = set()
    launches_terminal: set[str] = set()
    expert_findings: set[str] = set()
    resumes: set[str] = set()
    for row in rows[1:]:
        if not isinstance(row, Mapping):
            raise ValueError("ledger row is corrupt")
        kind = row.get("kind")
        if kind == "reservation":
            item = _closed(
                row,
                {"kind", "ordinal", "session_id", "provider_launched"},
                "ledger reservation",
            )
            session_id = item["session_id"]
            if (
                not isinstance(session_id, str)
                or session_id not in order
                or session_id in reservations
                or item["ordinal"] != len(reservations) + 1
                or order[len(reservations)] != session_id
                or item["provider_launched"] is not False
            ):
                raise ValueError("ledger reservation is corrupt")
            reservations.append(session_id)
        elif kind == "launch-started":
            item = _closed(
                row,
                {
                    "kind",
                    "ordinal",
                    "session_id",
                    "command_sha256",
                    "provider_launched",
                },
                "ledger launch started",
            )
            session_id = item["session_id"]
            if (
                not isinstance(session_id, str)
                or session_id not in reservations
                or session_id in launches_started
                or item["ordinal"] != reservations.index(session_id) + 1
                or not isinstance(item["command_sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", item["command_sha256"])
                or item["provider_launched"] is not True
            ):
                raise ValueError("ledger launch started is corrupt")
            launches_started.add(session_id)
        elif kind in {"launch-completed", "launch-failed"}:
            item = _closed(
                row,
                {
                    "kind",
                    "session_id",
                    "returncode",
                    "failure",
                    "stdout_sha256",
                    "stderr_sha256",
                    "provider_launched",
                },
                "ledger launch terminal",
            )
            session_id = item["session_id"]
            returncode = item["returncode"]
            failure = item["failure"]
            if (
                not isinstance(session_id, str)
                or session_id not in launches_started
                or session_id in launches_terminal
                or item["provider_launched"] is not True
                or not isinstance(item["stdout_sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", item["stdout_sha256"])
                or not isinstance(item["stderr_sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", item["stderr_sha256"])
                or (
                    kind == "launch-completed"
                    and (returncode != 0 or failure is not None)
                )
                or (
                    kind == "launch-failed"
                    and (
                        failure not in {"nonzero", "timeout", "launch-error"}
                        or (
                            failure == "nonzero"
                            and (
                                isinstance(returncode, bool)
                                or not isinstance(returncode, int)
                                or returncode == 0
                            )
                        )
                        or (
                            failure in {"timeout", "launch-error"}
                            and returncode is not None
                        )
                    )
                )
            ):
                raise ValueError("ledger launch terminal is corrupt")
            launches_terminal.add(session_id)
        elif kind == "expert-finding":
            item = _closed(
                row,
                {
                    "kind",
                    "session_id",
                    "run_id",
                    "role",
                    "snapshot_sha256",
                    "finding_sha256",
                    "read_only",
                    "findings_only",
                    "candidate_writes",
                    "child_subprocesses",
                    "retry",
                    "provider_launched",
                },
                "ledger expert finding",
            )
            session_id = item["session_id"]
            if (
                not isinstance(session_id, str)
                or session_id not in reservations
                or session_id in expert_findings
                or not isinstance(item["run_id"], str)
                or not _RUN_ID.fullmatch(item["run_id"])
                or item["read_only"] is not True
                or item["findings_only"] is not True
                or item["candidate_writes"] != 0
                or item["child_subprocesses"] != 0
                or item["retry"] is not False
                or item["provider_launched"] is not False
                or not isinstance(item["snapshot_sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", item["snapshot_sha256"])
                or not isinstance(item["finding_sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", item["finding_sha256"])
            ):
                raise ValueError("ledger expert finding is corrupt")
            expert_findings.add(session_id)
        elif kind == "writer-resume":
            item = _closed(
                row,
                {
                    "kind",
                    "run_id",
                    "writer_session_id",
                    "same_live_session",
                    "new_provider_session",
                    "finding_digest",
                },
                "ledger writer resume",
            )
            run_id = item["run_id"]
            if (
                not isinstance(run_id, str)
                or not _RUN_ID.fullmatch(run_id)
                or run_id in resumes
                or item["writer_session_id"] not in reservations
                or item["same_live_session"] is not True
                or item["new_provider_session"] is not False
                or not isinstance(item["finding_digest"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", item["finding_digest"])
            ):
                raise ValueError("ledger writer resume is corrupt")
            resumes.add(run_id)
        else:
            raise ValueError("ledger row kind is corrupt")
    return rows


def reserve_session(path: Path, session_id: str, *, retry: bool = False) -> int:
    """Reserve a fake-rehearsal slot without making it launchable."""
    if retry:
        raise ValueError("technical retry is forbidden")
    descriptor = os.open(path, os.O_RDWR | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0))
    try:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        rows = _ledger_rows_from_descriptor(descriptor)
        header = rows[0]
        order = header.get("execution_order")
        reserved = [
            row.get("session_id")
            for row in rows[1:]
            if row.get("kind") == "reservation"
        ]
        if not isinstance(order, list) or session_id not in order:
            if len(reserved) >= 19:
                raise ValueError("Provider session cap rejects attempt 20")
            raise ValueError("session is not predeclared")
        if session_id in reserved:
            raise ValueError("duplicate session reservation")
        next_index = len(reserved)
        if next_index >= 19:
            raise ValueError("Provider session cap rejects attempt 20")
        if order[next_index] != session_id:
            raise ValueError("session reservation order changed")
        row = {
            "kind": "reservation",
            "ordinal": next_index + 1,
            "session_id": session_id,
            "provider_launched": False,
        }
        _write_all(descriptor, _canonical_bytes(row) + b"\n")
        os.fsync(descriptor)
        return next_index + 1
    finally:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _provider_command_digest(argv: Sequence[str]) -> str:
    if not argv or any(not isinstance(item, str) or not item for item in argv):
        raise ValueError("Provider command is invalid")
    return sha256(_canonical_bytes(list(argv))).hexdigest()


def _validate_directional_provider_launch(
    manifest: DirectionalManifest,
    session_id: str,
    prepared: PreparedArm,
    profile: ProviderIsolationProfile,
    argv: Sequence[str],
) -> None:
    session = next(
        (item for item in manifest.sessions if item.session_id == session_id), None
    )
    if session is None:
        raise ValueError("Provider session is not predeclared")
    verify_prepared_directional_arm(prepared)
    command = tuple(argv)
    required_flags = {
        "--ephemeral",
        "--json",
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
    }

    def option_value(option: str) -> str | None:
        if command.count(option) != 1:
            return None
        index = command.index(option)
        return command[index + 1] if index + 1 < len(command) else None

    if (
        session.arm_id != prepared.arm_id
        or session.fixture_id != prepared.fixture_id
        or tuple(profile.argv) != command
        or Path(profile.run_root) != prepared.provider_cwd
        or len(command) < 2
        or command[0] != prepared.codex.executable
        or command[1] != "exec"
        or not required_flags <= set(command)
        or command[-1] != "-"
        or option_value("--model") != "gpt-5.6-sol"
        or option_value("-c") != 'model_reasoning_effort="high"'
        or option_value("--sandbox")
        != ("workspace-write" if session.kind == "writer" else "read-only")
        or "--add-dir" in command
        or any(item.startswith("--add-dir=") for item in command)
    ):
        raise ValueError("Provider launch binding is invalid")
    if session.kind == "writer":
        if command.count("-C") != 1:
            raise ValueError("Provider writer cwd binding is invalid")
        index = command.index("-C")
        if index + 1 >= len(command) or command[index + 1] != str(
            prepared.provider_cwd
        ):
            raise ValueError("Provider writer cwd binding is invalid")


def _append_directional_launch_terminal(
    path: Path,
    session_id: str,
    *,
    returncode: int | None,
    failure: str | None,
    stdout: str,
    stderr: str,
) -> None:
    descriptor = os.open(path, os.O_RDWR | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0))
    try:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        rows = _ledger_rows_from_descriptor(descriptor)
        started = {
            str(row["session_id"])
            for row in rows[1:]
            if row.get("kind") == "launch-started"
        }
        terminal = {
            str(row["session_id"])
            for row in rows[1:]
            if row.get("kind") in {"launch-completed", "launch-failed"}
        }
        if session_id not in started or session_id in terminal:
            raise ValueError("Provider launch terminal state is invalid")
        kind = (
            "launch-completed"
            if returncode == 0 and failure is None
            else "launch-failed"
        )
        event = {
            "kind": kind,
            "session_id": session_id,
            "returncode": returncode,
            "failure": failure,
            "stdout_sha256": sha256(stdout.encode()).hexdigest(),
            "stderr_sha256": sha256(stderr.encode()).hexdigest(),
            "provider_launched": True,
        }
        _write_all(descriptor, _canonical_bytes(event) + b"\n")
        os.fsync(descriptor)
    finally:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def launch_directional_provider_session(
    path: Path,
    manifest: DirectionalManifest,
    session_id: str,
    prepared: PreparedArm,
    profile: ProviderIsolationProfile,
    argv: Sequence[str],
) -> subprocess.CompletedProcess[str]:
    """The only cap-gated directional Provider launch path."""
    descriptor = os.open(path, os.O_RDWR | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0))
    try:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        rows = _ledger_rows_from_descriptor(descriptor)
        header = rows[0]
        order = header["execution_order"]
        expected_order = [item.session_id for item in manifest.sessions]
        if (
            header["manifest_sha256"] != manifest.canonical_sha256
            or order != expected_order
        ):
            raise ValueError("Provider ledger manifest binding changed")
        reservations = [
            str(row["session_id"])
            for row in rows[1:]
            if row.get("kind") == "reservation"
        ]
        started = {
            str(row["session_id"])
            for row in rows[1:]
            if row.get("kind") == "launch-started"
        }
        terminal = {
            str(row["session_id"])
            for row in rows[1:]
            if row.get("kind") in {"launch-completed", "launch-failed"}
        }
        if not isinstance(order, list) or session_id not in order:
            if len(reservations) >= 19:
                raise ValueError("Provider session cap rejects attempt 20")
            raise ValueError("Provider session is not predeclared")
        if any(item not in started for item in reservations):
            raise ValueError("reservation-only ledger cannot launch a Provider")
        if started != terminal:
            raise ValueError("prior Provider launch is not terminal")
        if session_id in reservations:
            raise ValueError("Provider session was already launched")
        ordinal = len(reservations) + 1
        if ordinal > 19:
            raise ValueError("Provider session cap rejects attempt 20")
        if order[ordinal - 1] != session_id:
            raise ValueError("Provider session launch order changed")
        _validate_directional_provider_launch(
            manifest, session_id, prepared, profile, argv
        )
        command_sha256 = _provider_command_digest(argv)
        reservation = {
            "kind": "reservation",
            "ordinal": ordinal,
            "session_id": session_id,
            "provider_launched": False,
        }
        started_event = {
            "kind": "launch-started",
            "ordinal": ordinal,
            "session_id": session_id,
            "command_sha256": command_sha256,
            "provider_launched": True,
        }
        _write_all(
            descriptor,
            _canonical_bytes(reservation)
            + b"\n"
            + _canonical_bytes(started_event)
            + b"\n",
        )
        os.fsync(descriptor)
    finally:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    try:
        result = run_provider_isolated(profile, argv)
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout if isinstance(error.stdout, str) else ""
        stderr = error.stderr if isinstance(error.stderr, str) else ""
        _append_directional_launch_terminal(
            path,
            session_id,
            returncode=None,
            failure="timeout",
            stdout=stdout,
            stderr=stderr,
        )
        raise
    except OSError:
        _append_directional_launch_terminal(
            path,
            session_id,
            returncode=None,
            failure="launch-error",
            stdout="",
            stderr="",
        )
        raise
    _append_directional_launch_terminal(
        path,
        session_id,
        returncode=result.returncode,
        failure=None if result.returncode == 0 else "nonzero",
        stdout=result.stdout,
        stderr=result.stderr,
    )
    return result


def append_writer_resume_event(
    path: Path, manifest: DirectionalManifest, run_id: str
) -> None:
    run = next((item for item in manifest.runs if item.run_id == run_id), None)
    if run is None or run.arm_id != "A11":
        raise ValueError("writer resume is only valid for a frozen A11 run")
    writer = next(
        item
        for item in manifest.sessions
        if item.run_id == run_id and item.kind == "writer"
    )
    required_experts = {
        item.session_id
        for item in manifest.sessions
        if item.run_id == run_id and item.kind != "writer"
    }
    descriptor = os.open(path, os.O_RDWR | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0))
    try:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        rows = _ledger_rows_from_descriptor(descriptor)
        findings = {
            str(row["session_id"])
            for row in rows[1:]
            if row.get("kind") == "expert-finding"
        }
        if not required_experts <= findings:
            raise ValueError("writer resume requires every predeclared expert finding")
        if any(
            row.get("kind") == "writer-resume" and row.get("run_id") == run_id
            for row in rows[1:]
        ):
            raise ValueError("writer resume is duplicated")
        finding_digest = sha256(
            _canonical_bytes(
                {
                    "run_id": run_id,
                    "expert_sessions": sorted(required_experts),
                    "format": "findings-only",
                }
            )
        ).hexdigest()
        event = {
            "kind": "writer-resume",
            "run_id": run_id,
            "writer_session_id": writer.session_id,
            "same_live_session": True,
            "new_provider_session": False,
            "finding_digest": finding_digest,
        }
        _write_all(descriptor, _canonical_bytes(event) + b"\n")
        os.fsync(descriptor)
    finally:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def append_fake_expert_finding(
    path: Path,
    manifest: DirectionalManifest,
    session_id: str,
    *,
    candidate_writes: int = 0,
    child_subprocesses: int = 0,
    retry: bool = False,
) -> None:
    if retry:
        raise ValueError("expert retry is forbidden")
    if candidate_writes != 0:
        raise ValueError("expert candidate write is forbidden")
    if child_subprocesses != 0:
        raise ValueError("expert subprocess or subagent is forbidden")
    session = next(
        (item for item in manifest.sessions if item.session_id == session_id), None
    )
    if session is None or session.kind not in {
        "primary_expert",
        "cross_risk_expert",
    }:
        raise ValueError("expert finding session is not predeclared")
    descriptor = os.open(path, os.O_RDWR | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0))
    try:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        rows = _ledger_rows_from_descriptor(descriptor)
        if not any(
            row.get("kind") == "reservation" and row.get("session_id") == session_id
            for row in rows[1:]
        ):
            raise ValueError("expert finding requires its reservation")
        if any(
            row.get("kind") == "expert-finding" and row.get("session_id") == session_id
            for row in rows[1:]
        ):
            raise ValueError("expert finding is duplicated")
        snapshot = sha256(f"snapshot:{session.run_id}".encode()).hexdigest()
        finding = sha256(
            _canonical_bytes(
                {
                    "session_id": session.session_id,
                    "snapshot_sha256": snapshot,
                    "role": session.role,
                    "format": "findings-only",
                }
            )
        ).hexdigest()
        event = {
            "kind": "expert-finding",
            "session_id": session.session_id,
            "run_id": session.run_id,
            "role": session.role,
            "snapshot_sha256": snapshot,
            "finding_sha256": finding,
            "read_only": True,
            "findings_only": True,
            "candidate_writes": 0,
            "child_subprocesses": 0,
            "retry": False,
            "provider_launched": False,
        }
        _write_all(descriptor, _canonical_bytes(event) + b"\n")
        os.fsync(descriptor)
    finally:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def read_attempt_ledger(path: Path) -> tuple[Mapping[str, Any], ...]:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_SH)
        return tuple(_ledger_rows_from_descriptor(descriptor))
    finally:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def validate_directional_metric(value: object) -> DirectionalMetric:
    raw = _closed(value, _METRIC_KEYS, "directional metric")
    if (
        raw["schema"] != "ai-sdlc-v2-directional-metric/v1"
        or not isinstance(raw["run_id"], str)
        or not _RUN_ID.fullmatch(raw["run_id"])
    ):
        raise ValueError("directional metric identity is invalid")
    delivered = raw["external_verified_delivery"]
    coverage = raw["weighted_acceptance_coverage"]
    defects = raw["severe_defect_escape_count"]
    wall = raw["wall_time_seconds"]
    sessions = raw["provider_sessions"]
    if not isinstance(delivered, bool):
        raise ValueError("delivery metric must be boolean")
    if (
        isinstance(coverage, bool)
        or not isinstance(coverage, (int, float))
        or not (0 <= coverage <= 1)
    ):
        raise ValueError("acceptance coverage is outside [0,1]")
    if isinstance(defects, bool) or not isinstance(defects, int) or defects < 0:
        raise ValueError("severe defect count is invalid")
    if isinstance(wall, bool) or not isinstance(wall, (int, float)) or wall < 0:
        raise ValueError("wall time is invalid")
    if isinstance(sessions, bool) or not isinstance(sessions, int) or sessions < 1:
        raise ValueError("provider session count is invalid")
    usage: dict[str, int | None] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        item = raw[key]
        if item is not None and (
            isinstance(item, bool) or not isinstance(item, int) or item < 0
        ):
            raise ValueError("authoritative token usage is invalid")
        usage[key] = item
    currency = raw["currency_cost"]
    if currency is not None and (
        isinstance(currency, bool)
        or not isinstance(currency, (int, float))
        or currency < 0
    ):
        raise ValueError("authoritative currency cost is invalid")
    if (
        usage["total_tokens"] is not None
        and usage["input_tokens"] is not None
        and usage["output_tokens"] is not None
        and usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]
    ):
        raise ValueError("authoritative token totals do not add up")
    return DirectionalMetric(
        schema=str(raw["schema"]),
        run_id=str(raw["run_id"]),
        external_verified_delivery=delivered,
        weighted_acceptance_coverage=float(coverage),
        severe_defect_escape_count=defects,
        wall_time_seconds=float(wall),
        provider_sessions=sessions,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        total_tokens=usage["total_tokens"],
        currency_cost=float(currency) if currency is not None else None,
    )


def build_fake_receipt(run_id: str, metric: Mapping[str, object]) -> dict[str, object]:
    validated = validate_directional_metric(metric)
    if validated.run_id != run_id:
        raise ValueError("receipt and metric run ids differ")
    return {
        "schema": "ai-sdlc-v2-directional-run-receipt/v1",
        "writer": "directional-runner",
        "run_id": run_id,
        "status": "fake-complete",
        "metric": dict(metric),
        "matrix_action": "continue",
        "winner": None,
    }


def build_terminal_failure_receipt(run_id: str, category: str) -> dict[str, object]:
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("failure receipt run id is invalid")
    semantics = classify_failure(category)
    action = "continue" if semantics.continue_matrix else "abort-incomplete"
    return {
        "schema": "ai-sdlc-v2-directional-run-receipt/v1",
        "writer": "directional-runner",
        "run_id": run_id,
        "status": semantics.matrix_status,
        "metric": None,
        "matrix_action": action,
        "winner": None,
    }


def validate_run_receipt(value: object) -> Mapping[str, Any]:
    raw = _closed(value, _RECEIPT_KEYS, "directional run receipt")
    if (
        raw["schema"] != "ai-sdlc-v2-directional-run-receipt/v1"
        or raw["writer"] != "directional-runner"
        or not isinstance(raw["run_id"], str)
        or not _RUN_ID.fullmatch(raw["run_id"])
        or raw["winner"] is not None
    ):
        raise ValueError("receipt must be runner-owned and opaque")
    status = raw["status"]
    if status == "fake-complete":
        metric = validate_directional_metric(raw["metric"])
        if metric.run_id != raw["run_id"] or raw["matrix_action"] != "continue":
            raise ValueError("complete receipt binding is invalid")
    elif status == "cell-terminal-failure":
        if raw["metric"] is not None or raw["matrix_action"] != "continue":
            raise ValueError("cell failure receipt is invalid")
    elif status in {"matrix-abort-incomplete", "matrix-incomplete"}:
        if raw["metric"] is not None or raw["matrix_action"] != "abort-incomplete":
            raise ValueError("matrix abort receipt is invalid")
    else:
        raise ValueError("receipt status is invalid")
    return raw


def build_blind_evaluator_input(
    run_id: str, candidate_snapshot_sha256: str
) -> Mapping[str, str]:
    if not _RUN_ID.fullmatch(run_id) or not re.fullmatch(
        r"[a-f0-9]{64}", candidate_snapshot_sha256
    ):
        raise ValueError("blind evaluator binding is invalid")
    return {
        "schema": "ai-sdlc-v2-directional-blind-evaluator-input/v1",
        "opaque_run_id": run_id,
        "candidate_snapshot_sha256": candidate_snapshot_sha256,
    }


def validate_blind_evaluator_input(value: object) -> Mapping[str, str]:
    raw = _closed(value, _BLIND_EVALUATOR_KEYS, "blind evaluator input")
    expected = build_blind_evaluator_input(
        str(raw["opaque_run_id"]), str(raw["candidate_snapshot_sha256"])
    )
    if raw != expected:
        raise ValueError("blind evaluator input changed")
    return expected


def _atomic_no_overwrite(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    staging = path.parent / f".{path.name}.{os.getpid()}.staging"
    descriptor = os.open(
        staging,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        _write_all(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.link(staging, path, follow_symlinks=False)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        staging.unlink(missing_ok=True)


def write_run_receipt(output_root: Path, receipt: Mapping[str, object]) -> Path:
    validated = validate_run_receipt(receipt)
    path = output_root / "raw" / f"{validated['run_id']}.json"
    _atomic_no_overwrite(path, _canonical_bytes(validated) + b"\n")
    return path


def build_directional_summary(
    manifest: DirectionalManifest, receipts: Sequence[Mapping[str, object]]
) -> DirectionalSummary:
    if len(receipts) != 15:
        raise ValueError("summary requires all 15 raw receipts")
    by_run: dict[str, Mapping[str, object]] = {}
    for receipt in receipts:
        validated = validate_run_receipt(receipt)
        run_id = str(validated["run_id"])
        if run_id in by_run:
            raise ValueError("summary receipt is duplicated")
        by_run[run_id] = validated
    if set(by_run) != {run.run_id for run in manifest.runs}:
        raise ValueError("summary does not cover the exact 15-run matrix")
    publishable = all(
        by_run[run.run_id]["status"]
        not in {"matrix-abort-incomplete", "matrix-incomplete"}
        for run in manifest.runs
    )
    paired: list[Mapping[str, object]] = []
    comparisons = (
        ("P", "S", "pure-llm-vs-superpowers"),
        ("S", "A11", "superpowers-vs-ai-sdlc"),
        ("A00", "A10", "loop-effect"),
        ("A10", "A11", "expert-effect"),
    )
    for fixture_id in manifest.fixture_ids:
        block = [run for run in manifest.runs if run.fixture_id == fixture_id]
        metrics = {
            run.arm_id: by_run[run.run_id]["metric"]
            for run in block
            if by_run[run.run_id]["metric"] is not None
        }
        deltas = []
        for left, right, label in comparisons:
            left_metric = metrics.get(left)
            right_metric = metrics.get(right)
            if not isinstance(left_metric, Mapping) or not isinstance(
                right_metric, Mapping
            ):
                deltas.append(
                    {
                        "comparison": label,
                        "left": left,
                        "right": right,
                        "status": "unavailable",
                        "delta": None,
                    }
                )
                continue
            token_delta = None
            if (
                left_metric["total_tokens"] is not None
                and right_metric["total_tokens"] is not None
            ):
                token_delta = right_metric["total_tokens"] - left_metric["total_tokens"]
            cost_delta = None
            if (
                left_metric["currency_cost"] is not None
                and right_metric["currency_cost"] is not None
            ):
                cost_delta = (
                    right_metric["currency_cost"] - left_metric["currency_cost"]
                )
            deltas.append(
                {
                    "comparison": label,
                    "left": left,
                    "right": right,
                    "status": "descriptive-only",
                    "delta": {
                        "external_verified_delivery": int(
                            bool(right_metric["external_verified_delivery"])
                        )
                        - int(bool(left_metric["external_verified_delivery"])),
                        "weighted_acceptance_coverage": round(
                            float(right_metric["weighted_acceptance_coverage"])
                            - float(left_metric["weighted_acceptance_coverage"]),
                            6,
                        ),
                        "severe_defect_escape_count": int(
                            right_metric["severe_defect_escape_count"]
                        )
                        - int(left_metric["severe_defect_escape_count"]),
                        "wall_time_seconds": round(
                            float(right_metric["wall_time_seconds"])
                            - float(left_metric["wall_time_seconds"]),
                            6,
                        ),
                        "provider_sessions": int(right_metric["provider_sessions"])
                        - int(left_metric["provider_sessions"]),
                        "total_tokens": token_delta,
                        "currency_cost": cost_delta,
                    },
                }
            )
        paired.append(
            {
                "fixture_id": fixture_id,
                "comparisons": deltas,
                "raw_metrics": metrics,
                "winner": None,
            }
        )
    return DirectionalSummary(
        labels=_LABELS,
        publishable=publishable,
        evaluator_inputs=("opaque_run_id", "candidate_snapshot"),
        main_quality_fields=(
            "external_verified_delivery",
            "weighted_acceptance_coverage",
            "severe_defect_escape_count",
        ),
        raw_receipts=tuple(by_run[run.run_id] for run in manifest.runs),
        paired_deltas=tuple(paired),
    )


def build_directional_preflight(
    manifest: DirectionalManifest, output_root: Path
) -> DirectionalPreflight:
    return DirectionalPreflight(
        schema="ai-sdlc-v2-directional-preflight/v1",
        manifest_sha256=manifest.canonical_sha256,
        execution_order=tuple(item.session_id for item in manifest.sessions),
        output_root=str(output_root.resolve()),
        provider_calls_started=0,
        formal_authority_status="NO-GO",
        evaluator_classification="legacy-directional-evaluator",
        model=manifest.model,
        reasoning_effort=manifest.reasoning_effort,
        writer_sessions=15,
        expert_sessions=4,
        hard_session_cap=19,
        technical_retries=0,
        input_tokens=None,
        output_tokens=None,
        currency_cost=None,
    )


def load_frozen_directional_preflight(
    path: Path = _DIRECTIONAL_ROOT / "preflight" / "preflight.json",
) -> DirectionalPreflight:
    raw = _closed(
        json.loads(path.read_bytes()), _PREFLIGHT_KEYS, "directional preflight"
    )
    manifest = load_directional_manifest()
    expected = build_directional_preflight(
        manifest,
        Path("/private/tmp/ai-sdlc-v2-directional-results/20260820-v1"),
    )
    loaded = DirectionalPreflight(
        schema=str(raw["schema"]),
        manifest_sha256=str(raw["manifest_sha256"]),
        execution_order=tuple(raw["execution_order"]),
        output_root=str(raw["output_root"]),
        provider_calls_started=int(raw["provider_calls_started"]),
        formal_authority_status=str(raw["formal_authority_status"]),
        evaluator_classification=str(raw["evaluator_classification"]),
        model=str(raw["model"]),
        reasoning_effort=str(raw["reasoning_effort"]),
        writer_sessions=int(raw["writer_sessions"]),
        expert_sessions=int(raw["expert_sessions"]),
        hard_session_cap=int(raw["hard_session_cap"]),
        technical_retries=int(raw["technical_retries"]),
        input_tokens=raw["input_tokens"],
        output_tokens=raw["output_tokens"],
        currency_cost=raw["currency_cost"],
    )
    if loaded != expected:
        raise ValueError("frozen directional preflight changed")
    return loaded


def _preflight_payload(preflight: DirectionalPreflight) -> Mapping[str, object]:
    return {
        "schema": preflight.schema,
        "manifest_sha256": preflight.manifest_sha256,
        "execution_order": list(preflight.execution_order),
        "output_root": preflight.output_root,
        "provider_calls_started": preflight.provider_calls_started,
        "formal_authority_status": preflight.formal_authority_status,
        "evaluator_classification": preflight.evaluator_classification,
        "model": preflight.model,
        "reasoning_effort": preflight.reasoning_effort,
        "writer_sessions": preflight.writer_sessions,
        "expert_sessions": preflight.expert_sessions,
        "hard_session_cap": preflight.hard_session_cap,
        "technical_retries": preflight.technical_retries,
        "input_tokens": preflight.input_tokens,
        "output_tokens": preflight.output_tokens,
        "currency_cost": preflight.currency_cost,
    }


def write_preflight_artifacts(
    destination: Path, preflight: DirectionalPreflight
) -> tuple[Path, Path, Path]:
    payload = _preflight_payload(preflight)
    json_path = destination / "preflight.json"
    markdown_path = destination / "preflight.md"
    budget_path = destination / "budget-request.md"
    _atomic_no_overwrite(json_path, _canonical_bytes(payload) + b"\n")
    markdown = (
        "# AI-SDLC 2.0 方向性效益实验预检\n\n"
        "- 状态：仅离线预演；正式 Provider 调用为 0\n"
        "- 定位：directional engineering observation\n"
        "- 权威边界：legacy-directional-evaluator；actual r2 authority = NO-GO\n"
        "- 矩阵：5 arms × 3 tasks × 1 run = 15 writer runs\n"
        "- 会话上限：15 writer + 4 expert = 19；无技术重试、无复审\n"
        "- 统计边界：n=3 per arm；single run per task；not statistically "
        "significant；not production SLA；no generalization\n"
    )
    _atomic_no_overwrite(markdown_path, markdown.encode())
    budget = build_budget_confirmation(preflight)
    budget_markdown = (
        "# 正式运行预算确认\n\n"
        f"- 模型：{budget['model']}\n"
        f"- 推理强度：{budget['reasoning_effort']}\n"
        f"- Writer 会话：{budget['writer_sessions']}\n"
        f"- Expert 会话：{budget['expert_sessions']}\n"
        f"- Provider 会话硬上限：{budget['hard_session_cap']}\n"
        "- 技术重试：0\n"
        "- Token 估算：未知（Provider 权威 usage 尚未产生）\n"
        "- 货币成本估算：未知（不臆造价格）\n"
        "- 授权状态：未请求；确认后才可启动正式调用\n"
    )
    _atomic_no_overwrite(budget_path, budget_markdown.encode())
    return json_path, markdown_path, budget_path


def build_budget_confirmation(preflight: DirectionalPreflight) -> dict[str, object]:
    if preflight.provider_calls_started != 0:
        raise ValueError("budget request must precede every Provider launch")
    return {
        "model": preflight.model,
        "reasoning_effort": preflight.reasoning_effort,
        "writer_sessions": preflight.writer_sessions,
        "expert_sessions": preflight.expert_sessions,
        "hard_session_cap": preflight.hard_session_cap,
        "technical_retries": preflight.technical_retries,
        "token_estimate": None,
        "currency_cost_estimate": None,
        "authorization_requested": False,
    }


def load_presentation_contract(
    path: Path = _DIRECTIONAL_ROOT / "presentation-contract.json",
) -> Mapping[str, Any]:
    raw = _closed(
        json.loads(path.read_bytes()), _PRESENTATION_KEYS, "presentation contract"
    )
    expected = {
        "schema": "ai-sdlc-v2-directional-presentation/v1",
        "homepage_tracks": ["P", "S", "A11"],
        "research_controls": ["A00", "A10"],
        "show_raw_paired_values": True,
        "show_losses": True,
        "quality_cost_frontier": True,
        "winner_cherry_pick": False,
        "required_labels": list(_LABELS),
    }
    if raw != expected:
        raise ValueError("presentation contract changed")
    return raw


def load_website_data_template(
    path: Path = _DIRECTIONAL_ROOT / "website-data.template.json",
) -> Mapping[str, Any]:
    raw = _closed(json.loads(path.read_bytes()), _WEBSITE_DATA_KEYS, "website data")
    if (
        raw["schema"] != "ai-sdlc-v2-directional-website-data/v1"
        or raw["status"] != "awaiting-real-complete-15"
        or raw["homepage_tracks"] != ["P", "S", "A11"]
        or raw["research_controls"] != ["A00", "A10"]
        or raw["raw_paired_values"] != []
        or raw["losses"] != []
        or raw["quality_cost_frontier"] != []
        or raw["winner"] is not None
        or raw["labels"] != list(_LABELS)
    ):
        raise ValueError("website data template changed")
    return raw


def directional_protected_roots(base: Path | None = None) -> tuple[ProtectedRoot, ...]:
    if base is not None:
        paths = {
            "sealed-r1": base / "sealed-r1",
            "sealed-r2": base / "sealed-r2",
            "sealed-r3": base / "sealed-r3",
            "source": base / "source",
            "rubric": base / "rubric",
            "results": base / "results",
            "control": base / "control",
            "home-codex": base / "home" / ".codex",
            "audit-wip": base / "audit-wip",
            "common-git": base / "common-git",
            "worktree-parent": base / "worktrees",
        }
    else:
        common_git = Path(
            subprocess.run(
                ["git", "rev-parse", "--git-common-dir"],
                cwd=_REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        if not common_git.is_absolute():
            common_git = (_REPO_ROOT / common_git).resolve()
        paths = {
            "sealed-r1": _SEALED_R1_ROOT,
            "sealed-r2": _SEALED_R2_ROOT,
            "sealed-r3": _SEALED_R3_ROOT,
            "source": _SOURCE_BASE,
            "rubric": _FORMAL_ROOT / "fixtures" / "sealed-commitments.json",
            "results": _FORMAL_ROOT / "results",
            "control": _REPO_ROOT,
            "home-codex": Path(pwd.getpwuid(os.getuid()).pw_dir) / ".codex",
            "audit-wip": common_git / "refs" / "heads" / "codex" / "benefit-audit-wip",
            "common-git": common_git,
            "worktree-parent": _REPO_ROOT.parent,
        }
    return tuple(ProtectedRoot(label, path) for label, path in paths.items())


def _directional_provider_launch_paths(prepared: PreparedArm) -> tuple[Path, ...]:
    names = (
        "codex",
        "claude",
        "cursor-agent",
        "copilot",
        "gemini",
        "aider",
        "opencode",
        "goose",
        "amp",
    )
    candidates = [
        Path(prepared.codex.executable),
        Path(prepared.codex.resolved_executable),
    ]
    for name in names:
        located = shutil.which(name, path=prepared.environment.environment["PATH"])
        if located is not None:
            candidates.append(Path(located))
    paths: list[Path] = []
    for candidate in candidates:
        absolute = Path(os.path.abspath(candidate))
        if not absolute.exists() or absolute.is_dir():
            raise ValueError("directional Provider executable identity is unavailable")
        paths.extend((absolute, absolute.resolve(strict=True)))
    return tuple(dict.fromkeys(paths))


def build_directional_provider_profile(
    prepared: PreparedArm,
    *,
    output_root: Path,
    other_run_roots: Sequence[Path] = (),
    argv: Sequence[str] = ("/usr/bin/true",),
) -> ProviderIsolationProfile:
    """Bind one prepared cell to the exact directional deny-read/write surface."""
    verify_prepared_directional_arm(prepared)
    if not output_root.is_dir() or output_root.is_symlink():
        raise ValueError("directional output root is unavailable")
    roots = {item.label: item.path for item in directional_protected_roots()}
    required = {
        "sealed-r1",
        "sealed-r2",
        "sealed-r3",
        "source",
        "rubric",
        "results",
        "control",
        "home-codex",
        "audit-wip",
        "common-git",
        "worktree-parent",
    }
    if set(roots) != required:
        raise ValueError("directional protected surface is not closed")
    # r3 and the formal results path are deliberately absent before execution.
    # Their existing canonical parents are denied so neither can be discovered or
    # created by a Provider.  Exact absent paths are separately write-denied.
    existing_protected = (
        roots["sealed-r1"],
        roots["sealed-r3"].parent,
        roots["source"],
        roots["rubric"],
        roots["home-codex"],
        roots["audit-wip"],
        roots["common-git"],
        roots["worktree-parent"],
        prepared.root / ".git",
    )
    unavailable = [
        path for path in existing_protected if not path.exists() or path.is_symlink()
    ]
    if unavailable:
        raise ValueError(
            "directional protected surface identity is unavailable: "
            + ",".join(str(path) for path in unavailable)
        )
    missing_method_paths: list[Path] = []
    if prepared.arm_id in {"P", "S"}:
        for base in (prepared.root, prepared.provider_cwd):
            for relative in (
                ".ai-sdlc",
                ".agents",
                ".codex",
                "AGENTS.md",
                "AGENTS.override.md",
            ):
                candidate = base / relative
                if not candidate.exists():
                    missing_method_paths.append(candidate)

    def launch_guard() -> None:
        verify_prepared_directional_arm(prepared)
        if (
            prepared.environment.environment_sha256
            != sha256(
                _canonical_bytes(dict(prepared.environment.environment))
            ).hexdigest()
        ):
            raise ValueError("directional launch environment changed")

    write_protected = (
        *prepared.method_instruction_paths,
        *prepared.method_instruction_roots,
    )
    if prepared.shared_runtime_root is not None:
        write_protected = (*write_protected, prepared.shared_runtime_root)
    profile = build_provider_isolation_profile(
        run_root=prepared.provider_cwd,
        sealed_root=roots["sealed-r2"],
        control_root=roots["control"],
        raw_results_root=output_root,
        protected_roots=existing_protected,
        write_protected_roots=tuple(dict.fromkeys(write_protected)),
        missing_write_protected_paths=tuple(dict.fromkeys(missing_method_paths)),
        missing_protected_paths=(roots["sealed-r3"], roots["results"]),
        deny_process_exec_paths=_directional_provider_launch_paths(prepared),
        deny_network=True,
        other_run_roots=other_run_roots,
        argv=argv,
        environment=prepared.environment.environment,
        preserve_environment=True,
        launch_guard=launch_guard,
    )
    if profile.issues:
        joined = ",".join(issue.code for issue in profile.issues)
        raise ValueError(f"directional isolation profile is invalid: {joined}")
    return profile


def _representative_regular_file(root: Path) -> Path:
    info = root.lstat()
    if stat.S_ISREG(info.st_mode):
        return root
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ValueError("protected root has no safe representative")
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        entry = path.lstat()
        if stat.S_ISREG(entry.st_mode) and entry.st_nlink == 1:
            return path
    raise ValueError("protected root has no regular-file representative")


def _launch_denied(
    profile: ProviderIsolationProfile,
    argv: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
) -> bool:
    result = run_provider_isolated(profile, argv, environment=environment)
    return (
        result.returncode != 0
        and "Operation not permitted" in result.stderr
        and "ISOLATION_REFUSED" not in result.stderr
    )


def _launch_allowed(profile: ProviderIsolationProfile, argv: Sequence[str]) -> bool:
    result = run_provider_isolated(profile, argv)
    return (
        result.returncode == 0
        and "ISOLATION_REFUSED" not in result.stderr
        and "Operation not permitted" not in result.stderr
    )


def _add_dir_preflight_rejected(profile: ProviderIsolationProfile) -> bool:
    for argv in (
        ("/usr/bin/true", "--add-dir"),
        ("/usr/bin/true", "--add-dir=../protected"),
    ):
        refreshed = build_provider_isolation_profile(
            run_root=profile.run_root,
            sealed_root=profile.sealed_root,
            control_root=profile.control_root,
            raw_results_root=profile.raw_results_root,
            protected_roots=profile.protected_roots,
            write_protected_roots=profile.write_protected_roots,
            missing_write_protected_paths=profile.missing_write_protected_paths,
            missing_protected_paths=profile.missing_protected_paths,
            deny_process_exec_paths=profile.deny_process_exec_paths,
            deny_network=profile.deny_network,
            other_run_roots=profile.other_run_roots,
            argv=argv,
            environment=profile.environment,
            preserve_environment=profile.preserve_environment,
            launch_guard=profile.launch_guard,
        )
        if {issue.code for issue in refreshed.issues} != {"isolation.add-dir"}:
            return False
    return True


def run_directional_system_isolation_canary(
    prepared: PreparedArm, profile: ProviderIsolationProfile
) -> DirectionalIsolationCanary:
    """Exercise the exact final profile with safe local commands, never Provider."""
    if sys.platform != "darwin" or not profile.executable:
        raise RuntimeError("macOS Seatbelt is required")
    verify_prepared_directional_arm(prepared)
    roots = {item.label: item.path for item in directional_protected_roots()}
    candidate_input = _representative_regular_file(prepared.provider_cwd)
    baseline_exec_allowed = _launch_allowed(profile, ["/usr/bin/true"])
    candidate_input_read_allowed = _launch_allowed(
        profile, ["/bin/cat", str(candidate_input)]
    )
    representatives = (
        _representative_regular_file(roots["sealed-r1"]),
        _representative_regular_file(roots["sealed-r2"]),
        _representative_regular_file(roots["source"]),
        _representative_regular_file(roots["rubric"]),
        _representative_regular_file(roots["control"]),
        _representative_regular_file(roots["home-codex"]),
        _representative_regular_file(roots["audit-wip"]),
        _representative_regular_file(roots["common-git"]),
        _representative_regular_file(roots["worktree-parent"]),
        _representative_regular_file(prepared.root / ".git"),
    )
    direct_reads = all(
        _launch_denied(profile, ["/bin/cat", str(path)]) for path in representatives
    )
    directory_lists = all(
        _launch_denied(profile, ["/bin/ls", str(path)])
        for path in (
            roots["sealed-r1"],
            roots["sealed-r2"],
            roots["source"],
            roots["control"],
            roots["home-codex"],
        )
    )
    parent_escape = _launch_denied(
        profile, ["/bin/cat", str(prepared.provider_cwd / ".." / ".git" / "HEAD")]
    )
    environment = _launch_denied(
        profile,
        [
            "/usr/bin/env",
            f"DIRECTIONAL_FORBIDDEN={representatives[0]}",
            "/bin/sh",
            "-c",
            'cat "$DIRECTIONAL_FORBIDDEN"',
        ],
    )
    add_dir = _add_dir_preflight_rejected(profile)
    nested_provider_denied = all(
        _launch_denied(profile, argv)
        for argv in (
            [prepared.codex.executable, "--version"],
            [
                "/bin/sh",
                "-c",
                'exec "$1" --version',
                "nested-provider",
                prepared.codex.executable,
            ],
        )
    )
    output_canary = profile.raw_results_root / ".directional-output-canary"
    created = profile.raw_results_root / ".directional-created-canary"
    renamed = profile.raw_results_root / ".directional-renamed-canary"
    if output_canary.exists() or created.exists() or renamed.exists():
        raise ValueError("directional system canary residue already exists")
    output_canary.write_bytes(b"frozen-directional-canary\n")
    output_canary.chmod(0o600)
    before_bytes = output_canary.read_bytes()
    before_mode = stat.S_IMODE(output_canary.lstat().st_mode)
    method = prepared.method_instruction_paths[0]
    method_mode = stat.S_IMODE(method.lstat().st_mode)
    append_denied = create_denied = rename_denied = chmod_denied = False
    try:
        append_denied = (
            _launch_denied(
                profile,
                [
                    "/bin/sh",
                    "-c",
                    'printf "mutation" >> "$1"',
                    "directional-canary",
                    str(output_canary),
                ],
            )
            and output_canary.read_bytes() == before_bytes
        )
        create_denied = (
            _launch_denied(profile, ["/usr/bin/touch", str(created)])
            and not created.exists()
        )
        rename_denied = (
            _launch_denied(profile, ["/bin/mv", str(output_canary), str(renamed)])
            and output_canary.exists()
            and not renamed.exists()
        )
        chmod_denied = (
            _launch_denied(profile, ["/bin/chmod", "600", str(method)])
            and stat.S_IMODE(method.lstat().st_mode) == method_mode
        )
    finally:
        # These are controller-owned canaries.  Clean only exact known paths and
        # restore their bytes/mode if a permissive profile allowed an operation.
        if renamed.exists() and not output_canary.exists():
            renamed.replace(output_canary)
        if output_canary.exists():
            output_canary.write_bytes(before_bytes)
            output_canary.chmod(before_mode)
            output_canary.unlink()
        created.unlink(missing_ok=True)
        renamed.unlink(missing_ok=True)
        if method.exists() and stat.S_IMODE(method.lstat().st_mode) != method_mode:
            method.chmod(method_mode)
    residue_free = not any(path.exists() for path in (output_canary, created, renamed))
    verify_prepared_directional_arm(prepared)
    return DirectionalIsolationCanary(
        baseline_exec_allowed=baseline_exec_allowed,
        candidate_input_read_allowed=candidate_input_read_allowed,
        direct_reads_denied=direct_reads,
        directory_lists_denied=directory_lists,
        parent_escape_denied=parent_escape,
        environment_leak_denied=environment,
        add_dir_denied=add_dir,
        output_append_denied=append_denied,
        output_create_denied=create_denied,
        output_rename_denied=rename_denied,
        method_chmod_denied=chmod_denied,
        nested_provider_denied=nested_provider_denied,
        residue_free=residue_free,
    )


def create_clean_directional_environment(root: Path) -> Mapping[str, str]:
    if root.exists():
        raise ValueError("directional environment root must be absent")
    root.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    home = root / "home"
    codex_home = root / "codex-home"
    home.mkdir(mode=0o700)
    codex_home.mkdir(mode=0o700)
    safe_path = ":".join(
        (
            "/opt/homebrew/bin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
        )
    )
    return {
        "PATH": safe_path,
        "HOME": str(home),
        "CODEX_HOME": str(codex_home),
        "LC_ALL": "C",
        "LANG": "C",
        "TZ": "UTC",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PIP_NO_INDEX": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "NO_PROXY": "*",
        "no_proxy": "*",
        "AI_SDLC_DIRECTIONAL_PROVIDER": "forbidden-preflight",
    }


def freeze_global_inventory(environment: Mapping[str, str]) -> str:
    if set(environment) != {
        "PATH",
        "HOME",
        "CODEX_HOME",
        "LC_ALL",
        "LANG",
        "TZ",
        "PYTHONDONTWRITEBYTECODE",
        "PIP_NO_INDEX",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_TERMINAL_PROMPT",
        "NO_PROXY",
        "no_proxy",
        "AI_SDLC_DIRECTIONAL_PROVIDER",
    }:
        raise ValueError("global inventory environment is not closed")
    for key in ("HOME", "CODEX_HOME"):
        path = Path(environment[key])
        if stat.S_IMODE(path.lstat().st_mode) != 0o700 or not path.is_dir():
            raise ValueError("global inventory home is not private")
    if ".venv" in environment["PATH"]:
        raise ValueError("global inventory path contains a workspace runtime")
    payload = {
        "schema": "ai-sdlc-v2-directional-global-inventory/v1",
        "environment": dict(environment),
        "plugins": [],
        "apps": [],
        "mcp_servers": [],
        "global_rules": [],
    }
    return sha256(_canonical_bytes(payload)).hexdigest()


def verify_global_inventory(environment: Mapping[str, str], expected: str) -> None:
    try:
        actual = freeze_global_inventory(environment)
    except (OSError, ValueError) as error:
        raise ValueError("global inventory changed") from error
    if actual != expected:
        raise ValueError("global inventory changed")


def validate_rehearsal_workspace(root: Path) -> None:
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("rehearsal root must be a private directory")
    if not (root / ".git").is_dir() or (root / ".git").is_symlink():
        raise ValueError("rehearsal root must have an internal Git directory")
    task = root / "benchmark-task"
    if not task.is_dir() or task.is_symlink():
        raise ValueError("rehearsal provider cwd is invalid")
    for forbidden in ("AGENTS.md", ".codex"):
        if (root / forbidden).exists() or (root / forbidden).is_symlink():
            raise ValueError("rehearsal root has injected global instructions")
    for path in root.rglob("*"):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode):
            raise ValueError("rehearsal root has an unsafe entry")
        if stat.S_ISREG(mode) and path.stat().st_nlink != 1:
            raise ValueError("rehearsal root has a hardlinked file")


def verify_prepared_directional_arm(prepared: PreparedArm) -> None:
    verify_prepared_arm_identity(prepared)
    if verify_method_instruction_immutability(prepared):
        raise ValueError("directional method instructions changed")
    if prepared.provider_cwd != prepared.root / "benchmark-task":
        raise ValueError("directional provider cwd changed")
    try:
        if not Path(prepared.subprocess_cwd).samefile(prepared.provider_cwd):
            raise ValueError("directional subprocess cwd changed")
    except OSError as error:
        raise ValueError("directional subprocess cwd changed") from error
    if prepared.prompt_sha256 != sha256(prepared.prompt.encode()).hexdigest():
        raise ValueError("directional prompt binding changed")
    if prepared.environment.provider_attempts_started != 0:
        raise ValueError("directional preparation started a Provider")
    if (
        prepared.environment.environment_sha256
        != sha256(_canonical_bytes(dict(prepared.environment.environment))).hexdigest()
    ):
        raise ValueError("directional environment binding changed")
    for key in ("HOME", "CODEX_HOME"):
        root = Path(prepared.environment.environment[key])
        if not root.is_dir() or stat.S_IMODE(root.lstat().st_mode) != 0o700:
            raise ValueError("directional environment home changed")
    codex_home = Path(prepared.environment.environment["CODEX_HOME"])
    for relative in (
        "config.toml",
        "AGENTS.md",
        "plugins",
        "memories",
        "apps",
        "mcp.json",
    ):
        if (codex_home / relative).exists() or (codex_home / relative).is_symlink():
            raise ValueError(
                f"directional global inventory is contaminated: {relative}"
            )
    skills = codex_home / "skills"
    if skills.exists() and (
        skills.is_symlink()
        or {path.name for path in skills.iterdir()} != {".system"}
        or (skills / ".system").is_symlink()
    ):
        raise ValueError("directional global inventory is contaminated: skills")
    status_result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=prepared.root,
        env={**prepared.environment.environment},
        check=True,
        capture_output=True,
        text=True,
    )
    if status_result.stdout:
        raise ValueError("directional prepared Git workspace is dirty")
    if stat.S_IMODE(prepared.root.lstat().st_mode) != 0o700:
        raise ValueError("directional prepared workspace is not private")


def _make_skeleton_workspace(root: Path) -> None:
    root.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    (root / "benchmark-task").mkdir(mode=0o700)
    (root / ".git").mkdir(mode=0o700)


def run_fake_rehearsal(
    manifest: DirectionalManifest,
    *,
    workspace_root: Path,
    output_root: Path,
    materialize_arms: bool = True,
) -> FakeRehearsalResult:
    """Prepare all cells and simulate the exact session schedule without Provider use."""
    if workspace_root.exists() or output_root.exists():
        raise ValueError("rehearsal roots must be fresh")
    workspace_root.mkdir(parents=True, mode=0o700)
    workspace_root.chmod(0o700)
    output_root.mkdir(parents=True, mode=0o700)
    output_root.chmod(0o700)
    prepared: list[PreparedArm] = []
    shared_runtime = workspace_root / ".shared-ai-sdlc-runtime"
    for run in manifest.runs:
        run_root = workspace_root / run.run_id
        if materialize_arms:
            fixture_root = workspace_root / f".fixture-{run.run_id}"
            fixture: PreparedFixture = prepare_fixture(run.fixture_id, fixture_root)
            arm = prepare_arm(
                run.arm_id,
                fixture,
                run_root,
                shared_runtime_root=shared_runtime,
                environment_root=workspace_root / f".environment-{run.run_id}",
            )
            run_root.chmod(0o700)
            prepared.append(arm)
            verify_prepared_directional_arm(arm)
        else:
            _make_skeleton_workspace(run_root)
            validate_rehearsal_workspace(run_root)
    ledger = initialize_attempt_ledger(output_root / "attempts.jsonl", manifest)
    for session in manifest.sessions:
        reserve_session(ledger, session.session_id)
    for session in manifest.sessions:
        if session.kind != "writer":
            append_fake_expert_finding(ledger, manifest, session.session_id)
    for run in manifest.runs:
        if run.arm_id == "A11":
            append_writer_resume_event(ledger, manifest, run.run_id)
    return FakeRehearsalResult(
        prepared_workspaces=15,
        simulated_sessions=19,
        external_provider_calls=0,
        input_tokens=None,
        output_tokens=None,
        currency_cost=None,
        ledger_path=ledger,
        prepared_arms=tuple(prepared),
    )
