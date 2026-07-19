from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SupportStatus(StrEnum):
    SIMULATED_ONLY = "SIMULATED_ONLY"
    ADAPTER_IMPLEMENTED = "ADAPTER_IMPLEMENTED"
    TRANSCRIPT_VERIFIED = "TRANSCRIPT_VERIFIED"
    LIVE_VERIFIED = "LIVE_VERIFIED"
    PRODUCTION_GATED = "PRODUCTION_GATED"


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    capability_id: str
    version: str
    description: str
    dependencies: tuple[str, ...]
    conflicts: tuple[str, ...]
    required_evidence: tuple[str, ...]
    required_validators: tuple[str, ...]
    minimum_trust: str
    minimum_sandbox: str
    privacy_requirements: tuple[str, ...]
    budget_profile: str
    engine_requirements: tuple[str, ...]
    rollback_requirements: tuple[str, ...]
    live_support_status: SupportStatus
    execution_contract: str
    positive_test: str
    negative_test: str


_CAPABILITY_ROWS = [
    ("inspect_project", (), (), ("definition", "configuration"), ("capability",), "studio_bridge"),
    ("inspect_selection", ("inspect_project",), (), ("definition",), ("capability",), "studio_bridge"),
    ("read_script", ("inspect_project",), (), ("implementation",), ("script_syntax",), "studio_bridge"),
    ("write_script", ("read_script",), ("read_only",), ("implementation", "rollback"), ("script_syntax", "artifact_hash"), "studio_bridge"),
    ("execute_luau", ("read_script",), (), ("implementation", "runtime_observation"), ("luau_diagnostics",), "studio_bridge"),
    ("start_playtest", ("inspect_project",), (), ("test",), ("playtest",), "playtest"),
    ("stop_playtest", ("start_playtest",), (), ("runtime_observation",), ("playtest",), "playtest"),
    ("read_client_logs", ("start_playtest",), (), ("runtime_observation", "failure"), ("log_expectations",), "playtest"),
    ("read_server_logs", ("start_playtest",), (), ("runtime_observation", "failure"), ("log_expectations",), "playtest"),
    ("capture_viewport", ("inspect_project",), (), ("runtime_observation",), ("artifact_hash",), "viewport"),
    ("creator_store_search", (), (), ("dependency",), ("response_schema",), "creator_store"),
    ("asset_analysis", ("creator_store_search",), (), ("dependency", "security_boundary"), ("asset_integrity",), "asset_analysis"),
    ("asset_import", ("asset_analysis",), (), ("dependency", "rollback"), ("asset_integrity", "artifact_hash"), "studio_bridge"),
    ("asset_sanitization", ("asset_analysis",), (), ("security_boundary",), ("asset_integrity",), "asset_analysis"),
    ("datamodel_graph", ("inspect_project",), (), ("definition", "dependency"), ("response_schema",), "studio_bridge"),
    ("require_graph", ("read_script",), (), ("dependency", "caller"), ("script_syntax",), "luau_validator"),
    ("remote_security_review", ("require_graph",), (), ("security_boundary", "caller"), ("remote_validation",), "luau_validator"),
    ("performance_review", ("read_script",), (), ("implementation", "runtime_observation"), ("luau_diagnostics",), "luau_validator"),
    ("ui_analysis", ("inspect_project",), (), ("definition", "runtime_observation"), ("response_schema",), "studio_bridge"),
    ("ui_edit", ("ui_analysis",), (), ("implementation", "rollback"), ("artifact_hash",), "studio_bridge"),
    ("animation_analysis", ("inspect_project",), (), ("definition",), ("response_schema",), "studio_bridge"),
    ("animation_generation", ("animation_analysis",), (), ("implementation",), ("artifact_hash",), "animation_worker"),
    ("animation_retarget", ("animation_analysis",), (), ("implementation", "validator"), ("artifact_hash",), "animation_worker"),
    ("blender_job", ("asset_analysis",), (), ("implementation",), ("artifact_hash",), "blender_worker"),
    ("device_simulation", ("start_playtest",), (), ("test", "runtime_observation"), ("playtest",), "device_simulator"),
    ("datastore_schema", ("inspect_project",), (), ("configuration", "security_boundary"), ("response_schema",), "datastore_validator"),
    ("datastore_migration_plan", ("datastore_schema",), (), ("historical_decision", "rollback"), ("migration",), "datastore_validator"),
    ("datastore_migration_execute", ("datastore_migration_plan",), (), ("runtime_observation", "rollback"), ("migration", "rollback"), "datastore_validator"),
    ("project_delta", ("inspect_project",), (), ("historical_decision",), ("artifact_hash",), "local_runtime"),
    ("context_selection", ("project_delta",), (), ("definition", "implementation", "validator"), ("context",), "local_runtime"),
    ("model_routing", ("context_selection",), (), ("configuration",), ("budget", "capability"), "local_runtime"),
    ("workflow_execution", ("model_routing",), (), ("implementation", "validator", "rollback"), ("workflow",), "local_runtime"),
    ("telemetry_replay", ("workflow_execution",), (), ("runtime_observation", "test"), ("telemetry",), "local_runtime"),
]


def default_capabilities() -> dict[str, CapabilitySpec]:
    result: dict[str, CapabilitySpec] = {}
    for capability_id, deps, conflicts, evidence, validators, engine in _CAPABILITY_ROWS:
        status = SupportStatus.ADAPTER_IMPLEMENTED if engine in {"local_runtime", "studio_bridge", "luau_validator", "playtest"} else SupportStatus.SIMULATED_ONLY
        result[capability_id] = CapabilitySpec(
            capability_id=capability_id,
            version="2.0.0",
            description=capability_id.replace("_", " ").title(),
            dependencies=tuple(deps),
            conflicts=tuple(conflicts),
            required_evidence=tuple(evidence),
            required_validators=tuple(validators),
            minimum_trust="PROJECT_ATTESTED",
            minimum_sandbox="PROJECT_WRITE" if any(word in capability_id for word in ("write", "import", "execute", "edit", "migration_execute")) else "READ_ONLY",
            privacy_requirements=("PROJECT_ISOLATION",),
            budget_profile="high_impact" if any(word in capability_id for word in ("write", "execute", "migration", "blender", "generation")) else "standard",
            engine_requirements=(engine,),
            rollback_requirements=("checkpoint",) if any(word in capability_id for word in ("write", "import", "execute", "edit", "migration_execute")) else (),
            live_support_status=status,
            execution_contract=f"contracts/{capability_id}.v2",
            positive_test=f"test_{capability_id}_positive",
            negative_test=f"test_{capability_id}_negative",
        )
    return result
