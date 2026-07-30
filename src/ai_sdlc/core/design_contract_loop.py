"""Deterministic local runtime for the Loop Engine design-contract loop."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from ai_sdlc.core.design_close_artifact_verification import (
    _capture_design_close_artifact_digests,
)
from ai_sdlc.core.design_close_authority_store import _record_design_close_authority
from ai_sdlc.core.design_contract_checks import (
    _verify_design_document_snapshot,
    analyze_design_contract,
    render_report_markdown,
)
from ai_sdlc.core.design_contract_models import (
    CURRENT_DESIGN_CONTRACT_PATH,
    ContractCoverageItem,
    DesignContractArtifactRef,
    DesignContractCheckOptions,
    DesignContractClose,
    DesignContractCloseOptions,
    DesignContractCommandResult,
    DesignContractCommandStatus,
    DesignContractCommandSummary,
    DesignContractCoverageMatrix,
    DesignContractCurrentPointer,
    DesignContractInput,
    DesignContractNextGuidance,
    DesignContractReport,
)
from ai_sdlc.core.design_contract_store import (
    DesignContractArtifacts,
    _design_contract_loop_identity_issue,
    _resolve_design_contract_loop_run_identity,
    append_unique,
    build_contract_input,
    design_contract_artifacts,
    read_loop_run,
    read_report,
    repo_relative_path,
    resolve_loop_id,
    resolve_work_item_dir,
)
from ai_sdlc.core.design_scope_authority_transition import (
    _advance_design_scope_authority,
)
from ai_sdlc.core.loop_artifacts import LoopArtifactStore
from ai_sdlc.core.loop_models import (
    LoopRound,
    LoopRun,
    LoopStatus,
    LoopType,
    utc_now_iso,
)
from ai_sdlc.core.requirement_loop import (
    RequirementFreeze,
    RequirementIntake,
    _requirement_artifacts,
    _requirement_freeze_digest,
    _requirement_intake_digest,
    _requirement_loop_identity_issue,
    _RequirementArtifacts,
    _resolve_requirement_loop_run_path,
)
from ai_sdlc.core.requirement_loop import (
    _read_loop_run as _read_requirement_loop_run,
)
from ai_sdlc.core.requirement_loop import (
    _validate_explicit_loop_id as _validate_requirement_loop_id,
)
from ai_sdlc.core.scope_authority_store import (
    ScopeAuthorityIntegrityError,
    _design_scope_input_digest,
    _verify_design_scope_authority,
    _verify_requirement_scope_authority,
)
from ai_sdlc.core.stable_file_read import (
    _stable_regular_file_exists,
    read_stable_text,
)
from ai_sdlc.core.stage_review.adapters import DesignContractStageAdapter
from ai_sdlc.core.stage_review.artifacts import create_json_exclusive
from ai_sdlc.core.stage_review.close_gate import (
    execute_stage_close,
    prepare_loop_stage_close,
)


def check_design_contract_loop(
    options: DesignContractCheckOptions,
) -> DesignContractCommandResult:
    """Check formal docs for implementation-readiness and persist artifacts."""

    prepared = _prepare_design_check(options)
    if isinstance(prepared, DesignContractCommandResult):
        return prepared
    root, work_item_dir, loop_id, artifacts, planned_refs = prepared

    built_input = _build_checked_contract_input(
        root,
        loop_id,
        work_item_dir,
        options.requirement_loop_id,
        planned_refs,
    )
    if isinstance(built_input, DesignContractCommandResult):
        return built_input
    contract_input = built_input
    resolved_requirement_loop_id, requirement_blocker, requirement_next_action = (
        _required_requirement_loop_id(root, contract_input.requirement_loop_id)
    )
    if requirement_blocker:
        return _blocked_result(
            requirement_blocker,
            loop_id=loop_id,
            next_action=requirement_next_action,
            artifacts=planned_refs,
        )
    contract_input = contract_input.model_copy(
        update={"requirement_loop_id": resolved_requirement_loop_id}
    )
    requirement_blocker, requirement_next_action, authority = _requirement_loop_gate(
        root,
        contract_input.requirement_loop_id,
        work_item_id=contract_input.work_item_id,
    )
    if requirement_blocker:
        return _blocked_result(
            requirement_blocker,
            loop_id=loop_id,
            next_action=requirement_next_action,
            artifacts=planned_refs,
        )
    contract_input = contract_input.model_copy(update=authority)
    if options.dry_run:
        return DesignContractCommandResult(
            status=DesignContractCommandStatus.DRY_RUN,
            result="Design-contract loop dry run.",
            loop_id=loop_id,
            loop_status=LoopStatus.CREATED,
            work_item_id=contract_input.work_item_id,
            work_item_path=contract_input.work_item_path,
            dry_run=True,
            next_action="Run ai-sdlc loop design-contract check without --dry-run.",
            next_guidance=DesignContractNextGuidance(
                command=f"ai-sdlc loop design-contract check --wi {contract_input.work_item_path}",
                reason="Dry run does not write artifacts; rerun without --dry-run to persist the contract report.",
                requires_model=False,
                writes_artifacts=True,
                writes_code=False,
                safety="writes_project_artifacts",
                evidence=[
                    contract_input.spec_path,
                    contract_input.plan_path,
                    contract_input.tasks_path,
                ],
            ),
            artifacts=planned_refs,
            design_contract=_command_summary(
                contract_input,
                status=LoopStatus.CREATED,
                artifacts=planned_refs,
            ),
        )

    report = analyze_design_contract(root, contract_input)
    report.next_action = _next_action_for_report(report)
    try:
        (
            previous_input,
            previous_loop_input_digest,
            loop_run_must_be_absent,
        ) = _previous_design_check(
            root,
            artifacts,
        )
        _advance_design_scope_authority(
            root,
            contract_input,
            previous_input=previous_input,
            previous_loop_input_digest=previous_loop_input_digest,
        )
    except (ScopeAuthorityIntegrityError, ValueError) as exc:
        return _blocked_result(
            f"Design checked authority snapshot is unavailable: {exc}",
            loop_id=loop_id,
            artifacts=planned_refs,
        )
    loop_run = _build_loop_run(
        contract_input=contract_input,
        report=report,
        loop_status=report.status,
        artifacts=artifacts,
        root=root,
    )
    try:
        _write_check_artifacts(
            root,
            contract_input,
            report,
            loop_run,
            artifacts,
            loop_run_must_be_absent=loop_run_must_be_absent,
        )
    except ScopeAuthorityIntegrityError as exc:
        return _blocked_result(
            f"Design check artifacts are unavailable: {exc}",
            loop_id=loop_id,
            artifacts=planned_refs,
        )
    return _result_from_report(
        report,
        artifacts=artifacts.refs(root),
        result=(
            "Design contract passed."
            if not report.blocker_count
            else "Design contract needs fixes."
        ),
    )


def _previous_design_check(
    root: Path,
    artifacts: DesignContractArtifacts,
) -> tuple[DesignContractInput | None, str, bool]:
    try:
        input_exists = _stable_regular_file_exists(root, artifacts.input_path)
        run_exists = _stable_regular_file_exists(root, artifacts.loop_run_path)
    except ValueError as exc:
        raise ScopeAuthorityIntegrityError(
            "previous design check artifacts are unavailable"
        ) from exc
    if not input_exists and not run_exists:
        return None, "", True
    if not input_exists:
        raise ScopeAuthorityIntegrityError(
            "previous design check artifacts are incomplete"
        )
    try:
        payload = json.loads(
            read_stable_text(root, artifacts.input_path, encoding="utf-8")
        )
        previous_input = DesignContractInput.model_validate(payload)
        if not run_exists:
            # 初次检查可能在 input 原子落盘后中断；共享 authority 会在推进时复验它。
            return (
                previous_input,
                _design_scope_input_digest(previous_input),
                True,
            )
        previous_run = read_loop_run(artifacts.loop_run_path, root=root)
    except (OSError, ValueError, ValidationError) as exc:
        raise ScopeAuthorityIntegrityError(
            "previous design check artifacts are unavailable"
        ) from exc
    return previous_input, previous_run.input_digest, False


def _prepare_design_check(
    options: DesignContractCheckOptions,
) -> (
    tuple[
        Path,
        Path,
        str,
        DesignContractArtifacts,
        list[DesignContractArtifactRef],
    ]
    | DesignContractCommandResult
):
    root = options.root.resolve()
    work_item_dir, work_item_blocker = resolve_work_item_dir(root, options.work_item)
    if not options.dry_run and not options.loop_id.strip() and not work_item_blocker:
        closed_current = _closed_current_recheck_result(root, work_item_dir)
        if closed_current is not None:
            return closed_current
    try:
        loop_id = resolve_loop_id(options.loop_id)
    except ValueError as exc:
        return _blocked_result(f"Invalid design-contract loop id: {exc}")
    artifacts = design_contract_artifacts(root, loop_id)
    planned_refs = artifacts.refs(root)
    closed_result = _closed_recheck_result(root, artifacts)
    if closed_result is not None:
        return closed_result
    if work_item_blocker:
        return _blocked_result(work_item_blocker, artifacts=planned_refs)
    return root, work_item_dir, loop_id, artifacts, planned_refs


def _build_checked_contract_input(
    root: Path,
    loop_id: str,
    work_item_dir: Path,
    requirement_loop_id: str,
    planned_refs: list[DesignContractArtifactRef],
) -> DesignContractInput | DesignContractCommandResult:
    try:
        return build_contract_input(
            root=root,
            loop_id=loop_id,
            work_item_dir=work_item_dir,
            requirement_loop_id=requirement_loop_id,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        return _blocked_result(
            f"Formal design documents are unavailable or unsafe: {exc}",
            loop_id=loop_id,
            artifacts=planned_refs,
        )


def close_design_contract_loop(
    options: DesignContractCloseOptions,
) -> DesignContractCommandResult:
    """Close the current design-contract loop after explicit confirmation."""

    root = options.root.resolve()
    loop_run_path, expected_loop_id, pointer_blocker = (
        _resolve_design_contract_loop_run_identity(
            root,
            options.loop_id,
        )
    )
    if pointer_blocker:
        return _blocked_result(pointer_blocker)
    if not options.yes:
        return _blocked_result(
            "Pass --yes after confirming the design contract report.",
            result="Design-contract close requires explicit confirmation.",
            next_action="Run ai-sdlc loop design-contract close --yes.",
        )
    context = _load_design_close_context(
        root,
        loop_run_path,
        expected_loop_id,
    )
    if isinstance(context, DesignContractCommandResult):
        return context
    loop_run, report, verified_input, artifacts = context
    return _close_verified_design_context(
        root,
        options,
        loop_run,
        report,
        verified_input,
        artifacts,
    )


def _load_design_close_context(
    root: Path,
    loop_run_path: Path,
    expected_loop_id: str,
) -> (
    tuple[
        LoopRun,
        DesignContractReport,
        DesignContractInput,
        DesignContractArtifacts,
    ]
    | DesignContractCommandResult
):
    try:
        loop_run = read_loop_run(loop_run_path, root=root)
    except ValueError as exc:
        return _blocked_result(
            str(exc),
            result="Design-contract loop artifact is malformed.",
        )
    identity_issue = _design_contract_loop_identity_issue(
        root,
        loop_run_path,
        expected_loop_id,
        loop_run,
    )
    if identity_issue:
        return _blocked_result(
            identity_issue,
            loop_id=expected_loop_id,
            result="Design-contract loop artifact is malformed.",
        )
    artifacts = design_contract_artifacts(root, expected_loop_id)
    try:
        report = read_report(artifacts.report_json_path)
    except ValueError as exc:
        return _blocked_result(
            str(exc),
            loop_id=loop_run.loop_id,
            artifacts=artifacts.refs(root),
        )
    verified_input = _verified_design_close_input(
        root,
        loop_run,
        report,
        artifacts,
    )
    if isinstance(verified_input, DesignContractCommandResult):
        return verified_input
    return loop_run, report, verified_input, artifacts


def _close_verified_design_context(
    root: Path,
    options: DesignContractCloseOptions,
    loop_run: LoopRun,
    report: DesignContractReport,
    verified_input: DesignContractInput,
    artifacts: DesignContractArtifacts,
) -> DesignContractCommandResult:
    existing = _existing_design_close_result(
        root,
        loop_run,
        report,
        verified_input,
        artifacts,
    )
    if existing is not None:
        return existing
    if report.blocker_count or loop_run.status != LoopStatus.PASSED:
        return _result_from_report(
            report,
            artifacts=artifacts.refs(root),
            result="Design contract cannot close while blockers remain.",
        )
    refreshed = _refresh_report_before_close(
        root,
        loop_run,
        artifacts,
        verified_input,
    )
    if isinstance(refreshed, DesignContractCommandResult):
        return refreshed
    report, loop_run = refreshed
    if report.blocker_count or loop_run.status != LoopStatus.PASSED:
        return _result_from_report(
            report,
            artifacts=artifacts.refs(root),
            result="Design contract cannot close while blockers remain.",
        )
    return _write_close(
        root,
        loop_run,
        report,
        verified_input,
        artifacts,
        options.closed_by,
    )


def _existing_design_close_result(
    root: Path,
    loop_run: LoopRun,
    report: DesignContractReport,
    verified_input: DesignContractInput,
    artifacts: DesignContractArtifacts,
) -> DesignContractCommandResult | None:
    try:
        close_exists = _trusted_close_artifact_exists(root, artifacts)
    except ScopeAuthorityIntegrityError as exc:
        return _blocked_result(
            str(exc),
            loop_id=loop_run.loop_id,
            artifacts=artifacts.refs(root, include_close=True),
        )
    if loop_run.status == LoopStatus.CLOSED and close_exists:
        return _already_closed_design_result(
            root, loop_run, report, verified_input, artifacts
        )
    if loop_run.status == LoopStatus.CLOSED or close_exists:
        return _blocked_result(
            "Existing closed design-contract artifact is unavailable.",
            loop_id=loop_run.loop_id,
            artifacts=artifacts.refs(root, include_close=True),
        )
    return None


def _already_closed_design_result(
    root: Path,
    loop_run: LoopRun,
    report: DesignContractReport,
    verified_input: DesignContractInput,
    artifacts: DesignContractArtifacts,
) -> DesignContractCommandResult:
    try:
        _verify_design_document_snapshot(root, verified_input)
    except (OSError, UnicodeError, ValueError) as exc:
        return _blocked_result(
            f"Closed design document snapshot changed: {exc}",
            loop_id=loop_run.loop_id,
            next_action="Rerun design-contract check with a new loop id.",
            artifacts=artifacts.refs(root, include_close=True),
        )
    result = _result_from_report(
        report,
        artifacts=artifacts.refs(root, include_close=True),
        result="Design contract is already closed.",
        closed=True,
        loop_status=LoopStatus.CLOSED,
        next_action=loop_run.next_action
        or _implementation_next_action(report.work_item_id),
    )
    return _execute_design_close_gate(
        root,
        loop_run,
        verified_input,
        artifacts,
        lambda: result,
    )


def _write_check_artifacts(
    root: Path,
    contract_input: DesignContractInput,
    report: DesignContractReport,
    loop_run: LoopRun,
    artifacts: DesignContractArtifacts,
    *,
    loop_run_must_be_absent: bool = False,
) -> None:
    store = LoopArtifactStore(root)
    store.create_loop_run_dir(
        contract_input.loop_id,
        loop_type=LoopType.DESIGN_CONTRACT.value,
    )
    store.write_json_artifact(artifacts.input_path, contract_input)
    store.write_json_artifact(
        artifacts.coverage_matrix_path,
        DesignContractCoverageMatrix(
            loop_id=contract_input.loop_id,
            work_item_id=contract_input.work_item_id,
            items=report.coverage_items,
        ),
    )
    store.write_json_artifact(artifacts.report_json_path, report)
    store.write_markdown_artifact(
        artifacts.report_md_path, render_report_markdown(report)
    )
    if loop_run_must_be_absent:
        try:
            created = create_json_exclusive(
                artifacts.loop_run_path,
                loop_run.model_dump(mode="json"),
            )
        except (OSError, ValueError) as exc:
            raise ScopeAuthorityIntegrityError(
                "previous design check loop-run could not be committed"
            ) from exc
        if not created:
            raise ScopeAuthorityIntegrityError(
                "previous design check loop-run appeared during recovery"
            )
    else:
        store.write_json_artifact(artifacts.loop_run_path, loop_run)
    store.write_json_artifact(
        artifacts.pointer_path,
        DesignContractCurrentPointer(
            loop_id=contract_input.loop_id,
            loop_run_path=repo_relative_path(root, artifacts.loop_run_path),
        ),
    )


def _refresh_report_before_close(
    root: Path,
    loop_run: LoopRun,
    artifacts: DesignContractArtifacts,
    contract_input: DesignContractInput,
) -> tuple[DesignContractReport, LoopRun] | DesignContractCommandResult:
    resolved_requirement_loop_id, requirement_blocker, requirement_next_action = (
        _required_requirement_loop_id(root, contract_input.requirement_loop_id)
    )
    if requirement_blocker:
        return _blocked_result(
            requirement_blocker,
            loop_id=loop_run.loop_id,
            next_action=requirement_next_action,
            artifacts=artifacts.refs(root),
        )
    contract_input = contract_input.model_copy(
        update={"requirement_loop_id": resolved_requirement_loop_id}
    )
    requirement_blocker, requirement_next_action, authority = _requirement_loop_gate(
        root,
        contract_input.requirement_loop_id,
        work_item_id=contract_input.work_item_id,
    )
    if requirement_blocker:
        return _blocked_result(
            requirement_blocker,
            loop_id=loop_run.loop_id,
            next_action=requirement_next_action,
            artifacts=artifacts.refs(root),
        )
    stored_authority = {
        "authorized_scope_families": contract_input.authorized_scope_families,
        "scope_authority_ref": contract_input.scope_authority_ref,
        "scope_authority_digest": contract_input.scope_authority_digest,
    }
    if stored_authority != authority:
        return _blocked_result(
            "Requirement scope authority changed after design-contract check.",
            loop_id=loop_run.loop_id,
            next_action="Start and freeze a new requirement loop.",
            artifacts=artifacts.refs(root),
        )
    report = analyze_design_contract(root, contract_input)
    report.next_action = _next_action_for_report(report)
    refreshed_loop_run = _build_loop_run(
        contract_input=contract_input,
        report=report,
        loop_status=report.status,
        artifacts=artifacts,
        root=root,
    )
    _write_check_artifacts(root, contract_input, report, refreshed_loop_run, artifacts)
    return report, refreshed_loop_run


def _verified_design_close_input(
    root: Path,
    loop_run: LoopRun,
    report: DesignContractReport,
    artifacts: DesignContractArtifacts,
) -> DesignContractInput | DesignContractCommandResult:
    if (
        report.loop_id != loop_run.loop_id
        or report.work_item_id != loop_run.work_item_id
    ):
        return _blocked_result(
            "Design-contract report identity does not match the confirmed loop.",
            loop_id=loop_run.loop_id,
            artifacts=artifacts.refs(root),
        )
    try:
        payload = LoopArtifactStore(root).read_json_artifact(artifacts.input_path)
        contract_input = DesignContractInput.model_validate(payload)
    except (OSError, ValueError, ValidationError) as exc:
        return _blocked_result(
            f"Design-contract input artifact is malformed: {exc}",
            result="Design-contract close requires a readable current input artifact.",
            loop_id=loop_run.loop_id,
            artifacts=artifacts.refs(root),
        )
    if (
        contract_input.loop_id != loop_run.loop_id
        or contract_input.work_item_id != loop_run.work_item_id
    ):
        return _blocked_result(
            "Design-contract input identity does not match the confirmed loop.",
            loop_id=loop_run.loop_id,
            artifacts=artifacts.refs(root),
        )
    try:
        _verify_design_scope_authority(
            root,
            contract_input,
            loop_input_digest=loop_run.input_digest,
            expected_loop_id=loop_run.loop_id,
            expected_work_item_id=loop_run.work_item_id,
        )
    except ScopeAuthorityIntegrityError as exc:
        return _blocked_result(
            f"Design checked authority snapshot changed: {exc}",
            loop_id=loop_run.loop_id,
            next_action="Rerun design-contract check with a new loop id.",
            artifacts=artifacts.refs(root),
        )
    return contract_input


def _write_close(
    root: Path,
    loop_run: LoopRun,
    report: DesignContractReport,
    contract_input: DesignContractInput,
    artifacts: DesignContractArtifacts,
    closed_by: str,
) -> DesignContractCommandResult:
    return _execute_design_close_gate(
        root,
        loop_run,
        contract_input,
        artifacts,
        lambda: _write_design_close(root, loop_run, report, artifacts, closed_by),
    )


def _write_design_close(
    root: Path,
    loop_run: LoopRun,
    report: DesignContractReport,
    artifacts: DesignContractArtifacts,
    closed_by: str,
) -> DesignContractCommandResult:
    close = DesignContractClose(
        loop_id=loop_run.loop_id,
        closed_by=closed_by.strip() or "local-user",
        report_path=repo_relative_path(root, artifacts.report_json_path),
    )
    loop_run.status = LoopStatus.CLOSED
    loop_run.updated_at = utc_now_iso()
    loop_run.next_action = _implementation_next_action(report.work_item_id)
    loop_run.current_round = 1
    if loop_run.rounds:
        loop_run.rounds[0].status = LoopStatus.CLOSED
        loop_run.rounds[0].output_artifacts = append_unique(
            loop_run.rounds[0].output_artifacts,
            repo_relative_path(root, artifacts.close_path),
        )
        loop_run.rounds[0].next_action = loop_run.next_action
    store = LoopArtifactStore(root)
    store.write_json_artifact(artifacts.close_path, close)
    store.write_json_artifact(artifacts.loop_run_path, loop_run)
    return _result_from_report(
        report,
        artifacts=artifacts.refs(root, include_close=True),
        result="Design contract closed.",
        closed=True,
        loop_status=LoopStatus.CLOSED,
        next_action=loop_run.next_action,
    )


def _execute_design_close_gate(
    root: Path,
    loop_run: LoopRun,
    contract_input: DesignContractInput,
    artifacts: DesignContractArtifacts,
    writer: Callable[[], DesignContractCommandResult],
) -> DesignContractCommandResult:
    try:
        expected_artifact_digests = _capture_design_close_artifact_digests(
            root,
            artifacts,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        return _blocked_result(
            f"Design close authority could not prepare artifacts: {exc}",
            loop_id=loop_run.loop_id,
            next_action="Rerun ai-sdlc loop design-contract check with a new loop id.",
            artifacts=artifacts.refs(root),
        )
    prepared = prepare_loop_stage_close(
        root=root,
        adapter=DesignContractStageAdapter(),
        loop_run=loop_run,
        close_kind="design-contract-close",
        target_status=LoopStatus.CLOSED.value,
        close_artifact_path=artifacts.close_path,
    )
    result = execute_stage_close(prepared, writer)
    try:
        _record_design_close_authority(
            root,
            contract_input,
            artifacts,
            prepared,
            expected_artifact_digests,
        )
    except ScopeAuthorityIntegrityError as exc:
        return _blocked_result(
            f"Design close authority could not be committed: {exc}",
            loop_id=loop_run.loop_id,
            next_action="Retry ai-sdlc loop design-contract close --yes.",
            artifacts=artifacts.refs(
                root, include_close=artifacts.close_path.is_file()
            ),
        )
    return result


def _closed_recheck_result(
    root: Path,
    artifacts: DesignContractArtifacts,
) -> DesignContractCommandResult | None:
    try:
        close_exists = _trusted_close_artifact_exists(root, artifacts)
    except ScopeAuthorityIntegrityError as exc:
        return _blocked_result(
            str(exc),
            artifacts=artifacts.refs(root, include_close=True),
        )
    if not close_exists:
        try:
            if not _stable_regular_file_exists(root, artifacts.loop_run_path):
                return None
            loop_run = read_loop_run(artifacts.loop_run_path, root=root)
        except ValueError:
            return None
        if loop_run.status != LoopStatus.CLOSED:
            return None
        return _blocked_result(
            "Existing closed design-contract artifact is unavailable.",
            loop_id=loop_run.loop_id,
            artifacts=artifacts.refs(root, include_close=True),
        )
    try:
        loop_run = read_loop_run(artifacts.loop_run_path, root=root)
    except ValueError as exc:
        return _blocked_result(
            f"Existing closed design-contract loop is malformed: {exc}",
            artifacts=artifacts.refs(root, include_close=True),
        )
    next_action = _implementation_next_action(loop_run.work_item_id)
    return _blocked_result(
        "Design-contract loop is already closed; start implementation instead of rechecking it.",
        result="Design-contract loop is already closed.",
        loop_id=loop_run.loop_id,
        next_action=next_action,
        artifacts=artifacts.refs(root, include_close=True),
    )


def _closed_current_recheck_result(
    root: Path,
    work_item_dir: Path,
) -> DesignContractCommandResult | None:
    loop_run_path, expected_loop_id, pointer_blocker = (
        _resolve_design_contract_loop_run_identity(root, "")
    )
    if pointer_blocker:
        if pointer_blocker == "No current design-contract loop exists.":
            return None
        return _blocked_result(pointer_blocker)
    try:
        loop_run = read_loop_run(loop_run_path, root=root)
    except ValueError:
        return None
    if _design_contract_loop_identity_issue(
        root,
        loop_run_path,
        expected_loop_id,
        loop_run,
    ):
        return None
    if (
        loop_run.status != LoopStatus.CLOSED
        or loop_run.work_item_id != work_item_dir.name
    ):
        return None
    artifacts = design_contract_artifacts(root, expected_loop_id)
    return _current_closed_design_result(root, loop_run, artifacts)


def _current_closed_design_result(
    root: Path,
    loop_run: LoopRun,
    artifacts: DesignContractArtifacts,
) -> DesignContractCommandResult:
    try:
        close_exists = _trusted_close_artifact_exists(root, artifacts)
    except ScopeAuthorityIntegrityError as exc:
        return _blocked_result(
            str(exc),
            loop_id=loop_run.loop_id,
            artifacts=artifacts.refs(root, include_close=True),
        )
    if not close_exists:
        return _blocked_result(
            "Existing closed design-contract artifact is unavailable.",
            loop_id=loop_run.loop_id,
            artifacts=artifacts.refs(root, include_close=True),
        )
    try:
        report = read_report(artifacts.report_json_path)
    except ValueError as exc:
        return _blocked_result(
            f"Existing closed design-contract report is malformed: {exc}",
            artifacts=artifacts.refs(root, include_close=True),
        )
    next_action = loop_run.next_action or _implementation_next_action(
        report.work_item_id
    )
    return _result_from_report(
        report,
        artifacts=artifacts.refs(root, include_close=True),
        result="Design contract is already closed.",
        closed=True,
        loop_status=LoopStatus.CLOSED,
        next_action=next_action,
    )


def _trusted_close_artifact_exists(
    root: Path,
    artifacts: DesignContractArtifacts,
) -> bool:
    try:
        return _stable_regular_file_exists(root, artifacts.close_path)
    except ValueError as exc:
        raise ScopeAuthorityIntegrityError(
            "Existing closed design-contract artifact is unavailable."
        ) from exc


def _requirement_loop_gate(
    root: Path,
    requirement_loop_id: str,
    *,
    work_item_id: str = "",
) -> tuple[str, str, dict[str, object]]:
    loop_id, blocker, next_action = _required_requirement_loop_id(
        root,
        requirement_loop_id,
    )
    if blocker:
        return blocker, next_action, {}
    try:
        safe_loop_id = _validate_requirement_loop_id(loop_id)
    except ValueError as exc:
        return (
            f"Invalid requirement loop id: {exc}",
            "Run ai-sdlc loop requirement status.",
            {},
        )
    artifacts = _requirement_artifacts(root, safe_loop_id)
    freeze, blocker, next_action = _load_requirement_freeze(
        root,
        safe_loop_id,
        artifacts,
    )
    if blocker:
        return blocker, next_action, {}
    assert freeze is not None
    intake, blocker, next_action = _load_requirement_intake(
        root,
        safe_loop_id,
        artifacts,
        freeze,
    )
    if blocker:
        return blocker, next_action, {}
    assert intake is not None
    blocker, next_action = _requirement_authority_issue(
        safe_loop_id,
        intake,
        work_item_id,
    )
    if blocker:
        return blocker, next_action, {}
    try:
        _verify_requirement_scope_authority(
            root,
            loop_id=safe_loop_id,
            work_item_id=intake.work_item_id,
            intake_path=repo_relative_path(root, artifacts.intake_path),
            intake_digest=freeze.intake_digest,
            freeze_path=repo_relative_path(root, artifacts.freeze_path),
            freeze_digest=_requirement_freeze_digest(freeze),
        )
    except ScopeAuthorityIntegrityError as exc:
        return (
            f"Requirement committed scope authority is invalid: {exc}",
            f"Rerun requirement freeze for {safe_loop_id}.",
            {},
        )
    return (
        "",
        "",
        {
            "authorized_scope_families": list(intake.design_scope_families),
            "scope_authority_ref": repo_relative_path(root, artifacts.intake_path),
            "scope_authority_digest": freeze.intake_digest,
        },
    )


def _load_requirement_freeze(
    root: Path,
    loop_id: str,
    artifacts: _RequirementArtifacts,
) -> tuple[RequirementFreeze | None, str, str]:
    freeze_next_action = (
        f"Run ai-sdlc loop requirement freeze --loop-id {loop_id} --yes."
    )
    try:
        loop_run = _read_requirement_loop_run(artifacts.loop_run_path)
    except ValueError as exc:
        return (
            None,
            f"Requirement loop {loop_id} must exist and be frozen before design-contract check: {exc}",
            "Run ai-sdlc loop requirement start.",
        )
    if loop_run.loop_id != loop_id:
        return (
            None,
            f"Requirement loop id mismatch: expected {loop_id}, found {loop_run.loop_id}.",
            "Run ai-sdlc loop requirement status.",
        )
    if loop_run.status != LoopStatus.CLOSED or not artifacts.freeze_path.is_file():
        return (
            None,
            f"Requirement loop {loop_id} must be frozen before design-contract check.",
            freeze_next_action,
        )
    try:
        freeze_payload = LoopArtifactStore(root).read_json_artifact(
            artifacts.freeze_path
        )
        freeze = RequirementFreeze.model_validate(freeze_payload)
    except (OSError, ValueError, ValidationError) as exc:
        return (
            None,
            f"Requirement freeze artifact for {loop_id} is malformed: {exc}",
            freeze_next_action,
        )
    blocker, next_action = _requirement_freeze_identity_issue(
        root,
        loop_id,
        artifacts,
        freeze,
    )
    if blocker:
        return None, blocker, next_action
    return freeze, "", ""


def _requirement_freeze_identity_issue(
    root: Path,
    loop_id: str,
    artifacts: _RequirementArtifacts,
    freeze: RequirementFreeze,
) -> tuple[str, str]:
    if freeze.loop_id != loop_id:
        return (
            f"Requirement freeze artifact id mismatch: expected {loop_id}, found {freeze.loop_id}.",
            f"Run ai-sdlc loop requirement freeze --loop-id {loop_id} --yes.",
        )
    expected_intake_path = repo_relative_path(root, artifacts.intake_path)
    if freeze.intake_path != expected_intake_path:
        return (
            f"Requirement freeze artifact for {loop_id} references another intake.",
            "Start and freeze a new requirement loop.",
        )
    return "", ""


def _load_requirement_intake(
    root: Path,
    loop_id: str,
    artifacts: _RequirementArtifacts,
    freeze: RequirementFreeze,
) -> tuple[RequirementIntake | None, str, str]:
    try:
        intake_payload = LoopArtifactStore(root).read_json_artifact(
            artifacts.intake_path
        )
        intake = RequirementIntake.model_validate(intake_payload)
    except (OSError, ValueError, ValidationError) as exc:
        return (
            None,
            f"Requirement intake artifact for {loop_id} is malformed: {exc}",
            "Run ai-sdlc loop requirement status.",
        )
    if intake.loop_id != loop_id:
        return (
            None,
            f"Requirement intake artifact id mismatch: expected {loop_id}, found {intake.loop_id}.",
            "Start and freeze a new requirement loop.",
        )
    if freeze.intake_digest:
        if freeze.intake_digest != _requirement_intake_digest(intake):
            return (
                None,
                f"Requirement intake artifact for {loop_id} changed after freeze.",
                "Start and freeze a new requirement loop.",
            )
    elif intake.design_scope_families:
        return (
            None,
            f"Requirement scope authority for {loop_id} is not digest-bound.",
            "Start and freeze a new requirement loop.",
        )
    return intake, "", ""


def _requirement_authority_issue(
    loop_id: str,
    intake: RequirementIntake,
    work_item_id: str,
) -> tuple[str, str]:
    work_item = work_item_id.strip()
    if work_item:
        intake_work_item = intake.work_item_id.strip()
        if intake_work_item and intake_work_item != work_item:
            return (
                (
                    f"Requirement loop {loop_id} belongs to work item "
                    f"{intake_work_item}, but design-contract work item is {work_item}."
                ),
                (
                    "Run ai-sdlc loop requirement start "
                    f'--work-item-id {work_item} --acceptance "<验收标准>".'
                ),
            )
    return "", ""


def _required_requirement_loop_id(
    root: Path,
    requirement_loop_id: str,
) -> tuple[str, str, str]:
    loop_id = requirement_loop_id.strip()
    if loop_id:
        return loop_id, "", ""
    loop_run_path, expected_loop_id, pointer_blocker = (
        _resolve_requirement_loop_run_path(root, "")
    )
    if pointer_blocker:
        return (
            "",
            (
                "A frozen current requirement loop is required before "
                f"design-contract check: {pointer_blocker}"
            ),
            "Run ai-sdlc loop requirement start.",
        )
    try:
        loop_run = _read_requirement_loop_run(loop_run_path)
    except ValueError as exc:
        return (
            "",
            (
                "Current requirement loop must exist and be frozen before "
                f"design-contract check: {exc}"
            ),
            "Run ai-sdlc loop requirement status.",
        )
    identity_issue = _requirement_loop_identity_issue(
        root,
        loop_run_path,
        expected_loop_id,
        loop_run,
    )
    if identity_issue:
        return (
            "",
            f"Current requirement loop identity is invalid: {identity_issue}",
            "Run ai-sdlc loop requirement status.",
        )
    return expected_loop_id, "", ""


def _build_loop_run(
    *,
    contract_input: DesignContractInput,
    report: DesignContractReport,
    loop_status: LoopStatus,
    artifacts: DesignContractArtifacts,
    root: Path,
) -> LoopRun:
    output_artifacts = [
        repo_relative_path(root, artifacts.input_path),
        repo_relative_path(root, artifacts.coverage_matrix_path),
        repo_relative_path(root, artifacts.report_json_path),
        repo_relative_path(root, artifacts.report_md_path),
    ]
    return LoopRun(
        loop_id=contract_input.loop_id,
        loop_type=LoopType.DESIGN_CONTRACT,
        status=loop_status,
        work_item_id=contract_input.work_item_id,
        input_digest=_design_scope_input_digest(contract_input),
        current_round=1,
        rounds=[
            LoopRound(
                round_number=1,
                input_artifacts=[
                    contract_input.spec_path,
                    contract_input.plan_path,
                    contract_input.tasks_path,
                ],
                output_artifacts=output_artifacts,
                command=["ai-sdlc", "loop", "design-contract", "check"],
                status=loop_status,
                result=report.status,
                next_action=report.next_action,
            )
        ],
        next_action=report.next_action,
    )


def _result_from_report(
    report: DesignContractReport,
    *,
    artifacts: list[DesignContractArtifactRef],
    result: str,
    closed: bool = False,
    loop_status: LoopStatus | str = "",
    next_action: str = "",
) -> DesignContractCommandResult:
    resolved_next_action = next_action or report.next_action
    resolved_loop_status = loop_status or report.status
    return DesignContractCommandResult(
        status=(
            DesignContractCommandStatus.READY
            if not report.blocker_count
            else DesignContractCommandStatus.NEEDS_FIX
        ),
        result=result,
        loop_id=report.loop_id,
        loop_status=resolved_loop_status,
        work_item_id=report.work_item_id,
        work_item_path=report.work_item_path,
        blocker_count=report.blocker_count,
        warning_count=report.warning_count,
        coverage_count=report.coverage_count,
        closed=closed,
        next_action=resolved_next_action,
        next_guidance=_next_guidance_for_result(
            report,
            next_action=resolved_next_action,
            closed=closed,
            artifacts=artifacts,
        ),
        artifacts=artifacts,
        design_contract=_command_summary_for_report(
            report,
            artifacts=artifacts,
            status=resolved_loop_status,
            closed=closed,
        ),
    )


def _blocked_result(
    blocker: str,
    *,
    result: str = "Design-contract loop is blocked.",
    loop_id: str = "",
    next_action: str = "Run ai-sdlc loop design-contract check --wi specs/<work-item>.",
    artifacts: list[DesignContractArtifactRef] | None = None,
) -> DesignContractCommandResult:
    return DesignContractCommandResult(
        status=DesignContractCommandStatus.BLOCKED,
        result=result,
        loop_id=loop_id,
        loop_status=LoopStatus.BLOCKED,
        blocker=blocker,
        next_action=next_action,
        next_guidance=DesignContractNextGuidance(
            command="",
            reason=blocker,
            requires_model=False,
            writes_artifacts=False,
            writes_code=False,
            safety="blocked",
        ),
        artifacts=artifacts or [],
    )


def _command_summary(
    contract_input: DesignContractInput,
    *,
    status: LoopStatus | str,
    artifacts: list[DesignContractArtifactRef],
) -> DesignContractCommandSummary:
    return DesignContractCommandSummary(
        status=_status_value(status),
        work_item_id=contract_input.work_item_id,
        work_item_path=contract_input.work_item_path,
        coverage_matrix_path=_artifact_path(artifacts, "coverage-matrix"),
        report_path=_artifact_path(artifacts, "design-contract-report-json"),
    )


def _command_summary_for_report(
    report: DesignContractReport,
    *,
    artifacts: list[DesignContractArtifactRef],
    status: LoopStatus | str,
    closed: bool,
) -> DesignContractCommandSummary:
    return DesignContractCommandSummary(
        status=_status_value(status),
        work_item_id=report.work_item_id,
        work_item_path=report.work_item_path,
        blocker_count=report.blocker_count,
        warning_count=report.warning_count,
        coverage_count=report.coverage_count,
        coverage_matrix_path=_artifact_path(artifacts, "coverage-matrix"),
        report_path=_artifact_path(artifacts, "design-contract-report-json"),
        closed=closed,
    )


def _artifact_path(
    artifacts: list[DesignContractArtifactRef],
    kind: str,
) -> str:
    return next((artifact.path for artifact in artifacts if artifact.kind == kind), "")


def _status_value(status: LoopStatus | str) -> str:
    return status.value if isinstance(status, LoopStatus) else str(status)


def _next_action_for_report(report: DesignContractReport) -> str:
    if report.blocker_count:
        return (
            "Fix design-contract blockers, then run "
            f"ai-sdlc loop design-contract check --wi {report.work_item_path}."
        )
    return "Run ai-sdlc loop design-contract close --yes."


def _next_guidance_for_result(
    report: DesignContractReport,
    *,
    next_action: str,
    closed: bool,
    artifacts: list[DesignContractArtifactRef],
) -> DesignContractNextGuidance:
    evidence = [artifact.path for artifact in artifacts if artifact.path]
    if closed:
        return DesignContractNextGuidance(
            command="",
            reason="The design contract is closed; the next loop type is implementation.",
            requires_model=False,
            writes_artifacts=False,
            writes_code=False,
            safety="no_action",
            evidence=evidence,
            alternatives=[next_action],
        )
    if report.blocker_count:
        return DesignContractNextGuidance(
            command=f"ai-sdlc loop design-contract check --wi {report.work_item_path}",
            reason="The design contract has blockers; fix the formal docs and rerun the deterministic check.",
            requires_model=False,
            writes_artifacts=True,
            writes_code=False,
            safety="writes_project_artifacts",
            evidence=evidence,
        )
    return DesignContractNextGuidance(
        command="ai-sdlc loop design-contract close --yes",
        reason="The design contract passed; close it before implementation.",
        requires_model=False,
        writes_artifacts=True,
        writes_code=False,
        safety="writes_project_artifacts",
        evidence=evidence,
    )


def _implementation_next_action(work_item_id: str) -> str:
    return f"Start implementation loop for {work_item_id}."


__all__ = [
    "CURRENT_DESIGN_CONTRACT_PATH",
    "ContractCoverageItem",
    "DesignContractCheckOptions",
    "DesignContractClose",
    "DesignContractCloseOptions",
    "DesignContractCommandResult",
    "DesignContractCommandStatus",
    "DesignContractInput",
    "DesignContractReport",
    "check_design_contract_loop",
    "close_design_contract_loop",
]
