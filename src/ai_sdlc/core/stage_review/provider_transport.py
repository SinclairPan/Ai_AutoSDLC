"""只允许受控 IPC endpoint 的 T601 Provider 传输执行器。"""

from __future__ import annotations

import json
import secrets
import socket
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from ai_sdlc.core.stage_review.artifacts import (
    create_json_exclusive,
    read_json_object,
    resolve_canonical_shared_state,
)
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.certificate_receipt_store import (
    FilesystemReviewReceiptArtifactStore,
)
from ai_sdlc.core.stage_review.provider_transport_models import (
    ProviderEgressPermit,
    ProviderEgressReceipt,
    ProviderTransportEnvelope,
    ProviderTransportExchangeResult,
    TrustedProviderTransportAuthority,
    TrustedProviderTransportContract,
)
from ai_sdlc.core.stage_review.provider_transport_models import (
    _transport_artifact_digest as transport_artifact_digest,
)
from ai_sdlc.core.stage_review.provider_transport_models import (
    _transport_artifact_id as transport_artifact_id,
)
from ai_sdlc.core.stage_review.resource_builders import parse_utc, utc_iso

_TransportModelT = TypeVar("_TransportModelT", bound=BaseModel)


class TrustedEgressUnavailable(RuntimeError):  # noqa: N818
    pass


class TrustedProviderBroker(Protocol):
    @property
    def remote_provider_exercised(self) -> bool: ...

    def exchange(
        self,
        permit: ProviderEgressPermit,
        envelope: ProviderTransportEnvelope,
    ) -> dict[str, object]: ...


class ControlledEndpointBroker:
    def __init__(
        self,
        contract: TrustedProviderTransportContract,
        endpoints: Mapping[
            str,
            Callable[[ProviderTransportEnvelope], dict[str, object]],
        ],
        *,
        authority: TrustedProviderTransportAuthority,
    ) -> None:
        self._contract = _trusted_contract(contract, authority)
        self._endpoints = dict(endpoints)
        self._consumed: set[str] = set()

    @property
    def remote_provider_exercised(self) -> bool:
        return False

    def exchange(
        self,
        permit: ProviderEgressPermit,
        envelope: ProviderTransportEnvelope,
    ) -> dict[str, object]:
        _verify_broker_request(self._contract, permit, envelope)
        if permit.permit_digest in self._consumed:
            raise TrustedEgressUnavailable(
                "provider egress permit was already consumed"
            )
        handler = self._endpoints.get(permit.endpoint_id)
        if handler is None:
            raise TrustedEgressUnavailable("controlled endpoint is unavailable")
        self._consumed.add(permit.permit_digest)
        return _socketpair_exchange(handler, envelope)


class TrustedProviderTransport:
    def __init__(
        self,
        root: Path,
        contract: TrustedProviderTransportContract,
        *,
        project_id: str,
        broker: TrustedProviderBroker | None,
        authority: TrustedProviderTransportAuthority,
    ) -> None:
        self._root = (
            resolve_canonical_shared_state(root, project_id) / "provider-transport"
        )
        self._contract = _trusted_contract(contract, authority)
        self._broker = broker
        self._receipt_artifacts = FilesystemReviewReceiptArtifactStore(
            root,
            project_id=project_id,
        )

    @property
    def remote_provider_available(self) -> bool:
        return bool(self._broker is not None and self._broker.remote_provider_exercised)

    @property
    def contract(self) -> TrustedProviderTransportContract:
        return self._contract

    def exchange(
        self,
        envelope: ProviderTransportEnvelope,
    ) -> ProviderTransportExchangeResult:
        if self._broker is None:
            raise TrustedEgressUnavailable("trusted provider broker is unavailable")
        trusted = ProviderTransportEnvelope.model_validate(
            envelope.model_dump(mode="json")
        )
        identity = self._contract.execution_identity
        if (
            trusted.provider_id != identity.provider_id
            or trusted.execution_identity_digest != identity.identity_digest
        ):
            raise TrustedEgressUnavailable("provider execution scope is invalid")
        now = datetime.now(UTC)
        self._ensure_no_incomplete(trusted.invocation_id)
        permit = _permit(self._contract, trusted, now)
        self._persist("permits", permit.permit_id, permit)
        self._receipt_artifacts.persist_egress_permit(permit)
        self._consume_permit(permit)
        response = self._broker.exchange(permit, trusted)
        response_digest = self._receipt_artifacts.persist_response(response)
        receipt = _receipt(
            self._contract,
            permit,
            response,
            now,
            remote_provider_exercised=self._broker.remote_provider_exercised,
        )
        if response_digest != receipt.response_digest:
            raise TrustedEgressUnavailable("provider response digest diverged")
        self._persist("receipts", receipt.receipt_id, receipt)
        self._receipt_artifacts.persist_egress_receipt(receipt)
        return ProviderTransportExchangeResult(response=response, receipt=receipt)

    def permits(self) -> tuple[ProviderEgressPermit, ...]:
        return self._load("permits", ProviderEgressPermit)

    def receipts(self) -> tuple[ProviderEgressReceipt, ...]:
        return self._load("receipts", ProviderEgressReceipt)

    def _persist(self, kind: str, identity: str, value: BaseModel) -> None:
        path = self._root / kind / f"{identity}.json"
        payload = value.model_dump(mode="json")
        if not create_json_exclusive(path, payload):
            raise TrustedEgressUnavailable("provider transport artifact already exists")

    def _consume_permit(self, permit: ProviderEgressPermit) -> None:
        path = self._root / "consumed" / f"{permit.permit_id}.json"
        if not create_json_exclusive(path, permit.model_dump(mode="json")):
            raise TrustedEgressUnavailable(
                "provider egress permit was already consumed"
            )

    def _ensure_no_incomplete(self, invocation_id: str) -> None:
        permits = {
            item.permit_digest
            for item in self.permits()
            if item.invocation_id == invocation_id
        }
        receipts = {
            item.permit_digest
            for item in self.receipts()
            if item.invocation_id == invocation_id
        }
        if permits - receipts:
            raise TrustedEgressUnavailable("provider egress recovery is required")

    def _load(
        self,
        kind: str,
        model: type[_TransportModelT],
    ) -> tuple[_TransportModelT, ...]:
        directory = self._root / kind
        if not directory.exists():
            return ()
        try:
            return tuple(
                model.model_validate(read_json_object(path))
                for path in sorted(directory.glob("*.json"))
            )
        except (ValidationError, ValueError) as exc:
            raise TrustedEgressUnavailable(
                "provider transport evidence is invalid"
            ) from exc


def _permit(
    contract: TrustedProviderTransportContract,
    envelope: ProviderTransportEnvelope,
    now: datetime,
) -> ProviderEgressPermit:
    nonce = secrets.token_hex(16)
    values: dict[str, Any] = {
        **envelope.model_dump(exclude={"payload", "execution_identity_digest"}),
        "execution_identity": contract.execution_identity,
        "permit_id": transport_artifact_id(
            "provider-egress-permit",
            envelope.invocation_id,
            nonce,
        ),
        "endpoint_id": contract.endpoint_id,
        "transport_contract_digest": contract.contract_digest,
        "transport_authority_digest": contract.authority_artifact_digest,
        "issued_at": utc_iso(now),
        "expires_at": utc_iso(
            now + timedelta(seconds=envelope.active_wall_clock_limit + 1)
        ),
        "nonce": nonce,
    }
    draft = ProviderEgressPermit.model_construct(**values, permit_digest="")
    return ProviderEgressPermit.model_validate(
        {**values, "permit_digest": transport_artifact_digest(draft, "permit_digest")}
    )


def _receipt(
    contract: TrustedProviderTransportContract,
    permit: ProviderEgressPermit,
    response: dict[str, object],
    now: datetime,
    *,
    remote_provider_exercised: bool,
) -> ProviderEgressReceipt:
    response_digest = canonical_digest(response, CanonicalizationPolicy())
    values: dict[str, Any] = {
        "receipt_id": transport_artifact_id(
            "provider-egress-receipt",
            permit.permit_digest,
        ),
        "permit_digest": permit.permit_digest,
        "invocation_id": permit.invocation_id,
        "assignment_digest": permit.assignment_digest,
        "provider_id": permit.provider_id,
        "execution_identity": contract.execution_identity,
        "request_digest": permit.request_digest,
        "turn_index": permit.turn_index,
        "idempotency_key": permit.idempotency_key,
        "credential_view_digest": permit.credential_view_digest,
        "backend_epoch": permit.backend_epoch,
        "endpoint_id": permit.endpoint_id,
        "transport_contract_digest": contract.contract_digest,
        "transport_authority_digest": contract.authority_artifact_digest,
        "response_digest": response_digest,
        "transport_contract_attested": True,
        "remote_provider_exercised": remote_provider_exercised,
        "recorded_at": utc_iso(now),
    }
    draft = ProviderEgressReceipt.model_construct(**values, receipt_digest="")
    return ProviderEgressReceipt.model_validate(
        {**values, "receipt_digest": transport_artifact_digest(draft, "receipt_digest")}
    )


def _verify_broker_request(
    contract: TrustedProviderTransportContract,
    permit: ProviderEgressPermit,
    envelope: ProviderTransportEnvelope,
) -> None:
    now = datetime.now(UTC)
    lineage = (
        permit.invocation_id == envelope.invocation_id,
        permit.assignment_digest == envelope.assignment_digest,
        permit.provider_id == envelope.provider_id,
        permit.execution_identity == contract.execution_identity,
        envelope.execution_identity_digest
        == contract.execution_identity.identity_digest,
        permit.request_digest == envelope.request_digest,
        permit.turn_index == envelope.turn_index,
        permit.idempotency_key == envelope.idempotency_key,
        permit.credential_view_digest == envelope.credential_view_digest,
        permit.backend_epoch == envelope.backend_epoch,
        permit.active_wall_clock_limit == envelope.active_wall_clock_limit,
        permit.endpoint_id == contract.endpoint_id,
        permit.transport_contract_digest == contract.contract_digest,
        permit.transport_authority_digest == contract.authority_artifact_digest,
        parse_utc(permit.issued_at) <= now < parse_utc(permit.expires_at),
    )
    if not all(lineage):
        raise TrustedEgressUnavailable("provider egress permit lineage is invalid")


def _trusted_contract(
    contract: TrustedProviderTransportContract,
    authority: TrustedProviderTransportAuthority,
) -> TrustedProviderTransportContract:
    trusted = TrustedProviderTransportContract.model_validate(
        contract.model_dump(mode="json")
    )
    evidence = TrustedProviderTransportAuthority.model_validate(
        authority.model_dump(mode="json")
    )
    lineage = (
        trusted.contract_id == evidence.contract_id,
        trusted.contract_version == evidence.contract_version,
        trusted.endpoint_id == evidence.endpoint_id,
        trusted.authority_artifact_digest == evidence.authority_digest,
    )
    if not all(lineage):
        raise TrustedEgressUnavailable("provider transport authority is invalid")
    return trusted


def _socketpair_exchange(
    handler: Callable[[ProviderTransportEnvelope], dict[str, object]],
    envelope: ProviderTransportEnvelope,
) -> dict[str, object]:
    client, server = socket.socketpair()
    client.settimeout(5)
    server.settimeout(5)
    errors: list[BaseException] = []

    def serve() -> None:
        try:
            request = ProviderTransportEnvelope.model_validate_json(
                _receive_frame(server)
            )
            response = handler(request)
            _send_frame(server, json.dumps(response, sort_keys=True).encode())
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            server.close()

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    try:
        _send_frame(client, envelope.model_dump_json().encode())
        try:
            payload = _receive_frame(client)
        except TimeoutError as exc:
            raise TrustedEgressUnavailable("controlled endpoint timed out") from exc
    finally:
        client.close()
        worker.join(timeout=5)
    if errors:
        raise TrustedEgressUnavailable("controlled endpoint failed") from errors[0]
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise TrustedEgressUnavailable("controlled endpoint response is invalid")
    return value


def _send_frame(connection: socket.socket, payload: bytes) -> None:
    connection.sendall(len(payload).to_bytes(8, "big") + payload)


def _receive_frame(connection: socket.socket) -> bytes:
    size = int.from_bytes(_receive_exact(connection, 8), "big")
    if size <= 0 or size > 16 * 1024 * 1024:
        raise TrustedEgressUnavailable("controlled endpoint frame is invalid")
    return _receive_exact(connection, size)


def _receive_exact(connection: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = connection.recv(size - len(chunks))
        if not chunk:
            raise TrustedEgressUnavailable("controlled endpoint closed early")
        chunks.extend(chunk)
    return bytes(chunks)
