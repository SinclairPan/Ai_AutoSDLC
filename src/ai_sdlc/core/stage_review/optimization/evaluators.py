"""版本化 Evaluator Contract/Adapter 注册表与调用前权限校验。"""

from __future__ import annotations

import dis
import hashlib
import importlib
import inspect
import math
import sys
import threading
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from functools import lru_cache, partial
from pathlib import Path
from types import CodeType, ModuleType, SimpleNamespace
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.canonical import CanonicalizationPolicy, canonical_digest
from ai_sdlc.core.stage_review.optimization.models import (
    CandidateDomain,
    CandidatePartition,
    OptimizationCandidate,
    OptimizationEvaluationReport,
    OptimizationStatisticalSample,
)
from ai_sdlc.core.stage_review.registry_versions import (
    require_machine_id,
    require_version,
)

_TRANSIENT_RUNTIME_FIELDS = frozenset(
    {
        "calls",
        "receipts",
        "events",
        "issued_publication_digests",
    }
)
_TRANSITIVE_BEHAVIOR_FIELDS = frozenset(
    {
        "adapter",
        "executor",
        "gate",
        "policy",
        "statistics_authority",
    }
)
_CLASS_CONFIGURATION_IGNORED_FIELDS = frozenset(
    {
        "__abstractmethods__",
        "__annotate_func__",
        "__annotations__",
        "__annotations_cache__",
        "__dict__",
        "__doc__",
        "__firstlineno__",
        "__module__",
        "__parameters__",
        "__protocol_attrs__",
        "__slots__",
        "__static_attributes__",
        "__weakref__",
    }
)
_TRUSTED_STDLIB_RUNTIME_OBJECTS = frozenset(
    {
        "_thread:RLock",
        "_thread:lock",
        "_json:Scanner",
        "datetime:timezone",
        "random:SystemRandom",
    }
)
_STATEFUL_STDLIB_RUNTIME_OBJECTS = frozenset(
    {
        "random:Random",
    }
)
_BOUNDED_DEPENDENCY_MAX_NODES = 2048
_BOUNDED_DEPENDENCY_MAX_DEPTH = 24
_IDENTITY_WRAPPER_MAX_DEPTH = 8
_IDENTITY_MEASUREMENT_KERNEL_CALLABLES = frozenset(
    {
        "_BoundedDependencyState",
        "_FastDependencyState",
        "_OptimizationDependencyScope",
        "_bounded_attribute_dependency",
        "_bounded_builtin_identity",
        "_bounded_callable_identity",
        "_bounded_collection_identity",
        "_bounded_dependency_class_identity",
        "_bounded_dependency_identity",
        "_bounded_partial_identity",
        "_bounded_release_dependency",
        "_bounded_stable_value",
        "_cached_callable_static_snapshot",
        "_cached_class_source",
        "_cached_installed_module_digest",
        "_cached_nested_code_objects",
        "_cached_optimization_dependency_scope",
        "_cached_function_global_names",
        "_cached_referenced_module_attributes",
        "_cached_source_path_is_release_owned",
        "_cached_release_artifact_digest",
        "_callable_capture_identity",
        "_callable_dependency_capture",
        "_captured_object_identity",
        "_captured_object_identity_once",
        "_class_configuration_value",
        "_class_runtime_member_groups",
        "_component_implementation_identity",
        "_compute_live_package_digest",
        "_dataclass_configuration",
        "_function_is_live_module_member",
        "_function_has_release_source",
        "_function_live_dependency_bindings",
        "_function_is_release_covered",
        "_generated_class_member_fast_token",
        "_generated_class_member_identity",
        "_generated_class_runtime_member_groups",
        "_fast_dependency_state",
        "_has_opaque_native_state",
        "_identity_measurement_kernel_binding_token",
        "_identity_measurement_callable_chain",
        "_identity_measurement_kernel_digest",
        "_identity_measurement_policy_digest",
        "_installed_module_provenance",
        "_is_identity_measurement_kernel_callable",
        "_is_runtime_identity_infrastructure_name",
        "_is_stable_configuration_value",
        "_is_stable_configuration_value_inner",
        "_live_callable_binding_identity",
        "_live_callable_cache_token",
        "_live_callable_capture_identity",
        "_live_capture_cache_token",
        "_live_capture_identity",
        "_live_class_configuration_token",
        "_live_package_fast_token",
        "_live_package_generation_token",
        "_module_attribute_identity",
        "_normalize_bounded_stable_value",
        "_normalized_callable_code",
        "_normalized_code_constant",
        "_normalized_code_object",
        "_object_state_items",
        "_optimization_dependency_module_names",
        "_optimization_dependency_scope",
        "_optimization_dependency_source_root",
        "_optimization_dependency_source_signature",
        "_optimization_live_package_digest",
        "_optimization_release_artifact_digest",
        "_optimization_release_module_names",
        "_optimization_seed_binding_token",
        "_referenced_module_attributes",
        "_release_class_runtime_member_groups",
        "_release_dependency_fast_token",
        "_release_node_callable_identity",
        "_release_node_value_identity",
        "_release_runtime_value_fast_token",
        "_resolve_live_package_digest",
        "_resolve_live_package_digest_for_token",
        "_runtime_member_callable",
        "_scope_function_dependencies",
        "_scope_value_targets",
        "_stable_cache_token",
        "_stable_callable_capture",
        "_stable_captured_object_configuration",
        "_stable_class_configuration",
        "_stable_component_configuration",
        "_stable_configuration_value",
        "_stable_configuration_value_inner",
        "_verified_first_party_scope_target",
        "component_implementation_digest",
        "component_implementation_identity",
        "component_module_runtime_identity",
        "component_runtime_digest",
        "component_runtime_identity",
        "has_explicit_runtime_identity",
        "invalidate_optimization_runtime_identity",
        "optimization_runtime_identity_snapshot",
    }
)
_LIVE_PACKAGE_DIGEST_CACHE: dict[object, str] = {}
_LIVE_PACKAGE_DIGEST_CACHE_LOCK = threading.Lock()
_LIVE_PACKAGE_SNAPSHOT_LOCAL = threading.local()
_LIVE_PACKAGE_GENERATION = 0


class EvaluationContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_digest: str
    partition: CandidatePartition
    evaluation_binding_id: str
    evaluation_provider_id: str
    provider_capabilities: tuple[str, ...]
    resource_reservation_digest: str
    hypothesis_family_digest: str = ""
    statistics_policy_digest: str = ""
    statistical_alpha: float = 0

    @field_validator("evaluation_binding_id", "evaluation_provider_id")
    @classmethod
    def _identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "evaluation provider identity")

    @field_validator("provider_capabilities")
    @classmethod
    def _capabilities_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or value != tuple(sorted(set(value))):
            raise ValueError("evaluation provider capabilities must be canonical")
        return value


class EvaluatorContract(ArtifactCompatibility):
    schema_version: Literal["optimization-evaluator-contract.v1"] = (
        "optimization-evaluator-contract.v1"
    )
    artifact_kind: Literal["optimization-evaluator-contract"] = (
        "optimization-evaluator-contract"
    )
    evaluator_kind: str
    evaluator_version: str
    candidate_schema_version: str
    report_schema_version: str
    allowed_partitions: tuple[CandidatePartition, ...]
    compatible_candidate_domains: tuple[CandidateDomain, ...]
    independence_level: Literal["deterministic", "independent_binding"]
    deterministic: bool
    provider_constraints: tuple[str, ...]
    contract_digest: str = ""

    @field_validator("evaluator_kind")
    @classmethod
    def _kind_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "evaluator_kind")

    @field_validator("evaluator_version")
    @classmethod
    def _version_is_stable(cls, value: str) -> str:
        return require_version(value)

    @field_validator(
        "allowed_partitions",
        "compatible_candidate_domains",
        "provider_constraints",
    )
    @classmethod
    def _sets_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or value != tuple(sorted(set(value))):
            raise ValueError("evaluator contract sets must be sorted and unique")
        return value

    @model_validator(mode="after")
    def _verify_contract(self) -> Self:
        if self.deterministic != (self.independence_level == "deterministic"):
            raise ValueError("evaluator deterministic declaration is inconsistent")
        return fill_artifact_digest(self, "contract_digest")


class EvaluatorAdapter(Protocol):
    def evaluate(
        self,
        candidate: OptimizationCandidate,
        context: EvaluationContext,
        contract: EvaluatorContract,
    ) -> OptimizationEvaluationReport: ...


class EvaluationStatisticsAuthority(Protocol):
    def sample(
        self,
        candidate: OptimizationCandidate,
        context: EvaluationContext,
    ) -> OptimizationStatisticalSample: ...


class OptimizationEvaluatorRegistry:
    def __init__(
        self,
        *,
        statistics_authority: EvaluationStatisticsAuthority | None = None,
    ) -> None:
        self._entries: dict[
            str,
            tuple[EvaluatorContract, EvaluatorAdapter | None],
        ] = {}
        self._statistics_authority = statistics_authority

    def register(
        self,
        contract: EvaluatorContract,
        adapter: EvaluatorAdapter,
    ) -> None:
        trusted = EvaluatorContract.model_validate(contract.model_dump(mode="json"))
        if trusted.evaluator_kind in self._entries:
            raise ValueError("evaluator_kind is already registered")
        self._entries[trusted.evaluator_kind] = (trusted, adapter)

    def register_contract(self, contract: EvaluatorContract) -> None:
        trusted = EvaluatorContract.model_validate(contract.model_dump(mode="json"))
        if trusted.evaluator_kind in self._entries:
            raise ValueError("evaluator_kind is already registered")
        self._entries[trusted.evaluator_kind] = (trusted, None)

    @property
    def registry_digest(self) -> str:
        contracts = tuple(
            contract
            for contract, _ in sorted(
                self._entries.values(), key=lambda item: item[0].evaluator_kind
            )
        )
        return canonical_digest(contracts, CanonicalizationPolicy())

    @property
    def implementation_digest(self) -> str:
        entries = tuple(
            {
                "contract_digest": contract.contract_digest,
                "adapter": component_runtime_identity(adapter),
            }
            for contract, adapter in sorted(
                self._entries.values(),
                key=lambda item: item[0].evaluator_kind,
            )
        )
        return canonical_digest(
            {
                "entries": entries,
                "statistics_authority": component_runtime_identity(
                    self._statistics_authority
                ),
            },
            CanonicalizationPolicy(),
        )

    def evaluate(
        self,
        *,
        evaluator_kind: str,
        candidate: OptimizationCandidate,
        context: EvaluationContext,
    ) -> OptimizationEvaluationReport:
        try:
            contract, adapter = self._entries[evaluator_kind]
        except KeyError as exc:
            raise ValueError("evaluator_kind is not registered") from exc
        if adapter is None:
            raise ValueError("evaluator adapter is unavailable")
        trusted = OptimizationCandidate.model_validate(
            candidate.model_dump(mode="json")
        )
        runtime = EvaluationContext.model_validate(context.model_dump(mode="json"))
        _validate_invocation(contract, trusted, runtime)
        runtime = runtime.model_copy(
            update={
                "hypothesis_family_digest": _evaluation_hypothesis_family(
                    contract, trusted, runtime
                )
            }
        )
        report = OptimizationEvaluationReport.model_validate(
            adapter.evaluate(trusted, runtime, contract).model_dump(mode="json")
        )
        _validate_report(contract, trusted, runtime, report)
        self._validate_statistical_sample(trusted, runtime, report)
        return report

    def validate_cached_report(
        self,
        *,
        evaluator_kind: str,
        candidate: OptimizationCandidate,
        context: EvaluationContext,
        report: OptimizationEvaluationReport,
    ) -> None:
        try:
            contract, _ = self._entries[evaluator_kind]
        except KeyError as exc:
            raise ValueError("evaluator_kind is not registered") from exc
        trusted = OptimizationCandidate.model_validate(
            candidate.model_dump(mode="json")
        )
        runtime = EvaluationContext.model_validate(context.model_dump(mode="json"))
        cached = OptimizationEvaluationReport.model_validate(
            report.model_dump(mode="json")
        )
        _validate_invocation(contract, trusted, runtime)
        runtime = runtime.model_copy(
            update={
                "hypothesis_family_digest": _evaluation_hypothesis_family(
                    contract, trusted, runtime
                )
            }
        )
        _validate_report(contract, trusted, runtime, cached)
        self._validate_statistical_sample(trusted, runtime, cached)

    def contract(self, evaluator_kind: str) -> EvaluatorContract:
        try:
            contract, _ = self._entries[evaluator_kind]
        except KeyError as exc:
            raise ValueError("evaluator_kind is not registered") from exc
        return contract

    def require_explicit_runtime_identity(self) -> None:
        components = (
            *(adapter for _, adapter in self._entries.values() if adapter is not None),
            self._statistics_authority,
        )
        if any(not has_explicit_runtime_identity(item) for item in components):
            raise ValueError(
                "optimization evaluator runtime identity is not explicit"
            )

    def _validate_statistical_sample(
        self,
        candidate: OptimizationCandidate,
        context: EvaluationContext,
        report: OptimizationEvaluationReport,
    ) -> None:
        authority = self._statistics_authority
        if authority is None:
            raise ValueError("core evaluation statistics authority is unavailable")
        sample = OptimizationStatisticalSample.model_validate(
            authority.sample(candidate, context).model_dump(mode="json")
        )
        bindings = (
            sample.candidate_digest == candidate.candidate_digest,
            sample.dataset_digest == context.dataset_digest,
            sample.comparison_session_ids == report.comparison_session_ids,
            len(sample.improved_session_ids) == report.improved_count,
            len(sample.comparison_session_ids) == report.sample_count,
            sample.sample_digest == report.statistical_sample_digest,
        )
        if not all(bindings):
            raise ValueError("evaluator statistical sample diverged from core evidence")


def component_implementation_identity(value: object | None) -> dict[str, str]:
    return _component_implementation_identity(value, set())


def _component_implementation_identity(
    value: object | None,
    seen: set[int],
) -> dict[str, str]:
    if value is None:
        return {
            "entrypoint": "contract-only",
            "source_digest": "sha256:contract-only",
        }
    if isinstance(value, partial):
        entrypoint = "functools:partial"
        return {
            "entrypoint": entrypoint,
            "source_digest": canonical_digest(
                {
                    "callable": _component_implementation_identity(
                        value.func, seen
                    ),
                    "args": [
                        _stable_callable_capture(item, seen)
                        for item in value.args
                    ],
                    "keywords": {
                        key: _stable_callable_capture(item, seen)
                        for key, item in sorted(
                            (value.keywords or {}).items()
                        )
                    },
                },
                CanonicalizationPolicy(),
            ),
        }
    if inspect.isfunction(value) or inspect.ismethod(value):
        function = value.__func__ if inspect.ismethod(value) else value
        identity = id(function)
        entrypoint = f"{function.__module__}:{function.__qualname__}"
        if identity in seen:
            return {
                "entrypoint": entrypoint,
                "source_digest": canonical_digest(
                    {"recursive_reference": entrypoint},
                    CanonicalizationPolicy(),
                ),
            }
        defaults = getattr(function, "__defaults__", None) or ()
        kwdefaults = getattr(function, "__kwdefaults__", None) or {}
        covered_function_ids = getattr(
            _LIVE_PACKAGE_SNAPSHOT_LOCAL,
            "covered_function_ids",
            frozenset(),
        )
        cacheable = (
            identity in covered_function_ids
            and not inspect.ismethod(value)
            and not (getattr(function, "__closure__", None) or ())
            and all(_is_stable_configuration_value(item) for item in defaults)
            and all(
                _is_stable_configuration_value(item)
                for item in kwdefaults.values()
            )
        )
        memo = getattr(
            _LIVE_PACKAGE_SNAPSHOT_LOCAL,
            "implementation_memo",
            None,
        )
        memo_key = ("function", identity)
        if cacheable and isinstance(memo, dict) and memo_key in memo:
            return memo[memo_key]
        seen.add(identity)
        try:
            _, source, normalized_code = _cached_callable_static_snapshot(
                function,
                getattr(function, "__code__", None),
            )
            captured = _callable_capture_identity(value, seen)
            result = {
                "entrypoint": entrypoint,
                "source_digest": canonical_digest(
                    {
                        "declared_source": source,
                        "normalized_code": normalized_code,
                        "captured_dependencies": captured,
                    },
                    CanonicalizationPolicy(),
                ),
            }
            if cacheable and isinstance(memo, dict):
                memo[memo_key] = result
            return result
        finally:
            seen.remove(identity)
    implementation = value if inspect.isclass(value) else type(value)
    identity = id(value)
    entrypoint = f"{implementation.__module__}:{implementation.__qualname__}"
    memo = getattr(
        _LIVE_PACKAGE_SNAPSHOT_LOCAL,
        "implementation_memo",
        None,
    )
    memo_key = ("class-implementation", id(implementation))
    if isinstance(memo, dict) and memo_key in memo:
        return memo[memo_key]
    if identity in seen:
        return {
            "entrypoint": entrypoint,
            "source_digest": canonical_digest(
                {"recursive_reference": entrypoint},
                CanonicalizationPolicy(),
            ),
        }
    seen.add(identity)
    source = _cached_class_source(implementation)
    try:
        live_members = {
            base_entrypoint: {
                name: _component_implementation_identity(member, seen)
                for name, member in members
            }
            for base_entrypoint, members in _class_runtime_member_groups(
                implementation
            )
        }
        result = {
            "entrypoint": entrypoint,
            "source_digest": canonical_digest(
                {
                    "declared_source": source,
                    "normalized_code": _normalized_callable_code(value),
                    "live_members": live_members,
                    "class_configuration": _stable_class_configuration(
                        implementation
                    ),
                },
                CanonicalizationPolicy(),
            ),
        }
        if isinstance(memo, dict):
            memo[memo_key] = result
        return result
    finally:
        seen.remove(identity)


def component_implementation_digest(value: object | None) -> str:
    return canonical_digest(
        component_implementation_identity(value),
        CanonicalizationPolicy(),
    )


def component_runtime_identity(value: object | None) -> dict[str, object]:
    """绑定实现代码及影响行为的稳定实例配置，排除机器绝对路径。"""
    identity: dict[str, object] = dict(component_implementation_identity(value))
    provider = getattr(value, "runtime_identity", None)
    if callable(provider):
        configuration = provider()
        if not isinstance(configuration, Mapping):
            raise ValueError("component runtime identity must be a mapping")
        identity["identity_contract"] = "explicit-runtime-identity.v1"
        identity["configuration"] = _stable_configuration_value(configuration)
        identity["behavior_module"] = component_module_runtime_identity(value)
    else:
        identity["identity_contract"] = "inferred-runtime-identity.v1"
        identity["configuration"] = _stable_component_configuration(value)
    return identity


def component_runtime_digest(value: object | None) -> str:
    return canonical_digest(
        component_runtime_identity(value),
        CanonicalizationPolicy(),
    )


def has_explicit_runtime_identity(value: object | None) -> bool:
    return callable(getattr(value, "runtime_identity", None))


def component_module_runtime_identity(value: object) -> dict[str, str]:
    """绑定 live 模块行为与完整 optimization 发布构建物。"""
    with optimization_runtime_identity_snapshot():
        module_name = (
            value.__module__
            if inspect.isfunction(value) or inspect.isclass(value)
            else type(value).__module__
        )
        module = sys.modules.get(module_name)
        if module is None:
            return {
                "module": module_name,
                "module_digest": canonical_digest(
                    module_name,
                    CanonicalizationPolicy(),
                ),
            }
        return {
            "module": module_name,
            "module_digest": canonical_digest(
                {
                    "identity_contract": "live-module-behavior.v1",
                    "release_artifact_digest": (
                        _optimization_release_artifact_digest()
                    ),
                    "live_package_digest": _optimization_live_package_digest(),
                },
                CanonicalizationPolicy(),
            ),
        }


def _optimization_release_artifact_digest() -> str:
    """文件清单变化时重散列发布构建物，避免永久缓存掩盖热更新。"""
    snapshot = getattr(_LIVE_PACKAGE_SNAPSHOT_LOCAL, "snapshot", None)
    if snapshot is not None:
        return str(snapshot[3])
    package_root, signature = _optimization_dependency_source_signature()
    return _cached_release_artifact_digest(str(package_root), signature)


@lru_cache(maxsize=8)
def _cached_release_artifact_digest(
    package_root_value: str,
    signature: tuple[tuple[str, int, int], ...],
) -> str:
    package_root = Path(package_root_value)
    sources = {
        relative: canonical_digest(
            (package_root / relative).read_text(encoding="utf-8"),
            CanonicalizationPolicy(),
        )
        for relative, _, _ in signature
    }
    return canonical_digest(
        {
            "identity_contract": "optimization-release-dependency-artifact.v1",
            "sources": sources,
        },
        CanonicalizationPolicy(),
    )


def _optimization_live_package_digest() -> str:
    """用前后相同的完整 token 缓存不可变快照，拒绝并发热变更投毒。"""

    snapshot = getattr(_LIVE_PACKAGE_SNAPSHOT_LOCAL, "snapshot", None)
    if snapshot is not None:
        return str(snapshot[2])
    with optimization_runtime_identity_snapshot():
        return str(_LIVE_PACKAGE_SNAPSHOT_LOCAL.snapshot[2])


def _resolve_live_package_digest() -> tuple[object, str]:
    return _resolve_live_package_digest_for_token(_live_package_fast_token())


def _resolve_live_package_digest_for_token(
    before: object,
) -> tuple[object, str]:
    for _ in range(3):
        with _LIVE_PACKAGE_DIGEST_CACHE_LOCK:
            cached = _LIVE_PACKAGE_DIGEST_CACHE.get(before)
        if cached is not None:
            return before, cached
        _cached_optimization_dependency_scope.cache_clear()
        digest = _compute_live_package_digest()
        after = _live_package_fast_token()
        if before != after:
            before = after
            continue
        with _LIVE_PACKAGE_DIGEST_CACHE_LOCK:
            if len(_LIVE_PACKAGE_DIGEST_CACHE) >= 8:
                _LIVE_PACKAGE_DIGEST_CACHE.pop(
                    next(iter(_LIVE_PACKAGE_DIGEST_CACHE))
                )
            _LIVE_PACKAGE_DIGEST_CACHE[before] = digest
        return before, digest
    raise ValueError("optimization live package changed during identity snapshot")


@contextmanager
def optimization_runtime_identity_snapshot():
    """同一 manifest 内复用快照，并用廉价 live token 自动发现漂移。"""

    existing = getattr(_LIVE_PACKAGE_SNAPSHOT_LOCAL, "snapshot", None)
    if existing is not None:
        yield
        return
    generation = _LIVE_PACKAGE_GENERATION
    fast_token = _live_package_fast_token()
    _token, digest = _resolve_live_package_digest_for_token(fast_token)
    dependency_scope = _optimization_dependency_scope()
    release_digest = _cached_release_artifact_digest(
        str(_optimization_dependency_source_root()),
        fast_token[1],
    )
    if generation != _LIVE_PACKAGE_GENERATION:
        raise ValueError(
            "optimization runtime generation changed or live binding changed "
            "during snapshot"
        )
    _LIVE_PACKAGE_SNAPSHOT_LOCAL.snapshot = (
        generation,
        fast_token,
        digest,
        release_digest,
    )
    _LIVE_PACKAGE_SNAPSHOT_LOCAL.covered_function_ids = (
        fast_token[2] if len(fast_token) > 2 else frozenset()
    )
    _LIVE_PACKAGE_SNAPSHOT_LOCAL.active_dependency_scope = dependency_scope
    _LIVE_PACKAGE_SNAPSHOT_LOCAL.implementation_memo = {}
    dependency_scope_active = True
    try:
        yield
    except BaseException:
        raise
    else:
        del _LIVE_PACKAGE_SNAPSHOT_LOCAL.active_dependency_scope
        dependency_scope_active = False
        if (
            generation != _LIVE_PACKAGE_GENERATION
            or fast_token != _live_package_fast_token()
        ):
            raise ValueError(
                "optimization runtime generation changed or live binding changed "
                "during manifest snapshot"
            )
    finally:
        if dependency_scope_active:
            del _LIVE_PACKAGE_SNAPSHOT_LOCAL.active_dependency_scope
        del _LIVE_PACKAGE_SNAPSHOT_LOCAL.implementation_memo
        del _LIVE_PACKAGE_SNAPSHOT_LOCAL.covered_function_ids
        del _LIVE_PACKAGE_SNAPSHOT_LOCAL.snapshot


def _compute_live_package_digest() -> str:
    scope = _optimization_dependency_scope()
    modules: dict[str, dict[str, object]] = {
        module_name: {"callables": {}}
        for module_name in scope.module_names
    }
    _LIVE_PACKAGE_SNAPSHOT_LOCAL.active_dependency_scope = scope
    _LIVE_PACKAGE_SNAPSHOT_LOCAL.generation_covered_function_ids = (
        scope.covered_function_ids
    )
    try:
        for item in scope.nodes:
            module_name = str(getattr(item, "__module__", "") or "")
            if module_name not in modules:
                continue
            callables = modules[module_name]["callables"]
            assert isinstance(callables, dict)
            callables[
                str(getattr(item, "__qualname__", type(item).__qualname__))
            ] = _release_node_callable_identity(item)
    finally:
        del _LIVE_PACKAGE_SNAPSHOT_LOCAL.generation_covered_function_ids
        del _LIVE_PACKAGE_SNAPSHOT_LOCAL.active_dependency_scope
    return canonical_digest(
        {
            "identity_contract": "optimization-live-package.v1",
            "identity_measurement_kernel_digest": (
                _identity_measurement_kernel_digest()
            ),
            "identity_measurement_policy_digest": (
                _identity_measurement_policy_digest()
            ),
            "modules": modules,
        },
        CanonicalizationPolicy(),
    )


def _release_node_callable_identity(value: object) -> dict[str, str]:
    if inspect.isfunction(value) or inspect.ismethod(value):
        function = value.__func__ if inspect.ismethod(value) else value
        entrypoint, source, normalized_code = _cached_callable_static_snapshot(
            function,
            getattr(function, "__code__", None),
        )
        closure = getattr(function, "__closure__", None) or ()
        freevars = getattr(
            getattr(function, "__code__", None),
            "co_freevars",
            (),
        )
        payload = {
            "declared_source": source,
            "normalized_code": normalized_code,
            "referenced_dependencies": {
                key: _bounded_release_dependency(
                    function,
                    key,
                    item,
                    _BoundedDependencyState(active=set()),
                    depth=0,
                )
                for key, item in sorted(
                    _function_live_dependency_bindings(function).items()
                )
            },
            "closure": {
                name: _release_node_value_identity(cell.cell_contents, set())
                for name, cell in zip(freevars, closure, strict=True)
            },
            "defaults": [
                _release_node_value_identity(item, set())
                for item in (getattr(function, "__defaults__", None) or ())
            ],
            "kwdefaults": {
                key: _release_node_value_identity(item, set())
                for key, item in sorted(
                    (getattr(function, "__kwdefaults__", None) or {}).items()
                )
            },
        }
        return {
            "entrypoint": entrypoint,
            "source_digest": canonical_digest(
                _normalize_bounded_stable_value(payload),
                CanonicalizationPolicy(),
            ),
        }
    entrypoint = f"{value.__module__}:{value.__qualname__}"
    covered_function_ids = _optimization_dependency_scope().covered_function_ids
    members = {
        base_entrypoint: {
            name: (
                {
                    "release_callable": (
                        f"{member.__module__}:{member.__qualname__}"
                    )
                }
                if id(member) in covered_function_ids
                else _release_node_callable_identity(member)
            )
            for name, member in base_members
        }
        for base_entrypoint, base_members in _release_class_runtime_member_groups(
            value
        )
    }
    configuration: dict[str, object] = {}
    for base in reversed(value.__mro__):
        if base is object:
            continue
        if (
            base.__module__ != value.__module__
            and not base.__module__.startswith("ai_sdlc.")
        ):
            continue
        prefix = f"{base.__module__}:{base.__qualname__}"
        for name, item in sorted(vars(base).items()):
            if (
                name in _CLASS_CONFIGURATION_IGNORED_FIELDS
                or (name.startswith("__") and name.endswith("__"))
                or name.startswith("__dataclass_")
                or name.startswith("_abc_")
                or _runtime_member_callable(item) is not None
                or inspect.isdatadescriptor(item)
            ):
                continue
            configuration[f"{prefix}.{name}"] = _release_node_value_identity(
                item,
                set(),
            )
    return {
        "entrypoint": entrypoint,
        "source_digest": canonical_digest(
            _normalize_bounded_stable_value(
                {
                    "declared_source": _cached_class_source(value),
                    "members": members,
                    "generated_members": _generated_class_member_identity(value),
                    "configuration": configuration,
                }
            ),
            CanonicalizationPolicy(),
        ),
    }


def _release_node_value_identity(value: object, active: set[int]) -> object:
    if value is Ellipsis:
        return {"singleton": "ellipsis"}
    if value is NotImplemented:
        return {"singleton": "not-implemented"}
    if _is_stable_configuration_value(value):
        return _bounded_stable_value(value)
    identity = id(value)
    if identity in active:
        return {
            "recursive_release_value": (
                f"{type(value).__module__}:{type(value).__qualname__}"
            )
        }
    active.add(identity)
    try:
        if isinstance(value, Mapping):
            return {
                str(key): _release_node_value_identity(item, active)
                for key, item in sorted(
                    value.items(),
                    key=lambda pair: str(pair[0]),
                )
            }
        if isinstance(value, (tuple, list, set, frozenset)):
            items = [
                _release_node_value_identity(item, active) for item in value
            ]
            if isinstance(value, (set, frozenset)):
                items.sort(
                    key=lambda item: canonical_digest(
                        item,
                        CanonicalizationPolicy(),
                    )
                )
            return items
        if isinstance(value, partial):
            return {
                "partial": (
                    f"{value.func.__module__}:"
                    f"{getattr(value.func, '__qualname__', type(value.func).__qualname__)}"
                ),
                "args": [
                    _release_node_value_identity(item, active)
                    for item in value.args
                ],
                "keywords": {
                    key: _release_node_value_identity(item, active)
                    for key, item in sorted((value.keywords or {}).items())
                },
            }
        if inspect.isfunction(value) or inspect.ismethod(value):
            function = value.__func__ if inspect.ismethod(value) else value
            return {
                "callable": f"{function.__module__}:{function.__qualname__}",
            }
        if inspect.isclass(value):
            return {"class": f"{value.__module__}:{value.__qualname__}"}
        if isinstance(value, ModuleType):
            return {"module": value.__name__}
        provider = getattr(value, "runtime_identity", None)
        if callable(provider):
            configuration = provider()
            if isinstance(configuration, Mapping):
                return {
                    "entrypoint": (
                        f"{type(value).__module__}:{type(value).__qualname__}"
                    ),
                    "configuration": _bounded_stable_value(configuration),
                }
        return {
            "runtime_value_type": (
                f"{type(value).__module__}:{type(value).__qualname__}"
            )
        }
    finally:
        active.remove(identity)


def _live_package_generation_token() -> tuple[object, frozenset[int]]:
    scope = _optimization_dependency_scope()
    modules: dict[str, list[object]] = {
        module_name: [] for module_name in scope.module_names
    }
    callable_memo: dict[tuple[str, int], object] = {}
    _LIVE_PACKAGE_SNAPSHOT_LOCAL.fast_dependency_memo = {}
    _LIVE_PACKAGE_SNAPSHOT_LOCAL.generation_covered_function_ids = (
        scope.covered_function_ids
    )
    try:
        for item in scope.nodes:
            module_name = str(getattr(item, "__module__", "") or "")
            if module_name not in modules:
                continue
            modules[module_name].append(
                (
                    str(getattr(item, "__qualname__", type(item).__qualname__)),
                    "class" if inspect.isclass(item) else "function",
                    _live_callable_cache_token(
                        item,
                        memo=callable_memo,
                    ),
                )
            )
    finally:
        del _LIVE_PACKAGE_SNAPSHOT_LOCAL.generation_covered_function_ids
        del _LIVE_PACKAGE_SNAPSHOT_LOCAL.fast_dependency_memo
    return (
        (
            _identity_measurement_policy_digest(),
            _identity_measurement_kernel_binding_token(),
            tuple(
                (module_name, tuple(sorted(bindings, key=lambda item: item[0])))
                for module_name, bindings in sorted(modules.items())
            ),
        ),
        scope.covered_function_ids,
    )


def _live_package_fast_token(
) -> tuple[
    object,
    tuple[tuple[str, int, int], ...],
    frozenset[int],
]:
    _package_root, release_signature = _optimization_dependency_source_signature()
    generation_token, covered_function_ids = _live_package_generation_token()
    return generation_token, release_signature, covered_function_ids


def _is_runtime_identity_infrastructure_name(name: str) -> bool:
    return (
        name.startswith("__")
        or name.endswith(("_CACHE", "_LOCK"))
        or name
        in {
            "_LIVE_PACKAGE_GENERATION",
            "_LIVE_PACKAGE_SNAPSHOT_LOCAL",
        }
    )


def _is_identity_measurement_kernel_callable(value: object) -> bool:
    candidate = value.__func__ if inspect.ismethod(value) else value
    return (
        str(getattr(candidate, "__module__", "") or "") == __name__
        and str(getattr(candidate, "__name__", "") or "")
        in _IDENTITY_MEASUREMENT_KERNEL_CALLABLES
    )


def _identity_measurement_policy_digest() -> str:
    return canonical_digest(
        {
            "identity_contract": "optimization-identity-policy.v1",
            "bounded_dependency_max_depth": _BOUNDED_DEPENDENCY_MAX_DEPTH,
            "bounded_dependency_max_nodes": _BOUNDED_DEPENDENCY_MAX_NODES,
            "identity_wrapper_max_depth": _IDENTITY_WRAPPER_MAX_DEPTH,
            "class_configuration_ignored_fields": sorted(
                _CLASS_CONFIGURATION_IGNORED_FIELDS
            ),
            "kernel_callables": sorted(_IDENTITY_MEASUREMENT_KERNEL_CALLABLES),
            "stateful_stdlib_runtime_objects": sorted(
                _STATEFUL_STDLIB_RUNTIME_OBJECTS
            ),
            "transient_runtime_fields": sorted(_TRANSIENT_RUNTIME_FIELDS),
            "transitive_behavior_fields": sorted(_TRANSITIVE_BEHAVIOR_FIELDS),
            "trusted_stdlib_runtime_objects": sorted(
                _TRUSTED_STDLIB_RUNTIME_OBJECTS
            ),
        },
        CanonicalizationPolicy(),
    )


def _identity_measurement_callable_chain(value: object) -> tuple[object, ...]:
    chain: list[object] = []
    active: set[int] = set()
    current = value
    missing = object()
    for _ in range(_IDENTITY_WRAPPER_MAX_DEPTH):
        if not callable(current):
            raise ValueError("identity kernel wrapper chain contains non-callable")
        identity = id(current)
        if identity in active:
            raise ValueError("identity kernel wrapper chain is recursive")
        active.add(identity)
        chain.append(current)
        wrapped = getattr(current, "__wrapped__", missing)
        if wrapped is missing:
            return tuple(chain)
        current = wrapped
    raise ValueError("identity kernel wrapper chain exceeds bounded depth")


def _identity_measurement_kernel_binding_token() -> tuple[object, ...]:
    bindings: list[object] = []
    namespace = vars(sys.modules[__name__])
    for name in sorted(_IDENTITY_MEASUREMENT_KERNEL_CALLABLES):
        value = namespace.get(name)
        if inspect.isclass(value):
            bindings.append(
                (
                    name,
                    "class",
                    id(value),
                    tuple(
                        (
                            member_name,
                            id(member),
                            id(getattr(member, "__code__", None)),
                        )
                        for _, members in _class_runtime_member_groups(value)
                        for member_name, member in members
                    ),
                )
            )
        elif callable(value):
            bindings.append(
                (
                    name,
                    "callable-chain",
                    tuple(
                        (
                            f"{type(item).__module__}:{type(item).__qualname__}",
                            str(getattr(item, "__module__", "") or ""),
                            str(getattr(item, "__qualname__", "") or ""),
                            id(item),
                            id(getattr(item, "__code__", None)),
                            _stable_cache_token(
                                getattr(item, "__defaults__", None) or ()
                            ),
                            _stable_cache_token(
                                getattr(item, "__kwdefaults__", None) or {}
                            ),
                            (
                                _stable_cache_token(item.cache_parameters())
                                if callable(
                                    getattr(item, "cache_parameters", None)
                                )
                                else ()
                            ),
                        )
                        for item in _identity_measurement_callable_chain(value)
                    ),
                )
            )
        else:
            bindings.append((name, "missing"))
    return tuple(bindings)


def _identity_measurement_kernel_digest() -> str:
    callables: dict[str, object] = {}
    namespace = vars(sys.modules[__name__])
    for name in sorted(_IDENTITY_MEASUREMENT_KERNEL_CALLABLES):
        value = namespace.get(name)
        if inspect.isclass(value):
            callables[name] = {
                "entrypoint": f"{value.__module__}:{value.__qualname__}",
                "source": _cached_class_source(value),
                "members": {
                    f"{base_entrypoint}.{member_name}": (
                        _cached_callable_static_snapshot(
                            member,
                            getattr(member, "__code__", None),
                        )
                    )
                    for base_entrypoint, members in _class_runtime_member_groups(
                        value
                    )
                    for member_name, member in members
                },
            }
        elif callable(value):
            chain = []
            for item in _identity_measurement_callable_chain(value):
                if inspect.isfunction(item):
                    entrypoint, source, code = _cached_callable_static_snapshot(
                        item,
                        item.__code__,
                    )
                    chain.append(
                        {
                            "binding": "function",
                            "entrypoint": entrypoint,
                            "source": source,
                            "code": code,
                            "defaults": _stable_cache_token(
                                item.__defaults__ or ()
                            ),
                            "kwdefaults": _stable_cache_token(
                                item.__kwdefaults__ or {}
                            ),
                        }
                    )
                else:
                    cache_parameters = getattr(item, "cache_parameters", None)
                    chain.append(
                        {
                            "binding": "callable-wrapper",
                            "entrypoint": (
                                f"{type(item).__module__}:"
                                f"{type(item).__qualname__}"
                            ),
                            "module": str(
                                getattr(item, "__module__", "") or ""
                            ),
                            "qualname": str(
                                getattr(item, "__qualname__", "") or ""
                            ),
                            "cache_parameters": (
                                _stable_cache_token(cache_parameters())
                                if callable(cache_parameters)
                                else ()
                            ),
                        }
                    )
            callables[name] = {"wrapped_chain": chain}
        else:
            callables[name] = {"binding": "missing"}
    return canonical_digest(
        {
            "identity_contract": "optimization-identity-kernel.v1",
            "policy_digest": _identity_measurement_policy_digest(),
            "callables": callables,
        },
        CanonicalizationPolicy(),
    )


def invalidate_optimization_runtime_identity() -> None:
    """受控热更新修改可变闭包或容器后，显式推进 runtime generation。"""

    global _LIVE_PACKAGE_GENERATION
    with _LIVE_PACKAGE_DIGEST_CACHE_LOCK:
        _LIVE_PACKAGE_GENERATION += 1
        _LIVE_PACKAGE_DIGEST_CACHE.clear()
        _optimization_release_module_names.cache_clear()
        _cached_optimization_dependency_scope.cache_clear()
        _cached_release_artifact_digest.cache_clear()


def _live_callable_cache_token(
    value: object,
    seen: set[int] | None = None,
    memo: dict[tuple[str, int], object] | None = None,
) -> object:
    active = set() if seen is None else seen
    if inspect.isfunction(value) or inspect.ismethod(value):
        function = value.__func__ if inspect.ismethod(value) else value
        identity = id(function)
        memo_key = ("function", identity)
        closure = getattr(function, "__closure__", None) or ()
        if not closure and memo is not None and memo_key in memo:
            return memo[memo_key]
        if identity in active:
            return (
                "recursive-function",
                f"{function.__module__}:{function.__qualname__}",
            )
        active.add(identity)
        try:
            token = (
                "function",
                identity,
                id(getattr(function, "__code__", None)),
                tuple(
                    (
                        key,
                        _release_dependency_fast_token(
                            function,
                            key,
                            item,
                            set(),
                        ),
                    )
                    for key, item in sorted(
                        _function_live_dependency_bindings(function).items()
                    )
                ),
                tuple(
                    _live_capture_cache_token(
                        cell.cell_contents,
                        active,
                        memo,
                    )
                    for cell in closure
                ),
                tuple(
                    _live_capture_cache_token(item, active, memo)
                    for item in (getattr(function, "__defaults__", None) or ())
                ),
                tuple(
                    (
                        key,
                        _live_capture_cache_token(item, active, memo),
                    )
                    for key, item in sorted(
                        (getattr(function, "__kwdefaults__", None) or {}).items()
                    )
                ),
            )
            if not closure and memo is not None:
                memo[memo_key] = token
            return token
        finally:
            active.remove(identity)
    if inspect.isclass(value):
        identity = id(value)
        memo_key = ("class", identity)
        if memo is not None and memo_key in memo:
            return memo[memo_key]
        if identity in active:
            return (
                "recursive-class",
                f"{value.__module__}:{value.__qualname__}",
            )
        active.add(identity)
        try:
            token = (
                "class",
                identity,
                _live_class_configuration_token(value, active, memo),
                tuple(
                    (
                        base_entrypoint,
                        tuple(
                            (
                                name,
                                (
                                    id(member),
                                    id(getattr(member, "__code__", None)),
                                ),
                            )
                            for name, member in members
                        ),
                    )
                    for base_entrypoint, members in _release_class_runtime_member_groups(
                        value
                    )
                ),
                _generated_class_member_fast_token(value),
            )
            if memo is not None:
                memo[memo_key] = token
            return token
        finally:
            active.remove(identity)
    return ("object", id(value))


def _release_dependency_fast_token(
    owner_function: object,
    dependency_name: str,
    value: object,
    active: set[int] | _FastDependencyState,
    *,
    depth: int = 0,
) -> object:
    if _is_identity_measurement_kernel_callable(value):
        candidate = value.__func__ if inspect.ismethod(value) else value
        return (
            "identity-kernel-callable",
            str(getattr(candidate, "__name__", "") or ""),
            id(candidate),
            id(getattr(candidate, "__code__", None)),
        )
    state = _fast_dependency_state(active)
    state.consume(depth)
    if isinstance(value, ModuleType):
        attributes = _referenced_module_attributes(
            owner_function,
            dependency_name,
        )
        if not attributes:
            return ("module", value.__name__, "dynamic-attribute")
        captured = []
        for path in attributes:
            current: object = value
            available = True
            for attribute in path:
                try:
                    current = getattr(current, attribute)
                except (AttributeError, TypeError, ValueError):
                    available = False
                    break
            captured.append(
                (
                    ".".join(path),
                    (
                        _release_runtime_value_fast_token(
                            current,
                            state,
                            depth=depth + 1,
                        )
                        if available
                        else (
                            "unavailable",
                            tuple(sys.version_info[:3]),
                            sys.platform,
                        )
                    ),
                )
            )
        return ("module", value.__name__, tuple(captured))
    return _release_runtime_value_fast_token(
        value,
        state,
        depth=depth + 1,
    )


@dataclass
class _FastDependencyState:
    active: set[int]
    memo: dict[int, object]
    nodes: int = 0
    max_nodes: int = _BOUNDED_DEPENDENCY_MAX_NODES
    max_depth: int = _BOUNDED_DEPENDENCY_MAX_DEPTH

    def consume(self, depth: int) -> None:
        self.nodes += 1
        if depth > self.max_depth or self.nodes > self.max_nodes:
            raise ValueError("fast dependency graph exceeds bounded limits")


def _fast_dependency_state(
    value: set[int] | _FastDependencyState,
) -> _FastDependencyState:
    if isinstance(value, _FastDependencyState):
        return value
    shared_memo = getattr(
        _LIVE_PACKAGE_SNAPSHOT_LOCAL,
        "fast_dependency_memo",
        None,
    )
    return _FastDependencyState(
        active=value,
        memo=shared_memo if isinstance(shared_memo, dict) else {},
    )


def _release_runtime_value_fast_token(
    value: object,
    active: set[int] | _FastDependencyState,
    *,
    depth: int = 0,
) -> object:
    state = _fast_dependency_state(active)
    state.consume(depth)
    if value is Ellipsis:
        return ("singleton", "ellipsis")
    if value is NotImplemented:
        return ("singleton", "not-implemented")
    if _is_stable_configuration_value(value):
        return _stable_cache_token(_bounded_stable_value(value))
    identity = id(value)
    if identity in state.memo:
        return state.memo[identity]
    if identity in state.active:
        return (
            "recursive",
            f"{type(value).__module__}:{type(value).__qualname__}",
        )
    state.active.add(identity)
    try:
        if isinstance(value, Mapping):
            return (
                "mapping",
                tuple(
                    (
                        _release_runtime_value_fast_token(
                            key,
                            state,
                            depth=depth + 1,
                        ),
                        _release_runtime_value_fast_token(
                            item,
                            state,
                            depth=depth + 1,
                        ),
                    )
                    for key, item in sorted(
                        value.items(),
                        key=lambda pair: str(pair[0]),
                    )
                ),
            )
        if isinstance(value, (tuple, list, set, frozenset)):
            items = [
                _release_runtime_value_fast_token(
                    item,
                    state,
                    depth=depth + 1,
                )
                for item in value
            ]
            if isinstance(value, (set, frozenset)):
                items.sort(key=repr)
            return (
                f"{type(value).__module__}:{type(value).__qualname__}",
                tuple(items),
            )
        if isinstance(value, ModuleType):
            return ("module", value.__name__)
        if isinstance(value, partial):
            return (
                "partial",
                _release_runtime_value_fast_token(
                    value.func,
                    state,
                    depth=depth + 1,
                ),
                tuple(
                    _release_runtime_value_fast_token(
                        item,
                        state,
                        depth=depth + 1,
                    )
                    for item in value.args
                ),
                tuple(
                    (
                        key,
                        _release_runtime_value_fast_token(
                            item,
                            state,
                            depth=depth + 1,
                        ),
                    )
                    for key, item in sorted((value.keywords or {}).items())
                ),
            )
        if inspect.isfunction(value) or inspect.ismethod(value):
            function = value.__func__ if inspect.ismethod(value) else value
            receiver = value.__self__ if inspect.ismethod(value) else None
            dependencies: tuple[object, ...] = ()
            release_covered = _function_is_release_covered(function)
            function_module = str(getattr(function, "__module__", "") or "")
            live_module_member = (
                False
                if release_covered
                else _function_is_live_module_member(function)
            )
            trusted_stdlib = (
                function_module.partition(".")[0] in sys.stdlib_module_names
                and live_module_member
            )
            installed_provenance = (
                _installed_module_provenance(sys.modules.get(function_module))
                if live_module_member
                else None
            )
            if (
                not release_covered
                and not trusted_stdlib
                and installed_provenance is None
            ):
                dependencies_by_name = _function_live_dependency_bindings(function)
                if dependencies_by_name:
                    dependencies = tuple(
                        (
                            key,
                            _release_dependency_fast_token(
                                function,
                                key,
                                item,
                                state,
                                depth=depth + 1,
                            ),
                        )
                        for key, item in sorted(
                            dependencies_by_name.items()
                        )
                    )
            token = (
                "callable",
                id(function),
                id(getattr(function, "__code__", None)),
                dependencies,
                tuple(
                    _release_runtime_value_fast_token(
                        item,
                        state,
                        depth=depth + 1,
                    )
                    for item in (getattr(function, "__defaults__", None) or ())
                ),
                tuple(
                    (
                        key,
                        _release_runtime_value_fast_token(
                            item,
                            state,
                            depth=depth + 1,
                        ),
                    )
                    for key, item in sorted(
                        (getattr(function, "__kwdefaults__", None) or {}).items()
                    )
                ),
                (
                    None
                    if receiver is None
                    else _release_runtime_value_fast_token(
                        receiver,
                        state,
                        depth=depth + 1,
                    )
                ),
            )
            state.memo[identity] = token
            return token
        if inspect.isclass(value):
            token = (
                "class",
                id(value),
                tuple(
                    (
                        name,
                        id(member),
                        id(getattr(member, "__code__", None)),
                    )
                    for _, members in _release_class_runtime_member_groups(value)
                    for name, member in members
                ),
            )
            state.memo[identity] = token
            return token
        if inspect.isbuiltin(value):
            receiver = getattr(value, "__self__", None)
            if receiver is sys.modules and getattr(value, "__name__", "") == "get":
                return (
                    "builtin",
                    "builtins:dict.get",
                    "runtime-module-registry",
                    tuple(sys.version_info[:3]),
                    sys.platform,
                )
            return (
                "builtin",
                getattr(value, "__module__", type(value).__module__),
                getattr(value, "__qualname__", type(value).__qualname__),
                (
                    None
                    if receiver is None or isinstance(receiver, (ModuleType, type))
                    else _release_runtime_value_fast_token(
                        receiver,
                        state,
                        depth=depth + 1,
                    )
                ),
            )
        if (
            type(value).__module__ == "re"
            and type(value).__qualname__ == "Pattern"
        ):
            return ("re-pattern", value.pattern, value.flags)
        provider = getattr(value, "runtime_identity", None)
        if callable(provider):
            configuration = provider()
            if isinstance(configuration, Mapping):
                return (
                    "runtime-identity",
                    f"{type(value).__module__}:{type(value).__qualname__}",
                    _stable_cache_token(
                        _normalize_bounded_stable_value(
                            _stable_configuration_value(configuration)
                        )
                    ),
                )
        state_items, enumerable = _object_state_items(value)
        return (
            "object",
            f"{type(value).__module__}:{type(value).__qualname__}",
            enumerable,
            tuple(
                (
                    key,
                    _release_runtime_value_fast_token(
                        item,
                        state,
                        depth=depth + 1,
                    ),
                )
                for key, item in state_items
                if key not in _TRANSIENT_RUNTIME_FIELDS
            ),
        )
    finally:
        state.active.remove(identity)


def _live_class_configuration_token(
    implementation: type[object],
    seen: set[int],
    memo: dict[tuple[str, int], object] | None,
) -> object:
    if issubclass(implementation, BaseModel):
        return (
            "pydantic",
            id(getattr(implementation, "__pydantic_core_schema__", None)),
            id(getattr(implementation, "__pydantic_validator__", None)),
            id(getattr(implementation, "__pydantic_serializer__", None)),
            _stable_cache_token(getattr(implementation, "model_config", {})),
        )
    configured = []
    for base in reversed(implementation.__mro__):
        if base is object:
            continue
        if (
            base.__module__ != implementation.__module__
            and not base.__module__.startswith("ai_sdlc.")
        ):
            continue
        prefix = f"{base.__module__}:{base.__qualname__}"
        for name, item in sorted(vars(base).items()):
            if (
                name in _CLASS_CONFIGURATION_IGNORED_FIELDS
                or (name.startswith("__") and name.endswith("__"))
                or name.startswith("__dataclass_")
                or name.startswith("_abc_")
                or _runtime_member_callable(item) is not None
                or inspect.isdatadescriptor(item)
            ):
                continue
            configured.append(
                (
                    f"{prefix}.{name}",
                    _live_capture_cache_token(item, seen, memo),
                )
            )
    return tuple(configured)


def _live_capture_cache_token(
    value: object,
    seen: set[int],
    memo: dict[tuple[str, int], object] | None,
) -> object:
    if _is_stable_configuration_value(value):
        return _stable_cache_token(value)
    if isinstance(value, partial):
        return (
            "partial",
            _live_callable_cache_token(value.func, seen, memo),
            tuple(
                _live_capture_cache_token(item, seen, memo)
                for item in value.args
            ),
            tuple(
                (key, _live_capture_cache_token(item, seen, memo))
                for key, item in sorted((value.keywords or {}).items())
            ),
        )
    if inspect.isfunction(value) or inspect.ismethod(value) or inspect.isclass(value):
        return _live_callable_cache_token(value, seen, memo)
    configuration, unsupported = _stable_captured_object_configuration(value)
    return (
        type(value).__module__,
        type(value).__qualname__,
        _stable_cache_token(
            {"configuration": configuration, "unsupported": unsupported}
        ),
    )


def _stable_cache_token(value: object) -> object:
    stable = (
        _stable_configuration_value(value)
        if _is_stable_configuration_value(value)
        else value
    )
    if isinstance(stable, Mapping):
        return tuple(
            (str(key), _stable_cache_token(item))
            for key, item in sorted(stable.items())
        )
    if isinstance(stable, (tuple, list)):
        return tuple(_stable_cache_token(item) for item in stable)
    return stable


@lru_cache(maxsize=1)
def _optimization_release_module_names() -> tuple[str, ...]:
    package_root = Path(__file__).parent
    prefix = "ai_sdlc.core.stage_review.optimization"
    names = []
    for path in package_root.rglob("*.py"):
        relative = path.relative_to(package_root)
        parts = relative.with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names.append(".".join((prefix, *parts)) if parts else prefix)
    return tuple(sorted(set(names)))


@dataclass(frozen=True)
class _OptimizationDependencyScope:
    module_names: tuple[str, ...]
    nodes: tuple[object, ...]
    covered_function_ids: frozenset[int]


def _optimization_seed_binding_token() -> tuple[object, ...]:
    token = []
    for module_name in _optimization_release_module_names():
        module = importlib.import_module(module_name)
        bindings = []
        for name, item in sorted(vars(module).items()):
            if _is_identity_measurement_kernel_callable(item):
                continue
            if inspect.isfunction(item) and item.__module__ == module_name:
                bindings.append(
                    (name, "function", id(item), id(item.__code__))
                )
            elif inspect.isclass(item) and item.__module__ == module_name:
                bindings.append(
                    (
                        name,
                        "class",
                        id(item),
                        tuple(
                            (member_name, id(member), id(member.__code__))
                            for _, members in _release_class_runtime_member_groups(
                                item
                            )
                            for member_name, member in members
                        ),
                    )
                )
        token.append((module_name, tuple(bindings)))
    return tuple(token)


def _optimization_dependency_scope() -> _OptimizationDependencyScope:
    active = getattr(
        _LIVE_PACKAGE_SNAPSHOT_LOCAL,
        "active_dependency_scope",
        None,
    )
    if active is not None:
        return active
    return _cached_optimization_dependency_scope(
        _optimization_seed_binding_token()
    )


@lru_cache(maxsize=4)
def _cached_optimization_dependency_scope(
    _seed_token: tuple[object, ...],
) -> _OptimizationDependencyScope:
    modules = set(_optimization_release_module_names())
    pending: list[object] = []
    for module_name in _optimization_release_module_names():
        module = importlib.import_module(module_name)
        pending.extend(
            item
            for item in vars(module).values()
            if not _is_identity_measurement_kernel_callable(item)
            and (
                (
                    inspect.isfunction(item)
                    and item.__module__ == module_name
                )
                or (
                    inspect.isclass(item)
                    and item.__module__ == module_name
                )
            )
        )
    nodes: dict[int, object] = {}
    covered_function_ids: set[int] = set()
    while pending:
        item = pending.pop()
        identity = id(item)
        if identity in nodes:
            continue
        nodes[identity] = item
        if inspect.isfunction(item) or inspect.ismethod(item):
            function = item.__func__ if inspect.ismethod(item) else item
            covered_function_ids.add(id(function))
            module_name = str(getattr(function, "__module__", "") or "")
            if module_name.startswith("ai_sdlc."):
                modules.add(module_name)
            pending.extend(_scope_function_dependencies(function, modules))
            continue
        if not inspect.isclass(item):
            continue
        module_name = str(getattr(item, "__module__", "") or "")
        if module_name.startswith("ai_sdlc."):
            modules.add(module_name)
        for implementation in item.__mro__:
            if _verified_first_party_scope_target(implementation):
                pending.append(implementation)
        for _, members in _release_class_runtime_member_groups(item):
            for _, member in members:
                covered_function_ids.add(id(member))
                pending.append(member)
        for base in item.__mro__:
            if (
                base is object
                or (
                    base.__module__ != item.__module__
                    and not base.__module__.startswith("ai_sdlc.")
                )
            ):
                continue
            for name, value in vars(base).items():
                if _runtime_member_callable(value) is None:
                    pending.extend(
                        _scope_value_targets(
                            None,
                            name,
                            value,
                            modules,
                            set(),
                        )
                    )
    ordered_nodes = tuple(
        sorted(
            nodes.values(),
            key=lambda value: (
                str(getattr(value, "__module__", "") or ""),
                str(getattr(value, "__qualname__", type(value).__qualname__)),
                "class" if inspect.isclass(value) else "function",
            ),
        )
    )
    return _OptimizationDependencyScope(
        module_names=tuple(sorted(modules)),
        nodes=ordered_nodes,
        covered_function_ids=frozenset(covered_function_ids),
    )


def _scope_function_dependencies(
    function: object,
    modules: set[str],
) -> list[object]:
    dependencies = _function_live_dependency_bindings(function)
    wrapped = getattr(function, "__wrapped__", None)
    targets: list[object] = (
        [wrapped]
        if (
            inspect.isfunction(wrapped)
            and str(getattr(wrapped, "__module__", "") or "").startswith(
                "ai_sdlc."
            )
        )
        else []
    )
    for name, value in dependencies.items():
        targets.extend(
            _scope_value_targets(function, name, value, modules, set())
        )
    for index, value in enumerate(
        getattr(function, "__defaults__", None) or ()
    ):
        targets.extend(
            _scope_value_targets(
                None,
                f"<default:{index}>",
                value,
                modules,
                set(),
            )
        )
    for name, value in (
        getattr(function, "__kwdefaults__", None) or {}
    ).items():
        targets.extend(
            _scope_value_targets(
                None,
                f"<kwdefault:{name}>",
                value,
                modules,
                set(),
            )
        )
    return targets


def _scope_value_targets(
    owner_function: object | None,
    dependency_name: str,
    value: object,
    modules: set[str],
    active: set[int],
) -> list[object]:
    identity = id(value)
    if identity in active:
        return []
    active.add(identity)
    try:
        candidates = [value]
        if isinstance(value, ModuleType):
            if (
                value.__name__.startswith("ai_sdlc.")
                and sys.modules.get(value.__name__) is value
            ):
                modules.add(value.__name__)
            if owner_function is not None:
                for path in _referenced_module_attributes(
                    owner_function,
                    dependency_name,
                ):
                    current: object = value
                    for attribute in path:
                        try:
                            current = getattr(current, attribute)
                        except (AttributeError, TypeError, ValueError):
                            current = value
                            break
                    candidates.append(current)
        elif isinstance(value, Mapping):
            candidates.extend(value.values())
        elif isinstance(value, (tuple, list, set, frozenset)):
            candidates.extend(value)
        elif isinstance(value, partial):
            candidates.extend((value.func, *value.args))
            candidates.extend((value.keywords or {}).values())
        targets: list[object] = []
        for candidate in candidates:
            if _verified_first_party_scope_target(candidate):
                targets.append(candidate)
            elif candidate is not value:
                targets.extend(
                    _scope_value_targets(
                        None,
                        dependency_name,
                        candidate,
                        modules,
                        active,
                    )
                )
        return targets
    finally:
        active.remove(identity)


def _verified_first_party_scope_target(value: object) -> bool:
    if _is_identity_measurement_kernel_callable(value):
        return False
    if inspect.isfunction(value) or inspect.ismethod(value):
        function = value.__func__ if inspect.ismethod(value) else value
        module_name = str(getattr(function, "__module__", "") or "")
        return (
            module_name.startswith("ai_sdlc.")
            and _function_is_live_module_member(function)
        )
    if inspect.isclass(value):
        module_name = str(getattr(value, "__module__", "") or "")
        module = sys.modules.get(module_name)
        return (
            module_name.startswith("ai_sdlc.")
            and module is not None
            and any(bound is value for bound in vars(module).values())
        )
    return False


def _optimization_dependency_module_names() -> tuple[str, ...]:
    return _optimization_dependency_scope().module_names


def _optimization_dependency_source_root() -> Path:
    return Path(__file__).parents[3]


def _optimization_dependency_source_signature(
) -> tuple[Path, tuple[tuple[str, int, int], ...]]:
    package_root = _optimization_dependency_source_root()
    paths: set[Path] = set()
    for module_name in _optimization_dependency_module_names():
        module = importlib.import_module(module_name)
        source = getattr(module, "__file__", None)
        if not source:
            continue
        path = Path(source)
        if path.suffix != ".py" or not path.is_file():
            continue
        try:
            path.relative_to(package_root)
        except ValueError:
            continue
        paths.add(path)
    signature = tuple(
        (
            path.relative_to(package_root).as_posix(),
            path.stat().st_mtime_ns,
            path.stat().st_size,
        )
        for path in sorted(paths)
    )
    return package_root, signature


def _live_callable_binding_identity(
    value: object,
    seen: set[int],
) -> dict[str, str]:
    identity = id(value)
    if inspect.isfunction(value) or inspect.ismethod(value):
        function = value.__func__ if inspect.ismethod(value) else value
        entrypoint, source, normalized_code = _cached_callable_static_snapshot(
            function,
            getattr(function, "__code__", None),
        )
        if identity in seen:
            return {
                "entrypoint": entrypoint,
                "source_digest": canonical_digest(
                    {"recursive_reference": entrypoint},
                    CanonicalizationPolicy(),
                ),
            }
        seen.add(identity)
        try:
            captured = _live_callable_capture_identity(value, seen)
            return {
                "entrypoint": entrypoint,
                "source_digest": canonical_digest(
                    {
                        "declared_source": source,
                        "normalized_code": normalized_code,
                        "captured_dependencies": captured,
                    },
                    CanonicalizationPolicy(),
                ),
            }
        finally:
            seen.remove(identity)
    if inspect.isclass(value):
        entrypoint = f"{value.__module__}:{value.__qualname__}"
        members = {
            base_entrypoint: {
                name: _live_callable_binding_identity(member, seen)
                for name, member in base_members
            }
            for base_entrypoint, base_members in _class_runtime_member_groups(value)
        }
        return {
            "entrypoint": entrypoint,
            "source_digest": canonical_digest(
                {
                    "live_members": members,
                    "class_configuration": _stable_class_configuration(value),
                },
                CanonicalizationPolicy(),
            ),
        }
    return component_implementation_identity(value)


@lru_cache(maxsize=4096)
def _cached_callable_static_snapshot(
    function: object,
    code: object,
) -> tuple[str, str, object]:
    entrypoint = f"{function.__module__}:{function.__qualname__}"
    try:
        source = inspect.getsource(function)
    except (OSError, TypeError):
        source = entrypoint
    normalized = _normalized_code_object(code) if isinstance(code, CodeType) else None
    return entrypoint, source, normalized


@lru_cache(maxsize=4096)
def _cached_class_source(implementation: type[object]) -> str:
    try:
        return inspect.getsource(implementation)
    except (OSError, TypeError):
        return f"{implementation.__module__}:{implementation.__qualname__}"


def _live_callable_capture_identity(
    value: object,
    seen: set[int],
) -> dict[str, object]:
    function = value.__func__ if inspect.ismethod(value) else value
    closure = getattr(function, "__closure__", None) or ()
    freevars = getattr(getattr(function, "__code__", None), "co_freevars", ())
    return {
        "closure": {
            name: _live_capture_identity(cell.cell_contents, seen)
            for name, cell in zip(freevars, closure, strict=True)
        },
        "defaults": [
            _live_capture_identity(item, seen)
            for item in (getattr(function, "__defaults__", None) or ())
        ],
        "kwdefaults": {
            key: _live_capture_identity(item, seen)
            for key, item in sorted(
                (getattr(function, "__kwdefaults__", None) or {}).items()
            )
        },
    }


def _live_capture_identity(value: object, seen: set[int]) -> object:
    if _is_stable_configuration_value(value):
        return _stable_configuration_value(value)
    if isinstance(value, partial):
        return {
            "callable": _live_callable_binding_identity(value.func, seen),
            "args": [_live_capture_identity(item, seen) for item in value.args],
            "keywords": {
                key: _live_capture_identity(item, seen)
                for key, item in sorted((value.keywords or {}).items())
            },
        }
    if inspect.isfunction(value) or inspect.ismethod(value):
        return _live_callable_binding_identity(value, seen)
    if inspect.isclass(value):
        return {"entrypoint": f"{value.__module__}:{value.__qualname__}"}
    if isinstance(value, ModuleType):
        return {"module": value.__name__}
    return _captured_object_identity(value, seen)


def _normalized_callable_code(value: object) -> object:
    function = value.__func__ if inspect.ismethod(value) else value
    code = getattr(function, "__code__", None)
    return _normalized_code_object(code) if isinstance(code, CodeType) else None


def _normalized_code_object(code: CodeType) -> dict[str, object]:
    return {
        "bytecode": code.co_code.hex(),
        "constants": [_normalized_code_constant(item) for item in code.co_consts],
        "names": code.co_names,
        "varnames": code.co_varnames,
        "freevars": code.co_freevars,
        "cellvars": code.co_cellvars,
        "argcount": code.co_argcount,
        "posonlyargcount": code.co_posonlyargcount,
        "kwonlyargcount": code.co_kwonlyargcount,
        "nlocals": code.co_nlocals,
        "stacksize": code.co_stacksize,
        "flags": code.co_flags,
        "exceptiontable": getattr(code, "co_exceptiontable", b"").hex(),
    }


def _normalized_code_constant(value: object) -> object:
    if isinstance(value, CodeType):
        return _normalized_code_object(value)
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if isinstance(value, tuple):
        return [_normalized_code_constant(item) for item in value]
    if isinstance(value, frozenset):
        normalized = [_normalized_code_constant(item) for item in value]
        return sorted(
            normalized,
            key=lambda item: canonical_digest(item, CanonicalizationPolicy()),
        )
    if isinstance(value, slice):
        return {
            "slice": [
                _normalized_code_constant(value.start),
                _normalized_code_constant(value.stop),
                _normalized_code_constant(value.step),
            ]
        }
    if isinstance(value, range):
        return {"range": [value.start, value.stop, value.step]}
    if value is Ellipsis:
        return {"singleton": "ellipsis"}
    if value is NotImplemented:
        return {"singleton": "not-implemented"}
    if value is None or isinstance(value, (str, bool, int, float, complex)):
        return (
            {"complex": [value.real, value.imag]}
            if isinstance(value, complex)
            else value
        )
    raise ValueError("callable code contains an unsupported constant")


def _callable_capture_identity(
    value: object,
    seen: set[int],
) -> dict[str, object]:
    function = value.__func__ if inspect.ismethod(value) else value
    closure = getattr(function, "__closure__", None) or ()
    freevars = getattr(getattr(function, "__code__", None), "co_freevars", ())
    captured: dict[str, object] = {}
    for name, cell in zip(freevars, closure, strict=True):
        try:
            item = cell.cell_contents
        except ValueError:
            item = None
        captured[name] = _callable_dependency_capture(
            function,
            name,
            item,
            seen,
            shallow_callable=False,
        )
    defaults = getattr(function, "__defaults__", None) or ()
    kwdefaults = getattr(function, "__kwdefaults__", None) or {}
    release_covered = _function_is_release_covered(function)
    referenced_globals = (
        {} if release_covered else inspect.getclosurevars(function).globals
    )
    bound_self = value.__self__ if inspect.ismethod(value) else None
    bound_identity = (
        None
        if bound_self is None
        else _stable_callable_capture(bound_self, seen)
    )
    return {
        "closure": captured,
        "globals": {
            key: _callable_dependency_capture(
                function,
                key,
                item,
                seen,
                shallow_callable=True,
            )
            for key, item in sorted(referenced_globals.items())
        },
        "defaults": [
            _stable_callable_capture(item, seen) for item in defaults
        ],
        "kwdefaults": {
            key: _stable_callable_capture(item, seen)
            for key, item in sorted(kwdefaults.items())
        },
        "bound_self": bound_identity,
    }


def _callable_dependency_capture(
    function: object,
    name: str,
    value: object,
    seen: set[int],
    *,
    shallow_callable: bool,
) -> object:
    if not isinstance(value, ModuleType):
        if shallow_callable and callable(value):
            return _module_attribute_identity(value, seen)
        return _stable_callable_capture(value, seen)
    attributes = _referenced_module_attributes(function, name)
    if not attributes:
        raise ValueError(
            "external module dependency requires explicit referenced attributes"
        )
    captured: dict[str, object] = {}
    for path in attributes:
        current: object = value
        for attribute in path:
            try:
                current = getattr(current, attribute)
            except (AttributeError, TypeError, ValueError) as exc:
                raise ValueError(
                    "external module dependency attribute is unavailable"
                ) from exc
        captured[".".join(path)] = _module_attribute_identity(current, seen)
    return {
        "module": value.__name__,
        "referenced_attributes": captured,
    }


@dataclass
class _BoundedDependencyState:
    active: set[int]
    nodes: int = 0
    max_nodes: int = _BOUNDED_DEPENDENCY_MAX_NODES
    max_depth: int = _BOUNDED_DEPENDENCY_MAX_DEPTH

    def consume(self, depth: int) -> None:
        self.nodes += 1
        if depth > self.max_depth or self.nodes > self.max_nodes:
            raise ValueError("external dependency graph exceeds bounded limits")


def _module_attribute_identity(value: object, seen: set[int]) -> object:
    return _bounded_dependency_identity(
        value,
        _BoundedDependencyState(active=seen),
        depth=0,
    )


def _bounded_dependency_identity(
    value: object,
    state: _BoundedDependencyState,
    *,
    depth: int,
    owner_function: object | None = None,
    dependency_name: str | None = None,
) -> object:
    state.consume(depth)
    if value is Ellipsis:
        result: object = {"singleton": "ellipsis"}
    elif value is NotImplemented:
        result = {"singleton": "not-implemented"}
    elif _is_stable_configuration_value(value):
        result = _bounded_stable_value(value)
    elif isinstance(value, (Mapping, tuple, list, set, frozenset)):
        result = _bounded_collection_identity(value, state, depth=depth + 1)
    elif isinstance(value, ModuleType):
        if owner_function is None or dependency_name is None:
            if (
                value.__name__.partition(".")[0] in sys.stdlib_module_names
                and sys.modules.get(value.__name__) is value
            ):
                result = {
                    "trusted_stdlib_module": value.__name__,
                    "python_version": tuple(sys.version_info[:3]),
                }
            elif (
                value.__name__ in _optimization_dependency_module_names()
                and sys.modules.get(value.__name__) is value
            ):
                result = {"release_module": value.__name__}
            elif (
                provenance := _installed_module_provenance(value)
            ) is not None:
                result = provenance
            else:
                raise ValueError(
                    "external module dependency requires explicit "
                    "referenced attributes"
                )
        else:
            result = _bounded_attribute_dependency(
                value,
                owner_function,
                dependency_name,
                state,
                depth=depth + 1,
            )
    elif isinstance(value, partial):
        result = _bounded_partial_identity(value, state, depth=depth + 1)
    elif inspect.isbuiltin(value):
        result = _bounded_builtin_identity(value, state, depth=depth + 1)
    elif inspect.isfunction(value) or inspect.ismethod(value):
        function = value.__func__ if inspect.ismethod(value) else value
        if _function_is_release_covered(function):
            result = {
                "release_callable": (
                    f"{function.__module__}:{function.__qualname__}"
                )
            }
        else:
            result = _bounded_callable_identity(value, state, depth=depth + 1)
    elif inspect.isclass(value):
        if any(
            node is value for node in _optimization_dependency_scope().nodes
        ):
            result = {
                "release_class": f"{value.__module__}:{value.__qualname__}",
            }
        else:
            result = _bounded_dependency_class_identity(
                value,
                state,
                depth=depth + 1,
            )
    else:
        provider = getattr(value, "runtime_identity", None)
        if callable(provider):
            configuration = provider()
            if not isinstance(configuration, Mapping):
                raise ValueError(
                    "module dependency runtime_identity must be a mapping"
                )
            result = {
                "entrypoint": (
                    f"{type(value).__module__}:{type(value).__qualname__}"
                ),
                "configuration": _stable_configuration_value(configuration),
            }
        else:
            result = _bounded_object_identity(
                value,
                state,
                depth=depth + 1,
                owner_function=owner_function,
                dependency_name=dependency_name,
            )
    return _normalize_bounded_stable_value(result)


def _bounded_collection_identity(
    value: object,
    state: _BoundedDependencyState,
    *,
    depth: int,
) -> object:
    identity = id(value)
    if identity in state.active:
        raise ValueError("external dependency contains a recursive collection")
    state.active.add(identity)
    try:
        if isinstance(value, Mapping):
            entries = [
                {
                    "key": _bounded_dependency_identity(
                        key,
                        state,
                        depth=depth + 1,
                    ),
                    "value": _bounded_dependency_identity(
                        item,
                        state,
                        depth=depth + 1,
                    ),
                }
                for key, item in value.items()
            ]
            return {
                "mapping": sorted(
                    entries,
                    key=lambda item: canonical_digest(
                        item["key"],
                        CanonicalizationPolicy(),
                    ),
                )
            }
        items = [
            _bounded_dependency_identity(item, state, depth=depth + 1)
            for item in value  # type: ignore[union-attr]
        ]
        if isinstance(value, (set, frozenset)):
            items.sort(
                key=lambda item: canonical_digest(
                    item,
                    CanonicalizationPolicy(),
                )
            )
        return {
            "collection_type": f"{type(value).__module__}:{type(value).__qualname__}",
            "items": items,
        }
    finally:
        state.active.remove(identity)


def _bounded_callable_identity(
    value: object,
    state: _BoundedDependencyState,
    *,
    depth: int,
) -> object:
    function = value.__func__ if inspect.ismethod(value) else value
    function_id = id(function)
    entrypoint, source, normalized_code = _cached_callable_static_snapshot(
        function,
        getattr(function, "__code__", None),
    )
    function_module = str(getattr(function, "__module__", "") or "")
    release_covered = _function_is_release_covered(function)
    live_module_member = (
        False if release_covered else _function_is_live_module_member(function)
    )
    trusted_stdlib = (
        function_module.partition(".")[0] in sys.stdlib_module_names
        and live_module_member
    )
    installed_provenance = (
        _installed_module_provenance(sys.modules.get(function_module))
        if live_module_member
        else None
    )
    if function_id in state.active:
        return {"recursive_module_dependency": entrypoint}
    state.active.add(function_id)
    try:
        if trusted_stdlib or installed_provenance is not None:
            dependencies: dict[str, object] = {}
            defaults: list[object] = []
            kwdefaults: dict[str, object] = {}
        elif release_covered:
            dependencies = {
                key: _bounded_release_dependency(
                    function,
                    key,
                    item,
                    state,
                    depth=depth + 1,
                )
                for key, item in sorted(
                    _function_live_dependency_bindings(function).items()
                )
            }
            defaults = [
                _bounded_release_dependency(
                    function,
                    "<default>",
                    item,
                    state,
                    depth=depth + 1,
                )
                for item in (getattr(function, "__defaults__", None) or ())
            ]
            kwdefaults = {
                key: _bounded_release_dependency(
                    function,
                    f"<kwdefault:{key}>",
                    item,
                    state,
                    depth=depth + 1,
                )
                for key, item in sorted(
                    (getattr(function, "__kwdefaults__", None) or {}).items()
                )
            }
        else:
            dependencies = {
                key: _bounded_dependency_identity(
                    item,
                    state,
                    depth=depth + 1,
                    owner_function=function,
                    dependency_name=key,
                )
                for key, item in sorted(
                    _function_live_dependency_bindings(function).items()
                )
            }
            defaults = [
                _bounded_dependency_identity(item, state, depth=depth + 1)
                for item in (getattr(function, "__defaults__", None) or ())
            ]
            kwdefaults = {
                key: _bounded_dependency_identity(item, state, depth=depth + 1)
                for key, item in sorted(
                    (getattr(function, "__kwdefaults__", None) or {}).items()
                )
            }
        receiver = value.__self__ if inspect.ismethod(value) else None
        return {
            "entrypoint": entrypoint,
            "source_digest": canonical_digest(
                _normalize_bounded_stable_value(
                    {
                        "declared_source": source,
                        "normalized_code": normalized_code,
                        "dependencies": dependencies,
                        "defaults": defaults,
                        "kwdefaults": kwdefaults,
                        "trusted_stdlib_python_version": (
                            tuple(sys.version_info[:3])
                            if trusted_stdlib
                            else None
                        ),
                        "installed_module_provenance": installed_provenance,
                        "release_dependency_scope": (
                            function_module if release_covered else None
                        ),
                        "bound_receiver": (
                            None
                            if receiver is None
                            else _bounded_dependency_identity(
                                receiver,
                                state,
                                depth=depth + 1,
                            )
                        ),
                    }
                ),
                CanonicalizationPolicy(),
            ),
        }
    finally:
        state.active.remove(function_id)


def _bounded_release_dependency(
    owner_function: object,
    dependency_name: str,
    value: object,
    state: _BoundedDependencyState,
    *,
    depth: int,
) -> object:
    if _is_identity_measurement_kernel_callable(value):
        candidate = value.__func__ if inspect.ismethod(value) else value
        return {
            "identity_kernel_callable": (
                f"{candidate.__module__}:"
                f"{getattr(candidate, '__qualname__', type(candidate).__qualname__)}"
            ),
        }
    if inspect.isfunction(value) or inspect.ismethod(value):
        function = value.__func__ if inspect.ismethod(value) else value
        if _function_is_release_covered(function):
            return {
                "release_callable": (
                    f"{function.__module__}:{function.__qualname__}"
                ),
            }
    if (
        inspect.isclass(value)
        and any(
            node is value for node in _optimization_dependency_scope().nodes
        )
    ):
        return {
            "release_class": f"{value.__module__}:{value.__qualname__}",
        }
    if (
        isinstance(value, ModuleType)
        and value.__name__ in _optimization_dependency_module_names()
        and sys.modules.get(value.__name__) is value
    ):
        return {"release_module": value.__name__}
    try:
        return _bounded_dependency_identity(
            value,
            state,
            depth=depth + 1,
            owner_function=owner_function,
            dependency_name=dependency_name,
        )
    except ValueError as exc:
        if (
            isinstance(value, ModuleType)
            and "dependency attribute is unavailable" in str(exc)
        ):
            return {
                "platform_module": value.__name__,
                "referenced_attributes": tuple(
                    ".".join(path)
                    for path in _referenced_module_attributes(
                        owner_function,
                        dependency_name,
                    )
                ),
                "availability": "unavailable",
                "python_version": tuple(sys.version_info[:3]),
            }
        raise


def _function_is_live_module_member(function: object) -> bool:
    module = sys.modules.get(str(getattr(function, "__module__", "")))
    if module is None:
        return False
    for item in vars(module).values():
        if item is function:
            return True
        if not inspect.isclass(item):
            continue
        if any(
            member is function
            for _, members in _class_runtime_member_groups(item)
            for _, member in members
        ):
            return True
    return False


def _installed_module_provenance(
    module: object,
) -> dict[str, object] | None:
    if not isinstance(module, ModuleType):
        return None
    source = getattr(module, "__file__", None)
    if not source:
        return None
    path = Path(source)
    try:
        resolved = path.resolve()
        stat = resolved.stat()
    except (OSError, RuntimeError):
        return None
    parts = set(resolved.parts)
    if not (
        "site-packages" in parts
        or "dist-packages" in parts
        or ".venv" in parts
    ):
        return None
    return {
        "installed_module": module.__name__,
        "artifact_digest": _cached_installed_module_digest(
            str(resolved),
            stat.st_mtime_ns,
            stat.st_size,
        ),
    }


@lru_cache(maxsize=512)
def _cached_installed_module_digest(
    path_value: str,
    _mtime_ns: int,
    _size: int,
) -> str:
    return "sha256:" + hashlib.sha256(Path(path_value).read_bytes()).hexdigest()


def _bounded_partial_identity(
    value: partial[object],
    state: _BoundedDependencyState,
    *,
    depth: int,
) -> object:
    identity = id(value)
    if identity in state.active:
        return {"recursive_module_dependency": "functools:partial"}
    state.active.add(identity)
    try:
        return {
            "partial": {
                "callable": _bounded_dependency_identity(
                    value.func,
                    state,
                    depth=depth + 1,
                ),
                "args": [
                    _bounded_dependency_identity(item, state, depth=depth + 1)
                    for item in value.args
                ],
                "keywords": {
                    key: _bounded_dependency_identity(
                        item,
                        state,
                        depth=depth + 1,
                    )
                    for key, item in sorted((value.keywords or {}).items())
                },
            }
        }
    finally:
        state.active.remove(identity)


def _bounded_builtin_identity(
    value: object,
    state: _BoundedDependencyState,
    *,
    depth: int,
) -> object:
    configuration, unsupported = _stable_captured_object_configuration(value)
    receiver = getattr(value, "__self__", None)
    receiver_entrypoint = (
        None
        if receiver is None
        else f"{type(receiver).__module__}:{type(receiver).__qualname__}"
    )
    if receiver is sys.modules and getattr(value, "__name__", "") == "get":
        return {
            "trusted_builtin": "builtins:dict.get",
            "bound_receiver": {
                "runtime_module_registry": "sys.modules",
                "python_version": tuple(sys.version_info[:3]),
                "platform": sys.platform,
            },
        }
    if unsupported and receiver_entrypoint == "re:Pattern":
        return {
            "trusted_builtin": (
                f"{getattr(value, '__module__', type(value).__module__)}:"
                f"{getattr(value, '__qualname__', type(value).__qualname__)}"
            ),
            "bound_receiver": {
                "pattern": receiver.pattern,
                "flags": receiver.flags,
            },
        }
    if (
        unsupported
        and receiver is not None
        and not isinstance(receiver, (ModuleType, type))
    ):
        configuration = {
            "trusted_builtin": (
                f"{getattr(value, '__module__', type(value).__module__)}:"
                f"{getattr(value, '__qualname__', type(value).__qualname__)}"
            ),
            "bound_receiver": _bounded_dependency_identity(
                receiver,
                state,
                depth=depth + 1,
            ),
        }
        unsupported = ()
    if unsupported:
        raise ValueError(
            "external builtin dependency requires explicit runtime_identity: "
            + ", ".join(unsupported)
        )
    return configuration


def _bounded_attribute_dependency(
    value: object,
    owner_function: object,
    dependency_name: str,
    state: _BoundedDependencyState,
    *,
    depth: int,
) -> object:
    attributes = _referenced_module_attributes(owner_function, dependency_name)
    if not attributes:
        raise ValueError(
            "external module dependency requires explicit referenced attributes"
        )
    captured: dict[str, object] = {}
    for path in attributes:
        current = value
        for attribute in path:
            try:
                current = getattr(current, attribute)
            except (AttributeError, TypeError, ValueError) as exc:
                raise ValueError(
                    "external module dependency attribute is unavailable"
                ) from exc
        captured[".".join(path)] = _bounded_dependency_identity(
            current,
            state,
            depth=depth + 1,
        )
    return {
        "entrypoint": f"{type(value).__module__}:{type(value).__qualname__}",
        "referenced_attributes": captured,
    }


def _bounded_object_identity(
    value: object,
    state: _BoundedDependencyState,
    *,
    depth: int,
    owner_function: object | None,
    dependency_name: str | None,
) -> object:
    if owner_function is not None and dependency_name is not None:
        attributes = _referenced_module_attributes(
            owner_function,
            dependency_name,
        )
        if attributes:
            return _bounded_attribute_dependency(
                value,
                owner_function,
                dependency_name,
                state,
                depth=depth + 1,
            )
    identity = id(value)
    entrypoint = f"{type(value).__module__}:{type(value).__qualname__}"
    if identity in state.active:
        return {"recursive_module_dependency": entrypoint}
    if entrypoint in _TRUSTED_STDLIB_RUNTIME_OBJECTS:
        return {
            "trusted_stdlib_runtime_object": entrypoint,
            "stable_representation": (
                repr(value) if entrypoint == "datetime:timezone" else None
            ),
        }
    state.active.add(identity)
    try:
        configuration, unsupported = _stable_captured_object_configuration(value)
        state_items = dict(_object_state_items(value)[0])
        unresolved: list[str] = []
        expanded = dict(configuration) if isinstance(configuration, Mapping) else {}
        trusted_stdlib_object = (
            type(value).__module__.partition(".")[0] in sys.stdlib_module_names
            and entrypoint not in _STATEFUL_STDLIB_RUNTIME_OBJECTS
        )
        for name in unsupported:
            item = state_items.get(name)
            if item is None or not (
                isinstance(item, (ModuleType, partial))
                or inspect.isfunction(item)
                or inspect.ismethod(item)
                or inspect.isbuiltin(item)
                or inspect.isclass(item)
                or callable(item)
                or callable(getattr(item, "runtime_identity", None))
            ):
                if trusted_stdlib_object:
                    expanded[name] = {
                        "trusted_stdlib_opaque_field": (
                            None
                            if item is None
                            else (
                                f"{type(item).__module__}:"
                                f"{type(item).__qualname__}"
                            )
                        ),
                        "python_version": tuple(sys.version_info[:3]),
                    }
                else:
                    unresolved.append(name)
                continue
            expanded[name] = _bounded_dependency_identity(
                item,
                state,
                depth=depth + 1,
            )
        if unresolved:
            raise ValueError(
                f"external object dependency {entrypoint!r} requires explicit "
                f"runtime_identity: {', '.join(unresolved)}"
            )
        return {
            "entrypoint": entrypoint,
            "implementation": _bounded_dependency_class_identity(
                type(value),
                state,
                depth=depth + 1,
            ),
            "configuration": expanded,
        }
    finally:
        state.active.remove(identity)


def _bounded_stable_value(value: object) -> object:
    return _normalize_bounded_stable_value(_stable_configuration_value(value))


def _normalize_bounded_stable_value(value: object) -> object:
    if isinstance(value, float) and not math.isfinite(value):
        return {"non_finite_float": repr(value)}
    if isinstance(value, Mapping):
        return {
            str(key): _normalize_bounded_stable_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_normalize_bounded_stable_value(item) for item in value]
    return value


def _bounded_dependency_class_identity(
    value: type[object],
    state: _BoundedDependencyState,
    *,
    depth: int,
) -> object:
    entrypoint = f"{value.__module__}:{value.__qualname__}"
    root_module = value.__module__.partition(".")[0]
    if root_module in sys.stdlib_module_names:
        return {
            "trusted_stdlib_class": entrypoint,
            "python_version": tuple(sys.version_info[:3]),
        }
    module = sys.modules.get(value.__module__)
    if (
        module is not None
        and (provenance := _installed_module_provenance(module)) is not None
    ):
        return {
            "installed_class": entrypoint,
            "module_provenance": provenance,
        }
    if value is BaseModel:
        return {"pydantic_base_model": "pydantic:BaseModel"}
    if issubclass(value, BaseModel):
        return {
            "pydantic_model": entrypoint,
            "schema": value.model_json_schema(),
        }
    identity = id(value)
    if identity in state.active:
        return {"recursive_module_dependency": entrypoint}
    state.active.add(identity)
    try:
        members = {
            base_entrypoint: {
                name: _bounded_callable_identity(
                    member,
                    state,
                    depth=depth + 1,
                )
                for name, member in base_members
            }
            for base_entrypoint, base_members in _class_runtime_member_groups(value)
        }
        configuration: dict[str, object] = {}
        for base in reversed(value.__mro__):
            if base is object:
                continue
            prefix = f"{base.__module__}:{base.__qualname__}"
            for name, item in sorted(vars(base).items()):
                if (
                    name in _CLASS_CONFIGURATION_IGNORED_FIELDS
                    or (name.startswith("__") and name.endswith("__"))
                    or name.startswith("__dataclass_")
                    or name.startswith("_abc_")
                    or _runtime_member_callable(item) is not None
                    or inspect.isdatadescriptor(item)
                ):
                    continue
                key = f"{prefix}.{name}"
                if _is_stable_configuration_value(item):
                    configuration[key] = _bounded_stable_value(item)
                elif isinstance(item, ModuleType):
                    configuration[key] = {"module": item.__name__}
                elif (
                    isinstance(item, partial)
                    or inspect.isfunction(item)
                    or inspect.ismethod(item)
                    or inspect.isclass(item)
                ):
                    configuration[key] = _bounded_dependency_identity(
                        item,
                        state,
                        depth=depth + 1,
                    )
                else:
                    configuration[key] = {
                        "runtime_input_type": (
                            f"{type(item).__module__}:{type(item).__qualname__}"
                        )
                    }
        return {
            "class": entrypoint,
            "source_digest": canonical_digest(
                _normalize_bounded_stable_value(
                    {
                        "declared_source": _cached_class_source(value),
                        "members": members,
                        "configuration": configuration,
                    }
                ),
                CanonicalizationPolicy(),
            ),
        }
    finally:
        state.active.remove(identity)


def _function_live_dependency_bindings(function: object) -> dict[str, object]:
    candidate = function.__func__ if inspect.ismethod(function) else function
    code = getattr(candidate, "__code__", None)
    if not isinstance(code, CodeType):
        return {}
    namespace = getattr(candidate, "__globals__", {})
    bindings = {
        name: namespace[name]
        for name in _cached_function_global_names(code)
        if name in namespace
    }
    for name, cell in zip(
        code.co_freevars,
        getattr(candidate, "__closure__", None) or (),
        strict=True,
    ):
        try:
            bindings[name] = cell.cell_contents
        except ValueError:
            continue
    return bindings


@lru_cache(maxsize=4096)
def _cached_function_global_names(code: CodeType) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                str(instruction.argval)
                for nested in _cached_nested_code_objects(code)
                for instruction in dis.get_instructions(nested)
                if instruction.opname in {"LOAD_GLOBAL", "LOAD_NAME"}
            }
        )
    )


@lru_cache(maxsize=4096)
def _cached_nested_code_objects(code: CodeType) -> tuple[CodeType, ...]:
    nested = [code]
    for item in code.co_consts:
        if isinstance(item, CodeType):
            nested.extend(_cached_nested_code_objects(item))
    return tuple(nested)


def _referenced_module_attributes(
    function: object,
    dependency_name: str,
) -> tuple[tuple[str, ...], ...]:
    code = getattr(function, "__code__", None)
    if not isinstance(code, CodeType):
        return ()
    return _cached_referenced_module_attributes(code, dependency_name)


@lru_cache(maxsize=4096)
def _cached_referenced_module_attributes(
    code: CodeType,
    dependency_name: str,
) -> tuple[tuple[str, ...], ...]:
    paths: set[tuple[str, ...]] = set()
    for nested in _cached_nested_code_objects(code):
        instructions = tuple(dis.get_instructions(nested))
        for index, instruction in enumerate(instructions):
            if (
                instruction.opname
                not in {"LOAD_DEREF", "LOAD_GLOBAL", "LOAD_NAME"}
                or instruction.argval != dependency_name
            ):
                continue
            path: list[str] = []
            for following in instructions[index + 1 :]:
                if following.opname in {"CACHE", "EXTENDED_ARG"}:
                    continue
                if following.opname not in {"LOAD_ATTR", "LOAD_METHOD"}:
                    break
                path.append(str(following.argval))
                paths.add(tuple(path))
    maximal = {
        path
        for path in paths
        if not any(
            len(other) > len(path) and other[: len(path)] == path
            for other in paths
        )
    }
    return tuple(sorted(maximal))


def _function_is_release_covered(function: object) -> bool:
    covered_function_ids = getattr(
        _LIVE_PACKAGE_SNAPSHOT_LOCAL,
        "generation_covered_function_ids",
        None,
    )
    if covered_function_ids is not None:
        return id(function) in covered_function_ids
    covered_function_ids = getattr(
        _LIVE_PACKAGE_SNAPSHOT_LOCAL,
        "covered_function_ids",
        None,
    )
    if covered_function_ids is not None:
        return id(function) in covered_function_ids
    return id(function) in _optimization_dependency_scope().covered_function_ids


def _stable_callable_capture(value: object, seen: set[int]) -> object:
    if value is None or _is_stable_configuration_value(value):
        return _stable_configuration_value(value)
    if (
        inspect.isclass(value)
        and issubclass(value, BaseModel)
    ):
        return {
            "pydantic_model": f"{value.__module__}:{value.__qualname__}",
            "schema": value.model_json_schema(),
        }
    if isinstance(value, partial) or inspect.isfunction(value) or inspect.ismethod(
        value
    ) or inspect.isclass(value):
        return _component_implementation_identity(value, seen)
    wrapped = getattr(value, "__wrapped__", None)
    if callable(value) and callable(wrapped):
        parameters = getattr(value, "cache_parameters", None)
        return {
            "wrapped": _component_implementation_identity(wrapped, seen),
            "configuration": (
                _stable_configuration_value(parameters())
                if callable(parameters)
                else {}
            ),
        }
    if isinstance(value, ModuleType):
        return {"module": value.__name__}
    if getattr(type(value), "__module__", "") == "typing":
        return {"typing": repr(value)}
    return _captured_object_identity(value, seen)


def _captured_object_identity(value: object, seen: set[int]) -> object:
    identity = id(value)
    entrypoint = f"{type(value).__module__}:{type(value).__qualname__}"
    if identity in seen:
        return {"recursive_reference": entrypoint}
    seen.add(identity)
    try:
        return _captured_object_identity_once(value, seen)
    finally:
        seen.remove(identity)


def _captured_object_identity_once(value: object, seen: set[int]) -> object:
    if (
        type(value).__module__ == "re"
        and type(value).__qualname__ == "Pattern"
    ):
        return {
            "identity_contract": "trusted-regex-pattern.v1",
            "pattern": value.pattern,
            "flags": value.flags,
        }
    implementation = _component_implementation_identity(type(value), seen)
    provider = getattr(value, "runtime_identity", None)
    internal = getattr(type(value), "__module__", "").startswith("ai_sdlc")
    if callable(provider) and not internal:
        configuration = provider()
        if not isinstance(configuration, Mapping):
            raise ValueError("captured runtime identity must be a mapping")
        return {
            "identity_contract": "explicit-runtime-identity.v1",
            "implementation": implementation,
            "configuration": _stable_configuration_value(configuration),
        }
    configuration, unsupported = _stable_captured_object_configuration(value)
    if unsupported:
        if not internal:
            raise ValueError(
                "captured object requires explicit runtime_identity: "
                + ", ".join(unsupported)
            )
        configuration = {
            "stable": configuration,
            "internal_dependencies": {
                key: _component_implementation_identity(
                    type(vars(value)[key]),
                    seen,
                )
                for key in unsupported
            },
        }
    return {
        "identity_contract": "inferred-runtime-identity.v1",
        "implementation": implementation,
        "configuration": configuration,
    }


def _stable_captured_object_configuration(
    value: object,
) -> tuple[object, tuple[str, ...]]:
    """仅绑定闭包对象的稳定数据，禁止沿可调用对象再次展开依赖图。"""
    value_type = type(value)
    if inspect.isbuiltin(value):
        configured: dict[str, object] = {
            "trusted_builtin": (
                f"{getattr(value, '__module__', value_type.__module__)}:"
                f"{getattr(value, '__qualname__', value_type.__qualname__)}"
            )
        }
        receiver = getattr(value, "__self__", None)
        if (
            receiver is not None
            and not isinstance(receiver, (ModuleType, type))
        ):
            if _is_stable_configuration_value(receiver):
                configured["bound_receiver"] = _stable_configuration_value(
                    receiver
                )
            else:
                provider = getattr(receiver, "runtime_identity", None)
                if not callable(provider):
                    return configured, ("bound_receiver.<opaque-native-state>",)
                receiver_config = provider()
                if not isinstance(receiver_config, Mapping):
                    return configured, ("bound_receiver.runtime_identity",)
                configured["bound_receiver"] = _stable_configuration_value(
                    receiver_config
                )
        return configured, ()
    descriptor_owner = getattr(value, "__objclass__", None)
    descriptor_name = getattr(value, "__name__", None)
    if (
        inspect.ismethoddescriptor(value)
        and isinstance(descriptor_owner, type)
    ) or (
        callable(value)
        and value_type.__module__ == "builtins"
        and isinstance(descriptor_name, str)
        and isinstance(descriptor_owner, type)
    ):
        return {
            "trusted_builtin_descriptor": (
                f"{descriptor_owner.__module__}:"
                f"{descriptor_owner.__qualname__}.{descriptor_name}"
            )
        }, ()
    if (
        value_type.__module__ == "operator"
        and value_type.__qualname__ in {"attrgetter", "itemgetter"}
    ):
        reduced = value.__reduce__()  # type: ignore[attr-defined]
        if (
            isinstance(reduced, tuple)
            and len(reduced) == 2
            and isinstance(reduced[1], tuple)
            and _is_stable_configuration_value(reduced[1])
        ):
            return {
                "trusted_stdlib_callable": (
                    f"{value_type.__module__}:{value_type.__qualname__}"
                ),
                "arguments": _stable_configuration_value(reduced[1]),
            }, ()
    if (
        value_type.__module__.startswith("pydantic_core.")
        and value_type.__qualname__ == "PydanticUndefinedType"
        and repr(value) == "PydanticUndefined"
    ):
        return {"trusted_singleton": "pydantic:PydanticUndefined"}, ()
    if isinstance(value, BaseModel):
        return _stable_configuration_value(value.model_dump(mode="json")), ()
    if is_dataclass(value) and not isinstance(value, type):
        configured: dict[str, object] = {}
        unsupported: list[str] = []
        for item in fields(value):  # type: ignore[arg-type]
            field_value = getattr(value, item.name)
            if item.name in _TRANSIENT_RUNTIME_FIELDS:
                continue
            if _is_stable_configuration_value(field_value):
                configured[item.name] = _stable_configuration_value(field_value)
            else:
                unsupported.append(item.name)
        return configured, tuple(unsupported)
    configured = {}
    unsupported = []
    state_items, enumerable = _object_state_items(value)
    for key, item in state_items:
        if key in _TRANSIENT_RUNTIME_FIELDS:
            continue
        if _is_stable_configuration_value(item):
            configured[key] = _stable_configuration_value(item)
        else:
            unsupported.append(key)
    if _has_opaque_native_state(value):
        unsupported.append("<opaque-native-state>")
    if not enumerable:
        unsupported.append("<opaque-state>")
    return configured, tuple(unsupported)


def _has_opaque_native_state(value: object) -> bool:
    if type(value) is SimpleNamespace:
        return False
    for implementation in type(value).__mro__:
        if implementation is object:
            continue
        try:
            source = inspect.getsourcefile(implementation)
        except (OSError, TypeError):
            source = None
        if source is None:
            return True
    return False


def _object_state_items(value: object) -> tuple[tuple[tuple[str, object], ...], bool]:
    configured: dict[str, object] = {}
    enumerable = False
    try:
        namespace = vars(value)
    except (AttributeError, TypeError, ValueError):
        namespace = None
    if isinstance(namespace, dict):
        enumerable = True
        configured.update(namespace)
    for implementation in type(value).__mro__:
        namespace = vars(implementation)
        if "__slots__" not in namespace:
            continue
        slots = namespace["__slots__"]
        if isinstance(slots, str):
            slots = (slots,)
        if not isinstance(slots, (tuple, list)):
            continue
        enumerable = True
        for slot in slots:
            if not isinstance(slot, str) or slot in {"__dict__", "__weakref__"}:
                continue
            name = (
                f"_{implementation.__name__.lstrip('_')}{slot}"
                if slot.startswith("__") and not slot.endswith("__")
                else slot
            )
            try:
                configured[name] = getattr(value, name)
            except (AttributeError, TypeError, ValueError):
                continue
    return tuple(sorted(configured.items())), enumerable


def _stable_class_configuration(implementation: type[object]) -> dict[str, object]:
    if implementation is BaseModel:
        return {"pydantic_base_model": "pydantic:BaseModel"}
    if issubclass(implementation, BaseModel):
        return {
            "pydantic_model_schema": implementation.model_json_schema(),
        }
    configured: dict[str, object] = {}
    for base in reversed(implementation.__mro__):
        if base is object:
            continue
        prefix = f"{base.__module__}:{base.__qualname__}"
        for name, item in sorted(vars(base).items()):
            if (
                name in _CLASS_CONFIGURATION_IGNORED_FIELDS
                or (name.startswith("__") and name.endswith("__"))
                or name.startswith("__dataclass_")
                or name.startswith("_abc_")
                or _runtime_member_callable(item) is not None
                or inspect.isdatadescriptor(item)
            ):
                continue
            key = f"{prefix}.{name}"
            try:
                stable = _is_stable_configuration_value(item)
            except Exception:
                stable = False
            if stable:
                configured[key] = _stable_configuration_value(item)
                continue
            nested, unsupported = _class_configuration_value(item)
            if unsupported:
                raise ValueError(
                    f"class field {key!r} requires explicit runtime_identity"
                )
            configured[key] = {
                "entrypoint": f"{type(item).__module__}:{type(item).__qualname__}",
                "configuration": nested,
            }
    return configured


def _class_configuration_value(
    value: object,
) -> tuple[object, tuple[str, ...]]:
    if isinstance(value, (staticmethod, classmethod)):
        return _stable_captured_object_configuration(value.__func__)
    if _is_stable_configuration_value(value):
        return _stable_configuration_value(value), ()
    if isinstance(value, type):
        configured: dict[str, object] = {
            "class": f"{value.__module__}:{value.__qualname__}",
        }
        if issubclass(value, BaseModel):
            configured["pydantic_model_schema"] = value.model_json_schema()
        return configured, ()
    if isinstance(value, Mapping):
        configured_mapping: dict[str, object] = {}
        unsupported: list[str] = []
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            if not isinstance(key, str):
                unsupported.append(f"[{key!r}]")
                continue
            nested, nested_unsupported = _class_configuration_value(item)
            configured_mapping[key] = nested
            unsupported.extend(
                f"{key}.{name}" for name in nested_unsupported
            )
        return configured_mapping, tuple(unsupported)
    if isinstance(value, (tuple, list)):
        configured_items: list[object] = []
        unsupported = []
        for index, item in enumerate(value):
            nested, nested_unsupported = _class_configuration_value(item)
            configured_items.append(nested)
            unsupported.extend(
                f"{index}.{name}" for name in nested_unsupported
            )
        return configured_items, tuple(unsupported)
    return _stable_captured_object_configuration(value)


def _runtime_member_callable(value: object) -> object | None:
    if isinstance(value, (staticmethod, classmethod)):
        value = value.__func__
    elif isinstance(value, property):
        value = value.fget
    return value if inspect.isfunction(value) or inspect.ismethod(value) else None


def _class_runtime_member_groups(
    implementation: type[object],
) -> tuple[tuple[str, tuple[tuple[str, object], ...]], ...]:
    groups = []
    for base in reversed(implementation.__mro__):
        if base is object:
            continue
        members = tuple(
            (name, member)
            for name, raw in sorted(vars(base).items())
            if (member := _runtime_member_callable(raw)) is not None
        )
        if members:
            groups.append(
                (
                    f"{base.__module__}:{base.__qualname__}",
                    members,
                )
            )
    return tuple(groups)


def _release_class_runtime_member_groups(
    implementation: type[object],
) -> tuple[tuple[str, tuple[tuple[str, object], ...]], ...]:
    groups = []
    for base_entrypoint, members in _class_runtime_member_groups(implementation):
        base_module = base_entrypoint.split(":", maxsplit=1)[0]
        if (
            base_module != implementation.__module__
            and not base_module.startswith("ai_sdlc.")
        ):
            continue
        filtered = tuple(
            (name, member)
            for name, member in members
            if (
                (
                    str(getattr(member, "__module__", "") or "") == base_module
                    or str(getattr(member, "__module__", "") or "").startswith(
                        "ai_sdlc."
                    )
                )
                and (
                    not base_module.startswith("ai_sdlc.")
                    or _function_has_release_source(member)
                )
            )
        )
        if filtered:
            groups.append((base_entrypoint, filtered))
    return tuple(groups)


def _generated_class_runtime_member_groups(
    implementation: type[object],
) -> tuple[tuple[str, tuple[tuple[str, object], ...]], ...]:
    groups = []
    for base_entrypoint, members in _class_runtime_member_groups(implementation):
        base_module = base_entrypoint.split(":", maxsplit=1)[0]
        if not base_module.startswith("ai_sdlc."):
            continue
        generated = tuple(
            (name, member)
            for name, member in members
            if (
                str(getattr(member, "__module__", "") or "").startswith(
                    "ai_sdlc."
                )
                and not _function_has_release_source(member)
            )
        )
        if generated:
            groups.append((base_entrypoint, generated))
    return tuple(groups)


def _generated_class_member_fast_token(
    implementation: type[object],
) -> tuple[object, ...]:
    return tuple(
        (
            base_entrypoint,
            tuple(
                (
                    name,
                    id(member),
                    id(getattr(member, "__code__", None)),
                    _stable_cache_token(getattr(member, "__defaults__", None) or ()),
                    _stable_cache_token(
                        getattr(member, "__kwdefaults__", None) or {}
                    ),
                )
                for name, member in members
            ),
        )
        for base_entrypoint, members in _generated_class_runtime_member_groups(
            implementation
        )
    )


def _generated_class_member_identity(
    implementation: type[object],
) -> dict[str, object]:
    return {
        base_entrypoint: {
            name: {
                "static": _cached_callable_static_snapshot(
                    member,
                    getattr(member, "__code__", None),
                ),
                "defaults": [
                    _release_node_value_identity(item, set())
                    for item in (getattr(member, "__defaults__", None) or ())
                ],
                "kwdefaults": {
                    key: _release_node_value_identity(item, set())
                    for key, item in sorted(
                        (getattr(member, "__kwdefaults__", None) or {}).items()
                    )
                },
                "python_version": tuple(sys.version_info[:3]),
            }
            for name, member in members
        }
        for base_entrypoint, members in _generated_class_runtime_member_groups(
            implementation
        )
    }


def _function_has_release_source(function: object) -> bool:
    code = getattr(function, "__code__", None)
    if not isinstance(code, CodeType):
        return False
    return _cached_source_path_is_release_owned(code.co_filename)


@lru_cache(maxsize=4096)
def _cached_source_path_is_release_owned(path_value: str) -> bool:
    if path_value.startswith("<"):
        return False
    try:
        return Path(path_value).resolve().is_relative_to(
            _optimization_dependency_source_root().resolve()
        )
    except (OSError, RuntimeError):
        return False


def _stable_component_configuration(value: object | None) -> object:
    if value is None:
        return None
    if isinstance(value, Path):
        return {"kind": "local-path"}
    if isinstance(value, BaseModel):
        return _stable_configuration_value(value.model_dump(mode="json"))
    if is_dataclass(value) and not isinstance(value, type):
        return _dataclass_configuration(value)
    if (
        isinstance(value, partial)
        or inspect.isfunction(value)
        or inspect.ismethod(value)
        or inspect.isclass(value)
    ):
        return {}
    configured: dict[str, object] = {}
    state_items, enumerable = _object_state_items(value)
    if not enumerable:
        raise ValueError(
            "opaque component requires explicit runtime_identity"
        )
    for key, item in state_items:
        if key in _TRANSIENT_RUNTIME_FIELDS:
            continue
        if _is_stable_configuration_value(item):
            stable = _stable_configuration_value(item)
            configured[key] = (
                {
                    "private_value_digest": canonical_digest(
                        stable,
                        CanonicalizationPolicy(),
                    )
                }
                if key.startswith("_")
                else stable
            )
        elif callable(item) or key in _TRANSITIVE_BEHAVIOR_FIELDS:
            configured[key] = component_runtime_identity(item)
        else:
            raise ValueError(
                f"component field {key!r} requires explicit runtime_identity"
            )
    known = {
        name: getattr(value, name)
        for name in (
            "policy_digest",
            "manifest_digest",
            "snapshot_digest",
            "registry_digest",
            "implementation_digest",
            "project_id",
        )
        if hasattr(value, name) and _is_stable_configuration_value(getattr(value, name))
    }
    return _stable_configuration_value({**configured, **known})


def _is_stable_configuration_value(value: object) -> bool:
    return _is_stable_configuration_value_inner(value, set())


def _is_stable_configuration_value_inner(
    value: object,
    seen: set[int],
) -> bool:
    if value is None or isinstance(
        value,
        (str, bool, int, float, bytes, Path, Enum, BaseModel),
    ):
        return True
    if isinstance(value, (Mapping, tuple, list, set, frozenset)) or (
        is_dataclass(value) and not isinstance(value, type)
    ):
        identity = id(value)
        if identity in seen:
            raise ValueError("stable configuration contains a recursive reference")
        seen.add(identity)
        try:
            if isinstance(value, Mapping):
                return all(
                    isinstance(key, str)
                    and _is_stable_configuration_value_inner(item, seen)
                    for key, item in value.items()
                )
            if isinstance(value, (tuple, list, set, frozenset)):
                return all(
                    _is_stable_configuration_value_inner(item, seen)
                    for item in value
                )
            return all(
                item.name in _TRANSIENT_RUNTIME_FIELDS
                or _is_stable_configuration_value_inner(
                    getattr(value, item.name),
                    seen,
                )
                for item in fields(value)  # type: ignore[arg-type]
            )
        finally:
            seen.remove(identity)
    return False


def _stable_configuration_value(value: object) -> object:
    return _stable_configuration_value_inner(value, set())


def _stable_configuration_value_inner(value: object, seen: set[int]) -> object:
    if isinstance(value, Enum):
        return {
            "enum": f"{type(value).__module__}:{type(value).__qualname__}",
            "value": _stable_configuration_value_inner(value.value, seen),
        }
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if isinstance(value, Path):
        return {"kind": "local-path"}
    if isinstance(value, BaseModel):
        return _stable_configuration_value_inner(value.model_dump(mode="json"), seen)
    if is_dataclass(value) and not isinstance(value, type):
        return _dataclass_configuration(value)
    if callable(value):
        return component_implementation_identity(value)
    if isinstance(value, (Mapping, tuple, list, set, frozenset)):
        identity = id(value)
        if identity in seen:
            raise ValueError("stable configuration contains a recursive reference")
        seen.add(identity)
        try:
            if isinstance(value, Mapping):
                return {
                    str(key): _stable_configuration_value_inner(item, seen)
                    for key, item in sorted(
                        value.items(),
                        key=lambda item: str(item[0]),
                    )
                }
            normalized = [
                _stable_configuration_value_inner(item, seen) for item in value
            ]
            if isinstance(value, (set, frozenset)):
                normalized.sort(
                    key=lambda item: canonical_digest(
                        item,
                        CanonicalizationPolicy(),
                    )
                )
            return normalized
        finally:
            seen.remove(identity)
    return value


def _dataclass_configuration(value: object) -> dict[str, object]:
    configured: dict[str, object] = {}
    for item in fields(value):  # type: ignore[arg-type]
        if item.name in _TRANSIENT_RUNTIME_FIELDS:
            continue
        field_value = getattr(value, item.name)
        if _is_stable_configuration_value(field_value):
            configured[item.name] = _stable_configuration_value(field_value)
        elif callable(field_value) or item.name in _TRANSITIVE_BEHAVIOR_FIELDS:
            configured[item.name] = component_runtime_identity(field_value)
    return configured


def _validate_invocation(
    contract: EvaluatorContract,
    candidate: OptimizationCandidate,
    context: EvaluationContext,
) -> None:
    if contract.candidate_schema_version != candidate.schema_version:
        raise ValueError("candidate schema is incompatible with evaluator")
    if candidate.candidate_domain not in contract.compatible_candidate_domains:
        raise ValueError("candidate domain is not authorized by evaluator")
    if context.partition not in contract.allowed_partitions:
        raise ValueError("dataset partition is not authorized by evaluator")
    if (
        contract.independence_level == "independent_binding"
        and context.evaluation_binding_id == candidate.generator_identity
    ):
        raise ValueError(
            "semantic evaluator requires an independent evaluation binding"
        )
    if (
        contract.independence_level == "independent_binding"
        and context.evaluation_provider_id == candidate.generator_provider_id
    ):
        raise ValueError("semantic evaluator cannot reuse the generator provider")
    if not set(contract.provider_constraints) <= set(context.provider_capabilities):
        raise ValueError("evaluation provider constraints are not satisfied")


def _validate_report(
    contract: EvaluatorContract,
    candidate: OptimizationCandidate,
    context: EvaluationContext,
    report: OptimizationEvaluationReport,
) -> None:
    lineage = (
        contract.report_schema_version == report.schema_version,
        report.candidate_digest == candidate.candidate_digest,
        report.evaluator_kind == contract.evaluator_kind,
        report.evaluator_version == contract.evaluator_version,
        report.evaluator_contract_digest == contract.contract_digest,
        report.dataset_digest == context.dataset_digest,
        report.partition == context.partition,
        report.evaluation_binding_id == context.evaluation_binding_id,
        report.hypothesis_family_digest == context.hypothesis_family_digest,
        report.statistics_policy_digest == context.statistics_policy_digest,
        report.statistical_alpha == context.statistical_alpha,
        report.domain_contract_digest == candidate.domain_contract_digest,
        report.domain_adapter_id == candidate.domain_adapter_id,
        report.domain_adapter_version == candidate.domain_adapter_version,
        report.domain_adapter_digest == candidate.domain_adapter_digest,
        report.domain_registry_digest == candidate.domain_registry_digest,
    )
    if not all(lineage):
        raise ValueError("evaluator report lineage is invalid")


def _evaluation_hypothesis_family(
    contract: EvaluatorContract,
    candidate: OptimizationCandidate,
    context: EvaluationContext,
) -> str:
    return canonical_digest(
        {
            "candidate_domain": candidate.candidate_domain,
            "target_stratum_ids": candidate.target_stratum_ids,
            "dataset_digest": context.dataset_digest,
            "partition": context.partition,
            "evaluator_kind": contract.evaluator_kind,
        },
        CanonicalizationPolicy(),
    )


def fixed_holdout_evaluator_contract(
    candidate_domains: tuple[CandidateDomain, ...],
) -> EvaluatorContract:
    return EvaluatorContract(
        evaluator_kind="fixed-holdout",
        evaluator_version="1.0.0",
        candidate_schema_version="optimization-candidate.v1",
        report_schema_version="optimization-evaluation-report.v1",
        allowed_partitions=("holdout",),
        compatible_candidate_domains=candidate_domains,
        independence_level="deterministic",
        deterministic=True,
        provider_constraints=("local-read-only",),
    )
