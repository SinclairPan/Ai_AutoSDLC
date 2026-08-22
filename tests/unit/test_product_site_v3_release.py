import re
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path

SITE_ROOT = Path("deliverables/ai-sdlc-2.0-offline-product-site")
V3_VERSION = "3.0.1"
V3_COMMIT = "9a59a3edd483b0e6526b67b03fbfcac3ba48d2e4"
V3_TREE = "fd5c2dac0a216f0eb17855d03cc7900d872d3c61"
V3_GUIDE_SHA256 = "b1bd464882e7a0ad1b163091d39d4650f16bef9630d44d968c73aa09251cbe7d"
TOP_LEVEL_PAGES = (
    "index.html",
    "loop-engineering.html",
    "dynamic-expert-review.html",
    "platform-capabilities.html",
    "downloads-docs.html",
)


class _TagInventory(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, {key: value or "" for key, value in attrs}))


def _markup(name: str) -> str:
    return (SITE_ROOT / name).read_text(encoding="utf-8")


def _inventory(name: str) -> list[tuple[str, dict[str, str]]]:
    parser = _TagInventory()
    parser.feed(_markup(name))
    parser.close()
    return parser.tags


def _attrs(name: str, attribute: str) -> list[dict[str, str]]:
    return [attrs for _, attrs in _inventory(name) if attribute in attrs]


def test_all_product_pages_use_the_v301_competition_identity() -> None:
    for page in TOP_LEVEL_PAGES:
        markup = _markup(page)
        release_nodes = _attrs(page, "data-release-version")
        assert [node["data-release-version"] for node in release_nodes] == [V3_VERSION]
        assert "AI-SDLC 2.0" not in markup
        assert "v2.0.0" not in markup
        assert "比赛最终版本" in markup


def test_reviewed_copy_spec_and_data_report_use_the_v301_truthful_baseline() -> None:
    reviewed_sources = (
        Path("docs/product-site/content/offline-product-site-copy-v1.md"),
        Path("docs/product-site/design/offline-product-site-visual-design-spec-v1.md"),
    )
    for source in reviewed_sources:
        text = source.read_text(encoding="utf-8")
        assert "AI-SDLC 2.0" not in text
        assert "v2.0.0" not in text
        assert "737bda39e05c53450e180a20581b7b7a70db9cf0" not in text
        assert "3db58121e228a7a1c4c6b760c535d6df1ffdbe84" not in text
        assert "v3.0.1" in text

    platform = _markup("platform-capabilities.html")
    report = Path(
        "docs/product-site/research/2026-08-21-current-benefit-data-report.md"
    ).read_text(encoding="utf-8")
    assert "按 v3.0.1 最新能力重算" not in platform
    assert "既有合成数值" in platform
    assert V3_COMMIT in report
    assert "未重新运行 Provider" in report


def test_homepage_exposes_three_linked_product_evidence_tracks() -> None:
    markup = _markup("index.html")
    evidence = _attrs("index.html", "data-evidence-link")

    assert [item["data-evidence-link"] for item in evidence] == [
        "loop",
        "expert",
        "overall",
    ]
    assert [item["href"] for item in evidence] == [
        "loop-engineering.html#loop-benefit-title",
        "dynamic-expert-review.html#expert-benefit-title",
        "platform-capabilities.html#overall-benefit-title",
    ]
    for proof in ("52% → 90%", "55% → 92%", "9 → 2"):
        assert proof in markup
    assert "证据锚定合成评估" in markup
    assert "50 个优势导向场景" in markup


def test_loop_and_expert_pages_describe_five_types_not_a_fixed_five_stage_flow() -> (
    None
):
    loop_markup = _markup("loop-engineering.html")
    expert_markup = _markup("dynamic-expert-review.html")

    assert 'data-loop-taxonomy="five-types"' in loop_markup
    assert "五类 Loop" in loop_markup
    assert "五阶段 Loop" not in loop_markup
    assert "五类生成物" in expert_markup
    assert "相同五阶段" not in expert_markup
    assert "最多两名只读专家" in expert_markup
    assert "一次修复与一次复核" in expert_markup


def test_benefit_pages_have_three_level_disclosure_and_raw_data_links() -> None:
    contracts = {
        "loop-engineering.html": "assets/data/loop-benefit-data.json",
        "dynamic-expert-review.html": "assets/data/expert-review-benefit-data.json",
        "platform-capabilities.html": "assets/data/overall-comparison-data.json",
    }

    for page, raw_href in contracts.items():
        markup = _markup(page)
        assert len(_attrs(page, "data-evidence-disclosure")) == 1
        assert len(_attrs(page, "data-evidence-methodology")) == 1
        assert f'href="{raw_href}"' in markup
        assert "证据锚定合成评估" in markup
        assert "非生产统计" in markup
        assert "选择偏差" in markup
        assert V3_COMMIT in markup


def test_platform_prioritizes_three_headlines_and_groups_all_fourteen_metrics() -> None:
    inventory = _inventory("platform-capabilities.html")
    headlines = [attrs for _, attrs in inventory if "data-overall-headline" in attrs]
    groups = [attrs for _, attrs in inventory if "data-metric-group" in attrs]
    rows = [attrs for _, attrs in inventory if "data-overall-metric-row" in attrs]

    assert [item["data-overall-headline"] for item in headlines] == [
        "first-pass-acceptance",
        "frontend-compliance",
        "delivery-cost",
    ]
    assert [item["data-metric-group"] for item in groups] == [
        "requirements-and-design",
        "implementation-and-quality",
        "delivery-and-recovery",
        "cost-and-evidence",
    ]
    assert len(rows) == 14
    assert all("data-mobile-label" in row for row in rows)


def test_detail_pages_keep_the_first_evidence_module_above_the_common_fold() -> None:
    stylesheet = _markup("assets/css/pages.css")

    assert re.search(
        r"\.loop-main,\s*\.expert-main,\s*\.platform-main\s*\{"
        r"[^}]*gap:\s*var\(--space-7\)"
        r"[^}]*padding-top:\s*var\(--space-7\)",
        stylesheet,
        flags=re.DOTALL,
    )
    assert re.search(
        r"\.loop-main > \.benefit-benchmark,\s*"
        r"\.expert-main > \.benefit-benchmark,\s*"
        r"\.platform-main > \.benefit-benchmark\s*\{"
        r"[^}]*margin-block:\s*0",
        stylesheet,
        flags=re.DOTALL,
    )


def test_downloads_page_uses_the_published_v301_release_assets() -> None:
    markup = _markup("downloads-docs.html")

    for identity in (f"v{V3_VERSION}", V3_COMMIT, V3_TREE):
        assert identity in markup
    for filename in (
        "ai-sdlc-offline-3.0.1-windows-amd64.zip",
        "ai-sdlc-offline-3.0.1-macos-arm64.tar.gz",
        "ai-sdlc-offline-3.0.1-linux-amd64.tar.gz",
    ):
        base = (
            "https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/"
            f"v{V3_VERSION}/{filename}"
        )
        assert base in markup
        assert f"{base}.sha256" in markup
    assert "12 条完整路径" in markup
    assert "Linux AMD64" in markup


def test_local_user_guide_is_the_exact_v301_twelve_route_guide() -> None:
    source = Path("docs/product-site/content/USER_GUIDE.zh-CN.md")
    text = source.read_text(encoding="utf-8")

    assert sha256(source.read_bytes()).hexdigest() == V3_GUIDE_SHA256
    assert "# AI-SDLC 3.0.1 中文用户指南" in text
    assert "AI-SDLC-USER-GUIDE-MATRIX: 2x2x3=12" in text
    assert (
        len(re.findall(r"^## 路线 (?:[1-9]|1[0-2])：", text, flags=re.MULTILINE)) == 12
    )


def test_rendered_guide_copy_controls_keep_the_site_js_command_contract() -> None:
    command_parts = _attrs("docs/USER_GUIDE.zh-CN.html", "data-guide-part")
    commands = _attrs("docs/USER_GUIDE.zh-CN.html", "data-guide-command")
    controls = _attrs("docs/USER_GUIDE.zh-CN.html", "data-copy-command")

    assert len(command_parts) == len(commands) == len(controls) == 78
    assert {item["data-guide-part"] for item in command_parts} == {"command"}


def test_guide_routes_transfer_keyboard_focus_and_commands_contain_overflow() -> None:
    script = _markup("assets/js/site.js")
    stylesheet = _markup("assets/css/pages.css")

    assert "const setupGuideRouteLinks" in script
    assert "target.tabIndex = -1" in script
    assert "target.focus({ preventScroll: true })" in script
    assert ".guide-command-shell pre" in stylesheet
    assert ".guide-command-shell pre code" in stylesheet
    assert re.search(
        r"\.guide-main\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\)",
        stylesheet,
        flags=re.DOTALL,
    )
    assert re.search(
        r"\.guide-main a:not\(\.guide-scenario-link\)\s*\{[^}]*flex-wrap:\s*wrap",
        stylesheet,
        flags=re.DOTALL,
    )
