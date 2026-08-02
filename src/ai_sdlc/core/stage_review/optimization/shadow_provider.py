"""使用受控 Codex 传输执行独立 Prospective Shadow Review。"""

from __future__ import annotations

import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.optimization.models import OptimizationCandidate
from ai_sdlc.core.stage_review.optimization.shadow import (
    OptimizationShadowAssignment,
    ShadowProviderSpec,
)
from ai_sdlc.core.stage_review.provider_execution_evidence import (
    build_provider_execution_outcome,
)
from ai_sdlc.core.stage_review.provider_execution_registry import (
    FrozenProviderExecutionRegistry,
    ProviderExecutionUnavailableError,
)
from ai_sdlc.core.stage_review.provider_journal import (
    ProviderInvocationRequest,
    ProviderQueryResult,
    ProviderRecoveryCapabilities,
    ProviderSubmission,
    build_provider_submission,
)
from ai_sdlc.core.stage_review.provider_journal_driver import ProviderDriverRefused
from ai_sdlc.core.stage_review.provider_transport_models import (
    ProviderTransportEnvelope,
    ProviderTransportExecutionError,
    provider_payload_digest,
)
from ai_sdlc.core.stage_review.provider_usage_models import (
    accounted_usage_from_payload,
)
from ai_sdlc.core.stage_review.remote_review_models import (
    RemoteReviewOutput,
    RemoteReviewProviderResponse,
)
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts
from ai_sdlc.core.stage_review.review_input_packet import (
    ReviewInputPacket,
    ReviewInputPacketSet,
)

_PROVIDER_ID = "provider.openai-codex.optimization-shadow"
_CAPABILITIES = ProviderRecoveryCapabilities(
    idempotency_support=False,
    invocation_query_support=False,
    cost_metering_support=False,
)
_ANTICIPATED_USAGE = ResourceAmounts(
    provider_calls=1,
    review_passes=1,
    tokens=50_000,
    cost=1,
    active_wall_clock=150,
    parallelism=1,
)
_MAX_PAYLOAD_BYTES = 180_000


class OptimizationShadowProviderOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["optimization-shadow-provider-output.v1"] = (
        "optimization-shadow-provider-output.v1"
    )
    review: RemoteReviewOutput

    @model_validator(mode="after")
    def _verify_output(self) -> Self:
        if not self.review.evidence_digests:
            raise ValueError("shadow review evidence is missing")
        return self


class CodexOptimizationShadowDriver:
    provider_id = _PROVIDER_ID
    capabilities = _CAPABILITIES

    def __init__(
        self,
        *,
        payload: dict[str, object],
        executions: FrozenProviderExecutionRegistry,
    ) -> None:
        self.payload = payload
        self.executions = executions

    def invoke(self, request: ProviderInvocationRequest) -> ProviderSubmission:
        if request.request_digest != provider_payload_digest(self.payload):
            raise ProviderDriverRefused("shadow provider request payload diverged")
        try:
            execution = self.executions.resolve_shadow(request)
        except ProviderExecutionUnavailableError as exc:
            raise ProviderDriverRefused(str(exc)) from exc
        try:
            exchange = execution.transport.exchange(
                self._envelope(request, execution.identity.identity_digest)
            )
        except ProviderTransportExecutionError as exc:
            raise ProviderDriverRefused(
                str(exc),
                outcome=build_provider_execution_outcome(exc.accounted_usage),
            ) from exc
        usage = accounted_usage_from_payload(exchange.response.get("accounted_usage"))
        outcome = build_provider_execution_outcome(
            usage,
            egress_receipt_digests=(exchange.receipt.receipt_digest,),
        )
        try:
            response = RemoteReviewProviderResponse.model_validate(exchange.response)
            output = OptimizationShadowProviderOutput(review=response.review)
        except ValueError as exc:
            raise ProviderDriverRefused(
                "shadow provider output is invalid",
                outcome=outcome,
            ) from exc
        return build_provider_submission(
            request,
            provider_call_id=response.provider_call_id,
            output_payload=output.model_dump(mode="json"),
            accounted_usage=response.accounted_usage,
            egress_receipt_digests=(exchange.receipt.receipt_digest,),
        )

    def query(self, request: ProviderInvocationRequest) -> ProviderQueryResult:
        del request
        raise ProviderDriverRefused("shadow provider invocation is not queryable")

    def _envelope(
        self,
        request: ProviderInvocationRequest,
        execution_identity_digest: str,
    ) -> ProviderTransportEnvelope:
        return ProviderTransportEnvelope(
            invocation_id=request.invocation_id,
            assignment_digest=request.assignment_digest,
            provider_id=request.provider_id,
            execution_identity_digest=execution_identity_digest,
            request_digest=request.request_digest,
            turn_index=1,
            idempotency_key=request.idempotency_key,
            credential_view_digest=canonical_digest(
                {"credential_view": "ephemeral-codex-shadow"},
                CanonicalizationPolicy(),
            ),
            backend_epoch="codex-shadow.v1",
            active_wall_clock_limit=request.anticipated_usage.active_wall_clock,
            payload=self.payload,
        )


def build_shadow_provider_payload(
    assignment: OptimizationShadowAssignment,
    candidate: OptimizationCandidate,
    packet_set: ReviewInputPacketSet,
    packets: tuple[ReviewInputPacket, ...],
) -> dict[str, object] | None:
    if not packets or any(
        item.candidate_manifest_digest != packet_set.candidate_manifest_digest
        or item.source_snapshot_digest != packet_set.source_snapshot_digest
        for item in packets
    ):
        return None
    payload: dict[str, object] = {
        "schema": "optimization-shadow-review-provider-request.v1",
        "assignment": assignment.model_dump(mode="json"),
        "candidate_policy": candidate.model_dump(mode="json"),
        "packet_set_digest": packet_set.packet_set_digest,
        "review_contexts": [_review_context(item) for item in packets],
        "changes": [item.model_dump(mode="json") for item in packets[0].changes],
        "instructions": (
            "Evaluate the challenger policy against only this frozen candidate. "
            "Report concrete findings; do not infer authority or mutate inputs."
        ),
        "required_response_schema": "remote-review.v1",
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    return payload if len(encoded) <= _MAX_PAYLOAD_BYTES else None


def shadow_provider_spec(payload: dict[str, object]) -> ShadowProviderSpec:
    return ShadowProviderSpec(
        provider_id=_PROVIDER_ID,
        request_digest=provider_payload_digest(payload),
        anticipated_usage=_ANTICIPATED_USAGE,
        capabilities=_CAPABILITIES,
    )


def validate_shadow_provider_output(submission: ProviderSubmission) -> str:
    output = OptimizationShadowProviderOutput.model_validate(submission.output_payload)
    return canonical_digest(output, CanonicalizationPolicy())


def _review_context(packet: ReviewInputPacket) -> dict[str, object]:
    return {
        "slot_id": packet.slot_id,
        "role_profile_id": packet.role_profile_id,
        "capability_ids": packet.capability_ids,
        "blocking_authorities": packet.blocking_authorities,
        "primary_dimensions": packet.primary_dimensions,
        "prompt_template_digest": packet.prompt_template_digest,
    }


__all__ = [
    "CodexOptimizationShadowDriver",
    "OptimizationShadowProviderOutput",
    "build_shadow_provider_payload",
    "shadow_provider_spec",
    "validate_shadow_provider_output",
]
