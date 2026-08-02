"""离线优化 Constitution、Trigger、Epoch 与维护结果合同。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from ai_sdlc.core.stage_review.artifact_compat import (
    ArtifactCompatibility,
    fill_artifact_digest,
)
from ai_sdlc.core.stage_review.canonical import (
    CanonicalizationPolicy,
    canonical_digest,
)
from ai_sdlc.core.stage_review.registry_versions import (
    require_machine_id,
    require_version,
)
from ai_sdlc.core.stage_review.resource_builders import parse_utc, stable_id
from ai_sdlc.core.stage_review.resource_models import ResourceAmounts

EpochState = Literal[
    "queued",
    "snapshotting",
    "generating",
    "replaying",
    "holdout_evaluating",
    "shadow_observing",
    "evaluating",
    "promoting",
    "pausing",
    "paused",
    "retry_wait",
    "safety_pending",
    "promoted",
    "no_change",
    "failed",
    "superseded_runtime_upgrade",
]
MaintenanceResultCode = Literal[
    "not_ready",
    "advanced",
    "paused",
    "promoted",
    "no_change",
    "failed",
    "superseded_runtime_upgrade",
]


class MaintenanceBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    maximum_provider_calls: int = Field(default=2, ge=1, le=2)
    maximum_tokens: int = Field(default=100_000, ge=1, le=100_000)
    maximum_cost: float = Field(default=2, gt=0, le=2)
    maximum_active_wall_clock: float = Field(default=300, gt=0, le=300)
    maximum_parallelism: int = Field(default=1, ge=1, le=1)


class OptimizationConstitution(ArtifactCompatibility):
    schema_version: Literal["optimization-constitution.v1"] = (
        "optimization-constitution.v1"
    )
    artifact_kind: Literal["optimization-constitution"] = "optimization-constitution"
    constitution_version: str
    epoch_budget_policy_digest: str
    attribution_policy_digest: str
    evaluator_registry_digest: str
    auto_promotion_policy_digest: str
    storage_policy_digest: str
    candidate_domain_registry_digest: str
    statistics_policy_digest: str
    minimum_created_sessions: int = Field(default=30, ge=1)
    minimum_evaluable_sessions: int = Field(default=20, ge=1)
    holdout_ratio: float = Field(default=0.2, gt=0, lt=1)
    minimum_holdout_sessions: int = Field(default=10, ge=1)
    minimum_shadow_sessions: int = Field(default=10, ge=1)
    minimum_shadow_days: int = Field(default=14, ge=1)
    candidate_family_limit: int = Field(default=8, ge=1)
    no_change_new_session_cooldown: int = Field(default=10, ge=1)
    promotion_new_session_cooldown: int = Field(default=10, ge=1)
    promotion_day_cooldown: int = Field(default=7, ge=1)
    familywise_alpha: float = Field(default=0.05, gt=0, lt=1)
    constitution_digest: str = ""

    @field_validator("constitution_version")
    @classmethod
    def _version_is_supported(cls, value: str) -> str:
        return require_version(value)

    @model_validator(mode="after")
    def _verify_constitution(self) -> Self:
        from ai_sdlc.core.stage_review.optimization.statistics import (
            statistics_policy_for_digest,
        )

        if not all(
            (
                self.candidate_domain_registry_digest.strip(),
                self.statistics_policy_digest.strip(),
            )
        ):
            raise ValueError("optimization policy digest is required")
        statistics_policy = statistics_policy_for_digest(
            self.statistics_policy_digest
        )
        if self.familywise_alpha != statistics_policy.familywise_alpha:
            raise ValueError(
                "optimization familywise alpha diverged from statistics policy"
            )
        if self.minimum_evaluable_sessions > self.minimum_created_sessions:
            raise ValueError("evaluable baseline cannot exceed created baseline")
        return fill_artifact_digest(self, "constitution_digest")


class OptimizationTriggerEvent(ArtifactCompatibility):
    schema_version: Literal[
        "optimization-trigger-event.v1",
        "optimization-trigger-event.v2",
    ] = (
        "optimization-trigger-event.v2"
    )
    artifact_kind: Literal["optimization-trigger-event"] = "optimization-trigger-event"
    trigger_id: str
    project_id: str
    session_sequence_high_watermark: int = Field(ge=0)
    trigger_fingerprint: str
    constitution_digest: str
    baseline_snapshot_digest: str
    candidate_domain_registry_digest: str
    statistics_policy_digest: str
    evaluator_registry_digest: str
    auto_promotion_policy_digest: str
    runtime_bundle_manifest_digest: str = "sha256:runtime-bundle-unbound"
    trigger_facts: tuple[str, ...]
    trigger_fact_digests: tuple[str, ...] = ()
    new_session_count: int = Field(ge=0)
    triggered: bool
    trigger_digest: str = ""

    @model_validator(mode="before")
    @classmethod
    def _decode_legacy_trigger(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        return _decode_legacy_policy_lineage(
            value,
            schema_version="optimization-trigger-event.v1",
            digest_field="trigger_digest",
            context=info.context,
        )

    @field_validator("trigger_id", "project_id")
    @classmethod
    def _identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "optimization trigger identity")

    @model_validator(mode="after")
    def _verify_trigger(self) -> Self:
        policy_lineage = (
            self.constitution_digest,
            self.candidate_domain_registry_digest,
            self.statistics_policy_digest,
            self.evaluator_registry_digest,
            self.auto_promotion_policy_digest,
            self.runtime_bundle_manifest_digest,
        )
        if any(not item.strip() for item in policy_lineage):
            raise ValueError("optimization trigger policy lineage is incomplete")
        if self.trigger_facts != tuple(sorted(set(self.trigger_facts))):
            raise ValueError("optimization trigger facts must be canonical")
        if self.trigger_fact_digests != tuple(
            sorted(set(self.trigger_fact_digests))
        ):
            raise ValueError("optimization trigger fact digests must be canonical")
        return _fill_optimization_lineage_digest(self, "trigger_digest")


class OptimizationEpoch(ArtifactCompatibility):
    schema_version: Literal[
        "optimization-epoch.v1",
        "optimization-epoch.v2",
    ] = "optimization-epoch.v2"
    artifact_kind: Literal["optimization-epoch"] = "optimization-epoch"
    epoch_id: str
    project_id: str
    trigger_fingerprint: str
    trigger_digest: str
    constitution_digest: str
    baseline_snapshot_digest: str
    candidate_domain_registry_digest: str
    statistics_policy_digest: str
    evaluator_registry_digest: str
    auto_promotion_policy_digest: str
    runtime_bundle_manifest_digest: str = "sha256:runtime-bundle-unbound"
    session_sequence_high_watermark: int = Field(ge=0)
    new_session_count: int = Field(ge=0)
    state: EpochState
    revision: int = Field(ge=1)
    previous_epoch_digest: str = ""
    reservation_id: str = ""
    reservation_fencing_token: int = Field(default=0, ge=0)
    dataset_digest: str = ""
    finalist_candidate_digest: str = ""
    failure_reason: str = ""
    resume_state: EpochState | None = None
    lease_fencing_epoch: int = Field(default=0, ge=0)
    started_at: str = ""
    terminal_at: str = ""
    cumulative_usage: ResourceAmounts = Field(default_factory=ResourceAmounts)
    epoch_digest: str = ""

    @model_validator(mode="before")
    @classmethod
    def _decode_legacy_epoch(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        return _decode_legacy_policy_lineage(
            value,
            schema_version="optimization-epoch.v1",
            digest_field="epoch_digest",
            context=info.context,
        )

    @field_validator("epoch_id", "project_id")
    @classmethod
    def _identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "optimization epoch identity")

    @model_validator(mode="after")
    def _verify_epoch(self) -> Self:
        policy_lineage = (
            self.constitution_digest,
            self.candidate_domain_registry_digest,
            self.statistics_policy_digest,
            self.evaluator_registry_digest,
            self.auto_promotion_policy_digest,
            self.runtime_bundle_manifest_digest,
        )
        if any(not item.strip() for item in policy_lineage):
            raise ValueError("optimization epoch policy lineage is incomplete")
        if self.revision == 1 and self.previous_epoch_digest:
            raise ValueError("initial epoch cannot have previous digest")
        if self.revision > 1 and not self.previous_epoch_digest:
            raise ValueError("advanced epoch requires previous digest")
        if self.started_at:
            parse_utc(self.started_at)
        if self.terminal_at:
            parse_utc(self.terminal_at)
        return _fill_optimization_lineage_digest(self, "epoch_digest")


class OptimizationEpochLeaseClaim(ArtifactCompatibility):
    schema_version: Literal["optimization-epoch-lease-claim.v1"] = (
        "optimization-epoch-lease-claim.v1"
    )
    artifact_kind: Literal["optimization-epoch-lease-claim"] = (
        "optimization-epoch-lease-claim"
    )
    epoch_id: str
    owner_id: str
    fencing_epoch: int = Field(ge=1)
    acquired_at: str
    expires_at: str
    previous_claim_digest: str = ""
    claim_digest: str = ""

    @field_validator("epoch_id", "owner_id")
    @classmethod
    def _identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "optimization lease identity")

    @model_validator(mode="after")
    def _verify_claim(self) -> Self:
        if parse_utc(self.expires_at) <= parse_utc(self.acquired_at):
            raise ValueError("optimization lease expiry must follow acquisition")
        return fill_artifact_digest(self, "claim_digest")


class OptimizationEpochLeaseRelease(ArtifactCompatibility):
    schema_version: Literal["optimization-epoch-lease-release.v1"] = (
        "optimization-epoch-lease-release.v1"
    )
    artifact_kind: Literal["optimization-epoch-lease-release"] = (
        "optimization-epoch-lease-release"
    )
    release_id: str
    epoch_id: str
    owner_id: str
    fencing_epoch: int = Field(ge=1)
    claim_digest: str
    released_at: str
    release_digest: str = ""

    @field_validator("release_id", "epoch_id", "owner_id")
    @classmethod
    def _identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "optimization lease release identity")

    @model_validator(mode="after")
    def _verify_release(self) -> Self:
        parse_utc(self.released_at)
        return fill_artifact_digest(self, "release_digest")


class OptimizationEpochEffectReceipt(ArtifactCompatibility):
    schema_version: Literal["optimization-epoch-effect-receipt.v1"] = (
        "optimization-epoch-effect-receipt.v1"
    )
    artifact_kind: Literal["optimization-epoch-effect-receipt"] = (
        "optimization-epoch-effect-receipt"
    )
    receipt_id: str
    project_id: str
    epoch_id: str
    epoch_revision: int = Field(ge=1)
    epoch_digest: str
    runtime_bundle_manifest_digest: str
    epoch_fencing_epoch: int = Field(ge=1)
    epoch_claim_digest: str
    effect_kind: Literal["shadow_observation"]
    effect_digest: str
    provider_journal_last_event_digest: str
    receipt_digest: str = ""

    @field_validator("receipt_id", "project_id", "epoch_id")
    @classmethod
    def _identity_is_stable(cls, value: str) -> str:
        return require_machine_id(value, "optimization effect receipt identity")

    @model_validator(mode="after")
    def _verify_receipt(self) -> Self:
        lineage = (
            self.epoch_digest,
            self.runtime_bundle_manifest_digest,
            self.epoch_claim_digest,
            self.effect_digest,
            self.provider_journal_last_event_digest,
        )
        if any(not item.strip() for item in lineage):
            raise ValueError("optimization effect receipt lineage is incomplete")
        if self.receipt_id != stable_id(
            "optimization-epoch-effect-receipt",
            self.effect_kind,
            self.effect_digest,
            self.epoch_claim_digest,
        ):
            raise ValueError("optimization effect receipt identity diverged")
        return fill_artifact_digest(self, "receipt_digest")


class OptimizationStepResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    next_state: EpochState
    reason: str = ""
    dataset_digest: str = ""
    finalist_candidate_digest: str = ""


class OptimizationMaintenanceResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    result_code: MaintenanceResultCode
    epoch: OptimizationEpoch | None = None
    reason: str = ""


def _decode_legacy_policy_lineage(
    value: object,
    *,
    schema_version: str,
    digest_field: str,
    context: object,
) -> object:
    if not isinstance(value, Mapping) or value.get("schema_version") != schema_version:
        return value
    if not isinstance(context, Mapping):
        raise ValueError(
            "trusted legacy optimization context is required"
        )
    lineage_fields = (
        "statistics_policy_digest",
        "evaluator_registry_digest",
        "auto_promotion_policy_digest",
        "runtime_bundle_manifest_digest",
    )
    marker_fields = (
        "legacy_source_digest",
        "legacy_source_schema_version",
        "legacy_source_extensions",
    )
    extensions_value = value.get("extensions", {})
    if not isinstance(extensions_value, Mapping):
        raise ValueError("legacy optimization extensions are invalid")
    extensions_value = dict(extensions_value)
    marker_presence = tuple(field in extensions_value for field in marker_fields)
    lineage_presence = tuple(field in value for field in lineage_fields)
    enriched = all(marker_presence)
    if any(marker_presence) and not enriched:
        raise ValueError("legacy optimization source markers are incomplete")
    if enriched:
        if not all(lineage_presence):
            raise ValueError("legacy optimization policy lineage is incomplete")
        source_extensions_value = extensions_value["legacy_source_extensions"]
        if not isinstance(source_extensions_value, Mapping):
            raise ValueError("legacy optimization source extensions are invalid")
        source_extensions = dict(source_extensions_value)
        if (
            extensions_value["legacy_source_digest"] != value.get(digest_field)
            or extensions_value["legacy_source_schema_version"] != schema_version
            or {
                key: item
                for key, item in extensions_value.items()
                if key not in marker_fields
            }
            != source_extensions
        ):
            raise ValueError("legacy optimization source markers diverged")
        source_payload = {
            str(key): item
            for key, item in value.items()
            if str(key) != digest_field and str(key) not in lineage_fields
        }
        source_payload["extensions"] = source_extensions
    else:
        if any(lineage_presence):
            raise ValueError("legacy optimization policy lineage is untrusted")
        source_extensions = extensions_value
        source_payload = {
            str(key): item
            for key, item in value.items()
            if str(key) != digest_field
        }
    source_digest = str(value.get(digest_field, "")).strip()
    if not source_digest:
        raise ValueError(f"legacy {digest_field} is required")
    expected = canonical_digest(source_payload, CanonicalizationPolicy())
    if source_digest != expected:
        raise ValueError(f"{digest_field} does not match legacy content")
    if value.get("compatibility_mode", "strict") != "strict":
        raise ValueError("legacy compatibility mode must be strict")
    constitution_digest = str(value.get("constitution_digest", ""))
    catalog = context.get("optimization_constitutions")
    constitution = (
        catalog.get(constitution_digest)
        if isinstance(catalog, Mapping)
        else None
    )
    if constitution is None:
        raise ValueError(
            "legacy optimization constitution is unavailable"
        )
    constitution = OptimizationConstitution.model_validate(constitution)
    if (
        str(value.get("candidate_domain_registry_digest", ""))
        != constitution.candidate_domain_registry_digest
    ):
        raise ValueError("legacy optimization constitution lineage diverged")
    manifests = context.get("optimization_legacy_runtime_bundle_manifests")
    legacy_key = f"{schema_version}:{constitution_digest}"
    runtime_manifest = (
        manifests.get(legacy_key)
        if isinstance(manifests, Mapping)
        else None
    )
    if not isinstance(runtime_manifest, str) or not runtime_manifest.strip():
        raise ValueError("legacy optimization runtime bundle is unavailable")
    expected_lineage = {
        "statistics_policy_digest": constitution.statistics_policy_digest,
        "evaluator_registry_digest": constitution.evaluator_registry_digest,
        "auto_promotion_policy_digest": constitution.auto_promotion_policy_digest,
        "runtime_bundle_manifest_digest": runtime_manifest,
    }
    if enriched and any(
        value.get(field) != expected_value
        for field, expected_value in expected_lineage.items()
    ):
        raise ValueError("legacy optimization policy lineage diverged")
    extensions = {
        **source_extensions,
        "legacy_source_digest": source_digest,
        "legacy_source_schema_version": schema_version,
        "legacy_source_extensions": source_extensions,
    }
    return {
        **dict(value),
        **expected_lineage,
        "compatibility_mode": "strict",
        "extensions": extensions,
    }


def _fill_optimization_lineage_digest(
    value: OptimizationTriggerEvent | OptimizationEpoch,
    digest_field: str,
) -> OptimizationTriggerEvent | OptimizationEpoch:
    source_digest = value.extensions.get("legacy_source_digest")
    source_schema = value.extensions.get("legacy_source_schema_version")
    source_extensions = value.extensions.get("legacy_source_extensions")
    if (
        value.schema_version == source_schema
        and isinstance(source_digest, str)
        and isinstance(source_extensions, Mapping)
    ):
        if getattr(value, digest_field) != source_digest:
            raise ValueError(f"{digest_field} diverged from legacy source")
        payload = value.model_dump(exclude={digest_field}, mode="json")
        for field_name in (
            "statistics_policy_digest",
            "evaluator_registry_digest",
            "auto_promotion_policy_digest",
            "runtime_bundle_manifest_digest",
        ):
            payload.pop(field_name)
        payload["extensions"] = dict(source_extensions)
        if canonical_digest(payload, CanonicalizationPolicy()) != source_digest:
            raise ValueError(f"{digest_field} does not match legacy content")
        return value
    return fill_artifact_digest(value, digest_field)


def bundled_optimization_constitutions() -> tuple[OptimizationConstitution, ...]:
    from ai_sdlc.core.stage_review.optimization.defaults import baseline_constitution

    return (baseline_constitution(),)


def bundled_legacy_runtime_bundle_manifests() -> dict[str, str]:
    """旧 Schema 只映射到发布时冻结的兼容身份，绝不借用当前运行时。"""
    constitution = bundled_optimization_constitutions()[0]
    key = f"optimization-trigger-event.v1:{constitution.constitution_digest}"
    epoch_key = f"optimization-epoch.v1:{constitution.constitution_digest}"
    manifest = canonical_digest(
        {
            "compatibility_catalog": "optimization-runtime-legacy.v1",
            "constitution_digest": constitution.constitution_digest,
            "execution_mode": "read-only-migration",
        },
        CanonicalizationPolicy(),
    )
    return {key: manifest, epoch_key: manifest}


def resolve_optimization_constitution(
    constitution_digest: str,
    *,
    configured_constitution: OptimizationConstitution | None = None,
) -> OptimizationConstitution:
    if not constitution_digest.strip():
        raise ValueError("optimization constitution digest is required")
    policies = {
        item.constitution_digest: item
        for item in (
            *bundled_optimization_constitutions(),
            *(() if configured_constitution is None else (configured_constitution,)),
        )
    }
    try:
        return policies[constitution_digest]
    except KeyError as exc:
        raise ValueError("optimization constitution is unavailable") from exc
