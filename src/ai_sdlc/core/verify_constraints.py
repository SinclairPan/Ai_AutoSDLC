"""Read-only governance and checkpoint checks."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from ai_sdlc.branch.git_client import GitError
from ai_sdlc.context.state import load_checkpoint
from ai_sdlc.core.artifact_target_guard import detect_misplaced_formal_artifacts
from ai_sdlc.core.backlog_breach_guard import collect_missing_backlog_entry_references
from ai_sdlc.core.frontend_visual_a11y_evidence_provider import (
    FRONTEND_VISUAL_A11Y_EVIDENCE_ARTIFACT_NAME,
)
from ai_sdlc.core.text_quality import collect_text_quality_blockers
from ai_sdlc.core.workitem_traceability import evaluate_work_item_branch_lifecycle
from ai_sdlc.gates.task_ac_checks import (
    first_doc_first_task_scope_violation,
    first_task_missing_acceptance,
)
from ai_sdlc.models.state import Checkpoint

if TYPE_CHECKING:
    pass

CONSTITUTION_REL = Path(".ai-sdlc") / "memory" / "constitution.md"
PIPELINE_RULE_REL = Path("src") / "ai_sdlc" / "rules" / "pipeline.md"
CODE_REVIEW_RULE_REL = Path("src") / "ai_sdlc" / "rules" / "code-review.md"
SKIP_REGISTRY_REL = Path("src") / "ai_sdlc" / "rules" / "agent-skip-registry.zh.md"
FRAMEWORK_DEFECT_BACKLOG_REL = Path("docs") / "framework-defect-backlog.zh-CN.md"
VERIFICATION_RULE_REL = Path("src") / "ai_sdlc" / "rules" / "verification.md"
PR_CHECKLIST_REL = Path("docs") / "pull-request-checklist.zh.md"
RELEASE_POLICY_REL = Path("docs") / "框架自迭代开发与发布约定.md"
README_REL = Path("README.md")
USER_GUIDE_REL = Path("USER_GUIDE.zh-CN.md")
AGENTS_REL = Path("AGENTS.md")
OFFLINE_README_REL = Path("packaging") / "offline" / "README.md"
ONLINE_INSTALL_PS_REL = Path("packaging") / "install_online.ps1"
ONLINE_INSTALL_SH_REL = Path("packaging") / "install_online.sh"
V2_MIGRATION_REL = Path("docs") / "v2-migration.zh-CN.md"
PYPROJECT_REL = Path("pyproject.toml")
PACKAGE_INIT_REL = Path("src") / "ai_sdlc" / "__init__.py"
RELEASE_BUILD_WORKFLOW_REL = Path(".github") / "workflows" / "release-build.yml"
RELEASE_ARTIFACT_SMOKE_WORKFLOW_REL = (
    Path(".github") / "workflows" / "release-artifact-smoke.yml"
)
WINDOWS_OFFLINE_SMOKE_WORKFLOW_REL = (
    Path(".github") / "workflows" / "windows-offline-smoke.yml"
)
CLI_COMMANDS_REL = Path("src") / "ai_sdlc" / "cli" / "commands.py"
CLI_RUN_CMD_REL = Path("src") / "ai_sdlc" / "cli" / "run_cmd.py"
FRONTEND_CONTRACT_OBSERVATION_INPUT_FILE = "frontend-contract-observations.json"
FRONTEND_VISUAL_A11Y_EVIDENCE_INPUT_FILE = FRONTEND_VISUAL_A11Y_EVIDENCE_ARTIFACT_NAME
DOC_FIRST_SURFACES: dict[Path, tuple[str, ...]] = {
    PIPELINE_RULE_REL: (
        "先文档 / 先需求 / 先 spec-plan-tasks",
        "design/decompose",
        "不得直接改产品代码",
    ),
    SKIP_REGISTRY_REL: (
        "仅文档 / 仅需求沉淀",
        "spec.md",
        "plan.md",
        "tasks.md",
        "`src/`、`tests/`",
    ),
}
DOC_FIRST_ACTIVATION_TOKENS = (
    "先文档",
    "先需求",
    "spec-plan-tasks",
    "仅文档",
    "仅需求",
)
VERIFICATION_PROFILE_SURFACES: dict[Path, tuple[str, ...]] = {
    VERIFICATION_RULE_REL: (
        "docs-only",
        "rules-only",
        "truth-only",
        "code-change",
        "uv run ai-sdlc verify constraints",
        "python -m ai_sdlc program truth sync --dry-run",
        "uv run pytest",
        "uv run ruff check",
    ),
    PR_CHECKLIST_REL: (
        "uv run ai-sdlc verify constraints",
        "uv run pytest -q",
        "uv run ruff check",
        "python scripts/validate_public_release_identity.py .",
    ),
}
VERIFICATION_PROFILE_ACTIVATION_SURFACES = (VERIFICATION_RULE_REL,)
FEATURE_REGRESSION_GUARD_SURFACES: dict[Path, tuple[str, ...]] = {
    CODE_REVIEW_RULE_REL: (
        "既有能力不退化",
        "既有用户可见能力",
        "未声明废弃",
        "回归测试",
        "只覆盖新能力 happy path",
    ),
    VERIFICATION_RULE_REL: (
        "既有能力未退化",
        "既有入口 / 既有选项 / 既有输出",
        "只验证新功能 happy path",
    ),
    PR_CHECKLIST_REL: (
        "用户可见行为与文档一致",
        "回归测试",
        "高影响动作具备确认、回滚或恢复路径",
    ),
}
FEATURE_REGRESSION_GUARD_ACTIVATION_SURFACES = (
    CODE_REVIEW_RULE_REL,
    VERIFICATION_RULE_REL,
)
RECONCILE_SMOKE_CONTRACT_SURFACES: dict[Path, tuple[str, ...]] = {
    VERIFICATION_RULE_REL: (
        "Reconcile Smoke Contract",
        "Existing Artifact Probe",
        "ai-sdlc recover --reconcile",
        "windows-offline-smoke.yml",
    ),
    CLI_COMMANDS_REL: (
        "Existing Artifact Probe",
        "ai-sdlc recover --reconcile",
    ),
    WINDOWS_OFFLINE_SMOKE_WORKFLOW_REL: (
        "Existing Artifact Probe",
        "recover --reconcile",
        (
            "reported repo-state reconciliation diagnostics; "
            "treating this as smoke pass."
        ),
    ),
}
RELEASE_DOCS_CONSISTENCY_SURFACES: dict[Path, tuple[str, ...]] = {
    README_REL: (
        "# AI-SDLC 2.0.0",
        "https://github.com/SinclairPan/Ai_AutoSDLC",
        "git+https://github.com/SinclairPan/Ai_AutoSDLC.git@v2.0.0",
        "git clone --branch v2.0.0 --depth 1 https://github.com/SinclairPan/Ai_AutoSDLC.git",
        "ai-sdlc-offline-2.0.0-windows-amd64.zip",
        "ai-sdlc-offline-2.0.0-macos-arm64.tar.gz",
        "ai-sdlc-offline-2.0.0-linux-amd64.tar.gz",
        "ai-sdlc init . --agent-target codex --shell powershell",
        "uv run python scripts/validate_public_release_identity.py .",
    ),
    USER_GUIDE_REL: (
        "# AI-SDLC 2.0.0 中文用户指南",
        "https://github.com/SinclairPan/Ai_AutoSDLC",
        "## 第一章：全新用户 + 全新空项目",
        "## 第二章：全新用户 + 已有项目",
        "Windows",
        "macOS",
        "Linux",
        "ai-sdlc-offline-2.0.0-windows-amd64.zip",
        "ai-sdlc-offline-2.0.0-macos-arm64.tar.gz",
        "ai-sdlc-offline-2.0.0-linux-amd64.tar.gz",
        "-AddToPath",
        "--add-to-path",
        "ai-sdlc init .",
        "ai-sdlc adopt .",
        "当前结果 / Result",
        "下一步 / Next",
    ),
    OFFLINE_README_REL: (
        "# AI-SDLC 2.0.0 离线打包说明",
        "https://github.com/SinclairPan/Ai_AutoSDLC",
        "git clone --branch v2.0.0 --depth 1 https://github.com/SinclairPan/Ai_AutoSDLC.git",
        "ai-sdlc-offline-2.0.0-windows-amd64.zip",
        "ai-sdlc-offline-2.0.0-macos-arm64.tar.gz",
        "ai-sdlc-offline-2.0.0-linux-amd64.tar.gz",
        "SHA256SUMS",
        ".sha256",
        "-AddToPath",
        "--add-to-path",
        "verify_offline_bundle.py",
    ),
    ONLINE_INSTALL_PS_REL: (
        "git+https://github.com/SinclairPan/Ai_AutoSDLC.git@v2.0.0",
    ),
    ONLINE_INSTALL_SH_REL: (
        "git+https://github.com/SinclairPan/Ai_AutoSDLC.git@v2.0.0",
    ),
    V2_MIGRATION_REL: (
        "v1.0.2",
        "v2.0.0",
        "Local PR Review",
        "代码精简分析只给出建议",
        "最多两名只读专家",
    ),
    RELEASE_POLICY_REL: (
        "README.md",
        "USER_GUIDE.zh-CN.md",
        "packaging/offline/README.md",
        "docs/pull-request-checklist.zh.md",
        "https://github.com/SinclairPan/Ai_AutoSDLC",
        "ai-sdlc-offline-2.0.0-windows-amd64.zip",
        "ai-sdlc-offline-2.0.0-macos-arm64.tar.gz",
        "ai-sdlc-offline-2.0.0-linux-amd64.tar.gz",
        "SHA256SUMS",
        ".sha256",
    ),
    PR_CHECKLIST_REL: (
        "README.md",
        "USER_GUIDE.zh-CN.md",
        "packaging/offline/README.md",
        "2.0.0",
        "python scripts/validate_public_release_identity.py .",
    ),
    RELEASE_BUILD_WORKFLOW_REL: ("default: v2.0.0",),
    RELEASE_ARTIFACT_SMOKE_WORKFLOW_REL: ("default: v2.0.0",),
    WINDOWS_OFFLINE_SMOKE_WORKFLOW_REL: (
        "build_offline_bundle.sh",
        "install_offline.ps1 -AddToPath",
        "ai-sdlc init . --agent-target codex --shell powershell",
        "ai-sdlc run --dry-run",
    ),
}
BEGINNER_GUIDE_REQUIRED_TOKENS = (
    "# AI-SDLC 2.0.0 中文用户指南",
    "## 第一章：全新用户 + 全新空项目",
    "## 第二章：全新用户 + 已有项目",
    "### 1.1 Windows",
    "### 1.2 macOS（Apple Silicon）",
    "### 1.3 Linux（amd64）",
    "### 1.4 选择 AI 适配器和 Shell",
    "### 2.4 选择 AI 适配器和 Shell",
    "## 异常情况速查",
    "ai-sdlc-offline-2.0.0-windows-amd64.zip",
    "ai-sdlc-offline-2.0.0-macos-arm64.tar.gz",
    "ai-sdlc-offline-2.0.0-linux-amd64.tar.gz",
    "Get-FileHash -Algorithm SHA256",
    "shasum -a 256 -c",
    "sha256sum -c",
    "-AddToPath",
    "--add-to-path",
    "Claude Code",
    "Codex",
    "Cursor",
    "VS Code",
    "其他-通用",
    "实际用于聊天开发的 AI 代理入口",
    "ai-sdlc init .",
    "Initialized AI-SDLC project",
    "当前结果 / Result",
    "下一步 / Next",
)
BEGINNER_GUIDE_EXISTING_PROJECT_INIT_TOKENS = (
    "## 第二章：全新用户 + 已有项目",
    "ai-sdlc adopt .",
    "接入已有项目：已生成桥接结果",
    "原任务文件不会被修改",
    "推荐继续点",
)
BEGINNER_GUIDE_FORBIDDEN_TOKENS = (
    "老版本升级",
    "从源码运行",
    "@main",
    "uv sync",
    "git+https://github.com/SinclairPan/Ai_AutoSDLC.git",
    "git clone --branch",
    "开发版",
    "Codex + PowerShell 为默认组合",
    "releases/download/v1.0.2/",
    "releases/download/v1.0.4/",
    "releases/download/v1.0.5/",
)
README_CLI_PATH_REQUIRED_TOKENS = (
    "## 快速开始",
    "ai-sdlc init . --agent-target codex --shell powershell",
    "自动执行一次安全预演",
    "ai-sdlc adapter status",
    "ai-sdlc run --dry-run",
)
README_CLI_PATH_FORBIDDEN_TOKENS: tuple[str, ...] = ()
AGENTS_CLI_PATH_REQUIRED_TOKENS = (
    "初始化入口（普通用户先执行）",
    "自动执行必要检查与安全预演",
    "排查入口（仅当 CLI 明确要求时执行）",
    "不要再要求用户手动执行 `adapter status` 或 `run --dry-run`",
)
AGENTS_CLI_PATH_FORBIDDEN_TOKENS = (
    "先检查接入真值：`ai-sdlc adapter status`",
    "启动入口（先执行）：`ai-sdlc run --dry-run`",
    "优先引导并先执行上述启动入口",
)
ADAPTER_TEMPLATE_CLI_PATH_RELS = (
    Path("src") / "ai_sdlc" / "adapters" / "codex" / "AI-SDLC.md",
    Path("src") / "ai_sdlc" / "adapters" / "claude_code" / "AI-SDLC.md",
    Path("src") / "ai_sdlc" / "adapters" / "vscode" / "AI-SDLC.md",
    Path("src") / "ai_sdlc" / "adapters" / "cursor" / "rules" / "ai-sdlc.md",
)
FRONTEND_SOLUTION_CONFIRMATION_RELS = (
    PIPELINE_RULE_REL,
    AGENTS_REL,
    *ADAPTER_TEMPLATE_CLI_PATH_RELS,
)
FRONTEND_SOLUTION_CONFIRMATION_REQUIRED_TOKENS = (
    "前端需求",
    "项目已有技术栈",
    "一个推荐方案",
    "至少一个可选 / 自定义方案",
    "用户明确确认",
    "不得进入 execute",
    "不得生成前端实现代码",
    "规范正文",
    "可选建议",
    "已经落地",
    "通用规则不得硬编码框架、组件库、provider 或 style pack",
    "Applicable Rules",
    "不要求用户手动执行 `rules show` 或 `stage show`",
)
FRONTEND_SOLUTION_CONFIRMATION_FORBIDDEN_TOKENS = (
    "frontend_stack=vue3",
    "provider_id=public-primevue",
    "style_pack_id=modern-saas",
    "PrimeVue + @primeuix/themes",
    "enterprise-vue2",
    "program solution-confirm --dry-run --mode advanced",
)
ADAPTER_TEMPLATE_COMMENT_POLICY_RELS = (
    *ADAPTER_TEMPLATE_CLI_PATH_RELS,
    Path("src") / "ai_sdlc" / "adapters" / "generic" / "ide-hint.md",
)
ADAPTER_TEMPLATE_COMMENT_POLICY_REQUIRED_TOKENS = (
    "后续 agent 或人工需要维护",
    "认证",
    "XHR/API 调用",
    "payload 字段映射",
    "加密",
    "阶段流程",
    "维护契约",
    "docstring",
)
FEATURE_CONTRACT_SURFACE_OBJECT = "feature_contract_surfaces"
FRAMEWORK_DEFECT_BACKLOG_REQUIRED_FIELDS = (
    "现象",
    "触发场景",
    "影响范围",
    "根因分类",
    "建议改动层级",
    "prompt / context",
    "rule / policy",
    "middleware",
    "workflow",
    "tool",
    "eval",
    "风险等级",
    "可验证成功标准",
    "是否需要回归测试补充",
)


class ConstraintProfile(str, Enum):
    """Select consumer-project or framework self-development verification."""

    PROJECT = "project"
    SELF_DEVELOPMENT = "self-development"


PROJECT_VERIFICATION_GATE_OBJECTS = (
    "required_governance_files",
    "branch_lifecycle",
    "checkpoint_spec_dir",
    "tasks_acceptance",
    "skip_registry_mapping",
)
VERIFICATION_GATE_OBJECTS = (
    "required_governance_files",
    "framework_defect_backlog",
    "reconcile_smoke_contract",
    "doc_first_surfaces",
    "verification_profiles",
    FEATURE_CONTRACT_SURFACE_OBJECT,
    "branch_lifecycle",
    "checkpoint_spec_dir",
    "tasks_acceptance",
    "skip_registry_mapping",
)
FRONTEND_SOLUTION_CONFIRMATION_SOURCE_NAME = (
    "frontend solution confirmation verification"
)
FRONTEND_SOLUTION_CONFIRMATION_COVERAGE_GAP = (
    "frontend_solution_confirmation_consistency"
)
FRONTEND_THEME_TOKEN_GOVERNANCE_SOURCE_NAME = (
    "frontend theme token governance verification"
)
FRONTEND_THEME_TOKEN_GOVERNANCE_COVERAGE_GAP = (
    "frontend_theme_token_governance_consistency"
)
FRONTEND_QUALITY_PLATFORM_SOURCE_NAME = (
    "frontend quality platform verification"
)
FRONTEND_QUALITY_PLATFORM_COVERAGE_GAP = (
    "frontend_quality_platform_consistency"
)
FRONTEND_PROVIDER_EXPANSION_SOURCE_NAME = (
    "frontend provider expansion verification"
)
FRONTEND_PROVIDER_EXPANSION_COVERAGE_GAP = (
    "frontend_provider_expansion_consistency"
)
FRONTEND_PROVIDER_RUNTIME_ADAPTER_SOURCE_NAME = (
    "frontend provider runtime adapter verification"
)
FRONTEND_PROVIDER_RUNTIME_ADAPTER_COVERAGE_GAP = (
    "frontend_provider_runtime_adapter_consistency"
)
FRONTEND_CROSS_PROVIDER_CONSISTENCY_SOURCE_NAME = (
    "frontend cross provider consistency verification"
)
FRONTEND_CROSS_PROVIDER_CONSISTENCY_COVERAGE_GAP = (
    "frontend_cross_provider_consistency"
)
FRONTEND_PUBLIC_PRIMEVUE_IMPORT_BOUNDARY_SOURCE_NAME = (
    "frontend public-primevue import boundary verification"
)
FRONTEND_PUBLIC_PRIMEVUE_IMPORT_BOUNDARY_CHECK_OBJECTS = (
    "frontend_public_primevue_import_boundary",
)
FRONTEND_PUBLIC_PRIMEVUE_IMPORT_BOUNDARY_IMPORT_RE = re.compile(
    r"""(?x)
    (?:import\s+(?:type\s+)?[^;]*?\s+from\s*|import\s*\()\s*
    ["']primevue/
    """
)
FRONTEND_PUBLIC_PRIMEVUE_IMPORT_BOUNDARY_EXTENSIONS = frozenset(
    {".js", ".jsx", ".ts", ".tsx", ".vue"}
)
FRONTEND_PUBLIC_PRIMEVUE_DEFAULT_RECOMMENDATION = (
    "vue3",
    "public-primevue",
    "modern-saas",
)
FRONTEND_PUBLIC_PRIMEVUE_REQUIRED_TEMPLATE_FILES = (
    Path("index.html"),
    Path("package.json"),
    Path("uno.config.ts"),
    Path("vite.config.ts"),
    Path("src") / "App.vue",
    Path("src") / "main.ts",
    Path("src") / "theme.ts",
    Path("src") / "api" / "client.ts",
    Path("src") / "api" / "interceptors.ts",
    Path("src") / "api" / "types.ts",
    Path("src") / "components" / "base" / "BaseButton.vue",
    Path("src") / "components" / "base" / "BaseTable.vue",
    Path("src") / "components" / "base" / "BaseDialog.vue",
    Path("src") / "components" / "base" / "BaseForm.vue",
    Path("src") / "i18n" / "index.ts",
    Path("src") / "plugins" / "primevue.ts",
    Path("src") / "router" / "index.ts",
    Path("src") / "stores" / "app.ts",
    Path("src") / "styles" / "variables.css",
    Path("src") / "styles" / "main.css",
    Path("src") / "transform" / "index.ts",
)
FRONTEND_PUBLIC_PRIMEVUE_SMOKE_PAGE_TEMPLATE_FILES = (
    Path("src") / "pages" / "ManagedDeliverySmoke.vue",
    Path("src") / "views" / "ManagedDeliverySmoke.vue",
)
FRONTEND_EVIDENCE_CLASS_ALLOWED_VALUES = (
    "framework_capability",
    "consumer_adoption",
)
FRONTEND_EVIDENCE_CLASS_CONTRACT_REF = (
    "specs/085-frontend-evidence-class-verify-first-runtime-cut-baseline/spec.md"
)
FRONTEND_EVIDENCE_CLASS_MIN_SEQUENCE = 82
FRONTEND_EVIDENCE_CLASS_KEY = "frontend_evidence_class"
FRONTEND_EVIDENCE_CLASS_PROBLEM_FAMILY = (
    "frontend_evidence_class_authoring_malformed"
)
_FRONTEND_EVIDENCE_CLASS_BODY_DECL_RE = re.compile(
    r'(?m)^[ \t]*frontend_evidence_class\s*:\s*(["\']?)(?P<value>[A-Za-z_-]*)\1\s*$'
)
_FRONTEND_EVIDENCE_CLASS_DUPLICATE_RE = re.compile(
    r"(?m)^[ \t]*frontend_evidence_class\s*:"
)
FRONTEND_SOLUTION_CONFIRMATION_CHECK_OBJECTS = (
    "frontend_provider_profile_artifacts",
    "frontend_solution_style_pack_artifacts",
    "frontend_solution_install_strategy_artifacts",
    "frontend_solution_snapshot_artifacts",
    FRONTEND_SOLUTION_CONFIRMATION_COVERAGE_GAP,
)
FRONTEND_THEME_TOKEN_GOVERNANCE_CHECK_OBJECTS = (
    "frontend_theme_governance_manifest_artifacts",
    "frontend_theme_token_mapping_artifacts",
    "frontend_theme_override_policy_artifacts",
    "frontend_theme_style_editor_boundary_artifacts",
    FRONTEND_THEME_TOKEN_GOVERNANCE_COVERAGE_GAP,
)
FRONTEND_QUALITY_PLATFORM_CHECK_OBJECTS = (
    "frontend_quality_platform_manifest_artifacts",
    "frontend_quality_platform_handoff_schema_artifacts",
    "frontend_quality_platform_coverage_matrix_artifacts",
    "frontend_quality_platform_evidence_platform_artifacts",
    "frontend_quality_platform_interaction_flow_artifacts",
    "frontend_quality_platform_truth_surfacing_artifacts",
    "frontend_quality_platform_verdict_artifacts",
    FRONTEND_QUALITY_PLATFORM_COVERAGE_GAP,
)
FRONTEND_PROVIDER_EXPANSION_CHECK_OBJECTS = (
    "frontend_provider_expansion_manifest_artifacts",
    "frontend_provider_expansion_handoff_schema_artifacts",
    "frontend_provider_expansion_truth_surfacing_artifacts",
    "frontend_provider_expansion_choice_surface_policy_artifacts",
    "frontend_provider_expansion_react_boundary_artifacts",
    FRONTEND_PROVIDER_EXPANSION_COVERAGE_GAP,
)
FRONTEND_PROVIDER_RUNTIME_ADAPTER_CHECK_OBJECTS = (
    "frontend_provider_runtime_adapter_manifest_artifacts",
    "frontend_provider_runtime_adapter_handoff_schema_artifacts",
    "frontend_provider_runtime_adapter_targets_artifacts",
    "frontend_provider_runtime_adapter_scaffold_artifacts",
    "frontend_provider_runtime_adapter_boundary_receipt_artifacts",
    FRONTEND_PROVIDER_RUNTIME_ADAPTER_COVERAGE_GAP,
)
FRONTEND_CROSS_PROVIDER_CONSISTENCY_CHECK_OBJECTS = (
    "frontend_cross_provider_consistency_manifest_artifacts",
    "frontend_cross_provider_consistency_handoff_schema_artifacts",
    "frontend_cross_provider_consistency_truth_surfacing_artifacts",
    "frontend_cross_provider_consistency_readiness_gate_artifacts",
    "frontend_cross_provider_consistency_pair_diff_summary_artifacts",
    "frontend_cross_provider_consistency_pair_certification_artifacts",
    "frontend_cross_provider_consistency_pair_evidence_index_artifacts",
    FRONTEND_CROSS_PROVIDER_CONSISTENCY_COVERAGE_GAP,
)


@dataclass(frozen=True, slots=True)
class FeatureContractEvidence:
    """One evidence entry required to satisfy a feature-contract surface."""

    relative_paths: tuple[Path, ...]
    required_tokens: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "relative_paths",
            tuple(dict.fromkeys(self.relative_paths)),
        )
        object.__setattr__(
            self,
            "required_tokens",
            tuple(dict.fromkeys(self.required_tokens)),
        )


@dataclass(frozen=True, slots=True)
class FeatureContractSurface:
    """Minimal work-item scoped feature-contract surface requirement."""

    label: str
    evidence_entries: tuple[FeatureContractEvidence, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "evidence_entries",
            tuple(dict.fromkeys(self.evidence_entries)),
        )


FRAMEWORK_003_FEATURE_CONTRACT_WORK_ITEM_ID = (
    "003-cross-cutting-authoring-and-extension-contracts"
)

FEATURE_CONTRACT_SURFACES: dict[str, tuple[FeatureContractSurface, ...]] = {
    "003": (
        # The 003 work item is intentionally scoped to the four missing contract groups
        # called out in the task plan. Each surface is satisfied by one of the listed
        # code files containing the required contract markers.
        FeatureContractSurface(
            label="draft_prd/final_prd",
            evidence_entries=(
                FeatureContractEvidence(
                    relative_paths=(Path("src") / "ai_sdlc" / "models" / "work.py",),
                    required_tokens=("draft_prd", "final_prd"),
                ),
            ),
        ),
        FeatureContractSurface(
            label="reviewer decision",
            evidence_entries=(
                FeatureContractEvidence(
                    relative_paths=(Path("src") / "ai_sdlc" / "models" / "work.py",),
                    required_tokens=("reviewer_decision", "approve", "revise", "block"),
                ),
                FeatureContractEvidence(
                    relative_paths=(Path("src") / "ai_sdlc" / "core" / "reviewer_gate.py",),
                    required_tokens=(
                        "ALLOW",
                        "DENY_MISSING",
                        "DENY_REVISE",
                        "DENY_BLOCK",
                    ),
                ),
                FeatureContractEvidence(
                    relative_paths=(Path("src") / "ai_sdlc" / "core" / "state_machine.py",),
                    required_tokens=(
                        "transition_work_item",
                        "ReviewerGateOutcomeKind",
                        "InvalidTransitionError",
                    ),
                ),
                FeatureContractEvidence(
                    relative_paths=(Path("src") / "ai_sdlc" / "core" / "close_check.py",),
                    required_tokens=(
                        "evaluate_reviewer_gate",
                        "DEV_REVIEWED",
                        "review_gate",
                    ),
                ),
            ),
        ),
        FeatureContractSurface(
            label="backend delegation/fallback",
            evidence_entries=(
                FeatureContractEvidence(
                    relative_paths=(Path("src") / "ai_sdlc" / "backends" / "native.py",),
                    required_tokens=("backend_capability", "delegation", "fallback"),
                ),
                FeatureContractEvidence(
                    relative_paths=(Path("src") / "ai_sdlc" / "backends" / "routing.py",),
                    required_tokens=(
                        "BackendRoutingCoordinator",
                        "generate_spec",
                        "generate_plan",
                        "generate_tasks",
                    ),
                ),
                FeatureContractEvidence(
                    relative_paths=(Path("src") / "ai_sdlc" / "generators" / "doc_gen.py",),
                    required_tokens=(
                        "backend_registry",
                        "requested_backend",
                        "backend_policy",
                        "backend_decisions",
                    ),
                ),
            ),
        ),
        FeatureContractSurface(
            label="release-gate evidence",
            evidence_entries=(
                FeatureContractEvidence(
                    relative_paths=(
                        Path("specs")
                        / "003-cross-cutting-authoring-and-extension-contracts"
                        / "release-gate-evidence.md",
                    ),
                    required_tokens=("release_gate_evidence", "PASS", "WARN", "BLOCK"),
                ),
            ),
        ),
    ),
    "189": (
        FeatureContractSurface(
            label="local PR review CLI",
            evidence_entries=(
                FeatureContractEvidence(
                    relative_paths=(
                        Path("src") / "ai_sdlc" / "cli" / "pr_review_cmd.py",
                    ),
                    required_tokens=(
                        "pr_review_doctor",
                        "pr_review_start",
                        "pr_review_status",
                        "pr_review_fix",
                        "pr_review_rerun",
                        "pr_review_close",
                    ),
                ),
            ),
        ),
        FeatureContractSurface(
            label="local PR review provider isolation",
            evidence_entries=(
                FeatureContractEvidence(
                    relative_paths=(
                        Path("src") / "ai_sdlc" / "core" / "pr_review_provider.py",
                    ),
                    required_tokens=(
                        "ProviderRunnerInvocation",
                        "run_provider_command",
                        "run_mock_reviewer",
                        "code_egress",
                        "allowlist",
                        "_is_reviewed_dirty_status_for_launch",
                    ),
                ),
            ),
        ),
        FeatureContractSurface(
            label="local PR review schema policy artifacts",
            evidence_entries=(
                FeatureContractEvidence(
                    relative_paths=(
                        Path("src") / "ai_sdlc" / "core" / "pr_review_pack.py",
                    ),
                    required_tokens=(
                        "review-pack.json",
                        "redaction-report.json",
                        "model-resolution.json",
                        "source-resolution.json",
                        "diff.patch",
                        "_diff_git_paths",
                        "_diff_file_blobs",
                        "_normalize_patch_path",
                    ),
                ),
                FeatureContractEvidence(
                    relative_paths=(
                        Path("src") / "ai_sdlc" / "core" / "loop_policy.py",
                    ),
                    required_tokens=(
                        "loop-policy.yaml",
                        "current session/current CLI agent",
                        "Explicit model service is unavailable",
                        "code_egress",
                    ),
                ),
            ),
        ),
        FeatureContractSurface(
            label="local PR review source adapter and model fallback boundary",
            evidence_entries=(
                FeatureContractEvidence(
                    relative_paths=(
                        Path("src") / "ai_sdlc" / "core" / "pr_review_models.py",
                    ),
                    required_tokens=(
                        "DiffSourceDescriptor",
                        "SourceAdapterResolution",
                        "source_access_status",
                        "model_unavailable_reason",
                    ),
                ),
                FeatureContractEvidence(
                    relative_paths=(
                        Path("src") / "ai_sdlc" / "core" / "pr_review_source.py",
                    ),
                    required_tokens=(
                        "local-git-range",
                        "local-staged",
                        "local-unstaged",
                        "Start local PR review from patch file",
                        "patch_file_not_found",
                        "git_head_unavailable",
                        "SCM PR/MR diff source adapter is not implemented in P0",
                    ),
                ),
                FeatureContractEvidence(
                    relative_paths=(
                        Path("src") / "ai_sdlc" / "core" / "loop_policy.py",
                    ),
                    required_tokens=(
                        "current-first local agent contract",
                        "Explicit model service is unavailable or not connected",
                        "provider_default_model",
                    ),
                ),
            ),
        ),
        FeatureContractSurface(
            label="local PR review user docs",
            evidence_entries=(
                FeatureContractEvidence(
                    relative_paths=(Path("README.md"),),
                    required_tokens=(
                        "ai-sdlc pr-review doctor",
                        "ai-sdlc pr-review start",
                        "model current",
                        "DiffSource",
                        "local-staged",
                    ),
                ),
                FeatureContractEvidence(
                    relative_paths=(
                        Path("docs") / "pull-request-checklist.zh.md",
                    ),
                    required_tokens=(
                        "本地 PR review",
                        "DiffSource",
                    ),
                ),
            ),
        ),
        FeatureContractSurface(
            label="local PR review P2 enterprise fail-closed boundaries",
            evidence_entries=(
                FeatureContractEvidence(
                    relative_paths=(
                        Path("specs")
                        / "189-loop-engine-local-adversarial-pr-review"
                        / "plan.md",
                    ),
                    required_tokens=(
                        "多 reviewer",
                        "artifact 签名",
                        "远端 PR inline comments",
                        "fail-closed",
                    ),
                ),
                FeatureContractEvidence(
                    relative_paths=(
                        Path("specs")
                        / "189-loop-engine-local-adversarial-pr-review"
                        / "tasks.md",
                    ),
                    required_tokens=(
                        "T91",
                        "不得在无企业身份",
                        "GitHub/GitLab/Gitee/self-hosted SCM",
                        "fail-closed",
                    ),
                ),
            ),
        ),
    ),
    "190": (
        FeatureContractSurface(
            label="loop status/list core readers",
            evidence_entries=(
                FeatureContractEvidence(
                    relative_paths=(
                        Path("src") / "ai_sdlc" / "core" / "loop_status.py",
                    ),
                    required_tokens=(
                        "get_loop_status",
                        "list_loops",
                        "current-review.json",
                        "review-run.json",
                        "malformed_count",
                    ),
                ),
            ),
        ),
        FeatureContractSurface(
            label="loop status/list CLI",
            evidence_entries=(
                FeatureContractEvidence(
                    relative_paths=(
                        Path("src") / "ai_sdlc" / "cli" / "loop_cmd.py",
                    ),
                    required_tokens=(
                        "loop_status",
                        "loop_list",
                        "get_loop_status",
                        "list_loops",
                        "json_output",
                    ),
                ),
            ),
        ),
        FeatureContractSurface(
            label="loop status/list read-only user docs",
            evidence_entries=(
                FeatureContractEvidence(
                    relative_paths=(Path("README.md"),),
                    required_tokens=(
                        "ai-sdlc loop status",
                        "ai-sdlc loop list",
                        "read-only artifact index",
                        "do not call GPT",
                        "Claude, DeepSeek",
                        "GLM, Codex",
                    ),
                ),
                FeatureContractEvidence(
                    relative_paths=(
                        Path("docs") / "pull-request-checklist.zh.md",
                    ),
                    required_tokens=(
                        "Loop status/list",
                        "只读读取",
                        "不得发起模型请求",
                        "不能替代本地对抗 review agent",
                    ),
                ),
            ),
        ),
    ),
    "191": (
        FeatureContractSurface(
            label="loop next guidance core readers",
            evidence_entries=(
                FeatureContractEvidence(
                    relative_paths=(
                        Path("src") / "ai_sdlc" / "core" / "loop_status.py",
                    ),
                    required_tokens=(
                        "LoopNextActionGuidance",
                        "next_guidance",
                        "requires_model",
                        "writes_artifacts",
                        "writes_code",
                    ),
                ),
            ),
        ),
        FeatureContractSurface(
            label="loop next guidance CLI",
            evidence_entries=(
                FeatureContractEvidence(
                    relative_paths=(
                        Path("src") / "ai_sdlc" / "cli" / "loop_cmd.py",
                    ),
                    required_tokens=(
                        "Next command",
                        "Model call",
                        "Writes artifacts",
                        "Writes code",
                        "_emit_guidance",
                    ),
                ),
            ),
        ),
        FeatureContractSurface(
            label="loop next guidance read-only user docs",
            evidence_entries=(
                FeatureContractEvidence(
                    relative_paths=(Path("README.md"),),
                    required_tokens=(
                        "next guidance",
                        "does not execute",
                        "does not call any model",
                        "local independent review agent",
                    ),
                ),
                FeatureContractEvidence(
                    relative_paths=(
                        Path("docs") / "pull-request-checklist.zh.md",
                    ),
                    required_tokens=(
                        "Next Action Guidance",
                        "只读推导",
                        "不得执行下一步命令",
                        "不得发起模型请求",
                    ),
                ),
            ),
        ),
    ),
    "192": (
        FeatureContractSurface(
            label="requirement loop core runtime",
            evidence_entries=(
                FeatureContractEvidence(
                    relative_paths=(
                        Path("src") / "ai_sdlc" / "core" / "requirement_loop.py",
                    ),
                    required_tokens=(
                        "RequirementIntake",
                        "RequirementFreeze",
                        "start_requirement_loop",
                        "freeze_requirement_loop",
                        "CURRENT_REQUIREMENT_PATH",
                    ),
                ),
            ),
        ),
        FeatureContractSurface(
            label="requirement loop status and CLI",
            evidence_entries=(
                FeatureContractEvidence(
                    relative_paths=(
                        Path("src") / "ai_sdlc" / "core" / "loop_status.py",
                    ),
                    required_tokens=(
                        "RequirementLoopSummary",
                        "_get_requirement_loop_status",
                        "_list_requirement_loops",
                        "current-requirement.json",
                    ),
                ),
                FeatureContractEvidence(
                    relative_paths=(
                        Path("src") / "ai_sdlc" / "cli" / "loop_cmd.py",
                    ),
                    required_tokens=(
                        "requirement_app",
                        "requirement_start",
                        "requirement_freeze",
                        "--type",
                    ),
                ),
            ),
        ),
        FeatureContractSurface(
            label="requirement loop user docs",
            evidence_entries=(
                FeatureContractEvidence(
                    relative_paths=(Path("README.md"),),
                    required_tokens=(
                        "ai-sdlc loop requirement start",
                        "ai-sdlc loop status --type requirement",
                        "ai-sdlc loop review --type requirement",
                        "ai-sdlc loop requirement freeze --loop-id <loop-id> "
                        "--expect-review-digest <input_digest> --yes",
                        "design-contract",
                    ),
                ),
            ),
        ),
    ),
    "193": (
        FeatureContractSurface(
            label="design-contract loop core runtime",
            evidence_entries=(
                FeatureContractEvidence(
                    relative_paths=(
                        Path("src") / "ai_sdlc" / "core" / "design_contract_loop.py",
                    ),
                    required_tokens=(
                        "check_design_contract_loop",
                        "close_design_contract_loop",
                        "CURRENT_DESIGN_CONTRACT_PATH",
                    ),
                ),
                FeatureContractEvidence(
                    relative_paths=(
                        Path("src")
                        / "ai_sdlc"
                        / "core"
                        / "design_contract_models.py",
                    ),
                    required_tokens=(
                        "DesignContractInput",
                        "DesignContractReport",
                        "DesignContractClose",
                        "DesignContractCheckOptions",
                    ),
                ),
            ),
        ),
        FeatureContractSurface(
            label="design-contract loop status and CLI",
            evidence_entries=(
                FeatureContractEvidence(
                    relative_paths=(
                        Path("src") / "ai_sdlc" / "core" / "loop_status.py",
                    ),
                    required_tokens=(
                        "DesignContractLoopSummary",
                        "_get_design_contract_loop_status",
                        "_list_design_contract_loops",
                        "current-design-contract.json",
                    ),
                ),
                FeatureContractEvidence(
                    relative_paths=(
                        Path("src") / "ai_sdlc" / "cli" / "loop_cmd.py",
                    ),
                    required_tokens=(
                        "design_contract_app",
                        "design_contract_check",
                        "design_contract_close",
                        "--type",
                    ),
                ),
            ),
        ),
        FeatureContractSurface(
            label="design-contract loop user docs",
            evidence_entries=(
                FeatureContractEvidence(
                    relative_paths=(Path("README.md"),),
                    required_tokens=(
                        "ai-sdlc loop design-contract check",
                        "ai-sdlc loop status --type design-contract",
                        "ai-sdlc loop review --type design-contract",
                        "ai-sdlc loop design-contract close --loop-id <loop-id> "
                        "--expect-review-digest <input_digest> --yes",
                        "implementation loop",
                    ),
                ),
            ),
        ),
    ),
    "194": (
        FeatureContractSurface(
            label="implementation loop core runtime",
            evidence_entries=(
                FeatureContractEvidence(
                    relative_paths=(
                        Path("src") / "ai_sdlc" / "core" / "implementation_loop.py",
                    ),
                    required_tokens=(
                        "start_implementation_loop",
                        "record_implementation_progress",
                        "close_implementation_loop",
                        "CURRENT_IMPLEMENTATION_PATH",
                    ),
                ),
                FeatureContractEvidence(
                    relative_paths=(
                        Path("src")
                        / "ai_sdlc"
                        / "core"
                        / "implementation_models.py",
                    ),
                    required_tokens=(
                        "ImplementationInput",
                        "ImplementationReport",
                        "ImplementationClose",
                        "ImplementationStartOptions",
                    ),
                ),
            ),
        ),
        FeatureContractSurface(
            label="implementation loop status and CLI",
            evidence_entries=(
                FeatureContractEvidence(
                    relative_paths=(
                        Path("src") / "ai_sdlc" / "core" / "loop_status.py",
                    ),
                    required_tokens=(
                        "ImplementationLoopSummary",
                        "_get_implementation_loop_status",
                        "_list_implementation_loops",
                        "current-implementation.json",
                    ),
                ),
                FeatureContractEvidence(
                    relative_paths=(
                        Path("src") / "ai_sdlc" / "cli" / "loop_cmd.py",
                    ),
                    required_tokens=(
                        "implementation_app",
                        "implementation_start",
                        "implementation_record",
                        "implementation_close",
                        "--type",
                    ),
                ),
            ),
        ),
        FeatureContractSurface(
            label="implementation loop user docs",
            evidence_entries=(
                FeatureContractEvidence(
                    relative_paths=(Path("README.md"),),
                    required_tokens=(
                        "ai-sdlc loop implementation start",
                        "ai-sdlc loop status --type implementation",
                        "ai-sdlc loop implementation record",
                        "ai-sdlc loop review --type implementation",
                        "ai-sdlc loop implementation close --loop-id <loop-id> "
                        "--expect-review-digest <input_digest> --yes",
                        "frontend-evidence",
                        "local-pr-review",
                    ),
                ),
            ),
        ),
    ),
    "195": (
        FeatureContractSurface(
            label="frontend-evidence loop core runtime",
            evidence_entries=(
                FeatureContractEvidence(
                    relative_paths=(
                        Path("src") / "ai_sdlc" / "core" / "frontend_evidence_loop.py",
                    ),
                    required_tokens=(
                        "start_frontend_evidence_loop",
                        "close_frontend_evidence_loop",
                        "CURRENT_FRONTEND_EVIDENCE_PATH",
                    ),
                ),
                FeatureContractEvidence(
                    relative_paths=(
                        Path("src")
                        / "ai_sdlc"
                        / "core"
                        / "frontend_evidence_models.py",
                    ),
                    required_tokens=(
                        "FrontendEvidenceInput",
                        "FrontendEvidenceSnapshot",
                        "FrontendEvidenceReport",
                        "FrontendEvidenceClose",
                        "FrontendEvidenceStartOptions",
                        "FrontendEvidenceDoctorOptions",
                        "FrontendEvidenceDoctorResult",
                        "FrontendEvidenceProviderCheck",
                        "FrontendEvidenceSkipOptions",
                    ),
                ),
            ),
        ),
        FeatureContractSurface(
            label="frontend-evidence loop status and CLI",
            evidence_entries=(
                FeatureContractEvidence(
                    relative_paths=(
                        Path("src") / "ai_sdlc" / "core" / "loop_status.py",
                    ),
                    required_tokens=(
                        "FrontendEvidenceLoopSummary",
                        "_get_frontend_evidence_loop_status",
                        "_list_frontend_evidence_loops",
                        "current-frontend-evidence.json",
                    ),
                ),
                FeatureContractEvidence(
                    relative_paths=(
                        Path("src") / "ai_sdlc" / "cli" / "loop_cmd.py",
                    ),
                    required_tokens=(
                        "frontend_evidence_app",
                        "frontend_evidence_doctor",
                        "frontend_evidence_start",
                        "frontend_evidence_close",
                        "frontend_evidence_skip",
                        "--provider",
                        "--reason",
                        "--allow-warnings",
                        "--type",
                    ),
                ),
            ),
        ),
        FeatureContractSurface(
            label="frontend-evidence loop user docs",
            evidence_entries=(
                FeatureContractEvidence(
                    relative_paths=(Path("README.md"),),
                    required_tokens=(
                        "ai-sdlc loop frontend-evidence doctor --provider auto",
                        "ai-sdlc loop frontend-evidence start",
                        "ai-sdlc loop status --type frontend-evidence",
                        "ai-sdlc loop review --type frontend-evidence",
                        "ai-sdlc loop frontend-evidence close --loop-id <loop-id> "
                        "--expect-review-digest <input_digest> --yes",
                        "ai-sdlc loop frontend-evidence skip",
                        "ai-sdlc program browser-gate-probe --execute",
                        "--allow-warnings",
                        "local-pr-review",
                    ),
                ),
            ),
        ),
    ),
}


@dataclass(frozen=True, slots=True)
class ConstraintReport:
    """Structured verify-constraints result for telemetry evidence capture."""

    root: str
    source_name: str
    blockers: tuple[str, ...]
    profile: ConstraintProfile = ConstraintProfile.PROJECT
    gate_name: str = "Verification Gate"
    check_objects: tuple[str, ...] = PROJECT_VERIFICATION_GATE_OBJECTS
    coverage_gaps: tuple[str, ...] = ()
    release_gate: dict[str, object] | None = None
    evidence_kinds: tuple[str, ...] = ("event", "structured_report")

    def __post_init__(self) -> None:
        object.__setattr__(self, "blockers", tuple(_dedupe_text_items(self.blockers)))
        object.__setattr__(
            self, "check_objects", tuple(_dedupe_text_items(self.check_objects))
        )
        object.__setattr__(
            self, "coverage_gaps", tuple(_dedupe_text_items(self.coverage_gaps))
        )
        object.__setattr__(
            self, "evidence_kinds", tuple(_dedupe_text_items(self.evidence_kinds))
        )


def _dedupe_text_items(values: object) -> list[str]:
    deduped: list[str] = []
    for value in values or []:
        normalized = str(value).strip()
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    return deduped
















def build_constraint_report(
    root: Path,
    *,
    profile: ConstraintProfile = ConstraintProfile.PROJECT,
) -> ConstraintReport:
    """Build a structured report for verify constraints."""
    checkpoint = load_checkpoint(root)
    if profile is ConstraintProfile.PROJECT:
        return ConstraintReport(
            root=str(root),
            gate_name="Verification Gate",
            source_name="verify constraints",
            profile=profile,
            check_objects=PROJECT_VERIFICATION_GATE_OBJECTS,
            blockers=tuple(collect_constraint_blockers(root, profile=profile)),
            coverage_gaps=(),
            release_gate=None,
        )

    return ConstraintReport(
        root=str(root),
        gate_name="Verification Gate",
        source_name="verify constraints",
        profile=profile,
        check_objects=VERIFICATION_GATE_OBJECTS,
        blockers=tuple(collect_constraint_blockers(root, profile=profile)),
        coverage_gaps=_feature_contract_coverage_gaps(root, checkpoint),
        release_gate=None,
    )


def build_verification_gate_context(
    root: Path,
    *,
    profile: ConstraintProfile = ConstraintProfile.PROJECT,
) -> dict[str, object]:
    """Build the explicit Verification Gate context consumed by runner and gate CLI."""
    report = build_constraint_report(root, profile=profile)
    if profile is ConstraintProfile.PROJECT:
        governance = build_verification_governance_bundle(
            report,
            decision_subject=f"verify:{root}",
            evidence_refs=("verify-constraints:structured-report",),
        )
        decision_result = str(governance["gate_decision_payload"]["decision_result"])
        return {
            "verification_profile": profile.value,
            "verification_sources": (report.source_name,),
            "verification_check_objects": report.check_objects,
            "constraint_blockers": report.blockers if decision_result == "block" else (),
            "coverage_gaps": report.coverage_gaps if decision_result == "block" else (),
            "release_gate": None,
            "verification_governance": governance,
        }

    governance = build_verification_governance_bundle(
        report,
        decision_subject=f"verify:{root}",
        evidence_refs=("verify-constraints:structured-report",),
    )
    decision_result = str(governance["gate_decision_payload"]["decision_result"])
    return {
        "verification_profile": profile.value,
        "verification_sources": (report.source_name,),
        "verification_check_objects": report.check_objects,
        "constraint_blockers": report.blockers if decision_result == "block" else (),
        "coverage_gaps": report.coverage_gaps if decision_result == "block" else (),
        "release_gate": None,
        "verification_governance": governance,
    }


def build_verification_governance_bundle(
    report: ConstraintReport,
    *,
    decision_subject: str,
    evidence_refs: tuple[str, ...] | list[str],
    source_closure_status: str = "closed",
    observer_version: str = "v1",
    policy: str = "default",
    profile: str = "self_hosting",
    mode: str = "lite",
) -> dict[str, object]:
    """Build a read-only decision summary directly from the constraint report."""
    encoded = json.dumps(
        asdict(report),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    report_digest = f"sha256:{hashlib.sha256(encoded).hexdigest()}"
    generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    evidence_refs = tuple(str(ref) for ref in evidence_refs if str(ref))
    effective_source_closure_status = (
        source_closure_status if evidence_refs else "incomplete"
    )
    advisories: list[str] = []
    if effective_source_closure_status != "closed":
        advisories.append(
            "verification summary advisory: "
            f"source_closure_status={effective_source_closure_status}"
        )
    decision_result = (
        "advisory"
        if effective_source_closure_status != "closed"
        else ("block" if report.blockers else "allow")
    )
    gate_decision_payload = {
        "decision_subject": decision_subject,
        "decision_result": decision_result,
        "confidence": "high",
        "evidence_refs": list(evidence_refs),
        "source_closure_status": effective_source_closure_status,
        "observer_version": observer_version,
        "policy": policy,
        "profile": profile,
        "mode": mode,
        "generated_at": generated_at,
        "report_digest": report_digest,
    }
    return {
        "audit_summary": {
            "audit_status": "blocked" if report.blockers else "passed",
            "blocker_count": len(report.blockers),
            "coverage_gap_count": len(report.coverage_gaps),
            "formal_outputs": ["constraint_report", "decision_summary"],
        },
        "gate_decision_payload": gate_decision_payload,
        "advisories": tuple(advisories),
    }


def collect_constraint_blockers(
    root: Path,
    *,
    profile: ConstraintProfile = ConstraintProfile.PROJECT,
) -> list[str]:
    """Return human-readable BLOCKER lines (empty list if none)."""
    blockers: list[str] = []
    cp = load_checkpoint(root)

    constitution = root / CONSTITUTION_REL
    if not constitution.is_file():
        blockers.append(
            "BLOCKER: missing required governance file "
            f"{CONSTITUTION_REL.as_posix()}"
        )

    blockers.extend(_formal_artifact_target_blockers(root))
    blockers.extend(_consumer_frontend_solution_confirmation_instruction_blockers(root))
    blockers.extend(collect_text_quality_blockers(root))
    if profile is ConstraintProfile.SELF_DEVELOPMENT:
        blockers.extend(_framework_defect_backlog_blockers(root))
        blockers.extend(_backlog_breach_reference_blockers(root))
        blockers.extend(_release_docs_consistency_blockers(root))
        blockers.extend(_readme_cli_path_blockers(root))
        blockers.extend(_beginner_guide_cli_path_blockers(root))
        blockers.extend(_agent_instruction_cli_path_blockers(root))
        blockers.extend(_adapter_template_cli_path_blockers(root))
        blockers.extend(_framework_frontend_instruction_consistency_blockers(root))
        blockers.extend(_adapter_template_comment_policy_blockers(root))
        blockers.extend(_reconcile_smoke_contract_blockers(root))
        blockers.extend(_doc_first_surface_blockers(root))
        blockers.extend(_verification_profile_blockers(root))
        blockers.extend(_feature_regression_guard_blockers(root))

    if cp is None or cp.feature is None:
        return _dedupe_text_items(blockers)

    spec_dir_raw = (cp.feature.spec_dir or "").strip()
    if not spec_dir_raw or spec_dir_raw == "specs/unknown":
        return blockers

    spec_path = root / spec_dir_raw
    resolved = spec_path.resolve()
    if not resolved.is_dir():
        blockers.append(
            "BLOCKER: checkpoint feature.spec_dir is not an existing directory "
            f"({spec_dir_raw!r})"
        )
        return _dedupe_text_items(blockers)

    tasks_file = spec_path / "tasks.md"
    if tasks_file.is_file():
        content = tasks_file.read_text(encoding="utf-8")
        missing_id = first_task_missing_acceptance(content)
        if missing_id is not None:
            blockers.append(
                "BLOCKER: tasks.md missing task-level acceptance (SC-014; same rule as "
                f"gate decompose `task_acceptance_present`): first breach Task {missing_id}"
            )
        doc_first_violation = first_doc_first_task_scope_violation(content)
        if doc_first_violation is not None:
            task_id, path = doc_first_violation
            blockers.append(
                "BLOCKER: doc-first task "
                f"Task {task_id} still points at execute-only path {path}; "
                "keep doc-first work in design/decompose and out of code/tests"
            )

    blockers.extend(_skip_registry_mapping_blockers(root, spec_path, cp))
    blockers.extend(_branch_lifecycle_blockers(root, spec_path))
    if profile is ConstraintProfile.SELF_DEVELOPMENT:
        blockers.extend(_frontend_evidence_class_blockers(spec_path))
        blockers.extend(_feature_contract_blockers(root, cp))
    return _dedupe_text_items(blockers)


def _frontend_evidence_class_blockers(spec_dir: Path) -> list[str]:
    spec_path = spec_dir / "spec.md"
    if not spec_path.is_file() or not _is_frontend_evidence_class_subject(spec_dir.name):
        return []

    body, footer = _split_markdown_footer(spec_path.read_text(encoding="utf-8"))
    if footer is None:
        return [
            _frontend_evidence_class_authoring_blocker(
                spec_path=spec_path,
                error_kind="missing_footer_key",
                human_remediation_hint=(
                    "add footer metadata with frontend_evidence_class to spec.md"
                ),
            )
        ]

    duplicate_count = len(_FRONTEND_EVIDENCE_CLASS_DUPLICATE_RE.findall(footer))
    if duplicate_count > 1:
        return [
            _frontend_evidence_class_authoring_blocker(
                spec_path=spec_path,
                error_kind="duplicate_key",
                human_remediation_hint=(
                    "keep exactly one frontend_evidence_class entry in the spec footer"
                ),
            )
        ]

    try:
        payload = yaml.safe_load(footer) or {}
    except yaml.YAMLError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    if FRONTEND_EVIDENCE_CLASS_KEY not in payload:
        return [
            _frontend_evidence_class_authoring_blocker(
                spec_path=spec_path,
                error_kind="missing_footer_key",
                human_remediation_hint=(
                    "declare frontend_evidence_class in the spec footer metadata"
                ),
            )
        ]

    value = payload.get(FRONTEND_EVIDENCE_CLASS_KEY)
    normalized_value = str(value).strip() if value is not None else ""
    if not normalized_value:
        return [
            _frontend_evidence_class_authoring_blocker(
                spec_path=spec_path,
                error_kind="empty_value",
                human_remediation_hint=(
                    "set frontend_evidence_class to framework_capability or consumer_adoption"
                ),
            )
        ]

    if normalized_value not in FRONTEND_EVIDENCE_CLASS_ALLOWED_VALUES:
        return [
            _frontend_evidence_class_authoring_blocker(
                spec_path=spec_path,
                error_kind="invalid_value",
                human_remediation_hint=(
                    "use frontend_evidence_class values framework_capability or consumer_adoption"
                ),
            )
        ]

    body_values = [
        match.group("value").strip()
        for match in _FRONTEND_EVIDENCE_CLASS_BODY_DECL_RE.finditer(body)
        if match.group("value").strip()
    ]
    if any(candidate != normalized_value for candidate in body_values):
        return [
            _frontend_evidence_class_authoring_blocker(
                spec_path=spec_path,
                error_kind="body_footer_conflict",
                human_remediation_hint=(
                    "align any body declaration with the canonical footer value"
                ),
            )
        ]

    return []


def collect_frontend_evidence_class_blockers(spec_dir: Path) -> list[str]:
    """Return WI-scoped frontend evidence authoring blockers for a specific spec dir."""
    return _frontend_evidence_class_blockers(spec_dir)


def _is_frontend_evidence_class_subject(spec_dir_name: str) -> bool:
    match = re.fullmatch(r"(?P<seq>\d{3})-(?P<slug>[a-z0-9-]+)", spec_dir_name.strip())
    if match is None:
        return False
    if int(match.group("seq")) < FRONTEND_EVIDENCE_CLASS_MIN_SEQUENCE:
        return False
    return "frontend" in match.group("slug").split("-")


def _split_markdown_footer(text: str) -> tuple[str, str | None]:
    return split_terminal_markdown_footer(text)


def split_terminal_markdown_footer(text: str) -> tuple[str, str | None]:
    stripped = text.rstrip()
    lines = stripped.splitlines()
    delimiter_indexes = _markdown_footer_delimiter_indexes(lines)
    if len(delimiter_indexes) < 2 or delimiter_indexes[-1] != len(lines) - 1:
        return stripped, None

    opening_index = delimiter_indexes[-2]
    body = "\n".join(lines[:opening_index]).rstrip()
    footer = "\n".join(lines[opening_index + 1 : -1])
    return body, footer


def _markdown_footer_delimiter_indexes(lines: list[str]) -> list[int]:
    indexes: list[int] = []
    active_fence: tuple[str, int] | None = None

    for index, line in enumerate(lines):
        fence = _markdown_fence_marker(line)
        if active_fence is None:
            if fence is not None:
                active_fence = fence
                continue
            if line == "---":
                indexes.append(index)
            continue

        if _markdown_fence_closes(line, active_fence):
            active_fence = None

    return indexes


def _markdown_fence_marker(line: str) -> tuple[str, int] | None:
    stripped = line.lstrip(" \t")
    if not stripped:
        return None
    if stripped.startswith("```"):
        return ("`", len(stripped) - len(stripped.lstrip("`")))
    if stripped.startswith("~~~"):
        return ("~", len(stripped) - len(stripped.lstrip("~")))
    return None


def _markdown_fence_closes(line: str, active_fence: tuple[str, int]) -> bool:
    fence_char, fence_len = active_fence
    stripped = line.lstrip(" \t").rstrip()
    if not stripped or stripped[0] != fence_char:
        return False

    run_len = len(stripped) - len(stripped.lstrip(fence_char))
    return run_len >= fence_len and not stripped[run_len:].strip()


def _frontend_evidence_class_authoring_blocker(
    *,
    spec_path: Path,
    error_kind: str,
    human_remediation_hint: str,
) -> str:
    return (
        "BLOCKER: "
        f"problem_family={FRONTEND_EVIDENCE_CLASS_PROBLEM_FAMILY} "
        "detection_surface=verify constraints "
        f"spec_path={spec_path.as_posix()} "
        f"error_kind={error_kind} "
        f"source_of_truth_path={spec_path.as_posix()}#footer "
        f"expected_contract_ref={FRONTEND_EVIDENCE_CLASS_CONTRACT_REF} "
        f"human_remediation_hint={human_remediation_hint}"
    )


def _branch_lifecycle_blockers(root: Path, spec_path: Path) -> list[str]:
    """Return blockers for unresolved active-work-item branch lifecycle drift."""
    if not (root / ".git").exists():
        return []

    exec_log = spec_path / "task-execution-log.md"
    log_text = exec_log.read_text(encoding="utf-8") if exec_log.is_file() else None
    try:
        result = evaluate_work_item_branch_lifecycle(
            root=root,
            wi_dir=spec_path,
            log_text=log_text,
            _require_final_branch_disposition=False,
        )
    except GitError:
        return []
    return _dedupe_text_items(list(result.blockers))


def _feature_contract_blockers(root: Path, checkpoint: Checkpoint | None) -> list[str]:
    """Validate the active work-item feature-contract surfaces."""
    gaps = _feature_contract_coverage_gaps(root, checkpoint)
    if not gaps:
        return []

    work_item_id = _effective_feature_contract_wi_id(checkpoint)
    return _dedupe_text_items(
        [
            "BLOCKER: "
            f"{work_item_id or 'active work item'} feature-contract surface missing: {gap}"
            for gap in gaps
        ]
    )


def _feature_contract_coverage_gaps(
    root: Path,
    checkpoint: Checkpoint | None,
) -> tuple[str, ...]:
    """Return missing feature-contract coverage labels for the active work item."""
    if checkpoint is None or checkpoint.feature is None:
        return ()

    surfaces = _feature_contract_surfaces_for_checkpoint(checkpoint)
    if not surfaces:
        return ()

    gaps = [
        surface.label
        for surface in surfaces
        if not _feature_contract_surface_present(root, surface)
    ]
    return tuple(gaps)






















































def _feature_contract_surfaces_for_checkpoint(
    checkpoint: Checkpoint | None,
) -> tuple[FeatureContractSurface, ...]:
    """Return the work-item-scoped feature-contract registry."""
    if _is_framework_003_feature_contract_work_item(checkpoint):
        return FEATURE_CONTRACT_SURFACES["003"]
    work_item_id = _effective_feature_contract_wi_id(checkpoint)
    if _is_189_work_item(work_item_id):
        return FEATURE_CONTRACT_SURFACES["189"]
    if _is_190_work_item(work_item_id):
        return FEATURE_CONTRACT_SURFACES["190"]
    if _is_191_work_item(work_item_id):
        return FEATURE_CONTRACT_SURFACES["191"]
    if _is_192_work_item(work_item_id):
        return FEATURE_CONTRACT_SURFACES["192"]
    if _is_193_work_item(work_item_id):
        return FEATURE_CONTRACT_SURFACES["193"]
    if _is_194_work_item(work_item_id):
        return FEATURE_CONTRACT_SURFACES["194"]
    if _is_195_work_item(work_item_id):
        return FEATURE_CONTRACT_SURFACES["195"]
    return ()


def _feature_contract_surface_present(
    root: Path,
    surface: FeatureContractSurface,
) -> bool:
    """Return True when all required evidence entries are present."""
    return all(
        _feature_contract_evidence_present(root, evidence)
        for evidence in surface.evidence_entries
    )


def _feature_contract_evidence_present(
    root: Path,
    evidence: FeatureContractEvidence,
) -> bool:
    """Return True when one evidence entry's required tokens exist in a file."""
    for rel in evidence.relative_paths:
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if all(token in text for token in evidence.required_tokens):
            return True
    return False


def _is_framework_003_feature_contract_work_item(
    checkpoint: Checkpoint | None,
) -> bool:
    """Return True only for Ai_AutoSDLC's framework-owned 003 contract WI."""
    if checkpoint is None or checkpoint.feature is None:
        return False

    canonical = FRAMEWORK_003_FEATURE_CONTRACT_WORK_ITEM_ID
    return _effective_wi_id_for_registry(checkpoint).strip() == canonical


















def _is_189_work_item(work_item_id: str) -> bool:
    normalized = work_item_id.strip()
    return normalized == "189" or normalized.startswith("189-") or normalized.startswith("189/")


def _is_190_work_item(work_item_id: str) -> bool:
    normalized = work_item_id.strip()
    return normalized == "190" or normalized.startswith("190-") or normalized.startswith("190/")


def _is_191_work_item(work_item_id: str) -> bool:
    normalized = work_item_id.strip()
    return normalized == "191" or normalized.startswith("191-") or normalized.startswith("191/")


def _is_192_work_item(work_item_id: str) -> bool:
    normalized = work_item_id.strip()
    return normalized == "192" or normalized.startswith("192-") or normalized.startswith("192/")


def _is_193_work_item(work_item_id: str) -> bool:
    normalized = work_item_id.strip()
    return normalized == "193" or normalized.startswith("193-") or normalized.startswith("193/")


def _is_194_work_item(work_item_id: str) -> bool:
    normalized = work_item_id.strip()
    return normalized == "194" or normalized.startswith("194-") or normalized.startswith("194/")


def _is_195_work_item(work_item_id: str) -> bool:
    normalized = work_item_id.strip()
    return normalized == "195" or normalized.startswith("195-") or normalized.startswith("195/")


def _effective_feature_contract_wi_id(checkpoint: Checkpoint | None) -> str:
    """Resolve the active work-item id for feature-contract coverage."""
    if checkpoint is None:
        return ""
    return _effective_wi_id_for_registry(checkpoint)


def _merge_unique_strings(
    primary: tuple[str, ...],
    secondary: tuple[str, ...],
) -> tuple[str, ...]:
    merged: list[str] = []
    for item in (*primary, *secondary):
        if item and item not in merged:
            merged.append(item)
    return tuple(merged)






def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    items: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            items.append(text)
    return tuple(items)


def _optional_str(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _coerce_bool(value: object, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    return default


def _parse_bool_literal(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    return None


def _load_yaml_mapping(path: Path) -> dict[str, object]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML syntax: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("expected top-level YAML mapping")
    return payload


def _load_json_mapping(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON syntax: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("expected top-level JSON mapping")
    return payload












def _doc_first_surface_blockers(root: Path) -> list[str]:
    """Validate the repo-wide rule surfaces for doc-first / requirements-first flow."""
    present_texts = {
        rel: (root / rel).read_text(encoding="utf-8")
        for rel in DOC_FIRST_SURFACES
        if (root / rel).is_file()
    }
    if not present_texts:
        return []
    if not any(
        any(token in text for token in DOC_FIRST_ACTIVATION_TOKENS)
        for text in present_texts.values()
    ):
        return []

    blockers: list[str] = []
    for rel, required_tokens in DOC_FIRST_SURFACES.items():
        path = root / rel
        if not path.is_file():
            blockers.append(
                "BLOCKER: doc-first rule surface missing: " f"{rel.as_posix()}"
            )
            continue
        text = path.read_text(encoding="utf-8")
        missing = [token for token in required_tokens if token not in text]
        if missing:
            blockers.append(
                "BLOCKER: doc-first rule surface "
                f"{rel.as_posix()} missing required markers: {', '.join(missing)}"
            )
    return blockers


def _verification_profile_blockers(root: Path) -> list[str]:
    """Validate docs-only / rules-only / code-change profile docs when surfaces exist."""
    if not any(
        (root / rel).is_file() for rel in VERIFICATION_PROFILE_ACTIVATION_SURFACES
    ):
        return []

    blockers: list[str] = []
    for rel, required_tokens in VERIFICATION_PROFILE_SURFACES.items():
        path = root / rel
        if not path.is_file():
            blockers.append(
                "BLOCKER: verification profile surface missing: " f"{rel.as_posix()}"
            )
            continue
        text = path.read_text(encoding="utf-8")
        missing = [token for token in required_tokens if token not in text]
        if missing:
            blockers.append(
                "BLOCKER: verification profile surface "
                f"{rel.as_posix()} missing required markers: {', '.join(missing)}"
            )
    return blockers


def _feature_regression_guard_blockers(root: Path) -> list[str]:
    """Validate that delivery rules protect existing user-visible behavior."""

    if not any(
        (root / rel).is_file() for rel in FEATURE_REGRESSION_GUARD_ACTIVATION_SURFACES
    ):
        return []

    blockers: list[str] = []
    for rel, required_tokens in FEATURE_REGRESSION_GUARD_SURFACES.items():
        path = root / rel
        if not path.is_file():
            blockers.append(
                "BLOCKER: feature regression guard surface missing: "
                f"{rel.as_posix()}"
            )
            continue
        text = path.read_text(encoding="utf-8")
        missing = [token for token in required_tokens if token not in text]
        if missing:
            blockers.append(
                "BLOCKER: feature regression guard surface "
                f"{rel.as_posix()} missing required markers: {', '.join(missing)}"
            )
    return blockers


def _reconcile_smoke_contract_blockers(root: Path) -> list[str]:
    """Validate repo-state reconcile diagnostic contract across CLI and workflow."""
    activation_surfaces = (
        CLI_COMMANDS_REL,
        WINDOWS_OFFLINE_SMOKE_WORKFLOW_REL,
    )
    if not any((root / rel).is_file() for rel in activation_surfaces):
        return []

    blockers: list[str] = []
    for rel, required_tokens in RECONCILE_SMOKE_CONTRACT_SURFACES.items():
        path = root / rel
        if not path.is_file():
            blockers.append(
                "BLOCKER: reconcile smoke contract missing required surface: "
                f"{rel.as_posix()}"
            )
            continue
        text = path.read_text(encoding="utf-8")
        missing = [token for token in required_tokens if token not in text]
        if missing:
            blockers.append(
                "BLOCKER: reconcile smoke contract drift: "
                f"{rel.as_posix()} missing required markers: "
                f"{', '.join(missing)}"
            )
    return blockers


def _framework_defect_backlog_blockers(root: Path) -> list[str]:
    """Validate the repo-local framework backlog structure when present."""
    path = root / FRAMEWORK_DEFECT_BACKLOG_REL
    if not path.is_file():
        return []

    entries = _parse_framework_defect_backlog(path.read_text(encoding="utf-8"))
    blockers: list[str] = []
    for title, fields in entries:
        missing = [
            name
            for name in FRAMEWORK_DEFECT_BACKLOG_REQUIRED_FIELDS
            if not fields.get(_norm_framework_backlog_key(name), "").strip()
        ]
        if missing:
            blockers.append(
                "BLOCKER: framework-defect-backlog entry "
                f"{title!r} missing required fields: {', '.join(missing)}"
            )
    return _dedupe_text_items(blockers)


def _formal_artifact_target_blockers(root: Path) -> list[str]:
    """Report misplaced formal artifacts found under docs/superpowers/*."""
    blockers: list[str] = []
    for violation in detect_misplaced_formal_artifacts(root):
        blockers.append(
            "BLOCKER: misplaced formal artifact detected under docs/superpowers/*: "
            f"{violation.path} ({violation.artifact_kind})"
        )
    return _dedupe_text_items(blockers)


def _backlog_breach_reference_blockers(root: Path) -> list[str]:
    """Block when specs reference FD ids that have no backlog entry."""
    blockers: list[str] = []
    for violation in collect_missing_backlog_entry_references(root):
        blockers.append(
            "BLOCKER: breach_detected_but_not_logged: "
            f"{violation.path} references missing backlog ids: "
            f"{', '.join(violation.missing_ids)}"
        )
    return _dedupe_text_items(blockers)


def _pyproject_version(root: Path) -> str | None:
    path = root / PYPROJECT_REL
    if not path.is_file():
        return None
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return None
    version = payload.get("project", {}).get("version")
    if not isinstance(version, str):
        return None
    return version.strip() or None


def _package_init_fallback_version(root: Path) -> str | None:
    path = root / PACKAGE_INIT_REL
    if not path.is_file():
        return None
    match = re.search(
        r'__version__\s*=\s*["\'](?P<version>[^"\']+)["\']',
        path.read_text(encoding="utf-8"),
    )
    if not match:
        return None
    return match.group("version").strip() or None


def _release_version_truth_blockers(root: Path) -> list[str]:
    expected_version = "2.0.0"
    blockers: list[str] = []
    pyproject_version = _pyproject_version(root)
    if pyproject_version and pyproject_version != expected_version:
        blockers.append(
            "BLOCKER: release version truth drift: "
            f"{PYPROJECT_REL.as_posix()} project.version is {pyproject_version}, "
            f"expected {expected_version}"
        )
    package_init_version = _package_init_fallback_version(root)
    if package_init_version and package_init_version != expected_version:
        blockers.append(
            "BLOCKER: release version truth drift: "
            f"{PACKAGE_INIT_REL.as_posix()} fallback __version__ is "
            f"{package_init_version}, expected {expected_version}"
        )
    return blockers


def _release_docs_consistency_blockers(root: Path) -> list[str]:
    """Validate the fixed release entry docs for the current staged release."""
    activation_surfaces = (
        README_REL,
        USER_GUIDE_REL,
        OFFLINE_README_REL,
        RELEASE_POLICY_REL,
    )
    if not any((root / rel).is_file() for rel in activation_surfaces):
        return []

    blockers = _release_version_truth_blockers(root)
    for rel, required_tokens in RELEASE_DOCS_CONSISTENCY_SURFACES.items():
        path = root / rel
        if not path.is_file():
            blockers.append(
                "BLOCKER: release docs consistency missing required entry doc: "
                f"{rel.as_posix()}"
            )
            continue
        text = path.read_text(encoding="utf-8")
        missing = [token for token in required_tokens if token not in text]
        if missing:
            blockers.append(
                "BLOCKER: release docs consistency drift: "
                f"{rel.as_posix()} missing required markers: {', '.join(missing)}"
            )
    return blockers


def _beginner_guide_cli_path_blockers(root: Path) -> list[str]:
    """Block beginner docs that regress to the old multi-command setup path."""
    path = root / USER_GUIDE_REL
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")

    blockers: list[str] = []
    missing = [token for token in BEGINNER_GUIDE_REQUIRED_TOKENS if token not in text]
    if missing:
        blockers.append(
            "BLOCKER: beginner guide CLI path missing required current-flow markers: "
            f"{', '.join(missing)}"
        )
    missing_existing_init = [
        token
        for token in BEGINNER_GUIDE_EXISTING_PROJECT_INIT_TOKENS
        if token not in text
    ]
    if missing_existing_init:
        blockers.append(
            "BLOCKER: beginner guide existing-project init path is not copyable "
            "from the offline bundle directory: "
            f"{', '.join(missing_existing_init)}"
        )
    forbidden = [token for token in BEGINNER_GUIDE_FORBIDDEN_TOKENS if token in text]
    if forbidden:
        blockers.append(
            "BLOCKER: beginner guide CLI path contains out-of-scope content: "
            f"{', '.join(forbidden)}"
        )
    return blockers


def _readme_cli_path_blockers(root: Path) -> list[str]:
    """Block README drift away from the init-first beginner CLI path."""
    path = root / README_REL
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")

    blockers: list[str] = []
    missing = [token for token in README_CLI_PATH_REQUIRED_TOKENS if token not in text]
    if missing:
        blockers.append(
            "BLOCKER: README CLI path missing required current-flow markers: "
            f"{', '.join(missing)}"
        )
    forbidden = [token for token in README_CLI_PATH_FORBIDDEN_TOKENS if token in text]
    if forbidden:
        blockers.append(
            "BLOCKER: README CLI path regressed to old manual setup steps: "
            f"{', '.join(forbidden)}"
        )
    return blockers


def _agent_instruction_cli_path_blockers(root: Path) -> list[str]:
    """Keep canonical agent instructions aligned with the beginner CLI path."""
    if not any((root / rel).is_file() for rel in ADAPTER_TEMPLATE_CLI_PATH_RELS):
        return []
    path = root / AGENTS_REL
    if not path.is_file():
        return [
            "BLOCKER: AGENTS.md CLI path missing while adapter templates are present"
        ]
    text = path.read_text(encoding="utf-8")

    blockers: list[str] = []
    missing = [token for token in AGENTS_CLI_PATH_REQUIRED_TOKENS if token not in text]
    if missing:
        blockers.append(
            "BLOCKER: AGENTS.md CLI path missing required current-flow markers: "
            f"{', '.join(missing)}"
        )
    forbidden = [token for token in AGENTS_CLI_PATH_FORBIDDEN_TOKENS if token in text]
    if forbidden:
        blockers.append(
            "BLOCKER: AGENTS.md CLI path regressed to old manual startup steps: "
            f"{', '.join(forbidden)}"
        )
    return blockers


def _adapter_template_cli_path_blockers(root: Path) -> list[str]:
    """Keep generated adapter instructions aligned with AGENTS.md guidance."""
    blockers: list[str] = []
    for rel in ADAPTER_TEMPLATE_CLI_PATH_RELS:
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        missing = [token for token in AGENTS_CLI_PATH_REQUIRED_TOKENS if token not in text]
        if missing:
            blockers.append(
                "BLOCKER: adapter template CLI path missing required current-flow "
                f"markers in {rel.as_posix()}: {', '.join(missing)}"
            )
        forbidden = [token for token in AGENTS_CLI_PATH_FORBIDDEN_TOKENS if token in text]
        if forbidden:
            blockers.append(
                "BLOCKER: adapter template CLI path regressed to old manual startup "
                f"steps in {rel.as_posix()}: {', '.join(forbidden)}"
            )
    return blockers


def _frontend_solution_confirmation_instruction_blockers(root: Path) -> list[str]:
    """Keep frontend implementation blocked until the user confirms the stack."""
    return _frontend_solution_confirmation_instruction_blockers_for_rels(
        root,
        FRONTEND_SOLUTION_CONFIRMATION_RELS,
    )


def _consumer_frontend_solution_confirmation_instruction_blockers(
    root: Path,
) -> list[str]:
    """Validate the installed project instruction without framework source files."""
    return _frontend_solution_confirmation_instruction_blockers_for_rels(
        root,
        (AGENTS_REL,),
    )


def _framework_frontend_instruction_consistency_blockers(root: Path) -> list[str]:
    """Validate framework rules and generated adapter templates."""
    return _frontend_solution_confirmation_instruction_blockers_for_rels(
        root,
        (PIPELINE_RULE_REL, *ADAPTER_TEMPLATE_CLI_PATH_RELS),
    )


def _frontend_solution_confirmation_instruction_blockers_for_rels(
    root: Path,
    rels: tuple[Path, ...],
) -> list[str]:
    """Validate frontend confirmation markers for selected instruction files."""
    existing_rels = [
        rel for rel in rels if (root / rel).is_file()
    ]
    if not existing_rels:
        return []
    has_adapter_or_agents = any(rel != PIPELINE_RULE_REL for rel in existing_rels)
    if not has_adapter_or_agents:
        pipeline_text = (root / PIPELINE_RULE_REL).read_text(encoding="utf-8")
        if "前端需求" not in pipeline_text and "frontend" not in pipeline_text.lower():
            return []

    blockers: list[str] = []
    for rel in existing_rels:
        path = root / rel
        text = path.read_text(encoding="utf-8")
        missing = [
            token
            for token in FRONTEND_SOLUTION_CONFIRMATION_REQUIRED_TOKENS
            if token not in text
        ]
        if missing:
            blockers.append(
                "BLOCKER: frontend solution confirmation instruction drift in "
                f"{rel.as_posix()}: {', '.join(missing)}"
            )
        forbidden = [
            token
            for token in FRONTEND_SOLUTION_CONFIRMATION_FORBIDDEN_TOKENS
            if token in text
        ]
        if forbidden:
            blockers.append(
                "BLOCKER: frontend solution confirmation instruction has stale "
                f"default tooling in {rel.as_posix()}: {', '.join(forbidden)}"
            )
    return blockers


def _adapter_template_comment_policy_blockers(root: Path) -> list[str]:
    """Keep generated adapter instructions honest about maintainability comments."""
    blockers: list[str] = []
    for rel in ADAPTER_TEMPLATE_COMMENT_POLICY_RELS:
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        missing = [
            token
            for token in ADAPTER_TEMPLATE_COMMENT_POLICY_REQUIRED_TOKENS
            if token not in text
        ]
        if missing:
            blockers.append(
                "BLOCKER: adapter template comment policy missing required "
                f"markers in {rel.as_posix()}: {', '.join(missing)}"
            )
    return blockers


def _parse_framework_defect_backlog(text: str) -> list[tuple[str, dict[str, str]]]:
    """Parse `##` entries and `- key: value` field lines from the backlog doc."""
    entries: list[tuple[str, dict[str, str]]] = []
    current_title = ""
    current_fields: dict[str, str] = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("## FD-"):
            if current_title:
                entries.append((current_title, current_fields))
            current_title = line[3:].strip()
            current_fields = {}
            continue

        if not current_title or not line.startswith("- "):
            continue

        key, sep, value = line[2:].partition(":")
        if not sep:
            continue
        current_fields[_norm_framework_backlog_key(key)] = value.strip()

    if current_title:
        entries.append((current_title, current_fields))
    return entries


def _norm_framework_backlog_key(key: str) -> str:
    return re.sub(r"\s+", " ", key.strip().lower())


def _effective_wi_id_for_registry(cp: Checkpoint) -> str:
    """Prefer linked_wi_id, otherwise use the feature.spec_dir basename."""
    linked = (cp.linked_wi_id or "").strip()
    if linked:
        return linked
    sd = (cp.feature.spec_dir or "").strip()
    if sd:
        return Path(sd).name
    return ""


def _norm_header_cell(cell: str) -> str:
    return re.sub(r"\*+", "", cell.strip()).strip().lower()


def _is_separator_row(parts: list[str]) -> bool:
    if not parts:
        return False
    for p in parts:
        t = p.strip().replace(" ", "")
        if not t:
            continue
        if not re.fullmatch(r":?-{3,}:?", t):
            return False
    return any(p.strip() for p in parts)


def _scoped_skip_registry_lines(reg_text: str, effective_wi_id: str) -> list[str]:
    """Lines from pipe tables whose header includes wi_id and row wi_id matches."""
    if not effective_wi_id:
        return []

    wi_id_idx: int | None = None
    past_separator = False
    scoped: list[str] = []

    for line in reg_text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        parts = [c.strip() for c in s.strip().strip("|").split("|")]

        if wi_id_idx is None:
            for i, cell in enumerate(parts):
                if _norm_header_cell(cell) == "wi_id":
                    wi_id_idx = i
                    break
            continue

        if _is_separator_row(parts):
            past_separator = True
            continue

        if not past_separator:
            continue

        if len(parts) <= wi_id_idx:
            continue

        raw_wi = parts[wi_id_idx].strip().strip("`").strip()
        if not raw_wi or raw_wi != effective_wi_id:
            continue
        scoped.append(s)

    return _dedupe_text_items(scoped)


def _skip_registry_mapping_blockers(
    root: Path, spec_dir: Path, cp: Checkpoint
) -> list[str]:
    """Include only registry rows with the matching wi_id."""
    registry = root / SKIP_REGISTRY_REL
    if not registry.is_file():
        return []

    effective = _effective_wi_id_for_registry(cp)
    reg_text = registry.read_text(encoding="utf-8")
    scoped_lines = _scoped_skip_registry_lines(reg_text, effective)
    if not scoped_lines:
        return []

    scoped_blob = "\n".join(scoped_lines)
    fr_refs = sorted(set(re.findall(r"\bFR-\d{3}\b", scoped_blob)))
    task_refs = sorted(set(re.findall(r"\bTask\s+\d+\.\d+\b", scoped_blob)))

    spec_text = (spec_dir / "spec.md").read_text(encoding="utf-8") if (spec_dir / "spec.md").is_file() else ""
    tasks_text = (spec_dir / "tasks.md").read_text(encoding="utf-8") if (spec_dir / "tasks.md").is_file() else ""
    mapped_text = spec_text + "\n" + tasks_text

    unmapped_fr = [x for x in fr_refs if x not in mapped_text]
    unmapped_tasks = [x for x in task_refs if x not in tasks_text]
    if not unmapped_fr and not unmapped_tasks:
        return []

    details: list[str] = []
    if unmapped_fr:
        details.append("FR: " + ", ".join(unmapped_fr[:10]))
    if unmapped_tasks:
        details.append("Task: " + ", ".join(unmapped_tasks[:10]))
    return [
        "BLOCKER: skip-registry contains unmapped references not found in current "
        f"spec/tasks ({'; '.join(details)})"
    ]
