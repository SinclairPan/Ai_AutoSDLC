from __future__ import annotations

import pytest

from ai_sdlc.core.stage_review.optimization.attribution import (
    AttributionCausalFacts,
    AttributionEvidence,
    AttributionPolicy,
)
from ai_sdlc.core.stage_review.optimization.attribution import (
    _attribute_finding as attribute_finding,
)


@pytest.mark.parametrize(
    ("cause", "expected_domain", "product_defect"),
    (
        ("panel_selection_gap", "selection", False),
        ("reviewer_execution_miss", "role_profile", False),
        ("evidence_visibility_gap", "", True),
        ("risk_classification_gap", "", True),
        ("role_contract_gap", "role_profile", False),
        ("deterministic_gate_gap", "", True),
        ("provider_quality_gap", "binding", False),
    ),
)
def test_late_critical_cause_maps_to_one_governed_domain(
    cause: str,
    expected_domain: str,
    product_defect: bool,
) -> None:
    decision = attribute_finding(
        _evidence((cause,)),
        AttributionPolicy.baseline(),
    )

    assert decision.attribution.candidate_domain == expected_domain
    assert (decision.product_defect_signal is not None) is product_defect
    assert decision.attribution.status == (
        "product_defect" if product_defect else "candidate_authorized"
    )


def test_conflicting_causes_cannot_generate_a_candidate() -> None:
    decision = attribute_finding(
        _evidence(("panel_selection_gap", "provider_quality_gap")),
        AttributionPolicy.baseline(),
    )

    assert decision.attribution.status == "no_change"
    assert decision.attribution.reason_code == "attribution_conflict"
    assert decision.attribution.candidate_domain == ""
    assert decision.product_defect_signal is None


def test_low_confidence_cause_cannot_generate_a_candidate() -> None:
    decision = attribute_finding(
        _evidence(("reviewer_execution_miss",), confidence=0.79),
        AttributionPolicy.baseline(),
    )

    assert decision.attribution.status == "no_change"
    assert decision.attribution.reason_code == "attribution_confidence_insufficient"


def test_non_late_critical_finding_cannot_drive_offline_policy() -> None:
    evidence = _evidence(("panel_selection_gap",)).model_copy(
        update={
            "late_critical_finding": False,
            "evidence_digest": "",
            "attribution_input_digest": "",
        }
    )

    decision = attribute_finding(evidence, AttributionPolicy.baseline())

    assert decision.attribution.status == "no_change"
    assert decision.attribution.reason_code == "critical_fact_not_confirmed"


def test_cause_cannot_be_self_reported_without_matching_causal_facts() -> None:
    payload = _evidence(("panel_selection_gap",)).model_dump(mode="json")
    payload.update(causal_facts={}, attribution_input_digest="")
    with pytest.raises(ValueError, match="not derived"):
        AttributionEvidence.model_validate(payload)


def _evidence(
    causes: tuple[str, ...],
    *,
    confidence: float = 1.0,
) -> AttributionEvidence:
    fact_fields = {
        "panel_selection_gap": "required_capability_missing_from_panel",
        "reviewer_execution_miss": "reviewer_completed_with_visible_evidence",
        "evidence_visibility_gap": "evidence_not_visible_to_initial_cohort",
        "risk_classification_gap": "risk_profile_omitted_required_capability",
        "role_contract_gap": "role_contract_omitted_required_capability",
        "deterministic_gate_gap": "deterministic_gate_omitted_or_failed",
        "provider_quality_gap": "provider_failure_confirmed",
    }
    return AttributionEvidence(
        project_id="project.optimization",
        session_id="session.1",
        finding_key="finding.1",
        finding_event_digest="sha256:finding-event.1",
        evidence_digest="sha256:late-critical-evidence.1",
        original_candidate_digest="sha256:candidate.original",
        discovery_candidate_digest="sha256:candidate.discovery",
        initial_cohort_id="cohort.initial",
        discovery_cohort_id="cohort.discovery",
        capability_coverage_digest="sha256:coverage",
        capability_id="capability.security",
        role_profile_id="role.security",
        provider_binding_digest="sha256:provider-binding",
        attribution_engine_version="1.0.0",
        causal_facts=AttributionCausalFacts(
            **{fact_fields[cause]: True for cause in causes}
        ),
        confirmed_cause_ids=causes,
        confidence=confidence,
        late_critical_finding=True,
        reviewer_coverage_leak=True,
        observed_at="2026-07-22T12:00:00Z",
    )
