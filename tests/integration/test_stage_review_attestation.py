from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_sdlc.cli.main import app
from ai_sdlc.core.stage_review.ci_certificate import (
    CiCertificateVerificationError,
    read_ci_certificate_bundle,
    verify_ci_certificate_bundle,
)
from ai_sdlc.core.stage_review.ci_certificate_export import (
    export_latest_ci_certificate_bundle,
)
from ai_sdlc.core.stage_review.shadow_planning_store import (
    _persist_shadow_plan as persist_shadow_plan,
)
from ai_sdlc.core.stage_review.stage_close_product_runtime import (
    authorize_product_stage_close,
)
from ai_sdlc.core.stage_review.stage_review_plan_runtime import HeldStageReviewPlan
from tests.integration.test_canonical_stage_review_executor import _executor_rig
from tests.integration.test_stage_close_product_runtime import (
    _enforce_decision,
    _held_plan,
    _prepared_close,
)


def test_ci_verifier_replays_certificate_without_writing(tmp_path: Path) -> None:
    bundle_path, tested_commit = _committed_bundle(tmp_path)
    before = _tree_digest(tmp_path)
    bundle = read_ci_certificate_bundle(bundle_path)

    result = verify_ci_certificate_bundle(
        tmp_path,
        bundle,
        tested_commit=tested_commit,
        expected_stage_key="implementation",
        expected_close_kind="implementation-close",
        expected_policy_digest=bundle.candidate.policy_digests[0],
        expected_mode="enforce",
    )

    assert result.valid is True
    assert result.tested_commit == tested_commit
    assert result.reviewed_commit != tested_commit
    assert before == _tree_digest(tmp_path)


def test_ci_verifier_rejects_certificate_for_another_stage_purpose(
    tmp_path: Path,
) -> None:
    bundle_path, tested_commit = _committed_bundle(tmp_path)
    bundle = read_ci_certificate_bundle(bundle_path)

    with pytest.raises(CiCertificateVerificationError, match="certificate purpose"):
        verify_ci_certificate_bundle(
            tmp_path,
            bundle,
            tested_commit=tested_commit,
            expected_stage_key="local-pr-review",
            expected_close_kind="local-pr-review-attest",
            expected_policy_digest=bundle.candidate.policy_digests[0],
            expected_mode="enforce",
        )


def test_ci_verifier_rejects_protected_change_after_review(tmp_path: Path) -> None:
    bundle_path, _ = _committed_bundle(tmp_path)
    bundle = read_ci_certificate_bundle(bundle_path)
    (tmp_path / "candidate.py").write_text("VALUE = 3\n", encoding="utf-8")
    tested_commit = _commit(tmp_path, "post-review source change", "candidate.py")

    with pytest.raises(
        CiCertificateVerificationError,
        match="post-review protected change",
    ):
        verify_ci_certificate_bundle(
            tmp_path,
            bundle,
            tested_commit=tested_commit,
            expected_stage_key="implementation",
            expected_close_kind="implementation-close",
            expected_policy_digest=bundle.candidate.policy_digests[0],
            expected_mode="enforce",
        )


def test_ci_verifier_rejects_tampered_certificate_digest(tmp_path: Path) -> None:
    bundle_path, tested_commit = _committed_bundle(tmp_path)
    payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    payload["certificate"]["target_status"] = "tampered"
    bundle_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CiCertificateVerificationError, match="bundle is invalid"):
        verify_ci_certificate_bundle(
            tmp_path,
            read_ci_certificate_bundle(bundle_path),
            tested_commit=tested_commit,
        )


def test_ci_verifier_rejects_tampered_authority_evidence(tmp_path: Path) -> None:
    bundle_path, tested_commit = _committed_bundle(tmp_path)
    payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    payload["authority_evidence"]["current_reservation"]["state"] = "final"
    bundle_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CiCertificateVerificationError, match="bundle is invalid"):
        verify_ci_certificate_bundle(
            tmp_path,
            read_ci_certificate_bundle(bundle_path),
            tested_commit=tested_commit,
        )


def test_cli_stage_certificate_verifier_is_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle_path, tested_commit = _committed_bundle(tmp_path)
    bundle = read_ci_certificate_bundle(bundle_path)
    before = _tree_digest(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "verify",
            "stage-certificate",
            "--bundle",
            str(bundle_path),
            "--tested-commit",
            tested_commit,
            "--expected-stage-key",
            "implementation",
            "--expected-close-kind",
            "implementation-close",
            "--expected-policy-digest",
            bundle.candidate.policy_digests[0],
            "--expected-mode",
            "enforce",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["valid"] is True
    assert before == _tree_digest(tmp_path)


def test_cli_stage_certificate_policy_is_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bundle_path, tested_commit = _committed_bundle(tmp_path)
    base_commit = _git(tmp_path, "rev-list", "--max-parents=0", "HEAD")
    before = _tree_digest(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "verify",
            "stage-certificate-policy",
            "--base-commit",
            base_commit,
            "--tested-commit",
            tested_commit,
            "--json",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0, result.output
    assert payload["valid"] is True
    assert payload["mode"] == "shadow"
    assert payload["certificate_required"] is False
    assert before == _tree_digest(tmp_path)


def _committed_bundle(root: Path) -> tuple[Path, str]:
    sessions = []
    rig = _executor_rig(
        root,
        transport_available=True,
        on_authorized=sessions.append,
        source_kind="local-git-range",
    )
    persist_shadow_plan(
        root,
        rig.request.proposal,
        rig.request.plan,
        rig.request.source_snapshot,
    )
    outcome = rig.executor.execute(rig.request)
    assert outcome.status == "completed", outcome
    assert sessions
    prepared = _prepared_close(root)
    runtime = HeldStageReviewPlan(
        planned=rig.request.proposal,
        held=_held_plan(rig.request),
        source_snapshot=rig.request.source_snapshot,
        refs={},
    )

    def writer() -> dict[str, str]:
        path = root / prepared.close_artifact_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"status":"closed"}\n', encoding="utf-8")
        return {"status": "ready", "loop_status": "closed"}

    authorize_product_stage_close(
        prepared,
        _enforce_decision(root, prepared),
        runtime,
        sessions[0],
        writer,
    )
    bundle_path = export_latest_ci_certificate_bundle(
        root,
        close_kind="implementation-close",
    )
    assert bundle_path is not None
    relative = bundle_path.relative_to(root).as_posix()
    return bundle_path, _commit(root, "certificate evidence", relative)


def _commit(root: Path, message: str, *paths: str) -> str:
    _git(root, "add", "--all" if not paths else "--", *paths)
    _git(root, "commit", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _tree_digest(root: Path) -> str:
    payload = "\n".join(
        f"{path.relative_to(root).as_posix()}:{hashlib.sha256(path.read_bytes()).hexdigest()}"
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    )
    return hashlib.sha256(payload.encode()).hexdigest()
