"""稳定读取并验证 Design Close authority 所依赖的产品工件。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ai_sdlc.core.design_close_enforce_authority import _DesignCloseProof
from ai_sdlc.core.design_contract_models import (
    DesignContractClose,
    DesignContractInput,
    DesignContractReport,
)
from ai_sdlc.core.design_contract_store import DesignContractArtifacts
from ai_sdlc.core.loop_models import LoopStatus
from ai_sdlc.core.scope_authority_store import ScopeAuthorityIntegrityError
from ai_sdlc.core.stable_file_read import read_stable_bytes
from ai_sdlc.core.stage_review.canonical import normalize_repo_path


@dataclass(frozen=True, slots=True)
class _PassableDesignArtifacts:
    input_digest: str
    report_digest: str
    close_digest: str
    report: DesignContractReport
    close: DesignContractClose


def _read_passable_design_artifacts(
    root: Path,
    artifacts: DesignContractArtifacts,
    proof: _DesignCloseProof,
    expected_artifact_digests: tuple[tuple[str, str], ...],
) -> _PassableDesignArtifacts:
    input_digest = _reviewed_artifact_digest(
        root,
        artifacts.input_path,
        proof,
        expected_artifact_digests,
    )
    report_content, report_digest = _reviewed_artifact(
        root,
        artifacts.report_json_path,
        proof,
        expected_artifact_digests,
    )
    close_content = read_stable_bytes(root, artifacts.close_path)
    close_digest = f"sha256:{hashlib.sha256(close_content).hexdigest()}"
    if close_digest != proof.close_artifact_digest:
        raise ValueError("design close artifact diverged from Stage Close receipt")
    return _PassableDesignArtifacts(
        input_digest=input_digest,
        report_digest=report_digest,
        close_digest=close_digest,
        report=DesignContractReport.model_validate_json(report_content),
        close=DesignContractClose.model_validate_json(close_content),
    )


def _require_passable_design_artifacts(
    root: Path,
    contract_input: DesignContractInput,
    artifacts: DesignContractArtifacts,
    trusted: _PassableDesignArtifacts,
) -> None:
    report = trusted.report
    close = trusted.close
    if (
        report.loop_id != contract_input.loop_id
        or report.work_item_id != contract_input.work_item_id
        or report.status != LoopStatus.PASSED
        or report.blocker_count != 0
        or close.loop_id != contract_input.loop_id
        or close.blocker_count != 0
        or close.report_path != _relative(root, artifacts.report_json_path)
    ):
        raise ValueError("design close reviewed artifacts are not passable")


def _relative(root: Path, path: Path) -> str:
    return normalize_repo_path(path.relative_to(root).as_posix())


def _digest(root: Path, path: Path) -> str:
    content = read_stable_bytes(root, path)
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _reviewed_artifact_digest(
    root: Path,
    path: Path,
    proof: _DesignCloseProof,
    expected_artifact_digests: tuple[tuple[str, str], ...],
) -> str:
    _content, digest = _reviewed_artifact(
        root,
        path,
        proof,
        expected_artifact_digests,
    )
    return digest


def _reviewed_artifact(
    root: Path,
    path: Path,
    proof: _DesignCloseProof,
    expected_artifact_digests: tuple[tuple[str, str], ...],
) -> tuple[bytes, str]:
    relative = _relative(root, path)
    captured = dict(expected_artifact_digests).get(relative)
    reviewed = dict(proof.candidate_artifact_digests).get(relative)
    if captured and reviewed and captured != reviewed:
        raise ScopeAuthorityIntegrityError(
            f"design close reviewed candidate changed after preparation: {relative}"
        )
    expected = reviewed or captured
    if not expected:
        raise ScopeAuthorityIntegrityError(
            f"design close reviewed candidate does not bind {relative}"
        )
    content = read_stable_bytes(root, path)
    digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
    if digest != expected:
        raise ScopeAuthorityIntegrityError(
            f"design close reviewed artifact changed: {relative}"
        )
    return content, digest


def _capture_design_close_artifact_digests(
    root: Path,
    artifacts: DesignContractArtifacts,
) -> tuple[tuple[str, str], ...]:
    return (
        (
            _relative(root, artifacts.input_path),
            _digest(root, artifacts.input_path),
        ),
        (
            _relative(root, artifacts.report_json_path),
            _digest(root, artifacts.report_json_path),
        ),
    )


__all__: list[str] = []
