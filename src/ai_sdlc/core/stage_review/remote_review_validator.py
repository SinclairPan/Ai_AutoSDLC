"""受限子进程内执行的固定远端 Reviewer 响应校验器。"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def _validate_response(
    response_path: Path,
    expected_digest: str,
    result_path: Path,
) -> dict[str, object]:
    payload = json.loads(response_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("remote review response must be an object")
    if _digest(payload) != expected_digest:
        raise ValueError("remote review response digest diverged")
    _validate_shape(payload)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return payload


def _digest(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _validate_shape(payload: dict[str, object]) -> None:
    if set(payload) != {"provider_call_id", "review", "accounted_usage"}:
        raise ValueError("remote review response fields are invalid")
    if not isinstance(payload["provider_call_id"], str):
        raise ValueError("remote provider call identity is invalid")
    review = payload["review"]
    usage = payload["accounted_usage"]
    if not isinstance(review, dict) or not isinstance(usage, dict):
        raise ValueError("remote review response shape is invalid")
    _validate_review(review)
    _validate_accounted_usage(usage)


def _validate_review(review: dict[str, object]) -> None:
    allowed = {"schema_version", "verdict", "coverage", "findings", "evidence_digests"}
    if not {"verdict", "coverage", "findings", "evidence_digests"} <= set(review):
        raise ValueError("remote review output is incomplete")
    if not set(review) <= allowed:
        raise ValueError("remote review output has unknown fields")
    findings = review["findings"]
    evidence = review["evidence_digests"]
    if not isinstance(findings, list) or not isinstance(evidence, list):
        raise ValueError("remote review collections are invalid")
    verdict = review["verdict"]
    if verdict not in {"passed", "findings"} or ((verdict == "findings") != bool(findings)):
        raise ValueError("remote review verdict is invalid")
    if not isinstance(review["coverage"], dict) or not evidence:
        raise ValueError("remote review evidence is incomplete")


def _validate_accounted_usage(usage: dict[str, object]) -> None:
    if set(usage) != {"schema_version", "amounts", "basis"}:
        raise ValueError("remote provider accounted usage fields are invalid")
    amounts = usage["amounts"]
    basis = usage["basis"]
    if not isinstance(amounts, dict) or not isinstance(basis, dict):
        raise ValueError("remote provider accounted usage is invalid")
    _validate_usage_amounts(amounts)
    _validate_usage_basis(basis)


def _validate_usage_amounts(usage: dict[str, object]) -> None:
    required = {
        "provider_calls",
        "review_passes",
        "tokens",
        "cost",
        "active_wall_clock",
    }
    if not required <= set(usage):
        raise ValueError("remote provider usage is incomplete")
    if usage["provider_calls"] != 1:
        raise ValueError("remote provider call count is invalid")
    if usage["review_passes"] != 1:
        raise ValueError("remote review pass count is invalid")
    for field in required - {"provider_calls", "review_passes"}:
        value = usage[field]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            raise ValueError("remote provider usage is invalid")


def _validate_usage_basis(basis: dict[str, object]) -> None:
    sources = ("token_source", "cost_source", "active_wall_clock_source")
    if any(basis.get(field) not in {"metered", "estimated"} for field in sources):
        raise ValueError("remote provider usage basis is invalid")
    estimated = any(basis.get(field) == "estimated" for field in sources)
    policy = (
        basis.get("estimation_policy_id"),
        basis.get("estimation_policy_version"),
        basis.get("estimation_policy_digest"),
    )
    if estimated != all(isinstance(value, str) and value for value in policy):
        raise ValueError("remote provider usage estimate lineage is invalid")
    input_characters = basis.get("input_characters")
    output_characters = basis.get("output_characters")
    if (
        not isinstance(input_characters, int)
        or isinstance(input_characters, bool)
        or not isinstance(output_characters, int)
        or isinstance(output_characters, bool)
    ):
        raise ValueError("remote provider usage estimate inputs are invalid")
    if estimated != (input_characters + output_characters > 0):
        raise ValueError("remote provider usage estimate inputs are incomplete")


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 3:
        return 2
    try:
        result = _validate_response(
            Path(arguments[0]),
            arguments[1],
            Path(arguments[2]),
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 3
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
