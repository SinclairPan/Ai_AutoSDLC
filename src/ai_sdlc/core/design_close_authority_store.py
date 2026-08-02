"""把已关闭 Design Contract 锚定到 Stage Close 可信证明。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import ConfigDict, model_validator

from ai_sdlc.core.design_close_artifact_verification import (
    _PassableDesignArtifacts,
    _read_passable_design_artifacts,
    _relative,
    _require_passable_design_artifacts,
)
from ai_sdlc.core.design_close_enforce_authority import (
    _DesignCloseProof,
    _ExpectedEnforceProof,
    _trusted_enforce_proof,
)
from ai_sdlc.core.design_close_shadow_authority import (
    _trusted_anchored_shadow_proof,
    _trusted_shadow_close_proof,
)
from ai_sdlc.core.design_contract_models import DesignContractInput
from ai_sdlc.core.design_contract_store import DesignContractArtifacts
from ai_sdlc.core.scope_authority_store import (
    ScopeAuthorityIntegrityError,
    _anchor_path,
    _read_anchor,
    _write_once,
)
from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.close_gate_models import (
    PreparedStageClose,
)
from ai_sdlc.core.stage_review.close_gate_observation import (
    stage_close_operation_id,
)
from ai_sdlc.core.stage_review.close_gate_store import _read_gate_operation

_CONFIG = ConfigDict(extra="forbid", frozen=True)


class DesignCloseAuthorityAnchor(ArtifactCompatibility):
    model_config = _CONFIG

    schema_version: Literal["design-close-authority-anchor.v1"] = (
        "design-close-authority-anchor.v1"
    )
    artifact_kind: Literal["design-close-authority-anchor"] = (
        "design-close-authority-anchor"
    )
    loop_id: str
    work_item_id: str
    input_path: str
    input_digest: str
    report_path: str
    report_digest: str
    close_path: str
    close_digest: str
    spec_digest: str
    plan_digest: str
    tasks_digest: str
    stage_key: str
    close_kind: str
    target_status: str
    stage_input_digest: str
    stage_close_operation_id: str
    stage_close_proof_kind: Literal["shadow-attestation", "enforce-certificate"]
    stage_close_proof_id: str
    stage_close_proof_digest: str
    candidate_manifest_digest: str = ""
    stage_close_marker_digest: str = ""
    anchor_digest: str = ""

    @model_validator(mode="after")
    def _validate_anchor(self) -> Self:
        required = (
            self.loop_id,
            self.work_item_id,
            self.input_path,
            self.input_digest,
            self.report_path,
            self.report_digest,
            self.close_path,
            self.close_digest,
            self.spec_digest,
            self.plan_digest,
            self.tasks_digest,
            self.stage_key,
            self.close_kind,
            self.target_status,
            self.stage_input_digest,
            self.stage_close_operation_id,
            self.stage_close_proof_id,
            self.stage_close_proof_digest,
        )
        if any(not value.strip() or value != value.strip() for value in required):
            raise ValueError("design close authority anchor is incomplete")
        enforce_bindings = (
            self.candidate_manifest_digest,
            self.stage_close_marker_digest,
        )
        if (self.stage_close_proof_kind == "enforce-certificate") != all(
            value.strip() for value in enforce_bindings
        ):
            raise ValueError("design close authority proof bindings are inconsistent")
        return fill_artifact_digest(self, "anchor_digest")


def _record_design_close_authority(
    root: Path,
    contract_input: DesignContractInput,
    artifacts: DesignContractArtifacts,
    prepared: PreparedStageClose,
    expected_artifact_digests: tuple[tuple[str, str], ...],
) -> DesignCloseAuthorityAnchor:
    authority_path = _anchor_path(root, "design-close", contract_input.loop_id)
    if authority_path.exists() or authority_path.is_symlink():
        return _verify_design_close_authority(root, contract_input, artifacts)
    proof = _trusted_close_proof(
        root,
        prepared,
        artifacts.close_path,
    )
    anchor = _build_anchor(
        root,
        contract_input,
        artifacts,
        proof,
        expected_artifact_digests=expected_artifact_digests,
    )
    _write_once(authority_path, anchor)
    return _verify_design_close_authority(root, contract_input, artifacts)


def _verify_design_close_authority(
    root: Path,
    contract_input: DesignContractInput,
    artifacts: DesignContractArtifacts,
) -> DesignCloseAuthorityAnchor:
    anchor = _read_anchor(
        _anchor_path(root, "design-close", contract_input.loop_id),
        DesignCloseAuthorityAnchor,
    )
    proof = _trusted_anchored_close_proof(
        root,
        anchor,
        artifacts.close_path,
    )
    current = _build_anchor(
        root,
        contract_input,
        artifacts,
        proof,
        expected_artifact_digests=(
            (anchor.input_path, anchor.input_digest),
            (anchor.report_path, anchor.report_digest),
        ),
    )
    if current != anchor:
        raise ScopeAuthorityIntegrityError("design close authority changed")
    return anchor


def _trusted_close_proof(
    root: Path,
    prepared: PreparedStageClose,
    close_path: Path,
) -> _DesignCloseProof:
    operation_id = stage_close_operation_id(prepared)
    try:
        operation = _read_gate_operation(root, operation_id)
    except (OSError, ValueError) as exc:
        raise ScopeAuthorityIntegrityError(
            "design close Stage Close operation is unavailable"
        ) from exc
    shadow_proof = _trusted_shadow_close_proof(
        root,
        prepared,
        close_path,
        operation,
    )
    if shadow_proof is not None:
        return shadow_proof
    return _trusted_enforce_proof(
        root,
        operation_id=operation_id,
        stage_key=prepared.stage_key,
        work_item_id=prepared.work_item_id,
        stage_instance_id=prepared.stage_instance_id,
        loop_id=prepared.loop_id,
        close_kind=prepared.close_kind,
        target_status=prepared.target_status,
        stage_input_digest=prepared.stage_input_digest,
        close_artifact_path=prepared.close_artifact_path,
    )


def _trusted_anchored_close_proof(
    root: Path,
    anchor: DesignCloseAuthorityAnchor,
    close_path: Path,
) -> _DesignCloseProof:
    if anchor.stage_close_proof_kind == "enforce-certificate":
        return _trusted_enforce_proof(
            root,
            operation_id=anchor.stage_close_operation_id,
            stage_key=anchor.stage_key,
            work_item_id=anchor.work_item_id,
            stage_instance_id=anchor.loop_id,
            loop_id=anchor.loop_id,
            close_kind=anchor.close_kind,
            target_status=anchor.target_status,
            stage_input_digest=anchor.stage_input_digest,
            close_artifact_path=_relative(root, close_path),
            expected=_ExpectedEnforceProof(
                proof_id=anchor.stage_close_proof_id,
                proof_digest=anchor.stage_close_proof_digest,
                candidate_manifest_digest=anchor.candidate_manifest_digest,
                marker_digest=anchor.stage_close_marker_digest,
            ),
        )
    return _trusted_anchored_shadow_proof(root, anchor, close_path)


def _build_anchor(
    root: Path,
    contract_input: DesignContractInput,
    artifacts: DesignContractArtifacts,
    proof: _DesignCloseProof,
    *,
    expected_artifact_digests: tuple[tuple[str, str], ...],
) -> DesignCloseAuthorityAnchor:
    try:
        trusted = _read_passable_design_artifacts(
            root,
            artifacts,
            proof,
            expected_artifact_digests,
        )
        _require_passable_design_artifacts(
            root,
            contract_input,
            artifacts,
            trusted,
        )
        return _design_close_anchor(contract_input, artifacts, proof, trusted, root)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ScopeAuthorityIntegrityError(
            "design close authority artifacts are unavailable"
        ) from exc


def _design_close_anchor(
    contract_input: DesignContractInput,
    artifacts: DesignContractArtifacts,
    proof: _DesignCloseProof,
    trusted: _PassableDesignArtifacts,
    root: Path,
) -> DesignCloseAuthorityAnchor:
    return DesignCloseAuthorityAnchor(
        loop_id=contract_input.loop_id,
        work_item_id=contract_input.work_item_id,
        input_path=_relative(root, artifacts.input_path),
        input_digest=trusted.input_digest,
        report_path=_relative(root, artifacts.report_json_path),
        report_digest=trusted.report_digest,
        close_path=_relative(root, artifacts.close_path),
        close_digest=trusted.close_digest,
        spec_digest=contract_input.spec_digest,
        plan_digest=contract_input.plan_digest,
        tasks_digest=contract_input.tasks_digest,
        stage_key=proof.stage_key,
        close_kind=proof.close_kind,
        target_status=proof.target_status,
        stage_input_digest=proof.stage_input_digest,
        stage_close_operation_id=proof.operation_id,
        stage_close_proof_kind=proof.kind,
        stage_close_proof_id=proof.proof_id,
        stage_close_proof_digest=proof.proof_digest,
        candidate_manifest_digest=proof.candidate_manifest_digest,
        stage_close_marker_digest=proof.marker_digest,
    )


__all__ = [
    "DesignCloseAuthorityAnchor",
]
