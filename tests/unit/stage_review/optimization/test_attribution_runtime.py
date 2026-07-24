from __future__ import annotations

from pathlib import Path
from threading import Event, Thread

import pytest
from tests.unit.stage_review.test_findings import (
    SCOPE,
    _append,
    _identity,
    _initialize,
    _service,
)

from ai_sdlc.core.stage_review.finding_models import FindingEvent
from ai_sdlc.core.stage_review.optimization.attribution import (
    AttributionCausalFacts,
    AttributionEvidence,
)
from ai_sdlc.core.stage_review.optimization.attribution_runtime import (
    FindingAttributionRecorder,
)
from ai_sdlc.core.stage_review.optimization.attribution_store import (
    FindingAttributionStore,
)


def test_late_critical_event_persists_unclassifiable_attribution(
    tmp_path: Path,
) -> None:
    recorder = FindingAttributionRecorder(tmp_path, project_id=SCOPE.project_id)
    service = _service(tmp_path, event_observer=recorder.record)
    key = _initialize(service)

    result = _append(
        service,
        key,
        "discovered",
        actor_id="reviewer.security",
        slot_id="slot.security",
        capability_id="security",
        finding_key=None,
        identity=_identity(failure_signature="late-attribution"),
        severity="P1",
        category="security",
        late_origin="late_confirmed_p0_p1",
        evidence_bundle_digest="sha256:evidence.late-confirmed-attribution",
    )

    store = FindingAttributionStore(tmp_path, project_id=SCOPE.project_id)
    attributions = store.attributions()
    assert result.event is not None
    assert len(attributions) == 1
    assert attributions[0].status == "no_change"
    assert attributions[0].reason_code == "attribution_unclassifiable"
    assert store.evidences()[0].finding_event_digest == result.event.event_digest


def test_confirmed_cause_can_authorize_one_candidate_domain(tmp_path: Path) -> None:
    recorder = FindingAttributionRecorder(tmp_path, project_id=SCOPE.project_id)
    service = _service(tmp_path, event_observer=recorder.record)
    key = _initialize(service)
    event = _append(
        service,
        key,
        "discovered",
        actor_id="reviewer.security",
        slot_id="slot.security",
        capability_id="security",
        finding_key=None,
        identity=_identity(failure_signature="confirmed-panel-gap"),
        severity="P1",
        category="security",
        late_origin="late_confirmed_p0_p1",
        evidence_bundle_digest="sha256:evidence.late-confirmed-panel-gap",
    ).event
    assert event is not None
    assert event.attribution_input is not None
    store = FindingAttributionStore(tmp_path, project_id=SCOPE.project_id)
    evidence = AttributionEvidence(
        project_id=SCOPE.project_id,
        session_id=event.scope.session_id,
        finding_key=str(event.finding_key),
        finding_event_digest=event.event_digest,
        evidence_digest="sha256:causal-evidence.1",
        original_candidate_digest=str(event.attribution_input.original_candidate_digest),
        discovery_candidate_digest=str(event.attribution_input.discovery_candidate_digest),
        initial_cohort_id=str(event.attribution_input.initial_cohort_id),
        discovery_cohort_id=str(event.attribution_input.discovery_cohort_id),
        capability_coverage_digest=str(event.attribution_input.capability_coverage_digest),
        capability_id=str(event.attribution_input.capability_id),
        role_profile_id=str(event.attribution_input.role_profile_id),
        provider_binding_digest=str(event.attribution_input.provider_binding_digest),
        attribution_engine_version="1.0.0",
        causal_facts=AttributionCausalFacts(
            required_capability_missing_from_panel=True
        ),
        confirmed_cause_ids=("panel_selection_gap",),
        confidence=1,
        late_critical_finding=True,
        reviewer_coverage_leak=True,
        observed_at="2026-07-22T12:00:00Z",
    )

    first = store.record(evidence, source_event=event)
    repeated = store.record(evidence, source_event=event)

    assert repeated == first
    assert first.attribution.status == "candidate_authorized"
    assert first.attribution.candidate_domain == "selection"
    assert first.attribution in store.attributions()


def test_attribution_writer_waits_for_activation_source_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FindingAttributionStore(
        tmp_path,
        project_id=SCOPE.project_id,
        lock_timeout_seconds=1,
    )
    entered = Event()
    result = object()
    monkeypatch.setattr(
        store,
        "_record_locked",
        lambda *_args, **_kwargs: entered.set() or result,
    )

    with store.lock():
        writer = Thread(
            target=lambda: store.record(
                AttributionEvidence.model_construct(),
                source_event=FindingEvent.model_construct(),
            )
        )
        writer.start()
        assert entered.wait(0.1) is False

    writer.join(timeout=1)
    assert writer.is_alive() is False
    assert entered.is_set()
