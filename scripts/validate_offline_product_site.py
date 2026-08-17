"""Validate that the AI-SDLC offline product site is self-contained."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections import Counter
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

REQUIRED_PAGES = (
    "index.html",
    "loop-engineering.html",
    "dynamic-expert-review.html",
    "platform-capabilities.html",
    "downloads-docs.html",
    "docs/USER_GUIDE.zh-CN.html",
)

FORBIDDEN_PUBLIC_COPY = (
    "赛事要求",
    "课题一",
    "课题二",
    "30 秒看懂",
    "v2 迁移指南",
    "产品契约",
)


@dataclass(frozen=True)
class SiteIssue:
    code: str
    path: Path
    detail: str


RUNTIME_REF_ATTRS = {
    "script": "src",
    "link": "href",
    "img": "src",
    "source": "src",
    "track": "src",
}

ALLOWED_EXTERNAL_URLS = frozenset(
    {
        "https://github.com/SinclairPan/Ai_AutoSDLC",
        "https://github.com/SinclairPan/Ai_AutoSDLC/releases/tag/v2.0.0",
        "https://github.com/SinclairPan/Ai_AutoSDLC/blob/v2.0.0/README.md",
        "https://github.com/SinclairPan/Ai_AutoSDLC/blob/v2.0.0/USER_GUIDE.zh-CN.md",
        "https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v2.0.0/ai-sdlc-offline-2.0.0-windows-amd64.zip",
        "https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v2.0.0/ai-sdlc-offline-2.0.0-windows-amd64.zip.sha256",
        "https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v2.0.0/ai-sdlc-offline-2.0.0-macos-arm64.tar.gz",
        "https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v2.0.0/ai-sdlc-offline-2.0.0-macos-arm64.tar.gz.sha256",
        "https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v2.0.0/ai-sdlc-offline-2.0.0-linux-amd64.tar.gz",
        "https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/v2.0.0/ai-sdlc-offline-2.0.0-linux-amd64.tar.gz.sha256",
    }
)

GUIDE_SOURCE_SHA256 = "8466b8535cea8f0a17e15181060b954ad84a815be96c7e2b269f84cfce054d67"
GUIDE_PATH_IDS = tuple(
    f"path-{group}{platform}" for group in range(1, 5) for platform in "abc"
)
GUIDE_STEPS = ("install", "verify", "initialize", "start")
GUIDE_PARTS = ("purpose", "location", "command", "expected", "troubleshoot", "next")

_NETWORK_HOST_RE = r"(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\])"
_NETWORK_URL_RE = re.compile(
    rf"(?:(?:[A-Za-z][A-Za-z0-9+.-]*:)?//{_NETWORK_HOST_RE}(?::[0-9]+)?(?:/[^\s<>'\"]*)?)",
    re.IGNORECASE,
)
_CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
_CSS_STRING_IMPORT_RE = re.compile(
    r"@import\s+(['\"])(.*?)\1", re.IGNORECASE | re.DOTALL
)
_JS_IMPORT_RE = re.compile(
    r"(?:import\s*(?:[^;]*?\s+from\s+)?|export\s+[^;]*?\s+from\s+)(['\"])(.*?)\1",
    re.DOTALL,
)
_JS_FETCH_RE = re.compile(r"fetch\s*\(\s*(['\"])(.*?)\1", re.DOTALL)
_VIDEO_FIELD_RE = re.compile(r"\b(src|poster|captions)\s*:\s*(['\"])(.*?)\2", re.DOTALL)
_GUIDE_HEADING_RE = re.compile(
    r"^###\s+(?P<group>[1-4])(?P<platform>[ABC])-\d+\s+(?P<step>Install|Verify|Initialize|Start)",
    re.IGNORECASE,
)
_FENCE_RE = re.compile(r"^```[^\n]*\n(.*?)^```\s*$", re.MULTILINE | re.DOTALL)

_NETWORK_BEARING_ATTRS = frozenset(
    {
        "action",
        "archive",
        "background",
        "cite",
        "codebase",
        "data",
        "formaction",
        "href",
        "imagesrcset",
        "manifest",
        "ping",
        "poster",
        "profile",
        "src",
        "srcset",
    }
)


class _SiteHTMLParser(HTMLParser):
    """Collect externally observable HTML contract data using stdlib parsing."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, dict[str, str]]] = []
        self.runtime_refs: list[tuple[str, str]] = []
        self.network_attribute_refs: list[tuple[str, str, str]] = []
        self.anchors: list[dict[str, str]] = []
        self.text_nodes: list[tuple[str, tuple[tuple[str, dict[str, str]], ...]]] = []
        self.main_count = 0
        self.h1_count = 0
        self.skip_links: list[str] = []
        self.viewport_count = 0
        self.nav_links: list[dict[str, str]] = []
        self.tabs: list[dict[str, str]] = []
        self.panels: list[dict[str, str]] = []
        self.images: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        self.stack.append((tag, attributes))
        runtime_attr = RUNTIME_REF_ATTRS.get(tag)
        if runtime_attr and runtime_attr in attributes:
            self.runtime_refs.append((tag, attributes[runtime_attr]))
        for key, value in attributes.items():
            is_anchor_href = tag == "a" and key == "href"
            is_refresh = (
                tag == "meta"
                and key == "content"
                and attributes.get("http-equiv", "").lower() == "refresh"
            )
            is_legacy_runtime_ref = key == runtime_attr
            if (
                not is_anchor_href
                and not is_legacy_runtime_ref
                and (key in _NETWORK_BEARING_ATTRS or is_refresh)
                and _NETWORK_URL_RE.search(value)
            ):
                self.network_attribute_refs.append((tag, key, value))
        if tag == "a":
            self.anchors.append(attributes)
            if attributes.get("href") == "#main":
                self.skip_links.append(attributes.get("class", ""))
            if any(element[0] == "nav" for element in self.stack[:-1]):
                self.nav_links.append(attributes)
        if tag == "main":
            self.main_count += 1
        if tag == "h1":
            self.h1_count += 1
        if tag == "meta" and attributes.get("name", "").lower() == "viewport":
            self.viewport_count += 1
        if attributes.get("role") == "tab":
            self.tabs.append(attributes)
        if attributes.get("role") == "tabpanel":
            self.panels.append(attributes)
        if tag == "img":
            self.images.append(attributes)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.text_nodes.append((data, tuple(self.stack)))


@dataclass
class _GuideCommand:
    in_pre: bool
    text: list[str]


class _GuideHTMLParser(HTMLParser):
    """Collect guide path, step, part, and command nodes without dependencies."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, dict[str, str]]] = []
        self.path_stack: list[str] = []
        self.step_stack: list[str] = []
        self.paths: list[str] = []
        self.steps: list[tuple[str | None, str]] = []
        self.parts: list[tuple[str | None, str | None, str]] = []
        self.commands: dict[str, list[_GuideCommand]] = {}
        self._command_stack: list[tuple[str, _GuideCommand]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        self.stack.append((tag, attributes))
        path_id = attributes.get("data-guide-path")
        if path_id:
            self.paths.append(path_id)
            self.path_stack.append(path_id)
        step = attributes.get("data-guide-step")
        if step:
            self.steps.append((self.path_stack[-1] if self.path_stack else None, step))
            self.step_stack.append(step)
        part = attributes.get("data-guide-part")
        if part:
            self.parts.append(
                (
                    self.path_stack[-1] if self.path_stack else None,
                    self.step_stack[-1] if self.step_stack else None,
                    part,
                )
            )
        command_id = attributes.get("data-guide-command")
        if tag == "code" and command_id:
            command = _GuideCommand(
                in_pre=bool(self.stack[:-1] and self.stack[-2][0] == "pre"), text=[]
            )
            self.commands.setdefault(command_id, []).append(command)
            self._command_stack.append((command_id, command))

    def handle_endtag(self, tag: str) -> None:
        if tag == "code" and self._command_stack:
            self._command_stack.pop()
        for index in range(len(self.stack) - 1, -1, -1):
            closing_tag, attributes = self.stack[index]
            if closing_tag != tag:
                continue
            if attributes.get("data-guide-path") and self.path_stack:
                self.path_stack.pop()
            if attributes.get("data-guide-step") and self.step_stack:
                self.step_stack.pop()
            del self.stack[index:]
            break

    def handle_data(self, data: str) -> None:
        if self._command_stack:
            self._command_stack[-1][1].text.append(data)


def _issue(code: str, path: Path, detail: str) -> SiteIssue:
    return SiteIssue(code=code, path=path, detail=detail)


def _normalized_url(value: str) -> str:
    parts = urlsplit(value)
    return parts._replace(query="", fragment="").geturl()


def _is_network_address(value: str) -> bool:
    normalized = unescape(value).strip()
    scheme = urlsplit(normalized).scheme.lower()
    return normalized.startswith("//") or bool(scheme and scheme not in {"data", "mailto", "tel"})


def _resolve_local(root: Path, source: Path, value: str) -> tuple[Path | None, str | None]:
    normalized = _normalized_url(unescape(value).strip())
    if not normalized or normalized.startswith("#"):
        return None, None
    if normalized.startswith("//"):
        return None, "remote"
    base = source if source == root else source.parent
    candidate = (base / normalized).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None, "escape"
    return candidate, None


def _validate_local_reference(
    root: Path, page: Path, value: str, issues: list[SiteIssue], *, video: bool = False
) -> None:
    if not value or value.startswith("data:"):
        return
    if _is_network_address(value):
        issues.append(_issue("remote_runtime_asset", page, value))
        return
    target, reason = _resolve_local(root, page, value)
    if reason == "escape":
        code = "video_path_escape" if video else "local_path_escape"
        issues.append(_issue(code, page, value))
    elif reason == "remote":
        issues.append(_issue("remote_runtime_asset", page, value))
    elif target is not None and not target.is_file():
        code = "missing_video_asset" if video else "missing_local_asset"
        issues.append(_issue(code, page, value))


def _is_inert_guide_command(
    relative_path: str, ancestors: tuple[tuple[str, dict[str, str]], ...]
) -> bool:
    if relative_path != "docs/USER_GUIDE.zh-CN.html":
        return False
    tags = [tag for tag, _ in ancestors]
    return "pre" in tags and any(
        tag == "code" and attributes.get("data-guide-command")
        for tag, attributes in ancestors
    )


def _validate_html_page(root: Path, page: Path) -> list[SiteIssue]:
    issues: list[SiteIssue] = []
    parser = _SiteHTMLParser()
    parser.feed(page.read_text(encoding="utf-8"))
    parser.close()
    relative_path = page.relative_to(root).as_posix()

    for _tag, value in parser.runtime_refs:
        _validate_local_reference(root, page, value, issues)

    for tag, attribute, value in parser.network_attribute_refs:
        issues.append(_issue("remote_runtime_asset", page, f"{tag}[{attribute}]={value}"))

    for anchor in parser.anchors:
        href = anchor.get("href", "")
        if _is_network_address(href):
            if _normalized_url(href) not in ALLOWED_EXTERNAL_URLS:
                issues.append(_issue("external_url_not_allowed", page, href))
        elif href and not href.startswith(("#", "mailto:", "tel:")):
            _validate_local_reference(root, page, href, issues)

    for text, ancestors in parser.text_nodes:
        hidden_runtime_text = any(tag in {"script", "style"} for tag, _ in ancestors)
        if (
            _NETWORK_URL_RE.search(text)
            and not hidden_runtime_text
            and not _is_inert_guide_command(relative_path, ancestors)
        ):
            issues.append(_issue("external_url_in_unbound_text", page, text.strip()))

    for forbidden in FORBIDDEN_PUBLIC_COPY:
        if forbidden in page.read_text(encoding="utf-8"):
            issues.append(_issue("forbidden_public_copy", page, forbidden))

    if relative_path in REQUIRED_PAGES:
        if parser.main_count != 1:
            issues.append(_issue("invalid_main_count", page, str(parser.main_count)))
        if parser.h1_count != 1:
            issues.append(_issue("invalid_h1_count", page, str(parser.h1_count)))
        if not parser.skip_links:
            issues.append(_issue("missing_skip_link", page, "Expected href='#main'."))
        if parser.viewport_count != 1:
            issues.append(_issue("invalid_viewport_meta", page, str(parser.viewport_count)))

    if relative_path in REQUIRED_PAGES[:-1]:
        expected_href = Path(relative_path).name
        current_links = [
            link
            for link in parser.nav_links
            if link.get("aria-current", "").lower() == "page"
        ]
        if len(current_links) != 1 or current_links[0].get("href") != expected_href:
            issues.append(_issue("invalid_nav_current_page", page, expected_href))

    panel_ids = {panel.get("id", "") for panel in parser.panels}
    for tab in parser.tabs:
        controls = tab.get("aria-controls", "")
        if not controls or controls not in panel_ids:
            issues.append(_issue("invalid_tab_controls", page, controls or "missing"))
        if "aria-selected" not in tab:
            issues.append(_issue("missing_tab_selection_state", page, controls or "missing"))

    for image in parser.images:
        if "alt" not in image:
            issues.append(_issue("missing_image_alt", page, image.get("src", "")))

    return issues


def _validate_css(root: Path, stylesheet: Path) -> list[SiteIssue]:
    issues: list[SiteIssue] = []
    contents = stylesheet.read_text(encoding="utf-8")
    if ":focus-visible" not in contents:
        issues.append(_issue("missing_focus_visible_style", stylesheet, ":focus-visible"))
    for _, value in _CSS_URL_RE.findall(contents):
        _validate_local_reference(root, stylesheet, value.strip(), issues)
    for _, value in _CSS_STRING_IMPORT_RE.findall(contents):
        _validate_local_reference(root, stylesheet, value.strip(), issues)
    return issues


def _validate_javascript(root: Path, script: Path) -> list[SiteIssue]:
    issues: list[SiteIssue] = []
    contents = script.read_text(encoding="utf-8")
    for url in _NETWORK_URL_RE.findall(contents):
        issues.append(_issue("remote_runtime_asset", script, url))
    for _, value in _JS_IMPORT_RE.findall(contents):
        _validate_local_reference(root, script, value, issues)
    for _, value in _JS_FETCH_RE.findall(contents):
        _validate_local_reference(root, script, value, issues)
    return issues


def validate_site(root: Path) -> list[SiteIssue]:
    """Return all static-site contract violations below *root*."""
    root = root.resolve()
    issues: list[SiteIssue] = []
    for required_page in REQUIRED_PAGES:
        path = root / required_page
        if not path.is_file():
            issues.append(_issue("missing_required_page", path, required_page))
    for page in sorted(root.rglob("*.html")):
        issues.extend(_validate_html_page(root, page))
    for stylesheet in sorted(root.rglob("*.css")):
        issues.extend(_validate_css(root, stylesheet))
    for script in sorted(root.rglob("*.js")):
        issues.extend(_validate_javascript(root, script))
    return issues


def validate_video_config(root: Path) -> list[SiteIssue]:
    """Validate configured local video, poster, and caption media paths."""
    root = root.resolve()
    config = root / "assets/js/video-config.js"
    if not config.is_file():
        return []
    fields = {name: value for name, _, value in _VIDEO_FIELD_RE.findall(config.read_text("utf-8"))}
    issues: list[SiteIssue] = []
    for field in ("src", "poster", "captions"):
        value = fields.get(field, "")
        if value:
            _validate_local_reference(root, root, value, issues, video=True)
    return issues


def _normalize_text(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").split("\n")).strip()


def _guide_source_commands(source: str) -> dict[str, str]:
    commands: dict[str, str] = {}
    current_path: str | None = None
    current_step: str | None = None
    counters: dict[tuple[str, str], int] = {}
    lines = source.replace("\r\n", "\n").splitlines(keepends=True)
    index = 0
    while index < len(lines):
        heading = _GUIDE_HEADING_RE.match(lines[index])
        if heading:
            current_path = f"path-{heading.group('group')}{heading.group('platform').lower()}"
            current_step = heading.group("step").lower()
        if lines[index].startswith("```") and current_path and current_step:
            index += 1
            body: list[str] = []
            while index < len(lines) and not lines[index].startswith("```"):
                body.append(lines[index])
                index += 1
            key = (current_path, current_step)
            counters[key] = counters.get(key, 0) + 1
            command_id = f"{current_path}-{current_step}-{counters[key]}"
            commands[command_id] = _normalize_text("".join(body))
        index += 1
    return commands


def validate_guide_parity(source_markdown: Path, rendered_html: Path) -> list[SiteIssue]:
    """Verify the frozen guide source and its rendered, navigable HTML form."""
    issues: list[SiteIssue] = []
    if not source_markdown.is_file():
        return [_issue("guide_source_missing", source_markdown, "Missing source guide.")]
    source_bytes = source_markdown.read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != GUIDE_SOURCE_SHA256:
        return [_issue("guide_source_sha_mismatch", source_markdown, "Frozen SHA256 differs.")]
    if not rendered_html.is_file():
        return [_issue("guide_rendered_html_missing", rendered_html, "Missing rendered guide.")]

    parser = _GuideHTMLParser()
    parser.feed(rendered_html.read_text(encoding="utf-8"))
    parser.close()
    expected_paths = set(GUIDE_PATH_IDS)
    expected_steps = set(GUIDE_STEPS)
    expected_parts = set(GUIDE_PARTS)
    path_counts = Counter(parser.paths)
    step_counts = Counter(parser.steps)
    part_counts = Counter(parser.parts)

    for path_id, count in path_counts.items():
        if path_id not in expected_paths:
            issues.append(_issue("guide_unknown_path", rendered_html, path_id))
        elif count > 1:
            issues.append(_issue("guide_duplicate_path", rendered_html, path_id))
    for path_id in GUIDE_PATH_IDS:
        if path_counts[path_id] == 0:
            issues.append(_issue("guide_path_missing", rendered_html, path_id))
        for step in GUIDE_STEPS:
            count = step_counts[(path_id, step)]
            if count == 0:
                issues.append(_issue("guide_step_missing", rendered_html, f"{path_id}:{step}"))
            elif count > 1:
                issues.append(_issue("guide_duplicate_step", rendered_html, f"{path_id}:{step}"))
            for part in GUIDE_PARTS:
                count = part_counts[(path_id, step, part)]
                if count == 0:
                    detail = f"{path_id}:{step}:{part}"
                    issues.append(_issue("guide_part_missing", rendered_html, detail))

                elif count > 1:
                    detail = f"{path_id}:{step}:{part}"
                    issues.append(_issue("guide_duplicate_part", rendered_html, detail))

    for path_id, step in step_counts:
        if path_id is None:
            issues.append(_issue("guide_step_without_path", rendered_html, step))
        elif path_id in expected_paths and step not in expected_steps:
            issues.append(_issue("guide_unknown_step", rendered_html, f"{path_id}:{step}"))
    for path_id, step, part in part_counts:
        if path_id is None or step is None:
            issues.append(_issue("guide_part_without_step", rendered_html, part))
        elif (
            path_id in expected_paths
            and step in expected_steps
            and part not in expected_parts
        ):
            issues.append(
                _issue("guide_unknown_part", rendered_html, f"{path_id}:{step}:{part}")
            )

    source_commands = _guide_source_commands(source_bytes.decode("utf-8"))
    for command_id, expected in source_commands.items():
        command_nodes = parser.commands.get(command_id, [])
        if not command_nodes:
            issues.append(_issue("guide_command_missing", rendered_html, command_id))
            continue
        if len(command_nodes) > 1:
            issues.append(_issue("guide_duplicate_command", rendered_html, command_id))
        actual = _normalize_text("".join(command_nodes[0].text))
        if actual != expected:
            issues.append(_issue("guide_command_mismatch", rendered_html, command_id))
    for command_id, command_nodes in parser.commands.items():
        if command_id not in source_commands:
            issues.append(_issue("guide_unknown_command", rendered_html, command_id))
        for command in command_nodes:
            if not command.in_pre:
                issues.append(_issue("guide_command_not_in_pre", rendered_html, command_id))
    return issues


def build_manifest(root: Path) -> str:
    """Build a deterministic, deliverable-only SHA256 manifest."""
    root = root.resolve()
    lines = []
    for path in sorted(path for path in root.rglob("*") if path.is_file()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(root).as_posix()}")
    return "\n".join(lines) + ("\n" if lines else "")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--write-manifest", type=Path)
    parser.add_argument("--guide-source", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    issues = validate_site(root)
    issues.extend(validate_video_config(root))
    if args.guide_source:
        issues.extend(validate_guide_parity(args.guide_source.resolve(), root / REQUIRED_PAGES[-1]))
    if issues:
        for issue in issues:
            print(f"{issue.code}: {issue.path}: {issue.detail}")
        return 1
    if args.write_manifest:
        args.write_manifest.write_text(build_manifest(root), encoding="utf-8")
    print(f"OFFLINE_PRODUCT_SITE_VALID root={root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
