"""Certificate 使用的只读 Receipt authority 与 JSON Artifact Store。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ValidationError

from ai_sdlc.core.stage_review.artifacts import (
    SharedStateIntegrityError,
    bind_repository_project,
    create_json_exclusive,
    read_json_object,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.isolation_models import (
    IsolationExecutionPermit,
    IsolationExecutionReceipt,
)
from ai_sdlc.core.stage_review.provider_journal_models import ProviderInvocation
from ai_sdlc.core.stage_review.provider_transport_models import (
    ProviderEgressPermit,
    ProviderEgressReceipt,
)

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class ReceiptArtifactError(SharedStateIntegrityError):
    pass


@runtime_checkable
class ReviewReceiptArtifactResolver(Protocol):
    def resolve_invocation(self, invocation_id: str) -> ProviderInvocation: ...

    def resolve_isolation_permit(
        self, permit_digest: str
    ) -> IsolationExecutionPermit: ...

    def resolve_isolation_receipt(
        self, receipt_digest: str
    ) -> IsolationExecutionReceipt: ...

    def resolve_egress_permit(self, permit_digest: str) -> ProviderEgressPermit: ...

    def resolve_egress_receipt(
        self, receipt_digest: str
    ) -> ProviderEgressReceipt: ...

    def resolve_response(self, response_digest: str) -> dict[str, object]: ...


class FilesystemReviewReceiptArtifactStore:
    def __init__(self, root: Path, *, project_id: str) -> None:
        self._bind(resolve_canonical_shared_state(root, project_id), project_id)

    @classmethod
    def from_shared_root(
        cls,
        shared_root: Path,
        *,
        project_id: str,
    ) -> FilesystemReviewReceiptArtifactStore:
        value = cls.__new__(cls)
        value._bind(shared_root, project_id)
        return value

    def resolve_invocation(self, invocation_id: str) -> ProviderInvocation:
        return self._read_model("invocations", invocation_id, ProviderInvocation)

    def resolve_isolation_permit(
        self,
        permit_digest: str,
    ) -> IsolationExecutionPermit:
        return self._read_model(
            "isolation-permits", permit_digest, IsolationExecutionPermit
        )

    def resolve_isolation_receipt(
        self,
        receipt_digest: str,
    ) -> IsolationExecutionReceipt:
        return self._read_model(
            "isolation-receipts", receipt_digest, IsolationExecutionReceipt
        )

    def resolve_egress_permit(self, permit_digest: str) -> ProviderEgressPermit:
        return self._read_model("egress-permits", permit_digest, ProviderEgressPermit)

    def resolve_egress_receipt(
        self,
        receipt_digest: str,
    ) -> ProviderEgressReceipt:
        return self._read_model(
            "egress-receipts", receipt_digest, ProviderEgressReceipt
        )

    def resolve_response(self, response_digest: str) -> dict[str, object]:
        payload = self._read_payload("responses", response_digest)
        if canonical_digest(payload, CanonicalizationPolicy()) != response_digest:
            raise ReceiptArtifactError("provider response digest is invalid")
        return payload

    def persist_invocation(self, value: ProviderInvocation) -> None:
        trusted = ProviderInvocation.model_validate(value.model_dump(mode="json"))
        self._persist("invocations", trusted.invocation_id, trusted)

    def persist_isolation_permit(self, value: IsolationExecutionPermit) -> None:
        trusted = IsolationExecutionPermit.model_validate(value.model_dump(mode="json"))
        self._persist("isolation-permits", trusted.permit_digest, trusted)

    def persist_isolation_receipt(self, value: IsolationExecutionReceipt) -> None:
        trusted = IsolationExecutionReceipt.model_validate(value.model_dump(mode="json"))
        self._persist("isolation-receipts", trusted.receipt_digest, trusted)

    def persist_egress_permit(self, value: ProviderEgressPermit) -> None:
        trusted = ProviderEgressPermit.model_validate(value.model_dump(mode="json"))
        self._persist("egress-permits", trusted.permit_digest, trusted)

    def persist_egress_receipt(self, value: ProviderEgressReceipt) -> None:
        trusted = ProviderEgressReceipt.model_validate(value.model_dump(mode="json"))
        self._persist("egress-receipts", trusted.receipt_digest, trusted)

    def persist_response(self, value: dict[str, object]) -> str:
        digest = canonical_digest(value, CanonicalizationPolicy())
        self._persist("responses", digest, value)
        return digest

    def artifact_path(self, kind: str, identity: str) -> Path:
        key = hashlib.sha256(identity.encode()).hexdigest()
        return self._root / kind / f"{key}.json"

    def _bind(self, shared_root: Path, project_id: str) -> None:
        self.project_id = project_id
        self.shared_root = shared_root
        bind_repository_project(shared_root, project_id)
        self._root = shared_root / "review-execution-authority"

    def _persist(
        self,
        kind: str,
        identity: str,
        value: BaseModel | dict[str, object],
    ) -> None:
        payload: dict[str, Any]
        if isinstance(value, BaseModel):
            payload = value.model_dump(mode="json")
        else:
            payload = dict(value)
        path = self.artifact_path(kind, identity)
        if create_json_exclusive(path, payload):
            return
        if read_json_object(path) != payload:
            raise ReceiptArtifactError("review execution artifact already exists")

    def _read_payload(self, kind: str, identity: str) -> dict[str, object]:
        try:
            return read_json_object(self.artifact_path(kind, identity))
        except (OSError, ValueError, TypeError) as exc:
            raise ReceiptArtifactError("review execution artifact is unavailable") from exc

    def _read_model(
        self,
        kind: str,
        identity: str,
        model: type[_ModelT],
    ) -> _ModelT:
        try:
            value = model.model_validate(self._read_payload(kind, identity))
        except (ValidationError, ValueError, TypeError) as exc:
            raise ReceiptArtifactError("review execution artifact is invalid") from exc
        field = {
            "invocations": "invocation_id",
            "isolation-permits": "permit_digest",
            "isolation-receipts": "receipt_digest",
            "egress-permits": "permit_digest",
            "egress-receipts": "receipt_digest",
        }[kind]
        actual_identity = str(getattr(value, field, ""))
        if actual_identity != identity:
            raise ReceiptArtifactError("review execution artifact identity diverged")
        return value


def _require_canonical_receipt_artifact_store(
    value: ReviewReceiptArtifactResolver | None,
    *,
    project_id: str,
    shared_root: Path,
) -> FilesystemReviewReceiptArtifactStore:
    """只允许与 Session 同项目、同共享状态根的生产 Store。"""

    if value is None:
        value = FilesystemReviewReceiptArtifactStore.from_shared_root(
            shared_root,
            project_id=project_id,
        )
    if (
        type(value) is not FilesystemReviewReceiptArtifactStore
        or value.project_id != project_id
        or value.shared_root != shared_root
    ):
        raise TypeError("trusted receipt artifact resolver is required")
    return value
