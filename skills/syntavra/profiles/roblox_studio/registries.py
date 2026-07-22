from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable

from .errors import CapabilityError


@dataclass(slots=True)
class EngineSpec:
    engine_id: str
    version: str
    capabilities: tuple[str, ...]
    live_or_simulated: str
    health: str
    trust_level: str
    sandbox_level: str
    privacy_level: str
    input_schema: str
    output_schema: str
    timeout: int
    retry_policy: dict[str, int]
    fallbacks: tuple[str, ...]
    certification: str
    last_verified: int


@dataclass(slots=True)
class ModelSpec:
    model_id: str
    provider: str
    context_capacity: int
    tool_support: bool
    multimodal_support: bool
    structured_output_support: bool
    observed_latency_ms: float
    observed_failure_rate: float
    input_price: float | None
    output_price: float | None
    cached_input_price: float | None
    price_source: str | None
    price_retrieved_at: int | None
    health: str
    quota: int
    task_family_certification: tuple[str, ...]
    local: bool = False
    failures: int = 0
    circuit_open_until: int = 0

    def pricing_is_stale(self, now: int | None = None, maximum_age_days: int = 30) -> bool:
        if self.local:
            return False
        if self.price_retrieved_at is None:
            return True
        current = int(time.time()) if now is None else int(now)
        return current - self.price_retrieved_at > maximum_age_days * 86400


class EngineRegistry:
    def __init__(self, engines: Iterable[EngineSpec]) -> None:
        self.engines = {engine.engine_id: engine for engine in engines}

    def select(self, capability: str, *, require_live: bool = False) -> EngineSpec:
        candidates = [engine for engine in self.engines.values() if capability in engine.capabilities and engine.health == "HEALTHY"]
        if require_live:
            candidates = [engine for engine in candidates if engine.live_or_simulated == "LIVE"]
        if not candidates:
            raise CapabilityError(f"no eligible engine for {capability}")
        return sorted(candidates, key=lambda item: (item.live_or_simulated != "LIVE", item.engine_id))[0]


class ModelRegistry:
    def __init__(self, models: Iterable[ModelSpec]) -> None:
        self.models = {model.model_id: model for model in models}

    def healthy(self, now: int | None = None) -> list[ModelSpec]:
        current = int(time.time()) if now is None else int(now)
        return [model for model in self.models.values() if model.health == "HEALTHY" and model.circuit_open_until <= current and model.quota > 0]

    def record_failure(self, model_id: str, now: int | None = None) -> None:
        model = self.models[model_id]
        model.failures += 1
        if model.failures >= 3:
            current = int(time.time()) if now is None else int(now)
            model.circuit_open_until = current + 60


def default_engines(capability_ids: Iterable[str]) -> EngineRegistry:
    ids = tuple(capability_ids)
    groups = {
        "local_runtime": tuple(item for item in ids if item in {"project_delta", "context_selection", "model_routing", "workflow_execution", "telemetry_replay"}),
        "studio_bridge": tuple(item for item in ids if item not in {"creator_store_search", "asset_analysis", "asset_sanitization", "blender_job", "animation_generation", "animation_retarget"}),
        "luau_validator": tuple(item for item in ids if item in {"read_script", "require_graph", "remote_security_review", "performance_review"}),
        "playtest": tuple(item for item in ids if "playtest" in item or "logs" in item or item == "device_simulation"),
        "viewport": ("capture_viewport",),
        "creator_store": ("creator_store_search",),
        "asset_analysis": ("asset_analysis", "asset_sanitization"),
        "animation_worker": ("animation_generation", "animation_retarget"),
        "blender_worker": ("blender_job",),
        "device_simulator": ("device_simulation",),
        "datastore_validator": tuple(item for item in ids if item.startswith("datastore_")),
    }
    now = int(time.time())
    return EngineRegistry([
        EngineSpec(key, "1.0.0", tuple(values), "SIMULATED" if key not in {"local_runtime", "studio_bridge", "luau_validator", "playtest"} else "ADAPTER", "HEALTHY", "PROJECT_ATTESTED", "PROJECT_WRITE", "PROJECT", f"schemas/{key}.input.json", f"schemas/{key}.output.json", 30, {"max_attempts": 2}, (), "INTERNALLY_VERIFIED", now)
        for key, values in groups.items()
    ])
