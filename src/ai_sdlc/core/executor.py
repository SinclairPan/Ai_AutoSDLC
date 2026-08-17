"""Batch execution engine with debug-round counting, circuit breaker, and execution logging."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ai_sdlc.branch.git_client import GitClient, GitError
from ai_sdlc.context.state import (
    build_execute_working_set,
    load_execution_plan,
    save_execution_plan,
    save_latest_summary,
    save_runtime_state,
    save_working_set,
)
from ai_sdlc.generators.doc_gen import TasksParser
from ai_sdlc.models.state import (
    ExecutionBatch,
    ExecutionPlan,
    ExecutionStatus,
    RuntimeState,
    Task,
    TaskStatus,
)
from ai_sdlc.telemetry.enums import TelemetryEventStatus
from ai_sdlc.telemetry.runtime import RuntimeTelemetry
from ai_sdlc.utils.helpers import now_iso

logger = logging.getLogger(__name__)

MAX_DEBUG_ROUNDS = 3
MAX_CONSECUTIVE_HALTS = 2


def _dedupe_text_items(values: list[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        text = str(value)
        if text not in unique:
            unique.append(text)
    return unique


@dataclass(slots=True)
class ExecutorSettings:
    """Runtime settings loaded from pipeline.yml for execute stage behavior."""

    max_tasks_per_batch: int = 12
    auto_archive: bool = True
    max_debug_rounds_per_task: int = MAX_DEBUG_ROUNDS
    consecutive_failure_limit: int = MAX_CONSECUTIVE_HALTS


@dataclass(slots=True)
class TaskExecutionOutcome:
    """Normalized outcome returned by a task runner."""

    status: TaskStatus
    details: str = ""


@dataclass(slots=True)
class ExecutionResult:
    """Top-level execute-stage result returned by Executor.run()."""

    plan: ExecutionPlan
    runtime: RuntimeState
    log_path: Path
    summary_path: Path
    status: ExecutionStatus = ExecutionStatus.PENDING
    detail: str = ""
    tasks_path: Path | None = None
    target_task_id: str = ""
    next_action: str = ""
    commit_hashes: list[str] = field(default_factory=list)
    completed_batches: int = 0
    last_log_timestamp: str = ""
    last_commit_timestamp: str = ""
    halted: bool = False
    error: str = ""

    def __post_init__(self) -> None:
        self.commit_hashes = _dedupe_text_items(self.commit_hashes)


TaskRunner = Callable[[Task, RuntimeState], TaskStatus | TaskExecutionOutcome]


class CircuitBreakerError(Exception):
    """Raised when consecutive task HALTs trigger the circuit breaker (BR-031)."""


class TaskRunnerHeadChangedError(Exception):
    """Raised when an injected task runner changes the repository HEAD."""


class BatchExecutor:
    """Manage batch-by-batch task execution with safety controls.

    Enforces BR-030 (debug round limit) and BR-031 (circuit breaker).
    """

    def __init__(
        self,
        plan: ExecutionPlan,
        runtime: RuntimeState,
        *,
        max_debug_rounds: int = MAX_DEBUG_ROUNDS,
        max_consecutive_halts: int = MAX_CONSECUTIVE_HALTS,
        telemetry: RuntimeTelemetry | None = None,
        step_id: str | None = None,
    ) -> None:
        self._plan = plan
        self._runtime = runtime
        self._max_debug_rounds = max_debug_rounds
        self._max_consecutive_halts = max_consecutive_halts
        self._telemetry = telemetry
        self._step_id = step_id
        self._task_map: dict[str, Task] = {t.task_id: t for t in plan.tasks}

    @property
    def plan(self) -> ExecutionPlan:
        """Return the execution plan."""
        return self._plan

    @property
    def runtime(self) -> RuntimeState:
        """Return the runtime state."""
        return self._runtime

    def get_current_batch(self) -> ExecutionBatch | None:
        """Return the current batch, or None if all batches are done."""
        idx = self._runtime.current_batch
        if idx >= len(self._plan.batches):
            return None
        return self._plan.batches[idx]

    def advance_task(self, task_id: str, status: TaskStatus) -> Task:
        """Update a task's status with debug-round and circuit-breaker checks.

        Raises:
            KeyError: If task_id not found.
            CircuitBreakerError: If consecutive HALTs exceed limit (BR-031).
        """
        task = self._task_map.get(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")

        terminal = {TaskStatus.COMPLETED, TaskStatus.HALTED, TaskStatus.CANCELLED}
        if task.status in terminal:
            logger.warning(
                "Task %s already in terminal state %s",
                task_id,
                task.status.value,
            )
            return task

        if status == TaskStatus.FAILED:
            pending_error: CircuitBreakerError | None = None
            try:
                self._handle_failure(task)
            except CircuitBreakerError as exc:
                pending_error = exc
            self._emit_tool_trace(task)
            if pending_error is not None:
                raise pending_error
            return task

        if status == TaskStatus.COMPLETED:
            task.status = TaskStatus.COMPLETED
            self._runtime.last_committed_task = task_id
            self._runtime.consecutive_halts = 0
            logger.info("Task %s completed", task_id)
            self._emit_tool_trace(task)
            return task

        task.status = status
        self._emit_tool_trace(task)
        return task

    def _handle_failure(self, task: Task) -> None:
        """Process a task failure: increment debug rounds, check limits."""
        tid = task.task_id
        rounds = self._runtime.debug_rounds.get(tid, 0) + 1
        self._runtime.debug_rounds[tid] = rounds
        logger.warning(
            "Task %s failed (round %d/%d)",
            tid,
            rounds,
            self._max_debug_rounds,
        )

        if rounds >= self._max_debug_rounds:
            task.status = TaskStatus.HALTED
            self._runtime.consecutive_halts += 1
            logger.error("Task %s HALTED after %d debug rounds (BR-030)", tid, rounds)
            if self._runtime.consecutive_halts >= self._max_consecutive_halts:
                raise CircuitBreakerError(
                    f"Circuit breaker triggered: "
                    f"{self._runtime.consecutive_halts} "
                    f"consecutive task HALTs (BR-031)"
                )
        else:
            task.status = TaskStatus.FAILED

    def advance_batch(self) -> ExecutionBatch | None:
        """Advance to the next batch if the current one is done."""
        batch = self.get_current_batch()
        if batch is None:
            return None

        statuses = [
            self._task_map[tid].status for tid in batch.tasks if tid in self._task_map
        ]
        if any(status == TaskStatus.HALTED for status in statuses):
            batch.status = TaskStatus.HALTED
            return batch
        if any(status == TaskStatus.CANCELLED for status in statuses):
            batch.status = TaskStatus.CANCELLED
            return batch
        if not statuses or not all(status == TaskStatus.COMPLETED for status in statuses):
            return batch

        batch.status = TaskStatus.COMPLETED
        batch.completed_at = now_iso()
        self._runtime.current_batch += 1
        self._plan.current_batch = self._runtime.current_batch
        logger.info(
            "Batch %d completed, advancing to batch %d",
            batch.batch_id,
            self._runtime.current_batch,
        )

        return self.get_current_batch()

    def is_complete(self) -> bool:
        """Check if all batches have been processed."""
        return self._runtime.current_batch >= len(self._plan.batches)

    def _emit_tool_trace(self, task: Task) -> None:
        """Emit executor-owned tool telemetry without writing workflow events."""
        if self._telemetry is None or self._step_id is None:
            return
        self._telemetry.record_tool_control_point(
            step_id=self._step_id,
            control_point_name="command_completed",
            status=self._tool_status(task.status),
            details={
                "task_id": task.task_id,
                "task_status": task.status.value,
            },
        )
        self._telemetry.record_tool_evidence(
            step_id=self._step_id,
            locator=f"executor://tasks/{task.task_id}",
            digest=f"status:{task.status.value}",
        )

    @staticmethod
    def _tool_status(status: TaskStatus) -> TelemetryEventStatus:
        if status is TaskStatus.COMPLETED:
            return TelemetryEventStatus.SUCCEEDED
        if status is TaskStatus.FAILED:
            return TelemetryEventStatus.FAILED
        if status is TaskStatus.HALTED:
            return TelemetryEventStatus.BLOCKED
        if status is TaskStatus.CANCELLED:
            return TelemetryEventStatus.CANCELLED
        return TelemetryEventStatus.STARTED


# ── execution logger ──


class ExecutionLogger:
    """Append-only logger writing execution results to a Markdown file."""

    def __init__(self, log_path: Path) -> None:
        self._path = log_path
        self._last_timestamp = ""
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text("# Execution Log\n\n", encoding="utf-8")

    def log_task(self, task_id: str, status: str, details: str = "") -> str:
        """Append a task execution record. Returns ISO timestamp."""
        ts = now_iso()
        self._last_timestamp = ts
        line = f"- [{ts}] **{task_id}**: {status}"
        if details:
            line += f" — {details}"
        line += "\n"
        self._append(line)
        return ts

    def log_batch(self, batch_id: int, summary: str) -> str:
        """Append a batch summary record. Returns ISO timestamp."""
        ts = now_iso()
        self._last_timestamp = ts
        self._append(f"\n### Batch {batch_id}\n\n{summary}\n\n")
        return ts

    def get_last_log_timestamp(self) -> str:
        """Return the timestamp of the most recent log entry."""
        return self._last_timestamp

    def _append(self, text: str) -> None:
        """Append text to the log file."""
        with self._path.open("a", encoding="utf-8") as f:
            f.write(text)


class Executor:
    """Execute a tasks.md plan batch by batch and emit required close-out artifacts."""

    def __init__(
        self,
        root: Path,
        *,
        task_runner: TaskRunner | None = None,
        git_client_factory: type[GitClient] = GitClient,
    ) -> None:
        self.root = root.resolve()
        self._task_runner = task_runner
        self._git_client_factory = git_client_factory
        self._settings = self._load_settings()

    def run(
        self,
        tasks_file: Path,
        *,
        runtime: RuntimeState | None = None,
    ) -> ExecutionResult:
        """Execute the given tasks file end-to-end and return close-out metadata."""
        tasks_file = tasks_file.resolve()
        spec_dir = tasks_file.parent
        log_path = spec_dir / "task-execution-log.md"
        summary_path = spec_dir / "development-summary.md"
        summary_log_path = spec_dir / "execution-log.md"
        runtime_state = runtime or RuntimeState(current_stage="execute")
        runtime_state.current_stage = "execute"
        if not runtime_state.started_at:
            runtime_state.started_at = now_iso()
        if not runtime_state.current_branch:
            runtime_state.current_branch = self._current_branch()

        plan = TasksParser().parse(tasks_file)
        plan = self._rebatch_plan(plan, self._settings.max_tasks_per_batch)
        work_item_id = spec_dir.name
        persisted_plan = load_execution_plan(self.root, work_item_id)
        if persisted_plan is not None and self._same_plan_topology(
            plan,
            persisted_plan,
        ):
            self._restore_persisted_plan_state(
                plan,
                persisted_plan,
                runtime_state,
            )
        else:
            self._restore_completed_batches(plan, runtime_state.current_batch)

        stopped_task = self._first_stopped_task(plan)
        if stopped_task is not None:
            detail = (
                f"Task {stopped_task.task_id} remains {stopped_task.status.value}; "
                "AI-SDLC will not run the next task or promote the batch."
            )
            return ExecutionResult(
                plan=plan,
                runtime=runtime_state,
                log_path=log_path,
                summary_path=summary_path,
                status=ExecutionStatus.HALTED,
                detail=detail,
                tasks_path=tasks_file,
                target_task_id=stopped_task.task_id,
                next_action=(
                    f"Inspect and explicitly resolve {stopped_task.task_id} before "
                    "rerunning; AI-SDLC preserves the stopped state by default."
                ),
                completed_batches=runtime_state.current_batch,
                halted=True,
                error=detail,
            )

        pending_task = self._next_pending_task(plan)
        if self._task_runner is None:
            target_task_id = pending_task.task_id if pending_task is not None else ""
            detail = (
                f"No task runner is available for {target_task_id}; "
                "AI-SDLC will not fabricate execution progress."
                if target_task_id
                else "No task runner is available; AI-SDLC will not fabricate execution progress."
            )
            next_action = self._implementation_next_action(target_task_id)
            return ExecutionResult(
                plan=plan,
                runtime=runtime_state,
                log_path=log_path,
                summary_path=summary_path,
                status=ExecutionStatus.NEEDS_USER,
                detail=detail,
                tasks_path=tasks_file,
                target_task_id=target_task_id,
                next_action=next_action,
            )

        logger_ = ExecutionLogger(log_path)
        executor = BatchExecutor(
            plan,
            runtime_state,
            max_debug_rounds=self._settings.max_debug_rounds_per_task,
            max_consecutive_halts=self._settings.consecutive_failure_limit,
        )
        task_map = {task.task_id: task for task in plan.tasks}
        latest_summary = ""

        self._persist_truth_surfaces(
            work_item_id=work_item_id,
            plan=plan,
            runtime=runtime_state,
            spec_dir=spec_dir,
            tasks_file=tasks_file,
            batch=executor.get_current_batch(),
            latest_summary=latest_summary,
        )

        last_log_timestamp = ""
        halted = False
        error = ""
        runner_head_changed = False
        batch = executor.get_current_batch()
        if batch is not None:
            if batch.started_at is None:
                batch.started_at = now_iso()
            self._persist_truth_surfaces(
                work_item_id=work_item_id,
                plan=plan,
                runtime=runtime_state,
                spec_dir=spec_dir,
                tasks_file=tasks_file,
                batch=batch,
                latest_summary=latest_summary,
            )

            try:
                for task_id in batch.tasks:
                    task = task_map[task_id]
                    if task.status == TaskStatus.COMPLETED:
                        continue
                    if task.status in {TaskStatus.HALTED, TaskStatus.CANCELLED}:
                        break
                    runtime_state.current_task = task.task_id
                    self._persist_truth_surfaces(
                        work_item_id=work_item_id,
                        plan=plan,
                        runtime=runtime_state,
                        spec_dir=spec_dir,
                        tasks_file=tasks_file,
                        batch=batch,
                        latest_summary=latest_summary,
                    )
                    last_log_timestamp = self._run_task_with_retry(
                        executor,
                        logger_,
                        task,
                    )
                    self._persist_truth_surfaces(
                        work_item_id=work_item_id,
                        plan=plan,
                        runtime=runtime_state,
                        spec_dir=spec_dir,
                        tasks_file=tasks_file,
                        batch=batch,
                        latest_summary=latest_summary,
                    )
                    self._sync_summary_log(log_path, summary_log_path)
                    if task.status in {TaskStatus.HALTED, TaskStatus.CANCELLED}:
                        break

                next_batch = executor.advance_batch()
                batch_summary = self._batch_summary(batch, plan)
                last_log_timestamp = logger_.log_batch(batch.batch_id, batch_summary)
                self._sync_summary_log(log_path, summary_log_path)
                runtime_state.current_task = ""
                latest_summary = self._latest_summary_markdown(
                    batch_summary,
                    runtime_state,
                    plan,
                )
                self._persist_truth_surfaces(
                    work_item_id=work_item_id,
                    plan=plan,
                    runtime=runtime_state,
                    spec_dir=spec_dir,
                    tasks_file=tasks_file,
                    batch=next_batch or batch,
                    latest_summary=latest_summary,
                )
                if any(
                    task_map[task_id].status
                    in {TaskStatus.HALTED, TaskStatus.CANCELLED}
                    for task_id in batch.tasks
                ):
                    halted = True
                    error = "The current execution batch contains a halted task."
            except TaskRunnerHeadChangedError as exc:
                halted = True
                runner_head_changed = True
                error = str(exc)
                self._persist_truth_surfaces(
                    work_item_id=work_item_id,
                    plan=plan,
                    runtime=runtime_state,
                    spec_dir=spec_dir,
                    tasks_file=tasks_file,
                    batch=batch,
                    latest_summary=latest_summary,
                )
            except CircuitBreakerError as exc:
                halted = True
                error = str(exc)
                self._persist_truth_surfaces(
                    work_item_id=work_item_id,
                    plan=plan,
                    runtime=runtime_state,
                    spec_dir=spec_dir,
                    tasks_file=tasks_file,
                    batch=batch,
                    latest_summary=latest_summary,
                )

        target = self._next_pending_task(plan)
        target_task_id = (
            target.task_id if target is not None else runtime_state.last_committed_task
        )
        status = ExecutionStatus.HALTED if halted else ExecutionStatus.NEEDS_USER
        detail = (
            error
            if halted
            else "The current batch ran, but review and a user-approved commit are still required."
        )

        next_action = self._post_batch_next_action(target_task_id)
        if runner_head_changed:
            next_action = (
                "Inspect the unexpected commit and ask the user whether to keep or "
                "revert it before rerunning AI-SDLC; no automatic rollback was performed."
            )

        return ExecutionResult(
            plan=plan,
            runtime=runtime_state,
            log_path=log_path,
            summary_path=summary_path,
            status=status,
            detail=detail,
            tasks_path=tasks_file,
            target_task_id=target_task_id,
            next_action=next_action,
            commit_hashes=[],
            completed_batches=runtime_state.current_batch,
            last_log_timestamp=last_log_timestamp,
            last_commit_timestamp="",
            halted=halted,
            error=error,
        )

    @staticmethod
    def _next_pending_task(plan: ExecutionPlan) -> Task | None:
        return next(
            (
                task
                for task in plan.tasks
                if task.status not in {
                    TaskStatus.COMPLETED,
                    TaskStatus.HALTED,
                    TaskStatus.CANCELLED,
                }
            ),
            None,
        )

    @staticmethod
    def _restore_completed_batches(
        plan: ExecutionPlan,
        completed_batches: int,
    ) -> None:
        """Rebuild prior batch truth when resuming from checkpoint progress."""
        task_map = {task.task_id: task for task in plan.tasks}
        for batch in plan.batches[:completed_batches]:
            batch.status = TaskStatus.COMPLETED
            for task_id in batch.tasks:
                task = task_map.get(task_id)
                if task is not None:
                    task.status = TaskStatus.COMPLETED

    @staticmethod
    def _same_plan_topology(
        plan: ExecutionPlan,
        persisted: ExecutionPlan,
    ) -> bool:
        current_tasks = [
            task.model_dump(mode="json", exclude={"status"}) for task in plan.tasks
        ]
        persisted_tasks = [
            task.model_dump(mode="json", exclude={"status"})
            for task in persisted.tasks
        ]
        current_batches = [
            batch.model_dump(
                mode="json",
                exclude={"status", "started_at", "completed_at"},
            )
            for batch in plan.batches
        ]
        persisted_batches = [
            batch.model_dump(
                mode="json",
                exclude={"status", "started_at", "completed_at"},
            )
            for batch in persisted.batches
        ]
        return current_tasks == persisted_tasks and current_batches == persisted_batches

    @staticmethod
    def _restore_persisted_plan_state(
        plan: ExecutionPlan,
        persisted: ExecutionPlan,
        runtime: RuntimeState,
    ) -> None:
        for task, persisted_task in zip(plan.tasks, persisted.tasks, strict=True):
            task.status = persisted_task.status
        for batch, persisted_batch in zip(
            plan.batches,
            persisted.batches,
            strict=True,
        ):
            batch.status = persisted_batch.status
            batch.started_at = persisted_batch.started_at
            batch.completed_at = persisted_batch.completed_at

        stopped_batch_indexes = [
            index
            for index, batch in enumerate(plan.batches)
            if any(
                task.status in {TaskStatus.HALTED, TaskStatus.CANCELLED}
                for task in plan.tasks
                if task.task_id in batch.tasks
            )
        ]
        if stopped_batch_indexes:
            runtime.current_batch = min(runtime.current_batch, stopped_batch_indexes[0])
        plan.current_batch = runtime.current_batch

    @staticmethod
    def _first_stopped_task(plan: ExecutionPlan) -> Task | None:
        return next(
            (
                task
                for task in plan.tasks
                if task.status in {TaskStatus.HALTED, TaskStatus.CANCELLED}
            ),
            None,
        )

    @staticmethod
    def _implementation_next_action(task_id: str) -> str:
        task_label = task_id or "the next pending task"
        return (
            f"Implement {task_label} with the active AI agent, stage the intended changes, "
            "then run ai-sdlc pr-review start --diff-source local-staged "
            "--provider local-agent."
        )

    @staticmethod
    def _post_batch_next_action(task_id: str) -> str:
        next_task = f" Next pending task: {task_id}." if task_id else ""
        return (
            "Run ai-sdlc stage show execute, stage the current batch, then run "
            "ai-sdlc pr-review start --diff-source local-staged --provider local-agent. "
            "Allow at most one repair re-review, obtain a user-approved commit, and rerun "
            f"ai-sdlc run.{next_task}"
        )

    def _persist_truth_surfaces(
        self,
        *,
        work_item_id: str,
        plan: ExecutionPlan,
        runtime: RuntimeState,
        spec_dir: Path,
        tasks_file: Path,
        batch: ExecutionBatch | None,
        latest_summary: str,
    ) -> None:
        runtime.last_updated = now_iso()
        save_execution_plan(self.root, work_item_id, plan)
        save_runtime_state(self.root, work_item_id, runtime)
        save_working_set(
            self.root,
            work_item_id,
            build_execute_working_set(
                self.root,
                spec_dir,
                tasks_file,
                active_files=self._batch_active_files(plan, batch),
                context_summary=latest_summary.strip(),
            ),
        )
        if latest_summary:
            save_latest_summary(self.root, work_item_id, latest_summary)

    @staticmethod
    def _batch_active_files(
        plan: ExecutionPlan,
        batch: ExecutionBatch | None,
    ) -> list[str]:
        if batch is None:
            return []
        task_map = {task.task_id: task for task in plan.tasks}
        files: list[str] = []
        for task_id in batch.tasks:
            task = task_map.get(task_id)
            if task is None:
                continue
            for path in task.file_paths:
                if path not in files:
                    files.append(path)
        return files

    @staticmethod
    def _latest_summary_markdown(
        batch_summary: str,
        runtime: RuntimeState,
        plan: ExecutionPlan,
    ) -> str:
        return (
            "# Latest Summary\n\n"
            f"Current Batch: {runtime.current_batch}/{plan.total_batches}\n\n"
            f"Last Task: {runtime.last_committed_task or 'none'}\n\n"
            f"{batch_summary}\n"
        )

    def _current_branch(self) -> str:
        try:
            return self._git_client_factory(self.root).current_branch()
        except GitError:
            return ""

    def _run_task_with_retry(
        self,
        executor: BatchExecutor,
        logger_: ExecutionLogger,
        task: Task,
    ) -> str:
        """Run a single task until it reaches a terminal state."""
        last_log_timestamp = ""
        while task.status not in {
            TaskStatus.COMPLETED,
            TaskStatus.HALTED,
            TaskStatus.CANCELLED,
        }:
            try:
                raw_outcome = self._invoke_task_runner(task, executor.runtime)
            except TaskRunnerHeadChangedError:
                task.status = TaskStatus.HALTED
                batch = executor.get_current_batch()
                if batch is not None:
                    batch.status = TaskStatus.HALTED
                raise
            outcome = self._normalize_outcome(raw_outcome)
            updated = executor.advance_task(task.task_id, outcome.status)
            last_log_timestamp = logger_.log_task(
                task.task_id,
                updated.status.value,
                details=outcome.details,
            )
            if updated.status == TaskStatus.FAILED:
                continue
        return last_log_timestamp

    def _invoke_task_runner(
        self,
        task: Task,
        runtime: RuntimeState,
    ) -> TaskStatus | TaskExecutionOutcome:
        before_head = self._head_commit()
        runner_error: Exception | None = None
        raw_outcome: TaskStatus | TaskExecutionOutcome | None = None
        try:
            if self._task_runner is None:  # pragma: no cover - guarded by run()
                raise RuntimeError("Task runner is unavailable")
            raw_outcome = self._task_runner(task, runtime)
        except Exception as exc:  # Preserve runner failure after the HEAD check.
            runner_error = exc

        after_head = self._head_commit()
        if after_head != before_head:
            raise TaskRunnerHeadChangedError(
                f"Task runner changed HEAD while executing {task.task_id}; the task and "
                "batch remain halted. Inspect the unexpected commit and ask the user "
                "whether to keep or revert it before rerunning."
            ) from runner_error
        if runner_error is not None:
            raise runner_error
        if raw_outcome is None:  # pragma: no cover - TaskRunner contract excludes None.
            raise RuntimeError("Task runner returned no outcome")
        return raw_outcome

    def _head_commit(self) -> str:
        try:
            return self._git_client_factory(self.root).resolve_revision("HEAD")
        except GitError as exc:
            raise TaskRunnerHeadChangedError(
                "AI-SDLC could not verify repository HEAD around the task runner; "
                "the task was not advanced."
            ) from exc

    @staticmethod
    def _normalize_outcome(
        raw: TaskStatus | TaskExecutionOutcome,
    ) -> TaskExecutionOutcome:
        if isinstance(raw, TaskExecutionOutcome):
            return raw
        return TaskExecutionOutcome(status=raw)

    def _load_settings(self) -> ExecutorSettings:
        """Load execute-stage settings from project pipeline.yml."""
        settings = ExecutorSettings()
        cfg_candidates = [
            self.root / ".ai-sdlc" / "config" / "pipeline.yml",
            self.root / "config" / "pipeline.yml",
        ]
        cfg_path = next((path for path in cfg_candidates if path.exists()), None)
        if cfg_path is None:
            return settings

        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        execute_stage: dict[str, Any] = {}
        for stage in raw.get("stages", []):
            if isinstance(stage, dict) and stage.get("id") == "execute":
                execute_stage = stage
                break
        batch_cfg = execute_stage.get("batch", {})
        circuit_cfg = raw.get("circuit_breaker", {})

        settings.max_tasks_per_batch = self._int_or_default(
            batch_cfg.get("max_tasks_per_batch"),
            settings.max_tasks_per_batch,
        )
        settings.auto_archive = bool(batch_cfg.get("auto_archive", settings.auto_archive))
        settings.max_debug_rounds_per_task = self._int_or_default(
            circuit_cfg.get("max_debug_rounds_per_task"),
            settings.max_debug_rounds_per_task,
        )
        settings.consecutive_failure_limit = self._int_or_default(
            circuit_cfg.get("consecutive_failure_limit"),
            settings.consecutive_failure_limit,
        )
        return settings

    @staticmethod
    def _int_or_default(value: Any, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _rebatch_plan(plan: ExecutionPlan, max_tasks_per_batch: int) -> ExecutionPlan:
        """Split phase batches further when pipeline.yml limits batch size."""
        if max_tasks_per_batch <= 0 or len(plan.tasks) <= max_tasks_per_batch:
            return plan

        phase_map: dict[int, list[str]] = {}
        for task in plan.tasks:
            phase_map.setdefault(task.phase, []).append(task.task_id)

        batches: list[ExecutionBatch] = []
        batch_id = 1
        for phase in sorted(phase_map):
            task_ids = phase_map[phase]
            for idx in range(0, len(task_ids), max_tasks_per_batch):
                batches.append(
                    ExecutionBatch(
                        batch_id=batch_id,
                        phase=phase,
                        tasks=task_ids[idx : idx + max_tasks_per_batch],
                    )
                )
                batch_id += 1

        plan.batches = batches
        plan.total_batches = len(batches)
        return plan

    @staticmethod
    def _batch_summary(batch: ExecutionBatch, plan: ExecutionPlan) -> str:
        task_map = {task.task_id: task for task in plan.tasks}
        completed = sum(
            1
            for task_id in batch.tasks
            if task_map[task_id].status == TaskStatus.COMPLETED
        )
        halted = sum(
            1 for task_id in batch.tasks if task_map[task_id].status == TaskStatus.HALTED
        )
        return (
            f"Phase {batch.phase} complete: {completed}/{len(batch.tasks)} tasks "
            f"completed, {halted} halted."
        )

    @staticmethod
    def _sync_summary_log(task_log: Path, summary_log: Path) -> None:
        """Keep the work-item summary log aligned with the task log."""
        summary_log.write_text(task_log.read_text(encoding="utf-8"), encoding="utf-8")

    def _write_summary(
        self,
        path: Path,
        *,
        plan: ExecutionPlan,
        runtime: RuntimeState,
        commit_hashes: list[str],
        halted: bool,
        error: str,
    ) -> None:
        completed_tasks = sum(
            1 for task in plan.tasks if task.status == TaskStatus.COMPLETED
        )
        halted_tasks = sum(1 for task in plan.tasks if task.status == TaskStatus.HALTED)
        lines = [
            "# Development Summary",
            "",
            f"Status: {'halted' if halted else 'completed'}",
            f"Total Tasks: {plan.total_tasks}",
            f"Completed Tasks: {completed_tasks}",
            f"Halted Tasks: {halted_tasks}",
            f"Total Batches: {plan.total_batches}",
            f"Completed Batches: {runtime.current_batch}",
            f"Last Committed Task: {runtime.last_committed_task or '-'}",
            "",
            "## Commit Records",
        ]
        if commit_hashes:
            lines.extend(f"- {commit_hash}" for commit_hash in commit_hashes)
        else:
            lines.append("- none")
        if error:
            lines.extend(["", "## Error", error])
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
