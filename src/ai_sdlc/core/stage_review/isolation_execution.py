"""核心拥有的隔离信任注册、短期 Permit 与单次消费收据。"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ai_sdlc.core.stage_review.binding_models import (
    HostCapabilitySnapshot,
    IsolationGrade,
)
from ai_sdlc.core.stage_review.isolation_models import (
    IsolationBoundaryResult,
    IsolationEvidenceManifest,
    IsolationExecutionPermit,
    IsolationExecutionReceipt,
    IsolationNativeDenial,
    IsolationPlatform,
)
from ai_sdlc.core.stage_review.isolation_models import (
    _manifest_digest as manifest_digest,
)
from ai_sdlc.core.stage_review.isolation_models import (
    _permit_digest as permit_digest,
)
from ai_sdlc.core.stage_review.isolation_permit_store import (
    IsolationPermitRefused,
    IsolationPermitStore,
    build_refusal_receipt,
)
from ai_sdlc.core.stage_review.resource_builders import parse_utc, stable_id

REQUIRED_ENFORCED_BOUNDARIES = tuple(
    sorted(
        (
            "candidate-read-only",
            "child-process-contained",
            "global-config-denied",
            "handles-filtered",
            "network-denied",
            "output-write-allowed",
            "peer-output-denied",
            "real-home-denied",
            "run-root-disposable",
            "symlink-boundary-denied",
        )
    )
)


@dataclass(frozen=True, slots=True)
class IsolationBackendContract:
    backend_id: str
    contract_version: str
    minimum_backend_version: str
    host_adapter_id: str
    capability_source: str
    platform_mechanisms: tuple[tuple[IsolationPlatform, str], ...]


class TrustedIsolationBackendRegistry:
    def __init__(self, contracts: tuple[IsolationBackendContract, ...]) -> None:
        keys = {(item.backend_id, item.contract_version) for item in contracts}
        if len(keys) != len(contracts):
            raise ValueError("isolation backend contract is duplicated")
        self._contracts = {
            (item.backend_id, item.contract_version): item for item in contracts
        }

    @classmethod
    def default(cls) -> TrustedIsolationBackendRegistry:
        return cls((codex_permission_profile_contract(),))

    def derive_grade(
        self,
        manifest: IsolationEvidenceManifest,
        host: HostCapabilitySnapshot,
        *,
        adapter_grade: IsolationGrade,
        now: datetime,
    ) -> IsolationGrade:
        if adapter_grade == "unproven":
            return "unproven"
        contract = self._contracts.get(
            (manifest.backend_id, manifest.contract_version)
        )
        if contract is None or not _trusted_manifest(contract, manifest, host, now):
            return "unproven"
        if adapter_grade == "detected_only":
            return "detected_only"
        if not _boundaries_enforced(manifest.boundary_results):
            return "unproven"
        return "enforced"


def codex_permission_profile_contract() -> IsolationBackendContract:
    return IsolationBackendContract(
        backend_id="codex.permission-profile",
        contract_version="2026-07-01",
        minimum_backend_version="0.138.0",
        host_adapter_id="ai-sdlc.core.codex-permission-profile-probe.v1",
        capability_source="builtin-subprocess-probe",
        platform_mechanisms=(
            ("linux", "bubblewrap-landlock-seccomp"),
            ("macos", "seatbelt"),
            ("windows", "native-windows-sandbox"),
        ),
    )


def _iterable(value: object) -> Iterable[object]:
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        return value
    raise TypeError("isolation evidence collection is invalid")


def build_isolation_evidence_manifest(
    **values: object,
) -> IsolationEvidenceManifest:
    boundaries = tuple(
        sorted(
            (
                IsolationBoundaryResult.model_validate(item)
                for item in _iterable(values["boundary_results"])
            ),
            key=lambda item: item.action,
        )
    )
    denials = tuple(
        sorted(
            (
                IsolationNativeDenial.model_validate(item)
                for item in _iterable(values["os_native_denials"])
            ),
            key=lambda item: (
                item.mechanism,
                item.operation,
                item.target,
                item.observed_at,
            ),
        )
    )
    canonical = {
        **values,
        "boundary_results": boundaries,
        "os_native_denials": denials,
    }
    draft = IsolationEvidenceManifest.model_construct(
        **canonical,  # type: ignore[arg-type]
        manifest_digest="",
    )
    payload = draft.model_dump(mode="json")
    payload["manifest_digest"] = manifest_digest(draft)
    return IsolationEvidenceManifest.model_validate(payload)


def build_isolation_execution_permit(
    **values: object,
) -> IsolationExecutionPermit:
    normalized = str(Path(str(values["normalized_run_root"])).resolve(strict=False))
    identity = stable_id(
        "isolation-permit",
        str(values["assignment_digest"]),
        str(values["nonce"]),
    )
    canonical = {
        **values,
        "permit_id": identity,
        "normalized_run_root": normalized,
    }
    draft = IsolationExecutionPermit.model_construct(
        **canonical,  # type: ignore[arg-type]
        permit_digest="",
    )
    payload = draft.model_dump(mode="json")
    payload["permit_digest"] = permit_digest(draft)
    return IsolationExecutionPermit.model_validate(payload)


def _trusted_manifest(
    contract: IsolationBackendContract,
    manifest: IsolationEvidenceManifest,
    host: HostCapabilitySnapshot,
    now: datetime,
) -> bool:
    mechanism = dict(contract.platform_mechanisms).get(manifest.platform)
    capabilities = set(host.capability_ids)
    return all(
        (
            host.host_adapter_id == contract.host_adapter_id,
            host.capability_source == contract.capability_source,
            host.snapshot_digest == manifest.host_snapshot_digest,
            host.backend_id == contract.backend_id == manifest.backend_id,
            host.backend_contract_version
            == contract.contract_version
            == manifest.contract_version,
            bool(host.backend_release_manifest_digest),
            manifest.release_manifest_digest
            == host.backend_release_manifest_digest,
            bool(host.backend_runtime_identity_digest),
            manifest.runtime_identity_digest
            == host.backend_runtime_identity_digest,
            manifest.cleanup_succeeded,
            parse_utc(host.expires_at) > now,
            parse_utc(manifest.issued_at) <= now < parse_utc(manifest.expires_at),
            manifest.backend_version == contract.minimum_backend_version,
            mechanism == manifest.platform_mechanism,
            f"isolation.{contract.backend_id}" in capabilities,
            f"network_enforcement.{contract.backend_id}" in capabilities,
        )
    )


def _boundaries_enforced(
    values: tuple[IsolationBoundaryResult, ...],
) -> bool:
    by_action = {item.action: item for item in values}
    if not set(REQUIRED_ENFORCED_BOUNDARIES) <= set(by_action):
        return False
    for action in REQUIRED_ENFORCED_BOUNDARIES:
        item = by_action[action]
        expected = "allowed" if action == "output-write-allowed" else "denied"
        if item.expected != expected or item.observed != expected:
            return False
        if expected == "denied" and (
            not item.blocked_before_side_effect
            or item.before_digest != item.after_digest
        ):
            return False
    return True


def _version_at_least(value: str, minimum: str) -> bool:
    pattern = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
    current = pattern.fullmatch(value)
    floor = pattern.fullmatch(minimum)
    if current is None or floor is None:
        return False
    return tuple(map(int, current.groups())) >= tuple(map(int, floor.groups()))


__all__ = [
    "IsolationEvidenceManifest",
    "IsolationBoundaryResult",
    "IsolationExecutionPermit",
    "IsolationExecutionReceipt",
    "IsolationPermitRefused",
    "IsolationNativeDenial",
    "IsolationPermitStore",
    "REQUIRED_ENFORCED_BOUNDARIES",
    "TrustedIsolationBackendRegistry",
    "build_isolation_evidence_manifest",
    "build_isolation_execution_permit",
    "build_refusal_receipt",
    "codex_permission_profile_contract",
]
