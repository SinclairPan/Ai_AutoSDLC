from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from tests.unit.stage_review.test_activation_policy import _phase_one_evidence

from ai_sdlc.core.stage_review.activation import (
    advance_activation_policy,
    assess_activation,
    baseline_activation_policy,
)
from ai_sdlc.core.stage_review.activation_policy_anchor import (
    write_activation_policy_anchor,
)
from ai_sdlc.core.stage_review.ci_certificate_policy import (
    CiCertificatePolicyError,
    verify_ci_certificate_policy,
)


def test_ci_policy_requires_a_protected_baseline_anchor(tmp_path: Path) -> None:
    base, head = _repository(tmp_path, "feature.py")
    before = _status(tmp_path)

    with pytest.raises(CiCertificatePolicyError, match="protected.*anchor"):
        verify_ci_certificate_policy(
            tmp_path,
            base_commit=base,
            tested_commit=head,
        )

    assert before == _status(tmp_path)


def test_ci_policy_baseline_anchor_is_shadow_and_read_only(tmp_path: Path) -> None:
    _init_repository(tmp_path)
    write_activation_policy_anchor(tmp_path, baseline_activation_policy())
    base = _commit(tmp_path, "protected phase one policy")
    (tmp_path / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    head = _commit(tmp_path, "candidate")
    before = _status(tmp_path)

    result = verify_ci_certificate_policy(
        tmp_path,
        base_commit=base,
        tested_commit=head,
    )

    assert result.mode == "shadow"
    assert result.certificate_required is False
    assert result.risk_level == "low"
    assert result.policy_source == "protected-base-anchor"
    assert before == _status(tmp_path)


def test_ci_policy_rejects_unattested_tracked_policy_promotion(
    tmp_path: Path,
) -> None:
    base, _head = _repository(tmp_path, "README.md")
    promoted = _phase_two_policy()
    write_activation_policy_anchor(tmp_path, promoted)
    (tmp_path / "feature.py").write_text("VALUE = 2\n", encoding="utf-8")
    head = _commit(tmp_path, "promote policy and change candidate")

    with pytest.raises(
        CiCertificatePolicyError,
        match="tracked activation policy promotion is not trusted",
    ):
        verify_ci_certificate_policy(
            tmp_path,
            base_commit=base,
            tested_commit=head,
        )


def test_ci_policy_cannot_delete_base_anchor_to_downgrade(tmp_path: Path) -> None:
    _init_repository(tmp_path)
    write_activation_policy_anchor(tmp_path, _phase_two_policy())
    base = _commit(tmp_path, "protected phase two policy")
    (tmp_path / ".ai-sdlc/policies/stage-gate-activation-policy.json").unlink()
    (tmp_path / "feature.py").write_text("VALUE = 2\n", encoding="utf-8")
    head = _commit(tmp_path, "attempt policy deletion")

    result = verify_ci_certificate_policy(
        tmp_path,
        base_commit=base,
        tested_commit=head,
    )

    assert result.mode == "enforce"
    assert result.certificate_required is True
    assert result.policy_source == "protected-base-anchor"


def test_ci_policy_rejects_non_head_tested_commit(tmp_path: Path) -> None:
    _init_repository(tmp_path)
    write_activation_policy_anchor(tmp_path, baseline_activation_policy())
    base = _commit(tmp_path, "protected phase one policy")
    (tmp_path / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    head = _commit(tmp_path, "candidate")
    (tmp_path / "later.py").write_text("VALUE = 3\n", encoding="utf-8")
    _commit(tmp_path, "later")

    with pytest.raises(CiCertificatePolicyError, match="checkout HEAD"):
        verify_ci_certificate_policy(
            tmp_path,
            base_commit=base,
            tested_commit=head,
        )


def test_ci_policy_uses_merge_base_when_feature_is_behind_base(
    tmp_path: Path,
) -> None:
    _init_repository(tmp_path)
    write_activation_policy_anchor(tmp_path, baseline_activation_policy())
    common = _commit(tmp_path, "common base")
    base_branch = _git(tmp_path, "branch", "--show-current")
    _git(tmp_path, "checkout", "-b", "feature")
    (tmp_path / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    feature = _commit(tmp_path, "feature candidate")
    _git(tmp_path, "checkout", base_branch)
    (tmp_path / "base-only.py").write_text("VALUE = 2\n", encoding="utf-8")
    protected_base = _commit(tmp_path, "protected base advanced")
    _git(tmp_path, "checkout", "feature")

    result = verify_ci_certificate_policy(
        tmp_path,
        base_commit=protected_base,
        tested_commit=feature,
    )

    assert result.valid is True
    assert result.base_commit == protected_base
    assert result.source_diff_hash
    assert _git(tmp_path, "merge-base", protected_base, feature) == common


def test_ci_policy_uses_newer_protected_policy_when_feature_did_not_edit_anchor(
    tmp_path: Path,
) -> None:
    _init_repository(tmp_path)
    write_activation_policy_anchor(tmp_path, baseline_activation_policy())
    _commit(tmp_path, "common phase one policy")
    base_branch = _git(tmp_path, "branch", "--show-current")
    _git(tmp_path, "checkout", "-b", "feature")
    (tmp_path / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    feature = _commit(tmp_path, "feature candidate")
    _git(tmp_path, "checkout", base_branch)
    write_activation_policy_anchor(tmp_path, _phase_two_policy())
    protected_base = _commit(tmp_path, "protected base advanced to phase two")
    _git(tmp_path, "checkout", "feature")

    result = verify_ci_certificate_policy(
        tmp_path,
        base_commit=protected_base,
        tested_commit=feature,
    )

    assert result.mode == "enforce"
    assert result.certificate_required is True
    assert result.policy_source == "protected-base-anchor"
    assert result.policy_digest == _phase_two_policy().policy_digest


def test_ci_policy_rejects_commits_without_a_common_history(tmp_path: Path) -> None:
    _init_repository(tmp_path)
    write_activation_policy_anchor(tmp_path, baseline_activation_policy())
    base = _commit(tmp_path, "protected history")
    _git(tmp_path, "checkout", "--orphan", "unrelated")
    (tmp_path / "unrelated.py").write_text("VALUE = 1\n", encoding="utf-8")
    head = _commit(tmp_path, "unrelated candidate")

    with pytest.raises(CiCertificatePolicyError, match="no merge base"):
        verify_ci_certificate_policy(
            tmp_path,
            base_commit=base,
            tested_commit=head,
        )


def _phase_two_policy():
    baseline = baseline_activation_policy()
    promoted = advance_activation_policy(
        baseline,
        assess_activation(baseline, _phase_one_evidence()),
    )
    assert promoted is not None
    return promoted


def _repository(root: Path, changed_path: str) -> tuple[str, str]:
    _init_repository(root)
    (root / "README.md").write_text("base\n", encoding="utf-8")
    base = _commit(root, "base")
    target = root / changed_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("VALUE = 1\n", encoding="utf-8")
    return base, _commit(root, "candidate")


def _init_repository(root: Path) -> None:
    _git(root, "init")
    _git(root, "config", "user.email", "ci@example.com")
    _git(root, "config", "user.name", "CI Test")


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "--all")
    _git(root, "commit", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _status(root: Path) -> str:
    return _git(root, "status", "--porcelain=v1", "--untracked-files=all")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
