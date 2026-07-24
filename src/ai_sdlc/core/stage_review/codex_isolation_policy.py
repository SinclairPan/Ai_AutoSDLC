"""Codex 隔离策略准备、校准与清理辅助。"""

from __future__ import annotations

import platform
import secrets
import shutil
from dataclasses import dataclass, replace
from pathlib import Path

from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.isolation_launch_models import IsolationLaunchContext
from ai_sdlc.core.stage_review.isolation_models import (
    IsolationEvidenceManifest,
    IsolationExecutionPermit,
)

_LINUX_RUNTIME_READ_CANDIDATES = (
    Path("/lib/ld-musl-x86_64.so.1"),
    Path("/lib/ld-musl-aarch64.so.1"),
    Path("/usr/lib/x86_64-linux-musl"),
    Path("/usr/lib/aarch64-linux-musl"),
)


@dataclass(frozen=True, slots=True)
class PreparedIsolationPolicy:
    context: IsolationLaunchContext
    manifest: IsolationEvidenceManifest
    runtime_identity_digest: str


def _policy_payload(context: IsolationLaunchContext) -> dict[str, object]:
    return {
        "run_root": str(Path(context.normalized_run_root).resolve()),
        "candidate_root": context.candidate_root,
        "peer_output_roots": tuple(sorted(context.peer_output_roots)),
        "disposable_home_root": context.disposable_home_root,
        "disposable_config_root": context.disposable_config_root,
        "disposable_credential_root": context.disposable_credential_root,
        "output_root": context.output_root,
        "controller_config_root": context.controller_config_root,
        "protected_home_root": context.protected_home_root,
        "protected_config_roots": tuple(sorted(context.protected_config_roots)),
        "runtime_read_roots": tuple(sorted(context.runtime_read_roots)),
        "layout_digest": context.layout_digest,
        "network": "deny-all",
        "environment": "minimal-no-proxy",
        "handles": "close-fds",
    }


def _policy_digests(payload: dict[str, object]) -> tuple[str, str, str]:
    policy = CanonicalizationPolicy()
    filesystem = {
        key: value
        for key, value in payload.items()
        if key.endswith("root") or key.endswith("roots")
    }
    return (
        canonical_digest(payload, policy),
        canonical_digest(filesystem, policy),
        canonical_digest({"network": payload["network"]}, policy),
    )


def _context_paths_valid(context: IsolationLaunchContext) -> bool:
    paths = (
        context.normalized_run_root,
        context.candidate_root,
        context.disposable_home_root,
        context.disposable_config_root,
        context.disposable_credential_root,
        context.output_root,
        context.controller_config_root,
        context.protected_home_root,
        *context.peer_output_roots,
        *context.protected_config_roots,
        *context.runtime_read_roots,
    )
    required = context.peer_output_roots and context.protected_config_roots
    return bool(required) and all(Path(value).is_absolute() for value in paths)


def _calibration_context(context: IsolationLaunchContext) -> IsolationLaunchContext:
    root = (
        Path(context.normalized_run_root).parent
        / f"calibration-probe-{secrets.token_hex(16)}"
    ).resolve(strict=False)
    return replace(
        context,
        normalized_run_root=str(root / "run"),
        candidate_root=str(root / "candidate"),
        peer_output_roots=(str(root / "peer-output"),),
        disposable_home_root=str(root / "home"),
        disposable_config_root=str(root / "child-config"),
        disposable_credential_root=str(root / "credentials"),
        output_root=str(root / "output"),
        controller_config_root=str(root / "controller"),
        protected_home_root=str(root / "protected-home"),
        protected_config_roots=(str(root / "protected-config" / ".gitconfig"),),
    )


def _seed_calibration_targets(context: IsolationLaunchContext) -> None:
    directories = (
        context.candidate_root,
        *context.peer_output_roots,
        context.protected_home_root,
    )
    for value in directories:
        Path(value).mkdir(parents=True, exist_ok=True)
    (Path(context.candidate_root) / "candidate-sentinel.txt").write_text(
        "calibration-readable",
        encoding="utf-8",
    )
    for value in context.protected_config_roots:
        target = Path(value)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("calibration-sentinel", encoding="utf-8")


def _cleanup_calibration_root(root: Path) -> bool:
    try:
        shutil.rmtree(root)
    except OSError:
        return False
    return not root.exists()


def _with_backend_runtime(
    context: IsolationLaunchContext,
    executable: Path,
) -> IsolationLaunchContext:
    roots = {
        *(Path(value).resolve(strict=False) for value in context.runtime_read_roots),
        executable.resolve(strict=False),
        executable.resolve(strict=False).parent,
    }
    if platform.system().lower() == "linux":
        for candidate in _LINUX_RUNTIME_READ_CANDIDATES:
            if not candidate.exists():
                continue
            resolved = candidate.resolve(strict=False)
            roots.update((resolved, resolved.parent))
    return replace(context, runtime_read_roots=tuple(sorted(map(str, roots))))


def _protected_digest(context: IsolationLaunchContext) -> str:
    values = {
        "candidate": _path_digest(context.candidate_root),
        "peers": tuple(_path_digest(path) for path in context.peer_output_roots),
        "home": _path_digest(context.protected_home_root),
        "config": tuple(_path_digest(path) for path in context.protected_config_roots),
    }
    return canonical_digest(values, CanonicalizationPolicy())


def _path_digest(value: str) -> str:
    path = Path(value)
    if not path.exists():
        return "missing"
    rows = []
    files = (path,) if path.is_file() else tuple(sorted(path.rglob("*")))
    for item in files:
        if not item.is_file():
            continue
        try:
            digest = canonical_digest(
                item.read_bytes().hex(), CanonicalizationPolicy()
            )
        except OSError:
            digest = "denied"
        rows.append((str(item), digest))
    return canonical_digest(rows, CanonicalizationPolicy())


def _permit_matches(
    prepared: PreparedIsolationPolicy,
    permit: IsolationExecutionPermit,
) -> bool:
    manifest = prepared.manifest
    return all(
        (
            permit.backend_id == manifest.backend_id,
            permit.contract_version == manifest.contract_version,
            permit.backend_version == manifest.backend_version,
            permit.backend_instance_id == manifest.backend_instance_id,
            permit.backend_epoch == manifest.backend_epoch,
            permit.host_snapshot_digest == manifest.host_snapshot_digest,
            permit.allocation_digest == manifest.allocation_digest,
            permit.assignment_digest == manifest.assignment_digest,
            permit.candidate_digest == manifest.candidate_digest,
            permit.layout_digest == manifest.layout_digest,
            permit.normalized_run_root == prepared.context.normalized_run_root,
            permit.filesystem_policy_digest == manifest.filesystem_policy_digest,
            permit.network_policy_digest == manifest.network_policy_digest,
            permit.manifest_digest == manifest.manifest_digest,
            permit.release_manifest_digest == manifest.release_manifest_digest,
            permit.runtime_identity_digest == manifest.runtime_identity_digest,
        )
    )


def cleanup_transient(context: IsolationLaunchContext) -> bool:
    roots = (
        context.disposable_home_root,
        context.disposable_config_root,
        context.disposable_credential_root,
    )
    allowed = Path(context.normalized_run_root).parent.resolve(strict=False)
    try:
        for value in roots:
            path = Path(value).resolve(strict=False)
            if not path.is_relative_to(allowed):
                return False
            shutil.rmtree(path, ignore_errors=False)
        return all(not Path(value).exists() for value in roots)
    except OSError:
        return False


def _cleanup_controller(controller: Path) -> bool:
    try:
        shutil.rmtree(controller, ignore_errors=False)
    except OSError:
        return False
    return not controller.exists()
