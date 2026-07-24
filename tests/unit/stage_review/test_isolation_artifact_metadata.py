from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.unit.stage_review.test_isolation_execution import (
    _host,
    _manifest,
    _permit,
)
from tests.unit.stage_review.test_resources import _now

from ai_sdlc.core import loop_models
from ai_sdlc.core.stage_review.isolation_detected_only import (
    _build_evidence as build_detected_only_evidence,
)
from ai_sdlc.core.stage_review.isolation_execution import (
    build_isolation_evidence_manifest,
    build_isolation_execution_permit,
)
from ai_sdlc.core.stage_review.isolation_launcher import IsolationProcessResult
from ai_sdlc.core.stage_review.isolation_permit_store import build_refusal_receipt
from ai_sdlc.core.stage_review.isolation_receipts import (
    build_execution_observation,
    build_execution_receipt,
)

_RUNTIME_FIELDS = {
    "ai_sdlc_version",
    "artifact_kind",
    "canonicalization_version",
    "compatibility_mode",
    "created_at",
    "created_by",
    "extensions",
    "schema_version",
}


@pytest.mark.parametrize(
    "artifact_factory",
    (
        "manifest",
        "permit",
        "execution_receipt",
        "execution_observation",
        "refusal_receipt",
        "detected_only_evidence",
    ),
)
def test_isolation_artifact_digest_keeps_one_creation_timestamp_across_second_boundary(
    monkeypatch: pytest.MonkeyPatch,
    artifact_factory: str,
) -> None:
    factory = _artifact_factories()[artifact_factory]
    instants = iter(
        (
            datetime(2026, 7, 24, 2, 55, 54, tzinfo=UTC),
            datetime(2026, 7, 24, 2, 55, 55, tzinfo=UTC),
        )
    )

    class BoundaryDatetime:
        @classmethod
        def now(cls, timezone):
            return next(instants).astimezone(timezone)

    monkeypatch.setattr(loop_models, "datetime", BoundaryDatetime)

    artifact = factory()

    assert artifact.created_at == "2026-07-24T02:55:54Z"
    assert next(instants) == datetime(2026, 7, 24, 2, 55, 55, tzinfo=UTC)


def _artifact_factories():
    from ai_sdlc.core.stage_review import isolation_execution

    api = isolation_execution
    host = _host(api)
    manifest = _manifest(api, host_snapshot_digest=host.snapshot_digest)
    permit = _permit(api)
    result = IsolationProcessResult(
        return_code=0,
        stdout="PASS",
        stderr="",
        process_id=202,
        parent_process_id=manifest.parent_process_id,
        boundary_results=manifest.boundary_results,
        os_native_denials=manifest.os_native_denials,
        before_digest="sha256:protected",
        after_digest="sha256:protected",
        cleanup_succeeded=True,
    )
    manifest_values = manifest.model_dump(
        mode="json",
        exclude={*_RUNTIME_FIELDS, "manifest_digest"},
    )
    permit_values = permit.model_dump(
        mode="json",
        exclude={*_RUNTIME_FIELDS, "permit_digest"},
    )
    return {
        "manifest": lambda: build_isolation_evidence_manifest(**manifest_values),
        "permit": lambda: build_isolation_execution_permit(**permit_values),
        "execution_receipt": lambda: build_execution_receipt(
            permit,
            "invoke",
            result,
            _now(),
        ),
        "execution_observation": lambda: build_execution_observation(
            permit,
            result,
            stage="completed",
            previous_observation_digest="",
            now=_now(),
        ),
        "refusal_receipt": lambda: build_refusal_receipt(
            permit,
            reason="isolation.command-build-refused",
            now=_now(),
        ),
        "detected_only_evidence": lambda: build_detected_only_evidence(
            SimpleNamespace(
                allocation_digest="sha256:allocation",
                assignment_digest="sha256:assignment",
                layout_digest="sha256:layout",
                host_snapshot=SimpleNamespace(snapshot_digest="sha256:host"),
            ),
            manifest,
            Path("/tmp/ai-sdlc-detected-only"),
            stage="polluted",
            observed_digest="sha256:observed",
            previous_evidence_digest="",
            cleanup_succeeded=False,
            now=_now(),
        ),
    }
