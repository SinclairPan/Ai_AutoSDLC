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


def _successful_runtime_probe() -> materializer._RuntimeCapsuleProbe:
    names = (
        "runtime_read",
        "runtime_exec",
        "runtime_append_denied",
        "runtime_create_denied",
        "runtime_chmod_denied",
        "runtime_rename_denied",
    )
    transcript = tuple(
        (name, 0 if index < 2 else 1, "1" * 64, "2" * 64)
        for index, name in enumerate(names)
    )
    return materializer._RuntimeCapsuleProbe(
        True,
        True,
        True,
        True,
        True,
        True,
        transcript,
        sha256(_canonical([list(item) for item in transcript])).hexdigest(),
    )


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
        "schema": "ai-sdlc-v2-benefit-sealed-source/v2",
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
    predecessor = tmp_path / "predecessor-r2"
    predecessor.mkdir(mode=0o700)
    (predecessor / "receipt.json").write_bytes(b'{"r2":true}')
    predecessor_fingerprint = fingerprint_tree(predecessor)
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
        expected_legacy_tree_sha256=fingerprint_tree(old).sha256,
        forbidden_roots=(repo, repo / ".git", repo / "benchmarks"),
        source_base=source_base,
        source_root=source_root,
        canary_run_root=canary_run,
        raw_results_root=raw_results,
        other_run_roots=(other_run,),
        immutable_roots=(
            materializer.ImmutableRoot(
                predecessor,
                predecessor_fingerprint.inode,
                predecessor_fingerprint.sha256,
                "validated-r2",
            ),
        ),
    )
    return policy, head, source


def _predecessor_tree_sha256(policy: MaterializerPolicy) -> str:
    return next(
        item.tree_sha256
        for item in policy.immutable_roots
        if item.label == "validated-r2"
    )


def _bind_synthetic_repo_to_actual_r2(
    policy: MaterializerPolicy,
) -> str:
    protocol = REPO_ROOT / "benchmarks/ai-sdlc-v2-benefits/protocol.json"
    target = policy.repo_root / "benchmarks/ai-sdlc-v2-benefits/protocol.json"
    shutil.copy2(protocol, target)
    if subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=policy.repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout:
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
                "bind exact r2 predecessor",
            ],
            cwd=policy.repo_root,
            check=True,
        )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=policy.repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit_synthetic_repo(policy: MaterializerPolicy, message: str) -> str:
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
            message,
        ],
        cwd=policy.repo_root,
        check=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=policy.repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_capture_repo_bindings_accepts_only_exact_bound_r2_predecessor(
    tmp_path: Path,
) -> None:
    policy, _head, _source = _policy(tmp_path)
    head = _bind_synthetic_repo_to_actual_r2(policy)

    bindings = materializer._capture_repo_bindings(head, policy)

    assert bindings.source_head == head
    assert (
        bindings.source_tree_sha
        == subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=policy.repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "pending",
        "partial-bound",
        "fixture-tree-wrong",
        "fixture-commitment-wrong",
        "evidence-sha-wrong",
        "evidence-commitment-wrong",
        "fixture-pair-mismatch",
        "evidence-pair-mismatch",
        "protocol-digest",
        "sealed-authority",
        "already-r3",
    ),
)
def test_capture_repo_bindings_rejects_nonexact_r2_predecessor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    policy, _head, _source = _policy(tmp_path)
    _bind_synthetic_repo_to_actual_r2(policy)
    protocol_path = policy.repo_root / "benchmarks/ai-sdlc-v2-benefits/protocol.json"
    authority_path = (
        policy.repo_root
        / "benchmarks/ai-sdlc-v2-benefits/fixtures/sealed-commitments.json"
    )
    if mutation in {
        "pending",
        "partial-bound",
        "fixture-tree-wrong",
        "fixture-commitment-wrong",
        "evidence-sha-wrong",
        "evidence-commitment-wrong",
        "fixture-pair-mismatch",
        "evidence-pair-mismatch",
        "protocol-digest",
    }:
        protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
        lock = protocol["execution_lock"]
        if mutation == "pending":
            for field in (
                "fixture_tree_sha256",
                "fixture_commitment",
                "evidence_contract_sha256",
                "evidence_contract_commitment",
            ):
                lock[field] = "pending-unbound"
        elif mutation == "partial-bound":
            lock["fixture_tree_sha256"] = "pending-unbound"
        elif mutation == "protocol-digest":
            lock["writer_timeout_seconds"] += 1
        else:
            field = {
                "fixture-tree-wrong": "fixture_tree_sha256",
                "fixture-commitment-wrong": "fixture_commitment",
                "evidence-sha-wrong": "evidence_contract_sha256",
                "evidence-commitment-wrong": "evidence_contract_commitment",
                "fixture-pair-mismatch": "fixture_commitment",
                "evidence-pair-mismatch": "evidence_contract_commitment",
            }[mutation]
            lock[field] = "0" * 64
        protocol_path.write_text(json.dumps(protocol), encoding="utf-8")
    else:
        authority = json.loads(authority_path.read_text(encoding="utf-8"))
        if mutation == "already-r3":
            authority["lock_id"] = "v2-benefits-20260819-r3"
        else:
            authority["source_bundle_sha256"] = "0" * 64
        authority_path.write_text(json.dumps(authority), encoding="utf-8")
    head = _commit_synthetic_repo(policy, f"invalid predecessor {mutation}")
    touched: list[str] = []
    monkeypatch.setattr(
        materializer,
        "_read_source_record",
        lambda **_kwargs: touched.append("source-read"),
    )
    monkeypatch.setattr(
        materializer,
        "_open_trusted_parent",
        lambda _policy: touched.append("target-write"),
    )

    with pytest.raises(MaterializationError, match="protocol-predecessor"):
        materializer.materialize_with_policy(
            source_fd=-1,
            expected_source_sha256="0" * 64,
            expected_head=head,
            expected_predecessor_r2_tree_sha256=_predecessor_tree_sha256(policy),
            policy=policy,
        )

    assert touched == []


def test_pending_predecessor_fails_before_source_read_or_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy, _head, _source = _policy(tmp_path)
    protocol_path = policy.repo_root / "benchmarks/ai-sdlc-v2-benefits/protocol.json"
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    for field in (
        "fixture_tree_sha256",
        "fixture_commitment",
        "evidence_contract_sha256",
        "evidence_contract_commitment",
    ):
        protocol["execution_lock"][field] = "pending-unbound"
    protocol_path.write_text(json.dumps(protocol), encoding="utf-8")
    head = _commit_synthetic_repo(policy, "pending predecessor")
    touched: list[str] = []
    monkeypatch.setattr(
        materializer,
        "_read_source_record",
        lambda **_kwargs: touched.append("source-read"),
    )
    monkeypatch.setattr(
        materializer,
        "_open_trusted_parent",
        lambda _policy: touched.append("target-write"),
    )

    with pytest.raises(MaterializationError, match="protocol-predecessor"):
        materializer.materialize_with_policy(
            source_fd=-1,
            expected_source_sha256="0" * 64,
            expected_head=head,
            expected_predecessor_r2_tree_sha256=_predecessor_tree_sha256(policy),
            policy=policy,
        )

    assert touched == []


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
        lambda _policy, *, pending_receipt_sha256, **_kwargs: _canonical(
            {
                "schema": "ai-sdlc-v2-benefit-isolation-attestation/v2",
                "state": "validated",
                "pending_receipt_sha256": pending_receipt_sha256,
                "evaluator_python_runtime_sha256": fixture_module.evaluator_runtime_identity_sha256(
                    fixture_module.evaluator_python_runtime_identity()
                ),
                "evaluator_runtime_capsule_sha256": fixture_module.evaluator_runtime_capsule_v2_sha256(
                    fixture_module.evaluator_runtime_capsule_v2_manifest(
                        fixture_module.EVALUATOR_PYTHON,
                        str(
                            fixture_module.evaluator_python_runtime_identity()[
                                "version"
                            ]
                        ),
                    )
                ),
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
                    "write_protected_roots": 2,
                    "runtime_read": True,
                    "runtime_exec": True,
                    "runtime_append_denied": True,
                    "runtime_create_denied": True,
                    "runtime_chmod_denied": True,
                    "runtime_rename_denied": True,
                    "runtime_probe_transcript_sha256": "3" * 64,
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
                expected_predecessor_r2_tree_sha256=_predecessor_tree_sha256(policy),
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


def test_r3_compiler_rejects_v1_source_schema(tmp_path: Path) -> None:
    policy, head, _source = _policy(tmp_path)
    bundle = _source_bundle(policy.target.name)
    bundle["schema"] = "ai-sdlc-v2-benefit-sealed-source/v1"
    encoded = _canonical(bundle)

    with pytest.raises(MaterializationError, match="source-schema"):
        compile_source_bundle(
            encoded,
            expected_source_sha256=sha256(encoded).hexdigest(),
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
                expected_predecessor_r2_tree_sha256=_predecessor_tree_sha256(policy),
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
            expected_predecessor_r2_tree_sha256=_predecessor_tree_sha256(policy),
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
            expected_predecessor_r2_tree_sha256=_predecessor_tree_sha256(policy),
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
    monkeypatch.setattr(
        materializer,
        "_probe_runtime_capsule_access",
        lambda *_args, **_kwargs: _successful_runtime_probe(),
    )
    data = REAL_FINAL_ISOLATION_CANARY(policy, pending_receipt_sha256="3" * 64)
    attestation = json.loads(data)
    profile = captured[0]

    assert profile.sealed_root == policy.target.resolve()
    assert profile.control_root == policy.repo_root.resolve()
    assert policy.source_root.resolve() in profile.protected_roots
    assert all(
        source.resolve() in profile.protected_roots
        for source in policy.prior_source_roots
    )
    assert all(
        item.path.resolve() in profile.protected_roots
        for item in policy.immutable_roots
    )
    assert (policy.repo_root / ".git").resolve() in profile.protected_roots
    assert profile.raw_results_root == policy.raw_results_root.resolve()
    assert profile.other_run_roots == tuple(
        path.resolve() for path in policy.other_run_roots
    )
    assert attestation["state"] == "validated"
    assert attestation["pending_receipt_sha256"] == "3" * 64
    assert {
        key: attestation["checks"][key]
        for key in (
            "runtime_read",
            "runtime_exec",
            "runtime_append_denied",
            "runtime_create_denied",
            "runtime_chmod_denied",
            "runtime_rename_denied",
        )
    } == {
        "runtime_read": True,
        "runtime_exec": True,
        "runtime_append_denied": True,
        "runtime_create_denied": True,
        "runtime_chmod_denied": True,
        "runtime_rename_denied": True,
    }


@pytest.mark.parametrize("mutation", ("unexecuted", "fake-digest", "false-check"))
def test_runtime_probe_attestation_is_closed_and_cannot_be_faked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    policy, _head, _source = _policy(tmp_path)
    policy.target.mkdir(mode=0o700)
    monkeypatch.setattr(
        materializer,
        "probe_provider_isolation",
        lambda _profile: IsolationProbeResult(
            True, True, True, True, True, True, True, ()
        ),
    )
    proof = _successful_runtime_probe()
    if mutation == "unexecuted":
        proof = replace(
            proof,
            transcript=(),
            transcript_sha256=sha256(_canonical([])).hexdigest(),
        )
    elif mutation == "fake-digest":
        proof = replace(proof, transcript_sha256="0" * 64)
    else:
        proof = replace(proof, runtime_rename_denied=False)
    monkeypatch.setattr(
        materializer,
        "_probe_runtime_capsule_access",
        lambda *_args, **_kwargs: proof,
    )

    with pytest.raises(MaterializationError, match="runtime-capsule-probe"):
        REAL_FINAL_ISOLATION_CANARY(policy, pending_receipt_sha256="6" * 64)

    assert not list(policy.canary_run_root.parent.glob(".runtime-write-*"))


def test_runtime_probe_executes_all_six_checks_and_rejects_permissive_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy, _head, _source = _policy(tmp_path)
    policy.target.mkdir(mode=0o700)
    launches: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        materializer,
        "probe_provider_isolation",
        lambda _profile: IsolationProbeResult(
            True, True, True, True, True, True, True, ()
        ),
    )

    def permissive_launch(
        _profile: object, argv: tuple[str, ...]
    ) -> subprocess.CompletedProcess[str]:
        launches.append(tuple(argv))
        if "read_bytes" in " ".join(argv):
            stdout = "1\n"
        elif "runtime-exec-ok" in " ".join(argv):
            stdout = "runtime-exec-ok\n"
        else:
            stdout = ""
        return subprocess.CompletedProcess(list(argv), 0, stdout, "")

    monkeypatch.setattr(materializer, "run_provider_isolated", permissive_launch)

    with pytest.raises(MaterializationError, match="runtime-capsule-probe"):
        REAL_FINAL_ISOLATION_CANARY(policy, pending_receipt_sha256="7" * 64)

    assert len(launches) == 6
    assert not list(policy.canary_run_root.parent.glob(".runtime-write-*"))


def test_runtime_canary_cleanup_failure_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy, _head, _source = _policy(tmp_path)
    policy.target.mkdir(mode=0o700)
    monkeypatch.setattr(
        materializer,
        "probe_provider_isolation",
        lambda _profile: IsolationProbeResult(
            True, True, True, True, True, True, True, ()
        ),
    )
    monkeypatch.setattr(
        materializer,
        "_probe_runtime_capsule_access",
        lambda *_args, **_kwargs: _successful_runtime_probe(),
    )
    monkeypatch.setattr(
        materializer,
        "_cleanup_runtime_write_canary",
        lambda _canary: (_ for _ in ()).throw(
            MaterializationError("runtime-canary-cleanup")
        ),
    )

    with pytest.raises(MaterializationError, match="runtime-canary-cleanup"):
        REAL_FINAL_ISOLATION_CANARY(policy, pending_receipt_sha256="8" * 64)


def test_runtime_canary_creation_failure_cleans_inode_bound_partial_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy, _head, _source = _policy(tmp_path)
    monkeypatch.setattr(
        materializer,
        "_write_all",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("short create")),
    )

    with pytest.raises(MaterializationError, match="runtime-canary-root"):
        materializer._create_runtime_write_canary(policy)

    assert not list(policy.canary_run_root.parent.glob(".runtime-write-*"))


@pytest.mark.parametrize("failure", ("chmod", "first-lstat", "directory-open"))
def test_runtime_canary_early_failure_leaves_no_residue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    policy, _head, _source = _policy(tmp_path)
    real_chmod = Path.chmod
    real_lstat = Path.lstat
    real_open = os.open
    failed = False

    def chmod(path: Path, mode: int) -> None:
        nonlocal failed
        if failure == "chmod" and path.name.startswith(".runtime-write-"):
            failed = True
            raise OSError("injected chmod failure")
        real_chmod(path, mode)

    def lstat(path: Path) -> os.stat_result:
        nonlocal failed
        if (
            failure == "first-lstat"
            and path.name.startswith(".runtime-write-")
            and not failed
        ):
            failed = True
            raise OSError("injected first lstat failure")
        return real_lstat(path)

    def open_file(path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal failed
        if (
            failure == "directory-open"
            and str(path).split("/")[-1].startswith(".runtime-write-")
            and not failed
        ):
            failed = True
            raise OSError("injected directory open failure")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(Path, "chmod", chmod)
    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(materializer.os, "open", open_file)

    with pytest.raises(MaterializationError):
        materializer._create_runtime_write_canary(policy)

    assert failed is True
    assert not list(policy.canary_run_root.parent.glob(".runtime-write-*"))


def test_incomplete_canary_cleanup_refuses_replacement_without_deleting_it(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".runtime-write-replaced"
    root.mkdir(mode=0o700)
    original = root.lstat()
    identity = (
        original.st_dev,
        original.st_ino,
        original.st_uid,
        stat.S_IMODE(original.st_mode),
    )
    moved = tmp_path / "original"
    root.rename(moved)
    root.mkdir(mode=0o700)
    marker = root / "do-not-delete"
    marker.write_text("replacement", encoding="utf-8")

    with pytest.raises(OSError):
        materializer._cleanup_incomplete_runtime_canary(root, identity)

    assert marker.read_text(encoding="utf-8") == "replacement"


@pytest.mark.parametrize("drift_at", ("probe", "cleanup"))
def test_runtime_capsule_full_post_probe_equality_is_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift_at: str
) -> None:
    policy, _head, _source = _policy(tmp_path)
    policy.target.mkdir(mode=0o700)
    runtime = fixture_module.evaluator_python_runtime_identity()
    capsule = fixture_module.evaluator_runtime_capsule_v2_manifest(
        Path(str(runtime["path"])), str(runtime["version"])
    )
    drifted_capsule = json.loads(json.dumps(capsule))
    selected = next(
        index
        for index, entry in enumerate(drifted_capsule["entries"])
        if entry["type"] == "file" and str(entry["path"]).endswith(".py")
    )
    other = next(
        index
        for index, entry in enumerate(drifted_capsule["entries"])
        if index != selected
        and entry["type"] == "file"
        and str(entry["path"]).endswith(".py")
    )
    drifted_capsule["entries"][other]["sha256"] = "0" * 64
    drifted = False

    monkeypatch.setattr(
        materializer,
        "evaluator_python_runtime_identity",
        lambda **_kwargs: runtime,
    )
    monkeypatch.setattr(
        materializer,
        "evaluator_runtime_capsule_v2_manifest",
        lambda *_args, **_kwargs: drifted_capsule if drifted else capsule,
    )
    monkeypatch.setattr(
        materializer,
        "probe_provider_isolation",
        lambda _profile: IsolationProbeResult(
            True, True, True, True, True, True, True, ()
        ),
    )

    def successful_probe(*_args: object, **_kwargs: object) -> object:
        nonlocal drifted
        if drift_at == "probe":
            drifted = True
        return _successful_runtime_probe()

    monkeypatch.setattr(materializer, "_probe_runtime_capsule_access", successful_probe)
    real_cleanup = materializer._cleanup_runtime_write_canary

    def cleanup(canary: object) -> None:
        nonlocal drifted
        real_cleanup(canary)
        if drift_at == "cleanup":
            drifted = True

    monkeypatch.setattr(materializer, "_cleanup_runtime_write_canary", cleanup)

    with pytest.raises(MaterializationError, match="runtime-capsule-drift"):
        REAL_FINAL_ISOLATION_CANARY(policy, pending_receipt_sha256="9" * 64)

    assert not list(policy.canary_run_root.parent.glob(".runtime-write-*"))


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
    source = tmp_path / "source-r2"
    prior_source = tmp_path / "source-r1"
    immutable = tmp_path / "invalid-r1"
    protected.mkdir(mode=0o700)
    target.mkdir(mode=0o700)
    for path in (canary, run, raw, other, source, prior_source, immutable):
        path.mkdir(mode=0o700)
    immutable_fingerprint = materializer.fingerprint_tree(immutable)
    return replace(
        base,
        target=target,
        source_root=source,
        prior_source_roots=(prior_source,),
        immutable_roots=(
            materializer.ImmutableRoot(
                immutable,
                immutable_fingerprint.inode,
                immutable_fingerprint.sha256,
                "invalid-r1",
            ),
        ),
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
    monkeypatch.setattr(
        materializer,
        "_probe_runtime_capsule_access",
        lambda *_args, **_kwargs: _successful_runtime_probe(),
    )
    data = REAL_FINAL_ISOLATION_CANARY(policy, pending_receipt_sha256="4" * 64)
    attestation = json.loads(data)
    profile = captured[0]
    expected = set(fixture_module.derive_repo_git_surfaces(policy.repo_root))

    assert expected <= set(profile.protected_roots)
    assert policy.source_root.resolve() in profile.protected_roots
    assert policy.prior_source_roots[0].resolve() in profile.protected_roots
    assert policy.immutable_roots[0].path.resolve() in profile.protected_roots
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
            expected_predecessor_r2_tree_sha256=_predecessor_tree_sha256(policy),
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
            expected_predecessor_r2_tree_sha256=_predecessor_tree_sha256(policy),
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
    expected_profile = materializer._build_final_isolation_profile(policy)
    expected_protected_roots = len(
        tuple(
            dict.fromkeys(
                (
                    expected_profile.sealed_root,
                    expected_profile.sealed_root.parent,
                    expected_profile.control_root,
                    expected_profile.raw_results_root,
                    *expected_profile.protected_roots,
                    *expected_profile.other_run_roots,
                )
            )
        )
    )
    assert attestation["checks"] == {
        "direct": True,
        "parent": True,
        "symlink": True,
        "hardlink": True,
        "environment": True,
        "other_run": True,
        "add_dir": True,
        "protected_roots": expected_protected_roots,
        "write_protected_roots": 2,
        "runtime_read": True,
        "runtime_exec": True,
        "runtime_append_denied": True,
        "runtime_create_denied": True,
        "runtime_chmod_denied": True,
        "runtime_rename_denied": True,
        "runtime_probe_transcript_sha256": attestation["checks"][
            "runtime_probe_transcript_sha256"
        ],
    }
    assert len(attestation["checks"]["runtime_probe_transcript_sha256"]) == 64
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
            expected_predecessor_r2_tree_sha256=_predecessor_tree_sha256(policy),
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

    assert manifest["schema"] == "ai-sdlc-v2-benefit-sealed-manifest/v5"
    assert commitments["schema"] == "ai-sdlc-v2-benefit-candidate-commitments/v4"
    assert receipt["schema"] == "ai-sdlc-v2-benefit-materialization-receipt/v4"
    assert (
        commitments["evaluator_runtime_capsule"]["schema"]
        == "ai-sdlc-v2-benefit-runtime-capsule/v2"
    )
    assert set(manifest) == {
        "schema",
        "lock_id",
        "entries",
        "intent_map",
        "evaluator_python_runtime_sha256",
        "evaluator_runtime_capsule_sha256",
    }
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
    runtime_sha256 = fixture_module.evaluator_runtime_identity_sha256(
        commitments["evaluator_python_runtime"]
    )
    assert runtime_sha256 == manifest["evaluator_python_runtime_sha256"]
    assert runtime_sha256 == commitments["evaluator_python_runtime_sha256"]
    assert runtime_sha256 == receipt["evaluator_python_runtime_sha256"]
    capsule_sha256 = fixture_module.evaluator_runtime_capsule_v2_sha256(
        commitments["evaluator_runtime_capsule"]
    )
    assert capsule_sha256 == manifest["evaluator_runtime_capsule_sha256"]
    assert capsule_sha256 == commitments["evaluator_runtime_capsule_sha256"]
    assert capsule_sha256 == receipt["evaluator_runtime_capsule_sha256"]


@pytest.mark.parametrize("mutation", ("r2-schema", "v1-capsule"))
def test_r3_candidate_validation_rejects_stale_runtime_authority(
    tmp_path: Path, mutation: str
) -> None:
    policy, head, source = _policy(tmp_path)
    compiled = _compile_for_test(policy, head, source)
    files = dict(compiled.files)
    candidate = json.loads(files["candidate-commitments.json"])
    if mutation == "r2-schema":
        candidate["schema"] = "ai-sdlc-v2-benefit-candidate-commitments/v3"
    else:
        candidate["evaluator_runtime_capsule"]["schema"] = (
            "ai-sdlc-v2-benefit-runtime-capsule/v1"
        )
    files["candidate-commitments.json"] = _canonical(candidate)
    receipt = json.loads(files["materialization-receipt.json"])
    receipt["candidate_commitments_sha256"] = sha256(
        files["candidate-commitments.json"]
    ).hexdigest()
    files["materialization-receipt.json"] = _canonical(receipt)
    stale = CompiledMaterialization(
        files=files,
        bindings=compiled.bindings,
        source_bundle_sha256=compiled.source_bundle_sha256,
    )
    candidate_root = tmp_path / "stale-candidate"
    materializer._write_plain_files(candidate_root, files)

    with pytest.raises(MaterializationError, match="candidate-commitments"):
        materializer._validate_candidate_commitments(candidate_root, stale)


def test_fix_round7_final_profile_write_protects_runtime_capsule(
    tmp_path: Path,
) -> None:
    policy = _default_policy_with_test_canary_roots(tmp_path)
    profile = materializer._build_final_isolation_profile(policy)
    capsule = fixture_module.evaluator_runtime_capsule_v2_manifest(
        fixture_module.EVALUATOR_PYTHON,
        str(fixture_module.evaluator_python_runtime_identity()["version"]),
    )

    assert Path(str(capsule["root"])) in profile.write_protected_roots
    assert f'(deny file-write* (subpath "{capsule["root"]}"))' in profile.sandbox_text
    assert f'(deny file-read* file-write* (subpath "{capsule["root"]}"))' not in (
        profile.sandbox_text
    )


def test_r3_production_target_is_monotonic_and_refuses_predecessors() -> None:
    policy = materializer.default_policy()

    assert materializer.FINAL_LOCK_ID == "v2-benefits-20260819-r3"
    assert policy.target == Path(
        "/private/tmp/ai-sdlc-v2-benefit-evaluator/v2-benefits-20260819-r3"
    )
    assert (
        Path("/private/tmp/ai-sdlc-v2-benefit-evaluator/v2-benefits-20260819-r1")
        == materializer.INVALID_R1_ROOT
    )
    assert policy.target != materializer.INVALID_R1_ROOT
    assert (
        policy.expected_legacy_tree_sha256 == materializer.EXPECTED_LEGACY_TREE_SHA256
    )
    assert policy.source_root.name == "sealed-source-r3"
    assert policy.prior_source_roots == (
        materializer.PRIOR_TRUSTED_SOURCE_ROOT,
        materializer.R2_TRUSTED_SOURCE_ROOT,
    )
    assert policy.immutable_roots == (
        materializer.ImmutableRoot(
            materializer.INVALID_R1_ROOT,
            materializer.EXPECTED_INVALID_R1_INODE,
            materializer.EXPECTED_INVALID_R1_TREE_SHA256,
            "invalid-r1",
        ),
        materializer.ImmutableRoot(
            materializer.R2_ROOT,
            materializer.EXPECTED_R2_INODE,
            materializer.EXPECTED_R2_TREE_SHA256,
            "validated-r2",
        ),
        materializer.ImmutableRoot(
            materializer.R2_TRUSTED_SOURCE_ROOT,
            materializer.EXPECTED_R2_SOURCE_INODE,
            materializer.EXPECTED_R2_SOURCE_TREE_SHA256,
            "r2-source",
        ),
        materializer.ImmutableRoot(
            materializer.R2_DISPOSITION_ROOT,
            materializer.EXPECTED_R2_DISPOSITION_INODE,
            materializer.EXPECTED_R2_DISPOSITION_TREE_SHA256,
            "r2-disposition",
        ),
    )
    if materializer.INVALID_R1_ROOT.is_dir():
        assert materializer.fingerprint_tree(materializer.INVALID_R1_ROOT) == (
            materializer.TreeFingerprint(
                materializer.EXPECTED_INVALID_R1_INODE,
                materializer.EXPECTED_INVALID_R1_TREE_SHA256,
            )
        )
    with pytest.raises(MaterializationError, match="target-lock"):
        materializer.materialize_sealed_bundle(
            source_fd=-1,
            expected_source_sha256="0" * 64,
            expected_head="0" * 40,
            lock_id="v2-benefits-20260819-r1",
            expected_predecessor_r2_tree_sha256="0" * 64,
        )
    with pytest.raises(MaterializationError, match="target-lock"):
        materializer.materialize_sealed_bundle(
            source_fd=-1,
            expected_source_sha256="0" * 64,
            expected_head="0" * 40,
            lock_id="v2-benefits-20260819-r2",
            expected_predecessor_r2_tree_sha256="0" * 64,
        )


def test_r3_disposition_successor_plan_is_closed_read_only_and_opaque(
    tmp_path: Path,
) -> None:
    policy, _head, _source = _policy(tmp_path)
    invalid = tmp_path / "protected" / "invalid-r1"
    predecessor = tmp_path / "protected" / "validated-r2"
    prior_disposition = tmp_path / "protected" / "r2-disposition"
    invalid.mkdir(mode=0o700)
    predecessor.mkdir(mode=0o700)
    prior_disposition.mkdir(mode=0o700)
    (invalid / "receipt.json").write_text('{"invalid":true}', encoding="utf-8")
    (predecessor / "receipt.json").write_text('{"runtime":"v1"}', encoding="utf-8")
    (prior_disposition / "plan.json").write_text(
        '{"replacement":"validated-r2"}', encoding="utf-8"
    )
    before = {
        path: materializer.fingerprint_tree(path)
        for path in (invalid, predecessor, prior_disposition)
    }
    policy = replace(
        policy,
        immutable_roots=(
            materializer.ImmutableRoot(
                invalid,
                before[invalid].inode,
                before[invalid].sha256,
                "invalid-r1",
            ),
            materializer.ImmutableRoot(
                predecessor,
                before[predecessor].inode,
                before[predecessor].sha256,
                "validated-r2",
            ),
            materializer.ImmutableRoot(
                prior_disposition,
                before[prior_disposition].inode,
                before[prior_disposition].sha256,
                "r2-disposition",
            ),
        ),
        disposition_root=tmp_path / "protected-disposition-audit",
    )

    record = json.loads(materializer.build_disposition_record(policy))

    assert set(record) == {
        "schema",
        "state",
        "invalid_lock_id",
        "invalid_root_tree_sha256",
        "superseded_lock_id",
        "superseded_root_tree_sha256",
        "prior_disposition_tree_sha256",
        "replacement_lock_id",
        "action",
    }
    assert record["schema"] == "ai-sdlc-v2-benefit-disposition-plan/v2"
    assert record["invalid_lock_id"] == "invalid-r1"
    assert record["superseded_lock_id"] == "validated-r2"
    assert record["replacement_lock_id"] == policy.target.name
    assert record["state"] == "requires-independent-review"
    assert record["action"] == "preserve-in-place"
    assert not policy.disposition_root.exists()
    assert {
        path: materializer.fingerprint_tree(path)
        for path in (invalid, predecessor, prior_disposition)
    } == before
    assert '{"invalid":true}' not in json.dumps(record)
    assert '{"runtime":"v1"}' not in json.dumps(record)


def test_fix_round6_immutable_r1_drift_stops_before_r2_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy, head, source = _policy(tmp_path)
    invalid = tmp_path / "protected" / "invalid-r1"
    invalid.mkdir(mode=0o700)
    marker = invalid / "receipt.json"
    marker.write_text('{"state":"invalid-unbound"}', encoding="utf-8")
    before = materializer.fingerprint_tree(invalid)
    policy = replace(
        policy,
        immutable_roots=(
            *policy.immutable_roots,
            materializer.ImmutableRoot(
                invalid, before.inode, before.sha256, "invalid-r1"
            ),
        ),
    )

    def drift_after_compile(*_args: object, **_kwargs: object) -> None:
        marker.write_text('{"state":"drifted"}', encoding="utf-8")

    monkeypatch.setattr(materializer, "_validate_scratch", drift_after_compile)
    with pytest.raises(MaterializationError, match="immutable-root"):
        _materialize_for_test(
            source,
            expected_source_sha256=sha256(source.read_bytes()).hexdigest(),
            expected_head=head,
            expected_predecessor_r2_tree_sha256=_predecessor_tree_sha256(policy),
            policy=policy,
        )

    assert not policy.target.exists()


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
            expected_predecessor_r2_tree_sha256=_predecessor_tree_sha256(policy),
            policy=policy,
        )
    dirty = policy.repo_root / "dirty.txt"
    dirty.write_text("dirty")
    with pytest.raises(MaterializationError, match="source-tree"):
        _materialize_for_test(
            source,
            expected_source_sha256=digest,
            expected_head=head,
            expected_predecessor_r2_tree_sha256=_predecessor_tree_sha256(policy),
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
    with pytest.raises(MaterializationError, match="protocol-predecessor"):
        _materialize_for_test(
            source,
            expected_source_sha256=digest,
            expected_head=invalid_head,
            expected_predecessor_r2_tree_sha256=_predecessor_tree_sha256(policy),
            policy=policy,
        )
    shutil.copy2(
        REPO_ROOT / "benchmarks/ai-sdlc-v2-benefits/protocol.json", protocol_path
    )
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
            expected_predecessor_r2_tree_sha256=_predecessor_tree_sha256(policy),
            policy=policy,
        )


def test_target_policy_rejects_existing_leaf_untrusted_parent_and_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy, head, source = _policy(tmp_path)
    digest = sha256(source.read_bytes()).hexdigest()
    predecessor_digest = _predecessor_tree_sha256(policy)
    monkeypatch.setattr(
        materializer, "_validate_scratch", lambda *_args, **_kwargs: None
    )
    policy.target.mkdir()
    with pytest.raises(MaterializationError, match="target-exists"):
        _materialize_for_test(
            source,
            expected_source_sha256=digest,
            expected_head=head,
            expected_predecessor_r2_tree_sha256=predecessor_digest,
            policy=policy,
        )
    policy.target.rmdir()
    policy.target.parent.chmod(0o777)
    with pytest.raises(MaterializationError, match="target-ancestor"):
        _materialize_for_test(
            source,
            expected_source_sha256=digest,
            expected_head=head,
            expected_predecessor_r2_tree_sha256=predecessor_digest,
            policy=policy,
        )
    policy.target.parent.chmod(0o700)
    overlap = MaterializerPolicy(
        repo_root=policy.repo_root,
        target=policy.target,
        trust_anchor=policy.trust_anchor,
        legacy_root=policy.legacy_root,
        expected_legacy_inode=policy.expected_legacy_inode,
        expected_legacy_tree_sha256=policy.expected_legacy_tree_sha256,
        forbidden_roots=(*policy.forbidden_roots, policy.target.parent),
        source_base=policy.source_base,
        source_root=policy.source_root,
        canary_run_root=policy.canary_run_root,
        raw_results_root=policy.raw_results_root,
        other_run_roots=policy.other_run_roots,
        immutable_roots=policy.immutable_roots,
    )
    with pytest.raises(MaterializationError, match="target-overlap"):
        _materialize_for_test(
            source,
            expected_source_sha256=digest,
            expected_head=head,
            expected_predecessor_r2_tree_sha256=predecessor_digest,
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
        expected_predecessor_r2_tree_sha256=_predecessor_tree_sha256(policy),
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
        expected_predecessor_r2_tree_sha256=_predecessor_tree_sha256(policy),
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
        expected_predecessor_r2_tree_sha256=_predecessor_tree_sha256(policy),
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
            expected_predecessor_r2_tree_sha256=_predecessor_tree_sha256(policy),
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
            expected_predecessor_r2_tree_sha256=_predecessor_tree_sha256(policy),
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
            expected_predecessor_r2_tree_sha256=_predecessor_tree_sha256(policy),
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
            expected_predecessor_r2_tree_sha256=_predecessor_tree_sha256(policy),
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
            expected_predecessor_r2_tree_sha256=_predecessor_tree_sha256(policy),
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
            expected_predecessor_r2_tree_sha256=_predecessor_tree_sha256(policy),
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
    wrong_tree = replace(policy, expected_legacy_tree_sha256="0" * 64)
    with pytest.raises(MaterializationError, match="legacy-tree"):
        _materialize_for_test(
            source,
            expected_source_sha256=digest,
            expected_head=head,
            expected_predecessor_r2_tree_sha256=_predecessor_tree_sha256(policy),
            policy=wrong_tree,
        )
    wrong_inode = MaterializerPolicy(
        repo_root=policy.repo_root,
        target=policy.target,
        trust_anchor=policy.trust_anchor,
        legacy_root=policy.legacy_root,
        expected_legacy_inode=policy.expected_legacy_inode + 1,
        expected_legacy_tree_sha256=policy.expected_legacy_tree_sha256,
        forbidden_roots=policy.forbidden_roots,
        source_base=policy.source_base,
        source_root=policy.source_root,
        canary_run_root=policy.canary_run_root,
        raw_results_root=policy.raw_results_root,
        other_run_roots=policy.other_run_roots,
        immutable_roots=policy.immutable_roots,
    )
    with pytest.raises(MaterializationError, match="legacy-inode"):
        _materialize_for_test(
            source,
            expected_source_sha256=digest,
            expected_head=head,
            expected_predecessor_r2_tree_sha256=_predecessor_tree_sha256(policy),
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
    predecessor_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_sdlc",
            "benefit-evidence",
            "fingerprint-predecessor-r2",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert predecessor_result.returncode == 0, predecessor_result.stderr
    predecessor = json.loads(predecessor_result.stdout)
    assert predecessor == {
        "inode": materializer.EXPECTED_R2_INODE,
        "tree_sha256": materializer.EXPECTED_R2_TREE_SHA256,
    }
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
        "--expected-predecessor-r2-tree-sha256",
    ):
        assert option in combined
    assert "--expected-old-root-tree-sha256" not in combined
    assert "--sealed-source " not in combined
    assert "--target" not in combined

    legacy_option = CliRunner().invoke(
        benefit_evidence_cmd.benefit_evidence_app,
        [
            "materialize-sealed",
            "--expected-old-root-tree-sha256",
            "0" * 64,
        ],
    )
    assert legacy_option.exit_code != 0
    assert "No such option" in legacy_option.output


def test_task2_binding_cli_reports_narrow_authority_without_provider_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def validate_r2(*args: object, **kwargs: object) -> list[object]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return []

    monkeypatch.setattr(
        benefit_evidence_cmd,
        "validate_sealed_commitments",
        validate_r2,
        raising=False,
    )

    result = CliRunner().invoke(
        benefit_evidence_cmd.benefit_evidence_app,
        ["verify-sealed-commitments"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "authority": "task2-commitment",
        "experiment_authorized": False,
        "provider_authorized": False,
        "status": "bound",
    }
    assert captured["args"][1] == materializer.R2_ROOT
    assert captured["kwargs"]["source_root"] == materializer.R2_TRUSTED_SOURCE_ROOT

    monkeypatch.setattr(
        benefit_evidence_cmd,
        "validate_sealed_commitments",
        lambda *_args, **_kwargs: [
            fixture_module.BenchmarkIssue("test", "/private/test-only-secret")
        ],
    )
    rejected = CliRunner().invoke(
        benefit_evidence_cmd.benefit_evidence_app,
        ["verify-sealed-commitments"],
    )
    assert rejected.exit_code == 1
    assert json.loads(rejected.stderr) == {
        "code": "sealed-commitments",
        "status": "no-go",
    }
    assert "/private/test-only-secret" not in rejected.output


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
                "--expected-predecessor-r2-tree-sha256",
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
                "--expected-predecessor-r2-tree-sha256",
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
    except (PermissionError, RuntimeError, fixture_module.EvaluatorNoGoError) as error:
        if (
            "sandbox_apply: Operation not permitted" in str(error)
            or getattr(error, "code", "") == "adapter-sandbox"
        ):
            pytest.skip("nested sandbox blocks exact evaluator profile")
        raise


@pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS Seatbelt")
def test_fix_round6_system_external_runtime_executes_six_oracles_twice(
    tmp_path: Path,
) -> None:
    policy, head, source = _policy(tmp_path)
    compiled = _compile_for_test(policy, head, source)
    sealed = tmp_path / "protected" / "sealed"
    sealed.parent.mkdir(mode=0o700, exist_ok=True)
    materializer._write_plain_files(sealed, compiled.files)
    first = fixture_module.prepare_fixture(
        "multi-tenant-security-review",
        tmp_path / "runs" / "first",
        fixture_root=FIXTURE_ROOT,
    )
    second = fixture_module.prepare_fixture(
        "multi-tenant-security-review",
        tmp_path / "runs" / "second",
        fixture_root=FIXTURE_ROOT,
    )

    try:
        first_result = fixture_module.evaluate_fixture(
            "multi-tenant-security-review", first.root, sealed
        )
        second_result = fixture_module.evaluate_fixture(
            "multi-tenant-security-review", second.root, sealed
        )
    except fixture_module.EvaluatorNoGoError as error:
        if error.code == "adapter-sandbox":
            pytest.skip("nested sandbox blocks exact evaluator profile")
        raise

    assert len(first_result.satisfied_criteria) + len(first_result.failed_criteria) == 6
    assert first_result == second_result
    assert first_result.external_verified_delivery is False
    assert first_result.result_sha256 == second_result.result_sha256
