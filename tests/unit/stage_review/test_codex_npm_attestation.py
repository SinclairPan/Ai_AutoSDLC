from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)


def test_registry_slsa_provenance_verifies_against_published_attestation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.core.stage_review.codex_trusted_releases import (
        _published_codex_attestation as published_codex_attestation,
    )
    from ai_sdlc.core.stage_review.codex_trusted_releases import (
        verify_published_codex_npm_attestations,
    )

    registry = _registry_attestations()
    _bind_registry_digest(monkeypatch, registry)

    verified = verify_published_codex_npm_attestations(
        registry,
        "linux",
        "x64",
    )

    assert verified == published_codex_attestation("linux", "x64")


def test_trusted_release_is_built_only_from_registry_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.core.stage_review.codex_trusted_releases import (
        _published_codex_attestation as published_codex_attestation,
    )
    from ai_sdlc.core.stage_review.codex_trusted_releases import (
        _trusted_published_codex_release as trusted_published_codex_release,
    )

    registry = _registry_attestations()
    _bind_registry_digest(monkeypatch, registry)

    trusted = trusted_published_codex_release(
        "linux",
        "x64",
        registry_attestations=registry,
    )

    assert trusted is not None
    assert trusted.ci_attestation_verified is True
    expectation = published_codex_attestation("linux", "x64")
    assert expectation is not None
    with pytest.raises(ValueError, match="npm registry attestations"):
        trusted_published_codex_release(
            "linux",
            "x64",
            registry_attestations=expectation,
        )


def test_trusted_release_digest_selects_the_requested_platform() -> None:
    from ai_sdlc.core.stage_review.codex_trusted_releases import (
        _trusted_published_codex_release_digest as trusted_release_digest,
    )
    from ai_sdlc.core.stage_review.codex_trusted_releases import (
        _trusted_published_codex_release_digests as trusted_release_digests,
    )

    linux = trusted_release_digest("linux", "x64")
    windows = trusted_release_digest("windows", "x64")

    assert linux in trusted_release_digests()
    assert windows in trusted_release_digests()
    assert linux != windows


def test_registry_provenance_rejects_invalid_dsse_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.core.stage_review.codex_trusted_releases import (
        _verify_published_codex_npm_attestations as verify_published_codex_npm_attestations,
    )

    registry = _registry_attestations()
    envelope = _slsa_entry(registry)["bundle"]["dsseEnvelope"]
    envelope["payload"] = "not-base64!"
    _bind_registry_digest(monkeypatch, registry)

    with pytest.raises(ValueError, match="DSSE payload"):
        verify_published_codex_npm_attestations(registry, "linux", "x64")


def test_registry_provenance_ignores_rekor_inclusion_proof_and_checkpoint_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.core.stage_review.codex_trusted_releases import (
        _published_codex_attestation as published_codex_attestation,
    )
    from ai_sdlc.core.stage_review.codex_trusted_releases import (
        _verify_published_codex_npm_attestations as verify_published_codex_npm_attestations,
    )

    registry = _registry_attestations()
    _bind_registry_digest(monkeypatch, registry)
    proof = _slsa_entry(registry)["bundle"]["verificationMaterial"]["tlogEntries"][0][
        "inclusionProof"
    ]
    proof["hashes"] = ["changed-as-rekor-grows"]
    proof["checkpoint"]["envelope"] = "changed-checkpoint"

    verified = verify_published_codex_npm_attestations(registry, "linux", "x64")

    assert verified == published_codex_attestation("linux", "x64")


def test_registry_provenance_rejects_canonical_dsse_envelope_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_sdlc.core.stage_review.codex_trusted_releases import (
        _verify_published_codex_npm_attestations as verify_published_codex_npm_attestations,
    )

    registry = _registry_attestations()
    _bind_registry_digest(monkeypatch, registry)
    envelope = _slsa_entry(registry)["bundle"]["dsseEnvelope"]
    envelope["signatures"][0]["sig"] = "tampered-signature"

    with pytest.raises(ValueError, match="provenance digest"):
        verify_published_codex_npm_attestations(registry, "linux", "x64")


@pytest.mark.parametrize(
    "field",
    (
        "statement_type",
        "predicate_type",
        "subject",
        "package_integrity",
        "workflow_repository",
        "workflow_path",
        "workflow_ref",
        "builder",
        "invocation_url",
    ),
)
def test_registry_provenance_rejects_dsse_lineage_tampering(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    from ai_sdlc.core.stage_review.codex_trusted_releases import (
        _verify_published_codex_npm_attestations as verify_published_codex_npm_attestations,
    )

    registry = _registry_attestations()
    _tamper_statement(registry, field)
    _bind_registry_digest(monkeypatch, registry)

    with pytest.raises(ValueError, match="published Codex DSSE"):
        verify_published_codex_npm_attestations(registry, "linux", "x64")


def _registry_attestations() -> dict[str, Any]:
    from ai_sdlc.core.stage_review.codex_trusted_releases import (
        published_codex_release,
    )

    release = published_codex_release("linux", "x64")
    assert release is not None
    package_digest = base64.b64decode(
        release.package_integrity.removeprefix("sha512-"),
        validate=True,
    ).hex()
    statement = {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [
            {
                "name": release.ci_attestation_subject,
                "digest": {"sha512": package_digest},
            }
        ],
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {
            "buildDefinition": {
                "externalParameters": {
                    "workflow": {
                        "repository": "https://github.com/openai/codex",
                        "path": ".github/workflows/rust-release.yml",
                        "ref": "refs/tags/rust-v0.138.0",
                    }
                }
            },
            "runDetails": {
                "builder": {"id": "https://github.com/actions/runner/github-hosted"},
                "metadata": {
                    "invocationId": (
                        "https://github.com/openai/codex/actions/runs/123456/attempts/1"
                    )
                },
            },
        },
    }
    payload = base64.b64encode(
        json.dumps(statement, sort_keys=True, separators=(",", ":")).encode()
    ).decode()
    slsa = {
        "predicateType": "https://slsa.dev/provenance/v1",
        "bundle": {
            "mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json",
            "dsseEnvelope": {
                "payloadType": "application/vnd.in-toto+json",
                "payload": payload,
                "signatures": [{"keyid": "", "sig": "offline-fixture"}],
            },
            "verificationMaterial": {
                "tlogEntries": [
                    {
                        "inclusionProof": {
                            "checkpoint": {"envelope": "initial-checkpoint"},
                            "hashes": ["initial-tree-hash"],
                        }
                    }
                ]
            },
        },
    }
    return {
        "attestations": [
            {"predicateType": "https://example.test/other", "bundle": {}},
            slsa,
        ]
    }


def _slsa_entry(registry: dict[str, Any]) -> dict[str, Any]:
    return registry["attestations"][1]


def _bind_registry_digest(
    monkeypatch: pytest.MonkeyPatch,
    registry: dict[str, Any],
) -> None:
    from ai_sdlc.core.stage_review import codex_trusted_releases as release_api

    entry = _slsa_entry(registry)
    digest = canonical_digest(
        {
            "predicateType": entry["predicateType"],
            "dsseEnvelope": entry["bundle"]["dsseEnvelope"],
        },
        CanonicalizationPolicy(),
    )
    monkeypatch.setitem(
        release_api._OFFICIAL_NPM_PROVENANCE,
        ("0.138.0", "linux", "x64"),
        digest,
    )


def _tamper_statement(registry: dict[str, Any], field: str) -> None:
    envelope = _slsa_entry(registry)["bundle"]["dsseEnvelope"]
    statement = json.loads(base64.b64decode(envelope["payload"]).decode())
    workflow = statement["predicate"]["buildDefinition"]["externalParameters"][
        "workflow"
    ]
    replacements = {
        "statement_type": (statement, "_type", "https://example.test/Statement"),
        "predicate_type": (
            statement,
            "predicateType",
            "https://example.test/provenance",
        ),
        "subject": (statement["subject"][0], "name", "pkg:npm/tampered@0.138.0"),
        "package_integrity": (
            statement["subject"][0]["digest"],
            "sha512",
            "00" * 64,
        ),
        "workflow_repository": (
            workflow,
            "repository",
            "https://github.com/example/tampered",
        ),
        "workflow_path": (workflow, "path", ".github/workflows/tampered.yml"),
        "workflow_ref": (workflow, "ref", "refs/tags/rust-v0.137.0"),
        "builder": (
            statement["predicate"]["runDetails"]["builder"],
            "id",
            "https://example.test/self-hosted",
        ),
        "invocation_url": (
            statement["predicate"]["runDetails"]["metadata"],
            "invocationId",
            "https://example.test/actions/runs/123/attempts/1",
        ),
    }
    target, key, replacement = replacements[field]
    target[key] = replacement
    envelope["payload"] = base64.b64encode(
        json.dumps(statement, sort_keys=True, separators=(",", ":")).encode()
    ).decode()
