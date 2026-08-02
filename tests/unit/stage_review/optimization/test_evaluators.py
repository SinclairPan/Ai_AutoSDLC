from __future__ import annotations

import os
import re
import subprocess
import sys
import typing as typing_module
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from functools import partial
from operator import attrgetter
from pathlib import Path
from random import Random
from types import ModuleType, SimpleNamespace
from typing import ForwardRef, Literal, NewType

import pytest
from typing_extensions import ParamSpec, TypeAliasType, TypeVar, TypeVarTuple, Unpack

from ai_sdlc.core import git_filter_safety as git_filter_safety_module
from ai_sdlc.core.stage_review.optimization import evaluators as evaluators_module
from ai_sdlc.core.stage_review.optimization.candidate_domain_defaults import (
    default_candidate_domain_registry,
)
from ai_sdlc.core.stage_review.optimization.evaluators import (
    EvaluationContext,
    EvaluatorContract,
    OptimizationEvaluatorRegistry,
    component_implementation_identity,
    component_runtime_digest,
)
from ai_sdlc.core.stage_review.optimization.models import (
    OptimizationCandidate,
    OptimizationEvaluationReport,
    OptimizationPatchOperation,
    OptimizationStatisticalSample,
)
from ai_sdlc.core.stage_review.optimization.statistics import (
    _binary_improvement_statistics as binary_improvement_statistics,
)
from ai_sdlc.core.stage_review.optimization.statistics import (
    baseline_statistics_policy,
)

_DETACHED_RUNTIME_THRESHOLD = 1
_FAST_EXTERNAL_THRESHOLD = 1
_DETACHED_HELPERS = ModuleType("plugin_helpers")


def _detached_helper2(value: int) -> bool:
    return value >= _DETACHED_HELPERS.threshold  # type: ignore[attr-defined]


def _fast_external_helper(value: int) -> bool:
    return value >= _FAST_EXTERNAL_THRESHOLD


def test_all_candidate_domains_share_one_schema_and_enforce_patch_authority() -> None:
    domains_and_paths = (
        ("role_profile", "role_profiles.compositions"),
        ("selection", "selection_policy.capability_requirement_rules"),
        ("binding", "binding_policy.require_independent_blocking_slots"),
        ("budget", "budget_policy.high.hard_review_passes"),
        ("capability_mapping", "capability_mapping.registry_digest"),
    )

    candidates = tuple(
        _candidate(domain, path, suffix=str(index))
        for index, (domain, path) in enumerate(domains_and_paths, start=1)
    )
    domains = default_candidate_domain_registry()

    assert {type(item) for item in candidates} == {OptimizationCandidate}
    for candidate in candidates:
        domains.require_candidate(candidate)
    with pytest.raises(ValueError, match="not authorized"):
        domains.require_candidate(
            _candidate(
                "budget",
                "optimization_constitution.familywise_alpha",
                suffix="forbidden",
            )
        )


def test_new_evaluator_contract_and_adapter_register_without_core_branch() -> None:
    registry = _registry()
    adapter = _Adapter()
    registry.register(_contract("custom-risk-evaluator"), adapter)

    report = registry.evaluate(
        evaluator_kind="custom-risk-evaluator",
        candidate=_candidate(
            "selection",
            "selection_policy.capability_requirement_rules",
            suffix="custom",
        ),
        context=_context(),
    )

    assert report.recommendation == "finalist_eligible"
    assert report.evaluator_kind == "custom-risk-evaluator"
    assert adapter.calls == 1


def test_registry_manifest_binds_the_actual_adapter_implementation() -> None:
    first = _registry()
    second = _registry()
    contract = _contract("custom-risk-evaluator")
    first.register(contract, _Adapter())
    second.register(contract, _AlternateAdapter())

    assert first.registry_digest == second.registry_digest
    assert first.implementation_digest != second.implementation_digest


def test_callable_identity_binds_closure_values() -> None:
    def with_behavior(value: str):
        def execute() -> str:
            return value

        return execute

    assert component_implementation_identity(
        with_behavior("first")
    ) != component_implementation_identity(with_behavior("second"))


def test_callable_identity_binds_bound_instance_configuration() -> None:
    class Policy:
        def __init__(self, threshold: int) -> None:
            self.threshold = threshold

        def decide(self, value: int) -> bool:
            return value >= self.threshold

    assert Policy(1).decide(50) is True
    assert Policy(99).decide(50) is False
    assert component_implementation_identity(
        Policy(1).decide
    ) != component_implementation_identity(Policy(99).decide)


def test_callable_identity_binds_builtin_bound_receiver() -> None:
    def with_lookup(mapping: dict[str, int]):
        lookup = mapping.get

        def execute() -> int | None:
            return lookup("value")

        return execute

    def with_append(items: list[int]):
        append = items.append

        def execute() -> tuple[int, ...]:
            append(3)
            return tuple(items)

        return execute

    assert component_implementation_identity(
        with_lookup({"value": 1})
    ) != component_implementation_identity(with_lookup({"value": 2}))
    assert component_implementation_identity(
        with_append([1])
    ) != component_implementation_identity(with_append([2]))


def test_callable_identity_rejects_opaque_native_builtin_receiver() -> None:
    def with_random(seed: int):
        draw = Random(seed).random

        def execute() -> float:
            return draw()

        return execute

    assert with_random(1)() != with_random(2)()
    with pytest.raises(
        ValueError,
        match="explicit runtime_identity",
    ):
        component_implementation_identity(with_random(1))


def test_callable_identity_binds_slots_and_live_class_configuration() -> None:
    class SlottedPolicy:
        __slots__ = ("threshold",)

        def __init__(self, threshold: int) -> None:
            self.threshold = threshold

        def decide(self, value: int) -> bool:
            return value >= self.threshold

    class ClassConfiguredPolicy:
        threshold = 1

        def decide(self, value: int) -> bool:
            return value >= self.threshold

    assert component_implementation_identity(
        SlottedPolicy(1).decide
    ) != component_implementation_identity(SlottedPolicy(99).decide)
    policy = ClassConfiguredPolicy()
    first = component_implementation_identity(policy.decide)
    ClassConfiguredPolicy.threshold = 99
    second = component_implementation_identity(policy.decide)
    assert first != second


def test_runtime_identity_binds_stateful_callable_object_configuration() -> None:
    class StatefulRoute:
        def __init__(self, provider: str) -> None:
            self.provider = provider

        def __call__(self) -> str:
            return self.provider

    route = StatefulRoute("provider-a")
    first = component_runtime_digest(route)
    route.provider = "provider-b"

    assert component_runtime_digest(route) != first


def test_runtime_identity_binds_private_callable_configuration() -> None:
    class PrivatePolicy:
        def __init__(self, threshold: int) -> None:
            self._threshold = threshold

        def __call__(self, value: int) -> bool:
            return value >= self._threshold

    assert PrivatePolicy(1)(50) is True
    assert PrivatePolicy(99)(50) is False
    assert component_runtime_digest(PrivatePolicy(1)) != component_runtime_digest(
        PrivatePolicy(99)
    )


def test_runtime_identity_binds_transparent_class_level_object_configuration() -> None:
    class ClassPolicy:
        settings = SimpleNamespace(threshold=1)

        def __call__(self, value: int) -> bool:
            return value >= self.settings.threshold

    policy = ClassPolicy()
    first = component_runtime_digest(policy)
    ClassPolicy.settings.threshold = 99

    assert component_runtime_digest(policy) != first


def test_runtime_identity_rejects_opaque_native_class_configuration() -> None:
    class ClassPolicy:
        rng = Random(1)

        def __call__(self) -> float:
            return self.rng.random()

    with pytest.raises(
        ValueError,
        match="explicit runtime_identity",
    ):
        component_runtime_digest(ClassPolicy())


def test_runtime_identity_binds_inherited_base_method_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BasePolicy:
        def decide(self) -> int:
            return 1

    class ChildPolicy(BasePolicy):
        pass

    policy = ChildPolicy()
    original = component_runtime_digest(policy)
    monkeypatch.setattr(BasePolicy, "decide", lambda self: 2)

    assert policy.decide() == 2
    assert component_runtime_digest(policy) != original


def test_snapshot_memo_does_not_alias_distinct_bound_method_receivers() -> None:
    class Adapter:
        def __init__(self, threshold: int) -> None:
            self.threshold = threshold

        def evaluate(self, value: int) -> bool:
            return value >= self.threshold

    first = Adapter(1)
    second = Adapter(99)

    with evaluators_module.optimization_runtime_identity_snapshot():
        first_identity = component_implementation_identity(first.evaluate)
        second_identity = component_implementation_identity(second.evaluate)

    assert first.evaluate(50) is True
    assert second.evaluate(50) is False
    assert first_identity != second_identity


@pytest.mark.parametrize(
    "module_name",
    (
        "test_evaluators",
        "ai_sdlc.extension",
        "ai_sdlc.core.stage_review",
        "ai_sdlc.core.stage_review.optimization.evaluators",
    ),
)
def test_snapshot_memo_revalidates_external_function_defaults(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
) -> None:
    def policy(value: int, threshold: int = 1) -> bool:
        return value >= threshold

    monkeypatch.setattr(policy, "__module__", module_name)
    with evaluators_module.optimization_runtime_identity_snapshot():
        original = component_implementation_identity(policy)
        monkeypatch.setattr(policy, "__defaults__", (99,))
        changed = component_implementation_identity(policy)

    assert policy(50) is False
    assert changed != original


@pytest.mark.parametrize(
    "module_name",
    (
        "ai_sdlc.extension",
        "ai_sdlc.core.stage_review.optimization.controller",
    ),
)
def test_component_identity_binds_globals_for_detached_ai_sdlc_function(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
) -> None:
    def decide(value: int) -> bool:
        return value >= _DETACHED_RUNTIME_THRESHOLD

    monkeypatch.setattr(decide, "__module__", module_name)
    monkeypatch.setitem(decide.__globals__, "_DETACHED_RUNTIME_THRESHOLD", 1)
    original = component_implementation_identity(decide)

    monkeypatch.setitem(decide.__globals__, "_DETACHED_RUNTIME_THRESHOLD", 99)

    assert decide(50) is False
    assert component_implementation_identity(decide) != original


def test_component_identity_binds_referenced_external_module_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def evaluate() -> int:
        return _DETACHED_HELPERS.route()  # type: ignore[attr-defined]

    monkeypatch.setattr(evaluate, "__module__", "plugin_adapter")
    monkeypatch.setattr(
        _DETACHED_HELPERS,
        "route",
        lambda: 1,
        raising=False,
    )
    original = component_implementation_identity(evaluate)

    monkeypatch.setattr(_DETACHED_HELPERS, "route", lambda: 2)

    assert evaluate() == 2
    assert component_implementation_identity(evaluate) != original


def test_component_identity_binds_nested_external_helper_constant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def route(value: int) -> bool:
        return _detached_helper2(value)

    def evaluate(value: int) -> bool:
        return _DETACHED_HELPERS.route(value)  # type: ignore[attr-defined]

    monkeypatch.setattr(_DETACHED_HELPERS, "route", route, raising=False)
    monkeypatch.setattr(_DETACHED_HELPERS, "threshold", 1, raising=False)
    monkeypatch.setattr(_DETACHED_HELPERS, "unrelated", 1, raising=False)
    original = component_implementation_identity(evaluate)

    monkeypatch.setattr(_DETACHED_HELPERS, "threshold", 99)

    assert evaluate(50) is False
    changed = component_implementation_identity(evaluate)
    assert changed != original

    monkeypatch.setattr(_DETACHED_HELPERS, "unrelated", 2)

    assert component_implementation_identity(evaluate) == changed


def test_component_identity_binds_cross_module_helper_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper2 = ModuleType("plugin_helper2")
    helper2.THRESHOLD = 1  # type: ignore[attr-defined]
    exec(
        compile(
            "def decide(value): return value >= THRESHOLD",
            "<plugin-helper2>",
            "exec",
        ),
        helper2.__dict__,
    )
    helper1 = ModuleType("plugin_helper1")
    helper1.helper2 = helper2  # type: ignore[attr-defined]
    exec(
        compile(
            "def route(value): return helper2.decide(value)",
            "<plugin-helper1>",
            "exec",
        ),
        helper1.__dict__,
    )

    def evaluate(value: int) -> bool:
        return _DETACHED_HELPERS.route(value)  # type: ignore[attr-defined]

    monkeypatch.setattr(_DETACHED_HELPERS, "route", helper1.route, raising=False)  # type: ignore[attr-defined]
    original = component_implementation_identity(evaluate)

    helper2.THRESHOLD = 99  # type: ignore[attr-defined]

    assert evaluate(50) is False
    assert component_implementation_identity(evaluate) != original


def test_component_identity_binds_module_bound_method_receiver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Router:
        def __init__(self, threshold: int) -> None:
            self.threshold = threshold

        def route(self, value: int) -> bool:
            return value >= self.threshold

    router = Router(1)

    def evaluate(value: int) -> bool:
        return _DETACHED_HELPERS.route(value)  # type: ignore[attr-defined]

    monkeypatch.setattr(_DETACHED_HELPERS, "route", router.route, raising=False)
    original = component_implementation_identity(evaluate)

    router.threshold = 99

    assert evaluate(50) is False
    assert component_implementation_identity(evaluate) != original


def test_component_identity_binds_module_partial_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def route(value: int, *, threshold: int) -> bool:
        return value >= threshold

    configured = partial(route, threshold=1)

    def evaluate(value: int) -> bool:
        return _DETACHED_HELPERS.route(value)  # type: ignore[attr-defined]

    monkeypatch.setattr(_DETACHED_HELPERS, "route", configured, raising=False)
    original = component_implementation_identity(evaluate)

    assert configured.keywords is not None
    configured.keywords["threshold"] = 99

    assert evaluate(50) is False
    assert component_implementation_identity(evaluate) != original


def test_component_identity_rejects_dynamic_module_attribute_lookup() -> None:
    def evaluate(name: str) -> object:
        return getattr(_DETACHED_HELPERS, name)

    with pytest.raises(
        ValueError,
        match="explicit referenced attributes",
    ):
        component_implementation_identity(evaluate)


def test_component_identity_supports_nested_stdlib_module_helper() -> None:
    def join(left: str, right: str) -> str:
        return os.path.join(left, right)

    assert Path(join("left", "right")).parts[-2:] == ("left", "right")
    assert component_implementation_identity(join)["source_digest"].startswith(
        "sha256:"
    )


def test_component_identity_does_not_capture_runtime_module_registry_contents() -> None:
    def lookup(name: str) -> object | None:
        return sys.modules.get(name)

    first = component_implementation_identity(lookup)
    transient_name = "test_runtime_identity_transient_module"
    sys.modules[transient_name] = ModuleType(transient_name)
    try:
        assert component_implementation_identity(lookup) == first
    finally:
        sys.modules.pop(transient_name, None)


def test_component_identity_binds_referenced_regex_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = component_implementation_identity(
        git_filter_safety_module.external_filter_overrides
    )
    monkeypatch.setattr(
        git_filter_safety_module,
        "_FILTER_KEY",
        re.compile(r"^never-match$"),
    )

    assert (
        component_implementation_identity(
            git_filter_safety_module.external_filter_overrides
        )
        != original
    )


def test_runtime_identity_rejects_opaque_callable_without_explicit_contract() -> None:
    with pytest.raises(
        ValueError,
        match="explicit runtime_identity",
    ):
        component_runtime_digest(attrgetter("left"))


def test_bounded_dependency_identity_binds_type_alias_semantics() -> None:
    first = TypeAliasType("PolicyValue", int | str)
    second = TypeAliasType("PolicyValue", int | float)

    first_identity = evaluators_module._bounded_dependency_identity(
        first,
        evaluators_module._BoundedDependencyState(active=set()),
        depth=0,
    )
    second_identity = evaluators_module._bounded_dependency_identity(
        second,
        evaluators_module._BoundedDependencyState(active=set()),
        depth=0,
    )

    assert first_identity["type_alias"]["name"] == "PolicyValue"
    assert first_identity != second_identity


def test_bounded_dependency_identity_binds_type_parameter_constraints() -> None:
    string_type = TypeVar("ValueType", bound=str)
    float_type = TypeVar("ValueType", bound=float)
    string_alias = TypeAliasType(
        "PolicyValues",
        list[string_type],
        type_params=(string_type,),
    )
    float_alias = TypeAliasType(
        "PolicyValues",
        list[float_type],
        type_params=(float_type,),
    )

    string_identity = evaluators_module._bounded_dependency_identity(
        string_alias,
        evaluators_module._BoundedDependencyState(active=set()),
        depth=0,
    )
    float_identity = evaluators_module._bounded_dependency_identity(
        float_alias,
        evaluators_module._BoundedDependencyState(active=set()),
        depth=0,
    )

    assert string_identity != float_identity


def test_type_alias_identity_binds_typing_kernel_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_token = evaluators_module._identity_measurement_kernel_binding_token()
    original_digest = evaluators_module._identity_measurement_kernel_digest()

    monkeypatch.setattr(evaluators_module, "get_args", lambda _value: ())

    assert (
        evaluators_module._identity_measurement_kernel_binding_token()
        != original_token
    )
    assert evaluators_module._identity_measurement_kernel_digest() != original_digest


def test_type_alias_identity_binds_no_default_kernel_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_token = evaluators_module._identity_measurement_kernel_binding_token()

    monkeypatch.setattr(
        evaluators_module,
        "_TYPE_PARAMETER_NO_DEFAULT",
        object(),
    )

    assert (
        evaluators_module._identity_measurement_kernel_binding_token()
        != original_token
    )
    with pytest.raises(ValueError, match="canonical NoDefault singleton drifted"):
        evaluators_module._identity_measurement_kernel_digest()


def test_type_alias_identity_rejects_spoofed_kernel_runtime_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_token = evaluators_module._identity_measurement_kernel_binding_token()
    spoofed = type("ForwardRef", (), {"__module__": "annotationlib"})

    monkeypatch.setattr(
        evaluators_module,
        "_FORWARD_REF_RUNTIME_TYPES",
        (("annotationlib:ForwardRef", spoofed),),
    )

    assert (
        evaluators_module._identity_measurement_kernel_binding_token()
        != original_token
    )
    with pytest.raises(ValueError, match="canonical runtime class drifted"):
        evaluators_module._identity_measurement_kernel_digest()


def test_type_alias_identity_binds_forward_ref_property_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.version_info < (3, 14):
        pytest.skip("ForwardRef properties are exposed by Python 3.14+")
    import annotationlib

    original_token = evaluators_module._identity_measurement_kernel_binding_token()
    original_digest = evaluators_module._identity_measurement_kernel_digest()

    monkeypatch.setattr(
        annotationlib.ForwardRef,
        "__forward_arg__",
        property(lambda _self: "spoofed"),
    )

    assert (
        evaluators_module._identity_measurement_kernel_binding_token()
        != original_token
    )
    assert evaluators_module._identity_measurement_kernel_digest() != original_digest


def test_type_alias_identity_rejects_spoofed_typing_marker_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_token = evaluators_module._identity_measurement_kernel_binding_token()

    def ClassVar(_parameters: object) -> object:  # noqa: N802
        return str

    spoofed = type(typing_module.ClassVar)(ClassVar)
    assert type(spoofed) is type(typing_module.ClassVar)
    assert repr(spoofed) == repr(typing_module.ClassVar)
    monkeypatch.setattr(
        evaluators_module,
        "_SUPPORTED_TYPE_MARKERS",
        tuple(
            (
                name,
                spoofed if name == "typing.ClassVar" else marker,
            )
            for name, marker in evaluators_module._SUPPORTED_TYPE_MARKERS
        ),
    )

    assert (
        evaluators_module._identity_measurement_kernel_binding_token()
        != original_token
    )
    with pytest.raises(ValueError, match="canonical typing marker drifted"):
        evaluators_module._identity_measurement_kernel_digest()


def test_identity_measurement_kernel_digest_is_stable_across_processes() -> None:
    script = (
        "from ai_sdlc.core.stage_review.optimization import evaluators as e;"
        "print(e._identity_measurement_kernel_digest())"
    )

    def digest() -> str:
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return completed.stdout.strip().splitlines()[-1]

    assert digest() == digest()


def test_type_alias_identity_enforces_node_budget() -> None:
    alias = TypeAliasType("PolicyValue", Literal[0, 1, 2])
    within_budget = evaluators_module._BoundedDependencyState(
        active=set(),
        max_nodes=6,
    )
    exceeded_budget = evaluators_module._BoundedDependencyState(
        active=set(),
        max_nodes=5,
    )

    evaluators_module._bounded_dependency_identity(
        alias,
        within_budget,
        depth=0,
    )
    with pytest.raises(ValueError, match="bounded limits"):
        evaluators_module._bounded_dependency_identity(
            alias,
            exceeded_budget,
            depth=0,
        )


def test_type_alias_identity_supports_literal_bytes_and_enum_members() -> None:
    class TransportMode(Enum):
        BINARY = b"binary"

    alias = TypeAliasType(
        "PolicyValue",
        Literal[b"payload", TransportMode.BINARY],
    )

    identity = evaluators_module._bounded_dependency_identity(
        alias,
        evaluators_module._BoundedDependencyState(active=set()),
        depth=0,
    )

    assert identity["type_alias"]["name"] == "PolicyValue"


def test_type_alias_identity_binds_new_type_supertype() -> None:
    user_id = NewType("UserId", int)
    external_id = NewType("UserId", str)
    user_alias = TypeAliasType("Identifier", user_id)
    external_alias = TypeAliasType("Identifier", external_id)

    user_identity = evaluators_module._bounded_dependency_identity(
        user_alias,
        evaluators_module._BoundedDependencyState(active=set()),
        depth=0,
    )
    external_identity = evaluators_module._bounded_dependency_identity(
        external_alias,
        evaluators_module._BoundedDependencyState(active=set()),
        depth=0,
    )

    assert user_identity != external_identity


def test_type_alias_identity_preserves_local_type_parameter_relationships() -> None:
    first_left = TypeVar("ValueType")
    first_right = TypeVar("ValueType")
    second_left = TypeVar("ValueType")
    second_right = TypeVar("ValueType")
    first_alias = TypeAliasType(
        "Relationship",
        tuple[first_left, first_left, first_right],
        type_params=(first_left, first_right),
    )
    second_alias = TypeAliasType(
        "Relationship",
        tuple[second_left, second_right, second_right],
        type_params=(second_left, second_right),
    )

    first_identity = evaluators_module._bounded_dependency_identity(
        first_alias,
        evaluators_module._BoundedDependencyState(active=set()),
        depth=0,
    )
    second_identity = evaluators_module._bounded_dependency_identity(
        second_alias,
        evaluators_module._BoundedDependencyState(active=set()),
        depth=0,
    )

    assert first_identity != second_identity


def test_type_alias_identity_scopes_symbols_per_alias_regardless_of_mapping_order() -> None:
    left_parameter = TypeVar("LeftValue")
    right_parameter = TypeVar("RightValue")
    left_alias = TypeAliasType(
        "LeftAlias",
        list[left_parameter],
        type_params=(left_parameter,),
    )
    right_alias = TypeAliasType(
        "RightAlias",
        list[right_parameter],
        type_params=(right_parameter,),
    )
    forward = {"left": left_alias, "right": right_alias}
    reverse = {"right": right_alias, "left": left_alias}

    forward_identity = evaluators_module._bounded_dependency_identity(
        forward,
        evaluators_module._BoundedDependencyState(active=set()),
        depth=0,
    )
    reverse_identity = evaluators_module._bounded_dependency_identity(
        reverse,
        evaluators_module._BoundedDependencyState(active=set()),
        depth=0,
    )

    assert forward_identity == reverse_identity


def test_type_alias_identity_does_not_accept_spoofed_no_default() -> None:
    no_default_type = type(
        "NoDefaultType",
        (),
        {"__repr__": lambda _self: "typing.NoDefault"},
    )
    missing_default = TypeVar("ValueType")
    spoofed_default = TypeVar("ValueType", default=no_default_type())
    missing_alias = TypeAliasType(
        "PolicyValue",
        list[missing_default],
        type_params=(missing_default,),
    )
    spoofed_alias = TypeAliasType(
        "PolicyValue",
        list[spoofed_default],
        type_params=(spoofed_default,),
    )

    missing_identity = evaluators_module._bounded_dependency_identity(
        missing_alias,
        evaluators_module._BoundedDependencyState(active=set()),
        depth=0,
    )
    with pytest.raises(ValueError, match="unsupported expression"):
        evaluators_module._bounded_dependency_identity(
            spoofed_alias,
            evaluators_module._BoundedDependencyState(active=set()),
            depth=0,
        )

    assert missing_identity["type_alias"]["name"] == "PolicyValue"


def test_type_alias_identity_rejects_unregistered_typing_object() -> None:
    class UnsupportedTypingValue:
        __module__ = "typing"

        def __repr__(self) -> str:
            raise AssertionError("unknown typing values must not execute repr")

    alias = TypeAliasType("PolicyValue", UnsupportedTypingValue())

    with pytest.raises(ValueError, match="unsupported"):
        evaluators_module._bounded_dependency_identity(
            alias,
            evaluators_module._BoundedDependencyState(active=set()),
            depth=0,
        )


def test_type_alias_identity_rejects_spoofed_runtime_type_names() -> None:
    def reject_attribute_access(_self: object, name: str) -> object:
        raise AssertionError(f"spoofed runtime value attribute accessed: {name}")

    for module_name, type_name in (
        ("typing_extensions", "TypeAliasType"),
        ("annotationlib", "ForwardRef"),
        ("typing", "NewType"),
        ("typing", "TypeVar"),
    ):
        spoofed_type = type(
            type_name,
            (),
            {
                "__module__": module_name,
                "__getattribute__": reject_attribute_access,
            },
        )
        alias = TypeAliasType("PolicyValue", spoofed_type())

        with pytest.raises(ValueError, match="unsupported"):
            evaluators_module._bounded_dependency_identity(
                alias,
                evaluators_module._BoundedDependencyState(active=set()),
                depth=0,
            )


def test_type_alias_identity_supports_native_forward_reference() -> None:
    alias = TypeAliasType("PolicyValue", ForwardRef("UserRecord"))

    identity = evaluators_module._bounded_dependency_identity(
        alias,
        evaluators_module._BoundedDependencyState(active=set()),
        depth=0,
    )

    assert (
        identity["type_alias"]["value"]["forward_reference"]["argument"]
        == "UserRecord"
    )


def test_type_alias_identity_rejects_contextual_forward_reference() -> None:
    if sys.version_info < (3, 14):
        pytest.skip("ForwardRef owner context is exposed by Python 3.14+")
    alias = TypeAliasType(
        "PolicyValue",
        ForwardRef("UserRecord", owner=object()),
    )

    with pytest.raises(ValueError, match="resolution context"):
        evaluators_module._bounded_dependency_identity(
            alias,
            evaluators_module._BoundedDependencyState(active=set()),
            depth=0,
        )


def test_type_alias_identity_supports_nested_generic_alias_origin() -> None:
    inner_parameter = TypeVar("ValueType")
    outer_parameter = TypeVar("ValueType")
    inner = TypeAliasType(
        "InnerValues",
        list[inner_parameter],
        type_params=(inner_parameter,),
    )
    outer = TypeAliasType(
        "OuterValues",
        inner[outer_parameter],
        type_params=(outer_parameter,),
    )

    identity = evaluators_module._bounded_dependency_identity(
        outer,
        evaluators_module._BoundedDependencyState(active=set()),
        depth=0,
    )

    assert (
        identity["type_alias"]["value"]["type_origin"]["type_alias"]["name"]
        == "InnerValues"
    )


def test_type_alias_identity_resolves_free_parameter_from_outer_scope_only() -> None:
    outer_parameter = TypeVar("ValueType")
    inner = TypeAliasType("InnerValues", list[outer_parameter])
    outer = TypeAliasType(
        "OuterValues",
        inner,
        type_params=(outer_parameter,),
    )

    with pytest.raises(ValueError, match="outside|undeclared"):
        evaluators_module._bounded_dependency_identity(
            inner,
            evaluators_module._BoundedDependencyState(active=set()),
            depth=0,
        )

    identity = evaluators_module._bounded_dependency_identity(
        outer,
        evaluators_module._BoundedDependencyState(active=set()),
        depth=0,
    )
    reference = identity["type_alias"]["value"]["type_alias"]["value"][
        "arguments"
    ][0]

    assert reference == {
        "type_parameter_reference": {
            "scope_id": 1,
            "symbol_id": 1,
        }
    }


def test_type_alias_identity_supports_common_callable_and_variadic_forms() -> None:
    parameters = ParamSpec("Parameters", default=[int, str])
    items = TypeVarTuple("Items", default=Unpack[tuple[str, ...]])
    aliases = (
        TypeAliasType("Row", tuple[int, ...]),
        TypeAliasType("Callback", Callable[[int, str], bool]),
        TypeAliasType(
            "GenericCallback",
            Callable[parameters, int],
            type_params=(parameters,),
        ),
        TypeAliasType(
            "VariadicRow",
            tuple[*items],
            type_params=(items,),
        ),
    )

    identities = [
        evaluators_module._bounded_dependency_identity(
            alias,
            evaluators_module._BoundedDependencyState(active=set()),
            depth=0,
        )
        for alias in aliases
    ]

    assert len(identities) == len(aliases)


def test_type_alias_identity_supports_registered_typing_generic_aliases() -> None:
    parameters = ParamSpec("Parameters")
    aliases = (
        TypeAliasType("LegacyList", typing_module.List[int]),  # noqa: UP006
        TypeAliasType(
            "LegacyDict",
            typing_module.Dict[str, int],  # noqa: UP006
        ),
        TypeAliasType("ClassValue", typing_module.ClassVar[int]),
        TypeAliasType("FinalValue", typing_module.Final[int]),
        TypeAliasType("RequiredValue", typing_module.Required[int]),
        TypeAliasType("OptionalValue", typing_module.NotRequired[int]),
        TypeAliasType("GuardValue", typing_module.TypeGuard[int]),
        TypeAliasType(
            "PrefixedParameters",
            typing_module.Concatenate[int, parameters],
            type_params=(parameters,),
        ),
    )

    identities = [
        evaluators_module._bounded_dependency_identity(
            alias,
            evaluators_module._BoundedDependencyState(active=set()),
            depth=0,
        )
        for alias in aliases
    ]

    assert len(identities) == len(aliases)


def test_live_package_cache_hit_fails_closed_then_rebuilds_for_changed_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluators_module._LIVE_PACKAGE_DIGEST_CACHE.clear()
    first_token = ("stable", ())
    changed_token = ("changed", ())
    evaluators_module._LIVE_PACKAGE_DIGEST_CACHE[first_token] = "sha256:stale"
    calls = 0

    def current_token() -> object:
        nonlocal calls
        calls += 1
        return first_token if calls == 1 else changed_token

    monkeypatch.setattr(
        evaluators_module,
        "_live_package_fast_token",
        current_token,
    )
    monkeypatch.setattr(
        evaluators_module,
        "_compute_live_package_digest",
        lambda: "sha256:current",
    )

    with pytest.raises(ValueError, match="changed during manifest snapshot"):
        evaluators_module._optimization_live_package_digest()

    assert evaluators_module._optimization_live_package_digest() == "sha256:current"


def test_live_class_fast_token_uses_shallow_member_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Policy:
        def decide(self) -> bool:
            return bool(_DETACHED_RUNTIME_THRESHOLD)

    def fail_on_recursive_member_dependency(*_args: object) -> object:
        raise AssertionError("class fast token recursively expanded a member")

    monkeypatch.setattr(
        evaluators_module,
        "_release_dependency_fast_token",
        fail_on_recursive_member_dependency,
    )

    token = evaluators_module._live_callable_cache_token(Policy)

    assert token[0] == "class"


def test_live_class_fast_token_reuses_one_generation_memo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Policy:
        def decide(self) -> bool:
            return True

    memo: dict[tuple[str, int], object] = {}
    first = evaluators_module._live_callable_cache_token(
        Policy,
        memo=memo,
    )

    def fail_if_reexpanded(_implementation: type[object]) -> object:
        raise AssertionError("class fast token ignored the generation memo")

    monkeypatch.setattr(
        evaluators_module,
        "_class_runtime_member_groups",
        fail_if_reexpanded,
    )

    assert (
        evaluators_module._live_callable_cache_token(
            Policy,
            memo=memo,
        )
        == first
    )


def test_release_class_fast_token_reuses_generation_dependency_memo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Policy:
        def decide(self) -> bool:
            return True

    monkeypatch.setattr(
        evaluators_module._LIVE_PACKAGE_SNAPSHOT_LOCAL,
        "fast_dependency_memo",
        {},
        raising=False,
    )
    first = evaluators_module._release_runtime_value_fast_token(
        Policy,
        set(),
    )

    def fail_if_reexpanded(_implementation: type[object]) -> object:
        raise AssertionError("release class token ignored the generation memo")

    monkeypatch.setattr(
        evaluators_module,
        "_class_runtime_member_groups",
        fail_if_reexpanded,
    )

    assert (
        evaluators_module._release_runtime_value_fast_token(
            Policy,
            set(),
        )
        == first
    )


def test_component_implementation_reuses_class_memo_within_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Policy:
        def decide(self) -> bool:
            return True

    monkeypatch.setattr(
        evaluators_module._LIVE_PACKAGE_SNAPSHOT_LOCAL,
        "implementation_memo",
        {},
        raising=False,
    )
    first = component_implementation_identity(Policy())

    def fail_if_reexpanded(_implementation: type[object]) -> object:
        raise AssertionError("component implementation ignored the class memo")

    monkeypatch.setattr(
        evaluators_module,
        "_class_runtime_member_groups",
        fail_if_reexpanded,
    )

    assert component_implementation_identity(Policy()) == first


def test_live_package_cache_miss_refreshes_dependency_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ScopeCacheSpy:
        def __init__(self) -> None:
            self.clear_calls = 0

        def cache_clear(self) -> None:
            self.clear_calls += 1

    token = ("stable", ())
    scope_cache = ScopeCacheSpy()
    evaluators_module._LIVE_PACKAGE_DIGEST_CACHE.clear()
    monkeypatch.setattr(
        evaluators_module,
        "_cached_optimization_dependency_scope",
        scope_cache,
    )
    monkeypatch.setattr(
        evaluators_module,
        "_compute_live_package_digest",
        lambda: "sha256:current",
    )
    monkeypatch.setattr(
        evaluators_module,
        "_live_package_fast_token",
        lambda: token,
    )

    assert evaluators_module._resolve_live_package_digest_for_token(token) == (
        token,
        "sha256:current",
    )
    assert scope_cache.clear_calls == 1


def test_identity_measurement_kernel_is_excluded_from_product_semantic_scope() -> None:
    assert evaluators_module._is_identity_measurement_kernel_callable(
        evaluators_module._bounded_builtin_identity
    )
    assert not evaluators_module._verified_first_party_scope_target(
        evaluators_module._bounded_builtin_identity
    )
    assert evaluators_module._verified_first_party_scope_target(
        evaluators_module._validate_invocation
    )
    assert evaluators_module._release_dependency_fast_token(
        evaluators_module._validate_invocation,
        "component_runtime_identity",
        evaluators_module.component_runtime_identity,
        set(),
    )[0] == "identity-kernel-callable"


@pytest.mark.parametrize(
    "helper_name",
    [
        "_identity_measurement_kernel_default_token",
        "_canonical_kernel_global_sentinel_identity",
    ],
)
def test_identity_kernel_sentinel_helpers_are_digest_bound_tcb(
    monkeypatch: pytest.MonkeyPatch,
    helper_name: str,
) -> None:
    helper = getattr(evaluators_module, helper_name)
    assert evaluators_module._is_identity_measurement_kernel_callable(helper)
    assert not evaluators_module._verified_first_party_scope_target(helper)
    original_token = evaluators_module._identity_measurement_kernel_binding_token()
    original_digest = evaluators_module._identity_measurement_kernel_digest()

    def replacement(*args: object, **kwargs: object) -> object:
        return helper(*args, **kwargs)

    replacement.__module__ = helper.__module__
    replacement.__name__ = helper.__name__
    replacement.__qualname__ = helper.__qualname__
    monkeypatch.setattr(evaluators_module, helper_name, replacement)

    assert (
        evaluators_module._identity_measurement_kernel_binding_token()
        != original_token
    )
    assert evaluators_module._identity_measurement_kernel_digest() != original_digest


def test_identity_measurement_kernel_rejects_spoofed_callable_metadata() -> None:
    canonical = evaluators_module._bounded_builtin_identity

    def first_spoof(_value: object) -> object:
        return {"spoof": "first"}

    def second_spoof(_value: object) -> object:
        return {"spoof": "second"}

    for spoof in (first_spoof, second_spoof):
        spoof.__module__ = canonical.__module__
        spoof.__name__ = canonical.__name__
        spoof.__qualname__ = canonical.__qualname__
        assert not evaluators_module._is_identity_measurement_kernel_callable(spoof)
        assert (
            evaluators_module._release_dependency_fast_token(
                evaluators_module._validate_invocation,
                "spoofed_kernel_helper",
                spoof,
                set(),
            )[0]
            != "identity-kernel-callable"
        )

    first_identity = evaluators_module._bounded_release_dependency(
        evaluators_module._validate_invocation,
        "spoofed_kernel_helper",
        first_spoof,
        evaluators_module._BoundedDependencyState(active=set()),
        depth=0,
    )
    second_identity = evaluators_module._bounded_release_dependency(
        evaluators_module._validate_invocation,
        "spoofed_kernel_helper",
        second_spoof,
        evaluators_module._BoundedDependencyState(active=set()),
        depth=0,
    )

    assert first_identity != second_identity


def test_identity_measurement_kernel_trusts_bound_wrapper_chain() -> None:
    wrapper = evaluators_module._cached_optimization_dependency_scope
    wrapped = wrapper.__wrapped__

    assert evaluators_module._is_identity_measurement_kernel_callable(wrapper)
    assert evaluators_module._is_identity_measurement_kernel_callable(wrapped)


def _forward_ref_class_backed_default_sentinel() -> tuple[object, object]:
    function = ForwardRef._evaluate
    defaults = function.__defaults__ or ()
    sentinel = next(
        (
            value
            for value in defaults
            if type(value) is not object
            and not evaluators_module._is_stable_configuration_value(value)
            and any(item is value for item in function.__globals__.values())
        ),
        None,
    )
    if sentinel is None:
        pytest.skip("runtime has no class-backed ForwardRef sentinel default")
    return function, sentinel


def test_identity_kernel_accepts_canonical_stdlib_global_sentinel_default() -> None:
    function, sentinel = _forward_ref_class_backed_default_sentinel()

    identity = evaluators_module._canonical_kernel_default_identity(
        function,
        sentinel,
        slot="positional:test",
    )

    assert identity["opaque_sentinel"]["global_names"]
    assert identity["opaque_sentinel"]["local_slot"] is None
    assert identity["opaque_sentinel"]["class_implementation"]


def test_identity_kernel_rejects_spoofed_stdlib_sentinel_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    function, sentinel = _forward_ref_class_backed_default_sentinel()
    sentinel_type = type(sentinel)
    spoofed_type = type(
        sentinel_type.__name__,
        (),
        {
            "__module__": sentinel_type.__module__,
            "__slots__": (),
        },
    )
    spoofed_type.__qualname__ = sentinel_type.__qualname__
    spoofed = spoofed_type()
    monkeypatch.setitem(function.__globals__, "_AI_SDLC_SPOOFED_SENTINEL", spoofed)

    with pytest.raises(ValueError, match="unsupported default value"):
        evaluators_module._canonical_kernel_default_identity(
            function,
            spoofed,
            slot="positional:test",
        )


def test_identity_kernel_rejects_drifted_stdlib_sentinel_type_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    function, sentinel = _forward_ref_class_backed_default_sentinel()
    sentinel_type = type(sentinel)
    module = sys.modules[sentinel_type.__module__]
    binding_name = sentinel_type.__qualname__.split(".")[0]
    monkeypatch.setattr(module, binding_name, object())

    with pytest.raises(ValueError, match="unsupported default value"):
        evaluators_module._canonical_kernel_default_identity(
            function,
            sentinel,
            slot="positional:test",
        )


def test_identity_kernel_class_export_rebinding_invalidates_fast_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _entrypoint, runtime_type = evaluators_module._FORWARD_REF_RUNTIME_TYPES[0]
    module = sys.modules[runtime_type.__module__]
    binding_name = runtime_type.__qualname__.split(".")[0]
    original_token = evaluators_module._identity_measurement_kernel_binding_token()

    monkeypatch.setattr(module, binding_name, object())

    assert (
        evaluators_module._identity_measurement_kernel_binding_token()
        != original_token
    )


def test_identity_kernel_sentinel_class_mutation_invalidates_fast_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    function, sentinel = _forward_ref_class_backed_default_sentinel()
    sentinel_type = type(sentinel)
    original_callable_token = (
        evaluators_module._identity_measurement_kernel_callable_token(function)
    )
    original_binding_token = (
        evaluators_module._identity_measurement_kernel_binding_token()
    )

    monkeypatch.setattr(
        sentinel_type,
        "__call__",
        lambda _self: None,
        raising=False,
    )

    assert (
        evaluators_module._identity_measurement_kernel_callable_token(function)
        != original_callable_token
    )
    assert (
        evaluators_module._identity_measurement_kernel_binding_token()
        != original_binding_token
    )
    with pytest.raises(ValueError, match="unsupported default value"):
        evaluators_module._canonical_kernel_default_identity(
            function,
            sentinel,
            slot="positional:test",
        )


def test_identity_kernel_bounds_self_referential_sentinel_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SelfSentinel:
        __slots__ = ()

    SelfSentinel.__module__ = "typing"
    SelfSentinel.__qualname__ = "_AiSdlcSelfSentinel"
    sentinel = SelfSentinel()

    def member(_self: object, default: object = sentinel) -> object:
        return default

    SelfSentinel.member = member
    monkeypatch.setattr(
        typing_module,
        SelfSentinel.__qualname__,
        SelfSentinel,
        raising=False,
    )
    monkeypatch.setitem(member.__globals__, "_AI_SDLC_SELF_SENTINEL", sentinel)

    def consumer(default: object = sentinel) -> object:
        return default

    token = evaluators_module._identity_measurement_kernel_callable_token(
        consumer
    )

    assert token == evaluators_module._identity_measurement_kernel_callable_token(
        consumer
    )
    assert "recursive-class" in repr(token)
    with pytest.raises(ValueError, match="recursive"):
        evaluators_module._canonical_kernel_callable_identity(consumer)


def test_identity_kernel_bounds_mutually_referential_sentinel_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FirstSentinel:
        __slots__ = ()

    class SecondSentinel:
        __slots__ = ()

    FirstSentinel.__module__ = "typing"
    FirstSentinel.__qualname__ = "_AiSdlcFirstSentinel"
    SecondSentinel.__module__ = "typing"
    SecondSentinel.__qualname__ = "_AiSdlcSecondSentinel"
    first = FirstSentinel()
    second = SecondSentinel()

    def first_member(_self: object, default: object = second) -> object:
        return default

    def second_member(_self: object, default: object = first) -> object:
        return default

    FirstSentinel.member = first_member
    SecondSentinel.member = second_member
    monkeypatch.setattr(
        typing_module,
        FirstSentinel.__qualname__,
        FirstSentinel,
        raising=False,
    )
    monkeypatch.setattr(
        typing_module,
        SecondSentinel.__qualname__,
        SecondSentinel,
        raising=False,
    )
    monkeypatch.setitem(first_member.__globals__, "_AI_SDLC_FIRST_SENTINEL", first)
    monkeypatch.setitem(
        second_member.__globals__,
        "_AI_SDLC_SECOND_SENTINEL",
        second,
    )

    def consumer(default: object = first) -> object:
        return default

    token = evaluators_module._identity_measurement_kernel_callable_token(
        consumer
    )

    assert token == evaluators_module._identity_measurement_kernel_callable_token(
        consumer
    )
    assert "recursive-class" in repr(token)
    with pytest.raises(ValueError, match="recursive"):
        evaluators_module._canonical_kernel_callable_identity(consumer)


def test_release_class_members_exclude_injected_third_party_methods() -> None:
    assert all(
        str(getattr(member, "__module__", "") or "").startswith("ai_sdlc.")
        for _, members in evaluators_module._release_class_runtime_member_groups(
            EvaluationContext
        )
        for _, member in members
    )


def test_product_semantic_scope_excludes_generated_class_methods() -> None:
    assert all(
        getattr(getattr(item, "__code__", None), "co_filename", None)
        != "<string>"
        for item in evaluators_module._optimization_dependency_scope().nodes
    )


def test_product_semantic_scope_stays_within_structural_budget() -> None:
    scope = evaluators_module._optimization_dependency_scope()

    assert len(scope.module_names) <= 180
    assert len(scope.nodes) <= 2500
    assert len(scope.covered_function_ids) <= 1800


def test_identity_measurement_policy_and_kernel_are_digest_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy_digest = evaluators_module._identity_measurement_policy_digest()
    kernel_digest = evaluators_module._identity_measurement_kernel_digest()
    monkeypatch.setattr(
        evaluators_module,
        "_TRUSTED_STDLIB_RUNTIME_OBJECTS",
        frozenset(
            (
                *evaluators_module._TRUSTED_STDLIB_RUNTIME_OBJECTS,
                "example:Policy",
            )
        ),
    )
    assert evaluators_module._identity_measurement_policy_digest() != policy_digest

    monkeypatch.setattr(
        evaluators_module,
        "_module_attribute_identity",
        lambda _value, _seen: {"replacement": True},
    )
    assert evaluators_module._identity_measurement_kernel_digest() != kernel_digest


def test_identity_measurement_kernel_binds_lru_wrapped_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = evaluators_module._identity_measurement_kernel_digest()
    original_fast_token = evaluators_module._live_package_fast_token()
    wrapped = evaluators_module._cached_function_global_names.__wrapped__

    def tampered(_code: object) -> tuple[str, ...]:
        return ("tampered-global",)

    monkeypatch.setattr(wrapped, "__code__", tampered.__code__)

    assert evaluators_module._identity_measurement_kernel_digest() != original
    assert evaluators_module._live_package_fast_token() != original_fast_token


def test_identity_measurement_kernel_binds_contextmanager_wrapped_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = evaluators_module._identity_measurement_kernel_digest()
    wrapped = evaluators_module.optimization_runtime_identity_snapshot.__wrapped__

    def tampered_snapshot():
        yield

    monkeypatch.setattr(wrapped, "__code__", tampered_snapshot.__code__)

    assert evaluators_module._identity_measurement_kernel_digest() != original


def test_release_fast_token_detects_external_helper_global_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def owner(value: int) -> bool:
        return _fast_external_helper(value)

    first = evaluators_module._release_dependency_fast_token(
        owner,
        "_fast_external_helper",
        _fast_external_helper,
        set(),
    )
    monkeypatch.setattr(
        sys.modules[__name__],
        "_FAST_EXTERNAL_THRESHOLD",
        99,
    )

    assert (
        evaluators_module._release_dependency_fast_token(
            owner,
            "_fast_external_helper",
            _fast_external_helper,
            set(),
        )
        != first
    )


def test_runtime_generation_change_invalidates_active_manifest_snapshot() -> None:
    with pytest.raises(
        ValueError,
        match="generation changed",
    ), evaluators_module.optimization_runtime_identity_snapshot():
        evaluators_module.invalidate_optimization_runtime_identity()


def test_noop_runtime_invalidation_does_not_change_content_digest() -> None:
    first = evaluators_module._optimization_live_package_digest()

    evaluators_module.invalidate_optimization_runtime_identity()
    second = evaluators_module._optimization_live_package_digest()
    evaluators_module.invalidate_optimization_runtime_identity()

    assert second == first
    assert evaluators_module._optimization_live_package_digest() == first


def test_live_package_identity_detects_in_place_keyword_default_change() -> None:
    from ai_sdlc.core.stage_review.optimization import (
        candidate_domain_defaults,
    )

    defaults = candidate_domain_defaults._contract.__kwdefaults__
    assert defaults is not None
    original = defaults["escape"]
    first = evaluators_module._optimization_live_package_digest()
    defaults["escape"] = not original
    try:
        assert evaluators_module._optimization_live_package_digest() != first
    finally:
        defaults["escape"] = original

    assert evaluators_module._optimization_live_package_digest() == first


def test_callable_identity_binds_third_party_closure_and_partial_objects() -> None:
    class ThirdPartyPolicy:
        def __init__(self, enabled: bool) -> None:
            self.enabled = enabled

        def decide(self) -> bool:
            return self.enabled

    def with_policy(policy: ThirdPartyPolicy):
        def execute() -> bool:
            return policy.decide()

        return execute

    def execute_partial(policy: ThirdPartyPolicy) -> bool:
        return policy.decide()

    enabled = ThirdPartyPolicy(True)
    disabled = ThirdPartyPolicy(False)
    assert component_implementation_identity(
        with_policy(enabled)
    ) != component_implementation_identity(with_policy(disabled))
    assert component_implementation_identity(
        partial(execute_partial, enabled)
    ) != component_implementation_identity(partial(execute_partial, disabled))


def test_dynamic_callable_identity_binds_normalized_code() -> None:
    first_namespace = {"__name__": "dynamic.reviewer"}
    second_namespace = {"__name__": "dynamic.reviewer"}
    exec(compile("def evaluate(): return 1", "<dynamic>", "exec"), first_namespace)
    exec(compile("def evaluate(): return 2", "<dynamic>", "exec"), second_namespace)

    assert component_implementation_identity(
        first_namespace["evaluate"]
    ) != component_implementation_identity(second_namespace["evaluate"])


def test_callable_identity_binds_referenced_globals_and_sets() -> None:
    namespace = {"__name__": "dynamic.global-reviewer", "THRESHOLD": 1}
    exec(
        compile(
            "def decide(value): return value >= THRESHOLD",
            "<dynamic-global>",
            "exec",
        ),
        namespace,
    )
    decide = namespace["decide"]
    first = component_implementation_identity(decide)
    namespace["THRESHOLD"] = 99
    second = component_implementation_identity(decide)

    def with_capabilities(capabilities: set[str]):
        def allows(name: str) -> bool:
            return name in capabilities

        return allows

    assert first != second
    assert component_implementation_identity(
        with_capabilities({"security"})
    ) != component_implementation_identity(with_capabilities({"delivery"}))


def test_callable_identity_fails_closed_for_recursive_capture() -> None:
    recursive: list[object] = []
    recursive.append(recursive)

    def execute() -> int:
        return len(recursive)

    with pytest.raises(ValueError, match="recursive reference"):
        component_implementation_identity(execute)


def test_core_statistics_authority_rejects_adapter_improvement_count_forgery() -> None:
    registry = _registry()
    adapter = _Adapter(improved_count=59)
    registry.register(_contract("forged-statistics"), adapter)

    with pytest.raises(
        ValueError,
        match="statistical sample diverged from core evidence",
    ):
        registry.evaluate(
            evaluator_kind="forged-statistics",
            candidate=_candidate(
                "selection",
                "selection_policy.capability_requirement_rules",
                suffix="forged-statistics",
            ),
            context=_context(),
        )


def test_evaluator_rejects_partition_not_authorized_by_contract() -> None:
    registry = _registry()
    adapter = _Adapter()
    registry.register(_contract("validation-only"), adapter)

    with pytest.raises(ValueError, match="partition is not authorized"):
        registry.evaluate(
            evaluator_kind="validation-only",
            candidate=_candidate(
                "selection",
                "selection_policy.capability_requirement_rules",
                suffix="partition",
            ),
            context=_context(partition="holdout"),
        )

    assert adapter.calls == 0


def test_semantic_evaluator_must_be_independent_from_candidate_generator() -> None:
    registry = _registry()
    adapter = _Adapter()
    registry.register(_contract("independent-semantic"), adapter)
    candidate = _candidate(
        "role_profile",
        "role_profiles.compositions",
        suffix="independence",
    )

    with pytest.raises(ValueError, match="independent evaluation binding"):
        registry.evaluate(
            evaluator_kind="independent-semantic",
            candidate=candidate,
            context=_context(evaluation_binding_id=candidate.generator_identity),
        )

    assert adapter.calls == 0


def test_evaluator_provider_identity_and_capabilities_are_enforced() -> None:
    registry = _registry()
    adapter = _Adapter()
    registry.register(_contract("provider-bound"), adapter)
    candidate = _candidate(
        "binding", "binding_policy.require_independent_blocking_slots", suffix="provider"
    )

    with pytest.raises(ValueError, match="generator provider"):
        registry.evaluate(
            evaluator_kind="provider-bound",
            candidate=candidate,
            context=_context(evaluation_provider_id=candidate.generator_provider_id),
        )
    with pytest.raises(ValueError, match="provider constraints"):
        registry.evaluate(
            evaluator_kind="provider-bound",
            candidate=candidate,
            context=_context(provider_capabilities=("network-write",)),
        )

    assert adapter.calls == 0


def test_schema_incompatible_evaluator_is_rejected_before_adapter_call() -> None:
    registry = _registry()
    adapter = _Adapter()
    registry.register(
        _contract(
            "future-schema",
            candidate_schema_version="optimization-candidate.v2",
        ),
        adapter,
    )

    with pytest.raises(ValueError, match="candidate schema is incompatible"):
        registry.evaluate(
            evaluator_kind="future-schema",
            candidate=_candidate(
                "budget",
                "budget_policy.high.hard_tokens",
                suffix="schema",
            ),
            context=_context(),
        )

    assert adapter.calls == 0


@dataclass
class _Adapter:
    calls: int = 0
    improved_count: int = 60

    def evaluate(
        self,
        candidate: OptimizationCandidate,
        context: EvaluationContext,
        contract: EvaluatorContract,
    ) -> OptimizationEvaluationReport:
        self.calls += 1
        policy = baseline_statistics_policy()
        session_ids = tuple(f"session.{index:03d}" for index in range(60))
        p_value, power, lower = binary_improvement_statistics(
            self.improved_count,
            len(session_ids),
            alpha=policy.shadow_alpha,
            policy=policy,
        )
        return OptimizationEvaluationReport(
            report_id=f"evaluation.{candidate.candidate_id}",
            candidate_digest=candidate.candidate_digest,
            domain_contract_digest=candidate.domain_contract_digest,
            domain_adapter_id=candidate.domain_adapter_id,
            domain_adapter_version=candidate.domain_adapter_version,
            domain_adapter_digest=candidate.domain_adapter_digest,
            domain_registry_digest=candidate.domain_registry_digest,
            evaluator_kind=contract.evaluator_kind,
            evaluator_version=contract.evaluator_version,
            evaluator_contract_digest=contract.contract_digest,
            dataset_digest=context.dataset_digest,
            partition=context.partition,
            evaluation_binding_id=context.evaluation_binding_id,
            quality_deltas={"confirmed_p0_p1_detection": 0.1},
            cost_deltas={"estimated_cost": 0.0},
            censoring_metrics={"unknown_or_censored_rate": 0.0},
            guard_results={"protocol_integrity": True},
            comparison_session_ids=session_ids,
            hypothesis_family_digest=context.hypothesis_family_digest,
            improved_count=self.improved_count,
            sample_count=len(session_ids),
            statistical_sample_digest=_sample(candidate, context).sample_digest,
            statistics_policy_digest=policy.policy_digest,
            statistical_alpha=policy.shadow_alpha,
            raw_p_value=p_value,
            holm_rank=1,
            holm_threshold=0.05,
            statistical_power=power,
            effect_confidence_lower=lower,
            recommendation="finalist_eligible",
        )


class _AlternateAdapter(_Adapter):
    pass


class _StatisticsAuthority:
    def sample(
        self,
        candidate: OptimizationCandidate,
        context: EvaluationContext,
    ) -> OptimizationStatisticalSample:
        return _sample(candidate, context)


def _registry() -> OptimizationEvaluatorRegistry:
    return OptimizationEvaluatorRegistry(
        statistics_authority=_StatisticsAuthority()
    )


def _sample(
    candidate: OptimizationCandidate,
    context: EvaluationContext,
) -> OptimizationStatisticalSample:
    session_ids = tuple(f"session.{index:03d}" for index in range(60))
    return OptimizationStatisticalSample(
        candidate_digest=candidate.candidate_digest,
        dataset_digest=context.dataset_digest,
        comparison_session_ids=session_ids,
        improved_session_ids=session_ids,
        source_evidence_digests=("sha256:test-evidence",),
    )


def _contract(
    kind: str,
    *,
    candidate_schema_version: str = "optimization-candidate.v1",
) -> EvaluatorContract:
    return EvaluatorContract(
        evaluator_kind=kind,
        evaluator_version="1.0.0",
        candidate_schema_version=candidate_schema_version,
        report_schema_version="optimization-evaluation-report.v1",
        allowed_partitions=("train", "validation"),
        compatible_candidate_domains=(
            "binding",
            "budget",
            "capability_mapping",
            "role_profile",
            "selection",
        ),
        independence_level="independent_binding",
        deterministic=False,
        provider_constraints=("read-only",),
    )


def _context(
    *,
    partition: str = "validation",
    evaluation_binding_id: str = "evaluation-binding.independent",
    evaluation_provider_id: str = "provider.evaluator",
    provider_capabilities: tuple[str, ...] = ("read-only",),
) -> EvaluationContext:
    policy = baseline_statistics_policy()
    return EvaluationContext(
        dataset_digest="sha256:dataset.1",
        partition=partition,
        evaluation_binding_id=evaluation_binding_id,
        evaluation_provider_id=evaluation_provider_id,
        provider_capabilities=provider_capabilities,
        resource_reservation_digest="sha256:offline-reservation.1",
        statistics_policy_digest=policy.policy_digest,
        statistical_alpha=policy.shadow_alpha,
    )


def _candidate(domain: str, field_path: str, *, suffix: str) -> OptimizationCandidate:
    values = {
        "binding": True,
        "budget": 2,
        "capability_mapping": "sha256:registry.next",
        "role_profile": [["sha256:role.security"]],
        "selection": [],
    }
    return OptimizationCandidate(
        candidate_id=f"optimization-candidate.{suffix}",
        candidate_domain=domain,
        **default_candidate_domain_registry().candidate_binding(domain),
        base_snapshot_digest="sha256:baseline.1",
        patch_operations=(
            OptimizationPatchOperation(
                operation="replace",
                field_path=field_path,
                value=values[domain],
            ),
        ),
        expected_effect="improve reviewer quality without lowering hard constraints",
        rollback_target="sha256:baseline.1",
        generator_identity="generator.binding.1",
        generator_provider_id="provider.generator",
        attribution_digests=(
            () if domain == "budget" else ("sha256:attribution.1",)
        ),
        metric_evidence_digests=(
            ("sha256:metric-evidence.1",) if domain == "budget" else ()
        ),
        target_stratum_ids=("implementation:high",),
        dataset_partition_refs=("train",),
        estimated_provider_calls=1,
        estimated_tokens=1000,
        estimated_cost=0.5,
        estimated_active_wall_clock=30,
        evidence_refs=("sha256:evidence.1",),
    )
