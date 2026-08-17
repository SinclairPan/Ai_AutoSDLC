"""Unit tests for BatchExecutor, Executor, and ExecutionLogger."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ai_sdlc.core.config import YamlStore
from ai_sdlc.core.executor import (
    MAX_CONSECUTIVE_HALTS,
    MAX_DEBUG_ROUNDS,
    BatchExecutor,
    CircuitBreakerError,
    ExecutionLogger,
    ExecutionResult,
    ExecutionStatus,
    Executor,
)
from ai_sdlc.models.state import (
    ExecutionBatch,
    ExecutionPlan,
    RuntimeState,
    Task,
    TaskStatus,
    WorkingSet,
)
from ai_sdlc.telemetry.paths import telemetry_local_root
from ai_sdlc.telemetry.runtime import RuntimeTelemetry


def _read_ndjson(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _make_plan(tasks: list[Task], batches: list[ExecutionBatch]) -> ExecutionPlan:
    return ExecutionPlan(
        total_tasks=len(tasks),
        total_batches=len(batches),
        tasks=tasks,
        batches=batches,
    )


def _make_two_batch_plan() -> ExecutionPlan:
    """Plan with 2 batches, 2 tasks each."""
    tasks = [
        Task(task_id="T1", title="Task 1", phase=1),
        Task(task_id="T2", title="Task 2", phase=1),
        Task(task_id="T3", title="Task 3", phase=2),
        Task(task_id="T4", title="Task 4", phase=2),
    ]
    batches = [
        ExecutionBatch(batch_id=1, phase=1, tasks=["T1", "T2"]),
        ExecutionBatch(batch_id=2, phase=2, tasks=["T3", "T4"]),
    ]
    return _make_plan(tasks, batches)


def _write_pipeline_config(
    root: Path,
    *,
    max_tasks_per_batch: int = 12,
    max_debug_rounds_per_task: int = 3,
    consecutive_failure_limit: int = 2,
) -> None:
    cfg = root / ".ai-sdlc" / "config" / "pipeline.yml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        (
            "stages:\n"
            "  - id: execute\n"
            "    batch:\n"
            "      strategy: by_phase\n"
            f"      max_tasks_per_batch: {max_tasks_per_batch}\n"
            "      auto_archive: true\n"
            "circuit_breaker:\n"
            f"  max_debug_rounds_per_task: {max_debug_rounds_per_task}\n"
            f"  consecutive_failure_limit: {consecutive_failure_limit}\n"
        ),
        encoding="utf-8",
    )


def _write_tasks_md(spec_dir: Path, body: str) -> Path:
    spec_dir.mkdir(parents=True, exist_ok=True)
    path = spec_dir / "tasks.md"
    path.write_text(body, encoding="utf-8")
    return path


def _sample_tasks_md() -> str:
    return """\
### Task 1.1 Setup batch one
- **Task ID**: T001
- **依赖**：无
- **文件**：src/setup.py
- **验收标准（AC）**：
  1. setup done

### Task 1.2 Continue batch one
- **Task ID**: T002
- **依赖**：T001
- **文件**：src/app.py
- **验收标准（AC）**：
  1. app done

### Task 1.3 Split by max tasks
- **Task ID**: T003
- **依赖**：T002
- **文件**：src/extra.py
- **验收标准（AC）**：
  1. extra done
"""


def _single_task_md() -> str:
    return """\
### Task 1.1 Flaky task
- **Task ID**: T001
- **依赖**：无
- **文件**：src/flaky.py
- **验收标准（AC）**：
  1. flaky handled
"""


def _git_commit_count(root: Path) -> int:
    result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return int(result.stdout.strip())


def test_execution_result_deduplicates_commit_hashes(tmp_path: Path) -> None:
    result = ExecutionResult(
        plan=ExecutionPlan(),
        runtime=RuntimeState(),
        log_path=tmp_path / "execution.log",
        summary_path=tmp_path / "summary.md",
        commit_hashes=["abc123", "abc123", "def456"],
    )

    assert result.commit_hashes == ["abc123", "def456"]


class TestBatchExecutorNormalFlow:
    """Normal execution: complete batch 1, advance to batch 2."""

    def test_advance_through_two_batches(self) -> None:
        plan = _make_two_batch_plan()
        runtime = RuntimeState()
        exe = BatchExecutor(plan, runtime)

        assert exe.get_current_batch() is not None
        assert exe.get_current_batch().batch_id == 1  # type: ignore[union-attr]
        assert not exe.is_complete()

        exe.advance_task("T1", TaskStatus.COMPLETED)
        exe.advance_task("T2", TaskStatus.COMPLETED)

        batch = exe.advance_batch()
        assert batch is not None
        assert batch.batch_id == 2
        assert runtime.current_batch == 1

        exe.advance_task("T3", TaskStatus.COMPLETED)
        exe.advance_task("T4", TaskStatus.COMPLETED)

        result = exe.advance_batch()
        assert result is None
        assert exe.is_complete()

    def test_completed_task_resets_consecutive_halts(self) -> None:
        plan = _make_two_batch_plan()
        runtime = RuntimeState(consecutive_halts=1)
        exe = BatchExecutor(plan, runtime)

        exe.advance_task("T1", TaskStatus.COMPLETED)
        assert runtime.consecutive_halts == 0

    def test_last_committed_task_tracks_completion(self) -> None:
        plan = _make_two_batch_plan()
        runtime = RuntimeState()
        exe = BatchExecutor(plan, runtime)

        exe.advance_task("T1", TaskStatus.COMPLETED)
        assert runtime.last_committed_task == "T1"

        exe.advance_task("T2", TaskStatus.COMPLETED)
        assert runtime.last_committed_task == "T2"


class TestDebugRoundHalt:
    """BR-030: task HALTED after MAX_DEBUG_ROUNDS failures."""

    def test_task_halted_after_max_debug_rounds(self) -> None:
        plan = _make_plan(
            [Task(task_id="TX", title="Flaky", phase=1)],
            [ExecutionBatch(batch_id=1, phase=1, tasks=["TX"])],
        )
        runtime = RuntimeState()
        exe = BatchExecutor(plan, runtime)

        for i in range(MAX_DEBUG_ROUNDS - 1):
            exe.advance_task("TX", TaskStatus.FAILED)
            assert plan.tasks[0].status == TaskStatus.FAILED
            assert runtime.debug_rounds["TX"] == i + 1

        exe.advance_task("TX", TaskStatus.FAILED)
        assert plan.tasks[0].status == TaskStatus.HALTED
        assert runtime.debug_rounds["TX"] == MAX_DEBUG_ROUNDS

    def test_failed_task_stays_failed_before_limit(self) -> None:
        plan = _make_plan(
            [Task(task_id="TX", title="Flaky", phase=1)],
            [ExecutionBatch(batch_id=1, phase=1, tasks=["TX"])],
        )
        runtime = RuntimeState()
        exe = BatchExecutor(plan, runtime)

        exe.advance_task("TX", TaskStatus.FAILED)
        assert plan.tasks[0].status == TaskStatus.FAILED


class TestCircuitBreaker:
    """BR-031: CircuitBreakerError after consecutive HALTs."""

    def test_circuit_breaker_on_consecutive_halts(self) -> None:
        tasks = [
            Task(task_id="A", title="A", phase=1),
            Task(task_id="B", title="B", phase=1),
        ]
        plan = _make_plan(
            tasks, [ExecutionBatch(batch_id=1, phase=1, tasks=["A", "B"])]
        )
        runtime = RuntimeState()
        exe = BatchExecutor(plan, runtime)

        for _ in range(MAX_DEBUG_ROUNDS):
            exe.advance_task("A", TaskStatus.FAILED)

        assert runtime.consecutive_halts == 1

        with pytest.raises(CircuitBreakerError, match="BR-031"):
            for _ in range(MAX_DEBUG_ROUNDS):
                exe.advance_task("B", TaskStatus.FAILED)

        assert runtime.consecutive_halts == MAX_CONSECUTIVE_HALTS

    def test_halt_then_complete_resets_breaker(self) -> None:
        tasks = [
            Task(task_id="A", title="A", phase=1),
            Task(task_id="B", title="B", phase=1),
        ]
        plan = _make_plan(
            tasks, [ExecutionBatch(batch_id=1, phase=1, tasks=["A", "B"])]
        )
        runtime = RuntimeState()
        exe = BatchExecutor(plan, runtime)

        for _ in range(MAX_DEBUG_ROUNDS):
            exe.advance_task("A", TaskStatus.FAILED)
        assert runtime.consecutive_halts == 1

        exe.advance_task("B", TaskStatus.COMPLETED)
        assert runtime.consecutive_halts == 0


class TestAdvanceBatchEdgeCases:
    """Edge cases for batch advancement."""

    def test_batch_not_ready_with_pending_tasks(self) -> None:
        plan = _make_two_batch_plan()
        runtime = RuntimeState()
        exe = BatchExecutor(plan, runtime)

        exe.advance_task("T1", TaskStatus.COMPLETED)
        batch = exe.advance_batch()
        assert batch is not None
        assert batch.batch_id == 1
        assert runtime.current_batch == 0

    def test_advance_batch_when_all_done_returns_none(self) -> None:
        plan = _make_two_batch_plan()
        runtime = RuntimeState()
        exe = BatchExecutor(plan, runtime)

        exe.advance_task("T1", TaskStatus.COMPLETED)
        exe.advance_task("T2", TaskStatus.COMPLETED)
        exe.advance_batch()
        exe.advance_task("T3", TaskStatus.COMPLETED)
        exe.advance_task("T4", TaskStatus.COMPLETED)
        result = exe.advance_batch()
        assert result is None

    def test_halted_task_allows_batch_advance(self) -> None:
        """HALTED is a terminal status; batch can still advance."""
        plan = _make_plan(
            [Task(task_id="X", title="X", phase=1)],
            [ExecutionBatch(batch_id=1, phase=1, tasks=["X"])],
        )
        runtime = RuntimeState()
        exe = BatchExecutor(plan, runtime)

        for _ in range(MAX_DEBUG_ROUNDS):
            exe.advance_task("X", TaskStatus.FAILED)

        result = exe.advance_batch()
        assert result is None
        assert exe.is_complete()


class TestExecutorRun:
    def test_run_without_task_runner_returns_needs_user_without_side_effects(
        self, git_repo: Path
    ) -> None:
        spec_dir = git_repo / "specs" / "WI-2026-EXEC"
        tasks_path = _write_tasks_md(spec_dir, _sample_tasks_md())
        _write_pipeline_config(git_repo, max_tasks_per_batch=2)

        before_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=git_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        before_status = subprocess.run(
            ["git", "status", "--porcelain=v1"],
            cwd=git_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        before_tasks = tasks_path.read_bytes()

        result = Executor(git_repo).run(tasks_path)

        assert result.plan.total_batches == 2
        assert result.status == ExecutionStatus.NEEDS_USER
        assert result.completed_batches == 0
        assert result.halted is False
        assert result.target_task_id == "T001"
        assert result.detail.startswith("No task runner is available for T001")
        assert result.next_action.startswith(
            "Implement T001 with the active AI agent"
        )
        assert (
            "ai-sdlc pr-review start --diff-source local-staged "
            "--provider local-agent"
        ) in result.next_action
        assert result.tasks_path == tasks_path
        assert result.log_path == spec_dir / "task-execution-log.md"
        assert result.summary_path == spec_dir / "development-summary.md"
        assert not result.log_path.exists()
        assert not result.summary_path.exists()
        assert result.commit_hashes == []
        assert tasks_path.read_bytes() == before_tasks
        assert all(task.status == TaskStatus.PENDING for task in result.plan.tasks)
        assert not (git_repo / ".ai-sdlc" / "work-items" / "WI-2026-EXEC").exists()
        assert subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=git_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout == before_head
        assert subprocess.run(
            ["git", "status", "--porcelain=v1"],
            cwd=git_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout == before_status

    def test_injected_runner_executes_only_current_batch_without_committing(
        self, git_repo: Path
    ) -> None:
        spec_dir = git_repo / "specs" / "WI-2026-BATCH"
        tasks_path = _write_tasks_md(spec_dir, _sample_tasks_md())
        _write_pipeline_config(git_repo, max_tasks_per_batch=2)
        before_commits = _git_commit_count(git_repo)

        result = Executor(
            git_repo,
            task_runner=lambda _task, _runtime: TaskStatus.COMPLETED,
        ).run(tasks_path)

        assert result.status == ExecutionStatus.NEEDS_USER
        assert result.completed_batches == 1
        assert result.target_task_id == "T003"
        assert [task.status for task in result.plan.tasks] == [
            TaskStatus.COMPLETED,
            TaskStatus.COMPLETED,
            TaskStatus.PENDING,
        ]
        assert result.log_path.exists()
        assert not result.summary_path.exists()
        assert result.commit_hashes == []
        assert _git_commit_count(git_repo) == before_commits
        assert "ai-sdlc stage show execute" in result.next_action
        assert "at most one repair re-review" in result.next_action
        assert "user-approved commit" in result.next_action

    def test_injected_runner_resumes_next_batch_without_reopening_completed_tasks(
        self, git_repo: Path
    ) -> None:
        spec_dir = git_repo / "specs" / "WI-2026-RESUME"
        tasks_path = _write_tasks_md(spec_dir, _sample_tasks_md())
        _write_pipeline_config(git_repo, max_tasks_per_batch=2)
        runtime = RuntimeState(current_stage="execute")
        executed: list[str] = []

        def runner(task: Task, _runtime: RuntimeState) -> TaskStatus:
            executed.append(task.task_id)
            return TaskStatus.COMPLETED

        first = Executor(git_repo, task_runner=runner).run(
            tasks_path,
            runtime=runtime,
        )
        second = Executor(git_repo, task_runner=runner).run(
            tasks_path,
            runtime=runtime,
        )

        assert first.completed_batches == 1
        assert first.target_task_id == "T003"
        assert second.completed_batches == 2
        assert second.status == ExecutionStatus.NEEDS_USER
        assert second.target_task_id == "T003"
        assert executed == ["T001", "T002", "T003"]
        assert all(task.status == TaskStatus.COMPLETED for task in second.plan.tasks)

    def test_executor_never_claims_commit_created_by_injected_runner(
        self, git_repo: Path
    ) -> None:
        spec_dir = git_repo / "specs" / "WI-2026-RUNNER-COMMIT"
        tasks_path = _write_tasks_md(spec_dir, _single_task_md())
        committed = False

        def runner(_task: Task, _runtime: RuntimeState) -> TaskStatus:
            nonlocal committed
            if not committed:
                artifact = git_repo / "runner-output.txt"
                artifact.write_text("runner-owned\n", encoding="utf-8")
                subprocess.run(["git", "add", "runner-output.txt"], cwd=git_repo, check=True)
                subprocess.run(
                    ["git", "commit", "-m", "test: runner-owned commit"],
                    cwd=git_repo,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                committed = True
            return TaskStatus.COMPLETED

        before_commits = _git_commit_count(git_repo)
        result = Executor(git_repo, task_runner=runner).run(tasks_path)

        assert _git_commit_count(git_repo) == before_commits + 1
        assert result.status == ExecutionStatus.NEEDS_USER
        assert result.commit_hashes == []
        assert result.last_commit_timestamp == ""

    def test_run_halts_task_after_configured_debug_limit(self, git_repo: Path) -> None:
        spec_dir = git_repo / "specs" / "WI-2026-HALT"
        tasks_path = _write_tasks_md(spec_dir, _single_task_md())
        _write_pipeline_config(
            git_repo,
            max_debug_rounds_per_task=2,
            consecutive_failure_limit=2,
        )

        executor = Executor(
            git_repo,
            task_runner=lambda _task, _runtime: TaskStatus.FAILED,
        )
        result = executor.run(tasks_path)

        assert result.status == ExecutionStatus.HALTED
        assert result.halted is True
        assert result.plan.tasks[0].status == TaskStatus.HALTED
        assert result.runtime.debug_rounds["T001"] == 2
        assert "halted" in result.log_path.read_text(encoding="utf-8").lower()

    def test_run_stops_when_circuit_breaker_triggers(self, git_repo: Path) -> None:
        spec_dir = git_repo / "specs" / "WI-2026-BREAK"
        tasks_path = _write_tasks_md(
            spec_dir,
            _single_task_md()
            + "\n"
            + _single_task_md().replace("Task 1.1", "Task 1.2").replace(
                "T001", "T002"
            ),
        )
        _write_pipeline_config(
            git_repo,
            max_debug_rounds_per_task=1,
            consecutive_failure_limit=2,
        )

        executor = Executor(
            git_repo,
            task_runner=lambda _task, _runtime: TaskStatus.FAILED,
        )
        result = executor.run(tasks_path)

        assert result.halted is True
        assert "Circuit breaker" in result.error
        assert result.plan.tasks[0].status == TaskStatus.HALTED
        assert result.plan.tasks[1].status == TaskStatus.HALTED

    def test_run_persists_formal_truth_surfaces_for_active_work_item(
        self, git_repo: Path
    ) -> None:
        spec_dir = git_repo / "specs" / "WI-2026-ART"
        tasks_path = _write_tasks_md(spec_dir, _sample_tasks_md())
        (spec_dir / "spec.md").write_text("# Spec\n", encoding="utf-8")
        (spec_dir / "plan.md").write_text("# Plan\n", encoding="utf-8")
        _write_pipeline_config(git_repo, max_tasks_per_batch=2)

        result = Executor(
            git_repo,
            task_runner=lambda _task, _runtime: TaskStatus.COMPLETED,
        ).run(tasks_path)

        wi_dir = git_repo / ".ai-sdlc" / "work-items" / "WI-2026-ART"
        persisted_plan = YamlStore.load(wi_dir / "execution-plan.yaml", ExecutionPlan)
        persisted_runtime = YamlStore.load(wi_dir / "runtime.yaml", RuntimeState)
        persisted_working_set = YamlStore.load(wi_dir / "working-set.yaml", WorkingSet)
        latest_summary = (wi_dir / "latest-summary.md").read_text(encoding="utf-8")

        assert persisted_plan.total_batches == 2
        assert persisted_plan.tasks[:2][0].status == TaskStatus.COMPLETED
        assert persisted_plan.tasks[:2][1].status == TaskStatus.COMPLETED
        assert persisted_plan.tasks[-1].status == TaskStatus.PENDING
        assert persisted_runtime.current_stage == "execute"
        assert persisted_runtime.current_batch == 1
        assert persisted_runtime.last_committed_task == "T002"
        assert persisted_runtime.current_branch != ""
        assert persisted_runtime.last_updated != ""
        assert persisted_working_set.spec_path.endswith("spec.md")
        assert persisted_working_set.plan_path.endswith("plan.md")
        assert persisted_working_set.tasks_path.endswith("tasks.md")
        assert "src/extra.py" in persisted_working_set.active_files
        assert result.status == ExecutionStatus.NEEDS_USER
        assert not result.summary_path.exists()
        assert "Phase 1 complete: 2/2 tasks completed" in latest_summary


class TestIsComplete:
    """is_complete checks."""

    def test_not_complete_initially(self) -> None:
        plan = _make_two_batch_plan()
        exe = BatchExecutor(plan, RuntimeState())
        assert not exe.is_complete()

    def test_complete_after_all_batches(self) -> None:
        plan = _make_two_batch_plan()
        runtime = RuntimeState()
        exe = BatchExecutor(plan, runtime)

        for tid in ["T1", "T2"]:
            exe.advance_task(tid, TaskStatus.COMPLETED)
        exe.advance_batch()
        for tid in ["T3", "T4"]:
            exe.advance_task(tid, TaskStatus.COMPLETED)
        exe.advance_batch()

        assert exe.is_complete()


class TestTaskNotFound:
    """KeyError for unknown task_id."""

    def test_unknown_task_raises_key_error(self) -> None:
        plan = _make_plan([], [])
        exe = BatchExecutor(plan, RuntimeState())

        with pytest.raises(KeyError, match="Task not found: GHOST"):
            exe.advance_task("GHOST", TaskStatus.COMPLETED)


def test_advance_terminal_task_is_noop() -> None:
    tasks = [Task(task_id="T1", title="t", phase=1)]
    plan = ExecutionPlan(
        total_tasks=1,
        total_batches=1,
        tasks=tasks,
        batches=[ExecutionBatch(batch_id=1, phase=1, tasks=["T1"])],
    )
    rt = RuntimeState()
    ex = BatchExecutor(plan, rt)
    ex.advance_task("T1", TaskStatus.COMPLETED)
    # Try to re-complete — should be no-op
    ex.advance_task("T1", TaskStatus.FAILED)
    assert tasks[0].status == TaskStatus.COMPLETED
    assert rt.consecutive_halts == 0


# --- ExecutionLogger ---


class TestExecutionLogger:
    """ExecutionLogger append-only Markdown logging."""

    def test_creates_file_if_not_exists(self, tmp_path: Path) -> None:
        log_path = tmp_path / "subdir" / "execution-log.md"
        ExecutionLogger(log_path)
        assert log_path.exists()
        content = log_path.read_text(encoding="utf-8")
        assert content.startswith("# Execution Log")

    def test_log_task_appends_entry(self, tmp_path: Path) -> None:
        log_path = tmp_path / "log.md"
        elog = ExecutionLogger(log_path)

        ts = elog.log_task("T001", "completed")

        content = log_path.read_text(encoding="utf-8")
        assert "**T001**: completed" in content
        assert ts in content

    def test_log_task_with_details(self, tmp_path: Path) -> None:
        log_path = tmp_path / "log.md"
        elog = ExecutionLogger(log_path)

        elog.log_task("T002", "failed", details="assertion error on line 42")

        content = log_path.read_text(encoding="utf-8")
        assert "assertion error on line 42" in content

    def test_log_batch_appends_summary(self, tmp_path: Path) -> None:
        log_path = tmp_path / "log.md"
        elog = ExecutionLogger(log_path)

        ts = elog.log_batch(1, "All 3 tasks passed.")

        content = log_path.read_text(encoding="utf-8")
        assert "### Batch 1" in content
        assert "All 3 tasks passed." in content
        assert ts != ""

    def test_get_last_log_timestamp(self, tmp_path: Path) -> None:
        log_path = tmp_path / "log.md"
        elog = ExecutionLogger(log_path)
        assert elog.get_last_log_timestamp() == ""

        ts1 = elog.log_task("T1", "completed")
        assert elog.get_last_log_timestamp() == ts1

        ts2 = elog.log_batch(1, "done")
        assert elog.get_last_log_timestamp() == ts2
        assert ts2 >= ts1

    def test_multiple_logs_accumulate(self, tmp_path: Path) -> None:
        log_path = tmp_path / "log.md"
        elog = ExecutionLogger(log_path)

        elog.log_task("T1", "completed")
        elog.log_task("T2", "failed")
        elog.log_task("T3", "completed")

        content = log_path.read_text(encoding="utf-8")
        assert content.count("**T") == 3

    def test_existing_file_not_overwritten(self, tmp_path: Path) -> None:
        log_path = tmp_path / "log.md"
        log_path.write_text("# Existing Log\n\nOld content.\n", encoding="utf-8")

        elog = ExecutionLogger(log_path)
        elog.log_task("T1", "completed")

        content = log_path.read_text(encoding="utf-8")
        assert "Old content." in content
        assert "**T1**: completed" in content


def test_executor_emits_only_tool_events_and_evidence(tmp_path: Path) -> None:
    telemetry = RuntimeTelemetry(tmp_path)
    telemetry.open_workflow_run()
    step_id = telemetry.begin_step("execute")
    plan = _make_plan(
        [Task(task_id="TX", title="Task X", phase=1)],
        [ExecutionBatch(batch_id=1, phase=1, tasks=["TX"])],
    )
    exe = BatchExecutor(
        plan,
        RuntimeState(),
        telemetry=telemetry,
        step_id=step_id,
    )

    step_root = (
        telemetry_local_root(tmp_path)
        / "sessions"
        / telemetry.goal_session_id
        / "runs"
        / telemetry.workflow_run_id
        / "steps"
        / step_id
    )
    before_events = _read_ndjson(step_root / "events.ndjson")

    exe.advance_task("TX", TaskStatus.COMPLETED)

    after_events = _read_ndjson(step_root / "events.ndjson")
    new_events = after_events[len(before_events) :]
    evidence = _read_ndjson(step_root / "evidence.ndjson")

    assert new_events
    assert all(event["trace_layer"] == "tool" for event in new_events)
    assert all(event["status"] == "succeeded" for event in new_events)
    assert evidence[-1]["locator"] == "executor://tasks/TX"
    assert any(
        payload["locator"].startswith("ccp:v1:command_completed:event:")
        for payload in evidence
    )


def test_circuit_breaker_failure_still_emits_tool_event_and_evidence(
    tmp_path: Path,
) -> None:
    telemetry = RuntimeTelemetry(tmp_path)
    telemetry.open_workflow_run()
    step_id = telemetry.begin_step("execute")
    tasks = [
        Task(task_id="A", title="A", phase=1),
        Task(task_id="B", title="B", phase=1),
    ]
    exe = BatchExecutor(
        _make_plan(tasks, [ExecutionBatch(batch_id=1, phase=1, tasks=["A", "B"])]),
        RuntimeState(),
        telemetry=telemetry,
        step_id=step_id,
    )

    for _ in range(MAX_DEBUG_ROUNDS):
        exe.advance_task("A", TaskStatus.FAILED)

    step_root = (
        telemetry_local_root(tmp_path)
        / "sessions"
        / telemetry.goal_session_id
        / "runs"
        / telemetry.workflow_run_id
        / "steps"
        / step_id
    )
    before_event_count = len(_read_ndjson(step_root / "events.ndjson"))
    before_evidence_count = len(_read_ndjson(step_root / "evidence.ndjson"))

    with pytest.raises(CircuitBreakerError, match="BR-031"):
        for _ in range(MAX_DEBUG_ROUNDS):
            exe.advance_task("B", TaskStatus.FAILED)

    after_events = _read_ndjson(step_root / "events.ndjson")
    after_evidence = _read_ndjson(step_root / "evidence.ndjson")

    assert len(after_events) == before_event_count + MAX_DEBUG_ROUNDS
    assert len(after_evidence) == before_evidence_count + (MAX_DEBUG_ROUNDS * 2)
    assert after_events[-1]["trace_layer"] == "tool"
    assert after_events[-1]["status"] == "blocked"
    assert after_evidence[-1]["locator"] == "executor://tasks/B"
    assert any(
        payload["locator"].startswith("ccp:v1:command_completed:event:")
        for payload in after_evidence[before_evidence_count:]
    )
