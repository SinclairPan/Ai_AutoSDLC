from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_sdlc.cli.main import app
from ai_sdlc.core.stage_review import activation_policy_store, activation_rollback
from ai_sdlc.core.stage_review.activation_policy import baseline_activation_policy
from ai_sdlc.core.stage_review.activation_policy_anchor import (
    ACTIVATION_POLICY_ANCHOR,
    read_activation_policy_anchor,
    write_activation_policy_anchor,
)
from ai_sdlc.core.stage_review.activation_rollback import (
    validate_v1_rollback_view,
)
from ai_sdlc.core.stage_review.artifacts import (
    resolve_canonical_shared_state,
    resolve_repository_project_id,
)
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)

runner = CliRunner()


def test_rollback_export_and_restore_create_recoverable_v1_view(
    tmp_path: Path,
) -> None:
    _init_repository(tmp_path)
    policy = baseline_activation_policy()
    write_activation_policy_anchor(tmp_path, policy)
    project_id = resolve_repository_project_id(tmp_path)
    shared = resolve_canonical_shared_state(tmp_path, project_id)
    _write_json(
        shared / "activation/session-records/v2.json",
        {"schema_version": "stage-gate-activation-session-record.v2"},
    )
    _write_json(
        shared / "stage-close-gate/attestations/v2.json",
        {"schema_version": "stage-close-gate-attestation.v2"},
    )
    _write_json(
        tmp_path / ".ai-sdlc/policies/activation-evidence/v2.package.json",
        {"schema_version": "activation-evidence-package.v2"},
    )
    bundle = tmp_path / "rollback-v1.json"

    exported = runner.invoke(
        app,
        [
            "activation",
            "rollback-export",
            str(tmp_path),
            "--output",
            str(bundle),
        ],
    )

    assert exported.exit_code == 0, exported.output
    assert bundle.is_file()
    assert (
        json.loads((tmp_path / ACTIVATION_POLICY_ANCHOR).read_text(encoding="utf-8"))[
            "schema_version"
        ]
        == "stage-gate-activation-policy.v2"
    )

    restored = runner.invoke(
        app,
        [
            "activation",
            "rollback-restore",
            str(tmp_path),
            "--bundle",
            str(bundle),
            "--execute",
            "--yes",
        ],
    )

    assert restored.exit_code == 0, restored.output
    assert "Read-only V1 compatibility view restored" in restored.output
    validate_v1_rollback_view(tmp_path)
    anchor = json.loads(
        (tmp_path / ACTIVATION_POLICY_ANCHOR).read_text(encoding="utf-8")
    )
    assert anchor["schema_version"] == "stage-gate-activation-policy.v1"
    backup = next((shared / "activation-rollback-backups").glob("*"))
    assert (
        backup / "shared/activation/session-records/v2.json"
    ).is_file()
    assert (
        backup / "shared/stage-close-gate/attestations/v2.json"
    ).is_file()
    assert (
        backup / "repository/activation-evidence/v2.package.json"
    ).is_file()
    assert not (
        tmp_path / ".ai-sdlc/policies/activation-evidence"
    ).exists()
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_policy_digest"] == policy.policy_digest
    assert manifest["schema_version"] == "activation-v1-rollback-backup.v1"
    assert manifest["artifact_kind"] == "activation-v1-rollback-backup"
    assert manifest["manifest_digest"].startswith("sha256:")

    recovered = runner.invoke(
        app,
        [
            "activation",
            "rollback-recover",
            str(tmp_path),
            "--backup-id",
            manifest["backup_id"],
            "--execute",
            "--yes",
        ],
    )

    assert recovered.exit_code == 0, recovered.output
    anchor = json.loads(
        (tmp_path / ACTIVATION_POLICY_ANCHOR).read_text(encoding="utf-8")
    )
    assert anchor["schema_version"] == "stage-gate-activation-policy.v2"
    assert (shared / "activation/session-records/v2.json").is_file()
    assert (shared / "stage-close-gate/attestations/v2.json").is_file()
    assert (
        tmp_path / ".ai-sdlc/policies/activation-evidence/v2.package.json"
    ).is_file()


def test_rollback_restore_refuses_state_drift_after_export(
    tmp_path: Path,
) -> None:
    _init_repository(tmp_path)
    write_activation_policy_anchor(tmp_path, baseline_activation_policy())
    project_id = resolve_repository_project_id(tmp_path)
    shared = resolve_canonical_shared_state(tmp_path, project_id)
    _write_json(shared / "activation/session-records/one.json", {"value": 1})
    bundle = tmp_path / "rollback-v1.json"
    assert runner.invoke(
        app,
        [
            "activation",
            "rollback-export",
            str(tmp_path),
            "--output",
            str(bundle),
        ],
    ).exit_code == 0
    _write_json(shared / "activation/session-records/two.json", {"value": 2})

    restored = runner.invoke(
        app,
        [
            "activation",
            "rollback-restore",
            str(tmp_path),
            "--bundle",
            str(bundle),
            "--execute",
            "--yes",
        ],
    )

    assert restored.exit_code != 0
    assert "state changed" in restored.output
    assert (shared / "activation/session-records/two.json").is_file()


def test_rollback_restore_refuses_self_consistent_forged_legacy_projection(
    tmp_path: Path,
) -> None:
    _init_repository(tmp_path)
    policy = baseline_activation_policy()
    write_activation_policy_anchor(tmp_path, policy)
    project_id = resolve_repository_project_id(tmp_path)
    shared = resolve_canonical_shared_state(tmp_path, project_id)
    source = shared / "activation/session-records/one.json"
    _write_json(source, {"value": 1})
    bundle = tmp_path / "rollback-v1.json"
    activation_rollback.export_v1_rollback_bundle(tmp_path, bundle)
    payload = json.loads(bundle.read_text(encoding="utf-8"))
    payload["legacy_policy"]["policy_id"] = "forged.stage-gate-activation"
    payload["legacy_policy"].pop("policy_digest")
    payload["legacy_policy"]["policy_digest"] = canonical_digest(
        payload["legacy_policy"],
        CanonicalizationPolicy(),
    )
    payload.pop("bundle_digest")
    payload["bundle_digest"] = canonical_digest(
        payload,
        CanonicalizationPolicy(),
    )
    _write_json(bundle, payload)

    with pytest.raises(ValueError, match="legacy policy projection mismatch"):
        activation_rollback.restore_v1_rollback_bundle(tmp_path, bundle)

    assert read_activation_policy_anchor(tmp_path) == policy
    assert source.is_file()
    assert not (
        shared / "activation-rollback-operations"
    ).exists()
    assert not (
        shared / "activation-rollback-backups"
    ).exists()


def test_rollback_restore_resumes_after_anchor_commit_fault(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_repository(tmp_path)
    write_activation_policy_anchor(tmp_path, baseline_activation_policy())
    project_id = resolve_repository_project_id(tmp_path)
    shared = resolve_canonical_shared_state(tmp_path, project_id)
    _write_json(shared / "activation/session-records/one.json", {"value": 1})
    bundle = tmp_path / "rollback-v1.json"
    activation_rollback.export_v1_rollback_bundle(tmp_path, bundle)
    original = activation_rollback.atomic_write_json
    failed = False

    def fail_after_anchor(path: Path, payload: object) -> None:
        nonlocal failed
        original(path, payload)
        if (
            not failed
            and path.parent.name == "activation-rollback-operations"
            and isinstance(payload, dict)
            and payload.get("state") == "anchor-committed"
        ):
            failed = True
            raise RuntimeError("fault after anchor commit")

    monkeypatch.setattr(
        activation_rollback,
        "atomic_write_json",
        fail_after_anchor,
    )
    with pytest.raises(RuntimeError, match="fault after anchor commit"):
        activation_rollback.restore_v1_rollback_bundle(tmp_path, bundle)
    with pytest.raises(ValueError, match="rollback operation is incomplete"):
        activation_policy_store.current_activation_policy(tmp_path)

    monkeypatch.setattr(
        activation_rollback,
        "atomic_write_json",
        original,
    )
    backup = activation_rollback.restore_v1_rollback_bundle(tmp_path, bundle)

    activation_rollback.validate_v1_rollback_view(tmp_path)
    assert (
        backup / "shared/activation/session-records/one.json"
    ).is_file()
    operation = json.loads(
        next(
            (
                shared / "activation-rollback-operations"
            ).glob("*.json")
        ).read_text(encoding="utf-8")
    )
    assert operation["state"] == "completed"


def test_rollback_recover_refuses_a_tampered_backup(
    tmp_path: Path,
) -> None:
    _init_repository(tmp_path)
    write_activation_policy_anchor(tmp_path, baseline_activation_policy())
    project_id = resolve_repository_project_id(tmp_path)
    shared = resolve_canonical_shared_state(tmp_path, project_id)
    _write_json(shared / "activation/session-records/one.json", {"value": 1})
    bundle = tmp_path / "rollback-v1.json"
    activation_rollback.export_v1_rollback_bundle(tmp_path, bundle)
    backup = activation_rollback.restore_v1_rollback_bundle(tmp_path, bundle)
    (backup / "shared/activation/session-records/one.json").unlink()
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))

    with pytest.raises(ValueError, match="rollback transfer state is incomplete"):
        activation_rollback.recover_v2_rollback_backup(
            tmp_path,
            manifest["backup_id"],
        )

    anchor = json.loads(
        (tmp_path / ACTIVATION_POLICY_ANCHOR).read_text(encoding="utf-8")
    )
    assert anchor["schema_version"] == "stage-gate-activation-policy.v1"
    operation = json.loads(
        next(
            (
                shared / "activation-rollback-operations"
            ).glob("*.json")
        ).read_text(encoding="utf-8")
    )
    assert operation["state"] == "recovering"


def test_rollback_restore_resumes_after_interrupted_cross_volume_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_repository(tmp_path)
    write_activation_policy_anchor(tmp_path, baseline_activation_policy())
    project_id = resolve_repository_project_id(tmp_path)
    shared = resolve_canonical_shared_state(tmp_path, project_id)
    _write_json(shared / "activation/session-records/one.json", {"value": 1})
    _write_json(shared / "activation/session-records/two.json", {"value": 2})
    bundle = tmp_path / "rollback-v1.json"
    activation_rollback.export_v1_rollback_bundle(tmp_path, bundle)
    original_copy = activation_rollback.shutil.copy2
    copied = 0

    def interrupt_second_copy(source, destination, *args, **kwargs):
        nonlocal copied
        copied += 1
        if copied == 2:
            raise OSError("simulated cross-volume copy interruption")
        return original_copy(source, destination, *args, **kwargs)

    monkeypatch.setattr(
        activation_rollback.shutil,
        "copy2",
        interrupt_second_copy,
    )
    with pytest.raises(OSError, match="cross-volume copy interruption"):
        activation_rollback.restore_v1_rollback_bundle(tmp_path, bundle)

    backup = next((shared / "activation-rollback-backups").glob("*"))
    assert (shared / "activation").is_dir()
    assert (backup / "shared/activation").is_dir()
    monkeypatch.setattr(activation_rollback.shutil, "copy2", original_copy)

    resumed = activation_rollback.restore_v1_rollback_bundle(tmp_path, bundle)

    assert resumed == backup
    activation_rollback.validate_v1_rollback_view(tmp_path)
    assert (
        backup / "shared/activation/session-records/one.json"
    ).is_file()
    assert (
        backup / "shared/activation/session-records/two.json"
    ).is_file()


def test_rollback_recover_aborts_prepared_partial_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_repository(tmp_path)
    policy = baseline_activation_policy()
    write_activation_policy_anchor(tmp_path, policy)
    project_id = resolve_repository_project_id(tmp_path)
    shared = resolve_canonical_shared_state(tmp_path, project_id)
    _write_json(shared / "activation/session-records/one.json", {"value": 1})
    _write_json(shared / "activation/session-records/two.json", {"value": 2})
    bundle = tmp_path / "rollback-v1.json"
    activation_rollback.export_v1_rollback_bundle(tmp_path, bundle)
    original_copy = activation_rollback.shutil.copy2
    copied = 0

    def interrupt_second_copy(source, destination, *args, **kwargs):
        nonlocal copied
        copied += 1
        if copied == 2:
            raise OSError("simulated prepared transfer interruption")
        return original_copy(source, destination, *args, **kwargs)

    monkeypatch.setattr(
        activation_rollback.shutil,
        "copy2",
        interrupt_second_copy,
    )
    with pytest.raises(OSError, match="prepared transfer interruption"):
        activation_rollback.restore_v1_rollback_bundle(tmp_path, bundle)
    operation = json.loads(
        next(
            (shared / "activation-rollback-operations").glob("*.json")
        ).read_text(encoding="utf-8")
    )
    assert operation["state"] == "prepared"

    monkeypatch.setattr(activation_rollback.shutil, "copy2", original_copy)
    activation_rollback.recover_v2_rollback_backup(
        tmp_path,
        operation["backup_id"],
    )

    assert read_activation_policy_anchor(tmp_path) == policy
    assert (
        shared / "activation/session-records/one.json"
    ).is_file()
    assert (
        shared / "activation/session-records/two.json"
    ).is_file()
    recovered = json.loads(
        next(
            (shared / "activation-rollback-operations").glob("*.json")
        ).read_text(encoding="utf-8")
    )
    assert recovered["state"] == "recovered"
    assert recovered["recovery_from_state"] == "prepared"


def test_rollback_recover_aborts_state_backed_up_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_repository(tmp_path)
    policy = baseline_activation_policy()
    write_activation_policy_anchor(tmp_path, policy)
    project_id = resolve_repository_project_id(tmp_path)
    shared = resolve_canonical_shared_state(tmp_path, project_id)
    _write_json(shared / "activation/session-records/one.json", {"value": 1})
    bundle = tmp_path / "rollback-v1.json"
    activation_rollback.export_v1_rollback_bundle(tmp_path, bundle)

    def interrupt_before_anchor(*_args, **_kwargs) -> None:
        raise OSError("simulated state-backed-up interruption")

    monkeypatch.setattr(
        activation_rollback,
        "_commit_legacy_anchor",
        interrupt_before_anchor,
    )
    with pytest.raises(OSError, match="state-backed-up interruption"):
        activation_rollback.restore_v1_rollback_bundle(tmp_path, bundle)
    operation = json.loads(
        next(
            (shared / "activation-rollback-operations").glob("*.json")
        ).read_text(encoding="utf-8")
    )
    assert operation["state"] == "state-backed-up"

    activation_rollback.recover_v2_rollback_backup(
        tmp_path,
        operation["backup_id"],
    )

    assert read_activation_policy_anchor(tmp_path) == policy
    assert (
        shared / "activation/session-records/one.json"
    ).is_file()
    recovered = json.loads(
        next(
            (shared / "activation-rollback-operations").glob("*.json")
        ).read_text(encoding="utf-8")
    )
    assert recovered["state"] == "recovered"
    assert recovered["recovery_from_state"] == "state-backed-up"


def test_rollback_restore_rebuilds_manifest_after_prepared_double_write_fault(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_repository(tmp_path)
    write_activation_policy_anchor(tmp_path, baseline_activation_policy())
    project_id = resolve_repository_project_id(tmp_path)
    shared = resolve_canonical_shared_state(tmp_path, project_id)
    _write_json(shared / "activation/session-records/one.json", {"value": 1})
    bundle = tmp_path / "rollback-v1.json"
    activation_rollback.export_v1_rollback_bundle(tmp_path, bundle)
    original = activation_rollback._write_backup_manifest
    failed = False

    def fail_first_manifest(backup: Path, operation: dict[str, object]) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("simulated first manifest write failure")
        original(backup, operation)

    monkeypatch.setattr(
        activation_rollback,
        "_write_backup_manifest",
        fail_first_manifest,
    )
    with pytest.raises(OSError, match="first manifest write failure"):
        activation_rollback.restore_v1_rollback_bundle(tmp_path, bundle)
    backup = next((shared / "activation-rollback-backups").glob("*"))
    (backup / ".0123456789abcdef.tmp").write_text(
        "orphaned atomic manifest",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        activation_rollback,
        "_write_backup_manifest",
        original,
    )
    backup = activation_rollback.restore_v1_rollback_bundle(tmp_path, bundle)

    activation_rollback.validate_v1_rollback_view(tmp_path)
    assert (backup / "manifest.json").is_file()
    assert (
        backup / "shared/activation/session-records/one.json"
    ).is_file()


def test_rollback_restore_refuses_late_source_file_before_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_repository(tmp_path)
    write_activation_policy_anchor(tmp_path, baseline_activation_policy())
    project_id = resolve_repository_project_id(tmp_path)
    shared = resolve_canonical_shared_state(tmp_path, project_id)
    source = shared / "activation/session-records"
    _write_json(source / "one.json", {"value": 1})
    bundle = tmp_path / "rollback-v1.json"
    activation_rollback.export_v1_rollback_bundle(tmp_path, bundle)
    original_copy = activation_rollback.shutil.copy2
    injected = False

    def inject_late_file(origin, destination, *args, **kwargs):
        nonlocal injected
        result = original_copy(origin, destination, *args, **kwargs)
        if not injected:
            injected = True
            _write_json(source / "late.json", {"value": "late"})
        return result

    monkeypatch.setattr(
        activation_rollback.shutil,
        "copy2",
        inject_late_file,
    )

    with pytest.raises(ValueError, match="transfer state is incomplete"):
        activation_rollback.restore_v1_rollback_bundle(tmp_path, bundle)

    backup = next((shared / "activation-rollback-backups").glob("*"))
    assert (source / "late.json").is_file()
    assert not (
        backup / "shared/activation/session-records/late.json"
    ).exists()


def test_rollback_round_trip_preserves_empty_directories_and_reserved_filename(
    tmp_path: Path,
) -> None:
    _init_repository(tmp_path)
    write_activation_policy_anchor(tmp_path, baseline_activation_policy())
    project_id = resolve_repository_project_id(tmp_path)
    shared = resolve_canonical_shared_state(tmp_path, project_id)
    activation = shared / "activation"
    (activation / "empty/nested").mkdir(parents=True)
    (activation / "@directory").write_text("real file\n", encoding="utf-8")
    bundle = tmp_path / "rollback-v1.json"
    activation_rollback.export_v1_rollback_bundle(tmp_path, bundle)

    backup = activation_rollback.restore_v1_rollback_bundle(tmp_path, bundle)
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    activation_rollback.recover_v2_rollback_backup(
        tmp_path,
        manifest["backup_id"],
    )

    assert (activation / "empty/nested").is_dir()
    assert (activation / "@directory").read_text(encoding="utf-8") == "real file\n"


def test_rollback_export_refuses_an_enforcing_policy(tmp_path: Path) -> None:
    _init_repository(tmp_path)
    payload = baseline_activation_policy().model_dump(
        mode="json",
        exclude={"policy_digest"},
    )
    payload.update(
        {
            "active_phase": 2,
            "enabled_risk_levels": ["low"],
            "previous_policy_digest": _digest("previous"),
            "activation_assessment_digest": _digest("assessment"),
        }
    )
    policy = type(baseline_activation_policy()).model_validate(payload)
    write_activation_policy_anchor(tmp_path, policy)

    result = runner.invoke(
        app,
        [
            "activation",
            "rollback-export",
            str(tmp_path),
            "--output",
            str(tmp_path / "rollback.json"),
        ],
    )

    assert result.exit_code != 0
    assert "phase 1" in result.output


def test_rollback_export_refuses_output_inside_managed_state(
    tmp_path: Path,
) -> None:
    _init_repository(tmp_path)
    write_activation_policy_anchor(tmp_path, baseline_activation_policy())
    managed = tmp_path / ".ai-sdlc/policies/activation-evidence/rollback.json"

    result = runner.invoke(
        app,
        [
            "activation",
            "rollback-export",
            str(tmp_path),
            "--output",
            str(managed),
        ],
    )

    assert result.exit_code != 0
    assert "outside managed state" in result.output
    assert not managed.exists()


def test_activation_policy_refuses_tampered_terminal_rollback_operation(
    tmp_path: Path,
) -> None:
    _init_repository(tmp_path)
    write_activation_policy_anchor(tmp_path, baseline_activation_policy())
    project_id = resolve_repository_project_id(tmp_path)
    shared = resolve_canonical_shared_state(tmp_path, project_id)
    bundle = tmp_path / "rollback-v1.json"
    activation_rollback.export_v1_rollback_bundle(tmp_path, bundle)
    activation_rollback.restore_v1_rollback_bundle(tmp_path, bundle)
    operation_path = next(
        (shared / "activation-rollback-operations").glob("*.json")
    )
    operation = json.loads(operation_path.read_text(encoding="utf-8"))
    operation["completed_at"] = "2099-01-01T00:00:00Z"
    _write_json(operation_path, operation)

    with pytest.raises(ValueError, match="operation digest mismatch"):
        activation_policy_store.current_activation_policy(tmp_path)


def _init_repository(root: Path) -> None:
    _git(root, "init", "--initial-branch=main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("# Test\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "initial")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def _digest(label: str) -> str:
    import hashlib

    return f"sha256:{hashlib.sha256(label.encode()).hexdigest()}"
