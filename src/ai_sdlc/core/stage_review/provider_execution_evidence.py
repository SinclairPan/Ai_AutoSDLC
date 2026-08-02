"""统一计算 Provider 隔离与出口证据根。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.provider_usage_models import AccountedProviderUsage


def provider_execution_evidence_root_digest(
    isolation_receipt_digests: tuple[str, ...],
    egress_receipt_digests: tuple[str, ...] = (),
) -> str:
    if not isolation_receipt_digests and not egress_receipt_digests:
        return ""
    return canonical_digest(
        {
            "egress_receipt_digests": egress_receipt_digests,
            "isolation_receipt_digests": isolation_receipt_digests,
        },
        CanonicalizationPolicy(),
    )


class ProviderExecutionOutcome(BaseModel):
    """Provider 响应不可用时仍可独立持久化的执行事实。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["not_executed", "executed"]
    accounted_usage: AccountedProviderUsage | None = None
    isolation_receipt_digests: tuple[str, ...] = ()
    egress_receipt_digests: tuple[str, ...] = ()
    execution_evidence_root_digest: str = ""

    @model_validator(mode="after")
    def _verify_outcome(self) -> Self:
        receipts = self.isolation_receipt_digests + self.egress_receipt_digests
        if any(not item.strip() for item in receipts) or len(receipts) != len(
            set(receipts)
        ):
            raise ValueError("provider execution receipt lineage is invalid")
        has_execution = self.accounted_usage is not None or bool(receipts)
        if (self.status == "executed") != has_execution:
            raise ValueError("provider execution outcome status is inconsistent")
        expected = provider_execution_evidence_root_digest(
            self.isolation_receipt_digests, self.egress_receipt_digests
        )
        if self.execution_evidence_root_digest != expected:
            raise ValueError("provider execution outcome root is invalid")
        return self


def build_provider_execution_outcome(
    accounted_usage: AccountedProviderUsage | None = None,
    *,
    isolation_receipt_digests: tuple[str, ...] = (),
    egress_receipt_digests: tuple[str, ...] = (),
) -> ProviderExecutionOutcome:
    return ProviderExecutionOutcome(
        status=(
            "executed"
            if accounted_usage is not None
            or isolation_receipt_digests
            or egress_receipt_digests
            else "not_executed"
        ),
        accounted_usage=accounted_usage,
        isolation_receipt_digests=tuple(dict.fromkeys(isolation_receipt_digests)),
        egress_receipt_digests=tuple(dict.fromkeys(egress_receipt_digests)),
        execution_evidence_root_digest=provider_execution_evidence_root_digest(
            tuple(dict.fromkeys(isolation_receipt_digests)),
            tuple(dict.fromkeys(egress_receipt_digests)),
        ),
    )


def merge_provider_execution_outcomes(
    first: ProviderExecutionOutcome,
    second: ProviderExecutionOutcome,
) -> ProviderExecutionOutcome:
    usages = tuple(
        item for item in (first.accounted_usage, second.accounted_usage) if item
    )
    if len(usages) == 2 and usages[0] != usages[1]:
        raise ValueError("provider execution usage lineage diverged")
    return build_provider_execution_outcome(
        usages[0] if usages else None,
        isolation_receipt_digests=(
            *first.isolation_receipt_digests,
            *second.isolation_receipt_digests,
        ),
        egress_receipt_digests=(
            *first.egress_receipt_digests,
            *second.egress_receipt_digests,
        ),
    )


__all__ = [
    "ProviderExecutionOutcome",
    "build_provider_execution_outcome",
    "merge_provider_execution_outcomes",
    "provider_execution_evidence_root_digest",
]
