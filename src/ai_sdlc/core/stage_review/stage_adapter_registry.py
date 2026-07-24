"""五类 Stage Candidate Adapter 的版本化注册真值。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, model_validator

from ai_sdlc.core.loop_models import LoopType
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)

AdapterInputKind = Literal["loop-run", "local-pr-review"]


class StageCloseAdapter(Protocol):
    @property
    def loop_type(self) -> LoopType: ...

    @property
    def stage_key(self) -> str: ...


class StageCandidateAdapterContract(BaseModel):
    """把稳定产品身份与具体 Python 实现绑定为内容摘要。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["stage-candidate-adapter-contract.v1"] = (
        "stage-candidate-adapter-contract.v1"
    )
    adapter_id: str
    adapter_version: str
    loop_type: LoopType
    stage_key: str
    input_kind: AdapterInputKind
    implementation_module: str
    implementation_qualname: str
    contract_digest: str

    @model_validator(mode="after")
    def _verify_contract(self) -> StageCandidateAdapterContract:
        required = (
            self.adapter_id,
            self.adapter_version,
            self.stage_key,
            self.implementation_module,
            self.implementation_qualname,
        )
        if any(not value.strip() or value != value.strip() for value in required):
            raise ValueError("stage candidate adapter contract identity is invalid")
        if self.stage_key != str(self.loop_type):
            raise ValueError("stage candidate adapter route is inconsistent")
        if self.contract_digest != _contract_digest(self):
            raise ValueError("stage candidate adapter contract digest is invalid")
        return self


@dataclass(frozen=True, slots=True)
class StageCandidateAdapterRegistration:
    contract: StageCandidateAdapterContract
    adapter_type: type[StageCloseAdapter]

    def create(self) -> StageCloseAdapter:
        return self.adapter_type()


class StageCandidateAdapterRegistry:
    """按精确实现类型注册，冻结后只允许解析既有合同。"""

    def __init__(self) -> None:
        self._by_loop: dict[LoopType, StageCandidateAdapterRegistration] = {}
        self._by_type: dict[type[object], StageCandidateAdapterRegistration] = {}
        self._frozen = False
        self._registry_digest = ""

    @property
    def registry_digest(self) -> str:
        if not self._frozen:
            raise ValueError("stage candidate adapter registry is not frozen")
        return self._registry_digest

    def register(
        self,
        *,
        adapter_type: type[StageCloseAdapter],
        adapter_id: str,
        adapter_version: str,
        input_kind: AdapterInputKind,
    ) -> None:
        if self._frozen:
            raise ValueError("stage candidate adapter registry is frozen")
        instance = adapter_type()
        loop_type = LoopType(str(instance.loop_type))
        if loop_type in self._by_loop or adapter_type in self._by_type:
            raise ValueError("stage candidate adapter registration is duplicated")
        contract = _build_contract(
            adapter_type=adapter_type,
            adapter_id=adapter_id,
            adapter_version=adapter_version,
            loop_type=loop_type,
            stage_key=instance.stage_key,
            input_kind=input_kind,
        )
        registration = StageCandidateAdapterRegistration(contract, adapter_type)
        self._by_loop[loop_type] = registration
        self._by_type[adapter_type] = registration

    def freeze(self, required_loop_types: Iterable[LoopType]) -> None:
        required = set(required_loop_types)
        if set(self._by_loop) != required:
            raise ValueError("stage candidate adapter registry coverage is incomplete")
        contracts = [
            item.contract.model_dump(mode="json")
            for item in sorted(
                self._by_loop.values(),
                key=lambda value: value.contract.adapter_id,
            )
        ]
        self._registry_digest = canonical_digest(
            {"schema_version": "stage-candidate-adapter-registry.v1", "contracts": contracts},
            CanonicalizationPolicy(),
        )
        self._frozen = True

    def resolve_instance(
        self,
        adapter: StageCloseAdapter,
    ) -> StageCandidateAdapterRegistration:
        registration = self._by_type.get(type(adapter))
        if registration is None:
            raise ValueError("stage candidate adapter is not registered")
        contract = registration.contract
        if str(adapter.loop_type) != str(contract.loop_type):
            raise ValueError("stage candidate adapter loop type is inconsistent")
        if adapter.stage_key != contract.stage_key:
            raise ValueError("stage candidate adapter stage key is inconsistent")
        return registration

    def resolve_prepared(
        self,
        *,
        adapter_id: str,
        adapter_version: str,
        adapter_contract_digest: str,
        loop_type: LoopType | str,
    ) -> StageCandidateAdapterRegistration:
        registration = self._by_loop.get(LoopType(str(loop_type)))
        if registration is None:
            raise ValueError("stage candidate adapter is not registered")
        contract = registration.contract
        actual = (contract.adapter_id, contract.adapter_version, contract.contract_digest)
        if actual != (adapter_id, adapter_version, adapter_contract_digest):
            raise ValueError("prepared stage candidate adapter contract is invalid")
        return registration


def _build_contract(
    *,
    adapter_type: type[StageCloseAdapter],
    adapter_id: str,
    adapter_version: str,
    loop_type: LoopType,
    stage_key: str,
    input_kind: AdapterInputKind,
) -> StageCandidateAdapterContract:
    values = {
        "adapter_id": adapter_id,
        "adapter_version": adapter_version,
        "loop_type": loop_type,
        "stage_key": stage_key,
        "input_kind": input_kind,
        "implementation_module": adapter_type.__module__,
        "implementation_qualname": adapter_type.__qualname__,
    }
    draft = StageCandidateAdapterContract.model_construct(
        **values,
        contract_digest="",
    )
    return StageCandidateAdapterContract.model_validate(
        {**values, "contract_digest": _contract_digest(draft)}
    )


def _contract_digest(contract: StageCandidateAdapterContract) -> str:
    return canonical_digest(
        contract.model_dump(mode="json", exclude={"contract_digest"}),
        CanonicalizationPolicy(),
    )


@lru_cache(maxsize=1)
def default_stage_candidate_adapter_registry() -> StageCandidateAdapterRegistry:
    from ai_sdlc.core.stage_review.adapters import (
        DesignContractStageAdapter,
        FrontendEvidenceStageAdapter,
        ImplementationStageAdapter,
        LocalPRReviewStageAdapter,
        RequirementStageAdapter,
    )

    registry = StageCandidateAdapterRegistry()
    cases: tuple[tuple[type[object], str, AdapterInputKind], ...] = (
        (RequirementStageAdapter, "stage-candidate.requirement", "loop-run"),
        (DesignContractStageAdapter, "stage-candidate.design-contract", "loop-run"),
        (ImplementationStageAdapter, "stage-candidate.implementation", "loop-run"),
        (FrontendEvidenceStageAdapter, "stage-candidate.frontend-evidence", "loop-run"),
        (LocalPRReviewStageAdapter, "stage-candidate.local-pr-review", "local-pr-review"),
    )
    for adapter_type, adapter_id, input_kind in cases:
        registry.register(
            adapter_type=cast(type[StageCloseAdapter], adapter_type),
            adapter_id=adapter_id,
            adapter_version="1.0.0",
            input_kind=input_kind,
        )
    registry.freeze(tuple(LoopType))
    return registry


__all__ = [
    "StageCandidateAdapterContract",
    "StageCandidateAdapterRegistration",
    "StageCandidateAdapterRegistry",
    "StageCloseAdapter",
    "default_stage_candidate_adapter_registry",
]
