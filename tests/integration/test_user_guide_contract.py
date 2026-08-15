"""Executable content contract for the new-user Chinese guide."""

from pathlib import Path

from ai_sdlc.integrations.agent_target import AGENT_TARGET_OPTIONS, agent_target_label

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "USER_GUIDE.zh-CN.md"


def guide_text() -> str:
    return GUIDE.read_text(encoding="utf-8")


def test_guide_is_scoped_to_two_new_user_scenarios() -> None:
    text = guide_text()
    assert "全新用户 + 全新空项目" in text
    assert "全新用户 + 已有项目" in text
    for forbidden in ("老版本升级", "从源码运行", "@main", "uv sync", "Lean Code"):
        assert forbidden not in text


def test_guide_lists_every_runtime_adapter_in_both_scenarios() -> None:
    text = guide_text()
    labels = [agent_target_label(option) for option in AGENT_TARGET_OPTIONS]
    assert labels == ["Claude Code", "Codex", "Cursor", "VS Code", "其他-通用"]
    for label in labels:
        assert text.count(label) >= 2
    assert "实际用于聊天开发的 AI 代理入口" in text
    assert "Codex + PowerShell 为默认组合" not in text


def test_guide_pins_published_assets_and_stable_output_contract() -> None:
    text = guide_text()
    for asset in (
        "ai-sdlc-offline-1.0.2-windows-amd64.zip",
        "ai-sdlc-offline-1.0.2-macos-arm64.tar.gz",
        "ai-sdlc-offline-1.0.2-linux-amd64.tar.gz",
    ):
        assert asset in text
        assert f"releases/download/v1.0.2/{asset}" in text
        assert f"{asset}.sha256" in text
    assert "releases/download/v1.0.4/" not in text
    for anchor in (
        "Offline installation completed",
        "1.0.2",
        "Initialized AI-SDLC project",
        "当前结果 / Result",
        "下一步 / Next",
        "接入已有项目：已生成桥接结果",
        "原任务文件不会被修改",
        "推荐继续点",
    ):
        assert anchor in text


def test_guide_contains_copyable_recovery_paths() -> None:
    text = guide_text()
    for command in (
        "ai-sdlc init .",
        "ai-sdlc adopt .",
        "ai-sdlc adapter select",
        "ai-sdlc adapter shell-select",
    ):
        assert command in text
    for symptom in (
        "SHA256 verification failed",
        "No module named ai_sdlc",
        "open gates",
    ):
        assert symptom in text
