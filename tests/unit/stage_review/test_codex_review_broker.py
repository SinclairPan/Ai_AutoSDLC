from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_sdlc.core.stage_review.codex_review_broker import (
    CodexReviewBroker,
    CodexReviewBrokerError,
)
from ai_sdlc.core.stage_review.provider_transport_models import (
    ProviderEgressPermit,
    ProviderTransportEnvelope,
)
from ai_sdlc.core.stage_review.remote_review_models import RemoteReviewOutput


def test_codex_broker_runs_toolless_ephemeral_structured_review(
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def run(argv, prompt, output_path, timeout_seconds):
        captured.update(
            argv=argv,
            prompt=prompt,
            timeout=timeout_seconds,
        )
        output_path.write_text(json.dumps(_passed_review()), encoding="utf-8")
        return 0, "", ""

    broker = CodexReviewBroker(
        tmp_path,
        codex_executable="codex",
        runner=run,
    )

    response = broker.exchange(_permit(), _envelope())

    argv = captured["argv"]
    assert isinstance(argv, tuple)
    assert "--ephemeral" in argv
    assert "--ignore-user-config" in argv
    assert "--ignore-rules" in argv
    assert _disabled_features(argv) >= {
        "apps",
        "browser_use",
        "computer_use",
        "multi_agent",
        "shell_tool",
    }
    review = RemoteReviewOutput.model_validate(response["review"])
    assert review.verdict == "passed"
    assert review.coverage.reviewed_area_ids == ("capability.correctness",)
    usage = response["accounted_usage"]
    assert isinstance(usage, dict)
    amounts = usage["amounts"]
    basis = usage["basis"]
    assert amounts["provider_calls"] == 1
    assert amounts["review_passes"] == 1
    assert amounts["tokens"] > 0
    assert amounts["cost"] > 0
    assert basis["token_source"] == "estimated"
    assert basis["cost_source"] == "estimated"
    assert basis["active_wall_clock_source"] == "metered"
    assert basis["estimation_policy_id"] == "usage-estimate.codex-local"
    assert basis["estimation_policy_version"] == "1.0.0"
    assert basis["estimation_policy_digest"].startswith("sha256:")


def test_codex_broker_caps_execution_by_authorized_active_time(
    tmp_path: Path,
) -> None:
    captured: dict[str, float] = {}

    def run(_argv, _prompt, output_path, timeout_seconds):
        captured["timeout"] = timeout_seconds
        output_path.write_text(json.dumps(_passed_review()), encoding="utf-8")
        return 0, "", ""

    broker = CodexReviewBroker(tmp_path, runner=run)

    broker.exchange(_permit(active_wall_clock_limit=12), _envelope(12))

    assert captured["timeout"] == 12


def test_codex_broker_failure_preserves_metered_active_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks = iter((10.0, 23.0))
    monkeypatch.setattr(
        "ai_sdlc.core.stage_review.codex_review_broker.time.monotonic",
        lambda: next(ticks),
    )

    def run(_argv, _prompt, _output_path, _timeout_seconds):
        return 9, "", "provider failed"

    broker = CodexReviewBroker(tmp_path, runner=run)

    with pytest.raises(CodexReviewBrokerError) as captured:
        broker.exchange(_permit(active_wall_clock_limit=12), _envelope(12))

    assert captured.value.accounted_usage is not None
    assert captured.value.accounted_usage.amounts.active_wall_clock == 13


def test_codex_broker_rejects_invalid_structured_output(tmp_path: Path) -> None:
    def run(_argv, _prompt, output_path, _timeout_seconds):
        output_path.write_text('{"verdict":"passed"}', encoding="utf-8")
        return 0, "", ""

    broker = CodexReviewBroker(tmp_path, runner=run)

    with pytest.raises(CodexReviewBrokerError, match="output is invalid"):
        broker.exchange(_permit(), _envelope())


def _passed_review() -> dict[str, object]:
    return {
        "schema_version": "remote-review.v1",
        "verdict": "passed",
        "coverage": {
            "reviewed_area_ids": ["capability.correctness"],
            "uncovered_area_ids": [],
            "evidence_gap_ids": [],
        },
        "findings": [],
        "evidence_digests": ["sha256:review-evidence"],
    }


def _envelope(active_wall_clock_limit: float = 300) -> ProviderTransportEnvelope:
    payload = {
        "schema": "review-provider-request.v1",
        "packet": {"capability_ids": ["capability.correctness"]},
    }
    from ai_sdlc.core.stage_review.provider_transport_models import (
        provider_payload_digest,
    )

    return ProviderTransportEnvelope(
        invocation_id="invocation.one",
        assignment_digest="assignment.one",
        provider_id="provider.codex",
        execution_identity_digest="sha256:provider-execution",
        request_digest=provider_payload_digest(payload),
        turn_index=1,
        idempotency_key="idempotency.one",
        credential_view_digest="sha256:credential",
        backend_epoch="epoch.one",
        active_wall_clock_limit=active_wall_clock_limit,
        payload=payload,
    )


def _permit(active_wall_clock_limit: float = 300) -> ProviderEgressPermit:
    envelope = _envelope(active_wall_clock_limit)
    return ProviderEgressPermit.model_construct(
        permit_id="permit.one",
        invocation_id=envelope.invocation_id,
        assignment_digest=envelope.assignment_digest,
        provider_id=envelope.provider_id,
        request_digest=envelope.request_digest,
        turn_index=envelope.turn_index,
        idempotency_key=envelope.idempotency_key,
        credential_view_digest=envelope.credential_view_digest,
        backend_epoch=envelope.backend_epoch,
        active_wall_clock_limit=active_wall_clock_limit,
        endpoint_id="ipc://codex-review",
        transport_contract_digest="sha256:transport",
        issued_at="2026-07-22T00:00:00Z",
        expires_at="2099-07-22T00:01:00Z",
        nonce="nonce",
        permit_digest="sha256:permit",
    )


def _disabled_features(argv: tuple[str, ...]) -> set[str]:
    return {
        argv[index + 1] for index, value in enumerate(argv[:-1]) if value == "--disable"
    }
