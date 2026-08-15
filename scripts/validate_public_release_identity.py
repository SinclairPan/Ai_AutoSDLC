#!/usr/bin/env python3
"""Validate the prepared-disabled 1.0.5 candidate and 1.0.2 public truth."""

from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
import os
import re
import ssl
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

CURRENT_REPOSITORY_URL = "https://github.com/SinclairPan/Ai_AutoSDLC"
CURRENT_VERSION = "1.0.5"
PUBLISHED_VERSION = "1.0.2"
STABLE_SOURCE_CLONE = (
    "git clone --branch v1.0.2 --depth 1 "
    "https://github.com/SinclairPan/Ai_AutoSDLC.git"
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
        "v1.0.5 release candidate / not published / prepared-disabled",
        "last published version is v1.0.2",
        "v1.0.4 terminal NO-GO / not released",
        "WorkItem 010 three-PR release migration",
        "active no-bypass tag ruleset protects software and Certificate tags",
    ),
    "USER_GUIDE.zh-CN.md": (
        CURRENT_REPOSITORY_URL,
        CURRENT_VERSION,
        PUBLISHED_VERSION,
        "v1.0.5 release candidate / not published / prepared-disabled",
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
    ),
    "docs/product-contract.md": (
        CURRENT_REPOSITORY_URL,
        CURRENT_VERSION,
        "v1.0.5 release candidate / not published / prepared-disabled",
        "last published version is v1.0.2",
        "v1.0.4 terminal NO-GO / not released",
        "WorkItem 010 three-PR release migration",
        "active no-bypass tag ruleset protects software and Certificate tags",
    ),
    "packaging/offline/README.md": (
        CURRENT_REPOSITORY_URL,
        CURRENT_VERSION,
        PUBLISHED_VERSION,
        STABLE_SOURCE_CLONE,
        "v1.0.5 release candidate / not published / prepared-disabled",
        "last published version is v1.0.2",
        "v1.0.4 terminal NO-GO / not released",
        "WorkItem 010 three-PR release migration",
        "不得 redispatch、rerun、上传或发布 v1.0.4",
        "不得上传、发布或下载 v1.0.5 候选",
    ),
    "packaging/offline/RELEASE_CHECKLIST.md": (
        CURRENT_REPOSITORY_URL,
        CURRENT_VERSION,
        PUBLISHED_VERSION,
        "v1.0.5 release candidate / not published / prepared-disabled",
        "last published version is v1.0.2",
        "v1.0.4 terminal NO-GO / not released",
        "WorkItem 010 three-PR release migration",
        "不得 redispatch、rerun、上传或发布 v1.0.4",
        "不得上传、发布或下载 v1.0.5 候选",
    ),
    "docs/pull-request-checklist.zh.md": (
        CURRENT_VERSION,
        PUBLISHED_VERSION,
        "v1.0.5 release candidate / not published / prepared-disabled",
        "last published version is v1.0.2",
        "v1.0.4 terminal NO-GO / not released",
        "WorkItem 010 three-PR release migration",
        "不得 redispatch、rerun、上传或发布 v1.0.4",
        "不得上传、发布或下载 v1.0.5 候选",
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
        "releases/download/v1.0.5/",
        "v1.0.5 已发布",
    ),
    "USER_GUIDE.zh-CN.md": (
        "WorkItem 008",
        "only future WorkItem 010 may migrate to v1.0.5",
        "releases/download/v1.0.5/",
        "v1.0.5 已发布",
    ),
    "docs/product-contract.md": (
        "WorkItem 008",
        "only future WorkItem 010 may migrate to v1.0.5",
        "releases/download/v1.0.5/",
        "v1.0.5 已发布",
    ),
    "packaging/offline/README.md": (
        "上传动作必须由有权限的维护者明确触发",
        "only future WorkItem 010 may migrate to v1.0.5",
        "releases/download/v1.0.5/",
        "v1.0.5 已发布",
    ),
    "packaging/offline/RELEASE_CHECKLIST.md": (
        "上传动作由有权限维护者明确执行",
        "only future WorkItem 010 may migrate to v1.0.5",
        "releases/download/v1.0.5/",
        "v1.0.5 已发布",
    ),
    "docs/pull-request-checklist.zh.md": (
        "当前发布版本为 `1.0.4`",
        "only future WorkItem 010 may migrate to v1.0.5",
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
WI010_PHASE_MARKER_PREFIX = "<!-- WI010_RELEASE_PHASE:"
WI010_PHASE_MARKER_PATTERN = re.compile(
    r"<!-- WI010_RELEASE_PHASE: (?P<payload>\{[^\r\n]*\}) -->"
)
WI010_SEAL_MARKER_PREFIX = "<!-- WI010_RELEASE_TREE_SEAL:"
WI010_SEAL_MARKER_PATTERN = re.compile(
    r"<!-- WI010_RELEASE_TREE_SEAL: (?P<seal>[0-9a-f]{64}) -->"
)
WI010_RELEASE_SURFACES = (
    "README.md",
    "USER_GUIDE.zh-CN.md",
    "docs/product-contract.md",
    "docs/pull-request-checklist.zh.md",
    "packaging/offline/README.md",
    "packaging/offline/RELEASE_CHECKLIST.md",
)
WI010_RELEASE_STATE_MARKERS = {
    "S0": "v1.0.5 release candidate / not published / prepared-disabled",
    "S1": "v1.0.5 release candidate / release-enabled / outcome-pending-closure",
    "S2-success": (
        "v1.0.5 Permanent Release Truth / published / immutable / "
        "Certificate-trusted"
    ),
    "S2-burn": (
        "v1.0.5 Permanent Release Truth / terminal-generation-burn / "
        "non-authoritative"
    ),
}
WI010_RELEASE_FLAGS = (
    "RELEASE_BOOTSTRAP_ENABLED",
    "RELEASE_ENVIRONMENT_PROTECTION_VERIFIED",
    "RELEASE_TAG_RULESET_PROTECTION_VERIFIED",
)
WI010_SUCCESS_PAYLOAD_KEYS = frozenset(
    {
        "archive_commit_sha",
        "certificate_commit_sha",
        "certificate_digest",
        "certificate_tag",
        "certificate_tree_sha",
        "generation",
        "immutable",
        "phase",
        "proof_commit_sha",
        "proof_digest",
        "release_attestation_digest",
        "release_id",
        "tag_name",
        "tag_peel_sha",
        "target_commitish_resolved_sha",
        "workflow_run_attempt",
        "workflow_run_id",
    }
)
WI010_BURN_PAYLOAD_KEYS = frozenset(
    {
        "authority_id",
        "authority_kind",
        "candidate_commit_sha",
        "candidate_tree_sha",
        "generation",
        "phase",
        "terminal_stage",
        "workflow_run_attempt",
        "workflow_run_id",
    }
)
WI010_BURN_AUTHORITY_KINDS = frozenset(
    {
        "actions-history-retention",
        "protected-software-tag",
        "protected-certificate-tag",
    }
)
WI010_BURN_TERMINAL_STAGES = frozenset(
    {
        "dispatch-recorded",
        "software-tag-created",
        "certificate-created",
    }
)
WI010_BURN_AUTHORITY_BY_STAGE = {
    "dispatch-recorded": (
        "actions-history-retention",
        "actions/runs/{workflow_run_id}/attempts/1",
    ),
    "software-tag-created": ("protected-software-tag", "refs/tags/v1.0.5"),
    "certificate-created": (
        "protected-certificate-tag",
        "refs/tags/release-truth/v1.0.5/certificate/g0",
    ),
}
WI010_BURN_TERMINAL_CONCLUSIONS = frozenset(
    {
        "action_required",
        "cancelled",
        "failure",
        "stale",
        "startup_failure",
        "timed_out",
    }
)
WI010_BURN_EARLY_STOP_CONCLUSIONS = frozenset(
    {"action_required", "cancelled", "stale", "startup_failure"}
)
WI010_BURN_REQUIRED_JOB_NAMES = (
    "Read-only Release Workflow Load Probe",
    "Resolve Pre-tag Release Qualification Policy",
    "windows zip",
    "macos tar.gz",
    "linux tar.gz",
    "Release Qualification",
    "Build Release Proof Inputs",
    "Publish Proof-bound Release",
)
WI010_CERTIFICATE_BURN_TAG_MESSAGE_PATTERN = re.compile(
    r"Permanent Certificate for v1\.0\.5\n\n"
    r"software_admission_digest=sha256:[0-9a-f]{64} "
    r"software_proof_digest=sha256:[0-9a-f]{64} "
    r"failure_policy=terminal-generation-burn; "
    r"no cleanup, edit, reuse, or rerun"
)
WI010_ARCHIVE_PATTERN = re.compile(
    r"https://github\.com/SinclairPan/Ai_AutoSDLC/archive/"
    r"(?P<sha>[0-9a-f]{40})\.zip"
)
WI010_ARCHIVE_PREFIX = "https://github.com/SinclairPan/Ai_AutoSDLC/archive/"
WI010_CANONICAL_ONLINE_SPEC_PREFIX = "Canonical online install spec: "
WI010_HEX40_PATTERN = re.compile(r"[0-9a-f]{40}")
WI010_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
WI010_INSTALLER_BASELINE_DEFAULTS = {
    "packaging/install_online.sh": (
        'PACKAGE_SPEC="${AI_SDLC_PACKAGE_SPEC:-ai-sdlc==1.0.2}"'
    ),
    "packaging/install_online.ps1": '[string]$PackageSpec = "ai-sdlc==1.0.2",',
}
WI010_INSTALLER_BASELINE_DIGESTS = {
    "packaging/install_online.sh": (
        "03c4ea31233384b77fb5017bf846e1169a16c43b7d17aab25bfc3e4b631cd0ed"
    ),
    "packaging/install_online.ps1": (
        "1eb47e3a077c3669b30e65a6b70d18f657fd73bb5c7b745e55e437cd30fd14d2"
    ),
}

WI010_SEAL_DOMAIN = b"ai-sdlc-wi010-release-tree-seal-v1"
WI010_README_PATH = b"README.md"
WI010_FOUNDATION_ANCHOR_OID = b"bafc16522098e62021e5bdfcaee40e7739b3a5f7"
WI010_PROTECTED_MAIN_REF = b"refs/heads/main"
WI010_PROTECTED_MAIN_URL = b"https://github.com/SinclairPan/Ai_AutoSDLC.git"
WI010_GITHUB_API_ROOT = "https://api.github.com/repos/SinclairPan/Ai_AutoSDLC"
WI010_CERTIFI_VERSION = "2026.7.22"
WI010_CERTIFI_PEM_SHA256 = (
    "9cc2a774b5198dcff14d9be1e66091f538975d867ce029a96bce15a55dfd730f"
)
WI010_REPOSITORY = "SinclairPan/Ai_AutoSDLC"
WI010_RELEASE_WORKFLOW_REF = (
    "SinclairPan/Ai_AutoSDLC/.github/workflows/release-build.yml@refs/heads/main"
)
WI010_RELEASE_WORKFLOW_PATH = ".github/workflows/release-build.yml"
WI010_PROOF_ASSET = "release-satisfaction-proof.json"
WI010_CERTIFICATE_ASSET = "release-certificate.json"
WI010_ATTESTATION_VERIFIER_PATH = b"src/ai_sdlc/core/github_attestation_verifier.py"
WI010_ATTESTATION_VERIFIER_BLOB_OID = (
    b"802e3294fcf51866bc22d8984750cd6fde8a9f60"
)
WI010_ATTESTATION_VERIFIER_TIMEOUT_SECONDS = 60
WI010_SOFTWARE_ASSETS = frozenset(
    {
        "ai-sdlc-offline-1.0.5-linux-amd64.tar.gz",
        "ai-sdlc-offline-1.0.5-linux-amd64.tar.gz.sha256",
        "ai-sdlc-offline-1.0.5-macos-arm64.tar.gz",
        "ai-sdlc-offline-1.0.5-macos-arm64.tar.gz.sha256",
        "ai-sdlc-offline-1.0.5-windows-amd64.zip",
        "ai-sdlc-offline-1.0.5-windows-amd64.zip.sha256",
    }
)
WI010_PROTECTED_ORIGIN_URLS = frozenset(
    {
        b"https://github.com/SinclairPan/Ai_AutoSDLC",
        b"https://github.com/SinclairPan/Ai_AutoSDLC.git",
    }
)
WI010_TRUST_ROOTS = {
    b"README.md": b"100644",
    b"scripts/validate_public_release_identity.py": b"100755",
    b"src/ai_sdlc/core/verify_constraints.py": b"100644",
    b"tests/unit/test_public_release_identity.py": b"100644",
}
WI010_CONSTRAINTS_PATH = b"src/ai_sdlc/core/verify_constraints.py"
WI010_TEST_TRUST_ROOT_PATH = b"tests/unit/test_public_release_identity.py"
WI010_CONSTRAINTS_VALIDATOR_PIN_PATTERN = re.compile(
    br'(?m)^WI010_VALIDATOR_BLOB_OID = "([0-9a-f]{40})"$'
)
WI010_FOUNDATION_TRUST_ROOT_OIDS = {
    WI010_CONSTRAINTS_PATH: b"0a52fe27cb6204f4c9fe3e15cc0b0547bf21338b",
    WI010_TEST_TRUST_ROOT_PATH: b"f7ebe5f720a9339e0a86acdaf51f18aab4804f65",
}
WI010_ALLOWED_MODES = frozenset({b"100644", b"100755"})
WI010_FOUNDATION_PATHS = frozenset(WI010_TRUST_ROOTS)
WI010_PR2_PATHS = frozenset(
    path.encode("utf-8")
    for path in (
        ".github/workflows/release-build.yml",
        "tests/integration/test_github_workflows.py",
        *WI010_RELEASE_SURFACES,
    )
)
WI010_S2_BURN_PATHS = WI010_PR2_PATHS
WI010_S2_SUCCESS_PATHS = frozenset(
    {
        *WI010_PR2_PATHS,
        b".github/workflows/posix-user-guide-e2e.yml",
        b".github/workflows/windows-user-guide-e2e.yml",
        b"packaging/install_online.sh",
        b"packaging/install_online.ps1",
        b"tests/integration/test_offline_bundle_scripts.py",
        b"tests/integration/test_user_guide_contract.py",
        b"tests/unit/test_release_identity.py",
    }
)

WI010_S1_README_STATUS_BODY = (
    "。`WorkItem 010 three-PR release migration` 在该制品构建时点处于 S1。"
    "该 README 与其生成的 wheel METADATA 只记录该制品"
    "构建时点的 S1 历史快照：三个发布开关在该时点为字符串 `true`，只授权 exact "
    "protected-main `release-build` writer 在 PR2 合并后的唯一只读 load-probe 成功后"
    "执行一次 actual generation；在该制品构建时点，普通用户和手工路径禁止上传、"
    "替换、发布、下载、安装或 rerun v1.0.5。在该制品构建时点，`last published "
    "version is v1.0.2`，普通用户按[中文用户指南](USER_GUIDE.zh-CN.md)安装该版本。"
    "该历史快照不对 PR3 closure 后的当前发布权威作结论；后续权威只能由 exact PR3 "
    "closure SHA 与 Certificate 确定。"
)
WI010_S1_README_STATUS = (
    "> 制品构建时点快照："
    + WI010_RELEASE_STATE_MARKERS["S1"]
    + WI010_S1_README_STATUS_BODY
)
WI010_S1_README_GO = (
    "在该制品构建时点，010 的 GO 依赖两个已经独立验证的远端保护：`release-publish` "
    "environment 必须以 required reviewers 阻断未审 writer（禁止自批与管理员 bypass）；"
    "`active no-bypass tag ruleset protects software and Certificate tags`，其精确覆盖"
    "软件 tag 与 generation-0 Certificate tag，允许新建但拒绝更新、删除和非快进变更。"
    "在该制品构建时点，S1 的三个验证/发布开关均为字符串 `true`；PR2 合并后必须先从"
    "精确 protected-main writer 完成唯一只读 load-probe，成功后才允许一次 actual "
    "generation，任一失败都永久烧毁该代际，不清理、不恢复、不重跑。该段只记录构建"
    "时点的授权边界，不表示 PR3 closure 后仍处于 pending。"
)
WI010_S1_README_INSTALL_CONTEXT = (
    "以下 v1.0.2 安装命令只记录该制品构建时点的普通用户权威；它们不是对 PR3 "
    "closure 后当前安装入口的声明，后续入口只由 exact PR3 closure SHA 与 Certificate 确定。"
)
WI010_S1_README_SOURCE_CONTEXT = (
    "在该制品构建时点，源码路径只用于维护者开发验证，不属于普通用户安装入口，也不"
    "授权任何 v1.0.5 手工安装或发布动作；该限制只记录 S1 历史快照，不对 PR3 closure "
    "后的当前权威作结论。"
)
WI010_S1_README_OFFLINE_CONTEXT = (
    "离线包会包含 AI-SDLC wheel、依赖 wheel、安装脚本、包内 `SHA256SUMS` 校验清单和"
    "可选的 Python 运行时。每个正式压缩包同时发布同名 `.sha256` 文件。以下是 `1.0.5` "
    "源码候选预期生成的产物名称；在该制品构建时点，S1 处于 "
    "outcome-pending-closure，它们不是普通用户公开安装权威。该历史快照不判定 PR3 "
    "closure 后的发行结论："
)
WI010_S1_README_OFFLINE_AUTHORITY = (
    "在该制品构建时点，公开可安装的离线版本是 `v1.0.2`，具体下载与校验命令见"
    "[中文用户指南](USER_GUIDE.zh-CN.md)；这是 S1 历史快照，不对 PR3 closure 后的"
    "当前发布权威作结论。"
)

WI010_S0_TO_S1_TEXT_REPLACEMENTS = {
    "README.md": (
        (
            "> 候选状态：`v1.0.5 release candidate / not published / prepared-disabled`。"
            "`WorkItem 010 three-PR release migration` 当前只准备候选，三个发布开关均保持 "
            "`false`；不得上传、发布或下载 v1.0.5 候选。`last published version is "
            "v1.0.2`，普通用户请按[中文用户指南](USER_GUIDE.zh-CN.md)安装该版本。",
            WI010_S1_README_STATUS,
        ),
        (
            "010 的 GO 同时依赖两个已经独立验证的远端保护：`release-publish` environment "
            "必须以 required reviewers 阻断未审 writer（禁止自批与管理员 bypass）；"
            "`active no-bypass tag ruleset protects software and Certificate tags`，其精确覆盖"
            "软件 tag 与 generation-0 Certificate tag，允许新建但拒绝更新、删除和非快进变更。"
            "PR1 保持三个验证/发布开关为字符串 `false`；只有独立的 PR2 可以启用一次实际 "
            "generation，任一失败都永久烧毁该代际，不清理、不恢复、不重跑。",
            WI010_S1_README_GO,
        ),
        (
            "运行要求：Python 3.11 或更高版本、Git。源码开发推荐使用 "
            "[uv](https://docs.astral.sh/uv/)。",
            "运行要求：Python 3.11 或更高版本、Git。源码开发推荐使用 "
            "[uv](https://docs.astral.sh/uv/)。\n\n" + WI010_S1_README_INSTALL_CONTEXT,
        ),
        (
            "需要验证尚未发布的开发版时，可显式把安装地址末尾改为 `@main`；开发版不承诺"
            "输出稳定版版本号。",
            WI010_S1_README_SOURCE_CONTEXT,
        ),
        (
            "离线包会包含 AI-SDLC wheel、依赖 wheel、安装脚本、包内 `SHA256SUMS` 校验清单和"
            "可选的 Python 运行时。每个正式压缩包同时发布同名 `.sha256` 文件。以下是 `1.0.5` "
            "源码候选预期生成的产物名称；它们尚未形成已发布、可安装的正式集合：",
            WI010_S1_README_OFFLINE_CONTEXT,
        ),
        (
            "当前公开可安装的离线版本仍是 `v1.0.2`，具体下载与校验命令见"
            "[中文用户指南](USER_GUIDE.zh-CN.md)。",
            WI010_S1_README_OFFLINE_AUTHORITY,
        ),
    ),
    "USER_GUIDE.zh-CN.md": (
        (
            "> 发布可用性：`v1.0.5 release candidate / not published / prepared-disabled`，"
            "`WorkItem 010 three-PR release migration` 当前没有授权上传或发布该候选；"
            "`last published version is v1.0.2`，本指南仍只安装 `v1.0.2`。"
            "`v1.0.4 terminal NO-GO / not released` 与 `v1.0.4 未发布`继续保持冻结，"
            "不能使用任何 `releases/download/v1.0.4` 路径，也不得 redispatch、rerun、上传或发布 v1.0.4。",
            "> 发布可用性：`v1.0.5 release candidate / release-enabled / outcome-pending-closure`，"
            "`WorkItem 010 three-PR release migration` 的 S1 只授权 exact protected-main "
            "`release-build` writer 在唯一只读 load-probe 成功后执行一次 actual generation；"
            "普通用户和手工路径仍禁止上传、替换、发布、下载、安装或 rerun v1.0.5。"
            "`last published version is v1.0.2`，本指南仍只安装 `v1.0.2`。"
            "`v1.0.4 terminal NO-GO / not released` 与 `v1.0.4 未发布`继续保持冻结，"
            "不能使用任何 `releases/download/v1.0.4` 路径，也不得 redispatch、rerun、上传或发布 v1.0.4。",
        ),
        (
            "010 只有在 PR1 的只读 load-probe 与全部门禁通过后，才可由独立 PR2 启用一次实际 "
            "generation：`release-publish` environment 以 required reviewers 阻断未审 writer，且禁止"
            "自批与管理员 bypass；`active no-bypass tag ruleset protects software and Certificate "
            "tags`，精确覆盖软件 tag 和 generation-0 Certificate tag，并拒绝更新、删除及非快进"
            "变更。PR1 中三个开关均为字符串 `false`；任何实际 generation 的部分创建或保护失败"
            "都属于 terminal generation burn，禁止清理、恢复或重跑。",
            "010 的 S1 将三个开关设为字符串 `true`：`release-publish` environment 以 required "
            "reviewers 阻断未审 writer，且禁止自批与管理员 bypass；`active no-bypass tag ruleset "
            "protects software and Certificate tags`，精确覆盖软件 tag 和 generation-0 Certificate "
            "tag，并拒绝更新、删除及非快进变更。PR2 合并后必须从精确 protected-main writer "
            "先执行唯一只读 load-probe，成功后才允许一次 actual generation；任何部分创建或"
            "保护失败都属于 terminal generation burn，禁止清理、恢复或重跑。",
        ),
    ),
    "docs/product-contract.md": (
        ("## 1.0.5 源码候选真值（prepared-disabled）", "## 1.0.5 源码候选真值（release-enabled / outcome-pending-closure）"),
        ("是该候选预期产物名，不是已发布发行集合；", "是该候选预期产物名；S1 仍处于 outcome-pending-closure，它们不是普通用户公开安装权威；"),
        (
            "`v1.0.5 release candidate / not published / prepared-disabled`：`WorkItem 010 "
            "three-PR release migration` 的 PR1 保持三个发布开关为 `false`，不得上传、发布或下载 v1.0.5 候选；",
            "`v1.0.5 release candidate / release-enabled / outcome-pending-closure`："
            "`WorkItem 010 three-PR release migration` 的 S1 将三个发布开关设为字符串 `true`，"
            "只授权 exact protected-main `release-build` writer 在 PR2 合并后的唯一只读 load-probe "
            "成功后执行一次 actual generation；普通用户和手工路径仍禁止上传、替换、发布、下载、"
            "安装或 rerun v1.0.5；",
        ),
        (
            "PR1 中三个验证/发布开关均保持字符串 `false`；",
            "S1 中三个验证/发布开关均为字符串 `true`；",
        ),
    ),
    "docs/pull-request-checklist.zh.md": (
        (
            "`v1.0.5 release candidate / not published / prepared-disabled`，且 `WorkItem 010 "
            "three-PR release migration` 的当前 PR 没有发布授权；",
            "`v1.0.5 release candidate / release-enabled / outcome-pending-closure`，且 "
            "`WorkItem 010 three-PR release migration` 的 S1 只授权 exact protected-main "
            "`release-build` writer 在 PR2 合并后的唯一只读 load-probe 成功后执行一次 actual generation；",
        ),
        ("不得上传、发布或下载 v1.0.5 候选；", "普通用户和手工路径仍禁止上传、替换、发布、下载、安装或 rerun v1.0.5；"),
    ),
    "packaging/offline/README.md": (
        (
            "候选状态：`v1.0.5 release candidate / not published / prepared-disabled`。"
            "`WorkItem 010 three-PR release migration` 的 PR1 只验证候选，三个发布开关保持 `false`；"
            "不得上传、发布或下载 v1.0.5 候选。`last published version is v1.0.2`。",
            "候选状态：`v1.0.5 release candidate / release-enabled / outcome-pending-closure`。"
            "`WorkItem 010 three-PR release migration` 的 S1 将三个发布开关设为字符串 `true`，"
            "只授权 exact protected-main `release-build` writer 在 PR2 合并后的唯一只读 load-probe "
            "成功后执行一次 actual generation；普通用户和手工路径仍禁止上传、替换、发布、下载、"
            "安装或 rerun v1.0.5。`last published version is v1.0.2`。",
        ),
        ("不构成上传或发布授权。", "不构成普通用户或手工上传、发布、下载、安装授权。"),
        (
            "工作流当前候选标识是 prepared-disabled 的 `v1.0.5`，PR1 只能运行只读 load-probe；",
            "工作流当前候选标识是 release-enabled / outcome-pending-closure 的 `v1.0.5`；"
            "PR2 合并后只允许精确 protected-main writer 先执行唯一只读 load-probe，成功后才允许一次 actual generation。",
        ),
    ),
    "packaging/offline/RELEASE_CHECKLIST.md": (
        (
            "状态：`v1.0.5 release candidate / not published / prepared-disabled`。"
            "`WorkItem 010 three-PR release migration` 的 PR1 只准备候选，三个发布开关保持 `false`；"
            "不得上传、发布或下载 v1.0.5 候选。`last published version is v1.0.2`。",
            "状态：`v1.0.5 release candidate / release-enabled / outcome-pending-closure`。"
            "`WorkItem 010 three-PR release migration` 的 S1 将三个发布开关设为字符串 `true`，"
            "只授权 exact protected-main `release-build` writer 在 PR2 合并后的唯一只读 load-probe "
            "成功后执行一次 actual generation；普通用户和手工路径仍禁止上传、替换、发布、下载、"
            "安装或 rerun v1.0.5。`last published version is v1.0.2`。",
        ),
        (
            "以下条目只用于 PR1 候选验证；它不授权实际 generation、发布或上传步骤。",
            "以下条目用于 S1 候选验证；除精确 protected-main writer 在唯一只读 load-probe 成功后的"
            "一次 actual generation 外，不授权其他 generation、发布或上传步骤。",
        ),
        ("且 PR1 的三个发布开关均为字符串 `false`；", "且 S1 的三个发布开关均为字符串 `true`；"),
        ("从全新目录安装正式制品并重复 smoke；", "从全新目录安装本次 actual generation 候选制品并重复 smoke；"),
    ),
}


class ReleasePhase(str, Enum):
    """WorkItem 010 的有限发布阶段。"""

    S0 = "S0"
    S1 = "S1"
    S2_SUCCESS = "S2-success"
    S2_BURN = "S2-burn"


WI010_TERMINAL_REQUIRED_SURFACE_MARKERS = {
    ReleasePhase.S2_SUCCESS: {
        "README.md": "v1.0.5 是当前普通用户发布权威",
        "USER_GUIDE.zh-CN.md": "v1.0.5 是当前普通用户发布权威",
        "docs/product-contract.md": "`current published version is v1.0.5`",
        "docs/pull-request-checklist.zh.md": "普通用户安装权威已迁移到 v1.0.5",
        "packaging/offline/README.md": "v1.0.5 是当前普通用户离线发布权威",
        "packaging/offline/RELEASE_CHECKLIST.md": "v1.0.5 是当前发布权威",
    },
    ReleasePhase.S2_BURN: {
        "README.md": (
            "无论远端是否残留公开或未公开对象，v1.0.5 均不具备当前发布权威"
        ),
        "USER_GUIDE.zh-CN.md": (
            "无论远端是否残留公开或未公开对象，v1.0.5 均不具备当前发布权威"
        ),
        "docs/product-contract.md": (
            "## 1.0.5 永久终止真值（terminal-generation-burn / "
            "non-authoritative）"
        ),
        "docs/pull-request-checklist.zh.md": "WorkItem 010 generation-0 已永久烧毁",
        "packaging/offline/README.md": (
            "无论远端是否残留公开或未公开对象，v1.0.5 均不具备当前发布权威"
        ),
        "packaging/offline/RELEASE_CHECKLIST.md": (
            "无论远端是否残留公开或未公开对象，v1.0.5 均不具备当前发布权威"
        ),
    },
}
WI010_TERMINAL_DROPPED_REQUIRED_MARKERS = frozenset(
    {
        "WorkItem 010 three-PR release migration",
        "active no-bypass tag ruleset protects software and Certificate tags",
    }
)


@dataclass(frozen=True)
class GitTreeEntry:
    """从同一不可变 Git tree 读取的一条原始记录。"""

    path: bytes
    mode: bytes
    object_type: bytes
    object_id: bytes
    blob: bytes


@dataclass(frozen=True)
class ReleaseTreeSnapshot:
    """用于 phase 与 seal 校验的不可变 Git tree 快照。"""

    commit_oid: bytes
    tree_oid: bytes
    phase: ReleasePhase
    phase_payload: Mapping[str, object]
    expected_seal: str
    entries: tuple[GitTreeEntry, ...]


@dataclass(frozen=True)
class SealResult:
    """Whole-tree seal 的计算结果与失败诊断。"""

    commit_oid: str
    tree_oid: str
    phase: ReleasePhase | None
    entry_count: int
    expected: str
    actual: str
    findings: tuple[Finding, ...] = ()


@dataclass(frozen=True)
class Finding:
    """One public release identity violation."""

    path: str
    line: int | None
    marker: str
    excerpt: str


def _finding(marker: str, excerpt: str, path: str = "README.md") -> Finding:
    return Finding(path, None, marker, excerpt)


def _canonical_json_object(payload_text: str) -> dict[str, object]:
    """严格解析单行 canonical JSON，并拒绝重复 key。"""

    def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        parsed = json.loads(payload_text, object_pairs_hook=_unique_object)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid WI010 phase JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("WI010 phase payload must be an object")
    canonical = json.dumps(
        parsed,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if canonical != payload_text:
        raise ValueError("WI010 phase JSON must be canonical")
    return parsed


def _extract_phase_payload(text: str) -> tuple[ReleasePhase, dict[str, object]]:
    """从 README 中提取唯一 phase marker 并验证封闭 schema。"""

    matches = tuple(WI010_PHASE_MARKER_PATTERN.finditer(text))
    if text.count(WI010_PHASE_MARKER_PREFIX) != 1 or len(matches) != 1:
        raise ValueError("WI010 phase marker must appear exactly once")
    payload = _canonical_json_object(matches[0].group("payload"))
    phase_value = payload.get("phase")
    if not isinstance(phase_value, str):
        raise ValueError("WI010 phase must be a string")
    try:
        phase = ReleasePhase(phase_value)
    except ValueError as exc:
        raise ValueError("unknown WI010 phase") from exc

    expected_keys: frozenset[str]
    if phase in {ReleasePhase.S0, ReleasePhase.S1}:
        expected_keys = frozenset({"phase"})
    elif phase is ReleasePhase.S2_SUCCESS:
        expected_keys = WI010_SUCCESS_PAYLOAD_KEYS
    else:
        expected_keys = WI010_BURN_PAYLOAD_KEYS
    if frozenset(payload) != expected_keys:
        raise ValueError("WI010 phase payload key set is not canonical")
    _validate_phase_evidence(phase, payload)
    return phase, payload


def _positive_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _validate_phase_evidence(
    phase: ReleasePhase,
    payload: Mapping[str, object],
) -> None:
    """验证 terminal phase 的严格本地事实投影。"""

    if phase in {ReleasePhase.S0, ReleasePhase.S1}:
        return
    if payload.get("generation") != 0 or payload.get("workflow_run_attempt") != 1:
        raise ValueError("WI010 terminal generation or attempt is invalid")
    if not _positive_integer(payload.get("workflow_run_id")):
        raise ValueError("WI010 workflow run ID must be positive")

    if phase is ReleasePhase.S2_SUCCESS:
        if payload.get("immutable") is not True:
            raise ValueError("WI010 success must be immutable")
        if payload.get("tag_name") != "v1.0.5":
            raise ValueError("WI010 success tag is invalid")
        if payload.get("certificate_tag") != (
            "release-truth/v1.0.5/certificate/g0"
        ):
            raise ValueError("WI010 certificate tag is invalid")
        if not _positive_integer(payload.get("release_id")):
            raise ValueError("WI010 release ID must be positive")
        sha_keys = (
            "archive_commit_sha",
            "certificate_commit_sha",
            "proof_commit_sha",
            "tag_peel_sha",
            "target_commitish_resolved_sha",
        )
        sha_values = [payload.get(key) for key in sha_keys]
        if not all(
            isinstance(value, str) and WI010_HEX40_PATTERN.fullmatch(value)
            for value in sha_values
        ) or len(set(sha_values)) != 1:
            raise ValueError("WI010 success commit projections must be one exact SHA")
        tree_sha = payload.get("certificate_tree_sha")
        if not isinstance(tree_sha, str) or not WI010_HEX40_PATTERN.fullmatch(
            tree_sha
        ):
            raise ValueError("WI010 certificate tree SHA is invalid")
        for key in (
            "certificate_digest",
            "proof_digest",
            "release_attestation_digest",
        ):
            value = payload.get(key)
            if not isinstance(value, str) or not WI010_DIGEST_PATTERN.fullmatch(value):
                raise ValueError(f"WI010 {key} is invalid")
        return

    authority_kind = payload.get("authority_kind")
    terminal_stage = payload.get("terminal_stage")
    if authority_kind not in WI010_BURN_AUTHORITY_KINDS:
        raise ValueError("WI010 burn authority kind is invalid")
    if terminal_stage not in WI010_BURN_TERMINAL_STAGES:
        raise ValueError("WI010 burn terminal stage is invalid")
    for key in ("candidate_commit_sha", "candidate_tree_sha"):
        value = payload.get(key)
        if not isinstance(value, str) or not WI010_HEX40_PATTERN.fullmatch(value):
            raise ValueError(f"WI010 {key} is invalid")
    expected_kind, authority_template = WI010_BURN_AUTHORITY_BY_STAGE[
        str(terminal_stage)
    ]
    expected_authority_id = authority_template.format(
        workflow_run_id=payload["workflow_run_id"]
    )
    if authority_kind != expected_kind or payload.get("authority_id") != (
        expected_authority_id
    ):
        raise ValueError("WI010 burn authority kind/stage/ID tuple is invalid")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """把 redirect 留给调用方按固定 host/path 手工裁决。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        del req, fp, code, msg, headers, newurl
        return None


def _wi010_authority_tls_context() -> ssl.SSLContext:
    """从锁定的 certifi bundle 构造不读取 ambient CA 环境的 TLS context。"""

    try:
        distribution = importlib.metadata.distribution("certifi")
        if distribution.version != WI010_CERTIFI_VERSION:
            raise ValueError("WI010 authority certifi version differs")
        bundle_path = Path(distribution.locate_file("certifi/cacert.pem"))
        bundle = bundle_path.read_bytes()
        if hashlib.sha256(bundle).hexdigest() != WI010_CERTIFI_PEM_SHA256:
            raise ValueError("WI010 authority CA bundle digest differs")
        certificate_authorities = bundle.decode("ascii", errors="strict")
    except importlib.metadata.PackageNotFoundError as exc:
        raise ValueError("WI010 authority CA bundle is unavailable") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("WI010 authority CA bundle is invalid") from exc

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    context.load_verify_locations(cadata=certificate_authorities)
    return context


def _wi010_read_response(response, limit: int) -> bytes:  # noqa: ANN001
    encoding = response.headers.get("Content-Encoding", "identity")
    if encoding not in {"", "identity"}:
        raise ValueError("WI010 authority response must not be content-encoded")
    declared = response.headers.get("Content-Length")
    if declared is not None:
        try:
            if int(declared) < 0 or int(declared) > limit:
                raise ValueError("WI010 authority response exceeds the size limit")
        except ValueError as exc:
            raise ValueError("WI010 authority Content-Length is invalid") from exc
    body = response.read(limit + 1)
    if len(body) > limit:
        raise ValueError("WI010 authority response exceeds the size limit")
    return body


def _wi010_fetch_public_bytes(
    url: str,
    *,
    asset: bool = False,
    max_bytes: int = 4 * 1024 * 1024,
) -> bytes:
    """从固定 GitHub HTTPS authority 读取有界公开对象。"""

    parsed = urllib.parse.urlsplit(url)
    allowed_host = "github.com" if asset else "api.github.com"
    if (
        parsed.scheme != "https"
        or parsed.hostname != allowed_host
        or parsed.port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("WI010 authority URL is not canonical HTTPS")
    if not asset and not url.startswith(WI010_GITHUB_API_ROOT + "/"):
        raise ValueError("WI010 authority API path is outside the protected repository")
    if asset and not parsed.path.startswith(
        "/SinclairPan/Ai_AutoSDLC/releases/download/"
    ):
        raise ValueError("WI010 authority asset path is outside the protected repository")

    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=_wi010_authority_tls_context()),
        _NoRedirect(),
    )
    current = url
    for redirect_count in range(3):
        request = urllib.request.Request(
            current,
            headers={
                "Accept": (
                    "application/octet-stream"
                    if asset
                    else "application/vnd.github+json"
                ),
                "User-Agent": "ai-sdlc-wi010-release-authority/1",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="GET",
        )
        try:
            with opener.open(request, timeout=20) as response:
                if response.status != 200:
                    raise ValueError(
                        f"WI010 authority HTTP status is {response.status}"
                    )
                return _wi010_read_response(response, max_bytes)
        except urllib.error.HTTPError as exc:
            if not asset or exc.code not in {301, 302, 303, 307, 308}:
                raise ValueError(f"WI010 authority HTTP status is {exc.code}") from exc
            location = exc.headers.get("Location", "")
            target = urllib.parse.urlsplit(location)
            if (
                target.scheme != "https"
                or target.hostname != "release-assets.githubusercontent.com"
                or target.port not in {None, 443}
                or target.username is not None
                or target.password is not None
                or target.fragment
            ):
                raise ValueError("WI010 authority asset redirect is not canonical") from exc
            current = location
        except (OSError, urllib.error.URLError) as exc:
            raise ValueError("WI010 authority transport failed") from exc
        if redirect_count == 2:
            break
    raise ValueError("WI010 authority asset redirect limit exceeded")


def _wi010_json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"WI010 authority JSON duplicates key {key!r}")
        result[key] = value
    return result


def _wi010_parse_json_object(raw: bytes, label: str) -> dict[str, object]:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError(f"WI010 {label} JSON must not contain a BOM")
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_wi010_json_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number {token}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"WI010 {label} JSON is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError(f"WI010 {label} JSON must be an object")
    nodes = 0

    def visit(item: object, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > 50_000 or depth > 32:
            raise ValueError(f"WI010 {label} JSON exceeds structural limits")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError(f"WI010 {label} JSON key is invalid")
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
        elif not (
            item is None or isinstance(item, (str, int, float, bool))
        ):
            raise ValueError(f"WI010 {label} JSON value is invalid")

    visit(value, 0)
    return value


def _wi010_parse_json_array(raw: bytes, label: str) -> list[object]:
    """严格解析 verifier 的有界 JSON array 输出。"""

    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError(f"WI010 {label} JSON must not contain a BOM")
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_wi010_json_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number {token}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"WI010 {label} JSON is invalid") from exc
    if not isinstance(value, list):
        raise ValueError(f"WI010 {label} JSON must be an array")
    nodes = 0

    def visit(item: object, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > 50_000 or depth > 32:
            raise ValueError(f"WI010 {label} JSON exceeds structural limits")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError(f"WI010 {label} JSON key is invalid")
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
        elif not (item is None or isinstance(item, (str, int, float, bool))):
            raise ValueError(f"WI010 {label} JSON value is invalid")

    visit(value, 0)
    return value


def _wi010_fetch_json(path: str, label: str) -> dict[str, object]:
    if not path.startswith("/") or "//" in path:
        raise ValueError("WI010 authority API path is invalid")
    return _wi010_parse_json_object(
        _wi010_fetch_public_bytes(
            WI010_GITHUB_API_ROOT + path,
            max_bytes=2 * 1024 * 1024,
        ),
        label,
    )


def _wi010_require_keys(
    value: Mapping[str, object], expected: frozenset[str], label: str
) -> None:
    if frozenset(value) != expected:
        raise ValueError(f"WI010 {label} key set is not canonical")


def _wi010_object_digest(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _wi010_logical_digest(value: Mapping[str, object], field: str) -> str:
    payload = dict(value)
    payload.pop(field, None)
    return _wi010_object_digest(payload)


def _wi010_release_assets(
    release: Mapping[str, object], label: str
) -> dict[str, dict[str, object]]:
    raw_assets = release.get("assets")
    if not isinstance(raw_assets, list):
        raise ValueError(f"WI010 {label} assets are invalid")
    assets: dict[str, dict[str, object]] = {}
    for raw in raw_assets:
        if not isinstance(raw, dict):
            raise ValueError(f"WI010 {label} asset is invalid")
        name = raw.get("name")
        digest = raw.get("digest")
        size = raw.get("size")
        download = raw.get("browser_download_url")
        if (
            not isinstance(name, str)
            or not name
            or name in assets
            or not isinstance(digest, str)
            or WI010_DIGEST_PATTERN.fullmatch(digest) is None
            or not _positive_integer(size)
            or not isinstance(download, str)
        ):
            raise ValueError(f"WI010 {label} asset metadata is invalid")
        assets[name] = {
            "name": name,
            "digest": digest,
            "size": size,
            "browser_download_url": download,
        }
    return assets


def _wi010_model_assets(
    value: object, label: str
) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"WI010 {label} asset bindings are invalid")
    result: list[dict[str, object]] = []
    names: list[str] = []
    expected_keys = frozenset({"name", "digest", "size_bytes", "platform"})
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"WI010 {label} asset binding is invalid")
        _wi010_require_keys(item, expected_keys, f"{label} asset")
        name = item.get("name")
        digest = item.get("digest")
        size = item.get("size_bytes")
        platform = item.get("platform")
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(digest, str)
            or WI010_DIGEST_PATTERN.fullmatch(digest) is None
            or not _positive_integer(size)
            or not isinstance(platform, str)
            or not platform
        ):
            raise ValueError(f"WI010 {label} asset binding is invalid")
        names.append(name)
        result.append(dict(item))
    if names != sorted(set(names)):
        raise ValueError(f"WI010 {label} assets are not canonical")
    return tuple(result)


def _wi010_download_asset(asset: Mapping[str, object], label: str) -> bytes:
    size = asset["size"]
    url = asset["browser_download_url"]
    if not isinstance(size, int) or not isinstance(url, str):
        raise ValueError(f"WI010 {label} asset metadata is invalid")
    raw = _wi010_fetch_public_bytes(url, asset=True, max_bytes=4 * 1024 * 1024)
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    if len(raw) != size or digest != asset["digest"]:
        raise ValueError(f"WI010 {label} asset bytes differ from GitHub authority")
    return raw


WI010_PROOF_KEYS = frozenset(
    {
        "schema_version",
        "canonicalization_version",
        "compatibility_mode",
        "extensions",
        "repository",
        "admission_id",
        "admission_digest",
        "draft_release_id",
        "upload_url",
        "release_user_agent",
        "draft_release_updated_at",
        "tag_name",
        "tag_object_sha",
        "commit_sha",
        "tree_sha",
        "tag_ruleset_id",
        "tag_ruleset_digest",
        "required_policy_digest",
        "required_gates",
        "workflow_run_id",
        "workflow_run_attempt",
        "assets",
        "release_settings_digest",
        "publish_workflow_ref",
        "evidence_cutoff_at",
        "proof_digest",
    }
)
WI010_CERTIFICATE_KEYS = frozenset(
    {
        "schema_version",
        "canonicalization_version",
        "compatibility_mode",
        "extensions",
        "repository",
        "admission_id",
        "admission_digest",
        "github_release_id",
        "upload_url",
        "release_user_agent",
        "github_release_url",
        "tag_name",
        "tag_object_sha",
        "commit_sha",
        "tree_sha",
        "tag_ruleset_id",
        "tag_ruleset_digest",
        "workflow_run_id",
        "workflow_run_attempt",
        "proof_digest",
        "release_attestation_digest",
        "assets",
        "immutable",
        "revocation_generation",
        "issued_at",
        "certificate_digest",
    }
)


def _wi010_validate_annotated_tag(
    tag_name: str,
    expected_commit: str,
) -> tuple[str, dict[str, object]]:
    encoded = urllib.parse.quote(tag_name, safe="")
    ref = _wi010_fetch_json(f"/git/ref/tags/{encoded}", f"{tag_name} ref")
    ref_object = ref.get("object")
    if (
        ref.get("ref") != f"refs/tags/{tag_name}"
        or not isinstance(ref_object, dict)
        or ref_object.get("type") != "tag"
        or not isinstance(ref_object.get("sha"), str)
        or WI010_HEX40_PATTERN.fullmatch(str(ref_object["sha"])) is None
    ):
        raise ValueError(f"WI010 {tag_name} ref is not an annotated tag")
    tag_object_sha = str(ref_object["sha"])
    tag = _wi010_fetch_json(f"/git/tags/{tag_object_sha}", f"{tag_name} tag")
    target = tag.get("object")
    if (
        tag.get("tag") != tag_name
        or not isinstance(target, dict)
        or target.get("type") != "commit"
        or target.get("sha") != expected_commit
        or not isinstance(tag.get("message"), str)
    ):
        raise ValueError(f"WI010 {tag_name} annotated tag authority differs")
    return tag_object_sha, tag


def _wi010_validate_release_jobs(run_id: int, commit_sha: str) -> None:
    """确认 authority run 实际走了发布分支，而不是只成功完成 load probe。"""

    response = _wi010_fetch_json(
        f"/actions/runs/{run_id}/attempts/1/jobs?per_page=100",
        "release workflow jobs",
    )
    jobs = response.get("jobs")
    if (
        not isinstance(jobs, list)
        or not 1 <= len(jobs) <= 100
        or response.get("total_count") != len(jobs)
    ):
        raise ValueError("WI010 release workflow jobs authority is invalid")

    expected = {
        "Read-only Release Workflow Load Probe": "skipped",
        "Build Release Proof Inputs": "success",
        "Publish Proof-bound Release": "success",
    }
    for name, conclusion in expected.items():
        matches = [
            job
            for job in jobs
            if isinstance(job, dict) and job.get("name") == name
        ]
        if len(matches) != 1:
            raise ValueError("WI010 release publisher job authority differs")
        job = matches[0]
        if (
            job.get("run_id") != run_id
            or job.get("run_attempt") != 1
            or job.get("status") != "completed"
            or job.get("conclusion") != conclusion
            or job.get("head_sha") != commit_sha
            or not isinstance(job.get("id"), int)
        ):
            raise ValueError("WI010 release publisher job authority differs")


def _wi010_software_tag_message(run_id: int, commit_sha: str) -> str:
    return (
        "AI-SDLC v1.0.5\n\n"
        f"run_id={run_id} run_attempt=1 workflow_ref={WI010_RELEASE_WORKFLOW_REF} "
        f"commit={commit_sha} failure_policy=terminal-generation-burn; "
        "no cleanup, edit, reuse, or rerun"
    )


def _wi010_validate_burn_workflow_run(run_id: int, commit_sha: str) -> None:
    """确认 retained run 是失败的 actual generation，并锁住发布拓扑。"""

    run = _wi010_fetch_json(
        f"/actions/runs/{run_id}/attempts/1", "burn workflow run"
    )
    head_repository = run.get("head_repository")
    repository = run.get("repository")
    if (
        run.get("id") != run_id
        or run.get("name") != "Release Build"
        or run.get("display_title") != "release-admission|v1.0.5|g0"
        or run.get("run_attempt") != 1
        or run.get("event") != "workflow_dispatch"
        or run.get("status") != "completed"
        or run.get("conclusion") not in WI010_BURN_TERMINAL_CONCLUSIONS
        or run.get("head_branch") != "main"
        or run.get("head_sha") != commit_sha
        or run.get("path") != WI010_RELEASE_WORKFLOW_PATH
        or not isinstance(head_repository, dict)
        or head_repository.get("full_name") != WI010_REPOSITORY
        or not isinstance(repository, dict)
        or repository.get("full_name") != WI010_REPOSITORY
    ):
        raise ValueError("WI010 burn workflow run authority differs")

    response = _wi010_fetch_json(
        f"/actions/runs/{run_id}/attempts/1/jobs?per_page=100",
        "burn workflow jobs",
    )
    jobs = response.get("jobs")
    early_stop = run.get("conclusion") in WI010_BURN_EARLY_STOP_CONCLUSIONS
    if (
        not isinstance(jobs, list)
        or len(jobs) > 100
        or (not early_stop and len(jobs) < 1)
    ):
        raise ValueError("WI010 burn workflow jobs authority is invalid")
    if type(response.get("total_count")) is not int or response.get(
        "total_count"
    ) != len(jobs):
        raise ValueError("WI010 burn workflow jobs authority is invalid")

    conclusions: dict[str, str] = {}
    allowed = {*WI010_BURN_TERMINAL_CONCLUSIONS, "skipped", "success"}
    for job in jobs:
        if not isinstance(job, dict):
            raise ValueError("WI010 burn workflow jobs authority differs")
        name = job.get("name")
        conclusion = job.get("conclusion")
        if (
            not isinstance(name, str)
            or not name
            or name in conclusions
            or job.get("run_id") != run_id
            or job.get("run_attempt") != 1
            or job.get("status") != "completed"
            or conclusion not in allowed
            or job.get("head_sha") != commit_sha
            or type(job.get("id")) is not int
            or job.get("id", 0) < 1
        ):
            raise ValueError("WI010 burn workflow jobs authority differs")
        conclusions[name] = str(conclusion)

    load_probe = "Read-only Release Workflow Load Probe"
    publisher = "Publish Proof-bound Release"
    terminal_jobs = {
        name
        for name, conclusion in conclusions.items()
        if conclusion in WI010_BURN_TERMINAL_CONCLUSIONS
    }
    if early_stop:
        if conclusions.get(load_probe, "skipped") != "skipped" or conclusions.get(
            publisher, "skipped"
        ) == "success":
            raise ValueError("WI010 burn workflow jobs authority differs")
        return
    if (
        any(name not in conclusions for name in WI010_BURN_REQUIRED_JOB_NAMES)
        or conclusions[load_probe] != "skipped"
        or not terminal_jobs
    ):
        raise ValueError("WI010 burn workflow jobs authority differs")


def _validate_s2_burn_remote_authority(
    payload: Mapping[str, object],
    expected_commit_oid: bytes,
) -> None:
    """把 S2-burn 同时绑定失败 run 与已到达的不可变 namespace。"""

    commit_sha = expected_commit_oid.decode("ascii")
    run_id = payload.get("workflow_run_id")
    authority_kind = payload.get("authority_kind")
    if not isinstance(run_id, int):
        raise ValueError("WI010 burn workflow run ID is invalid")
    _wi010_validate_burn_workflow_run(run_id, commit_sha)
    if authority_kind == "actions-history-retention":
        return

    _software_tag_oid, software_tag = _wi010_validate_annotated_tag(
        "v1.0.5", commit_sha
    )
    if software_tag.get("message") != _wi010_software_tag_message(
        run_id, commit_sha
    ):
        raise ValueError("WI010 burn software tag message differs")
    if authority_kind == "protected-software-tag":
        return
    if authority_kind != "protected-certificate-tag":
        raise ValueError("WI010 burn authority kind is invalid")

    certificate_tag = "release-truth/v1.0.5/certificate/g0"
    _certificate_tag_oid, certificate_tag_object = _wi010_validate_annotated_tag(
        certificate_tag, commit_sha
    )
    message = certificate_tag_object.get("message")
    if (
        not isinstance(message, str)
        or WI010_CERTIFICATE_BURN_TAG_MESSAGE_PATTERN.fullmatch(message) is None
    ):
        raise ValueError("WI010 burn Certificate tag message differs")


def _wi010_certificate_statement_matches(
    statement: object,
    *,
    certificate_sha256: str,
    commit_sha: str,
    run_id: int,
) -> bool:
    """验证已通过密码学验签的 Certificate SLSA statement 语义闭包。"""

    if not isinstance(statement, dict):
        return False
    expected_invocation = (
        f"https://github.com/{WI010_REPOSITORY}/actions/runs/{run_id}/attempts/1"
    )
    expected_dependency = {
        "uri": f"git+https://github.com/{WI010_REPOSITORY}@refs/heads/main",
        "digest": {"gitCommit": commit_sha},
    }
    subjects = statement.get("subject")
    predicate = statement.get("predicate")
    if (
        statement.get("_type") != "https://in-toto.io/Statement/v1"
        or statement.get("predicateType") != "https://slsa.dev/provenance/v1"
        or subjects
        != [
            {
                "name": WI010_CERTIFICATE_ASSET,
                "digest": {"sha256": certificate_sha256},
            }
        ]
        or not isinstance(predicate, dict)
    ):
        return False
    build = predicate.get("buildDefinition")
    run = predicate.get("runDetails")
    if not isinstance(build, dict) or not isinstance(run, dict):
        return False
    external = build.get("externalParameters")
    internal = build.get("internalParameters")
    dependencies = build.get("resolvedDependencies")
    builder = run.get("builder")
    metadata = run.get("metadata")
    workflow = external.get("workflow") if isinstance(external, dict) else None
    github = internal.get("github") if isinstance(internal, dict) else None
    return bool(
        build.get("buildType")
        == "https://actions.github.io/buildtypes/workflow/v1"
        and workflow
        == {
            "ref": "refs/heads/main",
            "repository": f"https://github.com/{WI010_REPOSITORY}",
            "path": WI010_RELEASE_WORKFLOW_PATH,
        }
        and isinstance(github, dict)
        and github.get("event_name") == "workflow_dispatch"
        and isinstance(dependencies, list)
        and expected_dependency in dependencies
        and isinstance(builder, dict)
        and builder.get("id") == "https://github.com/actions/runner/github-hosted"
        and isinstance(metadata, dict)
        and metadata.get("invocationId") == expected_invocation
    )


def _wi010_attestation_verifier_environment(home: Path) -> dict[str, str]:
    """为隔离 verifier 仅保留解释器启动所需的平台环境。"""

    environment = {
        name: os.environ[name]
        for name in (
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "TMPDIR",
            "TEMP",
            "TMP",
        )
        if name in os.environ
    }
    environment.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "NO_PROXY": "*",
            "no_proxy": "*",
        }
    )
    return environment


def _wi010_run_attestation_verifier(
    certificate_raw: bytes,
    bundles: list[dict[str, object]],
    commit_sha: str,
    run_id: int,
    verifier_source: bytes,
) -> tuple[dict[str, object], ...]:
    """在有界子进程中执行同一 sealed Git blob 的验签器源码。"""

    if _git_blob_oid(verifier_source) != WI010_ATTESTATION_VERIFIER_BLOB_OID:
        raise ValueError("WI010 Certificate attestation verifier blob differs")
    run_invocation = (
        f"https://github.com/{WI010_REPOSITORY}/actions/runs/{run_id}/attempts/1"
    )
    with tempfile.TemporaryDirectory(prefix="ai-sdlc-wi010-attestation-") as temp:
        root = Path(temp)
        verifier_path = root / "github_attestation_verifier.py"
        artifact_path = root / WI010_CERTIFICATE_ASSET
        bundle_path = root / "attestations.jsonl"
        verifier_path.write_bytes(verifier_source)
        artifact_path.write_bytes(certificate_raw)
        bundle_path.write_text(
            "".join(
                json.dumps(bundle, ensure_ascii=False, sort_keys=True) + "\n"
                for bundle in bundles
            ),
            encoding="utf-8",
        )
        command = [
            sys.executable,
            "-I",
            str(verifier_path),
            str(artifact_path),
            "--bundle",
            str(bundle_path),
            "--repository",
            WI010_REPOSITORY,
            "--signer-workflow",
            WI010_RELEASE_WORKFLOW_REF,
            "--source-ref",
            "refs/heads/main",
            "--source-digest",
            commit_sha,
            "--build-trigger",
            "workflow_dispatch",
            "--signer-digest",
            commit_sha,
            "--run-invocation",
            run_invocation,
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                timeout=WI010_ATTESTATION_VERIFIER_TIMEOUT_SECONDS,
                cwd=root,
                env=_wi010_attestation_verifier_environment(root),
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError(
                "WI010 Certificate attestation verification timed out"
            ) from exc
        except OSError as exc:
            raise ValueError(
                "WI010 Certificate attestation verifier is unavailable"
            ) from exc
    if completed.returncode != 0:
        raise ValueError("WI010 Certificate attestation verification failed")
    if len(completed.stdout) > 2 * 1024 * 1024:
        raise ValueError("WI010 Certificate attestation verification result is oversized")
    try:
        parsed = _wi010_parse_json_array(
            completed.stdout,
            "Certificate attestation verification result",
        )
    except ValueError as exc:
        raise ValueError(
            "WI010 Certificate attestation verification result is invalid"
        ) from exc
    if not parsed or not all(isinstance(statement, dict) for statement in parsed):
        raise ValueError("WI010 Certificate attestation verification result is invalid")
    return tuple(statement for statement in parsed if isinstance(statement, dict))


def _wi010_validate_certificate_attestation(
    certificate_raw: bytes,
    commit_sha: str,
    run_id: int,
    verifier_source: bytes,
) -> None:
    """以 GitHub artifact attestation 的真实签名绑定发布 Certificate。"""

    certificate_sha256 = hashlib.sha256(certificate_raw).hexdigest()
    response = _wi010_fetch_json(
        f"/attestations/sha256:{certificate_sha256}?per_page=100",
        "Certificate attestation",
    )
    attestations = response.get("attestations")
    if not isinstance(attestations, list) or not 1 <= len(attestations) <= 100:
        raise ValueError("WI010 Certificate attestation is missing")
    bundles = [
        item.get("bundle")
        for item in attestations
        if isinstance(item, dict) and isinstance(item.get("bundle"), dict)
    ]
    if len(bundles) != len(attestations):
        raise ValueError("WI010 Certificate attestation bundle is invalid")
    statements = _wi010_run_attestation_verifier(
        certificate_raw,
        bundles,
        commit_sha,
        run_id,
        verifier_source,
    )
    if not statements or not any(
        _wi010_certificate_statement_matches(
            statement,
            certificate_sha256=certificate_sha256,
            commit_sha=commit_sha,
            run_id=run_id,
        )
        for statement in statements
    ):
        raise ValueError("WI010 Certificate attestation authority is invalid")


def _validate_s2_success_remote_authority(
    payload: Mapping[str, object],
    expected_commit_oid: bytes,
    expected_tree_oid: bytes,
    verifier_source: bytes,
) -> None:
    """把 S2-success 投影绑定到公开 immutable GitHub authority。"""

    commit_sha = expected_commit_oid.decode("ascii")
    tree_sha = expected_tree_oid.decode("ascii")
    release_id = payload["release_id"]
    run_id = payload["workflow_run_id"]
    if not isinstance(release_id, int) or not isinstance(run_id, int):
        raise ValueError("WI010 success authority IDs are invalid")
    release_name = "AI-SDLC v1.0.5"
    release_body = (
        "Qualified protected-main release v1.0.5.\n\n"
        f"run_id={run_id} run_attempt=1 workflow_ref={WI010_RELEASE_WORKFLOW_REF} "
        f"commit={commit_sha} failure_policy=terminal-generation-burn; "
        "no cleanup, edit, reuse, or rerun"
    )

    software_release = _wi010_fetch_json(
        f"/releases/{release_id}", "software release"
    )
    if (
        software_release.get("id") != release_id
        or software_release.get("tag_name") != "v1.0.5"
        or software_release.get("target_commitish") != commit_sha
        or software_release.get("name") != release_name
        or software_release.get("body") != release_body
        or software_release.get("draft") is not False
        or software_release.get("prerelease") is not False
        or software_release.get("immutable") is not True
        or not isinstance(software_release.get("html_url"), str)
    ):
        raise ValueError("WI010 software Release authority differs")
    software_assets = _wi010_release_assets(software_release, "software release")
    if set(software_assets) != {*WI010_SOFTWARE_ASSETS, WI010_PROOF_ASSET}:
        raise ValueError("WI010 software Release asset set differs")
    latest_release = _wi010_fetch_json("/releases/latest", "latest release")
    latest_assets = _wi010_release_assets(latest_release, "latest release")
    release_identity = (
        release_id,
        "v1.0.5",
        commit_sha,
        software_release.get("html_url"),
        release_name,
        release_body,
        False,
        False,
        True,
    )
    if (
        (
            latest_release.get("id"),
            latest_release.get("tag_name"),
            latest_release.get("target_commitish"),
            latest_release.get("html_url"),
            latest_release.get("name"),
            latest_release.get("body"),
            latest_release.get("draft"),
            latest_release.get("prerelease"),
            latest_release.get("immutable"),
        )
        != release_identity
        or latest_assets != software_assets
    ):
        raise ValueError("WI010 latest Release authority differs")

    software_tag_oid, software_tag = _wi010_validate_annotated_tag(
        "v1.0.5", commit_sha
    )
    software_message = _wi010_software_tag_message(run_id, commit_sha)
    if software_tag.get("message") != software_message:
        raise ValueError("WI010 software annotated tag message differs")

    commit = _wi010_fetch_json(f"/git/commits/{commit_sha}", "software commit")
    commit_tree = commit.get("tree")
    if (
        commit.get("sha") != commit_sha
        or not isinstance(commit_tree, dict)
        or commit_tree.get("sha") != tree_sha
    ):
        raise ValueError("WI010 software commit/tree authority differs")

    proof_raw = _wi010_download_asset(
        software_assets[WI010_PROOF_ASSET], "satisfaction proof"
    )
    proof = _wi010_parse_json_object(proof_raw, "satisfaction proof")
    _wi010_require_keys(proof, WI010_PROOF_KEYS, "satisfaction proof")
    proof_assets = _wi010_model_assets(proof.get("assets"), "satisfaction proof")
    proof_digest = _wi010_logical_digest(proof, "proof_digest")
    release_settings_digest = _wi010_object_digest(
        {
            "id": release_id,
            "name": release_name,
            "body": release_body,
            "tag_name": "v1.0.5",
            "target_commitish": commit_sha,
            "draft": True,
            "prerelease": False,
        }
    )
    expected_proof_identity = (
        WI010_REPOSITORY,
        release_id,
        "v1.0.5",
        software_tag_oid,
        commit_sha,
        tree_sha,
        run_id,
        1,
        WI010_RELEASE_WORKFLOW_REF,
    )
    actual_proof_identity = (
        proof.get("repository"),
        proof.get("draft_release_id"),
        proof.get("tag_name"),
        proof.get("tag_object_sha"),
        proof.get("commit_sha"),
        proof.get("tree_sha"),
        proof.get("workflow_run_id"),
        proof.get("workflow_run_attempt"),
        proof.get("publish_workflow_ref"),
    )
    if (
        proof.get("schema_version") != "release-satisfaction-proof.v1"
        or proof.get("canonicalization_version") != "canonical-json.v1"
        or proof.get("compatibility_mode") != "strict"
        or proof.get("extensions") != {}
        or actual_proof_identity != expected_proof_identity
        or proof.get("release_settings_digest") != release_settings_digest
        or proof.get("proof_digest") != proof_digest
        or payload.get("proof_digest") != proof_digest
    ):
        raise ValueError("WI010 satisfaction Proof authority differs")
    live_binary_assets = {
        name: (asset["digest"], asset["size"])
        for name, asset in software_assets.items()
        if name != WI010_PROOF_ASSET
    }
    proof_binary_assets = {
        str(asset["name"]): (asset["digest"], asset["size_bytes"])
        for asset in proof_assets
    }
    if proof_binary_assets != live_binary_assets:
        raise ValueError("WI010 satisfaction Proof asset bindings differ")

    run = _wi010_fetch_json(
        f"/actions/runs/{run_id}/attempts/1", "release workflow run"
    )
    head_repository = run.get("head_repository")
    repository = run.get("repository")
    if (
        run.get("id") != run_id
        or run.get("run_attempt") != 1
        or run.get("event") != "workflow_dispatch"
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
        or run.get("head_branch") != "main"
        or run.get("head_sha") != commit_sha
        or run.get("path") != WI010_RELEASE_WORKFLOW_PATH
        or not isinstance(head_repository, dict)
        or head_repository.get("full_name") != WI010_REPOSITORY
        or not isinstance(repository, dict)
        or repository.get("full_name") != WI010_REPOSITORY
    ):
        raise ValueError("WI010 release workflow run authority differs")
    _wi010_validate_release_jobs(run_id, commit_sha)

    certificate_tag = "release-truth/v1.0.5/certificate/g0"
    certificate_release = _wi010_fetch_json(
        "/releases/tags/" + urllib.parse.quote(certificate_tag, safe=""),
        "certificate release",
    )
    if (
        certificate_release.get("tag_name") != certificate_tag
        or certificate_release.get("target_commitish") != commit_sha
        or certificate_release.get("draft") is not False
        or certificate_release.get("prerelease") is not True
        or certificate_release.get("immutable") is not True
    ):
        raise ValueError("WI010 Certificate Release authority differs")
    certificate_assets = _wi010_release_assets(
        certificate_release, "certificate release"
    )
    if set(certificate_assets) != {WI010_CERTIFICATE_ASSET}:
        raise ValueError("WI010 Certificate Release asset set differs")

    _certificate_tag_oid, certificate_tag_object = _wi010_validate_annotated_tag(
        certificate_tag, commit_sha
    )
    admission_digest = proof.get("admission_digest")
    if (
        not isinstance(admission_digest, str)
        or WI010_DIGEST_PATTERN.fullmatch(admission_digest) is None
    ):
        raise ValueError("WI010 Proof admission digest is invalid")
    certificate_tag_message = (
        "Permanent Certificate for v1.0.5\n\n"
        f"software_admission_digest={admission_digest} "
        f"software_proof_digest={proof_digest} "
        "failure_policy=terminal-generation-burn; no cleanup, edit, reuse, or rerun"
    )
    if certificate_tag_object.get("message") != certificate_tag_message:
        raise ValueError("WI010 Certificate annotated tag message differs")

    certificate_raw = _wi010_download_asset(
        certificate_assets[WI010_CERTIFICATE_ASSET], "release certificate"
    )
    certificate = _wi010_parse_json_object(certificate_raw, "release certificate")
    _wi010_require_keys(certificate, WI010_CERTIFICATE_KEYS, "release certificate")
    certificate_model_assets = _wi010_model_assets(
        certificate.get("assets"), "release certificate"
    )
    certificate_digest = _wi010_logical_digest(
        certificate, "certificate_digest"
    )
    expected_certificate_identity = (
        WI010_REPOSITORY,
        proof.get("admission_id"),
        admission_digest,
        release_id,
        software_release.get("html_url"),
        "v1.0.5",
        software_tag_oid,
        commit_sha,
        tree_sha,
        proof.get("tag_ruleset_id"),
        proof.get("tag_ruleset_digest"),
        run_id,
        1,
        proof_digest,
    )
    actual_certificate_identity = (
        certificate.get("repository"),
        certificate.get("admission_id"),
        certificate.get("admission_digest"),
        certificate.get("github_release_id"),
        certificate.get("github_release_url"),
        certificate.get("tag_name"),
        certificate.get("tag_object_sha"),
        certificate.get("commit_sha"),
        certificate.get("tree_sha"),
        certificate.get("tag_ruleset_id"),
        certificate.get("tag_ruleset_digest"),
        certificate.get("workflow_run_id"),
        certificate.get("workflow_run_attempt"),
        certificate.get("proof_digest"),
    )
    binding = {
        "repository": WI010_REPOSITORY,
        "tag_name": "v1.0.5",
        "commit_sha": commit_sha,
        "tree_sha": tree_sha,
        "assets": sorted(
            (
                {
                    "name": name,
                    "digest": asset["digest"],
                    "size_bytes": asset["size"],
                }
                for name, asset in software_assets.items()
            ),
            key=lambda item: str(item["name"]),
        ),
    }
    binding_bytes = json.dumps(
        binding,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    attestation_digest = "sha256:" + hashlib.sha256(binding_bytes).hexdigest()
    if (
        certificate.get("schema_version") != "release-certificate.v1"
        or certificate.get("canonicalization_version") != "canonical-json.v1"
        or certificate.get("compatibility_mode") != "strict"
        or certificate.get("extensions") != {}
        or actual_certificate_identity != expected_certificate_identity
        or certificate.get("immutable") is not True
        or certificate.get("revocation_generation") != 0
        or certificate.get("certificate_digest") != certificate_digest
        or payload.get("certificate_digest") != certificate_digest
        or certificate.get("release_attestation_digest") != attestation_digest
        or payload.get("release_attestation_digest") != attestation_digest
        or tuple(certificate_model_assets) != tuple(proof_assets)
    ):
        raise ValueError("WI010 Release Certificate authority differs")
    certificate_body = (
        "Permanent generation-0 release certificate for v1.0.5.\n\n"
        f"run_id={run_id} run_attempt=1 workflow_ref={WI010_RELEASE_WORKFLOW_REF} "
        f"commit={commit_sha} software_admission_digest={admission_digest} "
        f"software_proof_digest={proof_digest} failure_policy=terminal-generation-burn; "
        "no cleanup, edit, reuse, or rerun"
    )
    if (
        certificate_release.get("name") != "Release Truth v1.0.5 Certificate g0"
        or certificate_release.get("body") != certificate_body
    ):
        raise ValueError("WI010 Certificate Release identity text differs")
    _wi010_validate_certificate_attestation(
        certificate_raw,
        commit_sha,
        run_id,
        verifier_source,
    )


def _markdown_without_html_comments(text: str) -> str:
    """移除读者不可见的 HTML 注释，避免隐藏 token 满足公开面。"""

    return HTML_COMMENT_PATTERN.sub("", text)


def _validate_profile_flags(
    files: Mapping[str, str],
    phase: ReleasePhase,
) -> list[Finding]:
    expected = "true" if phase is ReleasePhase.S1 else "false"
    findings: list[Finding] = []
    workflow = files.get(".github/workflows/release-build.yml", "")
    for flag in WI010_RELEASE_FLAGS:
        pattern = re.compile(
            rf'^\s*{re.escape(flag)}:\s*"(?P<value>true|false)"\s*$',
            re.MULTILINE,
        )
        values = tuple(match.group("value") for match in pattern.finditer(workflow))
        if values != (expected,):
            findings.append(
                _finding(
                    "wi010-release-profile-flags-invalid",
                    f"{flag}: expected one {expected}, got {values}",
                    ".github/workflows/release-build.yml",
                )
            )

    integration = files.get("tests/integration/test_github_workflows.py", "")
    expected_counts = {
        "RELEASE_BOOTSTRAP_ENABLED": (2, 1) if expected == "true" else (0, 3),
        "RELEASE_ENVIRONMENT_PROTECTION_VERIFIED": (
            (2, 0) if expected == "true" else (0, 2)
        ),
        "RELEASE_TAG_RULESET_PROTECTION_VERIFIED": (
            (2, 0) if expected == "true" else (0, 2)
        ),
    }
    for flag, (true_count, false_count) in expected_counts.items():
        actual_true = integration.count(f'{flag}\"] == "true"') + integration.count(
            f'"{flag}": "true"'
        )
        actual_false = integration.count(
            f'{flag}\"] == "false"'
        ) + integration.count(f'"{flag}": "false"')
        if (actual_true, actual_false) != (true_count, false_count):
            findings.append(
                _finding(
                    "wi010-release-profile-expectations-invalid",
                    f"{flag}: expected {(true_count, false_count)}, "
                    f"got {(actual_true, actual_false)}",
                    "tests/integration/test_github_workflows.py",
                )
            )
    return findings


def _validate_profile_installers(
    files: Mapping[str, str],
    phase: ReleasePhase,
    payload: Mapping[str, object],
) -> list[Finding]:
    installer_texts = {
        path: files.get(path, "") for path in WI010_INSTALLER_BASELINE_DEFAULTS
    }
    if phase is ReleasePhase.S2_SUCCESS:
        expected_sha = str(payload["archive_commit_sha"])
        expected_url = (
            f"https://github.com/SinclairPan/Ai_AutoSDLC/archive/{expected_sha}.zip"
        )
        success_defaults = {
            "packaging/install_online.sh": (
                f'PACKAGE_SPEC="${{AI_SDLC_PACKAGE_SPEC:-{expected_url}}}"'
            ),
            "packaging/install_online.ps1": (
                f'[string]$PackageSpec = "{expected_url}",'
            ),
        }
        for path, success_default in success_defaults.items():
            text = installer_texts[path]
            if text.count(success_default) != 1:
                break
            text = text.replace(
                success_default,
                WI010_INSTALLER_BASELINE_DEFAULTS[path],
            )
            if path == "packaging/install_online.sh":
                success_comment = (
                    f"#   AI_SDLC_PACKAGE_SPEC={expected_url}   "
                    "可选的精确 PR2 commit archive 安装源"
                )
                baseline_comment = (
                    "#   AI_SDLC_PACKAGE_SPEC=ai-sdlc==1.0.2   "
                    "optional published package spec for pip install"
                )
                if text.count(success_comment) != 1:
                    break
                text = text.replace(success_comment, baseline_comment)
            installer_texts[path] = text
        else:
            valid = all(
                hashlib.sha256(installer_texts[path].encode("utf-8")).hexdigest()
                == WI010_INSTALLER_BASELINE_DIGESTS[path]
                for path in installer_texts
            )
            if valid:
                return []

    valid = all(
        hashlib.sha256(text.encode("utf-8")).hexdigest()
        == WI010_INSTALLER_BASELINE_DIGESTS[path]
        for path, text in installer_texts.items()
    )
    if valid:
        return []
    return [
        _finding(
            "wi010-release-profile-installer-invalid",
            phase.value,
            "packaging/install_online.sh",
        )
    ]


def validate_wi010_release_profile(files: Mapping[str, str]) -> list[Finding]:
    """验证四相闭集；不再从开放自然语言推断阶段。"""

    readme = files.get("README.md", "")
    try:
        phase, payload = _extract_phase_payload(readme)
    except ValueError as exc:
        marker = (
            "wi010-release-phase-marker-invalid"
            if readme.count(WI010_PHASE_MARKER_PREFIX) != 1
            else "wi010-release-phase-payload-invalid"
        )
        return [_finding(marker, str(exc))]

    findings: list[Finding] = []
    expected_marker = WI010_RELEASE_STATE_MARKERS[phase.value]
    other_markers = {
        marker
        for state, marker in WI010_RELEASE_STATE_MARKERS.items()
        if state != phase.value
    }
    for path in WI010_RELEASE_SURFACES:
        visible = _markdown_without_html_comments(files.get(path, ""))
        if visible.count(expected_marker) != 1 or any(
            marker in visible for marker in other_markers
        ):
            findings.append(
                _finding("wi010-release-profile-mixed", phase.value, path)
            )
        if phase is ReleasePhase.S2_SUCCESS:
            expected_archive = (
                WI010_ARCHIVE_PREFIX + str(payload["archive_commit_sha"]) + ".zip"
            )
            expected_spec_line = WI010_CANONICAL_ONLINE_SPEC_PREFIX + expected_archive
            visible_lines = visible.splitlines()
            if (
                visible_lines.count(expected_spec_line) != 1
                or visible.count(expected_archive) != 1
                or visible.count(WI010_ARCHIVE_PREFIX) != 1
            ):
                findings.append(
                    _finding(
                        "wi010-release-profile-archive-invalid",
                        phase.value,
                        path,
                    )
                )
        elif WI010_ARCHIVE_PREFIX in visible:
            findings.append(
                _finding(
                    "wi010-release-profile-archive-invalid",
                    phase.value,
                    path,
                )
            )
    findings.extend(_validate_profile_flags(files, phase))
    findings.extend(_validate_profile_installers(files, phase, payload))
    return findings


GIT_LOCAL_ENVIRONMENT_VARIABLES = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_ATTR_NOSYSTEM",
        "GIT_ATTR_SOURCE",
        "GIT_COMMON_DIR",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_SYSTEM",
        "GIT_DIR",
        "GIT_GRAFT_FILE",
        "GIT_IMPLICIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_INTERNAL_SUPER_PREFIX",
        "GIT_NO_REPLACE_OBJECTS",
        "GIT_OBJECT_DIRECTORY",
        "GIT_PREFIX",
        "GIT_REPLACE_REF_BASE",
        "GIT_SHALLOW_FILE",
        "GIT_WORK_TREE",
    }
)


def _git_environment() -> dict[str, str]:
    """保留平台 Git 配置，但拒绝把可信读取路由到另一仓库。"""

    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in GIT_LOCAL_ENVIRONMENT_VARIABLES
    }
    environment["GIT_NO_LAZY_FETCH"] = "1"
    environment["GIT_TERMINAL_PROMPT"] = "0"
    return environment


def _protected_main_environment(home: Path) -> dict[str, str]:
    """只保留启动 Git 所需的平台变量，隔离所有仓库与网络传输重写。"""

    environment = {
        name: os.environ[name]
        for name in (
            "ALL_PROXY",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "NO_PROXY",
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "TMPDIR",
            "TEMP",
            "TMP",
            "all_proxy",
            "https_proxy",
            "http_proxy",
            "no_proxy",
        )
        if name in os.environ
    }
    environment.update(
        {
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "never",
            "HOME": str(home),
            "LC_ALL": "C",
            "XDG_CONFIG_HOME": str(home),
        }
    )
    return environment


def _git_bytes(root: Path, *arguments: str, input_data: bytes | None = None) -> bytes:
    completed = subprocess.run(
        ["git", "--no-replace-objects", *arguments],
        cwd=root,
        env=_git_environment(),
        input=input_data,
        check=True,
        capture_output=True,
        shell=False,
    )
    return completed.stdout


def _assert_git_root(root: Path) -> None:
    discovered = _git_bytes(root, "rev-parse", "--show-toplevel").rstrip(b"\n")
    try:
        discovered_root = Path(discovered.decode("utf-8")).resolve()
    except UnicodeDecodeError as exc:
        raise ValueError("Git root is not strict UTF-8") from exc
    if discovered_root != root.resolve():
        raise ValueError(
            f"Git root differs from requested root: {discovered_root} != {root.resolve()}"
        )


def _assert_no_content_transform_attributes(
    root: Path,
    tracked_paths: Sequence[bytes],
) -> None:
    """拒绝会让 Git 比较内容不同于 Python 实际读取字节的 attributes。"""

    attributes = (b"filter", b"working-tree-encoding", b"ident")
    payload = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "check-attr",
            "-z",
            "--stdin",
            *(attribute.decode("ascii") for attribute in attributes),
        ],
        cwd=root,
        env=_git_environment(),
        input=b"".join(path + b"\0" for path in tracked_paths),
        check=True,
        capture_output=True,
    ).stdout
    fields = payload.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) != len(tracked_paths) * len(attributes) * 3:
        raise ValueError("malformed Git attribute result")
    tracked = set(tracked_paths)
    for index in range(0, len(fields), 3):
        path, attribute, value = fields[index : index + 3]
        if path not in tracked or attribute not in attributes:
            raise ValueError("Git attribute result differs from tracked paths")
        if value not in {b"unspecified", b"unset"}:
            excerpt = path.decode("utf-8", errors="backslashreplace")
            raise ValueError(
                f"tracked path uses content-transform attribute {attribute!r}: {excerpt}"
            )


def _parse_index_entries(payload: bytes) -> tuple[tuple[bytes, bytes, bytes], ...]:
    if not payload or not payload.endswith(b"\0"):
        raise ValueError("Git index listing is empty or unterminated")
    entries: list[tuple[bytes, bytes, bytes]] = []
    seen_paths: set[bytes] = set()
    for record in payload[:-1].split(b"\0"):
        try:
            metadata, path = record.split(b"\t", 1)
            mode, object_id, stage = metadata.split(b" ")
        except ValueError as exc:
            raise ValueError("malformed Git index entry") from exc
        if (
            not path
            or path in seen_paths
            or stage != b"0"
            or mode not in WI010_ALLOWED_MODES
        ):
            raise ValueError("Git index is unmerged, duplicated, or has invalid mode")
        _parse_object_id(object_id, "index entry")
        seen_paths.add(path)
        entries.append((path, mode, object_id))
    entries.sort(key=lambda entry: entry[0])
    return tuple(entries)


def _worktree_blob_matches(
    root: Path,
    entry: GitTreeEntry,
    actual: bytes,
) -> bool:
    expected = entry.blob
    if actual == expected:
        return True
    if os.name != "nt":
        return False
    if b"\0" in actual or b"\0" in expected:
        return False
    try:
        actual.decode("utf-8")
        expected.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if actual.replace(b"\r\n", b"\n") != expected:
        return False
    relative = entry.path.decode("utf-8")
    clean_oid = _git_bytes(
        root,
        "hash-object",
        "--filters",
        f"--path={relative}",
        "--stdin",
        input_data=actual,
    ).strip()
    return clean_oid == entry.object_id


def _read_worktree_blob(root: Path, entry: GitTreeEntry) -> bytes:
    try:
        relative = entry.path.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("tracked path is not strict UTF-8") from exc
    relative_path = Path(relative)
    if (
        relative_path.is_absolute()
        or relative_path.as_posix() != relative
        or any(part in {"", ".", ".."} for part in relative_path.parts)
    ):
        raise ValueError(f"tracked path is not canonical: {relative!r}")
    path = root / relative_path
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"tracked path is not a regular file: {relative}")
    if os.name != "nt":
        actual_executable = bool(before.st_mode & stat.S_IXUSR)
        if actual_executable != (entry.mode == b"100755"):
            raise ValueError(f"worktree mode differs from HEAD: {relative}")
    with path.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        content = handle.read()
        finished = os.fstat(handle.fileno())
    after = path.lstat()
    identity_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    if any(
        getattr(before, field) != getattr(opened, field)
        or getattr(opened, field) != getattr(finished, field)
        or getattr(finished, field) != getattr(after, field)
        for field in identity_fields
    ):
        raise ValueError(f"tracked path changed while being verified: {relative}")
    return content


def _parse_object_id(value: bytes, label: str) -> bytes:
    candidate = value.rstrip(b"\n")
    if len(candidate) not in {40, 64} or any(
        byte not in b"0123456789abcdef" for byte in candidate
    ):
        raise ValueError(f"invalid {label} object ID")
    return candidate


def _parse_ls_tree_z(payload: bytes) -> tuple[tuple[bytes, bytes, bytes, bytes], ...]:
    if not payload or not payload.endswith(b"\0"):
        raise ValueError("Git tree listing is empty or unterminated")
    records = payload[:-1].split(b"\0")
    entries: list[tuple[bytes, bytes, bytes, bytes]] = []
    seen_paths: set[bytes] = set()
    for record in records:
        try:
            metadata, path = record.split(b"\t", 1)
            mode, object_type, object_id = metadata.split(b" ")
        except ValueError as exc:
            raise ValueError("malformed Git tree entry") from exc
        if not path or path in seen_paths:
            raise ValueError("Git tree path is empty or duplicated")
        if object_type != b"blob" or mode not in WI010_ALLOWED_MODES:
            raise ValueError("unsupported Git tree object type or mode")
        _parse_object_id(object_id, "tree entry")
        seen_paths.add(path)
        entries.append((path, mode, object_type, object_id))
    entries.sort(key=lambda entry: entry[0])
    return tuple(entries)


def _parse_cat_file_batch(
    payload: bytes,
    entries: Sequence[tuple[bytes, bytes, bytes, bytes]],
) -> tuple[GitTreeEntry, ...]:
    stream = io.BytesIO(payload)
    resolved: list[GitTreeEntry] = []
    for path, mode, object_type, expected_id in entries:
        header = stream.readline()
        if not header.endswith(b"\n"):
            raise ValueError("unterminated git cat-file header")
        try:
            actual_id, actual_type, size_text = header[:-1].split(b" ")
        except ValueError as exc:
            raise ValueError("malformed git cat-file header") from exc
        if (
            actual_id != expected_id
            or actual_type != b"blob"
            or not size_text
            or (len(size_text) > 1 and size_text.startswith(b"0"))
            or not size_text.isdigit()
        ):
            raise ValueError("git cat-file object differs from tree entry")
        size = int(size_text)
        blob = stream.read(size)
        if len(blob) != size or stream.read(1) != b"\n":
            raise ValueError("truncated git cat-file blob")
        resolved.append(
            GitTreeEntry(path, mode, object_type, expected_id, blob)
        )
    if stream.read():
        raise ValueError("unexpected trailing git cat-file output")
    return tuple(resolved)


def _read_git_tree(
    root: Path,
    treeish: str,
) -> tuple[bytes, bytes, tuple[GitTreeEntry, ...]]:
    _assert_git_root(root)
    commit_oid = _parse_object_id(
        _git_bytes(root, "rev-parse", "--verify", f"{treeish}^{{commit}}"),
        "commit",
    )
    tree_oid = _parse_object_id(
        _git_bytes(
            root,
            "rev-parse",
            "--verify",
            f"{commit_oid.decode('ascii')}^{{tree}}",
        ),
        "tree",
    )
    listed = _parse_ls_tree_z(
        _git_bytes(
            root,
            "ls-tree",
            "-r",
            "-z",
            "--full-tree",
            tree_oid.decode("ascii"),
        )
    )
    batch = _git_bytes(
        root,
        "cat-file",
        "--batch",
        input_data=b"".join(entry[3] + b"\n" for entry in listed),
    )
    entries = _parse_cat_file_batch(batch, listed)
    for path, expected_mode in WI010_TRUST_ROOTS.items():
        matches = [entry for entry in entries if entry.path == path]
        if (
            len(matches) != 1
            or matches[0].mode != expected_mode
            or matches[0].object_type != b"blob"
        ):
            raise ValueError(f"WI010 trust root is missing or has wrong mode: {path!r}")
    return commit_oid, tree_oid, entries


def _readme_entry(entries: Sequence[GitTreeEntry]) -> GitTreeEntry:
    matches = [entry for entry in entries if entry.path == WI010_README_PATH]
    if len(matches) != 1:
        raise ValueError("README must appear exactly once in the Git tree")
    return matches[0]


def _extract_expected_seal(readme_blob: bytes) -> tuple[bytes, str]:
    try:
        text = readme_blob.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("README must be strict UTF-8") from exc
    matches = tuple(WI010_SEAL_MARKER_PATTERN.finditer(text))
    if text.count(WI010_SEAL_MARKER_PREFIX) != 1 or len(matches) != 1:
        raise ValueError("WI010 seal marker must appear exactly once")
    start, end = matches[0].span("seal")
    expected = matches[0].group("seal")
    prefix = text[:start].encode("utf-8")
    suffix = text[end:].encode("utf-8")
    return prefix + (b"0" * 64) + suffix, expected


def read_release_tree_snapshot(
    root: Path,
    treeish: str = "HEAD",
) -> ReleaseTreeSnapshot:
    """一次解析 treeish，并从同一 Git tree 读取 phase、anchor 与 blobs。"""

    commit_oid, tree_oid, entries = _read_git_tree(root, treeish)
    readme = _readme_entry(entries)
    try:
        readme_text = readme.blob.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("README must be strict UTF-8") from exc
    phase, payload = _extract_phase_payload(readme_text)
    _normalized, expected = _extract_expected_seal(readme.blob)
    return ReleaseTreeSnapshot(
        commit_oid=commit_oid,
        tree_oid=tree_oid,
        phase=phase,
        phase_payload=payload,
        expected_seal=expected,
        entries=entries,
    )


def _frame(digest: object, value: bytes) -> None:
    digest.update(str(len(value)).encode("ascii"))
    digest.update(b"\0")
    digest.update(value)


def compute_release_tree_seal(snapshot: ReleaseTreeSnapshot) -> str:
    """计算同一不可变 snapshot 的 domain-separated whole-tree seal。"""

    digest = hashlib.sha256()
    _frame(digest, WI010_SEAL_DOMAIN)
    _frame(digest, snapshot.phase.value.encode("utf-8"))
    _frame(digest, str(len(snapshot.entries)).encode("ascii"))
    for entry in sorted(snapshot.entries, key=lambda item: item.path):
        blob = entry.blob
        if entry.path == WI010_README_PATH:
            blob, _expected = _extract_expected_seal(blob)
        _frame(digest, entry.mode)
        _frame(digest, entry.object_type)
        _frame(digest, entry.path)
        _frame(digest, blob)
    return digest.hexdigest()


def validate_release_tree_seal(
    root: Path,
    treeish: str = "HEAD",
    *,
    snapshot: ReleaseTreeSnapshot | None = None,
) -> SealResult:
    """校验 whole-tree seal；任何 Git 或解析异常都 fail closed。"""

    try:
        resolved = snapshot or read_release_tree_snapshot(root, treeish)
        actual = compute_release_tree_seal(resolved)
        findings: tuple[Finding, ...] = ()
        if actual != resolved.expected_seal:
            findings = (
                _finding(
                    "wi010-release-tree-seal-mismatch",
                    f"expected={resolved.expected_seal} actual={actual}",
                ),
            )
        return SealResult(
            resolved.commit_oid.decode("ascii"),
            resolved.tree_oid.decode("ascii"),
            resolved.phase,
            len(resolved.entries),
            resolved.expected_seal,
            actual,
            findings,
        )
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        return SealResult(
            "",
            "",
            None,
            0,
            "",
            "",
            (_finding("wi010-release-tree-seal-invalid", str(exc)),),
        )


def _optional_phase(entries: Sequence[GitTreeEntry]) -> ReleasePhase | None:
    readme = _readme_entry(entries)
    try:
        text = readme.blob.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("parent README must be strict UTF-8") from exc
    if WI010_PHASE_MARKER_PREFIX not in text:
        return None
    phase, _payload = _extract_phase_payload(text)
    return phase


def _entry_map(entries: Sequence[GitTreeEntry]) -> dict[bytes, GitTreeEntry]:
    return {entry.path: entry for entry in entries}


def _git_blob_oid(blob: bytes) -> bytes:
    header = f"blob {len(blob)}\0".encode("ascii")
    return hashlib.sha1(header + blob).hexdigest().encode("ascii")


def _foundation_trust_root_oid(path: bytes, blob: bytes) -> bytes:
    """对 constraints 唯一自引用 pin 归零后计算冻结 Git blob 身份。"""

    if path == WI010_CONSTRAINTS_PATH:
        normalized, count = WI010_CONSTRAINTS_VALIDATOR_PIN_PATTERN.subn(
            b'WI010_VALIDATOR_BLOB_OID = "' + b"0" * 40 + b'"',
            blob,
        )
        if count != 1:
            raise ValueError("WI010 constraints validator pin is not unique")
        blob = normalized
    return _git_blob_oid(blob)


def _validate_foundation_trust_roots(
    current_map: Mapping[bytes, GitTreeEntry],
) -> None:
    """把已审 validator、constraints 与 unit test 固定为一个互锁闭集。"""

    validator = current_map[b"scripts/validate_public_release_identity.py"]
    constraints = current_map[WI010_CONSTRAINTS_PATH]
    pins = WI010_CONSTRAINTS_VALIDATOR_PIN_PATTERN.findall(constraints.blob)
    if pins != [validator.object_id]:
        raise ValueError("WI010 constraints does not pin the foundation validator")
    for path, expected_oid in WI010_FOUNDATION_TRUST_ROOT_OIDS.items():
        if _foundation_trust_root_oid(path, current_map[path].blob) != expected_oid:
            raise ValueError(f"WI010 foundation trust-root blob differs: {path!r}")


def _replace_once(blob: bytes, old: str, new: str, path: bytes) -> bytes:
    """只在 raw blob 中执行一次已冻结的 UTF-8 字节替换。"""

    old_bytes = old.encode("utf-8")
    if blob.count(old_bytes) != 1:
        raise ValueError(f"WI010 replacement anchor differs for {path!r}")
    return blob.replace(old_bytes, new.encode("utf-8"), 1)


def _normalize_readme_seal(blob: bytes) -> bytes:
    normalized, _seal = _extract_expected_seal(blob)
    return normalized


def _render_foundation_readme(parent_blob: bytes) -> bytes:
    """仅在固定 anchor README 标题后插入 S0 phase 与 seal 标记。"""

    title = b"# AI-SDLC 1.0.5\n\n"
    if not parent_blob.startswith(title):
        raise ValueError("WI010 foundation README title differs from the anchor")
    if (
        WI010_PHASE_MARKER_PREFIX.encode("ascii") in parent_blob
        or WI010_SEAL_MARKER_PREFIX.encode("ascii") in parent_blob
    ):
        raise ValueError("WI010 foundation anchor README already contains release markers")
    markers = (
        b'<!-- WI010_RELEASE_PHASE: {"phase":"S0"} -->\n'
        b"<!-- WI010_RELEASE_TREE_SEAL: " + b"0" * 64 + b" -->\n\n"
    )
    return title + markers + parent_blob[len(title) :]


def _phase_marker(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        dict(payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return f"<!-- WI010_RELEASE_PHASE: {encoded} -->"


def _render_s0_to_s1_blob(path: bytes, parent_blob: bytes) -> bytes:
    rendered = _normalize_readme_seal(parent_blob) if path == WI010_README_PATH else parent_blob
    if path == b".github/workflows/release-build.yml":
        for flag in WI010_RELEASE_FLAGS:
            rendered = _replace_once(
                rendered,
                f'  {flag}: "false"',
                f'  {flag}: "true"',
                path,
            )
        return rendered
    if path == b"tests/integration/test_github_workflows.py":
        replacements = (
            (
                '        "CURRENT_RELEASE_TAG": "v1.0.5",\n'
                '        "RELEASE_BOOTSTRAP_ENABLED": "false",\n'
                '        "RELEASE_PUBLISH_ENVIRONMENT": "release-publish",\n'
                '        "RELEASE_ENVIRONMENT_PROTECTION_VERIFIED": "false",\n'
                '        "RELEASE_TAG_RULESET_PROTECTION_VERIFIED": "false",',
                '        "CURRENT_RELEASE_TAG": "v1.0.5",\n'
                '        "RELEASE_BOOTSTRAP_ENABLED": "true",\n'
                '        "RELEASE_PUBLISH_ENVIRONMENT": "release-publish",\n'
                '        "RELEASE_ENVIRONMENT_PROTECTION_VERIFIED": "true",\n'
                '        "RELEASE_TAG_RULESET_PROTECTION_VERIFIED": "true",',
            ),
            ('    assert workflow["env"]["RELEASE_ENVIRONMENT_PROTECTION_VERIFIED"] == "false"', '    assert workflow["env"]["RELEASE_ENVIRONMENT_PROTECTION_VERIFIED"] == "true"'),
            ('    assert workflow["env"]["RELEASE_TAG_RULESET_PROTECTION_VERIFIED"] == "false"', '    assert workflow["env"]["RELEASE_TAG_RULESET_PROTECTION_VERIFIED"] == "true"'),
            ('    assert workflow["env"]["RELEASE_BOOTSTRAP_ENABLED"] == "false"', '    assert workflow["env"]["RELEASE_BOOTSTRAP_ENABLED"] == "true"'),
        )
        for old, new in replacements:
            rendered = _replace_once(rendered, old, new, path)
        return rendered

    relative = path.decode("utf-8")
    replacements = WI010_S0_TO_S1_TEXT_REPLACEMENTS.get(relative)
    if replacements is None:
        raise ValueError(f"WI010 S0-to-S1 renderer lacks {path!r}")
    for old, new in replacements:
        rendered = _replace_once(rendered, old, new, path)
    if path == WI010_README_PATH:
        rendered = _replace_once(
            rendered,
            '<!-- WI010_RELEASE_PHASE: {"phase":"S0"} -->',
            '<!-- WI010_RELEASE_PHASE: {"phase":"S1"} -->',
            path,
        )
    return rendered


def _render_terminal_surface(
    path: bytes,
    parent_blob: bytes,
    phase: ReleasePhase,
    payload: Mapping[str, object],
) -> bytes:
    """从受信 S1 blob 生成唯一 terminal 公开面。"""

    relative = path.decode("utf-8")
    text = parent_blob.decode("utf-8")
    old_marker = WI010_RELEASE_STATE_MARKERS[ReleasePhase.S1.value]
    new_marker = WI010_RELEASE_STATE_MARKERS[phase.value]
    if text.count(old_marker) != 1:
        raise ValueError(f"WI010 S1 surface marker differs for {path!r}")
    text = text.replace(old_marker, new_marker, 1)

    if phase is ReleasePhase.S2_SUCCESS:
        archive = WI010_ARCHIVE_PREFIX + str(payload["archive_commit_sha"]) + ".zip"
        # 终态用户指南必须完整迁移六套离线命令、版本输出与排障路径。
        if relative == "USER_GUIDE.zh-CN.md":
            text = text.replace("1.0.2", "1.0.5")
        success_replacements = {
            "README.md": (
                (
                    "> 制品构建时点快照："
                    + new_marker
                    + WI010_S1_README_STATUS_BODY,
                    "> 当前发布权威："
                    + new_marker
                    + "。远端 Proof、CAS、protected tag 与 generation-0 Certificate 已共同闭合，"
                    "v1.0.5 是当前普通用户发布权威；canonical online archive 精确绑定 PR2 commit。",
                ),
                (
                    WI010_S1_README_GO,
                    "010 generation-0 已由受保护软件 tag、Release Satisfaction Proof 与 generation-0 "
                    "Certificate 固化；三个发布开关已恢复为字符串 `false`，不得重跑、重发、编辑或复用。",
                ),
                (
                    WI010_S1_README_INSTALL_CONTEXT,
                    "当前普通用户在线安装入口只使用本页唯一 `Canonical online install spec` 行；"
                    "移动分支、tag 与包索引名称均不是当前权威。",
                ),
                (
                    'python -m pip install "git+https://github.com/SinclairPan/Ai_AutoSDLC.git@v1.0.2"',
                    "复制本页唯一 `Canonical online install spec` 行中的 URL 作为 `pip install` 参数。",
                ),
                ("版本输出应为 `1.0.2`。", "版本输出应为 `1.0.5`。"),
                (
                    WI010_S1_README_SOURCE_CONTEXT,
                    "源码检视只使用上述 PR2 commit archive；移动分支与 tag 均不是 canonical 安装入口。",
                ),
                (
                    "git clone --branch v1.0.2 --depth 1 https://github.com/SinclairPan/Ai_AutoSDLC.git\n"
                    "Set-Location Ai_AutoSDLC\nuv sync\nuv run ai-sdlc --version",
                    "先下载并解压本页唯一 canonical PR2 commit archive，再在解压目录执行：\n"
                    "Set-Location Ai_AutoSDLC-*\nuv sync\nuv run ai-sdlc --version",
                ),
                (
                    WI010_S1_README_OFFLINE_CONTEXT,
                    "以下是 generation-0 已发布并由 Certificate 绑定的 `1.0.5` 离线制品名称：",
                ),
                (
                    WI010_S1_README_OFFLINE_AUTHORITY,
                    "当前公开可安装的离线版本是 `v1.0.5`，具体下载与校验命令见"
                    "[中文用户指南](USER_GUIDE.zh-CN.md)。",
                ),
            ),
            "USER_GUIDE.zh-CN.md": (
                (
                    "`WorkItem 010 three-PR release migration` 的 S1 只授权 exact protected-main "
                    "`release-build` writer 在唯一只读 load-probe 成功后执行一次 actual generation；"
                    "普通用户和手工路径仍禁止上传、替换、发布、下载、安装或 rerun v1.0.5。"
                    "`last published version is v1.0.5`，本指南仍只安装 `v1.0.5`。",
                    "远端 Proof、CAS、protected tag 与 generation-0 Certificate 已共同闭合；"
                    "v1.0.5 是当前普通用户发布权威，本指南只安装 `v1.0.5`。",
                ),
                (
                    "010 的 S1 将三个开关设为字符串 `true`：`release-publish` environment 以 required "
                    "reviewers 阻断未审 writer，且禁止自批与管理员 bypass；`active no-bypass tag ruleset "
                    "protects software and Certificate tags`，精确覆盖软件 tag 和 generation-0 Certificate "
                    "tag，并拒绝更新、删除及非快进变更。PR2 合并后必须从精确 protected-main writer "
                    "先执行唯一只读 load-probe，成功后才允许一次 actual generation；任何部分创建或保护失败"
                    "都属于 terminal generation burn，禁止清理、恢复或重跑。",
                    "010 generation-0 已闭合并永久禁止 rerun、redispatch、清理、编辑或复用；"
                    "三个发布开关已恢复为字符串 `false`。",
                ),
            ),
            "docs/product-contract.md": (
                ("## 1.0.5 源码候选真值（release-enabled / outcome-pending-closure）", "## 1.0.5 永久发布真值（published / immutable / Certificate-trusted）"),
                ("是该候选预期产物名；S1 仍处于 outcome-pending-closure，它们不是普通用户公开安装权威；", "是 generation-0 已发布且由 Certificate 绑定的普通用户离线制品；"),
                (
                    "：`WorkItem 010 three-PR release migration` 的 S1 将三个发布开关设为字符串 `true`，只授权 exact protected-main `release-build` writer 在 PR2 合并后的唯一只读 load-probe 成功后执行一次 actual generation；普通用户和手工路径仍禁止上传、替换、发布、下载、安装或 rerun v1.0.5；",
                    "：generation-0 的 Proof、CAS、protected tag 与 Certificate 已闭合；"
                    "普通用户安装权威已迁移到 v1.0.5，禁止 rerun、redispatch、编辑或复用；",
                ),
                ("`last published version is v1.0.2`", "`current published version is v1.0.5`"),
                ("S1 中三个验证/发布开关均为字符串 `true`；实际 generation 的部分 namespace、环境或 ruleset 失败均执行 terminal generation burn，不清理、不恢复、不重跑。", "generation-0 已闭合；三个发布开关均恢复为字符串 `false`，该 generation 不清理、不编辑、不恢复、不重跑。"),
            ),
            "docs/pull-request-checklist.zh.md": (
                ("源码候选版本为 `1.0.5`，`last published version is v1.0.2`；", "源码与当前发布版本均为 `1.0.5`；"),
                ("且 `WorkItem 010 three-PR release migration` 的 S1 只授权 exact protected-main `release-build` writer 在 PR2 合并后的唯一只读 load-probe 成功后执行一次 actual generation；", "且 WorkItem 010 generation-0 的 Proof、CAS、protected tag 与 Certificate 已闭合；"),
                ("普通用户和手工路径仍禁止上传、替换、发布、下载、安装或 rerun v1.0.5；", "普通用户安装权威已迁移到 v1.0.5；generation-0 禁止 rerun、redispatch、编辑或复用；"),
            ),
            "packaging/offline/README.md": (
                ("`WorkItem 010 three-PR release migration` 的 S1 将三个发布开关设为字符串 `true`，只授权 exact protected-main `release-build` writer 在 PR2 合并后的唯一只读 load-probe 成功后执行一次 actual generation；普通用户和手工路径仍禁止上传、替换、发布、下载、安装或 rerun v1.0.5。`last published version is v1.0.2`。", "generation-0 的 Proof、CAS、protected tag 与 Certificate 已闭合；v1.0.5 是当前普通用户离线发布权威，三个发布开关已恢复为字符串 `false`。"),
                ("公开稳定源码仍固定为已发布的 `v1.0.2`：", "canonical 源码固定为顶部唯一 PR2 commit archive；不要使用移动分支或 tag："),
                ("git clone --branch v1.0.2 --depth 1 https://github.com/SinclairPan/Ai_AutoSDLC.git\ncd Ai_AutoSDLC", "解压顶部 canonical PR2 commit archive 后进入其根目录：\ncd Ai_AutoSDLC-*"),
                ("工作流当前候选标识是 release-enabled / outcome-pending-closure 的 `v1.0.5`；PR2 合并后只允许精确 protected-main writer 先执行唯一只读 load-probe，成功后才允许一次 actual generation。", "工作流 generation-0 已闭合并永久禁止 rerun、redispatch、清理、编辑或复用。"),
            ),
            "packaging/offline/RELEASE_CHECKLIST.md": (
                ("`WorkItem 010 three-PR release migration` 的 S1 将三个发布开关设为字符串 `true`，只授权 exact protected-main `release-build` writer 在 PR2 合并后的唯一只读 load-probe 成功后执行一次 actual generation；普通用户和手工路径仍禁止上传、替换、发布、下载、安装或 rerun v1.0.5。`last published version is v1.0.2`。", "generation-0 的 Proof、CAS、protected tag 与 Certificate 已闭合；v1.0.5 是当前发布权威，三个发布开关已恢复为字符串 `false`。"),
                ("以下条目用于 S1 候选验证；除精确 protected-main writer 在唯一只读 load-probe 成功后的一次 actual generation 外，不授权其他 generation、发布或上传步骤。", "以下条目记录已闭合的 generation-0；不得 rerun、redispatch、清理、编辑或复用。"),
                ("且 S1 的三个发布开关均为字符串 `true`；", "且三个发布开关均已恢复为字符串 `false`；"),
            ),
        }
        for old, new in success_replacements[relative]:
            if text.count(old) != 1:
                raise ValueError(f"WI010 success surface anchor differs for {path!r}")
            text = text.replace(old, new, 1)
        canonical_line = WI010_CANONICAL_ONLINE_SPEC_PREFIX + archive
        state_line = next(
            (line for line in text.splitlines() if new_marker in line),
            None,
        )
        if state_line is None or text.splitlines().count(state_line) != 1:
            raise ValueError(f"WI010 success state line differs for {path!r}")
        text = text.replace(state_line + "\n", state_line + "\n" + canonical_line + "\n", 1)
    else:
        burn_replacements = {
            "README.md": (
                (
                    "> 制品构建时点快照："
                    + new_marker
                    + WI010_S1_README_STATUS_BODY,
                    "> 永久终止权威："
                    + new_marker
                    + "。generation-0 已永久烧毁；无论远端是否残留公开或未公开对象，"
                    "v1.0.5 均不具备当前发布权威，且不得清理、恢复、重跑、安装或复用。"
                    "`v1.0.2` 仍是普通用户当前唯一受认可的发布与安装权威；普通用户继续按"
                    "[中文用户指南](USER_GUIDE.zh-CN.md)安装该版本。",
                ),
                (
                    WI010_S1_README_GO,
                    "010 generation-0 已永久烧毁；三个发布开关已恢复为字符串 `false`，"
                    "不得重跑、重发、清理、编辑或复用。",
                ),
                (
                    WI010_S1_README_INSTALL_CONTEXT,
                    "v1.0.5 generation-0 已永久烧毁；以下 v1.0.2 命令仍是普通用户安装权威。",
                ),
                (
                    WI010_S1_README_SOURCE_CONTEXT,
                    "v1.0.5 已永久终止且不得作为安装入口；源码路径只用于维护者开发验证。",
                ),
                (
                    WI010_S1_README_OFFLINE_CONTEXT,
                    "以下 `1.0.5` 名称只记录已烧毁 generation-0 的历史预期；即使远端"
                    "可见，也不是当前权威且不得下载或安装：",
                ),
                (
                    WI010_S1_README_OFFLINE_AUTHORITY,
                    "`v1.0.2` 仍是普通用户当前唯一受认可的离线发布与安装权威，"
                    "具体下载与校验命令见"
                    "[中文用户指南](USER_GUIDE.zh-CN.md)。",
                ),
            ),
            "USER_GUIDE.zh-CN.md": (
                (
                    "`WorkItem 010 three-PR release migration` 的 S1 只授权 exact protected-main "
                    "`release-build` writer 在唯一只读 load-probe 成功后执行一次 actual generation；"
                    "普通用户和手工路径仍禁止上传、替换、发布、下载、安装或 rerun v1.0.5。",
                    "generation-0 已永久烧毁；无论远端是否残留公开或未公开对象，v1.0.5 "
                    "均不具备当前发布权威，且不得清理、恢复、重跑、安装或复用。",
                ),
                (
                    "`last published version is v1.0.2`，本指南仍只安装 `v1.0.2`。",
                    "`v1.0.2` 仍是普通用户当前唯一受认可的发布与安装权威；"
                    "本指南只安装该版本。",
                ),
            ),
            "docs/product-contract.md": (
                ("## 1.0.5 源码候选真值（release-enabled / outcome-pending-closure）", "## 1.0.5 永久终止真值（terminal-generation-burn / non-authoritative）"),
                ("是该候选预期产物名；S1 仍处于 outcome-pending-closure，它们不是普通用户公开安装权威；", "只是已烧毁 generation 的历史预期名称，不是发布或安装权威；"),
                (
                    "：`WorkItem 010 three-PR release migration` 的 S1 将三个发布开关设为字符串 `true`，只授权 exact protected-main `release-build` writer 在 PR2 合并后的唯一只读 load-probe 成功后执行一次 actual generation；普通用户和手工路径仍禁止上传、替换、发布、下载、安装或 rerun v1.0.5；",
                    "：generation-0 已永久烧毁；无论远端是否残留公开或未公开对象，"
                    "v1.0.5 均不具备当前发布权威，且不得清理、恢复、重跑、安装或复用；",
                ),
                (
                    "`last published version is v1.0.2`，其公开下载与校验入口见 "
                    "`USER_GUIDE.zh-CN.md`；",
                    "`v1.0.2` 仍是普通用户当前唯一受认可的发布与安装权威；"
                    "其受认可的下载与校验入口见 `USER_GUIDE.zh-CN.md`；",
                ),
                ("S1 中三个验证/发布开关均为字符串 `true`；", "三个验证/发布开关均已恢复为字符串 `false`；"),
            ),
            "docs/pull-request-checklist.zh.md": (
                (
                    "源码候选版本为 `1.0.5`，`last published version is v1.0.2`；",
                    "失败 generation 的源码版本为 `1.0.5`；`v1.0.2` "
                    "仍是普通用户当前唯一受认可的发布与安装权威；",
                ),
                ("且 `WorkItem 010 three-PR release migration` 的 S1 只授权 exact protected-main `release-build` writer 在 PR2 合并后的唯一只读 load-probe 成功后执行一次 actual generation；", "且 WorkItem 010 generation-0 已永久烧毁；"),
                ("普通用户和手工路径仍禁止上传、替换、发布、下载、安装或 rerun v1.0.5；", "不得清理、恢复、重跑、安装或复用 v1.0.5 generation-0；"),
            ),
            "packaging/offline/README.md": (
                ("`WorkItem 010 three-PR release migration` 的 S1 将三个发布开关设为字符串 `true`，只授权 exact protected-main `release-build` writer 在 PR2 合并后的唯一只读 load-probe 成功后执行一次 actual generation；普通用户和手工路径仍禁止上传、替换、发布、下载、安装或 rerun v1.0.5。", "generation-0 已永久烧毁；无论远端是否残留公开或未公开对象，v1.0.5 均不具备当前发布权威，且不得清理、恢复、重跑、安装或复用。"),
                (
                    "`last published version is v1.0.2`。",
                    "`v1.0.2` 仍是普通用户当前唯一受认可的发布与安装权威。",
                ),
                ("工作流当前候选标识是 release-enabled / outcome-pending-closure 的 `v1.0.5`；PR2 合并后只允许精确 protected-main writer 先执行唯一只读 load-probe，成功后才允许一次 actual generation。", "工作流 generation-0 已永久烧毁并禁止 rerun、redispatch、清理或复用。"),
            ),
            "packaging/offline/RELEASE_CHECKLIST.md": (
                ("`WorkItem 010 three-PR release migration` 的 S1 将三个发布开关设为字符串 `true`，只授权 exact protected-main `release-build` writer 在 PR2 合并后的唯一只读 load-probe 成功后执行一次 actual generation；普通用户和手工路径仍禁止上传、替换、发布、下载、安装或 rerun v1.0.5。", "generation-0 已永久烧毁；无论远端是否残留公开或未公开对象，v1.0.5 均不具备当前发布权威，且不得清理、恢复、重跑、安装或复用。"),
                (
                    "`last published version is v1.0.2`。",
                    "`v1.0.2` 仍是普通用户当前唯一受认可的发布与安装权威。",
                ),
                ("以下条目用于 S1 候选验证；除精确 protected-main writer 在唯一只读 load-probe 成功后的一次 actual generation 外，不授权其他 generation、发布或上传步骤。", "以下条目记录已烧毁的 generation-0；不得再执行 generation、发布或上传步骤。"),
                ("且 S1 的三个发布开关均为字符串 `true`；", "且三个发布开关均已恢复为字符串 `false`；"),
            ),
        }
        for old, new in burn_replacements[relative]:
            if text.count(old) != 1:
                raise ValueError(f"WI010 burn surface anchor differs for {path!r}")
            text = text.replace(old, new, 1)

    rendered = text.encode("utf-8")
    if path == WI010_README_PATH:
        rendered = _normalize_readme_seal(rendered)
        rendered = _replace_once(
            rendered,
            '<!-- WI010_RELEASE_PHASE: {"phase":"S1"} -->',
            _phase_marker(payload),
            path,
        )
    return rendered


def _render_terminal_core_blob(
    path: bytes,
    parent_blob: bytes,
    phase: ReleasePhase,
    payload: Mapping[str, object],
) -> bytes:
    if path == b".github/workflows/release-build.yml":
        rendered = parent_blob
        for flag in WI010_RELEASE_FLAGS:
            rendered = _replace_once(
                rendered,
                f'  {flag}: "true"',
                f'  {flag}: "false"',
                path,
            )
        return rendered
    if path == b"tests/integration/test_github_workflows.py":
        rendered = parent_blob
        replacements = (
            (
                '        "CURRENT_RELEASE_TAG": "v1.0.5",\n'
                '        "RELEASE_BOOTSTRAP_ENABLED": "true",\n'
                '        "RELEASE_PUBLISH_ENVIRONMENT": "release-publish",\n'
                '        "RELEASE_ENVIRONMENT_PROTECTION_VERIFIED": "true",\n'
                '        "RELEASE_TAG_RULESET_PROTECTION_VERIFIED": "true",',
                '        "CURRENT_RELEASE_TAG": "v1.0.5",\n'
                '        "RELEASE_BOOTSTRAP_ENABLED": "false",\n'
                '        "RELEASE_PUBLISH_ENVIRONMENT": "release-publish",\n'
                '        "RELEASE_ENVIRONMENT_PROTECTION_VERIFIED": "false",\n'
                '        "RELEASE_TAG_RULESET_PROTECTION_VERIFIED": "false",',
            ),
            ('    assert workflow["env"]["RELEASE_ENVIRONMENT_PROTECTION_VERIFIED"] == "true"', '    assert workflow["env"]["RELEASE_ENVIRONMENT_PROTECTION_VERIFIED"] == "false"'),
            ('    assert workflow["env"]["RELEASE_TAG_RULESET_PROTECTION_VERIFIED"] == "true"', '    assert workflow["env"]["RELEASE_TAG_RULESET_PROTECTION_VERIFIED"] == "false"'),
            ('    assert workflow["env"]["RELEASE_BOOTSTRAP_ENABLED"] == "true"', '    assert workflow["env"]["RELEASE_BOOTSTRAP_ENABLED"] == "false"'),
        )
        for old, new in replacements:
            rendered = _replace_once(rendered, old, new, path)
        if phase is ReleasePhase.S2_SUCCESS:
            archive = WI010_ARCHIVE_PREFIX + str(payload["archive_commit_sha"]) + ".zip"
            rendered = _replace_once(
                rendered,
                '    assert "remote-release-tag" in workflow',
                '    assert "protected-main" in workflow',
                path,
            )
            rendered = _replace_once(
                rendered,
                '    assert "default: v1.0.2" in workflow',
                '    assert "default: v1.0.5" in workflow\n'
                '    assert \'else { "v1.0.5" }\' in workflow\n'
                '    assert \'$env:PIP_NO_CACHE_DIR = "1"\' in workflow\n'
                '    assert "s2-success-qualification-identity.json" in workflow\n'
                '    assert "reviewed_pr3_candidate_tree" in workflow\n'
                '    assert "importlib.metadata.version(\'ai-sdlc\')" in workflow\n'
                '    for field in ("workflow_sha", "run_id", "run_attempt", "runner_os", "python", "pr2_sha", "reviewed_pr3_head_sha", "reviewed_pr3_candidate_tree"):\n'
                '        assert f"{field} =" in workflow\n'
                f'    assert \'pr2_sha = "{payload["archive_commit_sha"]}"\' in workflow',
                path,
            )
            rendered = _replace_once(
                rendered,
                '    assert \'default: "v1.0.2"\' in workflow',
                '    assert \'default: "v1.0.5"\' in workflow\n'
                '    assert \'PIP_NO_CACHE_DIR: "1"\' in workflow\n'
                '    assert "command -v ai-sdlc >/dev/null 2>&1" in workflow\n'
                '    assert "runner already exposes ai-sdlc before qualification" in workflow\n'
                '    assert "s2-success-qualification-identity.json" in workflow\n'
                '    assert "reviewed_pr3_candidate_tree" in workflow\n'
                '    for field in ("workflow_sha", "run_id", "run_attempt", "runner_os", "python", "pr2_sha", "reviewed_pr3_head_sha", "reviewed_pr3_candidate_tree"):\n'
                '        assert f\'"{field}"\' in workflow\n'
                '    assert \'"actual_package_spec"\' in workflow\n'
                '    assert \'"direct_url"\' in workflow\n'
                '    assert \'"metadata_version"\' in workflow\n'
                '    assert \'"cli_version"\' in workflow\n'
                '    assert \'"actual_package_spec": actual_package_spec\' in workflow\n'
                '    assert \'"direct_url": direct_url["url"]\' in workflow\n'
                '    assert \'"metadata_version": distribution.version\' in workflow\n'
                '    assert \'"cli_version": cli_version\' in workflow\n'
                f'    assert \'PR2_SHA: {payload["archive_commit_sha"]}\' in workflow',
                path,
            )
            rendered = _replace_once(
                rendered,
                '    assert "git+https://github.com/$sourceRepository.git@$remoteSha" in workflow\n'
                '    assert "pywinpty" in workflow',
                f'    assert \'$expectedArchive = "{archive}"\' in workflow\n'
                '    assert "-PackageSpec" not in workflow\n'
                '    assert "$directUrl.url -ne $expectedArchive" in workflow\n'
                '    assert "pywinpty" in workflow',
                path,
            )
            old_pin_test = (
                "def test_windows_clean_user_e2e_pins_release_tag_before_online_install() -> None:\n"
                "    workflow_path = _WORKFLOWS_DIR / \"windows-user-guide-e2e.yml\"\n\n"
                "    workflow = workflow_path.read_text(encoding=\"utf-8\").split(\n"
                "        \"clean-online-interactive-user-journey:\", 1\n"
                "    )[1]\n"
                "    resolve_release_tag = (\n"
                "        \"git ls-remote https://github.com/SinclairPan/Ai_AutoSDLC.git \"\n"
                "        '\"refs/tags/$env:RELEASE_TAG\" \"refs/tags/$env:RELEASE_TAG^{}\"'\n"
                "    )\n"
                "    pinned_installer = (\n"
                "        \"raw.githubusercontent.com/$sourceRepository/\"\n"
                "        \"$remoteSha/packaging/install_online.ps1\"\n"
                "    )\n"
                '    pinned_package = "git+https://github.com/$sourceRepository.git@$remoteSha"\n\n'
                "    assert resolve_release_tag in workflow\n"
                "    assert '$sourceKind = \"remote-release-tag\"' in workflow\n"
                "    assert pinned_installer in workflow\n"
                "    assert pinned_package in workflow\n"
                "    assert workflow.index(resolve_release_tag) < workflow.index(pinned_installer)\n"
                '    assert workflow.index(pinned_installer) < workflow.index("Invoke-WebRequest")\n'
                "    assert workflow.count(resolve_release_tag) == 1\n"
                '    assert "$directUrl.vcs_info.requested_revision -ne $remoteSha" in workflow\n'
            )
            new_pin_test = (
                "def test_windows_clean_user_e2e_resolves_protected_main_installer_and_canonical_archive() -> None:\n"
                "    workflow_path = _WORKFLOWS_DIR / \"windows-user-guide-e2e.yml\"\n\n"
                "    workflow = workflow_path.read_text(encoding=\"utf-8\").split(\n"
                "        \"clean-online-interactive-user-journey:\", 1\n"
                "    )[1]\n"
                "    resolve_protected_main = (\n"
                "        \"git ls-remote https://github.com/SinclairPan/Ai_AutoSDLC.git \"\n"
                "        '\"refs/heads/main\"'\n"
                "    )\n"
                "    pinned_installer = (\n"
                "        \"raw.githubusercontent.com/$sourceRepository/\"\n"
                "        \"$remoteSha/packaging/install_online.ps1\"\n"
                "    )\n"
                f'    canonical_package = \'$expectedArchive = "{archive}"\'\n\n'
                "    assert resolve_protected_main in workflow\n"
                "    assert '$sourceKind = \"protected-main\"' in workflow\n"
                "    assert pinned_installer in workflow\n"
                "    assert canonical_package in workflow\n"
                "    assert workflow.index(resolve_protected_main) < workflow.index(pinned_installer)\n"
                '    assert workflow.index(pinned_installer) < workflow.index("Invoke-WebRequest")\n'
                "    assert workflow.count(resolve_protected_main) == 1\n"
                '    assert "$directUrl.url -ne $expectedArchive" in workflow\n'
            )
            rendered = _replace_once(rendered, old_pin_test, new_pin_test, path)
            rendered = _replace_once(
                rendered,
                "def test_windows_clean_user_e2e_installs_pull_request_head_on_pr_runs() -> None:",
                "def test_windows_clean_user_e2e_uses_pull_request_head_installer_on_pr_runs() -> None:",
                path,
            )
            rendered = _replace_once(
                rendered,
                '    assert "git+https://github.com/$sourceRepository.git@$remoteSha" in workflow\n'
                '    assert "AI_SDLC_E2E_INSTALL_SOURCE=$sourceKind" in workflow',
                f'    assert \'$expectedArchive = "{archive}"\' in workflow\n'
                '    assert "AI_SDLC_E2E_INSTALL_SOURCE=$sourceKind" in workflow',
                path,
            )
        return rendered
    if path.decode("utf-8") in WI010_RELEASE_SURFACES:
        return _render_terminal_surface(path, parent_blob, phase, payload)
    if phase is ReleasePhase.S2_SUCCESS and path in {
        b"packaging/install_online.sh",
        b"packaging/install_online.ps1",
    }:
        archive = WI010_ARCHIVE_PREFIX + str(payload["archive_commit_sha"]) + ".zip"
        old = WI010_INSTALLER_BASELINE_DEFAULTS[path.decode("utf-8")]
        new = {
            b"packaging/install_online.sh": (
                f'PACKAGE_SPEC="${{AI_SDLC_PACKAGE_SPEC:-{archive}}}"'
            ),
            b"packaging/install_online.ps1": f'[string]$PackageSpec = "{archive}",',
        }[path]
        rendered = parent_blob
        if path == b"packaging/install_online.sh":
            rendered = _replace_once(
                rendered,
                "#   AI_SDLC_PACKAGE_SPEC=ai-sdlc==1.0.2   optional published package spec for pip install",
                f"#   AI_SDLC_PACKAGE_SPEC={archive}   可选的精确 PR2 commit archive 安装源",
                path,
            )
        return _replace_once(rendered, old, new, path)
    if phase is ReleasePhase.S2_SUCCESS:
        archive = WI010_ARCHIVE_PREFIX + str(payload["archive_commit_sha"]) + ".zip"
        if path == b".github/workflows/posix-user-guide-e2e.yml":
            rendered = _replace_once(
                parent_blob,
                '      - "packaging/offline/**"',
                '      - "packaging/offline/**"\n      - "packaging/install_online.sh"',
                path,
            )
            rendered = _replace_once(
                rendered,
                '        default: "v1.0.2"',
                '        default: "v1.0.5"',
                path,
            )
            anchor = (
                "      - name: Install uv\n"
                "        if: ${{ github.event_name == 'pull_request' }}\n"
                "        uses: astral-sh/setup-uv@v7\n"
                "        with:\n"
                "          enable-cache: true\n"
            )
            block = anchor + (
                "\n      - name: Verify canonical online installer from a fresh POSIX venv\n"
                "        if: ${{ github.event_name == 'pull_request' }}\n"
                "        shell: bash\n"
                "        env:\n"
                f"          EXPECTED_ARCHIVE: {archive}\n"
                "          PIP_NO_CACHE_DIR: \"1\"\n"
                f"          PR2_SHA: {payload['archive_commit_sha']}\n"
                "          REVIEWED_PR3_HEAD_SHA: ${{ github.event.pull_request.head.sha }}\n"
                "        run: |\n"
                "          set -euo pipefail\n"
                "          unset AI_SDLC_PACKAGE_SPEC\n"
                "          if command -v ai-sdlc >/dev/null 2>&1; then\n"
                '            echo "runner already exposes ai-sdlc before qualification" >&2\n'
                "            exit 1\n"
                "          fi\n"
                "          install_venv=\"${RUNNER_TEMP}/ai-sdlc-canonical-online-${ASSET_OS}\"\n"
                "          test ! -e \"${install_venv}\"\n"
                "          PYTHON=python3.11 bash packaging/install_online.sh \"${install_venv}\"\n"
                "          \"${install_venv}/bin/python\" - <<'PY'\n"
                "          import importlib.metadata\n"
                "          import json\n"
                "          import os\n"
                "          from pathlib import Path\n"
                "\n"
                "          distribution = importlib.metadata.distribution(\"ai-sdlc\")\n"
                "          direct_url = json.loads(\n"
                "              Path(distribution.locate_file(\"ai_sdlc-1.0.5.dist-info/direct_url.json\"))\n"
                "              .read_text(encoding=\"utf-8\")\n"
                "          )\n"
                "          assert distribution.version == \"1.0.5\"\n"
                "          assert direct_url[\"url\"] == os.environ[\"EXPECTED_ARCHIVE\"]\n"
                "          PY\n"
                "          \"${install_venv}/bin/ai-sdlc\" --version | grep -Fx \"1.0.5\"\n"
                "          evidence_root=\"${RUNNER_TEMP}/posix-user-guide-e2e-evidence\"\n"
                "          mkdir -p \"${evidence_root}\"\n"
                "          reviewed_tree=\"$(git rev-parse \"${GITHUB_SHA}^{tree}\")\"\n"
                "          REVIEWED_PR3_CANDIDATE_TREE=\"${reviewed_tree}\" \\\n"
                "            \"${install_venv}/bin/python\" - <<'PY'\n"
                "          import importlib.metadata\n"
                "          import json\n"
                "          import os\n"
                "          import platform\n"
                "          import subprocess\n"
                "          import sys\n"
                "          from pathlib import Path\n"
                "\n"
                "          distribution = importlib.metadata.distribution(\"ai-sdlc\")\n"
                "          direct_url = json.loads(\n"
                "              Path(distribution.locate_file(\"ai_sdlc-1.0.5.dist-info/direct_url.json\"))\n"
                "              .read_text(encoding=\"utf-8\")\n"
                "          )\n"
                "          actual_package_spec = direct_url[\"url\"]\n"
                "          cli_result = subprocess.run(\n"
                "              [str(Path(sys.executable).with_name(\"ai-sdlc\")), \"--version\"],\n"
                "              check=True,\n"
                "              capture_output=True,\n"
                "              text=True,\n"
                "          )\n"
                "          cli_version = cli_result.stdout.strip()\n"
                "          assert actual_package_spec == os.environ[\"EXPECTED_ARCHIVE\"]\n"
                "          assert distribution.version == \"1.0.5\"\n"
                "          assert cli_version == \"1.0.5\"\n"
                "\n"
                "          identity = {\n"
                "              \"actual_package_spec\": actual_package_spec,\n"
                "              \"cli_version\": cli_version,\n"
                "              \"direct_url\": direct_url[\"url\"],\n"
                "              \"metadata_version\": distribution.version,\n"
                "              \"workflow_sha\": os.environ[\"GITHUB_WORKFLOW_SHA\"],\n"
                "              \"run_id\": os.environ[\"GITHUB_RUN_ID\"],\n"
                "              \"run_attempt\": int(os.environ[\"GITHUB_RUN_ATTEMPT\"]),\n"
                "              \"runner_os\": os.environ[\"RUNNER_OS\"],\n"
                "              \"python\": platform.python_version(),\n"
                "              \"pr2_sha\": os.environ[\"PR2_SHA\"],\n"
                "              \"reviewed_pr3_head_sha\": os.environ[\"REVIEWED_PR3_HEAD_SHA\"],\n"
                "              \"reviewed_pr3_candidate_tree\": os.environ[\"REVIEWED_PR3_CANDIDATE_TREE\"],\n"
                "          }\n"
                "          output = (\n"
                "              Path(os.environ[\"RUNNER_TEMP\"])\n"
                "              / \"posix-user-guide-e2e-evidence\"\n"
                "              / \"s2-success-qualification-identity.json\"\n"
                "          )\n"
                "          output.write_text(\n"
                "              json.dumps(identity, indent=2, sort_keys=True) + \"\\n\",\n"
                "              encoding=\"utf-8\",\n"
                "          )\n"
                "          PY\n"
            )
            return _replace_once(rendered, anchor, block, path)
        if path == b".github/workflows/windows-user-guide-e2e.yml":
            rendered = _replace_once(
                parent_blob,
                "        default: v1.0.2",
                "        default: v1.0.5",
                path,
            )
            rendered = _replace_once(
                rendered,
                '$expectedTag = if ($env:GITHUB_EVENT_NAME -eq "pull_request") { "v1.0.5" } else { "v1.0.2" }',
                '$expectedTag = if ($env:GITHUB_EVENT_NAME -eq "pull_request") { "v1.0.5" } else { "v1.0.5" }',
                path,
            )
            rendered = _replace_once(
                rendered,
                "          if ($versionText.Trim() -ne $expectedVersion) {\n"
                "            throw \"Remote online install produced version '$($versionText.Trim())', expected $expectedVersion.\"\n"
                "          }",
                "          if ($versionText.Trim() -ne $expectedVersion) {\n"
                "            throw \"Remote online install produced version '$($versionText.Trim())', expected $expectedVersion.\"\n"
                "          }\n"
                "          $installedPython = Join-Path $installVenv \"Scripts\\python.exe\"\n"
                "          $metadataVersion = & $installedPython -c \"import importlib.metadata; print(importlib.metadata.version('ai-sdlc'))\"\n"
                "          $metadataVersion |\n"
                "            Out-File -FilePath (Join-Path $evidenceRoot \"distribution-metadata-version.txt\") -Encoding utf8\n"
                "          if ($LASTEXITCODE -ne 0 -or $metadataVersion.Trim() -ne \"1.0.5\") {\n"
                "            throw \"Installed distribution metadata version '$($metadataVersion.Trim())' is not 1.0.5.\"\n"
                "          }",
                path,
            )
            rendered = _replace_once(
                rendered,
                '          $sourceKind = "remote-release-tag"',
                '          $sourceKind = "protected-main"',
                path,
            )
            rendered = _replace_once(
                rendered,
                '            $tagLines = @(git ls-remote https://github.com/SinclairPan/Ai_AutoSDLC.git "refs/tags/$env:RELEASE_TAG" "refs/tags/$env:RELEASE_TAG^{}")\n'
                "            $peeledTag = $tagLines | Where-Object { $_ -match '\\^\\{\\}$' } | Select-Object -First 1\n"
                "            $resolvedTag = if ($peeledTag) { $peeledTag } else { $tagLines | Select-Object -First 1 }\n"
                '            $remoteSha = ($resolvedTag -split "`t")[0]\n'
                "            if ($LASTEXITCODE -ne 0 -or -not $remoteSha) {\n"
                '              throw "Unable to resolve the public release tag $env:RELEASE_TAG."\n'
                "            }",
                '            $mainLine = git ls-remote https://github.com/SinclairPan/Ai_AutoSDLC.git "refs/heads/main"\n'
                '            $remoteSha = ($mainLine -split "`t")[0]\n'
                "            if ($LASTEXITCODE -ne 0 -or -not $remoteSha) {\n"
                '              throw "Unable to resolve protected main."\n'
                "            }",
                path,
            )
            rendered = _replace_once(
                rendered,
                '          $packageSpec = "git+https://github.com/$sourceRepository.git@$remoteSha"',
                f'          $expectedArchive = "{archive}"',
                path,
            )
            rendered = _replace_once(
                rendered,
                "            -VenvPath $installVenv `\n"
                "            -PackageSpec $packageSpec `\n"
                "            -AddToPath *>&1 |",
                "            -VenvPath $installVenv `\n"
                "            -AddToPath *>&1 |",
                path,
            )
            rendered = _replace_once(
                rendered,
                "          if ($directUrl.vcs_info.requested_revision -ne $remoteSha) {\n"
                "            throw \"Installed package did not record the pinned public revision $remoteSha.\"\n"
                "          }\n"
                "          if ($directUrl.vcs_info.commit_id -ne $remoteSha) {\n"
                "            throw \"Installed package commit $($directUrl.vcs_info.commit_id) does not match source revision $remoteSha.\"\n"
                "          }",
                "          if ($directUrl.url -ne $expectedArchive) {\n"
                "            throw \"Installed package URL $($directUrl.url) does not match $expectedArchive.\"\n"
                "          }",
                path,
            )
            rendered = _replace_once(
                rendered,
                '          "source_kind=$sourceKind`nsource_repository=$sourceRepository`nsource_revision=$remoteSha" |\n'
                '            Out-File -FilePath (Join-Path $evidenceRoot "remote-source.txt") -Encoding utf8',
                "          $reviewedHead = if ($env:GITHUB_EVENT_NAME -eq \"pull_request\") { $env:PR_HEAD_SHA } else { $env:GITHUB_SHA }\n"
                '          $reviewedTree = (git rev-parse "$env:GITHUB_SHA^{tree}").Trim()\n'
                "          if ($LASTEXITCODE -ne 0 -or $reviewedTree -notmatch '^[0-9a-f]{40}$') {\n"
                "            throw \"Unable to resolve the reviewed PR3 candidate tree.\"\n"
                "          }\n"
                "          $pythonVersion = (& $installedPython -c \"import platform; print(platform.python_version())\").Trim()\n"
                "          if ($LASTEXITCODE -ne 0 -or -not $pythonVersion) {\n"
                "            throw \"Unable to record the installed Python version.\"\n"
                "          }\n"
                "          $qualificationIdentity = [ordered]@{\n"
                "            workflow_sha = $env:GITHUB_WORKFLOW_SHA\n"
                "            run_id = $env:GITHUB_RUN_ID\n"
                "            run_attempt = [int]$env:GITHUB_RUN_ATTEMPT\n"
                "            runner_os = $env:RUNNER_OS\n"
                "            python = $pythonVersion\n"
                f'            pr2_sha = "{payload["archive_commit_sha"]}"\n'
                "            reviewed_pr3_head_sha = $reviewedHead\n"
                "            reviewed_pr3_candidate_tree = $reviewedTree\n"
                "          }\n"
                "          $qualificationIdentity | ConvertTo-Json |\n"
                "            Out-File -FilePath (Join-Path $evidenceRoot \"s2-success-qualification-identity.json\") -Encoding utf8\n"
                '          "source_kind=$sourceKind`nsource_repository=$sourceRepository`nsource_revision=$remoteSha" |\n'
                '            Out-File -FilePath (Join-Path $evidenceRoot "remote-source.txt") -Encoding utf8',
                path,
            )
            rendered = _replace_once(
                rendered,
                "      - name: Install AI-SDLC through the public remote online path\n"
                "        shell: pwsh\n"
                "        run: |\n"
                '          $ErrorActionPreference = "Stop"\n',
                "      - name: Install AI-SDLC through the public remote online path\n"
                "        shell: pwsh\n"
                "        run: |\n"
                '          $ErrorActionPreference = "Stop"\n'
                '          $env:PIP_NO_CACHE_DIR = "1"\n',
                path,
            )
            return rendered
        if path == b"tests/integration/test_offline_bundle_scripts.py":
            rendered = parent_blob
            for asset in (
                "windows-amd64.zip",
                "macos-arm64.tar.gz",
                "linux-amd64.tar.gz",
            ):
                rendered = _replace_once(
                    rendered,
                    f'    assert "ai-sdlc-offline-1.0.2-{asset}" in guide',
                    f'    assert "ai-sdlc-offline-1.0.5-{asset}" in guide',
                    path,
                )
            anchor = (
                "    online_ps1 = (_PACKAGING_DIR / \"install_online.ps1\").read_text(encoding=\"utf-8\")\n\n"
                "    assert \"function Assert-LastExitCode\" in online_ps1\n"
            )
            block = (
                "    online_sh = (_PACKAGING_DIR / \"install_online.sh\").read_text(encoding=\"utf-8\")\n"
                + anchor
                + f"    assert {archive!r} in online_sh\n"
                + f"    assert {archive!r} in online_ps1\n"
            )
            return _replace_once(rendered, anchor, block, path)
        if path == b"tests/integration/test_user_guide_contract.py":
            rendered = parent_blob.replace(b"1.0.2", b"1.0.5")
            anchor = b"    text = guide_text()\n"
            if rendered.count(anchor) != 4:
                raise ValueError(f"WI010 guide test anchor differs for {path!r}")
            insertion = (
                anchor
                + f"    assert {archive!r} in text\n".encode()
            )
            return rendered.replace(anchor, insertion, 1)
        if path == b"tests/unit/test_release_identity.py":
            rendered = parent_blob
            rendered = rendered.replace(
                b"test_release_workflow_defaults_target_v1_0_2",
                b"test_release_workflow_defaults_target_v1_0_5",
                1,
            )
            rendered = rendered.replace(b'assert "v1.0.2" in text, name', b'assert "v1.0.5" in text, name', 1)
            old_function = (
                b"def test_stable_git_install_examples_pin_v1_0_2() -> None:\n"
                b"    text = (REPO_ROOT / \"README.md\").read_text(encoding=\"utf-8\")\n"
                b"    assert \"git+https://github.com/SinclairPan/Ai_AutoSDLC.git@v1.0.2\" in text\n"
                b"    assert \"git+https://github.com/SinclairPan/Ai_AutoSDLC.git@v1.0.5\" not in text\n"
                b"    assert \"@main\" in text\n"
                b"    assert \"\xe5\xbc\x80\xe5\x8f\x91\xe7\x89\x88\" in text\n"
            )
            new_function = (
                b"def test_stable_git_install_examples_pin_v1_0_5() -> None:\n"
                b"    text = (REPO_ROOT / \"README.md\").read_text(encoding=\"utf-8\")\n"
                + f"    assert {archive!r} in text\n".encode()
                + b"    assert \"@main\" not in text\n"
            )
            rendered = _replace_once(
                rendered,
                old_function.decode("utf-8"),
                new_function.decode("utf-8"),
                path,
            )
            old_source_function = (
                "def test_stable_source_checkout_examples_pin_v1_0_2() -> None:\n"
                "    stable_clone = (\n"
                '        "git clone --branch v1.0.2 --depth 1 "\n'
                '        "https://github.com/SinclairPan/Ai_AutoSDLC.git"\n'
                "    )\n"
                '    for name in ("README.md", "packaging/offline/README.md"):\n'
                '        text = (REPO_ROOT / name).read_text(encoding="utf-8")\n'
                "        assert stable_clone in text, name\n"
            )
            new_source_function = (
                "def test_stable_source_checkout_examples_pin_pr2_archive() -> None:\n"
                f"    canonical_archive = {archive!r}\n"
                '    for name in ("README.md", "packaging/offline/README.md"):\n'
                '        text = (REPO_ROOT / name).read_text(encoding="utf-8")\n'
                "        assert canonical_archive in text, name\n"
                '        assert "git clone --branch v1.0.5" not in text, name\n'
            )
            rendered = _replace_once(
                rendered,
                old_source_function,
                new_source_function,
                path,
            )
            rendered = rendered.replace(
                b"ai-sdlc-offline-1.0.2", b"ai-sdlc-offline-1.0.5"
            )
            rendered = rendered.replace(b"v1.0.2", b"v1.0.5")
            return rendered
    raise ValueError(f"WI010 terminal renderer lacks {path!r}")


def render_release_transition(
    parent_entries: Sequence[GitTreeEntry],
    parent_phase: ReleasePhase | None,
    phase: ReleasePhase,
    payload: Mapping[str, object],
) -> dict[bytes, bytes]:
    """从 immutable first-parent tree 生成完整 candidate blob 闭集。"""

    parent_map = _entry_map(parent_entries)
    edge = (parent_phase, phase)
    expected_paths_by_edge = {
        (ReleasePhase.S0, ReleasePhase.S1): WI010_PR2_PATHS,
        (ReleasePhase.S1, ReleasePhase.S2_BURN): WI010_S2_BURN_PATHS,
        (ReleasePhase.S1, ReleasePhase.S2_SUCCESS): WI010_S2_SUCCESS_PATHS,
    }
    paths = expected_paths_by_edge.get(edge)
    if paths is None:
        raise ValueError(f"WI010 renderer does not support {edge!r}")
    rendered: dict[bytes, bytes] = {}
    for path in paths:
        parent = parent_map.get(path)
        if parent is None or parent.object_type != b"blob":
            raise ValueError(f"WI010 renderer parent lacks {path!r}")
        if edge == (ReleasePhase.S0, ReleasePhase.S1):
            rendered[path] = _render_s0_to_s1_blob(path, parent.blob)
        else:
            rendered[path] = _render_terminal_core_blob(
                path, parent.blob, phase, payload
            )
    return rendered


def _terminal_parent_binding_error(
    phase: ReleasePhase,
    payload: Mapping[str, object],
    parent_commit_oid: bytes,
    parent_tree_oid: bytes,
) -> str | None:
    """把 terminal evidence 绑定到真实 PR2 first-parent Git objects。"""

    parent_commit = parent_commit_oid.decode("ascii")
    parent_tree = parent_tree_oid.decode("ascii")
    if phase is ReleasePhase.S2_SUCCESS:
        commit_keys = (
            "archive_commit_sha",
            "certificate_commit_sha",
            "proof_commit_sha",
            "tag_peel_sha",
            "target_commitish_resolved_sha",
        )
        if any(payload.get(key) != parent_commit for key in commit_keys):
            return "WI010 success evidence differs from PR2 parent commit"
        if payload.get("certificate_tree_sha") != parent_tree:
            return "WI010 success evidence differs from PR2 parent tree"
    elif phase is ReleasePhase.S2_BURN:
        if payload.get("candidate_commit_sha") != parent_commit:
            return "WI010 burn evidence differs from PR2 parent commit"
        if payload.get("candidate_tree_sha") != parent_tree:
            return "WI010 burn evidence differs from PR2 parent tree"
    return None


def _parse_commit_parent_oids(commit_object: bytes) -> tuple[bytes, ...]:
    """从原始 commit header 读取父对象，不受 shallow boundary 隐藏影响。"""

    headers = commit_object.split(b"\n\n", 1)[0].splitlines()
    parents = tuple(
        _parse_object_id(line.removeprefix(b"parent "), "parent")
        for line in headers
        if line.startswith(b"parent ")
    )
    if len(parents) not in {1, 2}:
        raise ValueError("WI010 transition requires one or two commit parents")
    return parents


def _git_commit_object_exists(root: Path, object_id: bytes) -> bool:
    completed = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "cat-file",
            "-e",
            object_id.decode("ascii") + "^{commit}",
        ],
        cwd=root,
        env=_git_environment(),
        check=False,
        capture_output=True,
    )
    return completed.returncode == 0


def _transition_parent_oids(root: Path, commit_oid: bytes) -> tuple[bytes, ...]:
    """读取 exact parent；浅克隆仅深化该 commit 一层并保持工作树不变。"""

    parents = _parse_commit_parent_oids(
        _git_bytes(root, "cat-file", "commit", commit_oid.decode("ascii"))
    )
    missing = tuple(
        parent for parent in parents if not _git_commit_object_exists(root, parent)
    )
    if not missing:
        return parents
    shallow = _git_bytes(root, "rev-parse", "--is-shallow-repository").strip()
    if shallow != b"true":
        raise ValueError("WI010 transition parent object is missing")
    try:
        subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "fetch",
                "--no-tags",
                "--no-recurse-submodules",
                "--no-write-fetch-head",
                "--deepen=1",
                "origin",
                commit_oid.decode("ascii"),
            ],
            cwd=root,
            env=_git_environment(),
            check=True,
            capture_output=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("WI010 transition parent fetch timed out") from exc
    if any(not _git_commit_object_exists(root, parent) for parent in parents):
        raise ValueError("WI010 transition parent remains missing after deepen")
    return parents


def _protected_main_oid(root: Path) -> bytes:
    """从候选仓库之外读取当前 protected main，作为 review 后的动态锚。"""

    origin_values = _git_bytes(
        root,
        "config",
        "--local",
        "--null",
        "--get-all",
        "remote.origin.url",
    )
    if origin_values.count(b"\0") != 1 or not origin_values.endswith(b"\0"):
        raise ValueError("WI010 origin URL is missing or ambiguous")
    origin_url = origin_values[:-1]
    if origin_url not in WI010_PROTECTED_ORIGIN_URLS:
        raise ValueError("WI010 origin does not identify the protected repository")
    try:
        with tempfile.TemporaryDirectory(prefix="wi010-protected-main-") as temp:
            completed = subprocess.run(
                [
                    "git",
                    "--no-replace-objects",
                    "-c",
                    "core.askPass=",
                    "-c",
                    "credential.helper=",
                    "-c",
                    "http.sslVerify=true",
                    "ls-remote",
                    "--exit-code",
                    WI010_PROTECTED_MAIN_URL.decode("ascii"),
                    WI010_PROTECTED_MAIN_REF.decode("ascii"),
                ],
                cwd=temp,
                env=_protected_main_environment(Path(temp)),
                check=True,
                capture_output=True,
                timeout=60,
            )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("WI010 protected-main lookup timed out") from exc
    expected_suffix = b"\t" + WI010_PROTECTED_MAIN_REF + b"\n"
    if completed.stdout.count(b"\n") != 1 or not completed.stdout.endswith(
        expected_suffix
    ):
        raise ValueError("WI010 protected-main lookup is ambiguous")
    return _parse_object_id(
        completed.stdout[: -len(expected_suffix)],
        "protected-main",
    )


def validate_release_transition(
    root: Path,
    head: str = "HEAD",
    *,
    snapshot: ReleaseTreeSnapshot | None = None,
) -> list[Finding]:
    """锁定从 bootstrap anchor 到终态的有界 first-parent 状态链。"""

    try:
        resolved = snapshot or read_release_tree_snapshot(root, head)
        depth_by_phase = {
            ReleasePhase.S0: 1,
            ReleasePhase.S1: 2,
            ReleasePhase.S2_BURN: 3,
            ReleasePhase.S2_SUCCESS: 3,
        }
        top_level_parents = _transition_parent_oids(root, resolved.commit_oid)
        protected_main = _protected_main_oid(root)
        if (
            resolved.commit_oid != protected_main
            and top_level_parents[0] != protected_main
        ):
            raise ValueError(
                "WI010 transition is not based on the current protected main"
            )
        _validate_release_transition_chain(
            root,
            resolved,
            remaining_depth=depth_by_phase[resolved.phase],
            seen=set(),
        )
        return []
    except (OSError, subprocess.CalledProcessError, KeyError, ValueError) as exc:
        return [_finding("wi010-release-transition-invalid", str(exc))]


def _validate_release_transition_chain(
    root: Path,
    resolved: ReleaseTreeSnapshot,
    *,
    remaining_depth: int,
    seen: set[bytes],
) -> None:
    """验证一个 logical edge，并沿受信 first parent 回溯到固定 anchor。"""

    if remaining_depth <= 0 or resolved.commit_oid in seen:
        raise ValueError("WI010 transition ancestry exceeds the phase bound")
    seen.add(resolved.commit_oid)
    seal_result = validate_release_tree_seal(root, snapshot=resolved)
    if seal_result.findings:
        raise ValueError("WI010 transition snapshot seal is invalid")

    parents = _transition_parent_oids(root, resolved.commit_oid)
    parent_oid = parents[0]
    if len(parents) == 2:
        candidate_oid = parents[1]
        candidate_parents = _transition_parent_oids(root, candidate_oid)
        if candidate_parents != (parent_oid,):
            raise ValueError(
                "WI010 synthetic merge candidate must be one commit from its base"
            )
        _candidate_commit, candidate_tree, _candidate_entries = _read_git_tree(
            root, candidate_oid.decode("ascii")
        )
        if candidate_tree != resolved.tree_oid:
            raise ValueError(
                "WI010 synthetic merge tree differs from its candidate head tree"
            )

    _parent_commit, parent_tree, parent_entries = _read_git_tree(
        root, parent_oid.decode("ascii")
    )
    parent_phase = _optional_phase(parent_entries)
    edge = (parent_phase, resolved.phase)
    expected_paths_by_edge = {
        (None, ReleasePhase.S0): WI010_FOUNDATION_PATHS,
        (ReleasePhase.S0, ReleasePhase.S1): WI010_PR2_PATHS,
        (ReleasePhase.S1, ReleasePhase.S2_BURN): WI010_S2_BURN_PATHS,
        (ReleasePhase.S1, ReleasePhase.S2_SUCCESS): WI010_S2_SUCCESS_PATHS,
    }
    expected_paths = expected_paths_by_edge.get(edge)
    if expected_paths is None:
        raise ValueError(f"unsupported WI010 transition: {edge!r}")
    if parent_phase is None:
        if remaining_depth != 1 or parent_oid != WI010_FOUNDATION_ANCHOR_OID:
            raise ValueError("WI010 foundation parent differs from the fixed anchor")
    elif remaining_depth == 1:
        raise ValueError("WI010 transition ancestry did not reach the fixed anchor")

    binding_error = _terminal_parent_binding_error(
        resolved.phase,
        resolved.phase_payload,
        parent_oid,
        parent_tree,
    )
    if binding_error is not None:
        raise ValueError(binding_error)
    parent_map = _entry_map(parent_entries)
    current_map = _entry_map(resolved.entries)
    all_paths = set(parent_map) | set(current_map)
    changed_paths = {
        path
        for path in all_paths
        if (
            path not in parent_map
            or path not in current_map
            or (
                parent_map[path].mode,
                parent_map[path].object_type,
                parent_map[path].object_id,
            )
            != (
                current_map[path].mode,
                current_map[path].object_type,
                current_map[path].object_id,
            )
        )
    }
    if changed_paths != set(expected_paths):
        raise ValueError(
            "WI010 transition path set differs: "
            f"expected={sorted(expected_paths)!r} actual={sorted(changed_paths)!r}"
        )
    expected_modes = {path: b"100644" for path in expected_paths}
    if edge == (None, ReleasePhase.S0):
        expected_modes[b"scripts/validate_public_release_identity.py"] = b"100755"
    if edge == (ReleasePhase.S1, ReleasePhase.S2_SUCCESS):
        expected_modes[b"packaging/install_online.sh"] = b"100755"
    for path, expected_mode in expected_modes.items():
        entry = current_map.get(path)
        if entry is None or entry.mode != expected_mode or entry.object_type != b"blob":
            raise ValueError(f"WI010 transition mode differs for {path!r}")
    if edge == (None, ReleasePhase.S0):
        _validate_foundation_trust_roots(current_map)
        expected_readme = _render_foundation_readme(parent_map[WI010_README_PATH].blob)
        actual_readme = _normalize_readme_seal(
            current_map[WI010_README_PATH].blob
        )
        if actual_readme != expected_readme:
            raise ValueError(
                "WI010 foundation README differs from the marker-only renderer"
            )
    if parent_phase is not None:
        for path in (
            b"scripts/validate_public_release_identity.py",
            b"src/ai_sdlc/core/verify_constraints.py",
            b"tests/unit/test_public_release_identity.py",
        ):
            if parent_map[path].object_id != current_map[path].object_id:
                raise ValueError(f"WI010 trust-root blob changed: {path!r}")
        expected_blobs = render_release_transition(
            parent_entries,
            parent_phase,
            resolved.phase,
            resolved.phase_payload,
        )
        for path, expected_blob in expected_blobs.items():
            actual_blob = current_map[path].blob
            if path == WI010_README_PATH:
                actual_blob = _normalize_readme_seal(actual_blob)
            if actual_blob != expected_blob:
                raise ValueError(
                    f"WI010 transition blob differs from closed renderer: {path!r}"
                )
        if resolved.phase is ReleasePhase.S2_SUCCESS:
            verifier = current_map.get(WI010_ATTESTATION_VERIFIER_PATH)
            if (
                verifier is None
                or verifier.mode != b"100644"
                or verifier.object_type != b"blob"
                or verifier.object_id != WI010_ATTESTATION_VERIFIER_BLOB_OID
            ):
                raise ValueError("WI010 Certificate attestation verifier blob differs")
            _validate_s2_success_remote_authority(
                resolved.phase_payload,
                parent_oid,
                parent_tree,
                verifier.blob,
            )
        elif resolved.phase is ReleasePhase.S2_BURN:
            _validate_s2_burn_remote_authority(
                resolved.phase_payload,
                parent_oid,
            )

        parent_snapshot = read_release_tree_snapshot(
            root, parent_oid.decode("ascii")
        )
        _validate_release_transition_chain(
            root,
            parent_snapshot,
            remaining_depth=remaining_depth - 1,
            seen=seen,
        )


def tracked_paths(root: Path) -> tuple[str, ...]:
    """Return Git-tracked paths relative to ``root``."""

    _assert_git_root(root)
    completed = subprocess.run(
        ["git", "--no-replace-objects", "ls-files", "-z"],
        cwd=root,
        env=_git_environment(),
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

    phase = ReleasePhase.S0
    readme = files.get("README.md", "")
    if WI010_PHASE_MARKER_PREFIX in readme:
        with suppress(ValueError):
            phase, _payload = _extract_phase_payload(readme)
    findings: list[Finding] = []
    for path, required_markers in REQUIRED_SURFACES.items():
        text = files.get(path)
        if text is None:
            findings.append(
                Finding(path, None, "required-public-surface-missing", path)
            )
            continue
        phase_markers: list[str] = []
        for marker in required_markers:
            if marker == WI010_RELEASE_STATE_MARKERS["S0"]:
                phase_markers.append(WI010_RELEASE_STATE_MARKERS[phase.value])
            elif (
                (
                    phase in WI010_TERMINAL_REQUIRED_SURFACE_MARKERS
                    and marker in WI010_TERMINAL_DROPPED_REQUIRED_MARKERS
                )
                or (
                    phase is ReleasePhase.S2_SUCCESS
                    and path in WI010_RELEASE_SURFACES
                    and "1.0.2" in marker
                )
                or (
                    phase is ReleasePhase.S2_BURN
                    and path in WI010_RELEASE_SURFACES
                    and "last published version is v1.0.2" in marker
                )
            ) or (
                marker == "不得上传、发布或下载 v1.0.5 候选"
                and phase is not ReleasePhase.S0
            ) or (
                path
                in {"packaging/install_online.sh", "packaging/install_online.ps1"}
                and phase is ReleasePhase.S2_SUCCESS
            ):
                continue
            else:
                phase_markers.append(marker)
        terminal_marker = WI010_TERMINAL_REQUIRED_SURFACE_MARKERS.get(
            phase, {}
        ).get(path)
        if terminal_marker is not None:
            phase_markers.append(terminal_marker)
        for marker in phase_markers:
            if marker not in text:
                findings.append(
                    Finding(path, None, "required-identity-marker-missing", marker)
                )
        for marker in FORBIDDEN_SURFACE_MARKERS.get(path, ()):
            if phase is ReleasePhase.S2_SUCCESS and marker in {
                "v1.0.5 已发布",
                "releases/download/v1.0.5/",
            }:
                continue
            if marker in text:
                findings.append(
                    Finding(path, None, "obsolete-release-authorization", marker)
                )
    return findings


def validate_release_worktree_seal(
    root: Path,
    *,
    snapshot: ReleaseTreeSnapshot | None = None,
) -> list[Finding]:
    """要求自托管工作树与当前已 seal 的 HEAD 完全一致。"""

    try:
        _assert_git_root(root)
        resolved = snapshot or read_release_tree_snapshot(root)
        index_records = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "--no-optional-locks",
                "ls-files",
                "-v",
                "-z",
            ],
            cwd=root,
            env=_git_environment(),
            check=True,
            capture_output=True,
        ).stdout.split(b"\0")
        special_record = next(
            (
                record
                for record in index_records
                if record and not record.startswith(b"H ")
            ),
            None,
        )
        if special_record is not None:
            excerpt = special_record.decode("utf-8", errors="backslashreplace")
            return [
                _finding(
                    "wi010-release-worktree-unsealed",
                    f"index contains a hidden or non-ordinary entry: {excerpt}",
                    path=".",
                )
            ]
        tracked_index_paths = tuple(
            record[2:] for record in index_records if record
        )
        _assert_no_content_transform_attributes(root, tracked_index_paths)
        indexed = _parse_index_entries(
            subprocess.run(
                [
                    "git",
                    "--no-replace-objects",
                    "--no-optional-locks",
                    "ls-files",
                    "-s",
                    "-z",
                ],
                cwd=root,
                env=_git_environment(),
                check=True,
                capture_output=True,
            ).stdout
        )
        expected_index = tuple(
            (entry.path, entry.mode, entry.object_id) for entry in resolved.entries
        )
        if indexed != expected_index:
            raise ValueError("Git index path/mode/blob set differs from sealed HEAD")
        for entry in resolved.entries:
            actual_blob = _read_worktree_blob(root, entry)
            if not _worktree_blob_matches(root, entry, actual_blob):
                relative = entry.path.decode("utf-8", errors="backslashreplace")
                raise ValueError(f"worktree bytes differ from sealed HEAD: {relative}")
        untracked = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "--no-optional-locks",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            cwd=root,
            env=_git_environment(),
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        return [
            _finding(
                "wi010-release-worktree-unsealed",
                f"cannot verify worktree against sealed HEAD: {exc}",
                path=".",
            )
        ]
    if not untracked:
        return []
    first_record = untracked.split(b"\0", 1)[0].decode(
        "utf-8", errors="backslashreplace"
    )
    return [
        _finding(
            "wi010-release-worktree-unsealed",
            f"worktree contains an untracked path: {first_record}",
            path=".",
        )
    ]


def scan_public_tree(root: Path) -> list[Finding]:
    """从同一 HEAD Git snapshot 扫描全部可解码公开文件。"""

    try:
        snapshot = read_release_tree_snapshot(root)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        finding = _finding("wi010-release-tree-seal-invalid", str(exc))
        return [finding, *validate_release_worktree_seal(root)]
    worktree_findings = validate_release_worktree_seal(root, snapshot=snapshot)
    files: dict[str, str] = {}
    for entry in snapshot.entries:
        try:
            relative = entry.path.decode("utf-8")
            files[relative] = entry.blob.decode("utf-8")
        except UnicodeDecodeError:
            continue
    seal_result = validate_release_tree_seal(root, snapshot=snapshot)
    return [
        *scan_paths(root, files),
        *validate_required_surfaces(files),
        *validate_wi010_release_profile(files),
        *seal_result.findings,
        *validate_release_transition(root, snapshot=snapshot),
        *worktree_findings,
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
