from pathlib import Path

from scripts.validate_user_guide_standard import (
    EXPECTED_ROUTE_IDS,
    MATRIX_MARKER,
    REQUIRED_STEPS,
    parse_project_version,
    validate_guide_text,
    validate_repository,
)

ROOT = Path(__file__).resolve().parents[2]


def _complete_route(route_id: str) -> str:
    state, channel, platform = route_id.split("|")
    command = "init ." if state == "new" else "adopt ."
    installer = "install_online" if channel == "online" else "install_offline"
    verification = ""
    if channel == "offline":
        verification = {
            "windows-amd64": "package.sha256 Get-FileHash",
            "macos-arm64": "package.sha256 shasum -a 256",
            "linux-amd64": "package.sha256 sha256sum",
        }[platform]
    steps = "\n".join(
        f"<!-- AI-SDLC-USER-GUIDE-STEP: {step} -->" for step in REQUIRED_STEPS
    )
    return (
        f"<!-- AI-SDLC-USER-GUIDE-ROUTE: {route_id} -->\n"
        f"{steps}\n"
        f"{installer} {verification}\n"
        f"ai-sdlc {command}\n"
        f"python -m ai_sdlc {command}\n"
        "当前结果 / Result\n下一步 / Next\n失败时停止并按本路线恢复。\n"
    )


def _complete_guide() -> str:
    return (
        MATRIX_MARKER
        + "\n"
        + "\n".join(_complete_route(route_id) for route_id in EXPECTED_ROUTE_IDS)
    )


def test_repository_records_standard_without_rewriting_v3_guide() -> None:
    assert parse_project_version((ROOT / "pyproject.toml").read_text()) == (3, 0, 0)
    assert validate_repository(ROOT) == []


def test_first_post_v3_release_requires_all_twelve_routes() -> None:
    findings = validate_guide_text("", version=(3, 0, 1))

    route_findings = [
        finding for finding in findings if finding.marker == "guide-route-marker-count"
    ]
    assert len(route_findings) == 12
    assert any(finding.marker == "guide-matrix-marker-count" for finding in findings)


def test_complete_self_contained_matrix_passes_after_activation() -> None:
    assert validate_guide_text(_complete_guide(), version=(3, 0, 1)) == []


def test_route_cannot_delegate_required_recovery_to_shared_text() -> None:
    guide = _complete_guide().replace(
        "失败时停止并按本路线恢复。",
        "参见公共章节。",
        1,
    )

    findings = validate_guide_text(guide, version=(3, 1, 0))

    assert any(finding.marker == "guide-route-recovery-empty" for finding in findings)
