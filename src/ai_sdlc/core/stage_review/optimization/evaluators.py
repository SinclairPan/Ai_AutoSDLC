"""版本化 Evaluator Contract/Adapter 注册表与调用前权限校验。"""

from __future__ import annotations

from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.optimization.models import (
    CandidateDomain,
    CandidatePartition,
    OptimizationCandidate,
    OptimizationEvaluationReport,
)
from ai_sdlc.core.stage_review.registry_versions import (
    require_machine_id,
    require_version,
)


class EvaluationContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_digest: str
    partition: CandidatePartition
    evaluation_binding_id: str
    evaluation_provider_id: str
    provider_capabilities: tuple[str, ...]
    resource_reservation_digest: str
    hypothesis_family_digest: str = ""

    @field_validator("evaluation_binding_id", "evaluation_provider_id")
    @classmethod
    def _identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "evaluation provider identity")

    @field_validator("provider_capabilities")
    @classmethod
    def _capabilities_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or value != tuple(sorted(set(value))):
            raise ValueError("evaluation provider capabilities must be canonical")
        return value


class EvaluatorContract(ArtifactCompatibility):
    schema_version: Literal["optimization-evaluator-contract.v1"] = (
        "optimization-evaluator-contract.v1"
    )
    artifact_kind: Literal["optimization-evaluator-contract"] = (
        "optimization-evaluator-contract"
    )
    evaluator_kind: str
    evaluator_version: str
    candidate_schema_version: str
    report_schema_version: str
    allowed_partitions: tuple[CandidatePartition, ...]
    compatible_candidate_domains: tuple[CandidateDomain, ...]
    independence_level: Literal["deterministic", "independent_binding"]
    deterministic: bool
    provider_constraints: tuple[str, ...]
    contract_digest: str = ""

    @field_validator("evaluator_kind")
    @classmethod
    def _kind_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "evaluator_kind")

    @field_validator("evaluator_version")
    @classmethod
    def _version_is_stable(cls, value: str) -> str:
        return require_version(value)

    @field_validator(
        "allowed_partitions",
        "compatible_candidate_domains",
        "provider_constraints",
    )
    @classmethod
    def _sets_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or value != tuple(sorted(set(value))):
            raise ValueError("evaluator contract sets must be sorted and unique")
        return value

    @model_validator(mode="after")
    def _verify_contract(self) -> Self:
        if self.deterministic != (self.independence_level == "deterministic"):
            raise ValueError("evaluator deterministic declaration is inconsistent")
        return fill_artifact_digest(self, "contract_digest")


class EvaluatorAdapter(Protocol):
    def evaluate(
        self,
        candidate: OptimizationCandidate,
        context: EvaluationContext,
        contract: EvaluatorContract,
    ) -> OptimizationEvaluationReport: ...


class OptimizationEvaluatorRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, tuple[EvaluatorContract, EvaluatorAdapter]] = {}

    def register(
        self,
        contract: EvaluatorContract,
        adapter: EvaluatorAdapter,
    ) -> None:
        trusted = EvaluatorContract.model_validate(contract.model_dump(mode="json"))
        if trusted.evaluator_kind in self._entries:
            raise ValueError("evaluator_kind is already registered")
        self._entries[trusted.evaluator_kind] = (trusted, adapter)

    def evaluate(
        self,
        *,
        evaluator_kind: str,
        candidate: OptimizationCandidate,
        context: EvaluationContext,
    ) -> OptimizationEvaluationReport:
        try:
            contract, adapter = self._entries[evaluator_kind]
        except KeyError as exc:
            raise ValueError("evaluator_kind is not registered") from exc
        trusted = OptimizationCandidate.model_validate(
            candidate.model_dump(mode="json")
        )
        runtime = EvaluationContext.model_validate(context.model_dump(mode="json"))
        _validate_invocation(contract, trusted, runtime)
        runtime = runtime.model_copy(
            update={
                "hypothesis_family_digest": _evaluation_hypothesis_family(
                    contract, trusted, runtime
                )
            }
        )
        report = OptimizationEvaluationReport.model_validate(
            adapter.evaluate(trusted, runtime, contract).model_dump(mode="json")
        )
        _validate_report(contract, trusted, runtime, report)
        return report


def _validate_invocation(
    contract: EvaluatorContract,
    candidate: OptimizationCandidate,
    context: EvaluationContext,
) -> None:
    if contract.candidate_schema_version != candidate.schema_version:
        raise ValueError("candidate schema is incompatible with evaluator")
    if candidate.candidate_domain not in contract.compatible_candidate_domains:
        raise ValueError("candidate domain is not authorized by evaluator")
    if context.partition not in contract.allowed_partitions:
        raise ValueError("dataset partition is not authorized by evaluator")
    if (
        contract.independence_level == "independent_binding"
        and context.evaluation_binding_id == candidate.generator_identity
    ):
        raise ValueError(
            "semantic evaluator requires an independent evaluation binding"
        )
    if (
        contract.independence_level == "independent_binding"
        and context.evaluation_provider_id == candidate.generator_provider_id
    ):
        raise ValueError("semantic evaluator cannot reuse the generator provider")
    if not set(contract.provider_constraints) <= set(context.provider_capabilities):
        raise ValueError("evaluation provider constraints are not satisfied")


def _validate_report(
    contract: EvaluatorContract,
    candidate: OptimizationCandidate,
    context: EvaluationContext,
    report: OptimizationEvaluationReport,
) -> None:
    lineage = (
        contract.report_schema_version == report.schema_version,
        report.candidate_digest == candidate.candidate_digest,
        report.evaluator_kind == contract.evaluator_kind,
        report.evaluator_version == contract.evaluator_version,
        report.dataset_digest == context.dataset_digest,
        report.partition == context.partition,
        report.evaluation_binding_id == context.evaluation_binding_id,
        report.hypothesis_family_digest == context.hypothesis_family_digest,
        report.domain_contract_digest == candidate.domain_contract_digest,
        report.domain_adapter_id == candidate.domain_adapter_id,
        report.domain_adapter_version == candidate.domain_adapter_version,
        report.domain_adapter_digest == candidate.domain_adapter_digest,
        report.domain_registry_digest == candidate.domain_registry_digest,
    )
    if not all(lineage):
        raise ValueError("evaluator report lineage is invalid")


def _evaluation_hypothesis_family(
    contract: EvaluatorContract,
    candidate: OptimizationCandidate,
    context: EvaluationContext,
) -> str:
    return canonical_digest(
        {
            "candidate_domain": candidate.candidate_domain,
            "target_stratum_ids": candidate.target_stratum_ids,
            "dataset_digest": context.dataset_digest,
            "partition": context.partition,
            "evaluator_kind": contract.evaluator_kind,
        },
        CanonicalizationPolicy(),
    )
