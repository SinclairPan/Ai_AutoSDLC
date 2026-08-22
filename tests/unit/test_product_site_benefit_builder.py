from __future__ import annotations

import importlib
import json
from pathlib import Path

BENCHMARK_ROOT = Path("benchmarks/ai-sdlc-product-value")
DATA_ROOT = Path("deliverables/ai-sdlc-2.0-offline-product-site/assets/data")


def _read_json(relative_path: str) -> dict[str, object]:
    return json.loads((BENCHMARK_ROOT / relative_path).read_text(encoding="utf-8"))


def _builder_module():
    return importlib.import_module("scripts.build_product_site_benefit_data")


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


def test_current_ai_sdlc_arm_includes_loops_and_dynamic_experts() -> None:
    capabilities = _read_json("capabilities.json")
    arms = capabilities["arms"]
    assert isinstance(arms, dict)
    current = arms["llm-ai-sdlc"]
    assert isinstance(current, dict)

    assert current["loop_stages"] == [
        "requirement",
        "design-contract",
        "implementation",
        "frontend-evidence",
        "local-pr-review",
    ]
    assert current["dynamic_expert_review"] is True


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
    datasets = builder.build_datasets(inputs, source_commit="a" * 40)

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


def test_same_inputs_produce_byte_identical_datasets() -> None:
    builder = _builder_module()
    inputs = builder.load_benchmark_inputs(BENCHMARK_ROOT)

    first = builder.build_datasets(inputs, source_commit="b" * 40)
    second = builder.build_datasets(inputs, source_commit="b" * 40)

    assert builder.canonical_json_bytes(first) == builder.canonical_json_bytes(second)


def test_overall_keeps_all_fourteen_historical_metric_definitions() -> None:
    builder = _builder_module()
    inputs = builder.load_benchmark_inputs(BENCHMARK_ROOT)
    overall = builder.build_datasets(inputs, source_commit="c" * 40)["overall"]

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
