"""版本化 Provider Authority 注册表与发布证明边界。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ai_sdlc.core.stage_review.binding_models import (
    BindingAuthoritySnapshot,
    ProviderBindingDescriptor,
)
from ai_sdlc.core.stage_review.codex_provider_authority import (
    _codex_provider_descriptors,
)
from ai_sdlc.core.stage_review.codex_trusted_releases import (
    _trusted_published_codex_release_digests,
)
from ai_sdlc.core.stage_review.panel_plan_models import (
    ReviewerPanelPlan,
    ReviewerSlot,
)

_DescriptorCatalogBuilder = Callable[
    [ReviewerSlot, str],
    tuple[ProviderBindingDescriptor, ...],
]


@dataclass(frozen=True, slots=True)
class _ProviderAuthorityRegistration:
    attestor_id: str
    attestor_version: str
    evidence_digests: tuple[str, ...]
    descriptor_catalog_builder: _DescriptorCatalogBuilder


def _validate_registered_provider_authority(
    authority: BindingAuthoritySnapshot,
    plan: ReviewerPanelPlan,
) -> None:
    registrations = {
        (item.attestor_id, item.attestor_version): item
        for item in _provider_authority_registrations()
    }
    registration = registrations.get(
        (authority.attestor_id, authority.attestor_version)
    )
    if registration is None:
        raise ValueError("provider authority attestor is not registered")
    if authority.attestation_evidence_digest not in registration.evidence_digests:
        raise ValueError("provider authority release evidence is not trusted")
    expected = tuple(
        sorted(
            descriptor.descriptor_digest
            for slot in plan.proposal.required_slots
            for descriptor in registration.descriptor_catalog_builder(
                slot,
                authority.attestation_evidence_digest,
            )
        )
    )
    actual = tuple(
        descriptor.descriptor_digest for descriptor in authority.provider_descriptors
    )
    if actual != expected:
        raise ValueError("provider descriptor catalog is outside registered authority")


def _provider_authority_registrations(
) -> tuple[_ProviderAuthorityRegistration, ...]:
    return (
        _ProviderAuthorityRegistration(
            attestor_id="ai-sdlc.codex-runtime",
            attestor_version="1.0.0",
            evidence_digests=_trusted_published_codex_release_digests(),
            descriptor_catalog_builder=_codex_provider_descriptors,
        ),
    )


__all__: list[str] = []
