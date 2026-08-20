"""Trusted compiler and exclusive publisher for the v2 benefit evaluator bundle."""

from __future__ import annotations

import ctypes
import errno
import json
import os
import secrets
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType

try:
    import fcntl
except ImportError:  # pragma: no cover - 正式物化器仅在 macOS 上执行。
    fcntl = None  # type: ignore[assignment]

from ai_sdlc.benefit_benchmark_fixtures import (
    FIXTURE_IDS,
    FrozenIntentApprovalService,
    ProviderIsolationProfile,
    build_provider_isolation_profile,
    derive_repo_git_surfaces,
    evaluate_fixture,
    load_fixture_manifest,
    prepare_fixture,
    probe_provider_isolation,
    scan_candidate_for_sealed_leak,
    validate_fixture_manifest,
    validate_frontend_browser_program,
)

FINAL_LOCK_ID = "v2-benefits-20260819-r1"
FINAL_TARGET = Path("/private/tmp/ai-sdlc-v2-benefit-evaluator/v2-benefits-20260819-r1")
LEGACY_ROOT = Path("/private/tmp/ai-sdlc-v2-benefit-evaluator/v2-benefits-20260819")
TRUST_ANCHOR = Path("/private/tmp")
TRUSTED_SOURCE_BASE = Path("/private/tmp/ai-sdlc-v2-benefit-source")
TRUSTED_SOURCE_ROOT = TRUSTED_SOURCE_BASE / "sealed-source"
FINAL_CANARY_BASE = Path("/private/tmp/ai-sdlc-v2-benefit-isolation-canary")
EXPECTED_LEGACY_INODE = 400173643

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BENCHMARK_RELATIVE = Path("benchmarks/ai-sdlc-v2-benefits")
_PENDING_FIELDS = (
    "fixture_tree_sha256",
    "fixture_commitment",
    "evidence_contract_sha256",
    "evidence_contract_commitment",
)
_PROVIDER_STATE_RELATIVE = (
    Path("results"),
    Path("runs"),
    Path("raw-results"),
    Path("provider-attempt-ledger.json"),
    Path("results/provider-attempt-ledger.json"),
)
_DIGEST_LENGTH = 64
_SOURCE_KEYS = {"schema", "lock_id", "intent_map", "payloads"}
_INTENT_KEYS = {"schema", "questions", "approvals"}
_QUESTION_KEYS = {"answer", "delay_ms"}
_SECURITY_SCENARIO_REQUIRED = {
    "actor_id",
    "actor_tenant",
    "roles",
    "request_id",
    "request_tenant",
    "requester_id",
    "expires_at",
    "now",
}
_SECURITY_SCENARIO_OPTIONAL = {"status", "action", "audit_mode"}
_SECURITY_EXPECTED_KEYS = {
    "allowed",
    "reason",
    "status",
    "status_unchanged",
    "audit_count",
    "audit_events",
    "error",
}
_SECURITY_ROOT_CAUSES = {
    "tenant-isolation",
    "separation-of-duties",
    "request-lifecycle",
    "role-allowlist",
    "action-allowlist",
    "atomic-audit",
}
_FRONTEND_EXPECTED_KEYS = {
    "executed_with_real_browser",
    "scenarios",
    "console_errors",
    "basic_accessibility",
    "behavior_checks",
}
_OUTPUT_ORDER = (
    "intent-map.json",
    "requirement-contract-ambiguity.sealed.json",
    "frontend-recovery-delivery.sealed.json",
    "multi-tenant-security-review.sealed.json",
    "sealed-manifest.json",
    "candidate-commitments.json",
    "materialization-receipt.json",
)
_ATTESTATION_NAME = "isolation-attestation.json"
CANDIDATE_COMMITMENT_KEYS = {
    "schema",
    "lock_id",
    "source_head",
    "source_tree_sha",
    "materializer_sha256",
    "source_bundle_sha256",
    "fixture_manifest_sha256",
    "fixture_tree_sha256",
    "evidence_contract_sha256",
    "sealed_manifest_sha256",
    "intent_map_sha256",
    "fixture_payloads",
    "source_root_tree_sha256",
}
RECEIPT_KEYS = {
    "schema",
    "publication_state",
    "target_lock_id",
    "source_head",
    "source_tree_sha",
    "materializer_sha256",
    "source_bundle_sha256",
    "fixture_manifest_sha256",
    "fixture_tree_sha256",
    "evidence_contract_sha256",
    "sealed_manifest_sha256",
    "intent_map_sha256",
    "fixture_payloads",
    "candidate_commitments_sha256",
    "isolation_probe_state",
    "source_root_tree_sha256",
}


class MaterializationError(RuntimeError):
    """A sanitized, stable NO-GO code safe to expose at the CLI boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class TreeFingerprint:
    inode: int
    sha256: str


@dataclass(frozen=True)
class MaterializerPolicy:
    repo_root: Path
    target: Path
    trust_anchor: Path
    legacy_root: Path
    expected_legacy_inode: int
    forbidden_roots: tuple[Path, ...]
    source_base: Path
    source_root: Path
    canary_run_root: Path
    raw_results_root: Path
    other_run_roots: tuple[Path, ...]


@dataclass(frozen=True)
class RepoBindings:
    source_head: str
    source_tree_sha: str
    materializer_sha256: str
    fixture_manifest_sha256: str
    fixture_tree_sha256: str
    evidence_contract_sha256: str


@dataclass(frozen=True)
class SourceRead:
    canonical_bytes: bytes
    device: int
    inode: int
    source_path: Path | None


@dataclass(frozen=True)
class CompiledMaterialization:
    files: Mapping[str, bytes]
    bindings: RepoBindings
    source_bundle_sha256: str


@dataclass(frozen=True)
class MaterializationResult:
    lock_id: str
    target_inode: int
    file_sha256: Mapping[str, str]


@dataclass(frozen=True)
class FailureInjector:
    fail_at: str | None = None

    def hit(self, point: str) -> None:
        if self.fail_at == point:
            raise MaterializationError("injected-failure")


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _strict_json_loads(value: bytes) -> object:
    def reject_constant(_value: str) -> object:
        raise ValueError("non-finite JSON number")

    return json.loads(value, parse_constant=reject_constant)


def _digest_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _digest_file(path: Path) -> str:
    return _digest_bytes(path.read_bytes())


def materializer_sha256() -> str:
    """Bind the exact compiler/publisher implementation bytes."""
    return _digest_file(Path(__file__).resolve(strict=True))


def _closed(value: object, keys: set[str], code: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise MaterializationError(code)
    return value


def _valid_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _DIGEST_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _read_all(descriptor: int, *, positional: bool) -> bytes:
    chunks: list[bytes] = []
    offset = 0
    while True:
        try:
            chunk = (
                os.pread(descriptor, 1024 * 1024, offset)
                if positional
                else os.read(descriptor, 1024 * 1024)
            )
        except OSError as error:
            raise MaterializationError("source-read") from error
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        offset += len(chunk)


def _validate_source_stat(before: os.stat_result, after: os.stat_result) -> None:
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or stat.S_IMODE(before.st_mode) != 0o600
        or before.st_nlink != 1
    ):
        raise MaterializationError("source-security")
    stable = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_uid,
        before.st_nlink,
        before.st_size,
        before.st_mtime_ns,
    ) == (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_uid,
        after.st_nlink,
        after.st_size,
        after.st_mtime_ns,
    )
    if not stable:
        raise MaterializationError("source-raced")


def _descriptor_path(descriptor: int) -> Path:
    try:
        if sys.platform == "darwin":
            if fcntl is None:
                raise OSError("descriptor path lookup is unavailable")
            raw = fcntl.fcntl(descriptor, 50, b"\0" * 1024)
            value = os.fsdecode(raw.split(b"\0", 1)[0])
        else:
            value = os.readlink(f"/proc/self/fd/{descriptor}")
        if not value:
            raise OSError("descriptor path is empty")
        return Path(value).resolve(strict=True)
    except (OSError, ValueError) as error:
        raise MaterializationError("source-path") from error


def _read_source_record(
    *,
    source_fd: int,
    expected_sha256: str,
) -> SourceRead:
    if not _valid_digest(expected_sha256):
        raise MaterializationError("source-digest")
    opened_path: Path | None = None
    try:
        try:
            descriptor = os.dup(int(source_fd))
        except (OSError, TypeError, ValueError) as error:
            raise MaterializationError("source-open") from error
        try:
            opened_path = _descriptor_path(descriptor)
            before = os.fstat(descriptor)
            data = _read_all(descriptor, positional=True)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except MaterializationError:
        raise
    except OSError as error:
        raise MaterializationError("source-open") from error
    _validate_source_stat(before, after)
    if len(data) != before.st_size:
        raise MaterializationError("source-raced")
    if _digest_bytes(data) != expected_sha256:
        raise MaterializationError("source-digest")
    try:
        parsed = _strict_json_loads(data)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise MaterializationError("source-canonical") from error
    if _canonical_json_bytes(parsed) != data:
        raise MaterializationError("source-canonical")
    return SourceRead(data, before.st_dev, before.st_ino, opened_path)


def read_source_bundle(
    *,
    source_fd: int,
    expected_sha256: str,
) -> bytes:
    """Read and freeze one canonical source bundle without echoing its identity."""
    return _read_source_record(
        source_fd=source_fd,
        expected_sha256=expected_sha256,
    ).canonical_bytes


def fingerprint_tree(root: Path) -> TreeFingerprint:
    """Hash root identity plus sorted child identity and content records."""
    try:
        root_stat = root.lstat()
        if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
            raise MaterializationError("legacy-root")
        entries: list[dict[str, object]] = [
            {
                "path": ".",
                "type": "directory",
                "device": root_stat.st_dev,
                "inode": root_stat.st_ino,
                "uid": root_stat.st_uid,
                "gid": root_stat.st_gid,
                "mode": stat.S_IMODE(root_stat.st_mode),
                "nlink": root_stat.st_nlink,
                "size": root_stat.st_size,
            }
        ]
        for path in sorted(
            root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()
        ):
            relative = path.relative_to(root).as_posix()
            item_stat = path.lstat()
            record: dict[str, object] = {
                "path": relative,
                "device": item_stat.st_dev,
                "inode": item_stat.st_ino,
                "uid": item_stat.st_uid,
                "gid": item_stat.st_gid,
                "mode": stat.S_IMODE(item_stat.st_mode),
                "nlink": item_stat.st_nlink,
                "size": item_stat.st_size,
            }
            if stat.S_ISREG(item_stat.st_mode):
                record.update({"type": "file", "sha256": _digest_file(path)})
            elif stat.S_ISDIR(item_stat.st_mode):
                record.update({"type": "directory"})
            elif stat.S_ISLNK(item_stat.st_mode):
                record.update({"type": "symlink", "target": os.readlink(path)})
            else:
                record.update({"type": "other"})
            entries.append(record)
    except MaterializationError:
        raise
    except OSError as error:
        raise MaterializationError("legacy-root") from error
    return TreeFingerprint(
        root_stat.st_ino, _digest_bytes(_canonical_json_bytes(entries))
    )


def default_policy() -> MaterializerPolicy:
    benchmark = _REPO_ROOT / _BENCHMARK_RELATIVE
    return MaterializerPolicy(
        repo_root=_REPO_ROOT,
        target=FINAL_TARGET,
        trust_anchor=TRUST_ANCHOR,
        legacy_root=LEGACY_ROOT,
        expected_legacy_inode=EXPECTED_LEGACY_INODE,
        forbidden_roots=(
            _REPO_ROOT,
            _REPO_ROOT / ".git",
            benchmark / "results",
            benchmark / "runs",
            benchmark / "raw-results",
            benchmark / ".evaluation-raw-results",
        ),
        source_base=TRUSTED_SOURCE_BASE,
        source_root=TRUSTED_SOURCE_ROOT,
        canary_run_root=FINAL_CANARY_BASE / "run",
        raw_results_root=FINAL_CANARY_BASE / "raw-results",
        other_run_roots=(FINAL_CANARY_BASE / "other-run",),
    )


def _git(repo_root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MaterializationError("source-git") from error
    if completed.returncode != 0:
        raise MaterializationError("source-git")
    return completed.stdout.strip()


def _assert_protocol_pending(repo_root: Path) -> None:
    path = repo_root / _BENCHMARK_RELATIVE / "protocol.json"
    try:
        protocol = json.loads(path.read_text(encoding="utf-8"))
        lock = protocol["execution_lock"]
        if any(lock[field] != "pending-unbound" for field in _PENDING_FIELDS):
            raise MaterializationError("protocol-state")
    except MaterializationError:
        raise
    except (KeyError, OSError, TypeError, json.JSONDecodeError) as error:
        raise MaterializationError("protocol-state") from error


def _assert_provider_state_absent(repo_root: Path) -> None:
    benchmark = repo_root / _BENCHMARK_RELATIVE
    if any((benchmark / relative).exists() for relative in _PROVIDER_STATE_RELATIVE):
        raise MaterializationError("provider-state")


def _capture_repo_bindings(
    expected_head: str, policy: MaterializerPolicy
) -> RepoBindings:
    repo = policy.repo_root.resolve(strict=True)
    if _git(repo, "rev-parse", "HEAD") != expected_head:
        raise MaterializationError("source-head")
    if _git(repo, "status", "--porcelain", "--untracked-files=all"):
        raise MaterializationError("source-tree")
    _assert_protocol_pending(repo)
    _assert_provider_state_absent(repo)
    fixture_root = repo / _BENCHMARK_RELATIVE / "fixtures"
    manifest_path = fixture_root / "manifest.json"
    evidence_path = fixture_root / "evidence-contract.template.json"
    try:
        manifest = load_fixture_manifest(manifest_path)
        if validate_fixture_manifest(manifest, fixture_root):
            raise MaterializationError("fixture-manifest")
        source_tree_sha = _git(repo, "rev-parse", "HEAD^{tree}")
        return RepoBindings(
            source_head=expected_head,
            source_tree_sha=source_tree_sha,
            materializer_sha256=materializer_sha256(),
            fixture_manifest_sha256=_digest_file(manifest_path),
            fixture_tree_sha256=manifest.canonical_sha256,
            evidence_contract_sha256=_digest_file(evidence_path),
        )
    except MaterializationError:
        raise
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise MaterializationError("fixture-manifest") from error


def _assert_repo_unchanged(bindings: RepoBindings, policy: MaterializerPolicy) -> None:
    current = _capture_repo_bindings(bindings.source_head, policy)
    if current != bindings:
        raise MaterializationError("source-tree")


_CRITERION_KEYS: Mapping[str, set[str]] = MappingProxyType(
    {
        "json_literal": {"id", "weight", "severity", "kind", "path", "expected"},
        "json_enum": {"id", "weight", "severity", "kind", "path", "allowed"},
        "json_set_contains": {"id", "weight", "severity", "kind", "path", "expected"},
        "json_relation": {"id", "weight", "severity", "kind", "path", "relation"},
        "json_no_contradiction": {
            "id",
            "weight",
            "severity",
            "kind",
            "path",
            "forbidden",
        },
        "verification_command": {
            "id",
            "weight",
            "severity",
            "kind",
            "path",
            "expected",
        },
        "frontend_browser_suite": {"id", "weight", "severity", "kind", "expected"},
        "security_oracle": {
            "id",
            "weight",
            "severity",
            "kind",
            "path",
            "root_cause",
            "scenario",
            "expected",
        },
    }
)


def _validate_scalar_sequence(value: object) -> None:
    if (
        not isinstance(value, list)
        or not value
        or any(isinstance(item, (list, dict)) for item in value)
        or len({_canonical_json_bytes(item) for item in value}) != len(value)
    ):
        raise MaterializationError("source-schema")


def _require_source_schema(condition: bool) -> None:
    if not condition:
        raise MaterializationError("source-schema")


def _validate_frontend_expected(
    value: object, *, scenario_ids: set[str], behavior_ids: set[str]
) -> None:
    if (
        not isinstance(value, Mapping)
        or not value
        or not set(value).issubset(_FRONTEND_EXPECTED_KEYS)
    ):
        raise MaterializationError("source-schema")
    for key in {"executed_with_real_browser", "basic_accessibility"} & set(value):
        if not isinstance(value[key], bool):
            raise MaterializationError("source-schema")
    if "console_errors" in value and (
        not isinstance(value["console_errors"], list)
        or not all(isinstance(item, str) for item in value["console_errors"])
    ):
        raise MaterializationError("source-schema")
    for key, allowed in (
        ("scenarios", scenario_ids),
        ("behavior_checks", behavior_ids),
    ):
        if key not in value:
            continue
        child = value[key]
        if (
            not isinstance(child, Mapping)
            or not child
            or not set(child).issubset(allowed)
            or not all(isinstance(item, bool) for item in child.values())
        ):
            raise MaterializationError("source-schema")


def _validate_security_oracle(criterion: Mapping[str, object]) -> None:
    scenario = criterion["scenario"]
    expected = criterion["expected"]
    if (
        not isinstance(scenario, Mapping)
        or not _SECURITY_SCENARIO_REQUIRED.issubset(scenario)
        or not set(scenario).issubset(
            _SECURITY_SCENARIO_REQUIRED | _SECURITY_SCENARIO_OPTIONAL
        )
        or not isinstance(scenario["roles"], list)
        or not scenario["roles"]
        or not all(isinstance(role, str) and role for role in scenario["roles"])
    ):
        raise MaterializationError("source-schema")
    string_keys = _SECURITY_SCENARIO_REQUIRED - {"roles"}
    string_keys |= {key for key in _SECURITY_SCENARIO_OPTIONAL if key in scenario}
    if not all(isinstance(scenario[key], str) and scenario[key] for key in string_keys):
        raise MaterializationError("source-schema")
    if "audit_mode" in scenario and scenario["audit_mode"] not in {
        "list",
        "none",
        "failing",
    }:
        raise MaterializationError("source-schema")
    try:
        expires_at = datetime.fromisoformat(str(scenario["expires_at"]))
        now = datetime.fromisoformat(str(scenario["now"]))
    except ValueError as error:
        raise MaterializationError("source-schema") from error
    if expires_at.utcoffset() is None or now.utcoffset() is None:
        raise MaterializationError("source-schema")
    if (
        not isinstance(expected, Mapping)
        or not expected
        or not set(expected).issubset(_SECURITY_EXPECTED_KEYS)
    ):
        raise MaterializationError("source-schema")
    expected_types: Mapping[str, tuple[type, ...]] = {
        "allowed": (bool, type(None)),
        "reason": (str, type(None)),
        "status": (str,),
        "status_unchanged": (bool,),
        "audit_count": (int, type(None)),
        "audit_events": (list,),
        "error": (str, type(None)),
    }
    if any(not isinstance(expected[key], expected_types[key]) for key in expected):
        raise MaterializationError("source-schema")
    initial_status = scenario.get("status", "pending")
    if (
        expected.get("allowed") is not False
        or expected.get("status") != initial_status
        or expected.get("status_unchanged") is not True
    ):
        raise MaterializationError("source-schema")
    root_cause = criterion["root_cause"]
    coherent = {
        "tenant-isolation": scenario["actor_tenant"] != scenario["request_tenant"],
        "separation-of-duties": scenario["actor_id"] == scenario["requester_id"],
        "request-lifecycle": initial_status != "pending" or now >= expires_at,
        "role-allowlist": not set(scenario["roles"]).intersection(
            {"approver", "admin"}
        ),
        "action-allowlist": scenario.get("action", "approve")
        not in {"approve", "reject"},
        "atomic-audit": scenario.get("audit_mode", "list") in {"none", "failing"},
    }
    if root_cause not in coherent or not coherent[root_cause]:
        raise MaterializationError("source-schema")


def _validate_criterion_value(
    criterion: Mapping[str, object], *, scenario_ids: set[str], behavior_ids: set[str]
) -> None:
    kind = str(criterion["kind"])
    if kind == "json_enum":
        _validate_scalar_sequence(criterion["allowed"])
    elif kind == "json_set_contains":
        _validate_scalar_sequence(criterion["expected"])
    elif kind == "json_relation":
        _require_source_schema(
            criterion["relation"]
            in {
                "committed_fact_survives_notification_failure",
                "version_guard_precedes_terminal_transition",
            }
        )
    elif kind == "json_no_contradiction":
        forbidden = criterion["forbidden"]
        _require_source_schema(
            isinstance(forbidden, list)
            and bool(forbidden)
            and all(isinstance(item, str) and item for item in forbidden)
        )
    elif kind == "verification_command":
        commands = criterion["expected"]
        if (
            not isinstance(commands, list)
            or not commands
            or not all(
                isinstance(command, str)
                and command.startswith(("python -m ", "npm run "))
                for command in commands
            )
        ):
            raise MaterializationError("source-schema")
    elif kind == "frontend_browser_suite":
        _validate_frontend_expected(
            criterion["expected"],
            scenario_ids=scenario_ids,
            behavior_ids=behavior_ids,
        )
    elif kind == "security_oracle":
        _validate_security_oracle(criterion)


def _validate_criteria(
    fixture_id: str,
    criteria: object,
    *,
    scenario_ids: set[str] | None = None,
    behavior_ids: set[str] | None = None,
) -> list[Mapping[str, object]]:
    if not isinstance(criteria, list) or not criteria:
        raise MaterializationError("source-schema")
    validated: list[Mapping[str, object]] = []
    identifiers: set[str] = set()
    for raw in criteria:
        if not isinstance(raw, Mapping):
            raise MaterializationError("source-schema")
        kind = raw.get("kind")
        if not isinstance(kind, str) or kind not in _CRITERION_KEYS:
            raise MaterializationError("source-schema")
        criterion = _closed(raw, _CRITERION_KEYS[kind], "source-schema")
        identifier = criterion.get("id")
        weight = criterion.get("weight")
        if (
            not isinstance(identifier, str)
            or not identifier
            or identifier in identifiers
            or isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or weight <= 0
            or criterion.get("severity") not in {"blocker", "important", "minor"}
        ):
            raise MaterializationError("source-schema")
        path = criterion.get("path")
        if (
            (kind.startswith("json_") or kind == "verification_command")
            and (
                not isinstance(path, list)
                or not all(
                    isinstance(component, str) and component for component in path
                )
            )
            and (path != [] or kind != "json_no_contradiction")
        ):
            raise MaterializationError("source-schema")
        if kind == "security_oracle" and (
            not isinstance(path, str)
            or not path
            or Path(path).is_absolute()
            or ".." in Path(path).parts
        ):
            raise MaterializationError("source-schema")
        _validate_criterion_value(
            criterion,
            scenario_ids=scenario_ids or set(),
            behavior_ids=behavior_ids or set(),
        )
        identifiers.add(identifier)
        validated.append(criterion)
    kinds = {str(item["kind"]) for item in validated}
    if fixture_id == "requirement-contract-ambiguity" and kinds != {
        "json_literal",
        "json_enum",
        "json_set_contains",
        "json_relation",
        "json_no_contradiction",
        "verification_command",
    }:
        raise MaterializationError("source-schema")
    if fixture_id == "frontend-recovery-delivery" and (
        kinds != {"frontend_browser_suite"}
        or not {"FRD-AC001", "FRD-AC002", "FRD-AC006"}.issubset(identifiers)
    ):
        raise MaterializationError("source-schema")
    if fixture_id == "frontend-recovery-delivery":
        by_id = {str(item["id"]): item["expected"] for item in validated}
        required_behavior = {
            "FRD-AC001": {"behavior_checks": {"field_rendering": True}},
            "FRD-AC002": {"behavior_checks": {"filtering": True}},
            "FRD-AC006": {
                "executed_with_real_browser": True,
                "console_errors": [],
                "basic_accessibility": True,
            },
        }
        if any(
            by_id.get(identifier) != expected
            for identifier, expected in required_behavior.items()
        ):
            raise MaterializationError("source-schema")
    if fixture_id == "multi-tenant-security-review" and kinds != {"security_oracle"}:
        raise MaterializationError("source-schema")
    return validated


def _public_intent_taxonomy(
    policy: MaterializerPolicy,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    fixture_root = policy.repo_root / _BENCHMARK_RELATIVE / "fixtures"
    try:
        requirement = json.loads(
            (
                fixture_root
                / "requirement-contract-ambiguity/public/benchmark-task/input-contract.json"
            ).read_bytes()
        )
        questions = requirement["semantics"]["question_taxonomy"]
        approvals = []
        for fixture_id in (
            "requirement-contract-ambiguity",
            "frontend-recovery-delivery",
        ):
            contract = json.loads(
                (
                    fixture_root
                    / fixture_id
                    / "public/benchmark-task/service-contract.json"
                ).read_bytes()
            )
            approvals.append(contract["approval_type"])
    except (KeyError, OSError, TypeError, json.JSONDecodeError) as error:
        raise MaterializationError("source-schema") from error
    if (
        not isinstance(questions, list)
        or len(questions) != 4
        or len(set(questions)) != 4
        or not all(isinstance(item, str) and item for item in questions)
        or len(set(approvals)) != 2
        or not all(isinstance(item, str) and item for item in approvals)
    ):
        raise MaterializationError("source-schema")
    return tuple(questions), tuple(approvals)


def _validate_source_object_unchecked(
    source_bytes: bytes, *, policy: MaterializerPolicy
) -> Mapping[str, object]:
    try:
        raw = _closed(_strict_json_loads(source_bytes), _SOURCE_KEYS, "source-schema")
    except MaterializationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MaterializationError("source-schema") from error
    if (
        raw["schema"] != "ai-sdlc-v2-benefit-sealed-source/v1"
        or raw["lock_id"] != policy.target.name
    ):
        raise MaterializationError("source-schema")
    intent = _closed(raw["intent_map"], _INTENT_KEYS, "source-schema")
    if intent["schema"] != "ai-sdlc-v2-benefit-intent-map/v2":
        raise MaterializationError("source-schema")
    questions = intent["questions"]
    approvals = intent["approvals"]
    if not isinstance(questions, Mapping) or not isinstance(approvals, list):
        raise MaterializationError("source-schema")
    for question_id, item in questions.items():
        question = _closed(item, _QUESTION_KEYS, "source-schema")
        if (
            not isinstance(question_id, str)
            or not question_id
            or isinstance(question["delay_ms"], bool)
            or not isinstance(question["delay_ms"], int)
            or question["delay_ms"] < 0
        ):
            raise MaterializationError("source-schema")
    if len(approvals) != len(set(approvals)) or not all(
        isinstance(item, str) and item for item in approvals
    ):
        raise MaterializationError("source-schema")
    public_questions, public_approvals = _public_intent_taxonomy(policy)
    if set(questions) != set(public_questions) or tuple(approvals) != public_approvals:
        raise MaterializationError("source-schema")
    payloads = raw["payloads"]
    if not isinstance(payloads, Mapping) or set(payloads) != set(FIXTURE_IDS):
        raise MaterializationError("source-schema")
    for fixture_id in FIXTURE_IDS:
        expected_keys = {
            "requirement-contract-ambiguity": {"schema", "fixture_id", "criteria"},
            "frontend-recovery-delivery": {
                "schema",
                "fixture_id",
                "held_out_variant_classes",
                "browser_program",
                "criteria",
            },
            "multi-tenant-security-review": {
                "schema",
                "fixture_id",
                "held_out_variant_classes",
                "root_causes",
                "criteria",
            },
        }[fixture_id]
        payload = _closed(payloads[fixture_id], expected_keys, "source-schema")
        if (
            payload["schema"] != "ai-sdlc-v2-benefit-sealed-evaluator/v2"
            or payload["fixture_id"] != fixture_id
        ):
            raise MaterializationError("source-schema")
        scenario_ids: set[str] = set()
        behavior_ids: set[str] = set()
        if fixture_id == "frontend-recovery-delivery":
            try:
                scenario_ids = set(
                    validate_frontend_browser_program(payload["browser_program"])
                )
                behavior_ids = {
                    str(assertion["expose_as"])
                    for scenario in payload["browser_program"]["scenarios"]
                    for assertion in scenario["assertions"]
                    if assertion["expose_as"] is not None
                }
            except (KeyError, TypeError, ValueError) as error:
                raise MaterializationError("source-schema") from error
        criteria = _validate_criteria(
            fixture_id,
            payload["criteria"],
            scenario_ids=scenario_ids,
            behavior_ids=behavior_ids,
        )
        if fixture_id != "requirement-contract-ambiguity":
            variants = payload["held_out_variant_classes"]
            if (
                not isinstance(variants, list)
                or len(variants) != 4
                or len(set(variants)) != 4
                or not all(isinstance(item, str) and item for item in variants)
            ):
                raise MaterializationError("source-schema")
        if fixture_id == "frontend-recovery-delivery":
            covered_scenarios = {
                str(scenario_id)
                for criterion in criteria
                for scenario_id in (
                    criterion["expected"].get("scenarios", {})
                    if isinstance(criterion["expected"], Mapping)
                    else {}
                )
            }
            if len(scenario_ids) != 6 or len(covered_scenarios) != 4:
                raise MaterializationError("source-schema")
        if fixture_id == "multi-tenant-security-review":
            roots = payload["root_causes"]
            criterion_roots = [item["root_cause"] for item in criteria]
            if (
                not isinstance(roots, list)
                or len(roots) != 6
                or len(set(roots)) != 6
                or roots != criterion_roots
                or set(roots) != _SECURITY_ROOT_CAUSES
                or not all(isinstance(item, str) and item for item in roots)
            ):
                raise MaterializationError("source-schema")
    return raw


def _validate_source_object(
    source_bytes: bytes, *, policy: MaterializerPolicy
) -> Mapping[str, object]:
    try:
        return _validate_source_object_unchecked(source_bytes, policy=policy)
    except MaterializationError:
        raise
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as error:
        raise MaterializationError("source-schema") from error


def _assert_trusted_source(
    source: SourceRead, policy: MaterializerPolicy
) -> TreeFingerprint:
    if source.source_path is None:
        raise MaterializationError("source-path")
    try:
        base = policy.source_base.resolve(strict=True)
        root = policy.source_root.resolve(strict=True)
        source_path = source.source_path.resolve(strict=True)
        base_stat = policy.source_base.lstat()
        root_stat = policy.source_root.lstat()
        leaf_stat = source_path.lstat()
        if (
            stat.S_ISLNK(base_stat.st_mode)
            or stat.S_ISLNK(root_stat.st_mode)
            or not stat.S_ISDIR(base_stat.st_mode)
            or not stat.S_ISDIR(root_stat.st_mode)
            or base_stat.st_uid != os.geteuid()
            or root_stat.st_uid != os.geteuid()
            or stat.S_IMODE(base_stat.st_mode) != 0o700
            or stat.S_IMODE(root_stat.st_mode) != 0o700
            or base_stat.st_dev != root_stat.st_dev
            or root.parent != base
            or source_path.parent != root
            or source.device != root_stat.st_dev
            or (leaf_stat.st_dev, leaf_stat.st_ino) != (source.device, source.inode)
            or not stat.S_ISREG(leaf_stat.st_mode)
        ):
            raise MaterializationError("source-security")
    except MaterializationError:
        raise
    except OSError as error:
        raise MaterializationError("source-security") from error
    roots = (
        policy.repo_root,
        policy.target,
        policy.target.parent,
        *policy.forbidden_roots,
    )
    for root in roots:
        resolved = root.resolve(strict=False)
        try:
            source_path.relative_to(resolved)
            raise MaterializationError("source-overlap")
        except ValueError:
            pass
    fingerprint = fingerprint_tree(policy.source_root)
    try:
        rebound_root = policy.source_root.lstat()
        rebound_leaf = source_path.lstat()
    except OSError as error:
        raise MaterializationError("source-raced") from error
    if (
        fingerprint.inode != root_stat.st_ino
        or (rebound_root.st_dev, rebound_root.st_ino)
        != (root_stat.st_dev, root_stat.st_ino)
        or (rebound_leaf.st_dev, rebound_leaf.st_ino) != (source.device, source.inode)
    ):
        raise MaterializationError("source-raced")
    return fingerprint


def compile_source_bundle(
    source_bytes: bytes,
    *,
    expected_source_sha256: str,
    expected_head: str,
    policy: MaterializerPolicy,
) -> CompiledMaterialization:
    """Compile one external canonical source into closed committed output bytes."""
    if (
        not _valid_digest(expected_source_sha256)
        or _digest_bytes(source_bytes) != expected_source_sha256
    ):
        raise MaterializationError("source-digest")
    source = _validate_source_object(source_bytes, policy=policy)
    bindings = _capture_repo_bindings(expected_head, policy)
    source_root_tree_sha256 = fingerprint_tree(policy.source_root).sha256
    intent_bytes = _canonical_json_bytes(source["intent_map"])
    payloads = source["payloads"]
    files: dict[str, bytes] = {"intent-map.json": intent_bytes}
    entries: list[dict[str, str]] = []
    payload_commitments: list[dict[str, str]] = []
    for fixture_id in FIXTURE_IDS:
        name = f"{fixture_id}.sealed.json"
        data = _canonical_json_bytes(payloads[fixture_id])
        digest = _digest_bytes(data)
        files[name] = data
        entries.append({"fixture_id": fixture_id, "path": name, "sha256": digest})
        payload_commitments.append({"fixture_id": fixture_id, "sha256": digest})
    intent_sha = _digest_bytes(intent_bytes)
    manifest = {
        "schema": "ai-sdlc-v2-benefit-sealed-manifest/v2",
        "lock_id": policy.target.name,
        "entries": entries,
        "intent_map": {"path": "intent-map.json", "sha256": intent_sha},
    }
    manifest_bytes = _canonical_json_bytes(manifest)
    files["sealed-manifest.json"] = manifest_bytes
    commitments = {
        "schema": "ai-sdlc-v2-benefit-candidate-commitments/v1",
        "lock_id": policy.target.name,
        "source_head": bindings.source_head,
        "source_tree_sha": bindings.source_tree_sha,
        "materializer_sha256": bindings.materializer_sha256,
        "source_bundle_sha256": expected_source_sha256,
        "fixture_manifest_sha256": bindings.fixture_manifest_sha256,
        "fixture_tree_sha256": bindings.fixture_tree_sha256,
        "evidence_contract_sha256": bindings.evidence_contract_sha256,
        "sealed_manifest_sha256": _digest_bytes(manifest_bytes),
        "intent_map_sha256": intent_sha,
        "fixture_payloads": payload_commitments,
        "source_root_tree_sha256": source_root_tree_sha256,
    }
    commitment_bytes = _canonical_json_bytes(commitments)
    files["candidate-commitments.json"] = commitment_bytes
    receipt = {
        "schema": "ai-sdlc-v2-benefit-materialization-receipt/v1",
        "publication_state": "published-pending-isolation",
        "isolation_probe_state": "pending",
        "target_lock_id": policy.target.name,
        "source_head": bindings.source_head,
        "source_tree_sha": bindings.source_tree_sha,
        "materializer_sha256": bindings.materializer_sha256,
        "source_bundle_sha256": expected_source_sha256,
        "fixture_manifest_sha256": bindings.fixture_manifest_sha256,
        "fixture_tree_sha256": bindings.fixture_tree_sha256,
        "evidence_contract_sha256": bindings.evidence_contract_sha256,
        "sealed_manifest_sha256": _digest_bytes(manifest_bytes),
        "intent_map_sha256": intent_sha,
        "fixture_payloads": payload_commitments,
        "candidate_commitments_sha256": _digest_bytes(commitment_bytes),
        "source_root_tree_sha256": source_root_tree_sha256,
    }
    files["materialization-receipt.json"] = _canonical_json_bytes(receipt)
    if tuple(files) != _OUTPUT_ORDER:
        raise MaterializationError("compiler-order")
    return CompiledMaterialization(
        MappingProxyType(files), bindings, expected_source_sha256
    )


def _validate_candidate_commitments(
    root: Path, compiled: CompiledMaterialization
) -> None:
    try:
        if any(
            (root / name).read_bytes() != compiled.files[name] for name in _OUTPUT_ORDER
        ):
            raise MaterializationError("candidate-commitments")
        raw = _closed(
            json.loads((root / "candidate-commitments.json").read_bytes()),
            CANDIDATE_COMMITMENT_KEYS,
            "candidate-commitments",
        )
        receipt = _closed(
            json.loads((root / "materialization-receipt.json").read_bytes()),
            RECEIPT_KEYS,
            "materialization-receipt",
        )
        manifest_bytes = (root / "sealed-manifest.json").read_bytes()
        manifest = json.loads(manifest_bytes)
        expected_payloads = [
            {
                "fixture_id": fixture_id,
                "sha256": _digest_file(root / f"{fixture_id}.sealed.json"),
            }
            for fixture_id in FIXTURE_IDS
        ]
        if (
            raw["schema"] != "ai-sdlc-v2-benefit-candidate-commitments/v1"
            or raw["lock_id"] != manifest["lock_id"]
            or raw["sealed_manifest_sha256"] != _digest_bytes(manifest_bytes)
            or raw["intent_map_sha256"] != _digest_file(root / "intent-map.json")
            or raw["fixture_payloads"] != expected_payloads
            or receipt["candidate_commitments_sha256"]
            != _digest_file(root / "candidate-commitments.json")
            or receipt["fixture_payloads"] != expected_payloads
            or receipt["source_bundle_sha256"] != compiled.source_bundle_sha256
            or receipt["publication_state"] != "published-pending-isolation"
            or receipt["isolation_probe_state"] != "pending"
            or receipt["source_root_tree_sha256"] != raw["source_root_tree_sha256"]
        ):
            raise MaterializationError("candidate-commitments")
    except MaterializationError:
        raise
    except (KeyError, OSError, TypeError, json.JSONDecodeError) as error:
        raise MaterializationError("candidate-commitments") from error


def _write_plain_files(root: Path, files: Mapping[str, bytes]) -> None:
    root.mkdir(mode=0o700)
    for name in _OUTPUT_ORDER:
        path = root / name
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            _write_all(descriptor, files[name])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _validate_scratch(
    compiled: CompiledMaterialization,
    *,
    scratch_parent: Path | None,
    fixture_root: Path,
) -> None:
    """Run all deterministic validation in a disposable root before publication."""
    if scratch_parent is not None:
        scratch_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".ai-sdlc-materializer-validation-", dir=scratch_parent
    ) as temporary:
        validation = Path(temporary)
        protected = validation / "protected"
        runs = validation / "runs"
        protected.mkdir(mode=0o700)
        runs.mkdir(mode=0o700)
        sealed = protected / "sealed"
        _write_plain_files(sealed, compiled.files)
        _validate_candidate_commitments(sealed, compiled)
        intent = json.loads((sealed / "intent-map.json").read_bytes())
        events = validation / "intent-events.jsonl"
        service = FrozenIntentApprovalService.from_sealed_root(sealed, events)
        for question_id, item in intent["questions"].items():
            if service.answer("validation-known", question_id) != {
                "status": "answered",
                "answer": item["answer"],
            }:
                raise MaterializationError("intent-validation")
        if service.answer("validation-unknown", "__unknown__") != {
            "status": "unresolved"
        }:
            raise MaterializationError("intent-validation")
        for approval in intent["approvals"]:
            digest = _digest_bytes(approval.encode("utf-8"))
            correct_run = f"validation-correct-{approval}"
            wrong_run = f"validation-wrong-{approval}"
            zero_run = f"validation-zero-{approval}"
            expired_run = f"validation-expired-{approval}"
            service.register_proposal(correct_run, approval, digest)
            if (
                service.approval_request(correct_run, approval, digest).get("status")
                != "approved"
            ):
                raise MaterializationError("intent-validation")
            service.register_proposal(wrong_run, approval, digest)
            if service.approval_request(wrong_run, approval, "1" * 64) != {
                "status": "revise"
            }:
                raise MaterializationError("intent-validation")
            service.register_proposal(zero_run, approval, digest)
            if service.approval_request(zero_run, approval, "0" * 64) != {
                "status": "revise"
            }:
                raise MaterializationError("intent-validation")
            service.register_proposal(expired_run, approval, digest)
            service.expire_run(expired_run)
            if service.approval_request(expired_run, approval, digest) != {
                "status": "revise"
            }:
                raise MaterializationError("intent-validation")
        if service.approval_request("validation-unknown", "__unknown__", "1" * 64) != {
            "status": "revise"
        }:
            raise MaterializationError("intent-validation")
        for fixture_id in FIXTURE_IDS:
            first = prepare_fixture(
                fixture_id,
                runs / f"candidate-a-{fixture_id}",
                fixture_root=fixture_root,
            )
            second = prepare_fixture(
                fixture_id,
                runs / f"candidate-b-{fixture_id}",
                fixture_root=fixture_root,
            )
            first_result = evaluate_fixture(fixture_id, first.root, sealed)
            second_result = evaluate_fixture(fixture_id, second.root, sealed)
            if (
                first.public_tree_sha256 != second.public_tree_sha256
                or first.initial_commit != second.initial_commit
                or first.visible_results != second.visible_results
                or first_result != second_result
                or first_result.external_verified_delivery
                or scan_candidate_for_sealed_leak(
                    first.root, sealed / "sealed-manifest.json"
                )
                or scan_candidate_for_sealed_leak(
                    second.root, sealed / "sealed-manifest.json"
                )
            ):
                raise MaterializationError("evaluation-validation")


def _paths_overlap(left: Path, right: Path) -> bool:
    left_resolved = left.resolve(strict=False)
    right_resolved = right.resolve(strict=False)
    try:
        left_resolved.relative_to(right_resolved)
        return True
    except ValueError:
        pass
    try:
        right_resolved.relative_to(left_resolved)
        return True
    except ValueError:
        return False


def _open_trusted_parent(policy: MaterializerPolicy) -> tuple[int, os.stat_result]:
    target = policy.target
    parent = target.parent
    try:
        anchor = policy.trust_anchor.resolve(strict=True)
        parent.relative_to(anchor)
    except (OSError, ValueError) as error:
        raise MaterializationError("target-ancestor") from error
    current = anchor
    device = anchor.lstat().st_dev
    try:
        for component in parent.relative_to(anchor).parts:
            current = current / component
            item = current.lstat()
            if (
                stat.S_ISLNK(item.st_mode)
                or not stat.S_ISDIR(item.st_mode)
                or item.st_uid != os.geteuid()
                or stat.S_IMODE(item.st_mode) != 0o700
            ):
                raise MaterializationError("target-ancestor")
            if item.st_dev != device:
                raise MaterializationError("target-device")
        flags = (
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(parent, flags)
        opened = os.fstat(descriptor)
        lexical = parent.lstat()
        if (opened.st_dev, opened.st_ino) != (lexical.st_dev, lexical.st_ino):
            os.close(descriptor)
            raise MaterializationError("target-raced")
    except MaterializationError:
        raise
    except OSError as error:
        raise MaterializationError("target-ancestor") from error
    for root in policy.forbidden_roots:
        if _paths_overlap(target, root):
            os.close(descriptor)
            raise MaterializationError("target-overlap")
    try:
        os.stat(target.name, dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        pass
    except OSError as error:
        os.close(descriptor)
        raise MaterializationError("target-state") from error
    else:
        os.close(descriptor)
        raise MaterializationError("target-exists")
    return descriptor, opened


def _assert_parent_binding(
    parent_fd: int, parent: Path, expected: os.stat_result
) -> None:
    try:
        opened = os.fstat(parent_fd)
        lexical = parent.lstat()
    except OSError as error:
        raise MaterializationError("target-raced") from error
    identity = (expected.st_dev, expected.st_ino)
    if (
        (opened.st_dev, opened.st_ino) != identity
        or (lexical.st_dev, lexical.st_ino) != identity
        or not stat.S_ISDIR(opened.st_mode)
        or opened.st_uid != os.geteuid()
        or stat.S_IMODE(opened.st_mode) != 0o700
    ):
        raise MaterializationError("target-raced")


def _write_all(descriptor: int, value: bytes) -> None:
    view = memoryview(value)
    offset = 0
    while offset < len(view):
        try:
            written = os.write(descriptor, view[offset:])
        except OSError as error:
            raise MaterializationError("write-failed") from error
        if written <= 0:
            raise MaterializationError("write-failed")
        offset += written


def _mkdtemp_at(
    parent_fd: int, *, prefix: str, expected_device: int
) -> tuple[str, os.stat_result, int]:
    for _attempt in range(64):
        name = f"{prefix}{secrets.token_hex(16)}"
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            continue
        except OSError as error:
            raise MaterializationError("staging-create") from error
        try:
            item = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            descriptor = os.open(
                name,
                os.O_RDONLY
                | os.O_DIRECTORY
                | os.O_CLOEXEC
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
            opened = os.fstat(descriptor)
            if (
                (opened.st_dev, opened.st_ino) != (item.st_dev, item.st_ino)
                or item.st_dev != expected_device
                or item.st_uid != os.geteuid()
                or stat.S_IMODE(item.st_mode) != 0o700
                or not stat.S_ISDIR(item.st_mode)
            ):
                os.close(descriptor)
                raise MaterializationError("staging-security")
            return name, item, descriptor
        except Exception as error:
            try:
                os.rmdir(name, dir_fd=parent_fd)
            except OSError as cleanup_error:
                raise MaterializationError("cleanup-failed") from cleanup_error
            if isinstance(error, MaterializationError):
                raise
            raise MaterializationError("staging-security") from error
    raise MaterializationError("staging-create")


def _remove_directory_contents(directory_fd: int) -> None:
    try:
        entries = list(os.scandir(directory_fd))
    except OSError as error:
        raise MaterializationError("cleanup-failed") from error
    for entry in entries:
        try:
            item = entry.stat(follow_symlinks=False)
            if stat.S_ISREG(item.st_mode):
                os.unlink(entry.name, dir_fd=directory_fd)
            elif stat.S_ISDIR(item.st_mode):
                child_fd = os.open(
                    entry.name,
                    os.O_RDONLY
                    | os.O_DIRECTORY
                    | os.O_CLOEXEC
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=directory_fd,
                )
                try:
                    _remove_directory_contents(child_fd)
                finally:
                    os.close(child_fd)
                current = os.stat(
                    entry.name, dir_fd=directory_fd, follow_symlinks=False
                )
                if (current.st_dev, current.st_ino) != (item.st_dev, item.st_ino):
                    raise MaterializationError("cleanup-failed")
                os.rmdir(entry.name, dir_fd=directory_fd)
            else:
                raise MaterializationError("cleanup-failed")
        except MaterializationError:
            raise
        except OSError as error:
            raise MaterializationError("cleanup-failed") from error


def _remove_owned_tree_at(parent_fd: int, name: str, identity: tuple[int, int]) -> None:
    try:
        item = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (item.st_dev, item.st_ino) != identity or not stat.S_ISDIR(item.st_mode):
            raise MaterializationError("cleanup-failed")
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != identity:
                raise MaterializationError("cleanup-failed")
            _remove_directory_contents(descriptor)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != identity:
            raise MaterializationError("cleanup-failed")
        os.rmdir(name, dir_fd=parent_fd)
    except MaterializationError:
        raise
    except OSError as error:
        raise MaterializationError("cleanup-failed") from error


def _load_rename_exclusive():
    if sys.platform != "darwin":
        return None
    try:
        function = ctypes.CDLL(None, use_errno=True).renameatx_np
    except AttributeError:
        return None
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int

    def rename(
        source_fd: int,
        source_name: str,
        destination_fd: int,
        destination_name: str,
    ) -> None:
        result = function(
            source_fd,
            os.fsencode(source_name),
            destination_fd,
            os.fsencode(destination_name),
            0x00000004,
        )
        if result != 0:
            error_number = ctypes.get_errno()
            code = "target-exists" if error_number == errno.EEXIST else "rename-failed"
            raise MaterializationError(code)

    return rename


_rename_exclusive = _load_rename_exclusive()


def _verify_published(
    parent_fd: int,
    target_name: str,
    expected_identity: tuple[int, int],
    files: Mapping[str, bytes],
) -> tuple[int, Mapping[str, str]]:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        target_fd = os.open(target_name, flags, dir_fd=parent_fd)
    except OSError as error:
        raise MaterializationError("postverify") from error
    digests: dict[str, str] = {}
    try:
        target_stat = os.fstat(target_fd)
        if (
            (target_stat.st_dev, target_stat.st_ino) != expected_identity
            or target_stat.st_uid != os.geteuid()
            or stat.S_IMODE(target_stat.st_mode) != 0o700
        ):
            raise MaterializationError("postverify")
        for name in _OUTPUT_ORDER:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=target_fd,
            )
            try:
                item = os.fstat(descriptor)
                data = _read_all(descriptor, positional=False)
                if (
                    not stat.S_ISREG(item.st_mode)
                    or item.st_uid != os.geteuid()
                    or stat.S_IMODE(item.st_mode) != 0o600
                    or item.st_nlink != 1
                    or data != files[name]
                ):
                    raise MaterializationError("postverify")
                digests[name] = _digest_bytes(data)
            finally:
                os.close(descriptor)
    except (OSError, KeyError) as error:
        raise MaterializationError("postverify") from error
    finally:
        os.close(target_fd)
    return target_stat.st_ino, MappingProxyType(digests)


def _assert_private_directory(path: Path, code: str) -> None:
    try:
        item = path.lstat()
    except OSError as error:
        raise MaterializationError(code) from error
    if (
        stat.S_ISLNK(item.st_mode)
        or not stat.S_ISDIR(item.st_mode)
        or item.st_uid != os.geteuid()
        or stat.S_IMODE(item.st_mode) != 0o700
    ):
        raise MaterializationError(code)


def _build_final_isolation_profile(
    policy: MaterializerPolicy,
) -> ProviderIsolationProfile:
    git_surfaces = derive_repo_git_surfaces(policy.repo_root)
    protected_roots = (*git_surfaces, policy.source_root)
    return build_provider_isolation_profile(
        run_root=policy.canary_run_root,
        sealed_root=policy.target,
        control_root=policy.repo_root,
        raw_results_root=policy.raw_results_root,
        protected_roots=protected_roots,
        other_run_roots=policy.other_run_roots,
        argv=("/usr/bin/true",),
        environment={"PATH": os.environ.get("PATH", "")},
    )


def _run_final_isolation_canary(
    policy: MaterializerPolicy, *, pending_receipt_sha256: str
) -> bytes:
    for root in (
        policy.canary_run_root,
        policy.raw_results_root,
        *policy.other_run_roots,
    ):
        _assert_private_directory(root, "isolation-root")
    try:
        profile = _build_final_isolation_profile(policy)
    except (OSError, ValueError) as error:
        raise MaterializationError("isolation-profile") from error
    if not profile.executable or profile.issues:
        raise MaterializationError("isolation-profile")
    try:
        probe = probe_provider_isolation(profile)
    except (OSError, RuntimeError, ValueError) as error:
        raise MaterializationError("isolation-canary") from error
    required = (
        probe.direct,
        probe.parent,
        probe.symlink,
        probe.hardlink,
        probe.environment,
        probe.other_run,
        probe.add_dir,
        all(value for _label, value in probe.protected_root_results),
    )
    if not all(required):
        raise MaterializationError("isolation-canary")
    profile_sha256 = _digest_bytes(
        _canonical_json_bytes(
            {
                "sandbox_text": profile.sandbox_text,
                "argv": list(profile.argv),
                "protected_root_count": len(profile.protected_roots),
                "other_run_root_count": len(profile.other_run_roots),
            }
        )
    )
    return _canonical_json_bytes(
        {
            "schema": "ai-sdlc-v2-benefit-isolation-attestation/v1",
            "state": "validated",
            "pending_receipt_sha256": pending_receipt_sha256,
            "profile_sha256": profile_sha256,
            "checks": {
                "direct": probe.direct,
                "parent": probe.parent,
                "symlink": probe.symlink,
                "hardlink": probe.hardlink,
                "environment": probe.environment,
                "other_run": probe.other_run,
                "add_dir": probe.add_dir,
                "protected_roots": len(probe.protected_root_results),
            },
        }
    )


def _write_final_attestation(
    parent_fd: int,
    target_name: str,
    expected_identity: tuple[int, int],
    data: bytes,
    failure_injector: FailureInjector,
) -> str:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        target_fd = os.open(target_name, flags, dir_fd=parent_fd)
        try:
            item = os.fstat(target_fd)
            if (item.st_dev, item.st_ino) != expected_identity:
                raise MaterializationError("postverify")
            failure_injector.hit("write-attestation")
            descriptor = os.open(
                _ATTESTATION_NAME,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | os.O_CLOEXEC
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=target_fd,
            )
            try:
                _write_all(descriptor, data)
                written = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(written.st_mode)
                    or written.st_uid != os.geteuid()
                    or stat.S_IMODE(written.st_mode) != 0o600
                    or written.st_nlink != 1
                ):
                    raise MaterializationError("postverify")
                failure_injector.hit("fsync-attestation")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            failure_injector.hit("fsync-final-dir")
            os.fsync(target_fd)
            descriptor = os.open(
                _ATTESTATION_NAME,
                os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=target_fd,
            )
            try:
                observed = _read_all(descriptor, positional=False)
                verified = os.fstat(descriptor)
                if observed != data or verified.st_nlink != 1:
                    raise MaterializationError("postverify")
            finally:
                os.close(descriptor)
        finally:
            os.close(target_fd)
        os.fsync(parent_fd)
    except MaterializationError:
        raise
    except OSError as error:
        raise MaterializationError("postverify") from error
    return _digest_bytes(data)


def _quarantine_published(
    *,
    parent_fd: int,
    target_name: str,
    expected_identity: tuple[int, int],
) -> None:
    if _rename_exclusive is None:
        raise MaterializationError("cleanup-failed")
    quarantine_name: str | None = None
    quarantine_identity: tuple[int, int] | None = None
    quarantine_fd: int | None = None
    moved = False
    try:
        item = os.stat(target_name, dir_fd=parent_fd, follow_symlinks=False)
        if (item.st_dev, item.st_ino) != expected_identity:
            raise MaterializationError("cleanup-failed")
        quarantine_name, quarantine_stat, quarantine_fd = _mkdtemp_at(
            parent_fd,
            prefix=f".{target_name}.quarantine-",
            expected_device=item.st_dev,
        )
        quarantine_identity = (quarantine_stat.st_dev, quarantine_stat.st_ino)
        _rename_exclusive(parent_fd, target_name, quarantine_fd, "published")
        moved = True
        os.fsync(parent_fd)
        os.fsync(quarantine_fd)
        _remove_owned_tree_at(quarantine_fd, "published", expected_identity)
        os.fsync(quarantine_fd)
        os.close(quarantine_fd)
        quarantine_fd = None
        _remove_owned_tree_at(parent_fd, quarantine_name, quarantine_identity)
        quarantine_name = None
        os.fsync(parent_fd)
    except Exception as error:
        cleanup_ok = True
        try:
            if quarantine_fd is not None:
                if moved:
                    _remove_owned_tree_at(quarantine_fd, "published", expected_identity)
                os.close(quarantine_fd)
                quarantine_fd = None
            if quarantine_name is not None and quarantine_identity is not None:
                _remove_owned_tree_at(parent_fd, quarantine_name, quarantine_identity)
                quarantine_name = None
            os.fsync(parent_fd)
        except Exception:
            cleanup_ok = False
        if not cleanup_ok or not isinstance(error, MaterializationError):
            raise MaterializationError("cleanup-failed") from None
        raise MaterializationError("cleanup-failed") from None


def _publish_compiled(
    compiled: CompiledMaterialization,
    *,
    policy: MaterializerPolicy,
    parent_fd: int,
    parent_stat: os.stat_result,
    failure_injector: FailureInjector,
) -> MaterializationResult:
    if _rename_exclusive is None:
        raise MaterializationError("rename-unavailable")
    _assert_parent_binding(parent_fd, policy.target.parent, parent_stat)
    staging_name, staging_stat, staging_fd = _mkdtemp_at(
        parent_fd,
        prefix=f".{policy.target.name}.staging-",
        expected_device=parent_stat.st_dev,
    )
    staging_identity = (staging_stat.st_dev, staging_stat.st_ino)
    published = False
    try:
        for name in _OUTPUT_ORDER:
            failure_injector.hit(f"write:{name}")
            descriptor = os.open(
                name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | os.O_CLOEXEC
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=staging_fd,
            )
            try:
                _write_all(descriptor, compiled.files[name])
                item = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(item.st_mode)
                    or item.st_uid != os.geteuid()
                    or stat.S_IMODE(item.st_mode) != 0o600
                    or item.st_nlink != 1
                ):
                    raise MaterializationError("staging-security")
                failure_injector.hit(f"fsync-file:{name}")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        failure_injector.hit("fsync-staging-dir")
        os.fsync(staging_fd)
        _assert_parent_binding(parent_fd, policy.target.parent, parent_stat)
        failure_injector.hit("rename")
        _rename_exclusive(parent_fd, staging_name, parent_fd, policy.target.name)
        published = True
        failure_injector.hit("fsync-parent")
        os.fsync(parent_fd)
        _assert_parent_binding(parent_fd, policy.target.parent, parent_stat)
        failure_injector.hit("postverify")
        target_inode, digests = _verify_published(
            parent_fd, policy.target.name, staging_identity, compiled.files
        )
        failure_injector.hit("isolation-canary")
        attestation = _run_final_isolation_canary(
            policy,
            pending_receipt_sha256=digests["materialization-receipt.json"],
        )
        attestation_sha256 = _write_final_attestation(
            parent_fd,
            policy.target.name,
            staging_identity,
            attestation,
            failure_injector,
        )
        final_digests = dict(digests)
        final_digests[_ATTESTATION_NAME] = attestation_sha256
        _assert_parent_binding(parent_fd, policy.target.parent, parent_stat)
        return MaterializationResult(
            policy.target.name,
            target_inode,
            MappingProxyType(final_digests),
        )
    except Exception as error:
        if published:
            try:
                _quarantine_published(
                    parent_fd=parent_fd,
                    target_name=policy.target.name,
                    expected_identity=staging_identity,
                )
            except Exception as cleanup_error:
                raise MaterializationError("cleanup-failed") from cleanup_error
        else:
            try:
                _remove_owned_tree_at(parent_fd, staging_name, staging_identity)
                os.fsync(parent_fd)
            except Exception as cleanup_error:
                raise MaterializationError("cleanup-failed") from cleanup_error
        if isinstance(error, MaterializationError):
            raise
        raise MaterializationError("publish-failed") from error
    finally:
        os.close(staging_fd)


def materialize_with_policy(
    *,
    source_fd: int,
    expected_source_sha256: str,
    expected_head: str,
    expected_old_root_tree_sha256: str,
    policy: MaterializerPolicy,
    failure_injector: FailureInjector | None = None,
) -> MaterializationResult:
    """Validate, compile and exclusively publish one sealed evaluator bundle."""
    injector = failure_injector or FailureInjector()
    start_bindings = _capture_repo_bindings(expected_head, policy)
    legacy_before = fingerprint_tree(policy.legacy_root)
    if legacy_before.inode != policy.expected_legacy_inode:
        raise MaterializationError("legacy-inode")
    if (
        not _valid_digest(expected_old_root_tree_sha256)
        or legacy_before.sha256 != expected_old_root_tree_sha256
    ):
        raise MaterializationError("legacy-tree")
    source = _read_source_record(
        source_fd=source_fd,
        expected_sha256=expected_source_sha256,
    )
    source_root_before = _assert_trusted_source(source, policy)
    parent_fd, parent_stat = _open_trusted_parent(policy)
    try:
        compiled = compile_source_bundle(
            source.canonical_bytes,
            expected_source_sha256=expected_source_sha256,
            expected_head=expected_head,
            policy=policy,
        )
        if compiled.bindings != start_bindings:
            raise MaterializationError("source-tree")
        fixture_root = policy.repo_root / _BENCHMARK_RELATIVE / "fixtures"
        _validate_scratch(
            compiled,
            scratch_parent=None,
            fixture_root=fixture_root,
        )
        _assert_parent_binding(parent_fd, policy.target.parent, parent_stat)
        _assert_repo_unchanged(compiled.bindings, policy)
        if fingerprint_tree(policy.legacy_root) != legacy_before:
            raise MaterializationError("legacy-changed")
        if fingerprint_tree(policy.source_root) != source_root_before:
            raise MaterializationError("source-raced")
        result = _publish_compiled(
            compiled,
            policy=policy,
            parent_fd=parent_fd,
            parent_stat=parent_stat,
            failure_injector=injector,
        )
        try:
            _assert_parent_binding(parent_fd, policy.target.parent, parent_stat)
            _assert_repo_unchanged(compiled.bindings, policy)
            if fingerprint_tree(policy.legacy_root) != legacy_before:
                raise MaterializationError("legacy-changed")
            if fingerprint_tree(policy.source_root) != source_root_before:
                raise MaterializationError("source-raced")
        except MaterializationError as error:
            try:
                _quarantine_published(
                    parent_fd=parent_fd,
                    target_name=policy.target.name,
                    expected_identity=(parent_stat.st_dev, result.target_inode),
                )
            except Exception as cleanup_error:
                raise MaterializationError("cleanup-failed") from cleanup_error
            raise error
        return result
    finally:
        os.close(parent_fd)


def materialize_sealed_bundle(
    *,
    source_fd: int,
    expected_source_sha256: str,
    expected_head: str,
    lock_id: str,
    expected_old_root_tree_sha256: str,
) -> MaterializationResult:
    """Production entry point with a literal, non-overridable final target."""
    if lock_id != FINAL_LOCK_ID or FINAL_TARGET.name != FINAL_LOCK_ID:
        raise MaterializationError("target-lock")
    return materialize_with_policy(
        source_fd=source_fd,
        expected_source_sha256=expected_source_sha256,
        expected_head=expected_head,
        expected_old_root_tree_sha256=expected_old_root_tree_sha256,
        policy=default_policy(),
    )
