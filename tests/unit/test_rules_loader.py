"""Unit tests for RulesLoader."""

from __future__ import annotations

import pytest

from ai_sdlc.rules import RuleContextError, RulesLoader


class TestRulesLoader:
    def test_list_rules_at_least_thirteen(self) -> None:
        loader = RulesLoader()
        names = loader.list_rules()
        assert len(names) >= 13

    def test_load_rule_pipeline_contains_title_phrase(self) -> None:
        loader = RulesLoader()
        content = loader.load_rule("pipeline")
        assert "流水线总控规则" in content

    def test_load_rule_nonexistent_raises(self) -> None:
        loader = RulesLoader()
        with pytest.raises(FileNotFoundError):
            loader.load_rule("nonexistent")

    def test_get_active_rules_execute_includes_expected(self) -> None:
        loader = RulesLoader()
        active = loader.get_active_rules("execute")
        assert "batch-protocol" in active
        assert "tdd" in active
        assert "debugging" in active

    def test_get_active_rules_verify_includes_expected(self) -> None:
        loader = RulesLoader()
        active = loader.get_active_rules("verify")
        assert "quality-gate" in active
        assert "verification" in active

    def test_get_active_rules_unknown_stage_only_all_rules(self) -> None:
        loader = RulesLoader()
        active = loader.get_active_rules("unknown_stage")
        assert active == ["auto-decision", "pipeline"]

    def test_get_rule_title_pipeline(self) -> None:
        loader = RulesLoader()
        assert loader.get_rule_title("pipeline") == "流水线总控规则"

    def test_custom_rules_dir_tmp_path(self, tmp_path) -> None:
        (tmp_path / "custom.md").write_text("# Custom Title\n\nBody.", encoding="utf-8")
        loader = RulesLoader(rules_dir=tmp_path)
        assert loader.list_rules() == ["custom"]
        assert loader.load_rule("custom").startswith("# Custom Title")
        assert loader.get_rule_title("custom") == "Custom Title"

    @pytest.mark.parametrize(
        ("loop_type", "status", "expected"),
        [
            ("requirement", "needs_review", ["prd-guidance", "scenario-routing"]),
            ("design-contract", "needs_review", ["prd-guidance", "quality-gate"]),
            ("implementation", "needs_review", ["tdd", "verification"]),
            ("implementation", "needs_fix", ["debugging", "verification"]),
            ("frontend-evidence", "needs_review", ["verification", "quality-gate"]),
            ("local-pr-review", "needs_review", ["code-review", "verification"]),
        ],
    )
    def test_normal_path_context_maps_five_loops(
        self,
        loop_type: str,
        status: str,
        expected: list[str],
    ) -> None:
        context = RulesLoader().get_normal_path_context(loop_type, loop_status=status)

        assert [excerpt.name for excerpt in context.excerpts] == expected
        assert len(context.excerpts) <= 2
        assert (
            sum(len(item.content.encode("utf-8")) for item in context.excerpts) <= 2400
        )
        assert all("normal-path" not in item.content for item in context.excerpts)

    def test_normal_path_context_unknown_loop_returns_empty(self) -> None:
        context = RulesLoader().get_normal_path_context("unknown")

        assert context.excerpts == ()

    def test_normal_path_context_rejects_missing_marker_without_full_file_fallback(
        self,
        tmp_path,
    ) -> None:
        (tmp_path / "prd-guidance.md").write_text(
            "# PRD\n\nSECRET FULL RULE BODY\n",
            encoding="utf-8",
        )
        (tmp_path / "scenario-routing.md").write_text(
            "# Routing\n\n<!-- ai-sdlc:normal-path:start -->\nshort\n"
            "<!-- ai-sdlc:normal-path:end -->\n",
            encoding="utf-8",
        )

        with pytest.raises(RuleContextError, match="normal-path markers"):
            RulesLoader(rules_dir=tmp_path).get_normal_path_context("requirement")

    def test_normal_path_context_rejects_oversized_excerpt(self, tmp_path) -> None:
        oversized = "x" * 1201
        for name in ("prd-guidance", "scenario-routing"):
            (tmp_path / f"{name}.md").write_text(
                "# Rule\n\n<!-- ai-sdlc:normal-path:start -->\n"
                f"{oversized}\n"
                "<!-- ai-sdlc:normal-path:end -->\n",
                encoding="utf-8",
            )

        with pytest.raises(RuleContextError, match="byte limit"):
            RulesLoader(rules_dir=tmp_path).get_normal_path_context("requirement")
