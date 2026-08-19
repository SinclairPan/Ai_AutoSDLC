"""Offline command wrapper for the frozen AI-SDLC v2 benefit benchmark."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path

from ai_sdlc.benefit_benchmark import (
    ArtifactRequirement,
    AttemptCompletion,
    AttemptRequest,
    BenchmarkIssue,
    RunEvidenceRequest,
    load_protocol,
    record_provider_completion,
    record_run_evidence,
    redact_public_message,
    reserve_provider_attempt,
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


def _run_evidence_request(
    manifest: Mapping[str, object], *, run_id: str, workspace_root: Path
) -> RunEvidenceRequest:
    expected = {
        "phase_evidence",
        "artifacts",
        "changed_files",
        "automated_events",
        "human_events",
    }
    if set(manifest) != expected:
        raise ValueError("run evidence manifest has invalid fields")
    artifacts = manifest.get("artifacts")
    changed_files = manifest.get("changed_files")
    automated_events = manifest.get("automated_events")
    human_events = manifest.get("human_events")
    phases = manifest.get("phase_evidence")
    if (
        not isinstance(phases, Mapping)
        or not isinstance(artifacts, list)
        or not isinstance(changed_files, list)
        or not all(isinstance(path, str) for path in changed_files)
        or not isinstance(automated_events, list)
        or not all(isinstance(event, Mapping) for event in automated_events)
        or not isinstance(human_events, list)
        or not all(isinstance(event, Mapping) for event in human_events)
    ):
        raise ValueError("run evidence manifest has invalid values")
    requirements: list[ArtifactRequirement] = []
    for artifact in artifacts:
        if not isinstance(artifact, Mapping) or set(artifact) != {
            "path",
            "category",
            "required",
            "applicable",
        }:
            raise ValueError("run evidence artifact rule is invalid")
        path = artifact.get("path")
        category = artifact.get("category")
        required = artifact.get("required")
        applicable = artifact.get("applicable")
        if (
            not isinstance(path, str)
            or not isinstance(category, str)
            or not isinstance(required, bool)
            or not isinstance(applicable, bool)
        ):
            raise ValueError("run evidence artifact rule is invalid")
        requirements.append(
            ArtifactRequirement(path, category, required, applicable)
        )
    return RunEvidenceRequest(
        run_id=run_id,
        workspace_root=workspace_root,
        phase_evidence={
            str(name): dict(value)
            for name, value in phases.items()
            if isinstance(value, Mapping)
        },
        artifacts=tuple(requirements),
        changed_files=tuple(changed_files),
        automated_events=tuple(dict(event) for event in automated_events),
        human_events=tuple(dict(event) for event in human_events),
    )


def _emit(payload: Mapping[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _emit_error(code: str, message: str) -> None:
    _emit({"error": {"code": code, "message": message}})


def main() -> int:
    parser = _JsonArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--protocol", type=Path, required=True)
    reserve = commands.add_parser("reserve-attempt")
    reserve.add_argument("--ledger", type=Path, required=True)
    reserve.add_argument("--protocol", type=Path, required=True)
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
    complete = commands.add_parser("complete-attempt")
    complete.add_argument("--ledger", type=Path, required=True)
    complete.add_argument("--protocol", type=Path, required=True)
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
    run_evidence = commands.add_parser("record-run-evidence")
    run_evidence.add_argument("--ledger", type=Path, required=True)
    run_evidence.add_argument("--protocol", type=Path, required=True)
    run_evidence.add_argument("--run-id", required=True)
    run_evidence.add_argument("--workspace-root", type=Path, required=True)
    run_evidence.add_argument("--manifest", type=Path, required=True)
    receipt = commands.add_parser("verify-receipt")
    receipt.add_argument("--receipt", type=Path, required=True)
    receipt.add_argument("--protocol", type=Path, required=True)
    receipt.add_argument("--ledger", type=Path, required=True)
    summary = commands.add_parser("verify-summary")
    summary.add_argument("--summary", type=Path, required=True)
    summary.add_argument("--protocol", type=Path, required=True)
    try:
        arguments = parser.parse_args()
        if arguments.command == "validate":
            protocol = _protocol(arguments.protocol)
            issues = validate_protocol(protocol, Path.cwd())
            structural_issues = [
                issue for issue in issues if issue.code != "protocol.fixture-pending"
            ]
            _emit(
                {
                    "execution_ready": not issues,
                    "issues": [_issue_payload(issue) for issue in issues],
                    "structurally_valid": not structural_issues,
                }
            )
            return 1 if structural_issues else 0
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
            token_usage = None if all(value is None for value in token_values.values()) else token_values
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
            )
            _emit({"attempt_id": arguments.attempt_id, "recorded": True})
            return 0
        if arguments.command == "record-run-evidence":
            record_run_evidence(
                arguments.ledger,
                _protocol(arguments.protocol),
                _run_evidence_request(
                    _json_object(arguments.manifest, "run evidence manifest"),
                    run_id=arguments.run_id,
                    workspace_root=arguments.workspace_root,
                ),
            )
            _emit({"recorded": True, "run_id": arguments.run_id})
            return 0
        if arguments.command == "verify-receipt":
            issues = verify_receipt(
                _json_object(arguments.receipt, "receipt"),
                _protocol(arguments.protocol),
                arguments.ledger,
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
