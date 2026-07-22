from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


@dataclass(frozen=True, slots=True)
class EngineRequest:
    capability: str
    task_id: str
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class EngineResponse:
    engine_id: str
    engine_version: str
    capability: str
    status: str
    payload: Mapping[str, Any]
    evidence_references: tuple[str, ...]
    validator_results: Mapping[str, bool]


class EngineAdapter(Protocol):
    engine_id: str
    engine_version: str
    mode: str

    def execute(self, request: EngineRequest) -> EngineResponse: ...
