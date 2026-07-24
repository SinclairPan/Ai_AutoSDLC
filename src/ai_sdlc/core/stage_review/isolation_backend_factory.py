"""按 backend、contract、platform、architecture 精确选择 Bundle Factory。"""

from __future__ import annotations

from collections.abc import Callable

from ai_sdlc.core.stage_review.isolation_backend_identity import (
    TrustedBackendReleaseManifest,
    VerifiedBackendRuntimeIdentity,
)

BackendFactory = Callable[[VerifiedBackendRuntimeIdentity], object]
BackendKey = tuple[str, str, str, str]


class IsolationBackendBundleFactoryRegistry:
    def __init__(self) -> None:
        self._factories: dict[BackendKey, BackendFactory] = {}

    def register(
        self,
        *,
        backend_id: str,
        contract_version: str,
        platform_id: str,
        architecture: str,
        factory: BackendFactory,
    ) -> None:
        key = (backend_id, contract_version, platform_id, architecture)
        if key in self._factories:
            raise ValueError("isolation backend factory is already registered")
        self._factories[key] = factory

    def create(
        self,
        release: TrustedBackendReleaseManifest,
        runtime: VerifiedBackendRuntimeIdentity,
    ) -> object:
        key = (
            release.backend_id,
            release.contract_version,
            release.platform_id,
            release.architecture,
        )
        factory = self._factories.get(key)
        if factory is None:
            raise ValueError("isolation backend factory is not registered")
        lineage = (
            runtime.release_manifest_digest == release.manifest_digest,
            runtime.backend_id == release.backend_id,
            runtime.contract_version == release.contract_version,
            runtime.exact_backend_version == release.exact_backend_version,
            runtime.platform_id == release.platform_id,
            runtime.architecture == release.architecture,
            runtime.native_sha256 == release.native_sha256,
        )
        if not all(lineage):
            raise ValueError("isolation backend runtime identity does not match release")
        return factory(runtime)
