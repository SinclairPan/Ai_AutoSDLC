#!/usr/bin/env python3
"""Validate the release-enabled 1.0.5 candidate and 1.0.2 public truth."""

from __future__ import annotations

import hashlib
import io
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

CURRENT_REPOSITORY_URL = "https://github.com/SinclairPan/Ai_AutoSDLC"
CURRENT_VERSION = "1.0.5"
PUBLISHED_VERSION = "1.0.2"
STABLE_SOURCE_CLONE = (
    "git clone --branch v1.0.2 --depth 1 "
    "https://github.com/SinclairPan/Ai_AutoSDLC.git"
)
S1_RELEASE_STATE = (
    "v1.0.5 release candidate / release-enabled / outcome-pending-closure"
)
S1_BOUNDARY_MARKERS = (
    "PR2 合并后",
    "exact protected-main",
    "唯一只读 load-probe",
    "一次 actual generation",
    "普通用户和手工路径仍禁止上传、替换、发布、下载、安装或 rerun v1.0.5",
)

PUBLIC_DOC_PATHS = {
    "docs/enterprise-agentops-setup.zh-CN.md",
    "docs/framework-defect-backlog.zh-CN.md",
    "docs/product-contract.md",
    "docs/pull-request-checklist.zh.md",
    "docs/框架自迭代开发与发布约定.md",
}

REQUIRED_SURFACES: dict[str, tuple[str, ...]] = {
    "README.md": (
        CURRENT_REPOSITORY_URL,
        CURRENT_VERSION,
        STABLE_SOURCE_CLONE,
        S1_RELEASE_STATE,
        "last published version is v1.0.2",
        "v1.0.4 terminal NO-GO / not released",
        "WorkItem 010 three-PR release migration",
        "active no-bypass tag ruleset protects software and Certificate tags",
        *S1_BOUNDARY_MARKERS,
    ),
    "USER_GUIDE.zh-CN.md": (
        CURRENT_REPOSITORY_URL,
        CURRENT_VERSION,
        PUBLISHED_VERSION,
        S1_RELEASE_STATE,
        "last published version is v1.0.2",
        "## 第一章：全新用户 + 全新空项目",
        "## 第二章：全新用户 + 已有项目",
        "v1.0.4 未发布",
        "v1.0.4 terminal NO-GO / not released",
        "WorkItem 010 three-PR release migration",
        "active no-bypass tag ruleset protects software and Certificate tags",
        "releases/download/v1.0.2/ai-sdlc-offline-1.0.2-windows-amd64.zip",
        "releases/download/v1.0.2/ai-sdlc-offline-1.0.2-macos-arm64.tar.gz",
        "releases/download/v1.0.2/ai-sdlc-offline-1.0.2-linux-amd64.tar.gz",
        "Get-FileHash -Algorithm SHA256",
        "shasum -a 256 -c",
        "sha256sum -c",
        "Claude Code",
        "Codex",
        "Cursor",
        "VS Code",
        "其他-通用",
        "ai-sdlc init .",
        "ai-sdlc adopt .",
        "当前结果 / Result",
        "下一步 / Next",
        *S1_BOUNDARY_MARKERS,
    ),
    "docs/product-contract.md": (
        CURRENT_REPOSITORY_URL,
        CURRENT_VERSION,
        S1_RELEASE_STATE,
        "last published version is v1.0.2",
        "v1.0.4 terminal NO-GO / not released",
        "WorkItem 010 three-PR release migration",
        "active no-bypass tag ruleset protects software and Certificate tags",
        *S1_BOUNDARY_MARKERS,
    ),
    "packaging/offline/README.md": (
        CURRENT_REPOSITORY_URL,
        CURRENT_VERSION,
        PUBLISHED_VERSION,
        STABLE_SOURCE_CLONE,
        S1_RELEASE_STATE,
        "last published version is v1.0.2",
        "v1.0.4 terminal NO-GO / not released",
        "WorkItem 010 three-PR release migration",
        "不得 redispatch、rerun、上传或发布 v1.0.4",
        "普通用户和手工路径仍禁止上传、替换、发布、下载、安装或 rerun v1.0.5",
        *S1_BOUNDARY_MARKERS,
    ),
    "packaging/offline/RELEASE_CHECKLIST.md": (
        CURRENT_REPOSITORY_URL,
        CURRENT_VERSION,
        PUBLISHED_VERSION,
        S1_RELEASE_STATE,
        "last published version is v1.0.2",
        "v1.0.4 terminal NO-GO / not released",
        "WorkItem 010 three-PR release migration",
        "不得 redispatch、rerun、上传或发布 v1.0.4",
        "普通用户和手工路径仍禁止上传、替换、发布、下载、安装或 rerun v1.0.5",
        *S1_BOUNDARY_MARKERS,
    ),
    "docs/pull-request-checklist.zh.md": (
        CURRENT_VERSION,
        PUBLISHED_VERSION,
        S1_RELEASE_STATE,
        "last published version is v1.0.2",
        "v1.0.4 terminal NO-GO / not released",
        "WorkItem 010 three-PR release migration",
        "不得 redispatch、rerun、上传或发布 v1.0.4",
        "普通用户和手工路径仍禁止上传、替换、发布、下载、安装或 rerun v1.0.5",
        *S1_BOUNDARY_MARKERS,
    ),
    "packaging/install_online.sh": (
        'PACKAGE_SPEC="${AI_SDLC_PACKAGE_SPEC:-ai-sdlc==1.0.2}"',
    ),
    "packaging/install_online.ps1": (
        '[string]$PackageSpec = "ai-sdlc==1.0.2",',
    ),
    "docs/框架自迭代开发与发布约定.md": (
        "## v1.0.4 bootstrap 终止记录（2026-08-09）",
        "terminal NO-GO / not released / bootstrap budget exhausted",
        "0776885aeb6299bad3c13fd6c47658ad17dad5e1",
        "6125d7e80b1a66eead4ddf5654a578ec2a1e856e",
        "a6a1f2ac463d9ca2dc1ea68af73271e679449015",
        "367380686",
        "31295426083",
        "93199662116",
        "93211087289",
        "93211087697",
        "1 failed / 6219 passed / 16 skipped",
        "zero assets",
        "UNKNOWN",
        "pre-tag qualification",
        "WorkItem 009",
        "WorkItem 010",
        "active no-bypass tag ruleset protects software and Certificate tags",
        "## v1.0.5 prepared-disabled 候选记录",
        "WorkItem 010 three-PR release migration",
        "Actions history duplicate-run detector",
        "retention and no-delete trust boundary",
        "not an immutable authority",
        "protected tag namespace becomes the durable burn authority",
    ),
}

FORBIDDEN_SURFACE_MARKERS: dict[str, tuple[str, ...]] = {
    "README.md": (
        "WorkItem 008",
        "only future WorkItem 010 may migrate to v1.0.5",
        "v1.0.5 release candidate / not published / prepared-disabled",
        "releases/download/v1.0.5/",
        "v1.0.5 已发布",
    ),
    "USER_GUIDE.zh-CN.md": (
        "WorkItem 008",
        "only future WorkItem 010 may migrate to v1.0.5",
        "v1.0.5 release candidate / not published / prepared-disabled",
        "releases/download/v1.0.5/",
        "v1.0.5 已发布",
    ),
    "docs/product-contract.md": (
        "WorkItem 008",
        "only future WorkItem 010 may migrate to v1.0.5",
        "v1.0.5 release candidate / not published / prepared-disabled",
        "releases/download/v1.0.5/",
        "v1.0.5 已发布",
    ),
    "packaging/offline/README.md": (
        "上传动作必须由有权限的维护者明确触发",
        "only future WorkItem 010 may migrate to v1.0.5",
        "v1.0.5 release candidate / not published / prepared-disabled",
        "releases/download/v1.0.5/",
        "v1.0.5 已发布",
    ),
    "packaging/offline/RELEASE_CHECKLIST.md": (
        "上传动作由有权限维护者明确执行",
        "only future WorkItem 010 may migrate to v1.0.5",
        "v1.0.5 release candidate / not published / prepared-disabled",
        "releases/download/v1.0.5/",
        "v1.0.5 已发布",
    ),
    "docs/pull-request-checklist.zh.md": (
        "当前发布版本为 `1.0.4`",
        "only future WorkItem 010 may migrate to v1.0.5",
        "v1.0.5 release candidate / not published / prepared-disabled",
        "releases/download/v1.0.5/",
        "v1.0.5 已发布",
    ),
    "packaging/install_online.sh": (
        "AI_SDLC_PACKAGE_SPEC=ai-sdlc==1.0.5",
        'PACKAGE_SPEC="${AI_SDLC_PACKAGE_SPEC:-ai-sdlc}"',
    ),
    "packaging/install_online.ps1": (
        '[string]$PackageSpec = "ai-sdlc",',
        '[string]$PackageSpec = "ai-sdlc==1.0.5",',
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
    (re.compile(r"^\.ai-sdlc/state/"), "runtime-state"),
)

TEXT_RULES = (
    (
        re.compile(r"ai-sdlc-offline-0\.\d", re.IGNORECASE),
        "pre-1.0-product-version",
    ),
    (
        re.compile(
            r"(?:ai[-_]sdlc|AI_SDLC|__version__|installed_version|latest_version)"
            r"[^\n]{0,80}\bv?0\.\d+\.\d+",
            re.IGNORECASE,
        ),
        "pre-1.0-product-version",
    ),
    (
        re.compile(
            r"\bv?0\.\d+\.\d+[^\n]{0,80}(?:ai-sdlc|ai_sdlc)",
            re.IGNORECASE,
        ),
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
HTML_COMMENT_PATTERN = re.compile(r"<!--.*?(?:-->|$)", re.DOTALL)
RELEASE_TREE_SEAL_DOMAIN = b"ai-sdlc-s1-release-tree-seal-v1"
RELEASE_TREE_SEAL_TRUST_ROOT = b"scripts/validate_public_release_identity.py"
RELEASE_TREE_SEAL_ANCHOR = b"README.md"
RELEASE_TREE_SEAL_ANCHOR_PATTERN = re.compile(
    rb"<!-- S1_RELEASE_TREE_SEAL: (?P<seal>[0-9a-f]{64}) -->"
)
RELEASE_TREE_SEAL_ANCHOR_PLACEHOLDER = b"0" * 64
ALLOWED_RELEASE_TREE_MODES = frozenset({b"100644", b"100755"})


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


def _frame(digest: object, value: bytes) -> None:
    """向 release tree seal 写入无歧义的长度分帧字段。"""

    digest.update(str(len(value)).encode("ascii"))
    digest.update(b"\0")
    digest.update(value)


def _normalize_release_tree_seal_anchor(blob: bytes) -> tuple[bytes, str]:
    """只归一化 README 中唯一的 seal 值，其余原始字节仍进入全树摘要。"""

    matches = tuple(RELEASE_TREE_SEAL_ANCHOR_PATTERN.finditer(blob))
    if len(matches) != 1:
        raise ValueError("release tree seal anchor must appear exactly once")
    match = matches[0]
    start, end = match.span("seal")
    expected = match.group("seal").decode("ascii")
    normalized = (
        blob[:start] + RELEASE_TREE_SEAL_ANCHOR_PLACEHOLDER + blob[end:]
    )
    return normalized, expected


def release_tree_seal(root: Path, state: str, treeish: str = "HEAD") -> str:
    """从 Git 对象计算临时 S1 全树 seal，不读取 worktree 或 index。"""

    listed = subprocess.run(
        ["git", "ls-tree", "-r", "-z", "--full-tree", treeish],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    entries: list[tuple[bytes, bytes, bytes, bytes]] = []
    for record in listed.split(b"\0"):
        if not record:
            continue
        try:
            metadata, path = record.split(b"\t", 1)
            mode, object_type, object_id = metadata.split(b" ", 2)
        except ValueError as exc:
            raise ValueError("malformed Git tree entry") from exc
        entries.append((path, mode, object_type, object_id))
    entries.sort(key=lambda entry: entry[0])

    trust_roots = [entry for entry in entries if entry[0] == RELEASE_TREE_SEAL_TRUST_ROOT]
    if len(trust_roots) != 1 or trust_roots[0][1:3] != (b"100755", b"blob"):
        raise ValueError("validator trust root must be one 100755 blob")
    anchors = [entry for entry in entries if entry[0] == RELEASE_TREE_SEAL_ANCHOR]
    if len(anchors) != 1 or anchors[0][1:3] != (b"100644", b"blob"):
        raise ValueError("release tree seal anchor must be one 100644 blob")
    for path, mode, object_type, _object_id in entries:
        if object_type != b"blob" or mode not in ALLOWED_RELEASE_TREE_MODES:
            decoded_path = path.decode("utf-8", errors="backslashreplace")
            raise ValueError(f"unsupported Git tree entry: {mode!r} {decoded_path}")

    batch = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=root,
        input=b"".join(entry[3] + b"\n" for entry in entries),
        check=True,
        capture_output=True,
    ).stdout
    stream = io.BytesIO(batch)
    digest = hashlib.sha256()
    _frame(digest, RELEASE_TREE_SEAL_DOMAIN)
    _frame(digest, state.encode("utf-8"))
    _frame(digest, str(len(entries)).encode("ascii"))
    for path, mode, object_type, expected_id in entries:
        _frame(digest, mode)
        _frame(digest, object_type)
        _frame(digest, path)
        header = stream.readline()
        try:
            actual_id, actual_type, size_text = header.rstrip(b"\n").split(b" ", 2)
            size = int(size_text)
        except (TypeError, ValueError) as exc:
            raise ValueError("malformed git cat-file header") from exc
        if (
            actual_id != expected_id
            or actual_type != b"blob"
            or str(size).encode("ascii") != size_text
        ):
            raise ValueError("git cat-file object differs from tree entry")
        blob = stream.read(size)
        if len(blob) != size or stream.read(1) != b"\n":
            raise ValueError("truncated git cat-file blob")
        if path == RELEASE_TREE_SEAL_ANCHOR:
            blob, _expected = _normalize_release_tree_seal_anchor(blob)
        _frame(digest, blob)
    if stream.read():
        raise ValueError("unexpected trailing git cat-file output")
    return digest.hexdigest()


def release_tree_seal_anchor(root: Path, treeish: str = "HEAD") -> str:
    """从同一 Git tree 的 README blob 读取受审 seal 值。"""

    blob = subprocess.run(
        ["git", "cat-file", "blob", f"{treeish}:{RELEASE_TREE_SEAL_ANCHOR.decode('ascii')}"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    _normalized, expected = _normalize_release_tree_seal_anchor(blob)
    return expected


def validate_release_tree_seal(
    root: Path,
    state: str,
) -> list[Finding]:
    """将当前 HEAD 的临时 S1 全树 seal 与受审期望值比较。"""

    expected = release_tree_seal_anchor(root)
    actual = release_tree_seal(root, state)
    if actual == expected:
        return []
    return [
        Finding(
            RELEASE_TREE_SEAL_ANCHOR.decode("ascii"),
            None,
            "s1-release-tree-seal-mismatch",
            f"expected={expected} actual={actual}",
        )
    ]


def _markdown_without_html_comments(text: str) -> str:
    """移除 Markdown 中读者不可见的 HTML 注释，同时保留标记原文。"""

    return HTML_COMMENT_PATTERN.sub("", text)


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
        required_marker_text = (
            _markdown_without_html_comments(text) if path.endswith(".md") else text
        )
        for marker in required_markers:
            if marker not in required_marker_text:
                findings.append(
                    Finding(path, None, "required-identity-marker-missing", marker)
                )
        for marker in FORBIDDEN_SURFACE_MARKERS.get(path, ()):
            if marker in text:
                findings.append(
                    Finding(path, None, "obsolete-release-authorization", marker)
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
    return [
        *scan_paths(root, files),
        *validate_required_surfaces(files),
        *validate_release_tree_seal(
            root,
            S1_RELEASE_STATE,
        ),
    ]


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
