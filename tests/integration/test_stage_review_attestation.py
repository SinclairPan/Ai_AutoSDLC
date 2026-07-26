from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_sdlc.cli.main import app
from ai_sdlc.core.config import save_project_config
from ai_sdlc.core.lean_code_runtime import LeanCheckOptions, run_lean_check
from ai_sdlc.core.pr_review_provider import MockReviewerFixture
from ai_sdlc.core.pr_review_service import (
    PRReviewStartOptions,
    close_pr_review,
    start_pr_review,
)
from ai_sdlc.core.stage_review import codex_review_runtime
from ai_sdlc.core.stage_review.activation_policy_anchor import (
    write_activation_policy_anchor,
)
from ai_sdlc.core.stage_review.activation_policy_store import (
    _advance_activation_policy_from_evidence as advance_activation_policy_from_evidence,
)
from ai_sdlc.core.stage_review.activation_store import (
    _read_activation_session_records as read_activation_session_records,
)
from ai_sdlc.core.stage_review.artifacts import (
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.ci_certificate import (
    CI_CERTIFICATE_BUNDLE_PATH,
    CiCertificateVerificationError,
    read_ci_certificate_bundle,
    verify_ci_certificate_bundle,
)
from ai_sdlc.core.stage_review.ci_certificate_export import (
    export_ci_certificate_bundle,
)
from ai_sdlc.core.stage_review.isolation_backend_identity import (
    _build_trusted_backend_release_manifest as build_trusted_backend_release_manifest,
)
from ai_sdlc.core.stage_review.isolation_backend_identity import (
    _host_backend_platform as host_backend_platform,
)
from ai_sdlc.core.stage_review.shadow_planning_store import (
    _persist_shadow_plan as persist_shadow_plan,
)
from ai_sdlc.core.stage_review.stage_close_product_runtime import (
    authorize_product_stage_close,
)
from ai_sdlc.core.stage_review.stage_review_plan_runtime import (
    HeldStageReviewPlan,
)
from ai_sdlc.models.project import ProjectConfig
from tests.integration.test_canonical_stage_review_executor import (
    _executor_for_request,
    _executor_rig,
)
from tests.integration.test_stage_close_product_runtime import (
    _enforce_decision,
    _held_plan,
    _prepared_close,
)
from tests.unit.stage_review.test_activation_policy_store import _eligible_evidence
from tests.unit.test_lean_code_pr_review import _seed_lean_loop


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


def test_phase_two_local_pr_user_journey_exports_and_replays_ci_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    implementation_loop_id = "implementation-phase-two"
    _seed_lean_loop(
        tmp_path,
        implementation_loop_id,
        work_item_id="wi-review",
    )
    _git(tmp_path, "config", "core.autocrlf", "false")
    _git(tmp_path, "config", "core.eol", "lf")
    _git(
        tmp_path,
        "remote",
        "add",
        "origin",
        "https://github.com/SinclairPan/Ai_AutoSDLC.git",
    )
    (tmp_path / "src/app.py").write_text(
        "def _small():\n    return 0\n",
        encoding="utf-8",
    )
    policy, activation_assessment = advance_activation_policy_from_evidence(
        tmp_path,
        _eligible_evidence(tmp_path, monkeypatch),
    )
    assert activation_assessment.eligible
    (tmp_path / "activation-evidence-package.json").unlink()
    (tmp_path / "activation-evidence-attestation.jsonl").unlink()
    write_activation_policy_anchor(tmp_path, policy)
    save_project_config(tmp_path, ProjectConfig(agent_target="codex"))
    base_commit = _commit(tmp_path, "protected phase two policy")
    (tmp_path / "src/app.py").write_text(
        "def _small():\n    return 1\n",
        encoding="utf-8",
    )
    reviewed_head = _commit(tmp_path, "reviewed feature", "src/app.py")
    lean = run_lean_check(
        LeanCheckOptions(
            root=tmp_path,
            loop_id=implementation_loop_id,
            source_kind="local-git-range",
            base_ref=base_commit,
            head_ref=reviewed_head,
        )
    )
    assert lean.status == "ready", lean.blocker

    started = start_pr_review(
        PRReviewStartOptions(
            root=tmp_path,
            base_ref=base_commit,
            head_ref=reviewed_head,
            provider_id="mock-reviewer",
            review_id="review-phase-two",
            mock_fixture=MockReviewerFixture.CLEAN,
        )
    )

    policy_checks = []
    hold_checks = []
    released_sessions = []
    built_executor_sessions = []
    captured_closes = []
    read_policy = codex_review_runtime.current_activation_policy
    read_holds = codex_review_runtime.active_activation_safety_holds_for_lineage
    release_plan = codex_review_runtime.release_stage_review_plan
    enforce_close = codex_review_runtime.CodexStageReviewExecutor.enforce_close

    def observed_policy(root: Path):
        current = read_policy(root)
        policy_checks.append(current.policy_digest)
        return current

    def observed_holds(root: Path, *, policy):
        holds = read_holds(root, policy=policy)
        hold_checks.append(policy.policy_digest)
        return holds

    def observed_release(runtime):
        released_sessions.append(runtime.planned.candidate.review_session_id)
        return release_plan(runtime)

    def deterministic_executor(root, request, **kwargs):
        built_executor_sessions.append(request.candidate.review_session_id)
        return _executor_for_request(
            root,
            request,
            on_authorized=kwargs.get("on_authorized"),
        )

    def capture_enforce_close(self, prepared, decision, preflight, writer):
        captured_closes.append((self, prepared, decision, preflight))
        return enforce_close(self, prepared, decision, preflight, writer)

    trusted_release = _trusted_test_release()
    monkeypatch.setattr(
        codex_review_runtime,
        "resolve_codex_runtime_prerequisites",
        lambda: ("deterministic-provider", trusted_release),
    )
    monkeypatch.setattr(
        codex_review_runtime,
        "_build_executor",
        deterministic_executor,
    )
    monkeypatch.setattr(
        codex_review_runtime,
        "current_activation_policy",
        observed_policy,
    )
    monkeypatch.setattr(
        codex_review_runtime,
        "active_activation_safety_holds_for_lineage",
        observed_holds,
    )
    monkeypatch.setattr(
        codex_review_runtime,
        "release_stage_review_plan",
        observed_release,
    )
    monkeypatch.setattr(
        codex_review_runtime.CodexStageReviewExecutor,
        "enforce_close",
        capture_enforce_close,
    )

    closed = close_pr_review(tmp_path)
    assert closed.status == "closed"
    assert len(
        tuple(
            item
            for item in read_activation_session_records(tmp_path)
            if item.observation.mode == "enforce"
        )
    ) == 1
    assert len(captured_closes) == 1
    executor_count = len(built_executor_sessions)
    release_count = len(released_sessions)
    replay_writes = 0

    def replay_writer():
        nonlocal replay_writes
        replay_writes += 1
        raise AssertionError("an already committed Enforce close must not rewrite")

    enforcer, prepared, decision, preflight = captured_closes[0]
    assert preflight.candidate is not None
    shared = resolve_canonical_shared_state(
        tmp_path,
        preflight.candidate.project_id,
    )
    activation_record = next(
        path
        for path in (shared / "activation/session-records").glob("*.json")
        if json.loads(path.read_text(encoding="utf-8"))["observation"]["session_id"]
        == preflight.candidate.review_session_id
    )
    activation_record.unlink()
    assert all(
        item.observation.session_id != preflight.candidate.review_session_id
        for item in read_activation_session_records(tmp_path)
    )
    completion_path = (
        shared
        / "stage-review-sessions/sessions"
        / preflight.candidate.work_item_id
        / preflight.candidate.stage_instance_id
        / preflight.candidate.review_session_id
        / "completion.json"
    )
    original_completion = json.loads(completion_path.read_text(encoding="utf-8"))
    tampered_completion = dict(original_completion)
    tampered_completion["panel_plan_digest"] = f"sha256:{'0' * 64}"
    tampered_completion["completion_digest"] = ""
    completion_path.write_text(
        json.dumps(tampered_completion),
        encoding="utf-8",
    )
    with pytest.raises(
        codex_review_runtime.StageCloseGateUnavailableError,
        match="activation-session-recovery-failed",
    ):
        enforce_close(
            enforcer,
            prepared,
            decision,
            preflight,
            replay_writer,
        )
    assert all(
        item.observation.session_id != preflight.candidate.review_session_id
        for item in read_activation_session_records(tmp_path)
    )
    completion_path.write_text(
        json.dumps(original_completion),
        encoding="utf-8",
    )
    replayed = enforce_close(
        enforcer,
        prepared,
        decision,
        preflight,
        replay_writer,
    )

    assert replayed == closed
    assert replay_writes == 0
    assert len(built_executor_sessions) == executor_count
    assert len(released_sessions) == release_count
    monkeypatch.chdir(tmp_path)
    attested = CliRunner().invoke(
        app,
        ["pr-review", "attest", "--json"],
        catch_exceptions=False,
    )

    assert attested.exit_code == 0, attested.output
    activation_sessions = tuple(
        item
        for item in read_activation_session_records(tmp_path)
        if item.observation.mode == "enforce"
    )
    assert len(activation_sessions) == 2
    assert {item.observation.mode for item in activation_sessions} == {"enforce"}
    assert policy_checks == [policy.policy_digest] * 4
    assert hold_checks == [policy.policy_digest] * 2
    assert len(set(released_sessions)) == 2
    attested_payload = json.loads(attested.output)
    assert attested_payload["stage_review_session_id"]
    assert attested_payload["stage_close_certificate_id"]
    bundle_path = Path(attested_payload["ci_certificate_bundle_path"])
    assert bundle_path.relative_to(tmp_path).as_posix() == CI_CERTIFICATE_BUNDLE_PATH
    bundle = read_ci_certificate_bundle(bundle_path)
    tested_commit = _commit(
        tmp_path,
        "publish local PR review certificate",
        CI_CERTIFICATE_BUNDLE_PATH,
    )
    policy_result = CliRunner().invoke(
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

    assert policy_result.exit_code == 0, policy_result.output
    policy_payload = json.loads(policy_result.output)
    assert policy_payload["mode"] == "enforce"
    assert policy_payload["certificate_required"] is True
    assert policy_payload["policy_digest"] == policy.policy_digest
    before = _tree_digest(tmp_path)
    verification_command = [
        "verify",
        "stage-certificate",
        "--bundle",
        str(bundle_path),
        "--tested-commit",
        tested_commit,
        "--expected-stage-key",
        "local-pr-review",
        "--expected-close-kind",
        "local-pr-review-attest",
        "--expected-policy-digest",
        policy_payload["policy_digest"],
        "--expected-mode",
        policy_payload["mode"],
        "--json",
    ]
    verification = CliRunner().invoke(app, verification_command)

    assert verification.exit_code == 0, verification.output
    assert json.loads(verification.output)["valid"] is True
    assert before == _tree_digest(tmp_path)
    assert bundle.candidate.stage_key == "local-pr-review"
    assert bundle.certificate.close_kind == "local-pr-review-attest"
    assert (
        bundle.certificate.scope.session_id
        == attested_payload["stage_review_session_id"]
    )
    assert (
        bundle.certificate.certificate_id
        == attested_payload["stage_close_certificate_id"]
    )
    assert bundle.certificate_request.intent.close_kind == "local-pr-review-attest"
    assert bundle.candidate.stage_instance_id == started.review_id
    assert bundle.candidate.loop_id == started.loop_id
    review_pack_path = Path(started.review_pack_path).relative_to(tmp_path).as_posix()
    source_resolution_path = (
        Path(started.source_resolution_path).relative_to(tmp_path).as_posix()
    )
    assert review_pack_path in bundle.candidate.input_artifacts
    assert source_resolution_path in bundle.candidate.input_artifacts
    assert any(
        path.endswith("/diff.patch") for path in bundle.candidate.input_artifacts
    )
    assert bundle.source_snapshot.base_commit == base_commit
    assert bundle.source_snapshot.head_commit == reviewed_head
    assert bundle.reviewed_commit == reviewed_head
    assert tuple(bundle.candidate.policy_digests) == (policy.policy_digest,)
    assert (
        _git(
            tmp_path,
            "diff",
            "--name-only",
            reviewed_head,
            tested_commit,
        )
        == CI_CERTIFICATE_BUNDLE_PATH
    )
    bundle_path.unlink()
    missing = CliRunner().invoke(app, verification_command)
    assert missing.exit_code != 0


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


def test_cli_certificate_verifiers_accept_an_explicit_candidate_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle_path, tested_commit = _committed_bundle(tmp_path)
    bundle = read_ci_certificate_bundle(bundle_path)
    base_commit = _git(tmp_path, "rev-list", "--max-parents=0", "HEAD")
    outside = tmp_path.parent / f"{tmp_path.name}-trusted-verifier"
    outside.mkdir()
    monkeypatch.chdir(outside)

    policy = CliRunner().invoke(
        app,
        [
            "verify",
            "stage-certificate-policy",
            "--root",
            str(tmp_path),
            "--base-commit",
            base_commit,
            "--tested-commit",
            tested_commit,
            "--json",
        ],
    )
    verification = CliRunner().invoke(
        app,
        [
            "verify",
            "stage-certificate",
            "--root",
            str(tmp_path),
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

    assert policy.exit_code == 0, policy.output
    assert verification.exit_code == 0, verification.output
    assert json.loads(policy.output)["valid"] is True
    assert json.loads(verification.output)["valid"] is True


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
    shared = resolve_canonical_shared_state(
        root,
        rig.request.candidate.project_id,
    )
    proof_paths = tuple(
        (shared / "stage-review-sessions/sessions").glob(
            "*/*/*/certificate-proofs/*.json"
        )
    )
    assert len(proof_paths) == 1
    bundle_path = export_ci_certificate_bundle(
        root,
        close_kind="implementation-close",
        stage_instance_id=rig.request.candidate.stage_instance_id,
        review_session_id=rig.request.candidate.review_session_id,
        certificate_id=proof_paths[0].stem,
    )
    assert bundle_path is not None
    relative = bundle_path.relative_to(root).as_posix()
    assert relative == CI_CERTIFICATE_BUNDLE_PATH
    return bundle_path, _commit(root, "certificate evidence", relative)


def _trusted_test_release():
    platform_id, architecture = host_backend_platform()
    return build_trusted_backend_release_manifest(
        backend_id="codex.permission-profile",
        contract_version="2026-07-01",
        exact_backend_version="0.0.0",
        ecosystem="npm",
        package_name="@openai/codex-test",
        package_version=f"0.0.0-test-{architecture}",
        platform_id=platform_id,
        architecture=architecture,
        package_integrity="sha512:test",
        shim_resolver_id="codex-npm-layout.v1",
        native_relative_path="codex",
        native_sha256=f"sha256:{'a' * 64}",
        profile_digest="sha256:test-profile",
        policy_pin_digest=f"sha256:{'b' * 64}",
        ci_attestation_subject="test-subject",
        ci_attestation_workflow_ref="test-workflow",
        ci_attestation_digest=f"sha256:{'c' * 64}",
        ci_attestation_verified=True,
        revocation_metadata_digest=f"sha256:{'d' * 64}",
        revoked=False,
    )


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
