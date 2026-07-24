"""仅把受 GitHub provenance 约束的证据包导入激活评估。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from ai_sdlc.core.stage_review.activation_artifact_codec import (
    LegacyActivationArtifactUnavailableError,
    decode_activation_evidence_package,
)
from ai_sdlc.core.stage_review.activation_fence import (
    activation_safety_mutation_fence,
)
from ai_sdlc.core.stage_review.activation_models import StageGateActivationPolicy
from ai_sdlc.core.stage_review.activation_source_models import (
    ActivationEvidenceImportReceipt,
    ActivationEvidencePackage,
    ActivationIsolationSourceRecord,
    ActivationProbeSourceRecord,
)
from ai_sdlc.core.stage_review.artifacts import (
    create_json_exclusive,
    read_json_object,
    resolve_canonical_shared_state,
    resolve_repository_project_id,
)

ACTIVATION_EVIDENCE_INBOX = Path(".ai-sdlc/policies/activation-evidence")


def import_activation_evidence_inbox(
    root: Path,
    *,
    policy: StageGateActivationPolicy,
) -> tuple[ActivationEvidenceImportReceipt, ...]:
    inbox = root.resolve() / ACTIVATION_EVIDENCE_INBOX
    artifacts = _compatible_inbox_artifacts(
        root,
        tuple(sorted(inbox.glob("*.package.json"))),
    )
    if not artifacts:
        _download_latest_activation_evidence(root.resolve(), inbox, policy)
        artifacts = _compatible_inbox_artifacts(
            root,
            tuple(sorted(inbox.glob("*.package.json"))),
        )
    if len(artifacts) > 16:
        raise ValueError("activation evidence inbox budget exceeded")
    receipts = []
    for artifact in artifacts:
        bundle_name = artifact.name.removesuffix(".package.json") + ".bundle.jsonl"
        bundle = inbox / bundle_name
        if not bundle.is_file():
            raise ValueError("activation evidence attestation bundle is missing")
        receipts.append(
            ingest_activation_evidence_package(
                root,
                artifact,
                bundle,
                policy=policy,
            )
        )
    return tuple(receipts)


def _compatible_inbox_artifacts(
    root: Path,
    artifacts: tuple[Path, ...],
) -> tuple[Path, ...]:
    compatible = []
    for artifact in artifacts:
        payload = read_json_object(artifact)
        try:
            decode_activation_evidence_package(payload)
        except LegacyActivationArtifactUnavailableError as error:
            _quarantine_legacy_package(root, artifact, payload, error)
            continue
        compatible.append(artifact)
    return tuple(compatible)


def _quarantine_legacy_package(
    root: Path,
    artifact: Path,
    payload: object,
    error: LegacyActivationArtifactUnavailableError,
) -> None:
    project_id = resolve_repository_project_id(root)
    shared = resolve_canonical_shared_state(root, project_id)
    package = payload if isinstance(payload, dict) else {}
    source_digest = str(package.get("package_digest", ""))
    identity = hashlib.sha256(
        f"{source_digest}:legacy-package-rebuild-required".encode()
    ).hexdigest()
    with activation_safety_mutation_fence(root, project_id):
        create_json_exclusive(
            shared / "activation/compatibility-quarantine" / f"{identity}.json",
            {
                "schema_version": "activation-compatibility-quarantine.v1",
                "artifact_kind": "activation-compatibility-quarantine",
                "project_id": project_id,
                "source_artifact": artifact.name,
                "source_digest": source_digest,
                "reason_code": "legacy-package-rebuild-required",
                "detail": str(error),
            },
        )


def _download_latest_activation_evidence(
    root: Path,
    inbox: Path,
    policy: StageGateActivationPolicy,
) -> None:
    if shutil.which("gh") is None:
        return
    try:
        repository = _origin_repository(root)
    except ValueError:
        return
    listed = _run_gh(
        (
            "run",
            "list",
            "--repo",
            repository,
            "--workflow",
            "activation-evidence.yml",
            "--branch",
            "main",
            "--status",
            "success",
            "--limit",
            "20",
            "--json",
            "databaseId,headSha,conclusion",
        ),
        cwd=root,
        timeout=30,
    )
    if listed.returncode != 0:
        return
    try:
        runs = json.loads(listed.stdout)
    except json.JSONDecodeError:
        return
    if not isinstance(runs, list):
        return
    for run in runs:
        identity = _eligible_remote_run(root, run)
        if identity is None:
            continue
        run_id, tested_commit = identity
        with tempfile.TemporaryDirectory(prefix="ai-sdlc-activation-evidence-") as raw:
            temporary = Path(raw)
            downloaded = _run_gh(
                (
                    "run",
                    "download",
                    str(run_id),
                    "--repo",
                    repository,
                    "--name",
                    f"activation-evidence-{tested_commit}",
                    "--dir",
                    str(temporary),
                ),
                cwd=root,
                timeout=60,
            )
            if downloaded.returncode != 0:
                continue
            artifact = temporary / "activation-evidence-package.json"
            if not artifact.is_file():
                raise ValueError("downloaded activation evidence package is missing")
            package = decode_activation_evidence_package(read_json_object(artifact))
            if package.tested_commit != tested_commit:
                raise ValueError("activation evidence workflow run identity mismatch")
            _verify_package_scope(root, package)
            _verify_package_trust(package, policy)
            attestation = _run_gh(
                (
                    "attestation",
                    "download",
                    str(artifact),
                    "--repo",
                    repository,
                    "--predicate-type",
                    policy.evidence_predicate_type,
                ),
                cwd=temporary,
                timeout=60,
            )
            if attestation.returncode != 0:
                continue
            bundles = tuple(sorted(temporary.glob("*.jsonl")))
            if len(bundles) != 1:
                raise ValueError("downloaded activation attestation bundle is ambiguous")
            _install_remote_evidence(
                inbox,
                tested_commit=tested_commit,
                artifact=artifact,
                bundle=bundles[0],
            )
            return


def _eligible_remote_run(
    root: Path,
    payload: object,
) -> tuple[int, str] | None:
    if not isinstance(payload, dict) or payload.get("conclusion") != "success":
        return None
    run_id = payload.get("databaseId")
    tested_commit = payload.get("headSha")
    if (
        not isinstance(run_id, int)
        or run_id < 1
        or not isinstance(tested_commit, str)
        or re.fullmatch(r"[0-9a-f]{40}", tested_commit) is None
    ):
        return None
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", tested_commit, "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    return (run_id, tested_commit) if ancestry.returncode == 0 else None


def _install_remote_evidence(
    inbox: Path,
    *,
    tested_commit: str,
    artifact: Path,
    bundle: Path,
) -> None:
    inbox.mkdir(parents=True, exist_ok=True)
    # 包文件最后发布，避免并发读取观察到只有包、没有证明链的中间状态。
    _copy_exclusive_or_match(bundle, inbox / f"{tested_commit}.bundle.jsonl")
    _copy_exclusive_or_match(artifact, inbox / f"{tested_commit}.package.json")


def _copy_exclusive_or_match(source: Path, target: Path) -> None:
    digest = _file_digest(source)
    if target.is_file():
        if _file_digest(target) != digest:
            raise ValueError("activation evidence inbox artifact diverged")
        return
    temporary = target.with_name(
        f".{target.name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
    )
    try:
        temporary.write_bytes(source.read_bytes())
        os.link(temporary, target)
    except FileExistsError:
        if _file_digest(target) != digest:
            raise ValueError("activation evidence inbox artifact diverged") from None
    finally:
        temporary.unlink(missing_ok=True)


def _run_gh(
    arguments: tuple[str, ...],
    *,
    cwd: Path,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["GH_PROMPT_DISABLED"] = "1"
    command = ("gh", *arguments)
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(command, 124, "", str(exc))


def ingest_activation_evidence_package(
    root: Path,
    artifact_path: Path,
    bundle_path: Path,
    *,
    policy: StageGateActivationPolicy,
) -> ActivationEvidenceImportReceipt:
    repository = root.resolve()
    package = ActivationEvidencePackage.model_validate(
        read_json_object(artifact_path)
    )
    trusted_policy = StageGateActivationPolicy.model_validate(
        policy.model_dump(mode="json")
    )
    _verify_package_scope(repository, package)
    _verify_package_trust(package, trusted_policy)
    verification = _verify_github_attestation(
        artifact_path,
        bundle_path,
        package,
        trusted_policy,
    )
    shared = resolve_canonical_shared_state(repository, package.project_id)
    artifact_digest = _file_digest(artifact_path)
    bundle_digest = _file_digest(bundle_path)
    stored_artifact = _store_source(shared, artifact_path, artifact_digest, ".json")
    stored_bundle = _store_source(shared, bundle_path, bundle_digest, ".jsonl")
    receipt = ActivationEvidenceImportReceipt(
        project_id=package.project_id,
        repository=package.repository,
        tested_commit=package.tested_commit,
        signer_workflow=package.signer_workflow,
        evidence_purpose=package.evidence_purpose,
        activation_policy_digest=trusted_policy.policy_digest,
        package_digest=package.package_digest,
        artifact_path=stored_artifact,
        artifact_digest=artifact_digest,
        bundle_path=stored_bundle,
        bundle_digest=bundle_digest,
        verification_output_digest=verification,
    )
    _persist_import(shared, receipt, package)
    return receipt


def verify_activation_source_records(
    root: Path,
    isolation: tuple[ActivationIsolationSourceRecord, ...],
    probes: ActivationProbeSourceRecord,
    *,
    policy: StageGateActivationPolicy,
) -> None:
    receipt_digests = {
        probes.import_receipt_digest,
        *(item.import_receipt_digest for item in isolation),
    }
    verified = {
        digest: _verify_import_receipt(root, digest, policy)
        for digest in receipt_digests
    }
    for record in isolation:
        package = verified[record.import_receipt_digest]
        if record.evidence not in package.isolation_matrix:
            raise ValueError("activation isolation source is not in attested package")
    package = verified[probes.import_receipt_digest]
    if probes.evidence != package.probes:
        raise ValueError("activation probe source is not in attested package")
    if not set(package.source_artifact_digests).issubset(
        probes.source_artifact_digests
    ):
        raise ValueError("activation probe artifacts are not in attested package")


def _verify_import_receipt(
    root: Path,
    digest: str,
    policy: StageGateActivationPolicy,
) -> ActivationEvidencePackage:
    project_id = resolve_repository_project_id(root)
    shared = resolve_canonical_shared_state(root, project_id)
    path = shared / "activation/evidence-imports/receipts" / f"{_name(digest)}.json"
    receipt = ActivationEvidenceImportReceipt.model_validate(read_json_object(path))
    if (
        receipt.receipt_digest != digest
        or receipt.project_id != project_id
        or receipt.activation_policy_digest != policy.policy_digest
    ):
        raise ValueError("activation evidence import receipt identity mismatch")
    artifact = _shared_path(shared, receipt.artifact_path)
    bundle = _shared_path(shared, receipt.bundle_path)
    if _file_digest(artifact) != receipt.artifact_digest:
        raise ValueError("activation evidence imported artifact digest mismatch")
    if _file_digest(bundle) != receipt.bundle_digest:
        raise ValueError("activation evidence attestation bundle digest mismatch")
    package = decode_activation_evidence_package(read_json_object(artifact))
    _verify_receipt_package(receipt, package)
    _verify_package_scope(root.resolve(), package)
    _verify_package_trust(package, policy)
    _verify_github_attestation(artifact, bundle, package, policy)
    return package


def _verify_package_scope(root: Path, package: ActivationEvidencePackage) -> None:
    project_id = resolve_repository_project_id(root)
    if package.project_id != project_id:
        raise ValueError("activation evidence project mismatch")
    if package.repository.casefold() != _origin_repository(root).casefold():
        raise ValueError("activation evidence repository mismatch")
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", package.tested_commit, "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise ValueError("activation evidence commit is not a current ancestor")


def _verify_github_attestation(
    artifact: Path,
    bundle: Path,
    package: ActivationEvidencePackage,
    policy: StageGateActivationPolicy,
) -> str:
    signer_workflow = _trusted_signer_workflow(package, policy)
    result = subprocess.run(
        [
            "gh",
            "attestation",
            "verify",
            str(artifact),
            "--repo",
            package.repository,
            "--bundle",
            str(bundle),
            "--signer-workflow",
            signer_workflow,
            "--source-digest",
            package.tested_commit,
            "--source-ref",
            "refs/heads/main",
            "--predicate-type",
            policy.evidence_predicate_type,
            "--deny-self-hosted-runners",
            "--format",
            "json",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    if result.returncode != 0:
        raise ValueError("activation evidence GitHub attestation is invalid")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("activation evidence verification output is invalid") from exc
    if not isinstance(payload, list) or not payload:
        raise ValueError("activation evidence verification output is empty")
    return _payload_digest(payload)


def _persist_import(
    shared: Path,
    receipt: ActivationEvidenceImportReceipt,
    package: ActivationEvidencePackage,
) -> None:
    receipt_path = (
        shared
        / "activation/evidence-imports/receipts"
        / f"{_name(receipt.receipt_digest)}.json"
    )
    _create_or_match(receipt_path, receipt)
    attestation_digests = (
        receipt.artifact_digest,
        receipt.bundle_digest,
        receipt.verification_output_digest,
    )
    for evidence in package.isolation_matrix:
        record = ActivationIsolationSourceRecord(
            project_id=package.project_id,
            evidence=evidence,
            source_attestation_digests=attestation_digests,
            import_receipt_digest=receipt.receipt_digest,
        )
        path = shared / "activation/evidence-sources/isolation" / (
            f"{_name(record.record_digest)}.json"
        )
        _create_or_match(path, record)
    probes = ActivationProbeSourceRecord(
        project_id=package.project_id,
        evidence=package.probes,
        source_artifact_digests=(
            *package.source_artifact_digests,
            receipt.artifact_digest,
        ),
        import_receipt_digest=receipt.receipt_digest,
    )
    probe_path = shared / "activation/evidence-sources/probes" / (
        f"{_name(probes.record_digest)}.json"
    )
    _create_or_match(probe_path, probes)


def _verify_receipt_package(
    receipt: ActivationEvidenceImportReceipt,
    package: ActivationEvidencePackage,
) -> None:
    expected = (
        receipt.project_id,
        receipt.repository,
        receipt.tested_commit,
        receipt.signer_workflow,
        receipt.evidence_purpose,
        receipt.package_digest,
    )
    actual = (
        package.project_id,
        package.repository,
        package.tested_commit,
        package.signer_workflow,
        package.evidence_purpose,
        package.package_digest,
    )
    if actual != expected:
        raise ValueError("activation evidence import receipt diverged")


def _verify_package_trust(
    package: ActivationEvidencePackage,
    policy: StageGateActivationPolicy,
) -> None:
    if package.evidence_purpose != policy.evidence_purpose:
        raise ValueError("activation evidence purpose is not trusted")
    _trusted_signer_workflow(package, policy)


def _trusted_signer_workflow(
    package: ActivationEvidencePackage,
    policy: StageGateActivationPolicy,
) -> str:
    trusted = tuple(
        f"{package.repository}/{path}"
        for path in policy.trusted_evidence_workflow_paths
    )
    if package.signer_workflow not in trusted:
        raise ValueError("activation evidence trusted workflow mismatch")
    return package.signer_workflow


def _origin_repository(root: Path) -> str:
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise ValueError("activation evidence requires a GitHub origin")
    value = result.stdout.strip().removesuffix(".git")
    if value.startswith("git@github.com:"):
        return value.removeprefix("git@github.com:")
    parsed = urlparse(value)
    if parsed.hostname != "github.com":
        raise ValueError("activation evidence origin is not GitHub")
    repository = parsed.path.strip("/")
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None:
        raise ValueError("activation evidence GitHub origin is invalid")
    return repository


def _store_source(shared: Path, source: Path, digest: str, suffix: str) -> str:
    relative = Path("activation/evidence-imports/payloads") / f"{_name(digest)}{suffix}"
    target = shared / relative
    if target.is_file():
        if _file_digest(target) != digest:
            raise ValueError("activation evidence content address diverged")
        return relative.as_posix()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    try:
        temporary.write_bytes(source.read_bytes())
        os.link(temporary, target)
    except FileExistsError:
        if _file_digest(target) != digest:
            raise ValueError("activation evidence content address diverged") from None
    finally:
        temporary.unlink(missing_ok=True)
    return relative.as_posix()


def _create_or_match(path: Path, value: object) -> None:
    payload = value.model_dump(mode="json")  # type: ignore[attr-defined]
    if create_json_exclusive(path, payload):
        return
    if read_json_object(path) != payload:
        raise ValueError("activation evidence imported record diverged")


def _shared_path(shared: Path, relative: str) -> Path:
    path = (shared / relative).resolve()
    if shared.resolve() not in path.parents:
        raise ValueError("activation evidence import escaped shared state")
    return path


def _file_digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _payload_digest(payload: object) -> str:
    value = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"


def _name(digest: str) -> str:
    if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
        raise ValueError("activation evidence digest is invalid")
    return digest.removeprefix("sha256:")


__all__ = [
    "ACTIVATION_EVIDENCE_INBOX",
    "ActivationEvidencePackage",
    "import_activation_evidence_inbox",
    "ingest_activation_evidence_package",
    "verify_activation_source_records",
]
