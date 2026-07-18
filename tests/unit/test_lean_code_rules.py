"""公开 Lean Code 规则的单一真值与安装入口测试。"""

from __future__ import annotations

from pathlib import Path

from ai_sdlc.rules import RulesLoader


def test_root_and_builtin_lean_code_rules_are_identical() -> None:
    root = Path(__file__).resolve().parents[2]
    public_rule = root / "rules" / "lean-code.md"
    builtin_rule = root / "src" / "ai_sdlc" / "rules" / "lean-code.md"

    assert public_rule.read_bytes() == builtin_rule.read_bytes()


def test_rules_loader_activates_lean_code_for_execute_verify_and_close() -> None:
    loader = RulesLoader()

    assert "lean-code" in loader.list_rules()
    assert "lean-code" in loader.get_active_rules("execute")
    assert "lean-code" in loader.get_active_rules("verify")
    assert "lean-code" in loader.get_active_rules("close")
    assert "风险预算" in loader.load_rule("lean-code")
