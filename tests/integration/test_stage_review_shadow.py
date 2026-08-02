from __future__ import annotations

from pathlib import Path

from ai_sdlc.core.design_contract_loop import (
    DesignContractCheckOptions,
    DesignContractCloseOptions,
    check_design_contract_loop,
    close_design_contract_loop,
)
from ai_sdlc.core.frontend_evidence_loop import (
    FrontendEvidenceCloseOptions,
    FrontendEvidenceSkipOptions,
    FrontendEvidenceStartOptions,
    close_frontend_evidence_loop,
    skip_frontend_evidence_loop,
    start_frontend_evidence_loop,
)
from ai_sdlc.core.implementation_loop import (
    ImplementationCloseOptions,
    ImplementationRecordOptions,
    ImplementationStartOptions,
    close_implementation_loop,
    record_implementation_progress,
    start_implementation_loop,
)
from ai_sdlc.core.pr_review_provider import MockReviewerFixture
from ai_sdlc.core.pr_review_service import (
    PRReviewStartOptions,
    attest_pr_review,
    close_pr_review,
    start_pr_review,
)
from ai_sdlc.core.requirement_loop import (
    RequirementFreezeOptions,
    RequirementStartOptions,
    freeze_requirement_loop,
    start_requirement_loop,
)
from ai_sdlc.core.stage_review.close_gate import (
    _read_stage_close_gate_attestations as read_stage_close_gate_attestations,
)
from tests.unit.test_design_contract_loop import _write_work_item as _write_design_item
from tests.unit.test_frontend_evidence_loop import (
    _write_browser_gate_artifact,
    _write_closed_implementation_loop,
)
from tests.unit.test_frontend_evidence_loop import (
    _write_work_item as _write_frontend_item,
)
from tests.unit.test_implementation_loop import (
    _close_design_contract_for_work_item,
    _write_ready_work_item,
)
from tests.unit.test_pr_review_service import _commit_file, _init_repo


def test_five_formal_close_paths_record_unified_shadow_attestations(
    tmp_path: Path,
) -> None:
    roots = {
        "requirement": tmp_path / "requirement",
        "design-contract": tmp_path / "design",
        "implementation": tmp_path / "implementation",
        "frontend-evidence": tmp_path / "frontend",
        "local-pr-review": tmp_path / "pr-review",
    }
    for root in roots.values():
        root.mkdir()

    assert _close_requirement(roots["requirement"]) == "ready"
    assert _close_design(roots["design-contract"]) == "ready"
    assert _close_implementation(roots["implementation"]) == "ready"
    assert _close_frontend(roots["frontend-evidence"]) == "ready"
    assert _close_local_pr(roots["local-pr-review"]) == "closed"
    assert attest_pr_review(roots["local-pr-review"]).status == "ready"

    for stage_key, root in roots.items():
        matching = [
            item
            for item in read_stage_close_gate_attestations(root)
            if item.stage_key == stage_key
        ]
        assert matching, f"missing Shadow attestation for {stage_key}"
        assert all(item.applicability.mode == "shadow" for item in matching)
        assert all(item.certificate_required is False for item in matching)
        assert all(item.gate_id == "stage-close-authorizer" for item in matching)
        assert all(
            item.applicability.policy_id == "bundled.stage-gate-activation"
            for item in matching
        )
        assert all(item.candidate.status == "not_materialized" for item in matching)

    pr_close_kinds = {
        item.close_kind
        for item in read_stage_close_gate_attestations(roots["local-pr-review"])
    }
    assert pr_close_kinds == {"local-pr-review-attest", "local-pr-review-close"}


def test_frontend_skip_uses_the_same_shadow_gateway(tmp_path: Path) -> None:
    work_item = _write_frontend_item(tmp_path)
    _write_closed_implementation_loop(tmp_path, work_item)

    result = skip_frontend_evidence_loop(
        FrontendEvidenceSkipOptions(
            root=tmp_path,
            work_item="specs/demo-frontend",
            loop_id="frontend-shadow-skip",
            reason="本地环境当前没有可用的浏览器控制提供方。",
            yes=True,
        )
    )

    assert result.status == "ready"
    assert result.skipped is True
    attestations = read_stage_close_gate_attestations(tmp_path)
    assert [item.close_kind for item in attestations] == ["frontend-evidence-skip"]


def test_already_closed_replay_does_not_fork_the_attestation(
    tmp_path: Path,
) -> None:
    assert _close_requirement(tmp_path) == "ready"
    before = read_stage_close_gate_attestations(tmp_path)

    repeated = freeze_requirement_loop(
        RequirementFreezeOptions(root=tmp_path, loop_id="req-shadow", yes=True)
    )
    after = read_stage_close_gate_attestations(tmp_path)

    assert repeated.result == "Requirement loop is already frozen."
    assert len(before) == len(after) == 1
    assert before[0].attestation_digest == after[0].attestation_digest


def test_all_seven_close_entries_replay_with_current_attestations(
    tmp_path: Path,
) -> None:
    roots = _build_closed_roots(tmp_path)
    before = {
        name: read_stage_close_gate_attestations(root) for name, root in roots.items()
    }

    for name, replay in _loop_replays(roots):
        replay()
        assert read_stage_close_gate_attestations(roots[name]) == before[name]

    pr_root = roots["pr-review"]
    old_close = next(
        item
        for item in before["pr-review"]
        if item.close_kind == "local-pr-review-close"
    )
    old_attest = next(
        item
        for item in before["pr-review"]
        if item.close_kind == "local-pr-review-attest"
    )
    close_pr_review(pr_root, verification_evidence=["rerun-evidence"])
    after_close = read_stage_close_gate_attestations(pr_root)
    replacement = next(
        item
        for item in after_close
        if item.supersedes_attestation_id == old_close.attestation_id
    )
    assert replacement.close_artifact_digest != old_close.close_artifact_digest

    attest_pr_review(pr_root)
    after_attest = read_stage_close_gate_attestations(pr_root)
    assert any(
        item.supersedes_attestation_id == old_attest.attestation_id
        for item in after_attest
    )


def test_rejected_local_pr_close_does_not_create_success_attestation(
    tmp_path: Path,
) -> None:
    base_commit = _init_repo(tmp_path)
    _commit_file(tmp_path, "src/app.py", "print('needs fix')\n", "add app")
    start_pr_review(
        PRReviewStartOptions(
            root=tmp_path,
            base_ref=base_commit,
            provider_id="mock-reviewer",
            review_id="review-shadow-rejected",
            mock_fixture=MockReviewerFixture.CHANGES_REQUIRED,
        )
    )

    result = close_pr_review(tmp_path)

    assert result.status == "blocked"
    assert read_stage_close_gate_attestations(tmp_path) == ()


def test_local_pr_supersession_chain_is_linear_across_new_evidence(
    tmp_path: Path,
) -> None:
    _close_local_pr(tmp_path)
    first = read_stage_close_gate_attestations(tmp_path)[0]

    close_pr_review(tmp_path, verification_evidence=["evidence-b"])
    second = next(
        item
        for item in read_stage_close_gate_attestations(tmp_path)
        if item.supersedes_attestation_id == first.attestation_id
    )

    close_pr_review(tmp_path, verification_evidence=["evidence-c"])
    third = next(
        item
        for item in read_stage_close_gate_attestations(tmp_path)
        if item.supersedes_attestation_id == second.attestation_id
    )

    assert first.attestation_id != second.attestation_id != third.attestation_id


def _build_closed_roots(tmp_path: Path) -> dict[str, Path]:
    names = (
        "requirement",
        "design",
        "implementation",
        "frontend",
        "frontend-skip",
        "pr-review",
    )
    roots = {name: tmp_path / name for name in names}
    for root in roots.values():
        root.mkdir()
    _close_requirement(roots["requirement"])
    _close_design(roots["design"])
    _close_implementation(roots["implementation"])
    _close_frontend(roots["frontend"])
    _close_frontend_skip(roots["frontend-skip"])
    _close_local_pr(roots["pr-review"])
    attest_pr_review(roots["pr-review"])
    return roots


def _loop_replays(roots: dict[str, Path]):
    return (
        ("requirement", lambda: _replay_requirement(roots["requirement"])),
        ("design", lambda: _replay_design(roots["design"])),
        (
            "implementation",
            lambda: _replay_implementation(roots["implementation"]),
        ),
        ("frontend", lambda: _replay_frontend(roots["frontend"])),
        ("frontend-skip", lambda: _replay_frontend_skip(roots["frontend-skip"])),
    )


def _replay_requirement(root: Path) -> None:
    freeze_requirement_loop(
        RequirementFreezeOptions(root=root, loop_id="req-shadow", yes=True)
    )


def _replay_design(root: Path) -> None:
    close_design_contract_loop(
        DesignContractCloseOptions(root=root, loop_id="dc-shadow", yes=True)
    )


def _replay_implementation(root: Path) -> None:
    close_implementation_loop(
        ImplementationCloseOptions(root=root, loop_id="impl-shadow", yes=True)
    )


def _replay_frontend(root: Path) -> None:
    close_frontend_evidence_loop(
        FrontendEvidenceCloseOptions(root=root, loop_id="frontend-shadow", yes=True)
    )


def _replay_frontend_skip(root: Path) -> None:
    skip_frontend_evidence_loop(
        FrontendEvidenceSkipOptions(
            root=root,
            loop_id="frontend-shadow-skip",
            reason="本地环境当前没有可用的浏览器控制提供方。",
            yes=True,
        )
    )


def _close_requirement(root: Path) -> str:
    start_requirement_loop(
        RequirementStartOptions(
            root=root,
            loop_id="req-shadow",
            idea="需要统一记录需求关闭的 Shadow 门禁证明。",
            acceptance=("需求可以冻结",),
            work_item_id="shadow-requirement",
        )
    )
    result = freeze_requirement_loop(RequirementFreezeOptions(root=root, yes=True))
    return str(result.status)


def _close_design(root: Path) -> str:
    _write_design_item(root)
    check_design_contract_loop(
        DesignContractCheckOptions(
            root=root,
            work_item="specs/demo-contract",
            loop_id="dc-shadow",
        )
    )
    result = close_design_contract_loop(
        DesignContractCloseOptions(root=root, loop_id="dc-shadow", yes=True)
    )
    return str(result.status)


def _close_implementation(root: Path) -> str:
    work_item = _write_ready_work_item(root)
    _close_design_contract_for_work_item(root, work_item)
    start_implementation_loop(
        ImplementationStartOptions(
            root=root,
            work_item="specs/demo-implementation-loop",
            loop_id="impl-shadow",
        )
    )
    record_implementation_progress(
        ImplementationRecordOptions(
            root=root,
            loop_id="impl-shadow",
            task_id="T11",
            status="done",
            verification=("uv run pytest tests/unit/test_implementation_loop.py -q",),
        )
    )
    result = close_implementation_loop(
        ImplementationCloseOptions(root=root, loop_id="impl-shadow", yes=True)
    )
    return str(result.status)


def _close_frontend(root: Path) -> str:
    work_item = _write_frontend_item(root)
    _write_closed_implementation_loop(root, work_item)
    _write_browser_gate_artifact(root, work_item_path="specs/demo-frontend")
    start_frontend_evidence_loop(
        FrontendEvidenceStartOptions(
            root=root,
            work_item="specs/demo-frontend",
            loop_id="frontend-shadow",
        )
    )
    result = close_frontend_evidence_loop(
        FrontendEvidenceCloseOptions(root=root, loop_id="frontend-shadow", yes=True)
    )
    return str(result.status)


def _close_frontend_skip(root: Path) -> str:
    work_item = _write_frontend_item(root)
    _write_closed_implementation_loop(root, work_item)
    result = skip_frontend_evidence_loop(
        FrontendEvidenceSkipOptions(
            root=root,
            work_item="specs/demo-frontend",
            loop_id="frontend-shadow-skip",
            reason="本地环境当前没有可用的浏览器控制提供方。",
            yes=True,
        )
    )
    return str(result.status)


def _close_local_pr(root: Path) -> str:
    base_commit = _init_repo(root)
    _commit_file(root, "src/app.py", "print('shadow')\n", "add app")
    start_pr_review(
        PRReviewStartOptions(
            root=root,
            base_ref=base_commit,
            provider_id="mock-reviewer",
            review_id="review-shadow",
            mock_fixture=MockReviewerFixture.CLEAN,
        )
    )
    return str(close_pr_review(root).status)
