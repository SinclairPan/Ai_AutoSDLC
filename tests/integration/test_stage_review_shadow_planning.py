from __future__ import annotations

import json
import subprocess
from pathlib import Path
from threading import Event, Thread

import pytest

from ai_sdlc.core.loop_models import (
    LoopRound,
    LoopRun,
    LoopStatus,
    LoopType,
    utc_now_iso,
)
from ai_sdlc.core.stage_review import close_gate
from ai_sdlc.core.stage_review.activation import (
    ActivationAssessment,
    ActivationSessionRecord,
)
from ai_sdlc.core.stage_review.activation_artifact_codec import (
    LegacyActivationArtifactUnavailableError,
)
from ai_sdlc.core.stage_review.activation_fence import (
    activation_safety_mutation_fence,
)
from ai_sdlc.core.stage_review.activation_models import (
    ActivationSafetyHold,
    StageGateActivationPolicy,
)
from ai_sdlc.core.stage_review.activation_policy import (
    advance_activation_policy,
    baseline_activation_policy,
)
from ai_sdlc.core.stage_review.activation_policy_anchor import (
    read_activation_policy_anchor,
    write_activation_policy_anchor,
)
from ai_sdlc.core.stage_review.activation_store import (
    _read_activation_session_records as read_activation_session_records,
)
from ai_sdlc.core.stage_review.activation_store import (
    _record_activation_session as record_activation_session,
)
from ai_sdlc.core.stage_review.adapters import ImplementationStageAdapter
from ai_sdlc.core.stage_review.artifacts import (
    atomic_write_json,
    resolve_canonical_shared_state,
    resolve_repository_project_id,
)
from ai_sdlc.core.stage_review.candidate import candidate_binding_digest
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.close_gate import (
    GateApplicabilityDecision,
    StageCloseGateway,
    execute_stage_close,
    prepare_loop_stage_close,
)
from ai_sdlc.core.stage_review.close_gate import (
    _read_stage_close_gate_attestations as read_stage_close_gate_attestations,
)
from ai_sdlc.core.stage_review.close_gate_models import PreparedStageClose
from ai_sdlc.core.stage_review.close_gate_store import (
    _latest_gate_attestation_id as latest_gate_attestation_id,
)
from ai_sdlc.core.stage_review.finding_models import FindingScope
from ai_sdlc.core.stage_review.review_completion import ReviewSessionCompletion
from ai_sdlc.core.stage_review.session_paths import (
    _session_scope_root as session_scope_root,
)
from ai_sdlc.core.stage_review.shadow_planning_runtime import (
    _preflight_shadow_planning as preflight_shadow_planning,
)
from ai_sdlc.core.stage_review.stage_review_execution import (
    StageCloseGateUnavailableError,
    StageReviewExecutionOutcome,
    StageReviewExecutionRequest,
)


def test_real_git_close_materializes_candidate_and_shadow_panel(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path, "specs/001/spec.md", "# Requirement\n")
    _write(tmp_path, "src/app.py", "VALUE = 1\n")
    loop_run = LoopRun(
        loop_id="implementation.shadow",
        loop_type=LoopType.IMPLEMENTATION,
        status=LoopStatus.PASSED,
        work_item_id="001-shadow",
        current_round=1,
        rounds=[
            LoopRound(
                round_number=1,
                status=LoopStatus.PASSED,
                input_artifacts=["specs/001/spec.md"],
                output_artifacts=["src/app.py"],
            )
        ],
    )
    close_path = tmp_path / ".ai-sdlc/loops/implementation/shadow/close.json"
    prepared = prepare_loop_stage_close(
        root=tmp_path,
        adapter=ImplementationStageAdapter(),
        loop_run=loop_run,
        close_kind="implementation-close",
        target_status="closed",
        close_artifact_path=close_path,
    )

    def writer() -> dict[str, str]:
        _write(
            tmp_path,
            close_path.relative_to(tmp_path).as_posix(),
            '{"status":"closed"}\n',
        )
        return {"status": "ready", "loop_status": "closed"}

    result = execute_stage_close(prepared, writer)

    assert result["status"] == "ready"
    attestation = read_stage_close_gate_attestations(tmp_path)[0]
    _assert_resolved_shadow_plan(attestation)
    assert attestation.adapter_id == prepared.adapter_id
    assert attestation.adapter_version == prepared.adapter_version
    assert attestation.adapter_contract_digest == prepared.adapter_contract_digest
    assert (
        attestation.candidate.adapter_contract_digest
        == prepared.adapter_contract_digest
    )
    assert attestation.review_status == "needs_user"
    assert attestation.review_reason_code == "review-provider-unavailable"
    assert read_activation_session_records(tmp_path) == ()

    StageCloseGateway().execute(prepared, writer)

    assert read_activation_session_records(tmp_path) == ()
    _assert_completed_record_lineage(tmp_path, attestation)


def test_clean_committed_loop_close_materializes_artifact_bound_candidate(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path, "specs/001/spec.md", "# Requirement\n")
    _write(tmp_path, "src/app.py", "VALUE = 1\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "completed implementation")
    loop_run = LoopRun(
        loop_id="implementation.clean",
        loop_type=LoopType.IMPLEMENTATION,
        status=LoopStatus.PASSED,
        work_item_id="001-clean",
        current_round=1,
        rounds=[
            LoopRound(
                round_number=1,
                status=LoopStatus.PASSED,
                input_artifacts=["specs/001/spec.md"],
                output_artifacts=["src/app.py"],
            )
        ],
    )
    prepared = prepare_loop_stage_close(
        root=tmp_path,
        adapter=ImplementationStageAdapter(),
        loop_run=loop_run,
        close_kind="implementation-close",
        target_status="closed",
        close_artifact_path=tmp_path / ".ai-sdlc/loops/implementation/clean/close.json",
    )

    def writer() -> dict[str, str]:
        _write(tmp_path, prepared.close_artifact_path, '{"status":"closed"}\n')
        return {"status": "ready", "loop_status": "closed"}

    preflight = preflight_shadow_planning(prepared)
    assert preflight.source_snapshot is not None
    assert preflight.source_snapshot.source_kind == "loop-artifacts"
    assert preflight.source_snapshot.changed_files == []
    assert preflight.candidate is not None
    assert preflight.candidate.input_artifacts == ["specs/001/spec.md"]
    assert preflight.candidate.output_artifacts == ["src/app.py"]

    StageCloseGateway().execute(prepared, writer)

    attestation = read_stage_close_gate_attestations(tmp_path)[0]
    assert attestation.candidate.status == "materialized"


def test_shadow_candidate_never_omits_staged_changes(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path, "README.md", "# Staged\n")
    _git(tmp_path, "add", "README.md")
    _write(tmp_path, "src/app.py", "VALUE = 1\n")
    loop_run = LoopRun(
        loop_id="implementation.mixed",
        loop_type=LoopType.IMPLEMENTATION,
        status=LoopStatus.PASSED,
        work_item_id="001-mixed",
        current_round=1,
        rounds=[LoopRound(round_number=1, status=LoopStatus.PASSED)],
    )
    close_path = tmp_path / ".ai-sdlc/loops/implementation/mixed/close.json"
    prepared = prepare_loop_stage_close(
        root=tmp_path,
        adapter=ImplementationStageAdapter(),
        loop_run=loop_run,
        close_kind="implementation-close",
        target_status="closed",
        close_artifact_path=close_path,
    )

    def writer() -> dict[str, str]:
        _write(tmp_path, close_path.relative_to(tmp_path).as_posix(), "{}\n")
        return {"status": "ready", "loop_status": "closed"}

    StageCloseGateway().execute(prepared, writer)

    attestation = read_stage_close_gate_attestations(tmp_path)[0]
    assert attestation.candidate.status == "not_materialized"
    assert attestation.planning.status == "not_run"
    assert read_activation_session_records(tmp_path) == ()


def test_applicability_receives_candidate_derived_risk_before_writer(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path, "src/app.py", "VALUE = 1\n")
    loop_run = LoopRun(
        loop_id="implementation.risk-preflight",
        loop_type=LoopType.IMPLEMENTATION,
        status=LoopStatus.PASSED,
        work_item_id="001-risk-preflight",
        current_round=1,
        rounds=[LoopRound(round_number=1, status=LoopStatus.PASSED)],
    )
    close_path = tmp_path / ".ai-sdlc/loops/implementation/risk/close.json"
    prepared = prepare_loop_stage_close(
        root=tmp_path,
        adapter=ImplementationStageAdapter(),
        loop_run=loop_run,
        close_kind="implementation-close",
        target_status="closed",
        close_artifact_path=close_path,
    )

    def resolver(frozen: PreparedStageClose) -> GateApplicabilityDecision:
        assert frozen.risk_level == "low"
        return GateApplicabilityDecision(
            decision_id="decision.risk-preflight",
            gate_id="stage-close-authorizer",
            stage_key=frozen.stage_key,
            loop_id=frozen.loop_id,
            mode="shadow",
            policy_id="test-policy",
            policy_version="1.0.0",
            policy_digest="sha256:test-policy",
            reason_code="test-risk-preflight",
        )

    def writer() -> dict[str, str]:
        _write(tmp_path, close_path.relative_to(tmp_path).as_posix(), "{}\n")
        return {"status": "ready", "loop_status": "closed"}

    StageCloseGateway(resolver).execute(prepared, writer)


def test_only_completed_review_execution_creates_activation_sample(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path, "src/app.py", "VALUE = 1\n")
    prepared, close_path = _prepared_implementation_close(
        tmp_path,
        "implementation.completed-review",
    )

    def writer() -> dict[str, str]:
        _write(tmp_path, close_path.relative_to(tmp_path).as_posix(), "{}\n")
        return {"status": "ready", "loop_status": "closed"}

    StageCloseGateway(review_executor=_CompletedReviewExecutor()).execute(
        prepared,
        writer,
    )

    attestation = read_stage_close_gate_attestations(tmp_path)[0]
    records = read_activation_session_records(tmp_path)
    assert attestation.review_session_digest == "sha256:" + "1" * 64
    assert attestation.review_completion_digest == "sha256:" + "2" * 64
    assert len(records) == 1
    assert records[0].review_session_digest == attestation.review_session_digest


def test_active_safety_hold_runs_recovery_review_and_blocks_writer(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path, "src/app.py", "VALUE = 1\n")
    prepared, _close_path = _prepared_implementation_close(
        tmp_path,
        "implementation.safety-recovery",
    )
    policy_payload = _phase_two_policy().model_dump(
        mode="json",
        exclude={"policy_digest"},
    )
    policy_payload["outcome_maturity_window_days"] = 0
    policy = StageGateActivationPolicy.model_validate(policy_payload)
    write_activation_policy_anchor(tmp_path, policy)
    project_id = resolve_repository_project_id(tmp_path)
    shared = resolve_canonical_shared_state(tmp_path, project_id)
    hold = ActivationSafetyHold(
        hold_id="hold.integration",
        project_id=project_id,
        policy_digest=policy.policy_digest,
        evidence_digest=_digest("safety-evidence"),
        assessment_digest=_digest("safety-assessment"),
        triggering_outcome_digests=(_digest("safety-outcome"),),
        affected_combinations=({"stage_key": "implementation", "risk_level": "low"},),
        created_at="2026-06-01T00:00:00+00:00",
        recovery_not_before="2026-06-15T00:00:00+00:00",
        minimum_recovery_sessions=2,
    )
    atomic_write_json(
        shared / "activation/safety-holds" / f"{hold.hold_id}.json",
        hold.model_dump(mode="json"),
    )
    writes = 0

    def writer() -> dict[str, str]:
        nonlocal writes
        writes += 1
        return {"status": "ready", "loop_status": "closed"}

    executor = _AuthoritativeCompletedReviewExecutor(tmp_path)
    gateway = StageCloseGateway(
        review_executor=executor,
        close_enforcer=_PassThroughCloseEnforcer(),
    )
    with pytest.raises(
        StageCloseGateUnavailableError,
        match="activation-safety-hold",
    ):
        gateway.execute(
            prepared,
            writer,
        )

    assert writes == 0
    assert len(tuple((shared / "activation/safety-recovery").glob("*.json"))) == 1

    second, second_close_path = _prepared_implementation_close(
        tmp_path,
        "implementation.safety-recovery-second",
    )

    def second_writer() -> dict[str, str]:
        nonlocal writes
        writes += 1
        _write(
            tmp_path,
            second_close_path.relative_to(tmp_path).as_posix(),
            "{}\n",
        )
        return {"status": "ready", "loop_status": "closed"}

    result = gateway.execute(second, second_writer)

    assert result["status"] == "ready"
    assert writes == 1
    assert len(tuple((shared / "activation/safety-recovery").glob("*.json"))) == 2
    assert len(tuple((shared / "activation/safety-releases").glob("*.json"))) == 1


def test_final_safety_fence_blocks_hold_created_after_initial_check(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path, "src/app.py", "VALUE = 1\n")
    prepared, _close_path = _prepared_implementation_close(
        tmp_path,
        "implementation.safety-fence",
    )
    policy = _phase_two_policy()
    write_activation_policy_anchor(tmp_path, policy)
    project_id = resolve_repository_project_id(tmp_path)
    shared = resolve_canonical_shared_state(tmp_path, project_id)
    hold = ActivationSafetyHold(
        hold_id="hold.final-fence",
        project_id=project_id,
        policy_digest=policy.policy_digest,
        evidence_digest=_digest("fence-evidence"),
        assessment_digest=_digest("fence-assessment"),
        triggering_outcome_digests=(_digest("fence-outcome"),),
        affected_combinations=(
            {"stage_key": "implementation", "risk_level": "low"},
        ),
        created_at="2026-07-23T00:00:00+00:00",
        recovery_not_before="2026-08-06T00:00:00+00:00",
        minimum_recovery_sessions=2,
    )
    writes = 0

    class InjectingEnforcer:
        def enforce_close(
            self,
            _prepared,
            _decision,
            _preflight,
            guarded_writer,
        ):
            atomic_write_json(
                shared / "activation/safety-holds" / f"{hold.hold_id}.json",
                hold.model_dump(mode="json"),
            )
            return guarded_writer()

    def writer() -> dict[str, str]:
        nonlocal writes
        writes += 1
        return {"status": "ready", "loop_status": "closed"}

    with pytest.raises(
        StageCloseGateUnavailableError,
        match="activation-safety-hold",
    ):
        StageCloseGateway(close_enforcer=InjectingEnforcer()).execute(
            prepared,
            writer,
        )

    assert writes == 0


def test_phase_one_writer_read_lease_blocks_phase_two_promotion(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path, "src/app.py", "VALUE = 1\n")
    prepared, close_path = _prepared_implementation_close(
        tmp_path,
        "implementation.activation-epoch",
    )
    write_activation_policy_anchor(tmp_path, baseline_activation_policy())
    project_id = resolve_repository_project_id(tmp_path)
    writer_started = Event()
    allow_writer = Event()
    promotion_committed = Event()
    observed_phases: list[int] = []
    errors: list[BaseException] = []

    def writer() -> dict[str, str]:
        writer_started.set()
        assert allow_writer.wait(5)
        current = read_activation_policy_anchor(tmp_path)
        assert current is not None
        observed_phases.append(current.active_phase)
        _write(tmp_path, close_path.relative_to(tmp_path).as_posix(), "{}\n")
        return {"status": "ready", "loop_status": "closed"}

    def close_stage() -> None:
        try:
            StageCloseGateway().execute(prepared, writer)
        except BaseException as exc:
            errors.append(exc)

    def promote() -> None:
        try:
            with activation_safety_mutation_fence(tmp_path, project_id):
                write_activation_policy_anchor(tmp_path, _phase_two_policy())
                promotion_committed.set()
        except BaseException as exc:
            errors.append(exc)

    close_thread = Thread(target=close_stage)
    close_thread.start()
    assert writer_started.wait(5)
    promote_thread = Thread(target=promote)
    promote_thread.start()
    assert promotion_committed.wait(0.2) is False
    allow_writer.set()
    close_thread.join(timeout=5)
    promote_thread.join(timeout=5)

    assert close_thread.is_alive() is False
    assert promote_thread.is_alive() is False
    assert errors == []
    assert observed_phases == [1]
    assert promotion_committed.is_set()


def test_shadow_derived_state_keeps_read_lease_until_attestation_is_persisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path, "src/app.py", "VALUE = 1\n")
    prepared, close_path = _prepared_implementation_close(
        tmp_path,
        "implementation.derived-activation-epoch",
    )
    write_activation_policy_anchor(tmp_path, baseline_activation_policy())
    project_id = resolve_repository_project_id(tmp_path)
    derived_started = Event()
    allow_derived = Event()
    mutation_committed = Event()
    errors: list[BaseException] = []
    original = close_gate.persist_gate_attestation

    def persist_with_pause(root, attestation) -> None:
        derived_started.set()
        assert allow_derived.wait(5)
        original(root, attestation)

    monkeypatch.setattr(
        close_gate,
        "persist_gate_attestation",
        persist_with_pause,
    )

    def writer() -> dict[str, str]:
        _write(tmp_path, close_path.relative_to(tmp_path).as_posix(), "{}\n")
        return {"status": "ready", "loop_status": "closed"}

    def close_stage() -> None:
        try:
            StageCloseGateway().execute(prepared, writer)
        except BaseException as exc:
            errors.append(exc)

    def mutate_activation_state() -> None:
        try:
            with activation_safety_mutation_fence(tmp_path, project_id):
                mutation_committed.set()
        except BaseException as exc:
            errors.append(exc)

    close_thread = Thread(target=close_stage)
    close_thread.start()
    assert derived_started.wait(5)
    mutation_thread = Thread(target=mutate_activation_state)
    mutation_thread.start()
    assert mutation_committed.wait(0.2) is False
    allow_derived.set()
    close_thread.join(timeout=5)
    mutation_thread.join(timeout=5)

    assert close_thread.is_alive() is False
    assert mutation_thread.is_alive() is False
    assert errors == []
    assert mutation_committed.is_set()
    assert len(read_stage_close_gate_attestations(tmp_path)) == 1


def test_unreconstructable_v1_attestation_is_quarantined_without_hiding_v2(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path, "src/app.py", "VALUE = 1\n")
    prepared, close_path = _prepared_implementation_close(
        tmp_path,
        "implementation.legacy-attestation",
    )

    def writer() -> dict[str, str]:
        _write(tmp_path, close_path.relative_to(tmp_path).as_posix(), "{}\n")
        return {"status": "ready", "loop_status": "closed"}

    StageCloseGateway(review_executor=_CompletedReviewExecutor()).execute(
        prepared,
        writer,
    )
    current = read_stage_close_gate_attestations(tmp_path)
    assert len(current) == 1
    project_id = resolve_repository_project_id(tmp_path)
    shared = resolve_canonical_shared_state(tmp_path, project_id)
    legacy = current[0].model_dump(
        mode="json",
        exclude={"attestation_digest", "review_scope"},
    )
    legacy["schema_version"] = "stage-close-gate-attestation.v1"
    legacy["attestation_id"] = "legacy-unavailable"
    legacy["supersedes_attestation_id"] = current[0].attestation_id
    legacy["candidate"]["candidate_manifest_digest"] = _digest("missing-candidate")
    legacy["attestation_digest"] = canonical_digest(
        legacy,
        CanonicalizationPolicy(),
    )
    atomic_write_json(
        shared / "stage-close-gate/attestations" / "unrelated-file-name.json",
        legacy,
    )
    descendant = type(current[0]).model_validate(
        {
            **current[0].model_dump(
                mode="json",
                exclude={"attestation_digest"},
            ),
            "attestation_id": "v2-after-quarantine",
            "supersedes_attestation_id": "legacy-unavailable",
        }
    )
    atomic_write_json(
        shared / "stage-close-gate/attestations" / "v2-after-quarantine.json",
        descendant.model_dump(mode="json"),
    )

    compatible = read_stage_close_gate_attestations(tmp_path)

    assert compatible == (*current, descendant)
    quarantine = tuple((shared / "activation/compatibility-quarantine").glob("*.json"))
    assert len(quarantine) == 1
    quarantine_payload = json.loads(quarantine[0].read_text(encoding="utf-8"))
    assert quarantine_payload["attestation_id"] == "legacy-unavailable"
    assert (
        quarantine_payload["supersedes_attestation_id"]
        == current[0].attestation_id
    )
    with pytest.raises(
        LegacyActivationArtifactUnavailableError,
        match="quarantined attestation",
    ):
        latest_gate_attestation_id(tmp_path, current[0].attestation_id)


class _PassThroughCloseEnforcer:
    def enforce_close(
        self,
        _prepared,
        _decision,
        _preflight,
        guarded_writer,
    ):
        return guarded_writer()


class _AuthoritativeCompletedReviewExecutor:
    def __init__(self, root: Path) -> None:
        self._root = root

    def execute(
        self,
        request: StageReviewExecutionRequest,
    ) -> StageReviewExecutionOutcome:
        candidate = request.candidate
        scope = FindingScope(
            project_id=candidate.project_id,
            work_item_id=candidate.work_item_id,
            stage_instance_id=candidate.stage_instance_id,
            session_id=candidate.review_session_id,
        )
        completion = ReviewSessionCompletion(
            scope=scope,
            session_digest=_digest(f"session:{candidate.review_session_id}"),
            session_head_event_digest=_digest(
                f"head:{candidate.review_session_id}"
            ),
            candidate_manifest_digest=candidate_binding_digest(candidate),
            panel_plan_digest=request.plan.plan_digest,
            binding_set_digest=_digest(
                f"bindings:{candidate.review_session_id}"
            ),
            initial_review_seal_digest=_digest(
                f"seal:{candidate.review_session_id}"
            ),
            finding_ledger_digest=_digest(
                f"ledger:{candidate.review_session_id}"
            ),
            required_pass_digests=(
                _digest(f"pass:{candidate.review_session_id}"),
            ),
            completed_at=utc_now_iso(),
        )
        shared = resolve_canonical_shared_state(
            self._root,
            candidate.project_id,
        )
        atomic_write_json(
            session_scope_root(
                shared / "stage-review-sessions",
                candidate.project_id,
                scope,
            )
            / "completion.json",
            completion.model_dump(mode="json"),
        )
        return StageReviewExecutionOutcome(
            status="completed",
            review_session_digest=completion.session_digest,
            review_completion_digest=completion.completion_digest,
        )


class _CompletedReviewExecutor:
    def execute(
        self,
        request: StageReviewExecutionRequest,
    ) -> StageReviewExecutionOutcome:
        reservation = request.governor.get_reservation(
            request.plan.final_reservation_id
        )
        assert reservation.state == "final"
        return StageReviewExecutionOutcome(
            status="completed",
            review_session_digest="sha256:" + "1" * 64,
            review_completion_digest="sha256:" + "2" * 64,
        )


def _phase_two_policy():
    baseline = baseline_activation_policy()
    assessment = ActivationAssessment(
        assessment_id="assessment.phase-one",
        policy_digest=baseline.policy_digest,
        evidence_digest=_digest("phase-one-evidence"),
        assessed_at="2026-05-01T00:00:00+00:00",
        eligible=True,
        failed_guards=(),
        quality_intervals=(),
    )
    promoted = advance_activation_policy(baseline, assessment)
    assert promoted is not None
    return promoted


def _digest(label: str) -> str:
    import hashlib

    return f"sha256:{hashlib.sha256(label.encode()).hexdigest()}"


def _prepared_implementation_close(
    root: Path,
    loop_id: str,
) -> tuple[PreparedStageClose, Path]:
    run = LoopRun(
        loop_id=loop_id,
        loop_type=LoopType.IMPLEMENTATION,
        status=LoopStatus.PASSED,
        work_item_id="001-completed-review",
        current_round=1,
        rounds=[LoopRound(round_number=1, status=LoopStatus.PASSED)],
    )
    close_path = root / ".ai-sdlc/loops/implementation/completed/close.json"
    prepared = prepare_loop_stage_close(
        root=root,
        adapter=ImplementationStageAdapter(),
        loop_run=run,
        close_kind="implementation-close",
        target_status="closed",
        close_artifact_path=close_path,
    )
    return prepared, close_path


def _assert_completed_record_lineage(root: Path, attestation: object) -> None:
    payload = attestation.model_dump(  # type: ignore[attr-defined]
        mode="json", exclude={"attestation_digest"}
    )
    payload.update(
        {
            "review_status": "completed",
            "review_reason_code": "",
            "review_session_digest": "sha256:review-session",
            "review_completion_digest": "sha256:review-completion",
            "review_scope": {
                "project_id": resolve_repository_project_id(root),
                "work_item_id": payload["work_item_id"],
                "stage_instance_id": payload["stage_instance_id"],
                "session_id": Path(payload["candidate"]["candidate_ref"]).parent.name,
            },
        }
    )
    completed = type(attestation).model_validate(payload)
    record = record_activation_session(root, completed)
    assert record is not None
    assert record.observation.mode == "shadow"
    assert record.observation.stage_key == "implementation"
    assert record_activation_session(root, completed) == record
    _forge_activation_record(root, record)
    with pytest.raises(ValueError, match="lineage diverged"):
        record_activation_session(root, completed)


def _assert_resolved_shadow_plan(attestation: object) -> None:
    candidate = attestation.candidate  # type: ignore[attr-defined]
    planning = attestation.planning  # type: ignore[attr-defined]
    assert candidate.status == "materialized"
    assert planning.status == "resolved"
    assert planning.required_slot_count == 2
    assert len(planning.required_role_profile_ids) == 2
    assert planning.panel_plan_digest
    assert planning.final_reservation_digest


def _forge_activation_record(root: Path, record: ActivationSessionRecord) -> None:
    record_path = next(root.rglob(f"{record.record_id}.json"))
    payload = record.model_dump(mode="json", exclude={"record_digest"})
    payload["candidate_manifest_digest"] = "sha256:forged-candidate"
    forged = ActivationSessionRecord.model_validate(payload)
    record_path.write_text(
        json.dumps(forged.model_dump(mode="json")),
        encoding="utf-8",
    )


def _init_git_repo(root: Path) -> None:
    _git(root, "init", "--initial-branch=main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    _write(root, "README.md", "# Test\n")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "initial")


def _write(root: Path, relative: str, content: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr.decode("utf-8", errors="replace"))
