"""Provider 执行适配器注册、冻结与派发前完整绑定校验。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ai_sdlc.core.stage_review.binding_models import (
    ProviderBindingDescriptor,
    ReviewerRuntimeAllocation,
)
from ai_sdlc.core.stage_review.binding_result_models import (
    ReviewerDispatchAssignment,
)
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.provider_journal_models import ProviderInvocationRequest
from ai_sdlc.core.stage_review.provider_route_models import ProviderExecutionRoute
from ai_sdlc.core.stage_review.provider_transport import TrustedProviderTransport
from ai_sdlc.core.stage_review.provider_transport_models import (
    ProviderExecutionIdentity,
    ProviderExecutionScope,
)
from ai_sdlc.core.stage_review.provider_transport_trust import (
    _reviewer_transport_contract,
)

ProviderTransportFactory = Callable[
    [ProviderBindingDescriptor], TrustedProviderTransport
]


class ProviderExecutionUnavailableError(RuntimeError):
    """请求没有与已注册 Provider 执行合同形成唯一绑定。"""


@dataclass(frozen=True, slots=True)
class RegisteredProviderExecution:
    identity: ProviderExecutionIdentity
    transport: TrustedProviderTransport
    descriptor: ProviderBindingDescriptor | None = None

    @property
    def transport_contract_digest(self) -> str:
        return self.transport.contract.contract_digest


@dataclass(frozen=True, slots=True)
class RegisteredProviderAdapterFactory:
    route: ProviderExecutionRoute
    provider_id_prefixes: tuple[str, ...]
    model_family_prefixes: tuple[str, ...]
    factory: ProviderTransportFactory


class ProviderAdapterFactoryRegistry:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, ...], RegisteredProviderAdapterFactory] = {}
        self._frozen = False

    def register_reviewer(
        self,
        route: ProviderExecutionRoute,
        *,
        provider_id_prefixes: tuple[str, ...],
        model_family_prefixes: tuple[str, ...],
        factory: ProviderTransportFactory,
    ) -> None:
        if self._frozen:
            raise ProviderExecutionUnavailableError(
                "provider factory registry is frozen"
            )
        prefixes = (
            _canonical_prefixes(provider_id_prefixes),
            _canonical_prefixes(model_family_prefixes),
        )
        key = _route_key(route)
        if key in self._entries:
            raise ProviderExecutionUnavailableError(
                "provider adapter factory is ambiguous"
            )
        self._entries[key] = RegisteredProviderAdapterFactory(route, *prefixes, factory)

    def freeze(self) -> FrozenProviderAdapterFactoryRegistry:
        if self._frozen or not self._entries:
            raise ProviderExecutionUnavailableError(
                "provider factory registry cannot freeze"
            )
        self._frozen = True
        return FrozenProviderAdapterFactoryRegistry(tuple(self._entries.values()))


class FrozenProviderAdapterFactoryRegistry:
    def __init__(self, entries: tuple[RegisteredProviderAdapterFactory, ...]) -> None:
        self._entries = {_route_key(item.route): item for item in entries}

    def build_reviewer(
        self,
        descriptor: ProviderBindingDescriptor,
    ) -> TrustedProviderTransport:
        registration = self._entries.get(_route_key(descriptor.execution_route))
        if registration is None or registration.route != descriptor.execution_route:
            raise ProviderExecutionUnavailableError(
                "provider adapter factory is not registered"
            )
        compatible = (
            _matches_prefix(descriptor.provider_id, registration.provider_id_prefixes),
            _matches_prefix(
                descriptor.model_family, registration.model_family_prefixes
            ),
            descriptor.equivalence_class_id
            == registration.route.physical_equivalence_class_id,
        )
        if not all(compatible):
            raise ProviderExecutionUnavailableError(
                "provider descriptor is incompatible with adapter factory"
            )
        return registration.factory(descriptor)


class ProviderExecutionAdapterRegistry:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], RegisteredProviderExecution] = {}
        self._transport_owners: dict[int, tuple[str, str]] = {}
        self._frozen = False

    def register_reviewer(
        self,
        descriptor: ProviderBindingDescriptor,
        transport: TrustedProviderTransport,
    ) -> None:
        identity = transport.contract.execution_identity
        route = descriptor.execution_route
        expected_contract = _reviewer_transport_contract(descriptor)
        expected = (
            identity.execution_scope == "reviewer_binding",
            identity.provider_id == descriptor.provider_id,
            identity.provider_descriptor_digest == descriptor.descriptor_digest,
            identity.equivalence_class_id == descriptor.equivalence_class_id,
            identity.model_family == descriptor.model_family,
            identity.capability_ids == descriptor.capability_ids,
            identity.recovery_capabilities == descriptor.recovery_capabilities,
            identity.provider_adapter_id == route.provider_adapter_id,
            identity.provider_adapter_version == route.provider_adapter_version,
            identity.driver_factory_id == route.driver_factory_id,
            identity.driver_factory_version == route.driver_factory_version,
            identity.broker_id == route.broker_id,
            identity.physical_provider_id == route.physical_provider_id,
            identity.physical_equivalence_class_id
            == route.physical_equivalence_class_id,
            transport.contract.contract_id == route.transport_contract_id,
            transport.contract.contract_version == route.transport_contract_version,
            transport.contract.contract_digest == expected_contract.contract_digest,
            transport.contract.authority_artifact_digest
            == expected_contract.authority_artifact_digest,
        )
        if not all(expected):
            raise ProviderExecutionUnavailableError(
                "reviewer provider execution registration diverged"
            )
        self._register(RegisteredProviderExecution(identity, transport, descriptor))

    def register_shadow(self, transport: TrustedProviderTransport) -> None:
        identity = transport.contract.execution_identity
        if identity.execution_scope != "optimization_shadow":
            raise ProviderExecutionUnavailableError(
                "shadow provider execution registration diverged"
            )
        self._register(RegisteredProviderExecution(identity, transport))

    def freeze(self) -> FrozenProviderExecutionRegistry:
        if self._frozen:
            raise ProviderExecutionUnavailableError("provider registry already frozen")
        if not self._entries:
            raise ProviderExecutionUnavailableError("provider registry is empty")
        self._frozen = True
        return FrozenProviderExecutionRegistry(tuple(self._entries.values()))

    def _register(self, entry: RegisteredProviderExecution) -> None:
        if self._frozen:
            raise ProviderExecutionUnavailableError("provider registry is frozen")
        key = (entry.identity.execution_scope, entry.identity.provider_id)
        owner = self._transport_owners.get(id(entry.transport))
        if key in self._entries or (owner is not None and owner != key):
            raise ProviderExecutionUnavailableError(
                "provider transport has multiple logical identities"
            )
        self._entries[key] = entry
        self._transport_owners[id(entry.transport)] = key


class FrozenProviderExecutionRegistry:
    def __init__(self, entries: tuple[RegisteredProviderExecution, ...]) -> None:
        self._entries = {
            (item.identity.execution_scope, item.identity.provider_id): item
            for item in entries
        }
        if len(self._entries) != len(entries):
            raise ProviderExecutionUnavailableError(
                "provider registry snapshot is ambiguous"
            )
        self.registry_digest = canonical_digest(
            {
                "entries": sorted(
                    (
                        item.identity.identity_digest,
                        item.transport_contract_digest,
                    )
                    for item in entries
                )
            },
            CanonicalizationPolicy(),
        )

    def resolve_reviewer(
        self,
        request: ProviderInvocationRequest,
        assignment: ReviewerDispatchAssignment,
        allocation: ReviewerRuntimeAllocation,
    ) -> RegisteredProviderExecution:
        entry = self._resolve("reviewer_binding", request.provider_id)
        descriptor = entry.descriptor
        identity = entry.identity
        if descriptor is None or not all(
            (
                request.authorization_scope == "reviewer_binding",
                request.assignment_digest == assignment.assignment_digest,
                request.provider_id == assignment.provider_id,
                request.capabilities == assignment.recovery_capabilities,
                assignment.provider_descriptor_digest
                == identity.provider_descriptor_digest,
                assignment.provider_execution_identity_digest
                == identity.identity_digest,
                assignment.physical_provider_id == identity.physical_provider_id,
                assignment.physical_equivalence_class_id
                == identity.physical_equivalence_class_id,
                assignment.transport_profile_digest
                == descriptor.execution_route.transport_profile_digest,
                assignment.transport_contract_digest
                == entry.transport.contract.contract_digest,
                assignment.transport_authority_digest
                == entry.transport.contract.authority_artifact_digest,
                assignment.model_family == identity.model_family,
                assignment.recovery_capabilities == identity.recovery_capabilities,
                allocation.provider_id == identity.provider_id,
                allocation.provider_descriptor_digest
                == identity.provider_descriptor_digest,
                allocation.equivalence_class_id == identity.equivalence_class_id,
                allocation.physical_provider_id == identity.physical_provider_id,
                allocation.physical_equivalence_class_id
                == identity.physical_equivalence_class_id,
                allocation.model_family == identity.model_family,
                descriptor.descriptor_digest == identity.provider_descriptor_digest,
            )
        ):
            raise ProviderExecutionUnavailableError(
                "reviewer provider execution binding diverged"
            )
        return entry

    def resolve_shadow(
        self,
        request: ProviderInvocationRequest,
    ) -> RegisteredProviderExecution:
        entry = self._resolve("optimization_shadow", request.provider_id)
        if not all(
            (
                request.authorization_scope == "optimization_shadow",
                request.provider_id == entry.identity.provider_id,
                request.capabilities == entry.identity.recovery_capabilities,
            )
        ):
            raise ProviderExecutionUnavailableError(
                "shadow provider execution binding diverged"
            )
        return entry

    def _resolve(
        self,
        scope: ProviderExecutionScope,
        provider_id: str,
    ) -> RegisteredProviderExecution:
        entry = self._entries.get((scope, provider_id))
        if entry is None:
            raise ProviderExecutionUnavailableError(
                "provider execution adapter is not registered"
            )
        contract = entry.transport.contract
        if contract.execution_identity != entry.identity:
            raise ProviderExecutionUnavailableError(
                "provider transport execution identity diverged"
            )
        return entry


def _build_reviewer_execution_registry(
    descriptors: tuple[ProviderBindingDescriptor, ...],
    factories: FrozenProviderAdapterFactoryRegistry,
) -> FrozenProviderExecutionRegistry:
    registry = ProviderExecutionAdapterRegistry()
    for descriptor in descriptors:
        registry.register_reviewer(descriptor, factories.build_reviewer(descriptor))
    return registry.freeze()


def _route_key(route: ProviderExecutionRoute) -> tuple[str, ...]:
    return (
        route.provider_adapter_id,
        route.provider_adapter_version,
        route.driver_factory_id,
        route.driver_factory_version,
        route.broker_id,
    )


def _canonical_prefixes(values: tuple[str, ...]) -> tuple[str, ...]:
    result = tuple(sorted(set(values)))
    if not result or any(not value.strip() for value in result):
        raise ProviderExecutionUnavailableError("provider factory support is empty")
    return result


def _matches_prefix(value: str, prefixes: tuple[str, ...]) -> bool:
    return any(value.startswith(prefix) for prefix in prefixes)


__all__ = [
    "FrozenProviderAdapterFactoryRegistry",
    "FrozenProviderExecutionRegistry",
    "ProviderAdapterFactoryRegistry",
    "ProviderExecutionAdapterRegistry",
    "ProviderExecutionUnavailableError",
    "RegisteredProviderExecution",
]
