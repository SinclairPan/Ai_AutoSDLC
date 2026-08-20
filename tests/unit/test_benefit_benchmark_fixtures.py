from __future__ import annotations

import json
import os
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

import ai_sdlc.benefit_benchmark_fixtures as fixture_module
from ai_sdlc.benefit_benchmark import _load_evidence_contract, load_protocol
from ai_sdlc.benefit_benchmark_fixtures import (
    FIXTURE_IDS,
    FrozenIntentApprovalService,
    build_canonical_pre_state,
    build_provider_isolation_profile,
    evaluate_fixture,
    fixture_tree_digest,
    load_fixture_manifest,
    normalized_semantic_view,
    prepare_fixture,
    probe_provider_isolation,
    run_frontend_browser_e2e,
    scan_candidate_for_sealed_leak,
    validate_fixture_manifest,
    validate_frontend_runtime,
    validate_sealed_commitments,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPO_ROOT / "benchmarks" / "ai-sdlc-v2-benefits" / "fixtures"


def _test_browser_program() -> dict[str, object]:
    return {
        "schema": "ai-sdlc-v2-frontend-browser-program/v1",
        "scenarios": [
            {
                "id": "test-only-browser-scenario",
                "loader": {
                    "outcomes": [
                        {
                            "type": "resolve",
                            "value": [
                                {
                                    "id": "TEST-RISK",
                                    "name": "测试条目",
                                    "service": "test-service",
                                    "level": "high",
                                    "owner": "测试团队",
                                    "confirmed": False,
                                }
                            ],
                        }
                    ]
                },
                "confirmer": {"mode": "immediate"},
                "actions": [
                    {"op": "load", "handle": "load", "await": True},
                    {"op": "render", "filter": "high"},
                ],
                "assertions": [
                    {
                        "id": "fields",
                        "kind": "dom-text-contains",
                        "target": "body",
                        "expected": ["test-service", "测试团队", "high"],
                        "expose_as": "field_rendering",
                    },
                    {
                        "id": "filter",
                        "kind": "dom-count",
                        "target": "tbody tr",
                        "expected": 1,
                        "expose_as": "filtering",
                    },
                    {
                        "id": "console",
                        "kind": "console-empty",
                        "target": "console_errors",
                        "expected": [],
                        "expose_as": None,
                    },
                    {
                        "id": "a11y",
                        "kind": "basic-a11y",
                        "target": "document",
                        "expected": True,
                        "expose_as": None,
                    },
                ],
            }
        ],
    }


def _write_sealed_test_root(root: Path) -> Path:
    root.mkdir(parents=True)
    payloads = {
        "requirement-contract-ambiguity": {
            "schema": "ai-sdlc-v2-benefit-sealed-evaluator/v1",
            "fixture_id": "requirement-contract-ambiguity",
            "criteria": [
                {
                    "id": "opaque-r1",
                    "weight": 1,
                    "severity": "important",
                    "kind": "json_key_present",
                    "path": ["decisions"],
                }
            ],
        },
        "frontend-recovery-delivery": {
            "schema": "ai-sdlc-v2-benefit-sealed-evaluator/v1",
            "fixture_id": "frontend-recovery-delivery",
            "criteria": [
                {
                    "id": "opaque-f1",
                    "weight": 1,
                    "severity": "blocker",
                    "kind": "file_contains",
                    "path": "benchmark-task/src/release-state.mjs",
                    "value": "opaque-never-present",
                }
            ],
        },
        "multi-tenant-security-review": {
            "schema": "ai-sdlc-v2-benefit-sealed-evaluator/v1",
            "fixture_id": "multi-tenant-security-review",
            "criteria": [
                {
                    "id": "opaque-s1",
                    "weight": 1,
                    "severity": "blocker",
                    "kind": "file_contains",
                    "path": "benchmark-task/access_control.py",
                    "value": "opaque-never-present",
                }
            ],
        },
    }
    entries = []
    for fixture_id, payload in payloads.items():
        data = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        filename = f"{fixture_id}.sealed.json"
        (root / filename).write_bytes(data)
        entries.append(
            {
                "fixture_id": fixture_id,
                "path": filename,
                "sha256": sha256(data).hexdigest(),
            }
        )
    intent_map = {
        "schema": "ai-sdlc-v2-benefit-intent-map/v2",
        "questions": {"contract.boundary": {"answer": "opaque-answer", "delay_ms": 0}},
        "approvals": ["design-contract", "frontend-solution"],
    }
    intent_bytes = json.dumps(
        intent_map, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    (root / "intent-map.json").write_bytes(intent_bytes)
    runtime = fixture_module.evaluator_python_runtime_identity()
    runtime_sha256 = fixture_module.evaluator_runtime_identity_sha256(runtime)
    capsule = fixture_module.evaluator_runtime_capsule_manifest(
        Path(str(runtime["path"])), str(runtime["version"])
    )
    capsule_sha256 = fixture_module.evaluator_runtime_capsule_sha256(capsule)
    manifest = {
        "schema": "ai-sdlc-v2-benefit-sealed-manifest/v4",
        "lock_id": "unit-test-only",
        "entries": entries,
        "intent_map": {
            "path": "intent-map.json",
            "sha256": sha256(intent_bytes).hexdigest(),
        },
        "evaluator_python_runtime_sha256": runtime_sha256,
        "evaluator_runtime_capsule_sha256": capsule_sha256,
    }
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    (root / "sealed-manifest.json").write_bytes(manifest_bytes)
    (root / "candidate-commitments.json").write_text(
        json.dumps(
            {
                "schema": "ai-sdlc-v2-benefit-candidate-commitments/v3",
                "lock_id": "unit-test-only",
                "source_head": "0" * 40,
                "source_tree_sha": "1" * 40,
                "materializer_sha256": "2" * 64,
                "source_bundle_sha256": "3" * 64,
                "fixture_manifest_sha256": "4" * 64,
                "fixture_tree_sha256": "5" * 64,
                "evidence_contract_sha256": "6" * 64,
                "sealed_manifest_sha256": sha256(manifest_bytes).hexdigest(),
                "intent_map_sha256": sha256(intent_bytes).hexdigest(),
                "fixture_payloads": entries,
                "source_root_tree_sha256": "7" * 64,
                "evaluator_python_runtime": runtime,
                "evaluator_python_runtime_sha256": runtime_sha256,
                "evaluator_runtime_capsule": capsule,
                "evaluator_runtime_capsule_sha256": capsule_sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    root.chmod(0o700)
    for child in root.iterdir():
        child.chmod(0o600)
    return root


def _write_test_runtime_capsule(root: Path) -> Path:
    launcher = root / "bin" / "python3.14"
    libpython = root / "lib" / "libpython3.14.dylib"
    stdlib = root / "lib" / "python3.14"
    dynload = stdlib / "lib-dynload"
    for directory in (launcher.parent, dynload):
        directory.mkdir(parents=True, mode=0o755, exist_ok=True)
    launcher.write_bytes(b"test-launcher")
    launcher.chmod(0o755)
    libpython.write_bytes(b"test-libpython")
    libpython.chmod(0o755)
    (stdlib / "json.py").write_bytes(b"test-stdlib")
    (dynload / "_datetime.so").write_bytes(b"test-dynload")
    return launcher


def test_fix_round7_runtime_capsule_binds_actual_dependency_closure() -> None:
    runtime = fixture_module.evaluator_python_runtime_identity()
    capsule = fixture_module.evaluator_runtime_capsule_manifest(
        Path(str(runtime["path"])), str(runtime["version"])
    )
    entries = {item["path"]: item for item in capsule["entries"]}

    assert set(capsule) == {
        "schema",
        "root",
        "launcher",
        "libpython",
        "stdlib",
        "dynload",
        "entries",
    }
    assert capsule["launcher"] in entries
    assert capsule["libpython"] in entries
    assert capsule["stdlib"] in entries
    assert capsule["dynload"] in entries
    assert len(entries) > 100
    assert all(
        {
            "path",
            "type",
            "device",
            "inode",
            "uid",
            "gid",
            "mode",
            "nlink",
            "size",
            "ctime_ns",
            "mtime_ns",
        }
        <= set(item)
        for item in entries.values()
    )
    assert (
        fixture_module.evaluator_runtime_capsule_sha256(capsule)
        == sha256(
            json.dumps(capsule, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def test_fix_round7_capsule_detects_libpython_and_stdlib_drift_with_same_launcher(
    tmp_path: Path,
) -> None:
    launcher = _write_test_runtime_capsule(tmp_path / "runtime")
    before = fixture_module.evaluator_runtime_capsule_manifest(launcher, "3.14.3")
    launcher_sha256 = sha256(launcher.read_bytes()).hexdigest()

    libpython = launcher.parents[1] / "lib" / "libpython3.14.dylib"
    libpython.write_bytes(b"changed-libpython")
    after_lib = fixture_module.evaluator_runtime_capsule_manifest(launcher, "3.14.3")
    assert fixture_module.evaluator_runtime_capsule_sha256(
        after_lib
    ) != fixture_module.evaluator_runtime_capsule_sha256(before)
    assert sha256(launcher.read_bytes()).hexdigest() == launcher_sha256

    libpython.write_bytes(b"test-libpython")
    restored = fixture_module.evaluator_runtime_capsule_manifest(launcher, "3.14.3")
    stdlib = launcher.parents[1] / "lib" / "python3.14" / "json.py"
    stdlib.write_bytes(b"changed-stdlib")
    after_stdlib = fixture_module.evaluator_runtime_capsule_manifest(launcher, "3.14.3")
    assert fixture_module.evaluator_runtime_capsule_sha256(
        after_stdlib
    ) != fixture_module.evaluator_runtime_capsule_sha256(restored)
    assert sha256(launcher.read_bytes()).hexdigest() == launcher_sha256


def test_fix_round7_capsule_fingerprint_rejects_path_replacement_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "capsule"
    root.mkdir()
    target = root / "library.py"
    target.write_bytes(b"original")
    displaced = root / "displaced.py"
    original_read = fixture_module.os.read
    replaced = False

    def racing_read(descriptor: int, size: int) -> bytes:
        nonlocal replaced
        chunk = original_read(descriptor, size)
        if chunk and not replaced:
            target.rename(displaced)
            target.write_bytes(b"replacement")
            replaced = True
        return chunk

    monkeypatch.setattr(fixture_module.os, "read", racing_read)

    with pytest.raises(fixture_module.EvaluatorNoGoError, match="capsule-drift"):
        fixture_module._runtime_capsule_entry(root, target)


def test_fix_round7_adapter_revalidates_capsule_after_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sealed = tmp_path / "protected" / "sealed"
    sealed.mkdir(parents=True)
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    source = candidate / "candidate.py"
    source.write_text("pass\n", encoding="utf-8")
    capsule = tmp_path / "runtime"
    capsule.mkdir()
    before = fixture_module.EvaluatorRuntimeBinding(
        Path("/test/runtime/python"), capsule, "1" * 64
    )
    after = fixture_module.EvaluatorRuntimeBinding(
        Path("/test/runtime/python"), capsule, "2" * 64
    )
    bindings = iter((before, after))
    monkeypatch.setattr(
        fixture_module,
        "_load_bound_evaluator_runtime",
        lambda *_args: next(bindings),
    )
    monkeypatch.setattr(
        fixture_module,
        "run_provider_isolated",
        lambda _profile, argv, **_kwargs: subprocess.CompletedProcess(
            list(argv), 0, '{"allowed":false,"status":"pending"}', ""
        ),
    )

    with pytest.raises(fixture_module.EvaluatorNoGoError, match="capsule-drift"):
        fixture_module._run_candidate_adapter(
            candidate, sealed, source=source, scenario={}
        )


@pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS Seatbelt")
def test_fix_round7_system_runtime_capsule_is_readable_but_not_writable(
    tmp_path: Path,
) -> None:
    sealed = tmp_path / "protected" / "sealed"
    control = tmp_path / "control"
    raw = tmp_path / "raw"
    run = tmp_path / "run"
    other = tmp_path / "other"
    mirror = tmp_path / "runtime-mirror"
    for path in (sealed, control, raw, run, other):
        path.mkdir(parents=True)
    launcher = _write_test_runtime_capsule(mirror)
    before = fixture_module.evaluator_runtime_capsule_v2_manifest(launcher, "3.14.3")
    target = mirror / "lib" / "python3.14" / "json.py"
    profile = build_provider_isolation_profile(
        run_root=run,
        sealed_root=sealed,
        control_root=control,
        raw_results_root=raw,
        protected_roots=(),
        write_protected_roots=(mirror,),
        other_run_roots=(other,),
        argv=("/bin/cat", str(target)),
        environment={"PATH": "/usr/bin:/bin"},
    )

    readable = fixture_module.run_provider_isolated(profile, ["/bin/cat", str(target)])
    attempts = (
        ["/bin/sh", "-c", f'printf x >> "{target}"'],
        ["/bin/mv", str(target), str(target.with_name("renamed.py"))],
        ["/usr/bin/touch", str(mirror / "created.py")],
        ["/usr/bin/touch", str(mirror / "bin" / "created")],
        ["/bin/chmod", "0600", str(target)],
        ["/bin/chmod", "0700", str(mirror)],
        ["/bin/mv", str(mirror), str(mirror.with_name("runtime-renamed"))],
    )
    results = [fixture_module.run_provider_isolated(profile, argv) for argv in attempts]
    if any("sandbox_apply: Operation not permitted" in item.stderr for item in results):
        pytest.skip("nested sandbox blocks exact write-only profile")

    assert readable.returncode == 0
    assert all(item.returncode != 0 for item in results)
    assert (
        fixture_module.evaluator_runtime_capsule_v2_manifest(launcher, "3.14.3")
        == before
    )


def test_fix_round6_runtime_is_external_canonical_and_frozen() -> None:
    identity = fixture_module.evaluator_python_runtime_identity()

    runtime = Path(str(identity["path"]))
    assert runtime == Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    assert identity["sha256"] == sha256(runtime.read_bytes()).hexdigest()
    assert identity["implementation"] == "CPython"
    assert identity["version"]
    assert identity["cache_tag"]
    with pytest.raises(ValueError):
        runtime.relative_to(REPO_ROOT)
    assert (
        fixture_module.evaluator_runtime_identity_sha256(identity)
        == sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def test_fix_round6_runtime_rejects_control_overlap_and_identity_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(fixture_module.EvaluatorNoGoError, match="runtime-overlap"):
        fixture_module.evaluator_python_runtime_identity(
            forbidden_roots=(fixture_module.EVALUATOR_PYTHON.parent,)
        )

    original = fixture_module._digest_file
    monkeypatch.setattr(
        fixture_module,
        "_digest_file",
        lambda path: (
            "0" * 64 if path == fixture_module.EVALUATOR_PYTHON else original(path)
        ),
    )
    with pytest.raises(fixture_module.EvaluatorNoGoError, match="runtime-identity"):
        fixture_module.evaluator_python_runtime_identity(expected_sha256="f" * 64)


@pytest.mark.parametrize(
    "failure", ["exit71", "timeout", "invalid-json", "adapter-error"]
)
def test_fix_round6_adapter_infrastructure_failure_is_no_go(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    sealed = _write_sealed_test_root(tmp_path / "protected" / "sealed")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    source = candidate / "candidate.py"
    source.write_text("pass\n", encoding="utf-8")

    def isolated_launch(_profile: object, argv: object, **_kwargs: object):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(list(argv), 10)
        if failure == "exit71":
            return subprocess.CompletedProcess(
                list(argv), 71, "", "execvp not permitted"
            )
        if failure == "invalid-json":
            return subprocess.CompletedProcess(list(argv), 0, "not-json", "")
        return subprocess.CompletedProcess(list(argv), 0, '{"adapter_error":"bad"}', "")

    monkeypatch.setattr(fixture_module, "run_provider_isolated", isolated_launch)

    with pytest.raises(fixture_module.EvaluatorNoGoError, match="adapter-"):
        fixture_module._run_candidate_adapter(
            candidate,
            sealed,
            source=source,
            scenario={},
        )


def test_fix_round6_adapter_launches_bound_external_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sealed = _write_sealed_test_root(tmp_path / "protected" / "sealed")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    source = candidate / "candidate.py"
    source.write_text("pass\n", encoding="utf-8")
    launches: list[tuple[str, ...]] = []

    def isolated_launch(_profile: object, argv: object, **_kwargs: object):
        launches.append(tuple(argv))
        return subprocess.CompletedProcess(
            list(argv), 0, '{"allowed":false,"status":"pending"}', ""
        )

    monkeypatch.setattr(fixture_module, "run_provider_isolated", isolated_launch)
    fixture_module._run_candidate_adapter(candidate, sealed, source=source, scenario={})

    assert launches[0][0] == str(fixture_module.EVALUATOR_PYTHON)
    assert not launches[0][0].startswith(str(REPO_ROOT))


def test_fix_round6_invalid_r1_without_runtime_binding_is_no_go(tmp_path: Path) -> None:
    actual = Path("/private/tmp/ai-sdlc-v2-benefit-evaluator/v2-benefits-20260819-r1")
    if not actual.is_dir():
        pytest.skip("invalid r1 is not present on this host")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    source = candidate / "candidate.py"
    source.write_text("pass\n", encoding="utf-8")

    with pytest.raises(fixture_module.EvaluatorNoGoError, match="runtime-binding"):
        fixture_module._run_candidate_adapter(
            candidate,
            actual,
            source=source,
            scenario={},
        )


def test_fix_round6_frontend_browser_timeout_remains_expected_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = tmp_path / "candidate"
    sealed = tmp_path / "sealed"
    candidate.mkdir()
    sealed.mkdir()
    monkeypatch.setattr(
        fixture_module,
        "run_frontend_browser_e2e",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(["test-browser"], 30)
        ),
    )

    assert (
        fixture_module._criterion_passes(
            candidate,
            sealed,
            {
                "kind": "frontend_browser_suite",
                "expected": {"executed_with_real_browser": True},
            },
            browser_program=_test_browser_program(),
        )
        is False
    )


def test_task2_red_manifest_is_closed_stable_and_covers_three_fixtures() -> None:
    manifest = load_fixture_manifest(FIXTURE_ROOT / "manifest.json")

    assert manifest.fixture_ids == FIXTURE_IDS
    assert validate_fixture_manifest(manifest, FIXTURE_ROOT) == []
    assert manifest.canonical_sha256 == fixture_tree_digest(FIXTURE_ROOT)
    assert all(entry.provenance_commit for entry in manifest.fixtures)
    assert all(entry.public_tree_sha256 for entry in manifest.fixtures)


def test_task2_red_evidence_contract_template_is_accepted_by_task1_consumer(
    tmp_path: Path,
) -> None:
    protocol_path = REPO_ROOT / "benchmarks" / "ai-sdlc-v2-benefits" / "protocol.json"
    raw = json.loads(protocol_path.read_text(encoding="utf-8"))
    manifest = load_fixture_manifest(FIXTURE_ROOT / "manifest.json")
    raw["execution_lock"].update(
        {
            "fixture_tree_sha256": manifest.canonical_sha256,
            "fixture_commitment": manifest.canonical_sha256,
            "evidence_contract_sha256": manifest.evidence_contract_template_sha256,
            "evidence_contract_commitment": manifest.evidence_contract_template_sha256,
        }
    )
    bound = tmp_path / "protocol.json"
    bound.write_text(json.dumps(raw, separators=(",", ":")), encoding="utf-8")

    contract = _load_evidence_contract(
        FIXTURE_ROOT / "evidence-contract.template.json", load_protocol(bound)
    )

    assert len(contract["runs"]) == 15
    assert [item["run_id"] for item in contract["runs"]] == [
        item["run_id"] for item in raw["run_matrix"]
    ]


@pytest.mark.parametrize("fixture_id", FIXTURE_IDS)
def test_task2_red_prepare_is_clean_single_root_and_reproducible(
    tmp_path: Path, fixture_id: str
) -> None:
    first = prepare_fixture(fixture_id, tmp_path / "first")
    second = prepare_fixture(fixture_id, tmp_path / "second")

    assert first.public_tree_sha256 == second.public_tree_sha256
    assert first.initial_commit == second.initial_commit
    assert first.visible_results == second.visible_results
    assert (
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=first.root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        == ""
    )
    assert (
        subprocess.run(
            ["git", "rev-list", "--max-parents=0", "HEAD"],
            cwd=first.root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.count("\n")
        == 1
    )
    assert all(result.matches_expected for result in first.visible_results)


def test_task2_red_frontend_baseline_self_check_accepts_exact_expected_red(
    tmp_path: Path,
) -> None:
    prepared = prepare_fixture("frontend-recovery-delivery", tmp_path / "frontend")
    red = next(
        item for item in prepared.visible_results if item.command_id == "visible-red"
    )

    assert red.exit_code == 1
    assert red.expected_exit_code == 1
    assert red.expected_signature == "VISIBLE_RED: recoverable failure state is absent"
    assert red.expected_signature in red.stderr
    assert red.matches_expected is True


@pytest.mark.parametrize("fixture_id", FIXTURE_IDS)
def test_task2_red_baseline_fails_sealed_criterion_deterministically(
    tmp_path: Path, fixture_id: str
) -> None:
    sealed = _write_sealed_test_root(tmp_path / "sealed")
    first_prepared = prepare_fixture(fixture_id, tmp_path / "first" / fixture_id)
    second_prepared = prepare_fixture(fixture_id, tmp_path / "second" / fixture_id)

    first = evaluate_fixture(fixture_id, first_prepared.root, sealed)
    second = evaluate_fixture(fixture_id, second_prepared.root, sealed)

    assert first_prepared.visible_results == second_prepared.visible_results
    assert first == second
    assert first.external_verified_delivery is False
    assert first.failed_criteria
    assert first.result_sha256 == second.result_sha256


@pytest.mark.parametrize("fixture_id", FIXTURE_IDS)
def test_task2_red_canonical_pre_state_is_method_neutral_semantic_parity(
    tmp_path: Path, fixture_id: str
) -> None:
    prepared = prepare_fixture(fixture_id, tmp_path / fixture_id)
    state = build_canonical_pre_state(fixture_id, prepared.root, tmp_path / "state")
    public = json.loads(
        (prepared.root / "benchmark-task" / "input-contract.json").read_text()
    )

    assert normalized_semantic_view(state) == normalized_semantic_view(public)
    assert state["target_stage"] == (
        "design-contract"
        if fixture_id == "requirement-contract-ambiguity"
        else "implementation"
    )
    if fixture_id == "requirement-contract-ambiguity":
        assert state["canonical_pre_state"] == ["requirement"]
    else:
        assert state["canonical_pre_state"] == ["requirement", "design-contract"]


def test_task2_red_frontend_target_is_treatment_neutral(
    tmp_path: Path,
) -> None:
    prepared = prepare_fixture("frontend-recovery-delivery", tmp_path / "frontend")
    manifest = json.loads(
        (prepared.root / "benchmark-task" / "program-manifest.json").read_text()
    )
    input_contract = json.loads(
        (prepared.root / "benchmark-task" / "input-contract.json").read_text()
    )

    assert manifest["solution_target"] == input_contract["solution_target"]
    assert manifest["solution_target"] == {
        "frontend_stack": "vue3",
        "provider_id": "public-primevue",
        "style_pack_id": "modern-saas",
    }
    assert "applies_to_arms" not in manifest
    assert "arm_confirmation_state" not in manifest


def test_task2_red_intent_and_approval_service_is_deterministic_and_automated(
    tmp_path: Path,
) -> None:
    answers = {
        "schema": "ai-sdlc-v2-benefit-intent-map/v1",
        "questions": {"contract.boundary": {"answer": "opaque-answer", "delay_ms": 0}},
        "approvals": ["design-contract"],
    }
    sealed_map = tmp_path / "intent-map.json"
    sealed_map.write_text(json.dumps(answers), encoding="utf-8")
    log = tmp_path / "events.jsonl"
    service = FrozenIntentApprovalService(sealed_map, log)

    assert service.answer("run-1", "contract.boundary") == {
        "status": "answered",
        "answer": "opaque-answer",
    }
    assert service.answer("run-1", "unknown.question") == {"status": "unresolved"}
    proposal = sha256(b"proposal").hexdigest()
    service.register_proposal("run-1", "design-contract", proposal)
    assert service.approval_request("run-1", "design-contract", proposal) == {
        "status": "approved",
        "proposal_digest": proposal,
    }
    assert service.approval_request("run-1", "design-contract", "0" * 64) == {
        "status": "revise"
    }
    events = [json.loads(line) for line in log.read_text().splitlines()]
    assert [event["type"] for event in events] == [
        "intent_service_event",
        "intent_service_event",
        "approval_service_event",
        "approval_service_event",
    ]
    assert all(event["actor"] == "automated_service" for event in events)
    assert all("human" not in json.dumps(event).lower() for event in events)


def _write_v3_commitment_authority(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path]:
    from ai_sdlc.benefit_sealed_materializer import fingerprint_tree

    sealed = _write_sealed_test_root(tmp_path / "v2-benefits-20260819-r2")
    manifest_path = sealed / "sealed-manifest.json"
    sealed_manifest = json.loads(manifest_path.read_text())
    sealed_manifest["lock_id"] = sealed.name
    manifest_path.write_bytes(
        json.dumps(
            sealed_manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    )
    fixture_digest = fixture_tree_digest(FIXTURE_ROOT)
    fixture_manifest_digest = sha256(
        (FIXTURE_ROOT / "manifest.json").read_bytes()
    ).hexdigest()
    evidence_digest = sha256(
        (FIXTURE_ROOT / "evidence-contract.template.json").read_bytes()
    ).hexdigest()
    source = tmp_path / "sealed-source-r2"
    source.mkdir(mode=0o700)
    source_bundle = source / "formal-source.json"
    source_bundle.write_bytes(b'{"schema":"test-only-source/v1"}')
    source_bundle.chmod(0o600)
    source_bundle_digest = sha256(source_bundle.read_bytes()).hexdigest()
    source_tree_digest = fingerprint_tree(source).sha256
    candidate_path = sealed / "candidate-commitments.json"
    candidate = json.loads(candidate_path.read_text())
    payloads = [
        {"fixture_id": item["fixture_id"], "sha256": item["sha256"]}
        for item in sealed_manifest["entries"]
    ]
    candidate.update(
        {
            "lock_id": sealed.name,
            "source_bundle_sha256": source_bundle_digest,
            "fixture_manifest_sha256": fixture_manifest_digest,
            "fixture_tree_sha256": fixture_digest,
            "evidence_contract_sha256": evidence_digest,
            "sealed_manifest_sha256": sha256(manifest_path.read_bytes()).hexdigest(),
            "fixture_payloads": payloads,
            "source_root_tree_sha256": source_tree_digest,
        }
    )
    candidate_path.write_bytes(
        json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode()
    )
    candidate_digest = sha256(candidate_path.read_bytes()).hexdigest()
    receipt = {
        "schema": "ai-sdlc-v2-benefit-materialization-receipt/v3",
        "publication_state": "published-pending-isolation",
        "isolation_probe_state": "pending",
        "target_lock_id": sealed.name,
        "source_head": candidate["source_head"],
        "source_tree_sha": candidate["source_tree_sha"],
        "materializer_sha256": candidate["materializer_sha256"],
        "source_bundle_sha256": source_bundle_digest,
        "fixture_manifest_sha256": fixture_manifest_digest,
        "fixture_tree_sha256": fixture_digest,
        "evidence_contract_sha256": evidence_digest,
        "sealed_manifest_sha256": candidate["sealed_manifest_sha256"],
        "intent_map_sha256": candidate["intent_map_sha256"],
        "fixture_payloads": payloads,
        "candidate_commitments_sha256": candidate_digest,
        "source_root_tree_sha256": source_tree_digest,
        "evaluator_python_runtime_sha256": candidate["evaluator_python_runtime_sha256"],
        "evaluator_runtime_capsule_sha256": candidate[
            "evaluator_runtime_capsule_sha256"
        ],
    }
    receipt_path = sealed / "materialization-receipt.json"
    receipt_path.write_bytes(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
    )
    receipt_digest = sha256(receipt_path.read_bytes()).hexdigest()
    attestation = {
        "schema": "ai-sdlc-v2-benefit-isolation-attestation/v1",
        "state": "validated",
        "pending_receipt_sha256": receipt_digest,
        "evaluator_python_runtime_sha256": candidate["evaluator_python_runtime_sha256"],
        "evaluator_runtime_capsule_sha256": candidate[
            "evaluator_runtime_capsule_sha256"
        ],
        "profile_sha256": "8" * 64,
        "checks": {
            "direct": True,
            "parent": True,
            "symlink": True,
            "hardlink": True,
            "environment": True,
            "other_run": True,
            "add_dir": True,
            "protected_roots": 1,
            "write_protected_roots": 1,
        },
    }
    attestation_path = sealed / "isolation-attestation.json"
    attestation_path.write_bytes(
        json.dumps(attestation, sort_keys=True, separators=(",", ":")).encode()
    )
    for child in sealed.iterdir():
        child.chmod(0o600)
    sealed.chmod(0o700)
    commitments = {
        "schema": "ai-sdlc-v2-benefit-sealed-commitments/v3",
        "lock_id": sealed.name,
        "sealed_manifest_sha256": candidate["sealed_manifest_sha256"],
        "fixture_tree_sha256": fixture_digest,
        "fixture_commitment": fixture_digest,
        "fixture_manifest_sha256": fixture_manifest_digest,
        "evidence_contract_template_sha256": evidence_digest,
        "evidence_contract_commitment": evidence_digest,
        "fixture_payloads": payloads,
        "intent_map_sha256": sealed_manifest["intent_map"]["sha256"],
        "candidate_commitments_sha256": candidate_digest,
        "materialization_receipt_sha256": receipt_digest,
        "isolation_attestation_sha256": sha256(
            attestation_path.read_bytes()
        ).hexdigest(),
        "evaluator_python_runtime_sha256": candidate["evaluator_python_runtime_sha256"],
        "evaluator_runtime_capsule_sha256": candidate[
            "evaluator_runtime_capsule_sha256"
        ],
        "source_bundle_sha256": source_bundle_digest,
        "source_root_tree_sha256": source_tree_digest,
        "publication_state": "materialized-validated",
    }
    path = tmp_path / "sealed-commitments.json"
    path.write_text(json.dumps(commitments), encoding="utf-8")
    protocol_path = tmp_path / "protocol.json"
    protocol_path.write_text(
        json.dumps(
            {
                "execution_lock": {
                    "fixture_tree_sha256": fixture_digest,
                    "fixture_commitment": fixture_digest,
                    "evidence_contract_sha256": evidence_digest,
                    "evidence_contract_commitment": evidence_digest,
                }
            }
        ),
        encoding="utf-8",
    )
    return path, sealed, source, protocol_path


def test_task2_bound_commitment_authority_verifies_without_event_writes(
    tmp_path: Path,
) -> None:
    path, sealed, source, protocol = _write_v3_commitment_authority(tmp_path)
    event_path = sealed.parent / ".intent-validation-events.jsonl"

    assert (
        validate_sealed_commitments(
            path,
            sealed,
            FIXTURE_ROOT,
            source_root=source,
            protocol_path=protocol,
        )
        == []
    )
    assert not event_path.exists()


@pytest.mark.parametrize("case", ["root-mode", "file-mode", "extra", "hardlink"])
def test_task2_bound_authority_rejects_nonexclusive_publication_metadata(
    tmp_path: Path, case: str
) -> None:
    path, sealed, source, protocol = _write_v3_commitment_authority(tmp_path)
    if case == "root-mode":
        sealed.chmod(0o755)
    elif case == "file-mode":
        (sealed / "sealed-manifest.json").chmod(0o644)
    elif case == "extra":
        extra = sealed / "ninth-entry.json"
        extra.write_text("{}", encoding="utf-8")
        extra.chmod(0o600)
    else:
        os.link(sealed / "sealed-manifest.json", tmp_path / "manifest-hardlink.json")

    issues = validate_sealed_commitments(
        path, sealed, FIXTURE_ROOT, source_root=source, protocol_path=protocol
    )
    assert [issue.code for issue in issues] == ["fixture.sealed-commitment"]


def test_task2_bound_authority_rejects_symlinked_member(tmp_path: Path) -> None:
    path, sealed, source, protocol = _write_v3_commitment_authority(tmp_path)
    target = sealed / "sealed-manifest.json"
    outside = tmp_path / "outside-manifest.json"
    target.rename(outside)
    target.symlink_to(outside)

    issues = validate_sealed_commitments(
        path, sealed, FIXTURE_ROOT, source_root=source, protocol_path=protocol
    )
    assert [issue.code for issue in issues] == ["fixture.sealed-commitment"]


def test_task2_bound_authority_fails_closed_when_directory_scan_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path, sealed, source, protocol = _write_v3_commitment_authority(tmp_path)

    def denied(_target):
        raise OSError("test-only scan failure")

    monkeypatch.setattr(fixture_module.os, "listdir", denied)
    issues = validate_sealed_commitments(
        path, sealed, FIXTURE_ROOT, source_root=source, protocol_path=protocol
    )
    assert [issue.code for issue in issues] == ["fixture.sealed-commitment"]


def test_task2_bound_authority_rejects_root_replacement_race(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path, sealed, source, protocol = _write_v3_commitment_authority(tmp_path)
    real_listdir = os.listdir
    calls = 0

    def replace_after_first_scan(target):
        nonlocal calls
        result = real_listdir(target)
        calls += 1
        if calls == 1:
            sealed.rename(tmp_path / "moved-authority")
            sealed.mkdir(mode=0o700)
        return result

    monkeypatch.setattr(fixture_module.os, "listdir", replace_after_first_scan)
    issues = validate_sealed_commitments(
        path, sealed, FIXTURE_ROOT, source_root=source, protocol_path=protocol
    )
    assert [issue.code for issue in issues] == ["fixture.sealed-commitment"]


def test_task2_bound_authority_rejects_file_replacement_race(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path, sealed, source, protocol = _write_v3_commitment_authority(tmp_path)
    target = sealed / "candidate-commitments.json"
    original = target.read_bytes()
    real_read = os.read
    replaced = False

    def replace_open_member(fd: int, count: int) -> bytes:
        nonlocal replaced
        result = real_read(fd, count)
        if result and not replaced:
            replaced = True
            target.rename(tmp_path / "moved-candidate.json")
            target.write_bytes(original)
            target.chmod(0o600)
        return result

    monkeypatch.setattr(fixture_module.os, "read", replace_open_member)
    issues = validate_sealed_commitments(
        path, sealed, FIXTURE_ROOT, source_root=source, protocol_path=protocol
    )
    assert [issue.code for issue in issues] == ["fixture.sealed-commitment"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", "ai-sdlc-v2-benefit-sealed-commitments/v2"),
        ("lock_id", "v2-benefits-20260819-r1"),
        ("publication_state", "sealed-outside-provider-root"),
    ],
)
def test_task2_bound_commitment_rejects_old_or_unvalidated_authority(
    tmp_path: Path, field: str, value: str
) -> None:
    path, sealed, source, protocol = _write_v3_commitment_authority(tmp_path)
    authority = json.loads(path.read_text())
    authority[field] = value
    path.write_text(json.dumps(authority))

    issues = validate_sealed_commitments(
        path, sealed, FIXTURE_ROOT, source_root=source, protocol_path=protocol
    )

    assert [issue.code for issue in issues] == ["fixture.sealed-commitment"]


def test_task2_bound_commitment_is_closed(tmp_path: Path) -> None:
    path, sealed, source, protocol = _write_v3_commitment_authority(tmp_path)
    authority = json.loads(path.read_text())
    authority["absolute_path"] = str(sealed)
    path.write_text(json.dumps(authority))

    assert validate_sealed_commitments(
        path, sealed, FIXTURE_ROOT, source_root=source, protocol_path=protocol
    )

    authority = json.loads(path.read_text())
    authority.pop("absolute_path")
    authority.pop("candidate_commitments_sha256")
    path.write_text(json.dumps(authority))
    assert validate_sealed_commitments(
        path, sealed, FIXTURE_ROOT, source_root=source, protocol_path=protocol
    )


def test_task2_tracked_authority_contains_only_opaque_commitments() -> None:
    path = FIXTURE_ROOT / "sealed-commitments.json"
    text = path.read_text(encoding="utf-8")
    authority = json.loads(text)

    assert set(authority) == fixture_module._SEALED_AUTHORITY_KEYS
    assert authority["schema"] == "ai-sdlc-v2-benefit-sealed-commitments/v3"
    assert authority["lock_id"] == "v2-benefits-20260819-r2"
    assert authority["publication_state"] == "materialized-validated"
    assert "/private/" not in text
    assert "/Users/" not in text
    assert '"answer"' not in text


@pytest.mark.parametrize(
    "surface",
    ["manifest", "candidate", "receipt", "attestation", "source", "protocol"],
)
def test_task2_bound_commitment_rejects_mutated_authority_surface(
    tmp_path: Path, surface: str
) -> None:
    path, sealed, source, protocol = _write_v3_commitment_authority(tmp_path)
    targets = {
        "manifest": sealed / "sealed-manifest.json",
        "candidate": sealed / "candidate-commitments.json",
        "receipt": sealed / "materialization-receipt.json",
        "attestation": sealed / "isolation-attestation.json",
        "source": source / "formal-source.json",
        "protocol": protocol,
    }
    target = targets[surface]
    target.write_bytes(target.read_bytes() + b"x")

    assert validate_sealed_commitments(
        path, sealed, FIXTURE_ROOT, source_root=source, protocol_path=protocol
    )


def test_task2_red_leak_scanner_catches_filename_digest_phrase_and_paths(
    tmp_path: Path,
) -> None:
    sealed = _write_sealed_test_root(tmp_path / "sealed")
    manifest = sealed / "sealed-manifest.json"
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    payload = json.loads(manifest.read_text())["entries"][0]
    (candidate / "leak.txt").write_text(
        " ".join(
            [
                payload["path"],
                payload["sha256"],
                "SEALED_RUBRIC_PHRASE",
                str(sealed),
            ]
        )
    )

    issues = scan_candidate_for_sealed_leak(candidate, manifest)

    assert {issue.code for issue in issues} >= {
        "fixture.leak.filename",
        "fixture.leak.digest",
        "fixture.leak.rubric-phrase",
        "fixture.leak.path",
    }


def test_task2_red_isolation_rejects_links_env_other_run_and_add_dir(
    tmp_path: Path,
) -> None:
    sealed = _write_sealed_test_root(tmp_path / "protected" / "sealed")
    run = tmp_path / "runs" / "run-1"
    other_run = tmp_path / "runs" / "run-2"
    raw = tmp_path / "raw-results"
    run.mkdir(parents=True)
    other_run.mkdir(parents=True)
    raw.mkdir()
    source = next(path for path in sealed.iterdir() if path.suffix == ".json")
    os.symlink(source, run / "sealed-link.json")
    os.link(source, run / "sealed-hardlink.json")
    profile = build_provider_isolation_profile(
        run_root=run,
        sealed_root=sealed,
        control_root=REPO_ROOT,
        other_run_roots=[other_run],
        argv=["codex", "exec", "--add-dir", str(sealed)],
        environment={"PATH": os.environ.get("PATH", ""), "SEALED_ROOT": str(sealed)},
        raw_results_root=raw,
    )

    assert {issue.code for issue in profile.issues} >= {
        "isolation.symlink",
        "isolation.hardlink",
        "isolation.environment",
        "isolation.add-dir",
    }
    assert str(other_run.resolve()) in profile.sandbox_text
    assert profile.executable is False


@pytest.mark.skipif(os.uname().sysname != "Darwin", reason="macOS Seatbelt profile")
def test_task2_red_exact_provider_profile_denies_all_canary_shapes(
    tmp_path: Path,
) -> None:
    sealed = _write_sealed_test_root(tmp_path / "protected" / "sealed")
    run = tmp_path / "runs" / "run-1"
    other_run = tmp_path / "runs" / "run-2"
    control = tmp_path / "control"
    raw = tmp_path / "raw-results"
    run.mkdir(parents=True)
    other_run.mkdir(parents=True)
    control.mkdir()
    raw.mkdir()
    profile = build_provider_isolation_profile(
        run_root=run,
        sealed_root=sealed,
        control_root=control,
        other_run_roots=[other_run],
        argv=["/usr/bin/true"],
        environment={"PATH": os.environ.get("PATH", "")},
        raw_results_root=raw,
    )

    try:
        result = probe_provider_isolation(profile)
    except RuntimeError as error:
        if "sandbox_apply: Operation not permitted" in str(error):
            pytest.skip("nested Seatbelt is unavailable inside the test sandbox")
        raise
    assert result.direct is True
    assert result.parent is True
    assert result.symlink is True
    assert result.hardlink is True
    assert result.environment is True
    assert result.other_run is True
    assert result.add_dir is True
    assert result.protected_root_results
    assert all(denied for _, denied in result.protected_root_results)


def test_fix_round4_probe_uses_regular_gitfile_as_protected_canary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sealed = _write_sealed_test_root(tmp_path / "protected" / "sealed")
    control = tmp_path / "control"
    raw = tmp_path / "raw-results"
    source = tmp_path / "sealed-source"
    run = tmp_path / "runs" / "run-1"
    other = tmp_path / "runs" / "run-2"
    linked = tmp_path / "linked-worktree"
    actual_git = tmp_path / "git-worktree-metadata"
    for path in (control, raw, source, run, other, linked, actual_git):
        path.mkdir(parents=True)
    (control / "control.txt").write_text("control", encoding="utf-8")
    (source / "source.json").write_text("{}", encoding="utf-8")
    gitfile = linked / ".git"
    gitfile.write_text(f"gitdir: {actual_git}\n", encoding="utf-8")
    profile = build_provider_isolation_profile(
        run_root=run,
        sealed_root=sealed,
        control_root=control,
        raw_results_root=raw,
        protected_roots=[gitfile, actual_git, source],
        other_run_roots=[other],
        argv=["/usr/bin/true"],
        environment={"PATH": os.environ.get("PATH", "")},
    )
    denied: list[Path] = []

    def deny(_profile: object, target: Path) -> bool:
        denied.append(target)
        return True

    monkeypatch.setattr(fixture_module.sys, "platform", "darwin")
    monkeypatch.setattr(fixture_module, "_sandbox_denies", deny)
    monkeypatch.setattr(
        fixture_module,
        "run_provider_isolated",
        lambda _profile, argv, **_kwargs: subprocess.CompletedProcess(
            list(argv), 126, "", "ISOLATION_REFUSED\n"
        ),
    )

    result = probe_provider_isolation(profile)

    assert gitfile.resolve() in denied
    assert f'(literal "{gitfile.resolve()}")' in profile.sandbox_text
    assert f'(subpath "{actual_git.resolve()}")' in profile.sandbox_text
    assert any(path.parent == source.resolve() for path in denied)
    assert result.direct is True
    assert result.other_run is True
    assert not list(raw.glob(".provider-isolation-canary-*"))
    assert not list(other.glob(".provider-isolation-canary-*"))


@pytest.mark.skipif(os.uname().sysname != "Darwin", reason="macOS Seatbelt profile")
def test_fix_round4_real_profile_denies_gitfile_and_directory_roots(
    tmp_path: Path,
) -> None:
    sealed = _write_sealed_test_root(tmp_path / "protected" / "sealed")
    control = tmp_path / "control"
    raw = tmp_path / "raw-results"
    source = tmp_path / "sealed-source"
    run = tmp_path / "runs" / "run-1"
    other = tmp_path / "runs" / "run-2"
    linked = tmp_path / "linked-worktree"
    actual_git = tmp_path / "git-worktree-metadata"
    for path in (control, raw, source, run, other, linked, actual_git):
        path.mkdir(parents=True)
    (control / "control.txt").write_text("control", encoding="utf-8")
    (source / "source.json").write_text("{}", encoding="utf-8")
    gitfile = linked / ".git"
    gitfile.write_text(f"gitdir: {actual_git}\n", encoding="utf-8")
    profile = build_provider_isolation_profile(
        run_root=run,
        sealed_root=sealed,
        control_root=control,
        raw_results_root=raw,
        protected_roots=[gitfile, actual_git, source],
        other_run_roots=[other],
        argv=["/usr/bin/true"],
        environment={"PATH": os.environ.get("PATH", "")},
    )

    try:
        result = probe_provider_isolation(profile)
    except RuntimeError as error:
        if "sandbox_apply: Operation not permitted" in str(error):
            pytest.skip("nested Seatbelt is unavailable inside the test sandbox")
        raise

    assert f'(literal "{gitfile.resolve()}")' in profile.sandbox_text
    assert f'(subpath "{actual_git.resolve()}")' in profile.sandbox_text
    assert all(
        (
            result.direct,
            result.parent,
            result.symlink,
            result.hardlink,
            result.environment,
            result.other_run,
            result.add_dir,
        )
    )
    assert all(denied for _, denied in result.protected_root_results)


@pytest.mark.parametrize("root_kind", ["symlink", "fifo"])
def test_fix_round4_protected_root_non_regular_types_fail_closed(
    tmp_path: Path, root_kind: str
) -> None:
    sealed = _write_sealed_test_root(tmp_path / "protected" / "sealed")
    control = tmp_path / "control"
    raw = tmp_path / "raw-results"
    run = tmp_path / "runs" / "run-1"
    other = tmp_path / "runs" / "run-2"
    for path in (control, raw, run, other):
        path.mkdir(parents=True)
    target = tmp_path / "root-target"
    target.mkdir()
    unusual = tmp_path / "protected-root"
    if root_kind == "symlink":
        unusual.symlink_to(target, target_is_directory=True)
    else:
        os.mkfifo(unusual)

    profile = build_provider_isolation_profile(
        run_root=run,
        sealed_root=sealed,
        control_root=control,
        raw_results_root=raw,
        protected_roots=[unusual],
        other_run_roots=[other],
        argv=["/usr/bin/true"],
        environment={"PATH": os.environ.get("PATH", "")},
    )

    assert "isolation.protected-root-type" in {issue.code for issue in profile.issues}
    assert profile.executable is False


def test_fix_round4_protected_directory_scan_error_cleans_created_canaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sealed = _write_sealed_test_root(tmp_path / "protected" / "sealed")
    control = tmp_path / "control"
    raw = tmp_path / "raw-results"
    faulty = tmp_path / "faulty-protected"
    run = tmp_path / "runs" / "run-1"
    other = tmp_path / "runs" / "run-2"
    for path in (control, raw, faulty, run, other):
        path.mkdir(parents=True)
    (control / "control.txt").write_text("control", encoding="utf-8")
    profile = build_provider_isolation_profile(
        run_root=run,
        sealed_root=sealed,
        control_root=control,
        raw_results_root=raw,
        protected_roots=[faulty],
        other_run_roots=[other],
        argv=["/usr/bin/true"],
        environment={"PATH": os.environ.get("PATH", "")},
    )
    original_rglob = Path.rglob

    def failing_rglob(path: Path, pattern: str):
        if path == faulty.resolve():
            raise PermissionError("test-only protected scan denial")
        return original_rglob(path, pattern)

    monkeypatch.setattr(fixture_module.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "rglob", failing_rglob)

    with pytest.raises(RuntimeError, match="protected root canary"):
        probe_provider_isolation(profile)

    assert not list(raw.glob(".provider-isolation-canary-*"))
    assert not list(other.glob(".provider-isolation-canary-*"))


def test_fix_round4_failed_directory_canary_write_cleans_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sealed = _write_sealed_test_root(tmp_path / "protected" / "sealed")
    control = tmp_path / "control"
    raw = tmp_path / "raw-results"
    run = tmp_path / "runs" / "run-1"
    other = tmp_path / "runs" / "run-2"
    for path in (control, raw, run, other):
        path.mkdir(parents=True)
    (control / "control.txt").write_text("control", encoding="utf-8")
    profile = build_provider_isolation_profile(
        run_root=run,
        sealed_root=sealed,
        control_root=control,
        raw_results_root=raw,
        protected_roots=[],
        other_run_roots=[other],
        argv=["/usr/bin/true"],
        environment={"PATH": os.environ.get("PATH", "")},
    )
    monkeypatch.setattr(fixture_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        fixture_module.os,
        "fsync",
        lambda _descriptor: (_ for _ in ()).throw(
            OSError("test-only canary fsync failure")
        ),
    )

    with pytest.raises(RuntimeError, match="protected root canary"):
        probe_provider_isolation(profile)

    assert not list(raw.glob(".provider-isolation-canary-*"))


def test_fix_round5_derives_real_linked_worktree_git_surfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gitfile = REPO_ROOT / ".git"
    assert gitfile.is_file()
    monkeypatch.setenv("GIT_DIR", "/test-only/untrusted-git-dir")
    monkeypatch.setenv("GIT_COMMON_DIR", "/test-only/untrusted-common-dir")

    surfaces = fixture_module.derive_repo_git_surfaces(REPO_ROOT)
    trusted_env = {
        "PATH": "/usr/bin:/bin",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }

    assert surfaces[0] == gitfile
    assert surfaces[1] == Path(
        subprocess.run(
            [
                "/usr/bin/git",
                "rev-parse",
                "--path-format=absolute",
                "--absolute-git-dir",
            ],
            cwd=REPO_ROOT,
            env=trusted_env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    assert surfaces[2] == Path(
        subprocess.run(
            [
                "/usr/bin/git",
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ],
            cwd=REPO_ROOT,
            env=trusted_env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    assert len(set(surfaces)) == 3


@pytest.mark.parametrize("git_entry_kind", ["symlink", "fifo"])
def test_fix_round5_git_surface_untrusted_entry_types_fail_closed(
    tmp_path: Path, git_entry_kind: str
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git_entry = repo / ".git"
    target = tmp_path / "git-target"
    target.mkdir()
    if git_entry_kind == "symlink":
        git_entry.symlink_to(target, target_is_directory=True)
    else:
        os.mkfifo(git_entry)

    with pytest.raises(ValueError, match="git-surface"):
        fixture_module.derive_repo_git_surfaces(repo)


def test_fix_round5_git_surface_malformed_pointer_and_command_error_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").write_text("not-a-gitdir\n", encoding="utf-8")

    with pytest.raises(ValueError, match="git-surface"):
        fixture_module.derive_repo_git_surfaces(repo)

    monkeypatch.setattr(
        fixture_module.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["git", "rev-parse"], 1, "", "test-only git error"
        ),
    )
    with pytest.raises(ValueError, match="git-surface"):
        fixture_module.derive_repo_git_surfaces(REPO_ROOT)


def test_fix_round5_git_surface_pointer_boundary_and_owner_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    inside = repo / "metadata" / "worktrees" / "unit"
    inside.mkdir(parents=True)
    common = repo / "metadata"
    (repo / ".git").write_text(f"gitdir: {inside}\n", encoding="utf-8")

    def fake_git(arguments: list[str], **_kwargs: object):
        output = common if arguments[-1] == "--git-common-dir" else inside
        return subprocess.CompletedProcess(arguments, 0, f"{output}\n", "")

    monkeypatch.setattr(fixture_module.subprocess, "run", fake_git)
    with pytest.raises(ValueError, match="git-surface"):
        fixture_module.derive_repo_git_surfaces(repo)

    monkeypatch.setattr(fixture_module.os, "geteuid", lambda: os.getuid() + 1)
    with pytest.raises(ValueError, match="git-surface"):
        fixture_module.derive_repo_git_surfaces(REPO_ROOT)


def test_fix_round5_git_surface_scan_error_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_lstat = Path.lstat

    def failing_lstat(path: Path):
        if path == REPO_ROOT / ".git":
            raise PermissionError("test-only git surface scan failure")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", failing_lstat)
    with pytest.raises(ValueError, match="git-surface"):
        fixture_module.derive_repo_git_surfaces(REPO_ROOT)


def test_fix_round5_git_surface_symlinked_gitdir_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    common = tmp_path / "common"
    actual = common / "worktrees" / "unit"
    actual.mkdir(parents=True)
    linked = tmp_path / "linked-gitdir"
    linked.symlink_to(actual, target_is_directory=True)
    (repo / ".git").write_text(f"gitdir: {linked}\n", encoding="utf-8")

    def fake_git(arguments: list[str], **_kwargs: object):
        output = common if arguments[-1] == "--git-common-dir" else linked
        return subprocess.CompletedProcess(arguments, 0, f"{output}\n", "")

    monkeypatch.setattr(fixture_module.subprocess, "run", fake_git)
    with pytest.raises(ValueError, match="git-surface"):
        fixture_module.derive_repo_git_surfaces(repo)


def test_fix_round5_candidate_evaluator_automatically_protects_git_surfaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sealed = _write_sealed_test_root(tmp_path / "protected" / "sealed")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    source = candidate / "candidate.py"
    source.write_text("pass\n", encoding="utf-8")
    captured: list[object] = []

    def isolated_launch(
        profile: object,
        argv: tuple[str, ...] | list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        captured.append(profile)
        return subprocess.CompletedProcess(
            list(argv), 0, '{"allowed":false,"status":"pending"}\n', ""
        )

    monkeypatch.setattr(fixture_module, "run_provider_isolated", isolated_launch)
    result = fixture_module._run_candidate_adapter(
        candidate,
        sealed,
        source=source,
        scenario={},
    )

    assert result == {"allowed": False, "status": "pending"}
    profile = captured[0]
    assert set(fixture_module.derive_repo_git_surfaces(REPO_ROOT)) <= set(
        profile.protected_roots
    )


@pytest.mark.skipif(os.uname().sysname != "Darwin", reason="macOS Seatbelt profile")
def test_fix_round5_system_candidate_profile_denies_every_production_git_surface(
    tmp_path: Path,
) -> None:
    sealed = _write_sealed_test_root(tmp_path / "protected" / "sealed")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    raw = tmp_path / "raw-results"
    raw.mkdir()
    profile = fixture_module._build_candidate_isolation_profile(
        candidate=candidate,
        sealed_root=sealed,
        raw_results=raw,
        runtime_capsule_root=Path(
            str(
                fixture_module.evaluator_runtime_capsule_manifest(
                    fixture_module.EVALUATOR_PYTHON,
                    str(fixture_module.evaluator_python_runtime_identity()["version"]),
                )["root"]
            )
        ),
        argv=["/usr/bin/true"],
    )

    try:
        denied = {
            surface: fixture_module._sandbox_denies(profile, surface)
            for surface in fixture_module.derive_repo_git_surfaces(REPO_ROOT)
        }
    except RuntimeError as error:
        if "sandbox_apply: Operation not permitted" in str(error):
            pytest.skip("nested Seatbelt is unavailable inside the test sandbox")
        raise

    assert denied
    assert all(denied.values())


def test_task2_red_tracked_public_tree_contains_no_sealed_plaintext() -> None:
    forbidden = (
        "SEALED_RUBRIC_PHRASE",
        "consecutive-failure-recovery-answer",
        "delayed-response-race-answer",
        "rapid-double-submit-answer",
        "malformed-response-answer",
        "tenant-time-action-audit-answer",
    )
    public_bytes = b"\n".join(
        path.read_bytes() for path in FIXTURE_ROOT.rglob("*") if path.is_file()
    )

    assert all(token.encode() not in public_bytes for token in forbidden)


def test_task2_red_public_fixtures_remove_old_method_leakage() -> None:
    public_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in FIXTURE_ROOT.rglob("*")
        if path.is_file() and "public" in path.parts
    ).lower()
    for forbidden in (
        "故意遗漏",
        "intentionally_missing_acceptance_id",
        "四个角色",
        "majority",
        "quorum",
        "veto",
        "operator-decision",
    ):
        assert forbidden.lower() not in public_text


def test_fix_round1_requirement_placeholder_cannot_receive_credit(
    tmp_path: Path,
) -> None:
    sealed = _write_sealed_test_root(tmp_path / "sealed")
    payload_path = sealed / "requirement-contract-ambiguity.sealed.json"
    payload = {
        "schema": "ai-sdlc-v2-benefit-sealed-evaluator/v2",
        "fixture_id": "requirement-contract-ambiguity",
        "criteria": [
            {
                "id": "r-literal",
                "weight": 1,
                "severity": "blocker",
                "kind": "json_literal",
                "path": ["decisions", "version_binding"],
                "expected": "required",
            },
            {
                "id": "r-subset",
                "weight": 1,
                "severity": "important",
                "kind": "json_set_contains",
                "path": ["state_machine", "terminal_states"],
                "expected": ["approved", "rejected", "withdrawn"],
            },
            {
                "id": "r-relation",
                "weight": 1,
                "severity": "important",
                "kind": "json_relation",
                "path": ["failure_policy"],
                "relation": "committed_fact_survives_notification_failure",
            },
            {
                "id": "r-command",
                "weight": 1,
                "severity": "important",
                "kind": "verification_command",
                "path": ["verification", "commands"],
                "expected": ["python -m unittest -q"],
            },
            {
                "id": "r-enum",
                "weight": 1,
                "severity": "important",
                "kind": "json_enum",
                "path": ["open_questions", "status"],
                "allowed": ["none", "blocked"],
            },
            {
                "id": "r-contradiction",
                "weight": 1,
                "severity": "important",
                "kind": "json_no_contradiction",
                "path": [],
                "forbidden": ["wrong-but-nonempty"],
            },
        ],
    }
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload_path.write_bytes(data)
    manifest_path = sealed / "sealed-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    entry = next(
        item
        for item in manifest["entries"]
        if item["fixture_id"] == "requirement-contract-ambiguity"
    )
    entry["sha256"] = sha256(data).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    )
    commitments_path = sealed / "candidate-commitments.json"
    commitments = json.loads(commitments_path.read_text())
    commitments["sealed_manifest_sha256"] = sha256(
        manifest_path.read_bytes()
    ).hexdigest()
    commitments_path.write_text(
        json.dumps(commitments, sort_keys=True, separators=(",", ":"))
    )
    candidate = tmp_path / "candidate"
    (candidate / "benchmark-task").mkdir(parents=True)
    (candidate / "benchmark-task" / "design-contract.json").write_text(
        json.dumps(
            {
                "decisions": {"version_binding": "wrong-but-nonempty"},
                "state_machine": {"terminal_states": ["approved"]},
                "failure_policy": {"description": "nonempty"},
                "verification": {"commands": ["echo ok"]},
                "open_questions": {"status": "guessed"},
            }
        )
    )

    result = evaluate_fixture("requirement-contract-ambiguity", candidate, sealed)

    assert result.external_verified_delivery is False
    assert result.weighted_ac_coverage == 0
    assert set(result.failed_criteria) == {
        "r-literal",
        "r-subset",
        "r-relation",
        "r-command",
        "r-enum",
        "r-contradiction",
    }
    payload["implementation_hint"] = "must be rejected"
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload_path.write_bytes(data)
    entry["sha256"] = sha256(data).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    )
    with pytest.raises(ValueError, match="closed"):
        evaluate_fixture("requirement-contract-ambiguity", candidate, sealed)


def test_fix_round1_materialized_intent_map_is_independent_and_roundtrips(
    tmp_path: Path,
) -> None:
    sealed = _write_sealed_test_root(tmp_path / "sealed")
    intent_map = {
        "schema": "ai-sdlc-v2-benefit-intent-map/v2",
        "questions": {
            "release.emergency-authority": {
                "answer": {"authority": "service_owner", "second_approval": True},
                "delay_ms": 0,
            }
        },
        "approvals": ["design-contract"],
    }
    intent_bytes = json.dumps(
        intent_map, sort_keys=True, separators=(",", ":")
    ).encode()
    (sealed / "intent-map.json").write_bytes(intent_bytes)
    manifest = json.loads((sealed / "sealed-manifest.json").read_text())
    manifest["intent_map"] = {
        "path": "intent-map.json",
        "sha256": sha256(intent_bytes).hexdigest(),
    }
    (sealed / "sealed-manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    )

    service = FrozenIntentApprovalService.from_sealed_root(
        sealed, tmp_path / "events.jsonl"
    )

    assert service.answer("run-1", "release.emergency-authority") == {
        "status": "answered",
        "answer": {"authority": "service_owner", "second_approval": True},
    }
    assert "intent-map.json" not in (
        FIXTURE_ROOT / "sealed-commitments.json"
    ).read_text(encoding="utf-8")


def test_fix_round1_security_oracle_reports_finding_confusion_metrics(
    tmp_path: Path,
) -> None:
    sealed = _write_sealed_test_root(tmp_path / "protected" / "sealed")
    payload_path = sealed / "multi-tenant-security-review.sealed.json"
    scenarios = [
        (
            "tenant",
            {
                "actor_id": "a",
                "actor_tenant": "b",
                "roles": ["admin"],
                "request_id": "r",
                "request_tenant": "a",
                "requester_id": "u",
                "expires_at": "2030-01-01T00:00:00+00:00",
                "now": "2029-01-01T00:00:00+00:00",
            },
        ),
        (
            "self_approval",
            {
                "actor_id": "u",
                "actor_tenant": "a",
                "roles": ["approver"],
                "request_id": "r",
                "request_tenant": "a",
                "requester_id": "u",
                "expires_at": "2030-01-01T00:00:00+00:00",
                "now": "2029-01-01T00:00:00+00:00",
            },
        ),
        (
            "nonpending",
            {
                "actor_id": "a",
                "actor_tenant": "a",
                "roles": ["approver"],
                "request_id": "r",
                "request_tenant": "a",
                "requester_id": "u",
                "expires_at": "2030-01-01T00:00:00+00:00",
                "now": "2029-01-01T00:00:00+00:00",
                "status": "approved",
            },
        ),
        (
            "unknown_role",
            {
                "actor_id": "a",
                "actor_tenant": "a",
                "roles": ["operator"],
                "request_id": "r",
                "request_tenant": "a",
                "requester_id": "u",
                "expires_at": "2030-01-01T00:00:00+00:00",
                "now": "2029-01-01T00:00:00+00:00",
            },
        ),
        (
            "unknown_action",
            {
                "actor_id": "a",
                "actor_tenant": "a",
                "roles": ["approver"],
                "request_id": "r",
                "request_tenant": "a",
                "requester_id": "u",
                "expires_at": "2030-01-01T00:00:00+00:00",
                "now": "2029-01-01T00:00:00+00:00",
                "action": "escalate",
            },
        ),
        (
            "audit_failure",
            {
                "actor_id": "a",
                "actor_tenant": "a",
                "roles": ["approver"],
                "request_id": "r",
                "request_tenant": "a",
                "requester_id": "u",
                "expires_at": "2030-01-01T00:00:00+00:00",
                "now": "2029-01-01T00:00:00+00:00",
                "audit_mode": "failing",
            },
        ),
    ]
    payload = {
        "schema": "ai-sdlc-v2-benefit-sealed-evaluator/v2",
        "fixture_id": "multi-tenant-security-review",
        "criteria": [
            {
                "id": root,
                "weight": 1,
                "severity": "blocker",
                "kind": "security_oracle",
                "path": "benchmark-task/access_control.py",
                "root_cause": root,
                "scenario": scenario,
                "expected": {"allowed": False, "status": "pending"},
            }
            for root, scenario in scenarios
        ],
        "root_causes": [root for root, _ in scenarios],
        "held_out_variant_classes": ["tenant", "time", "action", "audit"],
    }
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload_path.write_bytes(data)
    manifest_path = sealed / "sealed-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    next(
        item
        for item in manifest["entries"]
        if item["fixture_id"] == "multi-tenant-security-review"
    )["sha256"] = sha256(data).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    )
    commitments_path = sealed / "candidate-commitments.json"
    commitments = json.loads(commitments_path.read_text())
    commitments["sealed_manifest_sha256"] = sha256(
        manifest_path.read_bytes()
    ).hexdigest()
    commitments_path.write_text(
        json.dumps(commitments, sort_keys=True, separators=(",", ":"))
    )
    prepared = prepare_fixture("multi-tenant-security-review", tmp_path / "candidate")
    (prepared.root / "benchmark-task" / "findings.json").write_text(
        json.dumps(
            {"findings": [{"root_cause": "tenant"}, {"root_cause": "unknown-role"}]}
        )
    )

    try:
        result = evaluate_fixture("multi-tenant-security-review", prepared.root, sealed)
    except (RuntimeError, fixture_module.EvaluatorNoGoError) as error:
        if (
            "sandbox_apply: Operation not permitted" in str(error)
            or getattr(error, "code", "") == "adapter-sandbox"
        ):
            pytest.skip("nested Seatbelt is unavailable inside the test sandbox")
        raise

    assert result.root_cause_count == 6
    assert result.finding_true_positive_count == 1
    assert result.finding_false_positive_count == 1
    assert result.finding_false_negative_count == 5
    assert result.finding_precision == 0.5
    assert result.finding_recall == pytest.approx(1 / 6)
    assert result.severe_finding_miss_count == 5


def test_fix_round1_frontend_is_locked_and_real_browser_e2e(tmp_path: Path) -> None:
    prepared = prepare_fixture("frontend-recovery-delivery", tmp_path / "frontend")
    task = prepared.root / "benchmark-task"
    package = json.loads((task / "package.json").read_text())
    lock = json.loads((task / "package-lock.json").read_text())
    environment = json.loads((task / "environment-lock.json").read_text())
    program = json.loads((task / "program-manifest.json").read_text())

    assert "dependencies" in package and "devDependencies" in package
    assert lock["lockfileVersion"] == 3
    assert len(environment["preinstalled_dependency_tree_sha256"]) == 64
    assert len(environment["node"]["executable_identity_sha256"]) == 64
    assert len(environment["browser"]["executable_identity_sha256"]) == 64
    assert set(program) == {
        "schema",
        "solution_target",
        "component_identity",
        "theme_identity",
        "tooling_identity",
        "application_identity",
        "quality_identity",
    }
    try:
        result = run_frontend_browser_e2e(task, _test_browser_program())
    except PermissionError:
        pytest.skip("nested sandbox blocks the local same-origin browser server")
    assert set(result["scenarios"]) == {"test-only-browser-scenario"}
    assert result["executed_with_real_browser"] is True
    assert result["same_origin"] is True
    assert result["scenarios"]["test-only-browser-scenario"] is True
    assert result["behavior_checks"] == {
        "field_rendering": True,
        "filtering": True,
    }
    assert all(result["scenarios"].values())
    assert result["console_errors"] == []
    assert result["basic_accessibility"] is True
    dependency_root = os.environ.get("AI_SDLC_BENCHMARK_DEPENDENCY_ROOT")
    if dependency_root:
        node = subprocess.run(
            ["which", "node"], check=True, capture_output=True, text=True
        ).stdout.strip()
        assert (
            validate_frontend_runtime(
                task,
                Path(dependency_root),
                node_binary=Path(node),
                browser_binary=Path(
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
                ),
            )
            == []
        )


def _fake_system_chrome(tmp_path: Path) -> Path:
    browser = (
        tmp_path
        / "Applications"
        / "Google Chrome.app"
        / "Contents"
        / "MacOS"
        / "Google Chrome"
    )
    browser.parent.mkdir(parents=True)
    browser.write_bytes(b"fake-browser")
    browser.chmod(0o755)
    return browser


def test_browser_launch_prefers_frozen_playwright_headless_shell(
    tmp_path: Path,
) -> None:
    headless = tmp_path / "chromium_headless_shell-1234" / "chrome-headless-shell"
    headless.parent.mkdir()
    headless.write_bytes(b"frozen-headless-shell")
    headless.chmod(0o755)
    system_chrome = _fake_system_chrome(tmp_path)

    command = fixture_module.build_frontend_browser_command(
        url="http://127.0.0.1:1/test",
        user_data_dir=tmp_path / "profile",
        environment={
            "AI_SDLC_BENCHMARK_PLAYWRIGHT_HEADLESS_SHELL": str(headless),
            "AI_SDLC_BENCHMARK_PLAYWRIGHT_HEADLESS_SHELL_SHA256": sha256(
                headless.read_bytes()
            ).hexdigest(),
            "AI_SDLC_BENCHMARK_BROWSER": str(system_chrome),
        },
    )

    assert command[0] == str(headless)
    assert "--use-mock-keychain" in command


def test_system_chrome_fallback_uses_non_keychain_arguments(tmp_path: Path) -> None:
    system_chrome = _fake_system_chrome(tmp_path)

    command = fixture_module.build_frontend_browser_command(
        url="http://127.0.0.1:1/test",
        user_data_dir=tmp_path / "profile",
        environment={
            "AI_SDLC_BENCHMARK_BROWSER": str(system_chrome),
            "AI_SDLC_BENCHMARK_BROWSER_SHA256": sha256(
                system_chrome.read_bytes()
            ).hexdigest(),
        },
    )

    assert command[0] == str(system_chrome)
    assert "--use-mock-keychain" in command
    assert "--password-store=basic" in command


def test_system_chrome_timeout_without_complete_result_remains_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = prepare_fixture("frontend-recovery-delivery", tmp_path / "frontend")
    task = prepared.root / "benchmark-task"
    system_chrome = _fake_system_chrome(tmp_path)
    monkeypatch.setenv("AI_SDLC_BENCHMARK_BROWSER", str(system_chrome))
    monkeypatch.setenv(
        "AI_SDLC_BENCHMARK_BROWSER_SHA256",
        sha256(system_chrome.read_bytes()).hexdigest(),
    )
    monkeypatch.delenv("AI_SDLC_BENCHMARK_PLAYWRIGHT_HEADLESS_SHELL", raising=False)
    monkeypatch.delenv("AI_SDLC_BENCHMARK_PLAYWRIGHT_MODULE", raising=False)

    def timeout(command: list[str], **_kwargs: object) -> object:
        assert command[0] == str(system_chrome)
        assert "--use-mock-keychain" in command
        raise subprocess.TimeoutExpired(
            command, 30, output=b"<html><body>incomplete</body></html>", stderr=b""
        )

    monkeypatch.setattr(fixture_module.subprocess, "run", timeout)

    with pytest.raises(RuntimeError, match="real-browser acceptance process failed"):
        run_frontend_browser_e2e(task, _test_browser_program())


def test_playwright_adapter_receives_the_same_closed_browser_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = prepare_fixture("frontend-recovery-delivery", tmp_path / "frontend")
    task = prepared.root / "benchmark-task"
    headless = tmp_path / "chromium_headless_shell-1234" / "chrome-headless-shell"
    headless.parent.mkdir()
    headless.write_bytes(b"frozen-headless-shell")
    headless.chmod(0o755)
    module = tmp_path / "playwright.mjs"
    module.write_text("export const chromium = {};\n", encoding="utf-8")
    monkeypatch.setenv("AI_SDLC_BENCHMARK_PLAYWRIGHT_MODULE", str(module))
    monkeypatch.setenv("AI_SDLC_BENCHMARK_PLAYWRIGHT_HEADLESS_SHELL", str(headless))
    monkeypatch.setenv(
        "AI_SDLC_BENCHMARK_PLAYWRIGHT_HEADLESS_SHELL_SHA256",
        sha256(headless.read_bytes()).hexdigest(),
    )
    launches: list[list[str]] = []

    def complete(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        launches.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                [
                    {
                        "passed": True,
                        "basic_accessibility": True,
                        "console_errors": [],
                        "behavior_checks": {
                            "field_rendering": True,
                            "filtering": True,
                        },
                    }
                ]
            ),
            "",
        )

    monkeypatch.setattr(fixture_module.subprocess, "run", complete)

    result = run_frontend_browser_e2e(task, _test_browser_program())

    assert result["executed_with_real_browser"] is True
    assert len(launches) == 1 and launches[0][0] == "node"
    assert "args:JSON.parse(rawSafetyArgs)" in launches[0][3]
    assert "--use-mock-keychain" in json.loads(launches[0][-1])


def test_fix_round1_frontend_held_out_harness_is_not_provider_readable(
    tmp_path: Path,
) -> None:
    prepared = prepare_fixture("frontend-recovery-delivery", tmp_path / "frontend")
    task = prepared.root / "benchmark-task"
    public_bytes = b"\n".join(
        path.read_bytes()
        for path in task.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(prepared.root).parts
    )

    assert not (task / "tests" / "browser-harness.html").exists()
    for marker in (
        b"consecutive_failure_recovery",
        b"delayed_race",
        b"rapid_double_click",
        b"malformed_response",
    ):
        assert marker not in public_bytes


def test_fix_round1_frontend_v2_scores_only_real_browser_behavior(
    tmp_path: Path,
) -> None:
    if not os.environ.get("AI_SDLC_BENCHMARK_PLAYWRIGHT_MODULE"):
        pytest.skip("exact real-browser runner is verified outside the nested sandbox")
    sealed = _write_sealed_test_root(tmp_path / "protected" / "sealed")
    payload_path = sealed / "frontend-recovery-delivery.sealed.json"
    payload = {
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
                "id": "FRD-H1",
                "weight": 1,
                "severity": "blocker",
                "kind": "frontend_browser_suite",
                "expected": {"scenarios": {"test-only-missing": True}},
            },
        ],
    }
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload_path.write_bytes(data)
    manifest_path = sealed / "sealed-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    next(
        item
        for item in manifest["entries"]
        if item["fixture_id"] == "frontend-recovery-delivery"
    )["sha256"] = sha256(data).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    )
    prepared = prepare_fixture("frontend-recovery-delivery", tmp_path / "candidate")

    try:
        result = evaluate_fixture("frontend-recovery-delivery", prepared.root, sealed)
    except PermissionError:
        pytest.skip("nested sandbox blocks the local same-origin browser server")

    assert result.external_verified_delivery is False
    assert result.satisfied_criteria == ("FRD-AC001", "FRD-AC002", "FRD-AC006")
    assert result.failed_criteria == ("FRD-H1",)


def test_fix_round1_canonical_contract_rejects_recursive_semantic_extras(
    tmp_path: Path,
) -> None:
    prepared = prepare_fixture("requirement-contract-ambiguity", tmp_path / "fixture")
    contract = json.loads(
        (prepared.root / "benchmark-task" / "input-contract.json").read_text()
    )
    contract["semantics"]["requirement"]["answer"] = "leak"
    with pytest.raises(ValueError, match="closed"):
        normalized_semantic_view(contract)
    del contract["semantics"]["requirement"]["answer"]
    contract["semantics"]["requirement"]["acceptance_criteria"] = ["AC-999"]
    with pytest.raises(ValueError, match="closed"):
        normalized_semantic_view(contract)


def test_fix_round1_leak_scanner_uses_payload_inventory_and_git_objects(
    tmp_path: Path,
) -> None:
    sealed = _write_sealed_test_root(tmp_path / "sealed")
    phrase = "opaque evaluator phrase unique 61371"
    payload_path = sealed / "requirement-contract-ambiguity.sealed.json"
    payload = json.loads(payload_path.read_text())
    payload["rubric_boundary"] = phrase
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload_path.write_bytes(data)
    manifest_path = sealed / "sealed-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    next(
        item
        for item in manifest["entries"]
        if item["fixture_id"] == "requirement-contract-ambiguity"
    )["sha256"] = sha256(data).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    )
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=candidate, check=True)
    leaked = candidate / f"proof-{phrase.replace(' ', '-')}.bin"
    leaked.write_bytes(b"\x00" + phrase.encode() + b"\xff")
    subprocess.run(["git", "add", leaked.name], cwd=candidate, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@invalid",
            "commit",
            "-qm",
            "leak",
        ],
        cwd=candidate,
        check=True,
    )
    leaked.unlink()

    issues = scan_candidate_for_sealed_leak(candidate, manifest_path)

    codes = {issue.code for issue in issues}
    assert "fixture.leak.path-name" in codes
    assert "fixture.leak.git-object" in codes
    assert all(phrase not in issue.message for issue in issues)


def test_fix_round1_leak_scanner_fails_closed_when_inventory_scan_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sealed = _write_sealed_test_root(tmp_path / "sealed")
    candidate = tmp_path / "candidate"
    candidate.mkdir()

    def fail_inventory(_root: Path) -> set[tuple[int, int]]:
        raise OSError("protected inventory unavailable")

    monkeypatch.setattr(fixture_module, "_protected_inodes", fail_inventory)

    issues = scan_candidate_for_sealed_leak(candidate, sealed / "sealed-manifest.json")

    assert [(issue.code, issue.message) for issue in issues] == [
        ("fixture.leak.scan-error", "sealed-inventory")
    ]


@pytest.mark.skipif(os.uname().sysname != "Darwin", reason="macOS Seatbelt profile")
def test_fix_round1_candidate_adapter_uses_dynamic_isolation_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sealed = _write_sealed_test_root(tmp_path / "protected" / "sealed")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    source = candidate / "candidate.py"
    source.write_text("pass\n")
    launches: list[tuple[str, ...]] = []

    def isolated_launch(
        _profile: object,
        argv: tuple[str, ...] | list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        launches.append(tuple(argv))
        return subprocess.CompletedProcess(
            list(argv), 0, '{"allowed":false,"status":"pending"}\n', ""
        )

    monkeypatch.setattr(fixture_module, "run_provider_isolated", isolated_launch)

    result = fixture_module._run_candidate_adapter(
        candidate,
        sealed,
        source=source,
        scenario={},
    )

    assert result == {"allowed": False, "status": "pending"}
    assert len(launches) == 1


def test_fix_round1_isolation_is_closed_over_all_protected_roots(
    tmp_path: Path,
) -> None:
    sealed = _write_sealed_test_root(tmp_path / "protected" / "sealed")
    control = tmp_path / "control"
    source_git = tmp_path / "source.git"
    raw = tmp_path / "raw-results"
    run = tmp_path / "runs" / "run-1"
    other = tmp_path / "runs" / "run-2"
    for path in (control, source_git, raw, run, other):
        path.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    os.symlink(outside, run / "outside-link")
    linked = run / "hardlinked"
    os.link(outside, linked)

    profile = build_provider_isolation_profile(
        run_root=run,
        sealed_root=sealed,
        control_root=control,
        raw_results_root=raw,
        protected_roots=[source_git],
        other_run_roots=[other],
        argv=["codex", "exec", f"--add-dir={run / 'missing' / '..' / '..'}"],
        environment={"PATH": os.environ.get("PATH", "")},
    )

    codes = {issue.code for issue in profile.issues}
    assert {"isolation.symlink", "isolation.hardlink", "isolation.add-dir"} <= codes
    for root in (sealed, sealed.parent, control, source_git, raw, other):
        assert str(root.resolve()) in profile.sandbox_text
