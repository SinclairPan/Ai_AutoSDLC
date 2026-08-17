#!/usr/bin/env python3
"""Verify independent, exact-commit reviewer attestations."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

EXPECTED_ROLES = {
    "requirements-copy",
    "interaction-accessibility",
    "visual-offline-delivery",
}
EXPECTED_INPUT_PATHS = (
    "docs/product-site/design/qa/package-manifest.sha256",
    "docs/product-site/design/qa/browser-acceptance-receipt.json",
    "scripts/run_offline_product_site_browser_acceptance.mjs",
    "docs/product-site/content/offline-product-site-copy-v1.md",
    "docs/product-site/content/USER_GUIDE.zh-CN.md",
    "docs/product-site/design/offline-product-site-visual-design-spec-v1.md",
    "docs/product-site/design/homepage-direction-v2-approved.png",
    "docs/product-site/design/qa/home-1440x900.png",
    "docs/product-site/design/qa/home-1366x768.png",
    "docs/product-site/design/qa/home-1280x800.png",
    "docs/product-site/design/qa/home-1024x768.png",
    "docs/product-site/design/qa/home-390x844.png",
    "docs/product-site/design/qa/loop-1366x768.png",
    "docs/product-site/design/qa/expert-review-1366x768.png",
    "docs/product-site/design/qa/platform-1366x768.png",
    "docs/product-site/design/qa/downloads-1366x768.png",
    "docs/product-site/design/qa/guide-1366x768.png",
    "docs/product-site/design/qa/guide-390x844.png",
)
CANONICAL_RULE = (
    "SHA-256 of UTF-8 file bytes after replacing the Canonical content SHA256 "
    "value with 64 ASCII zeroes."
)
HASH_LINE = re.compile(r"^Canonical content SHA256: `([0-9a-f]{64})`$", re.MULTILINE)
INPUT_LINE = re.compile(r"^`([^`]+)`: `([0-9a-f]{64})`$", re.MULTILINE)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def field(content: str, label: str) -> str | None:
    match = re.search(rf"^{re.escape(label)}: `([^`]+)`$", content, re.MULTILINE)
    return match.group(1) if match else None


def git_show(commit: str, relative_path: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{commit}:{relative_path}"],
        check=True,
        capture_output=True,
    ).stdout


def expected_inputs(reviewed_commit: str, fixture_mode: bool) -> dict[str, str]:
    if fixture_mode:
        return {"fixture": "a" * 64}
    resolved = subprocess.run(
        ["git", "rev-parse", f"{reviewed_commit}^{{commit}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if resolved != reviewed_commit:
        raise ValueError("reviewed commit must be the full resolved commit SHA")
    return {
        relative_path: sha256(git_show(reviewed_commit, relative_path))
        for relative_path in EXPECTED_INPUT_PATHS
    }


def verify_file(
    path: Path, reviewed_commit: str, inputs: dict[str, str]
) -> tuple[str | None, str | None, list[str]]:
    content = path.read_text(encoding="utf-8")
    errors: list[str] = []
    role = field(content, "Reviewer role")
    reviewer_id = field(content, "Reviewer ID")
    task_id = field(content, "Reviewer task")
    if role not in EXPECTED_ROLES:
        errors.append("unexpected reviewer role")
    if not reviewer_id or not task_id:
        errors.append("reviewer identity/task missing")
    if field(content, "Reviewed product baseline") != reviewed_commit:
        errors.append("reviewed commit mismatch")
    timestamp = field(content, "Reviewed at UTC") or ""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", timestamp):
        errors.append("invalid UTC timestamp")
    if field(content, "Verdict") != "PASS" or field(content, "Finding count") != "0":
        errors.append("verdict is not PASS with zero findings")
    if field(content, "Canonical hash rule") != CANONICAL_RULE:
        errors.append("canonical hash rule drifted")
    hash_match = HASH_LINE.search(content)
    if not hash_match:
        errors.append("canonical hash missing")
    else:
        canonical = HASH_LINE.sub(
            f"Canonical content SHA256: `{'0' * 64}`", content, count=1
        )
        if sha256(canonical.encode("utf-8")) != hash_match.group(1):
            errors.append("canonical content hash mismatch")
    observed_inputs = dict(INPUT_LINE.findall(content))
    if observed_inputs != inputs:
        errors.append("input hash block mismatch")
    for heading in ("## Scope", "## Independent verification", "## Findings"):
        if heading not in content:
            errors.append(f"missing section {heading}")
    if not re.search(r"^## Findings\n\nNone\.$", content, re.MULTILINE):
        errors.append("findings section must be exactly None")
    return role, f"{reviewer_id}|{task_id}" if reviewer_id and task_id else None, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reviewed-commit", required=True)
    parser.add_argument("--reviewer-dir", required=True, type=Path)
    parser.add_argument("--fixture-mode", choices=("true", "false"), default="false")
    args = parser.parse_args()
    errors: list[str] = []
    try:
        inputs = expected_inputs(args.reviewed_commit, args.fixture_mode == "true")
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(f"ATTESTATION_INVALID inputs: {exc}", file=sys.stderr)
        return 1
    files = sorted(args.reviewer_dir.glob("*.md"))
    if len(files) != 3:
        errors.append(f"expected 3 reviewer files, found {len(files)}")
    roles: list[str] = []
    identities: list[str] = []
    for path in files:
        role, identity, file_errors = verify_file(path, args.reviewed_commit, inputs)
        if role:
            roles.append(role)
        if identity:
            identities.append(identity)
        errors.extend(f"{path.name}: {error}" for error in file_errors)
    if set(roles) != EXPECTED_ROLES or len(roles) != len(set(roles)):
        errors.append("reviewer roles are incomplete or duplicated")
    if len(identities) != len(set(identities)):
        errors.append("reviewer identities/tasks are duplicated")
    if errors:
        for error in errors:
            print(f"ATTESTATION_INVALID {error}", file=sys.stderr)
        return 1
    print(
        f"REVIEWER_ATTESTATIONS_VALID commit={args.reviewed_commit} "
        f"files={len(files)} inputs={len(inputs)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
