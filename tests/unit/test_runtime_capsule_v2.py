from __future__ import annotations

import copy
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import ai_sdlc.benefit_benchmark_fixtures as fixture_module
import ai_sdlc.benefit_sealed_materializer as materializer


def _write_runtime(root: Path) -> Path:
    launcher = root / "bin" / "python3.14"
    libpython = root / "lib" / "libpython3.14.dylib"
    stdlib = root / "lib" / "python3.14"
    dynload = stdlib / "lib-dynload"
    launcher.parent.mkdir(parents=True, mode=0o755)
    dynload.mkdir(parents=True, mode=0o755)
    launcher.write_bytes(b"launcher-v2")
    launcher.chmod(0o755)
    libpython.write_bytes(b"libpython-v2")
    libpython.chmod(0o755)
    (stdlib / "json.py").write_bytes(b"stdlib-v2")
    (dynload / "_datetime.so").write_bytes(b"dynload-v2")
    return launcher


def _v2_manifest(launcher: Path, expected: str | None = None):
    return fixture_module.evaluator_runtime_capsule_v2_manifest(
        launcher,
        "3.14.3",
        expected_sha256=expected,
    )


def _v2_digest(capsule) -> str:
    return fixture_module.evaluator_runtime_capsule_v2_sha256(capsule)


def test_runtime_capsule_v2_root_time_only_churn_is_stable(tmp_path: Path) -> None:
    launcher = _write_runtime(tmp_path / "runtime")
    root = launcher.parents[1]
    before = _v2_manifest(launcher)
    metadata = root.stat()

    os.utime(
        root,
        ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1_000_000_000),
    )

    after = _v2_manifest(launcher, _v2_digest(before))
    assert after == before
    assert _v2_digest(after) == _v2_digest(before)


def test_runtime_capsule_v2_has_closed_stable_root_identity(tmp_path: Path) -> None:
    launcher = _write_runtime(tmp_path / "runtime")
    capsule = _v2_manifest(launcher)

    assert set(capsule) == {
        "schema",
        "root",
        "root_identity",
        "launcher",
        "libpython",
        "stdlib",
        "dynload",
        "entries",
    }
    assert capsule["schema"] == "ai-sdlc-v2-benefit-runtime-capsule/v2"
    assert set(capsule["root_identity"]) == {
        "path",
        "canonical_path",
        "type",
        "symlink",
        "device",
        "inode",
        "uid",
        "gid",
        "mode",
        "nlink",
        "size",
    }
    assert "ctime_ns" not in capsule["root_identity"]
    assert "mtime_ns" not in capsule["root_identity"]
    assert capsule["root_identity"]["path"] == "."
    assert capsule["root_identity"]["canonical_path"] == capsule["root"]
    assert capsule["root_identity"]["symlink"] is False
    assert all(entry["path"] != "." for entry in capsule["entries"])
    assert all(
        "ctime_ns" in entry and "mtime_ns" in entry for entry in capsule["entries"]
    )


def test_runtime_capsule_v2_actual_runtime_has_exact_non_root_closure() -> None:
    identity = fixture_module.evaluator_python_runtime_identity()
    capsule = fixture_module.evaluator_runtime_capsule_v2_manifest(
        Path(str(identity["path"])), str(identity["version"])
    )

    assert len(capsule["entries"]) == 1648
    assert len({entry["path"] for entry in capsule["entries"]}) == 1648
    assert capsule["root_identity"]["path"] == "."


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("st_dev", 999_991),
        ("st_ino", 999_992),
        ("st_uid", 999_993),
        ("st_gid", 999_996),
        ("st_nlink", 999_994),
        ("st_size", 999_995),
    ),
)
def test_runtime_capsule_v2_rejects_root_identity_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    replacement: int,
) -> None:
    launcher = _write_runtime(tmp_path / "runtime")
    root = launcher.parents[1]
    original = Path.lstat

    def drifted(path: Path):
        value = original(path)
        if path != root:
            return value
        fields = {
            name: getattr(value, name)
            for name in (
                "st_dev",
                "st_ino",
                "st_mode",
                "st_uid",
                "st_gid",
                "st_nlink",
                "st_size",
                "st_ctime_ns",
                "st_mtime_ns",
            )
        }
        fields[field] = replacement
        return SimpleNamespace(**fields)

    monkeypatch.setattr(Path, "lstat", drifted)
    with pytest.raises(
        fixture_module.EvaluatorNoGoError, match="capsule-(security|drift)"
    ):
        _v2_manifest(launcher)


def test_runtime_capsule_v2_rejects_root_replacement_and_symlink(
    tmp_path: Path,
) -> None:
    launcher = _write_runtime(tmp_path / "runtime")
    root = launcher.parents[1]
    displaced = tmp_path / "runtime-displaced"
    root.rename(displaced)
    root.symlink_to(displaced, target_is_directory=True)

    with pytest.raises(fixture_module.EvaluatorNoGoError, match="capsule-security"):
        _v2_manifest(root / "bin" / "python3.14")


def test_runtime_capsule_v2_rejects_root_mode_drift(tmp_path: Path) -> None:
    launcher = _write_runtime(tmp_path / "runtime")
    before = _v2_manifest(launcher)
    launcher.parents[1].chmod(0o700)

    with pytest.raises(fixture_module.EvaluatorNoGoError, match="capsule-drift"):
        _v2_manifest(launcher, _v2_digest(before))


def test_runtime_capsule_v2_rejects_group_or_world_writable_root(
    tmp_path: Path,
) -> None:
    launcher = _write_runtime(tmp_path / "runtime")
    launcher.parents[1].chmod(0o777)

    with pytest.raises(fixture_module.EvaluatorNoGoError, match="capsule-security"):
        _v2_manifest(launcher)


@pytest.mark.parametrize(
    "target", ("launcher", "libpython", "stdlib-file", "dynload-file")
)
@pytest.mark.parametrize("operation", ("content", "mode", "rename"))
def test_runtime_capsule_v2_rejects_dependency_content_or_mode_drift(
    tmp_path: Path, target: str, operation: str
) -> None:
    launcher = _write_runtime(tmp_path / "runtime")
    root = launcher.parents[1]
    paths = {
        "launcher": launcher,
        "libpython": root / "lib" / "libpython3.14.dylib",
        "stdlib-file": root / "lib" / "python3.14" / "json.py",
        "dynload-file": root / "lib" / "python3.14" / "lib-dynload" / "_datetime.so",
    }
    before = _v2_manifest(launcher)
    path = paths[target]
    if operation == "mode":
        path.chmod(0o700)
    elif operation == "rename":
        path.rename(path.with_name(f"{path.name}.moved"))
    else:
        path.write_bytes(path.read_bytes() + b"-drift")

    with pytest.raises(
        fixture_module.EvaluatorNoGoError,
        match="capsule-(drift|security)",
    ):
        _v2_manifest(launcher, _v2_digest(before))


@pytest.mark.parametrize("operation", ("add", "delete", "rename"))
def test_runtime_capsule_v2_rejects_stdlib_tree_drift(
    tmp_path: Path, operation: str
) -> None:
    launcher = _write_runtime(tmp_path / "runtime")
    stdlib = launcher.parents[1] / "lib" / "python3.14"
    target = stdlib / "json.py"
    before = _v2_manifest(launcher)
    if operation == "add":
        (stdlib / "added.py").write_bytes(b"added")
    elif operation == "delete":
        target.unlink()
    else:
        target.rename(stdlib / "renamed.py")

    with pytest.raises(fixture_module.EvaluatorNoGoError, match="capsule-drift"):
        _v2_manifest(launcher, _v2_digest(before))


def test_runtime_capsule_v2_rejects_mutation_during_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = _write_runtime(tmp_path / "runtime")
    stdlib = launcher.parents[1] / "lib" / "python3.14"
    original = fixture_module._runtime_capsule_v2_entry
    mutated = False

    def mutate(root: Path, path: Path):
        nonlocal mutated
        result = original(root, path)
        if not mutated and path.name == "json.py":
            (stdlib / "raced.py").write_bytes(b"raced")
            mutated = True
        return result

    monkeypatch.setattr(fixture_module, "_runtime_capsule_v2_entry", mutate)
    with pytest.raises(fixture_module.EvaluatorNoGoError, match="capsule-drift"):
        _v2_manifest(launcher)


def test_runtime_capsule_v1_keeps_root_time_binding_and_rejects_v2(
    tmp_path: Path,
) -> None:
    launcher = _write_runtime(tmp_path / "runtime")
    root = launcher.parents[1]
    v1 = fixture_module.evaluator_runtime_capsule_manifest(launcher, "3.14.3")
    metadata = root.stat()
    os.utime(
        root,
        ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1_000_000_000),
    )
    changed = fixture_module.evaluator_runtime_capsule_manifest(launcher, "3.14.3")

    assert fixture_module.evaluator_runtime_capsule_sha256(changed) != (
        fixture_module.evaluator_runtime_capsule_sha256(v1)
    )
    v2 = _v2_manifest(launcher)
    with pytest.raises(fixture_module.EvaluatorNoGoError, match="capsule-binding"):
        fixture_module.evaluator_runtime_capsule_sha256(v2)
    with pytest.raises(fixture_module.EvaluatorNoGoError, match="capsule-binding"):
        _v2_digest(v1)


@pytest.mark.parametrize(
    "mutation",
    ("missing-root-identity", "extra-field", "canonical-mismatch", "symlink-root"),
)
def test_runtime_capsule_v2_rejects_open_schema(tmp_path: Path, mutation: str) -> None:
    launcher = _write_runtime(tmp_path / "runtime")
    capsule = copy.deepcopy(_v2_manifest(launcher))
    if mutation == "missing-root-identity":
        del capsule["root_identity"]
    elif mutation == "canonical-mismatch":
        capsule["root_identity"]["canonical_path"] = "/private/tmp/not-the-runtime"
    elif mutation == "symlink-root":
        capsule["root_identity"]["symlink"] = True
    else:
        capsule["unexpected"] = True

    with pytest.raises(fixture_module.EvaluatorNoGoError, match="capsule-binding"):
        _v2_digest(capsule)


def test_r3_constants_are_monotonic_without_reinterpreting_r2() -> None:
    policy = materializer.default_policy()

    assert materializer.FINAL_LOCK_ID == "v2-benefits-20260819-r3"
    assert policy.target.name == "v2-benefits-20260819-r3"
    assert policy.source_root.name == "sealed-source-r3"
    assert materializer.R2_ROOT in policy.prior_source_roots or any(
        item.path == materializer.R2_ROOT and item.label == "validated-r2"
        for item in policy.immutable_roots
    )
    assert materializer.DISPOSITION_ROOT.name.endswith("r3")
    assert fixture_module._SEALED_AUTHORITY_LOCK_ID == "v2-benefits-20260819-r2"
