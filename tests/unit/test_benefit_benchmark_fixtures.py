from __future__ import annotations

import json
import os
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest

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
    scan_candidate_for_sealed_leak,
    validate_fixture_manifest,
    validate_sealed_commitments,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPO_ROOT / "benchmarks" / "ai-sdlc-v2-benefits" / "fixtures"


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
            {"fixture_id": fixture_id, "path": filename, "sha256": sha256(data).hexdigest()}
        )
    manifest = {
        "schema": "ai-sdlc-v2-benefit-sealed-manifest/v1",
        "lock_id": "unit-test-only",
        "entries": entries,
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
    assert subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=first.root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout == ""
    assert subprocess.run(
        ["git", "rev-list", "--max-parents=0", "HEAD"],
        cwd=first.root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.count("\n") == 1
    assert all(result.matches_expected for result in first.visible_results)


def test_task2_red_frontend_baseline_self_check_accepts_exact_expected_red(
    tmp_path: Path,
) -> None:
    prepared = prepare_fixture("frontend-recovery-delivery", tmp_path / "frontend")
    red = next(item for item in prepared.visible_results if item.command_id == "visible-red")

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
    prepared = prepare_fixture(fixture_id, tmp_path / fixture_id)

    first = evaluate_fixture(fixture_id, prepared.root, sealed)
    second = evaluate_fixture(fixture_id, prepared.root, sealed)

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
        "design-contract" if fixture_id == "requirement-contract-ambiguity" else "implementation"
    )
    if fixture_id == "requirement-contract-ambiguity":
        assert state["canonical_pre_state"] == ["requirement"]
    else:
        assert state["canonical_pre_state"] == ["requirement", "design-contract"]


def test_task2_red_frontend_target_is_identical_and_confirmation_pending(
    tmp_path: Path,
) -> None:
    prepared = prepare_fixture("frontend-recovery-delivery", tmp_path / "frontend")
    manifest = json.loads(
        (prepared.root / "benchmark-task" / "program-manifest.json").read_text()
    )
    input_contract = json.loads(
        (prepared.root / "benchmark-task" / "input-contract.json").read_text()
    )

    assert manifest["arm_confirmation_state"] == {
        "P": "not_applicable",
        "S": "not_applicable",
        "A00": "pending",
        "A10": "pending",
        "A11": "pending",
    }
    assert manifest["solution_target"] == input_contract["solution_target"]
    assert manifest["solution_target"] == {
        "frontend_stack": "vue3",
        "provider_id": "public-primevue",
        "style_pack_id": "modern-saas",
    }
    assert manifest["applies_to_arms"] == list(FIXTURE_IDS[:0]) + [
        "P",
        "S",
        "A00",
        "A10",
        "A11",
    ]


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


def test_task2_red_commitment_pair_verifies_without_exposing_root(tmp_path: Path) -> None:
    sealed = _write_sealed_test_root(tmp_path / "unit-test-only")
    sealed_manifest = json.loads((sealed / "sealed-manifest.json").read_text())
    fixture_digest = fixture_tree_digest(FIXTURE_ROOT)
    evidence_digest = sha256(
        (FIXTURE_ROOT / "evidence-contract.template.json").read_bytes()
    ).hexdigest()
    commitments = {
        "schema": "ai-sdlc-v2-benefit-sealed-commitments/v1",
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
    run.mkdir(parents=True)
    other_run.mkdir(parents=True)
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
def test_task2_red_exact_provider_profile_denies_all_canary_shapes(tmp_path: Path) -> None:
    sealed = _write_sealed_test_root(tmp_path / "protected" / "sealed")
    run = tmp_path / "runs" / "run-1"
    other_run = tmp_path / "runs" / "run-2"
    control = tmp_path / "control"
    run.mkdir(parents=True)
    other_run.mkdir(parents=True)
    control.mkdir()
    profile = build_provider_isolation_profile(
        run_root=run,
        sealed_root=sealed,
        control_root=control,
        other_run_roots=[other_run],
        argv=["/usr/bin/true"],
        environment={"PATH": os.environ.get("PATH", "")},
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
        path.read_bytes()
        for path in FIXTURE_ROOT.rglob("*")
        if path.is_file()
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
