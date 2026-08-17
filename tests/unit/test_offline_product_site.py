import json
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from scripts.validate_offline_product_site import (
    build_manifest,
    validate_guide_parity,
    validate_site,
    validate_video_config,
)

TOP_LEVEL_PAGES = (
    "index.html",
    "loop-engineering.html",
    "dynamic-expert-review.html",
    "platform-capabilities.html",
    "downloads-docs.html",
)


class _HomeValueParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None
        self._stack: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = {key: value or "" for key, value in attrs}
        self._stack.append((tag, attributes))
        classes = set((attributes.get("class") or "").split())
        if tag == "article" and "home-value" in classes:
            self._current = {"title": [], "description": [], "links": []}
            self.items.append(self._current)
        elif self._current is not None and tag == "a":
            link = {"href": attributes.get("href"), "text_parts": []}
            self._current["links"].append(link)

    def handle_data(self, data: str) -> None:
        if self._current is None or any(
            attributes.get("aria-hidden") == "true"
            for _, attributes in self._stack
        ):
            return
        if any(tag == "h3" for tag, _ in self._stack):
            self._current["title"].append(data)
        elif any(tag == "a" for tag, _ in self._stack):
            self._current["links"][-1]["text_parts"].append(data)
        elif any(
            tag == "p"
            and "home-value-label" not in attributes.get("class", "").split()
            for tag, attributes in self._stack
        ):
            self._current["description"].append(data)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, -1, -1):
            closing_tag, attributes = self._stack[index]
            if closing_tag != tag:
                continue
            if tag == "article" and "home-value" in attributes.get(
                "class", ""
            ).split():
                self._current = None
            del self._stack[index:]
            break


class _HtmlNode:
    def __init__(
        self,
        tag: str,
        attributes: dict[str, str] | None = None,
        parent: "_HtmlNode | None" = None,
    ) -> None:
        self.tag = tag
        self.attributes = attributes or {}
        self.parent = parent
        self.children: list[_HtmlNode] = []
        self.content: list[str | _HtmlNode] = []


class _DocumentParser(HTMLParser):
    _VOID_TAGS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _HtmlNode("document")
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _HtmlNode(
            tag,
            {key: value or "" for key, value in attrs},
            self._stack[-1],
        )
        self._stack[-1].children.append(node)
        self._stack[-1].content.append(node)
        if tag not in self._VOID_TAGS:
            self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].content.append(data)


def _normalize_html_text(parts: list[str]) -> str:
    return " ".join("".join(parts).split())


def _parse_document(markup: str) -> _HtmlNode:
    parser = _DocumentParser()
    parser.feed(markup)
    parser.close()
    return parser.root


def _find_nodes(
    root: _HtmlNode,
    *,
    tag: str | None = None,
    attribute: str | None = None,
    value: str | None = None,
) -> list[_HtmlNode]:
    found: list[_HtmlNode] = []
    pending = [root]
    while pending:
        node = pending.pop()
        matches_tag = tag is None or node.tag == tag
        matches_attribute = attribute is None or attribute in node.attributes
        matches_value = value is None or node.attributes.get(attribute or "") == value
        if matches_tag and matches_attribute and matches_value:
            found.append(node)
        pending.extend(reversed(node.children))
    return found


def _node_text(node: _HtmlNode) -> str:
    parts = [
        item if isinstance(item, str) else _node_text(item) for item in node.content
    ]
    return _normalize_html_text(parts)


def _swap_node_content(left: _HtmlNode, right: _HtmlNode) -> None:
    left.content, right.content = right.content, left.content
    left.children, right.children = right.children, left.children
    for child in left.children:
        child.parent = left
    for child in right.children:
        child.parent = right


def _is_descendant(node: _HtmlNode, ancestor: _HtmlNode) -> bool:
    current = node.parent
    while current is not None:
        if current is ancestor:
            return True
        current = current.parent
    return False


LOOP_PANEL_CONTRACTS = {
    "requirement-panel": {
        "data-loop-input": ("目标", "范围", "验收标准"),
        "data-loop-state": ("needs_user", "决定"),
        "data-loop-feedback": ("唯一下一步", "用户"),
        "data-loop-close": ("未漂移", "Requirement Freeze"),
        "data-loop-evidence": (
            "objective",
            "acceptance criteria",
            "input_digest",
            "review_snapshot",
        ),
    },
    "design-contract-panel": {
        "data-loop-input": ("冻结需求", "接口", "数据边界", "技术栈", "实现约束"),
        "data-loop-state": ("needs_fix", "设计覆盖", "任务映射"),
        "data-loop-feedback": ("Findings", "原 Writer", "覆盖报告"),
        "data-loop-close": ("工件彼此一致", "关键边界", "复核输入保持不变"),
        "data-loop-evidence": ("spec.md", "plan.md", "tasks.md", "coverage / report"),
    },
    "implementation-panel": {
        "data-loop-input": ("当前候选", "required tasks", "允许修改", "验证命令"),
        "data-loop-state": ("blocked task", "needs_fix", "needs_review"),
        "data-loop-feedback": ("原 Writer", "测试", "lint", "build", "项目验证"),
        "data-loop-close": ("required tasks", "当前候选证据", "独立复核"),
        "data-loop-evidence": (
            "required tasks",
            "verification-evidence.json",
            "test / lint / build",
        ),
    },
    "frontend-evidence-panel": {
        "data-loop-input": ("browser entry", "目标页面", "交互路径", "证据身份"),
        "data-loop-state": ("needs_review", "浏览器证据", "独立核验"),
        "data-loop-feedback": ("原 Writer", "Browser Gate"),
        "data-loop-close": ("页面", "交互", "错误证据", "身份匹配", "时效漂移"),
        "data-loop-evidence": (
            "browser entry",
            "interactions",
            "console / page errors",
            "screenshots",
        ),
    },
}


EXPERT_RISK_CONTRACTS = {
    "review-requirement-panel": {
        "data-primary-risk": "范围与验收标准是否清楚、可执行、可证明。",
        "data-primary-expert": "scope and acceptance expert",
        "data-cross-risk": "没有明确交叉风险时不增加 Cross-risk Expert。",
        "data-example-finding": (
            "“可用”没有对应可执行验收场景，失败路径也未进入范围。"
        ),
    },
    "review-design-panel": {
        "data-primary-risk": "接口与边界是否覆盖成功、失败和状态漂移。",
        "data-primary-expert": "interface and boundary expert",
        "data-cross-risk": "接口触及权限边界时，才条件加入安全交叉风险。",
        "data-example-finding": (
            "接口只定义成功返回，失败语义与权限边界没有对应任务。"
        ),
    },
    "review-implementation-panel": {
        "data-primary-risk": "实现行为、错误处理与回归面是否符合当前合同。",
        "data-primary-expert": "behavior and regression expert",
        "data-cross-risk": "错误处理改变用户可见状态时，才加入前端交叉风险。",
        "data-example-finding": (
            "异常分支吞掉错误，现有测试只覆盖成功路径，无法证明回归未发生。"
        ),
    },
    "review-frontend-panel": {
        "data-primary-risk": "交互结果与证据身份是否绑定当前页面和当前候选。",
        "data-primary-expert": "interaction and evidence-identity expert",
        "data-cross-risk": "交互包含授权决定时，才条件加入安全边界交叉风险。",
        "data-example-finding": (
            "截图来自旧 digest，且没有记录提交交互后的 console / page error。"
        ),
    },
    "review-pr-panel": {
        "data-primary-risk": "跨阶段回归是否穿透需求、设计、实现和当前证据。",
        "data-primary-expert": "cross-stage regression expert",
        "data-cross-risk": (
            "Review Pack 同时触及独立风险面时，才加入对应交叉风险。"
        ),
        "data-example-finding": (
            "Review Pack 已改变 API，需求和前端证据仍引用旧响应合同。"
        ),
    },
}

EXPERT_GRAPH_IDENTITY = (
    "这是一张 Writer 与临时只读专家之间关系的说明性拓扑。"
    "它不是持久 Graph，不是图数据库，不是专家调度运行时或自主多 Agent Runtime，"
    "也不是第二套状态机；Loop 仍是唯一状态源。"
)

PLATFORM_PANEL_CONTRACTS = (
    (
        "tool-governance",
        "跨 AI 工具治理",
        ("换 Agent", "项目规则"),
        ("不是", "实时", "多 Agent"),
    ),
    (
        "continuity",
        "断点续作",
        ("中断", "分支", "下一步"),
        ("模型内部思维", "进程栈", "事务回滚"),
    ),
    (
        "frontend-delivery",
        "前端工程与验收",
        ("组件", "主题", "浏览器"),
        ("企业环境", "基础可访问性", "WCAG", "无需配置"),
    ),
    (
        "engineering-controls",
        "工程控制边界",
        ("严格", "工程判断"),
        ("硬性行数", "远程 AI Provider"),
    ),
)

PLATFORM_ADAPTER_PAIRS = (
    ("codex", "Codex", "AGENTS.md"),
    ("claude-code", "Claude Code", ".claude/CLAUDE.md"),
    ("cursor", "Cursor", ".cursor/rules/ai-sdlc.mdc"),
    ("copilot", "VS Code / Copilot", ".github/copilot-instructions.md"),
)

PLATFORM_CONTINUITY_CHAIN = (
    "checkpoint",
    "status",
    "handoff",
    "recover",
    "reconcile",
)

PLATFORM_FRONTEND_ROUTES = {
    "default": (
        "默认推荐",
        "vue3 / public-primevue / modern-saas",
        "新前端需求的首个推荐；仍需用户显式确认。",
    ),
    "advanced": (
        "高级可选 Style Packs",
        "vue3 / public-primevue",
        "仍属于 Vue3 / public-primevue 候选，不是 Vue2 compatibility path。",
    ),
    "vue2-compatibility": (
        "历史 / 企业兼容",
        "vue2 / enterprise-vue2",
        "只有明确历史 Vue2 或私有企业约束时选择；组件、网络与授权由企业环境提供。",
    ),
    "custom-evidence": (
        "自定义 / 无组件库",
        "custom / no component library",
        "兼容执行与证据路径，不是第三个内置 Provider。",
    ),
}

PLATFORM_STYLE_PACKS = (
    "enterprise-default",
    "data-console",
    "high-clarity",
    "macos-glass",
)

PLATFORM_BROWSER_CHAIN = (
    ("browser-entry", "真实 browser entry"),
    ("browser-gate", "Browser Gate"),
    ("frontend-evidence", "Frontend Evidence"),
)

PLATFORM_FRONTEND_RESPONSIBILITIES = {
    "project-e2e": "项目 E2E Suite 执行项目自己的测试策略与覆盖。",
    "frontend-evidence": (
        "Frontend Evidence 消费当前候选的 Browser Gate 工件，检查身份、时效、结构与 Loop 状态。"
    ),
}

PLATFORM_CONTROL_LANES = {
    "strict": ("identity", "evidence", "provenance", "security"),
    "advisory": ("Lean Code", "code simplification"),
}

PLATFORM_ROLE_VALUES = {
    "tool-governance": {
        "planning-handoff": "先读 canonical 规则、WorkItem 与 Loop 事实，再决定续作范围。",
        "development": "切换执行工具时仍按同一项目约束修改当前候选。",
        "testing-review": "按项目身份定位当前 evidence，不用聊天摘要替代验证。",
        "delivery-owner": "只接受由项目工件支持的 closed 状态，不接受工具自报完成。",
    },
    "continuity": {
        "planning-handoff": "对照 checkpoint、分支、开放门禁与下一步完成接手。",
        "development": "按 recover 路径续作；发现漂移时停止并显式 reconcile。",
        "testing-review": "恢复后只复核受影响的当前证据，不沿用过期结论。",
        "delivery-owner": "恢复链仍未关闭时阻止把中断任务误报为完成。",
    },
    "frontend-delivery": {
        "planning-handoff": "实现前确认 default、advanced、Vue2 compatibility 或 custom 路径。",
        "development": "按已确认的 Provider、Style Pack、Theme 与目录合同实现。",
        "testing-review": "只消费当前候选绑定的 Browser Gate 与项目 E2E 证据。",
        "delivery-owner": "Frontend Evidence 未关闭时阻止误报前端验收完成。",
    },
    "engineering-controls": {
        "planning-handoff": "高风险授权或关键输入缺失时取得用户决定，不替用户猜测。",
        "development": "严格项先补证据；Lean Code 建议结合行为与维护成本判断。",
        "testing-review": "核对当前候选的 identity、evidence 与 provenance 后再复核。",
        "delivery-owner": "严格门禁未关闭时阻止误报，advisory 建议不伪造阻断。",
    },
}


def _single_node(
    root: _HtmlNode,
    *,
    tag: str | None = None,
    attribute: str | None = None,
    value: str | None = None,
) -> _HtmlNode:
    nodes = _find_nodes(root, tag=tag, attribute=attribute, value=value)
    assert len(nodes) == 1
    return nodes[0]


def _assert_loop_panel_contract(document: _HtmlNode) -> None:
    for panel_id, region_contracts in LOOP_PANEL_CONTRACTS.items():
        panel = _single_node(document, tag="section", attribute="id", value=panel_id)
        for attribute, required_tokens in region_contracts.items():
            region = _single_node(panel, attribute=attribute)
            region_text = _node_text(region)
            missing = [token for token in required_tokens if token not in region_text]
            assert not missing, f"{panel_id} {attribute} missing {missing}"


def _assert_local_pr_review_contract(document: _HtmlNode) -> None:
    rail = _single_node(document, attribute="id", value="local-pr-review")
    step_list = _single_node(rail, tag="ol")
    steps = [child for child in step_list.children if child.tag == "li"]
    labels = [_node_text(_single_node(step, tag="strong")) for step in steps]
    assert labels == [
        "Review Pack",
        "Findings",
        "fix/rerun",
        "final report",
    ], f"Local PR Review steps out of contract: {labels}"


def _assert_expert_risk_contract(document: _HtmlNode) -> None:
    for panel_id, field_contracts in EXPERT_RISK_CONTRACTS.items():
        panel = _single_node(document, tag="section", attribute="id", value=panel_id)
        fields = _find_nodes(panel, attribute="data-review-value")
        assert len(fields) == 4, f"{panel_id} must bind exactly four risk values"
        for attribute, expected_text in field_contracts.items():
            field = _single_node(panel, attribute=attribute)
            actual = _node_text(_single_node(field, tag="dd"))
            assert actual == expected_text, (
                f"{panel_id} {attribute} stage contract mismatch: {actual}"
            )


def _assert_expert_graph_contract(document: _HtmlNode) -> None:
    graph = _single_node(document, tag="ol", attribute="data-expert-graph")
    steps = [child for child in graph.children if child.tag == "li"]
    assert [step.attributes.get("data-graph-node") for step in steps] == [
        "risk",
        "capability",
        "expert-routing",
        "isolation",
        "findings",
        "writer-fix",
        "rereview",
        "outcomes",
    ]

    routing = _single_node(
        graph, tag="li", attribute="data-graph-node", value="expert-routing"
    )
    branches = _single_node(routing, tag="ol", attribute="data-expert-branches")
    branch_nodes = [child for child in branches.children if child.tag == "li"]
    assert [node.attributes.get("data-expert-branch") for node in branch_nodes] == [
        "primary",
        "cross-risk",
    ]
    primary, cross_risk = branch_nodes
    assert primary.attributes.get("data-route") == "required"
    assert "Primary Expert · 必选主路径" in _node_text(primary)
    assert cross_risk.attributes.get("data-route") == "conditional", (
        "Cross-risk Expert must be conditional"
    )
    assert (
        "只有存在明确第二风险面时，才加入一名 Cross-risk Expert。"
        in _node_text(cross_risk)
    ), "Cross-risk Expert condition is incomplete"

    findings = _single_node(
        graph, tag="li", attribute="data-graph-node", value="findings"
    )
    assert findings.attributes.get("data-merge-from") == "primary cross-risk"
    assert "Primary 主路径与条件 Cross-risk 支路在 Findings 汇合" in _node_text(
        findings
    )

    writer_fix = _single_node(
        graph, tag="li", attribute="data-graph-node", value="writer-fix"
    )
    assert "Findings 回到原 Writer；修改权不转移" in _node_text(writer_fix)
    rereview = _single_node(
        graph, tag="li", attribute="data-graph-node", value="rereview"
    )
    assert "最多一次修复后复审" in _node_text(rereview)

    outcomes = _single_node(
        graph, tag="li", attribute="data-graph-node", value="outcomes"
    )
    outcome_list = _single_node(outcomes, tag="ul", attribute="data-review-outcomes")
    outcome_nodes = [child for child in outcome_list.children if child.tag == "li"]
    assert [node.attributes.get("data-review-outcome") for node in outcome_nodes] == [
        "close",
        "needs-review",
    ]
    close, stop = outcome_nodes
    assert close.attributes.get("data-route") == "conditions-met"
    assert "满足原 Loop 的 Close 条件时关闭" in _node_text(close)
    assert stop.attributes.get("data-route") == "expert-failure"
    assert "专家执行失败、超时或输出无效时，保持 needs_review 并 Stop" in _node_text(
        stop
    )


def _assert_platform_capability_contract(document: _HtmlNode) -> None:
    tablist = _single_node(document, tag="div", attribute="role", value="tablist")
    tabs = [child for child in tablist.children if child.attributes.get("role") == "tab"]
    assert [tab.attributes.get("id") for tab in tabs] == [
        contract[0] for contract in PLATFORM_PANEL_CONTRACTS
    ]
    assert [_node_text(tab) for tab in tabs] == [
        contract[1] for contract in PLATFORM_PANEL_CONTRACTS
    ]

    panels = _find_nodes(document, tag="section", attribute="data-tab-panel")
    assert len(panels) == 4
    for tab_id, _label, problem_tokens, boundary_tokens in PLATFORM_PANEL_CONTRACTS:
        panel = _single_node(
            document,
            tag="section",
            attribute="id",
            value=f"{tab_id}-panel",
        )
        assert panel.attributes.get("aria-labelledby") == tab_id
        for attribute, required_tokens in (
            ("data-capability-problem", problem_tokens),
            ("data-capability-boundary", boundary_tokens),
        ):
            regions = _find_nodes(panel, attribute=attribute)
            assert len(regions) == 1, f"{tab_id} must expose one {attribute} region"
            region_text = _node_text(regions[0])
            missing = [token for token in required_tokens if token not in region_text]
            assert not missing, f"{tab_id} {attribute} missing {missing}"

        results = _find_nodes(panel, attribute="data-capability-results")
        assert len(results) == 1, f"{tab_id} must expose one results region"
        role_map = _single_node(results[0], attribute="data-role-values")
        role_nodes = _find_nodes(role_map, attribute="data-role")
        expected_roles = PLATFORM_ROLE_VALUES[tab_id]
        assert [node.attributes["data-role"] for node in role_nodes] == list(
            expected_roles
        ), f"{tab_id} role order mismatch"
        actual_role_values = {
            node.attributes["data-role"]: _node_text(_single_node(node, tag="dd"))
            for node in role_nodes
        }
        assert actual_role_values == expected_roles, f"{tab_id} role value mismatch"

    tool_panel = _single_node(document, attribute="id", value="tool-governance-panel")
    adapter_rows = _find_nodes(tool_panel, attribute="data-adapter")
    actual_adapters = [
        (
            row.attributes["data-adapter"],
            _node_text(_single_node(row, attribute="data-adapter-tool")),
            _node_text(_single_node(row, attribute="data-adapter-path")),
        )
        for row in adapter_rows
    ]
    assert actual_adapters == list(PLATFORM_ADAPTER_PAIRS), (
        f"Adapter mapping mismatch: {actual_adapters}"
    )

    continuity_panel = _single_node(document, attribute="id", value="continuity-panel")
    continuity_steps = _find_nodes(continuity_panel, attribute="data-continuity-step")
    assert [step.attributes["data-continuity-step"] for step in continuity_steps] == (
        list(PLATFORM_CONTINUITY_CHAIN)
    ), "Continuity chain mismatch"
    assert [_node_text(step) for step in continuity_steps] == list(
        PLATFORM_CONTINUITY_CHAIN
    ), "Continuity chain labels mismatch"

    frontend_panel = _single_node(
        document, attribute="id", value="frontend-delivery-panel"
    )
    route_nodes = _find_nodes(frontend_panel, attribute="data-frontend-route")
    actual_routes = []
    for route in route_nodes:
        actual_routes.append(
            (
                route.attributes["data-frontend-route"],
                _node_text(_single_node(route, attribute="data-route-title")),
                _node_text(_single_node(route, attribute="data-route-stack")),
                _node_text(_single_node(route, attribute="data-route-boundary")),
            )
        )
    expected_routes = [
        (route_id, *contract)
        for route_id, contract in PLATFORM_FRONTEND_ROUTES.items()
    ]
    assert actual_routes == expected_routes, f"Frontend route mismatch: {actual_routes}"

    advanced = _single_node(
        frontend_panel,
        attribute="data-frontend-route",
        value="advanced",
    )
    style_packs = _find_nodes(advanced, attribute="data-style-pack")
    assert [node.attributes["data-style-pack"] for node in style_packs] == list(
        PLATFORM_STYLE_PACKS
    ), "Advanced Style Pack identity mismatch"
    assert [_node_text(node) for node in style_packs] == list(PLATFORM_STYLE_PACKS), (
        "Advanced Style Pack labels mismatch"
    )

    browser_chain = _single_node(
        frontend_panel, attribute="data-browser-evidence-chain"
    )
    browser_steps = _find_nodes(browser_chain, attribute="data-browser-step")
    actual_browser_chain = [
        (step.attributes["data-browser-step"], _node_text(step))
        for step in browser_steps
    ]
    assert actual_browser_chain == list(PLATFORM_BROWSER_CHAIN), (
        f"Browser evidence chain mismatch: {actual_browser_chain}"
    )

    responsibilities = _single_node(
        frontend_panel, attribute="data-frontend-responsibilities"
    )
    responsibility_nodes = _find_nodes(
        responsibilities, attribute="data-frontend-owner"
    )
    actual_responsibilities = {
        node.attributes["data-frontend-owner"]: _node_text(node)
        for node in responsibility_nodes
    }
    assert actual_responsibilities == PLATFORM_FRONTEND_RESPONSIBILITIES, (
        f"Frontend responsibility mismatch: {actual_responsibilities}"
    )

    control_panel = _single_node(
        document, attribute="id", value="engineering-controls-panel"
    )
    lanes = _find_nodes(control_panel, attribute="data-control-lane")
    actual_lanes = {
        lane.attributes["data-control-lane"]: tuple(
            _node_text(item)
            for item in _find_nodes(lane, attribute="data-control-item")
        )
        for lane in lanes
    }
    assert actual_lanes == PLATFORM_CONTROL_LANES, (
        f"Control lane mismatch: {actual_lanes}"
    )


def _parse_home_values(markup: str) -> list[dict[str, object]]:
    parser = _HomeValueParser()
    parser.feed(markup)
    parser.close()
    return [
        {
            "title": _normalize_html_text(item["title"]),
            "description": _normalize_html_text(item["description"]),
            "links": [
                {
                    "href": link["href"],
                    "text": _normalize_html_text(link["text_parts"]),
                }
                for link in item["links"]
            ],
        }
        for item in parser.items
    ]


def _execute_video_config(path: Path) -> dict[str, object]:
    harness = r"""
"use strict";
const fs = require("node:fs");
const vm = require("node:vm");
const context = vm.createContext({ window: {} });
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
const config = context.window.AISDLC_VIDEO;
const fields = ["src", "type", "captions", "poster", "title"];
const before = Object.fromEntries(fields.map((field) => [field, config[field]]));
const mutationErrors = {};
for (const field of fields) {
  try {
    config[field] = "__mutated__";
    mutationErrors[field] = null;
  } catch (error) {
    mutationErrors[field] = error.name;
  }
}
const after = Object.fromEntries(fields.map((field) => [field, config[field]]));
process.stdout.write(JSON.stringify({
  isFrozen: Object.isFrozen(config),
  before,
  after,
  mutationErrors,
}));
"""
    result = subprocess.run(
        ["node", "-e", harness, str(path.resolve())],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _execute_tab_browser_contract(path: Path) -> dict[str, object]:
    harness = r"""
"use strict";
const fs = require("node:fs");
const vm = require("node:vm");
const tabIds = [
  "review-requirement",
  "review-design",
  "review-implementation",
  "review-frontend",
  "review-pr",
];

function boot(initialHash = "") {
  class FakeElement {
    constructor(id, panelId = "") {
      this.id = id;
      this.dataset = panelId ? { tab: id } : {};
      this.attributes = panelId
        ? { "aria-controls": panelId, "aria-selected": "false" }
        : {};
      this.hidden = false;
      this.tabIndex = 0;
      this.listeners = {};
    }
    addEventListener(type, listener) {
      (this.listeners[type] ||= []).push(listener);
    }
    setAttribute(name, value) {
      this.attributes[name] = String(value);
    }
    getAttribute(name) {
      return this.attributes[name] ?? null;
    }
    click() {
      for (const listener of this.listeners.click || []) listener({});
    }
    focus() {
      document.activeElement = this;
    }
    key(key) {
      const event = { key, preventDefault() {} };
      for (const listener of this.listeners.keydown || []) listener(event);
    }
  }

  const tabs = tabIds.map(
    (id) => new FakeElement(id, `${id}-panel`),
  );
  const panels = tabIds.map((id) => new FakeElement(`${id}-panel`));
  const group = {
    querySelectorAll(selector) {
      if (selector === "[data-tab]") return tabs;
      if (selector === "[data-tab-panel]") return panels;
      return [];
    },
  };
  const readyListeners = [];
  const windowListeners = {};
  const document = {
    activeElement: null,
    documentElement: { classList: { add() {} } },
    addEventListener(type, listener) {
      if (type === "DOMContentLoaded") readyListeners.push(listener);
    },
    querySelector() {
      return null;
    },
    querySelectorAll(selector) {
      return selector === "[data-tabs]" ? [group] : [];
    },
  };
  const location = { hash: initialHash };
  const historyEntries = [initialHash];
  let historyIndex = 0;
  const fireWindow = (type) => {
    for (const listener of windowListeners[type] || []) listener({});
  };
  const history = {
    pushState(_state, _title, hash) {
      historyEntries.splice(historyIndex + 1);
      historyEntries.push(hash);
      historyIndex = historyEntries.length - 1;
      location.hash = hash;
    },
    back() {
      if (historyIndex === 0) return;
      historyIndex -= 1;
      location.hash = historyEntries[historyIndex];
      fireWindow("popstate");
      fireWindow("hashchange");
    },
  };
  const window = {
    addEventListener(type, listener) {
      (windowListeners[type] ||= []).push(listener);
    },
  };
  const context = vm.createContext({
    console,
    document,
    history,
    location,
    window,
  });
  vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
  for (const listener of readyListeners) listener();

  const snapshot = () => ({
    hash: location.hash,
    selected: tabs.find((tab) => tab.getAttribute("aria-selected") === "true")?.id,
    visiblePanels: panels.filter((panel) => !panel.hidden).map((panel) => panel.id),
    focused: document.activeElement?.id || null,
  });
  return { tabs, panels, history, snapshot };
}

const session = boot();
const defaultState = session.snapshot();
const clicks = [];
for (const tab of session.tabs) {
  tab.click();
  clicks.push(session.snapshot());
}

session.tabs[2].click();
session.tabs[2].focus();
session.tabs[2].key("ArrowRight");
const arrowRight = session.snapshot();
session.tabs[3].key("Home");
const home = session.snapshot();
session.tabs[0].key("End");
const end = session.snapshot();
session.tabs[4].key("ArrowLeft");
const arrowLeft = session.snapshot();

session.tabs[0].click();
session.tabs[1].click();
session.history.back();
const back = session.snapshot();
const reload = boot("#review-pr").snapshot();

process.stdout.write(JSON.stringify({
  defaultState,
  clicks,
  keys: { arrowRight, home, end, arrowLeft },
  history: { back, reload },
}));
"""
    result = subprocess.run(
        ["node", "-e", harness, str(path.resolve())],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_top_level_page_shells_exist() -> None:
    root = Path("deliverables/ai-sdlc-2.0-offline-product-site")
    assert [name for name in TOP_LEVEL_PAGES if not (root / name).is_file()] == []


def test_loop_page_covers_formal_workitem_lifecycle() -> None:
    markup = Path(
        "deliverables/ai-sdlc-2.0-offline-product-site/loop-engineering.html"
    ).read_text(encoding="utf-8")
    document = _parse_document(markup)

    headings = _find_nodes(document, tag="h1")
    assert [_node_text(heading) for heading in headings] == [
        "让 AI 开发任务，从明确需求走到可验证完成"
    ]
    lifecycle_regions = _find_nodes(document, attribute="data-workitem-lifecycle")
    assert len(lifecycle_regions) == 1
    lifecycle_text = _node_text(lifecycle_regions[0])
    assert "正式 WorkItem 路径" in lifecycle_text
    assert "任意聊天输入不会自动成为完整 WorkItem" in lifecycle_text
    assert (
        "Init / Adopt → WorkItem → Requirement → Design Contract → Implementation → "
        "Frontend Evidence（按需） → Local PR Review（按需） → Close" in lifecycle_text
    )
    expert_ctas = [
        node
        for node in _find_nodes(document, tag="a")
        if node.attributes.get("href") == "dynamic-expert-review.html"
        and _node_text(node) == "继续了解专家如何挑战 Loop 结果"
    ]
    assert len(expert_ctas) == 1


def test_loop_page_covers_minimum_protocol() -> None:
    markup = Path(
        "deliverables/ai-sdlc-2.0-offline-product-site/loop-engineering.html"
    ).read_text(encoding="utf-8")
    document = _parse_document(markup)

    protocols = _find_nodes(document, tag="ol", attribute="data-loop-protocol")
    assert len(protocols) == 1
    steps = [child for child in protocols[0].children if child.tag == "li"]
    assert [_node_text(step) for step in steps] == [
        "接收明确目标与输入，建立 Loop 身份。",
        "不依赖聊天记忆，从项目工件重算当前状态。",
        "输出缺失信息、停止原因与唯一下一步动作。",
        "执行或复核前冻结 input_digest 与只读复核快照。",
        "将 Findings 回流原 Writer；Reviewer 不修改候选。",
        "定向修复后，只允许基于重新绑定输入进行最多一次复审。",
        "Freeze / Close 前重建输入；任何绑定输入漂移都拒绝关闭。",
    ]


def test_loop_page_keeps_pr_review_cross_stage() -> None:
    markup = Path(
        "deliverables/ai-sdlc-2.0-offline-product-site/loop-engineering.html"
    ).read_text(encoding="utf-8")
    document = _parse_document(markup)

    tab_groups = _find_nodes(document, attribute="data-tabs", value="loop-workspace")
    assert len(tab_groups) == 1
    tabs = [
        node
        for node in _find_nodes(tab_groups[0], attribute="data-tab")
        if node.attributes.get("role") == "tab"
    ]
    assert [tab.attributes["data-tab"] for tab in tabs] == [
        "requirement",
        "design-contract",
        "implementation",
        "frontend-evidence",
    ]

    required_panel_regions = {
        "data-loop-input",
        "data-loop-state",
        "data-loop-feedback",
        "data-loop-close",
        "data-loop-evidence",
    }
    for tab in tabs:
        panels = _find_nodes(
            tab_groups[0],
            tag="section",
            attribute="id",
            value=tab.attributes["aria-controls"],
        )
        assert len(panels) == 1
        present = {
            attribute
            for node in _find_nodes(panels[0])
            for attribute in required_panel_regions
            if attribute in node.attributes
        }
        assert present == required_panel_regions
    _assert_loop_panel_contract(document)

    review_rails = _find_nodes(document, attribute="id", value="local-pr-review")
    assert len(review_rails) == 1
    assert "提交前跨阶段复核（按需）" in _node_text(review_rails[0])
    assert not _is_descendant(review_rails[0], tab_groups[0])
    _assert_local_pr_review_contract(document)


def test_loop_panel_contract_rejects_swapped_evidence() -> None:
    markup = Path(
        "deliverables/ai-sdlc-2.0-offline-product-site/loop-engineering.html"
    ).read_text(encoding="utf-8")
    document = _parse_document(markup)
    requirement = _single_node(
        document, tag="section", attribute="id", value="requirement-panel"
    )
    frontend = _single_node(
        document, tag="section", attribute="id", value="frontend-evidence-panel"
    )
    requirement_evidence = _single_node(requirement, attribute="data-loop-evidence")
    frontend_evidence = _single_node(frontend, attribute="data-loop-evidence")
    requirement_evidence.content, frontend_evidence.content = (
        frontend_evidence.content,
        requirement_evidence.content,
    )

    with pytest.raises(
        AssertionError, match="requirement-panel data-loop-evidence missing"
    ):
        _assert_loop_panel_contract(document)


@pytest.mark.parametrize("mutation", ("delete", "reorder"))
def test_local_pr_review_contract_rejects_missing_or_reordered_step(
    mutation: str,
) -> None:
    markup = Path(
        "deliverables/ai-sdlc-2.0-offline-product-site/loop-engineering.html"
    ).read_text(encoding="utf-8")
    document = _parse_document(markup)
    rail = _single_node(document, attribute="id", value="local-pr-review")
    step_list = _single_node(rail, tag="ol")
    steps = [child for child in step_list.children if child.tag == "li"]
    if mutation == "delete":
        step_list.children.remove(steps[1])
    else:
        first_index = step_list.children.index(steps[0])
        second_index = step_list.children.index(steps[1])
        step_list.children[first_index], step_list.children[second_index] = (
            step_list.children[second_index],
            step_list.children[first_index],
        )

    with pytest.raises(AssertionError, match="Local PR Review steps out of contract"):
        _assert_local_pr_review_contract(document)


def test_loop_page_covers_state_and_recovery() -> None:
    markup = Path(
        "deliverables/ai-sdlc-2.0-offline-product-site/loop-engineering.html"
    ).read_text(encoding="utf-8")
    document = _parse_document(markup)

    state_tables = _find_nodes(document, tag="table", attribute="data-loop-state-table")
    assert len(state_tables) == 1
    state_text = _node_text(state_tables[0])
    for state, meaning in (
        ("needs_user", "缺少决定或输入"),
        ("needs_fix", "候选必须修改"),
        ("needs_review", "独立验证待完成或结论不确定"),
        ("closed", "新鲜绑定证据满足 Close 合同"),
    ):
        assert state in state_text
        assert meaning in state_text

    recovery_regions = _find_nodes(document, attribute="data-loop-recovery")
    assert len(recovery_regions) == 1
    recovery_text = _node_text(recovery_regions[0])
    cursor = -1
    for step in (
        "发现缺口",
        "明确状态",
        "用户 / Writer / Reviewer 行动",
        "重新计算工件与状态",
        "Close 或保持未关闭",
    ):
        cursor = recovery_text.index(step, cursor + 1)
    assert "输入发生漂移后，旧证据立即失效；重新绑定前拒绝复用" in recovery_text
    for token in ("checkpoint", "branch", "artifact", "recover", "reconcile"):
        assert token in recovery_text
    assert "fail-closed" in recovery_text
    assert "先停止当前运行" in recovery_text
    assert "提示 recover 或显式 reconcile" in recovery_text
    assert "恢复的是项目事实，而不是模型思维过程" in recovery_text


def test_expert_page_exposes_semantic_bounded_review_graph() -> None:
    markup = Path(
        "deliverables/ai-sdlc-2.0-offline-product-site/dynamic-expert-review.html"
    ).read_text(encoding="utf-8")
    document = _parse_document(markup)

    headings = _find_nodes(document, tag="h1")
    assert [_node_text(heading) for heading in headings] == [
        "让关键结果先经独立挑战，再进入下一步"
    ]
    _assert_expert_graph_contract(document)

    identity = _single_node(document, attribute="data-graph-identity")
    assert _node_text(identity) == EXPERT_GRAPH_IDENTITY
    assert markup.count("图数据库") == 1
    assert markup.count("自主多 Agent Runtime") == 1

    boundaries = _single_node(document, attribute="data-review-boundaries")
    boundary_text = _node_text(boundaries)
    for token in (
        "专家只读",
        "默认一名 Primary Expert",
        "最多再加一名",
        "最多一次复审",
        "needs_review",
    ):
        assert token in boundary_text

    closing = _single_node(document, attribute="data-review-closing")
    assert (
        "专家负责把问题说清楚，原 Writer 负责把结果修好，Loop 负责决定能不能关闭。"
        in _node_text(closing)
    )
    ctas = [
        node
        for node in _find_nodes(closing, tag="a")
        if node.attributes.get("href") == "platform-capabilities.html"
        and _node_text(node) == "查看支撑这一机制的平台能力"
    ]
    assert len(ctas) == 1


def test_expert_risk_tabs_change_only_the_four_bound_values() -> None:
    markup = Path(
        "deliverables/ai-sdlc-2.0-offline-product-site/dynamic-expert-review.html"
    ).read_text(encoding="utf-8")
    document = _parse_document(markup)
    workspace = _single_node(document, attribute="data-tabs", value="expert-risk")
    tabs = [
        node
        for node in _find_nodes(workspace, attribute="data-tab")
        if node.attributes.get("role") == "tab"
    ]
    expected_tab_ids = [
        "review-requirement",
        "review-design",
        "review-implementation",
        "review-frontend",
        "review-pr",
    ]
    assert [tab.attributes.get("id") for tab in tabs] == expected_tab_ids
    assert [tab.attributes.get("data-tab") for tab in tabs] == expected_tab_ids

    for tab, panel_id in zip(tabs, EXPERT_RISK_CONTRACTS, strict=True):
        assert tab.attributes.get("aria-controls") == panel_id
        panel = _single_node(workspace, tag="section", attribute="id", value=panel_id)
        assert "hidden" not in panel.attributes
    _assert_expert_risk_contract(document)


def test_expert_graph_contract_rejects_unconditional_cross_risk() -> None:
    markup = Path(
        "deliverables/ai-sdlc-2.0-offline-product-site/dynamic-expert-review.html"
    ).read_text(encoding="utf-8")
    document = _parse_document(markup)
    cross_risk = _single_node(
        document, tag="li", attribute="data-expert-branch", value="cross-risk"
    )
    cross_risk.attributes["data-route"] = "required"

    with pytest.raises(AssertionError, match="Cross-risk Expert must be conditional"):
        _assert_expert_graph_contract(document)


def test_expert_risk_contract_rejects_stage_expert_swap() -> None:
    markup = Path(
        "deliverables/ai-sdlc-2.0-offline-product-site/dynamic-expert-review.html"
    ).read_text(encoding="utf-8")
    document = _parse_document(markup)
    requirement = _single_node(
        _single_node(
            document,
            tag="section",
            attribute="id",
            value="review-requirement-panel",
        ),
        attribute="data-primary-expert",
    )
    implementation = _single_node(
        _single_node(
            document,
            tag="section",
            attribute="id",
            value="review-implementation-panel",
        ),
        attribute="data-primary-expert",
    )
    requirement_value = _single_node(requirement, tag="dd")
    implementation_value = _single_node(implementation, tag="dd")
    requirement_value.content, implementation_value.content = (
        implementation_value.content,
        requirement_value.content,
    )

    with pytest.raises(AssertionError, match="stage contract mismatch"):
        _assert_expert_risk_contract(document)


def test_site_javascript_executes_complete_expert_tab_browser_contract() -> None:
    site_js = Path(
        "deliverables/ai-sdlc-2.0-offline-product-site/assets/js/site.js"
    )

    observed = _execute_tab_browser_contract(site_js)

    tab_ids = [
        "review-requirement",
        "review-design",
        "review-implementation",
        "review-frontend",
        "review-pr",
    ]
    assert observed["defaultState"] == {
        "hash": "",
        "selected": "review-requirement",
        "visiblePanels": ["review-requirement-panel"],
        "focused": None,
    }
    assert [state["selected"] for state in observed["clicks"]] == tab_ids
    assert [state["hash"] for state in observed["clicks"]] == [
        f"#{tab_id}" for tab_id in tab_ids
    ]
    assert [state["visiblePanels"] for state in observed["clicks"]] == [
        [f"{tab_id}-panel"] for tab_id in tab_ids
    ]
    assert {
        key: (state["selected"], state["focused"])
        for key, state in observed["keys"].items()
    } == {
        "arrowRight": ("review-frontend", "review-frontend"),
        "home": ("review-requirement", "review-requirement"),
        "end": ("review-pr", "review-pr"),
        "arrowLeft": ("review-frontend", "review-frontend"),
    }
    assert observed["history"]["back"]["selected"] == "review-requirement"
    assert observed["history"]["reload"]["selected"] == "review-pr"


def test_expert_page_does_not_restore_removed_review_mechanisms() -> None:
    markup = Path(
        "deliverables/ai-sdlc-2.0-offline-product-site/dynamic-expert-review.html"
    ).read_text(encoding="utf-8")

    for forbidden in (
        "Veto",
        "Quorum",
        "投票",
        "Shadow",
        "Enforce",
        "缺陷发现率",
        "成功率",
    ):
        assert forbidden not in markup


def test_platform_page_exposes_exactly_four_structured_capability_tabs() -> None:
    markup = Path(
        "deliverables/ai-sdlc-2.0-offline-product-site/platform-capabilities.html"
    ).read_text(encoding="utf-8")
    document = _parse_document(markup)

    assert _node_text(_single_node(document, tag="h1")) == (
        "把零散 Skills，变成留在项目里的工程系统"
    )
    workspace = _single_node(
        document, attribute="data-tabs", value="platform-capabilities"
    )
    _assert_platform_capability_contract(workspace)

    tabs = _find_nodes(workspace, tag="button", attribute="data-tab")
    expected_ids = [contract[0] for contract in PLATFORM_PANEL_CONTRACTS]
    assert [tab.attributes.get("data-tab") for tab in tabs] == expected_ids
    assert [tab.attributes.get("aria-controls") for tab in tabs] == [
        f"{tab_id}-panel" for tab_id in expected_ids
    ]


def test_platform_page_keeps_every_capability_readable_without_javascript() -> None:
    markup = Path(
        "deliverables/ai-sdlc-2.0-offline-product-site/platform-capabilities.html"
    ).read_text(encoding="utf-8")
    document = _parse_document(markup)
    workspace = _single_node(
        document, attribute="data-tabs", value="platform-capabilities"
    )

    panels = _find_nodes(workspace, tag="section", attribute="data-tab-panel")
    assert len(panels) == 4
    assert all("hidden" not in panel.attributes for panel in panels)
    assert all(_node_text(panel) for panel in panels)


def test_shared_navigation_collapses_at_platform_tablet_acceptance_width() -> None:
    stylesheet = Path(
        "deliverables/ai-sdlc-2.0-offline-product-site/assets/css/site.css"
    ).read_text(encoding="utf-8")

    tablet_rule = re.search(
        r"@media \(max-width: 64rem\) \{(?P<rules>.*?)(?=\n@media|\Z)",
        stylesheet,
        re.DOTALL,
    )
    assert tablet_rule is not None
    assert ".js [data-nav-toggle]" in tablet_rule.group("rules")
    assert '.js [data-nav-menu]:not([data-open="true"])' in tablet_rule.group(
        "rules"
    )


def test_platform_contract_rejects_swapped_adapter_paths() -> None:
    markup = Path(
        "deliverables/ai-sdlc-2.0-offline-product-site/platform-capabilities.html"
    ).read_text(encoding="utf-8")
    document = _parse_document(markup)
    codex = _single_node(document, attribute="data-adapter", value="codex")
    claude = _single_node(document, attribute="data-adapter", value="claude-code")
    _swap_node_content(
        _single_node(codex, attribute="data-adapter-path"),
        _single_node(claude, attribute="data-adapter-path"),
    )

    with pytest.raises(AssertionError, match="Adapter mapping mismatch"):
        _assert_platform_capability_contract(document)


def test_platform_contract_rejects_default_and_vue2_route_swap() -> None:
    markup = Path(
        "deliverables/ai-sdlc-2.0-offline-product-site/platform-capabilities.html"
    ).read_text(encoding="utf-8")
    document = _parse_document(markup)
    default = _single_node(document, attribute="data-frontend-route", value="default")
    vue2 = _single_node(
        document,
        attribute="data-frontend-route",
        value="vue2-compatibility",
    )
    _swap_node_content(default, vue2)

    with pytest.raises(AssertionError, match="Frontend route mismatch"):
        _assert_platform_capability_contract(document)


def test_platform_contract_rejects_browser_gate_and_frontend_evidence_swap() -> None:
    markup = Path(
        "deliverables/ai-sdlc-2.0-offline-product-site/platform-capabilities.html"
    ).read_text(encoding="utf-8")
    document = _parse_document(markup)
    browser_gate = _single_node(
        document, attribute="data-browser-step", value="browser-gate"
    )
    frontend_evidence = _single_node(
        document,
        attribute="data-browser-step",
        value="frontend-evidence",
    )
    _swap_node_content(browser_gate, frontend_evidence)

    with pytest.raises(AssertionError, match="Browser evidence chain mismatch"):
        _assert_platform_capability_contract(document)


def test_platform_contract_rejects_lean_code_in_strict_lane() -> None:
    markup = Path(
        "deliverables/ai-sdlc-2.0-offline-product-site/platform-capabilities.html"
    ).read_text(encoding="utf-8")
    document = _parse_document(markup)
    lean_code = _single_node(
        document, attribute="data-control-item", value="Lean Code"
    )
    strict_identity = _single_node(
        document, attribute="data-control-item", value="identity"
    )
    assert lean_code.parent is not None
    assert strict_identity.parent is not None
    lean_parent = lean_code.parent
    strict_parent = strict_identity.parent
    lean_parent.children.remove(lean_code)
    lean_parent.content.remove(lean_code)
    strict_parent.children.append(lean_code)
    strict_parent.content.append(lean_code)
    lean_code.parent = strict_parent

    with pytest.raises(AssertionError, match="Control lane mismatch"):
        _assert_platform_capability_contract(document)


def test_platform_page_closes_with_bounded_claims_and_download_cta() -> None:
    markup = Path(
        "deliverables/ai-sdlc-2.0-offline-product-site/platform-capabilities.html"
    ).read_text(encoding="utf-8")
    document = _parse_document(markup)
    visible_text = _node_text(document)

    for required in (
        "Codex",
        "Claude Code",
        "Cursor",
        "Copilot",
        "checkpoint",
        "handoff",
        "recover",
        "PrimeVue",
        "enterprise-vue2",
        "enterprise-default",
        "data-console",
        "high-clarity",
        "macos-glass",
        "Browser Gate",
        "code simplification",
        "local-first",
    ):
        assert required in visible_text
    for unsupported_claim in (
        "macOS Intel",
        "完整 E2E 平台",
        "附送私有组件包",
        "完全离线 AI 推理",
    ):
        assert unsupported_claim not in visible_text

    closing = _single_node(document, attribute="data-platform-closing")
    assert (
        "Prompt 和 Skills 告诉 AI 怎么做；AI-SDLC 继续管理它做到哪一步、"
        "凭什么继续，以及何时可以结束。"
        in _node_text(closing)
    )
    ctas = [
        node
        for node in _find_nodes(closing, tag="a")
        if node.attributes.get("href") == "downloads-docs.html"
        and _node_text(node) == "下载 AI-SDLC 2.0.0"
    ]
    assert len(ctas) == 1


def test_missing_required_pages_are_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "index.html", "<main><h1>Home</h1></main>")

    issues = validate_site(tmp_path)

    assert "missing_required_page" in {issue.code for issue in issues}


def test_remote_runtime_asset_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "index.html", '<script src="https://cdn.example/app.js"></script>')

    issues = validate_site(tmp_path)

    assert "remote_runtime_asset" in {issue.code for issue in issues}


def test_missing_local_asset_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "index.html", '<img src="assets/images/missing.webp" alt="">')

    issues = validate_site(tmp_path)

    assert "missing_local_asset" in {issue.code for issue in issues}


def test_competition_copy_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "index.html", "<main>课题一赛事要求</main>")

    issues = validate_site(tmp_path)

    assert "forbidden_public_copy" in {issue.code for issue in issues}


def test_unknown_external_anchor_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "index.html", '<a href="https://example.com">unexpected</a>')

    issues = validate_site(tmp_path)

    assert "external_url_not_allowed" in {issue.code for issue in issues}


def test_allowed_external_anchor_is_valid(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "index.html",
        '<a href="https://github.com/SinclairPan/Ai_AutoSDLC">GitHub</a>',
    )

    issues = validate_site(tmp_path)

    assert "external_url_not_allowed" not in {issue.code for issue in issues}


@pytest.mark.parametrize(
    "markup",
    (
        '<iframe src="https://cdn.example/frame.html"></iframe>',
        '<iframe src="https://[2001:db8::1]/frame.html"></iframe>',
        '<iframe src="ftp://mirror.example/frame.html"></iframe>',
        '<script src="ftp://mirror.example/runtime.js"></script>',
        '<video src="//cdn.example/demo.mp4"></video>',
        '<base href="https://cdn.example/"><main></main>',
        '<form action="https://cdn.example/submit"></form>',
        '<button formaction="https://cdn.example/submit"></button>',
        '<meta http-equiv="refresh" content="0; url=https://cdn.example/next">',
    ),
)
def test_remote_browser_active_html_address_is_rejected(
    tmp_path: Path, markup: str
) -> None:
    _write(tmp_path, "index.html", markup)

    issues = validate_site(tmp_path)

    assert "remote_runtime_asset" in {issue.code for issue in issues}


def test_css_string_import_remote_address_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "assets/css/site.css", '@import "https://cdn.example/site.css";')

    issues = validate_site(tmp_path)

    assert "remote_runtime_asset" in {issue.code for issue in issues}


@pytest.mark.parametrize(
    "script",
    (
        'new WebSocket("wss://socket.example/events");',
        'new WebSocket("wss://[2001:db8::1]/socket");',
        'const endpoint = "//cdn.example/api";',
        'const mirror = "ftp://mirror.example/site.js";',
    ),
)
def test_javascript_network_address_is_rejected(tmp_path: Path, script: str) -> None:
    _write(tmp_path, "assets/js/site.js", script)

    issues = validate_site(tmp_path)

    assert "remote_runtime_asset" in {issue.code for issue in issues}


def test_external_url_in_unbound_text_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "index.html", "<main><p>https://example.com/install</p></main>")

    issues = validate_site(tmp_path)

    assert "external_url_in_unbound_text" in {issue.code for issue in issues}


def test_source_bound_guide_command_url_is_inert(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "docs/USER_GUIDE.zh-CN.html",
        '<main><pre><code data-guide-command="path-2a-install-1">'
        "git clone https://github.com/SinclairPan/Ai_AutoSDLC.git"
        "</code></pre></main>",
    )

    issues = validate_site(tmp_path)

    assert "external_url_in_unbound_text" not in {issue.code for issue in issues}


def test_homepage_exposes_approved_value_and_video_contract() -> None:
    root = Path("deliverables/ai-sdlc-2.0-offline-product-site")
    homepage = (root / "index.html").read_text(encoding="utf-8")

    assert "把不确定的 AI 生成，变成可验证的工程交付" in homepage
    assert "data-video-empty" in homepage
    assert "data-video-empty-poster" in homepage
    assert "data-video-player" in homepage
    assert "data-video-title" in homepage
    assert 'src="assets/js/video-config.js"' in homepage
    assert re.search(r"\b\d{1,2}:\d{2}\b", homepage) is None


def test_homepage_value_items_bind_approved_copy_and_matching_ctas() -> None:
    homepage = Path(
        "deliverables/ai-sdlc-2.0-offline-product-site/index.html"
    ).read_text(encoding="utf-8")

    assert _parse_home_values(homepage) == [
        {
            "title": "从一次生成，到持续完成",
            "description": (
                "Loop 把目标、执行、验证、反馈与修复连成闭环；证据满足 Close "
                "条件，任务才真正完成。"
            ),
            "links": [
                {
                    "text": "看 Loop 如何把任务做完",
                    "href": "loop-engineering.html",
                }
            ],
        },
        {
            "title": "从模型自审，到专家对抗",
            "description": (
                "系统按风险选择独立只读专家；Findings 回到原 Writer "
                "修复，专家数量和复审轮次都有边界。"
            ),
            "links": [
                {
                    "text": "看专家如何挑战关键结果",
                    "href": "dynamic-expert-review.html",
                }
            ],
        },
        {
            "title": "从零散 Skills，到项目级工程系统",
            "description": (
                "跨 AI 工具接入、断点续作、前端方案、组件规范与浏览器验收证据都留在项目中；"
                "换工具，项目规则、状态与工件不必重建。"
            ),
            "links": [
                {
                    "text": "查看完整平台能力",
                    "href": "platform-capabilities.html",
                }
            ],
        },
    ]


def test_homepage_video_defaults_to_an_honest_local_empty_state() -> None:
    root = Path("deliverables/ai-sdlc-2.0-offline-product-site")
    homepage = (root / "index.html").read_text(encoding="utf-8")

    assert '<img data-video-empty-poster src="assets/images/video-poster.png" alt="">' in homepage


def test_video_configuration_is_immutable_and_defaults_to_empty_state() -> None:
    config_path = Path(
        "deliverables/ai-sdlc-2.0-offline-product-site/assets/js/video-config.js"
    )
    expected = {
        "src": "",
        "type": "video/mp4",
        "captions": "",
        "poster": "assets/images/video-poster.png",
        "title": "AI-SDLC 2.0 产品实录",
    }

    observed = _execute_video_config(config_path)

    assert observed == {
        "isFrozen": True,
        "before": expected,
        "after": expected,
        "mutationErrors": dict.fromkeys(expected, "TypeError"),
    }


def test_homepage_keeps_native_video_controls_and_initializes_video() -> None:
    root = Path("deliverables/ai-sdlc-2.0-offline-product-site")
    homepage = (root / "index.html").read_text(encoding="utf-8")
    site_js = (root / "assets/js/site.js").read_text(encoding="utf-8")

    assert re.search(
        r"<video\b[^>]*\bcontrols\b[^>]*\bpreload=\"metadata\"[^>]*\bplaysinline\b",
        homepage,
    )
    initializer = re.search(
        r'document\.addEventListener\("DOMContentLoaded",\s*\(\)\s*=>\s*\{(?P<body>.*?)\}\);',
        site_js,
        re.DOTALL,
    )
    assert initializer is not None
    assert "setupVideo();" in initializer.group("body")


@pytest.mark.parametrize("field", ("src", "poster", "captions"))
def test_missing_configured_video_media_is_rejected(
    tmp_path: Path, field: str
) -> None:
    values = {"src": "", "poster": "", "captions": ""}
    values[field] = f"assets/video/missing-{field}.mp4"
    _write(
        tmp_path,
        "assets/js/video-config.js",
        "window.AISDLC_VIDEO = {"
        f'src:"{values["src"]}",'
        f'poster:"{values["poster"]}",'
        f'captions:"{values["captions"]}"'
        "};",
    )

    issues = validate_video_config(tmp_path)

    assert "missing_video_asset" in {issue.code for issue in issues}


def test_empty_video_with_existing_poster_is_valid(tmp_path: Path) -> None:
    _write(tmp_path, "assets/images/video-poster.png", "poster")
    _write(
        tmp_path,
        "assets/js/video-config.js",
        'window.AISDLC_VIDEO = {src:"",poster:"assets/images/video-poster.png",captions:""};',
    )

    assert validate_video_config(tmp_path) == []


def test_existing_configured_video_media_is_valid(tmp_path: Path) -> None:
    _write(tmp_path, "assets/video/demo.mp4", "video")
    _write(tmp_path, "assets/images/video-poster.png", "poster")
    _write(tmp_path, "assets/video/demo.vtt", "WEBVTT")
    _write(
        tmp_path,
        "assets/js/video-config.js",
        "window.AISDLC_VIDEO = {"
        'src:"assets/video/demo.mp4",'
        'poster:"assets/images/video-poster.png",'
        'captions:"assets/video/demo.vtt"'
        "};",
    )

    assert validate_video_config(tmp_path) == []


@pytest.mark.parametrize("field", ("src", "poster", "captions"))
def test_configured_video_media_path_cannot_escape_site_root(
    tmp_path: Path, field: str
) -> None:
    values = {"src": "", "poster": "", "captions": ""}
    values[field] = f"../outside-{field}.mp4"
    _write(
        tmp_path,
        "assets/js/video-config.js",
        "window.AISDLC_VIDEO = {"
        f'src:"{values["src"]}",'
        f'poster:"{values["poster"]}",'
        f'captions:"{values["captions"]}"'
        "};",
    )

    issues = validate_video_config(tmp_path)

    assert "video_path_escape" in {issue.code for issue in issues}


def test_manifest_uses_sorted_relative_paths(tmp_path: Path) -> None:
    _write(tmp_path, "z.txt", "z")
    _write(tmp_path, "a.txt", "a")

    lines = build_manifest(tmp_path).splitlines()

    assert lines == sorted(lines, key=lambda line: line.split("  ", 1)[1])
    assert str(tmp_path) not in "\n".join(lines)


def test_guide_parity_rejects_wrong_frozen_source_sha(tmp_path: Path) -> None:
    source = tmp_path / "USER_GUIDE.zh-CN.md"
    rendered = tmp_path / "USER_GUIDE.zh-CN.html"
    source.write_text("not the frozen guide", encoding="utf-8")
    rendered.write_text("<main></main>", encoding="utf-8")

    issues = validate_guide_parity(source, rendered)

    assert "guide_source_sha_mismatch" in {issue.code for issue in issues}


@pytest.mark.parametrize(
    ("fragment", "expected_code"),
    (
        ('<section data-guide-path="path-9z"></section>', "guide_unknown_path"),
        (
            '<section data-guide-path="path-1a"></section>'
            '<section data-guide-path="path-1a"></section>',
            "guide_duplicate_path",
        ),
        (
            '<section data-guide-path="path-1a">'
            '<article data-guide-step="publish"></article></section>',
            "guide_unknown_step",
        ),
        (
            '<section data-guide-path="path-1a">'
            '<article data-guide-step="install"></article>'
            '<article data-guide-step="install"></article></section>',
            "guide_duplicate_step",
        ),
        (
            '<section data-guide-path="path-1a">'
            '<article data-guide-step="install">'
            '<p data-guide-part="warning"></p></article></section>',
            "guide_unknown_part",
        ),
        (
            '<section data-guide-path="path-1a">'
            '<article data-guide-step="install">'
            '<p data-guide-part="purpose"></p><p data-guide-part="purpose"></p>'
            "</article></section>",
            "guide_duplicate_part",
        ),
    ),
)
def test_guide_parity_rejects_noncanonical_inventory_nodes(
    tmp_path: Path, fragment: str, expected_code: str
) -> None:
    source = Path("docs/product-site/content/USER_GUIDE.zh-CN.md")
    rendered = tmp_path / "USER_GUIDE.zh-CN.html"
    rendered.write_text(f"<main>{fragment}</main>", encoding="utf-8")

    issues = validate_guide_parity(source, rendered)

    assert expected_code in {issue.code for issue in issues}


def test_guide_parity_rejects_command_outside_pre(tmp_path: Path) -> None:
    source = Path("docs/product-site/content/USER_GUIDE.zh-CN.md")
    rendered = tmp_path / "USER_GUIDE.zh-CN.html"
    rendered.write_text(
        '<main><code data-guide-command="path-1a-install-1">command</code></main>',
        encoding="utf-8",
    )

    issues = validate_guide_parity(source, rendered)

    assert "guide_command_not_in_pre" in {issue.code for issue in issues}
