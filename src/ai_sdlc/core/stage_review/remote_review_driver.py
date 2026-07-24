"""Journal 派发后调用可信传输，并在受限子进程校验响应。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ai_sdlc.core.stage_review.isolation_launch_models import (
    CommandKind,
    IsolatedProviderCommand,
    IsolationProcessResult,
)
from ai_sdlc.core.stage_review.isolation_models import IsolationExecutionPermit
from ai_sdlc.core.stage_review.provider_execution_evidence import (
    ProviderExecutionOutcome,
    build_provider_execution_outcome,
)
from ai_sdlc.core.stage_review.provider_execution_registry import (
    RegisteredProviderExecution,
)
from ai_sdlc.core.stage_review.provider_journal_builders import (
    build_provider_submission,
)
from ai_sdlc.core.stage_review.provider_journal_driver import ProviderDriverRefused
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderInvocationRequest,
    ProviderQueryResult,
    ProviderSubmission,
)
from ai_sdlc.core.stage_review.provider_transport_models import (
    ProviderTransportEnvelope,
    ProviderTransportExchangeResult,
    ProviderTransportExecutionError,
    provider_payload_digest,
)
from ai_sdlc.core.stage_review.provider_usage_models import (
    AccountedProviderUsage,
    accounted_usage_from_payload,
)
from ai_sdlc.core.stage_review.remote_review_models import (
    RemoteReviewProviderResponse,
)
from ai_sdlc.core.stage_review.remote_review_validator import (
    isolated_validator_program,
)


class RemoteReviewDriver:
    def __init__(
        self,
        request: ProviderInvocationRequest,
        *,
        payload: dict[str, object],
        execution: RegisteredProviderExecution,
        output_root: Path,
        credential_view_digest: str,
        layout_digest: str,
        validation_python_executable: str,
    ) -> None:
        if request.request_digest != provider_payload_digest(payload):
            raise ValueError("remote review request payload diverged")
        if any(not value.strip() for value in (credential_view_digest, layout_digest)):
            raise ValueError("remote review execution identity is incomplete")
        if request.provider_id != execution.identity.provider_id:
            raise ValueError("remote review provider execution diverged")
        validation_python = Path(validation_python_executable).resolve(strict=True)
        if not validation_python.is_file():
            raise ValueError("remote review validation runtime is unavailable")
        self.provider_id = execution.identity.provider_id
        self.capabilities = execution.identity.recovery_capabilities
        self._request = request
        self._payload = payload
        self._execution = execution
        self._transport = execution.transport
        self._credential_view_digest = credential_view_digest
        self._layout_digest = layout_digest
        self._validation_python_executable = str(validation_python)
        suffix = hashlib.sha256(request.invocation_id.encode()).hexdigest()[:24]
        self._result_path = output_root / f"{suffix}.review-result.json"
        self._exchange: ProviderTransportExchangeResult | None = None
        self._accounted_usage: AccountedProviderUsage | None = None
        self._execution_outcome = build_provider_execution_outcome()

    @property
    def executed_accounted_usage(self) -> AccountedProviderUsage | None:
        return self._accounted_usage

    @property
    def executed_outcome(self) -> ProviderExecutionOutcome:
        return self._execution_outcome

    def invoke(self, request: ProviderInvocationRequest) -> ProviderSubmission:
        raise ProviderDriverRefused("remote reviewer requires isolation launcher")

    def query(self, request: ProviderInvocationRequest) -> ProviderQueryResult:
        raise ProviderDriverRefused("remote reviewer recovery requires user")

    def build_isolated_command(
        self,
        request: ProviderInvocationRequest,
        permit: IsolationExecutionPermit,
        command_kind: CommandKind,
    ) -> IsolatedProviderCommand:
        if command_kind != "invoke" or request != self._request:
            raise ProviderDriverRefused("remote reviewer command is not recoverable")
        if not self._permit_matches(request, permit):
            raise ProviderDriverRefused("remote reviewer isolation lineage diverged")
        if not self._transport.remote_provider_available:
            raise ProviderDriverRefused("remote provider is unavailable")
        try:
            exchange = self._transport.exchange(self._envelope(request, permit))
        except ProviderTransportExecutionError as exc:
            outcome = build_provider_execution_outcome(exc.accounted_usage)
            raise ProviderDriverRefused(str(exc), outcome=outcome) from exc
        self._accounted_usage = accounted_usage_from_payload(
            exchange.response.get("accounted_usage")
        )
        self._execution_outcome = build_provider_execution_outcome(
            self._accounted_usage,
            egress_receipt_digests=(exchange.receipt.receipt_digest,),
        )
        if not exchange.receipt.remote_provider_exercised:
            raise ProviderDriverRefused(
                "remote provider execution is unproven",
                outcome=self._execution_outcome,
            )
        self._exchange = exchange
        return build_remote_review_validation_command(
            exchange.response,
            exchange.receipt.response_digest,
            self._result_path,
            python_executable=self._validation_python_executable,
            command_kind=command_kind,
        )

    def decode_isolated_result(
        self,
        request: ProviderInvocationRequest,
        command_kind: CommandKind,
        result: IsolationProcessResult,
    ) -> ProviderSubmission | ProviderQueryResult:
        exchange = self._exchange
        if command_kind != "invoke" or exchange is None or result.return_code != 0:
            raise ProviderDriverRefused(
                "isolated remote review validation failed",
                outcome=self._execution_outcome,
            )
        try:
            stdout = json.loads(result.stdout.strip().splitlines()[-1])
            persisted = json.loads(self._result_path.read_text(encoding="utf-8"))
            expected = json.loads(
                json.dumps(exchange.response, sort_keys=True, allow_nan=False)
            )
            if (
                stdout != persisted
                or persisted != expected
                or provider_payload_digest(persisted)
                != exchange.receipt.response_digest
            ):
                raise ValueError("remote review validator output diverged")
            response = RemoteReviewProviderResponse.model_validate(persisted)
        except (IndexError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderDriverRefused(
                "remote review output is invalid",
                outcome=self._execution_outcome,
            ) from exc
        return build_provider_submission(
            request,
            provider_call_id=response.provider_call_id,
            output_payload=response.review.model_dump(mode="json"),
            accounted_usage=response.accounted_usage,
            egress_receipt_digests=(exchange.receipt.receipt_digest,),
        )

    def _envelope(
        self,
        request: ProviderInvocationRequest,
        permit: IsolationExecutionPermit,
    ) -> ProviderTransportEnvelope:
        return ProviderTransportEnvelope(
            invocation_id=request.invocation_id,
            assignment_digest=request.assignment_digest,
            provider_id=request.provider_id,
            execution_identity_digest=self._execution.identity.identity_digest,
            request_digest=request.request_digest,
            turn_index=1,
            idempotency_key=request.idempotency_key,
            credential_view_digest=self._credential_view_digest,
            backend_epoch=permit.backend_epoch,
            active_wall_clock_limit=request.anticipated_usage.active_wall_clock,
            payload=self._payload,
        )

    def _permit_matches(
        self,
        request: ProviderInvocationRequest,
        permit: IsolationExecutionPermit,
    ) -> bool:
        return all(
            (
                permit.assignment_digest == request.assignment_digest,
                permit.candidate_digest == request.candidate_digest,
                permit.layout_digest == self._layout_digest,
            )
        )


def build_remote_review_validation_command(
    response: dict[str, object],
    response_digest: str,
    result_path: Path,
    *,
    python_executable: str,
    command_kind: CommandKind,
) -> IsolatedProviderCommand:
    stdin_text = json.dumps(
        response,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return IsolatedProviderCommand(
        argv=(
            str(Path(python_executable).resolve(strict=True)),
            "-I",
            "-c",
            isolated_validator_program(),
            response_digest,
            str(result_path),
        ),
        stdin_text=stdin_text,
        command_kind=command_kind,
    )


__all__ = ["RemoteReviewDriver", "build_remote_review_validation_command"]
