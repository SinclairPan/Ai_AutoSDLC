"""Provider availability attestation 构建器。"""

from __future__ import annotations

from ai_sdlc.core.stage_review.binding_availability_models import (
    ProviderAvailabilityAttestation,
)
from ai_sdlc.core.stage_review.binding_digests import provider_availability_digest
from ai_sdlc.core.stage_review.resource_builders import stable_id


def build_provider_availability_attestation(
    *,
    plan_digest: str,
    previous_binding_set_digest: str,
    unavailable_provider_ids: tuple[str, ...],
    source_journal_event_digests: tuple[str, ...],
    attestor_id: str,
    attestor_version: str,
    evidence_digest: str,
    issued_at: str,
    expires_at: str,
) -> ProviderAvailabilityAttestation:
    values = {
        "attestation_id": stable_id(
            "provider-availability",
            plan_digest,
            previous_binding_set_digest,
            evidence_digest,
        ),
        "plan_digest": plan_digest,
        "previous_binding_set_digest": previous_binding_set_digest,
        "unavailable_provider_ids": tuple(sorted(set(unavailable_provider_ids))),
        "source_journal_event_digests": tuple(
            sorted(set(source_journal_event_digests))
        ),
        "attestor_id": attestor_id,
        "attestor_version": attestor_version,
        "evidence_digest": evidence_digest,
        "issued_at": issued_at,
        "expires_at": expires_at,
    }
    draft = ProviderAvailabilityAttestation.model_construct(
        **values,  # type: ignore[arg-type]
        attestation_digest="",
    )
    return ProviderAvailabilityAttestation.model_validate(
        {
            **values,
            "attestation_digest": provider_availability_digest(draft),
        }
    )
