"""通过无工具 Codex Exec 会话调用远端 Reviewer。"""

from __future__ import annotations

import json
import math
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from ai_sdlc.core.stage_review.provider_transport_models import (
    ProviderEgressPermit,
    ProviderTransportEnvelope,
    ProviderTransportExecutionError,
)
from ai_sdlc.core.stage_review.provider_usage_models import (
    AccountedProviderUsage,
    ProviderUsageBasis,
    ProviderUsageEstimatePolicy,
    build_usage_estimate_policy,
)
from ai_sdlc.core.stage_review.remote_review_models import RemoteReviewOutput
from ai_sdlc.core.stage_review.resource_builders import parse_utc, stable_id
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts

CodexRunner = Callable[
    [tuple[str, ...], str, Path, float],
    tuple[int, str, str],
]
_DISABLED_FEATURES = (
    "apps",
    "browser_use",
    "browser_use_external",
    "computer_use",
    "multi_agent",
    "plugins",
    "shell_tool",
)
_DEFAULT_ESTIMATE_POLICY = build_usage_estimate_policy(
    policy_id="usage-estimate.codex-local",
    version="1.0.0",
    characters_per_token=4,
    estimated_cost_per_token=0.000001,
)


class CodexReviewBrokerError(ProviderTransportExecutionError):
    """Codex Provider 未形成可信结构化结果。"""


class CodexReviewBroker:
    remote_provider_exercised = True

    def __init__(
        self,
        root: Path,
        *,
        codex_executable: str = "codex",
        timeout_seconds: float = 300,
        runner: CodexRunner | None = None,
        estimate_policy: ProviderUsageEstimatePolicy = _DEFAULT_ESTIMATE_POLICY,
    ) -> None:
        self._root = root.resolve(strict=False)
        self._codex = codex_executable
        self._timeout = timeout_seconds
        self._runner = runner or _run_codex
        self._estimate_policy = estimate_policy

    def exchange(
        self,
        permit: ProviderEgressPermit,
        envelope: ProviderTransportEnvelope,
    ) -> dict[str, object]:
        _require_lineage(permit, envelope)
        workspace = self._root / _safe_name(permit.permit_digest)
        workspace.mkdir(parents=True, exist_ok=True)
        schema_path = workspace / "review-output.schema.json"
        output_path = workspace / "review-output.json"
        schema_path.write_text(
            json.dumps(RemoteReviewOutput.model_json_schema(), sort_keys=True),
            encoding="utf-8",
        )
        prompt = _review_prompt(envelope)
        timeout = _execution_timeout(self._timeout, permit, envelope)
        elapsed = _execute_review(
            self._runner,
            _codex_argv(self._codex, workspace, schema_path, output_path),
            prompt,
            output_path,
            timeout,
            self._estimate_policy,
        )
        review, raw = _validated_review(
            output_path,
            prompt,
            elapsed,
            self._estimate_policy,
        )
        usage = _estimated_usage(prompt, raw, elapsed, self._estimate_policy)
        return {
            "provider_call_id": stable_id(
                "codex-provider-call",
                permit.permit_digest,
            ),
            "review": review.model_dump(mode="json"),
            "accounted_usage": usage.model_dump(mode="json"),
        }


def _execute_review(
    runner: CodexRunner,
    argv: tuple[str, ...],
    prompt: str,
    output_path: Path,
    timeout: float,
    policy: ProviderUsageEstimatePolicy,
) -> float:
    started = time.monotonic()
    try:
        code, _stdout, stderr = runner(argv, prompt, output_path, timeout)
    except CodexReviewBrokerError as exc:
        elapsed = _active_elapsed(started, timeout)
        usage = _estimated_usage(prompt, str(exc), elapsed, policy)
        raise CodexReviewBrokerError(str(exc), accounted_usage=usage) from exc
    elapsed = _active_elapsed(started, timeout)
    if code != 0:
        raise CodexReviewBrokerError(
            f"codex reviewer failed with exit code {code}: {_bounded(stderr)}",
            accounted_usage=_estimated_usage(prompt, stderr, elapsed, policy),
        )
    return elapsed


def _validated_review(
    output_path: Path,
    prompt: str,
    elapsed: float,
    policy: ProviderUsageEstimatePolicy,
) -> tuple[RemoteReviewOutput, str]:
    try:
        return _read_review(output_path)
    except CodexReviewBrokerError as exc:
        usage = _estimated_usage(prompt, _safe_output(output_path), elapsed, policy)
        raise CodexReviewBrokerError(str(exc), accounted_usage=usage) from exc


def _codex_argv(
    executable: str,
    workspace: Path,
    schema_path: Path,
    output_path: Path,
) -> tuple[str, ...]:
    argv = [
        executable,
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--color",
        "never",
        "--cd",
        str(workspace),
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_path),
    ]
    for feature in _DISABLED_FEATURES:
        argv.extend(("--disable", feature))
    argv.append("-")
    return tuple(argv)


def _review_prompt(envelope: ProviderTransportEnvelope) -> str:
    payload = json.dumps(envelope.payload, sort_keys=True, ensure_ascii=False)
    return (
        "You are an independent adversarial reviewer. Do not use tools, the "
        "filesystem, network browsing, memory, or prior reviewer output. Review "
        "only the frozen JSON packet below and return exactly one JSON object "
        "matching remote-review.v1. Findings require concrete packet evidence.\n"
        f"{payload}"
    )


def _read_review(path: Path) -> tuple[RemoteReviewOutput, str]:
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
        review = RemoteReviewOutput.model_validate(payload)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise CodexReviewBrokerError("codex reviewer output is invalid") from exc
    return review, raw


def _estimated_usage(
    prompt: str,
    output: str,
    elapsed: float,
    policy: ProviderUsageEstimatePolicy,
) -> AccountedProviderUsage:
    total_characters = len(prompt) + len(output)
    tokens = max(math.ceil(total_characters / policy.characters_per_token), 1)
    amounts = ResourceAmounts(
        provider_calls=1,
        review_passes=1,
        tokens=tokens,
        cost=max(tokens * policy.estimated_cost_per_token, 0.000001),
        active_wall_clock=elapsed,
    )
    return AccountedProviderUsage(
        amounts=amounts,
        basis=ProviderUsageBasis(
            token_source="estimated",
            cost_source="estimated",
            active_wall_clock_source="metered",
            estimation_policy_id=policy.policy_id,
            estimation_policy_version=policy.version,
            estimation_policy_digest=policy.policy_digest,
            input_characters=len(prompt),
            output_characters=len(output),
        ),
    )


def _require_lineage(
    permit: ProviderEgressPermit,
    envelope: ProviderTransportEnvelope,
) -> None:
    fields = (
        "invocation_id",
        "assignment_digest",
        "provider_id",
        "request_digest",
        "turn_index",
        "idempotency_key",
        "credential_view_digest",
        "backend_epoch",
        "active_wall_clock_limit",
    )
    if any(getattr(permit, field) != getattr(envelope, field) for field in fields):
        raise CodexReviewBrokerError("codex reviewer transport lineage diverged")
    now = datetime.now(UTC)
    if not (parse_utc(permit.issued_at) <= now < parse_utc(permit.expires_at)):
        raise CodexReviewBrokerError("codex reviewer transport permit expired")


def _execution_timeout(
    configured: float,
    permit: ProviderEgressPermit,
    envelope: ProviderTransportEnvelope,
) -> float:
    remaining = (parse_utc(permit.expires_at) - datetime.now(UTC)).total_seconds()
    timeout = min(configured, envelope.active_wall_clock_limit, remaining)
    if timeout <= 0:
        raise CodexReviewBrokerError("codex reviewer active execution budget expired")
    return timeout


def _active_elapsed(started: float, timeout: float) -> float:
    del timeout
    return max(time.monotonic() - started, 0.001)


def _safe_output(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _run_codex(
    argv: tuple[str, ...],
    prompt: str,
    output_path: Path,
    timeout_seconds: float,
) -> tuple[int, str, str]:
    del output_path
    try:
        completed = subprocess.run(
            argv,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CodexReviewBrokerError("codex reviewer could not be executed") from exc
    return completed.returncode, completed.stdout, completed.stderr


def _safe_name(digest: str) -> str:
    return digest.removeprefix("sha256:").replace(":", "-")


def _bounded(value: str) -> str:
    return value.strip().replace("\n", " ")[:500]


__all__ = ["CodexReviewBroker", "CodexReviewBrokerError"]
