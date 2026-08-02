from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from ai_sdlc.core.stage_review.optimization import (
    candidate_policy as candidate_policy_module,
)
from ai_sdlc.core.stage_review.optimization import controller as controller_module
from ai_sdlc.core.stage_review.optimization import evaluators as evaluators_module
from ai_sdlc.core.stage_review.optimization import (
    local_evaluation as local_evaluation_module,
)
from ai_sdlc.core.stage_review.optimization import (
    local_promotion as local_promotion_module,
)
from ai_sdlc.core.stage_review.optimization import (
    local_shadow as local_shadow_module,
)
from ai_sdlc.core.stage_review.optimization import (
    product_pipeline as product_pipeline_module,
)
from ai_sdlc.core.stage_review.optimization import (
    product_shadow_executor as product_shadow_executor_module,
)
from ai_sdlc.core.stage_review.optimization import (
    runtime_dataset as runtime_dataset_module,
)
from ai_sdlc.core.stage_review.optimization import (
    snapshot_retry as snapshot_retry_module,
)
from ai_sdlc.core.stage_review.optimization.defaults import (
    baseline_epoch_budget_policy,
)
from ai_sdlc.core.stage_review.optimization.evaluators import (
    component_runtime_digest,
    invalidate_optimization_runtime_identity,
)
from ai_sdlc.core.stage_review.optimization.pipeline import (
    OptimizationPipelineExecutor,
)
from ai_sdlc.core.stage_review.optimization.product_pipeline import (
    ProductOptimizationRuntimeFactory,
)
from ai_sdlc.core.stage_review.optimization.product_shadow_executor import (
    ProductShadowAssignmentExecutor,
)
from ai_sdlc.core.stage_review.optimization.runtime import (
    _run_bounded_optimization_maintenance as run_bounded_optimization_maintenance,
)
from ai_sdlc.core.stage_review.optimization.runtime import (
    build_optimization_runtime,
)


def test_runtime_bootstraps_one_deterministic_baseline(tmp_path: Path) -> None:
    first = build_optimization_runtime(
        tmp_path,
        clock=lambda: "2026-07-22T12:00:00Z",
    )
    second = build_optimization_runtime(
        tmp_path,
        clock=lambda: "2026-07-22T13:00:00Z",
    )

    assert first.project_id == second.project_id
    assert (
        first.snapshots.resolve_snapshot().active_snapshot_digest
        == second.snapshots.resolve_snapshot().active_snapshot_digest
    )


def test_product_runtime_factory_rejects_inferred_component_identity(
    tmp_path: Path,
) -> None:
    runtime = build_optimization_runtime(tmp_path)
    pipeline = runtime.controller.step_executor
    bundle = next(iter(pipeline.runtime_bundles.values()))
    factory = ProductOptimizationRuntimeFactory(
        constitution_factory=lambda: bundle.constitution,
        budget_policy_factory=baseline_epoch_budget_policy,
        bundle_builder=lambda _context, _constitution: replace(
            bundle,
            dataset_port=object(),
        ),
    )

    with pytest.raises(
        ValueError,
        match="product optimization runtime identity is not explicit",
    ):
        factory.build(None)  # type: ignore[arg-type]


def test_product_runtime_manifest_binds_live_module_helpers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = build_optimization_runtime(tmp_path)
    bundle = next(
        iter(runtime.controller.step_executor.runtime_bundles.values())
    )
    original = bundle.manifest_digest
    targets = (
        (local_evaluation_module, "_build_partition_report"),
        (local_promotion_module, "_promotion_evidence"),
        (candidate_policy_module, "_verify_lineage"),
        (runtime_dataset_module, "freeze_optimization_dataset"),
        (local_shadow_module, "_sample_plan"),
        (product_pipeline_module, "commit_effect"),
    )

    for module, name in targets:
        with monkeypatch.context() as scoped:
            scoped.setattr(module, name, lambda *args, **kwargs: None)
            assert bundle.manifest_digest != original, (
                f"{module.__name__}.{name} is absent from the runtime manifest"
            )
        assert bundle.manifest_digest == original
    shadow_executor = object.__new__(ProductShadowAssignmentExecutor)
    shadow_executor.project_id = "project.shared"
    shadow_executor.transport_source = lambda _: None
    shadow_executor.clock = lambda: "2026-07-25T00:00:00Z"
    shadow_original = component_runtime_digest(shadow_executor)
    with monkeypatch.context() as scoped:
        scoped.setattr(
            product_shadow_executor_module,
            "build_shadow_provider_payload",
            lambda *args, **kwargs: None,
        )
        assert component_runtime_digest(shadow_executor) != shadow_original
    assert component_runtime_digest(shadow_executor) == shadow_original


def test_runtime_manifest_hot_path_revalidates_live_binding_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = build_optimization_runtime(tmp_path)
    bundle = next(
        iter(runtime.controller.step_executor.runtime_bundles.values())
    )
    original = evaluators_module._live_package_fast_token

    class TokenSpy:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self) -> object:
            self.calls += 1
            return original()

    spy = TokenSpy()
    monkeypatch.setattr(
        evaluators_module,
        "_live_package_fast_token",
        spy,
    )

    assert bundle.manifest_digest
    spy.calls = 0
    assert bundle.manifest_digest
    assert spy.calls == 2


def test_runtime_manifest_reuses_dependency_scope_within_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = build_optimization_runtime(tmp_path)
    bundle = next(
        iter(runtime.controller.step_executor.runtime_bundles.values())
    )
    original = evaluators_module._optimization_seed_binding_token

    class SeedSpy:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self) -> object:
            self.calls += 1
            return original()

    spy = SeedSpy()
    monkeypatch.setattr(
        evaluators_module,
        "_optimization_seed_binding_token",
        spy,
    )

    assert bundle.manifest_digest
    assert spy.calls <= 8


def test_runtime_manifest_drift_token_does_not_rebuild_class_schemas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(_implementation: type[object]) -> object:
        raise AssertionError("hot drift token rebuilt a full class schema")

    monkeypatch.setattr(
        evaluators_module,
        "_stable_class_configuration",
        fail_if_called,
    )

    assert evaluators_module._live_package_fast_token()


def test_runtime_manifest_detects_replaced_stable_policy_constant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = evaluators_module._optimization_live_package_digest()

    monkeypatch.setattr(
        controller_module,
        "_CRITICAL_FACTS",
        frozenset((*controller_module._CRITICAL_FACTS, "missed_critical")),
    )

    assert evaluators_module._optimization_live_package_digest() != original


def test_runtime_manifest_detects_class_member_default_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = evaluators_module._optimization_live_package_digest()
    initializer = snapshot_retry_module.SnapshotControlRetryPolicy.__init__
    monkeypatch.setattr(initializer, "__defaults__", (7, 1.5))

    changed = snapshot_retry_module.SnapshotControlRetryPolicy()

    assert (changed.maximum_attempts, changed.maximum_active_seconds) == (7, 1.5)
    assert evaluators_module._optimization_live_package_digest() != original


def test_product_shadow_runtime_manifest_binds_transport_instance_configuration() -> None:
    class StatefulTransportSource:
        def __init__(self, provider: str) -> None:
            self.provider = provider

        def __call__(self, _assignment: object) -> None:
            return None

    source = StatefulTransportSource("provider-a")
    shadow_executor = object.__new__(ProductShadowAssignmentExecutor)
    shadow_executor.project_id = "project.shared"
    shadow_executor.transport_source = source
    shadow_executor.clock = lambda: "2026-07-25T00:00:00Z"
    original = component_runtime_digest(shadow_executor)

    source.provider = "provider-b"

    assert component_runtime_digest(shadow_executor) != original


def test_runtime_manifest_is_stable_across_clean_import_histories(
    tmp_path: Path,
) -> None:
    script = """
import sys
from pathlib import Path
if sys.argv[2] == "extra":
    import ai_sdlc.core.stage_review.optimization.foreground
from ai_sdlc.core.stage_review.optimization.runtime import build_optimization_runtime
runtime = build_optimization_runtime(Path(sys.argv[1]))
bundle = next(iter(runtime.controller.step_executor.runtime_bundles.values()))
print(bundle.manifest_digest)
"""

    def manifest(mode: str) -> str:
        completed = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path / "shared"), mode],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return completed.stdout.strip().splitlines()[-1]

    assert manifest("ordinary") == manifest("extra")


def test_product_runtime_manifest_binds_live_pipeline_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = build_optimization_runtime(tmp_path)
    bundle = next(
        iter(runtime.controller.step_executor.runtime_bundles.values())
    )
    original = bundle.manifest_digest

    monkeypatch.setattr(
        OptimizationPipelineExecutor,
        "_snapshot",
        lambda *args, **kwargs: None,
    )
    invalidate_optimization_runtime_identity()

    assert bundle.manifest_digest != original


def test_maintenance_without_session_threshold_is_non_blocking(tmp_path: Path) -> None:
    result = run_bounded_optimization_maintenance(tmp_path)

    assert result.result_code == "not_ready"
    assert result.epoch is None


def test_phase_one_never_bootstraps_offline_optimization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(_root: Path) -> object:
        raise AssertionError("phase one must not build optimization runtime")

    monkeypatch.setattr(
        "ai_sdlc.core.stage_review.optimization.runtime.build_optimization_runtime",
        forbidden,
    )

    result = run_bounded_optimization_maintenance(tmp_path)

    assert result.result_code == "not_ready"
    assert not (tmp_path / ".ai-sdlc").exists()


def test_idle_maintenance_compacts_existing_snapshot_control_storage(
    tmp_path: Path,
) -> None:
    runtime = build_optimization_runtime(tmp_path)
    storage = runtime.snapshots.store.storage
    for index in range(64):
        with storage.commit_leases.acquire(
            owner_id=f"writer.runtime-{index}",
            scope="snapshot_control",
            expected_head=f"head.{index}",
        ) as lease:
            storage.append(
                "snapshot-control",
                {"index": index, "padding": "x" * 200},
                keys={"operation_id": f"operation.runtime-{index}"},
                lease=lease,
            )
    assert storage.manifest().revision == 0

    result = run_bounded_optimization_maintenance(tmp_path)

    assert result.result_code == "not_ready"
    assert storage.manifest().revision == 1
    assert not tuple(storage.loose_root.rglob("*.json"))


def test_idle_maintenance_recovers_loose_cleanup_after_manifest_crash(
    tmp_path: Path,
) -> None:
    runtime = build_optimization_runtime(tmp_path)
    storage = runtime.snapshots.store.storage
    for index in range(64):
        with storage.commit_leases.acquire(
            owner_id=f"writer.recovery-{index}",
            scope="snapshot_control",
            expected_head=f"head.{index}",
        ) as lease:
            storage.append(
                "snapshot-control",
                {"index": index, "padding": "x" * 200},
                keys={"operation_id": f"operation.recovery-{index}"},
                lease=lease,
            )
    prepared = storage._prepare_compaction("snapshot-control")
    assert prepared is not None
    with runtime.snapshots.resources.storage_bundle(
        bundle_class="reclamation",
        bundle_bytes=prepared.required_bundle_bytes,
        net_reclaim_bytes=prepared.net_reclaim_bytes,
        policy=storage.policy,
        operation_id="bundle.runtime-crash",
    ) as bundle, storage.acquire_planned_lease(
        prepared.lease_plan,
        write_class="reclamation",
        bundle_bytes=prepared.required_bundle_bytes,
        net_reclaim_bytes=prepared.net_reclaim_bytes,
        resource_bundle=bundle,
    ) as lease, pytest.raises(RuntimeError, match="after manifest"):
        storage._commit_compaction(
            prepared,
            lease=lease,
            resource_bundle=bundle,
            crash_point="after_manifest",
        )
    assert tuple(storage.loose_root.rglob("*.json"))

    result = run_bounded_optimization_maintenance(tmp_path)

    assert result.result_code == "not_ready"
    assert not tuple(storage.loose_root.rglob("*.json"))
