"""Round-one adversarial regressions for the frozen benchmark arms.

These tests intentionally describe the aggregate security boundary before the
production implementation changes.  Provider execution remains impossible.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

import ai_sdlc.benefit_benchmark_arms as arms
from ai_sdlc.benefit_benchmark import (
    AttemptRequest,
    AttemptReservation,
    ExecutionLock,
    canonical_protocol_digest,
    load_protocol,
    validate_execution_authorization,
)
from ai_sdlc.benefit_benchmark_fixtures import prepare_fixture

REPO_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ROOT = REPO_ROOT / "benchmarks" / "ai-sdlc-v2-benefits"
ARMS_ROOT = BENCHMARK_ROOT / "arms"
PROTOCOL_PATH = BENCHMARK_ROOT / "protocol.json"
HEX = "a" * 64


def _write_v1_authorization(path: Path) -> Path:
    protocol = load_protocol(PROTOCOL_PATH)
    now = datetime.now(UTC)
    payload = {
        "schema": "ai-sdlc-v2-benefit-execution-authorization/v1",
        "protocol_sha256": canonical_protocol_digest(protocol),
        "execution_identity": {
            field.name: getattr(protocol.execution_lock, field.name)
            for field in fields(ExecutionLock)
        },
        "attempt_budget": {
            field.name: getattr(protocol.attempt_budget, field.name)
            for field in fields(protocol.attempt_budget)
        },
        "valid_from": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "scope": {
            "mode": "synthetic-unit-mutation",
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
    path.write_bytes(json.dumps(payload, sort_keys=True).encode())
    path.chmod(0o600)
    return path


@pytest.fixture(scope="module")
def prepared_p(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("fixr1-p")
    fixture = prepare_fixture("requirement-contract-ambiguity", root / "fixture")
    return arms.prepare_arm(
        "P",
        fixture,
        root / "run",
        environment_root=root / "environment",
    )


def _bridge() -> arms.BoundedReviewBridge:
    return arms.BoundedReviewBridge(
        run_id="A11:multi-tenant-security-review",
        fixture_id="multi-tenant-security-review",
        writer_session="writer-1",
        parent_digest=HEX,
        candidate_digest="b" * 64,
        initial_snapshot=b"initial-snapshot",
        input_digest="9" * 64,
        candidate_tree_digest=HEX,
    )


def _finding(value: str = "tenant") -> list[dict[str, str]]:
    return [{"id": "F-1", "severity": "high", "fix": value}]


def _review(
    bridge: arms.BoundedReviewBridge,
    role: str,
    *,
    session: str,
    snapshot: bytes = b"initial-snapshot",
    findings: list[dict[str, str]] | None = None,
) -> None:
    bridge.dispatch_fake_review(
        role,
        "security bounded role" if role == "Cross-risk" else "bounded role",
        session,
        snapshot,
        HEX,
        HEX,
        findings or [],
    )


def test_a_production_rejects_a_well_formed_v1_authorization(tmp_path: Path) -> None:
    protocol = load_protocol(PROTOCOL_PATH)
    auth = _write_v1_authorization(tmp_path / "authorization.json")

    issues = validate_execution_authorization(protocol, auth)

    assert [issue.code for issue in issues] == ["authorization.execution"]


def test_b_prepared_arm_freezes_git_and_prompt_identity() -> None:
    names = {field.name for field in fields(arms.PreparedArm)}

    assert {
        "prompt_sha256",
        "run_root_identity",
        "provider_cwd_identity",
        "git_dir_identity",
        "git_head",
        "git_tree",
        "provider_pre_tree_sha256",
        "method_surface_sha256",
    } <= names


def test_b_command_builder_requires_a_fresh_identity_recheck() -> None:
    source = inspect.getsource(arms.build_codex_command)

    assert "verify_prepared_arm_identity" in source
    assert "O_NOFOLLOW" in inspect.getsource(arms.verify_prepared_arm_identity)


def test_c_clean_environment_is_closed_and_launch_complete(prepared_p) -> None:
    environment = dict(prepared_p.environment.environment)
    path_parts = environment["PATH"].split(":")

    assert environment["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert "AI_SDLC_BENCHMARK_SERVICE_SOCKET" in environment
    assert all(".venv" not in item and ".codex" not in item for item in path_parts)
    assert (
        prepared_p.environment.environment_sha256
        == sha256(
            json.dumps(environment, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def test_d_common_contract_is_prompt_only_and_hash_bound(prepared_p) -> None:
    common = (ARMS_ROOT / "common-agent-contract.md").read_text(encoding="utf-8")

    assert not (prepared_p.root / "AGENTS.md").exists()
    assert prepared_p.prompt.count(common.rstrip()) == 1
    assert prepared_p.prompt_sha256 == sha256(prepared_p.prompt.encode()).hexdigest()
    assert prepared_p.instruction_inventory_sha256 == prepared_p.prompt_sha256


def test_d_prompt_mutation_is_rejected_before_command_construction(prepared_p) -> None:
    reservation = AttemptReservation(
        "attempt-001",
        1,
        AttemptRequest(
            run_id="P:requirement-contract-ambiguity", kind="writer", arm="P"
        ),
    )

    with pytest.raises(ValueError, match="prompt"):
        arms.build_codex_command(
            replace(prepared_p, prompt=prepared_p.prompt + "\nunbound injection"),
            reservation,
        )


def test_d_expert_command_requires_an_independent_snapshot_root() -> None:
    parameters = inspect.signature(arms.build_codex_command).parameters

    assert "expert_snapshot" in parameters


def test_e_superpowers_provider_closure_is_single_agent() -> None:
    manifest = arms.load_arm_manifest()
    provider = ARMS_ROOT / manifest.superpowers.provider_closure_path
    provenance = ARMS_ROOT / manifest.superpowers.provenance_root

    assert provider.is_dir() and provenance.is_dir()
    assert not provider.is_relative_to(provenance)
    reachable = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in provider.rglob("*")
        if path.is_file()
    ).lower()
    assert not any(word in reachable for word in ("subagent", "parallel", "dispatch"))
    assert manifest.superpowers.semantic_adaptation_diff_sha256


def test_f_method_namespace_policy_is_closed_in_manifest() -> None:
    manifest = arms.load_arm_manifest()

    assert manifest.ai_sdlc_method_surface_manifest_sha256
    assert manifest.isolation["p_s_forbidden_namespaces"] == [
        ".ai-sdlc",
        ".agents",
        ".codex",
        "AGENTS.md",
        "AGENTS.override.md",
    ]
    assert manifest.isolation["a_writable_method_leaves"]


def test_f_p_arm_rejects_a_new_method_namespace(tmp_path: Path) -> None:
    fixture = prepare_fixture("requirement-contract-ambiguity", tmp_path / "fixture")
    prepared = arms.prepare_arm(
        "P", fixture, tmp_path / "run", environment_root=tmp_path / "environment"
    )
    injected = prepared.provider_cwd / ".ai-sdlc"
    injected.mkdir()

    issues = arms.verify_method_instruction_immutability(prepared)

    assert [issue.code for issue in issues] == ["arms.instruction-mutation"]


def test_g_isolation_profile_accepts_only_a_closed_surface_contract() -> None:
    parameters = inspect.signature(arms.build_arm_isolation_profile).parameters

    assert "surfaces" in parameters
    assert "protected_roots" not in parameters
    assert "other_run_roots" not in parameters
    assert hasattr(arms, "ProductionSurfaceContract")


def test_h_git_environment_is_closed() -> None:
    environment = arms.closed_git_environment()

    assert environment["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert all(
        key in arms.ALLOWED_GIT_ENVIRONMENT_KEYS
        for key in environment
        if key.startswith("GIT_")
    )


def test_i_model_visible_capabilities_are_explicitly_disabled() -> None:
    required = {
        "hooks",
        "memories",
        "skill_mcp_dependency_install",
        "tool_suggest",
        "workspace_dependencies",
    }

    assert required <= set(arms._CAPABILITY_OFF_FEATURES)


def test_j_security_primary_only_cannot_close() -> None:
    bridge = _bridge()
    _review(bridge, "Primary", session="expert-primary")

    with pytest.raises(ValueError, match="required roles"):
        bridge.close("writer-1", bridge.review_digest, bridge.candidate_digest)


def test_j_initial_experts_must_share_the_frozen_snapshot() -> None:
    bridge = _bridge()
    _review(bridge, "Primary", session="expert-primary")

    with pytest.raises(ValueError, match="snapshot"):
        _review(
            bridge,
            "Cross-risk",
            session="expert-cross-risk",
            snapshot=b"different-snapshot",
        )


def test_j_repair_waits_for_every_required_initial_expert() -> None:
    bridge = _bridge()
    _review(
        bridge,
        "Primary",
        session="expert-primary",
        findings=_finding(),
    )

    with pytest.raises(ValueError, match="initial"):
        bridge.record_writer_repair(
            "writer-1", "c" * 64, "d" * 64, b"repaired-snapshot", "8" * 64
        )


def test_j_writer_can_repair_at_most_once() -> None:
    bridge = _bridge()
    _review(
        bridge,
        "Primary",
        session="expert-primary",
        findings=_finding(),
    )
    _review(bridge, "Cross-risk", session="expert-cross-risk")
    bridge.record_writer_repair(
        "writer-1", "c" * 64, "d" * 64, b"repaired-snapshot", "8" * 64
    )

    with pytest.raises(ValueError, match="once"):
        bridge.record_writer_repair(
            "writer-1", "e" * 64, "f" * 64, b"second-snapshot", "7" * 64
        )


def test_j_rereview_must_bind_the_repaired_snapshot() -> None:
    bridge = _bridge()
    _review(
        bridge,
        "Primary",
        session="expert-primary",
        findings=_finding(),
    )
    _review(bridge, "Cross-risk", session="expert-cross-risk")
    bridge.record_writer_repair(
        "writer-1", "c" * 64, "d" * 64, b"repaired-snapshot", "8" * 64
    )

    with pytest.raises(ValueError, match="repaired snapshot"):
        bridge.dispatch_fake_rereview(
            "Primary",
            "rereview-primary",
            b"stale-initial-snapshot",
            "8" * 64,
            "8" * 64,
            [],
        )


def test_k_task3_has_no_provider_launcher_or_result_artifacts() -> None:
    source = inspect.getsource(arms)

    assert "codex exec" not in source.replace(
        "Provider execution is forbidden in Task 3", ""
    )
    assert not (BENCHMARK_ROOT / "results").exists()
    assert not (BENCHMARK_ROOT / "evidence" / "preflight-receipt.json").exists()
