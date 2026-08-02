"""唯一项目级 OfflineOptimizationController。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path

from ai_sdlc.core.stage_review.activation_fence import (
    activation_safety_mutation_fence,
)
from ai_sdlc.core.stage_review.artifacts import SharedStateIntegrityError
from ai_sdlc.core.stage_review.optimization.attribution import AttributionEvidence
from ai_sdlc.core.stage_review.optimization.attribution_runtime import (
    FindingAttributionRecorder,
)
from ai_sdlc.core.stage_review.optimization.attribution_store import (
    FindingAttributionStore,
)
from ai_sdlc.core.stage_review.optimization.controller_models import (
    MaintenanceBudget,
    OptimizationConstitution,
    OptimizationEpoch,
    OptimizationMaintenanceResult,
    OptimizationTriggerEvent,
)
from ai_sdlc.core.stage_review.optimization.controller_store import (
    OptimizationControllerStore,
)
from ai_sdlc.core.stage_review.optimization.maintenance_execution import (
    TERMINAL_EPOCH_STATES,
    OptimizationMaintenanceRunner,
    OptimizationStepExecutor,
)
from ai_sdlc.core.stage_review.optimization.maintenance_execution import (
    _maintenance_result as maintenance_result,
)
from ai_sdlc.core.stage_review.optimization.observations import (
    OptimizationObservationStore,
    OptimizationSessionObservation,
)
from ai_sdlc.core.stage_review.optimization.statistics import (
    statistics_policy_for_digest,
)
from ai_sdlc.core.stage_review.panel_models import ReviewerBudgetPolicy
from ai_sdlc.core.stage_review.provider_journal import ProviderInvocationJournal
from ai_sdlc.core.stage_review.resource_builders import parse_utc, stable_id, utc_iso
from ai_sdlc.core.stage_review.resource_runtime import utc_now
from ai_sdlc.core.stage_review.resources import ResourceGovernor

_CRITICAL_FACTS = frozenset(
    {"late_critical_finding", "reviewer_coverage_leak", "safety_rollback"}
)


class OfflineOptimizationController:
    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        constitution: OptimizationConstitution,
        baseline_snapshot_digest: str,
        epoch_budget_policy: ReviewerBudgetPolicy,
        runtime_bundle_manifests: Mapping[str, str],
        constitution_bundles: Mapping[str, OptimizationConstitution] | None = None,
        epoch_budget_policies: Mapping[str, ReviewerBudgetPolicy] | None = None,
        resource_governor: ResourceGovernor,
        provider_journal: ProviderInvocationJournal,
        step_executor: OptimizationStepExecutor,
        foreground_requested: Callable[[], bool],
        active_snapshot_digest: Callable[[], str] | None = None,
        clock: Callable[[], str] | None = None,
        lock_timeout_seconds: float = 2,
    ) -> None:
        self.root = root
        self.project_id = project_id
        self.constitution = OptimizationConstitution.model_validate(
            constitution.model_dump(mode="json")
        )
        self.constitutions = {
            self.constitution.constitution_digest: self.constitution,
            **dict(constitution_bundles or {}),
        }
        self.runtime_bundle_manifests = dict(runtime_bundle_manifests)
        if set(self.runtime_bundle_manifests) != set(self.constitutions):
            raise ValueError(
                "optimization runtime bundle manifest coverage diverged"
            )
        self.epoch_budget_policies = {
            self.constitution.constitution_digest: _trusted_budget_policy(
                self.constitution, epoch_budget_policy
            ),
            **dict(epoch_budget_policies or {}),
        }
        if set(self.epoch_budget_policies) != set(self.constitutions):
            raise ValueError("optimization epoch budget policy coverage diverged")
        for digest, policy in self.epoch_budget_policies.items():
            _trusted_budget_policy(self.constitutions[digest], policy)
        self.baseline_snapshot_digest = baseline_snapshot_digest
        self.active_snapshot_digest = active_snapshot_digest or (
            lambda: self.baseline_snapshot_digest
        )
        self.clock = clock or (lambda: utc_iso(utc_now(None)))
        self.epoch_budget_policy = _trusted_budget_policy(
            self.constitution, epoch_budget_policy
        )
        self.resource_governor = resource_governor
        self.provider_journal = provider_journal
        self.step_executor = step_executor
        self.foreground_requested = foreground_requested
        self.observations = OptimizationObservationStore(root, project_id=project_id)
        self.attributions = FindingAttributionStore(root, project_id=project_id)
        self.attribution_recovery = FindingAttributionRecorder(
            root, project_id=project_id
        )
        self.store = OptimizationControllerStore(
            root,
            project_id=project_id,
            lock_timeout_seconds=lock_timeout_seconds,
            constitution_bundles={
                **self.constitutions,
            },
            runtime_bundle_manifests=self.runtime_bundle_manifests,
        )
        self._maintenance = OptimizationMaintenanceRunner(
            project_id=project_id,
            store=self.store,
            resource_governor=resource_governor,
            budget_policies=self.epoch_budget_policies,
            step_executor=step_executor,
            foreground_requested=foreground_requested,
        )

    def _record_session_observation(
        self, observation: OptimizationSessionObservation
    ) -> OptimizationTriggerEvent:
        self.observations.append(observation)
        return self.refresh_trigger()

    def refresh_trigger(self) -> OptimizationTriggerEvent:
        """从已恢复的完整观测真值重算一次幂等触发。"""
        with activation_safety_mutation_fence(self.root, self.project_id):
            self.attribution_recovery.recover()
        with self.store.locked():
            event = self._build_trigger_event()
            return self.store.append_trigger(event) if event.triggered else event

    def _trigger_events(self) -> tuple[OptimizationTriggerEvent, ...]:
        return self.store.triggers()

    def advance_optimization(
        self,
        project_id: str,
        budget: MaintenanceBudget,
        *,
        owner_id: str,
        now: datetime | None = None,
    ) -> OptimizationMaintenanceResult:
        self._validate_advance(project_id, budget)
        active = self._active_epoch()
        if active is not None and (
            self.runtime_bundle_manifests.get(active.constitution_digest)
            != active.runtime_bundle_manifest_digest
        ):
            superseded = self._maintenance.supersede_runtime_upgrade(
                active,
                budget,
                owner_id=owner_id,
                now=now,
            )
            if superseded.result_code == "paused":
                return superseded
            self.refresh_trigger()
        trigger = self._latest_trigger()
        if trigger is None:
            return OptimizationMaintenanceResult(result_code="not_ready")
        epoch = self._resolve_epoch(trigger)
        if epoch.state in TERMINAL_EPOCH_STATES:
            return maintenance_result(epoch)
        return self._maintenance.advance(
            epoch,
            budget,
            owner_id=owner_id,
            now=now,
        )

    def _build_trigger_event(self) -> OptimizationTriggerEvent:
        observations = self.observations.read_all()
        created = _created_sessions(observations)
        watermark = max((item.sequence for item in created), default=0)
        attribution_evidence = self.attributions.evidences()
        facts, fact_digests = _critical_facts(observations, attribution_evidence)
        triggered = self._trigger_allowed(
            len(created), watermark, self._new_fact_digests(fact_digests)
        )
        baseline_digest = self.active_snapshot_digest()
        runtime_manifest = self.runtime_bundle_manifests[
            self.constitution.constitution_digest
        ]
        fingerprint = _trigger_fingerprint(
            self.constitution.constitution_digest,
            baseline_digest,
            runtime_manifest,
            watermark,
            len(created),
            facts,
            fact_digests,
        )
        return OptimizationTriggerEvent(
            trigger_id=stable_id("optimization-trigger", self.project_id, fingerprint),
            project_id=self.project_id,
            session_sequence_high_watermark=watermark,
            trigger_fingerprint=fingerprint,
            constitution_digest=self.constitution.constitution_digest,
            baseline_snapshot_digest=baseline_digest,
            candidate_domain_registry_digest=(
                self.constitution.candidate_domain_registry_digest
            ),
            statistics_policy_digest=self.constitution.statistics_policy_digest,
            evaluator_registry_digest=self.constitution.evaluator_registry_digest,
            auto_promotion_policy_digest=(
                self.constitution.auto_promotion_policy_digest
            ),
            runtime_bundle_manifest_digest=runtime_manifest,
            trigger_facts=facts,
            trigger_fact_digests=fact_digests,
            new_session_count=len(created),
            triggered=triggered,
        )

    def _trigger_allowed(
        self, count: int, watermark: int, new_fact_digests: tuple[str, ...]
    ) -> bool:
        if new_fact_digests:
            return True
        if count < self.constitution.minimum_created_sessions:
            return False
        previous = self._latest_terminal_epoch()
        if previous is None:
            return True
        if (
            previous.state == "superseded_runtime_upgrade"
            and previous.runtime_bundle_manifest_digest
            != self.runtime_bundle_manifests[self.constitution.constitution_digest]
        ):
            return True
        if watermark <= previous.session_sequence_high_watermark:
            return False
        new_sessions = count - previous.new_session_count
        if previous.state != "promoted":
            return new_sessions >= self.constitution.no_change_new_session_cooldown
        if new_sessions >= self.constitution.promotion_new_session_cooldown:
            return True
        if not previous.terminal_at:
            return False
        eligible_at = parse_utc(previous.terminal_at) + timedelta(
            days=self.constitution.promotion_day_cooldown
        )
        return parse_utc(self.clock()) >= eligible_at

    def _new_fact_digests(self, current: tuple[str, ...]) -> tuple[str, ...]:
        previous = self._latest_terminal_epoch()
        if previous is None:
            return current
        trigger = next(
            (
                item
                for item in reversed(self.store.triggers())
                if item.trigger_digest == previous.trigger_digest
            ),
            None,
        )
        prior = set(trigger.trigger_fact_digests if trigger is not None else ())
        return tuple(item for item in current if item not in prior)

    def _latest_terminal_epoch(self) -> OptimizationEpoch | None:
        candidates = tuple(
            item
            for item in self.store.epochs()
            if item.state in TERMINAL_EPOCH_STATES
        )
        return candidates[-1] if candidates else None

    def _active_epoch(self) -> OptimizationEpoch | None:
        active = tuple(
            item
            for item in self.store.epochs()
            if item.state not in TERMINAL_EPOCH_STATES
        )
        if len(active) > 1:
            raise SharedStateIntegrityError(
                "multiple active optimization epochs detected"
            )
        return active[0] if active else None

    def _latest_trigger(self) -> OptimizationTriggerEvent | None:
        current_manifest = self.runtime_bundle_manifests[
            self.constitution.constitution_digest
        ]
        return next(
            (
                item
                for item in reversed(self.store.triggers())
                if item.constitution_digest
                == self.constitution.constitution_digest
                and item.runtime_bundle_manifest_digest == current_manifest
            ),
            None,
        )

    def _resolve_epoch(self, trigger: OptimizationTriggerEvent) -> OptimizationEpoch:
        if not trigger.statistics_policy_digest:
            raise SharedStateIntegrityError(
                "optimization trigger statistics policy is missing"
            )
        statistics_policy_for_digest(trigger.statistics_policy_digest)
        try:
            constitution = self.constitutions[trigger.constitution_digest]
            runtime_manifest = self.runtime_bundle_manifests[
                trigger.constitution_digest
            ]
        except KeyError as exc:
            raise SharedStateIntegrityError(
                "optimization historical runtime bundle is unavailable"
            ) from exc
        if (
            trigger.candidate_domain_registry_digest
            != constitution.candidate_domain_registry_digest
            or trigger.statistics_policy_digest
            != constitution.statistics_policy_digest
            or trigger.evaluator_registry_digest
            != constitution.evaluator_registry_digest
            or trigger.auto_promotion_policy_digest
            != constitution.auto_promotion_policy_digest
            or trigger.runtime_bundle_manifest_digest != runtime_manifest
        ):
            raise SharedStateIntegrityError(
                "optimization trigger constitution lineage diverged"
            )
        epoch_id = stable_id(
            "optimization-epoch", self.project_id, trigger.trigger_fingerprint
        )
        with self.store.locked():
            active = tuple(
                item
                for item in self.store.epochs()
                if item.state not in TERMINAL_EPOCH_STATES
            )
            if len(active) > 1:
                raise SharedStateIntegrityError(
                    "multiple active optimization epochs detected"
                )
            if active:
                return active[0]
            existing = self.store.epoch(epoch_id)
            if existing is not None:
                return existing
            return self.store.create_epoch(
                OptimizationEpoch(
                    epoch_id=epoch_id,
                    project_id=self.project_id,
                    trigger_fingerprint=trigger.trigger_fingerprint,
                    trigger_digest=trigger.trigger_digest,
                    constitution_digest=trigger.constitution_digest,
                    baseline_snapshot_digest=trigger.baseline_snapshot_digest,
                    candidate_domain_registry_digest=(
                        trigger.candidate_domain_registry_digest
                    ),
                    statistics_policy_digest=trigger.statistics_policy_digest,
                    evaluator_registry_digest=trigger.evaluator_registry_digest,
                    auto_promotion_policy_digest=(
                        trigger.auto_promotion_policy_digest
                    ),
                    runtime_bundle_manifest_digest=(
                        trigger.runtime_bundle_manifest_digest
                    ),
                    session_sequence_high_watermark=trigger.session_sequence_high_watermark,
                    new_session_count=trigger.new_session_count,
                    state="queued",
                    revision=1,
                    started_at=self.clock(),
                )
            )

    def _validate_advance(self, project_id: str, budget: MaintenanceBudget) -> None:
        MaintenanceBudget.model_validate(budget.model_dump(mode="json"))
        if project_id != self.project_id:
            raise ValueError("optimization project identity diverged")


def _created_sessions(
    observations: tuple[OptimizationSessionObservation, ...],
) -> tuple[OptimizationSessionObservation, ...]:
    unique: dict[tuple[str, str], OptimizationSessionObservation] = {}
    for item in observations:
        if item.observation_kind == "created":
            unique.setdefault((item.session_id, item.initial_candidate_digest), item)
    return tuple(sorted(unique.values(), key=lambda item: item.sequence))


def _trusted_budget_policy(
    constitution: OptimizationConstitution,
    policy: ReviewerBudgetPolicy,
) -> ReviewerBudgetPolicy:
    trusted = ReviewerBudgetPolicy.model_validate(policy.model_dump(mode="json"))
    if constitution.epoch_budget_policy_digest != trusted.policy_digest:
        raise ValueError("optimization budget policy lineage diverged")
    return trusted


def _critical_facts(
    observations: tuple[OptimizationSessionObservation, ...],
    evidence: tuple[AttributionEvidence, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    critical = tuple(
        item for item in observations if item.terminal_reason in _CRITICAL_FACTS
    )
    facts = {item.terminal_reason for item in critical}
    if evidence:
        facts.update({"late_critical_finding", "reviewer_coverage_leak"})
    digests = {
        *(item.attribution_input_digest for item in evidence),
        *(item.observation_digest for item in critical),
    }
    return tuple(sorted(facts)), tuple(sorted(digests))


def _trigger_fingerprint(
    constitution_digest: str,
    baseline_digest: str,
    runtime_bundle_manifest_digest: str,
    watermark: int,
    count: int,
    facts: tuple[str, ...],
    fact_digests: tuple[str, ...],
) -> str:
    payload = "\0".join(
        (
            constitution_digest,
            baseline_digest,
            runtime_bundle_manifest_digest,
            str(watermark),
            str(count),
            *facts,
            *fact_digests,
        )
    )
    return sha256(payload.encode("utf-8")).hexdigest()
