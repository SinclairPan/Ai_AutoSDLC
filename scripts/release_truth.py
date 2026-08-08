#!/usr/bin/env python3
"""仓库受保护 Release workflow 使用的内部 Permanent Release Truth 适配器。"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

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


def _upload_asset(args: argparse.Namespace) -> dict[str, Any]:
    authority = read_json_object(args.authority)
    release_id = authority.get("id")
    if isinstance(release_id, bool) or not isinstance(release_id, int) or release_id <= 0:
        raise ReleaseTruthError("release authority does not contain a numeric release ID")

    repository_parts = args.repository.split("/")
    if len(repository_parts) != 2 or not all(repository_parts):
        raise ReleaseTruthError("repository must use the OWNER/REPO form")
    expected_upload_url = (
        f"https://uploads.github.com/repos/{args.repository}/releases/"
        f"{release_id}/assets{{?name,label}}"
    )
    if authority.get("upload_url") != expected_upload_url:
        raise ReleaseTruthError(
            f"release upload_url is not bound to frozen release ID {release_id}"
        )

    asset_path = args.asset
    asset_name = args.name or asset_path.name
    if not asset_name:
        raise ReleaseTruthError("release asset name must not be empty")
    token = os.environ.get("GH_TOKEN")
    if not token:
        raise ReleaseTruthError("GH_TOKEN is required to upload a release asset")

    asset_size = asset_path.stat().st_size
    query = {"name": asset_name}
    if args.label is not None:
        query["label"] = args.label
    endpoint = expected_upload_url.removesuffix("{?name,label}")
    parsed = urlsplit(endpoint)
    request_target = f"{parsed.path}?{urlencode(query)}"
    connection = http.client.HTTPSConnection(
        parsed.hostname,
        parsed.port,
        timeout=600,
    )
    try:
        connection.putrequest("POST", request_target)
        connection.putheader("Accept", "application/vnd.github+json")
        connection.putheader("Authorization", f"Bearer {token}")
        connection.putheader("User-Agent", "Ai-AutoSDLC-release-truth-writer")
        connection.putheader("X-GitHub-Api-Version", "2022-11-28")
        connection.putheader("Content-Type", "application/octet-stream")
        connection.putheader("Content-Length", str(asset_size))
        connection.endheaders()
        with asset_path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                connection.send(chunk)
        response = connection.getresponse()
        response_body = response.read()
    finally:
        connection.close()

    if response.status != 201:
        detail = response_body.decode("utf-8", errors="replace")[:500]
        raise ReleaseTruthError(
            f"release asset upload failed with HTTP {response.status}: {detail}"
        )
    uploaded = json.loads(response_body)
    if uploaded.get("name") != asset_name or uploaded.get("size") != asset_size:
        raise ReleaseTruthError("uploaded asset response differs from requested asset")
    return {
        "status": "success",
        "release_id": release_id,
        "asset_id": uploaded.get("id"),
        "asset_name": asset_name,
        "size_bytes": asset_size,
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

    upload_asset = subparsers.add_parser("upload-asset")
    upload_asset.add_argument("--authority", type=Path, required=True)
    upload_asset.add_argument("--asset", type=Path, required=True)
    upload_asset.add_argument("--repository", required=True)
    upload_asset.add_argument("--name")
    upload_asset.add_argument("--label")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    handlers = {
        "proof": _proof,
        "publish-check": _publish_check,
        "certificate": _certificate,
        "upload-asset": _upload_asset,
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
