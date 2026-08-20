#!/usr/bin/env python3
"""校验 AI-SDLC 3.0.0 之后强制执行的新用户手册矩阵。"""

from __future__ import annotations

import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

BASELINE_VERSION = (3, 0, 0)
MATRIX_MARKER = "<!-- AI-SDLC-USER-GUIDE-MATRIX: 2x2x3=12 -->"
PROJECT_STATES = ("new", "existing")
INSTALL_CHANNELS = ("online", "offline")
PLATFORMS = ("windows-amd64", "macos-arm64", "linux-amd64")
REQUIRED_STEPS = (
    "prerequisites",
    "acquire",
    "verify",
    "install",
    "initialize",
    "success",
    "recover",
)
EXPECTED_ROUTE_IDS = tuple(
    f"{state}|{channel}|{platform}"
    for state in PROJECT_STATES
    for channel in INSTALL_CHANNELS
    for platform in PLATFORMS
)

_VERSION_PATTERN = re.compile(
    r'^version\s*=\s*"(\d+)\.(\d+)\.(\d+)(?:[^\"]*)"\s*$', re.MULTILINE
)
_ROUTE_PATTERN = re.compile(r"<!--\s*AI-SDLC-USER-GUIDE-ROUTE:\s*([^\s]+)\s*-->")
_STEP_PATTERN = re.compile(r"<!--\s*AI-SDLC-USER-GUIDE-STEP:\s*([a-z-]+)\s*-->")


@dataclass(frozen=True)
class Finding:
    """一项用户手册发布合同违规。"""

    marker: str
    excerpt: str


def parse_project_version(pyproject_text: str) -> tuple[int, int, int] | None:
    """返回用于激活合同的三段式项目版本。"""

    match = _VERSION_PATTERN.search(pyproject_text)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def validate_standard_text(text: str) -> list[Finding]:
    """在合同正式生效前也保证所记录的规范本身完整。"""

    findings: list[Finding] = []
    required_markers = (
        "2 × 2 × 3 = 12",
        "3.0.0` 之后的首个版本",
        MATRIX_MARKER,
        *EXPECTED_ROUTE_IDS,
        *(f"AI-SDLC-USER-GUIDE-STEP: {step}" for step in REQUIRED_STEPS),
        "不得发布",
    )
    for marker in required_markers:
        if marker not in text:
            findings.append(Finding("standard-marker-missing", marker))
    return findings


def _route_sections(text: str) -> tuple[list[str], dict[str, str]]:
    matches = list(_ROUTE_PATTERN.finditer(text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.setdefault(match.group(1), text[match.end() : end])
    return [match.group(1) for match in matches], sections


def _step_sections(text: str) -> tuple[list[str], dict[str, str]]:
    matches = list(_STEP_PATTERN.finditer(text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.setdefault(match.group(1), text[match.end() : end])
    return [match.group(1) for match in matches], sections


def validate_guide_text(text: str, *, version: tuple[int, int, int]) -> list[Finding]:
    """合同生效后校验 12 条可独立执行的路线。"""

    if version <= BASELINE_VERSION:
        return []

    findings: list[Finding] = []
    if text.count(MATRIX_MARKER) != 1:
        findings.append(
            Finding("guide-matrix-marker-count", str(text.count(MATRIX_MARKER)))
        )

    route_ids, sections = _route_sections(text)
    counts = Counter(route_ids)
    for route_id in EXPECTED_ROUTE_IDS:
        if counts[route_id] != 1:
            findings.append(
                Finding("guide-route-marker-count", f"{route_id}={counts[route_id]}")
            )
    for route_id in sorted(set(route_ids) - set(EXPECTED_ROUTE_IDS)):
        findings.append(Finding("guide-route-unknown", route_id))

    for route_id in EXPECTED_ROUTE_IDS:
        section = sections.get(route_id)
        if section is None:
            continue
        step_ids, step_sections = _step_sections(section)
        steps = tuple(step_ids)
        if steps != REQUIRED_STEPS:
            findings.append(
                Finding(
                    "guide-route-step-order",
                    f"{route_id}: {','.join(steps) or 'none'}",
                )
            )

        state, channel, platform = route_id.split("|")
        required_text = [
            "ai-sdlc",
            "-m ai_sdlc",
            "当前结果 / Result",
            "下一步 / Next",
        ]
        if state == "new":
            required_text.append("init .")
        else:
            required_text.extend(("init .", "adopt ."))
        if channel == "online":
            required_text.extend(
                {
                    "windows-amd64": ("install_online.ps1", "-AddToPath"),
                    "macos-arm64": ("install_online.sh", "--add-to-path"),
                    "linux-amd64": ("install_online.sh", "--add-to-path"),
                }[platform]
            )
        else:
            required_text.append("install_offline")
        if channel == "offline":
            required_text.append(".sha256")
            required_text.append(
                {
                    "windows-amd64": "Get-FileHash",
                    "macos-arm64": "shasum -a 256",
                    "linux-amd64": "sha256sum",
                }[platform]
            )
        for marker in required_text:
            if marker not in section:
                findings.append(
                    Finding("guide-route-content-missing", f"{route_id}: {marker}")
                )
        recovery_text = step_sections.get("recover", "")
        if not re.search(r"失败|错误|不可用|停止", recovery_text):
            findings.append(Finding("guide-route-recovery-empty", route_id))

    return findings


def validate_repository(root: Path) -> list[Finding]:
    """校验已记录规范，并在 3.0.0 之后激活手册门禁。"""

    findings: list[Finding] = []
    standard_path = root / "docs" / "user-guide-release-standard.zh-CN.md"
    guide_path = root / "USER_GUIDE.zh-CN.md"
    pyproject_path = root / "pyproject.toml"

    try:
        standard_text = standard_path.read_text(encoding="utf-8")
    except OSError:
        findings.append(Finding("standard-document-missing", str(standard_path)))
    else:
        findings.extend(validate_standard_text(standard_text))

    try:
        pyproject_text = pyproject_path.read_text(encoding="utf-8")
    except OSError:
        findings.append(Finding("project-version-unreadable", str(pyproject_path)))
        return findings
    version = parse_project_version(pyproject_text)
    if version is None:
        findings.append(Finding("project-version-unparseable", str(pyproject_path)))
        return findings

    try:
        guide_text = guide_path.read_text(encoding="utf-8")
    except OSError:
        findings.append(Finding("user-guide-missing", str(guide_path)))
    else:
        findings.extend(validate_guide_text(guide_text, version=version))
    return findings


def main(argv: Sequence[str] | None = None) -> int:
    """执行用户手册发布合同检查。"""

    arguments = list(argv or sys.argv[1:])
    root = Path(arguments[0] if arguments else ".").resolve()
    findings = validate_repository(root)
    for finding in findings:
        print(f"{finding.marker}: {finding.excerpt}")
    if findings:
        return 1
    print("USER_GUIDE_RELEASE_STANDARD_VALID")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
