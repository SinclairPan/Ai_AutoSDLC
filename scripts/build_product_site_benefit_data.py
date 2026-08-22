from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_GENERATED_AT_UTC = "2026-08-21T00:00:00Z"
_EXPECTED_RELEASE_BASELINE: dict[str, object] = {
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

_OVERALL_METRICS: dict[str, dict[str, str]] = {
    "requirement_understanding_accuracy": {
        "label": "需求理解准确率",
        "direction": "higher-is-better",
        "unit": "%",
    },
    "requirement_design_completeness": {
        "label": "需求设计完整度",
        "direction": "higher-is-better",
        "unit": "%",
    },
    "development_design_coverage": {
        "label": "开发设计覆盖率",
        "direction": "higher-is-better",
        "unit": "%",
    },
    "coding_first_build_pass_rate": {
        "label": "Coding 首轮构建通过率",
        "direction": "higher-is-better",
        "unit": "%",
    },
    "unit_test_completion_rate": {
        "label": "单元测试补齐率",
        "direction": "higher-is-better",
        "unit": "%",
    },
    "frontend_first_pass_compliance": {
        "label": "前端规范一次符合率",
        "direction": "higher-is-better",
        "unit": "%",
    },
    "frontend_visual_acceptance": {
        "label": "前端视觉验收一次通过率",
        "direction": "higher-is-better",
        "unit": "%",
    },
    "requirement_drift_rate": {
        "label": "需求实现偏移率",
        "direction": "lower-is-better",
        "unit": "%",
    },
    "rework_rate": {
        "label": "返工率",
        "direction": "lower-is-better",
        "unit": "%",
    },
    "interruption_recovery_failure_rate": {
        "label": "中断恢复失败率",
        "direction": "lower-is-better",
        "unit": "%",
    },
    "human_takeover_rate": {
        "label": "人工接管率",
        "direction": "lower-is-better",
        "unit": "%",
    },
    "first_pass_acceptance_rate": {
        "label": "验收一次通过率",
        "direction": "higher-is-better",
        "unit": "%",
    },
    "audit_evidence_completeness": {
        "label": "证据可审计完整度",
        "direction": "higher-is-better",
        "unit": "%",
    },
    "delivery_cost_index": {
        "label": "总交付成本指数",
        "direction": "lower-is-better",
        "unit": "index",
    },
}

_LOOP_METRICS: dict[str, dict[str, str]] = {
    "requirement_understanding": {
        "label": "需求理解",
        "direction": "higher-is-better",
        "unit": "%",
    },
    "requirement_design": {
        "label": "需求设计",
        "direction": "higher-is-better",
        "unit": "%",
    },
    "development_traceability": {
        "label": "需求开发追踪覆盖",
        "direction": "higher-is-better",
        "unit": "%",
    },
    "code_quality": {
        "label": "代码质量",
        "direction": "higher-is-better",
        "unit": "%",
    },
}

_EXPERT_METRICS: dict[str, dict[str, str]] = {
    "issue_detection_rate": {
        "label": "阶段问题发现率",
        "direction": "higher-is-better",
        "unit": "%",
    },
    "stage_artifact_completeness": {
        "label": "五阶段生成物完整度",
        "direction": "higher-is-better",
        "unit": "%",
    },
    "residual_severe_defects": {
        "label": "残余严重缺陷",
        "direction": "lower-is-better",
        "unit": "count",
    },
    "first_pass_acceptance_rate": {
        "label": "最终首轮验收率",
        "direction": "higher-is-better",
        "unit": "%",
    },
}

_LIMITATIONS = [
    "这是证据锚定合成基准，不是客户生产统计。",
    "场景有意识选择 AI-SDLC 擅长的复杂工程交付任务。",
    "结果用于产品能力展示，不代表随机总体或统计显著性。",
]


class BenefitDataError(ValueError):
    pass


@dataclass(frozen=True)
class BenchmarkInputs:
    contract: dict[str, object]
    scenarios: dict[str, object]
    capabilities: dict[str, object]
    release_baseline: dict[str, object]


def _read_object(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenefitDataError("benchmark-input-invalid") from exc
    if not isinstance(payload, dict):
        raise BenefitDataError("benchmark-input-invalid")
    return payload


def load_benchmark_inputs(root: Path) -> BenchmarkInputs:
    contract = _read_object(root / "benchmark-contract.json")
    scenarios = _read_object(root / "scenarios.json")
    capabilities = _read_object(root / "capabilities.json")
    release_baseline = _read_object(root / "release-baseline.json")

    if release_baseline != _EXPECTED_RELEASE_BASELINE:
        raise BenefitDataError("benchmark-input-invalid")

    if set(contract) != {
        "schema_version",
        "benchmark_type",
        "model_id",
        "reasoning_effort",
        "superpowers_version",
        "selection_disclosure",
        "scenario_count",
        "metric_count",
    }:
        raise BenefitDataError("benchmark-input-invalid")
    if contract.get("benchmark_type") != "evidence-anchored-synthetic":
        raise BenefitDataError("benchmark-input-invalid")
    if contract.get("scenario_count") != 50 or contract.get("metric_count") != 14:
        raise BenefitDataError("benchmark-input-invalid")

    categories = scenarios.get("categories")
    if (
        not isinstance(categories, list)
        or sum(entry.get("count", 0) for entry in categories if isinstance(entry, dict))
        != 50
    ):
        raise BenefitDataError("benchmark-input-invalid")

    arms = capabilities.get("arms")
    if not isinstance(arms, dict) or set(arms) != {
        "native-llm",
        "llm-superpowers",
        "llm-ai-sdlc",
    }:
        raise BenefitDataError("benchmark-input-invalid")
    return BenchmarkInputs(contract, scenarios, capabilities, release_baseline)


def canonical_json_bytes(payload: Mapping[str, object]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def _digest(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _base_dataset(
    inputs: BenchmarkInputs,
    *,
    schema_version: str,
    source_commit: str,
    arms: Mapping[str, object],
    metric_definitions: Mapping[str, object],
    results: Mapping[str, object],
) -> dict[str, object]:
    if not _SHA1_RE.fullmatch(source_commit):
        raise BenefitDataError("source-commit-invalid")
    if source_commit != inputs.release_baseline["commit"]:
        raise BenefitDataError("source-commit-invalid")
    return {
        "schema_version": schema_version,
        "benchmark_type": inputs.contract["benchmark_type"],
        "generated_at_utc": _GENERATED_AT_UTC,
        "source_commit": source_commit,
        "product_release": copy.deepcopy(inputs.release_baseline),
        "model_id": inputs.contract["model_id"],
        "reasoning_effort": inputs.contract["reasoning_effort"],
        "selection_disclosure": inputs.contract["selection_disclosure"],
        "arms": copy.deepcopy(dict(arms)),
        "metric_definitions": copy.deepcopy(dict(metric_definitions)),
        "scenario_digest": _digest(inputs.scenarios),
        "capability_digest": _digest(inputs.capabilities),
        "results": copy.deepcopy(dict(results)),
        "limitations": list(_LIMITATIONS),
    }


def build_datasets(
    inputs: BenchmarkInputs, *, source_commit: str
) -> dict[str, dict[str, object]]:
    capabilities = inputs.capabilities
    loop_scores = capabilities["loop_headline_scores"]
    loop_progression = capabilities["loop_stage_progression"]
    expert_effect = capabilities["expert_review_effect"]
    expert_stages = capabilities["expert_stage_quality"]
    overall_scores = capabilities["overall_metric_scores"]
    arms = capabilities["arms"]
    if not all(
        isinstance(value, dict)
        for value in (
            loop_scores,
            loop_progression,
            expert_effect,
            expert_stages,
            overall_scores,
            arms,
        )
    ):
        raise BenefitDataError("benchmark-input-invalid")

    loop = _base_dataset(
        inputs,
        schema_version="ai-sdlc-loop-benefit/v2",
        source_commit=source_commit,
        arms={
            "native-llm": {"method": "native model direct development"},
            "ai-sdlc-five-loop": {
                "method": "AI-SDLC v3.0.1 five-type Loop Engineering",
                "loop_types": list(arms["llm-ai-sdlc"]["loop_types"]),
            },
        },
        metric_definitions=_LOOP_METRICS,
        results={
            "headline": loop_scores,
            "stage_progression": loop_progression,
        },
    )
    expert = _base_dataset(
        inputs,
        schema_version="ai-sdlc-expert-review-benefit/v2",
        source_commit=source_commit,
        arms={
            "five-loop-without-experts": {
                "method": "same five-type AI-SDLC flow without independent experts"
            },
            "five-loop-with-dynamic-experts": {
                "method": "same five-type AI-SDLC flow with risk-routed read-only experts"
            },
        },
        metric_definitions=_EXPERT_METRICS,
        results={"headline": expert_effect, "stages": expert_stages},
    )
    overall = _base_dataset(
        inputs,
        schema_version="ai-sdlc-overall-comparison/v2",
        source_commit=source_commit,
        arms=arms,
        metric_definitions=_OVERALL_METRICS,
        results=overall_scores,
    )
    overall["historical_values_reused"] = False
    overall["historical_metric_system"] = (
        "Ai_AutoSDLC/docs/ai-sdlc-value-benchmark.zh-CN.md"
    )
    return {"loop": loop, "expert-review": expert, "overall": overall}


def write_datasets(
    output_root: Path, datasets: Mapping[str, Mapping[str, object]]
) -> None:
    filenames = {
        "loop": "loop-benefit-data.json",
        "expert-review": "expert-review-benefit-data.json",
        "overall": "overall-comparison-data.json",
    }
    if set(datasets) != set(filenames):
        raise BenefitDataError("dataset-set-invalid")
    output_root.mkdir(parents=True, exist_ok=True)
    for dataset_id, filename in filenames.items():
        (output_root / filename).write_bytes(canonical_json_bytes(datasets[dataset_id]))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    inputs = load_benchmark_inputs(args.benchmark_root)
    datasets = build_datasets(inputs, source_commit=args.source_commit)
    write_datasets(args.output_root, datasets)
    print(
        json.dumps(
            {
                "status": "generated",
                "datasets": sorted(datasets),
                "provider_sessions": 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
