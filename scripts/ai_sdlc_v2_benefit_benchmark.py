"""Offline command wrapper for the frozen AI-SDLC v2 benefit benchmark."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path

from ai_sdlc.benefit_benchmark import (
    AttemptCompletion,
    AttemptRequest,
    BenchmarkIssue,
    execution_authorization_is_formal,
    load_protocol,
    record_provider_completion,
    record_service_transaction,
    redact_public_message,
    reserve_provider_attempt,
    seal_run_evidence,
    start_run,
    start_service_transaction,
    transition_run_phase,
    validate_execution_authorization,
    validate_protocol,
    verify_receipt,
    verify_summary,
)


class _CliUsageError(ValueError):
    """A command-line shape error that must be returned as JSON."""


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise _CliUsageError("invalid command arguments")


def _public_message(message: str) -> str:
    return redact_public_message(message)


def _public_error_message(error: Exception) -> str:
    if isinstance(error, OSError):
        return "input or output file operation failed"
    if isinstance(error, json.JSONDecodeError):
        return "input is not valid JSON"
    return _public_message(str(error) or "benchmark input was rejected")


def _issue_payload(issue: BenchmarkIssue) -> Mapping[str, str]:
    return {"code": issue.code, "message": _public_message(issue.message)}


def _json_object(path: Path, label: str) -> Mapping[str, object]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"{label} could not be read") from error
    try:
        data = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error
    if not isinstance(data, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return data


def _protocol(path: Path):
    try:
        return load_protocol(path)
    except OSError as error:
        raise ValueError("protocol could not be read") from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("protocol is not valid JSON") from error


def _emit(payload: Mapping[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _emit_error(code: str, message: str) -> None:
    _emit({"error": {"code": code, "message": message}})


def main() -> int:
    parser = _JsonArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--protocol", type=Path, required=True)
    validate.add_argument("--authorization", type=Path)
    run_start = commands.add_parser("start-run")
    run_start.add_argument("--ledger", type=Path, required=True)
    run_start.add_argument("--protocol", type=Path, required=True)
    run_start.add_argument("--contract", type=Path, required=True)
    run_start.add_argument("--run-id", required=True)
    run_start.add_argument("--authorization", type=Path, required=True)
    phase_transition = commands.add_parser("transition-phase")
    phase_transition.add_argument("--ledger", type=Path, required=True)
    phase_transition.add_argument("--protocol", type=Path, required=True)
    phase_transition.add_argument("--contract", type=Path, required=True)
    phase_transition.add_argument("--run-id", required=True)
    phase_transition.add_argument("--next-phase", required=True)
    phase_transition.add_argument("--authorization", type=Path, required=True)
    reserve = commands.add_parser("reserve-attempt")
    reserve.add_argument("--ledger", type=Path, required=True)
    reserve.add_argument("--protocol", type=Path, required=True)
    reserve.add_argument("--contract", type=Path, required=True)
    reserve.add_argument("--run-id", required=True)
    reserve.add_argument("--kind", required=True)
    reserve.add_argument("--arm")
    reserve.add_argument("--retry-reason")
    reserve.add_argument("--retry-of-attempt-id")
    reserve.add_argument("--parent-attempt-id")
    reserve.add_argument("--role")
    reserve.add_argument("--parent-digest")
    reserve.add_argument("--candidate-digest")
    reserve.add_argument("--finding-digest")
    reserve.add_argument("--repair-digest")
    reserve.add_argument("--authorization", type=Path, required=True)
    complete = commands.add_parser("complete-attempt")
    complete.add_argument("--ledger", type=Path, required=True)
    complete.add_argument("--protocol", type=Path, required=True)
    complete.add_argument("--contract", type=Path, required=True)
    complete.add_argument("--attempt-id", required=True)
    complete.add_argument("--status", required=True)
    complete.add_argument("--content-produced", action="store_true")
    complete.add_argument("--candidate-digest")
    complete.add_argument("--finding-digest")
    complete.add_argument("--repair-digest")
    complete.add_argument("--close-digest")
    complete.add_argument("--child-session")
    complete.add_argument("--input-tokens", type=int)
    complete.add_argument("--cached-input-tokens", type=int)
    complete.add_argument("--output-tokens", type=int)
    complete.add_argument("--reasoning-output-tokens", type=int)
    complete.add_argument("--raw-provider-output-sha256")
    complete.add_argument("--authorization", type=Path, required=True)
    service_start = commands.add_parser("start-service-transaction")
    service_start.add_argument("--ledger", type=Path, required=True)
    service_start.add_argument("--protocol", type=Path, required=True)
    service_start.add_argument("--contract", type=Path, required=True)
    service_start.add_argument("--attempt-id", required=True)
    service_start.add_argument("--event-type", required=True)
    service_start.add_argument("--transaction-id", required=True)
    service_start.add_argument("--authorization", type=Path, required=True)
    service_event = commands.add_parser("complete-service-transaction")
    service_event.add_argument("--ledger", type=Path, required=True)
    service_event.add_argument("--protocol", type=Path, required=True)
    service_event.add_argument("--contract", type=Path, required=True)
    service_event.add_argument("--attempt-id", required=True)
    service_event.add_argument("--event-type", required=True)
    service_event.add_argument("--transaction-id", required=True)
    service_event.add_argument("--evidence", type=Path, required=True)
    service_event.add_argument("--authorization", type=Path, required=True)
    seal_evidence = commands.add_parser("seal-run-evidence")
    seal_evidence.add_argument("--ledger", type=Path, required=True)
    seal_evidence.add_argument("--protocol", type=Path, required=True)
    seal_evidence.add_argument("--contract", type=Path, required=True)
    seal_evidence.add_argument("--run-id", required=True)
    seal_evidence.add_argument("--workspace-root", type=Path, required=True)
    seal_evidence.add_argument("--authorization", type=Path, required=True)
    receipt = commands.add_parser("verify-receipt")
    receipt.add_argument("--receipt", type=Path, required=True)
    receipt.add_argument("--protocol", type=Path, required=True)
    receipt.add_argument("--ledger", type=Path, required=True)
    receipt.add_argument("--contract", type=Path, required=True)
    receipt.add_argument("--workspace-root", type=Path, required=True)
    summary = commands.add_parser("verify-summary")
    summary.add_argument("--summary", type=Path, required=True)
    summary.add_argument("--protocol", type=Path, required=True)
    try:
        arguments = parser.parse_args()
        if arguments.command == "validate":
            protocol = _protocol(arguments.protocol)
            issues = validate_protocol(protocol, Path.cwd())
            structural_issues = [
                issue
                for issue in issues
                if issue.code
                not in {
                    "protocol.fixture-pending",
                    "protocol.evidence-contract-pending",
                }
            ]
            authorization_issues = (
                validate_execution_authorization(protocol, arguments.authorization)
                if arguments.authorization is not None
                else []
            )
            explicitly_authorized = (
                arguments.authorization is not None
                and not issues
                and not authorization_issues
            )
            formally_authorized = explicitly_authorized and execution_authorization_is_formal(
                arguments.authorization
            )
            _emit(
                {
                    "execution_ready": formally_authorized,
                    "experiment_authorized": formally_authorized,
                    "issues": [
                        _issue_payload(issue)
                        for issue in (*issues, *authorization_issues)
                    ],
                    "provider_authorized": formally_authorized,
                    "structurally_valid": not structural_issues,
                    "task2_commitment_bound": not any(
                        issue.code
                        in {
                            "protocol.fixture-pending",
                            "protocol.evidence-contract-pending",
                            "protocol.lock",
                        }
                        for issue in issues
                    ),
                }
            )
            return 1 if structural_issues or authorization_issues else 0
        if arguments.command == "start-run":
            start_run(
                arguments.ledger,
                _protocol(arguments.protocol),
                arguments.contract,
                run_id=arguments.run_id,
                authorization_path=arguments.authorization,
            )
            _emit({"run_id": arguments.run_id, "started": True})
            return 0
        if arguments.command == "transition-phase":
            transition_run_phase(
                arguments.ledger,
                _protocol(arguments.protocol),
                arguments.contract,
                run_id=arguments.run_id,
                next_phase=arguments.next_phase,
                authorization_path=arguments.authorization,
            )
            _emit({"next_phase": arguments.next_phase, "run_id": arguments.run_id})
            return 0
        if arguments.command == "reserve-attempt":
            reservation = reserve_provider_attempt(
                arguments.ledger,
                _protocol(arguments.protocol),
                AttemptRequest(
                    run_id=arguments.run_id,
                    kind=arguments.kind,
                    arm=arguments.arm,
                    retry_reason=arguments.retry_reason,
                    retry_of_attempt_id=arguments.retry_of_attempt_id,
                    parent_attempt_id=arguments.parent_attempt_id,
                    role=arguments.role,
                    parent_digest=arguments.parent_digest,
                    candidate_digest=arguments.candidate_digest,
                    finding_digest=arguments.finding_digest,
                    repair_digest=arguments.repair_digest,
                ),
                arguments.contract,
                authorization_path=arguments.authorization,
            )
            _emit(
                {
                    "attempt_id": reservation.attempt_id,
                    "attempts_started": reservation.attempts_started,
                }
            )
            return 0
        if arguments.command == "complete-attempt":
            token_values = {
                "input_tokens": arguments.input_tokens,
                "cached_input_tokens": arguments.cached_input_tokens,
                "output_tokens": arguments.output_tokens,
                "reasoning_output_tokens": arguments.reasoning_output_tokens,
            }
            token_usage = (
                None
                if all(value is None for value in token_values.values())
                else token_values
            )
            record_provider_completion(
                arguments.ledger,
                _protocol(arguments.protocol),
                AttemptCompletion(
                    arguments.attempt_id,
                    arguments.status,
                    arguments.content_produced,
                    candidate_digest=arguments.candidate_digest,
                    finding_digest=arguments.finding_digest,
                    repair_digest=arguments.repair_digest,
                    close_digest=arguments.close_digest,
                    child_session=arguments.child_session,
                    token_usage=token_usage,
                    raw_provider_output_sha256=arguments.raw_provider_output_sha256,
                ),
                arguments.contract,
                authorization_path=arguments.authorization,
            )
            _emit({"attempt_id": arguments.attempt_id, "recorded": True})
            return 0
        if arguments.command == "start-service-transaction":
            start_service_transaction(
                arguments.ledger,
                _protocol(arguments.protocol),
                arguments.contract,
                attempt_id=arguments.attempt_id,
                event_type=arguments.event_type,
                transaction_id=arguments.transaction_id,
                authorization_path=arguments.authorization,
            )
            _emit({"attempt_id": arguments.attempt_id, "started": True})
            return 0
        if arguments.command == "complete-service-transaction":
            record_service_transaction(
                arguments.ledger,
                _protocol(arguments.protocol),
                arguments.contract,
                attempt_id=arguments.attempt_id,
                event_type=arguments.event_type,
                transaction_id=arguments.transaction_id,
                evidence=_json_object(arguments.evidence, "service evidence"),
                authorization_path=arguments.authorization,
            )
            _emit({"attempt_id": arguments.attempt_id, "recorded": True})
            return 0
        if arguments.command == "seal-run-evidence":
            seal_run_evidence(
                arguments.ledger,
                _protocol(arguments.protocol),
                arguments.contract,
                run_id=arguments.run_id,
                workspace_root=arguments.workspace_root,
                authorization_path=arguments.authorization,
            )
            _emit({"run_id": arguments.run_id, "sealed": True})
            return 0
        if arguments.command == "verify-receipt":
            issues = verify_receipt(
                _json_object(arguments.receipt, "receipt"),
                _protocol(arguments.protocol),
                arguments.ledger,
                arguments.contract,
                arguments.workspace_root,
            )
        else:
            issues = verify_summary(
                _json_object(arguments.summary, "summary"),
                _protocol(arguments.protocol),
            )
        _emit({"issues": [_issue_payload(issue) for issue in issues]})
        return 1 if issues else 0
    except _CliUsageError as error:
        _emit_error("cli.usage", str(error))
        return 2
    except (OSError, ValueError, json.JSONDecodeError) as error:
        _emit_error("cli.input", _public_error_message(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
