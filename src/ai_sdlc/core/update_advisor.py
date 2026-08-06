"""Installed-runtime update advisor for the AI-SDLC CLI."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from importlib import metadata
from pathlib import Path
from typing import Any

from ai_sdlc.core.release_truth import evaluate_release_trust
from ai_sdlc.core.release_truth_models import (
    PublishedReleaseSnapshot,
    ReleaseCertificate,
    ReleaseRevocationReceipt,
    ReleaseSatisfactionProof,
    ReleaseTrustDecision,
)

PROTOCOL_VERSION = "1"
PACKAGE_NAME = "ai-sdlc"
GITHUB_RELEASES_LATEST_URL = (
    "https://api.github.com/repos/SinclairPan/Ai_AutoSDLC/releases/latest"
)
GITHUB_REPOSITORY = "SinclairPan/Ai_AutoSDLC"
GITHUB_HOSTED_RUNNER_BUILDER_ID = "https://github.com/actions/runner/github-hosted"

NOTICE_LIGHT = "light_upstream_release_notice"
NOTICE_ACTIONABLE = "actionable_cli_update_notice"
NOTICE_FAILED = "check_failed_notice"

REFRESH_NOT_NEEDED = "not_needed"
REFRESH_SUCCESS = "success"
REFRESH_BACKOFF = "backoff"
REFRESH_NETWORK_ERROR = "network_error"
REFRESH_PARSE_ERROR = "parse_error"
REFRESH_TIMEOUT = "timeout"
REFRESH_DISABLED = "disabled"

FRESH_WINDOW = timedelta(hours=24)
EXPIRED_WINDOW = timedelta(days=7)
DEFAULT_TIMEOUT_SECONDS = 1.5
EXPLICIT_CHECK_TIMEOUT_SECONDS = 20.0
AUTO_NOTICE_REPEAT_INTERVAL = timedelta(hours=6)
RELEASE_TRUTH_FRESHNESS_TTL = timedelta(minutes=15)

FetchLatest = Callable[[float], dict[str, Any]]
FetchReleaseTruth = Callable[[dict[str, Any], float, str], ReleaseTrustDecision]


@dataclass(frozen=True)
class _TimeoutBudget:
    deadline: float

    @classmethod
    def start(cls, timeout_seconds: float) -> _TimeoutBudget:
        if timeout_seconds <= 0:
            raise TimeoutError("update refresh timeout budget is exhausted")
        return cls(deadline=time.monotonic() + timeout_seconds)

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("update refresh timeout budget is exhausted")
        return remaining


@dataclass(frozen=True)
class RuntimeIdentity:
    installed_runtime: bool
    binding_verified: bool
    runtime_identity: str
    installed_version: str | None
    install_channel: str
    executable_path: str
    distribution_path: str
    reason_code: str

    def to_machine_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "installed_runtime": self.installed_runtime,
            "binding_verified": self.binding_verified,
            "runtime_identity": self.runtime_identity,
            "installed_version": self.installed_version,
            "install_channel": self.install_channel,
            "executable_path": self.executable_path,
            "reason_code": self.reason_code,
        }


@dataclass
class UpdateCache:
    schema_version: int = 1
    runtime_identity: str = ""
    installed_version: str | None = None
    install_channel: str = "unknown"
    upstream_latest_version: str | None = None
    channel_latest_version: str | None = None
    release_url: str | None = None
    release_trust: str = "unknown"
    release_truth_reason_code: str = "release_truth_unavailable"
    release_truth_observed_at: str | None = None
    release_certificate_digest: str | None = None
    revocation_generation: int = 0
    last_checked_at: str | None = None
    last_success_checked_at: str | None = None
    last_check_status: str | None = None
    failure_count: int = 0
    failure_backoff_until: str | None = None
    notice_state: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: object) -> UpdateCache:
        if not isinstance(raw, dict):
            return cls()
        notice_state = raw.get("notice_state")
        return cls(
            schema_version=_as_int(raw.get("schema_version"), 1),
            runtime_identity=str(raw.get("runtime_identity") or ""),
            installed_version=_optional_str(raw.get("installed_version")),
            install_channel=str(raw.get("install_channel") or "unknown"),
            upstream_latest_version=_optional_str(raw.get("upstream_latest_version")),
            channel_latest_version=_optional_str(raw.get("channel_latest_version")),
            release_url=_optional_str(raw.get("release_url")),
            release_trust=str(raw.get("release_trust") or "unknown"),
            release_truth_reason_code=str(
                raw.get("release_truth_reason_code") or "release_truth_unavailable"
            ),
            release_truth_observed_at=_optional_str(
                raw.get("release_truth_observed_at")
            ),
            release_certificate_digest=_optional_str(
                raw.get("release_certificate_digest")
            ),
            revocation_generation=_as_int(raw.get("revocation_generation"), 0),
            last_checked_at=_optional_str(raw.get("last_checked_at")),
            last_success_checked_at=_optional_str(raw.get("last_success_checked_at")),
            last_check_status=_optional_str(raw.get("last_check_status")),
            failure_count=_as_int(raw.get("failure_count"), 0),
            failure_backoff_until=_optional_str(raw.get("failure_backoff_until")),
            notice_state=notice_state if isinstance(notice_state, dict) else {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "runtime_identity": self.runtime_identity,
            "installed_version": self.installed_version,
            "install_channel": self.install_channel,
            "upstream_latest_version": self.upstream_latest_version,
            "channel_latest_version": self.channel_latest_version,
            "release_url": self.release_url,
            "release_trust": self.release_trust,
            "release_truth_reason_code": self.release_truth_reason_code,
            "release_truth_observed_at": self.release_truth_observed_at,
            "release_certificate_digest": self.release_certificate_digest,
            "revocation_generation": self.revocation_generation,
            "last_checked_at": self.last_checked_at,
            "last_success_checked_at": self.last_success_checked_at,
            "last_check_status": self.last_check_status,
            "failure_count": self.failure_count,
            "failure_backoff_until": self.failure_backoff_until,
            "notice_state": self.notice_state,
        }


@dataclass(frozen=True)
class UpdateEvaluation:
    runtime_identity: RuntimeIdentity
    freshness: str
    refresh_attempted: bool
    refresh_result: str
    last_success_checked_at: str | None
    failure_backoff_until: str | None
    upstream_latest_version: str | None
    channel_latest_version: str | None
    release_url: str | None
    release_trust: str
    release_truth_freshness: str
    release_truth_reason_code: str
    release_certificate_digest: str | None
    revocation_generation: int
    eligible_notice_classes: tuple[str, ...]
    reason_code: str
    upgrade_command: str | None = None

    def to_machine_dict(self) -> dict[str, Any]:
        identity = self.runtime_identity
        return {
            "protocol_version": PROTOCOL_VERSION,
            "runtime_identity": identity.runtime_identity,
            "installed_runtime": identity.installed_runtime,
            "binding_verified": identity.binding_verified,
            "installed_version": identity.installed_version,
            "install_channel": identity.install_channel,
            "executable_path": identity.executable_path,
            "freshness": self.freshness,
            "refresh_attempted": self.refresh_attempted,
            "refresh_result": self.refresh_result,
            "last_success_checked_at": self.last_success_checked_at,
            "failure_backoff_until": self.failure_backoff_until,
            "upstream_latest_version": self.upstream_latest_version,
            "channel_latest_version": self.channel_latest_version,
            "release_url": self.release_url,
            "release_trust": self.release_trust,
            "release_truth_freshness": self.release_truth_freshness,
            "release_truth_reason_code": self.release_truth_reason_code,
            "release_certificate_digest": self.release_certificate_digest,
            "revocation_generation": self.revocation_generation,
            "eligible_notice_classes": list(self.eligible_notice_classes),
            "reason_code": self.reason_code,
            "upgrade_command": self.upgrade_command,
        }


@dataclass(frozen=True)
class NoticeAck:
    runtime_identity: str
    notice_class: str
    notice_version: str
    ack_recorded: bool

    def to_machine_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "runtime_identity": self.runtime_identity,
            "notice_class": self.notice_class,
            "notice_version": self.notice_version,
            "ack_recorded": self.ack_recorded,
        }


def detect_runtime_identity(env: dict[str, str] | None = None) -> RuntimeIdentity:
    """Return installed-runtime identity, failing closed for source/editable runs."""
    env_map = env or os.environ
    forced = env_map.get("AI_SDLC_UPDATE_ADVISOR_TEST_INSTALLED")
    if forced == "1":
        version = env_map.get("AI_SDLC_UPDATE_ADVISOR_TEST_VERSION", "1.0.0")
        channel = env_map.get("AI_SDLC_UPDATE_ADVISOR_TEST_CHANNEL", "github-archive")
        executable = env_map.get("AI_SDLC_UPDATE_ADVISOR_TEST_EXECUTABLE", sys.argv[0])
        distribution_path = env_map.get(
            "AI_SDLC_UPDATE_ADVISOR_TEST_DISTRIBUTION", sys.prefix
        )
        identity = _runtime_identity_hash(
            executable=executable,
            distribution_path=distribution_path,
            install_channel=channel,
            installed_version=version,
        )
        return RuntimeIdentity(
            installed_runtime=True,
            binding_verified=True,
            runtime_identity=identity,
            installed_version=version,
            install_channel=channel,
            executable_path=executable,
            distribution_path=distribution_path,
            reason_code="forced_test_installed_runtime",
        )

    executable = str(Path(sys.argv[0]).expanduser())
    try:
        dist = metadata.distribution(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return _not_installed_identity(executable, "distribution_not_found")

    installed_version = dist.version
    if _distribution_is_editable(dist):
        return _not_installed_identity(executable, "editable_runtime")

    distribution_path = _distribution_path(dist)
    if _is_source_or_module_invocation(executable, env_map) and not (
        _module_invocation_uses_distribution(executable, distribution_path)
    ):
        return _not_installed_identity(executable, "source_or_module_runtime")

    channel = _detect_install_channel(Path(executable), Path(distribution_path))
    identity = _runtime_identity_hash(
        executable=executable,
        distribution_path=distribution_path,
        install_channel=channel,
        installed_version=installed_version,
    )
    return RuntimeIdentity(
        installed_runtime=True,
        binding_verified=True,
        runtime_identity=identity,
        installed_version=installed_version,
        install_channel=channel,
        executable_path=executable,
        distribution_path=distribution_path,
        reason_code="installed_runtime",
    )


def evaluate_update_advisor(
    *,
    env: dict[str, str] | None = None,
    now: datetime | None = None,
    fetch_latest: FetchLatest | None = None,
    fetch_release_truth: FetchReleaseTruth | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    allow_refresh: bool = True,
    ignore_failure_backoff: bool = False,
) -> UpdateEvaluation:
    """Evaluate update notice eligibility and optionally refresh upstream truth."""
    env_map = env or os.environ
    current_time = _utc_now(now)
    identity = detect_runtime_identity(env_map)
    if _update_check_disabled(env_map):
        return _evaluation_from_identity(
            identity,
            freshness="expired",
            refresh_result=REFRESH_DISABLED,
            reason_code="disabled_by_config",
        )
    if not identity.installed_runtime:
        return _evaluation_from_identity(
            identity,
            freshness="expired",
            refresh_result=REFRESH_DISABLED,
            reason_code=identity.reason_code,
        )

    cache = _load_cache(identity)
    cache.runtime_identity = identity.runtime_identity
    cache.installed_version = identity.installed_version
    cache.install_channel = identity.install_channel

    freshness = _freshness(cache, current_time)
    release_truth_freshness = _release_truth_freshness(cache, current_time)
    refresh_attempted = False
    refresh_result = REFRESH_NOT_NEEDED
    reason_code = "cache_fresh" if freshness == "fresh" else "cache_only"

    if allow_refresh and (freshness != "fresh" or release_truth_freshness != "fresh"):
        backoff_until = _parse_iso(cache.failure_backoff_until)
        if (
            backoff_until is not None
            and current_time < backoff_until
            and not ignore_failure_backoff
        ):
            refresh_result = REFRESH_BACKOFF
            reason_code = "failure_backoff"
        else:
            refresh_attempted = True
            refresh_result, reason_code = _refresh_cache(
                cache,
                identity=identity,
                now=current_time,
                fetch_latest=fetch_latest,
                fetch_release_truth=fetch_release_truth,
                timeout_seconds=timeout_seconds,
                env=env_map,
            )
            freshness = _freshness(cache, current_time)
            release_truth_freshness = _release_truth_freshness(cache, current_time)

    _save_cache(identity, cache)
    release_trust = (
        cache.release_trust if release_truth_freshness == "fresh" else "unknown"
    )
    release_truth_reason = (
        cache.release_truth_reason_code
        if release_truth_freshness == "fresh"
        else "release_truth_expired"
    )
    eligible = _eligible_notice_classes(
        identity, cache, freshness, release_trust=release_trust
    )
    upgrade_command = (
        _upgrade_command(identity, cache) if NOTICE_ACTIONABLE in eligible else None
    )
    return UpdateEvaluation(
        runtime_identity=identity,
        freshness=freshness,
        refresh_attempted=refresh_attempted,
        refresh_result=refresh_result,
        last_success_checked_at=cache.last_success_checked_at,
        failure_backoff_until=cache.failure_backoff_until,
        upstream_latest_version=cache.upstream_latest_version,
        channel_latest_version=cache.channel_latest_version,
        release_url=cache.release_url,
        release_trust=release_trust,
        release_truth_freshness=release_truth_freshness,
        release_truth_reason_code=release_truth_reason,
        release_certificate_digest=cache.release_certificate_digest,
        revocation_generation=cache.revocation_generation,
        eligible_notice_classes=tuple(eligible),
        reason_code=reason_code,
        upgrade_command=upgrade_command,
    )


def ack_notice(
    notice_class: str,
    notice_version: str,
    *,
    env: dict[str, str] | None = None,
    now: datetime | None = None,
) -> NoticeAck:
    identity = detect_runtime_identity(env or os.environ)
    if not identity.installed_runtime:
        return NoticeAck(
            runtime_identity=identity.runtime_identity,
            notice_class=notice_class,
            notice_version=notice_version,
            ack_recorded=False,
        )
    cache = _load_cache(identity)
    cache.notice_state.setdefault(notice_class, {})
    cache.notice_state[notice_class].update(
        {
            "last_acknowledged_at": _iso(_utc_now(now)),
            "notice_version": notice_version,
        }
    )
    _save_cache(identity, cache)
    return NoticeAck(
        runtime_identity=identity.runtime_identity,
        notice_class=notice_class,
        notice_version=notice_version,
        ack_recorded=True,
    )


def record_notice_rendered(
    notice_class: str,
    notice_version: str,
    *,
    env: dict[str, str] | None = None,
    now: datetime | None = None,
) -> bool:
    identity = detect_runtime_identity(env or os.environ)
    if not identity.installed_runtime or not notice_version:
        return False
    cache = _load_cache(identity)
    cache.notice_state.setdefault(notice_class, {})
    cache.notice_state[notice_class].update(
        {
            "last_rendered_at": _iso(_utc_now(now)),
            "rendered_notice_version": notice_version,
        }
    )
    _save_cache(identity, cache)
    return True


def should_auto_render_notice(env: dict[str, str] | None = None) -> bool:
    env_map = env or os.environ
    return not _update_check_disabled(env_map)


def render_notice_lines(evaluation: UpdateEvaluation) -> list[str]:
    classes = set(evaluation.eligible_notice_classes)
    if NOTICE_ACTIONABLE in classes and evaluation.upgrade_command:
        latest = evaluation.channel_latest_version or evaluation.upstream_latest_version
        return [
            f"检测到 AI-SDLC {latest} 可用于当前安装渠道。",
            f"AI-SDLC {latest} is available for this install channel.",
            "更新命令会自动下载、安装并校验版本。",
            "The update command downloads, installs, and verifies the version automatically.",
            f"更新命令 / Update command: {evaluation.upgrade_command}",
        ]
    if NOTICE_LIGHT in classes:
        latest = evaluation.upstream_latest_version
        release_url = (
            evaluation.release_url
            or "https://github.com/SinclairPan/Ai_AutoSDLC/releases"
        )
        return [
            f"检测到 GitHub 上游新 release：AI-SDLC {latest}。",
            f"A newer upstream AI-SDLC release is available: {latest}.",
            f"查看发布 / Release: {release_url}",
            "当前运行入口不能被 CLI 安全覆盖；请使用离线安装包或公司/项目提供的安装入口更新。",
            "This CLI entry cannot be safely replaced automatically; use the offline bundle or your company/project installer.",
        ]
    if evaluation.refresh_result in {
        REFRESH_NETWORK_ERROR,
        REFRESH_PARSE_ERROR,
        REFRESH_TIMEOUT,
    }:
        return [
            "本次无法刷新 AI-SDLC update state；主命令会继续执行。",
            "Update check failed; the main command continues.",
        ]
    return []


def notice_version_for(evaluation: UpdateEvaluation, notice_class: str) -> str:
    if notice_class == NOTICE_ACTIONABLE:
        return evaluation.channel_latest_version or ""
    if notice_class == NOTICE_LIGHT:
        return evaluation.upstream_latest_version or ""
    return evaluation.reason_code


def notice_already_acknowledged(
    evaluation: UpdateEvaluation, notice_class: str
) -> bool:
    notice_version = notice_version_for(evaluation, notice_class)
    if not notice_version:
        return False
    cache = _load_cache(evaluation.runtime_identity)
    notice = cache.notice_state.get(notice_class)
    if not isinstance(notice, dict):
        return False
    return notice.get("notice_version") == notice_version


def notice_recently_rendered(
    evaluation: UpdateEvaluation,
    notice_class: str,
    *,
    now: datetime | None = None,
    repeat_interval: timedelta = AUTO_NOTICE_REPEAT_INTERVAL,
) -> bool:
    notice_version = notice_version_for(evaluation, notice_class)
    if not notice_version:
        return False
    cache = _load_cache(evaluation.runtime_identity)
    notice = cache.notice_state.get(notice_class)
    if not isinstance(notice, dict):
        return False
    if notice.get("rendered_notice_version") != notice_version:
        return False
    last_rendered_at = _parse_iso(notice.get("last_rendered_at"))
    if last_rendered_at is None:
        return False
    return _utc_now(now) - last_rendered_at < repeat_interval


def _refresh_cache(
    cache: UpdateCache,
    *,
    identity: RuntimeIdentity,
    now: datetime,
    fetch_latest: FetchLatest | None,
    fetch_release_truth: FetchReleaseTruth | None,
    timeout_seconds: float,
    env: dict[str, str],
) -> tuple[str, str]:
    cache.last_checked_at = _iso(now)
    try:
        budget = _TimeoutBudget.start(timeout_seconds)
        raw = _latest_release_from_env(env)
        if raw is None:
            fetcher = fetch_latest or fetch_latest_github_release
            raw = fetcher(budget.remaining())
        tag = str(raw.get("tag_name") or raw.get("version") or "").strip()
        if not tag:
            raise ValueError("release response did not include tag_name")
        if bool(raw.get("draft")) or bool(raw.get("prerelease")):
            raise ValueError("latest release response points to draft/prerelease")
        latest = _normalize_version_label(tag)
        if env.get("AI_SDLC_UPDATE_ADVISOR_TEST_LATEST_VERSION"):
            truth = ReleaseTrustDecision(
                status="trusted",
                reason_code="test_release_truth",
                certificate_digest="sha256:" + "0" * 64,
                revocation_generation=0,
                observed_at=_iso(now),
            )
        else:
            truth_fetcher = fetch_release_truth or fetch_release_truth_github
            truth = truth_fetcher(raw, budget.remaining(), _iso(now))
            truth = ReleaseTrustDecision.model_validate(truth.model_dump(mode="json"))
        cache.upstream_latest_version = latest
        cache.release_url = _optional_str(raw.get("html_url") or raw.get("url"))
        cache.channel_latest_version = latest
        cache.release_trust = truth.status
        cache.release_truth_reason_code = truth.reason_code
        cache.release_truth_observed_at = truth.observed_at
        cache.release_certificate_digest = truth.certificate_digest or None
        cache.revocation_generation = truth.revocation_generation
        cache.last_success_checked_at = _iso(now)
        cache.last_check_status = REFRESH_SUCCESS
        cache.failure_count = 0
        cache.failure_backoff_until = None
        return REFRESH_SUCCESS, "refresh_success"
    except TimeoutError:
        _record_failure(cache, now, REFRESH_TIMEOUT)
        return REFRESH_TIMEOUT, "refresh_timeout"
    except (urllib.error.URLError, OSError):
        _record_failure(cache, now, REFRESH_NETWORK_ERROR)
        return REFRESH_NETWORK_ERROR, "network_error"
    except (ValueError, json.JSONDecodeError, TypeError):
        _record_failure(cache, now, REFRESH_PARSE_ERROR)
        return REFRESH_PARSE_ERROR, "parse_error"


def fetch_latest_github_release(timeout_seconds: float) -> dict[str, Any]:
    request = urllib.request.Request(
        GITHUB_RELEASES_LATEST_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "ai-sdlc-update-advisor",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read().decode("utf-8")
    except TimeoutError:
        raise
    return json.loads(payload)


def fetch_release_truth_github(
    release: dict[str, Any], timeout_seconds: float, observed_at: str
) -> ReleaseTrustDecision:
    """从公开 GitHub Release/evidence assets 重建当前推荐真值。"""
    budget = _TimeoutBudget.start(timeout_seconds)
    if bool(release.get("draft")) or bool(release.get("prerelease")):
        raise ValueError("software release is not a published stable release")
    if release.get("immutable") is not True:
        raise ValueError("software release is not immutable")
    tag_name = str(release.get("tag_name") or "")
    if not tag_name:
        raise ValueError("software release tag is missing")
    release_id = release.get("id")
    if not isinstance(release_id, int) or release_id < 1:
        raise ValueError("software release id is invalid")

    proof_bytes = _verified_release_asset(
        release, "release-satisfaction-proof.json", budget.remaining()
    )
    proof = ReleaseSatisfactionProof.model_validate_json(proof_bytes)
    encoded_certificate_tag = urllib.parse.quote(
        f"release-truth/{tag_name}/certificate/g0", safe=""
    )
    try:
        certificate_release = _fetch_public_json(
            f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/tags/"
            f"{encoded_certificate_tag}",
            budget.remaining(),
        )
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        return ReleaseTrustDecision(
            status="untrusted",
            reason_code="certificate_missing",
            revocation_generation=0,
            observed_at=observed_at,
        )
    if not isinstance(certificate_release, dict):
        raise ValueError("certificate evidence response is invalid")
    certificate_bytes = _verified_evidence_asset(
        certificate_release,
        expected_tag=f"release-truth/{tag_name}/certificate/g0",
        asset_name="release-certificate.json",
        timeout_seconds=budget.remaining(),
    )
    certificate = ReleaseCertificate.model_validate_json(certificate_bytes)
    proof_identity = (
        proof.repository,
        proof.tag_name,
        proof.commit_sha,
        proof.tree_sha,
        proof.proof_digest,
    )
    certificate_identity = (
        certificate.repository,
        certificate.tag_name,
        certificate.commit_sha,
        certificate.tree_sha,
        certificate.proof_digest,
    )
    if proof_identity != certificate_identity:
        raise ValueError("certificate identity differs from satisfaction proof")
    if proof.assets != certificate.assets:
        raise ValueError("certificate assets differ from satisfaction proof")
    if certificate.repository != GITHUB_REPOSITORY:
        raise ValueError("certificate repository differs")
    _verify_certificate_artifact_attestation(
        certificate_bytes,
        proof,
        budget.remaining(),
    )
    _verify_public_workflow_authority(proof, budget.remaining())

    live_assets = {
        str(asset.get("name") or ""): asset
        for asset in release.get("assets", [])
        if isinstance(asset, dict)
    }
    expected_names = {asset.name for asset in certificate.assets}
    if set(live_assets) != expected_names | {"release-satisfaction-proof.json"}:
        raise ValueError("software release asset set differs from certificate")
    for binding in certificate.assets:
        current = live_assets[binding.name]
        if (
            current.get("digest") != binding.digest
            or current.get("size") != binding.size_bytes
        ):
            raise ValueError("software release asset binding differs")

    receipts: list[ReleaseRevocationReceipt] = []
    receipt_prefix = f"release-truth/{tag_name}/revocation/g"
    for evidence_release in _fetch_public_release_pages(
        budget.remaining(),
        stop_release_id=release_id,
    ):
        evidence_tag = str(evidence_release.get("tag_name") or "")
        if not evidence_tag.startswith(receipt_prefix):
            continue
        generation_text = evidence_tag.removeprefix(receipt_prefix)
        if not generation_text.isdigit() or int(generation_text) < 1:
            raise ValueError("receipt evidence generation tag is invalid")
        receipt_bytes = _verified_evidence_asset(
            evidence_release,
            expected_tag=evidence_tag,
            asset_name="release-revocation-receipt.json",
            timeout_seconds=budget.remaining(),
        )
        receipt = ReleaseRevocationReceipt.model_validate_json(receipt_bytes)
        if receipt.generation != int(generation_text):
            raise ValueError("receipt generation differs from evidence tag")
        _verify_receipt_artifact_attestation(
            receipt_bytes,
            receipt,
            certificate,
            budget.remaining(),
        )
        receipts.append(receipt)
    receipts.sort(key=lambda value: (value.generation, value.receipt_digest))
    max_generation = max((receipt.generation for receipt in receipts), default=0)
    published = PublishedReleaseSnapshot(
        repository=GITHUB_REPOSITORY,
        github_release_id=int(release.get("id") or 0),
        github_release_url=str(release.get("html_url") or ""),
        tag_name=tag_name,
        commit_sha=certificate.commit_sha,
        tree_sha=certificate.tree_sha,
        published=True,
        draft=False,
        immutable=True,
        release_attestation_verified=True,
        release_attestation_digest=certificate.release_attestation_digest,
        assets=certificate.assets,
        revocation_generation=max_generation,
    )
    observed = _parse_iso(observed_at)
    if observed is None:
        raise ValueError("release truth observed_at is invalid")
    return evaluate_release_trust(
        published,
        certificate,
        tuple(receipts),
        observed_at=observed_at,
        now=observed,
    )


def _verify_public_workflow_authority(
    proof: ReleaseSatisfactionProof,
    timeout_seconds: float,
) -> None:
    """从公开 Actions run 重建 Proof 的受保护 publisher 与 required gates 权威。"""

    budget = _TimeoutBudget.start(timeout_seconds)
    cached_runs: dict[tuple[int, int], dict[str, Any]] = {}

    def verified_run(
        *,
        workflow_run_id: int,
        workflow_run_attempt: int,
        workflow_ref: str,
        head_sha: str,
        conclusion: str,
        publisher: bool,
    ) -> None:
        key = (workflow_run_id, workflow_run_attempt)
        run = cached_runs.get(key)
        if run is None:
            payload = _fetch_public_json(
                f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/runs/"
                f"{workflow_run_id}/attempts/{workflow_run_attempt}",
                budget.remaining(),
            )
            if not isinstance(payload, dict):
                raise ValueError("release workflow authority response is invalid")
            run = payload
            cached_runs[key] = run
        expected_path = _protected_workflow_path(workflow_ref)
        head_repository = run.get("head_repository")
        if (
            run.get("id") != workflow_run_id
            or run.get("run_attempt") != workflow_run_attempt
            or run.get("status") != "completed"
            or run.get("conclusion") != conclusion
            or run.get("head_sha") != head_sha
            or run.get("path") != expected_path
            or not isinstance(head_repository, dict)
            or head_repository.get("full_name") != GITHUB_REPOSITORY
        ):
            raise ValueError("release workflow authority differs from proof")
        if publisher and (
            run.get("event") != "workflow_dispatch" or run.get("head_branch") != "main"
        ):
            raise ValueError("release publisher workflow authority is invalid")

    verified_run(
        workflow_run_id=proof.workflow_run_id,
        workflow_run_attempt=proof.workflow_run_attempt,
        workflow_ref=proof.publish_workflow_ref,
        head_sha=proof.commit_sha,
        conclusion="success",
        publisher=True,
    )
    if not proof.required_gates:
        raise ValueError("release proof has no required workflow authority")
    for gate in proof.required_gates:
        if (
            not gate.required
            or not gate.protected
            or gate.conclusion != "success"
            or gate.authority_repository != GITHUB_REPOSITORY
            or gate.head_sha != proof.commit_sha
        ):
            raise ValueError("required workflow authority is invalid")
        verified_run(
            workflow_run_id=gate.workflow_run_id,
            workflow_run_attempt=gate.workflow_run_attempt,
            workflow_ref=gate.workflow_ref,
            head_sha=gate.head_sha,
            conclusion=gate.conclusion,
            publisher=False,
        )


def _verify_certificate_artifact_attestation(
    certificate_bytes: bytes,
    proof: ReleaseSatisfactionProof,
    timeout_seconds: float,
) -> None:
    """核验 Certificate 摘要对应受保护 publisher 的 GitHub provenance。"""

    certificate_digest = hashlib.sha256(certificate_bytes).hexdigest()
    response = _fetch_public_json(
        f"https://api.github.com/repos/{GITHUB_REPOSITORY}/attestations/"
        f"sha256:{certificate_digest}?per_page=100",
        timeout_seconds,
    )
    if not isinstance(response, dict):
        raise ValueError("certificate artifact attestation response is invalid")
    attestations = response.get("attestations")
    if not isinstance(attestations, list) or not attestations:
        raise ValueError("certificate artifact attestation is missing")

    workflow_path = _protected_workflow_path(proof.publish_workflow_ref)
    expected_invocation = (
        f"https://github.com/{GITHUB_REPOSITORY}/actions/runs/"
        f"{proof.workflow_run_id}/attempts/{proof.workflow_run_attempt}"
    )
    expected_dependency = f"git+https://github.com/{GITHUB_REPOSITORY}@refs/heads/main"
    statements = _cryptographically_verified_artifact_attestation_statements(
        certificate_bytes,
        attestations,
        proof,
        workflow_path,
        timeout_seconds,
    )
    for statement in statements:
        subjects = statement.get("subject")
        predicate = statement.get("predicate")
        if (
            statement.get("_type") != "https://in-toto.io/Statement/v1"
            or statement.get("predicateType") != "https://slsa.dev/provenance/v1"
            or not isinstance(subjects, list)
            or subjects
            != [
                {
                    "name": "release-certificate.json",
                    "digest": {"sha256": certificate_digest},
                }
            ]
            or not isinstance(predicate, dict)
        ):
            continue
        build = predicate.get("buildDefinition")
        run = predicate.get("runDetails")
        if not isinstance(build, dict) or not isinstance(run, dict):
            continue
        external = build.get("externalParameters")
        internal = build.get("internalParameters")
        dependencies = build.get("resolvedDependencies")
        builder = run.get("builder")
        metadata = run.get("metadata")
        workflow = external.get("workflow") if isinstance(external, dict) else None
        github = internal.get("github") if isinstance(internal, dict) else None
        protected_dependency = {
            "uri": expected_dependency,
            "digest": {"gitCommit": proof.commit_sha},
        }
        if (
            build.get("buildType") != "https://actions.github.io/buildtypes/workflow/v1"
            or workflow
            != {
                "ref": "refs/heads/main",
                "repository": f"https://github.com/{GITHUB_REPOSITORY}",
                "path": workflow_path,
            }
            or not isinstance(github, dict)
            or github.get("event_name") != "workflow_dispatch"
            or not isinstance(dependencies, list)
            or protected_dependency not in dependencies
            or not isinstance(builder, dict)
            or builder.get("id") != GITHUB_HOSTED_RUNNER_BUILDER_ID
            or not isinstance(metadata, dict)
            or metadata.get("invocationId") != expected_invocation
        ):
            continue
        return
    raise ValueError("certificate artifact attestation authority is invalid")


def _verify_receipt_artifact_attestation(
    receipt_bytes: bytes,
    receipt: ReleaseRevocationReceipt,
    certificate: ReleaseCertificate,
    timeout_seconds: float,
) -> None:
    """核验 Receipt 摘要来自受保护 post-publish smoke writer。"""

    budget = _TimeoutBudget.start(timeout_seconds)
    receipt_digest = hashlib.sha256(receipt_bytes).hexdigest()
    response = _fetch_public_json(
        f"https://api.github.com/repos/{GITHUB_REPOSITORY}/attestations/"
        f"sha256:{receipt_digest}?per_page=100",
        budget.remaining(),
    )
    if not isinstance(response, dict):
        raise ValueError("receipt artifact attestation response is invalid")
    attestations = response.get("attestations")
    if not isinstance(attestations, list) or not attestations:
        raise ValueError("receipt artifact attestation is missing")

    workflow_path = ".github/workflows/release-artifact-smoke.yml"
    statements = _verified_artifact_attestation_statements(
        receipt_bytes,
        attestations,
        artifact_name="release-revocation-receipt.json",
        signer_workflow=(f"{GITHUB_REPOSITORY}/{workflow_path}@refs/heads/main"),
        source_ref=f"refs/tags/{receipt.tag_name}",
        source_digest=certificate.commit_sha,
        build_trigger="release",
        timeout_seconds=budget.remaining(),
    )
    expected_dependency = (
        f"git+https://github.com/{GITHUB_REPOSITORY}@refs/tags/{receipt.tag_name}"
    )
    for statement in statements:
        subjects = statement.get("subject")
        predicate = statement.get("predicate")
        if (
            statement.get("_type") != "https://in-toto.io/Statement/v1"
            or statement.get("predicateType") != "https://slsa.dev/provenance/v1"
            or subjects
            != [
                {
                    "name": "release-revocation-receipt.json",
                    "digest": {"sha256": receipt_digest},
                }
            ]
            or not isinstance(predicate, dict)
        ):
            continue
        build = predicate.get("buildDefinition")
        run = predicate.get("runDetails")
        if not isinstance(build, dict) or not isinstance(run, dict):
            continue
        external = build.get("externalParameters")
        internal = build.get("internalParameters")
        dependencies = build.get("resolvedDependencies")
        builder = run.get("builder")
        metadata = run.get("metadata")
        workflow = external.get("workflow") if isinstance(external, dict) else None
        github = internal.get("github") if isinstance(internal, dict) else None
        invocation = (
            metadata.get("invocationId") if isinstance(metadata, dict) else None
        )
        if (
            build.get("buildType") != "https://actions.github.io/buildtypes/workflow/v1"
            or workflow
            != {
                "ref": "refs/heads/main",
                "repository": f"https://github.com/{GITHUB_REPOSITORY}",
                "path": workflow_path,
            }
            or not isinstance(github, dict)
            or github.get("event_name") != "release"
            or not isinstance(dependencies, list)
            or {
                "uri": expected_dependency,
                "digest": {"gitCommit": certificate.commit_sha},
            }
            not in dependencies
            or not isinstance(builder, dict)
            or builder.get("id") != GITHUB_HOSTED_RUNNER_BUILDER_ID
            or not isinstance(invocation, str)
        ):
            continue
        if _verify_receipt_workflow_authority(
            invocation,
            certificate.commit_sha,
            workflow_path,
            receipt,
            budget.remaining(),
        ):
            return
    raise ValueError("receipt artifact attestation authority is invalid")


def _verify_receipt_workflow_authority(
    invocation: str,
    head_sha: str,
    workflow_path: str,
    receipt: ReleaseRevocationReceipt,
    timeout_seconds: float,
) -> bool:
    prefix = f"https://github.com/{GITHUB_REPOSITORY}/actions/runs/"
    match = re.fullmatch(
        re.escape(prefix)
        + r"(?P<run_id>[1-9][0-9]*)/attempts/(?P<attempt>[1-9][0-9]*)",
        invocation,
    )
    if match is None:
        return False
    run_id = int(match.group("run_id"))
    attempt = int(match.group("attempt"))
    payload = _fetch_public_json(
        f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/runs/"
        f"{run_id}/attempts/{attempt}",
        timeout_seconds,
    )
    head_repository = (
        payload.get("head_repository") if isinstance(payload, dict) else None
    )
    signal_evidence = {
        "release_tag": receipt.tag_name,
        "repository": GITHUB_REPOSITORY,
        "workflow_run_id": run_id,
    }
    evidence_digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                signal_evidence,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
    )
    return bool(
        isinstance(payload, dict)
        and payload.get("id") == run_id
        and payload.get("run_attempt") == attempt
        and payload.get("status") == "completed"
        and payload.get("conclusion") == "failure"
        and payload.get("event") == "release"
        and payload.get("head_sha") == head_sha
        and payload.get("path") == workflow_path
        and isinstance(head_repository, dict)
        and head_repository.get("full_name") == GITHUB_REPOSITORY
        and receipt.repository == GITHUB_REPOSITORY
        and receipt.work_item_id == f"release-smoke-{run_id}"
        and receipt.evidence_digest == evidence_digest
    )


def _cryptographically_verified_artifact_attestation_statements(
    certificate_bytes: bytes,
    attestations: list[object],
    proof: ReleaseSatisfactionProof,
    workflow_path: str,
    timeout_seconds: float,
) -> tuple[dict[str, Any], ...]:
    """使用已声明的 Python Sigstore verifier 核验 Certificate bundle。"""

    return _verified_artifact_attestation_statements(
        certificate_bytes,
        attestations,
        artifact_name="release-certificate.json",
        signer_workflow=(f"{GITHUB_REPOSITORY}/{workflow_path}@refs/heads/main"),
        signer_digest=proof.commit_sha,
        source_ref="refs/heads/main",
        source_digest=proof.commit_sha,
        build_trigger="workflow_dispatch",
        timeout_seconds=timeout_seconds,
    )


def _verified_artifact_attestation_statements(
    artifact_bytes: bytes,
    attestations: list[object],
    *,
    artifact_name: str,
    signer_workflow: str,
    source_ref: str,
    source_digest: str,
    build_trigger: str,
    timeout_seconds: float,
    signer_digest: str | None = None,
    run_invocation: str | None = None,
) -> tuple[dict[str, Any], ...]:
    """在当前 Python runtime 中执行 GitHub 私有 Sigstore 验签。"""

    bundles = [
        attestation["bundle"]
        for attestation in attestations
        if isinstance(attestation, dict) and isinstance(attestation.get("bundle"), dict)
    ]
    if not bundles:
        raise ValueError("certificate artifact attestation bundle is missing")

    with tempfile.TemporaryDirectory(prefix="ai-sdlc-attestation-") as temp_dir:
        root = Path(temp_dir)
        artifact_path = root / artifact_name
        bundle_path = root / "attestations.jsonl"
        artifact_path.write_bytes(artifact_bytes)
        bundle_path.write_text(
            "".join(
                json.dumps(bundle, ensure_ascii=False, sort_keys=True) + "\n"
                for bundle in bundles
            ),
            encoding="utf-8",
        )
        command = [
            sys.executable,
            "-m",
            "ai_sdlc.core.github_attestation_verifier",
            str(artifact_path),
            "--bundle",
            str(bundle_path),
            "--repository",
            GITHUB_REPOSITORY,
            "--signer-workflow",
            signer_workflow,
            "--source-ref",
            source_ref,
            "--source-digest",
            source_digest,
            "--build-trigger",
            build_trigger,
        ]
        if signer_digest:
            command.extend(["--signer-digest", signer_digest])
        if run_invocation:
            command.extend(["--run-invocation", run_invocation])
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise ValueError(
                "artifact attestation cryptographic verifier is unavailable"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                "artifact attestation cryptographic verification timed out"
            ) from exc

    if completed.returncode != 0:
        raise ValueError("artifact attestation cryptographic verification failed")
    try:
        results = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("artifact attestation verification result is invalid") from exc
    if not isinstance(results, list):
        raise ValueError("artifact attestation verification result is invalid")
    if not results or not all(isinstance(statement, dict) for statement in results):
        raise ValueError("artifact attestation verification result is invalid")
    return tuple(results)


def _protected_workflow_path(workflow_ref: str) -> str:
    prefix = f"{GITHUB_REPOSITORY}/"
    suffix = "@refs/heads/main"
    if not workflow_ref.startswith(prefix) or not workflow_ref.endswith(suffix):
        raise ValueError("release workflow authority is not protected main")
    path = workflow_ref[len(prefix) : -len(suffix)]
    if not path.startswith(".github/workflows/") or not path.endswith(
        (".yml", ".yaml")
    ):
        raise ValueError("release workflow authority path is invalid")
    return path


def _verified_evidence_asset(
    release: dict[str, Any],
    *,
    expected_tag: str,
    asset_name: str,
    timeout_seconds: float,
) -> bytes:
    if (
        str(release.get("tag_name") or "") != expected_tag
        or bool(release.get("draft"))
        or release.get("prerelease") is not True
        or release.get("immutable") is not True
    ):
        raise ValueError("evidence release authority is invalid")
    assets = release.get("assets")
    if not isinstance(assets, list) or len(assets) != 1:
        raise ValueError("evidence release must contain exactly one artifact")
    return _verified_release_asset(release, asset_name, timeout_seconds)


def _verified_release_asset(
    release: dict[str, Any], asset_name: str, timeout_seconds: float
) -> bytes:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ValueError("release assets are invalid")
    matches = [
        asset
        for asset in assets
        if isinstance(asset, dict) and asset.get("name") == asset_name
    ]
    if len(matches) != 1:
        raise ValueError(f"release asset is missing or duplicated: {asset_name}")
    asset = matches[0]
    url = str(asset.get("browser_download_url") or "")
    expected_digest = str(asset.get("digest") or "")
    if not url or re.fullmatch(r"sha256:[0-9a-f]{64}", expected_digest) is None:
        raise ValueError("release asset public identity is invalid")
    content = _fetch_public_bytes(url, timeout_seconds)
    actual_digest = "sha256:" + hashlib.sha256(content).hexdigest()
    if actual_digest != expected_digest or len(content) != int(asset.get("size") or -1):
        raise ValueError("release asset digest or size differs")
    return content


def _fetch_public_release_pages(
    timeout_seconds: float,
    *,
    stop_release_id: int,
) -> list[dict[str, Any]]:
    budget = _TimeoutBudget.start(timeout_seconds)
    releases: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = _fetch_public_json(
            f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases"
            f"?per_page=100&page={page}",
            budget.remaining(),
        )
        if not isinstance(payload, list) or any(
            not isinstance(item, dict) for item in payload
        ):
            raise ValueError("public release list response is invalid")
        for item in payload:
            releases.append(item)
            if item.get("id") == stop_release_id:
                return releases
        if len(payload) < 100:
            raise ValueError("software release is missing from public release history")
        page += 1


def _fetch_public_json(url: str, timeout_seconds: float) -> object:
    content = _fetch_public_bytes(url, timeout_seconds)
    return json.loads(content.decode("utf-8"))


def _fetch_public_bytes(url: str, timeout_seconds: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "ai-sdlc-update-advisor",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return response.read()


def _eligible_notice_classes(
    identity: RuntimeIdentity,
    cache: UpdateCache,
    freshness: str,
    *,
    release_trust: str,
) -> list[str]:
    if (
        freshness not in {"fresh", "stale_but_usable"}
        or not identity.installed_version
        or release_trust != "trusted"
    ):
        return []
    eligible: list[str] = []
    if _version_gt(cache.upstream_latest_version, identity.installed_version):
        eligible.append(NOTICE_LIGHT)
    if (
        cache.channel_latest_version
        and _version_gt(cache.channel_latest_version, identity.installed_version)
        and _upgrade_command(identity, cache)
    ):
        eligible.append(NOTICE_ACTIONABLE)
    return eligible


def _upgrade_command(identity: RuntimeIdentity, cache: UpdateCache) -> str | None:
    if not identity.installed_runtime or not cache.channel_latest_version:
        return None
    return "ai-sdlc self-update check"


def _evaluation_from_identity(
    identity: RuntimeIdentity,
    *,
    freshness: str,
    refresh_result: str,
    reason_code: str,
) -> UpdateEvaluation:
    return UpdateEvaluation(
        runtime_identity=identity,
        freshness=freshness,
        refresh_attempted=False,
        refresh_result=refresh_result,
        last_success_checked_at=None,
        failure_backoff_until=None,
        upstream_latest_version=None,
        channel_latest_version=None,
        release_url=None,
        release_trust="unknown",
        release_truth_freshness="unavailable",
        release_truth_reason_code=reason_code,
        release_certificate_digest=None,
        revocation_generation=0,
        eligible_notice_classes=(),
        reason_code=reason_code,
    )


def _latest_release_from_env(env: dict[str, str]) -> dict[str, Any] | None:
    latest_version = env.get("AI_SDLC_UPDATE_ADVISOR_TEST_LATEST_VERSION")
    if not latest_version:
        return None
    return {
        "tag_name": latest_version,
        "html_url": env.get(
            "AI_SDLC_UPDATE_ADVISOR_TEST_RELEASE_URL",
            f"https://example.test/releases/tag/{latest_version}",
        ),
        "draft": False,
        "prerelease": False,
    }


def _cache_path(identity: RuntimeIdentity) -> Path:
    root = os.environ.get("AI_SDLC_UPDATE_ADVISOR_CACHE_DIR")
    if root:
        base = Path(root)
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        base = base / "ai-sdlc" / "update-advisor"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        base = base / "ai-sdlc" / "update-advisor"
    return base / f"{_cache_file_stem(identity.runtime_identity)}.json"


def _cache_file_stem(runtime_identity: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", runtime_identity).strip("._")
    return safe or "unavailable"


def _load_cache(identity: RuntimeIdentity) -> UpdateCache:
    path = _cache_path(identity)
    try:
        return UpdateCache.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return UpdateCache(
            runtime_identity=identity.runtime_identity,
            installed_version=identity.installed_version,
            install_channel=identity.install_channel,
        )


def _save_cache(identity: RuntimeIdentity, cache: UpdateCache) -> None:
    path = _cache_path(identity)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                cache.to_dict(), handle, ensure_ascii=False, indent=2, sort_keys=True
            )
            handle.write("\n")
        os.replace(temp_name, path)
    except OSError:
        return


def _record_failure(cache: UpdateCache, now: datetime, status: str) -> None:
    cache.last_check_status = status
    cache.failure_count += 1
    if cache.failure_count <= 1:
        window = timedelta(hours=24)
    elif cache.failure_count == 2:
        window = timedelta(hours=72)
    else:
        window = timedelta(days=7)
    cache.failure_backoff_until = _iso(now + window)


def _freshness(cache: UpdateCache, now: datetime) -> str:
    last_success = _parse_iso(cache.last_success_checked_at)
    if last_success is None:
        return "expired"
    age = now - last_success
    if age < timedelta(0):
        return "expired"
    if age < FRESH_WINDOW:
        return "fresh"
    if age < EXPIRED_WINDOW:
        return "stale_but_usable"
    return "expired"


def _release_truth_freshness(cache: UpdateCache, now: datetime) -> str:
    observed_at = _parse_iso(cache.release_truth_observed_at)
    if observed_at is None:
        return "unavailable"
    age = now - observed_at
    if timedelta(0) <= age <= RELEASE_TRUTH_FRESHNESS_TTL:
        return "fresh"
    return "expired"


def _is_source_or_module_invocation(executable: str, env: dict[str, str]) -> bool:
    name = Path(executable).name.lower()
    if name in {"__main__.py", "python", "python3", "python.exe"}:
        return True
    if env.get("UV_RUN_RECURSION"):
        return True
    return env.get("AI_SDLC_SOURCE_RUNTIME") == "1"


def _module_invocation_uses_distribution(
    executable: str, distribution_path: str
) -> bool:
    if Path(executable).name.lower() != "__main__.py" or not distribution_path:
        return False
    try:
        executable_path = Path(executable).resolve()
        distribution_root = Path(distribution_path).resolve()
    except OSError:
        return False
    return executable_path.is_relative_to(distribution_root)


def _distribution_is_editable(dist: metadata.Distribution) -> bool:
    direct_url = dist.read_text("direct_url.json")
    if not direct_url:
        return False
    try:
        payload = json.loads(direct_url)
    except json.JSONDecodeError:
        return False
    dir_info = payload.get("dir_info")
    return isinstance(dir_info, dict) and bool(dir_info.get("editable"))


def _distribution_path(dist: metadata.Distribution) -> str:
    locate = getattr(dist, "locate_file", None)
    if callable(locate):
        try:
            return str(Path(locate("")).resolve())
        except OSError:
            return str(locate(""))
    return ""


def _detect_install_channel(executable: Path, distribution_path: Path) -> str:
    normalized = executable.as_posix().lower()
    if "pipx/venvs" in normalized or "/pipx/" in normalized:
        return "pipx"
    if _has_bundle_manifest(executable) or _has_bundle_manifest(distribution_path):
        return "github-archive"
    try:
        user_base = Path(os.path.expanduser("~/.local")).resolve()
        if distribution_path.resolve().is_relative_to(user_base):
            return "pip-user"
    except OSError:
        pass
    return "unknown"


def _has_bundle_manifest(path: Path) -> bool:
    for parent in [path, *path.parents]:
        manifest = parent / "bundle-manifest.json"
        if manifest.is_file():
            return True
    return False


def _not_installed_identity(executable: str, reason_code: str) -> RuntimeIdentity:
    return RuntimeIdentity(
        installed_runtime=False,
        binding_verified=False,
        runtime_identity="unavailable",
        installed_version=None,
        install_channel="source",
        executable_path=executable,
        distribution_path="",
        reason_code=reason_code,
    )


def _runtime_identity_hash(
    *,
    executable: str,
    distribution_path: str,
    install_channel: str,
    installed_version: str,
) -> str:
    payload = "\n".join(
        [
            str(Path(executable).expanduser()),
            distribution_path,
            install_channel,
            installed_version,
        ]
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_version_label(value: str) -> str:
    stripped = value.strip()
    return stripped[1:] if stripped.startswith("v") else stripped


def _version_gt(candidate: str | None, current: str | None) -> bool:
    if not candidate or not current:
        return False
    return _version_tuple(candidate) > _version_tuple(current)


def _version_tuple(value: str) -> tuple[int, ...]:
    normalized = _normalize_version_label(value)
    parts = re.findall(r"\d+", normalized)
    return tuple(int(part) for part in parts[:4]) or (0,)


def _update_check_disabled(env: dict[str, str]) -> bool:
    return env.get("AI_SDLC_DISABLE_UPDATE_CHECK", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _utc_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    return now if now.tzinfo else now.replace(tzinfo=UTC)


def _iso(value: datetime) -> str:
    return (
        value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def platform_asset_hint(version: str | None = None) -> dict[str, str]:
    release_version = _normalize_version_label(version or "")
    system = platform.system().lower()
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        machine = "amd64"
    elif machine in {"aarch64", "arm64"}:
        machine = "arm64"
    if system == "darwin":
        os_name = "macos"
        archive = "tar.gz"
    elif system == "windows":
        os_name = "windows"
        archive = "zip"
    else:
        os_name = "linux"
        archive = "tar.gz"
    suffix = f"{os_name}-{machine}"
    filename = (
        f"ai-sdlc-offline-{release_version}-{suffix}.{archive}"
        if release_version
        else f"ai-sdlc-offline-<version>-{suffix}.{archive}"
    )
    return {
        "os": os_name,
        "machine": machine,
        "archive": archive,
        "filename": filename,
    }
