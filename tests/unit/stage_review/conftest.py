from __future__ import annotations

import pytest

from ai_sdlc.core.stage_review import (
    binding_lineage,
    provider_authority_registry,
    session_authority,
)


@pytest.fixture
def allow_synthetic_binding_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registered = provider_authority_registry._validate_registered_provider_authority

    def validate(authority, plan) -> None:
        if authority.attestor_id == "binding-authority.test":
            return
        registered(authority, plan)

    monkeypatch.setattr(
        binding_lineage,
        "_validate_registered_provider_authority",
        validate,
    )


@pytest.fixture
def allow_synthetic_session_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registered = session_authority._validate_binding_authority_snapshot

    def validate(plan, authority, binding_set, assignments) -> None:
        if all(
            item.descriptor_id.startswith("descriptor.provider.openai-codex.")
            for item in authority.provider_descriptors
        ):
            return
        registered(plan, authority, binding_set, assignments)

    monkeypatch.setattr(
        session_authority,
        "_validate_binding_authority_snapshot",
        validate,
    )
