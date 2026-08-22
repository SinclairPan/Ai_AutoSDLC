"""Frozen arm construction and zero-Provider isolation proofs for the v2 benchmark.

This module deliberately stops at command construction.  It can create deterministic Git
workspaces, run the exact local AI-SDLC initializer, and inspect what Codex 0.147.0 would see,
but it has no Provider-launch API and never reserves an attempt.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

from ai_sdlc.benefit_benchmark import (
    AttemptReservation,
    BenchmarkIssue,
    BenchmarkProtocol,
    _load_execution_authorization,
    canonical_protocol_digest,
)
from ai_sdlc.benefit_benchmark_fixtures import (
    PreparedFixture,
    ProviderIsolationProfile,
    build_canonical_pre_state,
    build_provider_isolation_profile,
    derive_repo_git_surfaces,
    normalized_semantic_view,
)

ARM_IDS = ("P", "S", "A00", "A10", "A11")
AI_SDLC_COMMIT = "737bda39e05c53450e180a20581b7b7a70db9cf0"
AI_SDLC_TREE = "3db58121e228a7a1c4c6b760c535d6df1ffdbe84"
SUPERPOWERS_COMMIT = "b36e0829c6d0140e93cfef2ca599b1b07d4a7797"
SUPERPOWERS_TREE = "21219529a4e224bcb27baf8816b039c8bf7c6673"
SUPERPOWERS_SOURCE_URL = "https://github.com/obra/superpowers.git"
SUPERPOWERS_ARCHIVE_SHA256 = (
    "8d795dfb2141e467bdf448474fd9acfa97dffa4da5837f0f6cf0dc2c290640ba"
)
SUPERPOWERS_LICENSE_SHA256 = (
    "a37e0e9697144819e1d965176ac4ae5bc3fa02d11e7812036bbcadf6dafe2400"
)
SUPERPOWERS_PROVIDER_TREE_SHA256 = (
    "b307431b48ee4cabb4c9c3843aa7727131449aff4eaa89f8a4d5b6112a60ea6a"
)
SUPERPOWERS_ADAPTATION_SHA256 = (
    "9e8f69cc9630cf27df6cbbd1b36dc05db13e9d40fb5c11046447d54be1bb2aee"
)
SUPERPOWERS_SEMANTIC_DIFF_SHA256 = (
    "922274d50d5d21d65bc725e7eafeadd1e306d87a3f6fd18968f2558c8aa3fc57"
)
CODEX_VERSION = "0.147.0"
PROVIDER_CWD = "benchmark-task/"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BENCHMARK_ROOT = _REPO_ROOT / "benchmarks" / "ai-sdlc-v2-benefits"
_ARMS_ROOT = _BENCHMARK_ROOT / "arms"
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_COMMIT = re.compile(r"^[a-f0-9]{40}$")
_SKILL_REFERENCE = re.compile(r"\$([a-z][a-z0-9_-]*)")
_IGNORED_SHELL_REFERENCES = {"path"}
_METHODOLOGY_MARKERS = ("ai-sdlc", "superpowers", "spec-kit")
_CAPABILITY_OFF_FEATURES = (
    "apps",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "computer_use",
    "enable_mcp_apps",
    "image_generation",
    "hooks",
    "memories",
    "multi_agent",
    "multi_agent_v2",
    "plugins",
    "remote_plugin",
    "standalone_web_search",
    "skill_mcp_dependency_install",
    "tool_suggest",
    "web_search_request",
    "workspace_dependencies",
)
ALLOWED_GIT_ENVIRONMENT_KEYS = frozenset(
    {
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_AUTHOR_DATE",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        "GIT_COMMITTER_DATE",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_TERMINAL_PROMPT",
    }
)
_DETERMINISTIC_GIT_ENV = {
    "GIT_AUTHOR_NAME": "AI-SDLC Benchmark Builder",
    "GIT_AUTHOR_EMAIL": "benchmark@invalid.example",
    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
    "GIT_COMMITTER_NAME": "AI-SDLC Benchmark Builder",
    "GIT_COMMITTER_EMAIL": "benchmark@invalid.example",
    "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "TZ": "UTC",
}
_MANIFEST_KEYS = {
    "schema",
    "arm_ids",
    "provider_cwd",
    "common_agent_contract_path",
    "common_agent_contract_sha256",
    "prompt_matrix_path",
    "prompt_matrix_sha256",
    "ai_sdlc_method_surface_manifest_path",
    "ai_sdlc_method_surface_manifest_sha256",
    "ai_sdlc",
    "superpowers",
    "codex",
    "global_inventory",
    "isolation",
    "callback_bridge_path",
    "callback_bridge_sha256",
    "arms",
}
_AI_SDLC_KEYS = {
    "version",
    "peeled_commit",
    "source_tree",
    "stock_agents_source_path",
    "stock_agents_sha256",
    "runtime_policy",
    "init_policy",
}
_SUPERPOWERS_KEYS = {
    "source_url",
    "tag",
    "tag_object_type",
    "peeled_commit",
    "source_tree",
    "full_archive_sha256",
    "closure_archive_sha256",
    "provenance_archive_path",
    "provider_closure_path",
    "provenance_root",
    "source_inventory_path",
    "source_inventory_sha256",
    "license_path",
    "license_sha256",
    "adaptation_path",
    "adaptation_sha256",
    "namespace_diff_path",
    "namespace_diff_sha256",
    "namespace_rewrite",
    "semantic_adaptation_diff_path",
    "semantic_adaptation_diff_sha256",
    "multi_agent",
    "activation_skill",
    "activation_agents_path",
    "activation_agents_sha256",
    "files",
}
_VENDOR_FILE_KEYS = {
    "path",
    "kind",
    "mode",
    "upstream_sha256",
    "adapted_sha256",
    "namespace_replacements",
}
_CODEX_KEYS = {
    "version",
    "package",
    "entrypoint_sha256",
    "native_binary_sha256",
    "exec_help_sha256",
    "features_sha256",
    "capability_off_features",
    "writer_sandbox",
    "expert_sandbox",
    "model",
    "reasoning_effort",
    "required_exec_options",
    "forbidden_exec_options",
}
_ARM_ENTRY_KEYS = {
    "arm_id",
    "config_path",
    "config_sha256",
    "override_path",
    "override_sha256",
    "harness_classification",
}
_GLOBAL_INVENTORY_KEYS = {
    "home_policy",
    "codex_home_policy",
    "global_rule_paths",
    "installed_plugins",
    "apps",
    "mcp_servers",
    "builtin_skills",
    "methodology_contamination_markers",
}
_ISOLATION_KEYS = {
    "profile",
    "deny_surfaces",
    "method_instructions_immutable",
    "direct_link_race_probes_required",
    "p_s_forbidden_namespaces",
    "a_writable_method_leaves",
}
_AUTH_V2_KEYS = {
    "schema",
    "protocol_sha256",
    "execution_commit",
    "execution_tree_sha256",
    "execution_clean_state_sha256",
    "task3_runner_sha256",
    "source_capsule_sha256",
    "prompt_matrix_sha256",
    "arm_manifest_sha256",
    "neutral_envelope_sha256",
    "superpowers_adaptation_sha256",
    "preflight_receipt_sha256",
    "execution_identity",
    "attempt_budget",
    "valid_from",
    "expires_at",
    "scope",
}
_AUTH_SCOPE_KEYS = {"mode", "run_ids", "operations"}
_AUTH_OPERATIONS = (
    "start_run",
    "transition_run_phase",
    "reserve_provider_attempt",
    "record_provider_completion",
    "start_service_transaction",
    "record_service_transaction",
    "seal_run_evidence",
)
_EMPTY_GIT_STATUS_SHA256 = sha256(b"").hexdigest()


@dataclass(frozen=True)
class ExecutionSourceBinding:
    commit: str
    tree_sha256: str
    clean_state_sha256: str
    task3_runner_sha256: str
    source_capsule_sha256: str
    prompt_matrix_sha256: str


@dataclass(frozen=True)
class VendorFile:
    path: str
    kind: str
    mode: str
    upstream_sha256: str
    adapted_sha256: str
    namespace_replacements: int


@dataclass(frozen=True)
class SuperpowersBinding:
    source_url: str
    tag: str
    tag_object_type: str
    peeled_commit: str
    source_tree: str
    full_archive_sha256: str
    closure_archive_sha256: str
    provenance_archive_path: str
    provider_closure_path: str
    provenance_root: str
    source_inventory_path: str
    source_inventory_sha256: str
    license_path: str
    license_sha256: str
    adaptation_path: str
    adaptation_sha256: str
    namespace_diff_path: str
    namespace_diff_sha256: str
    namespace_rewrite: Mapping[str, str]
    semantic_adaptation_diff_path: str
    semantic_adaptation_diff_sha256: str
    multi_agent: bool
    activation_skill: str
    activation_agents_path: str
    activation_agents_sha256: str
    files: tuple[VendorFile, ...]


@dataclass(frozen=True)
class CodexBinding:
    version: str
    package: str
    entrypoint_sha256: str
    native_binary_sha256: str
    exec_help_sha256: str
    features_sha256: str
    capability_off_features: tuple[str, ...]
    writer_sandbox: str
    expert_sandbox: str
    model: str
    reasoning_effort: str
    required_exec_options: tuple[str, ...]
    forbidden_exec_options: tuple[str, ...]


@dataclass(frozen=True)
class ArmEntry:
    arm_id: str
    config_path: str
    config_sha256: str
    override_path: str | None
    override_sha256: str | None
    harness_classification: str


@dataclass(frozen=True)
class ArmManifest:
    schema: str
    arm_ids: tuple[str, ...]
    provider_cwd: str
    common_agent_contract_path: str
    common_agent_contract_sha256: str
    prompt_matrix_path: str
    prompt_matrix_sha256: str
    ai_sdlc_method_surface_manifest_path: str
    ai_sdlc_method_surface_manifest_sha256: str
    ai_sdlc_commit: str
    ai_sdlc_tree: str
    stock_agents_source_path: str
    stock_agents_sha256: str
    superpowers: SuperpowersBinding
    codex: CodexBinding
    global_inventory: Mapping[str, object]
    isolation: Mapping[str, object]
    callback_bridge_path: str
    callback_bridge_sha256: str
    arms: tuple[ArmEntry, ...]
    canonical_bytes: bytes

    @property
    def superpowers_commit(self) -> str:
        return self.superpowers.peeled_commit

    @property
    def superpowers_tree(self) -> str:
        return self.superpowers.source_tree

    def arm(self, arm_id: str) -> ArmEntry:
        for entry in self.arms:
            if entry.arm_id == arm_id:
                return entry
        raise ValueError("arm manifest has no such arm")


@dataclass(frozen=True)
class CleanEnvironment:
    home: Path
    codex_home: Path
    provider_attempts_started: int
    environment: Mapping[str, str]
    environment_sha256: str


@dataclass(frozen=True)
class PathIdentity:
    path: Path
    device: int
    inode: int
    uid: int
    gid: int
    mode: int
    nlink: int


@dataclass(frozen=True)
class PreparedGitIdentity:
    run_root: PathIdentity
    provider_cwd: PathIdentity
    git_dir: PathIdentity
    head: str
    tree: str
    provider_pre_tree_sha256: str
    absolute_git_dir: str
    common_git_dir: str


@dataclass(frozen=True)
class CodexRuntime:
    executable: str
    resolved_executable: str
    version: str
    entrypoint_sha256: str
    native_binary_sha256: str
    features_sha256: str
    exec_help_sha256: str


@dataclass(frozen=True)
class FrameworkInitEvidence:
    real_init: bool
    exit_code: int
    stdout_sha256: str
    stderr_sha256: str
    elapsed_seconds: float
    ai_sdlc_commit: str
    runtime_tree_sha: str
    runtime_tree_sha256: str
    provider_attempts_started: int


@dataclass(frozen=True)
class FrontendApprovalEvidence:
    required: bool
    approval_type: str | None
    sequence: tuple[str, ...]
    source_tree_before_approval_sha256: str | None
    source_tree_after_approval_sha256: str | None
    status: str


@dataclass(frozen=True)
class CommandPolicy:
    model: bool = True
    reasoning_effort: bool = True
    json: bool = True
    ephemeral: bool = True
    sandbox: bool = True
    ignore_user_config: bool = True
    ignore_rules: bool = True
    strict_config: bool = True
    forbid_add_dir: bool = True
    network_disabled: bool = True


@dataclass(frozen=True)
class InstructionInventory:
    arm_id: str
    base_global_sha256: str
    prompt_input_sha256: str
    resolved_instruction_chain: tuple[tuple[str, str], ...]
    resolved_instruction_chain_sha256: str
    repo_skills: tuple[str, ...]
    repo_skill_tree_sha256: str | None
    global_skills: tuple[str, ...]
    installed_plugins: tuple[str, ...]
    apps: tuple[str, ...]
    mcp_servers: tuple[str, ...]
    global_rules: tuple[str, ...]
    ai_sdlc_present: bool
    superpowers_present: bool
    issues: tuple[BenchmarkIssue, ...]


@dataclass(frozen=True)
class PreparedArm:
    arm_id: str
    fixture_id: str
    root: Path
    provider_cwd: Path
    provider_cwd_relative: str
    subprocess_cwd: str
    provider_cwd_tree_sha256: str
    provider_pre_tree_sha256: str
    run_root_identity: PathIdentity
    provider_cwd_identity: PathIdentity
    git_dir_identity: PathIdentity
    git_head: str
    git_tree: str
    public_input_sha256: str
    methodology_sha256: str
    base_global_sha256: str
    instruction_inventory_sha256: str
    instruction_inventory_path: Path
    method_instruction_paths: tuple[Path, ...]
    method_instruction_roots: tuple[Path, ...]
    method_surface_sha256: str
    shared_runtime_root: Path | None
    prompt: str
    prompt_sha256: str
    environment: CleanEnvironment
    codex: CodexRuntime
    framework_init: FrameworkInitEvidence
    canonical_pre_state_sha256: str | None
    frontend_approval: FrontendApprovalEvidence
    command_policy: CommandPolicy = field(default_factory=CommandPolicy)


@dataclass(frozen=True)
class ReviewEvidence:
    role: str
    reason: str
    child_session: str
    snapshot_sha256: str
    finding_digest: str
    findings: tuple[Mapping[str, object], ...]
    parent_tree_before: str
    parent_tree_after: str


@dataclass(frozen=True)
class ExpertSnapshot:
    root: Path
    root_identity: PathIdentity
    snapshot_sha256: str
    candidate_sha256: str
    tree_sha256: str


@dataclass(frozen=True)
class BoundSurface:
    name: str
    path: Path
    device: int
    inode: int
    uid: int
    mode: int
    nlink: int
    tree_sha256: str


@dataclass(frozen=True)
class ProductionSurfaceContract:
    sealed_r2: BoundSurface
    sealed_r1: BoundSurface
    sealed_legacy: BoundSurface
    source_r2: BoundSurface
    source_r1: BoundSurface
    disposition: BoundSurface
    control_repo: BoundSurface
    control_gitfile: BoundSurface
    control_gitdir: BoundSurface
    control_common_gitdir: BoundSurface
    raw_results: BoundSurface
    parent_runs: BoundSurface
    other_runs: tuple[BoundSurface, ...]
    fixture_source: BoundSurface
    template: BoundSurface
    runtime_capsule: BoundSurface
    contract_sha256: str


def _surface_tree(path: Path) -> str:
    if path.is_file():
        return _stable_regular_file_sha256(path)
    records = []
    for child in sorted(path.rglob("*"), key=lambda item: item.as_posix()):
        metadata = child.lstat()
        relative = child.relative_to(path).as_posix()
        if stat.S_ISLNK(metadata.st_mode):
            kind = "symlink"
            digest = sha256(os.readlink(child).encode()).hexdigest()
        elif stat.S_ISDIR(metadata.st_mode):
            kind = "directory"
            digest = None
        elif stat.S_ISREG(metadata.st_mode):
            kind = "file"
            digest = _stable_regular_file_sha256(child)
        else:
            raise ValueError("production surface contains an unsupported node")
        records.append(
            {
                "path": relative,
                "kind": kind,
                "mode": stat.S_IMODE(metadata.st_mode),
                "size": metadata.st_size,
                "sha256": digest,
            }
        )
    return sha256(_canonical_bytes(records)).hexdigest()


def _bound_surface(name: str, path: Path) -> BoundSurface:
    absolute = Path(os.path.abspath(path))
    metadata = os.lstat(absolute)
    if stat.S_ISLNK(metadata.st_mode) or not (
        stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)
    ):
        raise ValueError(f"production surface {name} has invalid type")
    return BoundSurface(
        name=name,
        path=absolute,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        uid=metadata.st_uid,
        mode=metadata.st_mode,
        nlink=metadata.st_nlink,
        tree_sha256=_surface_tree(absolute),
    )


def _surface_payload(surface: BoundSurface) -> Mapping[str, object]:
    return {
        "name": surface.name,
        "path": str(surface.path),
        "device": surface.device,
        "inode": surface.inode,
        "uid": surface.uid,
        "mode": surface.mode,
        "nlink": surface.nlink,
        "tree_sha256": surface.tree_sha256,
    }


def build_production_surface_contract(
    *,
    raw_results_root: Path,
    parent_runs_root: Path,
    run_roots: Mapping[str, Path],
    current_run_id: str,
    fixture_source_root: Path,
    template_root: Path,
) -> ProductionSurfaceContract:
    """Derive every protected path from Task 2 authority and a closed 15-run layout."""
    from ai_sdlc.benefit_sealed_materializer import (
        INVALID_R1_ROOT,
        LEGACY_ROOT,
        PRIOR_TRUSTED_SOURCE_ROOT,
        R2_DISPOSITION_ROOT,
        R2_ROOT,
        R2_TRUSTED_SOURCE_ROOT,
    )

    expected_run_ids = {
        f"{arm_id}:{fixture_id}"
        for arm_id in ARM_IDS
        for fixture_id in (
            "requirement-contract-ambiguity",
            "frontend-recovery-delivery",
            "multi-tenant-security-review",
        )
    }
    if set(run_roots) != expected_run_ids or current_run_id not in run_roots:
        raise ValueError("production run surface registry is not the closed matrix")
    normalized_runs = {
        key: Path(os.path.abspath(value)) for key, value in run_roots.items()
    }
    if len(set(normalized_runs.values())) != 15:
        raise ValueError("production run surfaces alias")
    control_gitfile, control_gitdir, control_common = derive_repo_git_surfaces(
        _REPO_ROOT
    )
    commitments = json.loads((R2_ROOT / "candidate-commitments.json").read_text())
    runtime_capsule = Path(str(commitments["evaluator_runtime_capsule"]["root"]))
    named = {
        "sealed_r2": _bound_surface("sealed-r2", R2_ROOT),
        "sealed_r1": _bound_surface("sealed-r1", INVALID_R1_ROOT),
        "sealed_legacy": _bound_surface("sealed-legacy", LEGACY_ROOT),
        "source_r2": _bound_surface("source-r2", R2_TRUSTED_SOURCE_ROOT),
        "source_r1": _bound_surface("source-r1", PRIOR_TRUSTED_SOURCE_ROOT),
        "disposition": _bound_surface("disposition", R2_DISPOSITION_ROOT),
        "control_repo": _bound_surface("control-repo", _REPO_ROOT),
        "control_gitfile": _bound_surface("control-gitfile", control_gitfile),
        "control_gitdir": _bound_surface("control-gitdir", control_gitdir),
        "control_common_gitdir": _bound_surface(
            "control-common-gitdir", control_common
        ),
        "raw_results": _bound_surface("raw-results", raw_results_root),
        "parent_runs": _bound_surface("parent-runs", parent_runs_root),
        "fixture_source": _bound_surface("fixture-source", fixture_source_root),
        "template": _bound_surface("template", template_root),
        "runtime_capsule": _bound_surface("runtime-capsule", runtime_capsule),
    }
    other_runs = tuple(
        _bound_surface(f"other-run:{run_id}", path)
        for run_id, path in sorted(normalized_runs.items())
        if run_id != current_run_id
    )
    if len(other_runs) != 14:
        raise ValueError("production other-run surface count is invalid")
    payload = {value.name: _surface_payload(value) for value in named.values()}
    payload["other_runs"] = [_surface_payload(item) for item in other_runs]
    digest = sha256(_canonical_bytes(payload)).hexdigest()
    return ProductionSurfaceContract(
        **named, other_runs=other_runs, contract_sha256=digest
    )


def verify_production_surface_contract(contract: ProductionSurfaceContract) -> None:
    surfaces = [
        contract.sealed_r2,
        contract.sealed_r1,
        contract.sealed_legacy,
        contract.source_r2,
        contract.source_r1,
        contract.disposition,
        contract.control_repo,
        contract.control_gitfile,
        contract.control_gitdir,
        contract.control_common_gitdir,
        contract.raw_results,
        contract.parent_runs,
        *contract.other_runs,
        contract.fixture_source,
        contract.template,
        contract.runtime_capsule,
    ]
    if len(contract.other_runs) != 14 or len({item.path for item in surfaces}) != len(
        surfaces
    ):
        raise ValueError("production surface contract is incomplete or aliased")
    rebound = [_bound_surface(item.name, item.path) for item in surfaces]
    if rebound != surfaces:
        raise ValueError("production surface identity changed")
    payload = {
        item.name: _surface_payload(item)
        for item in surfaces
        if not item.name.startswith("other-run:")
    }
    payload["other_runs"] = [
        _surface_payload(item)
        for item in surfaces
        if item.name.startswith("other-run:")
    ]
    if sha256(_canonical_bytes(payload)).hexdigest() != contract.contract_sha256:
        raise ValueError("production surface contract digest changed")


def freeze_expert_snapshot(
    root: Path,
    *,
    parent_candidate_root: Path,
    snapshot_sha256: str,
    candidate_sha256: str,
) -> ExpertSnapshot:
    """Freeze one independent, snapshot-only expert CWD."""
    absolute = Path(os.path.abspath(root))
    parent = Path(os.path.abspath(parent_candidate_root))
    if (
        absolute == parent
        or absolute.is_relative_to(parent)
        or parent.is_relative_to(absolute)
        or not _DIGEST.fullmatch(snapshot_sha256)
        or not _DIGEST.fullmatch(candidate_sha256)
    ):
        raise ValueError("expert snapshot root is not independent")
    return ExpertSnapshot(
        root=absolute,
        root_identity=_path_identity(absolute),
        snapshot_sha256=snapshot_sha256,
        candidate_sha256=candidate_sha256,
        tree_sha256=_tree_digest(absolute, exclude_git=False),
    )


def _verify_expert_snapshot(snapshot: ExpertSnapshot) -> None:
    if (
        _path_identity(snapshot.root) != snapshot.root_identity
        or _tree_digest(snapshot.root, exclude_git=False) != snapshot.tree_sha256
    ):
        raise ValueError("expert snapshot identity changed")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _stable_regular_file_sha256(path: Path) -> str:
    """Hash one owner-bound regular file without following or racing links."""
    absolute = Path(os.path.abspath(path))
    before = os.lstat(absolute)
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_nlink != 1
        or absolute.resolve(strict=True) != absolute
    ):
        raise ValueError("bound file metadata is invalid")

    def identity(value: os.stat_result) -> tuple[int, ...]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_uid,
            value.st_gid,
            value.st_mode,
            value.st_nlink,
            value.st_size,
            value.st_ctime_ns,
            value.st_mtime_ns,
        )

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(absolute, flags)
    try:
        opened = os.fstat(descriptor)
        if identity(opened) != identity(before):
            raise ValueError("bound file changed before read")
        digest = sha256()
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        after_path = os.lstat(absolute)
        if identity(after) != identity(opened) or identity(after_path) != identity(
            opened
        ):
            raise ValueError("bound file changed during read")
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _closed(value: object, keys: set[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError(f"{label} is not a closed object")
    return value


def _safe_relative(value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("arm manifest path is invalid")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path == Path("."):
        raise ValueError("arm manifest path is invalid")
    return path


def _file_under(root: Path, relative: str, expected: str) -> Path:
    path = root / _safe_relative(relative)
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise ValueError("arm manifest file escapes its root") from error
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("arm manifest file metadata is invalid")
    if _sha_file(path) != expected:
        raise ValueError("arm manifest file digest is invalid")
    return path


def _parse_manifest(raw: Mapping[str, object], canonical: bytes) -> ArmManifest:
    _closed(raw, _MANIFEST_KEYS, "arm manifest")
    ai = _closed(raw["ai_sdlc"], _AI_SDLC_KEYS, "AI-SDLC binding")
    superpowers = _closed(raw["superpowers"], _SUPERPOWERS_KEYS, "Superpowers binding")
    codex = _closed(raw["codex"], _CODEX_KEYS, "Codex binding")
    _closed(raw["global_inventory"], _GLOBAL_INVENTORY_KEYS, "global inventory")
    _closed(raw["isolation"], _ISOLATION_KEYS, "isolation binding")
    if not isinstance(superpowers["files"], list):
        raise ValueError("arm manifest vendor files are invalid")
    files = []
    for item in superpowers["files"]:
        file_raw = _closed(item, _VENDOR_FILE_KEYS, "vendor file")
        files.append(VendorFile(**file_raw))
    if not isinstance(raw["arms"], list):
        raise ValueError("arm manifest arms are invalid")
    arms = tuple(
        ArmEntry(**_closed(item, _ARM_ENTRY_KEYS, "arm entry")) for item in raw["arms"]
    )
    return ArmManifest(
        schema=str(raw["schema"]),
        arm_ids=tuple(raw["arm_ids"]),
        provider_cwd=str(raw["provider_cwd"]),
        common_agent_contract_path=str(raw["common_agent_contract_path"]),
        common_agent_contract_sha256=str(raw["common_agent_contract_sha256"]),
        prompt_matrix_path=str(raw["prompt_matrix_path"]),
        prompt_matrix_sha256=str(raw["prompt_matrix_sha256"]),
        ai_sdlc_method_surface_manifest_path=str(
            raw["ai_sdlc_method_surface_manifest_path"]
        ),
        ai_sdlc_method_surface_manifest_sha256=str(
            raw["ai_sdlc_method_surface_manifest_sha256"]
        ),
        ai_sdlc_commit=str(ai["peeled_commit"]),
        ai_sdlc_tree=str(ai["source_tree"]),
        stock_agents_source_path=str(ai["stock_agents_source_path"]),
        stock_agents_sha256=str(ai["stock_agents_sha256"]),
        superpowers=SuperpowersBinding(**{**superpowers, "files": tuple(files)}),
        codex=CodexBinding(
            **{
                **codex,
                "capability_off_features": tuple(codex["capability_off_features"]),
                "required_exec_options": tuple(codex["required_exec_options"]),
                "forbidden_exec_options": tuple(codex["forbidden_exec_options"]),
            }
        ),
        global_inventory=raw["global_inventory"],
        isolation=raw["isolation"],
        callback_bridge_path=str(raw["callback_bridge_path"]),
        callback_bridge_sha256=str(raw["callback_bridge_sha256"]),
        arms=arms,
        canonical_bytes=canonical,
    )


def load_arm_manifest(path: Path = _ARMS_ROOT / "manifest.json") -> ArmManifest:
    """Load and immediately verify the closed arm manifest."""
    canonical = path.read_bytes()
    raw = json.loads(canonical)
    if not isinstance(raw, Mapping):
        raise ValueError("arm manifest must be an object")
    manifest = _parse_manifest(raw, canonical)
    issues = validate_arm_manifest(manifest, path.parent)
    if issues:
        raise ValueError("arm manifest is invalid")
    return manifest


def validate_arm_manifest(
    manifest: ArmManifest, arms_root: Path = _ARMS_ROOT
) -> tuple[BenchmarkIssue, ...]:
    """Verify identities, every vendored byte, namespace diff and skill closure."""
    try:
        if (
            manifest.schema != "ai-sdlc-v2-benefit-arm-manifest/v2"
            or manifest.arm_ids != ARM_IDS
            or manifest.provider_cwd != PROVIDER_CWD
            or manifest.ai_sdlc_commit != AI_SDLC_COMMIT
            or manifest.ai_sdlc_tree != AI_SDLC_TREE
            or manifest.superpowers.peeled_commit != SUPERPOWERS_COMMIT
            or manifest.superpowers.source_tree != SUPERPOWERS_TREE
            or manifest.superpowers.tag != "v6.3.0"
            or manifest.superpowers.tag_object_type != "tag"
            or manifest.superpowers.source_url != SUPERPOWERS_SOURCE_URL
            or manifest.superpowers.full_archive_sha256 != SUPERPOWERS_ARCHIVE_SHA256
            or manifest.superpowers.license_sha256 != SUPERPOWERS_LICENSE_SHA256
            or manifest.superpowers.closure_archive_sha256
            != SUPERPOWERS_PROVIDER_TREE_SHA256
            or manifest.superpowers.adaptation_sha256 != SUPERPOWERS_ADAPTATION_SHA256
            or manifest.superpowers.source_inventory_sha256
            != SUPERPOWERS_ADAPTATION_SHA256
            or manifest.superpowers.semantic_adaptation_diff_sha256
            != SUPERPOWERS_SEMANTIC_DIFF_SHA256
            or manifest.superpowers.multi_agent is not False
            or manifest.superpowers.namespace_rewrite
            != {"from": "superpowers:<name>", "to": "$<name>"}
            or manifest.codex.version != CODEX_VERSION
            or manifest.codex.model != "gpt-5.6-sol"
            or manifest.codex.reasoning_effort != "high"
            or manifest.codex.capability_off_features != _CAPABILITY_OFF_FEATURES
        ):
            raise ValueError("frozen arm identity is invalid")
        if tuple(entry.arm_id for entry in manifest.arms) != ARM_IDS:
            raise ValueError("arm manifest order is invalid")
        _file_under(
            arms_root,
            manifest.common_agent_contract_path,
            manifest.common_agent_contract_sha256,
        )
        _file_under(
            arms_root, manifest.prompt_matrix_path, manifest.prompt_matrix_sha256
        )
        _file_under(
            arms_root,
            manifest.ai_sdlc_method_surface_manifest_path,
            manifest.ai_sdlc_method_surface_manifest_sha256,
        )
        prompt_matrix = _closed(
            json.loads((arms_root / manifest.prompt_matrix_path).read_text()),
            {"schema", "runs"},
            "prompt matrix",
        )
        if (
            prompt_matrix["schema"] != "ai-sdlc-v2-benefit-prompt-matrix/v1"
            or not isinstance(prompt_matrix["runs"], list)
            or len(prompt_matrix["runs"]) != 15
        ):
            raise ValueError("prompt matrix binding is invalid")
        common_text = (arms_root / manifest.common_agent_contract_path).read_text(
            encoding="utf-8"
        )
        expected_prompt_rows = []
        for fixture_id in (
            "requirement-contract-ambiguity",
            "frontend-recovery-delivery",
            "multi-tenant-security-review",
        ):
            prompt_digest = sha256(
                _prompt_for(fixture_id, common_text).encode()
            ).hexdigest()
            expected_prompt_rows.extend(
                {
                    "run_id": f"{arm_id}:{fixture_id}",
                    "prompt_sha256": prompt_digest,
                }
                for arm_id in ARM_IDS
            )
        if prompt_matrix["runs"] != expected_prompt_rows:
            raise ValueError("prompt matrix content drifted")
        method_policy = _closed(
            json.loads(
                (arms_root / manifest.ai_sdlc_method_surface_manifest_path).read_text()
            ),
            {"schema", "immutable_roots", "writable_leaves"},
            "AI-SDLC method surface manifest",
        )
        if (
            method_policy["schema"] != "ai-sdlc-v2-benefit-method-surfaces/v1"
            or method_policy["writable_leaves"]
            != manifest.isolation["a_writable_method_leaves"]
            or manifest.isolation["p_s_forbidden_namespaces"]
            != [
                ".ai-sdlc",
                ".agents",
                ".codex",
                "AGENTS.md",
                "AGENTS.override.md",
            ]
        ):
            raise ValueError("method surface policy drifted")
        _file_under(
            arms_root,
            manifest.callback_bridge_path,
            manifest.callback_bridge_sha256,
        )
        _file_under(
            arms_root,
            manifest.superpowers.license_path,
            manifest.superpowers.license_sha256,
        )
        _file_under(
            arms_root,
            manifest.superpowers.adaptation_path,
            manifest.superpowers.adaptation_sha256,
        )
        _file_under(
            arms_root,
            manifest.superpowers.namespace_diff_path,
            manifest.superpowers.namespace_diff_sha256,
        )
        _file_under(
            arms_root,
            manifest.superpowers.semantic_adaptation_diff_path,
            manifest.superpowers.semantic_adaptation_diff_sha256,
        )
        archive_path = _file_under(
            arms_root,
            manifest.superpowers.provenance_archive_path,
            manifest.superpowers.full_archive_sha256,
        )
        _file_under(
            arms_root,
            manifest.superpowers.source_inventory_path,
            manifest.superpowers.source_inventory_sha256,
        )
        source_inventory = _closed(
            json.loads(
                (arms_root / manifest.superpowers.source_inventory_path).read_text()
            ),
            {
                "schema",
                "source_url",
                "tag",
                "tag_object_type",
                "peeled_commit",
                "source_tree",
                "full_archive_sha256",
                "license_sha256",
                "provider_policy",
                "forbidden_reachable_terms",
                "semantic_adaptation_diff_sha256",
                "provider_files",
                "provider_tree_sha256",
            },
            "Superpowers source inventory",
        )
        if (
            source_inventory["schema"]
            != "ai-sdlc-v2-superpowers-single-agent-adaptation/v2"
            or source_inventory["source_url"] != SUPERPOWERS_SOURCE_URL
            or source_inventory["tag"] != "v6.3.0"
            or source_inventory["tag_object_type"] != "tag"
            or source_inventory["peeled_commit"] != SUPERPOWERS_COMMIT
            or source_inventory["source_tree"] != SUPERPOWERS_TREE
            or source_inventory["full_archive_sha256"] != SUPERPOWERS_ARCHIVE_SHA256
            or source_inventory["license_sha256"] != SUPERPOWERS_LICENSE_SHA256
            or source_inventory["provider_tree_sha256"]
            != SUPERPOWERS_PROVIDER_TREE_SHA256
            or source_inventory["semantic_adaptation_diff_sha256"]
            != SUPERPOWERS_SEMANTIC_DIFF_SHA256
            or source_inventory["forbidden_reachable_terms"]
            != ["subagent", "parallel", "dispatch"]
        ):
            raise ValueError("Superpowers source inventory drifted")
        _file_under(
            arms_root,
            manifest.superpowers.activation_agents_path,
            manifest.superpowers.activation_agents_sha256,
        )
        for entry in manifest.arms:
            _file_under(arms_root, entry.config_path, entry.config_sha256)
            if (entry.override_path is None) != (entry.override_sha256 is None):
                raise ValueError("arm override binding is incomplete")
            if entry.override_path is not None and entry.override_sha256 is not None:
                _file_under(arms_root, entry.override_path, entry.override_sha256)
        expected_paths = set()
        skill_names = set()
        provider_root = arms_root / _safe_relative(
            manifest.superpowers.provider_closure_path
        )
        with tarfile.open(archive_path, mode="r:") as archive:
            members = {member.name: member for member in archive.getmembers()}
            for member in members.values():
                archive_path_value = Path(member.name)
                link_path = (
                    Path(member.linkname) if member.issym() or member.islnk() else None
                )
                if (
                    archive_path_value.is_absolute()
                    or ".." in archive_path_value.parts
                    or (
                        link_path is not None
                        and (link_path.is_absolute() or ".." in link_path.parts)
                    )
                ):
                    raise ValueError("Superpowers provenance archive is unsafe")
            for entry in manifest.superpowers.files:
                if (
                    entry.kind != "file"
                    or entry.mode not in {"100644", "100755"}
                    or not _DIGEST.fullmatch(entry.upstream_sha256)
                    or not _DIGEST.fullmatch(entry.adapted_sha256)
                    or isinstance(entry.namespace_replacements, bool)
                    or entry.namespace_replacements < 0
                ):
                    raise ValueError("vendor file binding is invalid")
                path = _file_under(arms_root / "S", entry.path, entry.adapted_sha256)
                path.relative_to(provider_root)
                expected_paths.add(path.relative_to(provider_root).as_posix())
                if path.name == "SKILL.md":
                    skill_names.add(path.parent.name)
                upstream_name = "skills/" + path.relative_to(provider_root).as_posix()
                member = members.get(upstream_name)
                extracted = (
                    archive.extractfile(member)
                    if member is not None and member.isfile()
                    else None
                )
                if (
                    extracted is None
                    or sha256(extracted.read()).hexdigest() != entry.upstream_sha256
                ):
                    raise ValueError("Superpowers upstream file binding drifted")
        actual_paths = {
            path.relative_to(provider_root).as_posix()
            for path in provider_root.rglob("*")
            if path.is_file()
        }
        if actual_paths != expected_paths:
            raise ValueError("vendor closure is not closed")
        if manifest.superpowers.activation_skill not in skill_names:
            raise ValueError("Superpowers activation skill is missing")
        for relative in sorted(expected_paths):
            path = provider_root / relative
            text = path.read_text(encoding="utf-8")
            if "superpowers:" in text:
                raise ValueError("Superpowers namespace rewrite is incomplete")
            lowered = text.lower()
            if any(word in lowered for word in ("subagent", "parallel", "dispatch")):
                raise ValueError("Superpowers reachable closure is not single-agent")
            references = set(_SKILL_REFERENCE.findall(text))
            missing = references - skill_names - _IGNORED_SHELL_REFERENCES
            if missing:
                raise ValueError("Superpowers reference closure is incomplete")
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return (BenchmarkIssue("arms.manifest", "arm manifest is invalid"),)
    return ()


def _tree_digest(root: Path, *, exclude_git: bool = True) -> str:
    records: list[dict[str, object]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root)
        if exclude_git and relative.parts and relative.parts[0] == ".git":
            continue
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError("prepared arm contains a symlink")
        if stat.S_ISDIR(info.st_mode):
            kind = "directory"
            digest = None
        elif stat.S_ISREG(info.st_mode):
            if info.st_nlink != 1:
                raise ValueError("prepared arm contains a hardlink")
            kind = "file"
            digest = _sha_file(path)
        else:
            raise ValueError("prepared arm contains an unsupported entry")
        records.append(
            {
                "path": relative.as_posix(),
                "kind": kind,
                "mode": stat.S_IMODE(info.st_mode),
                "sha256": digest,
            }
        )
    return sha256(_canonical_bytes(records)).hexdigest()


def _source_tree_digest(root: Path) -> str:
    records = []
    for relative in ("src", "tests", "package.json", "package-lock.json"):
        path = root / relative
        if not path.exists():
            continue
        if path.is_file():
            records.append((relative, _sha_file(path)))
        else:
            records.append((relative, _tree_digest(path, exclude_git=False)))
    return sha256(_canonical_bytes(records)).hexdigest()


def _copy_fixture_without_git(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise ValueError("arm destination must be fresh and absent")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.chmod(0o700)
    for item in source.iterdir():
        if item.name == ".git":
            continue
        target = destination / item.name
        if item.is_symlink():
            raise ValueError("fixture contains a symlink")
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)


def _validate_fixture_source(root: Path) -> None:
    """Reject linked, aliased or non-repository fixture inputs before copying bytes."""
    root_info = root.lstat()
    git = root / ".git"
    git_info = git.lstat()
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or stat.S_ISLNK(root_info.st_mode)
        or not stat.S_ISDIR(git_info.st_mode)
        or stat.S_ISLNK(git_info.st_mode)
    ):
        raise ValueError("prepared fixture Git shape is invalid")
    _tree_digest(root)
    _tree_digest(git, exclude_git=False)


def _run_local(
    argv: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    if (
        len(argv) >= 2
        and argv[0].endswith("codex")
        and argv[1] == "exec"
        and "--help" not in argv[2:]
        and "-h" not in argv[2:]
    ):
        raise ValueError("Provider execution is forbidden in Task 3")
    return subprocess.run(
        list(argv),
        cwd=cwd,
        env=dict(environment),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def closed_git_environment() -> dict[str, str]:
    """Return the complete Git environment; inherited Git state is never visible."""
    return {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LC_ALL": "C",
        "LANG": "C",
        **_DETERMINISTIC_GIT_ENV,
    }


def _git(root: Path, *arguments: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        env=closed_git_environment(),
        check=check,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _initialize_single_root_git(root: Path, message: str) -> None:
    for arguments in (
        ("init", "--quiet", "--initial-branch=main", "--template="),
        ("config", "core.autocrlf", "false"),
        ("config", "core.hooksPath", "/dev/null"),
        ("add", "--all"),
        (
            "-c",
            "user.name=AI-SDLC Benchmark Builder",
            "-c",
            "user.email=benchmark@invalid.example",
            "commit",
            "--quiet",
            "--message",
            message,
        ),
    ):
        _git(root, *arguments)


def _amend_single_root_git(root: Path) -> None:
    _git(root, "add", "--all")
    _git(
        root,
        "-c",
        "user.name=AI-SDLC Benchmark Builder",
        "-c",
        "user.email=benchmark@invalid.example",
        "commit",
        "--quiet",
        "--amend",
        "--no-edit",
        "--date=2000-01-01T00:00:00Z",
    )
    if _git(root, "rev-list", "--count", "HEAD") != "1":
        raise ValueError("prepared arm is not a single-root Git repository")
    if _git(root, "status", "--porcelain=v1"):
        raise ValueError("prepared arm Git repository is dirty")


def _path_identity(path: Path) -> PathIdentity:
    absolute = Path(os.path.abspath(path))
    before = os.lstat(absolute)
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise ValueError("prepared directory identity is invalid")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(absolute, flags)
    try:
        opened = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity = (
        opened.st_dev,
        opened.st_ino,
        opened.st_uid,
        opened.st_gid,
        opened.st_mode,
        opened.st_nlink,
    )
    if identity != (
        before.st_dev,
        before.st_ino,
        before.st_uid,
        before.st_gid,
        before.st_mode,
        before.st_nlink,
    ):
        raise ValueError("prepared directory changed during identity read")
    return PathIdentity(
        absolute,
        before.st_dev,
        before.st_ino,
        before.st_uid,
        before.st_gid,
        before.st_mode,
        before.st_nlink,
    )


def _freeze_prepared_git_identity(
    root: Path, provider_cwd: Path
) -> PreparedGitIdentity:
    git_dir = root / ".git"
    absolute_git_dir = _git(root, "rev-parse", "--absolute-git-dir")
    common_git_dir = _git(root, "rev-parse", "--git-common-dir")
    expected_git_dir = str(git_dir.resolve(strict=True))
    resolved_common = str((root / common_git_dir).resolve(strict=True))
    if (
        absolute_git_dir != expected_git_dir
        or resolved_common != expected_git_dir
        or not git_dir.is_dir()
        or git_dir.is_symlink()
        or _git(root, "rev-list", "--count", "HEAD") != "1"
        or _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    ):
        raise ValueError("prepared Git identity is not a clean internal single root")
    return PreparedGitIdentity(
        run_root=_path_identity(root),
        provider_cwd=_path_identity(provider_cwd),
        git_dir=_path_identity(git_dir),
        head=_git(root, "rev-parse", "HEAD"),
        tree=_git(root, "rev-parse", "HEAD^{tree}"),
        provider_pre_tree_sha256=_tree_digest(provider_cwd, exclude_git=False),
        absolute_git_dir=absolute_git_dir,
        common_git_dir=resolved_common,
    )


def verify_prepared_arm_identity(prepared: PreparedArm) -> None:
    """Fail closed if a prepared run, CWD, or internal Git root was replaced."""
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("O_NOFOLLOW is required for prepared identity checks")
    current = _freeze_prepared_git_identity(prepared.root, prepared.provider_cwd)
    expected = (
        prepared.run_root_identity,
        prepared.provider_cwd_identity,
        prepared.git_dir_identity,
        prepared.git_head,
        prepared.git_tree,
        prepared.provider_pre_tree_sha256,
    )
    actual = (
        current.run_root,
        current.provider_cwd,
        current.git_dir,
        current.head,
        current.tree,
        current.provider_pre_tree_sha256,
    )
    if actual != expected:
        raise ValueError("prepared arm identity or expected pre-launch tree changed")


def _ensure_shared_runtime(runtime_root: Path) -> tuple[Path, str]:
    marker = runtime_root / ".runtime-binding.json"
    if runtime_root.exists():
        raw = json.loads(marker.read_text(encoding="utf-8"))
        if raw != {
            "commit": AI_SDLC_COMMIT,
            "tree": AI_SDLC_TREE,
            "tree_sha256": _runtime_content_digest(runtime_root),
        }:
            raise ValueError("shared AI-SDLC runtime binding drifted")
        return runtime_root, str(raw["tree_sha256"])
    runtime_root.parent.mkdir(parents=True, exist_ok=True)
    tree = subprocess.run(
        ["git", "rev-parse", f"{AI_SDLC_COMMIT}^{{tree}}"],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if tree != AI_SDLC_TREE:
        raise ValueError("AI-SDLC source tree binding is unavailable")
    archive = subprocess.run(
        ["git", "archive", "--format=tar", AI_SDLC_COMMIT],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
    ).stdout
    runtime_root.mkdir(mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as handle:
        for member in handle.getmembers():
            relative = Path(member.name)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or member.issym()
                or member.islnk()
            ):
                raise ValueError("AI-SDLC runtime archive is unsafe")
        handle.extractall(runtime_root, filter="data")
    tree_digest = _runtime_content_digest(runtime_root)
    marker.write_bytes(
        _canonical_bytes(
            {"commit": AI_SDLC_COMMIT, "tree": AI_SDLC_TREE, "tree_sha256": tree_digest}
        )
        + b"\n"
    )
    for path in sorted(runtime_root.rglob("*"), reverse=True):
        if path.is_file():
            executable = bool(path.stat().st_mode & stat.S_IXUSR)
            path.chmod(0o555 if executable else 0o444)
        elif path.is_dir():
            path.chmod(0o555)
    runtime_root.chmod(0o555)
    return runtime_root, tree_digest


def _runtime_content_digest(root: Path) -> str:
    """Hash immutable runtime bytes independently of read-only chmod normalization."""
    records = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path == root / ".runtime-binding.json":
            continue
        records.append(
            {
                "path": path.relative_to(root).as_posix(),
                "kind": "directory" if path.is_dir() else "file",
                "sha256": _sha_file(path) if path.is_file() else None,
            }
        )
    return sha256(_canonical_bytes(records)).hexdigest()


def _clean_environment(root: Path) -> CleanEnvironment:
    if root.exists() and any(root.iterdir()):
        raise ValueError("clean benchmark environment root is not empty")
    root.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    home = root / "home"
    codex_home = root / "codex-home"
    home.mkdir(parents=True, mode=0o700)
    codex_home.mkdir(mode=0o700)
    home.chmod(0o700)
    codex_home.chmod(0o700)
    codex_entry, _native = _resolve_codex_binary()
    safe_path = ":".join(
        dict.fromkeys(
            (
                str(codex_entry.parent),
                "/opt/homebrew/bin",
                "/usr/bin",
                "/bin",
                "/usr/sbin",
                "/sbin",
            )
        )
    )
    allowed = {
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
        "AI_SDLC_BENCHMARK_PROVIDER": "forbidden-task3",
        "AI_SDLC_BENCHMARK_SERVICE_SOCKET": str(root / "service.sock"),
    }
    environment_sha256 = sha256(_canonical_bytes(allowed)).hexdigest()
    return CleanEnvironment(home, codex_home, 0, allowed, environment_sha256)


def _copy_methodology(arm_id: str, root: Path, manifest: ArmManifest) -> None:
    if arm_id == "S":
        shutil.copy2(_ARMS_ROOT / "S" / "AGENTS.md", root / "AGENTS.md")
        shutil.copytree(_ARMS_ROOT / "S" / "provider" / ".agents", root / ".agents")
    if arm_id in {"A00", "A10"}:
        entry = manifest.arm(arm_id)
        if entry.override_path is None:
            raise ValueError("arm override is missing")
        shutil.copy2(
            _ARMS_ROOT / entry.override_path,
            root / "benchmark-task" / "AGENTS.override.md",
        )


_CLIENT_SOURCE = '''#!/usr/bin/env python3
"""Method-neutral client for the frozen external intent/approval service."""
import json, os, socket, sys
request=json.loads(sys.stdin.read())
if set(request) not in ({"run_id","question_id"},{"run_id","approval_type","proposal_digest"}):
    raise SystemExit("invalid closed service request")
endpoint=os.environ.get("AI_SDLC_BENCHMARK_SERVICE_SOCKET")
if not endpoint:
    raise SystemExit("benchmark service is unavailable")
payload=json.dumps(request,sort_keys=True,separators=(",",":")).encode()+b"\\n"
with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
    client.connect(endpoint); client.sendall(payload)
    response=client.makefile("rb").readline()
sys.stdout.buffer.write(response)
'''


def _install_common_client(root: Path) -> None:
    target = root / "benchmark-task" / ".benchmark" / "intent-approval-client.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_CLIENT_SOURCE, encoding="utf-8")
    target.chmod(0o555)


def _run_real_init(
    root: Path, runtime: Path, environment: CleanEnvironment, runtime_tree_sha256: str
) -> FrameworkInitEvidence:
    env = {
        **environment.environment,
        "PYTHONPATH": str(runtime / "src"),
    }
    started = time.monotonic()
    result = _run_local(
        [
            sys.executable,
            "-m",
            "ai_sdlc",
            "init",
            str(root),
            "--agent-target",
            "codex",
            "--shell",
            "powershell",
        ],
        cwd=root,
        environment=env,
        timeout=180,
    )
    elapsed = time.monotonic() - started
    if result.returncode != 0:
        raise ValueError(
            "real AI-SDLC init failed: "
            + (result.stderr[-1000:] or result.stdout[-1000:])
        )
    agents = root / "AGENTS.md"
    if (
        not agents.is_file()
        or _sha_file(agents) != load_arm_manifest().stock_agents_sha256
    ):
        raise ValueError("real AI-SDLC init did not install the exact stock adapter")
    return FrameworkInitEvidence(
        real_init=True,
        exit_code=result.returncode,
        stdout_sha256=sha256(result.stdout.encode()).hexdigest(),
        stderr_sha256=sha256(result.stderr.encode()).hexdigest(),
        elapsed_seconds=elapsed,
        ai_sdlc_commit=AI_SDLC_COMMIT,
        runtime_tree_sha=AI_SDLC_TREE,
        runtime_tree_sha256=runtime_tree_sha256,
        provider_attempts_started=0,
    )


def _no_framework_init() -> FrameworkInitEvidence:
    empty = sha256(b"").hexdigest()
    return FrameworkInitEvidence(
        real_init=False,
        exit_code=0,
        stdout_sha256=empty,
        stderr_sha256=empty,
        elapsed_seconds=0.0,
        ai_sdlc_commit=AI_SDLC_COMMIT,
        runtime_tree_sha=AI_SDLC_TREE,
        runtime_tree_sha256=empty,
        provider_attempts_started=0,
    )


def _resolve_codex_binary() -> tuple[Path, Path]:
    entrypoint = shutil.which("codex")
    if entrypoint is None:
        raise ValueError("Codex 0.147.0 is unavailable")
    entry = Path(entrypoint)
    package_root = entry.resolve().parents[1]
    candidates = sorted(
        package_root.glob("node_modules/@openai/codex-*/vendor/*/bin/codex")
    )
    if len(candidates) != 1:
        raise ValueError("Codex native binary identity is ambiguous")
    return entry, candidates[0]


def _capability_arguments() -> list[str]:
    result: list[str] = []
    for feature in _CAPABILITY_OFF_FEATURES:
        result.extend(["--disable", feature])
    return result


def _codex_local_probe(
    prepared_root: Path,
    environment: CleanEnvironment,
    manifest: ArmManifest,
    prompt: str,
) -> tuple[CodexRuntime, Mapping[str, object]]:
    entry, native = _resolve_codex_binary()
    if _sha_file(entry) != manifest.codex.entrypoint_sha256:
        raise ValueError("Codex entrypoint digest drifted")
    if _sha_file(native) != manifest.codex.native_binary_sha256:
        raise ValueError("Codex native binary digest drifted")
    version = _run_local(
        [str(entry), "--version"],
        cwd=prepared_root,
        environment=environment.environment,
    )
    if version.returncode != 0 or version.stdout.strip() != "codex-cli 0.147.0":
        raise ValueError("Codex version drifted")
    exec_help = _run_local(
        [str(entry), "exec", "--help"],
        cwd=prepared_root,
        environment=environment.environment,
    )
    if (
        exec_help.returncode != 0
        or sha256(exec_help.stdout.encode()).hexdigest()
        != manifest.codex.exec_help_sha256
    ):
        raise ValueError("Codex exec tool surface drifted")
    for option in manifest.codex.required_exec_options:
        if option not in exec_help.stdout:
            raise ValueError("Codex required command option is unavailable")
    features = _run_local(
        [str(entry), *_capability_arguments(), "features", "list"],
        cwd=prepared_root,
        environment=environment.environment,
    )
    if (
        features.returncode != 0
        or sha256(features.stdout.encode()).hexdigest()
        != manifest.codex.features_sha256
    ):
        raise ValueError("Codex feature surface drifted")
    for feature in _CAPABILITY_OFF_FEATURES:
        line = next(
            (
                item
                for item in features.stdout.splitlines()
                if item.split()[:1] == [feature]
            ),
            None,
        )
        if line is None or line.split()[-1] != "false":
            raise ValueError("Codex capability-off flag did not take effect")
    plugins = _run_local(
        [str(entry), *_capability_arguments(), "plugin", "list", "--json"],
        cwd=prepared_root,
        environment=environment.environment,
    )
    mcp = _run_local(
        [str(entry), *_capability_arguments(), "mcp", "list", "--json"],
        cwd=prepared_root,
        environment=environment.environment,
    )
    prompt_input = _run_local(
        [
            str(entry),
            "-C",
            str(prepared_root / "benchmark-task"),
            *_capability_arguments(),
            "debug",
            "prompt-input",
            prompt,
        ],
        cwd=prepared_root / "benchmark-task",
        environment=environment.environment,
    )
    if any(item.returncode != 0 for item in (plugins, mcp, prompt_input)):
        raise ValueError("Codex local inventory probe failed closed")
    plugin_json = json.loads(plugins.stdout)
    mcp_json = json.loads(mcp.stdout)
    prompt_json = json.loads(prompt_input.stdout)
    normalized_prompt = _normalize_prompt_input(
        prompt_json,
        replacements=(
            (str(prepared_root.resolve()), "<RUN_ROOT>"),
            (str(environment.home.resolve()), "<HOME>"),
            (str(environment.codex_home.resolve()), "<CODEX_HOME>"),
        ),
    )
    prompt_text = "\n".join(_string_leaves(prompt_json))
    skill_entries = re.findall(
        r"^- ([a-zA-Z0-9_-]+): .*?\(file: ([^\n)]+/SKILL\.md)\)$",
        prompt_text,
        re.M,
    )
    global_skill_names: set[str] = set()
    repo_skill_names: set[str] = set()
    unexpected_skill_sources: list[str] = []
    global_skills_root = environment.codex_home / "skills"
    repo_skills_root = prepared_root / ".agents" / "skills"
    for name, source in skill_entries:
        source_path = Path(source)
        try:
            source_path.relative_to(global_skills_root)
            global_skill_names.add(name)
            continue
        except ValueError:
            pass
        try:
            source_path.relative_to(repo_skills_root)
            repo_skill_names.add(name)
            continue
        except ValueError:
            unexpected_skill_sources.append(source)
    global_skills = tuple(sorted(global_skill_names))
    repo_prompt_skills = tuple(sorted(repo_skill_names))
    expected_global = tuple(manifest.global_inventory["builtin_skills"])
    if global_skills != expected_global or unexpected_skill_sources:
        raise ValueError("Codex global skill inventory drifted")
    installed = tuple(
        sorted(
            item.get("id", item.get("name", ""))
            for item in plugin_json.get("installed", [])
            if isinstance(item, Mapping)
        )
    )
    mcp_names = tuple(
        sorted(item.get("name", "") for item in mcp_json if isinstance(item, Mapping))
    )
    if installed or mcp_names:
        raise ValueError("Codex clean home contains plugins or MCP servers")
    base = {
        "codex_version": CODEX_VERSION,
        "entrypoint_sha256": manifest.codex.entrypoint_sha256,
        "native_binary_sha256": manifest.codex.native_binary_sha256,
        "exec_help_sha256": manifest.codex.exec_help_sha256,
        "features_sha256": manifest.codex.features_sha256,
        "capability_off_features": list(_CAPABILITY_OFF_FEATURES),
        "global_skills": list(global_skills),
        "installed_plugins": list(installed),
        "apps": [],
        "mcp_servers": list(mcp_names),
        "global_rules": [],
        "active_features": {
            line.split()[0]: line.split()[-1]
            for line in features.stdout.splitlines()
            if len(line.split()) >= 3
        },
    }
    lowered = json.dumps(
        {
            "base": base,
            "global_skill_sources": [
                Path(source).relative_to(global_skills_root).as_posix()
                for name, source in skill_entries
                if name in global_skill_names
            ],
        },
        sort_keys=True,
    ).lower()
    if any(marker in lowered for marker in _METHODOLOGY_MARKERS):
        raise ValueError("global methodology contamination detected")
    runtime = CodexRuntime(
        executable=str(entry),
        resolved_executable=str(native),
        version=CODEX_VERSION,
        entrypoint_sha256=manifest.codex.entrypoint_sha256,
        native_binary_sha256=manifest.codex.native_binary_sha256,
        features_sha256=manifest.codex.features_sha256,
        exec_help_sha256=manifest.codex.exec_help_sha256,
    )
    return runtime, {
        "base": base,
        "base_sha256": sha256(_canonical_bytes(base)).hexdigest(),
        "prompt_input_sha256": sha256(_canonical_bytes(normalized_prompt)).hexdigest(),
        "global_skills": global_skills,
        "repo_prompt_skills": repo_prompt_skills,
        "installed_plugins": installed,
        "mcp_servers": mcp_names,
        "exec_help_sha256": sha256(exec_help.stdout.encode()).hexdigest(),
        "features_sha256": sha256(features.stdout.encode()).hexdigest(),
    }


def _normalize_prompt_input(
    value: object, *, replacements: Sequence[tuple[str, str]] = ()
) -> object:
    if isinstance(value, list):
        return [
            _normalize_prompt_input(item, replacements=replacements) for item in value
        ]
    if isinstance(value, Mapping):
        return {
            key: _normalize_prompt_input(item, replacements=replacements)
            for key, item in sorted(value.items())
            if key not in {"id", "internal_chat_message_metadata_passthrough"}
        }
    if isinstance(value, str):
        for source, target in replacements:
            value = value.replace(source, target)
    return value


def _string_leaves(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(text for item in value for text in _string_leaves(item))
    if isinstance(value, Mapping):
        return tuple(text for item in value.values() for text in _string_leaves(item))
    return ()


def _prompt_for(fixture_id: str, common_contract: str) -> str:
    """Build the identical public user prompt used by every arm for one fixture."""
    return (
        "\n".join(
            [
                common_contract.rstrip(),
                "",
                f"Frozen fixture: {fixture_id}; provider_cwd={PROVIDER_CWD}",
                "Read input-contract.json and complete only its target stage.",
            ]
        )
        + "\n"
    )


def _instruction_chain(root: Path, arm_id: str) -> tuple[tuple[str, str], ...]:
    chain = []
    root_agents = root / "AGENTS.md"
    if root_agents.is_file():
        chain.append(("AGENTS.md", _sha_file(root_agents)))
    override = root / "benchmark-task" / "AGENTS.override.md"
    if override.is_file():
        chain.append(("benchmark-task/AGENTS.override.md", _sha_file(override)))
    expected = {"P": 0, "S": 1, "A00": 2, "A10": 2, "A11": 1}[arm_id]
    if len(chain) != expected:
        raise ValueError("resolved instruction chain is invalid")
    return tuple(chain)


def _freeze_method_instruction_files(
    root: Path, arm_id: str
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    paths = [root / relative for relative, _digest in _instruction_chain(root, arm_id)]
    skills = root / ".agents" / "skills"
    if skills.is_dir():
        paths.extend(path for path in skills.rglob("*") if path.is_file())
    client = root / "benchmark-task" / ".benchmark" / "intent-approval-client.py"
    paths.append(client)
    unique = tuple(dict.fromkeys(path.resolve(strict=True) for path in paths))
    instruction_roots = [client.parent]
    if skills.is_dir():
        instruction_roots.append(skills)
    if arm_id.startswith("A"):
        policy = json.loads(
            (_ARMS_ROOT / "ai-sdlc-method-surfaces.json").read_text(encoding="utf-8")
        )
        for relative in policy["immutable_roots"]:
            surface = root / relative
            if surface.is_dir():
                instruction_roots.append(surface)
            elif surface.is_file():
                paths.append(surface)
            else:
                raise ValueError("AI-SDLC immutable method surface is missing")
    else:
        for relative in (".agents", ".codex", ".ai-sdlc"):
            surface = root / relative
            if surface.is_dir():
                instruction_roots.append(surface)
    roots = tuple(
        dict.fromkeys(path.resolve(strict=True) for path in instruction_roots)
    )
    for path in unique:
        executable = bool(path.stat().st_mode & stat.S_IXUSR)
        path.chmod(0o555 if executable else 0o444)
    for directory in sorted(
        (
            item
            for protected_root in roots
            for item in (protected_root, *protected_root.rglob("*"))
            if item.is_dir()
        ),
        reverse=True,
    ):
        directory.chmod(0o555)
    return unique, roots


def _method_surface_sha256(root: Path, arm_id: str) -> str:
    """Bind static method namespaces while excluding only closed Loop output leaves."""
    if arm_id in {"P", "S"}:
        records: list[dict[str, object]] = []
        for base_name, base in (("root", root), ("provider", root / "benchmark-task")):
            for relative in (
                ".ai-sdlc",
                ".agents",
                ".codex",
                "AGENTS.md",
                "AGENTS.override.md",
            ):
                path = base / relative
                if not path.exists():
                    continue
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode):
                    raise ValueError("method namespace contains a symlink")
                records.append(
                    {
                        "path": f"{base_name}/{relative}",
                        "kind": "directory" if path.is_dir() else "file",
                        "sha256": (
                            _tree_digest(path, exclude_git=False)
                            if path.is_dir()
                            else _sha_file(path)
                        ),
                    }
                )
        return sha256(_canonical_bytes(records)).hexdigest()

    ai_sdlc = root / ".ai-sdlc"
    records = []
    for path in sorted(ai_sdlc.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root)
        parts = relative.parts
        allowed_state = len(parts) >= 3 and parts[:3] == (
            ".ai-sdlc",
            "state",
            "loop",
        )
        allowed_work_item = parts[:2] == (".ai-sdlc", "work-items") and (
            len(parts) == 3 or (len(parts) >= 4 and parts[3] in {"loop", "outputs"})
        )
        if allowed_state or allowed_work_item:
            continue
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError("AI-SDLC method surface contains a symlink")
        if stat.S_ISDIR(info.st_mode):
            kind = "directory"
            digest = None
        elif stat.S_ISREG(info.st_mode):
            kind = "file"
            digest = _sha_file(path)
        else:
            raise ValueError("AI-SDLC method surface contains an unsupported entry")
        records.append(
            {
                "path": relative.as_posix(),
                "kind": kind,
                "mode": stat.S_IMODE(info.st_mode),
                "sha256": digest,
            }
        )
    return sha256(_canonical_bytes(records)).hexdigest()


def prepare_arm(
    arm_id: str,
    fixture: PreparedFixture,
    destination: Path,
    *,
    shared_runtime_root: Path | None = None,
    environment_root: Path | None = None,
) -> PreparedArm:
    """Prepare one fresh arm workspace without launching or reserving a Provider."""
    manifest = load_arm_manifest()
    if arm_id not in ARM_IDS:
        raise ValueError("arm id is not frozen")
    if not fixture.root.is_dir() or not (fixture.root / ".git").is_dir():
        raise ValueError("prepared fixture is invalid")
    _validate_fixture_source(fixture.root)
    _copy_fixture_without_git(fixture.root, destination)
    _initialize_single_root_git(
        destination, f"benchmark: {arm_id} {fixture.fixture_id}"
    )
    environment = _clean_environment(
        environment_root or destination.parent / f".{destination.name}-environment"
    )
    _install_common_client(destination)
    runtime_tree_sha256 = sha256(b"").hexdigest()
    shared_runtime: Path | None = None
    if arm_id.startswith("A"):
        runtime, runtime_tree_sha256 = _ensure_shared_runtime(
            shared_runtime_root or destination.parent / ".ai-sdlc-v2-runtime"
        )
        shared_runtime = runtime
        framework_init = _run_real_init(
            destination, runtime, environment, runtime_tree_sha256
        )
        canonical = build_canonical_pre_state(
            fixture.fixture_id,
            destination,
            destination / ".ai-sdlc" / "benchmark",
        )
        canonical_path = (
            destination / ".ai-sdlc" / "benchmark" / "canonical-pre-state.json"
        )
        canonical_digest = _sha_file(canonical_path)
        public = json.loads(
            (destination / "benchmark-task" / "input-contract.json").read_text(
                encoding="utf-8"
            )
        )
        if normalized_semantic_view(canonical) != normalized_semantic_view(public):
            raise ValueError("A-arm canonical pre-state adds hidden semantics")
    else:
        framework_init = _no_framework_init()
        canonical_digest = None
    _copy_methodology(arm_id, destination, manifest)
    source_digest = _source_tree_digest(destination / "benchmark-task")
    frontend = fixture.fixture_id == "frontend-recovery-delivery"
    frontend_approval = FrontendApprovalEvidence(
        required=frontend and (arm_id.startswith("A") or arm_id == "S"),
        approval_type="frontend-solution" if frontend else None,
        sequence=(
            ("proposal", "approval-request", "approved", "implementation")
            if frontend and arm_id == "S"
            else (
                (
                    "solution-confirm-dry-run",
                    "approval-request",
                    "solution-confirm-execute",
                    "implementation",
                )
                if frontend and arm_id.startswith("A")
                else ()
            )
        ),
        source_tree_before_approval_sha256=source_digest if frontend else None,
        source_tree_after_approval_sha256=None,
        status="pending"
        if frontend and (arm_id.startswith("A") or arm_id == "S")
        else "not-applicable",
    )
    _amend_single_root_git(destination)
    method_instruction_paths, method_instruction_roots = (
        _freeze_method_instruction_files(destination, arm_id)
    )
    method_surface_sha256 = _method_surface_sha256(destination, arm_id)
    prompt = _prompt_for(
        fixture.fixture_id,
        (_ARMS_ROOT / manifest.common_agent_contract_path).read_text(encoding="utf-8"),
    )
    prompt_sha256 = sha256(prompt.encode()).hexdigest()
    runtime_binding, probe = _codex_local_probe(
        destination, environment, manifest, prompt
    )
    chain = _instruction_chain(destination, arm_id)
    repo_skills = (
        tuple(
            sorted(
                path.parent.name
                for path in (destination / ".agents" / "skills").glob("*/SKILL.md")
            )
        )
        if (destination / ".agents" / "skills").is_dir()
        else ()
    )
    methodology = {
        "arm": arm_id,
        "chain": chain,
        "repo_skills": repo_skills,
        "config_sha256": manifest.arm(arm_id).config_sha256,
        "callback_bridge_sha256": (
            manifest.callback_bridge_sha256 if arm_id == "A11" else None
        ),
    }
    inventory_payload = {
        "schema": "ai-sdlc-v2-benefit-instruction-inventory/v1",
        "arm_id": arm_id,
        "fixture_id": fixture.fixture_id,
        "provider_cwd": PROVIDER_CWD,
        "resolved_instruction_chain": [
            {"path": path, "sha256": digest} for path, digest in chain
        ],
        "repo_skills": list(repo_skills),
        "repo_skill_tree_sha256": (
            _tree_digest(destination / ".agents" / "skills", exclude_git=False)
            if repo_skills
            else None
        ),
        "base_global": probe["base"],
        "base_global_sha256": probe["base_sha256"],
        "prompt_sha256": prompt_sha256,
        "codex_prompt_input_sha256": probe["prompt_input_sha256"],
        "method_instruction_files": [
            {
                "path": (
                    path.relative_to(destination).as_posix()
                    if path.is_relative_to(destination)
                    else "shared-runtime"
                ),
                "sha256": _sha_file(path),
            }
            for path in method_instruction_paths
        ],
        "method_instruction_roots": [
            {
                "path": path.relative_to(destination).as_posix(),
                "tree_sha256": _tree_digest(path, exclude_git=False),
            }
            for path in method_instruction_roots
        ],
        "method_surface_sha256": method_surface_sha256,
        "provider_attempts_started": 0,
    }
    inventory_path = environment.home.parent / "instruction-inventory.json"
    inventory_path.write_bytes(_canonical_bytes(inventory_payload) + b"\n")
    inventory_path.chmod(0o600)
    git_identity = _freeze_prepared_git_identity(
        destination, destination / "benchmark-task"
    )
    prepared = PreparedArm(
        arm_id=arm_id,
        fixture_id=fixture.fixture_id,
        root=destination,
        provider_cwd=destination / "benchmark-task",
        provider_cwd_relative=PROVIDER_CWD,
        subprocess_cwd=str(destination / "benchmark-task"),
        provider_cwd_tree_sha256=_tree_digest(
            destination / "benchmark-task", exclude_git=False
        ),
        provider_pre_tree_sha256=git_identity.provider_pre_tree_sha256,
        run_root_identity=git_identity.run_root,
        provider_cwd_identity=git_identity.provider_cwd,
        git_dir_identity=git_identity.git_dir,
        git_head=git_identity.head,
        git_tree=git_identity.tree,
        public_input_sha256=_sha_file(
            destination / "benchmark-task" / "input-contract.json"
        ),
        methodology_sha256=sha256(_canonical_bytes(methodology)).hexdigest(),
        base_global_sha256=str(probe["base_sha256"]),
        instruction_inventory_sha256=prompt_sha256,
        instruction_inventory_path=inventory_path,
        method_instruction_paths=method_instruction_paths,
        method_instruction_roots=method_instruction_roots,
        method_surface_sha256=method_surface_sha256,
        shared_runtime_root=shared_runtime,
        prompt=prompt,
        prompt_sha256=prompt_sha256,
        environment=environment,
        codex=runtime_binding,
        framework_init=framework_init,
        canonical_pre_state_sha256=canonical_digest,
        frontend_approval=frontend_approval,
    )
    inventory = _inspect_instruction_sources(prepared, reprobe_global=False)
    if inventory.issues:
        raise ValueError("prepared arm instruction inventory is invalid")
    return prepared


def inspect_instruction_sources(prepared: PreparedArm) -> InstructionInventory:
    """Recompute the complete project and clean-global instruction inventory."""
    return _inspect_instruction_sources(prepared, reprobe_global=True)


def _inspect_instruction_sources(
    prepared: PreparedArm, *, reprobe_global: bool
) -> InstructionInventory:
    issues: list[BenchmarkIssue] = []
    try:
        chain = _instruction_chain(prepared.root, prepared.arm_id)
    except (OSError, ValueError):
        chain = ()
        issues.append(
            BenchmarkIssue(
                "arms.instruction-namespace-drift",
                "an unallowlisted instruction or methodology namespace exists",
            )
        )
    chain_digest = sha256(_canonical_bytes(chain)).hexdigest()
    skills_root = prepared.root / ".agents" / "skills"
    repo_skills = (
        tuple(sorted(path.parent.name for path in skills_root.glob("*/SKILL.md")))
        if skills_root.is_dir()
        else ()
    )
    skill_digest = _tree_digest(skills_root, exclude_git=False) if repo_skills else None
    ai_sdlc_present = (prepared.root / ".ai-sdlc").is_dir()
    superpowers_present = skills_root.exists()
    try:
        metadata = prepared.instruction_inventory_path.lstat()
        stored = json.loads(prepared.instruction_inventory_path.read_bytes())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or stored["base_global_sha256"] != prepared.base_global_sha256
            or stored["prompt_sha256"] != prepared.prompt_sha256
            or prepared.prompt_sha256 != sha256(prepared.prompt.encode()).hexdigest()
            or sha256(_canonical_bytes(stored["base_global"])).hexdigest()
            != prepared.base_global_sha256
            or stored["repo_skills"] != list(repo_skills)
            or stored["repo_skill_tree_sha256"] != skill_digest
        ):
            raise ValueError("stored instruction inventory changed")
        if reprobe_global:
            _runtime, current = _codex_local_probe(
                prepared.root,
                prepared.environment,
                load_arm_manifest(),
                prepared.prompt,
            )
            if (
                current["base_sha256"] != prepared.base_global_sha256
                or current["prompt_input_sha256"] != stored["codex_prompt_input_sha256"]
                or tuple(current["repo_prompt_skills"]) != repo_skills
            ):
                raise ValueError("actual Codex instruction inventory drifted")
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        issues.append(
            BenchmarkIssue(
                "arms.global-inventory-drift",
                "actual prompt/tool/global instruction inventory changed",
            )
        )
    allowed_instruction_files = {"AGENTS.md"} if prepared.arm_id != "P" else set()
    if prepared.arm_id in {"A00", "A10"}:
        allowed_instruction_files.add("benchmark-task/AGENTS.override.md")
    actual_instruction_files = {
        path.relative_to(prepared.root).as_posix()
        for path in prepared.root.rglob("AGENTS*.md")
        if ".git" not in path.relative_to(prepared.root).parts
    }
    allowed_method_dirs = {
        ".agents" if prepared.arm_id == "S" else None,
        ".ai-sdlc" if prepared.arm_id.startswith("A") else None,
        "benchmark-task/.benchmark",
    }
    allowed_method_dirs.discard(None)
    reserved_names = {".agents", ".ai-sdlc", ".codex", ".claude", ".plugins", ".mcp"}
    unexpected_method_dirs = []
    for path in prepared.root.rglob("*"):
        if not path.is_dir() or path.name not in reserved_names:
            continue
        relative = path.relative_to(prepared.root).as_posix()
        if relative not in allowed_method_dirs:
            unexpected_method_dirs.append(relative)
    unexpected_skills = {
        path.relative_to(prepared.root).as_posix()
        for path in prepared.root.rglob("SKILL.md")
        if not path.is_relative_to(skills_root)
    }
    if (
        actual_instruction_files != allowed_instruction_files
        or unexpected_method_dirs
        or unexpected_skills
    ):
        issues.append(
            BenchmarkIssue(
                "arms.instruction-namespace-drift",
                "an unallowlisted instruction or methodology namespace exists",
            )
        )
    if prepared.arm_id == "P" and (ai_sdlc_present or superpowers_present):
        issues.append(
            BenchmarkIssue("arms.p-contamination", "P methodology contamination")
        )
    if prepared.arm_id == "S" and (ai_sdlc_present or not superpowers_present):
        issues.append(
            BenchmarkIssue("arms.s-contamination", "S methodology isolation failed")
        )
    if prepared.arm_id.startswith("A") and (not ai_sdlc_present or superpowers_present):
        issues.append(
            BenchmarkIssue("arms.a-contamination", "A methodology isolation failed")
        )
    if prepared.arm_id == "S" and "using-superpowers" not in repo_skills:
        issues.append(
            BenchmarkIssue("arms.s-activation", "S activation skill is missing")
        )
    global_skills = tuple(load_arm_manifest().global_inventory["builtin_skills"])
    inventory = InstructionInventory(
        arm_id=prepared.arm_id,
        base_global_sha256=prepared.base_global_sha256,
        prompt_input_sha256=prepared.instruction_inventory_sha256,
        resolved_instruction_chain=chain,
        resolved_instruction_chain_sha256=chain_digest,
        repo_skills=repo_skills,
        repo_skill_tree_sha256=skill_digest,
        global_skills=global_skills,
        installed_plugins=(),
        apps=(),
        mcp_servers=(),
        global_rules=(),
        ai_sdlc_present=ai_sdlc_present,
        superpowers_present=superpowers_present,
        issues=tuple(issues),
    )
    return inventory


def verify_method_instruction_immutability(
    prepared: PreparedArm,
) -> tuple[BenchmarkIssue, ...]:
    """Recheck every hash-bound readable instruction before reservation or Close."""
    try:
        inventory = json.loads(prepared.instruction_inventory_path.read_text())
        expected = inventory["method_instruction_files"]
        expected_roots = inventory["method_instruction_roots"]
        expected_surface = inventory["method_surface_sha256"]
        if (
            not isinstance(expected, list)
            or len(expected) != len(prepared.method_instruction_paths)
            or not isinstance(expected_roots, list)
            or len(expected_roots) != len(prepared.method_instruction_roots)
            or expected_surface != prepared.method_surface_sha256
            or _method_surface_sha256(prepared.root, prepared.arm_id)
            != expected_surface
        ):
            raise ValueError("instruction inventory coverage changed")
        by_relative = {
            (
                path.relative_to(prepared.root).as_posix()
                if path.is_relative_to(prepared.root)
                else "shared-runtime"
            ): path
            for path in prepared.method_instruction_paths
        }
        for item in expected:
            if (
                not isinstance(item, Mapping)
                or set(item) != {"path", "sha256"}
                or item["path"] not in by_relative
                or _sha_file(by_relative[str(item["path"])]) != item["sha256"]
            ):
                raise ValueError("instruction file changed")
        roots_by_relative = {
            path.relative_to(prepared.root).as_posix(): path
            for path in prepared.method_instruction_roots
        }
        for item in expected_roots:
            if (
                not isinstance(item, Mapping)
                or set(item) != {"path", "tree_sha256"}
                or item["path"] not in roots_by_relative
                or _tree_digest(roots_by_relative[str(item["path"])], exclude_git=False)
                != item["tree_sha256"]
            ):
                raise ValueError("instruction namespace changed")
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return (
            BenchmarkIssue(
                "arms.instruction-mutation", "method instruction binding changed"
            ),
        )
    return ()


def build_arm_isolation_profile(
    prepared: PreparedArm,
    reservation: AttemptReservation,
    *,
    surfaces: ProductionSurfaceContract,
    output_schema: Path | None = None,
    expert_snapshot: ExpertSnapshot | None = None,
) -> ProviderIsolationProfile:
    """Bind Task 2's strong Seatbelt profile to an exact arm command and surfaces."""
    verify_production_surface_contract(surfaces)
    verify_prepared_arm_identity(prepared)
    if verify_method_instruction_immutability(prepared):
        raise ValueError("method instruction immutability check failed")
    command = build_codex_command(
        prepared,
        reservation,
        output_schema=output_schema,
        expert_snapshot=expert_snapshot,
    )
    write_protected = [
        *prepared.method_instruction_paths,
        *prepared.method_instruction_roots,
    ]
    if prepared.shared_runtime_root is not None:
        write_protected.append(prepared.shared_runtime_root)
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
        verify_prepared_arm_identity(prepared)
        verify_production_surface_contract(surfaces)
        if verify_method_instruction_immutability(prepared):
            raise ValueError("method instruction immutability check failed")
        if (
            prepared.environment.environment_sha256
            != sha256(
                _canonical_bytes(dict(prepared.environment.environment))
            ).hexdigest()
        ):
            raise ValueError("launch environment binding changed")

    protected = (
        surfaces.sealed_r1.path,
        surfaces.sealed_legacy.path,
        surfaces.source_r2.path,
        surfaces.source_r1.path,
        surfaces.disposition.path,
        surfaces.control_gitfile.path,
        surfaces.control_gitdir.path,
        surfaces.control_common_gitdir.path,
        surfaces.parent_runs.path,
        surfaces.fixture_source.path,
        surfaces.template.path,
        _ARMS_ROOT,
    )
    profile = build_provider_isolation_profile(
        run_root=prepared.provider_cwd,
        sealed_root=surfaces.sealed_r2.path,
        control_root=surfaces.control_repo.path,
        raw_results_root=surfaces.raw_results.path,
        protected_roots=tuple(dict.fromkeys(protected)),
        write_protected_roots=tuple(
            dict.fromkeys((*write_protected, surfaces.runtime_capsule.path))
        ),
        missing_write_protected_paths=tuple(missing_method_paths),
        other_run_roots=tuple(item.path for item in surfaces.other_runs),
        argv=command,
        environment=prepared.environment.environment,
        preserve_environment=True,
        launch_guard=launch_guard,
    )
    if profile.issues:
        codes = ",".join(f"{issue.code}:{issue.message}" for issue in profile.issues)
        raise ValueError(f"arm isolation profile is not executable: {codes}")
    return profile


def build_codex_command(
    prepared: PreparedArm,
    reservation: AttemptReservation,
    *,
    output_schema: Path | None = None,
    expert_snapshot: ExpertSnapshot | None = None,
) -> list[str]:
    """Construct, validate and return argv; this function never launches a subprocess."""
    policy = prepared.command_policy
    verify_prepared_arm_identity(prepared)
    if prepared.prompt_sha256 != sha256(prepared.prompt.encode()).hexdigest():
        raise ValueError("prepared prompt binding changed")
    if Path(prepared.subprocess_cwd).resolve() != prepared.provider_cwd.resolve():
        raise ValueError("provider cwd and subprocess cwd disagree")
    if (
        prepared.provider_cwd.stat().st_ino
        != Path(prepared.subprocess_cwd).stat().st_ino
    ):
        raise ValueError("provider cwd inode changed")
    if reservation.request.run_id != f"{prepared.arm_id}:{prepared.fixture_id}" or (
        reservation.request.arm is not None
        and reservation.request.arm != prepared.arm_id
    ):
        raise ValueError("reservation is not bound to the prepared arm")
    if not all(
        (
            policy.model,
            policy.reasoning_effort,
            policy.json,
            policy.ephemeral,
            policy.sandbox,
            policy.ignore_user_config,
            policy.ignore_rules,
            policy.strict_config,
            policy.forbid_add_dir,
            policy.network_disabled,
        )
    ):
        raise ValueError("command policy is not closed")
    kind = reservation.request.kind
    expert = kind in {"primary_expert", "cross_risk_expert", "expert_rereview"}
    if expert and (output_schema is None or expert_snapshot is None):
        raise ValueError("expert command requires a frozen output schema and snapshot")
    if not expert and expert_snapshot is not None:
        raise ValueError("writer command cannot use an expert snapshot")
    command_cwd = prepared.provider_cwd
    if expert_snapshot is not None:
        _verify_expert_snapshot(expert_snapshot)
        command_cwd = expert_snapshot.root
    sandbox = "read-only" if expert else "workspace-write"
    argv = [
        prepared.codex.executable,
        "exec",
        "--ephemeral",
        "--json",
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
        "--model",
        "gpt-5.6-sol",
        "-c",
        'model_reasoning_effort="high"',
        "--sandbox",
        sandbox,
        "-C",
        str(command_cwd),
        *_capability_arguments(),
    ]
    if expert:
        argv.extend(["--output-schema", str(output_schema.resolve(strict=True))])
    argv.append("-")
    forbidden = load_arm_manifest().codex.forbidden_exec_options
    if any(option in argv for option in forbidden):
        raise ValueError("command contains a forbidden capability")
    if argv.count("-C") != 1 or argv[argv.index("-C") + 1] != str(command_cwd):
        raise ValueError("command cwd binding is invalid")
    return argv


class BoundedReviewBridge:
    """Deterministic fake-only model of the A11 same-writer callback protocol.

    It validates state and evidence shape only.  No method launches Codex or an expert.
    """

    _ALLOWED_ROLES = {
        "requirement-contract-ambiguity": ("Primary",),
        "frontend-recovery-delivery": ("Primary",),
        "multi-tenant-security-review": ("Primary", "Cross-risk"),
    }

    def __init__(
        self,
        *,
        run_id: str,
        fixture_id: str,
        writer_session: str,
        parent_digest: str,
        candidate_digest: str,
        initial_snapshot: bytes,
        input_digest: str,
        candidate_tree_digest: str,
    ) -> None:
        if (
            fixture_id not in self._ALLOWED_ROLES
            or run_id != f"A11:{fixture_id}"
            or not writer_session
            or not _DIGEST.fullmatch(parent_digest)
            or not _DIGEST.fullmatch(candidate_digest)
            or not initial_snapshot
            or not _DIGEST.fullmatch(input_digest)
            or not _DIGEST.fullmatch(candidate_tree_digest)
        ):
            raise ValueError("review callback binding is invalid")
        self.run_id = run_id
        self.fixture_id = fixture_id
        self.writer_session = writer_session
        self.parent_digest = parent_digest
        self.candidate_digest = candidate_digest
        self.initial_snapshot_sha256 = sha256(initial_snapshot).hexdigest()
        self.input_digest = input_digest
        self.candidate_tree_digest = candidate_tree_digest
        self._reviews: dict[str, ReviewEvidence] = {}
        self._rereviews: dict[str, ReviewEvidence] = {}
        self._children: set[str] = set()
        self._repair_digest: str | None = None
        self._repaired_snapshot_sha256: str | None = None
        self._repair_count = 0
        self._failed: str | None = None
        self._conflict = False
        self._closed = False

    @staticmethod
    def _validate_findings(findings: Sequence[Mapping[str, object]]) -> None:
        for finding in findings:
            required = {"id", "severity", "fix"}
            optional = {"conflict_key", "exclusive_value"}
            if (
                not isinstance(finding, Mapping)
                or not required <= set(finding) <= required | optional
                or not all(
                    isinstance(finding[key], str) and finding[key] for key in required
                )
                or ("conflict_key" in finding) != ("exclusive_value" in finding)
            ):
                raise ValueError("review Findings schema is invalid")

    def dispatch_fake_review(
        self,
        role: str,
        reason: str,
        child_session: str,
        snapshot: bytes,
        parent_tree_before: str,
        parent_tree_after: str,
        findings: Sequence[Mapping[str, object]],
    ) -> ReviewEvidence:
        if self._failed or self._closed:
            raise ValueError("review callback is terminal")
        if role not in self._ALLOWED_ROLES[self.fixture_id] or role in self._reviews:
            raise ValueError("review role is not allowed or is duplicated")
        if (
            len(self._reviews) >= 2
            or child_session in self._children
            or not child_session
        ):
            raise ValueError("review child session is not unique or bounded")
        if (
            not reason
            or not snapshot
            or sha256(snapshot).hexdigest() != self.initial_snapshot_sha256
            or (role == "Cross-risk" and "security" not in reason.lower())
        ):
            raise ValueError("review snapshot binding is invalid")
        if (
            not _DIGEST.fullmatch(parent_tree_before)
            or parent_tree_before != parent_tree_after
            or parent_tree_before != self.candidate_tree_digest
        ):
            self._failed = "parent_mutation"
            raise ValueError("review parent mutation is forbidden")
        self._validate_findings(findings)
        evidence = ReviewEvidence(
            role=role,
            reason=reason,
            child_session=child_session,
            snapshot_sha256=sha256(snapshot).hexdigest(),
            finding_digest=sha256(_canonical_bytes(findings)).hexdigest(),
            findings=tuple(dict(item) for item in findings),
            parent_tree_before=parent_tree_before,
            parent_tree_after=parent_tree_after,
        )
        self._reviews[role] = evidence
        self._children.add(child_session)
        conflicts: dict[str, str] = {}
        for review in self._reviews.values():
            for finding in review.findings:
                key = finding.get("conflict_key")
                value = finding.get("exclusive_value")
                if isinstance(key, str) and isinstance(value, str):
                    if key in conflicts and conflicts[key] != value:
                        self._conflict = True
                        self._failed = "conflict"
                        raise ValueError("review conflict requires an operator")
                    conflicts[key] = value
        return evidence

    def record_writer_repair(
        self,
        writer_session: str,
        repair_digest: str,
        new_candidate_digest: str,
        repaired_snapshot: bytes,
        new_candidate_tree_digest: str,
    ) -> None:
        if writer_session != self.writer_session:
            raise ValueError("replacement writer cannot repair")
        if (
            self._failed
            or self._closed
            or set(self._reviews) != set(self._ALLOWED_ROLES[self.fixture_id])
        ):
            raise ValueError("required initial expert roles are incomplete")
        if self._repair_count:
            raise ValueError("writer may repair at most once")
        if not any(review.findings for review in self._reviews.values()):
            raise ValueError("review repair has no Findings")
        if (
            not _DIGEST.fullmatch(repair_digest)
            or not _DIGEST.fullmatch(new_candidate_digest)
            or new_candidate_digest == self.candidate_digest
            or not repaired_snapshot
            or not _DIGEST.fullmatch(new_candidate_tree_digest)
            or new_candidate_tree_digest == self.candidate_tree_digest
        ):
            raise ValueError("review repair digest is invalid")
        self._repair_digest = repair_digest
        self._repair_count = 1
        self.candidate_digest = new_candidate_digest
        self.candidate_tree_digest = new_candidate_tree_digest
        self._repaired_snapshot_sha256 = sha256(repaired_snapshot).hexdigest()

    def dispatch_fake_rereview(
        self,
        role: str,
        child_session: str,
        snapshot: bytes,
        parent_tree_before: str,
        parent_tree_after: str,
        findings: Sequence[Mapping[str, object]],
    ) -> ReviewEvidence:
        if self._repair_digest is None or role not in self._reviews:
            raise ValueError("review rereview preconditions are invalid")
        if child_session in self._children or role in self._rereviews:
            raise ValueError("review rereview session is not fresh")
        if findings:
            self._failed = "rereview_findings"
            raise ValueError("review rereview did not pass")
        if (
            not snapshot
            or sha256(snapshot).hexdigest() != self._repaired_snapshot_sha256
            or not _DIGEST.fullmatch(parent_tree_before)
            or parent_tree_before != parent_tree_after
            or parent_tree_before != self.candidate_tree_digest
        ):
            self._failed = "parent_mutation"
            raise ValueError("review repaired snapshot or parent binding is invalid")
        self._validate_findings(findings)
        evidence = ReviewEvidence(
            role=role,
            reason="fresh-rereview",
            child_session=child_session,
            snapshot_sha256=sha256(snapshot).hexdigest(),
            finding_digest=sha256(_canonical_bytes(findings)).hexdigest(),
            findings=(),
            parent_tree_before=parent_tree_before,
            parent_tree_after=parent_tree_after,
        )
        self._rereviews[role] = evidence
        self._children.add(child_session)
        return evidence

    def fail(self, reason: str) -> None:
        if reason not in {"timeout", "schema_failure", "expert_failure"}:
            raise ValueError("review failure reason is invalid")
        self._failed = reason

    @property
    def review_digest(self) -> str:
        if not self._reviews:
            raise ValueError("review digest is unavailable")
        return sha256(
            _canonical_bytes(
                {
                    "run_id": self.run_id,
                    "parent_digest": self.parent_digest,
                    "candidate_digest": self.candidate_digest,
                    "reviews": {
                        role: {
                            "snapshot_sha256": review.snapshot_sha256,
                            "finding_digest": review.finding_digest,
                            "child_session": review.child_session,
                        }
                        for role, review in sorted(self._reviews.items())
                    },
                    "repair_digest": self._repair_digest,
                    "rereviews": {
                        role: review.finding_digest
                        for role, review in sorted(self._rereviews.items())
                    },
                }
            )
        ).hexdigest()

    @property
    def close_allowed(self) -> bool:
        if (
            self._failed
            or self._conflict
            or set(self._reviews) != set(self._ALLOWED_ROLES[self.fixture_id])
        ):
            return False
        roles_with_findings = {
            role for role, review in self._reviews.items() if review.findings
        }
        if roles_with_findings and (
            self._repair_digest is None
            or not roles_with_findings <= set(self._rereviews)
        ):
            return False
        return self._closed

    def close(
        self,
        writer_session: str,
        expected_review_digest: str,
        candidate_digest: str,
    ) -> Mapping[str, object]:
        if writer_session != self.writer_session:
            raise ValueError("replacement writer cannot Close")
        if (
            self._failed
            or self._conflict
            or set(self._reviews) != set(self._ALLOWED_ROLES[self.fixture_id])
            or self._closed
        ):
            raise ValueError("review Close required roles are incomplete")
        roles_with_findings = {
            role for role, review in self._reviews.items() if review.findings
        }
        if roles_with_findings and (
            self._repair_digest is None
            or not roles_with_findings <= set(self._rereviews)
        ):
            raise ValueError("review Close is early")
        if (
            expected_review_digest != self.review_digest
            or candidate_digest != self.candidate_digest
        ):
            raise ValueError("review Close digest binding is invalid")
        self._closed = True
        return {
            "status": "closed",
            "writer_session": writer_session,
            "review_digest": expected_review_digest,
            "candidate_digest": candidate_digest,
        }


def _capture_execution_source_binding(
    arms_root: Path, execution_commit: str
) -> ExecutionSourceBinding:
    repo_root = arms_root.parents[2]
    head = _git(repo_root, "rev-parse", "HEAD")
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=repo_root,
        env=closed_git_environment(),
        check=True,
        capture_output=True,
    ).stdout
    tree_listing = subprocess.run(
        ["git", "ls-tree", "-r", "-z", "HEAD"],
        cwd=repo_root,
        env=closed_git_environment(),
        check=True,
        capture_output=True,
    ).stdout
    if head != execution_commit or status:
        raise ValueError("execution source is not the exact clean commit")
    source_paths = (
        repo_root / "src/ai_sdlc/benefit_benchmark.py",
        repo_root / "src/ai_sdlc/benefit_benchmark_fixtures.py",
        repo_root / "src/ai_sdlc/benefit_benchmark_arms.py",
        repo_root / "scripts/ai_sdlc_v2_benefit_benchmark.py",
        arms_root / "manifest.json",
        arms_root / "prompt-matrix.json",
        arms_root / "ai-sdlc-method-surfaces.json",
        arms_root / "common-agent-contract.md",
        arms_root / "S/adaptation.json",
        arms_root / "S/semantic-adaptation.diff",
        arms_root / "S/AGENTS.md",
    )
    source_capsule = [
        {
            "path": path.relative_to(repo_root).as_posix(),
            "sha256": _stable_regular_file_sha256(path),
        }
        for path in source_paths
    ]
    manifest = load_arm_manifest(arms_root / "manifest.json")
    return ExecutionSourceBinding(
        commit=head,
        tree_sha256=sha256(tree_listing).hexdigest(),
        clean_state_sha256=sha256(status).hexdigest(),
        task3_runner_sha256=_stable_regular_file_sha256(
            repo_root / "src/ai_sdlc/benefit_benchmark_arms.py"
        ),
        source_capsule_sha256=sha256(_canonical_bytes(source_capsule)).hexdigest(),
        prompt_matrix_sha256=manifest.prompt_matrix_sha256,
    )


def validate_execution_authorization_v2(
    protocol: BenchmarkProtocol,
    authorization_path: Path | None,
    *,
    execution_commit: str,
    preflight_receipt: Path,
    arms_root: Path = _ARMS_ROOT,
    _test_execution_binding: ExecutionSourceBinding | None = None,
) -> tuple[BenchmarkIssue, ...]:
    """Validate the Task-4 external authorization v2 bindings without writing it.

    Task 3 uses only synthetic 0600 files in tests.  Schema v1 is intentionally rejected so a
    legacy authorization cannot start the formal matrix.
    """
    try:
        if authorization_path is None:
            raise ValueError("authorization is missing")
        raw = _closed(
            _load_execution_authorization(authorization_path),
            _AUTH_V2_KEYS,
            "authorization v2",
        )
        scope = _closed(raw["scope"], _AUTH_SCOPE_KEYS, "authorization scope")
        manifest = load_arm_manifest(arms_root / "manifest.json")
        binding = _test_execution_binding or _capture_execution_source_binding(
            arms_root, execution_commit
        )
        if (
            raw["schema"] != "ai-sdlc-v2-benefit-execution-authorization/v2"
            or raw["protocol_sha256"] != canonical_protocol_digest(protocol)
            or raw["execution_commit"] != execution_commit
            or not _COMMIT.fullmatch(execution_commit)
            or raw["execution_tree_sha256"] != binding.tree_sha256
            or raw["execution_clean_state_sha256"] != binding.clean_state_sha256
            or binding.clean_state_sha256 != _EMPTY_GIT_STATUS_SHA256
            or raw["task3_runner_sha256"] != binding.task3_runner_sha256
            or raw["source_capsule_sha256"] != binding.source_capsule_sha256
            or raw["prompt_matrix_sha256"] != binding.prompt_matrix_sha256
            or raw["arm_manifest_sha256"] != _sha_file(arms_root / "manifest.json")
            or raw["neutral_envelope_sha256"]
            != _sha_file(arms_root / manifest.common_agent_contract_path)
            or raw["superpowers_adaptation_sha256"]
            != _sha_file(arms_root / manifest.superpowers.adaptation_path)
            or raw["preflight_receipt_sha256"]
            != _stable_regular_file_sha256(preflight_receipt)
            or raw["execution_identity"]
            != {
                name: getattr(protocol.execution_lock, name)
                for name in protocol.execution_lock.__dataclass_fields__
            }
            or raw["attempt_budget"]
            != {
                name: getattr(protocol.attempt_budget, name)
                for name in protocol.attempt_budget.__dataclass_fields__
            }
            or scope["mode"] != "single-frozen-matrix"
            or scope["run_ids"] != [run.run_id for run in protocol.run_matrix]
            or scope["operations"] != list(_AUTH_OPERATIONS)
        ):
            raise ValueError("authorization v2 binding is invalid")
        from datetime import UTC, datetime

        valid_from = datetime.fromisoformat(
            str(raw["valid_from"]).replace("Z", "+00:00")
        )
        expires_at = datetime.fromisoformat(
            str(raw["expires_at"]).replace("Z", "+00:00")
        )
        now = datetime.now(UTC)
        if (
            valid_from.tzinfo is None
            or expires_at.tzinfo is None
            or not (valid_from <= now < expires_at)
        ):
            raise ValueError("authorization v2 time window is invalid")
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return (
            BenchmarkIssue("authorization.v2", "execution authorization v2 is invalid"),
        )
    return ()
