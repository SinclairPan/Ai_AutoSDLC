"""Tests for the installed-runtime GitHub attestation verifier."""

from __future__ import annotations

import hashlib
import json
from unittest.mock import Mock

from cryptography import x509
from cryptography.x509.oid import NameOID
from sigstore.verify import Verifier, policy

from ai_sdlc.core.github_attestation_verifier import (
    AttestationPolicy,
    _normalize_github_trusted_root,
    _verify_dsse_by_issuer,
    verify_attestation_bundles,
)


def test_github_trusted_root_accepts_timestamp_only_schema() -> None:
    """捕获 GitHub 私有 root 缺少 public-good tlog/ctlog 字段而无法加载。"""

    root = {
        "mediaType": "application/vnd.dev.sigstore.trustedroot+json;version=0.1",
        "certificateAuthorities": [],
        "timestampAuthorities": [],
    }

    normalized = _normalize_github_trusted_root(root)

    assert normalized == {
        **root,
        "tlogs": [],
        "ctlogs": [],
    }


def test_public_good_bundle_uses_public_good_verifier(monkeypatch) -> None:
    """GitHub Actions 当前公开 attestation 必须走 sigstore.dev 公共根。"""

    bundle = Mock()
    bundle.signing_certificate.issuer = x509.Name(
        [x509.NameAttribute(NameOID.ORGANIZATION_NAME, "sigstore.dev")]
    )
    verifier = Mock()
    verifier.verify_dsse.return_value = ("application/vnd.in-toto+json", b"{}")
    monkeypatch.setattr(Verifier, "production", Mock(return_value=verifier))
    expected_policy = Mock(spec=policy.VerificationPolicy)

    result = _verify_dsse_by_issuer(bundle, expected_policy)

    assert result == ("application/vnd.in-toto+json", b"{}")
    verifier.verify_dsse.assert_called_once_with(bundle, expected_policy)


def test_bundle_scan_skips_invalid_candidate_and_binds_run_invocation(
    monkeypatch,
) -> None:
    artifact = b"receipt"
    artifact_name = "release-revocation-receipt.json"
    digest = hashlib.sha256(artifact).hexdigest()
    invocation = (
        "https://github.com/SinclairPan/Ai_AutoSDLC/actions/runs/200/attempts/1"
    )
    statement = {
        "subject": [{"name": artifact_name, "digest": {"sha256": digest}}],
        "predicate": {"runDetails": {"metadata": {"invocationId": invocation}}},
    }
    invalid = Mock()
    invalid.signing_certificate.issuer = x509.Name(
        [x509.NameAttribute(NameOID.ORGANIZATION_NAME, "untrusted.example")]
    )
    valid = Mock()
    valid.signing_certificate.issuer = x509.Name(
        [x509.NameAttribute(NameOID.ORGANIZATION_NAME, "sigstore.dev")]
    )
    monkeypatch.setattr(
        "ai_sdlc.core.github_attestation_verifier.Bundle.from_json",
        Mock(side_effect=[invalid, valid]),
    )
    verifier = Mock()
    verifier.verify_dsse.return_value = (
        "application/vnd.in-toto+json",
        json.dumps(statement).encode(),
    )
    monkeypatch.setattr(Verifier, "production", Mock(return_value=verifier))
    invocation_policy = Mock()
    monkeypatch.setattr(
        "ai_sdlc.core.github_attestation_verifier.policy.OIDCRunInvocationURI",
        Mock(return_value=invocation_policy),
    )

    results = verify_attestation_bundles(
        artifact,
        artifact_name,
        [{"candidate": 1}, {"candidate": 2}],
        AttestationPolicy(
            repository="SinclairPan/Ai_AutoSDLC",
            signer_workflow=(
                "SinclairPan/Ai_AutoSDLC/.github/workflows/"
                "release-artifact-smoke.yml@refs/heads/main"
            ),
            source_ref="refs/tags/v1.0.1",
            source_digest="a" * 40,
            build_trigger="release",
        ),
    )

    assert results == (statement,)
    invocation_policy.verify.assert_called_once_with(valid.signing_certificate)
