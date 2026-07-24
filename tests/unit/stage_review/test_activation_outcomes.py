from __future__ import annotations

import hashlib
from pathlib import Path
from threading import Event, Thread

import pytest

from ai_sdlc.core.stage_review.activation import (
    ActivationSessionObservation,
    ActivationSessionRecord,
    baseline_activation_policy,
)
from ai_sdlc.core.stage_review.activation_outcomes import (
    derive_activation_session_outcomes,
    lock_activation_outcome_sources,
)
from ai_sdlc.core.stage_review.finding_models import FindingEvent, FindingScope
from ai_sdlc.core.stage_review.finding_store import FindingEventStore
from ai_sdlc.core.stage_review.optimization.attribution import (
    AttributionPolicy,
    FindingAttribution,
    ProductDefectSignal,
)
from ai_sdlc.core.stage_review.optimization.attribution_store import (
    FindingAttributionStore,
)


def test_late_critical_without_terminal_attribution_is_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _record()
    late = _event("late", event_type="discovered", late=True)
    monkeypatch.setattr(
        FindingEventStore,
        "load_events",
        lambda *_args: (late,),
    )

    monkeypatch.setattr(
        FindingAttributionStore,
        "attributions",
        lambda *_args: (),
    )
    monkeypatch.setattr(
        FindingAttributionStore,
        "product_defect_signals",
        lambda *_args: (),
    )

    outcome = derive_activation_session_outcomes(
        tmp_path,
        (record,),
        policy=baseline_activation_policy(),
        assessed_at="2026-07-16T00:00:00+00:00",
    )[0]

    assert outcome.status == "incomplete"
    assert outcome.had_late_critical is True
    assert outcome.had_escape is False
    assert outcome.reason_codes == ("attribution-terminal-decision-missing",)


def test_terminal_runtime_facts_drive_bernoulli_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _record()
    late = _event("late", event_type="discovered", late=True)
    reversal = _event("reversal", event_type="verification_failed")
    attribution = FindingAttribution.model_construct(
        finding_event_digest=late.event_digest,
        status="candidate_authorized",
        primary_cause_id="reviewer_execution_miss",
        policy_digest=AttributionPolicy.baseline().policy_digest,
        attribution_digest=_digest("attribution"),
    )
    monkeypatch.setattr(
        FindingEventStore,
        "load_events",
        lambda *_args: (late, reversal),
    )
    monkeypatch.setattr(
        FindingAttributionStore,
        "attributions",
        lambda *_args: (attribution,),
    )
    monkeypatch.setattr(
        FindingAttributionStore,
        "product_defect_signals",
        lambda *_args: (),
    )

    outcome = derive_activation_session_outcomes(
        tmp_path,
        (record,),
        policy=baseline_activation_policy(),
        assessed_at="2026-07-16T00:00:00+00:00",
    )[0]

    assert outcome.status == "complete"
    assert outcome.had_reversal is True
    assert outcome.had_late_critical is True
    assert outcome.had_escape is False
    assert outcome.finding_chain_head_digest == reversal.event_digest
    assert outcome.attribution_decision_digests == (
        attribution.attribution_digest,
    )


def test_product_defect_signal_is_required_for_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _record()
    late = _event("late-product-defect", event_type="discovered", late=True)
    attribution = FindingAttribution.model_construct(
        project_id=record.project_id,
        session_id=record.observation.session_id,
        finding_key="finding.product-defect",
        finding_event_digest=late.event_digest,
        status="product_defect",
        primary_cause_id="deterministic_gate_gap",
        policy_digest=AttributionPolicy.baseline().policy_digest,
        attribution_digest=_digest("product-defect-attribution"),
    )
    signal = ProductDefectSignal.model_construct(
        project_id=record.project_id,
        session_id=record.observation.session_id,
        finding_key=attribution.finding_key,
        cause_id=attribution.primary_cause_id,
        attribution_digest=attribution.attribution_digest,
        signal_digest=_digest("product-defect-signal"),
    )
    monkeypatch.setattr(
        FindingEventStore,
        "load_events",
        lambda *_args: (late,),
    )
    monkeypatch.setattr(
        FindingAttributionStore,
        "attributions",
        lambda *_args: (attribution,),
    )
    monkeypatch.setattr(
        FindingAttributionStore,
        "product_defect_signals",
        lambda *_args: (signal,),
    )

    outcome = derive_activation_session_outcomes(
        tmp_path,
        (record,),
        policy=baseline_activation_policy(),
        assessed_at="2026-07-16T00:00:00+00:00",
    )[0]

    assert outcome.status == "complete"
    assert outcome.had_late_critical is True
    assert outcome.had_escape is True
    assert outcome.product_defect_signal_digests == (signal.signal_digest,)

    monkeypatch.setattr(
        FindingAttributionStore,
        "product_defect_signals",
        lambda *_args: (),
    )
    missing = derive_activation_session_outcomes(
        tmp_path,
        (record,),
        policy=baseline_activation_policy(),
        assessed_at="2026-07-16T00:00:00+00:00",
    )[0]

    assert missing.status == "incomplete"
    assert "product-defect-signal-missing" in missing.reason_codes
    assert missing.had_escape is False


def test_outcome_source_lock_blocks_attribution_mutation(tmp_path: Path) -> None:
    record = _record()
    attribution_store = FindingAttributionStore(
        tmp_path,
        project_id=record.project_id,
        lock_timeout_seconds=1,
    )
    entered = Event()

    def write_attribution() -> None:
        with attribution_store.lock():
            entered.set()

    with lock_activation_outcome_sources(tmp_path, (record,)):
        writer = Thread(target=write_attribution)
        writer.start()
        assert entered.wait(0.1) is False

    writer.join(timeout=1)
    assert writer.is_alive() is False
    assert entered.is_set()


def _record() -> ActivationSessionRecord:
    scope = FindingScope(
        project_id="project.activation-outcomes",
        work_item_id="WI-1",
        stage_instance_id="implementation.1",
        session_id="session.1",
    )
    return ActivationSessionRecord(
        record_id="record.1",
        project_id=scope.project_id,
        close_proof_kind="shadow-attestation",
        close_proof_id="attestation.1",
        close_proof_digest=_digest("attestation"),
        candidate_manifest_digest=_digest("candidate"),
        panel_plan_digest=_digest("panel"),
        review_session_digest=_digest("review-session"),
        review_completion_digest=_digest("review-completion"),
        scope=scope,
        observation=ActivationSessionObservation(
            session_id=scope.session_id,
            stage_key="implementation",
            risk_level="low",
            mode="shadow",
            completed_at="2026-07-01T00:00:00+00:00",
        ),
    )


def _event(
    identity: str,
    *,
    event_type: str,
    late: bool = False,
) -> FindingEvent:
    return FindingEvent.model_construct(
        event_digest=_digest(identity),
        event_type=event_type,
        late_critical_finding=object() if late else None,
        severity="P1" if late else None,
    )


def _digest(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"
