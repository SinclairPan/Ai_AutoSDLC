from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.stage_review.test_isolation_runtime_layout import (
    _candidate_authority,
)

from ai_sdlc.core.stage_review.activation_policy_store import current_activation_policy
from ai_sdlc.core.stage_review.review_input_packet import build_review_input_packet
from ai_sdlc.core.stage_review.shadow_planner import (
    _build_shadow_panel_proposal as build_shadow_panel_proposal,
)


def test_review_packet_contains_frozen_before_and_after_source(tmp_path: Path) -> None:
    source, candidate, snapshot = _candidate_authority(tmp_path)
    planned = build_shadow_panel_proposal(
        candidate=candidate,
        activation_policy=current_activation_policy(source),
    )

    packet = build_review_input_packet(
        source,
        candidate=candidate,
        source_snapshot=snapshot,
        slot=planned.resolution.proposal.required_slots[0],  # type: ignore[union-attr]
    )

    assert tuple(item.path for item in packet.changes) == ("candidate.txt",)
    assert packet.changes[0].before.text == "base"
    assert packet.changes[0].after.text == "candidate"


def test_review_packet_rejects_source_changed_after_candidate_freeze(
    tmp_path: Path,
) -> None:
    source, candidate, snapshot = _candidate_authority(tmp_path)
    planned = build_shadow_panel_proposal(
        candidate=candidate,
        activation_policy=current_activation_policy(source),
    )
    (source / "candidate.txt").write_text("changed-again", encoding="utf-8")

    with pytest.raises(ValueError, match="source snapshot is stale"):
        build_review_input_packet(
            source,
            candidate=candidate,
            source_snapshot=snapshot,
            slot=planned.resolution.proposal.required_slots[0],  # type: ignore[union-attr]
        )
