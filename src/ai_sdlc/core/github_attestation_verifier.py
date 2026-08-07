"""Cryptographically verify GitHub artifact attestations without GitHub CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography.x509 import ExtendedKeyUsage, KeyUsage
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from OpenSSL.crypto import (
    X509,
    X509Store,
    X509StoreContext,
    X509StoreFlags,
)
from rfc3161_client import VerificationError as TimestampVerificationError
from rfc3161_client import VerifierBuilder
from sigstore import dsse
from sigstore._internal.tuf import TrustUpdater
from sigstore.errors import Error as SigstoreError
from sigstore.models import Bundle, TrustedRoot
from sigstore.verify import Verifier, policy
from sigstore_models.trustroot import v1 as trustroot_v1

GITHUB_TUF_URL = "https://tuf-repo.github.com"
GITHUB_TUF_BOOTSTRAP_URL = (
    "https://raw.githubusercontent.com/cli/cli/v2.88.1/"
    "pkg/cmd/attestation/verification/embed/tuf-repo.github.com/root.json"
)
GITHUB_TUF_BOOTSTRAP_SHA256 = (
    "98cba97be9075bc98b2322de3de85fbd1b70ec7392991dfd2f53d215bede1a8d"
)
GITHUB_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
PUBLIC_GOOD_ISSUER_ORGANIZATION = "sigstore.dev"
GITHUB_ISSUER_ORGANIZATION = "GitHub, Inc."


@dataclass(frozen=True)
class AttestationPolicy:
    repository: str
    signer_workflow: str
    source_ref: str
    source_digest: str
    build_trigger: str
    signer_digest: str | None = None
    run_invocation: str | None = None


def verify_attestation_bundles(
    artifact_bytes: bytes,
    artifact_name: str,
    bundles: list[dict[str, Any]],
    expected: AttestationPolicy,
) -> tuple[dict[str, Any], ...]:
    """Verify bundle signatures and certificate claims, returning bound statements."""

    certificate_policies: list[policy.VerificationPolicy] = [
        policy.OIDCIssuerV2(GITHUB_OIDC_ISSUER),
        policy.OIDCBuildSignerURI(f"https://github.com/{expected.signer_workflow}"),
        policy.OIDCRunnerEnvironment("github-hosted"),
        policy.OIDCSourceRepositoryURI(f"https://github.com/{expected.repository}"),
        policy.OIDCSourceRepositoryDigest(expected.source_digest),
        policy.OIDCSourceRepositoryRef(expected.source_ref),
        policy.OIDCBuildTrigger(expected.build_trigger),
    ]
    if expected.signer_digest:
        certificate_policies.append(
            policy.OIDCBuildSignerDigest(expected.signer_digest)
        )
    if expected.run_invocation:
        certificate_policies.append(
            policy.OIDCRunInvocationURI(expected.run_invocation)
        )
    verification_policy = policy.AllOf(certificate_policies)
    artifact_digest = hashlib.sha256(artifact_bytes).hexdigest()
    expected_subject = {
        "name": artifact_name,
        "digest": {"sha256": artifact_digest},
    }

    statements: list[dict[str, Any]] = []
    for raw_bundle in bundles:
        try:
            bundle = Bundle.from_json(json.dumps(raw_bundle))
            payload_type, payload = _verify_dsse_by_issuer(bundle, verification_policy)
            if payload_type != dsse.Envelope._TYPE:
                continue
            statement = json.loads(payload)
            if not isinstance(statement, dict):
                continue
            subjects = statement.get("subject")
            if not isinstance(subjects, list) or expected_subject not in subjects:
                continue
            predicate = statement.get("predicate")
            run_details = (
                predicate.get("runDetails") if isinstance(predicate, dict) else None
            )
            metadata = (
                run_details.get("metadata") if isinstance(run_details, dict) else None
            )
            invocation = (
                metadata.get("invocationId") if isinstance(metadata, dict) else None
            )
            if not isinstance(invocation, str):
                continue
            policy.OIDCRunInvocationURI(invocation).verify(bundle.signing_certificate)
        except (SigstoreError, ValueError, json.JSONDecodeError):
            continue
        statements.append(statement)
    if not statements:
        raise ValueError("no GitHub artifact attestation matched the required policy")
    return tuple(statements)


def _verify_dsse_by_issuer(
    bundle: Bundle,
    verification_policy: policy.VerificationPolicy,
) -> tuple[str, bytes]:
    """Select only the trust root that matches the leaf certificate issuer."""

    organizations = bundle.signing_certificate.issuer.get_attributes_for_oid(
        NameOID.ORGANIZATION_NAME
    )
    if len(organizations) != 1:
        raise ValueError("attestation certificate issuer organization is ambiguous")
    issuer = organizations[0].value
    if issuer == PUBLIC_GOOD_ISSUER_ORGANIZATION:
        return Verifier.production().verify_dsse(bundle, verification_policy)
    if issuer == GITHUB_ISSUER_ORGANIZATION:
        return _verify_github_dsse(
            bundle,
            _github_trusted_root(),
            verification_policy,
        )
    raise ValueError("attestation certificate issuer is not trusted")


def _github_trusted_root() -> TrustedRoot:
    with urllib.request.urlopen(GITHUB_TUF_BOOTSTRAP_URL) as response:
        bootstrap = response.read()
    if hashlib.sha256(bootstrap).hexdigest() != GITHUB_TUF_BOOTSTRAP_SHA256:
        raise ValueError("GitHub TUF bootstrap root digest differs")
    with tempfile.TemporaryDirectory(prefix="ai-sdlc-github-tuf-") as temp_dir:
        bootstrap_path = Path(temp_dir) / "root.json"
        bootstrap_path.write_bytes(bootstrap)
        updater = TrustUpdater(
            GITHUB_TUF_URL,
            bootstrap_root=bootstrap_path,
        )
        trusted_root_path = updater.get_trusted_root_path()
        raw_root = json.loads(Path(trusted_root_path).read_text(encoding="utf-8"))
        normalized = _normalize_github_trusted_root(raw_root)
        return TrustedRoot(trustroot_v1.TrustedRoot.from_json(json.dumps(normalized)))


def _normalize_github_trusted_root(root: object) -> dict[str, Any]:
    if not isinstance(root, dict):
        raise ValueError("GitHub trusted root is invalid")
    normalized = dict(root)
    normalized.setdefault("tlogs", [])
    normalized.setdefault("ctlogs", [])
    return normalized


def _verify_github_dsse(
    bundle: Bundle,
    trusted_root: TrustedRoot,
    verification_policy: policy.VerificationPolicy,
) -> tuple[str, bytes]:
    """Apply GitHub's TSA-backed Sigstore profile to one DSSE bundle."""

    signed_times = _verified_timestamp_times(bundle, trusted_root)
    certificate = bundle.signing_certificate
    for signed_time in signed_times:
        _verify_certificate_chain(certificate, trusted_root, signed_time)

    usage = certificate.extensions.get_extension_for_class(KeyUsage).value
    if not usage.digital_signature:
        raise ValueError("GitHub attestation certificate cannot sign")
    extended_usage = certificate.extensions.get_extension_for_class(
        ExtendedKeyUsage
    ).value
    if ExtendedKeyUsageOID.CODE_SIGNING not in extended_usage:
        raise ValueError("GitHub attestation certificate is not for code signing")
    verification_policy.verify(certificate)

    envelope = bundle._dsse_envelope
    if envelope is None:
        raise ValueError("GitHub attestation bundle has no DSSE envelope")
    dsse._verify(certificate.public_key(), envelope)
    return envelope._inner.payload_type, envelope._inner.payload


def _verified_timestamp_times(
    bundle: Bundle,
    trusted_root: TrustedRoot,
) -> tuple[datetime, ...]:
    timestamp_data = bundle.verification_material.timestamp_verification_data
    responses = list(timestamp_data.rfc3161_timestamps) if timestamp_data else []
    if not responses or len(responses) > 32:
        raise ValueError("GitHub attestation has no bounded signed timestamp")
    encoded = [response.as_bytes() for response in responses]
    if len(encoded) != len(set(encoded)):
        raise ValueError("GitHub attestation contains duplicate timestamps")

    verified: list[datetime] = []
    for response in responses:
        for authority in trusted_root.get_timestamp_authorities():
            certificates = authority.certificates(allow_expired=True)
            if len(certificates) < 2:
                continue
            builder = (
                VerifierBuilder()
                .tsa_certificate(certificates[0])
                .add_root_certificate(certificates[-1])
            )
            for intermediate in certificates[1:-1]:
                builder = builder.add_intermediate_certificate(intermediate)
            try:
                builder.build().verify_message(response, bundle.signature)
            except TimestampVerificationError:
                continue
            signed_time = response.tst_info.gen_time
            if authority.validity_period_start <= signed_time and (
                authority.validity_period_end is None
                or signed_time < authority.validity_period_end
            ):
                verified.append(signed_time)
                break
    if not verified:
        raise ValueError("GitHub attestation signed timestamp is invalid")
    return tuple(verified)


def _verify_certificate_chain(
    certificate: Any,
    trusted_root: TrustedRoot,
    signed_time: datetime,
) -> None:
    store = X509Store()
    store.set_flags(X509StoreFlags.X509_STRICT)
    for parent in trusted_root.get_fulcio_certs():
        store.add_cert(X509.from_cryptography(parent))
    store.set_time(signed_time)
    X509StoreContext(store, X509.from_cryptography(certificate)).get_verified_chain()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--signer-workflow", required=True)
    parser.add_argument("--signer-digest")
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--source-digest", required=True)
    parser.add_argument("--build-trigger", required=True)
    parser.add_argument("--run-invocation")
    return parser


def main() -> None:
    args = _parser().parse_args()
    bundles = [
        json.loads(line)
        for line in args.bundle.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    statements = verify_attestation_bundles(
        args.artifact.read_bytes(),
        args.artifact.name,
        bundles,
        AttestationPolicy(
            repository=args.repository,
            signer_workflow=args.signer_workflow,
            signer_digest=args.signer_digest,
            source_ref=args.source_ref,
            source_digest=args.source_digest,
            build_trigger=args.build_trigger,
            run_invocation=args.run_invocation,
        ),
    )
    print(json.dumps(statements, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
