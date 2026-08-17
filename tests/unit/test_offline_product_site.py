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


def _is_descendant(node: _HtmlNode, ancestor: _HtmlNode) -> bool:
    current = node.parent
    while current is not None:
        if current is ancestor:
            return True
        current = current.parent
    return False


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
        and "让专家挑战结果" in _node_text(node)
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

    review_rails = _find_nodes(document, attribute="id", value="local-pr-review")
    assert len(review_rails) == 1
    assert "提交前跨阶段复核（按需）" in _node_text(review_rails[0])
    assert not _is_descendant(review_rails[0], tab_groups[0])


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
