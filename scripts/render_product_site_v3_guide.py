"""Render the frozen v3.0.1 guide into the offline product-site shell."""

from __future__ import annotations

import argparse
import html
import re
from hashlib import sha256
from pathlib import Path

ROUTE_RE = re.compile(r"^## 路线 (?P<number>(?:[1-9]|1[0-2]))：(?P<title>.+)$")
STEP_RE = re.compile(r"^### (?P<number>[1-7])\. (?P<title>.+)$")
FENCE_RE = re.compile(r"^```(?P<language>[^`]*)$")
STEP_IDS = {
    "1": "prepare",
    "2": "acquire",
    "3": "verify",
    "4": "install",
    "5": "initialize",
    "6": "success",
    "7": "recover",
}


def _external_link(url: str, label: str | None = None) -> str:
    text = html.escape(label or url)
    escaped_url = html.escape(url, quote=True)
    return (
        f'<a target="_blank" rel="noopener noreferrer" href="{escaped_url}">'
        f'{text} <span class="network-label">需要联网</span></a>'
    )


def _inline_markup(value: str) -> str:
    placeholders: dict[str, str] = {}

    def store(fragment: str) -> str:
        key = f"@@AISDLC{len(placeholders)}@@"
        placeholders[key] = fragment
        return key

    value = re.sub(
        r"`([^`]+)`",
        lambda match: store(f"<code>{html.escape(match.group(1))}</code>"),
        value,
    )
    value = re.sub(
        r"\[([^\]]+)\]\((https://[^)]+)\)",
        lambda match: store(_external_link(match.group(2), match.group(1))),
        value,
    )
    value = re.sub(
        r"<(https://[^>]+)>",
        lambda match: store(_external_link(match.group(1))),
        value,
    )
    rendered = html.escape(value)
    rendered = rendered.replace("**", "")
    for key, fragment in placeholders.items():
        rendered = rendered.replace(key, fragment)
    return rendered


def _route_metadata(route_number: int) -> tuple[str, str]:
    if route_number <= 3:
        scenario = "new-online"
    elif route_number <= 6:
        scenario = "new-offline"
    elif route_number <= 9:
        scenario = "existing-online"
    else:
        scenario = "existing-offline"
    os_name = ("windows", "macos", "linux")[(route_number - 1) % 3]
    return scenario, os_name


def _route_selector(source: str) -> str:
    items = []
    for line in source.splitlines():
        match = ROUTE_RE.match(line)
        if not match:
            continue
        number = int(match.group("number"))
        scenario, os_name = _route_metadata(number)
        items.append(
            f'<a href="#route-{number}" data-guide-route-link="route-{number}" '
            f'data-guide-scenario="{scenario}" data-guide-os="{os_name}">'
            f"<span>{number:02d}</span>{_inline_markup(match.group('title').strip())}</a>"
        )
    return (
        '<nav class="guide-route-grid" aria-label="十二条新用户路线">'
        + "".join(items)
        + "</nav>"
    )


def _render_table(lines: list[str]) -> str:
    rows = []
    for line in lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-+:?", cell) for cell in cells):
            continue
        rows.append(cells)
    if not rows:
        return ""
    head, *body = rows
    return (
        '<div class="guide-table-wrap"><table><thead><tr>'
        + "".join(f'<th scope="col">{_inline_markup(cell)}</th>' for cell in head)
        + "</tr></thead><tbody>"
        + "".join(
            "<tr>"
            + "".join(f"<td>{_inline_markup(cell)}</td>" for cell in row)
            + "</tr>"
            for row in body
        )
        + "</tbody></table></div>"
    )


def render_guide(source: str) -> str:
    source_sha = sha256(source.encode("utf-8")).hexdigest()
    lines = source.replace("\r\n", "\n").splitlines()
    output: list[str] = []
    route_number: int | None = None
    step_id: str | None = None
    command_counters: dict[tuple[int, str], int] = {}
    section_open = False
    article_open = False
    index = 0

    def close_article() -> None:
        nonlocal article_open
        if article_open:
            output.append("</article>")
            article_open = False

    def close_section() -> None:
        nonlocal section_open
        close_article()
        if section_open:
            output.append("</section>")
            section_open = False

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        route_match = ROUTE_RE.match(line)
        step_match = STEP_RE.match(line)
        fence_match = FENCE_RE.match(stripped)

        if line.startswith("# "):
            index += 1
            continue
        if route_match:
            close_section()
            route_number = int(route_match.group("number"))
            scenario, os_name = _route_metadata(route_number)
            output.append(
                f'<section id="route-{route_number}" class="guide-path guide-route" '
                f'data-guide-path="route-{route_number}" data-guide-route="route-{route_number}" '
                f'data-guide-scenario="{scenario}" data-guide-os="{os_name}">'
                f'<header class="guide-path-heading"><p class="guide-path-index">ROUTE {route_number:02d} / '
                f"{os_name.upper()}</p><h2>{_inline_markup(route_match.group('title').strip())}</h2></header>"
            )
            section_open = True
            step_id = None
            index += 1
            continue
        if line.startswith("## "):
            close_section()
            output.append(
                f'<section class="guide-reference"><h2>{_inline_markup(line[3:].strip())}</h2>'
            )
            section_open = True
            route_number = None
            step_id = None
            index += 1
            continue
        if step_match and route_number is not None:
            close_article()
            step_id = STEP_IDS[step_match.group("number")]
            output.append(
                f'<article class="guide-step" data-guide-step="{step_id}"><header><span>'
                f"{step_match.group('number').zfill(2)}</span><div><p>{step_id.upper()}</p>"
                f"<h3>{_inline_markup(step_match.group('title').strip())}</h3></div></header>"
            )
            article_open = True
            index += 1
            continue
        if line.startswith("### "):
            close_article()
            output.append(f"<h3>{_inline_markup(line[4:].strip())}</h3>")
            index += 1
            continue
        if stripped.startswith("<!--"):
            while index < len(lines) and "-->" not in lines[index]:
                index += 1
            index += 1
            continue
        if fence_match:
            index += 1
            body: list[str] = []
            while index < len(lines) and not FENCE_RE.match(lines[index].strip()):
                body.append(lines[index])
                index += 1
            if route_number is not None and step_id is not None:
                key = (route_number, step_id)
                command_counters[key] = command_counters.get(key, 0) + 1
                command_id = f"route-{route_number}-{step_id}-{command_counters[key]}"
                output.append(
                    '<div class="guide-command-shell" data-guide-part="command"><div class="guide-copy-row">'
                    f'<button type="button" class="guide-copy-button" data-copy-command="{command_id}" '
                    f'aria-describedby="{command_id}-copy-status">复制命令</button>'
                    f'<span id="{command_id}-copy-status" data-copy-status="{command_id}" '
                    'role="status" aria-live="polite"></span></div>'
                    f'<pre><code data-guide-command="{command_id}">{html.escape(chr(10).join(body))}</code></pre></div>'
                )
            else:
                output.append(
                    f"<pre><code>{html.escape(chr(10).join(body))}</code></pre>"
                )
            index += 1
            continue
        if stripped.startswith("|"):
            table_lines = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index])
                index += 1
            output.append(_render_table(table_lines))
            continue
        if stripped.startswith(">"):
            quote_lines = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote_lines.append(lines[index].strip().lstrip(">").strip())
                index += 1
            output.append(
                f'<aside class="guide-note">{_inline_markup(" ".join(quote_lines))}</aside>'
            )
            continue
        list_match = re.match(r"^(?P<marker>[-*]|\d+\.)\s+(?P<text>.+)$", stripped)
        if list_match:
            ordered = list_match.group("marker").endswith(".")
            tag = "ol" if ordered else "ul"
            items = []
            while index < len(lines):
                item = re.match(r"^(?:[-*]|\d+\.)\s+(.+)$", lines[index].strip())
                if not item:
                    break
                items.append(f"<li>{_inline_markup(item.group(1))}</li>")
                index += 1
            output.append(f"<{tag}>" + "".join(items) + f"</{tag}>")
            continue
        if not stripped or stripped == "---":
            index += 1
            continue

        paragraph = [stripped]
        index += 1
        while index < len(lines):
            candidate = lines[index]
            if not candidate.strip() or candidate.startswith(
                ("#", ">", "```", "|", "- ")
            ):
                break
            if re.match(r"^\d+\.\s+", candidate.strip()):
                break
            paragraph.append(candidate.strip())
            index += 1
        output.append(f"<p>{_inline_markup(' '.join(paragraph))}</p>")

    close_section()
    route_selector = _route_selector(source)
    content = "\n".join(output)
    return f'''<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>AI-SDLC v3.0.1 中文用户指南</title>
    <link rel="stylesheet" href="../assets/css/tokens.css">
    <link rel="stylesheet" href="../assets/css/site.css">
    <link rel="stylesheet" href="../assets/css/pages.css">
    <script src="../assets/js/site.js" defer></script>
  </head>
  <body data-page="guide">
    <a class="skip-link" href="#main">跳到主要内容</a>
    <header class="site-header guide-header">
      <div class="site-shell">
        <a class="brand" href="../downloads-docs.html">AI-SDLC / 用户指南</a>
        <span class="release-badge" data-release-version="3.0.1">v3.0.1 · 比赛最终版本</span>
        <a class="button-secondary guide-return" href="../downloads-docs.html">返回 Downloads &amp; Docs</a>
      </div>
    </header>
    <main id="main" class="site-shell page-main guide-main" data-guide-source-sha256="{source_sha}">
      <section class="page-intro guide-hero" aria-labelledby="guide-title">
        <p class="page-eyebrow">BEGINNER GUIDE / SOURCE-BOUND v3.0.1</p>
        <h1 id="guide-title">AI-SDLC v3.0.1 中文用户指南</h1>
        <p>面向第一次接触 AI-SDLC 的普通用户。当前公开稳定版本与比赛最终版本均为 v3.0.1。</p>
      </section>
      <section id="choose-route" class="guide-path-selector" aria-labelledby="choose-route-title">
        <div class="guide-selector-heading"><div><p class="guide-path-index">CHOOSE ONE COMPLETE ROUTE</p>
        <h2 id="choose-route-title">选择唯一一条完整路线</h2></div>
        <p>空项目 / 已有项目 × 在线 / 离线 × Windows / macOS / Linux，共 12 条自包含路线。</p></div>
        {route_selector}
      </section>
      <div class="guide-route-content">{content}</div>
    </main>
  </body>
</html>
'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rendered = render_guide(args.source.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
