#!/usr/bin/env python3
"""Validate the public AI-SDLC 1.0.0 release identity."""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

CURRENT_REPOSITORY_URL = "https://github.com/SinclairPan/Ai_AutoSDLC"
CURRENT_VERSION = "1.0.0"

PUBLIC_DOC_PATHS = {
    "docs/enterprise-agentops-setup.zh-CN.md",
    "docs/framework-defect-backlog.zh-CN.md",
    "docs/product-contract.md",
    "docs/pull-request-checklist.zh.md",
    "docs/框架自迭代开发与发布约定.md",
}

REQUIRED_SURFACES: dict[str, tuple[str, ...]] = {
    "README.md": (CURRENT_REPOSITORY_URL, CURRENT_VERSION),
    "USER_GUIDE.zh-CN.md": (CURRENT_REPOSITORY_URL, CURRENT_VERSION),
    "docs/product-contract.md": (CURRENT_REPOSITORY_URL, CURRENT_VERSION),
    "packaging/offline/README.md": (CURRENT_REPOSITORY_URL, CURRENT_VERSION),
    "packaging/offline/RELEASE_CHECKLIST.md": (
        CURRENT_REPOSITORY_URL,
        CURRENT_VERSION,
    ),
}

PUBLIC_ROOT_MARKDOWN = {
    "AGENTS.md",
    "autopilot.md",
    "README.md",
    "USER_GUIDE.zh-CN.md",
}

PATH_RULES = (
    (re.compile(r"^specs/"), "non-public-work-state"),
    (re.compile(r"^\.ai-sdlc/work-items/"), "non-public-work-state"),
    (re.compile(r"^\.ai-sdlc/project/(?:generated|memory)/"), "generated-state"),
    (
        re.compile(r"^\.ai-sdlc/state/(?:codex-handoff|repo-facts|resume-pack)"),
        "runtime-state",
    ),
    (re.compile(r"\bv0\.\d", re.IGNORECASE), "pre-1.0-product-version"),
)

TEXT_RULES = (
    (re.compile(r"\bv0\.\d", re.IGNORECASE), "pre-1.0-product-version"),
    (
        re.compile(r"ai-sdlc-offline-0\.\d", re.IGNORECASE),
        "pre-1.0-product-version",
    ),
    (
        re.compile(
            r"(?:ai[-_]sdlc|AI_SDLC|__version__|installed_version|latest_version)"
            r"[^\n]{0,80}\b0\.\d+\.\d+",
            re.IGNORECASE,
        ),
        "pre-1.0-product-version",
    ),
    (
        re.compile(r"\b0\.\d+\.\d+[^\n]{0,80}(?:ai-sdlc|ai_sdlc)", re.IGNORECASE),
        "pre-1.0-product-version",
    ),
    (
        re.compile(
            r"(?:/Users/[A-Za-z0-9._-]+/(?:project|projects|workspace)/|"
            r"[A-Za-z]:\\Users\\[^\\\s]+\\(?:project|projects|workspace)\\)",
            re.IGNORECASE,
        ),
        "local-path-disclosure",
    ),
)

GITHUB_REPOSITORY_PATTERN = re.compile(
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
)
IDENTITY_PATHS = set(REQUIRED_SURFACES)


@dataclass(frozen=True)
class Finding:
    """One public release identity violation."""

    path: str
    line: int | None
    marker: str
    excerpt: str


def tracked_paths(root: Path) -> tuple[str, ...]:
    """Return Git-tracked paths relative to ``root``."""

    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return tuple(item.decode("utf-8") for item in completed.stdout.split(b"\0") if item)


def scan_paths(root: Path, files: Mapping[str, str]) -> list[Finding]:
    """Scan supplied relative paths and decoded contents."""

    del root
    findings: list[Finding] = []
    for path, text in files.items():
        has_path_finding = False
        if (
            "/" not in path
            and path.lower().endswith(".md")
            and path not in PUBLIC_ROOT_MARKDOWN
        ):
            findings.append(Finding(path, None, "non-public-root-doc", path))
            has_path_finding = True
        if "/" not in path and path.lower().endswith((".yaml", ".yml")):
            findings.append(Finding(path, None, "non-public-root-state", path))
            has_path_finding = True
        for pattern, marker in PATH_RULES:
            if pattern.search(path):
                findings.append(Finding(path, None, marker, path))
                has_path_finding = True
        if (
            not has_path_finding
            and path.startswith("docs/")
            and path not in PUBLIC_DOC_PATHS
        ):
            findings.append(Finding(path, None, "non-public-doc", path))

        for line_number, line in enumerate(text.splitlines(), start=1):
            for pattern, marker in TEXT_RULES:
                if pattern.search(line):
                    findings.append(
                        Finding(path, line_number, marker, line.strip()[:240])
                    )
            if path in IDENTITY_PATHS:
                for repository_url in GITHUB_REPOSITORY_PATTERN.findall(line):
                    normalized_url = repository_url.rstrip("/")
                    if normalized_url.endswith(".git"):
                        normalized_url = normalized_url[:-4]
                    if normalized_url != CURRENT_REPOSITORY_URL:
                        findings.append(
                            Finding(
                                path,
                                line_number,
                                "repository-identity-mismatch",
                                repository_url,
                            )
                        )
    return findings


def validate_required_surfaces(files: Mapping[str, str]) -> list[Finding]:
    """Require the current repository and version on release identity surfaces."""

    findings: list[Finding] = []
    for path, required_markers in REQUIRED_SURFACES.items():
        text = files.get(path)
        if text is None:
            findings.append(
                Finding(path, None, "required-public-surface-missing", path)
            )
            continue
        for marker in required_markers:
            if marker not in text:
                findings.append(
                    Finding(path, None, "required-identity-marker-missing", marker)
                )
    return findings


def scan_public_tree(root: Path) -> list[Finding]:
    """Scan all tracked, decodable files in the public tree."""

    files: dict[str, str] = {}
    for relative in tracked_paths(root):
        try:
            files[relative] = (root / relative).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
    return [*scan_paths(root, files), *validate_required_surfaces(files)]


def main(argv: Sequence[str] | None = None) -> int:
    """Run the public release identity check."""

    arguments = list(argv or sys.argv[1:])
    root = Path(arguments[0] if arguments else ".").resolve()
    findings = scan_public_tree(root)
    for finding in findings:
        location = (
            finding.path if finding.line is None else f"{finding.path}:{finding.line}"
        )
        print(f"{location}: {finding.marker}: {finding.excerpt}")
    if findings:
        return 1
    print("PUBLIC_RELEASE_IDENTITY_VALID")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
