"""Adversarial tests for the five frozen v2 benchmark arm environments."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from ai_sdlc.benefit_benchmark import AttemptRequest, AttemptReservation, load_protocol
from ai_sdlc.benefit_benchmark_arms import (
    AI_SDLC_COMMIT,
    AI_SDLC_TREE,
    ARM_IDS,
    CODEX_VERSION,
    SUPERPOWERS_COMMIT,
    SUPERPOWERS_TREE,
    BoundedReviewBridge,
    build_arm_isolation_profile,
    build_codex_command,
    inspect_instruction_sources,
    load_arm_manifest,
    prepare_arm,
    validate_arm_manifest,
    validate_execution_authorization_v2,
    verify_method_instruction_immutability,
)
from ai_sdlc.benefit_benchmark_fixtures import (
    prepare_fixture,
    probe_provider_isolation,
    run_provider_isolated,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ROOT = REPO_ROOT / "benchmarks" / "ai-sdlc-v2-benefits"
ARMS_ROOT = BENCHMARK_ROOT / "arms"
PROTOCOL_PATH = BENCHMARK_ROOT / "protocol.json"
FIXTURE_IDS = (
    "requirement-contract-ambiguity",
    "frontend-recovery-delivery",
    "multi-tenant-security-review",
)


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _prepared(tmp_path: Path, arm: str = "P", fixture_id: str = FIXTURE_IDS[0]):
    fixture = prepare_fixture(fixture_id, tmp_path / "fixture")
    return prepare_arm(
        arm,
        fixture,
        tmp_path / "run",
        shared_runtime_root=tmp_path / "runtime",
        environment_root=tmp_path / "environment",
    )


def _reservation(arm: str = "P") -> AttemptReservation:
    request = AttemptRequest(
        run_id=f"{arm}:requirement-contract-ambiguity", kind="writer", arm=arm
    )
    return AttemptReservation("attempt-001", 1, request)


def test_arm_manifest_is_closed_and_binds_every_frozen_source() -> None:
    manifest = load_arm_manifest(ARMS_ROOT / "manifest.json")

    assert validate_arm_manifest(manifest, ARMS_ROOT) == ()
    assert manifest.arm_ids == ARM_IDS == ("P", "S", "A00", "A10", "A11")
    assert manifest.ai_sdlc_commit == AI_SDLC_COMMIT
    assert manifest.ai_sdlc_tree == AI_SDLC_TREE
    assert manifest.superpowers_commit == SUPERPOWERS_COMMIT
    assert manifest.superpowers_tree == SUPERPOWERS_TREE
    assert manifest.codex.version == CODEX_VERSION == "0.147.0"
    assert manifest.superpowers.multi_agent is False
    assert manifest.superpowers.namespace_rewrite == {
        "from": "superpowers:<name>",
        "to": "$<name>",
    }
    assert manifest.superpowers.source_url == "https://github.com/obra/superpowers.git"
    assert manifest.superpowers.tag == "v6.3.0"
    assert manifest.superpowers.license_path == "S/LICENSE.superpowers"
    assert len(manifest.superpowers.files) >= 20
    assert all(
        entry.mode in {"100644", "100755"} for entry in manifest.superpowers.files
    )
    assert not any(entry.kind == "symlink" for entry in manifest.superpowers.files)


@pytest.mark.parametrize("mutation", ["extra", "missing", "digest", "reference"])
def test_arm_manifest_rejects_closed_world_and_vendor_drift(
    tmp_path: Path, mutation: str
) -> None:
    copied = tmp_path / "arms"
    shutil.copytree(ARMS_ROOT, copied)
    raw = json.loads((copied / "manifest.json").read_text(encoding="utf-8"))
    if mutation == "extra":
        raw["unfrozen"] = True
    elif mutation == "missing":
        raw.pop("codex")
    elif mutation == "digest":
        raw["common_agent_contract_sha256"] = "0" * 64
    else:
        path = copied / "S" / ".agents" / "skills" / "using-superpowers" / "SKILL.md"
        path.write_text(path.read_text() + "\nUse $does-not-exist.\n", encoding="utf-8")
    (copied / "manifest.json").write_text(
        json.dumps(raw, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="arm manifest"):
        load_arm_manifest(copied / "manifest.json")


@pytest.mark.parametrize("arm", ARM_IDS)
def test_prepare_arm_uses_fresh_single_root_git_and_identical_public_bytes(
    tmp_path: Path, arm: str
) -> None:
    prepared = _prepared(tmp_path, arm)
    root = prepared.root

    assert prepared.provider_cwd_relative == "benchmark-task/"
    assert prepared.provider_cwd == root / "benchmark-task"
    assert (
        prepared.provider_cwd.stat().st_ino
        == Path(prepared.subprocess_cwd).stat().st_ino
    )
    assert root.stat().st_mode & 0o777 == 0o700
    assert (root / ".git").is_dir() and not (root / ".git").is_symlink()
    assert (
        subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == "1"
    )
    source_contract = (
        BENCHMARK_ROOT
        / "fixtures"
        / "requirement-contract-ambiguity"
        / "public"
        / "benchmark-task"
        / "input-contract.json"
    )
    assert (
        prepared.provider_cwd / "input-contract.json"
    ).read_bytes() == source_contract.read_bytes()
    assert prepared.public_input_sha256 == _sha(source_contract)
    assert prepared.environment.home != Path.home()
    assert (
        prepared.environment.codex_home
        != Path(os.environ.get("CODEX_HOME", "~")).expanduser()
    )
    assert prepared.environment.home.stat().st_mode & 0o777 == 0o700
    assert prepared.environment.codex_home.stat().st_mode & 0o777 == 0o700
    assert prepared.environment.provider_attempts_started == 0


def test_all_fifteen_prepared_workspaces_are_inode_isolated_and_reproducible(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    environment = tmp_path / "environment"
    records = []
    public_inodes: set[int] = set()
    git_inodes: set[int] = set()
    for fixture_id in FIXTURE_IDS:
        for arm in ARM_IDS:
            run_id = f"{arm}-{fixture_id}"
            fixture = prepare_fixture(fixture_id, tmp_path / "fixtures" / run_id)
            prepared = prepare_arm(
                arm,
                fixture,
                tmp_path / "runs" / run_id,
                shared_runtime_root=runtime,
                environment_root=environment / run_id,
            )
            contract = prepared.provider_cwd / "input-contract.json"
            public_inodes.add(contract.stat().st_ino)
            git_inodes.add((prepared.root / ".git").stat().st_ino)
            records.append(
                (
                    arm,
                    fixture_id,
                    prepared.public_input_sha256,
                    prepared.methodology_sha256,
                    prepared.base_global_sha256,
                )
            )

    assert len(records) == 15
    assert len(public_inodes) == 15
    assert len(git_inodes) == 15
    assert len({record[4] for record in records}) == 1
    for fixture_id in FIXTURE_IDS:
        assert len({record[2] for record in records if record[1] == fixture_id}) == 1
    assert len({record[3] for record in records if record[0] == "P"}) == 1


def test_instruction_inventory_proves_methodology_separation(tmp_path: Path) -> None:
    inventories = {}
    base_digests = set()
    for arm in ARM_IDS:
        prepared = _prepared(tmp_path / arm, arm)
        inventory = inspect_instruction_sources(prepared)
        inventories[arm] = inventory
        base_digests.add(inventory.base_global_sha256)

    assert len(base_digests) == 1
    assert inventories["P"].repo_skills == ()
    assert inventories["P"].ai_sdlc_present is False
    assert inventories["P"].superpowers_present is False
    assert inventories["S"].superpowers_present is True
    assert inventories["S"].ai_sdlc_present is False
    assert "using-superpowers" in inventories["S"].repo_skills
    for arm in ("A00", "A10", "A11"):
        assert inventories[arm].ai_sdlc_present is True
        assert inventories[arm].superpowers_present is False
        assert inventories[arm].repo_skills == ()
    assert len(inventories["A00"].resolved_instruction_chain) == 2
    assert len(inventories["A10"].resolved_instruction_chain) == 2
    assert len(inventories["A11"].resolved_instruction_chain) == 1


def test_actual_prompt_tool_inventory_is_persisted_and_path_stable(
    tmp_path: Path,
) -> None:
    first = _prepared(tmp_path / "first", "P")
    second = _prepared(tmp_path / "second", "P")
    first_inventory = json.loads(first.instruction_inventory_path.read_text())
    second_inventory = json.loads(second.instruction_inventory_path.read_text())

    assert first.instruction_inventory_sha256 == second.instruction_inventory_sha256
    assert first.base_global_sha256 == second.base_global_sha256
    assert first_inventory["provider_attempts_started"] == 0
    assert first_inventory["base_global"] == second_inventory["base_global"]
    assert first_inventory["base_global"]["installed_plugins"] == []
    assert first_inventory["base_global"]["apps"] == []
    assert first_inventory["base_global"]["mcp_servers"] == []
    assert first_inventory["base_global"]["global_rules"] == []
    assert first_inventory["base_global"]["global_skills"] == [
        "imagegen",
        "openai-docs",
        "plugin-creator",
        "skill-creator",
        "skill-installer",
    ]
    assert first.instruction_inventory_path.stat().st_mode & 0o777 == 0o600


def test_destination_reuse_symlink_and_git_shape_fail_closed(tmp_path: Path) -> None:
    fixture = prepare_fixture(FIXTURE_IDS[0], tmp_path / "fixture")
    destination = tmp_path / "reuse"
    destination.mkdir()
    with pytest.raises(ValueError, match="destination"):
        prepare_arm("P", fixture, destination)

    linked_fixture = prepare_fixture(FIXTURE_IDS[0], tmp_path / "linked-fixture")
    nested = linked_fixture.root / "benchmark-task" / "nested"
    nested.mkdir()
    (nested / "outside-link").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="symlink"):
        prepare_arm("P", linked_fixture, tmp_path / "linked-run")

    hardlinked_fixture = prepare_fixture(FIXTURE_IDS[0], tmp_path / "hardlink-fixture")
    contract = hardlinked_fixture.root / "benchmark-task" / "input-contract.json"
    os.link(contract, hardlinked_fixture.root / "benchmark-task" / "input-alias.json")
    with pytest.raises(ValueError, match="hardlink"):
        prepare_arm("P", hardlinked_fixture, tmp_path / "hardlink-run")

    malformed_fixture = prepare_fixture(FIXTURE_IDS[0], tmp_path / "bad-git-fixture")
    shutil.rmtree(malformed_fixture.root / ".git")
    (malformed_fixture.root / ".git").write_text("gitdir: /untrusted")
    with pytest.raises(ValueError, match="fixture"):
        prepare_arm("P", malformed_fixture, tmp_path / "bad-git-run")


def test_instruction_mutation_and_cross_contamination_are_detected(
    tmp_path: Path,
) -> None:
    prepared = _prepared(tmp_path, "P")
    agents = prepared.root / "AGENTS.md"
    agents.chmod(0o600)
    agents.write_text(agents.read_text() + "\nmutated\n")
    assert verify_method_instruction_immutability(prepared)
    (prepared.root / ".ai-sdlc").mkdir()
    assert "arms.p-contamination" in {
        issue.code for issue in inspect_instruction_sources(prepared).issues
    }


def test_instruction_namespace_additions_and_skill_tree_drift_are_detected(
    tmp_path: Path,
) -> None:
    plain = _prepared(tmp_path / "plain", "P")
    nested_agents = plain.provider_cwd / "AGENTS.md"
    nested_agents.write_text("unallowlisted nested instruction")
    assert "arms.instruction-namespace-drift" in {
        issue.code for issue in inspect_instruction_sources(plain).issues
    }

    superpowers = _prepared(tmp_path / "superpowers", "S")
    skills = superpowers.root / ".agents" / "skills"
    skills.chmod(0o755)
    injected = skills / "injected"
    injected.mkdir()
    (injected / "SKILL.md").write_text("---\nname: injected\ndescription: drift\n---\n")
    assert verify_method_instruction_immutability(superpowers)
    assert "arms.global-inventory-drift" in {
        issue.code for issue in inspect_instruction_sources(superpowers).issues
    }


def test_global_methodology_pollution_after_prepare_is_detected(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path, "P")
    skill = prepared.environment.codex_home / "skills" / "superpowers"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: superpowers\ndescription: contaminated global method\n---\n"
    )

    assert "arms.global-inventory-drift" in {
        issue.code for issue in inspect_instruction_sources(prepared).issues
    }


def _isolation_fixture(tmp_path: Path):
    prepared = _prepared(tmp_path / "runs" / "prepared", "A11")
    protected_parent = tmp_path / "protected"
    sealed = protected_parent / "sealed-r2"
    raw = protected_parent / "raw-results"
    other = tmp_path / "runs" / "other-run"
    protected = [
        protected_parent / "sealed-r1",
        protected_parent / "sealed-legacy",
        protected_parent / "sealed-source",
        protected_parent / "sealed-source-r2",
        protected_parent / "disposition",
        protected_parent / "template",
    ]
    for root in (sealed, raw, other, *protected):
        root.mkdir(parents=True)
        (root / "canary").write_text(root.name)
    profile = build_arm_isolation_profile(
        prepared,
        _reservation("A11"),
        sealed_root=sealed,
        control_root=REPO_ROOT,
        raw_results_root=raw,
        other_run_roots=[other],
        protected_roots=protected,
    )
    return prepared, profile, sealed, raw, other, protected


def test_task2_strong_profile_covers_every_surface_and_method_write_denial(
    tmp_path: Path,
) -> None:
    prepared, profile, _sealed, raw, other, protected = _isolation_fixture(tmp_path)

    assert profile.executable is (sys.platform == "darwin")
    assert profile.issues == ()
    assert profile.run_root == prepared.provider_cwd
    assert str(raw.resolve()) in profile.sandbox_text
    assert str(other.resolve()) in profile.sandbox_text
    for root in protected:
        assert str(root.resolve()) in profile.sandbox_text
    for path in prepared.method_instruction_paths:
        assert str(path.resolve()) in profile.sandbox_text
    for path in prepared.method_instruction_roots:
        assert str(path.resolve()) in profile.sandbox_text
    assert prepared.shared_runtime_root is not None
    assert str(prepared.shared_runtime_root.resolve()) in profile.sandbox_text
    assert "--add-dir" not in profile.argv


@pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("AI_SDLC_RUN_SYSTEM_ISOLATION") != "1",
    reason="requires an unnested macOS Seatbelt execution",
)
def test_system_outside_seatbelt_denies_read_links_and_instruction_runtime_writes(
    tmp_path: Path,
) -> None:
    prepared, profile, sealed, raw, other, protected = _isolation_fixture(tmp_path)
    probe = probe_provider_isolation(profile)
    read = run_provider_isolated(profile, ["/bin/cat", str(sealed / "canary")])
    instruction = prepared.root / "AGENTS.md"
    write_instruction = run_provider_isolated(
        profile,
        ["/bin/sh", "-c", f"printf drift >> '{instruction}'"],
    )
    injected_method = prepared.provider_cwd / ".benchmark" / "injected-method.md"
    write_method_namespace = run_provider_isolated(
        profile,
        ["/bin/sh", "-c", f"printf drift > '{injected_method}'"],
    )
    assert prepared.shared_runtime_root is not None
    runtime_marker = prepared.shared_runtime_root / ".runtime-binding.json"
    write_runtime = run_provider_isolated(
        profile,
        ["/bin/sh", "-c", f"printf drift >> '{runtime_marker}'"],
    )
    allowed = prepared.provider_cwd / "allowed-write.txt"
    write_allowed = run_provider_isolated(
        profile,
        ["/bin/sh", "-c", f"printf allowed > '{allowed}'"],
    )
    superpowers = _prepared(tmp_path / "runs" / "superpowers", "S")
    superpowers_profile = build_arm_isolation_profile(
        superpowers,
        _reservation("S"),
        sealed_root=sealed,
        control_root=REPO_ROOT,
        raw_results_root=raw,
        other_run_roots=[other, prepared.provider_cwd],
        protected_roots=protected,
    )
    injected_skill = superpowers.root / ".agents" / "skills" / "injected.md"
    write_skill_namespace = run_provider_isolated(
        superpowers_profile,
        ["/bin/sh", "-c", f"printf drift > '{injected_skill}'"],
    )

    assert all(
        (
            probe.direct,
            probe.parent,
            probe.symlink,
            probe.hardlink,
            probe.environment,
            probe.other_run,
            probe.add_dir,
        )
    )
    assert read.returncode != 0 and "Operation not permitted" in read.stderr
    assert write_instruction.returncode != 0
    assert write_method_namespace.returncode != 0
    assert not injected_method.exists()
    assert write_runtime.returncode != 0
    assert write_skill_namespace.returncode != 0
    assert not injected_skill.exists()
    assert write_allowed.returncode == 0
    assert allowed.read_text() == "allowed"
    assert verify_method_instruction_immutability(prepared) == ()


def test_stock_agents_and_allowlisted_overlays_are_exact(tmp_path: Path) -> None:
    prepared = {arm: _prepared(tmp_path / arm, arm) for arm in ("A00", "A10", "A11")}
    manifest = load_arm_manifest(ARMS_ROOT / "manifest.json")
    stock = (prepared["A11"].root / "AGENTS.md").read_bytes()

    assert sha256(stock).hexdigest() == manifest.stock_agents_sha256
    assert (prepared["A00"].root / "AGENTS.md").read_bytes() == stock
    assert (prepared["A10"].root / "AGENTS.md").read_bytes() == stock
    assert not (prepared["A11"].provider_cwd / "AGENTS.override.md").exists()
    for arm in ("A00", "A10"):
        override = prepared[arm].provider_cwd / "AGENTS.override.md"
        assert _sha(override) == manifest.arm(arm).override_sha256
    assert (
        "不调用本场景 Loop"
        in (prepared["A00"].provider_cwd / "AGENTS.override.md").read_text()
    )
    assert (
        "runner 不派发专家"
        in (prepared["A10"].provider_cwd / "AGENTS.override.md").read_text()
    )


def test_ai_sdlc_real_init_and_canonical_semantic_parity(tmp_path: Path) -> None:
    for arm in ("A00", "A10", "A11"):
        prepared = _prepared(tmp_path / arm, arm, "frontend-recovery-delivery")
        assert prepared.framework_init.exit_code == 0
        assert prepared.framework_init.real_init is True
        assert prepared.framework_init.ai_sdlc_commit == AI_SDLC_COMMIT
        assert prepared.framework_init.provider_attempts_started == 0
        assert prepared.canonical_pre_state_sha256 is not None
        state = json.loads(
            (
                prepared.root / ".ai-sdlc" / "benchmark" / "canonical-pre-state.json"
            ).read_text()
        )
        contract = json.loads(
            (prepared.provider_cwd / "input-contract.json").read_text()
        )
        assert state["semantics"] == contract["semantics"]
        assert set(json.dumps(state, sort_keys=True).lower().split())
        assert prepared.frontend_approval.required is True
        assert prepared.frontend_approval.source_tree_before_approval_sha256
        assert prepared.frontend_approval.source_tree_after_approval_sha256 is None


def test_build_codex_command_is_pure_exact_and_non_launching(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path, "S")
    before = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=prepared.root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    argv = build_codex_command(prepared, _reservation("S"))
    after = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=prepared.root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    assert argv[:2] == [prepared.codex.executable, "exec"]
    assert "--ephemeral" in argv
    assert "--json" in argv
    assert "--ignore-user-config" in argv
    assert "--ignore-rules" in argv
    assert "--strict-config" in argv
    assert argv[argv.index("--model") + 1] == "gpt-5.6-sol"
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    assert argv[argv.index("-C") + 1] == str(prepared.provider_cwd)
    assert argv[-1] == "-"
    assert "$using-superpowers" in prepared.prompt
    assert before == after == ""
    assert prepared.environment.provider_attempts_started == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "cwd",
        "reservation-arm",
        "model",
        "reasoning-effort",
        "json",
        "ephemeral",
        "sandbox",
        "add-dir",
        "network",
    ],
)
def test_command_negative_matrix_fails_closed(tmp_path: Path, mutation: str) -> None:
    prepared = _prepared(tmp_path, "P")
    reservation = _reservation("P")
    if mutation == "cwd":
        prepared = replace(prepared, subprocess_cwd=str(prepared.root))
    elif mutation == "reservation-arm":
        reservation = _reservation("S")
    else:
        key = {
            "model": "model",
            "reasoning-effort": "reasoning_effort",
            "json": "json",
            "ephemeral": "ephemeral",
            "sandbox": "sandbox",
            "add-dir": "forbid_add_dir",
            "network": "network_disabled",
        }[mutation]
        prepared = replace(
            prepared,
            command_policy=replace(prepared.command_policy, **{key: False}),
        )
    with pytest.raises(ValueError, match="command|cwd|reservation"):
        build_codex_command(prepared, reservation)


def test_expert_command_is_read_only_schema_bound_and_non_launching(
    tmp_path: Path,
) -> None:
    prepared = _prepared(tmp_path, "A11", "multi-tenant-security-review")
    schema = tmp_path / "findings.schema.json"
    schema.write_text('{"type":"object"}')
    request = AttemptRequest(
        run_id="A11:multi-tenant-security-review",
        kind="primary_expert",
        arm="A11",
    )
    reservation = AttemptReservation("attempt-001", 1, request)

    with pytest.raises(ValueError, match="output schema"):
        build_codex_command(prepared, reservation)
    argv = build_codex_command(prepared, reservation, output_schema=schema)

    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert argv[argv.index("--output-schema") + 1] == str(schema.resolve())
    assert prepared.environment.provider_attempts_started == 0


def test_a11_fake_bridge_same_writer_repair_rereview_and_close() -> None:
    bridge = BoundedReviewBridge(
        run_id="A11:multi-tenant-security-review",
        fixture_id="multi-tenant-security-review",
        writer_session="writer-1",
        parent_digest="1" * 64,
        candidate_digest="2" * 64,
    )
    first = bridge.dispatch_fake_review(
        role="Primary",
        reason="implementation correctness",
        child_session="expert-1",
        snapshot=b"frozen snapshot",
        parent_tree_before="3" * 64,
        parent_tree_after="3" * 64,
        findings=[{"id": "F1", "severity": "important", "fix": "tenant-filter"}],
    )
    bridge.dispatch_fake_review(
        role="Cross-risk",
        reason="security risk surface",
        child_session="expert-2",
        snapshot=b"frozen snapshot",
        parent_tree_before="3" * 64,
        parent_tree_after="3" * 64,
        findings=[],
    )
    assert first.finding_digest
    assert bridge.close_allowed is False
    bridge.record_writer_repair(
        writer_session="writer-1",
        repair_digest="4" * 64,
        new_candidate_digest="5" * 64,
    )
    bridge.dispatch_fake_rereview(
        role="Primary",
        child_session="rereview-1",
        snapshot=b"repaired frozen snapshot",
        parent_tree_before="6" * 64,
        parent_tree_after="6" * 64,
        findings=[],
    )
    review_digest = bridge.review_digest
    close = bridge.close(
        writer_session="writer-1",
        expected_review_digest=review_digest,
        candidate_digest="5" * 64,
    )
    assert close["status"] == "closed"
    assert close["writer_session"] == "writer-1"
    assert bridge.close_allowed is True


def test_a11_fake_bridge_requires_primary_and_bound_fresh_rereview() -> None:
    cross_only = BoundedReviewBridge(
        run_id="A11:multi-tenant-security-review",
        fixture_id="multi-tenant-security-review",
        writer_session="writer-1",
        parent_digest="1" * 64,
        candidate_digest="2" * 64,
    )
    cross_only.dispatch_fake_review(
        "Cross-risk", "security", "expert-1", b"s", "3" * 64, "3" * 64, []
    )
    with pytest.raises(ValueError, match="Close preconditions"):
        cross_only.close("writer-1", cross_only.review_digest, "2" * 64)

    bridge = BoundedReviewBridge(
        run_id="A11:multi-tenant-security-review",
        fixture_id="multi-tenant-security-review",
        writer_session="writer-1",
        parent_digest="1" * 64,
        candidate_digest="2" * 64,
    )
    bridge.dispatch_fake_review(
        "Primary",
        "correctness",
        "expert-1",
        b"s",
        "3" * 64,
        "3" * 64,
        [{"id": "F1", "severity": "important", "fix": "repair"}],
    )
    bridge.record_writer_repair("writer-1", "4" * 64, "5" * 64)
    with pytest.raises(ValueError, match="snapshot|parent"):
        bridge.dispatch_fake_rereview(
            "Primary", "rereview-1", b"", "not-a-digest", "not-a-digest", []
        )


@pytest.mark.parametrize(
    "case",
    [
        "cross-risk-wrong-fixture",
        "third-role",
        "duplicate-child",
        "parent-mutation",
        "replacement-writer",
        "early-close",
        "schema",
        "timeout",
        "conflict",
    ],
)
def test_a11_fake_bridge_adversarial_fail_closed(case: str) -> None:
    fixture_id = (
        "frontend-recovery-delivery"
        if case == "cross-risk-wrong-fixture"
        else "multi-tenant-security-review"
    )
    bridge = BoundedReviewBridge(
        run_id=f"A11:{fixture_id}",
        fixture_id=fixture_id,
        writer_session="writer-1",
        parent_digest="1" * 64,
        candidate_digest="2" * 64,
    )
    with pytest.raises(ValueError, match="review|close|writer|conflict|schema|timeout"):
        if case == "early-close":
            bridge.close("writer-1", "3" * 64, "2" * 64)
        elif case == "replacement-writer":
            bridge.record_writer_repair("writer-2", "4" * 64, "5" * 64)
        elif case == "timeout":
            bridge.fail("timeout")
            bridge.close("writer-1", "3" * 64, "2" * 64)
        elif case == "schema":
            bridge.dispatch_fake_review(
                "Primary", "reason", "expert-1", b"s", "3" * 64, "3" * 64, [{}]
            )
        else:
            role = "Cross-risk" if case == "cross-risk-wrong-fixture" else "Primary"
            bridge.dispatch_fake_review(
                role,
                "security",
                "expert-1",
                b"s",
                "3" * 64,
                "4" * 64 if case == "parent-mutation" else "3" * 64,
                [
                    {
                        "id": "F1",
                        "severity": "important",
                        "fix": "a",
                        **(
                            {"conflict_key": "policy", "exclusive_value": "allow"}
                            if case == "conflict"
                            else {}
                        ),
                    }
                ],
            )
            if case == "duplicate-child":
                bridge.dispatch_fake_review(
                    "Cross-risk", "security", "expert-1", b"s", "3" * 64, "3" * 64, []
                )
            elif case == "third-role":
                bridge.dispatch_fake_review(
                    "Cross-risk", "security", "expert-2", b"s", "3" * 64, "3" * 64, []
                )
                bridge.dispatch_fake_review(
                    "Primary", "again", "expert-3", b"s", "3" * 64, "3" * 64, []
                )
            elif case == "conflict":
                bridge.dispatch_fake_review(
                    "Cross-risk",
                    "security",
                    "expert-2",
                    b"s",
                    "3" * 64,
                    "3" * 64,
                    [
                        {
                            "id": "F2",
                            "severity": "important",
                            "fix": "b",
                            "conflict_key": "policy",
                            "exclusive_value": "deny",
                        }
                    ],
                )


def _v2_authorization_payload(protocol, preflight: Path) -> dict[str, object]:
    return {
        "schema": "ai-sdlc-v2-benefit-execution-authorization/v2",
        "protocol_sha256": sha256(protocol.canonical_bytes).hexdigest(),
        "execution_commit": "a" * 40,
        "arm_manifest_sha256": _sha(ARMS_ROOT / "manifest.json"),
        "neutral_envelope_sha256": _sha(ARMS_ROOT / "common-agent-contract.md"),
        "superpowers_adaptation_sha256": _sha(ARMS_ROOT / "S" / "adaptation.json"),
        "preflight_receipt_sha256": _sha(preflight),
        "execution_identity": json.loads(PROTOCOL_PATH.read_text())["execution_lock"],
        "attempt_budget": json.loads(PROTOCOL_PATH.read_text())["attempt_budget"],
        "valid_from": "2020-01-01T00:00:00Z",
        "expires_at": "2099-01-01T00:00:00Z",
        "scope": {
            "mode": "single-frozen-matrix",
            "run_ids": [run.run_id for run in protocol.run_matrix],
            "operations": [
                "start_run",
                "transition_run_phase",
                "reserve_provider_attempt",
                "record_provider_completion",
                "start_service_transaction",
                "record_service_transaction",
                "seal_run_evidence",
            ],
        },
    }


def _write_v2_authorization(path: Path, payload: dict[str, object]) -> Path:
    path.write_bytes(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    )
    path.chmod(0o600)
    return path


def test_execution_authorization_v2_binds_task3_and_rejects_v1(tmp_path: Path) -> None:
    protocol = load_protocol(PROTOCOL_PATH)
    preflight = tmp_path / "preflight-receipt.json"
    preflight.write_bytes(b'{"schema":"synthetic-preflight/v1"}\n')
    payload = _v2_authorization_payload(protocol, preflight)
    authorization = _write_v2_authorization(tmp_path / "authorization.json", payload)

    assert (
        validate_execution_authorization_v2(
            protocol,
            authorization,
            execution_commit="a" * 40,
            preflight_receipt=preflight,
            arms_root=ARMS_ROOT,
        )
        == ()
    )
    payload["schema"] = "ai-sdlc-v2-benefit-execution-authorization/v1"
    authorization.write_bytes(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    )
    assert validate_execution_authorization_v2(
        protocol,
        authorization,
        execution_commit="a" * 40,
        preflight_receipt=preflight,
        arms_root=ARMS_ROOT,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "protocol",
        "execution-commit",
        "arm-manifest",
        "neutral-envelope",
        "superpowers-adaptation",
        "preflight",
        "identity",
        "budget",
        "scope",
        "expiry",
        "extra",
        "mode",
        "hardlink",
        "symlink",
        "preflight-hardlink",
        "preflight-symlink",
    ],
)
def test_execution_authorization_v2_attack_matrix_fails_closed(
    tmp_path: Path, mutation: str
) -> None:
    protocol = load_protocol(PROTOCOL_PATH)
    preflight = tmp_path / "preflight-receipt.json"
    preflight.write_bytes(b'{"schema":"synthetic-preflight/v1"}\n')
    payload = _v2_authorization_payload(protocol, preflight)
    key = {
        "protocol": "protocol_sha256",
        "execution-commit": "execution_commit",
        "arm-manifest": "arm_manifest_sha256",
        "neutral-envelope": "neutral_envelope_sha256",
        "superpowers-adaptation": "superpowers_adaptation_sha256",
        "preflight": "preflight_receipt_sha256",
    }.get(mutation)
    if key is not None:
        payload[key] = "0" * (40 if key == "execution_commit" else 64)
    elif mutation == "identity":
        payload["execution_identity"]["model"] = "drift"
    elif mutation == "budget":
        payload["attempt_budget"]["limit"] = 34
    elif mutation == "scope":
        payload["scope"]["operations"] = []
    elif mutation == "expiry":
        payload["expires_at"] = "2020-01-01T00:00:01Z"
    elif mutation == "extra":
        payload["permission"] = True
    authorization = _write_v2_authorization(tmp_path / "authorization.json", payload)
    if mutation == "mode":
        authorization.chmod(0o644)
    elif mutation == "hardlink":
        os.link(authorization, tmp_path / "authorization-alias.json")
    elif mutation == "symlink":
        target = tmp_path / "authorization-target.json"
        authorization.rename(target)
        authorization.symlink_to(target)
    elif mutation == "preflight-hardlink":
        os.link(preflight, tmp_path / "preflight-alias.json")
    elif mutation == "preflight-symlink":
        target = tmp_path / "preflight-target.json"
        preflight.rename(target)
        preflight.symlink_to(target)

    assert validate_execution_authorization_v2(
        protocol,
        authorization,
        execution_commit="a" * 40,
        preflight_receipt=preflight,
        arms_root=ARMS_ROOT,
    )


def test_zero_execution_explode_guards(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prepared = _prepared(tmp_path)
    calls = []

    def explode(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Provider execution is forbidden in Task 3")

    monkeypatch.setattr(subprocess, "Popen", explode)
    monkeypatch.setattr(os, "system", explode)
    argv = build_codex_command(prepared, _reservation())

    assert argv[1] == "exec"
    assert calls == []
    assert prepared.environment.provider_attempts_started == 0
    assert not (BENCHMARK_ROOT / "results").exists()
