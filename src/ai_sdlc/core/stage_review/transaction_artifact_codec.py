"""关闭事务、治理与 Repo Lease 工件的 current/previous-major 路由。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar, cast

from pydantic import BaseModel

from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.close_governance_models import (
    StageCloseGovernanceAuthorityBinding,
    StageCloseGovernanceDecision,
)
from ai_sdlc.core.stage_review.close_models import (
    CloseConsumptionClaim,
    CloseConsumptionEvent,
    StageCloseConsumptionReceipt,
)
from ai_sdlc.core.stage_review.close_recovery_models import (
    StageCloseRecoveryDecision,
)
from ai_sdlc.core.stage_review.repo_write_lease_models import (
    RepoWriteLease,
    RepoWriteLeaseEvent,
)

_ModelT = TypeVar("_ModelT", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class _CodecSpec:
    current_version: str
    previous_version: str
    digest_field: str


_SPECS: dict[type[BaseModel], _CodecSpec] = {
    CloseConsumptionClaim: _CodecSpec(
        "close-consumption-claim.v1",
        "close-consumption-claim.v0",
        "claim_digest",
    ),
    CloseConsumptionEvent: _CodecSpec(
        "close-consumption-event.v1",
        "close-consumption-event.v0",
        "event_digest",
    ),
    StageCloseConsumptionReceipt: _CodecSpec(
        "stage-close-consumption-receipt.v1",
        "stage-close-consumption-receipt.v0",
        "receipt_digest",
    ),
    RepoWriteLease: _CodecSpec(
        "repo-write-lease.v1",
        "repo-write-lease.v0",
        "lease_digest",
    ),
    RepoWriteLeaseEvent: _CodecSpec(
        "repo-write-lease-event.v1",
        "repo-write-lease-event.v0",
        "event_digest",
    ),
    StageCloseGovernanceAuthorityBinding: _CodecSpec(
        "stage-close-governance-authority.v1",
        "stage-close-governance-authority.v0",
        "binding_digest",
    ),
    StageCloseGovernanceDecision: _CodecSpec(
        "stage-close-governance-decision.v1",
        "stage-close-governance-decision.v0",
        "decision_digest",
    ),
    StageCloseRecoveryDecision: _CodecSpec(
        "stage-close-recovery-decision.v1",
        "stage-close-recovery-decision.v0",
        "decision_digest",
    ),
}


def decode_transaction_artifact(
    model_type: type[_ModelT],
    payload: dict[str, object],
) -> _ModelT:
    spec = _SPECS.get(model_type)
    if spec is None:
        raise TypeError(f"unsupported transaction artifact: {model_type.__name__}")
    version = str(payload.get("schema_version", ""))
    if version == spec.current_version:
        return model_type.model_validate(payload)
    if version != spec.previous_version:
        raise ValueError(
            f"unknown transaction artifact schema: {model_type.__name__}/{version}"
        )
    return cast(_ModelT, _migrate_previous(model_type, payload, spec))


def _migrate_previous(
    model_type: type[BaseModel],
    payload: dict[str, object],
    spec: _CodecSpec,
) -> BaseModel:
    source_digest = str(payload.get(spec.digest_field, ""))
    protected = {
        key: value for key, value in payload.items() if key != spec.digest_field
    }
    if (
        not source_digest
        or canonical_digest(protected, CanonicalizationPolicy()) != source_digest
    ):
        raise ValueError("previous transaction artifact digest is invalid")
    extensions = payload.get("extensions", {})
    if not isinstance(extensions, dict):
        raise ValueError("previous transaction artifact extensions are invalid")
    migrated = {
        **payload,
        "schema_version": spec.current_version,
        "canonicalization_version": "canonical-json.v1",
        "compatibility_mode": "read-only-legacy",
        "extensions": {
            **extensions,
            "source_schema_version": spec.previous_version,
            "source_digest": source_digest,
        },
    }
    if model_type is CloseConsumptionClaim:
        migrated.setdefault("session_start_revision", 1)
    if model_type is StageCloseRecoveryDecision:
        migrated.setdefault("aborted_session_revision", 1)
        migrated.setdefault(
            "aborted_session_digest",
            f"legacy:{source_digest}",
        )
    if model_type is RepoWriteLeaseEvent:
        lease = payload.get("lease")
        if not isinstance(lease, dict):
            raise ValueError("previous repo lease event has no lease")
        migrated["lease"] = decode_transaction_artifact(
            RepoWriteLease,
            lease,
        ).model_dump(mode="json")
    return model_type.model_validate(migrated)
