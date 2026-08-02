"""Candidate Domain 的完整、版本化生命周期适配器注册表。"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from ai_sdlc.core.stage_review.artifact_compat import JsonValue
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.optimization.attribution import FindingAttribution
from ai_sdlc.core.stage_review.optimization.candidate_dataset import (
    CandidateDatasetView,
)
from ai_sdlc.core.stage_review.optimization.candidate_domain_contracts import (
    CandidateDomainAdapterBundle,
    CandidateDomainContract,
    CandidateGenerator,
    CandidatePayloadValidator,
    candidate_domain_contract_payload,
)
from ai_sdlc.core.stage_review.optimization.datasets import (
    OptimizationDatasetSnapshot,
)
from ai_sdlc.core.stage_review.optimization.models import (
    OptimizationCandidate,
    OptimizationEvaluationReport,
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


@dataclass(frozen=True, slots=True)
class _CandidateDomainRegistration:
    contract: CandidateDomainContract
    adapter: CandidateDomainAdapterBundle


class CandidateDomainRegistry:
    def __init__(self) -> None:
        self._registrations: dict[str, _CandidateDomainRegistration] = {}
        self._frozen = False
        self._snapshot_digest = ""

    @property
    def snapshot_digest(self) -> str:
        self._require_frozen()
        return self._snapshot_digest

    @property
    def domain_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._registrations))

    def candidate_binding(self, domain_id: str) -> Mapping[str, str]:
        """供受信生成器显式构造已绑定 Candidate。"""

        self._require_frozen()
        return dict(self._binding_values(self._registration(domain_id)))

    def register(
        self,
        contract: CandidateDomainContract,
        adapter: CandidateDomainAdapterBundle,
    ) -> None:
        if self._frozen:
            raise ValueError("candidate domain registry is frozen")
        if contract.domain_id in self._registrations:
            raise ValueError("candidate domain is already registered")
        self._registrations[contract.domain_id] = _CandidateDomainRegistration(
            contract,
            adapter,
        )

    def freeze(self) -> CandidateDomainRegistry:
        if not self._registrations:
            raise ValueError("candidate domain registry cannot be empty")
        payload = tuple(
            {
                "contract": candidate_domain_contract_payload(item.contract),
                "adapter_id": item.adapter.adapter_id,
                "adapter_version": item.adapter.adapter_version,
                "adapter_digest": item.adapter.adapter_digest,
            }
            for item in self._ordered_registrations()
        )
        self._snapshot_digest = canonical_digest(
            {"schema_version": "candidate-domain-registry.v1", "entries": payload},
            CanonicalizationPolicy(),
        )
        self._frozen = True
        return self

    def generate(
        self,
        baseline: OptimizationSnapshot,
        dataset: CandidateDatasetView,
    ) -> tuple[OptimizationCandidate, ...]:
        self._require_frozen()
        candidates = tuple(
            self._bind_candidate(candidate, registration)
            for registration in self._ordered_registrations()
            for candidate in _required_callback(registration.adapter.generator)(
                baseline,
                dataset,
            )
        )
        for candidate in candidates:
            self.require_candidate(candidate)
            self._require_dataset_lineage(candidate, dataset)
        return candidates

    def require_candidate(self, candidate: OptimizationCandidate) -> None:
        self._require_frozen()
        registration = self._registration(candidate.candidate_domain)
        self._require_candidate_binding(candidate, registration)
        patterns = registration.contract.authorized_field_patterns
        if any(
            not any(re.fullmatch(pattern, item.field_path) for pattern in patterns)
            for item in candidate.patch_operations
        ):
            raise ValueError("optimization patch field is not authorized")
        metric = bool(candidate.metric_evidence_digests)
        expected_metric = registration.contract.lineage_kind == "metric"
        if metric != expected_metric:
            raise ValueError("candidate evidence lineage contradicts domain contract")

    def apply_patch(
        self,
        candidate: OptimizationCandidate,
        payload: dict[str, JsonValue],
    ) -> None:
        registration = self._trusted_registration(candidate)
        _required_callback(registration.adapter.patch_applier)(
            payload,
            candidate.patch_operations,
        )

    def validate_payload(
        self,
        candidate: OptimizationCandidate,
        payload: dict[str, JsonValue],
        baseline: Mapping[str, JsonValue],
    ) -> None:
        registration = self._trusted_registration(candidate)
        _required_callback(registration.adapter.payload_validator)(payload, baseline)

    def improved_sessions(
        self,
        candidate: OptimizationCandidate,
        dataset: OptimizationDatasetSnapshot,
        session_ids: tuple[str, ...],
        sources: tuple[FindingAttribution, ...],
        all_attributions: tuple[FindingAttribution, ...],
    ) -> tuple[str, ...]:
        registration = self._trusted_registration(candidate)
        return _required_callback(registration.adapter.improvement_evaluator)(
            candidate, dataset, session_ids, sources, all_attributions
        )

    def evaluation_metrics(
        self,
        candidate: OptimizationCandidate,
        dataset: OptimizationDatasetSnapshot,
        session_ids: tuple[str, ...],
        improved: tuple[str, ...],
        sources: tuple[FindingAttribution, ...],
    ) -> dict[str, object]:
        registration = self._trusted_registration(candidate)
        return _required_callback(registration.adapter.report_metrics)(
            candidate, dataset, session_ids, improved, sources
        )

    def matches_shadow(
        self,
        binding: CommittedSessionBinding,
        candidate: OptimizationCandidate,
    ) -> bool:
        registration = self._trusted_registration(candidate)
        return _required_callback(registration.adapter.shadow_matcher)(
            binding, candidate
        )

    def shadow_improved(
        self,
        candidate: OptimizationCandidate,
        observation: OptimizationShadowObservation,
    ) -> bool:
        registration = self._trusted_registration(candidate)
        return _required_callback(registration.adapter.shadow_comparator)(
            candidate, observation
        )

    def promotion_guards(
        self,
        candidate: OptimizationCandidate,
        reports: tuple[OptimizationEvaluationReport, ...],
        shadow: PipelineShadowResult,
    ) -> Mapping[str, bool]:
        registration = self._trusted_registration(candidate)
        return _required_callback(registration.adapter.promotion_guard)(
            candidate, reports, shadow
        )

    def _trusted_registration(
        self, candidate: OptimizationCandidate
    ) -> _CandidateDomainRegistration:
        self.require_candidate(candidate)
        return self._registration(candidate.candidate_domain)

    def _require_dataset_lineage(
        self,
        candidate: OptimizationCandidate,
        dataset: CandidateDatasetView,
    ) -> None:
        registration = self._registration(candidate.candidate_domain)
        if registration.contract.lineage_kind == "metric":
            if dataset.view_digest not in candidate.metric_evidence_digests:
                raise ValueError("candidate metric evidence is outside train view")
            return
        available = {
            item.attribution_digest
            for item in dataset.attributions
            if item.candidate_domain == candidate.candidate_domain
        }
        if not set(candidate.attribution_digests) <= available:
            raise ValueError("candidate attribution is outside train view")

    def _bind_candidate(
        self,
        candidate: OptimizationCandidate,
        registration: _CandidateDomainRegistration,
    ) -> OptimizationCandidate:
        if candidate.candidate_domain != registration.contract.domain_id:
            raise ValueError("candidate generator returned another domain")
        expected = self._binding_values(registration)
        current = _candidate_binding_values(candidate)
        if any(current) and current != tuple(expected.values()):
            raise ValueError("candidate domain adapter binding is forged")
        payload = candidate.model_dump(mode="json", exclude={"candidate_digest"})
        return OptimizationCandidate.model_validate({**payload, **expected})

    def _require_candidate_binding(
        self,
        candidate: OptimizationCandidate,
        registration: _CandidateDomainRegistration,
    ) -> None:
        if _candidate_binding_values(candidate) != tuple(
            self._binding_values(registration).values()
        ):
            raise ValueError("candidate domain adapter binding is invalid")

    def _binding_values(
        self, registration: _CandidateDomainRegistration
    ) -> dict[str, str]:
        return {
            "domain_contract_digest": registration.contract.contract_digest,
            "domain_adapter_id": registration.adapter.adapter_id,
            "domain_adapter_version": registration.adapter.adapter_version,
            "domain_adapter_digest": registration.adapter.adapter_digest,
            "domain_registry_digest": self.snapshot_digest,
        }

    def _registration(self, domain_id: str) -> _CandidateDomainRegistration:
        try:
            return self._registrations[domain_id]
        except KeyError as exc:
            raise ValueError("candidate domain is not registered") from exc

    def _ordered_registrations(self) -> tuple[_CandidateDomainRegistration, ...]:
        return tuple(self._registrations[key] for key in sorted(self._registrations))

    def _require_frozen(self) -> None:
        if not self._frozen:
            raise ValueError("candidate domain registry is not frozen")


def _candidate_binding_values(candidate: OptimizationCandidate) -> tuple[str, ...]:
    return (
        candidate.domain_contract_digest,
        candidate.domain_adapter_id,
        candidate.domain_adapter_version,
        candidate.domain_adapter_digest,
        candidate.domain_registry_digest,
    )


def _required_callback(value: Callable[..., object] | None) -> Callable[..., object]:
    if value is None:  # pragma: no cover - Bundle 构造器已阻断。
        raise ValueError("candidate domain lifecycle adapter is incomplete")
    return value


__all__ = [
    "CandidateDomainAdapterBundle",
    "CandidateDomainContract",
    "CandidateDomainRegistry",
    "CandidateGenerator",
    "CandidatePayloadValidator",
]
