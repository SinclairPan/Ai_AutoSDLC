from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest
from typer.testing import CliRunner

import ai_sdlc.benefit_benchmark_fixtures as fixture_module
import ai_sdlc.benefit_sealed_materializer as materializer
import ai_sdlc.cli.benefit_evidence_cmd as benefit_evidence_cmd
from ai_sdlc.benefit_benchmark_fixtures import IsolationProbeResult
from ai_sdlc.benefit_sealed_materializer import (
    FINAL_LOCK_ID,
    CompiledMaterialization,
    FailureInjector,
    MaterializationError,
    MaterializationResult,
    MaterializerPolicy,
    compile_source_bundle,
    fingerprint_tree,
    materialize_with_policy,
    read_source_bundle,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPO_ROOT / "benchmarks" / "ai-sdlc-v2-benefits" / "fixtures"
REAL_FINAL_ISOLATION_CANARY = materializer._run_final_isolation_canary


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _security_scenario(**overrides: object) -> dict[str, object]:
    scenario: dict[str, object] = {
        "actor_id": "reviewer-a",
        "actor_tenant": "tenant-a",
        "roles": ["approver"],
        "request_id": "request-a",
        "request_tenant": "tenant-a",
        "requester_id": "requester-a",
        "expires_at": "2030-01-01T00:00:00+00:00",
        "now": "2029-01-01T00:00:00+00:00",
    }
    scenario.update(overrides)
    return scenario


def _test_browser_program() -> dict[str, object]:
    risks = [
        {
            "id": "TEST-R1",
            "name": "测试风险甲",
            "service": "test-api",
            "level": "high",
            "owner": "测试团队",
            "confirmed": False,
        },
        {
            "id": "TEST-R2",
            "name": "测试风险乙",
            "service": "test-query",
            "level": "medium",
            "owner": "平台测试",
            "confirmed": False,
        },
        {
            "id": "TEST-R3",
            "name": "测试风险丙",
            "service": "test-gateway",
            "level": "low",
            "owner": "运维测试",
            "confirmed": False,
        },
    ]

    def assertion(
        identifier: str,
        kind: str,
        target: object,
        expected: object,
        expose_as: str | None = None,
    ) -> dict[str, object]:
        return {
            "id": identifier,
            "kind": kind,
            "target": target,
            "expected": expected,
            "expose_as": expose_as,
        }

    return {
        "schema": "ai-sdlc-v2-frontend-browser-program/v1",
        "scenarios": [
            {
                "id": "test-only-scenario-a",
                "loader": {"outcomes": [{"type": "resolve", "value": risks}]},
                "confirmer": {"mode": "deferred"},
                "actions": [
                    {"op": "load", "handle": "load-a", "await": True},
                    {"op": "render", "filter": "high"},
                ],
                "assertions": [
                    assertion("a-state", "json-equal", ["state", "error"], None),
                    assertion("a-length", "json-length", ["state", "risks"], 3),
                    assertion(
                        "a-fields",
                        "dom-text-contains",
                        "body",
                        ["test-api", "测试团队", "high"],
                        "field_rendering",
                    ),
                    assertion("a-filter", "dom-count", "tbody tr", 1, "filtering"),
                    assertion("a-console", "console-empty", "console_errors", []),
                    assertion("a-a11y", "basic-a11y", "document", True),
                ],
            },
            {
                "id": "test-only-scenario-b",
                "loader": {
                    "outcomes": [
                        {"type": "reject"},
                        {"type": "resolve", "value": risks},
                    ]
                },
                "confirmer": {"mode": "deferred"},
                "actions": [
                    {"op": "load", "handle": "load-b1", "await": True},
                    {"op": "render", "filter": "all"},
                    {"op": "checkpoint", "name": "failed"},
                    {"op": "retry", "handle": "load-b2", "await": True},
                    {"op": "render", "filter": "all"},
                ],
                "assertions": [
                    assertion(
                        "b-failed",
                        "json-equal",
                        ["snapshots", "failed", "state", "error"],
                        "加载失败",
                    ),
                    assertion("b-final", "json-equal", ["state", "error"], None),
                    assertion("b-length", "json-length", ["state", "risks"], 3),
                ],
            },
            {
                "id": "test-only-scenario-c",
                "loader": {
                    "outcomes": [
                        {"type": "reject"},
                        {"type": "reject"},
                        {"type": "resolve", "value": risks},
                    ]
                },
                "confirmer": {"mode": "deferred"},
                "actions": [
                    {"op": "load", "handle": "load-c1", "await": True},
                    {"op": "retry", "handle": "load-c2", "await": True},
                    {"op": "checkpoint", "name": "failed-twice"},
                    {"op": "retry", "handle": "load-c3", "await": True},
                    {"op": "render", "filter": "all"},
                ],
                "assertions": [
                    assertion(
                        "c-failed",
                        "json-equal",
                        ["snapshots", "failed-twice", "state", "error"],
                        "加载失败",
                    ),
                    assertion("c-final", "json-equal", ["state", "error"], None),
                    assertion("c-length", "json-length", ["state", "risks"], 3),
                ],
            },
            {
                "id": "test-only-scenario-d",
                "loader": {
                    "outcomes": [
                        {"type": "deferred", "key": "older"},
                        {"type": "deferred", "key": "newer"},
                    ]
                },
                "confirmer": {"mode": "deferred"},
                "actions": [
                    {"op": "load", "handle": "load-d1", "await": False},
                    {"op": "load", "handle": "load-d2", "await": False},
                    {
                        "op": "resolve-load",
                        "key": "newer",
                        "value": [{**risks[2], "id": "TEST-NEW"}],
                    },
                    {"op": "await", "handle": "load-d2"},
                    {
                        "op": "resolve-load",
                        "key": "older",
                        "value": [{**risks[0], "id": "TEST-OLD"}],
                    },
                    {"op": "await", "handle": "load-d1"},
                    {"op": "render", "filter": "all"},
                ],
                "assertions": [
                    assertion(
                        "d-latest",
                        "json-equal",
                        ["state", "risks", 0, "id"],
                        "TEST-NEW",
                    )
                ],
            },
            {
                "id": "test-only-scenario-e",
                "loader": {"outcomes": [{"type": "resolve", "value": risks}]},
                "confirmer": {"mode": "deferred"},
                "actions": [
                    {"op": "load", "handle": "load-e", "await": True},
                    {
                        "op": "confirm",
                        "risk_id": "TEST-R1",
                        "handle": "confirm-e1",
                        "await": False,
                    },
                    {
                        "op": "confirm",
                        "risk_id": "TEST-R1",
                        "handle": "confirm-e2",
                        "await": False,
                    },
                    {"op": "release-confirms"},
                    {"op": "await-all", "handles": ["confirm-e1", "confirm-e2"]},
                    {"op": "render", "filter": "all"},
                ],
                "assertions": [
                    assertion("e-calls", "json-equal", ["confirm_calls"], 1),
                    assertion(
                        "e-confirmed",
                        "json-equal",
                        ["state", "risks", 0, "confirmed"],
                        True,
                    ),
                ],
            },
            {
                "id": "test-only-scenario-f",
                "loader": {
                    "outcomes": [{"type": "resolve", "value": {"unexpected": True}}]
                },
                "confirmer": {"mode": "deferred"},
                "actions": [
                    {"op": "load", "handle": "load-f", "await": True},
                    {"op": "render", "filter": "all"},
                ],
                "assertions": [
                    assertion("f-error", "json-equal", ["state", "error"], "加载失败"),
                    assertion("f-empty", "json-length", ["state", "risks"], 0),
                    assertion("f-a11y", "basic-a11y", "document", True),
                ],
            },
        ],
    }


def _source_bundle(lock_id: str) -> dict[str, object]:
    roots = [
        "tenant-isolation",
        "separation-of-duties",
        "request-lifecycle",
        "role-allowlist",
        "action-allowlist",
        "atomic-audit",
    ]
    scenarios = [
        _security_scenario(actor_tenant="tenant-b"),
        _security_scenario(actor_id="requester-a"),
        _security_scenario(status="approved"),
        _security_scenario(roles=["observer"]),
        _security_scenario(action="escalate"),
        _security_scenario(audit_mode="failing"),
    ]
    return {
        "schema": "ai-sdlc-v2-benefit-sealed-source/v1",
        "lock_id": lock_id,
        "intent_map": {
            "schema": "ai-sdlc-v2-benefit-intent-map/v2",
            "questions": {
                question_id: {
                    "answer": {"mode": f"test-only-answer-{index}"},
                    "delay_ms": 0,
                }
                for index, question_id in enumerate(
                    (
                        "release.emergency-authority",
                        "release.withdraw-boundary",
                        "notification.delivery-semantics",
                        "audit.retention-window",
                    ),
                    start=1,
                )
            },
            "approvals": ["design-contract", "frontend-solution"],
        },
        "payloads": {
            "requirement-contract-ambiguity": {
                "schema": "ai-sdlc-v2-benefit-sealed-evaluator/v2",
                "fixture_id": "requirement-contract-ambiguity",
                "criteria": [
                    {
                        "id": "test-r-literal",
                        "weight": 1,
                        "severity": "important",
                        "kind": "json_literal",
                        "path": ["decisions", "version_binding"],
                        "expected": "request-version",
                    },
                    {
                        "id": "test-r-enum",
                        "weight": 1,
                        "severity": "important",
                        "kind": "json_enum",
                        "path": ["open_questions", "status"],
                        "allowed": ["none", "blocked"],
                    },
                    {
                        "id": "test-r-subset",
                        "weight": 1,
                        "severity": "important",
                        "kind": "json_set_contains",
                        "path": ["state_machine", "terminal_states"],
                        "expected": ["approved", "rejected"],
                    },
                    {
                        "id": "test-r-relation",
                        "weight": 1,
                        "severity": "blocker",
                        "kind": "json_relation",
                        "path": ["failure_policy"],
                        "relation": "committed_fact_survives_notification_failure",
                    },
                    {
                        "id": "test-r-contradiction",
                        "weight": 1,
                        "severity": "important",
                        "kind": "json_no_contradiction",
                        "path": [],
                        "forbidden": ["test-only-forbidden-claim"],
                    },
                    {
                        "id": "test-r-command",
                        "weight": 1,
                        "severity": "important",
                        "kind": "verification_command",
                        "path": ["verification", "commands"],
                        "expected": ["python -m unittest -q"],
                    },
                ],
            },
            "frontend-recovery-delivery": {
                "schema": "ai-sdlc-v2-benefit-sealed-evaluator/v2",
                "fixture_id": "frontend-recovery-delivery",
                "held_out_variant_classes": [
                    "test-only-class-one",
                    "test-only-class-two",
                    "test-only-class-three",
                    "test-only-class-four",
                ],
                "browser_program": _test_browser_program(),
                "criteria": [
                    {
                        "id": "FRD-AC001",
                        "weight": 1,
                        "severity": "important",
                        "kind": "frontend_browser_suite",
                        "expected": {"behavior_checks": {"field_rendering": True}},
                    },
                    {
                        "id": "FRD-AC002",
                        "weight": 1,
                        "severity": "important",
                        "kind": "frontend_browser_suite",
                        "expected": {"behavior_checks": {"filtering": True}},
                    },
                    {
                        "id": "FRD-AC006",
                        "weight": 1,
                        "severity": "important",
                        "kind": "frontend_browser_suite",
                        "expected": {
                            "executed_with_real_browser": True,
                            "console_errors": [],
                            "basic_accessibility": True,
                        },
                    },
                    {
                        "id": "test-f-held-out",
                        "weight": 1,
                        "severity": "blocker",
                        "kind": "frontend_browser_suite",
                        "expected": {
                            "scenarios": {
                                "test-only-scenario-c": True,
                                "test-only-scenario-d": True,
                                "test-only-scenario-e": True,
                                "test-only-scenario-f": True,
                            }
                        },
                    },
                ],
            },
            "multi-tenant-security-review": {
                "schema": "ai-sdlc-v2-benefit-sealed-evaluator/v2",
                "fixture_id": "multi-tenant-security-review",
                "held_out_variant_classes": ["tenant", "time", "action", "audit"],
                "root_causes": roots,
                "criteria": [
                    {
                        "id": f"test-s-{index}",
                        "weight": 1,
                        "severity": "blocker",
                        "kind": "security_oracle",
                        "path": "benchmark-task/access_control.py",
                        "root_cause": root,
                        "scenario": scenario,
                        "expected": {
                            "allowed": False,
                            "status": scenario.get("status", "pending"),
                            "status_unchanged": True,
                        },
                    }
                    for index, (root, scenario) in enumerate(
                        zip(roots, scenarios, strict=True), start=1
                    )
                ],
            },
        },
    }


def _write_source(path: Path, lock_id: str) -> tuple[bytes, str]:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.parent.chmod(0o700)
    data = _canonical(_source_bundle(lock_id))
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, data)
    finally:
        os.close(descriptor)
    return data, sha256(data).hexdigest()


def _copy_repo_contract(root: Path) -> str:
    fixture_target = root / "benchmarks" / "ai-sdlc-v2-benefits" / "fixtures"
    fixture_target.parent.mkdir(parents=True)
    shutil.copytree(FIXTURE_ROOT, fixture_target)
    shutil.copy2(
        REPO_ROOT / "benchmarks" / "ai-sdlc-v2-benefits" / "protocol.json",
        fixture_target.parent / "protocol.json",
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "--all"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=materializer-test",
            "-c",
            "user.email=test@invalid",
            "commit",
            "-qm",
            "test contract",
        ],
        cwd=root,
        check=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _policy(tmp_path: Path) -> tuple[MaterializerPolicy, str, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    head = _copy_repo_contract(repo)
    protected = tmp_path / "protected"
    protected.mkdir(mode=0o700)
    old = protected / "old-lock"
    old.mkdir(mode=0o700)
    (old / "legacy.json").write_bytes(b'{"legacy":true}')
    source_base = tmp_path / "trusted-source-base"
    source_base.mkdir(mode=0o700)
    source_root = source_base / "sealed-source"
    source_root.mkdir(mode=0o700)
    source = source_root / "source.json"
    _, source_sha = _write_source(source, "test-lock-r1")
    canary_base = tmp_path / "isolation-canary"
    canary_base.mkdir(mode=0o700)
    canary_run = canary_base / "run"
    raw_results = canary_base / "raw-results"
    other_run = canary_base / "other-run"
    for path in (canary_run, raw_results, other_run):
        path.mkdir(mode=0o700)
    policy = MaterializerPolicy(
        repo_root=repo,
        target=protected / "test-lock-r1",
        trust_anchor=tmp_path,
        legacy_root=old,
        expected_legacy_inode=old.stat().st_ino,
        forbidden_roots=(repo, repo / ".git", repo / "benchmarks"),
        source_base=source_base,
        source_root=source_root,
        canary_run_root=canary_run,
        raw_results_root=raw_results,
        other_run_roots=(other_run,),
    )
    return policy, head, source


def _compile_for_test(
    policy: MaterializerPolicy, head: str, source: Path
) -> CompiledMaterialization:
    source_bytes = source.read_bytes()
    return compile_source_bundle(
        source_bytes,
        expected_source_sha256=sha256(source_bytes).hexdigest(),
        expected_head=head,
        policy=policy,
    )


def _materialize_for_test(
    source: Path, **kwargs: object
) -> materializer.MaterializationResult:
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        return materialize_with_policy(source_fd=descriptor, **kwargs)
    finally:
        os.close(descriptor)


def _read_source_for_test(source: Path, digest: str) -> bytes:
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        return read_source_bundle(source_fd=descriptor, expected_sha256=digest)
    finally:
        os.close(descriptor)


@pytest.fixture(autouse=True)
def _stable_unit_isolation_attestation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        materializer,
        "_run_final_isolation_canary",
        lambda _policy, *, pending_receipt_sha256: _canonical(
            {
                "schema": "ai-sdlc-v2-benefit-isolation-attestation/v1",
                "state": "validated",
                "pending_receipt_sha256": pending_receipt_sha256,
                "profile_sha256": "2" * 64,
                "checks": {
                    "direct": True,
                    "parent": True,
                    "symlink": True,
                    "hardlink": True,
                    "environment": True,
                    "other_run": True,
                    "add_dir": True,
                    "protected_roots": 2,
                },
            }
        ),
    )


def test_read_source_bundle_requires_canonical_secure_regular_file(
    tmp_path: Path,
) -> None:
    source = tmp_path / "secret" / "bundle.json"
    data, digest = _write_source(source, FINAL_LOCK_ID)

    assert _read_source_for_test(source, digest) == data

    source.chmod(0o640)
    with pytest.raises(MaterializationError, match="source-security"):
        _read_source_for_test(source, digest)
    source.chmod(0o600)
    alias = source.with_name("alias.json")
    os.link(source, alias)
    with pytest.raises(MaterializationError, match="source-security"):
        _read_source_for_test(source, digest)
    alias.unlink()
    symlink = source.with_name("link.json")
    symlink.symlink_to(source)
    with pytest.raises(OSError):
        _read_source_for_test(symlink, digest)
    with pytest.raises(MaterializationError, match="source-digest"):
        _read_source_for_test(source, "0" * 64)
    source.write_bytes(data + b"\n")
    source.chmod(0o600)
    with pytest.raises(MaterializationError, match="source-canonical"):
        _read_source_for_test(source, sha256(data + b"\n").hexdigest())


def test_read_source_bundle_accepts_owned_fd_without_reopening(tmp_path: Path) -> None:
    source = tmp_path / "secret" / "bundle.json"
    data, digest = _write_source(source, FINAL_LOCK_ID)
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        assert read_source_bundle(source_fd=descriptor, expected_sha256=digest) == data
    finally:
        os.close(descriptor)


def test_source_fd_alias_inside_repository_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy, _head, _source = _policy(tmp_path)
    source = policy.repo_root / "tracked-sealed-source.json"
    data = _canonical(_source_bundle(policy.target.name))
    source.write_bytes(data)
    source.chmod(0o600)
    digest = sha256(data).hexdigest()
    subprocess.run(["git", "add", "--all"], cwd=policy.repo_root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=materializer-test",
            "-c",
            "user.email=test@invalid",
            "commit",
            "-qm",
            "track forbidden source",
        ],
        cwd=policy.repo_root,
        check=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=policy.repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    monkeypatch.setattr(
        materializer, "_validate_scratch", lambda *_args, **_kwargs: None
    )
    try:
        with pytest.raises(MaterializationError, match="source-security"):
            materialize_with_policy(
                source_fd=descriptor,
                expected_source_sha256=digest,
                expected_head=head,
                expected_old_root_tree_sha256=fingerprint_tree(
                    policy.legacy_root
                ).sha256,
                policy=policy,
            )
    finally:
        os.close(descriptor)


def test_compile_rejects_open_or_incomplete_source_schema(tmp_path: Path) -> None:
    policy, head, source = _policy(tmp_path)
    bundle = _source_bundle("test-lock-r1")
    bundle["implementation_hint"] = "test-only-secret-hint"
    with pytest.raises(MaterializationError, match="source-schema"):
        compile_source_bundle(
            _canonical(bundle),
            expected_source_sha256=sha256(_canonical(bundle)).hexdigest(),
            expected_head=head,
            policy=policy,
        )
    del bundle["implementation_hint"]
    del bundle["payloads"]["frontend-recovery-delivery"]
    with pytest.raises(MaterializationError, match="source-schema"):
        compile_source_bundle(
            _canonical(bundle),
            expected_source_sha256=sha256(_canonical(bundle)).hexdigest(),
            expected_head=head,
            policy=policy,
        )


def test_fix_round3_production_sources_contain_no_hidden_browser_program() -> None:
    production = b"\n".join(
        path.read_bytes() for path in (REPO_ROOT / "src" / "ai_sdlc").rglob("*.py")
    )

    for hidden in (
        b"_FRONTEND_BROWSER_HARNESS",
        b"consecutive_failure_recovery",
        b"delayed_race",
        b"rapid_double_click",
        b"malformed_response",
        "鉴权回归".encode(),
    ):
        assert hidden not in production


@pytest.mark.parametrize(
    "mutation",
    [
        "open-action",
        "unknown-handle",
        "bad-assertion",
        "missing-production-scenario",
        "weak-public-ac",
    ],
)
def test_fix_round3_browser_program_is_recursively_closed_and_executable(
    tmp_path: Path, mutation: str
) -> None:
    policy, head, _source = _policy(tmp_path)
    bundle = _source_bundle(policy.target.name)
    scenario = bundle["payloads"]["frontend-recovery-delivery"]["browser_program"][
        "scenarios"
    ][0]
    if mutation == "open-action":
        scenario["actions"][0]["implementation_hint"] = "forbidden"
    elif mutation == "unknown-handle":
        scenario["actions"].insert(1, {"op": "await", "handle": "missing"})
    elif mutation == "bad-assertion":
        scenario["assertions"][0]["target"] = {"unexpected": True}
    elif mutation == "missing-production-scenario":
        bundle["payloads"]["frontend-recovery-delivery"]["browser_program"][
            "scenarios"
        ].pop()
    else:
        bundle["payloads"]["frontend-recovery-delivery"]["criteria"][0]["expected"] = {
            "behavior_checks": {"filtering": True}
        }
    encoded = _canonical(bundle)

    with pytest.raises(MaterializationError, match="source-schema"):
        compile_source_bundle(
            encoded,
            expected_source_sha256=sha256(encoded).hexdigest(),
            expected_head=head,
            policy=policy,
        )


@pytest.mark.parametrize(
    ("surface", "mutation"),
    [
        ("questions", "missing"),
        ("questions", "extra"),
        ("approvals", "missing"),
        ("approvals", "extra"),
    ],
)
def test_fix_round3_intent_exactly_matches_public_taxonomy(
    tmp_path: Path, surface: str, mutation: str
) -> None:
    policy, head, _source = _policy(tmp_path)
    bundle = _source_bundle("test-lock-r1")
    if surface == "questions" and mutation == "missing":
        bundle["intent_map"]["questions"].pop("audit.retention-window")
    elif surface == "questions":
        bundle["intent_map"]["questions"]["test-only-unrelated"] = {
            "answer": "irrelevant",
            "delay_ms": 0,
        }
    elif mutation == "missing":
        bundle["intent_map"]["approvals"].remove("design-contract")
    else:
        bundle["intent_map"]["approvals"].append("test-only-unrelated")
    encoded = _canonical(bundle)

    with pytest.raises(MaterializationError, match="source-schema"):
        compile_source_bundle(
            encoded,
            expected_source_sha256=sha256(encoded).hexdigest(),
            expected_head=head,
            policy=policy,
        )


def test_fix_round3_materialization_receipt_is_pending_before_final_canary(
    tmp_path: Path,
) -> None:
    policy, head, source = _policy(tmp_path)
    compiled = _compile_for_test(policy, head, source)
    receipt = json.loads(compiled.files["materialization-receipt.json"])

    assert receipt["publication_state"] == "published-pending-isolation"
    assert receipt["isolation_probe_state"] == "pending"


def test_fix_round3_trusted_source_base_is_literal_and_cli_is_fd_only() -> None:
    assert (
        Path("/private/tmp/ai-sdlc-v2-benefit-source")
        == materializer.TRUSTED_SOURCE_BASE
    )
    help_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_sdlc",
            "benefit-evidence",
            "materialize-sealed",
            "--help",
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "COLUMNS": "200"},
        capture_output=True,
        text=True,
        check=False,
    )
    combined = help_result.stdout + help_result.stderr

    assert help_result.returncode == 0
    assert "--sealed-source-fd" in combined
    assert "--sealed-source " not in combined


def test_fix_round3_tree_fingerprint_binds_root_metadata(tmp_path: Path) -> None:
    root = tmp_path / "fingerprint-root"
    root.mkdir(mode=0o700)
    (root / "value.txt").write_text("stable")
    before = fingerprint_tree(root)

    root.chmod(0o750)
    after = fingerprint_tree(root)

    assert after != before


def test_fix_round3_tree_fingerprint_binds_child_identity_and_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "fingerprint-root"
    root.mkdir(mode=0o700)
    child = root / "value.txt"
    child.write_text("stable")
    before = fingerprint_tree(root)
    child.chmod(0o640)
    assert fingerprint_tree(root) != before
    child.chmod(0o644)
    child.write_text("changed")
    changed = fingerprint_tree(root)
    child.rename(root / "renamed.txt")
    assert fingerprint_tree(root) != changed

    real_lstat = Path.lstat

    def simulated_owner(path: Path) -> os.stat_result:
        observed = real_lstat(path)
        if path == root:
            values = list(observed)
            values[4] = observed.st_uid + 1
            return os.stat_result(values)
        return observed

    owner_before = fingerprint_tree(root)
    monkeypatch.setattr(Path, "lstat", simulated_owner)
    assert fingerprint_tree(root) != owner_before


def test_fix_round3_source_must_be_direct_child_of_strict_trusted_root(
    tmp_path: Path,
) -> None:
    policy, head, _source = _policy(tmp_path)
    outside = tmp_path / "candidate" / "source.json"
    data, digest = _write_source(outside, policy.target.name)
    assert data
    descriptor = os.open(outside, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with pytest.raises(MaterializationError, match="source-security"):
            materialize_with_policy(
                source_fd=descriptor,
                expected_source_sha256=digest,
                expected_head=head,
                expected_old_root_tree_sha256=fingerprint_tree(
                    policy.legacy_root
                ).sha256,
                policy=policy,
            )
    finally:
        os.close(descriptor)
    policy.source_root.chmod(0o750)
    with pytest.raises(MaterializationError, match="source-security"):
        _materialize_for_test(
            _source,
            expected_source_sha256=sha256(_source.read_bytes()).hexdigest(),
            expected_head=head,
            expected_old_root_tree_sha256=fingerprint_tree(policy.legacy_root).sha256,
            policy=policy,
        )


def test_fix_round3_target_parent_requires_exact_private_mode(tmp_path: Path) -> None:
    policy, head, source = _policy(tmp_path)
    policy.target.parent.chmod(0o750)
    with pytest.raises(MaterializationError, match="target-ancestor"):
        _materialize_for_test(
            source,
            expected_source_sha256=sha256(source.read_bytes()).hexdigest(),
            expected_head=head,
            expected_old_root_tree_sha256=fingerprint_tree(policy.legacy_root).sha256,
            policy=policy,
        )


def test_fix_round3_final_canary_uses_exact_published_and_protected_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy, _head, _source = _policy(tmp_path)
    policy.target.mkdir(mode=0o700)
    captured: list[object] = []

    def successful_probe(profile: object) -> IsolationProbeResult:
        captured.append(profile)
        return IsolationProbeResult(
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            (("protected-root-0", True), ("protected-root-1", True)),
        )

    monkeypatch.setattr(materializer, "probe_provider_isolation", successful_probe)
    data = REAL_FINAL_ISOLATION_CANARY(policy, pending_receipt_sha256="3" * 64)
    attestation = json.loads(data)
    profile = captured[0]

    assert profile.sealed_root == policy.target.resolve()
    assert profile.control_root == policy.repo_root.resolve()
    assert policy.source_root.resolve() in profile.protected_roots
    assert (policy.repo_root / ".git").resolve() in profile.protected_roots
    assert profile.raw_results_root == policy.raw_results_root.resolve()
    assert profile.other_run_roots == tuple(
        path.resolve() for path in policy.other_run_roots
    )
    assert attestation["state"] == "validated"
    assert attestation["pending_receipt_sha256"] == "3" * 64


def _default_policy_with_test_canary_roots(
    tmp_path: Path,
) -> MaterializerPolicy:
    base = materializer.default_policy()
    protected = tmp_path / "protected"
    target = protected / "target"
    canary = tmp_path / "canary"
    run = canary / "run"
    raw = canary / "raw-results"
    other = canary / "other-run"
    protected.mkdir(mode=0o700)
    target.mkdir(mode=0o700)
    for path in (canary, run, raw, other):
        path.mkdir(mode=0o700)
    return replace(
        base,
        target=target,
        canary_run_root=run,
        raw_results_root=raw,
        other_run_roots=(other,),
    )


def test_fix_round5_default_policy_final_profile_derives_all_git_surfaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = _default_policy_with_test_canary_roots(tmp_path)
    captured: list[object] = []

    def successful_probe(profile: object) -> IsolationProbeResult:
        captured.append(profile)
        return IsolationProbeResult(
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            tuple(
                (f"protected-root-{index}", True)
                for index in range(
                    5 + len(profile.protected_roots) + len(profile.other_run_roots)
                )
            ),
        )

    monkeypatch.setattr(materializer, "probe_provider_isolation", successful_probe)
    data = REAL_FINAL_ISOLATION_CANARY(policy, pending_receipt_sha256="4" * 64)
    attestation = json.loads(data)
    profile = captured[0]
    expected = set(fixture_module.derive_repo_git_surfaces(policy.repo_root))

    assert expected <= set(profile.protected_roots)
    assert policy.source_root.resolve() in profile.protected_roots
    assert attestation["state"] == "validated"


@pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS Seatbelt")
def test_fix_round5_system_default_policy_denies_each_exact_git_surface(
    tmp_path: Path,
) -> None:
    policy = _default_policy_with_test_canary_roots(tmp_path)
    profile = materializer._build_final_isolation_profile(policy)
    surfaces = fixture_module.derive_repo_git_surfaces(policy.repo_root)

    try:
        attestation = json.loads(
            REAL_FINAL_ISOLATION_CANARY(policy, pending_receipt_sha256="5" * 64)
        )
        denied = {
            surface: fixture_module._sandbox_denies(profile, surface)
            for surface in surfaces
        }
    except (MaterializationError, RuntimeError) as error:
        if "sandbox_apply: Operation not permitted" in str(error.__cause__):
            pytest.skip("nested Seatbelt is unavailable inside the test sandbox")
        raise

    assert set(surfaces) <= set(profile.protected_roots)
    assert all(denied.values())
    assert attestation["state"] == "validated"


def test_fix_round3_canary_failure_quarantines_published_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy, head, source = _policy(tmp_path)
    monkeypatch.setattr(materializer, "_validate_scratch", lambda *_a, **_k: None)
    monkeypatch.setattr(
        materializer,
        "_run_final_isolation_canary",
        lambda *_a, **_k: (_ for _ in ()).throw(
            MaterializationError("isolation-canary")
        ),
    )

    with pytest.raises(MaterializationError, match="isolation-canary"):
        _materialize_for_test(
            source,
            expected_source_sha256=sha256(source.read_bytes()).hexdigest(),
            expected_head=head,
            expected_old_root_tree_sha256=fingerprint_tree(policy.legacy_root).sha256,
            policy=policy,
        )

    assert not policy.target.exists()
    assert not list(policy.target.parent.glob(f".{policy.target.name}.quarantine-*"))


@pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS Seatbelt")
def test_fix_round3_system_publication_requires_real_final_path_canary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy, head, source = _policy(tmp_path)
    monkeypatch.setattr(materializer, "_validate_scratch", lambda *_a, **_k: None)
    monkeypatch.setattr(
        materializer, "_run_final_isolation_canary", REAL_FINAL_ISOLATION_CANARY
    )
    try:
        result = _materialize_for_test(
            source,
            expected_source_sha256=sha256(source.read_bytes()).hexdigest(),
            expected_head=head,
            expected_old_root_tree_sha256=fingerprint_tree(policy.legacy_root).sha256,
            policy=policy,
        )
    except MaterializationError as error:
        if error.code == "isolation-canary":
            pytest.skip("nested sandbox blocks exact final isolation profile")
        raise

    attestation = json.loads(
        (policy.target / "isolation-attestation.json").read_bytes()
    )
    assert attestation["state"] == "validated"
    assert attestation["checks"] == {
        "direct": True,
        "parent": True,
        "symlink": True,
        "hardlink": True,
        "environment": True,
        "other_run": True,
        "add_dir": True,
        "protected_roots": 7,
    }
    assert (
        result.file_sha256["isolation-attestation.json"]
        == sha256(
            (policy.target / "isolation-attestation.json").read_bytes()
        ).hexdigest()
    )


def test_fix_round3_source_root_drift_aborts_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy, head, source = _policy(tmp_path)

    def mutate_source_root(*_args: object, **_kwargs: object) -> None:
        extra = policy.source_root / "unexpected.json"
        extra.write_text("{}")
        extra.chmod(0o600)

    monkeypatch.setattr(materializer, "_validate_scratch", mutate_source_root)
    with pytest.raises(MaterializationError, match="source-raced"):
        _materialize_for_test(
            source,
            expected_source_sha256=sha256(source.read_bytes()).hexdigest(),
            expected_head=head,
            expected_old_root_tree_sha256=fingerprint_tree(policy.legacy_root).sha256,
            policy=policy,
        )

    assert not policy.target.exists()


@pytest.mark.parametrize(
    ("fixture_id", "criterion_index", "surface"),
    [
        ("multi-tenant-security-review", 0, "scenario"),
        ("multi-tenant-security-review", 0, "expected"),
        ("frontend-recovery-delivery", 0, "expected"),
    ],
)
def test_compile_rejects_unknown_recursive_evaluator_fields(
    tmp_path: Path, fixture_id: str, criterion_index: int, surface: str
) -> None:
    policy, head, _source = _policy(tmp_path)
    bundle = _source_bundle("test-lock-r1")
    criterion = bundle["payloads"][fixture_id]["criteria"][criterion_index]
    criterion[surface]["implementation_hint"] = "must-never-cross-compiler"
    encoded = _canonical(bundle)

    with pytest.raises(MaterializationError, match="source-schema"):
        compile_source_bundle(
            encoded,
            expected_source_sha256=sha256(encoded).hexdigest(),
            expected_head=head,
            policy=policy,
        )


@pytest.mark.parametrize("mutation", ["wrong-root", "wrong-status", "invalid-time"])
def test_compile_rejects_incoherent_security_oracles(
    tmp_path: Path, mutation: str
) -> None:
    policy, head, _source = _policy(tmp_path)
    bundle = _source_bundle("test-lock-r1")
    criteria = bundle["payloads"]["multi-tenant-security-review"]["criteria"]
    if mutation == "wrong-root":
        criteria[0]["scenario"]["actor_tenant"] = criteria[0]["scenario"][
            "request_tenant"
        ]
    elif mutation == "wrong-status":
        criteria[2]["expected"]["status"] = "pending"
    else:
        criteria[2]["scenario"]["now"] = "not-a-time"
    encoded = _canonical(bundle)

    with pytest.raises(MaterializationError, match="source-schema"):
        compile_source_bundle(
            encoded,
            expected_source_sha256=sha256(encoded).hexdigest(),
            expected_head=head,
            policy=policy,
        )


def test_compile_binds_all_receipt_and_commitment_inputs(tmp_path: Path) -> None:
    policy, head, source = _policy(tmp_path)
    compiled = _compile_for_test(policy, head, source)
    receipt = json.loads(compiled.files["materialization-receipt.json"])
    commitments = json.loads(compiled.files["candidate-commitments.json"])
    manifest = json.loads(compiled.files["sealed-manifest.json"])

    assert set(manifest) == {"schema", "lock_id", "entries", "intent_map"}
    assert [item["fixture_id"] for item in manifest["entries"]] == list(
        materializer.FIXTURE_IDS
    )
    assert set(receipt) == materializer.RECEIPT_KEYS
    assert set(commitments) == materializer.CANDIDATE_COMMITMENT_KEYS
    assert receipt["source_head"] == head
    assert receipt["target_lock_id"] == policy.target.name
    assert receipt["source_bundle_sha256"] == sha256(source.read_bytes()).hexdigest()
    assert receipt["materializer_sha256"] == materializer.materializer_sha256()
    assert (
        receipt["fixture_manifest_sha256"]
        == sha256(
            (
                policy.repo_root
                / "benchmarks/ai-sdlc-v2-benefits/fixtures/manifest.json"
            ).read_bytes()
        ).hexdigest()
    )
    assert (
        receipt["evidence_contract_sha256"]
        == sha256(
            (
                policy.repo_root
                / "benchmarks/ai-sdlc-v2-benefits/fixtures/evidence-contract.template.json"
            ).read_bytes()
        ).hexdigest()
    )
    assert (
        receipt["candidate_commitments_sha256"]
        == sha256(compiled.files["candidate-commitments.json"]).hexdigest()
    )


def test_materializer_rejects_head_dirty_protocol_and_provider_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy, head, source = _policy(tmp_path)
    _, digest = source.read_bytes(), sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(
        materializer, "_validate_scratch", lambda *_args, **_kwargs: None
    )

    with pytest.raises(MaterializationError, match="source-head"):
        _materialize_for_test(
            source,
            expected_source_sha256=digest,
            expected_head="0" * 40,
            expected_old_root_tree_sha256=fingerprint_tree(policy.legacy_root).sha256,
            policy=policy,
        )
    dirty = policy.repo_root / "dirty.txt"
    dirty.write_text("dirty")
    with pytest.raises(MaterializationError, match="source-tree"):
        _materialize_for_test(
            source,
            expected_source_sha256=digest,
            expected_head=head,
            expected_old_root_tree_sha256=fingerprint_tree(policy.legacy_root).sha256,
            policy=policy,
        )
    dirty.unlink()
    protocol_path = policy.repo_root / "benchmarks/ai-sdlc-v2-benefits/protocol.json"
    protocol = json.loads(protocol_path.read_text())
    protocol["execution_lock"]["fixture_commitment"] = "bound"
    protocol_path.write_text(json.dumps(protocol))
    subprocess.run(["git", "add", "--all"], cwd=policy.repo_root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@invalid",
            "commit",
            "-qm",
            "invalid protocol",
        ],
        cwd=policy.repo_root,
        check=True,
    )
    invalid_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=policy.repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    with pytest.raises(MaterializationError, match="protocol-state"):
        _materialize_for_test(
            source,
            expected_source_sha256=digest,
            expected_head=invalid_head,
            expected_old_root_tree_sha256=fingerprint_tree(policy.legacy_root).sha256,
            policy=policy,
        )
    protocol["execution_lock"]["fixture_commitment"] = "pending-unbound"
    protocol_path.write_text(json.dumps(protocol))
    subprocess.run(["git", "add", "--all"], cwd=policy.repo_root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@invalid",
            "commit",
            "-qm",
            "restore protocol",
        ],
        cwd=policy.repo_root,
        check=True,
    )
    valid_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=policy.repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    results = policy.repo_root / "benchmarks/ai-sdlc-v2-benefits/results"
    results.mkdir()
    with pytest.raises(MaterializationError, match="provider-state"):
        _materialize_for_test(
            source,
            expected_source_sha256=digest,
            expected_head=valid_head,
            expected_old_root_tree_sha256=fingerprint_tree(policy.legacy_root).sha256,
            policy=policy,
        )


def test_target_policy_rejects_existing_leaf_untrusted_parent_and_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy, head, source = _policy(tmp_path)
    digest = sha256(source.read_bytes()).hexdigest()
    old_digest = fingerprint_tree(policy.legacy_root).sha256
    monkeypatch.setattr(
        materializer, "_validate_scratch", lambda *_args, **_kwargs: None
    )
    policy.target.mkdir()
    with pytest.raises(MaterializationError, match="target-exists"):
        _materialize_for_test(
            source,
            expected_source_sha256=digest,
            expected_head=head,
            expected_old_root_tree_sha256=old_digest,
            policy=policy,
        )
    policy.target.rmdir()
    policy.target.parent.chmod(0o777)
    with pytest.raises(MaterializationError, match="target-ancestor"):
        _materialize_for_test(
            source,
            expected_source_sha256=digest,
            expected_head=head,
            expected_old_root_tree_sha256=old_digest,
            policy=policy,
        )
    policy.target.parent.chmod(0o700)
    overlap = MaterializerPolicy(
        repo_root=policy.repo_root,
        target=policy.target,
        trust_anchor=policy.trust_anchor,
        legacy_root=policy.legacy_root,
        expected_legacy_inode=policy.expected_legacy_inode,
        forbidden_roots=(*policy.forbidden_roots, policy.target.parent),
        source_base=policy.source_base,
        source_root=policy.source_root,
        canary_run_root=policy.canary_run_root,
        raw_results_root=policy.raw_results_root,
        other_run_roots=policy.other_run_roots,
    )
    with pytest.raises(MaterializationError, match="target-overlap"):
        _materialize_for_test(
            source,
            expected_source_sha256=digest,
            expected_head=head,
            expected_old_root_tree_sha256=old_digest,
            policy=overlap,
        )


def test_successful_publication_is_exclusive_closed_and_mode_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy, head, source = _policy(tmp_path)
    digest = sha256(source.read_bytes()).hexdigest()
    old_before = fingerprint_tree(policy.legacy_root)
    monkeypatch.setattr(
        materializer, "_validate_scratch", lambda *_args, **_kwargs: None
    )

    result = _materialize_for_test(
        source,
        expected_source_sha256=digest,
        expected_head=head,
        expected_old_root_tree_sha256=old_before.sha256,
        policy=policy,
    )

    assert result.lock_id == policy.target.name
    assert result.target_inode == policy.target.stat().st_ino
    assert stat.S_IMODE(policy.target.stat().st_mode) == 0o700
    assert set(path.name for path in policy.target.iterdir()) == set(result.file_sha256)
    for path in policy.target.iterdir():
        assert path.is_file() and not path.is_symlink()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert path.stat().st_nlink == 1
        assert result.file_sha256[path.name] == sha256(path.read_bytes()).hexdigest()
    assert fingerprint_tree(policy.legacy_root) == old_before
    receipt = json.loads((policy.target / "materialization-receipt.json").read_text())
    assert receipt["publication_state"] == "published-pending-isolation"
    attestation = json.loads((policy.target / "isolation-attestation.json").read_text())
    assert attestation["state"] == "validated"
    assert (
        attestation["pending_receipt_sha256"]
        == result.file_sha256["materialization-receipt.json"]
    )
    assert not list(policy.target.parent.glob(f".{policy.target.name}.staging-*"))
    assert not list(policy.target.parent.glob(f".{policy.target.name}.quarantine-*"))


def test_publication_retries_short_writes_until_every_byte_is_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy, head, source = _policy(tmp_path)
    digest = sha256(source.read_bytes()).hexdigest()
    old_before = fingerprint_tree(policy.legacy_root)
    monkeypatch.setattr(
        materializer, "_validate_scratch", lambda *_args, **_kwargs: None
    )
    real_write = os.write
    short_write_count = 0

    def short_write(descriptor: int, value: bytes | memoryview) -> int:
        nonlocal short_write_count
        if len(value) > 7:
            short_write_count += 1
            return real_write(descriptor, value[:7])
        return real_write(descriptor, value)

    monkeypatch.setattr(materializer.os, "write", short_write)
    result = _materialize_for_test(
        source,
        expected_source_sha256=digest,
        expected_head=head,
        expected_old_root_tree_sha256=old_before.sha256,
        policy=policy,
    )

    assert short_write_count > 0
    for name, expected_digest in result.file_sha256.items():
        assert (
            sha256((policy.target / name).read_bytes()).hexdigest() == expected_digest
        )


def test_staging_creation_is_relative_to_the_pinned_parent_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy, head, source = _policy(tmp_path)
    digest = sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(
        materializer, "_validate_scratch", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        materializer.tempfile,
        "mkdtemp",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("path-based mkdtemp is forbidden during publication")
        ),
    )

    result = _materialize_for_test(
        source,
        expected_source_sha256=digest,
        expected_head=head,
        expected_old_root_tree_sha256=fingerprint_tree(policy.legacy_root).sha256,
        policy=policy,
    )

    assert result.target_inode == policy.target.stat().st_ino


def test_parent_path_replacement_after_pin_fails_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy, head, source = _policy(tmp_path)
    digest = sha256(source.read_bytes()).hexdigest()
    moved_parent = tmp_path / "protected-original"

    def replace_parent(*_args: object, **_kwargs: object) -> None:
        policy.target.parent.rename(moved_parent)
        policy.target.parent.mkdir(mode=0o700)

    monkeypatch.setattr(materializer, "_validate_scratch", replace_parent)

    with pytest.raises(MaterializationError, match="target-raced"):
        _materialize_for_test(
            source,
            expected_source_sha256=digest,
            expected_head=head,
            expected_old_root_tree_sha256=fingerprint_tree(policy.legacy_root).sha256,
            policy=policy,
        )

    assert not policy.target.exists()
    assert not list(moved_parent.glob(f".{policy.target.name}.staging-*"))


def test_repository_preflight_precedes_protected_source_read(tmp_path: Path) -> None:
    policy, _head, _source = _policy(tmp_path)

    with pytest.raises(MaterializationError, match="source-head"):
        materialize_with_policy(
            source_fd=-1,
            expected_source_sha256="0" * 64,
            expected_head="0" * 40,
            expected_old_root_tree_sha256=fingerprint_tree(policy.legacy_root).sha256,
            policy=policy,
        )


@pytest.mark.parametrize(
    "failure_point",
    [
        *[
            f"write:{name}"
            for name in (
                "intent-map.json",
                "requirement-contract-ambiguity.sealed.json",
                "frontend-recovery-delivery.sealed.json",
                "multi-tenant-security-review.sealed.json",
                "sealed-manifest.json",
                "candidate-commitments.json",
                "materialization-receipt.json",
            )
        ],
        *[
            f"fsync-file:{name}"
            for name in (
                "intent-map.json",
                "requirement-contract-ambiguity.sealed.json",
                "frontend-recovery-delivery.sealed.json",
                "multi-tenant-security-review.sealed.json",
                "sealed-manifest.json",
                "candidate-commitments.json",
                "materialization-receipt.json",
            )
        ],
        "fsync-staging-dir",
        "rename",
    ],
)
def test_prepublish_failure_removes_only_owned_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    policy, head, source = _policy(tmp_path)
    digest = sha256(source.read_bytes()).hexdigest()
    old_before = fingerprint_tree(policy.legacy_root)
    unrelated = policy.target.parent / ".unrelated-staging"
    unrelated.mkdir()
    monkeypatch.setattr(
        materializer, "_validate_scratch", lambda *_args, **_kwargs: None
    )

    with pytest.raises(MaterializationError, match="injected-failure"):
        _materialize_for_test(
            source,
            expected_source_sha256=digest,
            expected_head=head,
            expected_old_root_tree_sha256=old_before.sha256,
            policy=policy,
            failure_injector=FailureInjector(failure_point),
        )

    assert not policy.target.exists()
    assert unrelated.is_dir()
    assert not list(policy.target.parent.glob(f".{policy.target.name}.staging-*"))
    assert fingerprint_tree(policy.legacy_root) == old_before


@pytest.mark.parametrize(
    "failure_point",
    [
        "fsync-parent",
        "postverify",
        "isolation-canary",
        "write-attestation",
        "fsync-attestation",
        "fsync-final-dir",
    ],
)
def test_postpublish_failure_quarantines_only_matching_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    policy, head, source = _policy(tmp_path)
    digest = sha256(source.read_bytes()).hexdigest()
    old_before = fingerprint_tree(policy.legacy_root)
    monkeypatch.setattr(
        materializer, "_validate_scratch", lambda *_args, **_kwargs: None
    )

    with pytest.raises(MaterializationError, match="injected-failure"):
        _materialize_for_test(
            source,
            expected_source_sha256=digest,
            expected_head=head,
            expected_old_root_tree_sha256=old_before.sha256,
            policy=policy,
            failure_injector=FailureInjector(failure_point),
        )

    assert not policy.target.exists()
    assert not list(policy.target.parent.glob(f".{policy.target.name}.quarantine-*"))
    assert fingerprint_tree(policy.legacy_root) == old_before


def test_cleanup_failure_is_explicit_no_go(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy, head, source = _policy(tmp_path)
    digest = sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(
        materializer, "_validate_scratch", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        materializer,
        "_quarantine_published",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("cleanup failed")),
    )

    with pytest.raises(MaterializationError, match="cleanup-failed"):
        _materialize_for_test(
            source,
            expected_source_sha256=digest,
            expected_head=head,
            expected_old_root_tree_sha256=fingerprint_tree(policy.legacy_root).sha256,
            policy=policy,
            failure_injector=FailureInjector("postverify"),
        )


def test_renameatx_unavailable_is_fail_closed_before_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy, head, source = _policy(tmp_path)
    digest = sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(
        materializer, "_validate_scratch", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(materializer, "_rename_exclusive", None)

    with pytest.raises(MaterializationError, match="rename-unavailable"):
        _materialize_for_test(
            source,
            expected_source_sha256=digest,
            expected_head=head,
            expected_old_root_tree_sha256=fingerprint_tree(policy.legacy_root).sha256,
            policy=policy,
        )
    assert not policy.target.exists()


def test_old_root_inode_and_tree_are_required_and_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy, head, source = _policy(tmp_path)
    digest = sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(
        materializer, "_validate_scratch", lambda *_args, **_kwargs: None
    )
    with pytest.raises(MaterializationError, match="legacy-tree"):
        _materialize_for_test(
            source,
            expected_source_sha256=digest,
            expected_head=head,
            expected_old_root_tree_sha256="0" * 64,
            policy=policy,
        )
    wrong_inode = MaterializerPolicy(
        repo_root=policy.repo_root,
        target=policy.target,
        trust_anchor=policy.trust_anchor,
        legacy_root=policy.legacy_root,
        expected_legacy_inode=policy.expected_legacy_inode + 1,
        forbidden_roots=policy.forbidden_roots,
        source_base=policy.source_base,
        source_root=policy.source_root,
        canary_run_root=policy.canary_run_root,
        raw_results_root=policy.raw_results_root,
        other_run_roots=policy.other_run_roots,
    )
    with pytest.raises(MaterializationError, match="legacy-inode"):
        _materialize_for_test(
            source,
            expected_source_sha256=digest,
            expected_head=head,
            expected_old_root_tree_sha256=fingerprint_tree(policy.legacy_root).sha256,
            policy=wrong_inode,
        )


def test_materializer_errors_do_not_echo_source_path_or_plaintext(
    tmp_path: Path,
) -> None:
    source = tmp_path / "secret-name-do-not-echo.json"
    secret = "test-only-plaintext-do-not-echo"
    source.write_text(secret)
    source.chmod(0o600)

    with pytest.raises(MaterializationError) as captured:
        _read_source_for_test(source, "0" * 64)

    rendered = str(captured.value)
    assert str(source) not in rendered
    assert secret not in rendered


def test_production_materializer_contains_no_test_payload_plaintext() -> None:
    production = (REPO_ROOT / "src/ai_sdlc/benefit_sealed_materializer.py").read_text(
        encoding="utf-8"
    )
    cli = (REPO_ROOT / "src/ai_sdlc/cli/benefit_evidence_cmd.py").read_text(
        encoding="utf-8"
    )
    for marker in (
        "test-only-fixed",
        "test-only-forbidden-claim",
        "test-only-sequential-recovery",
        "test-only-response-order",
        "test-only-submit-race",
        "test-only-shape-guard",
        "test-only-plaintext-do-not-echo",
    ):
        assert marker not in production
        assert marker not in cli


def test_cli_fingerprint_is_read_only_and_materialize_help_is_closed() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ai_sdlc", "benefit-evidence", "fingerprint-old-root"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert set(payload) == {"inode", "tree_sha256"}
    help_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_sdlc",
            "benefit-evidence",
            "materialize-sealed",
            "--help",
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "COLUMNS": "200"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert help_result.returncode == 0, help_result.stderr
    combined = help_result.stdout + help_result.stderr
    for option in (
        "--sealed-source-fd",
        "--expected-source-sha256",
        "--expected-head",
        "--lock-id",
        "--expected-old-root-tree-sha256",
    ):
        assert option in combined
    assert "--sealed-source " not in combined
    assert "--target" not in combined


def test_cli_redacts_unexpected_materializer_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "never-echo-source.json"
    source.write_bytes(b"{}")
    source.chmod(0o600)
    marker = "never-echo-internal-plaintext"
    monkeypatch.setattr(
        benefit_evidence_cmd,
        "materialize_sealed_bundle",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError(marker)),
    )

    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        result = CliRunner().invoke(
            benefit_evidence_cmd.benefit_evidence_app,
            [
                "materialize-sealed",
                "--sealed-source-fd",
                str(descriptor),
                "--expected-source-sha256",
                "0" * 64,
                "--expected-head",
                "0" * 40,
                "--lock-id",
                FINAL_LOCK_ID,
                "--expected-old-root-tree-sha256",
                "0" * 64,
            ],
        )
    finally:
        os.close(descriptor)

    assert result.exit_code == 1
    rendered = result.stdout + result.stderr
    assert marker not in rendered
    assert marker not in str(result.exception)
    assert json.loads(rendered) == {"status": "no-go", "code": "internal-error"}


def test_fix_round3_cli_success_is_opaque(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "source.json"
    source.write_bytes(b"{}")
    source.chmod(0o600)
    monkeypatch.setattr(
        benefit_evidence_cmd,
        "materialize_sealed_bundle",
        lambda **_kwargs: MaterializationResult(
            FINAL_LOCK_ID,
            987654321,
            {
                "materialization-receipt.json": "4" * 64,
                "isolation-attestation.json": "5" * 64,
            },
        ),
    )
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        result = CliRunner().invoke(
            benefit_evidence_cmd.benefit_evidence_app,
            [
                "materialize-sealed",
                "--sealed-source-fd",
                str(descriptor),
                "--expected-source-sha256",
                "0" * 64,
                "--expected-head",
                "0" * 40,
                "--lock-id",
                FINAL_LOCK_ID,
                "--expected-old-root-tree-sha256",
                "0" * 64,
            ],
        )
    finally:
        os.close(descriptor)

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "status": "materialized",
        "count": 2,
        "receipt_sha256": "5" * 64,
    }
    assert "987654321" not in result.stdout
    assert FINAL_LOCK_ID not in result.stdout


@pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS Seatbelt/browser")
def test_compiled_scratch_validation_is_deterministic_and_non_delivery(
    tmp_path: Path,
) -> None:
    policy, head, source = _policy(tmp_path)
    compiled = _compile_for_test(policy, head, source)
    scratch = tmp_path / "scratch"

    try:
        materializer._validate_scratch(
            compiled,
            scratch_parent=scratch,
            fixture_root=FIXTURE_ROOT,
        )
    except (PermissionError, RuntimeError) as error:
        if "sandbox_apply: Operation not permitted" in str(error):
            pytest.skip("nested sandbox blocks exact evaluator profile")
        raise
