"""把 Phase 1 v2 激活状态事务化转换为可恢复的 v1 兼容视图。"""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any, TypedDict

from ai_sdlc.core.loop_models import utc_now_iso
from ai_sdlc.core.stage_review.activation_fence import (
    activation_safety_mutation_fence,
)
from ai_sdlc.core.stage_review.activation_policy_anchor import (
    ACTIVATION_POLICY_ANCHOR,
    read_activation_policy_anchor,
)
from ai_sdlc.core.stage_review.artifacts import (
    ShortFileLock,
    atomic_write_json,
    read_json_object,
    resolve_canonical_shared_state,
    resolve_repository_project_id,
)
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.resource_builders import stable_id

_DIGEST_POLICY = CanonicalizationPolicy()
_TERMINAL_OPERATION_STATES = {"completed", "recovered"}


class _DirectoryManifest(TypedDict):
    present: bool
    files: dict[str, str]
    directories: list[str]


def export_v1_rollback_bundle(root: Path, output: Path) -> Path:
    repository = root.resolve()
    require_activation_rollback_idle(repository)
    policy = read_activation_policy_anchor(repository)
    if policy is None:
        raise ValueError("activation rollback requires a protected policy anchor")
    if policy.active_phase != 1:
        raise ValueError("activation rollback is allowed only from phase 1")
    if policy.compatibility_mode != "strict":
        raise ValueError("activation rollback source must be the strict v2 policy")
    source_policy_digest = policy.policy_digest
    project_id = resolve_repository_project_id(repository)
    shared = resolve_canonical_shared_state(repository, project_id)
    target = output.resolve()
    _require_external_bundle_target(repository, shared, target)
    with activation_safety_mutation_fence(repository, project_id):
        require_activation_rollback_idle(repository)
        policy = read_activation_policy_anchor(repository)
        if (
            policy is None
            or policy.active_phase != 1
            or policy.compatibility_mode != "strict"
            or policy.policy_digest != source_policy_digest
        ):
            raise ValueError("activation rollback source policy changed")
        legacy = _v1_policy_payload(policy.model_dump(mode="json"))
        payload: dict[str, Any] = {
            "schema_version": "activation-v1-rollback-bundle.v1",
            "artifact_kind": "activation-v1-rollback-bundle",
            "project_id": project_id,
            "source_policy_digest": policy.policy_digest,
            "created_at": utc_now_iso(),
            "legacy_policy": legacy,
            "state_manifest": _state_manifest(repository, shared),
        }
        payload["bundle_digest"] = canonical_digest(payload, _DIGEST_POLICY)
        atomic_write_json(target, payload)
    return target


def restore_v1_rollback_bundle(root: Path, bundle_path: Path) -> Path:
    repository = root.resolve()
    bundle = _verified_bundle(bundle_path)
    project_id = resolve_repository_project_id(repository)
    if bundle["project_id"] != project_id:
        raise ValueError("activation rollback bundle project mismatch")
    shared = resolve_canonical_shared_state(repository, project_id)
    backup_id = stable_id(
        "activation-v1-rollback-backup",
        str(bundle["bundle_digest"]),
    )
    backup = shared / "activation-rollback-backups" / backup_id
    operation_path = _operation_path(shared, backup_id)
    with (
        activation_safety_mutation_fence(repository, project_id),
        ShortFileLock(
            shared / "activation-v1-rollback.lock",
            timeout_seconds=5,
        ),
    ):
        operation = _read_or_prepare_restore_operation(
            repository,
            shared,
            backup,
            operation_path,
            bundle,
            project_id=project_id,
            backup_id=backup_id,
        )
        state = str(operation["state"])
        if state == "recovered":
            raise ValueError("activation rollback backup was already recovered")
        if state == "completed":
            validate_v1_rollback_view(repository)
            _write_backup_manifest(backup, operation)
            return backup
        if state == "prepared":
            _transfer_rollback_targets(
                repository,
                shared,
                backup,
                operation,
                restore_to_runtime=False,
            )
            operation = _advance_operation(
                operation_path,
                backup,
                operation,
                "state-backed-up",
            )
            state = "state-backed-up"
        if state == "state-backed-up":
            _commit_legacy_anchor(repository, bundle, operation)
            operation = _advance_operation(
                operation_path,
                backup,
                operation,
                "anchor-committed",
            )
            state = "anchor-committed"
        if state == "anchor-committed":
            validate_v1_rollback_view(repository)
            operation = _advance_operation(
                operation_path,
                backup,
                operation,
                "completed",
                completed_at=utc_now_iso(),
            )
        if operation["state"] != "completed":
            raise ValueError("activation rollback operation did not complete")
    return backup


def recover_v2_rollback_backup(root: Path, backup_id: str) -> Path:
    repository = root.resolve()
    project_id = resolve_repository_project_id(repository)
    shared = resolve_canonical_shared_state(repository, project_id)
    backup = shared / "activation-rollback-backups" / backup_id
    operation_path = _operation_path(shared, backup_id)
    with (
        activation_safety_mutation_fence(repository, project_id),
        ShortFileLock(
            shared / "activation-v1-rollback.lock",
            timeout_seconds=5,
        ),
    ):
        if not operation_path.is_file():
            raise ValueError("activation rollback operation is unavailable")
        operation = read_json_object(operation_path)
        _verify_operation_identity(operation, backup_id, project_id)
        if (backup / "manifest.json").is_file():
            _verify_backup_manifest(backup, operation)
        else:
            _rebuild_missing_prepared_manifest(
                repository,
                shared,
                backup,
                operation,
            )
            _verify_backup_manifest(backup, operation)
        state = str(operation.get("state", ""))
        if state == "recovered":
            _write_backup_manifest(backup, operation)
            return backup
        recoverable_states = {
            "prepared",
            "state-backed-up",
            "anchor-committed",
            "completed",
            "recovering",
        }
        if state not in recoverable_states:
            raise ValueError("activation rollback operation is not recoverable")
        _require_recoverable_anchor(repository, operation, state)
        if state == "completed":
            validate_v1_rollback_view(repository)
        if state != "recovering":
            operation = _advance_operation(
                operation_path,
                backup,
                operation,
                "recovering",
                recovery_from_state=state,
            )
        _transfer_rollback_targets(
            repository,
            shared,
            backup,
            operation,
            restore_to_runtime=True,
        )
        if _state_manifest(repository, shared) != _operation_state_manifest(
            operation
        ):
            raise ValueError("activation rollback v2 state verification failed")
        previous_anchor = operation.get("previous_anchor")
        if not isinstance(previous_anchor, dict):
            raise ValueError("activation rollback previous anchor is missing")
        atomic_write_json(repository / ACTIVATION_POLICY_ANCHOR, previous_anchor)
        policy = read_activation_policy_anchor(repository)
        if (
            policy is None
            or policy.compatibility_mode != "strict"
            or policy.policy_digest != operation.get("source_policy_digest")
        ):
            raise ValueError("activation rollback v2 recovery verification failed")
        _advance_operation(
            operation_path,
            backup,
            operation,
            "recovered",
            recovered_at=utc_now_iso(),
        )
    return backup


def _require_recoverable_anchor(
    repository: Path,
    operation: dict[str, Any],
    state: str,
) -> None:
    previous = operation.get("previous_anchor")
    if not isinstance(previous, dict):
        raise ValueError("activation rollback previous anchor is missing")
    legacy = _v1_policy_payload(previous)
    current = read_json_object(repository / ACTIVATION_POLICY_ANCHOR)
    allowed = (
        (previous,)
        if state == "prepared"
        else (legacy,)
        if state in {"anchor-committed", "completed"}
        else (previous, legacy)
    )
    if current not in allowed:
        raise ValueError("activation rollback anchor is not recoverable")


def require_activation_rollback_idle(root: Path) -> None:
    repository = root.resolve()
    project_id = resolve_repository_project_id(repository)
    shared = resolve_canonical_shared_state(repository, project_id)
    operations_root = shared / "activation-rollback-operations"
    for path in sorted(operations_root.glob("*.json")):
        operation = read_json_object(path)
        _verify_operation_identity(
            operation,
            str(operation.get("backup_id", "")),
            project_id,
        )
        if operation.get("state") not in _TERMINAL_OPERATION_STATES:
            raise ValueError("activation rollback operation is incomplete")


def validate_v1_rollback_view(root: Path) -> None:
    repository = root.resolve()
    anchor = read_json_object(repository / ACTIVATION_POLICY_ANCHOR)
    _verified_v1_policy(anchor)
    project_id = resolve_repository_project_id(repository)
    shared = resolve_canonical_shared_state(repository, project_id)
    remaining = tuple(
        key
        for key, path in _rollback_targets(repository, shared)
        if path.exists()
    )
    if remaining:
        raise ValueError(
            "v1 rollback view still exposes v2 runtime state: "
            + ", ".join(remaining)
        )


def _read_or_prepare_restore_operation(
    repository: Path,
    shared: Path,
    backup: Path,
    operation_path: Path,
    bundle: dict[str, Any],
    *,
    project_id: str,
    backup_id: str,
) -> dict[str, Any]:
    if operation_path.is_file():
        existing = read_json_object(operation_path)
        _verify_operation_identity(existing, backup_id, project_id)
        if existing.get("bundle_digest") != bundle["bundle_digest"]:
            raise ValueError("activation rollback operation identity fork")
        if (backup / "manifest.json").is_file():
            _verify_backup_manifest(backup, existing)
        else:
            _rebuild_missing_prepared_manifest(
                repository,
                shared,
                backup,
                existing,
            )
        return existing
    current = read_activation_policy_anchor(repository)
    if current is None or current.policy_digest != bundle["source_policy_digest"]:
        raise ValueError("activation rollback source policy changed")
    if current.active_phase != 1 or current.compatibility_mode != "strict":
        raise ValueError("activation rollback is allowed only from strict phase 1")
    expected_legacy = _v1_policy_payload(current.model_dump(mode="json"))
    if bundle.get("legacy_policy") != expected_legacy:
        raise ValueError("activation rollback legacy policy projection mismatch")
    expected_manifest = bundle.get("state_manifest")
    actual_manifest = _state_manifest(repository, shared)
    if expected_manifest != actual_manifest:
        raise ValueError("activation rollback state changed after export")
    backup.mkdir(parents=True, exist_ok=True)
    operation = _with_operation_digest({
        "schema_version": "activation-v1-rollback-operation.v1",
        "artifact_kind": "activation-v1-rollback-operation",
        "backup_id": backup_id,
        "project_id": project_id,
        "source_policy_digest": current.policy_digest,
        "bundle_digest": bundle["bundle_digest"],
        "previous_anchor": current.model_dump(mode="json"),
        "state_manifest": actual_manifest,
        "state": "prepared",
        "created_at": utc_now_iso(),
    })
    atomic_write_json(operation_path, operation)
    _write_backup_manifest(backup, operation)
    return operation


def _advance_operation(
    operation_path: Path,
    backup: Path,
    operation: dict[str, Any],
    state: str,
    **timestamps: str,
) -> dict[str, Any]:
    updated = _with_operation_digest(
        {
            **{
                key: value
                for key, value in operation.items()
                if key != "operation_digest"
            },
            "state": state,
            **timestamps,
        }
    )
    atomic_write_json(operation_path, updated)
    _write_backup_manifest(backup, updated)
    return updated


def _write_backup_manifest(backup: Path, operation: dict[str, Any]) -> None:
    payload = {
        **{
            key: value
            for key, value in operation.items()
            if key not in {"schema_version", "artifact_kind"}
        },
        "schema_version": "activation-v1-rollback-backup.v1",
        "artifact_kind": "activation-v1-rollback-backup",
        "operation_schema_version": operation.get("schema_version"),
        "operation_artifact_kind": operation.get("artifact_kind"),
    }
    payload["manifest_digest"] = canonical_digest(payload, _DIGEST_POLICY)
    atomic_write_json(backup / "manifest.json", payload)


def _commit_legacy_anchor(
    repository: Path,
    bundle: dict[str, Any],
    operation: dict[str, Any],
) -> None:
    legacy = bundle.get("legacy_policy")
    if not isinstance(legacy, dict):
        raise ValueError("activation rollback legacy policy is missing")
    anchor_path = repository / ACTIVATION_POLICY_ANCHOR
    if anchor_path.is_file():
        current_payload = read_json_object(anchor_path)
        if current_payload == legacy:
            return
        if current_payload != operation.get("previous_anchor"):
            raise ValueError("activation rollback anchor changed during restore")
    atomic_write_json(anchor_path, legacy)


def _transfer_rollback_targets(
    repository: Path,
    shared: Path,
    backup: Path,
    operation: dict[str, Any],
    *,
    restore_to_runtime: bool,
) -> None:
    expected = _operation_state_manifest(operation)
    backup_id = str(operation["backup_id"])
    for key, runtime_path in _rollback_targets(repository, shared):
        backup_path = backup / key
        source, target = (
            (backup_path, runtime_path)
            if restore_to_runtime
            else (runtime_path, backup_path)
        )
        _transfer_directory(
            source,
            target,
            expected[key],
            backup_id=backup_id,
        )


def _transfer_directory(
    source: Path,
    target: Path,
    expected: _DirectoryManifest,
    *,
    backup_id: str,
) -> None:
    source_manifest = _directory_manifest(source)
    target_manifest = _directory_manifest(target)
    _require_manifest_subset(source_manifest, expected)
    _require_manifest_subset(target_manifest, expected)
    if not expected["present"]:
        if source_manifest["present"] or target_manifest["present"]:
            raise ValueError("activation rollback transfer state is incomplete")
        return
    target.mkdir(parents=True, exist_ok=True)
    staging = (
        target.parent
        / f".{target.name}.activation-rollback-staging.{backup_id}"
    )
    if staging.exists():
        shutil.rmtree(staging)
    for relative in expected["directories"]:
        (target / relative).mkdir(parents=True, exist_ok=True)
    for relative, digest in expected["files"].items():
        destination = target / relative
        if destination.is_file() and _file_digest(destination) == digest:
            continue
        origin = source / relative
        if not origin.is_file() or _file_digest(origin) != digest:
            raise ValueError("activation rollback transfer state is incomplete")
        staged = staging / relative
        staged.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, staged)
        if _file_digest(staged) != digest:
            raise ValueError("activation rollback staged copy digest mismatch")
        destination.parent.mkdir(parents=True, exist_ok=True)
        staged.replace(destination)
    if _directory_manifest(target) != expected:
        raise ValueError("activation rollback transfer verification failed")
    _require_manifest_subset(_directory_manifest(source), expected)
    if staging.exists():
        shutil.rmtree(staging)
    if source.exists():
        if not source.is_dir() or source.is_symlink():
            raise ValueError("activation rollback transfer source type is invalid")
        shutil.rmtree(source)


def _require_manifest_subset(
    actual: _DirectoryManifest,
    expected: _DirectoryManifest,
) -> None:
    if (
        actual["present"] and not expected["present"]
        or not set(actual["directories"]).issubset(expected["directories"])
        or any(
            expected["files"].get(path) != digest
            for path, digest in actual["files"].items()
        )
    ):
        raise ValueError("activation rollback transfer state is incomplete")


def _operation_state_manifest(
    operation: dict[str, Any],
) -> dict[str, _DirectoryManifest]:
    manifest = operation.get("state_manifest")
    if not isinstance(manifest, dict):
        raise ValueError("activation rollback operation state manifest is missing")
    try:
        return _validated_state_manifest(manifest)
    except ValueError as exc:
        raise ValueError(
            "activation rollback operation state manifest is invalid"
        ) from exc


def _verify_backup_manifest(
    backup: Path,
    operation: dict[str, Any],
) -> dict[str, Any]:
    path = backup / "manifest.json"
    if not path.is_file():
        raise ValueError("activation rollback backup manifest is unavailable")
    payload = read_json_object(path)
    if (
        payload.get("schema_version")
        != "activation-v1-rollback-backup.v1"
        or payload.get("artifact_kind")
        != "activation-v1-rollback-backup"
    ):
        raise ValueError("activation rollback backup manifest type is invalid")
    source_digest = payload.get("manifest_digest")
    expected_digest = canonical_digest(
        {
            key: value
            for key, value in payload.items()
            if key != "manifest_digest"
        },
        _DIGEST_POLICY,
    )
    if source_digest != expected_digest:
        raise ValueError("activation rollback backup manifest digest mismatch")
    identity_fields = (
        "backup_id",
        "project_id",
        "source_policy_digest",
        "bundle_digest",
        "previous_anchor",
        "state_manifest",
    )
    if any(payload.get(key) != operation.get(key) for key in identity_fields):
        raise ValueError("activation rollback backup manifest identity mismatch")
    if (
        payload.get("operation_schema_version")
        != operation.get("schema_version")
        or payload.get("operation_artifact_kind")
        != operation.get("artifact_kind")
    ):
        raise ValueError("activation rollback backup operation type mismatch")
    manifest_operation = {
        key: value
        for key, value in payload.items()
        if key
        not in {
            "schema_version",
            "artifact_kind",
            "operation_schema_version",
            "operation_artifact_kind",
            "manifest_digest",
        }
    }
    manifest_operation.update(
        {
            "schema_version": payload["operation_schema_version"],
            "artifact_kind": payload["operation_artifact_kind"],
        }
    )
    _verify_operation_identity(
        manifest_operation,
        str(payload.get("backup_id", "")),
        str(payload.get("project_id", "")),
    )
    return payload


def _rollback_targets(
    repository: Path,
    shared: Path,
) -> tuple[tuple[str, Path], ...]:
    return (
        ("shared/activation", shared / "activation"),
        ("shared/stage-close-gate", shared / "stage-close-gate"),
        (
            "repository/activation-evidence",
            repository / ".ai-sdlc/policies/activation-evidence",
        ),
    )


def _require_external_bundle_target(
    repository: Path,
    shared: Path,
    output: Path,
) -> None:
    managed_roots = (
        shared,
        repository / ".ai-sdlc/policies/activation-evidence",
    )
    anchor = (repository / ACTIVATION_POLICY_ANCHOR).resolve()
    if output == anchor or any(
        output == root.resolve() or root.resolve() in output.parents
        for root in managed_roots
    ):
        raise ValueError(
            "activation rollback bundle output must be outside managed state"
        )


def _state_manifest(
    repository: Path,
    shared: Path,
) -> dict[str, _DirectoryManifest]:
    return {
        key: _directory_manifest(path)
        for key, path in _rollback_targets(repository, shared)
    }


def _operation_path(shared: Path, backup_id: str) -> Path:
    return shared / "activation-rollback-operations" / f"{backup_id}.json"


def _verify_operation_identity(
    operation: dict[str, Any],
    backup_id: str,
    project_id: str,
) -> None:
    if (
        operation.get("schema_version")
        != "activation-v1-rollback-operation.v1"
        or operation.get("artifact_kind")
        != "activation-v1-rollback-operation"
        or not backup_id
        or operation.get("backup_id") != backup_id
        or operation.get("project_id") != project_id
    ):
        raise ValueError("activation rollback operation identity mismatch")
    source_digest = operation.get("operation_digest")
    expected_digest = canonical_digest(
        {
            key: value
            for key, value in operation.items()
            if key != "operation_digest"
        },
        _DIGEST_POLICY,
    )
    if source_digest != expected_digest:
        raise ValueError("activation rollback operation digest mismatch")


def _v1_policy_payload(payload: dict[str, object]) -> dict[str, object]:
    legacy = {
        key: value
        for key, value in payload.items()
        if key
        not in {
            "policy_digest",
            "outcome_maturity_window_days",
            "maximum_reversal_rate_upper",
            "maximum_late_critical_rate_upper",
            "maximum_escape_rate_upper",
            "activation_escape_cause_ids",
            "attribution_policy_digest",
        }
    }
    legacy["schema_version"] = "stage-gate-activation-policy.v1"
    legacy["policy_digest"] = canonical_digest(legacy, _DIGEST_POLICY)
    return legacy


def _verified_bundle(path: Path) -> dict[str, Any]:
    payload = read_json_object(path.resolve())
    if payload.get("schema_version") != "activation-v1-rollback-bundle.v1":
        raise ValueError("unsupported activation rollback bundle")
    source_digest = payload.get("bundle_digest")
    expected = canonical_digest(
        {key: value for key, value in payload.items() if key != "bundle_digest"},
        _DIGEST_POLICY,
    )
    if source_digest != expected:
        raise ValueError("activation rollback bundle digest mismatch")
    legacy = payload.get("legacy_policy")
    if not isinstance(legacy, dict):
        raise ValueError("activation rollback legacy policy is missing")
    _verified_v1_policy(legacy)
    manifest = payload.get("state_manifest")
    if not isinstance(manifest, dict):
        raise ValueError("activation rollback state manifest is invalid")
    try:
        _validated_state_manifest(manifest)
    except ValueError as exc:
        raise ValueError(
            "activation rollback state manifest content is invalid"
        ) from exc
    return payload


def _verified_v1_policy(payload: dict[str, object]) -> None:
    if payload.get("schema_version") != "stage-gate-activation-policy.v1":
        raise ValueError("activation rollback did not produce a v1 policy")
    source_digest = payload.get("policy_digest")
    expected = canonical_digest(
        {key: value for key, value in payload.items() if key != "policy_digest"},
        _DIGEST_POLICY,
    )
    if source_digest != expected:
        raise ValueError("activation rollback v1 policy digest mismatch")
    if payload.get("active_phase") != 1:
        raise ValueError("activation rollback v1 policy is not shadow-only")


def _directory_manifest(path: Path) -> _DirectoryManifest:
    if not path.exists():
        return {"present": False, "files": {}, "directories": []}
    if not path.is_dir() or path.is_symlink():
        raise ValueError("activation rollback state root type is invalid")
    files: dict[str, str] = {}
    directories: list[str] = []
    for item in sorted(path.rglob("*")):
        if item.is_symlink():
            raise ValueError("activation rollback state contains a symbolic link")
        relative = item.relative_to(path).as_posix()
        if item.is_dir():
            directories.append(relative)
        elif item.is_file():
            files[relative] = _file_digest(item)
        else:
            raise ValueError("activation rollback state contains an unsupported entry")
    return {
        "present": True,
        "files": files,
        "directories": directories,
    }


def _validated_state_manifest(
    manifest: dict[str, Any],
) -> dict[str, _DirectoryManifest]:
    expected_keys = {
        "shared/activation",
        "shared/stage-close-gate",
        "repository/activation-evidence",
    }
    if set(manifest) != expected_keys:
        raise ValueError("activation rollback state manifest keys are invalid")
    return {
        key: _validated_directory_manifest(manifest[key])
        for key in sorted(expected_keys)
    }


def _validated_directory_manifest(value: object) -> _DirectoryManifest:
    if not isinstance(value, dict) or set(value) != {
        "present",
        "files",
        "directories",
    }:
        raise ValueError("activation rollback directory manifest is invalid")
    present = value.get("present")
    files = value.get("files")
    directories = value.get("directories")
    if (
        not isinstance(present, bool)
        or not isinstance(files, dict)
        or not isinstance(directories, list)
        or any(
            not isinstance(path, str)
            or not _safe_manifest_path(path)
            or not isinstance(digest, str)
            or not digest.startswith("sha256:")
            for path, digest in files.items()
        )
        or any(
            not isinstance(path, str) or not _safe_manifest_path(path)
            for path in directories
        )
        or directories != sorted(set(directories))
        or set(files).intersection(directories)
        or (not present and (files or directories))
    ):
        raise ValueError("activation rollback directory manifest content is invalid")
    return {
        "present": present,
        "files": dict(files),
        "directories": list(directories),
    }


def _safe_manifest_path(value: str) -> bool:
    if not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and path.as_posix() == value
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def _with_operation_digest(payload: dict[str, Any]) -> dict[str, Any]:
    operation = {
        key: value
        for key, value in payload.items()
        if key != "operation_digest"
    }
    operation["operation_digest"] = canonical_digest(operation, _DIGEST_POLICY)
    return operation


def _rebuild_missing_prepared_manifest(
    repository: Path,
    shared: Path,
    backup: Path,
    operation: dict[str, Any],
) -> None:
    if operation.get("state") != "prepared":
        raise ValueError("activation rollback backup manifest is unavailable")
    if backup.exists():
        if not backup.is_dir() or backup.is_symlink():
            raise ValueError("activation rollback backup manifest is unavailable")
        entries = tuple(backup.iterdir())
        if any(
            not item.is_file()
            or item.is_symlink()
            or re.fullmatch(r"\.[0-9a-f]{16}\.tmp", item.name) is None
            for item in entries
        ):
            raise ValueError("activation rollback backup manifest is unavailable")
        for item in entries:
            item.unlink()
    current = read_activation_policy_anchor(repository)
    if (
        current is None
        or current.policy_digest != operation.get("source_policy_digest")
        or _state_manifest(repository, shared)
        != _operation_state_manifest(operation)
    ):
        raise ValueError("activation rollback prepared state cannot be replayed")
    backup.mkdir(parents=True, exist_ok=True)
    _write_backup_manifest(backup, operation)


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


__all__ = [
    "export_v1_rollback_bundle",
    "recover_v2_rollback_backup",
    "require_activation_rollback_idle",
    "restore_v1_rollback_bundle",
    "validate_v1_rollback_view",
]
