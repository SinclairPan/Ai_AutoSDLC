"""把可信迟到关键 Finding 自动转换为保守的离线归因事实。"""

from __future__ import annotations

from pathlib import Path

from ai_sdlc.core.stage_review.artifacts import (
    read_json_object,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.finding_artifact_codec import decode_finding_event
from ai_sdlc.core.stage_review.finding_models import FindingEvent
from ai_sdlc.core.stage_review.optimization.attribution import (
    AttributionCausalFacts,
    AttributionDecision,
    AttributionEvidence,
)
from ai_sdlc.core.stage_review.optimization.attribution import (
    _causes_from_facts as causes_from_facts,
)
from ai_sdlc.core.stage_review.optimization.attribution_store import (
    FindingAttributionStore,
)


class FindingAttributionRecorder:
    def __init__(self, root: Path, *, project_id: str) -> None:
        shared = resolve_canonical_shared_state(root, project_id)
        self.finding_root = shared / "finding-ledgers" / "sessions"
        self.store = FindingAttributionStore(root, project_id=project_id)

    def recover(self) -> None:
        if not self.finding_root.is_dir():
            return
        for path in sorted(self.finding_root.glob("*/*/*/events/*.json")):
            self.record(decode_finding_event(read_json_object(path)))

    def record(self, event: FindingEvent) -> AttributionDecision | None:
        if event.late_critical_finding is None:
            return None
        source = event.attribution_input
        if source is None:
            raise ValueError("late critical finding lacks attribution input")
        facts = _deterministic_facts(event)
        evidence = AttributionEvidence(
            project_id=event.scope.project_id,
            session_id=event.scope.session_id,
            finding_key=event.late_critical_finding.finding_key,
            finding_event_digest=event.event_digest,
            evidence_digest=(
                event.evidence_descriptor_digest or event.evidence_bundle_digest
            ),
            original_candidate_digest=source.original_candidate_digest,
            discovery_candidate_digest=source.discovery_candidate_digest,
            initial_cohort_id=source.initial_cohort_id,
            discovery_cohort_id=source.discovery_cohort_id,
            capability_coverage_digest=source.capability_coverage_digest,
            capability_id=source.capability_id,
            role_profile_id=source.role_profile_id,
            provider_binding_digest=source.provider_binding_digest,
            attribution_engine_version=source.engine_version,
            causal_facts=facts,
            confirmed_cause_ids=causes_from_facts(facts),
            confidence=1,
            late_critical_finding=True,
            reviewer_coverage_leak=event.reviewer_coverage_leak is not None,
            observed_at=event.evidence_first_visible_at,
        )
        return self.store.record(evidence, source_event=event)


def _deterministic_facts(event: FindingEvent) -> AttributionCausalFacts:
    return AttributionCausalFacts(
        deterministic_gate_omitted_or_failed=(
            event.late_origin == "protocol_or_required_test_failure"
        ),
        evidence_not_visible_to_initial_cohort=(
            event.evidence_initial_visibility == "not_visible"
        ),
    )
