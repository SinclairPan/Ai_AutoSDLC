"""Finding 归因证据、决定和产品缺陷信号的不可变本地存储。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    ShortFileLock,
    bind_repository_project,
    create_json_exclusive,
    read_json_object,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.finding_models import FindingEvent
from ai_sdlc.core.stage_review.optimization.attribution import (
    AttributionDecision,
    AttributionEvidence,
    AttributionPolicy,
    FindingAttribution,
    ProductDefectSignal,
)
from ai_sdlc.core.stage_review.optimization.attribution import (
    _attribute_finding as attribute_finding,
)
from ai_sdlc.core.stage_review.registry_versions import require_machine_id

T = TypeVar("T", bound=BaseModel)


class FindingAttributionStore:
    def __init__(
        self,
        root: Path,
        *,
        project_id: str,
        lock_timeout_seconds: float = 2,
    ) -> None:
        self.project_id = require_machine_id(project_id, "project_id")
        shared = resolve_canonical_shared_state(root, self.project_id)
        bind_repository_project(shared, self.project_id)
        self.root = shared / "offline-optimization" / "finding-attributions"
        self.lock_timeout_seconds = lock_timeout_seconds

    @contextmanager
    def lock(self) -> Iterator[None]:
        with ShortFileLock(
            self.root / "mutation.lock",
            timeout_seconds=self.lock_timeout_seconds,
        ):
            yield

    def record(
        self,
        evidence: AttributionEvidence,
        *,
        source_event: FindingEvent,
        policy: AttributionPolicy | None = None,
    ) -> AttributionDecision:
        with self.lock():
            return self._record_locked(
                evidence,
                source_event=source_event,
                policy=policy,
            )

    def _record_locked(
        self,
        evidence: AttributionEvidence,
        *,
        source_event: FindingEvent,
        policy: AttributionPolicy | None = None,
    ) -> AttributionDecision:
        trusted = AttributionEvidence.model_validate(evidence.model_dump(mode="json"))
        event = FindingEvent.model_validate(source_event.model_dump(mode="json"))
        self._validate_source(trusted, event)
        governed = policy or AttributionPolicy.baseline()
        decision = attribute_finding(trusted, governed)
        self._persist(
            "evidence",
            trusted.attribution_input_digest,
            trusted,
            AttributionEvidence,
        )
        self._persist(
            "decisions",
            decision.attribution.attribution_id,
            decision.attribution,
            FindingAttribution,
        )
        if decision.product_defect_signal is not None:
            self._persist(
                "product-defects",
                decision.product_defect_signal.signal_id,
                decision.product_defect_signal,
                ProductDefectSignal,
            )
        return decision

    def evidences(self) -> tuple[AttributionEvidence, ...]:
        return self._read_all("evidence", AttributionEvidence)

    def attributions(self) -> tuple[FindingAttribution, ...]:
        return self._read_all("decisions", FindingAttribution)

    def product_defect_signals(self) -> tuple[ProductDefectSignal, ...]:
        return self._read_all("product-defects", ProductDefectSignal)

    def _validate_source(
        self,
        evidence: AttributionEvidence,
        event: FindingEvent,
    ) -> None:
        expected = (
            evidence.project_id == self.project_id == event.scope.project_id,
            evidence.session_id == event.scope.session_id,
            evidence.finding_key == event.finding_key,
            evidence.finding_event_digest == event.event_digest,
            event.late_critical_finding is not None,
            event.reviewer_coverage_leak is not None,
            event.attribution_input is not None,
            evidence.late_critical_finding,
            evidence.reviewer_coverage_leak,
        )
        if not all(expected):
            raise SharedStateIntegrityError("attribution source event lineage diverged")

    def _persist(
        self,
        directory: str,
        identity: str,
        value: T,
        model: type[T],
    ) -> T:
        path = self.root / directory / f"{identity}.json"
        payload = value.model_dump(mode="json")
        if create_json_exclusive(path, payload):
            return value
        existing = model.model_validate(read_json_object(path))
        if existing != value:
            raise SharedStateIntegrityError("attribution immutable identity fork")
        return existing

    def _read_all(self, directory: str, model: type[T]) -> tuple[T, ...]:
        root = self.root / directory
        if not root.is_dir():
            return ()
        return tuple(model.model_validate(read_json_object(path)) for path in sorted(root.glob("*.json")))
