"""Offline command wrapper for the frozen AI-SDLC v2 benefit benchmark."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path

from ai_sdlc.benefit_benchmark import (
    AttemptCompletion,
    AttemptRequest,
    load_protocol,
    record_provider_completion,
    reserve_provider_attempt,
    validate_protocol,
    verify_receipt,
    verify_summary,
)


def _json_object(path: Path) -> Mapping[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _emit(payload: Mapping[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
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
    receipt = commands.add_parser("verify-receipt")
    receipt.add_argument("--receipt", type=Path, required=True)
    receipt.add_argument("--protocol", type=Path, required=True)
    receipt.add_argument("--ledger", type=Path, required=True)
    summary = commands.add_parser("verify-summary")
    summary.add_argument("--summary", type=Path, required=True)
    summary.add_argument("--protocol", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        if arguments.command == "validate":
            protocol = load_protocol(arguments.protocol)
            issues = validate_protocol(protocol, Path.cwd())
            _emit({"issues": [issue.__dict__ for issue in issues]})
            return 1 if any(issue.code != "protocol.fixture-pending" for issue in issues) else 0
        if arguments.command == "reserve-attempt":
            reservation = reserve_provider_attempt(
                arguments.ledger,
                load_protocol(arguments.protocol),
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
            record_provider_completion(
                arguments.ledger,
                load_protocol(arguments.protocol),
                AttemptCompletion(
                    arguments.attempt_id,
                    arguments.status,
                    arguments.content_produced,
                    candidate_digest=arguments.candidate_digest,
                    finding_digest=arguments.finding_digest,
                    repair_digest=arguments.repair_digest,
                    close_digest=arguments.close_digest,
                ),
            )
            _emit({"attempt_id": arguments.attempt_id, "recorded": True})
            return 0
        if arguments.command == "verify-receipt":
            _ = arguments.ledger
            issues = verify_receipt(_json_object(arguments.receipt), load_protocol(arguments.protocol))
        else:
            issues = verify_summary(_json_object(arguments.summary), load_protocol(arguments.protocol))
        _emit({"issues": [issue.__dict__ for issue in issues]})
        return 1 if issues else 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        _emit({"error": str(error)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
