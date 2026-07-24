from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_sdlc.core.stage_review import activation_evidence_ingestor
from ai_sdlc.core.stage_review.activation import (
    ActivationEvidence,
    ActivationProbeEvidence,
    ActivationSessionObservation,
    ActivationSessionRecord,
    IsolationPlatformEvidence,
)
from ai_sdlc.core.stage_review.activation_artifact_codec import (
    read_activation_session_records,
)
from ai_sdlc.core.stage_review.activation_evidence_ingestor import (
    ActivationEvidencePackage,
    ingest_activation_evidence_package,
)
from ai_sdlc.core.stage_review.activation_evidence_runtime import (
    _assemble_activation_evidence as assemble_activation_evidence,
)
from ai_sdlc.core.stage_review.activation_evidence_runtime import (
    _refresh_activation_policy_from_local_evidence as refresh_activation_policy_from_local_evidence,
)
from ai_sdlc.core.stage_review.activation_policy import baseline_activation_policy
from ai_sdlc.core.stage_review.activation_policy_anchor import (
    ACTIVATION_POLICY_ANCHOR,
    read_activation_policy_anchor,
    read_ci_activation_policy,
)
from ai_sdlc.core.stage_review.activation_policy_store import (
    ActivationPolicyPointer,
    current_activation_policy,
)
from ai_sdlc.core.stage_review.activation_policy_store import (
    _advance_activation_policy_from_evidence as advance_activation_policy_from_evidence,
)
from ai_sdlc.core.stage_review.artifacts import (
    resolve_canonical_shared_state,
    resolve_repository_project_id,
)
from ai_sdlc.core.stage_review.candidate import (
    CandidateManifest,
    candidate_binding_digest,
)
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.finding_models import FindingScope


def test_policy_read_is_baseline_and_write_free_before_activation(
    tmp_path: Path,
) -> None:
    policy = current_activation_policy(tmp_path)

    assert policy.active_phase == 1
    assert not (tmp_path / ".ai-sdlc").exists()


def test_v1_policy_is_verified_and_upgraded_without_breaking_ci_base(
    tmp_path: Path,
) -> None:
    _init_repository(tmp_path)
    anchor = tmp_path / ACTIVATION_POLICY_ANCHOR
    _write_payload(anchor, _legacy_policy_payload())
    _git(tmp_path, "add", ACTIVATION_POLICY_ANCHOR.as_posix())
    _git(tmp_path, "commit", "-m", "legacy activation policy")
    base_commit = _git(tmp_path, "rev-parse", "HEAD")
    legacy = read_activation_policy_anchor(tmp_path)
    assert legacy is not None
    assert legacy.compatibility_mode == "read-only-legacy"
    assert legacy.schema_version == "stage-gate-activation-policy.v2"
    assert legacy.extensions["migrated_from_schema_version"] == (
        "stage-gate-activation-policy.v1"
    )
    _write_payload(
        anchor,
        baseline_activation_policy().model_dump(mode="json"),
    )

    policy, source = read_ci_activation_policy(
        tmp_path,
        base_commit,
        baseline_activation_policy(),
    )

    assert source == "protected-candidate-anchor"
    assert policy.schema_version == "stage-gate-activation-policy.v2"
    assert policy.compatibility_mode == "strict"


def test_v1_local_pointer_yields_to_equivalent_v2_anchor(
    tmp_path: Path,
) -> None:
    _init_repository(tmp_path)
    project_id = resolve_repository_project_id(tmp_path)
    shared = resolve_canonical_shared_state(tmp_path, project_id)
    legacy = _legacy_policy_payload()
    legacy_digest = str(legacy["policy_digest"])
    _write_payload(
        shared
        / "activation/policies"
        / f"{legacy_digest.removeprefix('sha256:')}.json",
        legacy,
    )
    pointer = ActivationPolicyPointer(
        project_id=project_id,
        revision=1,
        policy_digest=legacy_digest,
        previous_policy_digest=_digest("pre-v1-policy"),
    )
    _write_json(shared / "activation/active-policy.json", pointer)
    _write_payload(
        tmp_path / ACTIVATION_POLICY_ANCHOR,
        baseline_activation_policy().model_dump(mode="json"),
    )

    selected = current_activation_policy(tmp_path)

    assert selected.policy_digest == baseline_activation_policy().policy_digest
    assert selected.compatibility_mode == "strict"


def test_v1_session_record_rebuilds_authoritative_scope(
    tmp_path: Path,
) -> None:
    _init_repository(tmp_path)
    project_id = resolve_repository_project_id(tmp_path)
    observation = ActivationSessionObservation(
        session_id="session-requirement-0",
        stage_key="requirement",
        risk_level="low",
        mode="shadow",
        completed_at="2026-07-01T00:00:00+00:00",
    )
    candidate = _candidate_manifest(project_id, observation)
    shared = resolve_canonical_shared_state(tmp_path, project_id)
    _write_json(
        shared / "shadow-planning" / observation.session_id / "candidate.json",
        candidate,
    )
    current = _session_records(project_id, (observation,))[0].model_copy(
        update={"candidate_manifest_digest": candidate_binding_digest(candidate)}
    )
    legacy = current.model_dump(
        mode="json",
        exclude={
            "record_digest",
            "scope",
            "close_proof_kind",
            "close_proof_id",
            "close_proof_digest",
        },
    )
    legacy["schema_version"] = "stage-gate-activation-session-record.v1"
    legacy["attestation_id"] = current.close_proof_id
    legacy["attestation_digest"] = current.close_proof_digest
    legacy["record_digest"] = canonical_digest(
        legacy,
        CanonicalizationPolicy(),
    )
    path = shared / "activation/session-records/legacy.json"
    _write_payload(path, legacy)

    migrated = read_activation_session_records(tmp_path, (path,))

    assert len(migrated) == 1
    assert migrated[0].schema_version == "stage-gate-activation-session-record.v2"
    assert migrated[0].compatibility_mode == "read-only-legacy"
    assert migrated[0].scope.session_id == candidate.review_session_id
    assert migrated[0].scope.work_item_id == candidate.work_item_id


def test_v1_evidence_package_is_explicitly_quarantined(
    tmp_path: Path,
) -> None:
    _init_repository(tmp_path)
    project_id = resolve_repository_project_id(tmp_path)
    package = _evidence_package(
        project_id,
        repository="SinclairPan/Ai_AutoSDLC",
        tested_commit=_git(tmp_path, "rev-parse", "HEAD"),
    )
    legacy = package.model_dump(mode="json", exclude={"package_digest"})
    legacy["schema_version"] = "activation-evidence-package.v1"
    legacy["quality"] = legacy.pop("probes")
    legacy["package_digest"] = canonical_digest(
        legacy,
        CanonicalizationPolicy(),
    )
    artifact = tmp_path / "legacy.package.json"
    _write_payload(artifact, legacy)

    compatible = activation_evidence_ingestor._compatible_inbox_artifacts(
        tmp_path,
        (artifact,),
    )

    assert compatible == ()
    shared = resolve_canonical_shared_state(tmp_path, project_id)
    quarantine = tuple((shared / "activation/compatibility-quarantine").glob("*.json"))
    assert len(quarantine) == 1
    payload = json.loads(quarantine[0].read_text(encoding="utf-8"))
    assert payload["reason_code"] == "legacy-package-rebuild-required"


def test_eligible_evidence_atomically_advances_the_active_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    promoted, assessment = advance_activation_policy_from_evidence(
        tmp_path,
        _eligible_evidence(tmp_path, monkeypatch),
    )

    assert assessment.eligible is True
    assert promoted.active_phase == 2
    assert current_activation_policy(tmp_path).policy_digest == promoted.policy_digest
    anchor = tmp_path / ACTIVATION_POLICY_ANCHOR
    assert json.loads(anchor.read_text(encoding="utf-8"))["policy_digest"] == (
        promoted.policy_digest
    )
    assert len(tuple(tmp_path.rglob("evidences/*.json"))) == 1


def test_tracked_anchor_recovers_deleted_local_pointer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    promoted, _assessment = advance_activation_policy_from_evidence(
        tmp_path,
        _eligible_evidence(tmp_path, monkeypatch),
    )
    pointer = next(tmp_path.rglob("activation/active-policy.json"))

    pointer.unlink()

    assert current_activation_policy(tmp_path).policy_digest == promoted.policy_digest


def test_local_sources_automatically_advance_without_user_parameters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _trust_attestations(monkeypatch)
    start, project_id, _sessions = _prepare_session_sources(tmp_path)
    package = _evidence_package(
        project_id,
        repository="SinclairPan/Ai_AutoSDLC",
        tested_commit=_git(tmp_path, "rev-parse", "HEAD"),
    )
    inbox = tmp_path / ".ai-sdlc/policies/activation-evidence"
    artifact, bundle = _write_package(tmp_path, package)
    inbox.mkdir(parents=True, exist_ok=True)
    artifact.replace(inbox / "qualification.package.json")
    bundle.replace(inbox / "qualification.bundle.jsonl")

    promoted, assessment = refresh_activation_policy_from_local_evidence(
        tmp_path,
        assessed_at=(start + timedelta(days=24)).isoformat(),
    )

    assert assessment is not None and assessment.eligible
    assert promoted.active_phase == 2


def test_missing_local_package_downloads_latest_attested_ancestor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _trust_attestations(monkeypatch)
    start, project_id, _sessions = _prepare_session_sources(tmp_path)
    head = _git(tmp_path, "rev-parse", "HEAD")
    package = _evidence_package(
        project_id,
        repository="SinclairPan/Ai_AutoSDLC",
        tested_commit=head,
    )
    calls: list[tuple[str, ...]] = []

    def fake_gh(
        arguments: tuple[str, ...],
        *,
        cwd: Path,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        del timeout
        calls.append(arguments)
        if arguments[:2] == ("run", "list"):
            return subprocess.CompletedProcess(
                arguments,
                0,
                json.dumps(
                    [
                        {
                            "databaseId": 12345,
                            "headSha": head,
                            "conclusion": "success",
                        }
                    ]
                ),
                "",
            )
        if arguments[:2] == ("run", "download"):
            download_root = Path(arguments[arguments.index("--dir") + 1])
            _write_json(download_root / "activation-evidence-package.json", package)
            return subprocess.CompletedProcess(arguments, 0, "", "")
        if arguments[:2] == ("attestation", "download"):
            (cwd / f"sha256-{package.package_digest[7:]}.jsonl").write_text(
                '{"trusted":"fixture"}\n',
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(arguments, 0, "", "")
        raise AssertionError(f"unexpected gh invocation: {arguments}")

    monkeypatch.setattr(
        activation_evidence_ingestor.shutil,
        "which",
        lambda command: "/usr/bin/gh" if command == "gh" else None,
    )
    monkeypatch.setattr(activation_evidence_ingestor, "_run_gh", fake_gh)

    promoted, assessment = refresh_activation_policy_from_local_evidence(
        tmp_path,
        assessed_at=(start + timedelta(days=24)).isoformat(),
    )

    assert assessment is not None and assessment.eligible
    assert promoted.active_phase == 2
    inbox = tmp_path / ".ai-sdlc/policies/activation-evidence"
    assert (inbox / f"{head}.package.json").is_file()
    assert (inbox / f"{head}.bundle.jsonl").is_file()
    assert [call[:2] for call in calls] == [
        ("run", "list"),
        ("run", "download"),
        ("attestation", "download"),
    ]


def test_remote_evidence_timeout_remains_a_fail_closed_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(("gh", "run", "list"), 30)

    monkeypatch.setattr(activation_evidence_ingestor.subprocess, "run", timeout)

    result = activation_evidence_ingestor._run_gh(
        ("run", "list"),
        cwd=tmp_path,
        timeout=30,
    )

    assert result.returncode == 124
    assert result.stdout == ""


def test_activation_attestation_verification_is_bound_to_main_ref(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_repository(tmp_path)
    project_id = resolve_repository_project_id(tmp_path)
    package = _evidence_package(
        project_id,
        repository="SinclairPan/Ai_AutoSDLC",
        tested_commit=_git(tmp_path, "rev-parse", "HEAD"),
    )
    artifact, bundle = _write_package(tmp_path, package)
    calls: list[list[str]] = []

    def verify(
        arguments: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, '[{"verified":true}]', "")

    monkeypatch.setattr(activation_evidence_ingestor.subprocess, "run", verify)

    activation_evidence_ingestor._verify_github_attestation(
        artifact,
        bundle,
        package,
        baseline_activation_policy(),
    )

    command = calls[0]
    assert command[command.index("--source-ref") + 1] == "refs/heads/main"


def test_missing_protected_sources_keeps_shadow_policy_without_writes(
    tmp_path: Path,
) -> None:
    current, assessment = refresh_activation_policy_from_local_evidence(tmp_path)

    assert current.active_phase == 1
    assert assessment is None
    assert not (tmp_path / ".ai-sdlc").exists()


def test_tampered_active_policy_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    promoted, _assessment = advance_activation_policy_from_evidence(
        tmp_path,
        _eligible_evidence(tmp_path, monkeypatch),
    )
    path = next(
        tmp_path.rglob(f"{promoted.policy_digest.removeprefix('sha256:')}.json")
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["active_phase"] = 4
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="offline optimization|policy_digest"):
        current_activation_policy(tmp_path)


def test_unpersisted_activation_sources_cannot_advance_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = _eligible_evidence(tmp_path, monkeypatch)
    forged = evidence.model_copy(
        update={
            "session_record_digests": (
                _digest("missing-session"),
                *evidence.session_record_digests[1:],
            )
        }
    )

    with pytest.raises(ValueError, match="mature session population is incomplete"):
        advance_activation_policy_from_evidence(tmp_path, forged)


def test_forged_zero_or_runtime_event_outcome_cannot_advance_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = _eligible_evidence(tmp_path, monkeypatch)
    payload = evidence.model_dump(mode="json", exclude={"evidence_digest"})
    outcome = payload["session_outcomes"][0]
    outcome["had_reversal"] = True
    outcome["finding_event_digests"] = [_digest("forged-reversal")]
    outcome["finding_chain_head_digest"] = _digest("forged-reversal-head")
    outcome["outcome_digest"] = ""
    forged = ActivationEvidence.model_validate(payload)

    with pytest.raises(ValueError, match="session outcome lineage diverged"):
        advance_activation_policy_from_evidence(tmp_path, forged)


def test_only_mature_sessions_enter_activation_trials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _trust_attestations(monkeypatch)
    start, project_id, _sessions = _prepare_session_sources(tmp_path)
    package = _evidence_package(
        project_id,
        repository="SinclairPan/Ai_AutoSDLC",
        tested_commit=_git(tmp_path, "rev-parse", "HEAD"),
    )
    artifact, bundle = _write_package(tmp_path, package)
    ingest_activation_evidence_package(
        tmp_path,
        artifact,
        bundle,
        policy=baseline_activation_policy(),
    )

    evidence = assemble_activation_evidence(
        tmp_path,
        policy=baseline_activation_policy(),
        assessed_at=(start + timedelta(days=15)).isoformat(),
    )

    assert evidence is not None
    assert len(evidence.sessions) == 10
    assert len(evidence.session_outcomes) == 10
    assessment = advance_activation_policy_from_evidence(tmp_path, evidence)[1]
    assert assessment.eligible is False
    assert "total_shadow_sample" in assessment.failed_guards


def test_handwritten_digest_only_sources_cannot_activate(
    tmp_path: Path,
) -> None:
    start, project_id, sessions = _prepare_session_sources(tmp_path)
    isolation = _isolation_evidence()
    probes = _passing_probes()
    shared = resolve_canonical_shared_state(tmp_path, project_id)
    bare_isolation = {
        "schema_version": "activation-isolation-source-record.v1",
        "artifact_kind": "activation-isolation-source-record",
        "project_id": project_id,
        "evidence": isolation[0].model_dump(mode="json"),
        "source_attestation_digests": [_digest("handwritten")],
        "record_digest": "",
    }
    bare_probes = {
        "schema_version": "activation-probe-source-record.v1",
        "artifact_kind": "activation-probe-source-record",
        "project_id": project_id,
        "evidence": probes.model_dump(mode="json"),
        "source_artifact_digests": [_digest("handwritten")],
        "record_digest": "",
    }
    _write_payload(
        shared / "activation/evidence-sources/isolation/handwritten.json",
        bare_isolation,
    )
    _write_payload(
        shared / "activation/evidence-sources/probes/handwritten.json",
        bare_probes,
    )

    with pytest.raises(ValueError, match="import_receipt_digest"):
        refresh_activation_policy_from_local_evidence(
            tmp_path,
            assessed_at=(start + timedelta(days=24)).isoformat(),
        )

    assert sessions


@pytest.mark.parametrize(
    ("repository", "tested_commit", "message"),
    (
        ("Other/Repository", None, "repository"),
        (None, "f" * 40, "commit"),
    ),
)
def test_attested_package_is_bound_to_repository_and_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repository: str | None,
    tested_commit: str | None,
    message: str,
) -> None:
    _trust_attestations(monkeypatch)
    _start, project_id, _sessions = _prepare_session_sources(tmp_path)
    head = _git(tmp_path, "rev-parse", "HEAD")
    package = _evidence_package(
        project_id,
        repository=repository or "SinclairPan/Ai_AutoSDLC",
        tested_commit=tested_commit or head,
    )
    artifact, bundle = _write_package(tmp_path, package)

    with pytest.raises(ValueError, match=message):
        ingest_activation_evidence_package(
            tmp_path,
            artifact,
            bundle,
            policy=baseline_activation_policy(),
        )


def test_attested_package_cannot_select_its_own_trusted_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _trust_attestations(monkeypatch)
    _start, project_id, _sessions = _prepare_session_sources(tmp_path)
    trusted = _evidence_package(
        project_id,
        repository="SinclairPan/Ai_AutoSDLC",
        tested_commit=_git(tmp_path, "rev-parse", "HEAD"),
    )
    payload = trusted.model_dump(mode="json")
    payload["signer_workflow"] = (
        "SinclairPan/Ai_AutoSDLC/.github/workflows/unrelated-or-attacker-controlled.yml"
    )
    payload["package_digest"] = ""
    package = ActivationEvidencePackage.model_validate(payload)
    artifact, bundle = _write_package(tmp_path, package)

    with pytest.raises(ValueError, match="trusted workflow"):
        ingest_activation_evidence_package(
            tmp_path,
            artifact,
            bundle,
            policy=baseline_activation_policy(),
        )


def _eligible_evidence(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> ActivationEvidence:
    _trust_attestations(monkeypatch)
    start = datetime(2026, 7, 1, tzinfo=UTC)
    _prepared_at, project_id, _sessions = _prepare_session_sources(root, start=start)
    package = _evidence_package(
        project_id,
        repository="SinclairPan/Ai_AutoSDLC",
        tested_commit=_git(root, "rev-parse", "HEAD"),
    )
    artifact, bundle = _write_package(root, package)
    ingest_activation_evidence_package(
        root,
        artifact,
        bundle,
        policy=baseline_activation_policy(),
    )
    evidence = assemble_activation_evidence(
        root,
        policy=baseline_activation_policy(),
        assessed_at=(start + timedelta(days=24)).isoformat(),
    )
    assert evidence is not None
    return evidence


def _session_observations(
    start: datetime,
) -> tuple[ActivationSessionObservation, ...]:
    stages = (
        "requirement",
        "design-contract",
        "implementation",
        "frontend-evidence",
        "local-pr-review",
    )
    return tuple(
        ActivationSessionObservation(
            session_id=f"session.{stage}.{index}",
            stage_key=stage,
            risk_level="low",
            mode="shadow",
            completed_at=(start + timedelta(days=index)).isoformat(),
        )
        for stage in stages
        for index in range(10)
    )


def _isolation_evidence() -> tuple[IsolationPlatformEvidence, ...]:
    return tuple(
        IsolationPlatformEvidence(
            platform_id=platform,
            isolation_level="enforced",
            candidate_write_blocked=True,
            sibling_write_blocked=True,
            home_write_blocked=True,
            network_blocked=True,
            evidence_digest=_digest(f"platform:{platform}"),
        )
        for platform in ("linux", "macos", "windows")
    )


def _session_records(
    project_id: str,
    sessions: tuple[ActivationSessionObservation, ...],
) -> tuple[ActivationSessionRecord, ...]:
    return tuple(
        ActivationSessionRecord(
            record_id=f"record.{index}",
            project_id=project_id,
            close_proof_kind="shadow-attestation",
            close_proof_id=f"attestation.{index}",
            close_proof_digest=_digest(f"attestation:{index}"),
            candidate_manifest_digest=_digest(f"candidate:{index}"),
            panel_plan_digest=_digest(f"panel:{index}"),
            review_session_digest=_digest(f"review-session:{index}"),
            review_completion_digest=_digest(f"review-completion:{index}"),
            scope=FindingScope(
                project_id=project_id,
                work_item_id=f"WI-{index}",
                stage_instance_id=f"{observation.stage_key}.1",
                session_id=observation.session_id,
            ),
            observation=observation,
        )
        for index, observation in enumerate(sessions)
    )


def _passing_probes() -> ActivationProbeEvidence:
    return ActivationProbeEvidence(
        canonical_plan_replay_passed=True,
        certificate_integrity_passed=True,
        provider_billing_integrity_passed=True,
        crash_recovery_passed=True,
        hard_budget_integrity_passed=True,
        clean_user_e2e_passed=True,
        planner_benchmark_p95_seconds=0.5,
        work_item_fencing_passed=True,
        hard_constraint_integrity_passed=True,
        non_waivable_integrity_passed=True,
        platform_count=3,
        probe_trial_count=30,
    )


def _persist_session_sources(
    root: Path,
    sessions: tuple[ActivationSessionRecord, ...],
) -> None:
    project_id = resolve_repository_project_id(root)
    shared = resolve_canonical_shared_state(root, project_id)
    for record in sessions:
        _write_json(
            shared / "activation/session-records" / f"{record.record_id}.json",
            record,
        )


def _prepare_session_sources(
    root: Path,
    *,
    start: datetime | None = None,
) -> tuple[datetime, str, tuple[ActivationSessionObservation, ...]]:
    _init_repository(root)
    started_at = start or datetime(2026, 7, 1, tzinfo=UTC)
    project_id = resolve_repository_project_id(root)
    sessions = _session_observations(started_at)
    _persist_session_sources(root, _session_records(project_id, sessions))
    return started_at, project_id, sessions


def _candidate_manifest(
    project_id: str,
    observation: ActivationSessionObservation,
) -> CandidateManifest:
    return CandidateManifest(
        work_item_id="wi-legacy-activation",
        project_id=project_id,
        loop_id="loop.legacy-activation",
        loop_round_number=1,
        stage_key=observation.stage_key,
        stage_instance_id="requirement.legacy",
        review_session_id=observation.session_id,
        adapter_id="stage-candidate.requirement",
        adapter_version="1.0.0",
        adapter_contract_digest=_digest("adapter"),
        input_artifacts=(),
        input_digests={},
        output_artifacts=(),
        output_digests={},
        change_surface=("README.md",),
        test_evidence_digests=(_digest("test"),),
        policy_digests=(_digest("policy"),),
        toolchain_ids=("python",),
        target_platform_ids=("linux",),
        protected_source_set=("README.md",),
        review_artifact_exclusion_set=(
            f".ai-sdlc/state/stage-review/{project_id}/sessions/"
            "wi-legacy-activation/requirement.legacy/"
            f"{observation.session_id}",
        ),
        source_snapshot_digest=_digest("source"),
        source_tree_digest=_digest("tree"),
        change_surface_digest=_digest("change"),
    )


def _evidence_package(
    project_id: str,
    *,
    repository: str,
    tested_commit: str,
) -> ActivationEvidencePackage:
    return ActivationEvidencePackage(
        project_id=project_id,
        repository=repository,
        tested_commit=tested_commit,
        signer_workflow=f"{repository}/.github/workflows/activation-evidence.yml",
        isolation_matrix=_isolation_evidence(),
        probes=_passing_probes(),
        source_artifact_digests=(_digest("trusted-test-results"),),
    )


def _write_package(
    root: Path,
    package: ActivationEvidencePackage,
) -> tuple[Path, Path]:
    artifact = root / "activation-evidence-package.json"
    bundle = root / "activation-evidence-attestation.jsonl"
    _write_json(artifact, package)
    bundle.write_text('{"trusted":"fixture"}\n', encoding="utf-8")
    return artifact, bundle


def _trust_attestations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        activation_evidence_ingestor,
        "_verify_github_attestation",
        lambda *_args, **_kwargs: _digest("verified-github-attestation"),
    )


def _init_repository(root: Path) -> None:
    if (root / ".git").exists():
        return
    _git(root, "init")
    _git(root, "config", "user.email", "activation@example.com")
    _git(root, "config", "user.name", "Activation Test")
    _git(
        root,
        "remote",
        "add",
        "origin",
        "https://github.com/SinclairPan/Ai_AutoSDLC.git",
    )
    (root / "README.md").write_text("activation fixture\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "activation fixture")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = value.model_dump(mode="json")  # type: ignore[attr-defined]
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_payload(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _legacy_policy_payload() -> dict[str, object]:
    payload = baseline_activation_policy().model_dump(
        mode="json",
        exclude={
            "policy_digest",
            "outcome_maturity_window_days",
            "maximum_reversal_rate_upper",
            "maximum_late_critical_rate_upper",
            "maximum_escape_rate_upper",
            "activation_escape_cause_ids",
            "attribution_policy_digest",
        },
    )
    payload["schema_version"] = "stage-gate-activation-policy.v1"
    payload["policy_digest"] = canonical_digest(
        payload,
        CanonicalizationPolicy(),
    )
    return payload


def _digest(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"
