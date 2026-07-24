"""Reviewer 隔离启动边界使用的稳定数据与协议。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from ai_sdlc.core.stage_review.binding_models import (
    HostCapabilitySnapshot,
    IsolationGrade,
)
from ai_sdlc.core.stage_review.isolation_models import (
    IsolationBoundaryResult,
    IsolationEvidenceManifest,
    IsolationExecutionPermit,
    IsolationNativeDenial,
)
from ai_sdlc.core.stage_review.isolation_runtime_layout import IsolationRuntimeLayout
from ai_sdlc.core.stage_review.provider_journal_models import (
    ProviderInvocationRequest,
    ProviderQueryResult,
    ProviderRecoveryCapabilities,
    ProviderSubmission,
)

CommandKind = Literal["invoke", "query"]


@dataclass(frozen=True, slots=True)
class IsolationLaunchContext:
    allocation_digest: str
    assignment_digest: str
    candidate_digest: str
    host_snapshot: HostCapabilitySnapshot
    adapter_grade: IsolationGrade
    normalized_run_root: str
    layout_digest: str = ""
    candidate_root: str = ""
    peer_output_roots: tuple[str, ...] = ()
    disposable_home_root: str = ""
    disposable_config_root: str = ""
    disposable_credential_root: str = ""
    output_root: str = ""
    controller_config_root: str = ""
    protected_home_root: str = ""
    protected_config_roots: tuple[str, ...] = ()
    runtime_read_roots: tuple[str, ...] = ()
    selected_backend_id: str = ""
    selected_contract_version: str = ""
    release_manifest_digest: str = ""
    runtime_identity_digest: str = ""

    @classmethod
    def from_layout(
        cls,
        layout: IsolationRuntimeLayout,
        *,
        host_snapshot: HostCapabilitySnapshot,
        adapter_grade: IsolationGrade,
    ) -> IsolationLaunchContext:
        return cls(
            allocation_digest=layout.allocation_digest,
            assignment_digest=layout.assignment_digest,
            candidate_digest=layout.candidate_digest,
            host_snapshot=host_snapshot,
            adapter_grade=adapter_grade,
            normalized_run_root=layout.normalized_run_root,
            layout_digest=layout.layout_digest,
            candidate_root=layout.candidate_root,
            peer_output_roots=layout.peer_output_roots,
            disposable_home_root=layout.disposable_home_root,
            disposable_config_root=layout.disposable_config_root,
            disposable_credential_root=layout.disposable_credential_root,
            output_root=layout.output_root,
            controller_config_root=layout.controller_config_root,
            protected_home_root=layout.protected_home_root,
            protected_config_roots=layout.protected_config_roots,
            runtime_read_roots=layout.runtime_read_roots,
            selected_backend_id=host_snapshot.backend_id,
            selected_contract_version=host_snapshot.backend_contract_version,
            release_manifest_digest=host_snapshot.backend_release_manifest_digest,
            runtime_identity_digest=host_snapshot.backend_runtime_identity_digest,
        )


@dataclass(frozen=True, slots=True)
class IsolatedProviderCommand:
    argv: tuple[str, ...]
    stdin_text: str
    command_kind: CommandKind


@dataclass(frozen=True, slots=True)
class IsolationProcessResult:
    return_code: int
    stdout: str
    stderr: str
    process_id: int
    parent_process_id: int
    boundary_results: tuple[IsolationBoundaryResult, ...]
    os_native_denials: tuple[IsolationNativeDenial, ...]
    before_digest: str
    after_digest: str
    cleanup_succeeded: bool


class IsolationBackendBundle(Protocol):
    def probe(
        self,
        context: IsolationLaunchContext,
        now: datetime,
    ) -> IsolationEvidenceManifest: ...

    def execute(
        self,
        command: IsolatedProviderCommand,
        permit: IsolationExecutionPermit,
    ) -> IsolationProcessResult: ...


class IsolationExecutionRecorder(Protocol):
    def record_completed(self, result: IsolationProcessResult) -> None: ...

    def record_cleanup(self, result: IsolationProcessResult) -> None: ...


@runtime_checkable
class JournaledIsolationBackend(Protocol):
    def execute_journaled(
        self,
        command: IsolatedProviderCommand,
        permit: IsolationExecutionPermit,
        recorder: IsolationExecutionRecorder,
    ) -> IsolationProcessResult: ...


@runtime_checkable
class IsolatedCommandProviderDriver(Protocol):
    provider_id: str
    capabilities: ProviderRecoveryCapabilities

    def build_isolated_command(
        self,
        request: ProviderInvocationRequest,
        permit: IsolationExecutionPermit,
        command_kind: CommandKind,
    ) -> IsolatedProviderCommand: ...

    def decode_isolated_result(
        self,
        request: ProviderInvocationRequest,
        command_kind: CommandKind,
        result: IsolationProcessResult,
    ) -> ProviderSubmission | ProviderQueryResult: ...


__all__ = [
    "CommandKind",
    "IsolatedCommandProviderDriver",
    "IsolatedProviderCommand",
    "IsolationBackendBundle",
    "IsolationExecutionRecorder",
    "IsolationLaunchContext",
    "IsolationProcessResult",
    "JournaledIsolationBackend",
]
