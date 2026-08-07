#!/usr/bin/env python3
"""仓库受保护 Release workflow 使用的内部 Permanent Release Truth 适配器。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from ai_sdlc.core.release_truth import (
    ReleaseTruthError,
    build_release_certificate,
    build_release_satisfaction_proof,
    validate_publish_claim,
)
from ai_sdlc.core.release_truth_models import (
    PublishedReleaseSnapshot,
    ReleaseCandidateSnapshot,
    ReleaseSatisfactionProof,
)
from ai_sdlc.core.stage_review.artifacts import (
    create_json_exclusive,
    read_json_object,
)


def _read_model(path: Path, model_type: type[BaseModel]) -> BaseModel:
    return model_type.model_validate(read_json_object(path))


def _persist_model(path: Path, model: BaseModel) -> None:
    payload = model.model_dump(mode="json")
    if create_json_exclusive(path, payload):
        return
    if read_json_object(path) != payload:
        raise ReleaseTruthError(f"immutable artifact fork: {path.name}")


def _proof(args: argparse.Namespace) -> dict[str, Any]:
    snapshot = _read_model(args.snapshot, ReleaseCandidateSnapshot)
    assert isinstance(snapshot, ReleaseCandidateSnapshot)
    proof = build_release_satisfaction_proof(snapshot)
    _persist_model(args.output, proof)
    return {"status": "success", "proof_digest": proof.proof_digest}


def _publish_check(args: argparse.Namespace) -> dict[str, Any]:
    proof = _read_model(args.proof, ReleaseSatisfactionProof)
    snapshot = _read_model(args.snapshot, ReleaseCandidateSnapshot)
    assert isinstance(proof, ReleaseSatisfactionProof)
    assert isinstance(snapshot, ReleaseCandidateSnapshot)
    validate_publish_claim(
        proof,
        snapshot,
        caller_workflow_ref=args.caller_workflow_ref,
        caller_run_id=args.caller_run_id,
        caller_run_attempt=args.caller_run_attempt,
    )
    return {"status": "success", "proof_digest": proof.proof_digest}


def _certificate(args: argparse.Namespace) -> dict[str, Any]:
    proof = _read_model(args.proof, ReleaseSatisfactionProof)
    published = _read_model(args.published, PublishedReleaseSnapshot)
    assert isinstance(proof, ReleaseSatisfactionProof)
    assert isinstance(published, PublishedReleaseSnapshot)
    certificate = build_release_certificate(
        proof,
        published,
        release_attestation_digest=args.attestation_digest,
        issued_at=args.issued_at,
    )
    _persist_model(args.output, certificate)
    return {
        "status": "success",
        "certificate_digest": certificate.certificate_digest,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Internal protected Release Truth workflow adapter."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    proof = subparsers.add_parser("proof")
    proof.add_argument("--snapshot", type=Path, required=True)
    proof.add_argument("--output", type=Path, required=True)

    publish_check = subparsers.add_parser("publish-check")
    publish_check.add_argument("--proof", type=Path, required=True)
    publish_check.add_argument("--snapshot", type=Path, required=True)
    publish_check.add_argument("--caller-workflow-ref", required=True)
    publish_check.add_argument("--caller-run-id", type=int, required=True)
    publish_check.add_argument("--caller-run-attempt", type=int, required=True)

    certificate = subparsers.add_parser("certificate")
    certificate.add_argument("--proof", type=Path, required=True)
    certificate.add_argument("--published", type=Path, required=True)
    certificate.add_argument("--attestation-digest", required=True)
    certificate.add_argument("--issued-at", required=True)
    certificate.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    handlers = {
        "proof": _proof,
        "publish-check": _publish_check,
        "certificate": _certificate,
    }
    try:
        result = handlers[args.command](args)
    except (
        FileNotFoundError,
        json.JSONDecodeError,
        ValidationError,
        ValueError,
    ) as exc:
        print(f"release truth: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
