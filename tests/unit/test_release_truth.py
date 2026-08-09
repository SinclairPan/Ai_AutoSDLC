"""Permanent Release Truth 的 canonical 工件与 fail-closed 决策测试。"""

from __future__ import annotations

import argparse
import hashlib
import json
import runpy
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RELEASE_TRUTH_SCRIPT = _REPO_ROOT / "scripts" / "release_truth.py"


def _api():
    from ai_sdlc.core import release_truth
    from ai_sdlc.core.release_truth_models import (
        ReleaseAssetBinding,
        ReleaseCandidateSnapshot,
        RequiredGateBinding,
    )

    return release_truth, ReleaseAssetBinding, ReleaseCandidateSnapshot, RequiredGateBinding


def _candidate(**changes: object):
    _, asset_type, candidate_type, gate_type = _api()
    assets = (
        asset_type(
            name="ai_sdlc-1.0.3-py3-none-any.whl",
            digest="sha256:" + "a" * 64,
            size_bytes=101,
            platform="python",
        ),
        asset_type(
            name="ai_sdlc-1.0.3.tar.gz",
            digest="sha256:" + "b" * 64,
            size_bytes=202,
            platform="source",
        ),
    )
    gates = (
        gate_type(
            name="Compatibility Gate Result",
            conclusion="success",
            required=True,
            protected=True,
            authority_repository="SinclairPan/Ai_AutoSDLC",
            workflow_ref="SinclairPan/Ai_AutoSDLC/.github/workflows/compatibility-gate.yml@refs/heads/main",
            workflow_run_id=9001,
            workflow_run_attempt=2,
            workflow_job_id=91001,
            head_sha="1" * 40,
            completed_at="2026-08-05T20:00:00Z",
            valid_until="2026-08-05T21:00:00Z",
            evidence_digest="sha256:" + "c" * 64,
        ),
        gate_type(
            name="Fast Gate",
            conclusion="success",
            required=True,
            protected=True,
            authority_repository="SinclairPan/Ai_AutoSDLC",
            workflow_ref="SinclairPan/Ai_AutoSDLC/.github/workflows/fast-gate.yml@refs/heads/main",
            workflow_run_id=9001,
            workflow_run_attempt=2,
            workflow_job_id=91002,
            head_sha="1" * 40,
            completed_at="2026-08-05T20:01:00Z",
            valid_until="2026-08-05T21:00:00Z",
            evidence_digest="sha256:" + "d" * 64,
        ),
    )
    values: dict[str, object] = {
        "repository": "SinclairPan/Ai_AutoSDLC",
        "admission_id": "release-admission/v1.0.3/run-9001-attempt-2/release-1234",
        "admission_digest": "sha256:" + "9" * 64,
        "draft_release_id": 1234,
        "upload_url": (
            "https://uploads.github.com/repos/SinclairPan/Ai_AutoSDLC/"
            "releases/1234/assets{?name,label}"
        ),
        "release_user_agent": "ai-sdlc-release-writer/1.0",
        "draft_release_updated_at": "2026-08-05T20:02:00Z",
        "draft": True,
        "tag_name": "v1.0.3",
        "tag_object_sha": "2" * 40,
        "commit_sha": "1" * 40,
        "tree_sha": "3" * 40,
        "tag_ruleset_id": 77,
        "tag_ruleset_digest": "sha256:" + "a" * 64,
        "required_policy_digest": "sha256:" + "e" * 64,
        "required_gate_names": ("Compatibility Gate Result", "Fast Gate"),
        "required_gates": gates,
        "workflow_run_id": 9001,
        "workflow_run_attempt": 2,
        "expected_assets": assets,
        "assets": assets,
        "release_settings_digest": "sha256:" + "f" * 64,
        "publish_workflow_ref": "SinclairPan/Ai_AutoSDLC/.github/workflows/release-build.yml@refs/heads/main",
        "evidence_cutoff_at": "2026-08-05T20:05:00Z",
    }
    values.update(changes)
    return candidate_type(**values)


def test_proof_replay_is_deterministic() -> None:
    """捕获把运行时钟或非确定顺序写入 Proof 导致同输入 digest 漂移。"""
    release_truth, *_ = _api()
    candidate = _candidate()

    first = release_truth.build_release_satisfaction_proof(candidate)
    second = release_truth.build_release_satisfaction_proof(candidate)

    assert first == second
    assert first.proof_digest.startswith("sha256:")
    assert len(first.proof_digest) == 71
    assert all(gate.workflow_job_id > 0 for gate in first.required_gates)

    gates = list(candidate.required_gates)
    gates[0] = gates[0].model_copy(update={"workflow_job_id": 91003})
    changed = release_truth.build_release_satisfaction_proof(
        candidate.model_copy(update={"required_gates": tuple(gates)})
    )

    assert changed.proof_digest != first.proof_digest
    _, _, _, gate_type = _api()
    with pytest.raises(ValueError, match="greater than or equal to 1"):
        gate_type.model_validate(
            {**candidate.required_gates[0].model_dump(), "workflow_job_id": 0}
        )


@pytest.mark.parametrize(
    ("gate_change", "message"),
    [
        ({"conclusion": "failure"}, "successful"),
        ({"required": False}, "required"),
        ({"protected": False}, "protected"),
        ({"authority_repository": "attacker/fork"}, "authority"),
        (
            {
                "workflow_ref": "SinclairPan/Ai_AutoSDLC/.github/workflows/fast-gate.yml@refs/pull/11/merge"
            },
            "protected mainline",
        ),
        ({"head_sha": "9" * 40}, "head SHA"),
        ({"workflow_run_attempt": 3}, "run attempt"),
        ({"valid_until": "2026-08-05T20:04:59Z"}, "stale"),
    ],
)
def test_proof_rejects_untrusted_required_gate(
    gate_change: dict[str, object], message: str
) -> None:
    """捕获失败、非 required、自签、错候选、错 attempt 与过期 Gate 被纳入 Proof。"""
    release_truth, *_ = _api()
    candidate = _candidate()
    gates = list(candidate.required_gates)
    gates[0] = gates[0].model_copy(update=gate_change)

    with pytest.raises(release_truth.ReleaseTruthError, match=message):
        release_truth.build_release_satisfaction_proof(
            candidate.model_copy(update={"required_gates": tuple(gates)})
        )


def test_proof_rejects_missing_or_drifted_assets() -> None:
    """捕获缺失资产或同名摘要漂移仍产生 Proof。"""
    release_truth, *_ = _api()
    candidate = _candidate()
    missing = candidate.model_copy(update={"assets": candidate.assets[:1]})
    drifted_asset = candidate.assets[0].model_copy(
        update={"digest": "sha256:" + "0" * 64}
    )
    drifted = candidate.model_copy(
        update={"assets": (drifted_asset, candidate.assets[1])}
    )

    with pytest.raises(release_truth.ReleaseTruthError, match="asset"):
        release_truth.build_release_satisfaction_proof(missing)
    with pytest.raises(release_truth.ReleaseTruthError, match="asset"):
        release_truth.build_release_satisfaction_proof(drifted)


def test_proof_rejects_noncanonical_gate_or_asset_collection() -> None:
    """捕获重复或非排序集合形成第二种内容身份。"""
    release_truth, *_ = _api()
    candidate = _candidate()

    with pytest.raises(release_truth.ReleaseTruthError, match="canonical"):
        release_truth.build_release_satisfaction_proof(
            candidate.model_copy(update={"required_gates": tuple(reversed(candidate.required_gates))})
        )
    with pytest.raises(release_truth.ReleaseTruthError, match="canonical"):
        release_truth.build_release_satisfaction_proof(
            candidate.model_copy(update={"assets": (candidate.assets[0], candidate.assets[0])})
        )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"draft_release_id": 4321}, "proof identity"),
        ({"draft_release_updated_at": "2026-08-05T20:03:00Z"}, "proof identity"),
        ({"draft": False}, "draft"),
        ({"tag_name": "v1.0.4"}, "proof identity"),
        ({"tree_sha": "8" * 40}, "proof identity"),
        ({"workflow_run_attempt": 3}, "run attempt"),
        ({"release_settings_digest": "sha256:" + "0" * 64}, "proof identity"),
    ],
)
def test_publish_claim_rejects_candidate_drift(
    change: dict[str, object], message: str
) -> None:
    """捕获 Draft 重建、tag/tree、attempt 或不可变设置变化后消费旧 Proof。"""
    release_truth, *_ = _api()
    candidate = _candidate()
    proof = release_truth.build_release_satisfaction_proof(candidate)

    with pytest.raises(release_truth.ReleaseTruthError, match=message):
        release_truth.validate_publish_claim(
            proof,
            candidate.model_copy(update=change),
            caller_workflow_ref=candidate.publish_workflow_ref,
            caller_run_id=candidate.workflow_run_id,
            caller_run_attempt=candidate.workflow_run_attempt,
            observed_at="2026-08-05T20:06:00Z",
        )


def test_publish_claim_rejects_unbound_caller() -> None:
    """捕获非唯一 protected writer 或替换 run attempt 消费 Proof。"""
    release_truth, *_ = _api()
    candidate = _candidate()
    proof = release_truth.build_release_satisfaction_proof(candidate)

    with pytest.raises(release_truth.ReleaseTruthError, match="caller"):
        release_truth.validate_publish_claim(
            proof,
            candidate,
            caller_workflow_ref="attacker/fork/.github/workflows/release.yml@main",
            caller_run_id=candidate.workflow_run_id,
            caller_run_attempt=candidate.workflow_run_attempt,
            observed_at="2026-08-05T20:06:00Z",
        )

    with pytest.raises(release_truth.ReleaseTruthError, match="expired"):
        release_truth.validate_publish_claim(
            proof,
            candidate,
            caller_workflow_ref=candidate.publish_workflow_ref,
            caller_run_id=candidate.workflow_run_id,
            caller_run_attempt=candidate.workflow_run_attempt,
            observed_at="2026-08-05T21:00:01Z",
        )
    with pytest.raises(release_truth.ReleaseTruthError, match="predates"):
        release_truth.validate_publish_claim(
            proof,
            candidate,
            caller_workflow_ref=candidate.publish_workflow_ref,
            caller_run_id=candidate.workflow_run_id,
            caller_run_attempt=candidate.workflow_run_attempt,
            observed_at="2026-08-05T19:59:59Z",
        )
    with pytest.raises(release_truth.ReleaseTruthError, match="canonical UTC"):
        release_truth.validate_publish_claim(
            proof,
            candidate,
            caller_workflow_ref=candidate.publish_workflow_ref,
            caller_run_id=candidate.workflow_run_id,
            caller_run_attempt=candidate.workflow_run_attempt,
            observed_at="2026-08-05T20:06:00+00:00",
        )


def test_proof_digest_fork_is_rejected_on_load() -> None:
    """捕获篡改 Proof 任一绑定后仍沿用原 digest。"""
    _, _, _, _ = _api()
    from ai_sdlc.core.release_truth_models import ReleaseSatisfactionProof

    proof = _api()[0].build_release_satisfaction_proof(_candidate())
    payload = proof.model_dump(mode="json")
    payload["tree_sha"] = "7" * 40

    with pytest.raises(ValueError, match="proof_digest"):
        ReleaseSatisfactionProof.model_validate(payload)


def _post_publish_api():
    from ai_sdlc.core import release_truth
    from ai_sdlc.core.release_truth_models import (
        PublishedReleaseSnapshot,
        ReleaseCertificate,
        ReleaseRevocationReceipt,
        RevocationSignal,
    )

    return (
        release_truth,
        PublishedReleaseSnapshot,
        ReleaseCertificate,
        ReleaseRevocationReceipt,
        RevocationSignal,
    )


def _published(**changes: object):
    _, published_type, *_ = _post_publish_api()
    candidate = _candidate()
    values: dict[str, object] = {
        "repository": candidate.repository,
        "github_release_id": candidate.draft_release_id,
        "github_release_url": "https://github.com/SinclairPan/Ai_AutoSDLC/releases/tag/v1.0.3",
        "tag_name": candidate.tag_name,
        "commit_sha": candidate.commit_sha,
        "tree_sha": candidate.tree_sha,
        "published": True,
        "draft": False,
        "immutable": True,
        "release_attestation_verified": True,
        "release_attestation_digest": "sha256:" + "4" * 64,
        "assets": candidate.assets,
        "revocation_generation": 0,
    }
    values.update(changes)
    return published_type(**values)


def _certificate():
    release_truth, *_ = _post_publish_api()
    return release_truth.build_release_certificate(
        release_truth.build_release_satisfaction_proof(_candidate()),
        _published(),
        release_attestation_digest="sha256:" + "4" * 64,
        issued_at="2026-08-05T20:10:00Z",
    )


def _signal(**changes: object):
    *_, signal_type = _post_publish_api()
    values: dict[str, object] = {
        "reason_code": "post_publish_smoke_failed",
        "evidence_digest": "sha256:" + "5" * 64,
        "work_item_id": "005-release-truth-incident",
        "observed_at": "2026-08-05T20:12:00Z",
    }
    values.update(changes)
    return signal_type(**values)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"published": False}, "published"),
        ({"draft": True}, "published"),
        ({"immutable": False}, "immutable"),
        ({"release_attestation_verified": False}, "attestation"),
        ({"release_attestation_digest": "sha256:" + "6" * 64}, "attestation"),
        ({"tag_name": "v1.0.4"}, "identity"),
        ({"revocation_generation": 1}, "generation"),
    ],
)
def test_certificate_rejects_unverified_or_drifted_release(
    change: dict[str, object], message: str
) -> None:
    """捕获 published_unverified、可变 Release、attestation 或身份漂移仍签发证书。"""
    release_truth, *_ = _post_publish_api()
    proof = release_truth.build_release_satisfaction_proof(_candidate())

    with pytest.raises(release_truth.ReleaseTruthError, match=message):
        release_truth.build_release_certificate(
            proof,
            _published(**change),
            release_attestation_digest="sha256:" + "4" * 64,
            issued_at="2026-08-05T20:10:00Z",
        )


def test_certificate_rejects_final_asset_drift() -> None:
    """捕获 Publish 后资产同名摘要变化仍生成 trusted Certificate。"""
    release_truth, *_ = _post_publish_api()
    proof = release_truth.build_release_satisfaction_proof(_candidate())
    assets = list(_published().assets)
    assets[0] = assets[0].model_copy(update={"digest": "sha256:" + "7" * 64})

    with pytest.raises(release_truth.ReleaseTruthError, match="asset"):
        release_truth.build_release_certificate(
            proof,
            _published(assets=tuple(assets)),
            release_attestation_digest="sha256:" + "4" * 64,
            issued_at="2026-08-05T20:10:00Z",
        )


def test_certificate_replay_is_deterministic_and_tamper_evident() -> None:
    """捕获同一发布产生多个 generation-0 身份或篡改后沿用 digest。"""
    _, _, certificate_type, *_ = _post_publish_api()
    first = _certificate()
    second = _certificate()

    assert first == second
    assert first.admission_id == _candidate().admission_id
    assert first.admission_digest == _candidate().admission_digest
    assert first.github_release_id == _candidate().draft_release_id
    assert first.upload_url == _candidate().upload_url
    assert first.tag_object_sha == _candidate().tag_object_sha
    assert first.release_user_agent == _candidate().release_user_agent
    assert first.tag_ruleset_id == _candidate().tag_ruleset_id
    assert first.tag_ruleset_digest == _candidate().tag_ruleset_digest
    payload = first.model_dump(mode="json")
    payload["admission_digest"] = "sha256:" + "8" * 64
    with pytest.raises(ValueError, match="certificate_digest"):
        certificate_type.model_validate(payload)


def test_certificate_revalidates_proof_digest_before_issue() -> None:
    """捕获调用者用未重验证的 model_copy 绕过 Proof digest 绑定。"""
    release_truth, *_ = _post_publish_api()
    proof = release_truth.build_release_satisfaction_proof(_candidate()).model_copy(
        update={"required_policy_digest": "sha256:" + "0" * 64}
    )

    with pytest.raises(release_truth.ReleaseTruthError, match="proof"):
        release_truth.build_release_certificate(
            proof,
            _published(),
            release_attestation_digest="sha256:" + "4" * 64,
            issued_at="2026-08-05T20:10:00Z",
        )


def test_revocation_receipt_generation_is_monotonic_and_replayable() -> None:
    """捕获跳号、错 predecessor 或同安全信号重放产生不同 Receipt。"""
    release_truth, *_ = _post_publish_api()
    certificate = _certificate()

    first = release_truth.build_revocation_receipt(
        certificate,
        None,
        _signal(),
        expected_generation=1,
    )
    replay = release_truth.build_revocation_receipt(
        certificate,
        None,
        _signal(),
        expected_generation=1,
    )
    second = release_truth.build_revocation_receipt(
        certificate,
        first,
        _signal(
            reason_code="late_p0",
            evidence_digest="sha256:" + "8" * 64,
            observed_at="2026-08-05T20:13:00Z",
        ),
        expected_generation=2,
    )

    assert replay == first
    assert first.generation == 1
    assert first.predecessor_receipt_digest == ""
    assert second.generation == 2
    assert second.predecessor_receipt_digest == first.receipt_digest
    with pytest.raises(release_truth.ReleaseTruthError, match="generation"):
        release_truth.build_revocation_receipt(
            certificate,
            first,
            _signal(),
            expected_generation=3,
        )


def test_revocation_receipt_rejects_certificate_fork() -> None:
    """捕获 latest Receipt 属于另一 Certificate 时继续追加 generation。"""
    release_truth, *_ = _post_publish_api()
    certificate = _certificate()
    first = release_truth.build_revocation_receipt(
        certificate, None, _signal(), expected_generation=1
    )
    fork = first.model_copy(update={"certificate_digest": "sha256:" + "0" * 64})

    with pytest.raises(release_truth.ReleaseTruthError, match="certificate"):
        release_truth.build_revocation_receipt(
            certificate,
            fork,
            _signal(),
            expected_generation=2,
        )


def test_revocation_receipt_revalidates_certificate_before_append() -> None:
    """捕获调用者用未重验证的 Certificate 内容生成 Receipt。"""
    release_truth, *_ = _post_publish_api()
    certificate = _certificate().model_copy(
        update={"github_release_url": "https://example.invalid/fork"}
    )

    with pytest.raises(release_truth.ReleaseTruthError, match="certificate"):
        release_truth.build_revocation_receipt(
            certificate,
            None,
            _signal(),
            expected_generation=1,
        )


def test_trust_reducer_transitions_from_certificate_to_receipt() -> None:
    """捕获推荐读取后 Receipt 已提交却仍沿用先前 trusted 投影。"""
    release_truth, *_ = _post_publish_api()
    certificate = _certificate()
    observed_at = "2026-08-05T20:14:00Z"
    now = datetime(2026, 8, 5, 20, 20, tzinfo=UTC)

    before = release_truth.evaluate_release_trust(
        _published(), certificate, (), observed_at=observed_at, now=now
    )
    receipt = release_truth.build_revocation_receipt(
        certificate, None, _signal(), expected_generation=1
    )
    after = release_truth.evaluate_release_trust(
        _published(revocation_generation=1),
        certificate,
        (receipt,),
        observed_at=observed_at,
        now=now,
    )

    assert before.status == "trusted"
    assert before.revocation_generation == 0
    assert after.status == "untrusted"
    assert after.reason_code == "revoked"
    assert after.revocation_generation == 1


def test_trust_reducer_returns_unknown_for_stale_projection() -> None:
    """捕获超过 15 分钟仍声称当前 trusted。"""
    release_truth, *_ = _post_publish_api()

    decision = release_truth.evaluate_release_trust(
        _published(),
        _certificate(),
        (),
        observed_at="2026-08-05T20:00:00Z",
        now=datetime(2026, 8, 5, 20, 0, tzinfo=UTC) + timedelta(minutes=15, seconds=1),
    )

    assert decision.status == "unknown"
    assert decision.reason_code == "stale_projection"


def test_trust_reducer_returns_unknown_for_receipt_gap_or_fork() -> None:
    """捕获 Receipt 缺代或同 generation 身份分叉时错误推荐。"""
    release_truth, *_ = _post_publish_api()
    certificate = _certificate()
    first = release_truth.build_revocation_receipt(
        certificate, None, _signal(), expected_generation=1
    )
    second = release_truth.build_revocation_receipt(
        certificate,
        first,
        _signal(
            reason_code="late_p0",
            evidence_digest="sha256:" + "8" * 64,
            observed_at="2026-08-05T20:13:00Z",
        ),
        expected_generation=2,
    )
    now = datetime(2026, 8, 5, 20, 20, tzinfo=UTC)

    gap = release_truth.evaluate_release_trust(
        _published(revocation_generation=2),
        certificate,
        (second,),
        observed_at="2026-08-05T20:14:00Z",
        now=now,
    )
    fork = release_truth.evaluate_release_trust(
        _published(revocation_generation=1),
        certificate,
        (first, first.model_copy(update={"receipt_digest": "sha256:" + "f" * 64})),
        observed_at="2026-08-05T20:14:00Z",
        now=now,
    )

    assert gap.status == "unknown"
    assert gap.reason_code == "receipt_chain_invalid"
    assert fork.status == "unknown"
    assert fork.reason_code == "receipt_chain_invalid"


def test_trust_reducer_does_not_trust_missing_or_mismatched_certificate() -> None:
    """捕获仅凭 GitHub Published/tag 或另一 Release 的 Certificate 推荐。"""
    release_truth, *_ = _post_publish_api()
    now = datetime(2026, 8, 5, 20, 20, tzinfo=UTC)
    missing = release_truth.evaluate_release_trust(
        _published(),
        None,
        (),
        observed_at="2026-08-05T20:14:00Z",
        now=now,
    )
    mismatch = release_truth.evaluate_release_trust(
        _published(commit_sha="9" * 40),
        _certificate(),
        (),
        observed_at="2026-08-05T20:14:00Z",
        now=now,
    )

    assert missing.status == "untrusted"
    assert missing.reason_code == "certificate_missing"
    assert mismatch.status == "unknown"
    assert mismatch.reason_code == "certificate_mismatch"


def _run_release_truth_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_RELEASE_TRUTH_SCRIPT), *args],
        cwd=_REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_internal_script_proof_and_publish_check_are_cas_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """捕获内部命令覆盖异内容 Proof 或放行漂移候选。"""
    snapshot_path = tmp_path / "candidate.json"
    proof_path = tmp_path / "release-satisfaction-proof.json"
    authority_path = tmp_path / "release-authority.json"
    admission_path = tmp_path / "release-admission.json"
    ref_path = tmp_path / "release-ref.json"
    tag_path = tmp_path / "release-tag.json"
    commit_path = tmp_path / "release-commit.json"
    rulesets_path = tmp_path / "tag-rulesets.json"
    ruleset_authority_path = tmp_path / "tag-ruleset-authority.json"
    certificate_admission_path = tmp_path / "certificate-admission.json"
    current_run_path = tmp_path / "current-release-run.json"
    run_pages_path = tmp_path / "release-run-pages.json"
    run_authority_path = tmp_path / "release-run-authority.json"
    asset_path = tmp_path / "candidate.zip"
    snapshot_path.write_text(
        json.dumps(_candidate().model_dump(mode="json")), encoding="utf-8"
    )

    first = _run_release_truth_script(
        "proof", "--snapshot", str(snapshot_path), "--output", str(proof_path)
    )
    replay = _run_release_truth_script(
        "proof", "--snapshot", str(snapshot_path), "--output", str(proof_path)
    )
    publish_check = _run_release_truth_script(
        "publish-check",
        "--proof",
        str(proof_path),
        "--snapshot",
        str(snapshot_path),
        "--caller-workflow-ref",
        _candidate().publish_workflow_ref,
        "--caller-run-id",
        str(_candidate().workflow_run_id),
        "--caller-run-attempt",
        str(_candidate().workflow_run_attempt),
        "--observed-at",
        "2026-08-05T20:06:00Z",
    )
    drifted = _candidate(
        release_settings_digest="sha256:" + "0" * 64
    ).model_dump(mode="json")
    snapshot_path.write_text(json.dumps(drifted), encoding="utf-8")
    rejected = _run_release_truth_script(
        "publish-check",
        "--proof",
        str(proof_path),
        "--snapshot",
        str(snapshot_path),
        "--caller-workflow-ref",
        _candidate().publish_workflow_ref,
        "--caller-run-id",
        str(_candidate().workflow_run_id),
        "--caller-run-attempt",
        str(_candidate().workflow_run_attempt),
        "--observed-at",
        "2026-08-05T20:06:00Z",
    )
    authority_path.write_text(
        json.dumps(
            {
                "id": 1234,
                "upload_url": (
                    "https://uploads.github.com/repos/SinclairPan/Ai_AutoSDLC/"
                    "releases/9999/assets{?name,label}"
                ),
            }
        ),
        encoding="utf-8",
    )
    asset_path.write_bytes(b"candidate")
    rejected_upload = _run_release_truth_script(
        "upload-asset",
        "--authority",
        str(authority_path),
        "--asset",
        str(asset_path),
        "--repository",
        "SinclairPan/Ai_AutoSDLC",
        "--user-agent",
        "ai-sdlc-release-writer/1.0",
    )

    actual_run = {
        "id": 9001,
        "event": "workflow_dispatch",
        "run_attempt": 1,
        "head_sha": "a" * 40,
        "head_branch": "main",
        "display_title": "release-admission|v1.0.5|g0",
        "workflow_id": 314982030,
        "path": ".github/workflows/release-build.yml",
    }
    current_run_path.write_text(json.dumps(actual_run), encoding="utf-8")
    run_pages_path.write_text(
        json.dumps([{"total_count": 1, "workflow_runs": [actual_run]}]),
        encoding="utf-8",
    )
    accepted_run_authority = _run_release_truth_script(
        "run-authority-check",
        "--current-run",
        str(current_run_path),
        "--run-pages",
        str(run_pages_path),
        "--release-tag",
        "v1.0.5",
        "--generation",
        "g0",
        "--expected-candidate-sha",
        "a" * 40,
        "--current-run-id",
        "9001",
        "--workflow-path",
        ".github/workflows/release-build.yml",
        "--output",
        str(run_authority_path),
    )
    duplicate_run = {**actual_run, "id": 9002, "head_sha": "b" * 40}
    run_pages_path.write_text(
        json.dumps(
            [
                {
                    "total_count": 2,
                    "workflow_runs": [actual_run, duplicate_run],
                }
            ]
        ),
        encoding="utf-8",
    )
    rejected_duplicate_run = _run_release_truth_script(
        "run-authority-check",
        "--current-run",
        str(current_run_path),
        "--run-pages",
        str(run_pages_path),
        "--release-tag",
        "v1.0.5",
        "--generation",
        "g0",
        "--expected-candidate-sha",
        "a" * 40,
        "--current-run-id",
        "9001",
        "--workflow-path",
        ".github/workflows/release-build.yml",
        "--output",
        str(tmp_path / "duplicate-run-authority.json"),
    )
    assert accepted_run_authority.returncode == 0, accepted_run_authority.stderr
    assert json.loads(run_authority_path.read_text(encoding="utf-8")) == {
        "candidate_sha": "a" * 40,
        "generation": "g0",
        "release_tag": "v1.0.5",
        "run_id": 9001,
        "run_name": "release-admission|v1.0.5|g0",
        "workflow_id": 314982030,
        "workflow_path": ".github/workflows/release-build.yml",
    }
    assert rejected_duplicate_run.returncode != 0
    assert "actual release generation has already been dispatched" in (
        rejected_duplicate_run.stderr
    )
    malformed_run_cases = (
        (
            {**actual_run, "run_attempt": 2},
            [{"total_count": 1, "workflow_runs": [actual_run]}],
            "current release run authority differs",
        ),
        (
            {**actual_run, "head_branch": "feature/unreviewed"},
            [{"total_count": 1, "workflow_runs": [actual_run]}],
            "current release run authority differs",
        ),
        (
            actual_run,
            [{"total_count": 2, "workflow_runs": [actual_run]}],
            "release run history is incomplete",
        ),
        (
            actual_run,
            [{"total_count": 2, "workflow_runs": [actual_run, actual_run]}],
            "release run history contains invalid identities",
        ),
    )
    for index, (current_run, pages, message) in enumerate(malformed_run_cases):
        current_run_path.write_text(json.dumps(current_run), encoding="utf-8")
        run_pages_path.write_text(json.dumps(pages), encoding="utf-8")
        rejected_malformed = _run_release_truth_script(
            "run-authority-check",
            "--current-run",
            str(current_run_path),
            "--run-pages",
            str(run_pages_path),
            "--release-tag",
            "v1.0.5",
            "--generation",
            "g0",
            "--expected-candidate-sha",
            "a" * 40,
            "--current-run-id",
            "9001",
            "--workflow-path",
            ".github/workflows/release-build.yml",
            "--output",
            str(tmp_path / f"malformed-run-authority-{index}.json"),
        )
        assert rejected_malformed.returncode != 0
        assert message in rejected_malformed.stderr

    authority_path.write_text(
        json.dumps(
            {
                "id": 1234,
                "upload_url": (
                    "https://uploads.github.com/repos/SinclairPan/Ai_AutoSDLC/"
                    "releases/1234/assets{?name,label}"
                ),
            }
        ),
        encoding="utf-8",
    )
    sent_requests: list[dict[str, object]] = []

    class FakeResponse:
        status = 201

        def read(self) -> bytes:
            return json.dumps(
                {"id": 88, "name": "candidate.zip", "size": 9}
            ).encode()

    class FakeConnection:
        def __init__(self, host: str, port: int | None, timeout: int) -> None:
            self.request: dict[str, object] = {
                "host": host,
                "port": port,
                "timeout": timeout,
                "headers": {},
                "body": bytearray(),
            }
            sent_requests.append(self.request)

        def putrequest(self, method: str, target: str) -> None:
            self.request["method"] = method
            self.request["target"] = target

        def putheader(self, name: str, value: str) -> None:
            headers = self.request["headers"]
            assert isinstance(headers, dict)
            headers[name] = value

        def endheaders(self) -> None:
            return None

        def send(self, chunk: bytes) -> None:
            body = self.request["body"]
            assert isinstance(body, bytearray)
            body.extend(chunk)

        def getresponse(self) -> FakeResponse:
            return FakeResponse()

        def close(self) -> None:
            return None

    script_globals = runpy.run_path(str(_RELEASE_TRUTH_SCRIPT))
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setattr(
        script_globals["http"].client,
        "HTTPSConnection",
        FakeConnection,
    )
    uploaded = script_globals["_upload_asset"](
        argparse.Namespace(
            authority=authority_path,
            asset=asset_path,
            repository="SinclairPan/Ai_AutoSDLC",
            name="candidate.zip",
            label="Candidate bundle",
            user_agent="ai-sdlc-release-writer/1.0",
        )
    )

    admission = {
        "admission_id": "9001:2:" + "2" * 40 + ":1234",
        "numeric_release_id": 1234,
        "upload_url": _candidate().upload_url,
        "tag_object_sha": "2" * 40,
        "commit_sha": "1" * 40,
        "expected_candidate_sha": "1" * 40,
        "tree_sha": "3" * 40,
        "workflow_ref": _candidate().publish_workflow_ref,
        "workflow_run_id": 9001,
        "workflow_run_attempt": 2,
        "user_agent": "ai-sdlc-release-writer/1.0",
        "failure_policy": "terminal-generation-burn",
    }
    admission["admission_digest"] = "sha256:" + hashlib.sha256(
        json.dumps(
            admission, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
    ).hexdigest()
    admission_path.write_text(json.dumps(admission), encoding="utf-8")
    ref_path.write_text(
        json.dumps({"ref": "refs/tags/v1.0.3", "object": {"sha": "2" * 40}}),
        encoding="utf-8",
    )
    tag_path.write_text(
        json.dumps({"sha": "2" * 40, "object": {"type": "commit", "sha": "1" * 40}}),
        encoding="utf-8",
    )
    commit_path.write_text(
        json.dumps({"sha": "1" * 40, "tree": {"sha": "3" * 40}}),
        encoding="utf-8",
    )
    authority_path.write_text(
        json.dumps(
            {
                "id": 1234,
                "tag_name": "v1.0.3",
                "target_commitish": "1" * 40,
                "upload_url": _candidate().upload_url,
                "draft": True,
            }
        ),
        encoding="utf-8",
    )
    authority_check = _run_release_truth_script(
        "authority-check",
        "--admission", str(admission_path),
        "--ref", str(ref_path),
        "--tag", str(tag_path),
        "--commit", str(commit_path),
        "--release", str(authority_path),
        "--release-tag", "v1.0.3",
        "--release-state", "draft",
    )
    drifted_candidate_admission = dict(admission)
    drifted_candidate_admission["expected_candidate_sha"] = "9" * 40
    drifted_candidate_admission.pop("admission_digest")
    drifted_candidate_admission["admission_digest"] = "sha256:" + hashlib.sha256(
        json.dumps(
            drifted_candidate_admission,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    admission_path.write_text(
        json.dumps(drifted_candidate_admission), encoding="utf-8"
    )
    rejected_candidate_admission = _run_release_truth_script(
        "authority-check",
        "--admission", str(admission_path),
        "--ref", str(ref_path),
        "--tag", str(tag_path),
        "--commit", str(commit_path),
        "--release", str(authority_path),
        "--release-tag", "v1.0.3",
        "--release-state", "draft",
    )
    admission_path.write_text(json.dumps(admission), encoding="utf-8")
    drifted_ref = {"ref": "refs/tags/v1.0.3", "object": {"sha": "7" * 40}}
    ref_path.write_text(json.dumps(drifted_ref), encoding="utf-8")
    rejected_authority = _run_release_truth_script(
        "authority-check",
        "--admission", str(admission_path),
        "--ref", str(ref_path),
        "--tag", str(tag_path),
        "--commit", str(commit_path),
        "--release", str(authority_path),
        "--release-tag", "v1.0.3",
        "--release-state", "draft",
    )
    ruleset = {
        "id": 77,
        "name": "immutable-release-tags",
        "target": "tag",
        "source": "SinclairPan/Ai_AutoSDLC",
        "source_type": "Repository",
        "enforcement": "active",
        "conditions": {
            "ref_name": {
                "include": [
                    "refs/tags/release-truth/v*/certificate/g0",
                    "refs/tags/v*",
                ],
                "exclude": [],
            }
        },
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {"type": "update"},
        ],
        "bypass_actors": [],
        "current_user_can_bypass": "never",
    }
    rulesets_path.write_text(json.dumps([ruleset]), encoding="utf-8")
    ruleset_check = _run_release_truth_script(
        "ruleset-check",
        "--rulesets", str(rulesets_path),
        "--repository", "SinclairPan/Ai_AutoSDLC",
        "--output", str(ruleset_authority_path),
    )
    ruleset["rules"].append({"type": "creation"})
    rulesets_path.write_text(json.dumps([ruleset]), encoding="utf-8")
    rejected_ruleset = _run_release_truth_script(
        "ruleset-check",
        "--rulesets", str(rulesets_path),
        "--repository", "SinclairPan/Ai_AutoSDLC",
        "--output", str(ruleset_authority_path),
    )
    ruleset["rules"].pop()
    ruleset["bypass_actors"] = [{"actor_id": 1, "actor_type": "Team"}]
    rulesets_path.write_text(json.dumps([ruleset]), encoding="utf-8")
    rejected_bypass = _run_release_truth_script(
        "ruleset-check",
        "--rulesets", str(rulesets_path),
        "--repository", "SinclairPan/Ai_AutoSDLC",
        "--output", str(ruleset_authority_path),
    )
    ruleset["bypass_actors"] = []
    ruleset["conditions"]["ref_name"]["include"] = ["refs/tags/v*"]
    rulesets_path.write_text(json.dumps([ruleset]), encoding="utf-8")
    rejected_coverage = _run_release_truth_script(
        "ruleset-check",
        "--rulesets", str(rulesets_path),
        "--repository", "SinclairPan/Ai_AutoSDLC",
        "--output", str(ruleset_authority_path),
    )
    certificate_admission = {
        "certificate_tag": "release-truth/v1.0.3/certificate/g0",
        "tag_object_sha": "2" * 40,
        "commit_sha": "1" * 40,
        "tree_sha": "3" * 40,
        "software_admission_digest": admission["admission_digest"],
        "software_proof_digest": "sha256:" + "4" * 64,
    }
    certificate_admission_path.write_text(
        json.dumps(certificate_admission), encoding="utf-8"
    )
    ref_path.write_text(
        json.dumps(
            {
                "ref": "refs/tags/release-truth/v1.0.3/certificate/g0",
                "object": {"sha": "2" * 40},
            }
        ),
        encoding="utf-8",
    )
    tag_authority_check = _run_release_truth_script(
        "tag-authority-check",
        "--admission", str(certificate_admission_path),
        "--ref", str(ref_path),
        "--tag", str(tag_path),
        "--commit", str(commit_path),
    )

    assert first.returncode == 0, first.stderr
    assert replay.returncode == 0, replay.stderr
    assert publish_check.returncode == 0, publish_check.stderr
    assert rejected.returncode != 0
    assert "proof identity" in rejected.stderr
    assert rejected_upload.returncode == 2
    assert "frozen release ID" in rejected_upload.stderr
    assert uploaded["release_id"] == 1234
    assert authority_check.returncode == 0, authority_check.stderr
    assert rejected_candidate_admission.returncode == 2
    assert "expected candidate" in rejected_candidate_admission.stderr
    assert rejected_authority.returncode == 2
    assert "live release authority differs" in rejected_authority.stderr
    assert ruleset_check.returncode == 0, ruleset_check.stderr
    assert rejected_ruleset.returncode == 2
    assert "protective tag ruleset" in rejected_ruleset.stderr
    assert rejected_bypass.returncode == 2
    assert "protective tag ruleset" in rejected_bypass.stderr
    assert rejected_coverage.returncode == 2
    assert "protective tag ruleset" in rejected_coverage.stderr
    assert tag_authority_check.returncode == 0, tag_authority_check.stderr
    assert sent_requests == [
        {
            "host": "uploads.github.com",
            "port": None,
            "timeout": 600,
            "headers": {
                "Accept": "application/vnd.github+json",
                "Authorization": "Bearer test-token",
                "User-Agent": "ai-sdlc-release-writer/1.0",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/octet-stream",
                "Content-Length": "9",
            },
            "body": bytearray(b"candidate"),
            "method": "POST",
            "target": (
                "/repos/SinclairPan/Ai_AutoSDLC/releases/1234/assets"
                "?name=candidate.zip&label=Candidate+bundle"
            ),
        }
    ]


def test_internal_script_certificate_is_idempotent_and_fork_safe(
    tmp_path: Path,
) -> None:
    """捕获 Certificate 命令重复投递覆盖同 generation 异内容。"""
    release_truth, *_ = _post_publish_api()
    proof_path = tmp_path / "proof.json"
    published_path = tmp_path / "published.json"
    certificate_path = tmp_path / "release-certificate.json"
    proof_path.write_text(
        json.dumps(
            release_truth.build_release_satisfaction_proof(_candidate()).model_dump(
                mode="json"
            )
        ),
        encoding="utf-8",
    )
    published_path.write_text(
        json.dumps(_published().model_dump(mode="json")), encoding="utf-8"
    )
    args = (
        "certificate",
        "--proof",
        str(proof_path),
        "--published",
        str(published_path),
        "--attestation-digest",
        "sha256:" + "4" * 64,
        "--issued-at",
        "2026-08-05T20:10:00Z",
        "--output",
        str(certificate_path),
    )

    first = _run_release_truth_script(*args)
    replay = _run_release_truth_script(*args)
    fork = _run_release_truth_script(
        *(
            *args[:-4],
            "--issued-at",
            "2026-08-05T20:11:00Z",
            "--output",
            str(certificate_path),
        )
    )

    assert first.returncode == 0, first.stderr
    assert replay.returncode == 0, replay.stderr
    assert fork.returncode != 0
    assert "immutable artifact fork" in fork.stderr
