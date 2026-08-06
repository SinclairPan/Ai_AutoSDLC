"""Unit tests for installed runtime update advisor."""

from __future__ import annotations

import base64
import hashlib
import json
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_sdlc.core.release_truth import build_release_satisfaction_proof
from ai_sdlc.core.release_truth_models import (
    ReleaseAssetBinding,
    ReleaseCandidateSnapshot,
    ReleaseCertificate,
    ReleaseRevocationReceipt,
    ReleaseTrustDecision,
    RequiredGateBinding,
)
from ai_sdlc.core.update_advisor import (
    AUTO_NOTICE_REPEAT_INTERVAL,
    NOTICE_ACTIONABLE,
    NOTICE_LIGHT,
    _cache_path,
    _fetch_public_release_pages,
    ack_notice,
    detect_runtime_identity,
    evaluate_update_advisor,
    fetch_release_truth_github,
    notice_already_acknowledged,
    notice_recently_rendered,
    record_notice_rendered,
)


def _force_installed(monkeypatch, tmp_path, *, channel: str = "github-archive") -> None:
    monkeypatch.setenv("AI_SDLC_UPDATE_ADVISOR_TEST_INSTALLED", "1")
    monkeypatch.setenv("AI_SDLC_UPDATE_ADVISOR_TEST_VERSION", "1.0.0")
    monkeypatch.setenv("AI_SDLC_UPDATE_ADVISOR_TEST_CHANNEL", channel)
    monkeypatch.setenv("AI_SDLC_UPDATE_ADVISOR_CACHE_DIR", str(tmp_path))


def _latest_release() -> dict[str, object]:
    return {
        "tag_name": "v1.0.1",
        "html_url": "https://github.com/SinclairPan/Ai_AutoSDLC/releases/tag/v1.0.1",
        "draft": False,
        "prerelease": False,
        "immutable": True,
    }


def _truth(
    *,
    status: str = "trusted",
    reason_code: str = "certificate_current",
    observed_at: str = "2026-05-01T12:00:00Z",
    generation: int = 0,
) -> ReleaseTrustDecision:
    return ReleaseTrustDecision(
        status=status,
        reason_code=reason_code,
        certificate_digest="sha256:" + "9" * 64,
        revocation_generation=generation,
        observed_at=observed_at,
    )


def _public_truth_fixture(*, receipt_generation: int = 0):
    repository = "SinclairPan/Ai_AutoSDLC"
    tag = "v1.0.1"
    commit = "1" * 40
    tree = "2" * 40
    software_bytes = b"release-archive"
    software_asset = ReleaseAssetBinding(
        name="ai-sdlc-offline-1.0.1-linux-amd64.tar.gz",
        digest="sha256:" + hashlib.sha256(software_bytes).hexdigest(),
        size_bytes=len(software_bytes),
        platform="linux-amd64",
    )
    gate = RequiredGateBinding(
        name="Release Assurance",
        conclusion="success",
        required=True,
        protected=True,
        authority_repository=repository,
        workflow_ref=(
            f"{repository}/.github/workflows/release-build.yml@refs/heads/main"
        ),
        workflow_run_id=100,
        workflow_run_attempt=1,
        head_sha=commit,
        completed_at="2026-05-01T11:59:00Z",
        valid_until="2026-05-01T12:14:00Z",
        evidence_digest="sha256:" + "3" * 64,
    )
    candidate = ReleaseCandidateSnapshot(
        repository=repository,
        draft_release_id=10,
        draft_release_updated_at="2026-05-01T11:58:00Z",
        draft=True,
        tag_name=tag,
        tag_object_sha=commit,
        commit_sha=commit,
        tree_sha=tree,
        required_policy_digest="sha256:" + "4" * 64,
        required_gate_names=(gate.name,),
        required_gates=(gate,),
        workflow_run_id=100,
        workflow_run_attempt=1,
        expected_assets=(software_asset,),
        assets=(software_asset,),
        release_settings_digest="sha256:" + "5" * 64,
        publish_workflow_ref=gate.workflow_ref,
        evidence_cutoff_at="2026-05-01T12:00:00Z",
    )
    proof = build_release_satisfaction_proof(candidate)
    certificate = ReleaseCertificate(
        repository=repository,
        github_release_id=10,
        github_release_url=(
            "https://github.com/SinclairPan/Ai_AutoSDLC/releases/tag/v1.0.1"
        ),
        tag_name=tag,
        commit_sha=commit,
        tree_sha=tree,
        proof_digest=proof.proof_digest,
        release_attestation_digest="sha256:" + "6" * 64,
        assets=(software_asset,),
        issued_at="2026-05-01T12:00:00Z",
    )
    proof_bytes = json.dumps(proof.model_dump(mode="json"), sort_keys=True).encode()
    certificate_bytes = json.dumps(
        certificate.model_dump(mode="json"), sort_keys=True
    ).encode()
    certificate_digest = hashlib.sha256(certificate_bytes).hexdigest()
    attestation_statement = {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [
            {
                "name": "release-certificate.json",
                "digest": {"sha256": certificate_digest},
            }
        ],
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {
            "buildDefinition": {
                "buildType": "https://actions.github.io/buildtypes/workflow/v1",
                "externalParameters": {
                    "workflow": {
                        "ref": "refs/heads/main",
                        "repository": f"https://github.com/{repository}",
                        "path": ".github/workflows/release-build.yml",
                    }
                },
                "internalParameters": {
                    "github": {"event_name": "workflow_dispatch"}
                },
                "resolvedDependencies": [
                    {
                        "uri": f"git+https://github.com/{repository}@refs/heads/main",
                        "digest": {"gitCommit": commit},
                    }
                ],
            },
            "runDetails": {
                "builder": {"id": f"https://github.com/{gate.workflow_ref}"},
                "metadata": {
                    "invocationId": (
                        f"https://github.com/{repository}/actions/runs/"
                        f"{candidate.workflow_run_id}/attempts/{candidate.workflow_run_attempt}"
                    )
                },
            },
        },
    }
    attestation_url = (
        f"https://api.github.com/repos/{repository}/attestations/"
        f"sha256:{certificate_digest}?per_page=100"
    )
    attestations = {
        attestation_url: {
            "attestations": [
                {
                    "repository_id": 1_303_749_243,
                    "bundle": {
                        "mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json",
                        "dsseEnvelope": {
                            "payloadType": "application/vnd.in-toto+json",
                            "payload": base64.b64encode(
                                json.dumps(attestation_statement).encode()
                            ).decode(),
                            "signatures": [{"keyid": "", "sig": "signature"}],
                        },
                        "verificationMaterial": {
                            "certificate": {"rawBytes": "certificate"},
                            "tlogEntries": [{"logIndex": "1"}],
                        },
                    },
                }
            ]
        }
    }
    software_release = {
        **_latest_release(),
        "id": 10,
        "assets": [
            {
                "name": software_asset.name,
                "digest": software_asset.digest,
                "size": software_asset.size_bytes,
                "browser_download_url": "https://example.test/software",
            },
            {
                "name": "release-satisfaction-proof.json",
                "digest": "sha256:" + hashlib.sha256(proof_bytes).hexdigest(),
                "size": len(proof_bytes),
                "browser_download_url": "https://example.test/proof",
            },
        ],
    }
    certificate_release = {
        "tag_name": f"release-truth/{tag}/certificate/g0",
        "draft": False,
        "prerelease": True,
        "immutable": True,
        "assets": [
            {
                "name": "release-certificate.json",
                "digest": "sha256:" + hashlib.sha256(certificate_bytes).hexdigest(),
                "size": len(certificate_bytes),
                "browser_download_url": "https://example.test/certificate",
            }
        ],
    }
    release_pages: list[dict[str, object]] = []
    bytes_by_url = {
        "https://example.test/software": software_bytes,
        "https://example.test/proof": proof_bytes,
        "https://example.test/certificate": certificate_bytes,
    }
    if receipt_generation:
        receipt = ReleaseRevocationReceipt(
            repository=repository,
            tag_name=tag,
            certificate_digest=certificate.certificate_digest,
            generation=receipt_generation,
            predecessor_receipt_digest="sha256:" + "7" * 64,
            reason_code="post_publish_smoke_failed",
            evidence_digest="sha256:" + "8" * 64,
            work_item_id="release-smoke-100",
            observed_at="2026-05-01T12:01:00Z",
        )
        receipt_bytes = json.dumps(
            receipt.model_dump(mode="json"), sort_keys=True
        ).encode()
        receipt_url = "https://example.test/receipt"
        release_pages.append(
            {
                "tag_name": f"release-truth/{tag}/revocation/g{receipt_generation}",
                "draft": False,
                "prerelease": True,
                "immutable": True,
                "assets": [
                    {
                        "name": "release-revocation-receipt.json",
                        "digest": "sha256:" + hashlib.sha256(receipt_bytes).hexdigest(),
                        "size": len(receipt_bytes),
                        "browser_download_url": receipt_url,
                    }
                ],
            }
        )
        bytes_by_url[receipt_url] = receipt_bytes
    release_pages.append({"id": software_release["id"], "tag_name": tag})
    workflow_run = {
        "id": candidate.workflow_run_id,
        "run_attempt": candidate.workflow_run_attempt,
        "status": "completed",
        "conclusion": "success",
        "event": "workflow_dispatch",
        "head_sha": commit,
        "head_branch": "main",
        "path": ".github/workflows/release-build.yml",
        "head_repository": {"full_name": repository},
    }
    workflow_runs = {
        (
            f"https://api.github.com/repos/{repository}/actions/runs/"
            f"{candidate.workflow_run_id}/attempts/{candidate.workflow_run_attempt}"
        ): workflow_run
    }
    return (
        software_release,
        certificate_release,
        release_pages,
        bytes_by_url,
        workflow_runs,
        attestations,
    )


def test_source_runtime_fails_closed(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AI_SDLC_SOURCE_RUNTIME", "1")
    monkeypatch.setenv("AI_SDLC_UPDATE_ADVISOR_CACHE_DIR", str(tmp_path))

    identity = detect_runtime_identity()
    evaluation = evaluate_update_advisor()

    assert identity.installed_runtime is False
    assert evaluation.refresh_attempted is False
    assert evaluation.refresh_result == "disabled"
    assert evaluation.eligible_notice_classes == ()


def test_installed_module_invocation_is_installed_runtime(monkeypatch, tmp_path) -> None:
    site_packages = tmp_path / "site-packages"
    executable = site_packages / "ai_sdlc" / "__main__.py"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")

    class FakeDistribution:
        version = "1.1.0"

        def read_text(self, name: str) -> str | None:
            return None

        def locate_file(self, path: str) -> Path:
            return site_packages / path

    monkeypatch.setattr("sys.argv", [str(executable), "self-update", "check"])
    monkeypatch.setattr(
        "ai_sdlc.core.update_advisor.metadata.distribution",
        lambda name: FakeDistribution(),
    )
    monkeypatch.setenv("AI_SDLC_UPDATE_ADVISOR_CACHE_DIR", str(tmp_path / "cache"))

    identity = detect_runtime_identity()

    assert identity.installed_runtime is True
    assert identity.installed_version == "1.1.0"
    assert identity.reason_code == "installed_runtime"


def test_github_archive_installed_runtime_gets_actionable_notice(
    monkeypatch, tmp_path
) -> None:
    _force_installed(monkeypatch, tmp_path, channel="github-archive")
    monkeypatch.setenv("AI_SDLC_UPDATE_ADVISOR_TEST_LATEST_VERSION", "v1.0.1")

    evaluation = evaluate_update_advisor(
        now=datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    )

    assert evaluation.refresh_attempted is True
    assert evaluation.refresh_result == "success"
    assert evaluation.freshness == "fresh"
    assert evaluation.upstream_latest_version == "1.0.1"
    assert evaluation.channel_latest_version == "1.0.1"
    assert NOTICE_LIGHT in evaluation.eligible_notice_classes
    assert NOTICE_ACTIONABLE in evaluation.eligible_notice_classes
    assert evaluation.upgrade_command == "ai-sdlc self-update check"


def test_cache_path_sanitizes_runtime_identity_for_windows(monkeypatch, tmp_path) -> None:
    _force_installed(monkeypatch, tmp_path, channel="github-archive")

    identity = detect_runtime_identity()

    assert identity.runtime_identity.startswith("sha256:")
    assert ":" not in _cache_path(identity).name


def test_unknown_installed_channel_still_gets_actionable_update(
    monkeypatch, tmp_path
) -> None:
    _force_installed(monkeypatch, tmp_path, channel="unknown")
    monkeypatch.setenv("AI_SDLC_UPDATE_ADVISOR_TEST_LATEST_VERSION", "v1.0.1")

    evaluation = evaluate_update_advisor()

    assert evaluation.upstream_latest_version == "1.0.1"
    assert evaluation.channel_latest_version == "1.0.1"
    assert NOTICE_LIGHT in evaluation.eligible_notice_classes
    assert NOTICE_ACTIONABLE in evaluation.eligible_notice_classes
    assert evaluation.upgrade_command == "ai-sdlc self-update check"


def test_failure_backoff_prevents_repeated_refresh(monkeypatch, tmp_path) -> None:
    _force_installed(monkeypatch, tmp_path)
    calls = 0

    def fail_fetch(timeout: float) -> dict[str, object]:
        nonlocal calls
        calls += 1
        raise OSError("network unavailable")

    first = evaluate_update_advisor(
        now=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        fetch_latest=fail_fetch,
    )
    second = evaluate_update_advisor(
        now=datetime(2026, 5, 1, 13, 0, tzinfo=UTC),
        fetch_latest=fail_fetch,
    )

    assert first.refresh_attempted is True
    assert first.refresh_result == "network_error"
    assert second.refresh_attempted is False
    assert second.refresh_result == "backoff"
    assert calls == 1


def test_explicit_check_can_ignore_failure_backoff(monkeypatch, tmp_path) -> None:
    _force_installed(monkeypatch, tmp_path)
    calls = 0

    def fail_fetch(timeout: float) -> dict[str, object]:
        nonlocal calls
        calls += 1
        raise OSError("network unavailable")

    now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    first = evaluate_update_advisor(now=now, fetch_latest=fail_fetch)
    second = evaluate_update_advisor(
        now=now + timedelta(hours=1),
        fetch_latest=fail_fetch,
        ignore_failure_backoff=True,
    )

    assert first.refresh_attempted is True
    assert second.refresh_attempted is True
    assert second.refresh_result == "network_error"
    assert calls == 2


def test_stale_cache_still_emits_known_update_notice_without_refresh(
    monkeypatch, tmp_path
) -> None:
    """保留既有 baseline 身份；Release Truth TTL 现在覆盖旧 24 小时回退。"""
    _force_installed(monkeypatch, tmp_path)
    monkeypatch.setenv("AI_SDLC_UPDATE_ADVISOR_TEST_LATEST_VERSION", "v1.0.1")
    now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    evaluate_update_advisor(now=now)

    stale = evaluate_update_advisor(now=now + timedelta(days=2), allow_refresh=False)

    assert stale.freshness == "stale_but_usable"
    assert stale.refresh_attempted is False
    assert stale.release_trust == "unknown"
    assert stale.release_truth_freshness == "expired"
    assert stale.eligible_notice_classes == ()
    assert stale.upgrade_command is None


def test_rendered_notice_throttles_without_acknowledging(
    monkeypatch, tmp_path
) -> None:
    _force_installed(monkeypatch, tmp_path)
    monkeypatch.setenv("AI_SDLC_UPDATE_ADVISOR_TEST_LATEST_VERSION", "v1.0.1")
    now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    evaluation = evaluate_update_advisor(now=now)

    recorded = record_notice_rendered(NOTICE_ACTIONABLE, "1.0.1", now=now)

    assert recorded is True
    assert notice_already_acknowledged(evaluation, NOTICE_ACTIONABLE) is False
    assert notice_recently_rendered(
        evaluation,
        NOTICE_ACTIONABLE,
        now=now + AUTO_NOTICE_REPEAT_INTERVAL - timedelta(seconds=1),
    )
    assert not notice_recently_rendered(
        evaluation,
        NOTICE_ACTIONABLE,
        now=now + AUTO_NOTICE_REPEAT_INTERVAL + timedelta(seconds=1),
    )


def test_ack_notice_records_notice_version(monkeypatch, tmp_path) -> None:
    _force_installed(monkeypatch, tmp_path)
    monkeypatch.setenv("AI_SDLC_UPDATE_ADVISOR_TEST_LATEST_VERSION", "v1.0.1")
    evaluation = evaluate_update_advisor()

    ack = ack_notice(NOTICE_LIGHT, "1.0.1")

    assert ack.ack_recorded is True
    assert notice_already_acknowledged(evaluation, NOTICE_LIGHT) is True


def test_fresh_public_release_truth_allows_actionable_notice(
    monkeypatch, tmp_path
) -> None:
    """捕获忽略可信 Certificate/Receipt 投影，仅凭 latest tag 推荐更新。"""
    _force_installed(monkeypatch, tmp_path)
    now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

    evaluation = evaluate_update_advisor(
        now=now,
        fetch_latest=lambda timeout: _latest_release(),
        fetch_release_truth=lambda release, timeout, observed: _truth(),
    )

    assert evaluation.release_trust == "trusted"
    assert evaluation.release_truth_freshness == "fresh"
    assert evaluation.revocation_generation == 0
    assert NOTICE_ACTIONABLE in evaluation.eligible_notice_classes
    assert evaluation.to_machine_dict()["release_trust"] == "trusted"


def test_missing_or_invalid_release_truth_blocks_all_update_notices(
    monkeypatch, tmp_path
) -> None:
    """捕获 Certificate 缺失或 Receipt gap/fork 时静默回退到 tag。"""
    _force_installed(monkeypatch, tmp_path)
    now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

    missing = evaluate_update_advisor(
        now=now,
        fetch_latest=lambda timeout: _latest_release(),
        fetch_release_truth=lambda release, timeout, observed: _truth(
            status="untrusted", reason_code="certificate_missing"
        ),
    )
    monkeypatch.setenv("AI_SDLC_UPDATE_ADVISOR_CACHE_DIR", str(tmp_path / "fork"))
    fork = evaluate_update_advisor(
        now=now + timedelta(seconds=1),
        fetch_latest=lambda timeout: _latest_release(),
        fetch_release_truth=lambda release, timeout, observed: _truth(
            status="unknown", reason_code="receipt_chain_invalid", generation=2
        ),
        ignore_failure_backoff=True,
    )

    assert missing.release_trust == "untrusted"
    assert missing.eligible_notice_classes == ()
    assert missing.upgrade_command is None
    assert fork.release_trust == "unknown"
    assert fork.revocation_generation == 2
    assert fork.eligible_notice_classes == ()


def test_release_truth_expires_after_fifteen_minutes_without_tag_fallback(
    monkeypatch, tmp_path
) -> None:
    """捕获 24 小时 update cache 掩盖 15 分钟 Release Truth TTL。"""
    _force_installed(monkeypatch, tmp_path)
    now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    evaluate_update_advisor(
        now=now,
        fetch_latest=lambda timeout: _latest_release(),
        fetch_release_truth=lambda release, timeout, observed: _truth(),
    )

    expired = evaluate_update_advisor(
        now=now + timedelta(minutes=15, seconds=1),
        allow_refresh=False,
    )

    assert expired.freshness == "fresh"
    assert expired.release_trust == "unknown"
    assert expired.release_truth_freshness == "expired"
    assert expired.eligible_notice_classes == ()
    assert expired.upgrade_command is None


def test_offline_truth_refresh_fails_closed_after_truth_ttl(
    monkeypatch, tmp_path
) -> None:
    """捕获 Release Truth 过期后网络失败仍沿用先前 trusted 结论。"""
    _force_installed(monkeypatch, tmp_path)
    now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    evaluate_update_advisor(
        now=now,
        fetch_latest=lambda timeout: _latest_release(),
        fetch_release_truth=lambda release, timeout, observed: _truth(),
    )

    def offline(timeout: float) -> dict[str, object]:
        raise OSError("offline")

    evaluation = evaluate_update_advisor(
        now=now + timedelta(minutes=16),
        fetch_latest=offline,
        ignore_failure_backoff=True,
    )

    assert evaluation.refresh_attempted is True
    assert evaluation.refresh_result == "network_error"
    assert evaluation.release_trust == "unknown"
    assert evaluation.release_truth_freshness == "expired"
    assert evaluation.eligible_notice_classes == ()


def test_public_truth_loader_validates_immutable_assets_and_receipt_gaps(
    monkeypatch,
) -> None:
    """捕获公开 loader 不核验 GitHub asset digest，或漏代 Receipt 仍 trusted。"""
    software, certificate, pages, content, workflow_runs, attestations = (
        _public_truth_fixture()
    )
    (
        gap_software,
        gap_certificate,
        gap_pages,
        gap_content,
        gap_workflow_runs,
        gap_attestations,
    ) = _public_truth_fixture(receipt_generation=2)

    def install_fixture(cert, releases, blobs, runs, provenance) -> None:
        def fetch_json(url: str, timeout: float):
            if "/actions/runs/" in url:
                return runs[url]
            if "/attestations/" in url:
                return provenance[url]
            return releases if "?per_page=" in url else cert

        monkeypatch.setattr(
            "ai_sdlc.core.update_advisor._fetch_public_json",
            fetch_json,
        )
        monkeypatch.setattr(
            "ai_sdlc.core.update_advisor._fetch_public_bytes",
            lambda url, timeout: blobs[url],
        )

    install_fixture(certificate, pages, content, workflow_runs, attestations)
    trusted = fetch_release_truth_github(
        software, 1.0, "2026-05-01T12:02:00Z"
    )
    install_fixture(
        gap_certificate,
        gap_pages,
        gap_content,
        gap_workflow_runs,
        gap_attestations,
    )
    gap = fetch_release_truth_github(
        gap_software, 1.0, "2026-05-01T12:02:00Z"
    )

    assert trusted.status == "trusted"
    assert trusted.revocation_generation == 0
    assert gap.status == "unknown"
    assert gap.reason_code == "receipt_chain_invalid"


def test_public_truth_loader_rejects_unverified_publish_workflow_authority(
    monkeypatch,
) -> None:
    """捕获公开 loader 仅复制 Certificate attestation 摘要并自证 trusted。"""
    software, certificate, pages, content, workflow_runs, attestations = (
        _public_truth_fixture()
    )
    run_url, run = next(iter(workflow_runs.items()))
    workflow_runs[run_url] = {**run, "conclusion": "failure"}

    def fetch_json(url: str, timeout: float):
        if "/actions/runs/" in url:
            return workflow_runs[url]
        if "/attestations/" in url:
            return attestations[url]
        return pages if "?per_page=" in url else certificate

    monkeypatch.setattr(
        "ai_sdlc.core.update_advisor._fetch_public_json",
        fetch_json,
    )
    monkeypatch.setattr(
        "ai_sdlc.core.update_advisor._fetch_public_bytes",
        lambda url, timeout: content[url],
    )

    with pytest.raises(ValueError, match="workflow authority"):
        fetch_release_truth_github(software, 1.0, "2026-05-01T12:02:00Z")


def test_public_truth_loader_rejects_certificate_without_protected_provenance(
    monkeypatch,
) -> None:
    """捕获任意 contents:write 发布者复用历史成功 run 自证 Certificate。"""
    software, certificate, pages, content, workflow_runs, attestations = (
        _public_truth_fixture()
    )
    attestation_url, response = next(iter(attestations.items()))
    envelope = response["attestations"][0]["bundle"]["dsseEnvelope"]
    statement = json.loads(base64.b64decode(envelope["payload"]))
    statement["predicate"]["runDetails"]["metadata"]["invocationId"] = (
        "https://github.com/SinclairPan/Ai_AutoSDLC/actions/runs/99/attempts/1"
    )
    envelope["payload"] = base64.b64encode(json.dumps(statement).encode()).decode()

    def fetch_json(url: str, timeout: float):
        if "/actions/runs/" in url:
            return workflow_runs[url]
        if "/attestations/" in url:
            assert url == attestation_url
            return response
        return pages if "?per_page=" in url else certificate

    monkeypatch.setattr(
        "ai_sdlc.core.update_advisor._fetch_public_json",
        fetch_json,
    )
    monkeypatch.setattr(
        "ai_sdlc.core.update_advisor._fetch_public_bytes",
        lambda url, timeout: content[url],
    )

    with pytest.raises(ValueError, match="artifact attestation"):
        fetch_release_truth_github(software, 1.0, "2026-05-01T12:02:00Z")


def test_public_release_pages_continue_past_ten_pages_and_stop_at_software_release(
    monkeypatch,
) -> None:
    """捕获固定十页上限导致长期运行后所有公开 Release Truth 失效。"""
    software_release_id = 42
    calls: list[int] = []

    def fetch_page(url: str, timeout: float) -> list[dict[str, object]]:
        page = int(url.rsplit("page=", 1)[1])
        calls.append(page)
        if page <= 10:
            return [
                {"id": page * 1_000 + offset, "tag_name": f"evidence-{page}-{offset}"}
                for offset in range(100)
            ]
        return [
            {"id": software_release_id, "tag_name": "v1.0.1"},
            {"id": 1, "tag_name": "older-release"},
        ]

    monkeypatch.setattr(
        "ai_sdlc.core.update_advisor._fetch_public_json",
        fetch_page,
    )

    releases = _fetch_public_release_pages(
        1.0,
        stop_release_id=software_release_id,
    )

    assert calls == list(range(1, 12))
    assert len(releases) == 1_001
    assert releases[-1]["id"] == software_release_id


def test_public_truth_loader_marks_missing_certificate_untrusted(monkeypatch) -> None:
    """捕获公开 Certificate 404 被误当作可回退 tag 或永久网络故障。"""
    software, _, _, content, _, _ = _public_truth_fixture()
    monkeypatch.setattr(
        "ai_sdlc.core.update_advisor._fetch_public_bytes",
        lambda url, timeout: content[url],
    )

    def missing(url: str, timeout: float):
        raise urllib.error.HTTPError(url, 404, "not found", {}, None)

    monkeypatch.setattr(
        "ai_sdlc.core.update_advisor._fetch_public_json",
        missing,
    )

    decision = fetch_release_truth_github(
        software, 1.0, "2026-05-01T12:02:00Z"
    )

    assert decision.status == "untrusted"
    assert decision.reason_code == "certificate_missing"
