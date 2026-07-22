from __future__ import annotations

from dataclasses import dataclass

from .errors import CapabilityError
from .registries import ModelRegistry, ModelSpec


@dataclass(frozen=True, slots=True)
class RouteDecision:
    route: str
    model_id: str | None
    reason: str


def select_route(*, deterministic: bool, privacy_class: str, task_family: str, registry: ModelRegistry, require_multimodal: bool = False) -> RouteDecision:
    if deterministic:
        return RouteDecision("DETERMINISTIC", None, "task has a certified zero-model path")
    candidates = registry.healthy()
    if privacy_class == "LOCAL_ONLY":
        candidates = [model for model in candidates if model.local]
    if require_multimodal:
        candidates = [model for model in candidates if model.multimodal_support]
    candidates = [model for model in candidates if task_family in model.task_family_certification and model.structured_output_support]
    if not candidates:
        raise CapabilityError("no healthy certified model route")
    def score(model: ModelSpec) -> tuple[float, float, str]:
        price = (model.input_price or 0.0) + (model.output_price or 0.0)
        return (model.observed_failure_rate, model.observed_latency_ms + price * 1000, model.model_id)
    selected = min(candidates, key=score)
    return RouteDecision("LOCAL_MODEL" if selected.local else "CLOUD_MODEL", selected.model_id, "lowest certified health-adjusted score")
