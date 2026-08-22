from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_sdlc import benefit_directional_demo as demo

FIXTURES = (
    "requirement-contract-ambiguity",
    "frontend-recovery-delivery",
    "multi-tenant-security-review",
)
ARMS = ("P", "S", "A00", "A10", "A11")
SCHEMA_ROOT = (
    Path(__file__).resolve().parents[2]
    / "benchmarks"
    / "ai-sdlc-v2-directional"
    / "schemas"
)


def _metric(run_id: str, *, delivered: bool = True) -> dict[str, object]:
    return {
        "schema": "ai-sdlc-v2-directional-metric/v1",
        "run_id": run_id,
        "external_verified_delivery": delivered,
        "weighted_acceptance_coverage": 0.75 if delivered else 0.0,
        "severe_defect_escape_count": 0 if delivered else 1,
        "wall_time_seconds": 12.5,
        "provider_sessions": 1,
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "currency_cost": None,
    }


def test_manifest_freezes_exact_matrix_and_rotated_order() -> None:
    manifest = demo.load_directional_manifest()
    assert manifest.schema == "ai-sdlc-v2-directional-manifest/v1"
    assert manifest.arm_ids == ARMS
    assert manifest.fixture_ids == FIXTURES
    assert len(manifest.runs) == 15
    assert len({run.run_id for run in manifest.runs}) == 15
    assert all(
        run.run_id.startswith("run-") and run.arm_id not in run.run_id
        for run in manifest.runs
    )
    for fixture_id in FIXTURES:
        block = [run for run in manifest.runs if run.fixture_id == fixture_id]
        assert len(block) == 5
        assert {run.arm_id for run in block} == set(ARMS)
    orders = [
        tuple(run.arm_id for run in manifest.runs if run.fixture_id == fixture_id)
        for fixture_id in FIXTURES
    ]
    assert len(set(orders)) == 3


def test_manifest_freezes_exact_19_session_table() -> None:
    manifest = demo.load_directional_manifest()
    assert manifest.max_provider_sessions == 19
    assert len(manifest.sessions) == 19
    assert len({session.session_id for session in manifest.sessions}) == 19
    assert sum(session.kind == "writer" for session in manifest.sessions) == 15
    assert sum(session.kind == "primary_expert" for session in manifest.sessions) == 3
    assert (
        sum(session.kind == "cross_risk_expert" for session in manifest.sessions) == 1
    )
    assert all(
        session.arm_id == "A11"
        for session in manifest.sessions
        if session.kind != "writer"
    )


def test_a11_expert_roles_are_minimal_and_predeclared() -> None:
    manifest = demo.load_directional_manifest()
    experts = [session for session in manifest.sessions if session.kind != "writer"]
    primary = {
        (session.fixture_id, session.role)
        for session in experts
        if session.kind == "primary_expert"
    }
    cross = {
        (session.fixture_id, session.role)
        for session in experts
        if session.kind == "cross_risk_expert"
    }
    assert primary == {(fixture, "Primary") for fixture in FIXTURES}
    assert cross == {("multi-tenant-security-review", "Cross-risk")}
    assert all(not session.retry and not session.rereview for session in experts)


def test_manifest_rejects_extra_fields_and_attempt_20(tmp_path: Path) -> None:
    raw = json.loads(demo.directional_manifest_path().read_text(encoding="utf-8"))
    raw["unexpected"] = True
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="closed"):
        demo.load_directional_manifest(path)
    ledger = demo.initialize_attempt_ledger(
        tmp_path / "ledger.jsonl", demo.load_directional_manifest()
    )
    for session in demo.load_directional_manifest().sessions:
        demo.reserve_session(ledger, session.session_id)
    before = ledger.read_bytes()
    with pytest.raises(ValueError, match="session cap"):
        demo.reserve_session(ledger, "session-attempt-20")
    assert ledger.read_bytes() == before


def test_ledger_rejects_duplicate_extra_expert_and_retry(tmp_path: Path) -> None:
    manifest = demo.load_directional_manifest()
    ledger = demo.initialize_attempt_ledger(tmp_path / "ledger.jsonl", manifest)
    first = manifest.sessions[0]
    demo.reserve_session(ledger, first.session_id)
    before = ledger.read_bytes()
    with pytest.raises(ValueError, match="duplicate"):
        demo.reserve_session(ledger, first.session_id)
    with pytest.raises(ValueError, match="predeclared"):
        demo.reserve_session(ledger, "session-extra-expert")
    with pytest.raises(ValueError, match="retry"):
        demo.reserve_session(ledger, first.session_id, retry=True)
    assert ledger.read_bytes() == before


def test_ledger_is_append_only_and_no_overwrite(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    demo.initialize_attempt_ledger(ledger, demo.load_directional_manifest())
    original = ledger.read_bytes()
    with pytest.raises(FileExistsError):
        demo.initialize_attempt_ledger(ledger, demo.load_directional_manifest())
    assert ledger.read_bytes() == original


def test_ledger_corruption_fails_before_append(tmp_path: Path) -> None:
    manifest = demo.load_directional_manifest()
    ledger = demo.initialize_attempt_ledger(tmp_path / "ledger.jsonl", manifest)
    first = manifest.sessions[0]
    corrupted = ledger.read_bytes().replace(
        b'"max_provider_sessions":19', b'"max_provider_sessions":20'
    )
    ledger.write_bytes(corrupted)
    ledger.chmod(0o600)
    before = ledger.read_bytes()
    with pytest.raises(ValueError, match="corrupt"):
        demo.reserve_session(ledger, first.session_id)
    assert ledger.read_bytes() == before
    ledger.write_bytes(b'{"partial":')
    ledger.chmod(0o600)
    before = ledger.read_bytes()
    with pytest.raises(ValueError, match="corrupt"):
        demo.reserve_session(ledger, first.session_id)
    assert ledger.read_bytes() == before


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("model-timeout", ("cell-terminal-failure", True)),
        ("model-nonzero", ("cell-terminal-failure", True)),
        ("invalid-output", ("cell-terminal-failure", True)),
        ("provider-5xx", ("matrix-abort-incomplete", False)),
        ("network", ("matrix-abort-incomplete", False)),
        ("rate-limit", ("matrix-abort-incomplete", False)),
        ("host", ("matrix-abort-incomplete", False)),
        ("isolation", ("matrix-abort-incomplete", False)),
        ("ledger-corruption", ("matrix-abort-incomplete", False)),
        ("budget-exhausted", ("matrix-incomplete", False)),
    ],
)
def test_failure_semantics_are_closed(
    category: str, expected: tuple[str, bool]
) -> None:
    result = demo.classify_failure(category)
    assert (result.matrix_status, result.continue_matrix) == expected


def test_metric_schema_accepts_authoritative_null_usage() -> None:
    metric = demo.validate_directional_metric(_metric("run-e1a68b37c685b81436b21438"))
    assert metric.input_tokens is None
    assert metric.currency_cost is None


def test_published_schemas_are_closed_and_directional() -> None:
    expected = {
        "metric.schema.json": "ai-sdlc-v2-directional-metric/v1",
        "blind-evaluator-input.schema.json": (
            "ai-sdlc-v2-directional-blind-evaluator-input/v1"
        ),
        "preflight.schema.json": "ai-sdlc-v2-directional-preflight/v1",
        "run-receipt.schema.json": "ai-sdlc-v2-directional-run-receipt/v1",
        "summary.schema.json": "ai-sdlc-v2-directional-summary/v1",
    }
    for name, schema_id in expected.items():
        schema = json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))
        assert schema["$id"] == schema_id
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("weighted_acceptance_coverage", 1.01),
        ("weighted_acceptance_coverage", -0.01),
        ("severe_defect_escape_count", -1),
        ("wall_time_seconds", -0.1),
        ("provider_sessions", 0),
        ("input_tokens", "123"),
        ("currency_cost", "cheap"),
    ],
)
def test_metric_schema_rejects_invalid_values(field: str, value: object) -> None:
    payload = _metric("run-e1a68b37c685b81436b21438")
    payload[field] = value
    with pytest.raises(ValueError):
        demo.validate_directional_metric(payload)


def test_stdout_claimed_score_and_cost_are_not_metrics() -> None:
    payload = _metric("run-e1a68b37c685b81436b21438")
    payload["stdout"] = "score=1.0 cost=$0.01"
    with pytest.raises(ValueError, match="closed"):
        demo.validate_directional_metric(payload)


def test_receipts_are_atomic_unique_and_runner_owned(tmp_path: Path) -> None:
    run_id = "run-e1a68b37c685b81436b21438"
    receipt = demo.build_fake_receipt(run_id, _metric(run_id))
    path = demo.write_run_receipt(tmp_path, receipt)
    assert path.stat().st_mode & 0o777 == 0o600
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        demo.write_run_receipt(tmp_path, receipt)
    assert path.read_bytes() == before
    tampered = {**receipt, "writer": "provider"}
    with pytest.raises(ValueError, match="runner"):
        demo.validate_run_receipt(tampered)


def test_summary_requires_all_15_and_has_exact_caveats() -> None:
    manifest = demo.load_directional_manifest()
    receipts = [
        demo.build_fake_receipt(run.run_id, _metric(run.run_id))
        for run in manifest.runs
    ]
    summary = demo.build_directional_summary(manifest, receipts)
    assert summary.labels == (
        "directional engineering observation",
        "n=3 per arm",
        "single run per task",
        "not statistically significant",
        "not production SLA",
        "no generalization",
    )
    assert summary.publishable is True
    with pytest.raises(ValueError, match="15"):
        demo.build_directional_summary(manifest, receipts[:-1])


def test_summary_uses_blind_external_metrics_and_separates_process() -> None:
    manifest = demo.load_directional_manifest()
    receipts = [
        demo.build_fake_receipt(run.run_id, _metric(run.run_id))
        for run in manifest.runs
    ]
    summary = demo.build_directional_summary(manifest, receipts)
    assert summary.evaluator_inputs == ("opaque_run_id", "candidate_snapshot")
    assert summary.main_quality_fields == (
        "external_verified_delivery",
        "weighted_acceptance_coverage",
        "severe_defect_escape_count",
    )
    assert "process_auditability" not in summary.main_quality_fields
    envelope = demo.build_blind_evaluator_input(manifest.runs[0].run_id, "a" * 64)
    assert set(envelope) == {
        "schema",
        "opaque_run_id",
        "candidate_snapshot_sha256",
    }
    assert "arm_id" not in envelope
    with pytest.raises(ValueError, match="closed"):
        demo.validate_blind_evaluator_input({**envelope, "arm_id": "P"})


def test_preflight_freezes_order_output_and_budget_request(tmp_path: Path) -> None:
    manifest = demo.load_directional_manifest()
    output = tmp_path / "directional-output"
    preflight = demo.build_directional_preflight(manifest, output)
    assert preflight.execution_order == tuple(
        session.session_id for session in manifest.sessions
    )
    assert preflight.output_root == str(output.resolve())
    assert preflight.provider_calls_started == 0
    assert preflight.formal_authority_status == "NO-GO"
    request = demo.build_budget_confirmation(preflight)
    assert request == {
        "model": "gpt-5.6-sol",
        "reasoning_effort": "high",
        "writer_sessions": 15,
        "expert_sessions": 4,
        "hard_session_cap": 19,
        "technical_retries": 0,
        "token_estimate": None,
        "currency_cost_estimate": None,
        "authorization_requested": False,
    }
    frozen = demo.load_frozen_directional_preflight()
    assert frozen.provider_calls_started == 0
    assert frozen.execution_order == preflight.execution_order
    assert frozen.output_root == (
        "/private/tmp/ai-sdlc-v2-directional-results/20260820-v1"
    )


def test_presentation_contract_distinguishes_product_and_controls() -> None:
    contract = demo.load_presentation_contract()
    assert contract["homepage_tracks"] == ["P", "S", "A11"]
    assert contract["research_controls"] == ["A00", "A10"]
    assert contract["show_raw_paired_values"] is True
    assert contract["show_losses"] is True
    assert contract["quality_cost_frontier"] is True
    assert contract["winner_cherry_pick"] is False
    website = demo.load_website_data_template()
    assert website["status"] == "awaiting-real-complete-15"
    assert [item["id"] for item in website["comparisons"]] == [
        "pure-llm-vs-superpowers",
        "superpowers-vs-ai-sdlc",
        "loop-effect",
        "expert-effect",
    ]
    assert website["raw_paired_values"] == []
    assert website["losses"] == []
    assert website["winner"] is None


def test_forbidden_provider_surfaces_cover_authorities_and_wip(tmp_path: Path) -> None:
    roots = demo.directional_protected_roots(tmp_path)
    labels = {item.label for item in roots}
    assert {
        "sealed-r1",
        "sealed-r2",
        "sealed-r3",
        "source",
        "rubric",
        "results",
        "control",
        "home-codex",
        "audit-wip",
        "common-git",
        "worktree-parent",
    } <= labels


def test_cap_gated_launcher_records_complete_lifecycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = demo.load_directional_manifest()
    session = manifest.sessions[0]
    ledger = demo.initialize_attempt_ledger(tmp_path / "ledger.jsonl", manifest)
    provider_cwd = tmp_path / "benchmark-task"
    provider_cwd.mkdir()
    command = (
        "/frozen/codex",
        "exec",
        "--ephemeral",
        "--json",
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
        "--model",
        "gpt-5.6-sol",
        "-c",
        'model_reasoning_effort="high"',
        "--sandbox",
        "workspace-write",
        "-C",
        str(provider_cwd),
        "-",
    )
    prepared = SimpleNamespace(
        arm_id=session.arm_id,
        fixture_id=session.fixture_id,
        provider_cwd=provider_cwd,
        codex=SimpleNamespace(
            executable="/frozen/codex",
            resolved_executable="/frozen/native-codex",
            entrypoint_sha256="1" * 64,
            native_binary_sha256="2" * 64,
        ),
    )
    profile = SimpleNamespace(argv=command, run_root=provider_cwd)
    launches: list[tuple[str, ...]] = []

    monkeypatch.setattr(demo, "verify_prepared_directional_arm", lambda _value: None)

    def fake_launch(
        _prepared: object,
        _profile: object,
        argv: tuple[str, ...],
        **_kwargs: object,
    ) -> demo.OneShotLaunchResult:
        launches.append(tuple(argv))
        return demo.OneShotLaunchResult(
            completed=subprocess.CompletedProcess(list(argv), 0, "ok", ""),
            original_sha256="2" * 64,
            one_shot_sha256="2" * 64,
            residue_free=True,
        )

    monkeypatch.setattr(demo, "_launch_directional_one_shot", fake_launch)
    result = demo.launch_directional_provider_session(
        ledger, manifest, session.session_id, prepared, profile, command
    )
    assert result.returncode == 0
    assert launches == [command]
    rows = demo.read_attempt_ledger(ledger)
    assert [row["kind"] for row in rows[-3:]] == [
        "reservation",
        "launch-started",
        "launch-completed",
    ]
    assert rows[-2]["provider_launched"] is True
    assert rows[-2]["original_entrypoint_sha256"] == "1" * 64
    assert rows[-2]["original_native_sha256"] == "2" * 64
    assert rows[-2]["one_shot_sha256"] == "2" * 64
    assert all(
        "/" not in value for value in rows[-2].values() if isinstance(value, str)
    )
    assert rows[-1]["provider_launched"] is True


def test_cap_gated_launcher_records_failed_terminal_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = demo.load_directional_manifest()
    session = manifest.sessions[0]
    ledger = demo.initialize_attempt_ledger(tmp_path / "ledger.jsonl", manifest)
    provider_cwd = tmp_path / "benchmark-task"
    provider_cwd.mkdir()
    command = (
        "/frozen/codex",
        "exec",
        "--ephemeral",
        "--json",
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
        "--model",
        "gpt-5.6-sol",
        "-c",
        'model_reasoning_effort="high"',
        "--sandbox",
        "workspace-write",
        "-C",
        str(provider_cwd),
        "-",
    )
    prepared = SimpleNamespace(
        arm_id=session.arm_id,
        fixture_id=session.fixture_id,
        provider_cwd=provider_cwd,
        codex=SimpleNamespace(
            executable="/frozen/codex",
            resolved_executable="/frozen/native-codex",
            entrypoint_sha256="1" * 64,
            native_binary_sha256="2" * 64,
        ),
    )
    profile = SimpleNamespace(argv=command, run_root=provider_cwd)
    monkeypatch.setattr(demo, "verify_prepared_directional_arm", lambda _value: None)
    monkeypatch.setattr(
        demo,
        "_launch_directional_one_shot",
        lambda _prepared, _profile, argv, **_kwargs: demo.OneShotLaunchResult(
            completed=subprocess.CompletedProcess(list(argv), 7, "", "bad"),
            original_sha256="2" * 64,
            one_shot_sha256="2" * 64,
            residue_free=True,
        ),
    )
    result = demo.launch_directional_provider_session(
        ledger, manifest, session.session_id, prepared, profile, command
    )
    assert result.returncode == 7
    rows = demo.read_attempt_ledger(ledger)
    assert rows[-1]["kind"] == "launch-failed"
    assert rows[-1]["failure"] == "nonzero"
    assert rows[-1]["returncode"] == 7


def test_cap_gated_launcher_rejects_non_provider_command_before_append(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = demo.load_directional_manifest()
    session = manifest.sessions[0]
    ledger = demo.initialize_attempt_ledger(tmp_path / "ledger.jsonl", manifest)
    before = ledger.read_bytes()
    provider_cwd = tmp_path / "benchmark-task"
    provider_cwd.mkdir()
    prepared = SimpleNamespace(
        arm_id=session.arm_id,
        fixture_id=session.fixture_id,
        provider_cwd=provider_cwd,
        codex=SimpleNamespace(
            executable="/frozen/codex",
            resolved_executable="/frozen/native-codex",
            entrypoint_sha256="1" * 64,
            native_binary_sha256="2" * 64,
        ),
    )
    command = ("/usr/bin/true",)
    profile = SimpleNamespace(argv=command, run_root=provider_cwd)
    launched = False

    monkeypatch.setattr(demo, "verify_prepared_directional_arm", lambda _value: None)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        nonlocal launched
        launched = True
        raise AssertionError("launch must not be reached")

    monkeypatch.setattr(demo, "_launch_directional_one_shot", forbidden)
    with pytest.raises(ValueError, match="binding"):
        demo.launch_directional_provider_session(
            ledger, manifest, session.session_id, prepared, profile, command
        )
    assert launched is False
    assert ledger.read_bytes() == before


def test_cap_gated_launcher_rejects_reservation_only_bypass_and_attempt_20(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = demo.load_directional_manifest()
    ledger = demo.initialize_attempt_ledger(tmp_path / "ledger.jsonl", manifest)
    first = manifest.sessions[0]
    demo.reserve_session(ledger, first.session_id)
    before = ledger.read_bytes()
    launched = False

    def forbidden(*_args: object, **_kwargs: object) -> None:
        nonlocal launched
        launched = True
        raise AssertionError("launch must not be reached")

    monkeypatch.setattr(demo, "_launch_directional_one_shot", forbidden)
    with pytest.raises(ValueError, match="reservation-only"):
        demo.launch_directional_provider_session(
            ledger,
            manifest,
            first.session_id,
            SimpleNamespace(),
            SimpleNamespace(),
            ("/frozen/codex", "exec"),
        )
    assert launched is False
    assert ledger.read_bytes() == before

    full = demo.initialize_attempt_ledger(tmp_path / "full.jsonl", manifest)
    for session in manifest.sessions:
        demo.reserve_session(full, session.session_id)
    full_before = full.read_bytes()
    with pytest.raises(ValueError, match="session cap"):
        demo.launch_directional_provider_session(
            full,
            manifest,
            "session-attempt-20",
            SimpleNamespace(),
            SimpleNamespace(),
            ("/frozen/codex", "exec"),
        )
    assert launched is False
    assert full.read_bytes() == full_before


def test_inner_workspace_write_state_is_closed_and_network_restricted(
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "benchmark-task"
    cwd.mkdir()
    state = demo.build_codex_inner_sandbox_state(cwd)
    assert set(state) == {
        "permissionProfile",
        "codexLinuxSandboxExe",
        "sandboxCwd",
        "useLegacyLandlock",
    }
    profile = state["permissionProfile"]
    assert profile["type"] == "managed"
    assert profile["network"] == "restricted"
    assert profile["file_system"]["type"] == "restricted"
    assert profile["file_system"]["entries"] == [
        {
            "path": {"type": "special", "value": {"kind": "root"}},
            "access": "read",
        },
        {
            "path": {"type": "path", "path": str(cwd.resolve())},
            "access": "write",
        },
    ]
    assert state["sandboxCwd"] == cwd.resolve().as_uri()


def test_one_shot_exec_handshake_is_closed() -> None:
    assert demo._one_shot_handshake_is_valid(
        ("/private/codex", "--version"), "codex-cli 0.147.0\n"
    )
    assert demo._one_shot_handshake_is_valid(
        ("/private/codex", "exec", "--json"),
        '{"type":"thread.started","thread_id":"frozen-thread"}\n',
    )
    assert not demo._one_shot_handshake_is_valid(
        ("/private/codex", "exec", "--json"),
        '{"type":"item.completed","thread_id":"frozen-thread"}\n',
    )
    assert not demo._one_shot_handshake_is_valid(
        ("/private/codex", "exec", "--json"), "not-json\n"
    )


def test_one_shot_copy_is_exact_private_and_cleanup_bound(tmp_path: Path) -> None:
    original = tmp_path / "codex-native"
    original.write_bytes(b"frozen-codex-binary")
    original.chmod(0o500)
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    frozen = SimpleNamespace(
        codex=SimpleNamespace(
            resolved_executable=str(original),
            native_binary_sha256=demo.sha256(original.read_bytes()).hexdigest(),
        )
    )
    one_shot = demo._create_directional_one_shot(frozen, output)
    assert one_shot.executable.read_bytes() == original.read_bytes()
    assert stat.S_IMODE(one_shot.executable.lstat().st_mode) == 0o500
    assert stat.S_IMODE(one_shot.private_root.lstat().st_mode) == 0o700
    assert one_shot.one_shot_sha256 == one_shot.original_sha256
    demo._cleanup_directional_one_shot(one_shot)
    assert not one_shot.executable.exists()
    assert not one_shot.private_root.exists()


def test_attempt_20_and_reservation_only_fail_before_one_shot_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = demo.load_directional_manifest()
    copied = False

    def forbidden(*_args: object, **_kwargs: object) -> None:
        nonlocal copied
        copied = True
        raise AssertionError("one-shot copy must not be created")

    monkeypatch.setattr(demo, "_create_directional_one_shot", forbidden)
    ledger = demo.initialize_attempt_ledger(tmp_path / "ledger.jsonl", manifest)
    demo.reserve_session(ledger, manifest.sessions[0].session_id)
    with pytest.raises(ValueError, match="reservation-only"):
        demo.launch_directional_provider_session(
            ledger,
            manifest,
            manifest.sessions[0].session_id,
            SimpleNamespace(),
            SimpleNamespace(),
            ("/frozen/codex", "exec"),
        )
    full = demo.initialize_attempt_ledger(tmp_path / "full.jsonl", manifest)
    for session in manifest.sessions:
        demo.reserve_session(full, session.session_id)
    with pytest.raises(ValueError, match="session cap"):
        demo.launch_directional_provider_session(
            full,
            manifest,
            "session-attempt-20",
            SimpleNamespace(),
            SimpleNamespace(),
            ("/frozen/codex", "exec"),
        )
    assert copied is False


@pytest.mark.parametrize(
    "argv",
    [
        (),
        ("/frozen/codex",),
        ("/frozen/codex", "--version"),
        ("/frozen/codex", "e"),
        ("/frozen/codex", "exec", "--json"),
        ("/frozen/codex", "review"),
        ("/frozen/codex", "resume", "thread-id"),
        ("/frozen/codex", "fork", "thread-id"),
        ("/frozen/codex", "cloud", "exec"),
        ("/frozen/codex", "completion", "zsh"),
        ("/frozen/codex", "arbitrary-command"),
        ("/other/codex", "exec", "--json"),
    ],
)
def test_low_level_one_shot_rejects_every_missing_capability_before_copy(
    monkeypatch: pytest.MonkeyPatch,
    argv: tuple[str, ...],
) -> None:
    copied = False

    def forbidden(*_args: object, **_kwargs: object) -> None:
        nonlocal copied
        copied = True
        raise AssertionError("copy must not be reached")

    monkeypatch.setattr(demo, "_create_directional_one_shot", forbidden)
    prepared = SimpleNamespace(codex=SimpleNamespace(executable="/frozen/codex"))
    with pytest.raises(ValueError, match="cap-gated"):
        demo._launch_directional_one_shot(prepared, SimpleNamespace(), argv)
    assert copied is False


def test_private_version_canary_is_exact_and_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_launch(
        _prepared: object,
        _profile: object,
        argv: tuple[str, ...],
    ) -> demo.OneShotLaunchResult:
        calls.append(argv)
        return demo.OneShotLaunchResult(
            completed=subprocess.CompletedProcess(argv, 0, "codex-cli 0.147.0\n", ""),
            original_sha256="1" * 64,
            one_shot_sha256="1" * 64,
            residue_free=True,
        )

    monkeypatch.setattr(demo, "_launch_directional_one_shot_after_gate", fake_launch)
    prepared = SimpleNamespace(codex=SimpleNamespace(executable="/frozen/codex"))
    result = demo._launch_directional_version_canary(prepared, SimpleNamespace())
    assert result.completed.returncode == 0
    assert calls == [("/frozen/codex", "--version")]

    for argv in (
        ("/frozen/codex",),
        ("/frozen/codex", "exec"),
        ("/other/codex", "--version"),
    ):
        with pytest.raises(ValueError, match="version canary"):
            demo._launch_directional_version_canary(
                prepared,
                SimpleNamespace(),
                argv=argv,
            )
    assert calls == [("/frozen/codex", "--version")]


def test_provider_environment_is_clean_and_inventory_bound(tmp_path: Path) -> None:
    environment = demo.create_clean_directional_environment(tmp_path / "env")
    assert Path(environment["HOME"]).stat().st_mode & 0o777 == 0o700
    assert Path(environment["CODEX_HOME"]).stat().st_mode & 0o777 == 0o700
    assert ".venv" not in environment["PATH"]
    assert "GIT_CONFIG_GLOBAL" in environment
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    digest = demo.freeze_global_inventory(environment)
    changed = {**environment, "CODEX_HOME": str(tmp_path / "changed")}
    with pytest.raises(ValueError, match="inventory"):
        demo.verify_global_inventory(changed, digest)


def test_fake_rehearsal_does_not_invoke_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called = False

    def forbidden(*_args: object, **_kwargs: object) -> None:
        nonlocal called
        called = True
        raise AssertionError("Provider launch is forbidden")

    monkeypatch.setattr(demo.subprocess, "Popen", forbidden)
    result = demo.run_fake_rehearsal(
        demo.load_directional_manifest(),
        workspace_root=tmp_path / "workspaces",
        output_root=tmp_path / "output",
        materialize_arms=False,
    )
    assert called is False
    assert result.prepared_workspaces == 15
    assert result.simulated_sessions == 19
    assert result.external_provider_calls == 0
    assert result.input_tokens is None
    assert result.output_tokens is None
    assert result.currency_cost is None
    rows = demo.read_attempt_ledger(result.ledger_path)
    reservations = [row for row in rows if row["kind"] == "reservation"]
    findings = [row for row in rows if row["kind"] == "expert-finding"]
    resumes = [row for row in rows if row["kind"] == "writer-resume"]
    assert len(reservations) == 19
    assert len(findings) == 4
    assert all(
        row["read_only"]
        and row["findings_only"]
        and row["candidate_writes"] == 0
        and row["child_subprocesses"] == 0
        and not row["provider_launched"]
        for row in findings
    )
    assert len(resumes) == 3
    assert all(
        row["same_live_session"] and not row["new_provider_session"] for row in resumes
    )


def test_fake_rehearsal_workspace_guards_reject_injections(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir(mode=0o700)
    (root / "benchmark-task").mkdir()
    (root / ".git").mkdir()
    demo.validate_rehearsal_workspace(root)
    for relative in ("AGENTS.md", ".codex", "fixture-link"):
        target = root / relative
        if relative == "fixture-link":
            os.symlink(root / "benchmark-task", target)
        elif relative.startswith("."):
            target.mkdir()
        else:
            target.write_text("injected", encoding="utf-8")
        with pytest.raises(ValueError):
            demo.validate_rehearsal_workspace(root)
        if target.is_symlink() or target.is_file():
            target.unlink()
        else:
            target.rmdir()


def test_fake_receipt_failure_and_infra_abort_are_distinct() -> None:
    run_id = "run-e1a68b37c685b81436b21438"
    cell = demo.build_terminal_failure_receipt(run_id, "model-nonzero")
    assert cell["matrix_action"] == "continue"
    infra = demo.build_terminal_failure_receipt(run_id, "network")
    assert infra["matrix_action"] == "abort-incomplete"
    assert infra["winner"] is None


def test_writer_resume_requires_all_experts_and_cannot_duplicate(
    tmp_path: Path,
) -> None:
    manifest = demo.load_directional_manifest()
    ledger = demo.initialize_attempt_ledger(tmp_path / "ledger.jsonl", manifest)
    first_a11 = next(run for run in manifest.runs if run.arm_id == "A11")
    for session in manifest.sessions[:15]:
        demo.reserve_session(ledger, session.session_id)
    with pytest.raises(ValueError, match="expert"):
        demo.append_writer_resume_event(ledger, manifest, first_a11.run_id)
    for session in manifest.sessions[15:]:
        demo.reserve_session(ledger, session.session_id)
        demo.append_fake_expert_finding(ledger, manifest, session.session_id)
    demo.append_writer_resume_event(ledger, manifest, first_a11.run_id)
    before = ledger.read_bytes()
    with pytest.raises(ValueError, match="duplicated"):
        demo.append_writer_resume_event(ledger, manifest, first_a11.run_id)
    assert ledger.read_bytes() == before


def test_expert_findings_reject_write_subagent_retry_and_duplicate(
    tmp_path: Path,
) -> None:
    manifest = demo.load_directional_manifest()
    ledger = demo.initialize_attempt_ledger(tmp_path / "ledger.jsonl", manifest)
    for session in manifest.sessions:
        demo.reserve_session(ledger, session.session_id)
    expert = next(session for session in manifest.sessions if session.kind != "writer")
    before = ledger.read_bytes()
    with pytest.raises(ValueError, match="write"):
        demo.append_fake_expert_finding(
            ledger, manifest, expert.session_id, candidate_writes=1
        )
    with pytest.raises(ValueError, match="subprocess"):
        demo.append_fake_expert_finding(
            ledger, manifest, expert.session_id, child_subprocesses=1
        )
    with pytest.raises(ValueError, match="retry"):
        demo.append_fake_expert_finding(ledger, manifest, expert.session_id, retry=True)
    assert ledger.read_bytes() == before
    demo.append_fake_expert_finding(ledger, manifest, expert.session_id)
    after = ledger.read_bytes()
    with pytest.raises(ValueError, match="duplicated"):
        demo.append_fake_expert_finding(ledger, manifest, expert.session_id)
    assert ledger.read_bytes() == after


def test_preflight_artifacts_are_closed_no_overwrite_and_no_price(
    tmp_path: Path,
) -> None:
    manifest = demo.load_directional_manifest()
    preflight = demo.build_directional_preflight(manifest, tmp_path / "future-results")
    paths = demo.write_preflight_artifacts(tmp_path / "evidence", preflight)
    assert len(paths) == 3
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    assert payload["provider_calls_started"] == 0
    assert payload["formal_authority_status"] == "NO-GO"
    budget = paths[2].read_text(encoding="utf-8")
    assert "未知" in budget
    assert "$" not in budget
    before = [path.read_bytes() for path in paths]
    with pytest.raises(FileExistsError):
        demo.write_preflight_artifacts(tmp_path / "evidence", preflight)
    assert [path.read_bytes() for path in paths] == before


def test_cli_validate_has_zero_provider_and_null_budget(tmp_path: Path) -> None:
    script = Path("scripts/ai_sdlc_v2_directional_demo.py").resolve()
    completed = subprocess.run(
        [
            os.environ.get("PYTHON", "python"),
            str(script),
            "validate",
            "--output-root",
            str(tmp_path / "output"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["status"] == "preflight-only"
    assert payload["provider_calls_started"] == 0
    assert payload["budget_confirmation"]["hard_session_cap"] == 19
    assert payload["budget_confirmation"]["token_estimate"] is None
    assert payload["budget_confirmation"]["currency_cost_estimate"] is None


@pytest.mark.parametrize(
    "mutation",
    [
        ("model", "other-model"),
        ("technical_retries", 1),
        ("max_provider_sessions", 20),
        ("actual_r2_authority_status", "GO"),
        ("evaluator_classification", "sealed-authority"),
    ],
)
def test_manifest_rejects_fairness_and_authority_mutations(
    mutation: tuple[str, object], tmp_path: Path
) -> None:
    raw = json.loads(demo.directional_manifest_path().read_text(encoding="utf-8"))
    raw[mutation[0]] = mutation[1]
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        demo.load_directional_manifest(path)


def test_manifest_rejects_run_reordering_and_extra_expert(tmp_path: Path) -> None:
    raw = json.loads(demo.directional_manifest_path().read_text(encoding="utf-8"))
    raw["runs"][0], raw["runs"][1] = raw["runs"][1], raw["runs"][0]
    path = tmp_path / "reordered.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        demo.load_directional_manifest(path)
    raw = json.loads(demo.directional_manifest_path().read_text(encoding="utf-8"))
    extra = dict(raw["sessions"][-1])
    extra["session_id"] = "session-000000000000000000000000"
    extra["ordinal"] = 20
    raw["sessions"].append(extra)
    path = tmp_path / "extra.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        demo.load_directional_manifest(path)


def test_partial_receipt_and_fake_usage_are_rejected() -> None:
    run_id = "run-e1a68b37c685b81436b21438"
    receipt = demo.build_fake_receipt(run_id, _metric(run_id))
    partial = dict(receipt)
    partial.pop("metric")
    with pytest.raises(ValueError, match="closed"):
        demo.validate_run_receipt(partial)
    fake_usage = _metric(run_id)
    fake_usage["input_tokens"] = 2
    fake_usage["output_tokens"] = 3
    fake_usage["total_tokens"] = 99
    with pytest.raises(ValueError, match="totals"):
        demo.validate_directional_metric(fake_usage)


def test_workspace_guards_reject_fifo_and_hardlink(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir(mode=0o700)
    (root / "benchmark-task").mkdir()
    (root / ".git").mkdir()
    source = root / "benchmark-task" / "input"
    source.write_text("frozen", encoding="utf-8")
    hardlink = root / "benchmark-task" / "hardlink"
    os.link(source, hardlink)
    with pytest.raises(ValueError, match="hardlink"):
        demo.validate_rehearsal_workspace(root)
    hardlink.unlink()
    fifo = root / "benchmark-task" / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="unsafe"):
        demo.validate_rehearsal_workspace(root)
    fifo.unlink()
    demo.validate_rehearsal_workspace(root)
