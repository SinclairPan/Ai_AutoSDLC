"""Implementation start 的 WorkType、scope 与 Lean capability marker 测试。"""

from __future__ import annotations

from pathlib import Path

from ai_sdlc.core.implementation_models import ImplementationTaskItem
from ai_sdlc.core.implementation_store import build_implementation_input
from ai_sdlc.core.state_machine import save_work_item
from ai_sdlc.models.work import WorkItem, WorkType


def test_persisted_feature_work_type_enables_lean_profile(tmp_path: Path) -> None:
    work_item_dir = tmp_path / "specs" / "WI-FEATURE"
    work_item_dir.mkdir(parents=True)
    save_work_item(
        tmp_path,
        WorkItem(
            work_item_id="WI-FEATURE",
            work_type=WorkType.NEW_REQUIREMENT,
            title="Feature",
        ),
    )
    tasks = [
        ImplementationTaskItem(
            task_id="T11",
            required=True,
            files=["src/app.py", "tests/test_app.py"],
            acceptance=["AC-1"],
        )
    ]

    model = build_implementation_input(
        root=tmp_path,
        loop_id="impl-feature",
        work_item_dir=work_item_dir,
        design_contract_loop_id="design-feature",
        design_contract_report_path="design/report.json",
        task_items=tasks,
    )

    assert model.work_type == WorkType.NEW_REQUIREMENT
    assert model.quality_profiles == ["lean-code"]
    assert model.declared_scope == ["src/app.py", "tests/test_app.py"]
    assert model.tasks_digest.startswith("sha256:")
    assert model.acceptance_digest.startswith("sha256:")


def test_missing_work_item_artifact_keeps_legacy_profile_disabled(
    tmp_path: Path,
) -> None:
    work_item_dir = tmp_path / "specs" / "WI-LEGACY"
    work_item_dir.mkdir(parents=True)

    model = build_implementation_input(
        root=tmp_path,
        loop_id="impl-legacy",
        work_item_dir=work_item_dir,
        design_contract_loop_id="design-legacy",
        design_contract_report_path="",
        task_items=[],
    )

    assert model.work_type == WorkType.UNCERTAIN
    assert model.quality_profiles == []
