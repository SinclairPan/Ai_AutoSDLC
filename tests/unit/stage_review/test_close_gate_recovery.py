from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from tests.unit.stage_review.test_close_gate import (
    _attestation_path,
    _frontend_skip_options,
    _prepare_requirement_close,
    _start_requirement,
)
from tests.unit.test_frontend_evidence_loop import (
    _write_closed_implementation_loop,
    _write_work_item,
)

import ai_sdlc.core.stage_review.close_gate as close_gate_module
from ai_sdlc.core.frontend_evidence_loop import skip_frontend_evidence_loop
from ai_sdlc.core.requirement_loop import (
    RequirementFreezeOptions,
    freeze_requirement_loop,
)
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.close_gate import (
    StageCloseGateway,
)
from ai_sdlc.core.stage_review.close_gate import (
    _read_stage_close_gate_attestations as read_stage_close_gate_attestations,
)
from ai_sdlc.core.stage_review.close_gate_models import StageCloseGateAttestation
from ai_sdlc.core.stage_review.close_gate_store import (
    _gate_attestation_is_current as gate_attestation_is_current,
)
from ai_sdlc.core.stage_review.close_gate_store import (
    _latest_gate_attestation_id as latest_gate_attestation_id,
)
from ai_sdlc.core.stage_review.close_gate_store import (
    _read_gate_operation as read_gate_operation,
)


def test_shadow_failure_preserves_original_result_and_recovers(tmp_path: Path) -> None:
    _start_requirement(tmp_path, "req-shadow-failure")
    with patch(
        "ai_sdlc.core.stage_review.close_gate._persist_attestation",
        side_effect=OSError("simulated Shadow storage failure"),
    ):
        result = freeze_requirement_loop(
            RequirementFreezeOptions(root=tmp_path, yes=True)
        )
    expected_digest = canonical_digest(
        result.model_dump(mode="json"),
        CanonicalizationPolicy(),
    )

    assert result.status == "ready"
    assert result.loop_status == "closed"
    recovered = freeze_requirement_loop(
        RequirementFreezeOptions(root=tmp_path, yes=True)
    )
    attestations = read_stage_close_gate_attestations(tmp_path)
    assert recovered.result == "Requirement loop is already frozen."
    assert len(attestations) == 1
    assert attestations[0].observation_origin == "close_execution"
    assert attestations[0].result_digest == expected_digest


def test_post_writer_shadow_read_failure_does_not_escape_to_user(
    tmp_path: Path,
) -> None:
    _start_requirement(tmp_path, "req-shadow-read-failure")
    with patch(
        "ai_sdlc.core.stage_review.close_gate.read_gate_operation",
        side_effect=OSError("simulated shared-state read failure"),
    ):
        result = freeze_requirement_loop(
            RequirementFreezeOptions(root=tmp_path, yes=True)
        )

    assert result.status == "ready"
    assert result.loop_status == "closed"
    assert result.result == "Requirement loop frozen."
    recovered = freeze_requirement_loop(
        RequirementFreezeOptions(root=tmp_path, yes=True)
    )
    assert recovered.status == "ready"
    assert len(read_stage_close_gate_attestations(tmp_path)) == 1


def test_partial_advance_outage_is_labeled_as_closed_reconciliation(
    tmp_path: Path,
) -> None:
    _start_requirement(tmp_path, "req-shadow-advance-failure")
    original_advance = close_gate_module.advance_gate_operation
    calls = 0

    def fail_first_advance(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("simulated first advance failure")
        return original_advance(*args, **kwargs)

    with patch.object(
        close_gate_module,
        "advance_gate_operation",
        side_effect=fail_first_advance,
    ):
        first = freeze_requirement_loop(
            RequirementFreezeOptions(root=tmp_path, yes=True)
        )

    replay = freeze_requirement_loop(RequirementFreezeOptions(root=tmp_path, yes=True))
    attestation = read_stage_close_gate_attestations(tmp_path)[0]
    assert first.result == "Requirement loop frozen."
    assert replay.result == "Requirement loop is already frozen."
    assert attestation.result_status == "reconciled"
    assert attestation.observation_origin == "closed_reconciliation"


def test_shadow_attestation_is_content_addressed_and_recoverable(
    tmp_path: Path,
) -> None:
    _start_requirement(tmp_path, "req-shadow")
    freeze_requirement_loop(RequirementFreezeOptions(root=tmp_path, yes=True))
    attestation = read_stage_close_gate_attestations(tmp_path)[0]
    path = _attestation_path(tmp_path, attestation.attestation_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["result_status"] = "blocked"
    path.write_text(json.dumps(payload), encoding="utf-8")

    replay = freeze_requirement_loop(
        RequirementFreezeOptions(root=tmp_path, loop_id="req-shadow", yes=True)
    )
    operation = read_gate_operation(tmp_path, attestation.operation_id)
    assert replay.status == "ready"
    assert operation is not None
    assert operation.last_error_code == "validationerror"
    assert (
        gate_attestation_is_current(
            tmp_path,
            operation,
            tmp_path / attestation.close_artifact_path,
        )
        is False
    )

    path.unlink()
    freeze_requirement_loop(
        RequirementFreezeOptions(root=tmp_path, loop_id="req-shadow", yes=True)
    )
    recovered = read_stage_close_gate_attestations(tmp_path)
    assert len(recovered) == 1
    assert recovered[0].attestation_digest == attestation.attestation_digest


def test_frontend_skip_shadow_failure_recovers_without_changing_replay_result(
    tmp_path: Path,
) -> None:
    work_item = _write_work_item(tmp_path)
    _write_closed_implementation_loop(tmp_path, work_item)
    options = _frontend_skip_options(tmp_path)
    with patch(
        "ai_sdlc.core.stage_review.close_gate._persist_attestation",
        side_effect=OSError("simulated Shadow storage failure"),
    ):
        first = skip_frontend_evidence_loop(options)

    assert first.status == "ready"
    assert read_stage_close_gate_attestations(tmp_path) == ()
    replay = skip_frontend_evidence_loop(options)
    attestations = read_stage_close_gate_attestations(tmp_path)
    assert replay.status == "blocked"
    assert len(attestations) == 1
    assert attestations[0].result_status == "ready"


def test_frontend_skip_recovers_after_complete_shadow_store_outage(
    tmp_path: Path,
) -> None:
    work_item = _write_work_item(tmp_path)
    _write_closed_implementation_loop(tmp_path, work_item)
    options = _frontend_skip_options(tmp_path)
    with (
        patch(
            "ai_sdlc.core.stage_review.close_gate.prepare_gate_operation",
            side_effect=OSError("simulated prepare outage"),
        ),
        patch(
            "ai_sdlc.core.stage_review.close_gate.advance_gate_operation",
            side_effect=OSError("simulated advance outage"),
        ),
        patch(
            "ai_sdlc.core.stage_review.close_gate._persist_attestation",
            side_effect=OSError("simulated attestation outage"),
        ),
    ):
        first = skip_frontend_evidence_loop(options)

    replay = skip_frontend_evidence_loop(options)
    attestations = read_stage_close_gate_attestations(tmp_path)
    assert first.status == "ready"
    assert replay.status == "blocked"
    assert len(attestations) == 1
    assert attestations[0].result_status == "reconciled"
    assert attestations[0].observation_origin == "closed_reconciliation"


def test_shadow_lock_store_outage_falls_back_to_original_writer(
    tmp_path: Path,
) -> None:
    _start_requirement(tmp_path, "req-shadow-lock-outage")
    with patch(
        "ai_sdlc.core.stage_review.close_gate.gate_execution_lock",
        side_effect=OSError("simulated lock store outage"),
    ):
        result = freeze_requirement_loop(
            RequirementFreezeOptions(root=tmp_path, yes=True)
        )

    assert result.status == "ready"
    assert result.result == "Requirement loop frozen."
    freeze_requirement_loop(RequirementFreezeOptions(root=tmp_path, yes=True))
    attestation = read_stage_close_gate_attestations(tmp_path)[0]
    assert attestation.observation_origin == "closed_reconciliation"


def test_shadow_lock_release_failure_preserves_original_result(
    tmp_path: Path,
) -> None:
    _start_requirement(tmp_path, "req-shadow-lock-release")
    with patch(
        "ai_sdlc.core.stage_review.close_gate.gate_execution_lock",
        return_value=_FailingReleaseLock(),
    ):
        result = freeze_requirement_loop(
            RequirementFreezeOptions(root=tmp_path, yes=True)
        )

    assert result.status == "ready"
    assert result.result == "Requirement loop frozen."
    assert len(read_stage_close_gate_attestations(tmp_path)) == 1


def test_shadow_lock_release_failure_preserves_writer_exception(
    tmp_path: Path,
) -> None:
    prepared = _prepare_requirement_close(tmp_path, next_action="writer-failure")
    with (
        patch(
            "ai_sdlc.core.stage_review.close_gate.gate_execution_lock",
            return_value=_FailingReleaseLock(),
        ),
        pytest.raises(RuntimeError, match="original writer failure"),
    ):
        StageCloseGateway().execute(
            prepared,
            lambda: (_ for _ in ()).throw(RuntimeError("original writer failure")),
        )


def test_supersession_cycle_fails_observation_without_hanging_close(
    tmp_path: Path,
) -> None:
    _start_requirement(tmp_path, "req-shadow-cycle")
    freeze_requirement_loop(RequirementFreezeOptions(root=tmp_path, yes=True))
    first = read_stage_close_gate_attestations(tmp_path)[0]
    close_path = tmp_path / first.close_artifact_path
    close_path.write_text(close_path.read_text(encoding="utf-8") + "\n")
    freeze_requirement_loop(RequirementFreezeOptions(root=tmp_path, yes=True))
    second = next(
        item
        for item in read_stage_close_gate_attestations(tmp_path)
        if item.supersedes_attestation_id == first.attestation_id
    )
    first_path = _attestation_path(tmp_path, first.attestation_id)
    payload = json.loads(first_path.read_text(encoding="utf-8"))
    payload["supersedes_attestation_id"] = second.attestation_id
    payload["attestation_digest"] = ""
    cycled = StageCloseGateAttestation.model_validate(payload)
    first_path.write_text(
        json.dumps(cycled.model_dump(mode="json")),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="supersession cycle"):
        latest_gate_attestation_id(tmp_path, first.attestation_id)
    close_path.write_text(close_path.read_text(encoding="utf-8") + "\n")
    result = freeze_requirement_loop(RequirementFreezeOptions(root=tmp_path, yes=True))
    assert result.status == "ready"


class _FailingReleaseLock:
    def __enter__(self) -> _FailingReleaseLock:
        return self

    def __exit__(self, *_args: object) -> None:
        raise OSError("simulated lock release failure")
