"""按可信发布清单精确解析 npm Codex shim 到 native binary。"""

from __future__ import annotations

from pathlib import Path

from ai_sdlc.core.stage_review.isolation_backend_identity import (
    TrustedBackendReleaseManifest,
)

_SHIM_SUFFIXES = frozenset({".cmd", ".ps1", ".js"})


def resolve_codex_native_executable(
    value: Path,
    release: TrustedBackendReleaseManifest | None = None,
) -> Path:
    unresolved = value.expanduser().resolve(strict=False)
    if release is None:
        return unresolved
    if release.shim_resolver_id != "codex-npm-layout.v1":
        raise ValueError("Codex shim resolver is not trusted")
    candidates = _manifest_candidates(value, release)
    if len(candidates) > 1:
        raise ValueError("Codex shim resolves to multiple native binaries")
    if candidates:
        return candidates[0]
    raise ValueError("Codex shim does not resolve to the manifest native binary")


def _manifest_candidates(
    shim: Path,
    release: TrustedBackendReleaseManifest,
) -> tuple[Path, ...]:
    package_parts = tuple(part for part in release.package_name.split("/") if part)
    candidates: set[Path] = set()
    for node_modules in _node_modules_roots(shim):
        package_root = node_modules.joinpath(*package_parts).resolve(strict=False)
        native = (package_root / release.native_relative_path).resolve(strict=False)
        if not native.is_relative_to(package_root):
            raise ValueError("Codex manifest native path escapes package root")
        if native.is_file():
            candidates.add(native.resolve(strict=True))
    return tuple(sorted(candidates))


def _node_modules_roots(shim: Path) -> tuple[Path, ...]:
    roots: set[Path] = set()
    start = shim.expanduser().resolve(strict=False)
    for parent in (start.parent, *start.parents):
        if parent.name == "node_modules":
            roots.add(parent)
        adjacent = parent / "node_modules"
        if adjacent.is_dir():
            roots.add(adjacent.resolve(strict=True))
    return tuple(sorted(roots))
