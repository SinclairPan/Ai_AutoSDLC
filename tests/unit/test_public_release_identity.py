import ast
import hashlib
import importlib.util
import json
import os
import py_compile
import shutil
import socket
import ssl
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import scripts.validate_public_release_identity as release_identity
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from scripts.validate_public_release_identity import (
    CURRENT_REPOSITORY_URL,
    CURRENT_VERSION,
    FORBIDDEN_SURFACE_MARKERS,
    PUBLIC_DOC_PATHS,
    PUBLISHED_VERSION,
    REQUIRED_SURFACES,
    STABLE_SOURCE_CLONE,
    scan_paths,
    validate_required_surfaces,
)

from ai_sdlc.core import verify_constraints

RELEASE_SURFACE_PATHS = (
    "README.md",
    "USER_GUIDE.zh-CN.md",
    "docs/product-contract.md",
    "docs/pull-request-checklist.zh.md",
    "packaging/offline/README.md",
    "packaging/offline/RELEASE_CHECKLIST.md",
)

SUCCESS_AUTHORITY_RESPONSES: dict[str, bytes] = {}


def _tls_client_error(
    client_context: ssl.SSLContext,
    server_context: ssl.SSLContext,
) -> BaseException | None:
    """在真实 TLS socketpair 上返回 client handshake 错误。"""

    server_socket, client_socket = socket.socketpair()
    server_errors: list[BaseException] = []

    def serve() -> None:
        try:
            with server_context.wrap_socket(server_socket, server_side=True) as tls:
                tls.recv(1)
        except BaseException as exc:  # 测试线程必须把握手错误带回主线程。
            server_errors.append(exc)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    client_error: BaseException | None = None
    try:
        with client_context.wrap_socket(
            client_socket, server_hostname="api.github.com"
        ) as tls:
            tls.sendall(b"x")
    except BaseException as exc:  # 与 server 侧一样保留真实 TLS 异常类型。
        client_error = exc
    thread.join(timeout=5)
    assert not thread.is_alive()
    if client_error is None:
        assert server_errors == []
    return client_error


def _assert_authority_tls_ignores_ambient_ca(tmp_path: Path) -> None:
    """证明 authority opener 不会继承 SSL_CERT_FILE 注入的根证书。"""

    now = datetime.now(UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "WI010 Test CA")])
    ca_certificate = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_certificate = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name(
                [x509.NameAttribute(NameOID.COMMON_NAME, "api.github.com")]
            )
        )
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("api.github.com")]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_path = tmp_path / "ambient-ca.pem"
    certificate_path = tmp_path / "ambient-api-github.pem"
    key_path = tmp_path / "ambient-api-github.key"
    ca_path.write_bytes(ca_certificate.public_bytes(serialization.Encoding.PEM))
    certificate_path.write_bytes(
        server_certificate.public_bytes(serialization.Encoding.PEM)
    )
    key_path.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(certificate_path, key_path)
    original_cert_file = os.environ.get("SSL_CERT_FILE")
    original_cert_dir = os.environ.get("SSL_CERT_DIR")
    try:
        os.environ["SSL_CERT_FILE"] = str(ca_path)
        os.environ.pop("SSL_CERT_DIR", None)
        assert _tls_client_error(ssl.create_default_context(), server_context) is None
        pinned_error = _tls_client_error(
            release_identity._wi010_authority_tls_context(), server_context
        )
        assert isinstance(pinned_error, ssl.SSLCertVerificationError)
    finally:
        if original_cert_file is None:
            os.environ.pop("SSL_CERT_FILE", None)
        else:
            os.environ["SSL_CERT_FILE"] = original_cert_file
        if original_cert_dir is None:
            os.environ.pop("SSL_CERT_DIR", None)
        else:
            os.environ["SSL_CERT_DIR"] = original_cert_dir


def _run_git(root: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
    )


def _checkout_exact_detached_head(source: Path, destination: Path) -> tuple[str, str]:
    """把源仓库当前 HEAD 精确物化为不继承工作树杂质的 detached checkout。"""

    source_head = (
        _run_git(source, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    )
    source_tree = (
        _run_git(source, "rev-parse", "HEAD^{tree}")
        .stdout.decode("ascii")
        .strip()
    )
    destination.mkdir(parents=True)
    _run_git(destination, "init", "-q")
    _run_git(destination, "remote", "add", "source", source.resolve().as_uri())
    fetched_ref = "refs/remotes/source/wi010-source-head"
    _run_git(
        destination,
        "fetch",
        "-q",
        "--no-tags",
        "source",
        f"HEAD:{fetched_ref}",
    )
    fetched_head = (
        _run_git(destination, "rev-parse", "--verify", f"{fetched_ref}^{{commit}}")
        .stdout.decode("ascii")
        .strip()
    )
    assert fetched_head == source_head
    _run_git(destination, "checkout", "-q", "--detach", source_head)
    _run_git(destination, "update-ref", "-d", fetched_ref)
    assert (
        _run_git(destination, "rev-parse", "HEAD").stdout.decode("ascii").strip()
        == source_head
    )
    assert (
        _run_git(destination, "rev-parse", "HEAD^{tree}")
        .stdout.decode("ascii")
        .strip()
        == source_tree
    )
    assert _run_git(destination, "status", "--porcelain=v1").stdout == b""
    _run_git(destination, "remote", "remove", "source")
    _run_git(
        destination,
        "remote",
        "add",
        "origin",
        "https://github.com/SinclairPan/Ai_AutoSDLC.git",
    )
    return source_head, source_tree


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _phase_line(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"<!-- WI010_RELEASE_PHASE: {encoded} -->"


def _canonical_digest(payload: dict[str, object], field: str) -> str:
    content = dict(payload)
    content.pop(field, None)
    encoded = json.dumps(
        content,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _certificate_attestation_statement(
    candidate_commit_sha: str,
    certificate_raw: bytes,
    run_id: int,
) -> dict[str, object]:
    """构造测试边界所需的精确 Certificate SLSA statement。"""

    certificate_sha256 = hashlib.sha256(certificate_raw).hexdigest()
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [
            {
                "name": "release-certificate.json",
                "digest": {"sha256": certificate_sha256},
            }
        ],
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {
            "buildDefinition": {
                "buildType": "https://actions.github.io/buildtypes/workflow/v1",
                "externalParameters": {
                    "workflow": {
                        "ref": "refs/heads/main",
                        "repository": (
                            "https://github.com/SinclairPan/Ai_AutoSDLC"
                        ),
                        "path": ".github/workflows/release-build.yml",
                    }
                },
                "internalParameters": {
                    "github": {"event_name": "workflow_dispatch"}
                },
                "resolvedDependencies": [
                    {
                        "uri": (
                            "git+https://github.com/"
                            "SinclairPan/Ai_AutoSDLC@refs/heads/main"
                        ),
                        "digest": {"gitCommit": candidate_commit_sha},
                    }
                ],
            },
            "runDetails": {
                "builder": {
                    "id": "https://github.com/actions/runner/github-hosted"
                },
                "metadata": {
                    "invocationId": (
                        "https://github.com/SinclairPan/Ai_AutoSDLC/"
                        f"actions/runs/{run_id}/attempts/1"
                    )
                },
            },
        },
    }


def _success_authority_fixture(
    candidate_commit_sha: str,
    candidate_tree_sha: str,
) -> tuple[dict[str, object], dict[str, bytes]]:
    repository = "SinclairPan/Ai_AutoSDLC"
    workflow_ref = (
        "SinclairPan/Ai_AutoSDLC/.github/workflows/"
        "release-build.yml@refs/heads/main"
    )
    release_id = 11
    run_id = 12
    software_tag_oid = "6" * 40
    certificate_tag_oid = "7" * 40
    certificate_tag = "release-truth/v1.0.5/certificate/g0"
    release_url = "https://github.com/SinclairPan/Ai_AutoSDLC/releases/tag/v1.0.5"
    release_name = "AI-SDLC v1.0.5"
    release_body = (
        "Qualified protected-main release v1.0.5.\n\n"
        f"run_id={run_id} run_attempt=1 workflow_ref={workflow_ref} "
        f"commit={candidate_commit_sha} "
        "failure_policy=terminal-generation-burn; "
        "no cleanup, edit, reuse, or rerun"
    )
    release_settings = {
        "id": release_id,
        "name": release_name,
        "body": release_body,
        "tag_name": "v1.0.5",
        "target_commitish": candidate_commit_sha,
        "draft": True,
        "prerelease": False,
    }
    release_settings_digest = _canonical_digest(release_settings, "")
    asset_names = sorted(release_identity.WI010_SOFTWARE_ASSETS)
    model_assets = [
        {
            "name": name,
            "digest": "sha256:" + format(index + 1, "064x"),
            "size_bytes": 100 + index,
            "platform": name.split("-1.0.5-", 1)[-1],
        }
        for index, name in enumerate(asset_names)
    ]
    proof: dict[str, object] = {
        "schema_version": "release-satisfaction-proof.v1",
        "canonicalization_version": "canonical-json.v1",
        "compatibility_mode": "strict",
        "extensions": {},
        "repository": repository,
        "admission_id": "wi010-release-admission",
        "admission_digest": "sha256:" + "a" * 64,
        "draft_release_id": release_id,
        "upload_url": "https://uploads.github.com/repos/SinclairPan/Ai_AutoSDLC/releases/11/assets{?name,label}",
        "release_user_agent": "ai-sdlc-release-writer/v1",
        "draft_release_updated_at": "2026-08-15T00:00:00Z",
        "tag_name": "v1.0.5",
        "tag_object_sha": software_tag_oid,
        "commit_sha": candidate_commit_sha,
        "tree_sha": candidate_tree_sha,
        "tag_ruleset_id": 101,
        "tag_ruleset_digest": "sha256:" + "b" * 64,
        "required_policy_digest": "sha256:" + "c" * 64,
        "required_gates": [],
        "workflow_run_id": run_id,
        "workflow_run_attempt": 1,
        "assets": model_assets,
        "release_settings_digest": release_settings_digest,
        "publish_workflow_ref": workflow_ref,
        "evidence_cutoff_at": "2026-08-15T00:01:00Z",
        "proof_digest": "",
    }
    proof["proof_digest"] = _canonical_digest(proof, "proof_digest")
    proof_raw = json.dumps(proof, ensure_ascii=False, sort_keys=True).encode("utf-8")
    proof_file_digest = "sha256:" + hashlib.sha256(proof_raw).hexdigest()

    live_assets = [
        {
            "name": item["name"],
            "digest": item["digest"],
            "size": item["size_bytes"],
            "browser_download_url": (
                "https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/"
                f"v1.0.5/{item['name']}"
            ),
        }
        for item in model_assets
    ]
    proof_url = (
        "https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/"
        "v1.0.5/release-satisfaction-proof.json"
    )
    live_assets.append(
        {
            "name": "release-satisfaction-proof.json",
            "digest": proof_file_digest,
            "size": len(proof_raw),
            "browser_download_url": proof_url,
        }
    )
    live_assets.sort(key=lambda item: str(item["name"]))
    binding = {
        "repository": repository,
        "tag_name": "v1.0.5",
        "commit_sha": candidate_commit_sha,
        "tree_sha": candidate_tree_sha,
        "assets": [
            {
                "name": item["name"],
                "digest": item["digest"],
                "size_bytes": item["size"],
            }
            for item in live_assets
        ],
    }
    binding_raw = json.dumps(
        binding,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    attestation_digest = "sha256:" + hashlib.sha256(binding_raw).hexdigest()
    certificate: dict[str, object] = {
        "schema_version": "release-certificate.v1",
        "canonicalization_version": "canonical-json.v1",
        "compatibility_mode": "strict",
        "extensions": {},
        "repository": repository,
        "admission_id": proof["admission_id"],
        "admission_digest": proof["admission_digest"],
        "github_release_id": release_id,
        "upload_url": proof["upload_url"],
        "release_user_agent": proof["release_user_agent"],
        "github_release_url": release_url,
        "tag_name": "v1.0.5",
        "tag_object_sha": software_tag_oid,
        "commit_sha": candidate_commit_sha,
        "tree_sha": candidate_tree_sha,
        "tag_ruleset_id": proof["tag_ruleset_id"],
        "tag_ruleset_digest": proof["tag_ruleset_digest"],
        "workflow_run_id": run_id,
        "workflow_run_attempt": 1,
        "proof_digest": proof["proof_digest"],
        "release_attestation_digest": attestation_digest,
        "assets": model_assets,
        "immutable": True,
        "revocation_generation": 0,
        "issued_at": "2026-08-15T00:02:00Z",
        "certificate_digest": "",
    }
    certificate["certificate_digest"] = _canonical_digest(
        certificate, "certificate_digest"
    )
    certificate_raw = json.dumps(
        certificate, ensure_ascii=False, sort_keys=True
    ).encode("utf-8")
    certificate_file_digest = "sha256:" + hashlib.sha256(
        certificate_raw
    ).hexdigest()
    certificate_file_sha256 = certificate_file_digest.removeprefix("sha256:")
    certificate_url = (
        "https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/"
        "release-truth/v1.0.5/certificate/g0/release-certificate.json"
    )
    api = release_identity.WI010_GITHUB_API_ROOT
    responses: dict[str, object] = {
        f"{api}/releases/{release_id}": {
            "id": release_id,
            "html_url": release_url,
            "name": release_name,
            "body": release_body,
            "tag_name": "v1.0.5",
            "target_commitish": candidate_commit_sha,
            "draft": False,
            "prerelease": False,
            "immutable": True,
            "assets": live_assets,
        },
        f"{api}/git/ref/tags/v1.0.5": {
            "ref": "refs/tags/v1.0.5",
            "object": {"type": "tag", "sha": software_tag_oid},
        },
        f"{api}/git/tags/{software_tag_oid}": {
            "tag": "v1.0.5",
            "object": {"type": "commit", "sha": candidate_commit_sha},
            "message": (
                "AI-SDLC v1.0.5\n\n"
                f"run_id={run_id} run_attempt=1 workflow_ref={workflow_ref} "
                f"commit={candidate_commit_sha} "
                "failure_policy=terminal-generation-burn; "
                "no cleanup, edit, reuse, or rerun"
            ),
        },
        f"{api}/git/commits/{candidate_commit_sha}": {
            "sha": candidate_commit_sha,
            "tree": {"sha": candidate_tree_sha},
        },
        f"{api}/actions/runs/{run_id}/attempts/1": {
            "id": run_id,
            "name": "Release Build",
            "display_title": "release-admission|v1.0.5|g0",
            "run_attempt": 1,
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "success",
            "head_branch": "main",
            "head_sha": candidate_commit_sha,
            "path": ".github/workflows/release-build.yml",
            "head_repository": {"full_name": repository},
            "repository": {"full_name": repository},
        },
        f"{api}/actions/runs/{run_id}/attempts/1/jobs?per_page=100": {
            "total_count": 8,
            "jobs": [
                {
                    "id": 1201,
                    "run_id": run_id,
                    "run_attempt": 1,
                    "name": "Read-only Release Workflow Load Probe",
                    "status": "completed",
                    "conclusion": "skipped",
                    "head_sha": candidate_commit_sha,
                },
                {
                    "id": 1202,
                    "run_id": run_id,
                    "run_attempt": 1,
                    "name": "Resolve Pre-tag Release Qualification Policy",
                    "status": "completed",
                    "conclusion": "success",
                    "head_sha": candidate_commit_sha,
                },
                {
                    "id": 1203,
                    "run_id": run_id,
                    "run_attempt": 1,
                    "name": "windows zip",
                    "status": "completed",
                    "conclusion": "success",
                    "head_sha": candidate_commit_sha,
                },
                {
                    "id": 1204,
                    "run_id": run_id,
                    "run_attempt": 1,
                    "name": "macos tar.gz",
                    "status": "completed",
                    "conclusion": "success",
                    "head_sha": candidate_commit_sha,
                },
                {
                    "id": 1205,
                    "run_id": run_id,
                    "run_attempt": 1,
                    "name": "linux tar.gz",
                    "status": "completed",
                    "conclusion": "success",
                    "head_sha": candidate_commit_sha,
                },
                {
                    "id": 1206,
                    "run_id": run_id,
                    "run_attempt": 1,
                    "name": "Release Qualification",
                    "status": "completed",
                    "conclusion": "success",
                    "head_sha": candidate_commit_sha,
                },
                {
                    "id": 1207,
                    "run_id": run_id,
                    "run_attempt": 1,
                    "name": "Build Release Proof Inputs",
                    "status": "completed",
                    "conclusion": "success",
                    "head_sha": candidate_commit_sha,
                },
                {
                    "id": 1208,
                    "run_id": run_id,
                    "run_attempt": 1,
                    "name": "Publish Proof-bound Release",
                    "status": "completed",
                    "conclusion": "success",
                    "head_sha": candidate_commit_sha,
                },
            ],
        },
        (
            f"{api}/releases/tags/"
            "release-truth%2Fv1.0.5%2Fcertificate%2Fg0"
        ): {
            "tag_name": certificate_tag,
            "target_commitish": candidate_commit_sha,
            "draft": False,
            "prerelease": True,
            "immutable": True,
            "name": "Release Truth v1.0.5 Certificate g0",
            "body": (
                "Permanent generation-0 release certificate for v1.0.5.\n\n"
                f"run_id={run_id} run_attempt=1 workflow_ref={workflow_ref} "
                f"commit={candidate_commit_sha} "
                f"software_admission_digest={proof['admission_digest']} "
                f"software_proof_digest={proof['proof_digest']} "
                "failure_policy=terminal-generation-burn; "
                "no cleanup, edit, reuse, or rerun"
            ),
            "assets": [
                {
                    "name": "release-certificate.json",
                    "digest": certificate_file_digest,
                    "size": len(certificate_raw),
                    "browser_download_url": certificate_url,
                }
            ],
        },
        (
            f"{api}/git/ref/tags/"
            "release-truth%2Fv1.0.5%2Fcertificate%2Fg0"
        ): {
            "ref": f"refs/tags/{certificate_tag}",
            "object": {"type": "tag", "sha": certificate_tag_oid},
        },
        f"{api}/git/tags/{certificate_tag_oid}": {
            "tag": certificate_tag,
            "object": {"type": "commit", "sha": candidate_commit_sha},
            "message": (
                "Permanent Certificate for v1.0.5\n\n"
                f"software_admission_digest={proof['admission_digest']} "
                f"software_proof_digest={proof['proof_digest']} "
                "failure_policy=terminal-generation-burn; "
                "no cleanup, edit, reuse, or rerun"
            ),
        },
        (
            f"{api}/attestations/sha256:{certificate_file_sha256}"
            "?per_page=100"
        ): {
            "attestations": [
                {
                    "bundle": {
                        "mediaType": (
                            "application/vnd.dev.sigstore.bundle.v0.3+json"
                        ),
                        "verificationMaterial": {},
                        "dsseEnvelope": {},
                    }
                }
            ]
        },
    }
    software_release = responses[f"{api}/releases/{release_id}"]
    assert isinstance(software_release, dict)
    responses[f"{api}/releases/latest"] = dict(software_release)
    encoded_responses = {
        url: json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        for url, value in responses.items()
    }
    encoded_responses[proof_url] = proof_raw
    encoded_responses[certificate_url] = certificate_raw
    payload = {
        "archive_commit_sha": candidate_commit_sha,
        "certificate_commit_sha": candidate_commit_sha,
        "certificate_digest": certificate["certificate_digest"],
        "certificate_tag": certificate_tag,
        "certificate_tree_sha": candidate_tree_sha,
        "generation": 0,
        "immutable": True,
        "phase": "S2-success",
        "proof_commit_sha": candidate_commit_sha,
        "proof_digest": proof["proof_digest"],
        "release_attestation_digest": attestation_digest,
        "release_id": release_id,
        "tag_name": "v1.0.5",
        "tag_peel_sha": candidate_commit_sha,
        "target_commitish_resolved_sha": candidate_commit_sha,
        "workflow_run_attempt": 1,
        "workflow_run_id": run_id,
    }
    return payload, encoded_responses


def _profile_files(
    phase: str,
    *,
    candidate_commit_sha: str = "1" * 40,
    candidate_tree_sha: str = "3" * 40,
) -> dict[str, str]:
    state_tokens = {
        "S0": "v1.0.5 release candidate / not published / prepared-disabled",
        "S1": "v1.0.5 release candidate / release-enabled / outcome-pending-closure",
        "S2-success": (
            "v1.0.5 Permanent Release Truth / published / immutable / "
            "Certificate-trusted"
        ),
        "S2-burn": (
            "v1.0.5 Permanent Release Truth / terminal-generation-burn / "
            "non-authoritative"
        ),
    }
    success_payload, success_responses = _success_authority_fixture(
        candidate_commit_sha,
        candidate_tree_sha,
    )
    SUCCESS_AUTHORITY_RESPONSES.clear()
    SUCCESS_AUTHORITY_RESPONSES.update(success_responses)
    payloads: dict[str, dict[str, object]] = {
        "S0": {"phase": "S0"},
        "S1": {"phase": "S1"},
        "S2-success": success_payload,
        "S2-burn": {
            "authority_id": (
                "refs/tags/release-truth/v1.0.5/certificate/g0"
            ),
            "authority_kind": "protected-certificate-tag",
            "candidate_commit_sha": candidate_commit_sha,
            "candidate_tree_sha": candidate_tree_sha,
            "generation": 0,
            "phase": "S2-burn",
            "terminal_stage": "certificate-created",
            "workflow_run_attempt": 1,
            "workflow_run_id": 12,
        },
    }
    flag = "true" if phase == "S1" else "false"
    files = {
        path: state_tokens[phase] for path in RELEASE_SURFACE_PATHS
    }
    if phase == "S2-success":
        archive = (
            "https://github.com/SinclairPan/Ai_AutoSDLC/archive/"
            + candidate_commit_sha
            + ".zip"
        )
        for path in RELEASE_SURFACE_PATHS:
            files[path] += f"\nCanonical online install spec: {archive}\n"
    files["README.md"] += (
        "\n" + _phase_line(payloads[phase])
        + "\n<!-- WI010_RELEASE_TREE_SEAL: " + "0" * 64 + " -->\n"
    )
    files[".github/workflows/release-build.yml"] = "\n".join(
        (
            f'RELEASE_BOOTSTRAP_ENABLED: "{flag}"',
            f'RELEASE_ENVIRONMENT_PROTECTION_VERIFIED: "{flag}"',
            f'RELEASE_TAG_RULESET_PROTECTION_VERIFIED: "{flag}"',
        )
    )
    files["tests/integration/test_github_workflows.py"] = "\n".join(
        (
            f'"RELEASE_BOOTSTRAP_ENABLED": "{flag}"',
            '"RELEASE_BOOTSTRAP_ENABLED": "false"',
            f'workflow["env"]["RELEASE_BOOTSTRAP_ENABLED"] == "{flag}"',
            f'"RELEASE_ENVIRONMENT_PROTECTION_VERIFIED": "{flag}"',
            (
                'workflow["env"]["RELEASE_ENVIRONMENT_PROTECTION_VERIFIED"] '
                f'== "{flag}"'
            ),
            f'"RELEASE_TAG_RULESET_PROTECTION_VERIFIED": "{flag}"',
            (
                'workflow["env"]["RELEASE_TAG_RULESET_PROTECTION_VERIFIED"] '
                f'== "{flag}"'
            ),
        )
    )
    repository_root = Path(__file__).resolve().parents[2]
    shell_installer = (repository_root / "packaging/install_online.sh").read_text(
        encoding="utf-8"
    )
    powershell_installer = (
        repository_root / "packaging/install_online.ps1"
    ).read_text(encoding="utf-8")
    if phase == "S2-success":
        archive = (
            "https://github.com/SinclairPan/Ai_AutoSDLC/archive/"
            + candidate_commit_sha
            + ".zip"
        )
        files["packaging/install_online.sh"] = shell_installer.replace(
            "#   AI_SDLC_PACKAGE_SPEC=ai-sdlc==1.0.2   optional published package spec for pip install",
            f"#   AI_SDLC_PACKAGE_SPEC={archive}   可选的精确 PR2 commit archive 安装源",
        ).replace(
            'PACKAGE_SPEC="${AI_SDLC_PACKAGE_SPEC:-ai-sdlc==1.0.2}"',
            f'PACKAGE_SPEC="${{AI_SDLC_PACKAGE_SPEC:-{archive}}}"',
        )
        files["packaging/install_online.ps1"] = powershell_installer.replace(
            '[string]$PackageSpec = "ai-sdlc==1.0.2",',
            f'[string]$PackageSpec = "{archive}",',
        )
    else:
        files["packaging/install_online.sh"] = shell_installer
        files["packaging/install_online.ps1"] = powershell_installer
    return files


def _init_foundation_repository(root: Path) -> None:
    _run_git(root, "init", "-q")
    _run_git(root, "config", "user.name", "WI010 Test")
    _run_git(root, "config", "user.email", "wi010@example.invalid")
    _run_git(root, "config", "core.fileMode", "false")
    repository_root = Path(__file__).resolve().parents[2]
    tracked_paths = {
        *release_identity.WI010_S2_SUCCESS_PATHS,
        *release_identity.WI010_TRUST_ROOTS,
        release_identity.WI010_ATTESTATION_VERIFIER_PATH,
        b".github/workflows/release-artifact-smoke.yml",
        b"ai_sdlc/__init__.py",
        "docs/框架自迭代开发与发布约定.md".encode(),
        b"pyproject.toml",
        b"scripts/posix_clean_user_e2e.py",
        b"scripts/windows_clean_user_e2e.py",
        b"scripts/windows_clean_user_e2e_support.py",
        b"src/ai_sdlc/__init__.py",
    }
    files = {
        raw_path.decode("utf-8"): (repository_root / raw_path.decode("utf-8")).read_text(
            encoding="utf-8"
        )
        for raw_path in tracked_paths
    }
    foundation_trust_roots = {
        relative: files[relative]
        for relative in (
            "scripts/validate_public_release_identity.py",
            "src/ai_sdlc/core/verify_constraints.py",
            "tests/unit/test_public_release_identity.py",
        )
    }
    foundation_readme = files["README.md"]
    foundation_readme = release_identity.WI010_SEAL_MARKER_PATTERN.sub(
        "<!-- WI010_RELEASE_TREE_SEAL: " + "0" * 64 + " -->",
        foundation_readme,
    )
    foundation_marker_block = (
        '<!-- WI010_RELEASE_PHASE: {"phase":"S0"} -->\n'
        "<!-- WI010_RELEASE_TREE_SEAL: " + "0" * 64 + " -->\n\n"
    )
    assert foundation_readme.count(foundation_marker_block) == 1
    base_readme = foundation_readme.replace(foundation_marker_block, "", 1)
    files["README.md"] = base_readme
    files.update(
        {
            "scripts/validate_public_release_identity.py": "#!/usr/bin/env python3\nbase\n",
            "src/ai_sdlc/core/verify_constraints.py": "base\n",
            "tests/unit/test_public_release_identity.py": "base\n",
            "unchanged.txt": "unchanged\n",
        }
    )
    for relative, content in files.items():
        _write(root, relative, content)
    _run_git(root, "add", "--all")
    _run_git(root, "update-index", "--chmod=+x", "scripts/validate_public_release_identity.py")
    _run_git(root, "update-index", "--chmod=+x", "packaging/install_online.sh")
    _run_git(root, "commit", "-qm", "base")

    _write(root, "README.md", foundation_readme)
    for relative, content in foundation_trust_roots.items():
        _write(root, relative, content)
    _run_git(root, "add", "--all")
    _run_git(root, "update-index", "--chmod=+x", "scripts/validate_public_release_identity.py")
    _run_git(root, "commit", "-qm", "foundation")


def _seal_head(root: Path) -> str:
    snapshot = release_identity.read_release_tree_snapshot(root)
    computed = release_identity.compute_release_tree_seal(snapshot)
    readme = (root / "README.md").read_text(encoding="utf-8")
    _write(
        root,
        "README.md",
        release_identity.WI010_SEAL_MARKER_PATTERN.sub(
            "<!-- WI010_RELEASE_TREE_SEAL: " + computed + " -->",
            readme,
        ),
    )
    _run_git(root, "add", "README.md")
    _run_git(root, "commit", "--amend", "--no-edit", "-q")
    return computed


def _commit_profile_transition(root: Path, phase: str) -> bytes:
    parent_commit = _run_git(root, "rev-parse", "HEAD").stdout.decode().strip()
    parent_tree = _run_git(root, "rev-parse", "HEAD^{tree}").stdout.decode().strip()
    profile_files = _profile_files(
        phase,
        candidate_commit_sha=parent_commit,
        candidate_tree_sha=parent_tree,
    )
    target_phase, payload = release_identity._extract_phase_payload(
        profile_files["README.md"]
    )
    parent_snapshot = release_identity.read_release_tree_snapshot(root)
    rendered = release_identity.render_release_transition(
        parent_snapshot.entries,
        parent_snapshot.phase,
        target_phase,
        payload,
    )
    for raw_path, content in rendered.items():
        path = raw_path.decode("utf-8")
        _write(root, path, content.decode("utf-8"))
    decoded_paths = [path.decode("utf-8") for path in sorted(rendered)]
    _run_git(root, "add", "--", *decoded_paths)
    _run_git(root, "commit", "-qm", phase)
    _seal_head(root)
    return _run_git(root, "rev-parse", "HEAD").stdout.strip()


def test_scan_rejects_non_public_surfaces_and_pre_release_identity(
    tmp_path: Path,
) -> None:
    candidate_version = f"{0}.{8}.{0}"
    files = {
        f"docs/releases/v{candidate_version}.md": "candidate release",
        ".ai-sdlc/work-items/001-demo/handoff.md": "runtime state",
        ".ai-sdlc/state/checkpoint.yml": "current_stage: init",
        "internal-notes.md": "private material",
        "README.md": f"AI-SDLC v{candidate_version}",
    }

    findings = scan_paths(tmp_path, files)

    assert {finding.marker for finding in findings} == {
        "non-public-doc",
        "non-public-root-doc",
        "non-public-work-state",
        "pre-1.0-product-version",
        "runtime-state",
    }


def test_scan_rejects_repository_mismatch_and_local_path_disclosure(
    tmp_path: Path,
) -> None:
    local_path = "/" + "Users" + "/demo/project/sample"
    files = {
        "README.md": "https://github.com/example/sample\n" + local_path,
    }

    findings = scan_paths(tmp_path, files)

    assert {finding.marker for finding in findings} == {
        "local-path-disclosure",
        "repository-identity-mismatch",
    }


def test_required_surfaces_enforce_current_release_identity(tmp_path: Path) -> None:
    _assert_authority_tls_ignores_ambient_ca(tmp_path)
    files = {
        "README.md": (
            f"{CURRENT_REPOSITORY_URL}\nAI-SDLC {CURRENT_VERSION}\n{STABLE_SOURCE_CLONE}\n"
            "v1.0.5 release candidate / not published / prepared-disabled\n"
            "last published version is v1.0.2\n"
            "v1.0.4 terminal NO-GO / not released\n"
            "WorkItem 010 three-PR release migration\n"
            "active no-bypass tag ruleset protects software and Certificate tags"
        ),
    }

    findings = validate_required_surfaces(files)

    assert any(
        finding.marker == "required-public-surface-missing" for finding in findings
    )
    assert not any(finding.path == "README.md" for finding in findings)
    assert "WorkItem 008" in FORBIDDEN_SURFACE_MARKERS["README.md"]
    obsolete = validate_required_surfaces(
        {
            "README.md": (
                f"{CURRENT_REPOSITORY_URL}\nAI-SDLC {CURRENT_VERSION}\n{STABLE_SOURCE_CLONE}\n"
                "WorkItem 008 正在恢复 v1.0.4"
            )
        }
    )
    assert any(
        finding.path == "README.md"
        and finding.marker == "obsolete-release-authorization"
        for finding in obsolete
    )
    for path in ("README.md", "USER_GUIDE.zh-CN.md", "docs/product-contract.md"):
        markers = REQUIRED_SURFACES[path]
        assert "v1.0.5 release candidate / not published / prepared-disabled" in markers
        assert "last published version is v1.0.2" in markers
        assert "v1.0.4 terminal NO-GO / not released" in markers
        assert "WorkItem 010 three-PR release migration" in markers
        assert (
            "active no-bypass tag ruleset protects software and Certificate tags"
            in markers
        )
    terminal_release_surfaces = {
        "packaging/offline/README.md": "上传动作必须由有权限的维护者明确触发",
        "packaging/offline/RELEASE_CHECKLIST.md": "上传动作由有权限维护者明确执行",
        "docs/pull-request-checklist.zh.md": "当前发布版本为 `1.0.4`",
    }
    for path, obsolete_marker in terminal_release_surfaces.items():
        markers = REQUIRED_SURFACES[path]
        assert PUBLISHED_VERSION in markers
        assert "v1.0.5 release candidate / not published / prepared-disabled" in markers
        assert "v1.0.4 terminal NO-GO / not released" in markers
        assert "WorkItem 010 three-PR release migration" in markers
        assert "不得 redispatch、rerun、上传或发布 v1.0.4" in markers
        assert "不得上传、发布或下载 v1.0.5 候选" in markers
        assert obsolete_marker in FORBIDDEN_SURFACE_MARKERS[path]
    assert REQUIRED_SURFACES["packaging/install_online.sh"] == (
        'PACKAGE_SPEC="${AI_SDLC_PACKAGE_SPEC:-ai-sdlc==1.0.2}"',
    )
    assert (
        "AI_SDLC_PACKAGE_SPEC=ai-sdlc==1.0.5"
        in FORBIDDEN_SURFACE_MARKERS["packaging/install_online.sh"]
    )
    assert (
        'PACKAGE_SPEC="${AI_SDLC_PACKAGE_SPEC:-ai-sdlc}"'
        in FORBIDDEN_SURFACE_MARKERS["packaging/install_online.sh"]
    )
    repository_root = Path(__file__).resolve().parents[2]
    release_readme = (repository_root / "README.md").read_text(encoding="utf-8")
    installer = (repository_root / "packaging" / "install_online.sh").read_text(
        encoding="utf-8"
    )
    installer_findings = [
        finding
        for finding in validate_required_surfaces(
            {"README.md": release_readme, "packaging/install_online.sh": installer}
        )
        if finding.path == "packaging/install_online.sh"
    ]
    assert installer_findings == []
    assert REQUIRED_SURFACES["packaging/install_online.ps1"] == (
        '[string]$PackageSpec = "ai-sdlc==1.0.2",',
    )
    assert (
        '[string]$PackageSpec = "ai-sdlc",'
        in FORBIDDEN_SURFACE_MARKERS["packaging/install_online.ps1"]
    )
    powershell_installer = (
        repository_root / "packaging" / "install_online.ps1"
    ).read_text(encoding="utf-8")
    powershell_findings = [
        finding
        for finding in validate_required_surfaces(
            {
                "README.md": release_readme,
                "packaging/install_online.ps1": powershell_installer,
            }
        )
        if finding.path == "packaging/install_online.ps1"
    ]
    assert powershell_findings == []
    release_convention = REQUIRED_SURFACES["docs/框架自迭代开发与发布约定.md"]
    assert {
        "## v1.0.4 bootstrap 终止记录（2026-08-09）",
        "terminal NO-GO / not released / bootstrap budget exhausted",
        "0776885aeb6299bad3c13fd6c47658ad17dad5e1",
        "6125d7e80b1a66eead4ddf5654a578ec2a1e856e",
        "a6a1f2ac463d9ca2dc1ea68af73271e679449015",
        "367380686",
        "31295426083",
        "93199662116",
        "93211087289",
        "93211087697",
        "1 failed / 6219 passed / 16 skipped",
        "zero assets",
        "UNKNOWN",
        "pre-tag qualification",
        "WorkItem 009",
        "WorkItem 010",
        "active no-bypass tag ruleset protects software and Certificate tags",
        "Actions history duplicate-run detector",
        "retention and no-delete trust boundary",
        "not an immutable authority",
        "protected tag namespace becomes the durable burn authority",
    } <= set(release_convention)

    # 生产变更：四相 profile 必须是闭集，任一六面、flag 或证据错位都失败。
    for phase in ("S0", "S1", "S2-success", "S2-burn"):
        phase_files = _profile_files(phase)
        assert release_identity.validate_wi010_release_profile(phase_files) == []
        assert verify_constraints._wi010_release_phase_blockers_from_files(
            phase_files
        ) == []

        mixed_files = dict(phase_files)
        mixed_files["docs/product-contract.md"] = _profile_files("S0" if phase != "S0" else "S1")[
            "docs/product-contract.md"
        ]
        assert any(
            finding.marker == "wi010-release-profile-mixed"
            for finding in release_identity.validate_wi010_release_profile(mixed_files)
        )

    # 正常 pre-commit 约束必须同时检查工作树，不能只信任已 seal 的 HEAD。
    worktree_root = tmp_path / "wi010-worktree-phase-drift"
    worktree_files = _profile_files("S0")
    for relative, content in worktree_files.items():
        _write(worktree_root, relative, content)
    foundation_policy = (
        repository_root / "docs/框架自迭代开发与发布约定.md"
    ).read_text(encoding="utf-8")
    _write(
        worktree_root,
        "docs/框架自迭代开发与发布约定.md",
        foundation_policy,
    )
    _write(
        worktree_root,
        "scripts/validate_public_release_identity.py",
        (repository_root / "scripts/validate_public_release_identity.py").read_text(
            encoding="utf-8"
        ),
    )
    _write(worktree_root, ".gitignore", "__pycache__/\n")
    _run_git(worktree_root, "init", "-q")
    _run_git(worktree_root, "config", "user.name", "WI010 Test")
    _run_git(worktree_root, "config", "user.email", "wi010@example.invalid")
    _run_git(worktree_root, "config", "core.fileMode", "false")
    _run_git(worktree_root, "add", "--all")
    _run_git(
        worktree_root,
        "update-index",
        "--chmod=+x",
        "scripts/validate_public_release_identity.py",
    )
    if os.name != "nt":
        (worktree_root / "scripts/validate_public_release_identity.py").chmod(0o755)
    _run_git(worktree_root, "commit", "-qm", "sealed S0")

    # 约束入口必须把公开面解码失败收敛为 blocker，不能让调用方收到异常。
    invalid_utf8_root = tmp_path / "wi010-invalid-utf8-surface"
    _run_git(worktree_root, "clone", "-q", ".", str(invalid_utf8_root))
    unreadable_prefix = "BLOCKER: constraint input is unreadable as strict UTF-8:"
    for relative in verify_constraints.WI010_PHASE_SURFACES:
        invalid_path = invalid_utf8_root / relative
        sealed_bytes = invalid_path.read_bytes()
        invalid_path.write_bytes(b"\xff\xfe")
        invalid_utf8_blockers = verify_constraints.collect_constraint_blockers(
            invalid_utf8_root
        )
        assert any(
            blocker.startswith(unreadable_prefix)
            for blocker in invalid_utf8_blockers
        )
        invalid_utf8_report = verify_constraints.build_constraint_report(
            invalid_utf8_root
        )
        assert any(
            blocker.startswith(unreadable_prefix)
            for blocker in invalid_utf8_report.blockers
        )
        invalid_path.write_bytes(sealed_bytes)

    (invalid_utf8_root / "README.md").write_bytes(b"\xff\xfe")
    (invalid_utf8_root / ".ai-sdlc").mkdir()
    cli_environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            filter(
                None,
                (str(repository_root / "src"), os.environ.get("PYTHONPATH", "")),
            )
        ),
    }
    normal_cli = subprocess.run(
        [sys.executable, "-m", "ai_sdlc", "verify", "constraints"],
        cwd=invalid_utf8_root,
        env=cli_environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert normal_cli.returncode == 1
    assert unreadable_prefix in normal_cli.stdout
    assert "Traceback" not in normal_cli.stdout + normal_cli.stderr
    json_cli = subprocess.run(
        [sys.executable, "-m", "ai_sdlc", "verify", "constraints", "--json"],
        cwd=invalid_utf8_root,
        env=cli_environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert json_cli.returncode == 1
    json_report = json.loads(json_cli.stdout)
    assert json_report["ok"] is False
    assert any(
        blocker.startswith(unreadable_prefix) for blocker in json_report["blockers"]
    )

    if os.name != "nt":
        mode_root = tmp_path / "wi010-worktree-mode-semantics"
        mode_root.mkdir()
        _init_foundation_repository(mode_root)
        validator_worktree_path = (
            mode_root / "scripts/validate_public_release_identity.py"
        )
        installer_worktree_path = mode_root / "packaging/install_online.sh"
        validator_worktree_path.chmod(0o755)
        installer_worktree_path.chmod(0o755)
        assert release_identity.validate_release_worktree_seal(mode_root) == []
        validator_worktree_path.chmod(0o655)
        assert any(
            finding.marker == "wi010-release-worktree-unsealed"
            for finding in release_identity.validate_release_worktree_seal(
                mode_root
            )
        )
        validator_worktree_path.chmod(0o700)
        assert release_identity.validate_release_worktree_seal(mode_root) == []
        readme_mode_path = mode_root / "README.md"
        readme_mode_path.chmod(0o645)
        assert release_identity.validate_release_worktree_seal(mode_root) == []
        crlf_script_path = mode_root / "packaging/install_online.sh"
        sealed_script = crlf_script_path.read_bytes()
        assert b"\r\n" not in sealed_script
        crlf_script_path.write_bytes(sealed_script.replace(b"\n", b"\r\n"))
        status = _run_git(mode_root, "status", "--porcelain=v1").stdout
        assert b"packaging/install_online.sh" in status
        assert any(
            finding.marker == "wi010-release-worktree-unsealed"
            for finding in release_identity.validate_release_worktree_seal(
                mode_root
            )
        )
    missing_entry_root = tmp_path / "wi010-missing-worktree-entries"
    _run_git(worktree_root, "clone", "-q", ".", str(missing_entry_root))
    for relative in (
        "README.md",
        "USER_GUIDE.zh-CN.md",
        "packaging/offline/README.md",
        "docs/框架自迭代开发与发布约定.md",
    ):
        (missing_entry_root / relative).unlink()
    assert any(
        "WI010 foundation contract marker is missing" in blocker
        for blocker in verify_constraints._release_docs_consistency_blockers(
            missing_entry_root
        )
    )
    assert any(
        "WI010 foundation contract marker is missing" in blocker
        for blocker in verify_constraints.collect_constraint_blockers(
            missing_entry_root
        )
    )
    readme_path = worktree_root / "README.md"
    sealed_readme = readme_path.read_text(encoding="utf-8")
    clean_redirect_root = tmp_path / "wi010-clean-git-routing-target"
    _run_git(worktree_root, "clone", "-q", ".", str(clean_redirect_root))
    _write(
        worktree_root,
        "README.md",
        sealed_readme + "\nv1.0.5 已正式发布，普通用户现在可以下载安装。\n",
    )
    routed_variables = {
        "GIT_DIR": str(clean_redirect_root / ".git"),
        "GIT_WORK_TREE": str(clean_redirect_root),
        "GIT_INDEX_FILE": str(clean_redirect_root / ".git" / "index"),
    }
    previous_routing = {name: os.environ.get(name) for name in routed_variables}
    try:
        os.environ.update(routed_variables)
        routed_findings = release_identity.validate_release_worktree_seal(
            worktree_root
        )
        assert any(
            finding.marker == "wi010-release-worktree-unsealed"
            for finding in routed_findings
        ), routed_findings
        routed_blockers = verify_constraints._release_docs_consistency_blockers(
            worktree_root
        )
        assert any(
            "wi010-release-worktree-unsealed" in blocker
            for blocker in routed_blockers
        ), routed_blockers
    finally:
        for name, previous in previous_routing.items():
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous
    contradictory_blockers = verify_constraints._release_docs_consistency_blockers(
        worktree_root
    )
    assert any(
        "wi010-release-worktree-unsealed" in blocker
        for blocker in contradictory_blockers
    ), contradictory_blockers
    _write(worktree_root, "README.md", sealed_readme)

    # Git clean filter 可伪造 status/hash，但不得改变 validator 实际执行身份。
    validator_path = worktree_root / "scripts/validate_public_release_identity.py"
    sealed_validator = validator_path.read_bytes()
    attributes_path = worktree_root / ".git" / "info" / "attributes"
    attributes_path.write_text(
        "scripts/validate_public_release_identity.py filter=wi010-mask\n",
        encoding="utf-8",
    )
    _run_git(
        worktree_root,
        "config",
        "filter.wi010-mask.clean",
        "git show HEAD:scripts/validate_public_release_identity.py",
    )
    _run_git(worktree_root, "config", "filter.wi010-mask.required", "true")
    validator_path.write_bytes(
        sealed_validator + b"\ndef scan_public_tree(root):\n    return []\n"
    )
    _run_git(
        worktree_root,
        "add",
        "scripts/validate_public_release_identity.py",
    )
    assert _run_git(worktree_root, "status", "--porcelain=v1").stdout == b""
    filtered_findings = release_identity.validate_release_worktree_seal(
        worktree_root
    )
    assert any(
        finding.marker == "wi010-release-worktree-unsealed"
        for finding in filtered_findings
    ), filtered_findings
    filtered_blockers = verify_constraints._release_docs_consistency_blockers(
        worktree_root
    )
    assert any(
        "wi010-release-worktree-unsealed" in blocker
        or "validator worktree differs" in blocker
        for blocker in filtered_blockers
    ), filtered_blockers
    validator_path.write_bytes(sealed_validator)
    attributes_path.unlink()
    _run_git(worktree_root, "config", "--unset-all", "filter.wi010-mask.clean")
    _run_git(worktree_root, "config", "--unset-all", "filter.wi010-mask.required")
    _run_git(
        worktree_root,
        "add",
        "scripts/validate_public_release_identity.py",
    )

    # 路径 loader 会消费 ignored pyc；冻结 validator 必须改为执行 Git blob 字节。
    malicious_source = tmp_path / "wi010-malicious-validator.py"
    malicious_source.write_text(
        "def scan_public_tree(root):\n    return []\n",
        encoding="utf-8",
    )
    cache_path = Path(importlib.util.cache_from_source(str(validator_path)))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    py_compile.compile(
        str(malicious_source),
        cfile=str(cache_path),
        doraise=True,
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )
    malicious_spec = importlib.util.spec_from_file_location(
        "_wi010_malicious_path_loader_probe", validator_path
    )
    assert malicious_spec is not None and malicious_spec.loader is not None
    malicious_module = importlib.util.module_from_spec(malicious_spec)
    malicious_spec.loader.exec_module(malicious_module)
    assert malicious_module.scan_public_tree(worktree_root) == []
    assert _run_git(worktree_root, "status", "--porcelain=v1").stdout == b""
    _write(
        worktree_root,
        "README.md",
        sealed_readme + "\nv1.0.5 已正式发布，普通用户现在可以下载安装。\n",
    )
    pyc_blockers = verify_constraints._release_docs_consistency_blockers(
        worktree_root
    )
    assert any(
        "wi010-release-worktree-unsealed" in blocker
        for blocker in pyc_blockers
    ), pyc_blockers
    _write(worktree_root, "README.md", sealed_readme)
    cache_path.unlink()

    # stat-cache 可在同size/同mtime下误判；raw HEAD snapshot 比较必须识别。
    _run_git(worktree_root, "config", "core.trustctime", "false")
    _run_git(worktree_root, "config", "core.checkStat", "minimal")
    assert _run_git(worktree_root, "status", "--porcelain=v1").stdout == b""
    sealed_stat = readme_path.stat()
    same_size_readme = sealed_readme.replace(
        "last published version is v1.0.2",
        "last published version is v1.0.5",
        1,
    )
    assert len(same_size_readme.encode("utf-8")) == len(
        sealed_readme.encode("utf-8")
    )
    _write(worktree_root, "README.md", same_size_readme)
    os.utime(
        readme_path,
        ns=(sealed_stat.st_atime_ns, sealed_stat.st_mtime_ns),
    )
    assert _run_git(worktree_root, "status", "--porcelain=v1").stdout == b""
    stat_findings = release_identity.validate_release_worktree_seal(worktree_root)
    assert any(
        finding.marker == "wi010-release-worktree-unsealed"
        for finding in stat_findings
    ), stat_findings
    stat_blockers = verify_constraints._release_docs_consistency_blockers(
        worktree_root
    )
    assert any(
        "wi010-release-worktree-unsealed" in blocker
        for blocker in stat_blockers
    ), stat_blockers
    _write(worktree_root, "README.md", sealed_readme)
    _run_git(worktree_root, "config", "--unset-all", "core.trustctime")
    _run_git(worktree_root, "config", "--unset-all", "core.checkStat")
    _run_git(worktree_root, "add", "README.md")

    # 空 fsmonitor token 可让 Git status 跳过变化；raw 比较不消费该提示。
    if os.name != "nt":
        fsmonitor_hook = tmp_path / "wi010-empty-fsmonitor"
        fsmonitor_hook.write_text(
            '#!/bin/sh\nprintf "fake-token\\0"\n',
            encoding="utf-8",
        )
        fsmonitor_hook.chmod(0o755)
        _run_git(
            worktree_root,
            "config",
            "core.fsmonitor",
            str(fsmonitor_hook),
        )
        assert _run_git(worktree_root, "status", "--porcelain=v1").stdout == b""
        _write(
            worktree_root,
            "README.md",
            sealed_readme + "\nv1.0.5 已正式发布，普通用户现在可以下载安装。\n",
        )
        assert _run_git(worktree_root, "status", "--porcelain=v1").stdout == b""
        fsmonitor_findings = release_identity.validate_release_worktree_seal(
            worktree_root
        )
        assert any(
            finding.marker == "wi010-release-worktree-unsealed"
            for finding in fsmonitor_findings
        ), fsmonitor_findings
        fsmonitor_blockers = verify_constraints._release_docs_consistency_blockers(
            worktree_root
        )
        assert any(
            "wi010-release-worktree-unsealed" in blocker
            for blocker in fsmonitor_blockers
        ), fsmonitor_blockers
        _write(worktree_root, "README.md", sealed_readme)
        _run_git(worktree_root, "config", "--unset-all", "core.fsmonitor")
        _run_git(worktree_root, "update-index", "--no-fsmonitor")
        _run_git(worktree_root, "add", "README.md")

    for hidden_flag, clear_flag in (
        ("--assume-unchanged", "--no-assume-unchanged"),
        ("--skip-worktree", "--no-skip-worktree"),
    ):
        _run_git(worktree_root, "update-index", hidden_flag, "README.md")
        _write(
            worktree_root,
            "README.md",
            sealed_readme + "\nv1.0.5 已正式发布，普通用户现在可以下载安装。\n",
        )
        hidden_blockers = verify_constraints._release_docs_consistency_blockers(
            worktree_root
        )
        assert any(
            "wi010-release-worktree-unsealed" in blocker
            for blocker in hidden_blockers
        ), (hidden_flag, hidden_blockers)
        _run_git(worktree_root, "update-index", clear_flag, "README.md")
        _write(worktree_root, "README.md", sealed_readme)
    _write(
        worktree_root,
        "README.md",
        sealed_readme + "\nv1.0.5 已正式发布，普通用户现在可以下载安装。\n",
    )
    _run_git(worktree_root, "add", "--all")
    replacement_tree = (
        _run_git(worktree_root, "write-tree").stdout.decode("ascii").strip()
    )
    replacement_commit = (
        _run_git(
            worktree_root,
            "commit-tree",
            replacement_tree,
            "-p",
            "HEAD",
            "-m",
            "replacement with contradictory truth",
        )
        .stdout.decode("ascii")
        .strip()
    )
    _run_git(worktree_root, "replace", "HEAD", replacement_commit)
    assert _run_git(worktree_root, "status", "--porcelain=v1").stdout == b""
    assert (
        _run_git(
            worktree_root,
            "--no-replace-objects",
            "status",
            "--porcelain=v1",
        ).stdout
        != b""
    )
    direct_replace_findings = release_identity.validate_release_worktree_seal(
        worktree_root
    )
    assert any(
        finding.marker == "wi010-release-worktree-unsealed"
        for finding in direct_replace_findings
    ), direct_replace_findings
    replace_blockers = verify_constraints._release_docs_consistency_blockers(
        worktree_root
    )
    assert any(
        "wi010-release-worktree-unsealed" in blocker
        for blocker in replace_blockers
    ), replace_blockers
    _run_git(worktree_root, "replace", "-d", "HEAD")
    _run_git(worktree_root, "restore", "--staged", "--worktree", ".")
    worktree_files["docs/product-contract.md"] = _profile_files("S1")[
        "docs/product-contract.md"
    ]
    _write(
        worktree_root,
        "docs/product-contract.md",
        worktree_files["docs/product-contract.md"],
    )
    assert any(
        "WI010 release phase surface mismatch: "
        "docs/product-contract.md expected S0" in blocker
        for blocker in verify_constraints._release_docs_consistency_blockers(
            worktree_root
        )
    )
    _write(
        worktree_root,
        "docs/product-contract.md",
        _profile_files("S0")["docs/product-contract.md"],
    )
    _write(
        worktree_root,
        "docs/框架自迭代开发与发布约定.md",
        foundation_policy.replace("S0 seal foundation PR", "S0 foundation PR", 1),
    )
    assert any(
        "WI010 foundation contract marker is missing" in blocker
        for blocker in verify_constraints._release_docs_consistency_blockers(
            worktree_root
        )
    )
    _run_git(
        worktree_root,
        "add",
        "docs/框架自迭代开发与发布约定.md",
    )
    _run_git(worktree_root, "commit", "-qm", "remove activation token")
    assert any(
        "WI010 foundation contract marker is missing" in blocker
        for blocker in verify_constraints._release_docs_consistency_blockers(
            worktree_root
        )
    )
    _write(
        worktree_root,
        "docs/框架自迭代开发与发布约定.md",
        foundation_policy,
    )
    _run_git(
        worktree_root,
        "add",
        "docs/框架自迭代开发与发布约定.md",
    )
    _run_git(
        worktree_root,
        "update-index",
        "--chmod=-x",
        "scripts/validate_public_release_identity.py",
    )
    _run_git(worktree_root, "commit", "-qm", "drift validator mode")
    assert any(
        "WI010 validator Git blob/mode differs from foundation" in blocker
        for blocker in verify_constraints._release_docs_consistency_blockers(
            worktree_root
        )
    )
    _write(
        worktree_root,
        "docs/框架自迭代开发与发布约定.md",
        foundation_policy.replace("S0 seal foundation PR", "S0 foundation PR", 1),
    )
    validator_path = worktree_root / "scripts/validate_public_release_identity.py"
    validator_path.write_bytes(validator_path.read_bytes() + b"\n# identity drift\n")
    _run_git(worktree_root, "add", "--all")
    _run_git(
        worktree_root,
        "update-index",
        "--chmod=-x",
        "scripts/validate_public_release_identity.py",
    )
    _run_git(worktree_root, "commit", "-qm", "drift both frozen identities")
    assert verify_constraints._wi010_foundation_contract_active(worktree_root)
    assert any(
        "WI010 foundation contract marker is missing" in blocker
        for blocker in verify_constraints._release_docs_consistency_blockers(
            worktree_root
        )
    )
    shallow_identity_root = tmp_path / "wi010-shallow-identity-drift"
    _run_git(
        worktree_root,
        "clone",
        "-q",
        "--depth=1",
        worktree_root.as_uri(),
        str(shallow_identity_root),
    )
    before_shallow_identity = (
        _run_git(shallow_identity_root, "rev-parse", "HEAD").stdout,
        _run_git(shallow_identity_root, "rev-parse", "HEAD^{tree}").stdout,
        _run_git(shallow_identity_root, "status", "--porcelain").stdout,
    )
    assert verify_constraints._wi010_foundation_contract_active(
        shallow_identity_root
    )
    after_shallow_identity = (
        _run_git(shallow_identity_root, "rev-parse", "HEAD").stdout,
        _run_git(shallow_identity_root, "rev-parse", "HEAD^{tree}").stdout,
        _run_git(shallow_identity_root, "status", "--porcelain").stdout,
    )
    assert after_shallow_identity == before_shallow_identity

    # depth-1 自托管 checkout 即使离线且候选删除全部身份/入口，也必须继续激活。
    for relative in (
        "README.md",
        "USER_GUIDE.zh-CN.md",
        "packaging/offline/README.md",
        "docs/框架自迭代开发与发布约定.md",
        "scripts/validate_public_release_identity.py",
    ):
        (worktree_root / relative).unlink()
    _write(
        worktree_root,
        "src/ai_sdlc/core/verify_constraints.py",
        (repository_root / "src/ai_sdlc/core/verify_constraints.py").read_text(
            encoding="utf-8"
        ),
    )
    _run_git(worktree_root, "add", "--all")
    _run_git(worktree_root, "commit", "-qm", "delete identities while offline")
    shallow_offline_root = tmp_path / "wi010-shallow-offline-deletion"
    _run_git(
        worktree_root,
        "clone",
        "-q",
        "--depth=1",
        worktree_root.as_uri(),
        str(shallow_offline_root),
    )
    _run_git(
        shallow_offline_root,
        "remote",
        "set-url",
        "origin",
        "file:///definitely-missing/wi010.git",
    )
    before_shallow_offline = (
        _run_git(shallow_offline_root, "rev-parse", "HEAD").stdout,
        _run_git(shallow_offline_root, "rev-parse", "HEAD^{tree}").stdout,
        _run_git(shallow_offline_root, "status", "--porcelain").stdout,
    )
    original_module_file = verify_constraints.__file__
    try:
        verify_constraints.__file__ = str(
            shallow_offline_root / "src/ai_sdlc/core/verify_constraints.py"
        )
        assert verify_constraints._wi010_foundation_contract_active(
            shallow_offline_root
        )
        assert any(
            "WI010 foundation contract marker is missing" in blocker
            for blocker in verify_constraints._release_docs_consistency_blockers(
                shallow_offline_root
            )
        )
    finally:
        verify_constraints.__file__ = original_module_file
    after_shallow_offline = (
        _run_git(shallow_offline_root, "rev-parse", "HEAD").stdout,
        _run_git(shallow_offline_root, "rev-parse", "HEAD^{tree}").stdout,
        _run_git(shallow_offline_root, "status", "--porcelain").stdout,
    )
    assert after_shallow_offline == before_shallow_offline

    for index, relative in enumerate(
        (
            "scripts/validate_public_release_identity.py",
            "src/ai_sdlc/core/verify_constraints.py",
            "tests/unit/test_public_release_identity.py",
        )
    ):
        ordinary_root = tmp_path / f"ordinary-wi010-path-{index}"
        _write(ordinary_root, "README.md", "ordinary project\n")
        _write(ordinary_root, relative, "ordinary project file\n")
        assert not verify_constraints._wi010_foundation_contract_active(
            ordinary_root
        )
        _run_git(ordinary_root, "init", "-q")
        _run_git(ordinary_root, "config", "user.name", "Ordinary Test")
        _run_git(ordinary_root, "config", "user.email", "ordinary@example.invalid")
        _run_git(ordinary_root, "add", "--all")
        _run_git(ordinary_root, "commit", "-qm", "ordinary project")
        assert not verify_constraints._wi010_foundation_contract_active(
            ordinary_root
        )
        assert not any(
            "WI010" in blocker
            for blocker in verify_constraints._release_docs_consistency_blockers(
                ordinary_root
            )
        )

    malformed = _profile_files("S0")
    malformed["README.md"] += "\n<!-- WI010_RELEASE_PHASE: {\"phase\":\"unknown\"} -->"
    assert any(
        finding.marker == "wi010-release-phase-marker-invalid"
        for finding in release_identity.validate_wi010_release_profile(malformed)
    )

    noncanonical = _profile_files("S0")
    noncanonical["README.md"] = noncanonical["README.md"].replace(
        '{"phase":"S0"}', '{ "phase": "S0" }'
    )
    assert any(
        finding.marker == "wi010-release-phase-payload-invalid"
        for finding in release_identity.validate_wi010_release_profile(noncanonical)
    )

    wrong_flag = _profile_files("S1")
    wrong_flag[".github/workflows/release-build.yml"] = wrong_flag[
        ".github/workflows/release-build.yml"
    ].replace('RELEASE_BOOTSTRAP_ENABLED: "true"', 'RELEASE_BOOTSTRAP_ENABLED: "false"')
    assert any(
        finding.marker == "wi010-release-profile-flags-invalid"
        for finding in release_identity.validate_wi010_release_profile(wrong_flag)
    )
    assert verify_constraints._wi010_release_phase_blockers_from_files(wrong_flag)

    success_mismatch = _profile_files("S2-success")
    success_mismatch["packaging/install_online.ps1"] = success_mismatch[
        "packaging/install_online.ps1"
    ].replace("1" * 40, "6" * 40)
    assert any(
        finding.marker == "wi010-release-profile-installer-invalid"
        for finding in release_identity.validate_wi010_release_profile(
            success_mismatch
        )
    )

    comment_bypass = _profile_files("S2-success")
    expected_archive = (
        "https://github.com/SinclairPan/Ai_AutoSDLC/archive/" + "1" * 40 + ".zip"
    )
    comment_bypass["packaging/install_online.sh"] = (
        'PACKAGE_SPEC="${AI_SDLC_PACKAGE_SPEC:-ai-sdlc==9.9.9}"\n'
        f"# {expected_archive}"
    )
    comment_bypass["packaging/install_online.ps1"] = (
        '[string]$PackageSpec = "ai-sdlc==9.9.9",\n'
        f"# {expected_archive}"
    )
    assert any(
        finding.marker == "wi010-release-profile-installer-invalid"
        for finding in release_identity.validate_wi010_release_profile(comment_bypass)
    )

    reassignment_bypass = _profile_files("S2-success")
    reassignment_bypass["packaging/install_online.sh"] += (
        '\nPACKAGE_SPEC="ai-sdlc==9.9.9"\n'
    )
    reassignment_bypass["packaging/install_online.ps1"] += (
        '\n$PackageSpec = "ai-sdlc==9.9.9"\n'
    )
    assert any(
        finding.marker == "wi010-release-profile-installer-invalid"
        for finding in release_identity.validate_wi010_release_profile(
            reassignment_bypass
        )
    )

    missing_surface_spec = _profile_files("S2-success")
    missing_surface_spec["docs/product-contract.md"] = (
        "v1.0.5 Permanent Release Truth / published / immutable / "
        "Certificate-trusted"
    )
    assert any(
        finding.marker == "wi010-release-profile-archive-invalid"
        for finding in release_identity.validate_wi010_release_profile(
            missing_surface_spec
        )
    )

    wrong_surface_spec = _profile_files("S2-success")
    wrong_surface_spec["docs/product-contract.md"] = wrong_surface_spec[
        "docs/product-contract.md"
    ].replace("1" * 40, "6" * 40)
    assert any(
        finding.marker == "wi010-release-profile-archive-invalid"
        for finding in release_identity.validate_wi010_release_profile(
            wrong_surface_spec
        )
    )

    tag_surface_spec = _profile_files("S2-success")
    tag_surface_spec["docs/product-contract.md"] = tag_surface_spec[
        "docs/product-contract.md"
    ].replace(
        "archive/" + "1" * 40 + ".zip",
        "archive/refs/tags/v1.0.5.zip",
    )
    assert any(
        finding.marker == "wi010-release-profile-archive-invalid"
        for finding in release_identity.validate_wi010_release_profile(
            tag_surface_spec
        )
    )

    for invalid_suffix in (".extra", "?download=1", "#fragment"):
        suffixed_surface_spec = _profile_files("S2-success")
        suffixed_surface_spec["docs/product-contract.md"] = (
            suffixed_surface_spec["docs/product-contract.md"].replace(
                expected_archive,
                expected_archive + invalid_suffix,
            )
        )
        assert any(
            finding.marker == "wi010-release-profile-archive-invalid"
            for finding in release_identity.validate_wi010_release_profile(
                suffixed_surface_spec
            )
        )

    prefixed_surface_spec = _profile_files("S2-success")
    prefixed_surface_spec["docs/product-contract.md"] = prefixed_surface_spec[
        "docs/product-contract.md"
    ].replace(
        "Canonical online install spec: ",
        "Current Canonical online install spec: ",
    )
    assert any(
        finding.marker == "wi010-release-profile-archive-invalid"
        for finding in release_identity.validate_wi010_release_profile(
            prefixed_surface_spec
        )
    )

    success_download = _profile_files("S2-success")
    success_download["USER_GUIDE.zh-CN.md"] += (
        "\nhttps://github.com/SinclairPan/Ai_AutoSDLC/"
        "releases/download/v1.0.5/ai-sdlc-offline-1.0.5-windows-amd64.zip\n"
    )
    assert not any(
        finding.marker == "obsolete-release-authorization"
        and finding.excerpt == "releases/download/v1.0.5/"
        for finding in release_identity.validate_required_surfaces(success_download)
    )

    success_constraint_root = Path(os.environ.get("PYTEST_TMPDIR", "/tmp")) / (
        "wi010-success-constraints-" + os.urandom(8).hex()
    )
    try:
        success_guide = (
            Path(__file__).resolve().parents[2] / "USER_GUIDE.zh-CN.md"
        ).read_text(encoding="utf-8")
        replacements = {
            "v1.0.5 release candidate / not published / prepared-disabled": (
                "v1.0.5 Permanent Release Truth / published / immutable / "
                "Certificate-trusted"
            ),
            "# AI-SDLC 1.0.2 中文用户指南": "# AI-SDLC 1.0.5 中文用户指南",
            "ai-sdlc-offline-1.0.2-windows-amd64.zip": (
                "ai-sdlc-offline-1.0.5-windows-amd64.zip"
            ),
            "ai-sdlc-offline-1.0.2-macos-arm64.tar.gz": (
                "ai-sdlc-offline-1.0.5-macos-arm64.tar.gz"
            ),
            "ai-sdlc-offline-1.0.2-linux-amd64.tar.gz": (
                "ai-sdlc-offline-1.0.5-linux-amd64.tar.gz"
            ),
        }
        for old, new in replacements.items():
            success_guide = success_guide.replace(old, new)
        success_guide += (
            "\nhttps://github.com/SinclairPan/Ai_AutoSDLC/"
            "releases/download/v1.0.5/ai-sdlc-offline-1.0.5-windows-amd64.zip\n"
        )
        _write(
            success_constraint_root,
            "README.md",
            success_download["README.md"],
        )
        _write(success_constraint_root, "USER_GUIDE.zh-CN.md", success_guide)
        assert not any(
            "releases/download/v1.0.5/" in blocker
            for blocker in verify_constraints._beginner_guide_cli_path_blockers(
                success_constraint_root
            )
        )
    finally:
        shutil.rmtree(success_constraint_root, ignore_errors=True)

    success_payload = release_identity._extract_phase_payload(
        success_download["README.md"]
    )[1]
    assert release_identity._terminal_parent_binding_error(
        release_identity.ReleasePhase.S2_SUCCESS,
        success_payload,
        b"1" * 40,
        b"3" * 40,
    ) is None
    assert release_identity._terminal_parent_binding_error(
        release_identity.ReleasePhase.S2_SUCCESS,
        success_payload,
        b"6" * 40,
        b"3" * 40,
    ) is not None

    authority_payload, authority_responses = _success_authority_fixture(
        "1" * 40, "3" * 40
    )
    authority_calls: list[str] = []
    certificate_url = (
        "https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/"
        "release-truth/v1.0.5/certificate/g0/release-certificate.json"
    )
    certificate_raw = authority_responses[certificate_url]
    expected_statement = _certificate_attestation_statement(
        "1" * 40, certificate_raw, 12
    )

    # ignored unchecked pyc 可劫持路径 loader；子进程必须执行捕获的 sealed 源码。
    verifier_probe = tmp_path / "github_attestation_verifier.py"
    verifier_probe_source = (
        "import json\n"
        f"STATEMENTS = {expected_statement!r}\n"
        "if __name__ == '__main__':\n"
        "    print(json.dumps([STATEMENTS], sort_keys=True))\n"
    ).encode()
    verifier_probe.write_bytes(verifier_probe_source)
    malicious_verifier_source = tmp_path / "malicious-attestation-verifier.py"
    malicious_verifier_source.write_text(
        "STATEMENTS = {}\n",
        encoding="utf-8",
    )
    verifier_probe_cache = Path(
        importlib.util.cache_from_source(str(verifier_probe))
    )
    verifier_probe_cache.parent.mkdir(parents=True, exist_ok=True)
    py_compile.compile(
        str(malicious_verifier_source),
        cfile=str(verifier_probe_cache),
        doraise=True,
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )
    malicious_verifier_spec = importlib.util.spec_from_file_location(
        "_wi010_malicious_attestation_verifier_probe",
        verifier_probe,
    )
    assert (
        malicious_verifier_spec is not None
        and malicious_verifier_spec.loader is not None
    )
    malicious_verifier = importlib.util.module_from_spec(malicious_verifier_spec)
    malicious_verifier_spec.loader.exec_module(malicious_verifier)
    assert malicious_verifier.STATEMENTS == {}
    production_verifier_oid = release_identity.WI010_ATTESTATION_VERIFIER_BLOB_OID
    try:
        release_identity.WI010_ATTESTATION_VERIFIER_BLOB_OID = (
            release_identity._git_blob_oid(verifier_probe_source)
        )
        assert release_identity._wi010_run_attestation_verifier(
            certificate_raw,
            [{"bundle": "fixture"}],
            "1" * 40,
            12,
            verifier_probe_source,
        ) == (expected_statement,)
    finally:
        release_identity.WI010_ATTESTATION_VERIFIER_BLOB_OID = production_verifier_oid
        verifier_probe_cache.unlink()

    def fixture_fetch(
        url: str,
        *,
        asset: bool = False,
        max_bytes: int = 4 * 1024 * 1024,
    ) -> bytes:
        del asset
        authority_calls.append(url)
        raw = authority_responses.get(url)
        if raw is None:
            raise ValueError("fixture authority is missing")
        assert len(raw) <= max_bytes
        return raw

    original_authority_fetch = release_identity._wi010_fetch_public_bytes
    original_subprocess_run = release_identity.subprocess.run
    verifier_source = (
        repository_root / "src/ai_sdlc/core/github_attestation_verifier.py"
    ).read_bytes()
    verifier_mode = ["pass"]

    def run_certificate_attestation_verifier(
        command: list[str],
        *,
        check: bool,
        capture_output: bool,
        timeout: int,
        cwd: Path,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[bytes]:
        assert command[:2] == [sys.executable, "-I"]
        assert Path(command[2]).read_bytes() == verifier_source
        assert command[3:5] == [
            str(cwd / "release-certificate.json"),
            "--bundle",
        ]
        assert Path(command[5]).read_text(encoding="utf-8").splitlines() == [
            json.dumps(
                {
                    "mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json",
                    "verificationMaterial": {},
                    "dsseEnvelope": {},
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        ]
        assert Path(command[3]).read_bytes() == certificate_raw
        assert command[6:] == [
            "--repository",
            "SinclairPan/Ai_AutoSDLC",
            "--signer-workflow",
            (
                "SinclairPan/Ai_AutoSDLC/.github/workflows/"
                "release-build.yml@refs/heads/main"
            ),
            "--source-ref",
            "refs/heads/main",
            "--source-digest",
            "1" * 40,
            "--build-trigger",
            "workflow_dispatch",
            "--signer-digest",
            "1" * 40,
            "--run-invocation",
            (
                "https://github.com/SinclairPan/Ai_AutoSDLC/"
                "actions/runs/12/attempts/1"
            ),
        ]
        assert check is False
        assert capture_output is True
        assert timeout == 60
        assert Path(cwd).is_dir()
        for name in (
            "PYTHONPATH",
            "PYTHONHOME",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
        ):
            assert name not in env
        if verifier_mode[0] == "timeout":
            raise subprocess.TimeoutExpired(command, timeout)
        if verifier_mode[0] == "fail":
            return subprocess.CompletedProcess(command, 1, b"", b"invalid")
        if verifier_mode[0] == "malformed":
            return subprocess.CompletedProcess(command, 0, b"{", b"")
        assert verifier_mode[0] == "pass"
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps([expected_statement], sort_keys=True).encode("utf-8"),
            b"",
        )

    release_identity._wi010_fetch_public_bytes = fixture_fetch
    release_identity.subprocess.run = run_certificate_attestation_verifier
    try:
        release_identity._validate_s2_success_remote_authority(
            authority_payload, b"1" * 40, b"3" * 40, verifier_source
        )
        assert len(authority_calls) == 13
        latest_url = release_identity.WI010_GITHUB_API_ROOT + "/releases/latest"
        valid_latest = authority_responses[latest_url]
        wrong_latest = json.loads(valid_latest)
        wrong_latest["id"] = 99
        wrong_latest["tag_name"] = "v1.0.6"
        authority_responses[latest_url] = json.dumps(wrong_latest).encode("utf-8")
        try:
            release_identity._validate_s2_success_remote_authority(
                authority_payload, b"1" * 40, b"3" * 40, verifier_source
            )
        except ValueError as exc:
            assert "latest Release authority differs" in str(exc)
        else:
            raise AssertionError("different latest Release must fail closed")
        authority_responses.pop(latest_url)
        try:
            release_identity._validate_s2_success_remote_authority(
                authority_payload, b"1" * 40, b"3" * 40, verifier_source
            )
        except ValueError as exc:
            assert "fixture authority is missing" in str(exc)
        else:
            raise AssertionError("missing latest Release must fail closed")
        authority_responses[latest_url] = valid_latest
        release_url = release_identity.WI010_GITHUB_API_ROOT + "/releases/11"
        valid_release = authority_responses[release_url]
        wrong_release = json.loads(valid_release)
        wrong_release["immutable"] = False
        authority_responses[release_url] = json.dumps(wrong_release).encode("utf-8")
        try:
            release_identity._validate_s2_success_remote_authority(
                authority_payload, b"1" * 40, b"3" * 40, verifier_source
            )
        except ValueError as exc:
            assert "software Release authority differs" in str(exc)
        else:
            raise AssertionError("mutable software Release must fail closed")
        authority_responses[release_url] = valid_release

        wrong_release = json.loads(valid_release)
        wrong_release["name"] = "AI-SDLC v1.0.5 (superseded)"
        authority_responses[release_url] = json.dumps(wrong_release).encode("utf-8")
        try:
            release_identity._validate_s2_success_remote_authority(
                authority_payload, b"1" * 40, b"3" * 40, verifier_source
            )
        except ValueError as exc:
            assert "software Release authority differs" in str(exc)
        else:
            raise AssertionError("edited software Release name must fail closed")
        authority_responses[release_url] = valid_release

        wrong_latest = json.loads(valid_latest)
        wrong_latest["body"] = "Install an unrelated package instead."
        authority_responses[latest_url] = json.dumps(wrong_latest).encode("utf-8")
        try:
            release_identity._validate_s2_success_remote_authority(
                authority_payload, b"1" * 40, b"3" * 40, verifier_source
            )
        except ValueError as exc:
            assert "latest Release authority differs" in str(exc)
        else:
            raise AssertionError("edited latest Release body must fail closed")
        authority_responses[latest_url] = valid_latest

        proof_url = (
            "https://github.com/SinclairPan/Ai_AutoSDLC/releases/download/"
            "v1.0.5/release-satisfaction-proof.json"
        )
        valid_proof = authority_responses[proof_url]
        authority_responses[proof_url] = valid_proof + b" "
        try:
            release_identity._validate_s2_success_remote_authority(
                authority_payload, b"1" * 40, b"3" * 40, verifier_source
            )
        except ValueError as exc:
            assert "asset bytes differ" in str(exc)
        else:
            raise AssertionError("Proof asset byte drift must fail closed")
        authority_responses[proof_url] = valid_proof

        run_url = (
            release_identity.WI010_GITHUB_API_ROOT
            + "/actions/runs/12/attempts/1"
        )
        valid_run = authority_responses[run_url]
        wrong_run = json.loads(valid_run)
        wrong_run["head_sha"] = "9" * 40
        authority_responses[run_url] = json.dumps(wrong_run).encode("utf-8")
        try:
            release_identity._validate_s2_success_remote_authority(
                authority_payload, b"1" * 40, b"3" * 40, verifier_source
            )
        except ValueError as exc:
            assert "workflow run authority differs" in str(exc)
        else:
            raise AssertionError("cross-run authority must fail closed")
        authority_responses[run_url] = valid_run

        jobs_url = run_url + "/jobs?per_page=100"
        valid_jobs = authority_responses[jobs_url]
        wrong_jobs = json.loads(valid_jobs)
        wrong_jobs["jobs"][-1]["conclusion"] = "skipped"
        authority_responses[jobs_url] = json.dumps(wrong_jobs).encode("utf-8")
        try:
            release_identity._validate_s2_success_remote_authority(
                authority_payload, b"1" * 40, b"3" * 40, verifier_source
            )
        except ValueError as exc:
            assert "publisher job authority differs" in str(exc)
        else:
            raise AssertionError("load-probe-only run must fail closed")
        authority_responses[jobs_url] = valid_jobs

        certificate_sha256 = hashlib.sha256(certificate_raw).hexdigest()
        attestation_url = (
            release_identity.WI010_GITHUB_API_ROOT
            + f"/attestations/sha256:{certificate_sha256}?per_page=100"
        )
        valid_attestation = authority_responses[attestation_url]
        authority_responses[attestation_url] = b'{"attestations":[]}'
        try:
            release_identity._validate_s2_success_remote_authority(
                authority_payload, b"1" * 40, b"3" * 40, verifier_source
            )
        except ValueError as exc:
            assert "Certificate attestation is missing" in str(exc)
        else:
            raise AssertionError("missing Certificate attestation must fail closed")
        authority_responses[attestation_url] = valid_attestation

        verifier_mode[0] = "timeout"
        try:
            release_identity._validate_s2_success_remote_authority(
                authority_payload, b"1" * 40, b"3" * 40, verifier_source
            )
        except ValueError as exc:
            assert "verification timed out" in str(exc)
        else:
            raise AssertionError("timed out Certificate verifier must fail closed")
        verifier_mode[0] = "fail"
        try:
            release_identity._validate_s2_success_remote_authority(
                authority_payload, b"1" * 40, b"3" * 40, verifier_source
            )
        except ValueError as exc:
            assert "verification failed" in str(exc)
        else:
            raise AssertionError("failed Certificate verifier must fail closed")
        verifier_mode[0] = "malformed"
        try:
            release_identity._validate_s2_success_remote_authority(
                authority_payload, b"1" * 40, b"3" * 40, verifier_source
            )
        except ValueError as exc:
            assert "verification result is invalid" in str(exc)
        else:
            raise AssertionError("malformed verifier output must fail closed")
        verifier_mode[0] = "pass"

        burn_payload = release_identity._extract_phase_payload(
            _profile_files("S2-burn")["README.md"]
        )[1]
        run_url = (
            release_identity.WI010_GITHUB_API_ROOT
            + "/actions/runs/12/attempts/1"
        )
        jobs_url = run_url + "/jobs?per_page=100"
        valid_run = authority_responses[run_url]
        valid_jobs = authority_responses[jobs_url]
        valid_jobs_as_dict = json.loads(valid_jobs)
        try:
            release_identity._validate_s2_burn_remote_authority(
                burn_payload, b"1" * 40
            )
        except ValueError as exc:
            assert "burn workflow run authority differs" in str(exc)
        else:
            raise AssertionError(
                "successful generation cannot authorize tag-based S2-burn"
            )

        failed_run = json.loads(valid_run)
        failed_run["conclusion"] = "failure"
        failed_jobs = json.loads(valid_jobs)
        failed_jobs["jobs"][-1]["conclusion"] = "failure"
        authority_responses[run_url] = json.dumps(failed_run).encode("utf-8")
        authority_responses[jobs_url] = json.dumps(failed_jobs).encode("utf-8")
        release_identity._validate_s2_burn_remote_authority(
            burn_payload, b"1" * 40
        )
        software_tag_url = (
            release_identity.WI010_GITHUB_API_ROOT + "/git/tags/" + "6" * 40
        )
        valid_software_tag = authority_responses[software_tag_url]
        wrong_software_tag = json.loads(valid_software_tag)
        wrong_software_tag["object"]["sha"] = "9" * 40
        authority_responses[software_tag_url] = json.dumps(
            wrong_software_tag
        ).encode("utf-8")
        try:
            release_identity._validate_s2_burn_remote_authority(
                burn_payload, b"1" * 40
            )
        except ValueError as exc:
            assert "annotated tag authority differs" in str(exc)
        else:
            raise AssertionError("burn tag target drift must fail closed")
        authority_responses[software_tag_url] = valid_software_tag

        dispatch_payload = dict(burn_payload)
        dispatch_payload.update(
            {
                "authority_id": "actions/runs/12/attempts/1",
                "authority_kind": "actions-history-retention",
                "terminal_stage": "dispatch-recorded",
            }
        )
        policy_failed_jobs = json.loads(valid_jobs)
        policy_failed_jobs["jobs"][1]["conclusion"] = "failure"
        for job in policy_failed_jobs["jobs"][2:]:
            job["conclusion"] = "skipped"
        authority_responses[jobs_url] = json.dumps(policy_failed_jobs).encode(
            "utf-8"
        )
        release_identity._validate_s2_burn_remote_authority(
            dispatch_payload, b"1" * 40
        )

        compatibility_failed_jobs = json.loads(valid_jobs)
        for job in compatibility_failed_jobs["jobs"][2:]:
            job["conclusion"] = "skipped"
        compatibility_failed_jobs["jobs"].append(
            {
                "id": 1210,
                "run_id": 12,
                "run_attempt": 1,
                "name": "release-assurance / Fast Gate",
                "status": "completed",
                "conclusion": "failure",
                "head_sha": "1" * 40,
            }
        )
        compatibility_failed_jobs["total_count"] = len(
            compatibility_failed_jobs["jobs"]
        )
        authority_responses[jobs_url] = json.dumps(
            compatibility_failed_jobs
        ).encode("utf-8")
        release_identity._validate_s2_burn_remote_authority(
            dispatch_payload, b"1" * 40
        )

        for failed_name in (
            "windows zip",
            "Build Release Proof Inputs",
            "Publish Proof-bound Release",
        ):
            stage_failed_jobs = json.loads(valid_jobs)
            failed_index = next(
                index
                for index, job in enumerate(stage_failed_jobs["jobs"])
                if job["name"] == failed_name
            )
            stage_failed_jobs["jobs"][failed_index]["conclusion"] = "failure"
            for job in stage_failed_jobs["jobs"][failed_index + 1 :]:
                job["conclusion"] = "skipped"
            authority_responses[jobs_url] = json.dumps(
                stage_failed_jobs
            ).encode("utf-8")
            release_identity._validate_s2_burn_remote_authority(
                dispatch_payload, b"1" * 40
            )

        cancelled_run = dict(failed_run)
        cancelled_run["conclusion"] = "cancelled"
        cancelled_jobs = json.loads(valid_jobs)
        for job in cancelled_jobs["jobs"]:
            job["conclusion"] = "skipped"
        authority_responses[run_url] = json.dumps(cancelled_run).encode("utf-8")
        authority_responses[jobs_url] = json.dumps(cancelled_jobs).encode("utf-8")
        release_identity._validate_s2_burn_remote_authority(
            dispatch_payload, b"1" * 40
        )

        for early_jobs in (
            {"total_count": 0, "jobs": []},
            {
                "total_count": 1,
                "jobs": [
                    {
                        **valid_jobs_as_dict["jobs"][1],
                        "conclusion": "success",
                    }
                ],
            },
            {
                "total_count": 2,
                "jobs": [
                    {
                        **valid_jobs_as_dict["jobs"][0],
                        "conclusion": "skipped",
                    },
                    {
                        **valid_jobs_as_dict["jobs"][1],
                        "conclusion": "cancelled",
                    },
                ],
            },
        ):
            authority_responses[jobs_url] = json.dumps(early_jobs).encode("utf-8")
            release_identity._validate_s2_burn_remote_authority(
                dispatch_payload, b"1" * 40
            )

        for forbidden_early_job in (
            {**valid_jobs_as_dict["jobs"][0], "conclusion": "success"},
            {**valid_jobs_as_dict["jobs"][-1], "conclusion": "success"},
        ):
            authority_responses[jobs_url] = json.dumps(
                {"total_count": 1, "jobs": [forbidden_early_job]}
            ).encode("utf-8")
            try:
                release_identity._validate_s2_burn_remote_authority(
                    dispatch_payload, b"1" * 40
                )
            except ValueError as exc:
                assert "burn workflow jobs authority differs" in str(exc)
            else:
                raise AssertionError(
                    "early-stop burn cannot contain a successful load probe or publish"
                )

        authority_responses[run_url] = valid_run
        authority_responses[jobs_url] = valid_jobs
        try:
            release_identity._validate_s2_burn_remote_authority(
                dispatch_payload, b"1" * 40
            )
        except ValueError as exc:
            assert "burn workflow run authority differs" in str(exc)
        else:
            raise AssertionError("successful run cannot authorize S2-burn")
        for tag_payload in (
            burn_payload,
            {
                **burn_payload,
                "authority_id": "refs/tags/v1.0.5",
                "authority_kind": "protected-software-tag",
                "terminal_stage": "software-tag-created",
            },
        ):
            try:
                release_identity._validate_s2_burn_remote_authority(
                    tag_payload, b"1" * 40
                )
            except ValueError as exc:
                assert "burn workflow run authority differs" in str(exc)
            else:
                raise AssertionError(
                    "successful generation cannot authorize tagged S2-burn"
                )
    finally:
        release_identity._wi010_fetch_public_bytes = original_authority_fetch
        release_identity.subprocess.run = original_subprocess_run

    burn_payload = release_identity._extract_phase_payload(
        _profile_files("S2-burn")["README.md"]
    )[1]
    for stage, kind, authority_id in (
        (
            "dispatch-recorded",
            "actions-history-retention",
            "actions/runs/12/attempts/1",
        ),
        ("software-tag-created", "protected-software-tag", "refs/tags/v1.0.5"),
        (
            "certificate-created",
            "protected-certificate-tag",
            "refs/tags/release-truth/v1.0.5/certificate/g0",
        ),
    ):
        authority_payload = dict(burn_payload)
        authority_payload.update(
            {
                "authority_id": authority_id,
                "authority_kind": kind,
                "terminal_stage": stage,
            }
        )
        release_identity._validate_phase_evidence(
            release_identity.ReleasePhase.S2_BURN,
            authority_payload,
        )
    for unprovable_stage in ("release-created", "assets-uploaded", "proof-recorded"):
        try:
            release_identity._validate_phase_evidence(
                release_identity.ReleasePhase.S2_BURN,
                {
                    **burn_payload,
                    "authority_id": "refs/tags/v1.0.5",
                    "authority_kind": "protected-software-tag",
                    "terminal_stage": unprovable_stage,
                },
            )
        except ValueError as exc:
            assert "terminal stage is invalid" in str(exc)
        else:
            raise AssertionError(
                "unobservable post-tag stage must not be asserted as burn truth"
            )
    assert release_identity._terminal_parent_binding_error(
        release_identity.ReleasePhase.S2_BURN,
        burn_payload,
        b"1" * 40,
        b"3" * 40,
    ) is None
    assert release_identity._terminal_parent_binding_error(
        release_identity.ReleasePhase.S2_BURN,
        burn_payload,
        b"1" * 40,
        b"6" * 40,
    ) is not None

    contradictory_burn = _profile_files("S2-burn")
    contradictory_burn["README.md"] = contradictory_burn["README.md"].replace(
        '"authority_id":"refs/tags/release-truth/v1.0.5/certificate/g0",',
        '"authority_id":"not-an-authority",',
    ).replace(
        '"authority_kind":"protected-certificate-tag",',
        '"authority_kind":"actions-history-retention",',
    )
    assert any(
        finding.marker == "wi010-release-phase-payload-invalid"
        for finding in release_identity.validate_wi010_release_profile(
            contradictory_burn
        )
    )

    extra_key = _profile_files("S2-burn")
    extra_key["README.md"] = extra_key["README.md"].replace(
        '"phase":"S2-burn"', '"phase":"S2-burn","unexpected":true'
    )
    assert any(
        finding.marker == "wi010-release-phase-payload-invalid"
        for finding in release_identity.validate_wi010_release_profile(extra_key)
    )

    repository_root = Path(__file__).resolve().parents[2]
    if os.name != "nt":
        real_git = shutil.which("git")
        assert real_git is not None
        git_wrapper_root = tmp_path / "wi010-git-without-fetch-head"
        git_wrapper_root.mkdir()
        git_wrapper = git_wrapper_root / "git"
        git_wrapper.write_text(
            "#!/bin/sh\n"
            f"real_git={json.dumps(real_git)}\n"
            'if [ "$1" = "fetch" ]; then\n'
            "  shift\n"
            '  exec "$real_git" fetch --no-write-fetch-head "$@"\n'
            "fi\n"
            'exec "$real_git" "$@"\n',
            encoding="utf-8",
        )
        git_wrapper.chmod(0o755)
        original_path = os.environ["PATH"]
        try:
            os.environ["PATH"] = f"{git_wrapper_root}{os.pathsep}{original_path}"
            _checkout_exact_detached_head(
                repository_root,
                tmp_path / "wi010-current-candidate-without-fetch-head",
            )
        finally:
            os.environ["PATH"] = original_path

    candidate_snapshot_root = tmp_path / "wi010-current-candidate-clean"
    candidate_head, candidate_tree = _checkout_exact_detached_head(
        repository_root,
        candidate_snapshot_root,
    )
    assert candidate_head == _run_git(
        repository_root, "rev-parse", "HEAD"
    ).stdout.decode("ascii").strip()
    assert candidate_tree == _run_git(
        repository_root, "rev-parse", "HEAD^{tree}"
    ).stdout.decode("ascii").strip()
    assert (
        verify_constraints._release_docs_consistency_blockers(
            candidate_snapshot_root
        )
        == []
    )

    detached_source_root = tmp_path / "wi010-detached-synthetic-source"
    detached_source_root.mkdir()
    _run_git(detached_source_root, "init", "-q")
    _run_git(detached_source_root, "config", "user.name", "WI010 Test")
    _run_git(detached_source_root, "config", "user.email", "wi010@example.invalid")
    _write(detached_source_root, "identity.txt", "base\n")
    _run_git(detached_source_root, "add", "identity.txt")
    _run_git(detached_source_root, "commit", "-qm", "base")
    detached_base = (
        _run_git(detached_source_root, "rev-parse", "HEAD")
        .stdout.decode("ascii")
        .strip()
    )
    detached_tree = (
        _run_git(detached_source_root, "rev-parse", "HEAD^{tree}")
        .stdout.decode("ascii")
        .strip()
    )
    detached_side = subprocess.run(
        ["git", "commit-tree", detached_tree, "-p", detached_base],
        cwd=detached_source_root,
        input=b"side\n",
        check=True,
        capture_output=True,
    ).stdout.decode("ascii").strip()
    detached_merge = subprocess.run(
        [
            "git",
            "commit-tree",
            detached_tree,
            "-p",
            detached_base,
            "-p",
            detached_side,
        ],
        cwd=detached_source_root,
        input=b"synthetic merge\n",
        check=True,
        capture_output=True,
    ).stdout.decode("ascii").strip()
    _run_git(detached_source_root, "checkout", "-q", "--detach", detached_merge)
    detached_checkout_root = tmp_path / "wi010-detached-synthetic-checkout"
    cloned_merge, cloned_tree = _checkout_exact_detached_head(
        detached_source_root,
        detached_checkout_root,
    )
    assert cloned_merge == detached_merge
    assert cloned_tree == detached_tree
    assert len(
        _run_git(detached_checkout_root, "rev-list", "--parents", "-n", "1", "HEAD")
        .stdout.split()
    ) == 3

    # fetch 必须绑定其实际解析的 source HEAD，不能只证明旧对象作为新提交祖先存在。
    if os.name != "nt":
        drift_source_root = tmp_path / "wi010-source-head-drift"
        drift_source_root.mkdir()
        _run_git(drift_source_root, "init", "-q")
        _run_git(drift_source_root, "config", "user.name", "WI010 Test")
        _run_git(drift_source_root, "config", "user.email", "wi010@example.invalid")
        _write(drift_source_root, "identity.txt", "A\n")
        _run_git(drift_source_root, "add", "identity.txt")
        _run_git(drift_source_root, "commit", "-qm", "A")
        drift_head_a = (
            _run_git(drift_source_root, "rev-parse", "HEAD")
            .stdout.decode("ascii")
            .strip()
        )
        _write(drift_source_root, "identity.txt", "B\n")
        _run_git(drift_source_root, "commit", "-qam", "B")
        drift_head_b = (
            _run_git(drift_source_root, "rev-parse", "HEAD")
            .stdout.decode("ascii")
            .strip()
        )
        _run_git(drift_source_root, "checkout", "-q", "--detach", drift_head_a)
        drift_wrapper_root = tmp_path / "wi010-git-source-head-drift"
        drift_wrapper_root.mkdir()
        drift_wrapper = drift_wrapper_root / "git"
        drift_wrapper.write_text(
            "#!/bin/sh\n"
            f"real_git={json.dumps(real_git)}\n"
            f"source_root={json.dumps(str(drift_source_root))}\n"
            f"drift_head={json.dumps(drift_head_b)}\n"
            'if [ "$1" = "fetch" ]; then\n'
            '  "$real_git" -C "$source_root" checkout -q --detach "$drift_head"\n'
            "  shift\n"
            '  exec "$real_git" fetch --no-write-fetch-head "$@"\n'
            "fi\n"
            'exec "$real_git" "$@"\n',
            encoding="utf-8",
        )
        drift_wrapper.chmod(0o755)
        original_path = os.environ["PATH"]
        try:
            os.environ["PATH"] = f"{drift_wrapper_root}{os.pathsep}{original_path}"
            try:
                _checkout_exact_detached_head(
                    drift_source_root,
                    tmp_path / "wi010-source-head-drift-checkout",
                )
            except AssertionError:
                pass
            else:
                raise AssertionError("source HEAD drift during fetch must fail closed")
        finally:
            os.environ["PATH"] = original_path

    assert (
        _run_git(
            repository_root,
            "hash-object",
            "scripts/validate_public_release_identity.py",
        )
        .stdout.decode("ascii")
        .strip()
        == verify_constraints.WI010_VALIDATOR_BLOB_OID
    )
    validator_bytes = (
        repository_root / "scripts/validate_public_release_identity.py"
    ).read_bytes()
    validator_crlf = validator_bytes.replace(b"\n", b"\r\n")
    assert (
        verify_constraints._wi010_raw_blob_oid(validator_crlf)
        == verify_constraints.WI010_VALIDATOR_BLOB_OID
    )
    assert (
        verify_constraints._wi010_raw_blob_oid(
            validator_crlf.replace(b"PUBLIC_RELEASE_IDENTITY_VALID", b"INVALID", 1)
        )
        != verify_constraints.WI010_VALIDATOR_BLOB_OID
    )

    # 生产变更：seal 必须只读取同一 HEAD Git object snapshot，并锁定 transition。
    seal_root = Path(os.environ.get("PYTEST_TMPDIR", "/tmp")) / (
        "wi010-release-seal-" + os.urandom(8).hex()
    )
    seal_root.mkdir(parents=True)
    production_anchor = release_identity.WI010_FOUNDATION_ANCHOR_OID
    production_main_url = release_identity.WI010_PROTECTED_MAIN_URL
    production_origins = release_identity.WI010_PROTECTED_ORIGIN_URLS
    production_authority_fetch = release_identity._wi010_fetch_public_bytes
    production_attestation_verifier = (
        release_identity._wi010_run_attestation_verifier
    )
    authority_calls: list[str] = []

    def fetch_success_authority(
        url: str,
        *,
        asset: bool = False,
        max_bytes: int = 4 * 1024 * 1024,
    ) -> bytes:
        del asset
        authority_calls.append(url)
        body = SUCCESS_AUTHORITY_RESPONSES.get(url)
        assert body is not None, url
        assert len(body) <= max_bytes
        return body

    def verify_success_attestation(
        artifact_bytes: bytes,
        bundles: list[dict[str, object]],
        source_digest: str,
        run_id: int,
        verifier_source: bytes,
    ) -> tuple[dict[str, object], ...]:
        assert bundles
        assert verifier_source
        return (
            _certificate_attestation_statement(
                source_digest, artifact_bytes, run_id
            ),
        )

    release_identity._wi010_fetch_public_bytes = fetch_success_authority
    release_identity._wi010_run_attestation_verifier = (
        verify_success_attestation
    )
    trusted_origin_root = seal_root.with_name(seal_root.name + "-origin.git")
    assert production_anchor == b"bafc16522098e62021e5bdfcaee40e7739b3a5f7"
    assert (
        b"https://github.com/SinclairPan/Ai_AutoSDLC"
        in release_identity.WI010_PROTECTED_ORIGIN_URLS
    )
    assert (
        b"https://github.example/SinclairPan/Ai_AutoSDLC"
        not in release_identity.WI010_PROTECTED_ORIGIN_URLS
    )
    assert (
        b"https://github.com/SinclairPan/Ai_AutoSDLC-fork"
        not in release_identity.WI010_PROTECTED_ORIGIN_URLS
    )
    assert (
        b"git@github.com:SinclairPan/Ai_AutoSDLC.git"
        not in release_identity.WI010_PROTECTED_ORIGIN_URLS
    )
    assert (
        b"ssh://git@github.com/SinclairPan/Ai_AutoSDLC.git"
        not in release_identity.WI010_PROTECTED_ORIGIN_URLS
    )
    try:
        _init_foundation_repository(seal_root)
        base_commit = _run_git(seal_root, "rev-parse", "HEAD^").stdout.strip()
        release_identity.WI010_FOUNDATION_ANCHOR_OID = base_commit
        initial_snapshot = release_identity.read_release_tree_snapshot(seal_root)
        computed = release_identity.compute_release_tree_seal(initial_snapshot)
        assert len(computed) == 64
        readme = (seal_root / "README.md").read_text(encoding="utf-8")
        _write(seal_root, "README.md", readme.replace("0" * 64, computed))
        _run_git(seal_root, "add", "README.md")
        _run_git(seal_root, "commit", "--amend", "--no-edit", "-q")

        sealed_snapshot = release_identity.read_release_tree_snapshot(seal_root)
        assert release_identity.compute_release_tree_seal(sealed_snapshot) == computed
        assert release_identity.validate_release_tree_seal(seal_root).actual == computed
        foundation_commit = sealed_snapshot.commit_oid
        foundation_tree = sealed_snapshot.tree_oid
        _run_git(seal_root, "init", "--bare", "-q", str(trusted_origin_root))
        trusted_origin_url = trusted_origin_root.as_uri()
        release_identity.WI010_PROTECTED_ORIGIN_URLS = frozenset(
            {trusted_origin_url.encode("utf-8")}
        )
        release_identity.WI010_PROTECTED_MAIN_URL = trusted_origin_url.encode("utf-8")
        _run_git(seal_root, "remote", "add", "origin", trusted_origin_url)
        _run_git(
            seal_root,
            "push",
            "-q",
            "origin",
            f"{base_commit.decode('ascii')}:refs/heads/main",
            (
                f"{foundation_commit.decode('ascii')}:"
                "refs/heads/foundation-candidate"
            ),
        )
        ambient_home = seal_root / "ambient-home"
        ambient_home.mkdir()
        ambient_config = ambient_home / ".gitconfig"
        ambient_config.write_text(
            '[url "file:///definitely-not-the-protected-repository"]\n'
            f"\tinsteadOf = {trusted_origin_url}\n",
            encoding="utf-8",
        )
        transport_overrides = {
            "ALL_PROXY": "http://127.0.0.1:9",
            "GIT_ASKPASS": "false",
            "GIT_CONFIG_GLOBAL": str(ambient_config),
            "GIT_SSH_COMMAND": "false",
            "GIT_SSL_NO_VERIFY": "1",
            "HOME": str(ambient_home),
            "HTTPS_PROXY": "http://127.0.0.1:9",
        }
        previous_transport = {
            name: os.environ.get(name) for name in transport_overrides
        }
        try:
            os.environ.update(transport_overrides)
            assert release_identity._protected_main_oid(seal_root) == base_commit
        finally:
            for name, previous in previous_transport.items():
                if previous is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = previous
        _run_git(
            seal_root,
            "remote",
            "set-url",
            "origin",
            "git@github.com:SinclairPan/Ai_AutoSDLC.git",
        )
        try:
            release_identity._protected_main_oid(seal_root)
        except ValueError as exc:
            assert str(exc) == "WI010 origin does not identify the protected repository"
        else:
            raise AssertionError("SSH origin must not select the protected-main transport")
        _run_git(seal_root, "remote", "set-url", "origin", trusted_origin_url)
        assert release_identity.validate_release_transition(seal_root) == []

        alternate_readme_root = seal_root.with_name(
            seal_root.name + "-alternate-foundation-readme"
        )
        _run_git(seal_root, "clone", "-q", ".", str(alternate_readme_root))
        _run_git(
            alternate_readme_root,
            "remote",
            "set-url",
            "origin",
            trusted_origin_url,
        )
        _run_git(
            alternate_readme_root,
            "switch",
            "--detach",
            base_commit.decode("ascii"),
            "-q",
        )
        foundation_paths = [
            path.decode("utf-8")
            for path in sorted(release_identity.WI010_FOUNDATION_PATHS)
        ]
        _run_git(
            alternate_readme_root,
            "checkout",
            foundation_commit.decode("ascii"),
            "--",
            *foundation_paths,
        )
        alternate_readme = alternate_readme_root / "README.md"
        alternate_readme.write_text(
            alternate_readme.read_text(encoding="utf-8")
            + "\n未授权的 foundation 可见发布结论。\n",
            encoding="utf-8",
        )
        _run_git(alternate_readme_root, "add", "--", *foundation_paths)
        _run_git(alternate_readme_root, "commit", "-qm", "alternate README")
        _seal_head(alternate_readme_root)
        assert any(
            finding.marker == "wi010-release-transition-invalid"
            and "marker-only renderer" in finding.excerpt
            for finding in release_identity.validate_release_transition(
                alternate_readme_root
            )
        )
        shutil.rmtree(alternate_readme_root)

        # 已审 foundation 不能只靠共同父提交识别；否则替代 S0 可污染信任根后继续生成合法 S1。
        _run_git(
            trusted_origin_root,
            "update-ref",
            "refs/heads/main",
            foundation_commit.decode("ascii"),
        )
        alternate_results: list[bool] = []
        for index, (drift_paths, update_validator_pin) in enumerate(
            (
                (
                    ("src/ai_sdlc/core/verify_constraints.py",),
                    False,
                ),
                (("tests/unit/test_public_release_identity.py",), False),
                (
                    ("scripts/validate_public_release_identity.py",),
                    True,
                ),
            )
        ):
            alternate_root = seal_root.with_name(
                seal_root.name + f"-alternate-foundation-{index}"
            )
            _run_git(seal_root, "clone", "-q", ".", str(alternate_root))
            _run_git(
                alternate_root,
                "remote",
                "set-url",
                "origin",
                trusted_origin_url,
            )
            _run_git(
                alternate_root,
                "switch",
                "--detach",
                base_commit.decode("ascii"),
                "-q",
            )
            _run_git(
                alternate_root,
                "checkout",
                foundation_commit.decode("ascii"),
                "--",
                *foundation_paths,
            )
            original_validator_oid = _run_git(
                alternate_root,
                "hash-object",
                "scripts/validate_public_release_identity.py",
            ).stdout.strip()
            for drift_relative in drift_paths:
                alternate_trust_root = alternate_root / drift_relative
                alternate_trust_root.write_text(
                    alternate_trust_root.read_text(encoding="utf-8")
                    + "\n# 未审 foundation 信任根漂移\n",
                    encoding="utf-8",
                )
            if update_validator_pin:
                replacement_validator_oid = _run_git(
                    alternate_root,
                    "hash-object",
                    "scripts/validate_public_release_identity.py",
                ).stdout.strip()
                alternate_constraints = (
                    alternate_root / "src/ai_sdlc/core/verify_constraints.py"
                )
                alternate_constraints.write_text(
                    alternate_constraints.read_text(encoding="utf-8").replace(
                        original_validator_oid.decode("ascii"),
                        replacement_validator_oid.decode("ascii"),
                        1,
                    ),
                    encoding="utf-8",
                )
            _run_git(alternate_root, "add", "--", *foundation_paths)
            _run_git(alternate_root, "commit", "-qm", "alternate foundation")
            _seal_head(alternate_root)
            _commit_profile_transition(alternate_root, "S1")
            alternate_results.append(
                any(
                    finding.marker == "wi010-release-transition-invalid"
                    for finding in release_identity.validate_release_transition(
                        alternate_root
                    )
                )
            )
            shutil.rmtree(alternate_root)
        assert alternate_results == [True, True, True]
        _run_git(
            trusted_origin_root,
            "update-ref",
            "refs/heads/main",
            base_commit.decode("ascii"),
        )

        release_identity.WI010_FOUNDATION_ANCHOR_OID = b"0" * 40
        assert any(
            finding.marker == "wi010-release-transition-invalid"
            for finding in release_identity.validate_release_transition(seal_root)
        )
        release_identity.WI010_FOUNDATION_ANCHOR_OID = base_commit
        assert release_identity._parse_commit_parent_oids(
            b"tree " + foundation_tree + b"\nparent " + base_commit + b"\nauthor x\n\nmsg\n"
        ) == (base_commit,)
        for invalid_parent_shape in (
            b"tree " + foundation_tree + b"\nauthor x\n\nmsg\n",
            (
                b"tree "
                + foundation_tree
                + b"\nparent "
                + base_commit
                + b"\nparent "
                + foundation_commit
                + b"\nparent "
                + base_commit
                + b"\nauthor x\n\nmsg\n"
            ),
        ):
            try:
                release_identity._parse_commit_parent_oids(invalid_parent_shape)
            except ValueError:
                pass
            else:
                raise AssertionError("zero/three-parent transition must fail closed")

        synthetic_commit = _run_git(
            seal_root,
            "commit-tree",
            foundation_tree.decode("ascii"),
            "-p",
            base_commit.decode("ascii"),
            "-p",
            foundation_commit.decode("ascii"),
            "-m",
            "synthetic merge",
        ).stdout.strip()
        _run_git(
            seal_root,
            "push",
            "-q",
            "origin",
            (
                f"{synthetic_commit.decode('ascii')}:"
                "refs/heads/synthetic-candidate"
            ),
        )
        _run_git(
            seal_root,
            "update-ref",
            "refs/heads/foundation-shallow-test",
            foundation_commit.decode("ascii"),
        )
        _run_git(
            seal_root,
            "update-ref",
            "refs/heads/synthetic-shallow-test",
            synthetic_commit.decode("ascii"),
        )
        for branch in ("foundation-shallow-test", "synthetic-shallow-test"):
            shallow_root = seal_root / ("clone-" + branch)
            _run_git(
                seal_root,
                "clone",
                "-q",
                "--depth=1",
                "--branch",
                branch,
                seal_root.as_uri(),
                str(shallow_root),
            )
            _run_git(
                shallow_root,
                "remote",
                "set-url",
                "origin",
                trusted_origin_url,
            )
            before = (
                _run_git(shallow_root, "rev-parse", "HEAD").stdout,
                _run_git(shallow_root, "rev-parse", "HEAD^{tree}").stdout,
                _run_git(shallow_root, "status", "--porcelain").stdout,
            )
            assert _run_git(
                shallow_root, "rev-parse", "--is-shallow-repository"
            ).stdout.strip() == b"true"
            assert release_identity.validate_release_transition(shallow_root) == []
            after = (
                _run_git(shallow_root, "rev-parse", "HEAD").stdout,
                _run_git(shallow_root, "rev-parse", "HEAD^{tree}").stdout,
                _run_git(shallow_root, "status", "--porcelain").stdout,
            )
            assert after == before

        missing_origin_root = seal_root / "clone-missing-origin"
        _run_git(
            seal_root,
            "clone",
            "-q",
            "--depth=1",
            "--branch",
            "foundation-shallow-test",
            seal_root.as_uri(),
            str(missing_origin_root),
        )
        _run_git(missing_origin_root, "remote", "remove", "origin")
        assert any(
            finding.marker == "wi010-release-transition-invalid"
            for finding in release_identity.validate_release_transition(
                missing_origin_root
            )
        )

        # 末次提交即使满足 closed renderer，也不能掩盖更早的 phase-preserving 漂移。
        multi_commit_root = seal_root / "clone-multi-commit-transition"
        _run_git(seal_root, "clone", "-q", ".", str(multi_commit_root))
        _write(multi_commit_root, "unchanged.txt", "hidden intermediate drift\n")
        _run_git(multi_commit_root, "add", "unchanged.txt")
        _run_git(multi_commit_root, "commit", "-qm", "hidden intermediate drift")
        _seal_head(multi_commit_root)
        _commit_profile_transition(multi_commit_root, "S1")
        assert any(
            finding.marker == "wi010-release-transition-invalid"
            for finding in release_identity.validate_release_transition(
                multi_commit_root
            )
        )

        _run_git(
            trusted_origin_root,
            "update-ref",
            "refs/heads/main",
            foundation_commit.decode("ascii"),
        )
        s1_commit = _commit_profile_transition(seal_root, "S1")
        assert release_identity.validate_release_tree_seal(seal_root).findings == ()
        assert release_identity.validate_release_transition(seal_root) == []
        s1_snapshot = release_identity.read_release_tree_snapshot(seal_root)
        s1_files = {
            entry.path.decode("utf-8"): entry.blob.decode("utf-8")
            for entry in s1_snapshot.entries
        }
        assert validate_required_surfaces(s1_files) == []

        s1_readme_path = seal_root / "README.md"
        s1_readme = s1_readme_path.read_text(encoding="utf-8")
        historical_snapshot = (
            "该 README 与其生成的 wheel METADATA 只记录该制品构建时点的 S1 历史快照"
        )
        assert historical_snapshot in s1_readme
        assert "在该制品构建时点，`last published version is v1.0.2`" in s1_readme
        assert "当前公开可安装的离线版本仍是 `v1.0.2`" not in s1_readme
        assert "PR3 closure 后的当前发布权威" in s1_readme

        # 即使重算合法 seal，也不能把不可变 wheel METADATA 改回无时间边界的当前结论。
        s1_readme_path.write_text(
            s1_readme.replace(
                historical_snapshot,
                "该 README 与其生成的 wheel METADATA 记录当前 S1 状态",
                1,
            ),
            encoding="utf-8",
        )
        _run_git(seal_root, "add", "README.md")
        _run_git(seal_root, "commit", "--amend", "--no-edit", "-q")
        _seal_head(seal_root)
        assert any(
            finding.marker == "wi010-release-transition-invalid"
            for finding in release_identity.validate_release_transition(seal_root)
        )
        _run_git(seal_root, "switch", "--detach", s1_commit.decode("ascii"), "-q")

        _run_git(
            seal_root,
            "push",
            "-q",
            "origin",
            f"{s1_commit.decode('ascii')}:refs/heads/s1-candidate",
        )
        _run_git(
            trusted_origin_root,
            "update-ref",
            "refs/heads/main",
            s1_commit.decode("ascii"),
        )

        calls_before_success = len(authority_calls)
        _commit_profile_transition(seal_root, "S2-success")
        assert release_identity.validate_release_tree_seal(seal_root).findings == ()
        assert release_identity.validate_release_transition(seal_root) == []
        assert len(authority_calls) > calls_before_success
        software_release_url = (
            release_identity.WI010_GITHUB_API_ROOT + "/releases/11"
        )
        valid_software_release = SUCCESS_AUTHORITY_RESPONSES[software_release_url]
        mutable_release = json.loads(valid_software_release)
        mutable_release["immutable"] = False
        SUCCESS_AUTHORITY_RESPONSES[software_release_url] = json.dumps(
            mutable_release, sort_keys=True
        ).encode("utf-8")
        assert any(
            finding.marker == "wi010-release-transition-invalid"
            and "software Release authority differs" in finding.excerpt
            for finding in release_identity.validate_release_transition(seal_root)
        )
        SUCCESS_AUTHORITY_RESPONSES[software_release_url] = valid_software_release
        assert release_identity.validate_release_transition(seal_root) == []
        success_snapshot = release_identity.read_release_tree_snapshot(seal_root)
        expected_archive = (
            release_identity.WI010_ARCHIVE_PREFIX
            + s1_commit.decode("ascii")
            + ".zip"
        )
        success_shell_installer = (
            seal_root / "packaging/install_online.sh"
        ).read_text(encoding="utf-8")
        assert "ai-sdlc==1.0.2" not in success_shell_installer
        assert f"AI_SDLC_PACKAGE_SPEC={expected_archive}" in success_shell_installer
        success_files = {
            entry.path.decode("utf-8"): entry.blob.decode("utf-8")
            for entry in success_snapshot.entries
        }
        assert release_identity.validate_wi010_release_profile(success_files) == []
        assert validate_required_surfaces(success_files) == []
        success_required_markers = {
            "README.md": "v1.0.5 是当前普通用户发布权威",
            "USER_GUIDE.zh-CN.md": "v1.0.5 是当前普通用户发布权威",
            "docs/product-contract.md": "`current published version is v1.0.5`",
            "docs/pull-request-checklist.zh.md": "普通用户安装权威已迁移到 v1.0.5",
            "packaging/offline/README.md": "v1.0.5 是当前普通用户离线发布权威",
            "packaging/offline/RELEASE_CHECKLIST.md": "v1.0.5 是当前发布权威",
        }
        for relative, marker in success_required_markers.items():
            assert success_files[relative].count(marker) == 1
            missing_marker = dict(success_files)
            missing_marker[relative] = missing_marker[relative].replace(marker, "", 1)
            assert any(
                finding.path == relative
                and finding.marker == "required-identity-marker-missing"
                and finding.excerpt == marker
                for finding in validate_required_surfaces(missing_marker)
            )
        for relative in RELEASE_SURFACE_PATHS:
            text = (seal_root / relative).read_text(encoding="utf-8")
            assert text.splitlines().count(
                release_identity.WI010_CANONICAL_ONLINE_SPEC_PREFIX
                + expected_archive
            ) == 1
            assert "release-enabled / outcome-pending-closure" not in text
            assert (
                "普通用户和手工路径仍禁止上传、替换、发布、下载、安装或 rerun v1.0.5"
                not in text
            )
        for relative in (
            ".github/workflows/posix-user-guide-e2e.yml",
            ".github/workflows/windows-user-guide-e2e.yml",
        ):
            workflow_text = (seal_root / relative).read_text(encoding="utf-8")
            assert yaml.safe_load(workflow_text)["jobs"]
            assert expected_archive in workflow_text
        posix_workflow = (
            seal_root / ".github/workflows/posix-user-guide-e2e.yml"
        ).read_text(encoding="utf-8")
        windows_workflow = (
            seal_root / ".github/workflows/windows-user-guide-e2e.yml"
        ).read_text(encoding="utf-8")
        assert 'default: "v1.0.5"' in posix_workflow
        assert 'default: "v1.0.2"' not in posix_workflow
        assert posix_workflow.count('PIP_NO_CACHE_DIR: "1"') == 1
        assert posix_workflow.count("s2-success-qualification-identity.json") == 1
        posix_jobs = yaml.safe_load(posix_workflow)["jobs"]
        qualification_steps = [
            step
            for job in posix_jobs.values()
            for step in job.get("steps", ())
            if step.get("name")
            == "Verify canonical online installer from a fresh POSIX venv"
        ]
        assert len(qualification_steps) == 1
        qualification_prefix = qualification_steps[0]["run"].split(
            "PYTHON=python3.11 bash packaging/install_online.sh", 1
        )[0]
        fake_bin = tmp_path / "wi010-preinstalled-cli-bin"
        fake_bin.mkdir()
        fake_cli = fake_bin / "ai-sdlc"
        fake_cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_cli.chmod(0o755)
        preinstalled_result = subprocess.run(
            ["bash", "-c", qualification_prefix],
            cwd=seal_root,
            env={
                **os.environ,
                "ASSET_OS": "linux",
                "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                "RUNNER_TEMP": str(tmp_path / "wi010-preinstalled-runner"),
            },
            check=False,
            capture_output=True,
            text=True,
        )
        assert preinstalled_result.returncode != 0
        assert "runner already exposes ai-sdlc before qualification" in (
            preinstalled_result.stderr
        )
        assert "default: v1.0.5" in windows_workflow
        assert 'else { "v1.0.5" }' in windows_workflow
        assert "default: v1.0.2" not in windows_workflow
        assert 'else { "v1.0.2" }' not in windows_workflow
        assert windows_workflow.count('$env:PIP_NO_CACHE_DIR = "1"') == 1
        assert windows_workflow.count("s2-success-qualification-identity.json") == 1
        identity_fields = (
            "workflow_sha",
            "run_id",
            "run_attempt",
            "runner_os",
            "python",
            "pr2_sha",
            "reviewed_pr3_head_sha",
            "reviewed_pr3_candidate_tree",
        )
        for field in identity_fields:
            assert posix_workflow.count(f'"{field}"') == 1
            assert windows_workflow.count(f"{field} =") == 1
        persisted_posix_qualification_fields = (
            "actual_package_spec",
            "direct_url",
            "metadata_version",
            "cli_version",
        )
        for field in persisted_posix_qualification_fields:
            assert posix_workflow.count(f'"{field}"') == 1
        assert '"actual_package_spec": actual_package_spec' in posix_workflow
        assert '"direct_url": direct_url["url"]' in posix_workflow
        assert '"metadata_version": distribution.version' in posix_workflow
        assert '"cli_version": cli_version' in posix_workflow
        assert (
            'assert actual_package_spec == os.environ["EXPECTED_ARCHIVE"]'
            in posix_workflow
        )
        assert 'assert cli_version == "1.0.5"' in posix_workflow
        expected_pr2_sha = s1_commit.decode("ascii")
        assert f"PR2_SHA: {expected_pr2_sha}" in posix_workflow
        assert f'pr2_sha = "{expected_pr2_sha}"' in windows_workflow
        assert "importlib.metadata.version('ai-sdlc')" in windows_workflow
        assert '$metadataVersion.Trim() -ne "1.0.5"' in windows_workflow
        clean_online_workflow = windows_workflow.split(
            "clean-online-interactive-user-journey:", 1
        )[1]
        assert '$sourceKind = "protected-main"' in clean_online_workflow
        assert (
            'git ls-remote https://github.com/SinclairPan/Ai_AutoSDLC.git "refs/heads/main"'
            in clean_online_workflow
        )
        assert '"refs/tags/$env:RELEASE_TAG"' not in clean_online_workflow
        assert "-PackageSpec" not in (
            seal_root / ".github/workflows/windows-user-guide-e2e.yml"
        ).read_text(encoding="utf-8")
        integration_contract = (
            seal_root / "tests/integration/test_github_workflows.py"
        ).read_text(encoding="utf-8")
        assert 'assert "default: v1.0.5" in workflow' in integration_contract
        assert "assert 'default: \"v1.0.5\"' in workflow" in integration_contract
        assert 'else { "v1.0.5" }' in integration_contract
        assert f'assert \'$expectedArchive = "{expected_archive}"\' in workflow' in (
            integration_contract
        )
        assert 'assert "-PackageSpec" not in workflow' in integration_contract
        assert 'assert "$directUrl.url -ne $expectedArchive" in workflow' in (
            integration_contract
        )
        assert 'assert \'PIP_NO_CACHE_DIR: "1"\' in workflow' in integration_contract
        assert (
            'assert "command -v ai-sdlc >/dev/null 2>&1" in workflow'
            in integration_contract
        )
        assert (
            'assert "runner already exposes ai-sdlc before qualification" in workflow'
            in integration_contract
        )
        assert 'assert \'$env:PIP_NO_CACHE_DIR = "1"\' in workflow' in (
            integration_contract
        )
        assert integration_contract.count(
            'assert "s2-success-qualification-identity.json" in workflow'
        ) == 2
        assert integration_contract.count(
            'assert "reviewed_pr3_candidate_tree" in workflow'
        ) == 2
        for field in persisted_posix_qualification_fields:
            assert (
                f'assert \'"{field}"\' in workflow'
                in integration_contract
            )
        assert (
            'assert \'"actual_package_spec": actual_package_spec\' in workflow'
            in integration_contract
        )
        assert (
            'assert \'"direct_url": direct_url["url"]\' in workflow'
            in integration_contract
        )
        assert (
            'assert \'"metadata_version": distribution.version\' in workflow'
            in integration_contract
        )
        assert (
            'assert \'"cli_version": cli_version\' in workflow'
            in integration_contract
        )
        assert 'assert "importlib.metadata.version(\'ai-sdlc\')" in workflow' in (
            integration_contract
        )
        assert "git+https://github.com/$sourceRepository.git@$remoteSha" not in (
            integration_contract
        )
        assert "$directUrl.vcs_info.requested_revision" not in integration_contract
        assert (
            "test_windows_clean_user_e2e_resolves_protected_main_installer_and_canonical_archive"
            in integration_contract
        )
        future_nodes = (
            "tests/integration/test_github_workflows.py::test_windows_user_guide_e2e_replays_existing_project_install_path",
            "tests/integration/test_github_workflows.py::test_posix_user_guide_e2e_replays_published_guide_commands",
            "tests/integration/test_github_workflows.py::test_windows_clean_user_e2e_uses_remote_install_and_real_interactive_init",
            "tests/integration/test_github_workflows.py::test_windows_clean_user_e2e_resolves_protected_main_installer_and_canonical_archive",
            "tests/integration/test_github_workflows.py::test_windows_clean_user_e2e_uses_pull_request_head_installer_on_pr_runs",
            "tests/integration/test_offline_bundle_scripts.py::test_user_guide_documents_published_assets_and_two_new_user_paths",
            "tests/unit/test_release_identity.py",
        )
        future_result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *future_nodes],
            cwd=seal_root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        assert future_result.returncode == 0, future_result.stdout
        for relative in (
            "tests/integration/test_offline_bundle_scripts.py",
            "tests/integration/test_user_guide_contract.py",
            "tests/unit/test_release_identity.py",
        ):
            ast.parse((seal_root / relative).read_text(encoding="utf-8"))
        assert success_snapshot.phase is release_identity.ReleasePhase.S2_SUCCESS

        conflict_path = seal_root / "USER_GUIDE.zh-CN.md"
        conflict_path.write_text(
            conflict_path.read_text(encoding="utf-8")
            + "\npython -m pip install ai-sdlc==1.0.5  # 冲突的当前在线入口\n",
            encoding="utf-8",
        )
        _run_git(seal_root, "add", "USER_GUIDE.zh-CN.md")
        _run_git(seal_root, "commit", "--amend", "--no-edit", "-q")
        _seal_head(seal_root)
        assert any(
            finding.marker == "wi010-release-transition-invalid"
            for finding in release_identity.validate_release_transition(seal_root)
        )

        _run_git(seal_root, "switch", "--detach", s1_commit.decode("ascii"), "-q")
        calls_before_burn = len(authority_calls)
        _commit_profile_transition(seal_root, "S2-burn")
        burn_run_url = (
            release_identity.WI010_GITHUB_API_ROOT
            + "/actions/runs/12/attempts/1"
        )
        burn_jobs_url = burn_run_url + "/jobs?per_page=100"
        burn_run = json.loads(SUCCESS_AUTHORITY_RESPONSES[burn_run_url])
        burn_run["conclusion"] = "failure"
        burn_jobs = json.loads(SUCCESS_AUTHORITY_RESPONSES[burn_jobs_url])
        burn_jobs["jobs"][-1]["conclusion"] = "failure"
        SUCCESS_AUTHORITY_RESPONSES[burn_run_url] = json.dumps(burn_run).encode(
            "utf-8"
        )
        SUCCESS_AUTHORITY_RESPONSES[burn_jobs_url] = json.dumps(burn_jobs).encode(
            "utf-8"
        )
        assert release_identity.validate_release_tree_seal(seal_root).findings == ()
        assert release_identity.validate_release_transition(seal_root) == []
        assert len(authority_calls) == calls_before_burn + 6
        burn_snapshot = release_identity.read_release_tree_snapshot(seal_root)
        burn_files = {
            entry.path.decode("utf-8"): entry.blob.decode("utf-8")
            for entry in burn_snapshot.entries
        }
        assert validate_required_surfaces(burn_files) == []
        burn_required_markers = {
            "README.md": "无论远端是否残留公开或未公开对象，v1.0.5 均不具备当前发布权威",
            "USER_GUIDE.zh-CN.md": "无论远端是否残留公开或未公开对象，v1.0.5 均不具备当前发布权威",
            "docs/product-contract.md": "## 1.0.5 永久终止真值（terminal-generation-burn / non-authoritative）",
            "docs/pull-request-checklist.zh.md": "WorkItem 010 generation-0 已永久烧毁",
            "packaging/offline/README.md": "无论远端是否残留公开或未公开对象，v1.0.5 均不具备当前发布权威",
            "packaging/offline/RELEASE_CHECKLIST.md": "无论远端是否残留公开或未公开对象，v1.0.5 均不具备当前发布权威",
        }
        for relative, marker in burn_required_markers.items():
            for forbidden_publication_claim in (
                "v1.0.5 未发布",
                "last published version is v1.0.2",
                "当前公开可安装",
            ):
                assert forbidden_publication_claim not in burn_files[relative]
            assert burn_files[relative].count(marker) == 1
            missing_marker = dict(burn_files)
            missing_marker[relative] = missing_marker[relative].replace(marker, "", 1)
            assert any(
                finding.path == relative
                and finding.marker == "required-identity-marker-missing"
                and finding.excerpt == marker
                for finding in validate_required_surfaces(missing_marker)
            )
        assert (
            burn_files["README.md"].count(
                "`v1.0.2` 仍是普通用户当前唯一受认可的发布与安装权威"
            )
            == 1
        )

        _run_git(
            seal_root,
            "switch",
            "--detach",
            foundation_commit.decode("ascii"),
            "-q",
        )

        _write(seal_root, "unchanged.txt", "dirty worktree\n")
        assert release_identity.compute_release_tree_seal(
            release_identity.read_release_tree_snapshot(seal_root)
        ) == computed
        _run_git(seal_root, "restore", "unchanged.txt")

        _write(seal_root, "unchanged.txt", "committed drift\n")
        _run_git(seal_root, "add", "unchanged.txt")
        _run_git(seal_root, "commit", "-qm", "extra path drift")
        assert release_identity.compute_release_tree_seal(
            release_identity.read_release_tree_snapshot(seal_root)
        ) != computed
        assert release_identity.validate_release_transition(seal_root)

        _run_git(seal_root, "switch", "--detach", "HEAD^", "-q")
        _run_git(
            seal_root,
            "update-index",
            "--chmod=+x",
            "src/ai_sdlc/core/verify_constraints.py",
        )
        _run_git(seal_root, "commit", "-qm", "mode drift")
        assert release_identity.validate_release_tree_seal(seal_root).findings
    finally:
        release_identity.WI010_FOUNDATION_ANCHOR_OID = production_anchor
        release_identity.WI010_PROTECTED_MAIN_URL = production_main_url
        release_identity.WI010_PROTECTED_ORIGIN_URLS = production_origins
        release_identity._wi010_fetch_public_bytes = production_authority_fetch
        release_identity._wi010_run_attestation_verifier = (
            production_attestation_verifier
        )
        shutil.rmtree(seal_root)
        shutil.rmtree(trusted_origin_root, ignore_errors=True)


def test_scan_allows_current_release_and_dependency_versions(tmp_path: Path) -> None:
    files = {
        "README.md": f"{CURRENT_REPOSITORY_URL}\nAI-SDLC {CURRENT_VERSION}",
        "uv.lock": 'name = "example"\nversion = "3.4.2"',
        "managed/frontend/package-lock.json": '{"version":"3.3.0"}',
        "src/provider.py": 'release_ref = "refs/tags/rust-v0.138.0"',
        "tests/test_attestation.py": (
            'media_type = "application/vnd.dev.sigstore.bundle.v0.3+json"'
        ),
    }

    assert scan_paths(tmp_path, files) == []


def test_public_identity_does_not_require_release_history_documents() -> None:
    public_paths = {*PUBLIC_DOC_PATHS, *REQUIRED_SURFACES}

    assert not any(path.startswith("docs/releases/") for path in public_paths)
    assert not any("prd" in path.casefold() for path in public_paths)


def test_user_guide_identity_requires_new_user_release_paths() -> None:
    markers = REQUIRED_SURFACES["USER_GUIDE.zh-CN.md"]

    assert "## 第一章：全新用户 + 全新空项目" in markers
    assert "## 第二章：全新用户 + 已有项目" in markers
    assert "ai-sdlc init ." in markers
    assert "ai-sdlc adopt ." in markers
    assert STABLE_SOURCE_CLONE not in markers
    assert PUBLISHED_VERSION == "1.0.2"
    assert CURRENT_VERSION == "1.0.5"
    assert any("releases/download/v1.0.2/" in marker for marker in markers)
    assert not any("releases/download/v1.0.4/" in marker for marker in markers)
    assert not any("releases/download/v1.0.5/" in marker for marker in markers)
    assert "v1.0.4 未发布" in markers
    assert "v1.0.5 release candidate / not published / prepared-disabled" in markers
