from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path

import pytest

BENCHMARK_ROOT = Path("benchmarks/ai-sdlc-product-value")
DATA_ROOT = Path("deliverables/ai-sdlc-2.0-offline-product-site/assets/data")


def _read_json(relative_path: str) -> dict[str, object]:
    return json.loads((BENCHMARK_ROOT / relative_path).read_text(encoding="utf-8"))


def _builder_module():
    return importlib.import_module("scripts.build_product_site_benefit_data")


V3_RELEASE_BASELINE = {
    "schema_version": "ai-sdlc-product-release-baseline/v1",
    "version": "3.0.1",
    "tag": "v3.0.1",
    "tag_object": "408086505718fbd26824373bb72ed98c27c3b652",
    "commit": "9a59a3edd483b0e6526b67b03fbfcac3ba48d2e4",
    "tree": "fd5c2dac0a216f0eb17855d03cc7900d872d3c61",
    "runtime_equivalent_to": "v3.0.0",
    "evidence_blobs": {
        "README.md": "87b0b7c1f60b0f28a53fda8f3426ad2f8270ed0b",
        "USER_GUIDE.zh-CN.md": "52b1fbb8399ef55674cb54953e9c5411d2b83b61",
        "docs/product-contract.md": "2cc25f28b5903a2223bec93c5b7a192f05caca30",
        "rules/pipeline.md": "27543fe347d802b81b724f8957a2c61f9f110b37",
        "rules/quality-gate.md": "501f3466a9285dc1c0f474946a10103dd9bc6edc",
    },
}


def test_benchmark_contract_freezes_current_comparison_identity() -> None:
    contract = _read_json("benchmark-contract.json")

    assert contract == {
        "schema_version": "ai-sdlc-product-benefit-contract/v1",
        "benchmark_type": "evidence-anchored-synthetic",
        "model_id": "gpt-5.6-sol",
        "reasoning_effort": "high",
        "superpowers_version": "6.3.0",
        "selection_disclosure": "advantage-aligned engineering scenarios",
        "scenario_count": 50,
        "metric_count": 14,
    }


def test_release_baseline_freezes_v301_competition_identity() -> None:
    baseline = _read_json("release-baseline.json")

    assert baseline == V3_RELEASE_BASELINE
    inputs = _builder_module().load_benchmark_inputs(BENCHMARK_ROOT)
    assert inputs.release_baseline == V3_RELEASE_BASELINE


@pytest.mark.parametrize("mutation", ["missing", "extra", "wrong-commit"])
def test_builder_rejects_open_or_drifted_release_baseline(
    tmp_path: Path, mutation: str
) -> None:
    benchmark_root = tmp_path / "benchmark"
    shutil.copytree(BENCHMARK_ROOT, benchmark_root)
    baseline_path = benchmark_root / "release-baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if mutation == "missing":
        baseline.pop("tree")
    elif mutation == "extra":
        baseline["untrusted"] = True
    else:
        baseline["commit"] = "f" * 40
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

    with pytest.raises(
        _builder_module().BenefitDataError, match="benchmark-input-invalid"
    ):
        _builder_module().load_benchmark_inputs(benchmark_root)


def test_current_ai_sdlc_arm_includes_loops_and_dynamic_experts() -> None:
    capabilities = _read_json("capabilities.json")
    arms = capabilities["arms"]
    assert isinstance(arms, dict)
    current = arms["llm-ai-sdlc"]
    assert isinstance(current, dict)

    assert current["loop_types"] == [
        "requirement",
        "design-contract",
        "implementation",
        "frontend-evidence",
        "local-pr-review",
    ]
    assert current["dynamic_expert_review"] is True
    assert current["evidence_sources"] == [
        "README.md",
        "docs/product-contract.md",
        "src/ai_sdlc/core/requirement_loop.py",
        "src/ai_sdlc/core/design_contract_loop.py",
        "src/ai_sdlc/core/implementation_loop.py",
        "src/ai_sdlc/core/frontend_evidence_loop.py",
        "src/ai_sdlc/core/loop_review_service.py",
    ]


def test_scenarios_keep_the_historical_task_mix() -> None:
    scenarios = _read_json("scenarios.json")
    categories = scenarios["categories"]
    assert isinstance(categories, list)

    assert {
        entry["id"]: entry["count"] for entry in categories if isinstance(entry, dict)
    } == {
        "backend-runtime": 11,
        "specification-design": 9,
        "frontend": 14,
        "full-stack": 6,
        "quality-regression": 5,
        "continuity-recovery": 5,
    }
    assert sum(entry["count"] for entry in categories if isinstance(entry, dict)) == 50


def test_builder_emits_three_independent_comparisons() -> None:
    builder = _builder_module()
    inputs = builder.load_benchmark_inputs(BENCHMARK_ROOT)
    datasets = builder.build_datasets(
        inputs, source_commit=V3_RELEASE_BASELINE["commit"]
    )

    assert set(datasets) == {"loop", "expert-review", "overall"}
    assert set(datasets["loop"]["arms"]) == {
        "native-llm",
        "ai-sdlc-five-loop",
    }
    assert set(datasets["expert-review"]["arms"]) == {
        "five-loop-without-experts",
        "five-loop-with-dynamic-experts",
    }
    assert set(datasets["overall"]["arms"]) == {
        "native-llm",
        "llm-superpowers",
        "llm-ai-sdlc",
    }
    for payload in datasets.values():
        assert payload["product_release"] == V3_RELEASE_BASELINE
        assert payload["source_commit"] == V3_RELEASE_BASELINE["commit"]


def test_same_inputs_produce_byte_identical_datasets() -> None:
    builder = _builder_module()
    inputs = builder.load_benchmark_inputs(BENCHMARK_ROOT)

    first = builder.build_datasets(
        inputs, source_commit=V3_RELEASE_BASELINE["commit"]
    )
    second = builder.build_datasets(
        inputs, source_commit=V3_RELEASE_BASELINE["commit"]
    )

    assert builder.canonical_json_bytes(first) == builder.canonical_json_bytes(second)


def test_overall_keeps_all_fourteen_historical_metric_definitions() -> None:
    builder = _builder_module()
    inputs = builder.load_benchmark_inputs(BENCHMARK_ROOT)
    overall = builder.build_datasets(
        inputs, source_commit=V3_RELEASE_BASELINE["commit"]
    )["overall"]

    assert len(overall["metric_definitions"]) == 14
    assert set(overall["results"]) == set(overall["metric_definitions"])
    assert overall["historical_values_reused"] is False
    assert overall["arms"]["llm-ai-sdlc"]["dynamic_expert_review"] is True


def test_generated_assets_bind_current_capabilities() -> None:
    loop = json.loads((DATA_ROOT / "loop-benefit-data.json").read_text())
    expert = json.loads((DATA_ROOT / "expert-review-benefit-data.json").read_text())
    overall = json.loads((DATA_ROOT / "overall-comparison-data.json").read_text())

    assert loop["model_id"] == expert["model_id"] == overall["model_id"]
    assert overall["model_id"] == "gpt-5.6-sol"
    assert overall["reasoning_effort"] == "high"
    assert overall["arms"]["llm-ai-sdlc"]["dynamic_expert_review"] is True
    assert overall["historical_values_reused"] is False
    assert all(
        payload["selection_disclosure"] == "advantage-aligned engineering scenarios"
        for payload in (loop, expert, overall)
    )


def test_generated_assets_are_reproducible(tmp_path: Path) -> None:
    builder = _builder_module()
    inputs = builder.load_benchmark_inputs(BENCHMARK_ROOT)
    datasets = builder.build_datasets(
        inputs,
        source_commit=json.loads(
            (DATA_ROOT / "overall-comparison-data.json").read_text()
        )["source_commit"],
    )
    builder.write_datasets(tmp_path, datasets)

    assert {
        path.name: path.read_bytes() for path in sorted(DATA_ROOT.glob("*.json"))
    } == {path.name: path.read_bytes() for path in sorted(tmp_path.glob("*.json"))}
