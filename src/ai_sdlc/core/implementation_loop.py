"""Deterministic local runtime for the Loop Engine implementation loop."""

from __future__ import annotations

import hashlib
import re
import stat
from pathlib import Path

from pydantic import ValidationError

from ai_sdlc.core.design_contract_checks import _verify_design_document_snapshot
from ai_sdlc.core.design_contract_models import (
    DesignContractClose,
    DesignContractInput,
    DesignContractReport,
)
from ai_sdlc.core.design_contract_store import (
    DesignContractArtifacts,
    _design_contract_loop_identity_issue,
    _resolve_design_contract_loop_run_identity,
    design_contract_artifacts,
    design_contract_input_digest,
    resolve_work_item_dir,
)
from ai_sdlc.core.design_contract_store import (
    _read_close as read_design_contract_close,
)
from ai_sdlc.core.design_contract_store import (
    read_loop_run as read_design_contract_loop_run,
)
from ai_sdlc.core.design_contract_store import (
    read_report as read_design_contract_report,
)
from ai_sdlc.core.design_contract_store import (
    validate_explicit_loop_id as validate_design_contract_loop_id,
)
from ai_sdlc.core.implementation_models import (
    CURRENT_IMPLEMENTATION_PATH,
    ImplementationArtifactRef,
    ImplementationClose,
    ImplementationCloseOptions,
    ImplementationCommandResult,
    ImplementationCommandStatus,
    ImplementationCommandSummary,
    ImplementationCurrentPointer,
    ImplementationInput,
    ImplementationNextGuidance,
    ImplementationProgress,
    ImplementationRecordOptions,
    ImplementationReport,
    ImplementationStartOptions,
    ImplementationTaskItem,
    ImplementationTaskProgress,
    ImplementationTasks,
    ImplementationTaskStatus,
    ImplementationVerificationEvidence,
)
from ai_sdlc.core.implementation_store import (
    ImplementationArtifacts,
    append_unique,
    build_implementation_input,
    implementation_artifacts,
    implementation_input_digest,
    read_input,
    read_loop_run,
    read_progress,
    read_tasks,
    repo_relative_path,
    resolve_implementation_loop_run_path,
    resolve_loop_id,
    validate_explicit_loop_id,
)
from ai_sdlc.core.implementation_store import (
    read_report as read_implementation_report,
)
from ai_sdlc.core.loop_artifacts import LoopArtifactStore
from ai_sdlc.core.loop_models import (
    LoopRound,
    LoopRun,
    LoopStatus,
    LoopType,
    utc_now_iso,
)
from ai_sdlc.core.slimming_advice import collect_slimming_advice
from ai_sdlc.core.stable_file_read import read_stable_bytes

_TASK_ID = re.compile(r"\bT\d{2,}\b")
_TASK_SECTION = re.compile(r"(?m)^###\s+(?:Task|任务)\b.*$")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_PRIORITY = re.compile(r"(?:优先级|priority)[^\n]*(P[0-9])\b", re.IGNORECASE)
_CANONICAL_SCOPE = re.compile(r"^\s*-\s*scope\s*:\s*(.*)$", re.IGNORECASE)
_INDENTED_LIST_ITEM = re.compile(r"^\s{2,}-\s+(.+?)\s*$")
_FRONTEND_SIGNAL = re.compile(
    r"(?i)(\b(?:frontend|browser|playwright|vue|react|ui|css)\b|前端|浏览器|页面|组件)"
)


def start_implementation_loop(
    options: ImplementationStartOptions,
) -> ImplementationCommandResult:
    """Start tracking implementation execution after design-contract close."""

    root = options.root.resolve()
    work_item_dir, work_item_blocker = resolve_work_item_dir(root, options.work_item)
    try:
        loop_id = resolve_loop_id(options.loop_id)
    except ValueError as exc:
        return _blocked_result(f"Invalid implementation loop id: {exc}")
    artifacts = implementation_artifacts(root, loop_id)
    planned_refs = artifacts.refs(root)
    if work_item_blocker:
        return _blocked_result(
            work_item_blocker, loop_id=loop_id, artifacts=planned_refs
        )
    if artifacts.loop_run_path.is_file() and not options.dry_run:
        return _blocked_result(
            "Implementation loop id already exists; choose a new --loop-id.",
            loop_id=loop_id,
            artifacts=planned_refs,
        )

    design_contract_loop_id, design_report_path, design_blocker, design_next = (
        _design_contract_gate(
            root,
            options.design_contract_loop_id,
            work_item_id=work_item_dir.name,
        )
    )
    if design_blocker:
        return _blocked_result(
            design_blocker,
            loop_id=loop_id,
            next_action=design_next,
            artifacts=planned_refs,
        )
    task_items, task_blocker = _parse_tasks_file(
        root=root,
        work_item_dir=work_item_dir,
        design_contract_loop_id=design_contract_loop_id,
    )
    if task_blocker:
        return _blocked_result(
            task_blocker,
            loop_id=loop_id,
            next_action=f"Fix {repo_relative_path(root, work_item_dir / 'tasks.md')}.",
            artifacts=planned_refs,
        )

    try:
        impl_input = build_implementation_input(
            root=root,
            loop_id=loop_id,
            work_item_dir=work_item_dir,
            design_contract_loop_id=design_contract_loop_id,
            design_contract_report_path=design_report_path,
            task_items=task_items,
        )
    except ValueError as exc:
        return _blocked_result(
            f"Invalid implementation input: {exc}",
            loop_id=loop_id,
            next_action=(
                f"Fix {repo_relative_path(root, work_item_dir / 'spec.md')} and retry."
            ),
            artifacts=planned_refs,
        )
    tasks = ImplementationTasks(
        loop_id=loop_id,
        work_item_id=work_item_dir.name,
        items=task_items,
    )
    progress = ImplementationProgress(
        loop_id=loop_id,
        work_item_id=work_item_dir.name,
        tasks=[ImplementationTaskProgress(task_id=item.task_id) for item in task_items],
    )
    evidence = ImplementationVerificationEvidence(
        loop_id=loop_id,
        work_item_id=work_item_dir.name,
        tasks=[],
    )
    report = _build_report(root, impl_input, tasks, progress)
    loop_run = _build_loop_run(
        impl_input=impl_input,
        report=report,
        artifacts=artifacts,
        root=root,
    )

    if options.dry_run:
        return _result_from_report(
            report,
            artifacts=planned_refs,
            result="Implementation loop dry run.",
            status=ImplementationCommandStatus.DRY_RUN,
            dry_run=True,
        )

    _write_artifacts(
        root, impl_input, tasks, progress, evidence, report, loop_run, artifacts
    )
    return _result_from_report(
        report,
        artifacts=artifacts.refs(root),
        result="Implementation loop started.",
    )


def record_implementation_progress(
    options: ImplementationRecordOptions,
) -> ImplementationCommandResult:
    """Record local implementation progress for one task."""

    root = options.root.resolve()
    loop_run_path, pointer_blocker = resolve_implementation_loop_run_path(
        root,
        options.loop_id,
    )
    if pointer_blocker:
        return _blocked_result(pointer_blocker)
    if not loop_run_path.is_file():
        return _blocked_result("Implementation loop-run.json does not exist.")
    try:
        loop_run = read_loop_run(loop_run_path)
        validate_explicit_loop_id(loop_run.loop_id)
    except ValueError as exc:
        return _blocked_result(str(exc))
    if loop_run.status == LoopStatus.CLOSED:
        return _blocked_result(
            "Closed implementation loops cannot record more progress.",
            loop_id=loop_run.loop_id,
            next_action=loop_run.next_action,
        )
    artifacts = implementation_artifacts(root, loop_run.loop_id)
    loaded = _read_current_state(
        root,
        artifacts,
        loop_id=loop_run.loop_id,
        input_digest=loop_run.input_digest,
    )
    if isinstance(loaded, ImplementationCommandResult):
        return loaded
    impl_input, tasks, progress = loaded

    task_id = options.task_id.strip()
    if not task_id:
        return _blocked_result("Pass --task-id Txx.", loop_id=loop_run.loop_id)
    task_ids = {item.task_id for item in tasks.items}
    if task_id not in task_ids:
        return _blocked_result(
            f"Unknown implementation task id: {task_id}.",
            loop_id=loop_run.loop_id,
            artifacts=artifacts.refs(root),
        )
    try:
        status = ImplementationTaskStatus(options.status.strip())
    except ValueError:
        return _blocked_result(
            f"Invalid implementation task status: {options.status}.",
            loop_id=loop_run.loop_id,
        )

    progress_by_task = {item.task_id: item for item in progress.tasks}
    current = progress_by_task.get(task_id) or ImplementationTaskProgress(
        task_id=task_id
    )
    evidence = _clean_items((*current.evidence, *options.evidence))
    verification = _clean_items((*current.verification_commands, *options.verification))
    if status == ImplementationTaskStatus.DONE and not evidence and not verification:
        return _blocked_result(
            "Done implementation tasks must include --evidence or --verification.",
            loop_id=loop_run.loop_id,
            artifacts=artifacts.refs(root),
        )
    updated = current.model_copy(
        update={
            "status": status,
            "evidence": evidence,
            "verification_commands": verification,
            "note": options.note.strip() or current.note,
            "updated_at": utc_now_iso(),
        }
    )
    progress.tasks = [
        updated if item.task_id == task_id else item for item in progress.tasks
    ]
    if task_id not in progress_by_task:
        progress.tasks.append(updated)
    evidence_artifact = _evidence_from_progress(progress)
    report = _build_report(root, impl_input, tasks, progress)
    loop_run.status = report.status
    loop_run.updated_at = utc_now_iso()
    loop_run.next_action = report.next_action
    execution_round = _execution_round(loop_run)
    if execution_round is not None:
        execution_round.status = report.status
        execution_round.next_action = report.next_action
    _write_artifacts(
        root,
        impl_input,
        tasks,
        progress,
        evidence_artifact,
        report,
        loop_run,
        artifacts,
    )
    return _result_from_report(
        report,
        artifacts=artifacts.refs(root),
        result=f"Implementation progress recorded for {task_id}.",
    )


def close_implementation_loop(
    options: ImplementationCloseOptions,
) -> ImplementationCommandResult:
    """Close an implementation loop after required task evidence is complete."""

    root = options.root.resolve()
    loop_run_path, pointer_blocker = resolve_implementation_loop_run_path(
        root,
        options.loop_id,
    )
    if pointer_blocker:
        return _blocked_result(pointer_blocker)
    if not options.yes:
        return _blocked_result(
            "Pass --yes after confirming implementation evidence.",
            result="Implementation close requires explicit confirmation.",
            next_action="Repeat the same guarded implementation close command with --yes.",
        )
    try:
        loop_run = read_loop_run(loop_run_path)
        validate_explicit_loop_id(loop_run.loop_id)
    except ValueError as exc:
        return _blocked_result(
            str(exc), result="Implementation loop artifact is malformed."
        )
    expected_loop_id = options.loop_id.strip()
    if expected_loop_id and loop_run.loop_id != expected_loop_id:
        return _blocked_result(
            (
                "Implementation loop identity mismatch: expected "
                f"{expected_loop_id}, found {loop_run.loop_id}."
            ),
            loop_id=expected_loop_id,
            result="Implementation loop artifact is malformed.",
        )
    artifacts = implementation_artifacts(root, loop_run.loop_id)
    loaded = _read_current_state(
        root,
        artifacts,
        loop_id=loop_run.loop_id,
        input_digest=loop_run.input_digest,
    )
    if isinstance(loaded, ImplementationCommandResult):
        return loaded
    impl_input, tasks, progress = loaded
    report = _build_report(root, impl_input, tasks, progress)
    if loop_run.status == LoopStatus.CLOSED and artifacts.close_path.is_file():
        try:
            report = read_implementation_report(artifacts.report_json_path)
        except (OSError, ValueError) as exc:
            return _blocked_result(
                f"Persisted implementation report is malformed: {exc}",
                loop_id=loop_run.loop_id,
                artifacts=artifacts.refs(root, include_close=True),
            )
        result = _result_from_report(
            report,
            artifacts=artifacts.refs(root, include_close=True),
            result="Implementation loop is already closed.",
            closed=True,
            loop_status=LoopStatus.CLOSED,
            next_action=loop_run.next_action or _next_loop_action(report),
        )
        return result
    close_blockers = _close_blockers(tasks, progress)
    if close_blockers:
        report = report.model_copy(
            update={
                "status": LoopStatus.NEEDS_FIX,
                "blocker_count": len(close_blockers),
                "blockers": close_blockers,
                "next_action": _record_next_action(tasks, progress),
            }
        )
        _write_artifacts(
            root,
            impl_input,
            tasks,
            progress,
            _evidence_from_progress(progress),
            report,
            loop_run.model_copy(
                update={
                    "status": LoopStatus.NEEDS_FIX,
                    "next_action": report.next_action,
                    "updated_at": utc_now_iso(),
                }
            ),
            artifacts,
        )
        return _result_from_report(
            report,
            artifacts=artifacts.refs(root),
            result="Implementation loop cannot close while required tasks lack evidence.",
            status=ImplementationCommandStatus.NEEDS_FIX,
            blocker=close_blockers[0],
        )
    return _write_close(root, loop_run, report, artifacts, options.closed_by)


def _write_close(
    root: Path,
    loop_run: LoopRun,
    report: ImplementationReport,
    artifacts: ImplementationArtifacts,
    closed_by: str,
) -> ImplementationCommandResult:
    return _write_implementation_close(
        root,
        loop_run,
        report,
        artifacts,
        closed_by,
    )


def _write_implementation_close(
    root: Path,
    loop_run: LoopRun,
    report: ImplementationReport,
    artifacts: ImplementationArtifacts,
    closed_by: str,
) -> ImplementationCommandResult:
    report = report.model_copy(
        update={
            "status": LoopStatus.PASSED,
            "next_action": _next_loop_action(report),
        }
    )
    next_loop_type = (
        LoopType.FRONTEND_EVIDENCE
        if report.requires_frontend_evidence
        else LoopType.LOCAL_PR_REVIEW
    )
    close = ImplementationClose(
        loop_id=loop_run.loop_id,
        closed_by=closed_by.strip() or "local-user",
        report_path=repo_relative_path(root, artifacts.report_json_path),
        required_task_count=report.required_task_count,
        next_loop_type=next_loop_type,
    )
    loop_run.status = LoopStatus.CLOSED
    loop_run.updated_at = utc_now_iso()
    loop_run.next_action = _next_loop_action(report)
    execution_round = _execution_round(loop_run)
    if execution_round is not None:
        execution_round.status = LoopStatus.CLOSED
        _record_close_outputs(root, execution_round, artifacts)
        execution_round.next_action = loop_run.next_action
    _persist_close_artifacts(root, artifacts, report, close, loop_run)
    return _result_from_report(
        report,
        artifacts=artifacts.refs(root, include_close=True),
        result="Implementation loop closed.",
        closed=True,
        loop_status=LoopStatus.CLOSED,
        next_action=loop_run.next_action,
    )


def _record_close_outputs(
    root: Path,
    execution_round: LoopRound,
    artifacts: ImplementationArtifacts,
) -> None:
    execution_round.output_artifacts = append_unique(
        execution_round.output_artifacts,
        repo_relative_path(root, artifacts.close_path),
    )


def _persist_close_artifacts(
    root: Path,
    artifacts: ImplementationArtifacts,
    report: ImplementationReport,
    close: ImplementationClose,
    loop_run: LoopRun,
) -> None:
    store = LoopArtifactStore(root)
    store.write_json_artifact(artifacts.report_json_path, report)
    store.write_markdown_artifact(
        artifacts.report_md_path, _render_report_markdown(report)
    )
    store.write_json_artifact(artifacts.close_path, close)
    store.write_json_artifact(artifacts.loop_run_path, loop_run)


def _write_artifacts(
    root: Path,
    impl_input: ImplementationInput,
    tasks: ImplementationTasks,
    progress: ImplementationProgress,
    evidence: ImplementationVerificationEvidence,
    report: ImplementationReport,
    loop_run: LoopRun,
    artifacts: ImplementationArtifacts,
) -> None:
    store = LoopArtifactStore(root)
    store.create_loop_run_dir(
        impl_input.loop_id,
        loop_type=LoopType.IMPLEMENTATION.value,
    )
    store.write_json_artifact(artifacts.input_path, impl_input)
    store.write_json_artifact(artifacts.tasks_path, tasks)
    store.write_json_artifact(artifacts.progress_path, progress)
    store.write_json_artifact(artifacts.evidence_path, evidence)
    store.write_json_artifact(artifacts.report_json_path, report)
    store.write_markdown_artifact(
        artifacts.report_md_path,
        _render_report_markdown(report),
    )
    store.write_json_artifact(artifacts.loop_run_path, loop_run)
    store.write_json_artifact(
        artifacts.pointer_path,
        ImplementationCurrentPointer(
            loop_id=impl_input.loop_id,
            loop_run_path=repo_relative_path(root, artifacts.loop_run_path),
        ),
    )


def _read_current_state(
    root: Path,
    artifacts: ImplementationArtifacts,
    *,
    loop_id: str,
    input_digest: str = "",
) -> (
    tuple[ImplementationInput, ImplementationTasks, ImplementationProgress]
    | ImplementationCommandResult
):
    try:
        impl_input = read_input(artifacts.input_path)
        tasks = read_tasks(artifacts.tasks_path)
        progress = read_progress(artifacts.progress_path)
    except ValueError as exc:
        return _blocked_result(
            str(exc),
            loop_id=loop_id,
            result="Implementation loop artifact is malformed.",
            artifacts=artifacts.refs(root),
        )
    if input_digest and implementation_input_digest(impl_input) != input_digest:
        return _blocked_result(
            "Implementation input digest mismatch; the frozen input was modified.",
            loop_id=loop_id,
            result="Implementation loop artifact is malformed or tampered.",
            artifacts=artifacts.refs(root),
        )
    return impl_input, tasks, progress


def _design_contract_gate(
    root: Path,
    design_contract_loop_id: str,
    *,
    work_item_id: str,
) -> tuple[str, str, str, str]:
    loop_run_path, expected_loop_id, target_blocker, target_next = (
        _resolve_design_gate_target(root, design_contract_loop_id)
    )
    if target_blocker:
        return "", "", target_blocker, target_next
    try:
        loop_run = read_design_contract_loop_run(loop_run_path)
    except ValueError as exc:
        return (
            "",
            "",
            (
                f"Design-contract loop must exist and be closed before implementation start: {exc}"
            ),
            "Run ai-sdlc loop design-contract check --wi specs/<work-item>.",
        )
    identity_issue = _design_contract_loop_identity_issue(
        root,
        loop_run_path,
        expected_loop_id,
        loop_run,
    )
    if identity_issue:
        return (
            "",
            "",
            f"Design-contract loop identity is invalid: {identity_issue}",
            "Run ai-sdlc loop design-contract status.",
        )
    artifacts = design_contract_artifacts(root, expected_loop_id)
    if loop_run.status != LoopStatus.CLOSED or not artifacts.close_path.is_file():
        return (
            "",
            "",
            (
                f"Design-contract loop {loop_run.loop_id} must be closed before "
                "implementation start."
            ),
            "Run ai-sdlc loop review --type design-contract "
            f"--loop-id {loop_run.loop_id}.",
        )
    try:
        report = read_design_contract_report(artifacts.report_json_path)
        close = read_design_contract_close(artifacts.close_path)
    except ValueError as exc:
        return (
            "",
            "",
            f"Design-contract report or close artifact is malformed: {exc}",
            ("Run ai-sdlc loop design-contract check --wi specs/<work-item>."),
        )
    blocker = _design_contract_blocker(
        root,
        loop_run,
        report,
        close,
        artifacts,
        expected_loop_id,
        work_item_id,
    )
    if blocker:
        return (
            "",
            "",
            blocker,
            "Run ai-sdlc loop review --type design-contract "
            f"--loop-id {loop_run.loop_id}.",
        )
    input_issue = _design_close_input_issue(
        root,
        loop_run,
        artifacts,
        expected_loop_id,
        work_item_id,
    )
    if input_issue:
        return (
            "",
            "",
            input_issue,
            "Rerun ai-sdlc loop design-contract check with a new loop id.",
        )
    return (
        loop_run.loop_id,
        repo_relative_path(root, artifacts.report_json_path),
        "",
        "",
    )


def _resolve_design_gate_target(
    root: Path,
    design_contract_loop_id: str,
) -> tuple[Path, str, str, str]:
    loop_id = design_contract_loop_id.strip()
    if loop_id:
        try:
            safe_loop_id = validate_design_contract_loop_id(loop_id)
        except ValueError as exc:
            return (
                root,
                "",
                f"Invalid design-contract loop id: {exc}",
                "Run ai-sdlc loop design-contract status.",
            )
        artifacts = design_contract_artifacts(root, safe_loop_id)
        return artifacts.loop_run_path, safe_loop_id, "", ""
    loop_run_path, expected_loop_id, blocker = (
        _resolve_design_contract_loop_run_identity(root, "")
    )
    if blocker:
        return (
            loop_run_path,
            expected_loop_id,
            (
                "A closed current design-contract loop is required before "
                f"implementation start: {blocker}"
            ),
            "Run ai-sdlc loop design-contract check --wi specs/<work-item>.",
        )
    return (
        loop_run_path,
        expected_loop_id,
        "",
        "",
    )


def _design_close_input_issue(
    root: Path,
    loop_run: LoopRun,
    artifacts: DesignContractArtifacts,
    expected_loop_id: str,
    work_item_id: str,
) -> str:
    try:
        payload = LoopArtifactStore(root).read_json_artifact(artifacts.input_path)
        contract_input = DesignContractInput.model_validate(payload)
        if contract_input.loop_id != expected_loop_id:
            raise ValueError("design-contract input loop id changed")
        if contract_input.work_item_id != work_item_id:
            raise ValueError("design-contract input work item changed")
        if design_contract_input_digest(contract_input) != loop_run.input_digest:
            raise ValueError("design-contract input changed after check")
        _verify_design_document_snapshot(root, contract_input)
    except (
        OSError,
        UnicodeError,
        ValueError,
        ValidationError,
    ) as exc:
        return f"Design close input verification failed: {exc}"
    return ""


def _design_contract_blocker(
    root: Path,
    loop_run: LoopRun,
    report: DesignContractReport,
    close: DesignContractClose,
    artifacts: DesignContractArtifacts,
    expected_loop_id: str,
    work_item_id: str,
) -> str:
    close_path = artifacts.close_path
    if loop_run.status != LoopStatus.CLOSED or not close_path.is_file():
        return (
            f"Design-contract loop {loop_run.loop_id} must be closed before "
            "implementation start."
        )
    expected_report_path = repo_relative_path(root, artifacts.report_json_path)
    if (
        report.loop_id != expected_loop_id
        or close.loop_id != expected_loop_id
        or close.report_path != expected_report_path
    ):
        return "Design-contract artifact identity does not match the confirmed loop."
    if loop_run.work_item_id != work_item_id or report.work_item_id != work_item_id:
        return (
            f"Design-contract loop {loop_run.loop_id} belongs to work item "
            f"{report.work_item_id or loop_run.work_item_id}, but implementation "
            f"work item is {work_item_id}."
        )
    if (
        report.status != LoopStatus.NEEDS_REVIEW
        or report.blocker_count != 0
        or close.blocker_count != 0
    ):
        return "Design-contract report or close artifact still contains blockers."
    return ""


def _parse_tasks_file(
    *,
    root: Path,
    work_item_dir: Path,
    design_contract_loop_id: str,
) -> tuple[list[ImplementationTaskItem], str]:
    tasks_path = work_item_dir / "tasks.md"
    try:
        artifacts = design_contract_artifacts(root, design_contract_loop_id)
        payload = LoopArtifactStore(root).read_json_artifact(artifacts.input_path)
        contract_input = DesignContractInput.model_validate(payload)
        content = read_stable_bytes(root, tasks_path)
    except (OSError, UnicodeError, ValueError, ValidationError) as exc:
        return [], f"tasks.md is unavailable or unsafe: {exc}"
    digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
    if digest != contract_input.tasks_digest:
        return [], "tasks.md changed after the design contract was closed."
    text = content.decode("utf-8")
    sections = _task_sections(text)
    if not sections:
        return [], "tasks.md does not contain parseable ### Task or ### 任务 sections."
    items: list[ImplementationTaskItem] = []
    for section in sections:
        task_id = next(iter(_TASK_ID.findall(section)), "")
        if not task_id:
            continue
        priority = _task_priority(section)
        items.append(
            ImplementationTaskItem(
                task_id=task_id,
                title=_task_title(section),
                priority=priority,
                required=priority in {"P0", "P1"},
                files=_task_files(section),
                acceptance=_task_list_after_label(section, "验收标准", "acceptance"),
                verification_hints=_task_list_after_label(
                    section, "验证", "verification"
                ),
                source_path=repo_relative_path(root, tasks_path),
            )
        )
    if not items:
        return [], "tasks.md does not define executable task ids."
    if not any(item.required for item in items):
        return [], "tasks.md must include at least one P0/P1 implementation task."
    return items, ""


def _task_sections(text: str) -> list[str]:
    matches = list(_TASK_SECTION.finditer(text))
    sections: list[str] = []
    for index, match in enumerate(matches):
        next_task_start = (
            matches[index + 1].start() if index + 1 < len(matches) else len(text)
        )
        next_heading_start = _next_peer_heading_start(text, match.end())
        sections.append(text[match.start() : min(next_task_start, next_heading_start)])
    return sections


def _next_peer_heading_start(text: str, start: int) -> int:
    for match in _HEADING.finditer(text, start):
        if len(match.group(1)) <= 2:
            return match.start()
    return len(text)


def _task_title(section: str) -> str:
    first = section.splitlines()[0].strip().lstrip("#").strip()
    return first


def _task_priority(section: str) -> str:
    match = _PRIORITY.search(section)
    return match.group(1).upper() if match else ""


def _task_files(section: str) -> list[str]:
    canonical_scope = _canonical_task_scope(section)
    if canonical_scope:
        return canonical_scope
    for line in section.splitlines():
        if "文件" in line or "files" in line.lower():
            value = _label_value(line)
            if not value:
                continue
            return _split_values(value)
    return []


def _canonical_task_scope(section: str) -> list[str]:
    lines = section.splitlines()
    for index, line in enumerate(lines):
        match = _CANONICAL_SCOPE.match(line)
        if match is None:
            continue
        values = _split_values(match.group(1))
        for candidate in lines[index + 1 :]:
            item = _INDENTED_LIST_ITEM.match(candidate)
            if item is not None:
                values.extend(_split_values(item.group(1)))
                continue
            if candidate.strip() and not candidate[:1].isspace():
                break
        return list(dict.fromkeys(values))
    return []


def _task_list_after_label(section: str, *labels: str) -> list[str]:
    lines = section.splitlines()
    values: list[str] = []
    for index, line in enumerate(lines):
        lower = line.lower()
        if not any(label.lower() in lower for label in labels):
            continue
        inline_value = _label_value(line)
        if inline_value:
            values.extend(_split_values(inline_value))
        for candidate in lines[index + 1 :]:
            stripped = candidate.strip()
            if not stripped:
                continue
            if stripped.startswith("- **") or stripped.startswith("### "):
                break
            if stripped.startswith(("-", "*")) or re.match(r"^\d+[.)]", stripped):
                values.append(stripped.lstrip("-* ").strip())
        break
    return [value for value in values if value]


def _label_value(line: str) -> str:
    if "：" in line:
        return line.split("：", 1)[1].strip()
    if ":" in line:
        return line.split(":", 1)[1].strip()
    return ""


def _split_values(value: str) -> list[str]:
    cleaned = value.strip().strip("`")
    if not cleaned:
        return []
    return [
        item.strip().strip("`")
        for item in re.split(r"[,，]", cleaned)
        if item.strip().strip("`")
    ]


def _build_report(
    root: Path,
    impl_input: ImplementationInput,
    tasks: ImplementationTasks,
    progress: ImplementationProgress,
) -> ImplementationReport:
    progress_by_task = {item.task_id: item for item in progress.tasks}
    required = [item for item in tasks.items if item.required]
    done_required = [
        item
        for item in required
        if progress_by_task.get(item.task_id) is not None
        and progress_by_task[item.task_id].status == ImplementationTaskStatus.DONE
        and _has_evidence(progress_by_task[item.task_id])
    ]
    blocked = [
        progress_by_task[item.task_id]
        for item in required
        if progress_by_task.get(item.task_id) is not None
        and progress_by_task[item.task_id].status == ImplementationTaskStatus.BLOCKED
    ]
    blockers = [f"{item.task_id} is blocked." for item in blocked]
    status = LoopStatus.RUNNING
    if blockers:
        status = LoopStatus.NEEDS_FIX
    elif len(done_required) == len(required):
        status = LoopStatus.NEEDS_REVIEW
    evidence_count = sum(
        len(item.evidence) + len(item.verification_commands) for item in progress.tasks
    )
    return ImplementationReport(
        loop_id=impl_input.loop_id,
        work_item_id=impl_input.work_item_id,
        work_item_path=impl_input.work_item_path,
        status=status,
        required_task_count=len(required),
        done_count=len(done_required),
        blocked_count=len(blocked),
        evidence_count=evidence_count,
        blocker_count=len(blockers),
        blockers=blockers,
        advisories=_implementation_slimming_advisories(root, impl_input),
        requires_frontend_evidence=_requires_frontend_evidence(root, impl_input),
        next_action=_next_action_for_progress(
            tasks,
            progress,
            status,
        ),
    )


def _next_action_for_progress(
    tasks: ImplementationTasks,
    progress: ImplementationProgress,
    status: LoopStatus,
) -> str:
    if status == LoopStatus.NEEDS_REVIEW:
        return (
            "Run ai-sdlc loop review --type implementation "
            f"--loop-id {progress.loop_id}."
        )
    return _record_next_action(tasks, progress)


def _record_next_action(
    tasks: ImplementationTasks,
    progress: ImplementationProgress,
) -> str:
    progress_by_task = {item.task_id: item for item in progress.tasks}
    blocked = [
        item.task_id
        for item in tasks.items
        if item.required
        and progress_by_task.get(item.task_id) is not None
        and progress_by_task[item.task_id].status == ImplementationTaskStatus.BLOCKED
    ]
    if blocked:
        return f"Resolve implementation blocker for {blocked[0]}, then record progress."
    for item in tasks.items:
        progress_item = progress_by_task.get(item.task_id)
        if not item.required:
            continue
        if (
            progress_item is None
            or progress_item.status != ImplementationTaskStatus.DONE
        ):
            return (
                "Run ai-sdlc loop implementation record "
                f"--task-id {item.task_id} --status done "
                '--verification "<command>" --evidence <path>.'
            )
    return (
        f"Run ai-sdlc loop review --type implementation --loop-id {progress.loop_id}."
    )


def _implementation_slimming_advisories(
    root: Path,
    impl_input: ImplementationInput,
) -> list[str]:
    root = root.resolve()
    paths: list[Path] = []
    for path_text in impl_input.declared_scope:
        pattern = Path(path_text)
        if pattern.is_absolute() or ".." in pattern.parts:
            continue
        for candidate in sorted(root.glob(path_text)):
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            kind = _slimming_path_kind(root, candidate)
            if kind == "directory":
                paths.extend(
                    path
                    for path in sorted(candidate.rglob("*"))
                    if _slimming_path_kind(root, path) == "file"
                )
            elif kind == "file":
                paths.append(candidate)
    rendered: list[str] = []
    for advice in collect_slimming_advice(paths):
        path = Path(advice.path)
        try:
            path_text = path.relative_to(root).as_posix()
        except ValueError:
            path_text = path.as_posix()
        location = f"{path_text}:{advice.line}" if advice.line else path_text
        rendered.append(f"{location}: {advice.message}")
    return rendered


def _slimming_path_kind(root: Path, path: Path) -> str | None:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return None
    current = root
    for index, part in enumerate(relative.parts):
        current /= part
        try:
            metadata = current.lstat()
        except OSError:
            return None
        attributes = getattr(metadata, "st_file_attributes", 0)
        if stat.S_ISLNK(metadata.st_mode) or attributes & 0x400:
            return None
        is_leaf = index == len(relative.parts) - 1
        if is_leaf:
            if stat.S_ISREG(metadata.st_mode):
                return "file"
            if stat.S_ISDIR(metadata.st_mode):
                return "directory"
            return None
        if not stat.S_ISDIR(metadata.st_mode):
            return None
    return None


def _close_blockers(
    tasks: ImplementationTasks,
    progress: ImplementationProgress,
) -> list[str]:
    progress_by_task = {item.task_id: item for item in progress.tasks}
    blockers: list[str] = []
    for item in tasks.items:
        if not item.required:
            continue
        progress_item = progress_by_task.get(item.task_id)
        if (
            progress_item is None
            or progress_item.status != ImplementationTaskStatus.DONE
        ):
            blockers.append(f"{item.task_id} is not done.")
            continue
        if not _has_evidence(progress_item):
            blockers.append(f"{item.task_id} has no evidence or verification command.")
    return blockers


def _has_evidence(progress: ImplementationTaskProgress) -> bool:
    return bool(progress.evidence or progress.verification_commands)


def _execution_round(loop_run: LoopRun) -> LoopRound | None:
    return next(
        (item for item in loop_run.rounds if item.round_kind == "execution"),
        None,
    )


def _evidence_from_progress(
    progress: ImplementationProgress,
) -> ImplementationVerificationEvidence:
    return ImplementationVerificationEvidence(
        loop_id=progress.loop_id,
        work_item_id=progress.work_item_id,
        tasks=[
            item
            for item in progress.tasks
            if item.evidence or item.verification_commands
        ],
    )


def _build_loop_run(
    *,
    impl_input: ImplementationInput,
    report: ImplementationReport,
    artifacts: ImplementationArtifacts,
    root: Path,
) -> LoopRun:
    output_artifacts = [
        repo_relative_path(root, artifacts.input_path),
        repo_relative_path(root, artifacts.tasks_path),
        repo_relative_path(root, artifacts.progress_path),
        repo_relative_path(root, artifacts.evidence_path),
        repo_relative_path(root, artifacts.report_json_path),
        repo_relative_path(root, artifacts.report_md_path),
    ]
    return LoopRun(
        loop_id=impl_input.loop_id,
        loop_type=LoopType.IMPLEMENTATION,
        status=report.status,
        work_item_id=impl_input.work_item_id,
        input_digest=implementation_input_digest(impl_input),
        current_round=1,
        rounds=[
            LoopRound(
                round_number=1,
                input_artifacts=[
                    impl_input.spec_path,
                    impl_input.plan_path,
                    impl_input.tasks_path,
                    impl_input.design_contract_report_path,
                ],
                output_artifacts=output_artifacts,
                command=["ai-sdlc", "loop", "implementation", "start"],
                status=report.status,
                result=report.status,
                next_action=report.next_action,
            )
        ],
        next_action=report.next_action,
    )


def _result_from_report(
    report: ImplementationReport,
    *,
    artifacts: list[ImplementationArtifactRef],
    result: str,
    status: ImplementationCommandStatus | None = None,
    closed: bool = False,
    dry_run: bool = False,
    loop_status: LoopStatus | str = "",
    next_action: str = "",
    blocker: str = "",
) -> ImplementationCommandResult:
    resolved_status = status or (
        ImplementationCommandStatus.NEEDS_FIX
        if report.status == LoopStatus.NEEDS_FIX
        else ImplementationCommandStatus.READY
    )
    resolved_next_action = next_action or report.next_action
    resolved_loop_status = loop_status or report.status
    return ImplementationCommandResult(
        status=resolved_status,
        result=result,
        loop_id=report.loop_id,
        loop_status=resolved_loop_status,
        work_item_id=report.work_item_id,
        work_item_path=report.work_item_path,
        required_task_count=report.required_task_count,
        done_count=report.done_count,
        blocked_count=report.blocked_count,
        evidence_count=report.evidence_count,
        blocker_count=report.blocker_count,
        advisories=list(report.advisories),
        closed=closed,
        dry_run=dry_run,
        blocker=blocker,
        next_action=resolved_next_action,
        next_guidance=_next_guidance_for_result(
            report,
            next_action=resolved_next_action,
            closed=closed,
            artifacts=artifacts,
        ),
        artifacts=artifacts,
        implementation=ImplementationCommandSummary(
            status=_status_value(resolved_loop_status),
            work_item_id=report.work_item_id,
            work_item_path=report.work_item_path,
            required_task_count=report.required_task_count,
            done_count=report.done_count,
            blocked_count=report.blocked_count,
            evidence_count=report.evidence_count,
            report_path=_artifact_path(artifacts, "implementation-report-json"),
            closed=closed,
        ),
    )


def _blocked_result(
    blocker: str,
    *,
    result: str = "Implementation loop is blocked.",
    loop_id: str = "",
    next_action: str = "Run ai-sdlc loop implementation start --wi specs/<work-item>.",
    artifacts: list[ImplementationArtifactRef] | None = None,
) -> ImplementationCommandResult:
    return ImplementationCommandResult(
        status=ImplementationCommandStatus.BLOCKED,
        result=result,
        loop_id=loop_id,
        loop_status=LoopStatus.BLOCKED,
        blocker=blocker,
        next_action=next_action,
        next_guidance=ImplementationNextGuidance(
            command="",
            reason=blocker,
            requires_model=False,
            writes_artifacts=False,
            writes_code=False,
            safety="blocked",
        ),
        artifacts=artifacts or [],
    )


def _next_guidance_for_result(
    report: ImplementationReport,
    *,
    next_action: str,
    closed: bool,
    artifacts: list[ImplementationArtifactRef],
) -> ImplementationNextGuidance:
    evidence = [
        artifact.path
        for artifact in artifacts
        if artifact.kind in {"implementation-report-json", "implementation-progress"}
    ]
    if closed:
        next_loop = (
            "frontend-evidence"
            if report.requires_frontend_evidence
            else "local-pr-review"
        )
        return ImplementationNextGuidance(
            command=_command_from_next_action(next_action),
            reason=f"Implementation is closed; continue with {next_loop}.",
            requires_model=not report.requires_frontend_evidence,
            writes_artifacts=True,
            writes_code=False,
            safety="writes_project_artifacts",
            evidence=evidence,
        )
    if report.status == LoopStatus.NEEDS_REVIEW:
        return ImplementationNextGuidance(
            command=(
                f"ai-sdlc loop review --type implementation --loop-id {report.loop_id}"
            ),
            reason="Implementation evidence is ready for bounded adversarial review.",
            requires_model=True,
            writes_artifacts=False,
            writes_code=False,
            safety="safe_read_only",
            evidence=evidence,
        )
    return ImplementationNextGuidance(
        command=_command_from_next_action(next_action),
        reason="Record implementation evidence until all required tasks are done.",
        requires_model=False,
        writes_artifacts=True,
        writes_code=False,
        safety="writes_project_artifacts",
        evidence=evidence,
    )


def _artifact_path(
    artifacts: list[ImplementationArtifactRef],
    kind: str,
) -> str:
    return next((artifact.path for artifact in artifacts if artifact.kind == kind), "")


def _status_value(status: LoopStatus | str) -> str:
    return status.value if isinstance(status, LoopStatus) else str(status)


def _command_from_next_action(next_action: str) -> str:
    text = next_action.strip()
    if text.lower().startswith("run "):
        text = text[4:].strip()
    if not text.startswith("ai-sdlc "):
        return ""
    return text[:-1] if text.endswith(".") else text


def _requires_frontend_evidence(root: Path, impl_input: ImplementationInput) -> bool:
    texts = []
    for path_text in (
        impl_input.spec_path,
        impl_input.plan_path,
        impl_input.tasks_path,
    ):
        path = root / path_text
        if path.is_file():
            texts.append(path.read_text(encoding="utf-8"))
    return bool(_FRONTEND_SIGNAL.search("\n".join(texts)))


def _next_loop_action(report: ImplementationReport) -> str:
    if report.requires_frontend_evidence:
        return f"Run ai-sdlc loop frontend-evidence start --wi {report.work_item_path}."
    return "Run ai-sdlc pr-review start."


def _render_report_markdown(report: ImplementationReport) -> str:
    lines = [
        "# Implementation Loop Report",
        "",
        f"- Loop ID: `{report.loop_id}`",
        f"- Status: `{report.status}`",
        f"- Work item: `{report.work_item_id}`",
        f"- Required tasks: {report.required_task_count}",
        f"- Done: {report.done_count}",
        f"- Blocked: {report.blocked_count}",
        f"- Evidence items: {report.evidence_count}",
        f"- Next: {report.next_action}",
    ]
    if report.blockers:
        lines.extend(["", "## Blockers"])
        lines.extend(f"- {blocker}" for blocker in report.blockers)
    if report.advisories:
        lines.extend(["", "## Slimming advice"])
        lines.extend(f"- {advice}" for advice in report.advisories)
    return "\n".join(lines) + "\n"


def _clean_items(values: tuple[str, ...] | list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        text = value.strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


__all__ = [
    "CURRENT_IMPLEMENTATION_PATH",
    "ImplementationCloseOptions",
    "ImplementationCommandResult",
    "ImplementationRecordOptions",
    "ImplementationStartOptions",
    "close_implementation_loop",
    "record_implementation_progress",
    "start_implementation_loop",
]
