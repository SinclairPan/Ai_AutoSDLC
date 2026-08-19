"""Public fixtures and sealed-evaluator boundaries for the v2 benefit benchmark."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from ai_sdlc.benefit_benchmark import BenchmarkIssue

FIXTURE_IDS = (
    "requirement-contract-ambiguity",
    "frontend-recovery-delivery",
    "multi-tenant-security-review",
)
ARMS = ("P", "S", "A00", "A10", "A11")
_BENCHMARK_ROOT = (
    Path(__file__).resolve().parents[2] / "benchmarks" / "ai-sdlc-v2-benefits"
)
_FIXTURE_ROOT = _BENCHMARK_ROOT / "fixtures"
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_SEALED_PHRASE_MARKERS = (
    "SEALED_RUBRIC_PHRASE",
    "consecutive-failure-recovery-answer",
    "delayed-response-race-answer",
    "rapid-double-submit-answer",
    "malformed-response-answer",
    "tenant-time-action-audit-answer",
)
_DETERMINISTIC_GIT_ENV = {
    "GIT_AUTHOR_NAME": "AI-SDLC Fixture Builder",
    "GIT_AUTHOR_EMAIL": "fixture@invalid.example",
    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
    "GIT_COMMITTER_NAME": "AI-SDLC Fixture Builder",
    "GIT_COMMITTER_EMAIL": "fixture@invalid.example",
    "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
    "TZ": "UTC",
}


@dataclass(frozen=True)
class VisibleCommand:
    command_id: str
    argv: tuple[str, ...]
    cwd: str
    expected_exit_code: int
    expected_signature: str
    signature_stream: str


@dataclass(frozen=True)
class FixtureManifestEntry:
    fixture_id: str
    public_root: str
    public_tree_sha256: str
    input_contract_sha256: str
    provenance_commit: str
    provenance_paths: tuple[str, ...]
    visible_commands: tuple[VisibleCommand, ...]


@dataclass(frozen=True)
class FixtureManifest:
    schema: str
    fixture_ids: tuple[str, ...]
    fixtures: tuple[FixtureManifestEntry, ...]
    canonical_sha256: str
    evidence_contract_template_sha256: str
    canonical_bytes: bytes


@dataclass(frozen=True)
class VisibleCommandResult:
    command_id: str
    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    stdout_sha256: str
    stderr_sha256: str
    expected_exit_code: int
    expected_signature: str
    matches_expected: bool


@dataclass(frozen=True)
class PreparedFixture:
    fixture_id: str
    root: Path
    public_tree_sha256: str
    initial_commit: str
    visible_results: tuple[VisibleCommandResult, ...]


@dataclass(frozen=True)
class EvaluationResult:
    fixture_id: str
    external_verified_delivery: bool
    weighted_ac_coverage: float
    severe_defect_escape_count: int
    satisfied_criteria: tuple[str, ...]
    failed_criteria: tuple[str, ...]
    result_sha256: str


@dataclass(frozen=True)
class ProviderIsolationProfile:
    run_root: Path
    sealed_root: Path
    control_root: Path
    other_run_roots: tuple[Path, ...]
    argv: tuple[str, ...]
    environment: Mapping[str, str]
    sandbox_text: str
    issues: tuple[BenchmarkIssue, ...]
    executable: bool


@dataclass(frozen=True)
class IsolationProbeResult:
    direct: bool
    parent: bool
    symlink: bool
    hardlink: bool
    environment: bool
    other_run: bool
    add_dir: bool


def _closed_object(value: object, keys: set[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError(f"{label} must be a closed object")
    return value


def _safe_relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("fixture paths must be non-empty POSIX relative paths")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("fixture paths must stay inside the public root")
    return value


def _digest_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _tree_digest(root: Path) -> str:
    if not root.is_dir():
        raise ValueError("fixture tree root is unavailable")
    entries: list[dict[str, object]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if ".git" in path.relative_to(root).parts:
            continue
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ValueError("public fixture trees cannot contain symlinks")
        if path.is_file():
            entries.append(
                {
                    "path": relative,
                    "mode": path.stat().st_mode & 0o777,
                    "size": path.stat().st_size,
                    "sha256": _digest_file(path),
                }
            )
    return sha256(_canonical_json_bytes(entries)).hexdigest()


def fixture_tree_digest(fixture_root: Path = _FIXTURE_ROOT) -> str:
    """Hash public fixture trees and paired contract/commitment files without cycles."""
    inputs: list[dict[str, str]] = []
    for fixture_id in FIXTURE_IDS:
        public = fixture_root / fixture_id / "public"
        inputs.append({"fixture_id": fixture_id, "public_tree_sha256": _tree_digest(public)})
    for name in ("evidence-contract.template.json",):
        path = fixture_root / name
        inputs.append({"fixture_id": name, "public_tree_sha256": _digest_file(path)})
    return sha256(_canonical_json_bytes(inputs)).hexdigest()


def load_fixture_manifest(path: Path = _FIXTURE_ROOT / "manifest.json") -> FixtureManifest:
    canonical_bytes = path.read_bytes()
    raw = _closed_object(
        json.loads(canonical_bytes),
        {
            "schema",
            "fixture_ids",
            "fixtures",
            "canonical_sha256",
            "evidence_contract_template_sha256",
        },
        "fixture manifest",
    )
    raw_fixtures = raw["fixtures"]
    if not isinstance(raw_fixtures, list):
        raise ValueError("fixture manifest fixtures must be a list")
    entries: list[FixtureManifestEntry] = []
    for raw_entry in raw_fixtures:
        entry = _closed_object(
            raw_entry,
            {
                "fixture_id",
                "public_root",
                "public_tree_sha256",
                "input_contract_sha256",
                "provenance_commit",
                "provenance_paths",
                "visible_commands",
            },
            "fixture manifest entry",
        )
        raw_commands = entry["visible_commands"]
        if not isinstance(raw_commands, list):
            raise ValueError("visible commands must be a list")
        commands: list[VisibleCommand] = []
        for raw_command in raw_commands:
            command = _closed_object(
                raw_command,
                {
                    "command_id",
                    "argv",
                    "cwd",
                    "expected_exit_code",
                    "expected_signature",
                    "signature_stream",
                },
                "visible command",
            )
            argv = command["argv"]
            if not isinstance(argv, list) or not argv or not all(
                isinstance(item, str) and item for item in argv
            ):
                raise ValueError("visible command argv must be non-empty strings")
            commands.append(
                VisibleCommand(
                    command_id=str(command["command_id"]),
                    argv=tuple(argv),
                    cwd=_safe_relative(command["cwd"]),
                    expected_exit_code=int(command["expected_exit_code"]),
                    expected_signature=str(command["expected_signature"]),
                    signature_stream=str(command["signature_stream"]),
                )
            )
        provenance_paths = entry["provenance_paths"]
        if not isinstance(provenance_paths, list) or not all(
            isinstance(item, str) and item for item in provenance_paths
        ):
            raise ValueError("provenance paths must be non-empty strings")
        entries.append(
            FixtureManifestEntry(
                fixture_id=str(entry["fixture_id"]),
                public_root=_safe_relative(entry["public_root"]),
                public_tree_sha256=str(entry["public_tree_sha256"]),
                input_contract_sha256=str(entry["input_contract_sha256"]),
                provenance_commit=str(entry["provenance_commit"]),
                provenance_paths=tuple(provenance_paths),
                visible_commands=tuple(commands),
            )
        )
    fixture_ids = raw["fixture_ids"]
    if not isinstance(fixture_ids, list) or not all(
        isinstance(item, str) for item in fixture_ids
    ):
        raise ValueError("fixture_ids must be a list of strings")
    return FixtureManifest(
        schema=str(raw["schema"]),
        fixture_ids=tuple(fixture_ids),
        fixtures=tuple(entries),
        canonical_sha256=str(raw["canonical_sha256"]),
        evidence_contract_template_sha256=str(
            raw["evidence_contract_template_sha256"]
        ),
        canonical_bytes=canonical_bytes,
    )


def validate_fixture_manifest(
    manifest: FixtureManifest, fixture_root: Path = _FIXTURE_ROOT
) -> list[BenchmarkIssue]:
    issues: list[BenchmarkIssue] = []
    if manifest.schema != "ai-sdlc-v2-benefit-fixture-manifest/v1":
        issues.append(BenchmarkIssue("fixture.manifest.schema", "unexpected schema"))
    if manifest.fixture_ids != FIXTURE_IDS or tuple(
        item.fixture_id for item in manifest.fixtures
    ) != FIXTURE_IDS:
        issues.append(
            BenchmarkIssue("fixture.manifest.coverage", "fixture order must match protocol")
        )
    for entry in manifest.fixtures:
        public = fixture_root / entry.public_root
        try:
            public.relative_to(fixture_root)
            actual_tree = _tree_digest(public)
            actual_input = _digest_file(public / "benchmark-task" / "input-contract.json")
        except (OSError, ValueError) as error:
            issues.append(BenchmarkIssue("fixture.manifest.path", str(error)))
            continue
        if actual_tree != entry.public_tree_sha256:
            issues.append(
                BenchmarkIssue("fixture.manifest.tree", f"{entry.fixture_id} tree drift")
            )
        if actual_input != entry.input_contract_sha256:
            issues.append(
                BenchmarkIssue(
                    "fixture.manifest.input-contract",
                    f"{entry.fixture_id} input contract drift",
                )
            )
        if not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", entry.provenance_commit):
            issues.append(
                BenchmarkIssue(
                    "fixture.manifest.provenance",
                    f"{entry.fixture_id} provenance commit is invalid",
                )
            )
    try:
        if manifest.canonical_sha256 != fixture_tree_digest(fixture_root):
            issues.append(BenchmarkIssue("fixture.manifest.digest", "fixture digest drift"))
        template = fixture_root / "evidence-contract.template.json"
        if _digest_file(template) != manifest.evidence_contract_template_sha256:
            issues.append(
                BenchmarkIssue(
                    "fixture.manifest.evidence-contract",
                    "evidence contract template drift",
                )
            )
    except (OSError, ValueError) as error:
        issues.append(BenchmarkIssue("fixture.manifest.digest", str(error)))
    return issues


def _entry_for(fixture_id: str, manifest: FixtureManifest) -> FixtureManifestEntry:
    if fixture_id not in FIXTURE_IDS:
        raise ValueError("fixture id is not frozen")
    entry = next((item for item in manifest.fixtures if item.fixture_id == fixture_id), None)
    if entry is None:
        raise ValueError("fixture manifest does not cover fixture")
    return entry


def _run_visible_commands(
    root: Path, entry: FixtureManifestEntry
) -> tuple[VisibleCommandResult, ...]:
    results: list[VisibleCommandResult] = []
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    for command in entry.visible_commands:
        completed = subprocess.run(
            list(command.argv),
            cwd=root / command.cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        stream = completed.stdout if command.signature_stream == "stdout" else completed.stderr
        results.append(
            VisibleCommandResult(
                command_id=command.command_id,
                argv=command.argv,
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                stdout_sha256=sha256(completed.stdout.encode()).hexdigest(),
                stderr_sha256=sha256(completed.stderr.encode()).hexdigest(),
                expected_exit_code=command.expected_exit_code,
                expected_signature=command.expected_signature,
                matches_expected=(
                    completed.returncode == command.expected_exit_code
                    and command.expected_signature in stream
                ),
            )
        )
    return tuple(results)


def prepare_fixture(fixture_id: str, destination: Path) -> PreparedFixture:
    """Copy one public fixture into a clean deterministic single-root Git repository."""
    manifest = load_fixture_manifest()
    issues = validate_fixture_manifest(manifest)
    if issues:
        raise ValueError("fixture manifest is invalid")
    entry = _entry_for(fixture_id, manifest)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("fixture destination must be absent or empty")
    source = _FIXTURE_ROOT / entry.public_root
    shutil.copytree(source, destination, dirs_exist_ok=True)
    public_digest = _tree_digest(destination)
    if public_digest != entry.public_tree_sha256:
        raise ValueError("copied fixture does not match the frozen public tree")
    env = {**os.environ, **_DETERMINISTIC_GIT_ENV}
    commands = (
        ["git", "init", "--quiet", "--initial-branch=main"],
        ["git", "config", "core.autocrlf", "false"],
        ["git", "add", "--all"],
        [
            "git",
            "-c",
            "user.name=AI-SDLC Fixture Builder",
            "-c",
            "user.email=fixture@invalid.example",
            "commit",
            "--quiet",
            "--message",
            f"fixture: freeze {fixture_id}",
        ],
    )
    for argv in commands:
        subprocess.run(argv, cwd=destination, env=env, check=True, capture_output=True)
    initial_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=destination,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    visible_results = _run_visible_commands(destination, entry)
    if not all(result.matches_expected for result in visible_results):
        raise ValueError("visible baseline does not match the frozen command contract")
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=destination,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if status:
        raise ValueError("visible baseline commands modified the fixture")
    return PreparedFixture(
        fixture_id=fixture_id,
        root=destination,
        public_tree_sha256=public_digest,
        initial_commit=initial_commit,
        visible_results=visible_results,
    )


def normalized_semantic_view(value: Mapping[str, object]) -> Mapping[str, object]:
    """Project public inputs and canonical state onto the same method-neutral semantics."""
    semantics = value.get("semantics")
    if not isinstance(semantics, Mapping):
        raise ValueError("semantic contract is missing")
    return json.loads(_canonical_json_bytes(semantics))


def build_canonical_pre_state(
    fixture_id: str, prepared_root: Path, destination: Path
) -> Mapping[str, object]:
    """Build deterministic A-arm state without adding semantics to the public contract."""
    if fixture_id not in FIXTURE_IDS:
        raise ValueError("fixture id is not frozen")
    source = prepared_root / "benchmark-task" / "input-contract.json"
    contract = json.loads(source.read_text(encoding="utf-8"))
    semantics = normalized_semantic_view(contract)
    target_stage = str(contract.get("target_stage"))
    canonical_pre_state = (
        ["requirement"]
        if target_stage == "design-contract"
        else ["requirement", "design-contract"]
    )
    state = {
        "schema": "ai-sdlc-v2-benefit-canonical-pre-state/v1",
        "fixture_id": fixture_id,
        "target_stage": target_stage,
        "canonical_pre_state": canonical_pre_state,
        "semantics": semantics,
        "source_input_contract_sha256": _digest_file(source),
    }
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "canonical-pre-state.json").write_bytes(
        _canonical_json_bytes(state) + b"\n"
    )
    return state


class FrozenIntentApprovalService:
    """Deterministic, automated-only intent and proposal-digest approval service."""

    def __init__(self, sealed_mapping: Path, event_log: Path):
        raw = _closed_object(
            json.loads(sealed_mapping.read_text(encoding="utf-8")),
            {"schema", "questions", "approvals"},
            "intent map",
        )
        if raw["schema"] != "ai-sdlc-v2-benefit-intent-map/v1":
            raise ValueError("intent map schema is invalid")
        if not isinstance(raw["questions"], Mapping) or not isinstance(
            raw["approvals"], list
        ):
            raise ValueError("intent map content is invalid")
        self._questions = raw["questions"]
        self._approvals = frozenset(raw["approvals"])
        self._expected_proposals: dict[tuple[str, str], str] = {}
        self._event_log = event_log

    def _record(self, payload: Mapping[str, object]) -> None:
        self._event_log.parent.mkdir(parents=True, exist_ok=True)
        with self._event_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    def answer(self, run_id: str, question_id: str) -> Mapping[str, object]:
        item = self._questions.get(question_id)
        if isinstance(item, Mapping) and isinstance(item.get("answer"), str):
            result: Mapping[str, object] = {
                "status": "answered",
                "answer": item["answer"],
            }
        else:
            result = {"status": "unresolved"}
        self._record(
            {
                "type": "intent_service_event",
                "actor": "automated_service",
                "run_id": run_id,
                "question_id": question_id,
                "configured_delay_ms": (
                    item.get("delay_ms", 0) if isinstance(item, Mapping) else 0
                ),
                "result_sha256": sha256(_canonical_json_bytes(result)).hexdigest(),
            }
        )
        return result

    def register_proposal(
        self, run_id: str, approval_type: str, proposal_digest: str
    ) -> None:
        """Bind the controller-observed proposal before the Provider requests approval."""
        if approval_type not in self._approvals or not _DIGEST.fullmatch(proposal_digest):
            raise ValueError("proposal registration is invalid")
        key = (run_id, approval_type)
        existing = self._expected_proposals.get(key)
        if existing is not None and existing != proposal_digest:
            raise ValueError("proposal registration is immutable")
        self._expected_proposals[key] = proposal_digest

    def approval_request(
        self, run_id: str, approval_type: str, proposal_digest: str
    ) -> Mapping[str, object]:
        expected = self._expected_proposals.get((run_id, approval_type))
        valid_digest = (
            bool(_DIGEST.fullmatch(proposal_digest))
            and proposal_digest != "0" * 64
            and proposal_digest == expected
        )
        if approval_type in self._approvals and valid_digest:
            result: Mapping[str, object] = {
                "status": "approved",
                "proposal_digest": proposal_digest,
            }
        else:
            result = {"status": "revise"}
        self._record(
            {
                "type": "approval_service_event",
                "actor": "automated_service",
                "run_id": run_id,
                "approval_type": approval_type,
                "proposal_digest": proposal_digest,
                "result_sha256": sha256(_canonical_json_bytes(result)).hexdigest(),
            }
        )
        return result


def _load_sealed_payload(fixture_id: str, sealed_root: Path) -> Mapping[str, object]:
    root = sealed_root.resolve(strict=True)
    manifest_path = root / "sealed-manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = _closed_object(
        json.loads(manifest_bytes), {"schema", "lock_id", "entries"}, "sealed manifest"
    )
    if manifest["schema"] != "ai-sdlc-v2-benefit-sealed-manifest/v1":
        raise ValueError("sealed manifest schema is invalid")
    entries = manifest["entries"]
    if not isinstance(entries, list) or len(entries) != len(FIXTURE_IDS):
        raise ValueError("sealed manifest coverage is invalid")
    entry = next(
        (
            item
            for item in entries
            if isinstance(item, Mapping) and item.get("fixture_id") == fixture_id
        ),
        None,
    )
    if not isinstance(entry, Mapping) or set(entry) != {"fixture_id", "path", "sha256"}:
        raise ValueError("sealed manifest entry is invalid")
    relative = _safe_relative(entry["path"])
    payload_path = root / relative
    try:
        payload_path.resolve(strict=True).relative_to(root)
    except ValueError as error:
        raise ValueError("sealed payload escapes evaluator root") from error
    payload_bytes = payload_path.read_bytes()
    if sha256(payload_bytes).hexdigest() != entry["sha256"]:
        raise ValueError("sealed payload commitment mismatch")
    payload = json.loads(payload_bytes)
    if not isinstance(payload, Mapping) or payload.get("fixture_id") != fixture_id:
        raise ValueError("sealed payload fixture binding is invalid")
    return payload


def _json_path_present(value: object, path: Sequence[object]) -> bool:
    current = value
    for part in path:
        if not isinstance(part, str) or not isinstance(current, Mapping) or part not in current:
            return False
        current = current[part]
    return current not in (None, "", [], {})


def _subset_matches(actual: object, expected: object) -> bool:
    if isinstance(expected, Mapping):
        return isinstance(actual, Mapping) and all(
            key in actual and _subset_matches(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            _subset_matches(item, expected[index]) for index, item in enumerate(actual)
        )
    return actual == expected


_SECURITY_ADAPTER = r'''
import importlib.util,json,sys
from datetime import datetime
source,raw=sys.argv[1:3]
scenario=json.loads(raw)
spec=importlib.util.spec_from_file_location("candidate_access_control",source)
module=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=module
spec.loader.exec_module(module)
actor=module.Actor(scenario["actor_id"],scenario["actor_tenant"],frozenset(scenario["roles"]))
request=module.ApprovalRequest(scenario["request_id"],scenario["request_tenant"],scenario["requester_id"],datetime.fromisoformat(scenario["expires_at"]),scenario.get("status","pending"))
class FailingAudit(list):
    def append(self,value):
        raise RuntimeError("audit unavailable")
mode=scenario.get("audit_mode","list")
audit=None if mode=="none" else FailingAudit() if mode=="failing" else []
result=None
error=None
try:
    result=module.approve_request(request,actor,action=scenario.get("action","approve"),now=datetime.fromisoformat(scenario["now"]),audit_log=audit)
except Exception as exc:
    error=type(exc).__name__
print(json.dumps({"allowed":getattr(result,"allowed",None),"reason":getattr(result,"reason",None),"status":request.status,"audit_count":None if audit is None else len(audit),"error":error},sort_keys=True))
'''


_FRONTEND_ADAPTER = r'''
import { pathToFileURL } from "node:url";
const [source,raw]=process.argv.slice(1);
const scenario=JSON.parse(raw);
const module=await import(pathToFileURL(source).href+`?probe=${Date.now()}`);
let loadCalls=0;
let confirmCalls=0;
const outcomes=[...(scenario.outcomes||[])];
const deferred=[];
const loader=()=>{
  loadCalls+=1;
  if(scenario.kind==="delayed_race") return new Promise((resolve,reject)=>deferred.push({resolve,reject}));
  const outcome=outcomes.shift();
  if(outcome?.type==="reject") return Promise.reject(new Error("unavailable"));
  return Promise.resolve(outcome?.value);
};
let releaseConfirm;
const confirmer=()=>{confirmCalls+=1; return new Promise((resolve)=>{releaseConfirm=resolve;});};
const controller=module.createRiskController(loader,confirmer);
if(scenario.kind==="failure_recovery") { await controller.load(); await controller.retry(); }
if(scenario.kind==="malformed") { await controller.load(); }
if(scenario.kind==="delayed_race") {
  const first=controller.load(); const second=controller.load();
  deferred[1].resolve(scenario.newer); await second;
  deferred[0].resolve(scenario.older); await first;
}
if(scenario.kind==="double_submit") {
  await controller.load();
  const first=controller.confirm(scenario.risk_id); const second=controller.confirm(scenario.risk_id);
  releaseConfirm(); await Promise.all([first,second]);
}
const state={...controller.state,confirming:[...(controller.state.confirming||[])]};
console.log(JSON.stringify({state,loadCalls,confirmCalls,hasRetry:typeof controller.retry==="function"}));
'''


def _run_candidate_adapter(
    candidate: Path,
    sealed_root: Path,
    *,
    runtime: str,
    source: Path,
    scenario: Mapping[str, object],
) -> Mapping[str, object]:
    if sys.platform != "darwin":
        raise RuntimeError("sealed candidate evaluation requires the macOS deny-read profile")
    if runtime == "python":
        argv = [sys.executable, "-I", "-c", _SECURITY_ADAPTER, str(source), json.dumps(scenario)]
    elif runtime == "node":
        argv = ["node", "--input-type=module", "-e", _FRONTEND_ADAPTER, str(source), json.dumps(scenario)]
    else:
        raise ValueError("sealed candidate runtime is unsupported")
    profile = build_provider_isolation_profile(
        run_root=candidate,
        sealed_root=sealed_root,
        control_root=_BENCHMARK_ROOT.parent.parent,
        other_run_roots=[],
        argv=argv,
        environment={"PATH": os.environ.get("PATH", "")},
    )
    if not profile.executable:
        raise ValueError("sealed candidate isolation preflight failed")
    completed = subprocess.run(
        ["/usr/bin/sandbox-exec", "-p", profile.sandbox_text, *argv],
        cwd=candidate,
        env=dict(profile.environment),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if "sandbox_apply: Operation not permitted" in completed.stderr:
        raise RuntimeError(completed.stderr.strip())
    if completed.returncode != 0:
        return {"adapter_error": "candidate_execution_failed"}
    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"adapter_error": "candidate_output_invalid"}
    return parsed if isinstance(parsed, Mapping) else {"adapter_error": "candidate_output_invalid"}


def _criterion_passes(
    candidate: Path, sealed_root: Path, criterion: Mapping[str, object]
) -> bool:
    kind = criterion.get("kind")
    if kind == "json_key_present":
        path = criterion.get("path")
        if not isinstance(path, list):
            return False
        target = candidate / "benchmark-task" / "design-contract.json"
        if not target.is_file():
            return False
        try:
            return _json_path_present(json.loads(target.read_text()), path)
        except json.JSONDecodeError:
            return False
    if kind in {"file_contains", "file_not_contains"}:
        relative = criterion.get("path")
        value = criterion.get("value")
        if not isinstance(relative, str) or not isinstance(value, str):
            return False
        try:
            path = candidate.joinpath(*_safe_relative(relative).split("/"))
            path.resolve(strict=True).relative_to(candidate.resolve(strict=True))
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            return False
        present = value in content
        return present if kind == "file_contains" else not present
    if kind in {"frontend_scenario", "security_scenario"}:
        relative = criterion.get("path")
        scenario = criterion.get("scenario")
        expected = criterion.get("expected")
        if (
            not isinstance(relative, str)
            or not isinstance(scenario, Mapping)
            or not isinstance(expected, Mapping)
        ):
            return False
        try:
            source = candidate.joinpath(*_safe_relative(relative).split("/"))
            source.resolve(strict=True).relative_to(candidate.resolve(strict=True))
        except (OSError, ValueError):
            return False
        actual = _run_candidate_adapter(
            candidate,
            sealed_root,
            runtime="node" if kind == "frontend_scenario" else "python",
            source=source,
            scenario=scenario,
        )
        return _subset_matches(actual, expected)
    raise ValueError("sealed evaluator criterion kind is unsupported")


def evaluate_fixture(
    fixture_id: str, candidate: Path, sealed_root: Path
) -> EvaluationResult:
    """Score one candidate from a committed sealed payload, never from model output."""
    if fixture_id not in FIXTURE_IDS:
        raise ValueError("fixture id is not frozen")
    candidate_root = candidate.resolve(strict=True)
    sealed = sealed_root.resolve(strict=True)
    try:
        sealed.relative_to(candidate_root)
        raise ValueError("sealed evaluator root must be outside the candidate")
    except ValueError as error:
        if str(error) == "sealed evaluator root must be outside the candidate":
            raise
    payload = _load_sealed_payload(fixture_id, sealed)
    criteria = payload.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        raise ValueError("sealed evaluator criteria are invalid")
    satisfied: list[str] = []
    failed: list[str] = []
    total_weight = 0.0
    satisfied_weight = 0.0
    severe = 0
    for raw in criteria:
        kind = raw.get("kind") if isinstance(raw, Mapping) else None
        extra_keys = (
            {"value"}
            if kind in {"file_contains", "file_not_contains"}
            else {"scenario", "expected"}
            if kind in {"frontend_scenario", "security_scenario"}
            else set()
        )
        if not isinstance(raw, Mapping) or set(raw) != {
            "id",
            "weight",
            "severity",
            "kind",
            "path",
            *extra_keys,
        }:
            raise ValueError("sealed evaluator criterion surface is invalid")
        identifier = str(raw["id"])
        weight = raw["weight"]
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight <= 0:
            raise ValueError("sealed evaluator criterion weight is invalid")
        total_weight += float(weight)
        passed = _criterion_passes(candidate_root, sealed, raw)
        if passed:
            satisfied.append(identifier)
            satisfied_weight += float(weight)
        else:
            failed.append(identifier)
            if raw["severity"] in {"blocker", "important"}:
                severe += 1
    coverage = satisfied_weight / total_weight
    public_result = {
        "fixture_id": fixture_id,
        "external_verified_delivery": not failed,
        "weighted_ac_coverage": coverage,
        "severe_defect_escape_count": severe,
        "satisfied_criteria": satisfied,
        "failed_criteria": failed,
    }
    return EvaluationResult(
        fixture_id=fixture_id,
        external_verified_delivery=not failed,
        weighted_ac_coverage=coverage,
        severe_defect_escape_count=severe,
        satisfied_criteria=tuple(satisfied),
        failed_criteria=tuple(failed),
        result_sha256=sha256(_canonical_json_bytes(public_result)).hexdigest(),
    )


def scan_candidate_for_sealed_leak(
    candidate: Path, sealed_manifest: Path
) -> list[BenchmarkIssue]:
    """Detect public candidates that contain sealed names, commitments or root hints."""
    raw = json.loads(sealed_manifest.read_text(encoding="utf-8"))
    entries = raw.get("entries", []) if isinstance(raw, Mapping) else []
    filenames = {
        Path(str(item.get("path"))).name
        for item in entries
        if isinstance(item, Mapping)
    }
    digests = {
        str(item.get("sha256")) for item in entries if isinstance(item, Mapping)
    }
    sealed_root = sealed_manifest.parent.resolve().as_posix()
    issues: list[BenchmarkIssue] = []
    for path in sorted(candidate.rglob("*")):
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError):
            continue
        relative = path.relative_to(candidate).as_posix()
        if any(name in text for name in filenames):
            issues.append(BenchmarkIssue("fixture.leak.filename", relative))
        if any(digest in text for digest in digests):
            issues.append(BenchmarkIssue("fixture.leak.digest", relative))
        if any(marker in text for marker in _SEALED_PHRASE_MARKERS):
            issues.append(BenchmarkIssue("fixture.leak.rubric-phrase", relative))
        if sealed_root in text or f"file://{sealed_root}" in text:
            issues.append(BenchmarkIssue("fixture.leak.path", relative))
    return issues


def validate_sealed_commitments(
    commitments_path: Path, sealed_root: Path, fixture_root: Path | None = None
) -> list[BenchmarkIssue]:
    """Verify tracked digests against an evaluator root without returning plaintext."""
    issues: list[BenchmarkIssue] = []
    try:
        commitments = _closed_object(
            json.loads(commitments_path.read_text(encoding="utf-8")),
            {
                "schema",
                "lock_id",
                "sealed_manifest_sha256",
                "fixture_tree_sha256",
                "fixture_commitment",
                "evidence_contract_template_sha256",
                "evidence_contract_commitment",
                "fixture_payloads",
                "publication_state",
            },
            "sealed commitments",
        )
        if commitments["schema"] != "ai-sdlc-v2-benefit-sealed-commitments/v1":
            raise ValueError("sealed commitment schema is invalid")
        if commitments["lock_id"] != sealed_root.name:
            raise ValueError("sealed root lock id is invalid")
        public_root = fixture_root or commitments_path.parent
        fixture_digest = fixture_tree_digest(public_root)
        if not (
            commitments["fixture_tree_sha256"]
            == commitments["fixture_commitment"]
            == fixture_digest
        ):
            raise ValueError("fixture commitment pair is invalid")
        evidence_digest = _digest_file(
            public_root / "evidence-contract.template.json"
        )
        if not (
            commitments["evidence_contract_template_sha256"]
            == commitments["evidence_contract_commitment"]
            == evidence_digest
        ):
            raise ValueError("evidence contract commitment pair is invalid")
        manifest = sealed_root / "sealed-manifest.json"
        if _digest_file(manifest) != commitments["sealed_manifest_sha256"]:
            raise ValueError("sealed manifest commitment is invalid")
        manifest_raw = json.loads(manifest.read_text(encoding="utf-8"))
        manifest_entries = {
            item["fixture_id"]: item["sha256"] for item in manifest_raw["entries"]
        }
        payloads = commitments["fixture_payloads"]
        if not isinstance(payloads, list) or {
            item.get("fixture_id"): item.get("sha256")
            for item in payloads
            if isinstance(item, Mapping)
        } != manifest_entries:
            raise ValueError("sealed payload commitments are invalid")
        for fixture_id in FIXTURE_IDS:
            _load_sealed_payload(fixture_id, sealed_root)
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        issues.append(BenchmarkIssue("fixture.sealed-commitment", str(error)))
    return issues


def _seatbelt_literal(path: Path) -> str:
    return str(path.resolve()).replace("\\", "\\\\").replace('"', '\\"')


def _protected_inodes(root: Path) -> set[tuple[int, int]]:
    return {
        (path.stat().st_dev, path.stat().st_ino)
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def _link_issues(run_root: Path, sealed_root: Path) -> list[BenchmarkIssue]:
    issues: list[BenchmarkIssue] = []
    protected = _protected_inodes(sealed_root)
    for path in run_root.rglob("*"):
        if path.is_symlink():
            try:
                path.resolve(strict=True).relative_to(sealed_root.resolve(strict=True))
                issues.append(BenchmarkIssue("isolation.symlink", path.name))
            except (OSError, ValueError):
                pass
        elif path.is_file() and (path.stat().st_dev, path.stat().st_ino) in protected:
            issues.append(BenchmarkIssue("isolation.hardlink", path.name))
    return issues


def _contains_path(value: str, roots: Iterable[Path]) -> bool:
    normalized = value.replace("file://", "")
    return any(str(root.resolve()) in normalized for root in roots)


def build_provider_isolation_profile(
    *,
    run_root: Path,
    sealed_root: Path,
    control_root: Path,
    other_run_roots: Sequence[Path],
    argv: Sequence[str],
    environment: Mapping[str, str],
) -> ProviderIsolationProfile:
    """Create a fail-closed macOS Provider profile plus link/env/add-dir preflight."""
    run = run_root.resolve(strict=True)
    sealed = sealed_root.resolve(strict=True)
    control = control_root.resolve(strict=True)
    other = tuple(path.resolve(strict=True) for path in other_run_roots)
    issues = _link_issues(run, sealed)
    protected = (sealed, sealed.parent, control, *other)
    for key, value in environment.items():
        if key != "PATH" and _contains_path(value, protected):
            issues.append(BenchmarkIssue("isolation.environment", key))
    for index, value in enumerate(argv):
        if value == "--add-dir" and index + 1 < len(argv):
            add_dir = argv[index + 1]
            if _contains_path(add_dir, protected) or not _contains_path(add_dir, (run,)):
                issues.append(BenchmarkIssue("isolation.add-dir", "forbidden --add-dir"))
    deny_paths = tuple(dict.fromkeys((sealed, sealed.parent, control, *other)))
    deny_rules = "\n".join(
        f'  (deny file-read* file-write* (subpath "{_seatbelt_literal(path)}"))'
        for path in deny_paths
    )
    sandbox_text = (
        "(version 1)\n"
        "(allow default)\n"
        f"{deny_rules}\n"
    )
    executable = sys.platform == "darwin" and not issues
    return ProviderIsolationProfile(
        run_root=run,
        sealed_root=sealed,
        control_root=control,
        other_run_roots=other,
        argv=tuple(argv),
        environment={"PATH": environment.get("PATH", "")},
        sandbox_text=sandbox_text,
        issues=tuple(issues),
        executable=executable,
    )


def _sandbox_denies(profile: ProviderIsolationProfile, target: Path) -> bool:
    completed = subprocess.run(
        ["/usr/bin/sandbox-exec", "-p", profile.sandbox_text, "/bin/cat", str(target)],
        env=dict(profile.environment),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if "sandbox_apply: Operation not permitted" in completed.stderr:
        raise RuntimeError(completed.stderr.strip())
    return completed.returncode != 0 and "Operation not permitted" in completed.stderr


def probe_provider_isolation(profile: ProviderIsolationProfile) -> IsolationProbeResult:
    """Exercise the exact final profile against direct, parent, link and policy canaries."""
    if sys.platform != "darwin":
        raise RuntimeError("OS deny-read profile is unavailable on this platform")
    if profile.issues:
        raise ValueError("Provider isolation preflight has unresolved issues")
    sealed_file = next(path for path in profile.sealed_root.rglob("*") if path.is_file())
    direct = _sandbox_denies(profile, sealed_file)
    parent = _sandbox_denies(profile, profile.sealed_root.parent)
    symlink_path = profile.run_root / ".isolation-symlink-canary"
    hardlink_path = profile.run_root / ".isolation-hardlink-canary"
    symlink_path.unlink(missing_ok=True)
    hardlink_path.unlink(missing_ok=True)
    os.symlink(sealed_file, symlink_path)
    hardlink_created = False
    try:
        os.link(sealed_file, hardlink_path)
        hardlink_created = True
    except OSError:
        pass
    try:
        symlink = _sandbox_denies(profile, symlink_path)
        if hardlink_created:
            linked_profile = build_provider_isolation_profile(
                run_root=profile.run_root,
                sealed_root=profile.sealed_root,
                control_root=profile.control_root,
                other_run_roots=profile.other_run_roots,
                argv=profile.argv,
                environment=profile.environment,
            )
            hardlink = any(
                issue.code == "isolation.hardlink" for issue in linked_profile.issues
            )
        else:
            hardlink = True
    finally:
        symlink_path.unlink(missing_ok=True)
        if hardlink_created:
            hardlink_path.unlink(missing_ok=True)
    other_file = profile.other_run_roots[0] / ".other-run-canary"
    other_file.write_text("other-run", encoding="utf-8")
    try:
        other_run = _sandbox_denies(profile, other_file)
    finally:
        other_file.unlink(missing_ok=True)
    environment = not any(
        _contains_path(value, (profile.sealed_root, profile.control_root))
        for value in profile.environment.values()
    )
    add_dir = not any(value == "--add-dir" for value in profile.argv)
    return IsolationProbeResult(
        direct=direct,
        parent=parent,
        symlink=symlink,
        hardlink=hardlink,
        environment=environment,
        other_run=other_run,
        add_dir=add_dir,
    )
