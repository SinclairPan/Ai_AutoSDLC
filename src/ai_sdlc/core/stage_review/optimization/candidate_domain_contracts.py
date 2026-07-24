"""Candidate Domain 生命周期适配器的版本化合同。"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from ai_sdlc.core.stage_review.artifact_compat import JsonValue
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.optimization.attribution import FindingAttribution
from ai_sdlc.core.stage_review.optimization.candidate_dataset import (
    CandidateDatasetView,
)
from ai_sdlc.core.stage_review.optimization.datasets import (
    OptimizationDatasetSnapshot,
)
from ai_sdlc.core.stage_review.optimization.models import (
    OptimizationCandidate,
    OptimizationEvaluationReport,
    OptimizationPatchOperation,
)
from ai_sdlc.core.stage_review.optimization.observations import (
    CommittedSessionBinding,
)
from ai_sdlc.core.stage_review.optimization.pipeline_contracts import (
    PipelineShadowResult,
)
from ai_sdlc.core.stage_review.optimization.shadow_observations import (
    OptimizationShadowObservation,
)
from ai_sdlc.core.stage_review.optimization.snapshot_models import OptimizationSnapshot
from ai_sdlc.core.stage_review.registry_versions import (
    require_machine_id,
    require_version,
)

CandidateLineageKind = Literal["attribution", "metric"]
CandidateGenerator = Callable[
    [OptimizationSnapshot, CandidateDatasetView],
    tuple[OptimizationCandidate, ...],
]
CandidatePayloadValidator = Callable[
    [dict[str, JsonValue], Mapping[str, JsonValue]],
    None,
]
CandidatePatchApplier = Callable[
    [dict[str, JsonValue], tuple[OptimizationPatchOperation, ...]],
    None,
]
CandidateImprovementEvaluator = Callable[
    [
        OptimizationCandidate,
        OptimizationDatasetSnapshot,
        tuple[str, ...],
        tuple[FindingAttribution, ...],
        tuple[FindingAttribution, ...],
    ],
    tuple[str, ...],
]
CandidateReportMetrics = Callable[
    [
        OptimizationCandidate,
        OptimizationDatasetSnapshot,
        tuple[str, ...],
        tuple[str, ...],
        tuple[FindingAttribution, ...],
    ],
    dict[str, object],
]
CandidateShadowMatcher = Callable[
    [CommittedSessionBinding, OptimizationCandidate],
    bool,
]
CandidateShadowComparator = Callable[
    [OptimizationCandidate, OptimizationShadowObservation],
    bool,
]
CandidatePromotionGuard = Callable[
    [
        OptimizationCandidate,
        tuple[OptimizationEvaluationReport, ...],
        PipelineShadowResult,
    ],
    Mapping[str, bool],
]


@dataclass(frozen=True, slots=True)
class CandidateDomainContract:
    domain_id: str
    contract_version: str
    lineage_kind: CandidateLineageKind
    authorized_field_patterns: tuple[str, ...]
    contract_digest: str = field(init=False)

    def __post_init__(self) -> None:
        require_machine_id(self.domain_id, "candidate domain")
        require_version(self.contract_version)
        if not self.authorized_field_patterns:
            raise ValueError("candidate domain contract is incomplete")
        for pattern in self.authorized_field_patterns:
            re.compile(pattern)
        object.__setattr__(self, "contract_digest", _contract_digest(self))


@dataclass(frozen=True, slots=True)
class CandidateDomainAdapterBundle:
    adapter_id: str
    adapter_version: str
    generator: CandidateGenerator | None = None
    payload_validator: CandidatePayloadValidator | None = None
    patch_applier: CandidatePatchApplier | None = None
    improvement_evaluator: CandidateImprovementEvaluator | None = None
    report_metrics: CandidateReportMetrics | None = None
    shadow_matcher: CandidateShadowMatcher | None = None
    shadow_comparator: CandidateShadowComparator | None = None
    promotion_guard: CandidatePromotionGuard | None = None
    adapter_digest: str = field(init=False)

    def __post_init__(self) -> None:
        require_machine_id(self.adapter_id, "candidate domain adapter")
        require_version(self.adapter_version)
        if any(not callable(item) for item in self.callbacks()):
            raise ValueError("candidate domain lifecycle adapter is incomplete")
        object.__setattr__(self, "adapter_digest", _adapter_digest(self))

    def callbacks(self) -> tuple[Callable[..., object] | None, ...]:
        return (
            self.generator,
            self.payload_validator,
            self.patch_applier,
            self.improvement_evaluator,
            self.report_metrics,
            self.shadow_matcher,
            self.shadow_comparator,
            self.promotion_guard,
        )


def candidate_domain_contract_payload(
    contract: CandidateDomainContract,
) -> dict[str, object]:
    return {
        **_contract_payload_unbound(contract),
        "contract_digest": contract.contract_digest,
    }


def _contract_digest(contract: CandidateDomainContract) -> str:
    return canonical_digest(
        _contract_payload_unbound(contract),
        CanonicalizationPolicy(),
    )


def _contract_payload_unbound(
    contract: CandidateDomainContract,
) -> dict[str, object]:
    return {
        "domain_id": contract.domain_id,
        "contract_version": contract.contract_version,
        "lineage_kind": contract.lineage_kind,
        "authorized_field_patterns": contract.authorized_field_patterns,
    }


def _adapter_digest(adapter: CandidateDomainAdapterBundle) -> str:
    return canonical_digest(
        {
            "adapter_id": adapter.adapter_id,
            "adapter_version": adapter.adapter_version,
            "callbacks": tuple(_callable_identity(item) for item in adapter.callbacks()),
        },
        CanonicalizationPolicy(),
    )


def _callable_identity(value: Callable[..., object] | None) -> str:
    if value is None:
        return ""
    return f"{value.__module__}:{value.__qualname__}"


__all__ = [
    "CandidateDomainAdapterBundle",
    "CandidateDomainContract",
    "CandidateGenerator",
    "CandidatePayloadValidator",
    "candidate_domain_contract_payload",
]
