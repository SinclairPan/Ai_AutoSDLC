from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

from ai_sdlc.benefit_benchmark_arms import prepare_arm
from ai_sdlc.benefit_benchmark_fixtures import prepare_fixture, run_provider_isolated
from ai_sdlc.benefit_directional_demo import (
    build_directional_provider_profile,
    directional_protected_roots,
    run_directional_system_isolation_canary,
)


@pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS Seatbelt")
def test_exact_directional_profile_denies_actual_surfaces(tmp_path: Path) -> None:
    fixture = prepare_fixture("multi-tenant-security-review", tmp_path / "fixture")
    prepared = prepare_arm(
        "A11",
        fixture,
        tmp_path / "run",
        shared_runtime_root=tmp_path / "shared-runtime",
        environment_root=tmp_path / "environment",
    )
    prepared.root.chmod(0o700)
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    profile = build_directional_provider_profile(prepared, output_root=output)

    allowed = run_provider_isolated(profile, ["/usr/bin/true"])
    assert allowed.returncode == 0, (allowed.returncode, allowed.stderr)
    candidate_input = next(
        path
        for path in sorted(prepared.provider_cwd.rglob("*"))
        if path.is_file() and not path.is_symlink()
    )
    readable = run_provider_isolated(profile, ["/bin/cat", str(candidate_input)])
    assert readable.returncode == 0, (readable.returncode, readable.stderr)

    roots = {item.label: item.path for item in directional_protected_roots()}
    common_git_read = run_provider_isolated(
        profile, ["/bin/cat", str(roots["common-git"] / "config")]
    )
    assert common_git_read.returncode != 0
    assert "Operation not permitted" in common_git_read.stderr
    assert "ISOLATION_REFUSED" not in common_git_read.stderr
    worktree_parent_list = run_provider_isolated(
        profile, ["/bin/ls", str(roots["worktree-parent"])]
    )
    assert worktree_parent_list.returncode != 0
    assert "Operation not permitted" in worktree_parent_list.stderr
    assert "ISOLATION_REFUSED" not in worktree_parent_list.stderr

    for provider_path in (
        prepared.codex.executable,
        prepared.codex.resolved_executable,
    ):
        for argv in (
            [provider_path, "--version"],
            [
                "/bin/sh",
                "-c",
                'exec "$1" --version',
                "nested-provider",
                provider_path,
            ],
        ):
            nested = run_provider_isolated(profile, argv)
            assert nested.returncode != 0
            assert "Operation not permitted" in nested.stderr
            assert "ISOLATION_REFUSED" not in nested.stderr

    copied = prepared.provider_cwd / ".copied-provider"
    copy_attempt = run_provider_isolated(
        profile,
        [
            "/bin/sh",
            "-c",
            'cp "$1" "$2" && "$2" --version',
            "nested-copy",
            prepared.codex.resolved_executable,
            str(copied),
        ],
    )
    assert copy_attempt.returncode != 0
    assert "Operation not permitted" in copy_attempt.stderr
    assert not copied.exists()

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(2)
    port = listener.getsockname()[1]
    token = "outer-loopback-ok"
    try:
        outer_network = run_provider_isolated(
            profile,
            [
                "/usr/bin/python3",
                "-I",
                "-c",
                (
                    "import socket,sys; s=socket.socket(); "
                    "s.connect(('127.0.0.1',int(sys.argv[1]))); "
                    "s.sendall(sys.argv[2].encode()); s.close()"
                ),
                str(port),
                token,
            ],
        )
        connection, _ = listener.accept()
        with connection:
            received = connection.recv(128)
    finally:
        listener.close()
    assert outer_network.returncode == 0, outer_network.stderr
    assert received == token.encode()

    result = run_directional_system_isolation_canary(prepared, profile)
    assert result.passed is True
    assert result.outer_loopback_allowed is True
    assert result.inner_network_denied is True
    assert result.nested_provider_denied is True
    assert result.nested_provider_copy_denied is True
    assert result.one_shot_cleanup is True
