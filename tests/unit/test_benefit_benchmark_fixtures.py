from __future__ import annotations

import json
import os
import subprocess
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
    manifest = {
        "schema": "ai-sdlc-v2-benefit-sealed-manifest/v2",
        "lock_id": "unit-test-only",
        "entries": entries,
        "intent_map": {
            "path": "intent-map.json",
            "sha256": sha256(intent_bytes).hexdigest(),
        },
    }
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    (root / "sealed-manifest.json").write_bytes(manifest_bytes)
    return root


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


def test_task2_red_commitment_pair_verifies_without_exposing_root(
    tmp_path: Path,
) -> None:
    sealed = _write_sealed_test_root(tmp_path / "unit-test-only")
    sealed_manifest = json.loads((sealed / "sealed-manifest.json").read_text())
    fixture_digest = fixture_tree_digest(FIXTURE_ROOT)
    evidence_digest = sha256(
        (FIXTURE_ROOT / "evidence-contract.template.json").read_bytes()
    ).hexdigest()
    commitments = {
        "schema": "ai-sdlc-v2-benefit-sealed-commitments/v2",
        "lock_id": "unit-test-only",
        "sealed_manifest_sha256": sha256(
            (sealed / "sealed-manifest.json").read_bytes()
        ).hexdigest(),
        "fixture_tree_sha256": fixture_digest,
        "fixture_commitment": fixture_digest,
        "evidence_contract_template_sha256": evidence_digest,
        "evidence_contract_commitment": evidence_digest,
        "fixture_payloads": [
            {"fixture_id": item["fixture_id"], "sha256": item["sha256"]}
            for item in sealed_manifest["entries"]
        ],
        "intent_map_sha256": sealed_manifest["intent_map"]["sha256"],
        "publication_state": "sealed-outside-provider-root",
    }
    path = tmp_path / "sealed-commitments.json"
    path.write_text(json.dumps(commitments), encoding="utf-8")

    assert validate_sealed_commitments(path, sealed, FIXTURE_ROOT) == []


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
    prepared = prepare_fixture("multi-tenant-security-review", tmp_path / "candidate")
    (prepared.root / "benchmark-task" / "findings.json").write_text(
        json.dumps(
            {"findings": [{"root_cause": "tenant"}, {"root_cause": "unknown-role"}]}
        )
    )

    try:
        result = evaluate_fixture("multi-tenant-security-review", prepared.root, sealed)
    except RuntimeError as error:
        if "sandbox_apply: Operation not permitted" in str(error):
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
