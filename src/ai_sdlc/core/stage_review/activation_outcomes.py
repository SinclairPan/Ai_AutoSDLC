"""从可信 Finding 与 Attribution 事实重建激活会话结果。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import timedelta
from pathlib import Path

from ai_sdlc.core.stage_review.activation_fence import (
    activation_safety_mutation_fence,
)
from ai_sdlc.core.stage_review.activation_models import (
    ActivationSafetyRecoverySample,
    ActivationSessionObservation,
    ActivationSessionOutcome,
    ActivationSessionRecord,
    StageGateActivationPolicy,
)
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.finding_models import FindingEvent
from ai_sdlc.core.stage_review.finding_store import FindingEventStore
from ai_sdlc.core.stage_review.optimization.attribution import (
    FindingAttribution,
    ProductDefectSignal,
)
from ai_sdlc.core.stage_review.optimization.attribution_runtime import (
    FindingAttributionRecorder,
)
from ai_sdlc.core.stage_review.optimization.attribution_store import (
    FindingAttributionStore,
)
from ai_sdlc.core.stage_review.resource_builders import parse_utc

_DIGEST_POLICY = CanonicalizationPolicy()
_TERMINAL_ATTRIBUTION_STATUSES = {"candidate_authorized", "product_defect"}


def mature_activation_session_records(
    records: tuple[ActivationSessionRecord, ...],
    *,
    policy: StageGateActivationPolicy,
    assessed_at: str,
) -> tuple[ActivationSessionRecord, ...]:
    cutoff = parse_utc(assessed_at)
    maturity = timedelta(days=policy.outcome_maturity_window_days)
    return tuple(
        item
        for item in records
        if parse_utc(item.observation.completed_at) + maturity <= cutoff
    )


@contextmanager
def lock_activation_outcome_sources(
    root: Path,
    records: tuple[ActivationSessionRecord, ...],
) -> Iterator[None]:
    if not records:
        yield
        return
    project_ids = {item.project_id for item in records}
    if len(project_ids) != 1:
        raise ValueError("activation outcome records span projects")
    store = FindingEventStore(root, project_id=records[0].project_id)
    attribution_store = FindingAttributionStore(
        root,
        project_id=records[0].project_id,
    )
    scopes = tuple(
        sorted(
            {item.scope for item in records},
            key=lambda item: (
                item.work_item_id,
                item.stage_instance_id,
                item.session_id,
            ),
        )
    )
    with ExitStack() as stack:
        for scope in scopes:
            stack.enter_context(store.lock(scope))
        stack.enter_context(attribution_store.lock())
        yield


def recover_activation_session_attributions(
    root: Path,
    records: tuple[ActivationSessionRecord, ...],
) -> None:
    """按 Finding→Attribution 的固定锁序补齐崩溃后可确定重建的归因。"""

    if not records:
        return
    project_id = records[0].project_id
    if any(item.project_id != project_id for item in records):
        raise ValueError("activation outcome records span projects")
    event_store = FindingEventStore(root, project_id=project_id)
    recorder = FindingAttributionRecorder(root, project_id=project_id)
    scopes = tuple(
        sorted(
            {item.scope for item in records},
            key=lambda item: (
                item.work_item_id,
                item.stage_instance_id,
                item.session_id,
            ),
        )
    )
    with activation_safety_mutation_fence(root, project_id):
        for scope in scopes:
            with event_store.lock(scope):
                for event in _late_critical_events(
                    event_store.load_events(scope)
                ):
                    try:
                        recorder.record(event)
                    except Exception:
                        # 缺失归因会在同一事实快照中转化为 incomplete outcome。
                        continue


def derive_activation_session_outcomes(
    root: Path,
    records: tuple[ActivationSessionRecord, ...],
    *,
    policy: StageGateActivationPolicy,
    assessed_at: str,
) -> tuple[ActivationSessionOutcome, ...]:
    if not records:
        return ()
    project_id = records[0].project_id
    if any(item.project_id != project_id for item in records):
        raise ValueError("activation outcome records span projects")
    event_store = FindingEventStore(root, project_id=project_id)
    attribution_store = FindingAttributionStore(root, project_id=project_id)
    events_by_session: dict[str, tuple[FindingEvent, ...]] = {}
    for record in records:
        events = event_store.load_events(record.scope)
        events_by_session[record.observation.session_id] = events
    attributions = attribution_store.attributions()
    signals = attribution_store.product_defect_signals()
    by_event_lists: dict[str, list[FindingAttribution]] = {}
    for attribution in attributions:
        by_event_lists.setdefault(
            attribution.finding_event_digest,
            [],
        ).append(attribution)
    by_event = {
        event_digest: tuple(items)
        for event_digest, items in by_event_lists.items()
    }
    by_attribution_lists: dict[str, list[ProductDefectSignal]] = {}
    for signal in signals:
        by_attribution_lists.setdefault(signal.attribution_digest, []).append(signal)
    by_attribution = {
        attribution_digest: tuple(items)
        for attribution_digest, items in by_attribution_lists.items()
    }
    return tuple(
        _build_outcome(
            record,
            events_by_session[record.observation.session_id],
            by_event,
            by_attribution,
            policy=policy,
            assessed_at=assessed_at,
        )
        for record in records
    )


def derive_activation_recovery_session_outcomes(
    root: Path,
    samples: tuple[ActivationSafetyRecoverySample, ...],
    *,
    policy: StageGateActivationPolicy,
    assessed_at: str,
) -> tuple[ActivationSessionOutcome, ...]:
    """用恢复样本绑定的权威会话范围重新推导当前成熟结果。"""

    records = activation_recovery_session_records(samples)
    return derive_activation_session_outcomes(
        root,
        records,
        policy=policy,
        assessed_at=assessed_at,
    )


def activation_recovery_session_records(
    samples: tuple[ActivationSafetyRecoverySample, ...],
) -> tuple[ActivationSessionRecord, ...]:
    return tuple(
        ActivationSessionRecord(
            record_id=f"recovery.{item.sample_id}",
            project_id=item.project_id,
            close_proof_kind="shadow-attestation",
            close_proof_id=item.sample_id,
            close_proof_digest=item.review_completion_digest,
            candidate_manifest_digest=item.candidate_manifest_digest,
            panel_plan_digest=item.panel_plan_digest,
            review_session_digest=item.review_session_digest,
            review_completion_digest=item.review_completion_digest,
            scope=item.scope,
            observation=ActivationSessionObservation(
                session_id=item.scope.session_id,
                stage_key=item.stage_key,
                risk_level=item.risk_level,
                mode="shadow",
                completed_at=item.review_completed_at,
            ),
        )
        for item in samples
    )


def _build_outcome(
    record: ActivationSessionRecord,
    events: tuple[FindingEvent, ...],
    by_event: dict[str, tuple[FindingAttribution, ...]],
    by_attribution: dict[str, tuple[ProductDefectSignal, ...]],
    *,
    policy: StageGateActivationPolicy,
    assessed_at: str,
) -> ActivationSessionOutcome:
    late = _late_critical_events(events)
    selected: list[FindingAttribution] = []
    selected_signals: list[ProductDefectSignal] = []
    reasons: set[str] = set()
    for event in late:
        matches = by_event.get(event.event_digest, ())
        if len(matches) != 1:
            reasons.add("attribution-terminal-decision-missing")
            continue
        attribution = matches[0]
        selected.append(attribution)
        if (
            attribution.status not in _TERMINAL_ATTRIBUTION_STATUSES
            or not attribution.primary_cause_id
        ):
            reasons.add("attribution-terminal-decision-incomplete")
            continue
        if attribution.policy_digest != policy.attribution_policy_digest:
            reasons.add("attribution-policy-diverged")
            continue
        signals = by_attribution.get(attribution.attribution_digest, ())
        if attribution.status == "product_defect":
            if len(signals) != 1:
                reasons.add("product-defect-signal-missing")
                continue
            signal = signals[0]
            if (
                signal.project_id != record.project_id
                or signal.session_id != record.observation.session_id
                or signal.finding_key != attribution.finding_key
                or signal.cause_id != attribution.primary_cause_id
            ):
                reasons.add("product-defect-signal-lineage-diverged")
                continue
            selected_signals.append(signal)
        elif signals:
            reasons.add("product-defect-signal-unexpected")
    escape_causes = set(policy.activation_escape_cause_ids)
    finding_digests = tuple(item.event_digest for item in events)
    attribution_digests = tuple(
        sorted(item.attribution_digest for item in selected)
    )
    signal_digests = tuple(
        sorted(item.signal_digest for item in selected_signals)
    )
    observation_cutoff = (
        parse_utc(record.observation.completed_at)
        + timedelta(days=policy.outcome_maturity_window_days)
    ).isoformat()
    return ActivationSessionOutcome(
        session_id=record.observation.session_id,
        session_record_digest=record.record_digest,
        status="incomplete" if reasons else "complete",
        reason_codes=tuple(sorted(reasons)),
        had_reversal=any(
            item.event_type in {"regressed", "verification_failed"} for item in events
        ),
        had_late_critical=bool(late),
        had_escape=any(
            item.status == "product_defect"
            and item.primary_cause_id in escape_causes
            and any(
                signal.attribution_digest == item.attribution_digest
                for signal in selected_signals
            )
            for item in selected
        ),
        finalized_at=assessed_at,
        observation_cutoff=observation_cutoff,
        finding_chain_head_digest=(
            events[-1].event_digest
            if events
            else _fact_set_digest("finding-chain-empty", ())
        ),
        attribution_set_digest=_fact_set_digest(
            "activation-attribution-signal-set",
            (*attribution_digests, *signal_digests),
        ),
        finding_event_digests=finding_digests,
        attribution_decision_digests=attribution_digests,
        product_defect_signal_digests=signal_digests,
    )


def _late_critical_events(
    events: tuple[FindingEvent, ...],
) -> tuple[FindingEvent, ...]:
    return tuple(
        item
        for item in events
        if item.late_critical_finding is not None and item.severity in {"P0", "P1"}
    )


def _fact_set_digest(kind: str, values: tuple[str, ...]) -> str:
    return canonical_digest(
        {"artifact_kind": kind, "digests": values},
        _DIGEST_POLICY,
    )
