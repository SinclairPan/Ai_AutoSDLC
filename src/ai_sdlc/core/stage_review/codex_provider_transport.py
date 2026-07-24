"""Codex Provider 的可信受控传输组合。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from ai_sdlc.core.stage_review.binding_models import ProviderBindingDescriptor
from ai_sdlc.core.stage_review.codex_provider_execution import (
    build_codex_execution_identity,
)
from ai_sdlc.core.stage_review.codex_review_broker import CodexReviewBroker
from ai_sdlc.core.stage_review.isolation_backend_identity import (
    TrustedBackendReleaseManifest,
)
from ai_sdlc.core.stage_review.provider_transport import TrustedProviderTransport
from ai_sdlc.core.stage_review.provider_transport_models import (
    _build_transport_authority as build_transport_authority,
)
from ai_sdlc.core.stage_review.provider_transport_models import (
    _build_transport_contract as build_transport_contract,
)
from ai_sdlc.core.stage_review.provider_transport_trust import (
    _reviewer_transport_authority,
    _reviewer_transport_contract,
)
from ai_sdlc.core.stage_review.provider_usage_models import (
    ProviderUsageEstimatePolicy,
)


def build_codex_review_transport(
    root: Path,
    project_id: str,
    shared: Path,
    executable: str,
    release: TrustedBackendReleaseManifest,
    *,
    estimate_policy: ProviderUsageEstimatePolicy | None = None,
    execution_scope: Literal["optimization_shadow", "reviewer_binding"],
    descriptor: ProviderBindingDescriptor | None = None,
) -> TrustedProviderTransport:
    identity = build_codex_execution_identity(execution_scope, descriptor)
    release_digest = str(getattr(release, "manifest_digest", ""))
    if descriptor is not None:
        if descriptor.provider_policy_evidence_digest != release_digest:
            raise ValueError("Codex transport release authority diverged")
        authority = _reviewer_transport_authority(descriptor)
        contract = _reviewer_transport_contract(descriptor)
    else:
        authority = build_transport_authority(
            contract_id="transport.codex-review",
            contract_version="1.0.0",
            endpoint_id="ipc://codex-review/provider",
            workflow_ref="workflow:stage-review",
            evidence_digest=release_digest,
        )
        contract = build_transport_contract(
            contract_id=authority.contract_id,
            contract_version=authority.contract_version,
            endpoint_id=authority.endpoint_id,
            authority=authority,
            execution_identity=identity,
        )
    broker = _codex_broker(shared, executable, estimate_policy)
    return TrustedProviderTransport(
        root, contract, project_id=project_id, broker=broker, authority=authority
    )


def _codex_broker(
    shared: Path,
    executable: str,
    estimate_policy: ProviderUsageEstimatePolicy | None,
) -> CodexReviewBroker:
    options = {"codex_executable": executable}
    if estimate_policy is not None:
        options["estimate_policy"] = estimate_policy
    return CodexReviewBroker(shared / "codex-review-broker", **options)


__all__ = ["build_codex_review_transport"]
