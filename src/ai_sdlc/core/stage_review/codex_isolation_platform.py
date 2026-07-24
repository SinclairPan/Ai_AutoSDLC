"""Codex Reviewer 隔离的平台机制选择与 Linux 外层边界。"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path

from ai_sdlc.core.stage_review.isolation_models import IsolationPlatform


def wrap_platform_sandbox(
    codex_command: tuple[str, ...],
    writable_roots: tuple[str, ...],
) -> tuple[str, ...]:
    del writable_roots
    # Codex 0.138+ owns the native sandbox process. Wrapping it in a second
    # bubblewrap layer prevents the inner sandbox from creating its namespaces.
    return codex_command


def platform_mechanism() -> tuple[IsolationPlatform, str]:
    current = platform.system().lower()
    if current == "darwin":
        return "macos", "seatbelt"
    if current == "windows":
        return "windows", "native-windows-sandbox"
    mechanism = (
        "bubblewrap-landlock-seccomp"
        if _system_bubblewrap_path() is not None
        else "linux-mechanism-unconfirmed"
    )
    return "linux", mechanism


def _system_bubblewrap_path() -> Path | None:
    for candidate in (Path("/usr/bin/bwrap"), Path("/bin/bwrap")):
        if not candidate.is_file():
            continue
        try:
            completed = subprocess.run(
                (str(candidate), "--version"),
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                env={},
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if completed.returncode == 0 and "bubblewrap" in completed.stdout.lower():
            return candidate
    return None


__all__ = ["platform_mechanism", "wrap_platform_sandbox"]
