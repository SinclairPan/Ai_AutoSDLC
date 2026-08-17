from pathlib import Path

import pytest
from scripts.validate_offline_product_site import (
    build_manifest,
    validate_guide_parity,
    validate_site,
    validate_video_config,
)


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


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


def test_missing_configured_video_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "assets/js/video-config.js",
        'window.AISDLC_VIDEO = {src:"assets/video/missing.mp4",poster:"",captions:""};',
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


def test_configured_video_path_cannot_escape_site_root(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "assets/js/video-config.js",
        'window.AISDLC_VIDEO = {src:"../outside.mp4",poster:"",captions:""};',
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
