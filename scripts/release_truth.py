#!/usr/bin/env python3
"""仓库受保护 Release workflow 使用的内部 Permanent Release Truth 适配器。"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
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

_PROTECTED_TAG_PATTERNS = (
    "refs/tags/release-truth/v*/certificate/g0",
    "refs/tags/v*",
)
_PROTECTED_TAG_RULES = ("deletion", "non_fast_forward", "update")
_RELEASE_TAG_PATTERN = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
_CANDIDATE_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
_GENERATION_PATTERN = re.compile(r"g(?:0|[1-9][0-9]*)")
_RELEASE_ENABLEMENT_FLAGS = (
    "RELEASE_BOOTSTRAP_ENABLED",
    "RELEASE_ENVIRONMENT_PROTECTION_VERIFIED",
    "RELEASE_TAG_RULESET_PROTECTION_VERIFIED",
)


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ReleaseTruthError(f"JSON artifact must be an object: {path}")
    return payload


def _create_json_exclusive(path: Path, payload: dict[str, Any]) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    return True


def _read_model(path: Path, model_type: type[BaseModel]) -> BaseModel:
    return model_type.model_validate(_read_json_object(path))


def _persist_model(path: Path, model: BaseModel) -> None:
    payload = model.model_dump(mode="json")
    if _create_json_exclusive(path, payload):
        return
    if _read_json_object(path) != payload:
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
        observed_at=args.observed_at,
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
    authority = _read_json_object(args.authority)
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
        connection.putheader("User-Agent", args.user_agent)
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


def _authority_check(args: argparse.Namespace) -> dict[str, Any]:
    admission = _read_json_object(args.admission)
    ref = _read_json_object(args.ref)
    tag = _read_json_object(args.tag)
    commit = _read_json_object(args.commit)
    release = _read_json_object(args.release)
    frozen = {key: value for key, value in admission.items() if key != "admission_digest"}
    encoded = json.dumps(
        frozen, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    expected_digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
    expected_draft = args.release_state == "draft"
    if admission.get("admission_digest") != expected_digest:
        raise ReleaseTruthError("frozen release admission digest differs")
    if (
        _CANDIDATE_SHA_PATTERN.fullmatch(
            str(admission.get("expected_candidate_sha", ""))
        )
        is None
        or admission.get("expected_candidate_sha") != admission.get("commit_sha")
    ):
        raise ReleaseTruthError(
            "expected candidate differs from frozen release admission commit"
        )
    if (
        ref.get("ref") != f"refs/tags/{args.release_tag}"
        or ref.get("object", {}).get("sha") != admission.get("tag_object_sha")
        or tag.get("sha") != admission.get("tag_object_sha")
        or tag.get("object", {}).get("type") != "commit"
        or tag.get("object", {}).get("sha") != admission.get("commit_sha")
        or commit.get("sha") != admission.get("commit_sha")
        or commit.get("tree", {}).get("sha") != admission.get("tree_sha")
        or release.get("id") != admission.get("numeric_release_id")
        or release.get("tag_name") != args.release_tag
        or release.get("target_commitish") != admission.get("commit_sha")
        or release.get("upload_url") != admission.get("upload_url")
        or release.get("draft") is not expected_draft
    ):
        raise ReleaseTruthError("live release authority differs from frozen admission")
    if args.release_state == "immutable" and release.get("immutable") is not True:
        raise ReleaseTruthError("immutable release authority is not immutable")
    return {"status": "success", "admission_digest": expected_digest}


def _release_generation_enabled(workflow_snapshot: Path) -> bool:
    """只接受三个发布开关均为规范字符串值的精确 workflow 快照。"""

    lines = workflow_snapshot.read_text(encoding="utf-8").splitlines()
    enabled: list[bool] = []
    for flag in _RELEASE_ENABLEMENT_FLAGS:
        candidates = [line for line in lines if line.startswith(f"  {flag}:")]
        if len(candidates) != 1:
            raise ReleaseTruthError(
                f"release workflow snapshot has invalid {flag} authority"
            )
        if candidates[0] == f'  {flag}: "true"':
            enabled.append(True)
        elif candidates[0] == f'  {flag}: "false"':
            enabled.append(False)
        else:
            raise ReleaseTruthError(
                f"release workflow snapshot has non-canonical {flag} authority"
            )
    return all(enabled)


def _run_authority_check(args: argparse.Namespace) -> dict[str, Any]:
    """在受信 Actions 历史内拒绝重复的已启用实际发布 dispatch。"""

    if _RELEASE_TAG_PATTERN.fullmatch(args.release_tag) is None:
        raise ReleaseTruthError("release tag is not canonical")
    if _GENERATION_PATTERN.fullmatch(args.generation) is None:
        raise ReleaseTruthError("release generation is not canonical")
    if _CANDIDATE_SHA_PATTERN.fullmatch(args.expected_candidate_sha) is None:
        raise ReleaseTruthError("expected candidate SHA is not canonical")
    if args.current_run_id <= 0:
        raise ReleaseTruthError("current workflow run ID must be positive")

    current_run = _read_json_object(args.current_run)
    expected_run_name = (
        f"release-admission|{args.release_tag}|{args.generation}"
    )
    workflow_id = current_run.get("workflow_id")
    if (
        isinstance(workflow_id, bool)
        or not isinstance(workflow_id, int)
        or workflow_id <= 0
        or current_run.get("id") != args.current_run_id
        or current_run.get("event") != "workflow_dispatch"
        or current_run.get("run_attempt") != 1
        or current_run.get("head_sha") != args.expected_candidate_sha
        or current_run.get("head_branch") != "main"
        or current_run.get("display_title") != expected_run_name
        or current_run.get("path") != args.workflow_path
    ):
        raise ReleaseTruthError("current release run authority differs")

    pages = json.loads(args.run_pages.read_text(encoding="utf-8"))
    if not isinstance(pages, list) or not pages:
        raise ReleaseTruthError("release run history is incomplete")
    expected_total: int | None = None
    runs: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, dict):
            raise ReleaseTruthError("release run history page is invalid")
        total_count = page.get("total_count")
        page_runs = page.get("workflow_runs")
        if (
            isinstance(total_count, bool)
            or not isinstance(total_count, int)
            or total_count < 0
            or not isinstance(page_runs, list)
            or not all(isinstance(run, dict) for run in page_runs)
        ):
            raise ReleaseTruthError("release run history page is invalid")
        if expected_total is None:
            expected_total = total_count
        elif total_count != expected_total:
            raise ReleaseTruthError("release run history changed during pagination")
        runs.extend(page_runs)
    if expected_total is None or len(runs) != expected_total:
        raise ReleaseTruthError("release run history is incomplete")
    run_ids = [run.get("id") for run in runs]
    if any(
        isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0
        for run_id in run_ids
    ) or len(set(run_ids)) != len(run_ids):
        raise ReleaseTruthError("release run history contains invalid identities")

    observed_matches = [
        run
        for run in runs
        if run.get("path") == args.workflow_path
        and run.get("event") == "workflow_dispatch"
        and run.get("display_title") == expected_run_name
    ]
    enabled_matches: list[dict[str, Any]] = []
    current_enabled = False
    for run in observed_matches:
        run_sha = run.get("head_sha")
        if (
            not isinstance(run_sha, str)
            or _CANDIDATE_SHA_PATTERN.fullmatch(run_sha) is None
        ):
            raise ReleaseTruthError(
                "release run history contains non-canonical candidate SHA"
            )
        snapshot = args.workflow_snapshots / f"{run_sha}.yml"
        if not snapshot.is_file():
            raise ReleaseTruthError("release workflow snapshot history is incomplete")
        is_enabled = _release_generation_enabled(snapshot)
        if run.get("id") == args.current_run_id:
            current_enabled = is_enabled
        if is_enabled:
            enabled_matches.append(run)
    if not current_enabled:
        raise ReleaseTruthError("current release workflow is not enabled")
    if (
        len(enabled_matches) != 1
        or enabled_matches[0].get("id") != args.current_run_id
    ):
        raise ReleaseTruthError(
            "actual release generation has already been dispatched"
        )

    authority = {
        "candidate_sha": args.expected_candidate_sha,
        "generation": args.generation,
        "release_tag": args.release_tag,
        "run_id": args.current_run_id,
        "run_name": expected_run_name,
        "workflow_id": workflow_id,
        "workflow_path": args.workflow_path,
    }
    if (
        not _create_json_exclusive(args.output, authority)
        and _read_json_object(args.output) != authority
    ):
        raise ReleaseTruthError("release run authority changed")
    return {"status": "success", **authority}


def _ruleset_check(args: argparse.Namespace) -> dict[str, Any]:
    rulesets = json.loads(args.rulesets.read_text(encoding="utf-8"))
    if not isinstance(rulesets, list):
        raise ReleaseTruthError("protective tag ruleset response is not a list")
    matches: list[dict[str, Any]] = []
    for ruleset in rulesets:
        if not isinstance(ruleset, dict):
            continue
        ref_name = ruleset.get("conditions", {}).get("ref_name", {})
        rule_types = tuple(
            sorted(
                rule.get("type")
                for rule in ruleset.get("rules", [])
                if isinstance(rule, dict) and isinstance(rule.get("type"), str)
            )
        )
        ruleset_id = ruleset.get("id")
        if (
            isinstance(ruleset_id, int)
            and not isinstance(ruleset_id, bool)
            and ruleset_id > 0
            and ruleset.get("target") == "tag"
            and ruleset.get("source") == args.repository
            and ruleset.get("source_type") == "Repository"
            and ruleset.get("enforcement") == "active"
            and tuple(sorted(ref_name.get("include", [])))
            == _PROTECTED_TAG_PATTERNS
            and ref_name.get("exclude") == []
            and rule_types == _PROTECTED_TAG_RULES
            and ruleset.get("bypass_actors") == []
            and ruleset.get("current_user_can_bypass") == "never"
        ):
            matches.append(ruleset)
    if len(matches) != 1:
        raise ReleaseTruthError(
            "expected exactly one active no-bypass protective tag ruleset"
        )
    match = matches[0]
    authority = {
        "repository": args.repository,
        "ruleset_id": match["id"],
        "ruleset_name": match.get("name"),
        "target": "tag",
        "enforcement": "active",
        "include": list(_PROTECTED_TAG_PATTERNS),
        "exclude": [],
        "rules": list(_PROTECTED_TAG_RULES),
        "bypass_actors": [],
        "current_user_can_bypass": "never",
    }
    encoded = json.dumps(
        authority, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    authority["ruleset_digest"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
    if (
        not _create_json_exclusive(args.output, authority)
        and _read_json_object(args.output) != authority
    ):
        raise ReleaseTruthError("protective tag ruleset authority changed")
    return {
        "status": "success",
        "ruleset_id": authority["ruleset_id"],
        "ruleset_digest": authority["ruleset_digest"],
    }


def _tag_authority_check(args: argparse.Namespace) -> dict[str, Any]:
    admission = _read_json_object(args.admission)
    ref = _read_json_object(args.ref)
    tag = _read_json_object(args.tag)
    commit = _read_json_object(args.commit)
    certificate_tag = admission.get("certificate_tag")
    if (
        not isinstance(certificate_tag, str)
        or ref.get("ref") != f"refs/tags/{certificate_tag}"
        or ref.get("object", {}).get("sha") != admission.get("tag_object_sha")
        or tag.get("sha") != admission.get("tag_object_sha")
        or tag.get("object", {}).get("type") != "commit"
        or tag.get("object", {}).get("sha") != admission.get("commit_sha")
        or commit.get("sha") != admission.get("commit_sha")
        or commit.get("tree", {}).get("sha") != admission.get("tree_sha")
    ):
        raise ReleaseTruthError("live Certificate tag authority differs")
    return {"status": "success", "certificate_tag": certificate_tag}


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
    publish_check.add_argument("--observed-at", required=True)

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
    upload_asset.add_argument("--user-agent", required=True)

    authority_check = subparsers.add_parser("authority-check")
    authority_check.add_argument("--admission", type=Path, required=True)
    authority_check.add_argument("--ref", type=Path, required=True)
    authority_check.add_argument("--tag", type=Path, required=True)
    authority_check.add_argument("--commit", type=Path, required=True)
    authority_check.add_argument("--release", type=Path, required=True)
    authority_check.add_argument("--release-tag", required=True)
    authority_check.add_argument(
        "--release-state", choices=("draft", "published", "immutable"), required=True
    )

    run_authority_check = subparsers.add_parser("run-authority-check")
    run_authority_check.add_argument("--current-run", type=Path, required=True)
    run_authority_check.add_argument("--run-pages", type=Path, required=True)
    run_authority_check.add_argument(
        "--workflow-snapshots", type=Path, required=True
    )
    run_authority_check.add_argument("--release-tag", required=True)
    run_authority_check.add_argument("--generation", required=True)
    run_authority_check.add_argument("--expected-candidate-sha", required=True)
    run_authority_check.add_argument("--current-run-id", type=int, required=True)
    run_authority_check.add_argument("--workflow-path", required=True)
    run_authority_check.add_argument("--output", type=Path, required=True)

    ruleset_check = subparsers.add_parser("ruleset-check")
    ruleset_check.add_argument("--rulesets", type=Path, required=True)
    ruleset_check.add_argument("--repository", required=True)
    ruleset_check.add_argument("--output", type=Path, required=True)

    tag_authority_check = subparsers.add_parser("tag-authority-check")
    tag_authority_check.add_argument("--admission", type=Path, required=True)
    tag_authority_check.add_argument("--ref", type=Path, required=True)
    tag_authority_check.add_argument("--tag", type=Path, required=True)
    tag_authority_check.add_argument("--commit", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    handlers = {
        "proof": _proof,
        "publish-check": _publish_check,
        "certificate": _certificate,
        "upload-asset": _upload_asset,
        "authority-check": _authority_check,
        "run-authority-check": _run_authority_check,
        "ruleset-check": _ruleset_check,
        "tag-authority-check": _tag_authority_check,
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
