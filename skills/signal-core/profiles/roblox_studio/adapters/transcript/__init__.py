from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ..base import EngineRequest, EngineResponse
from ...errors import ValidationError


@dataclass(frozen=True, slots=True)
class TranscriptEvent:
    sequence: int
    event_type: str
    payload: Mapping[str, Any]


class TranscriptAdapter:
    engine_id = "studio_bridge"
    engine_version = "1.0.0-transcript"
    mode = "TRANSCRIPT"

    def __init__(self, events: Iterable[TranscriptEvent]) -> None:
        self.events = tuple(events)
        sequences = [event.sequence for event in self.events]
        if sequences != list(range(len(sequences))):
            raise ValidationError("transcript event ordering is invalid")

    def execute(self, request: EngineRequest) -> EngineResponse:
        matching = [event for event in self.events if event.event_type == "response" and event.payload.get("capability") == request.capability]
        if not matching:
            raise ValidationError("transcript has no matching response")
        payload = dict(matching[0].payload)
        if payload.get("status") not in {"SUCCEEDED", "FAILED", "BLOCKED", "PARTIAL"}:
            raise ValidationError("transcript response status is invalid")
        return EngineResponse(self.engine_id, self.engine_version, request.capability, str(payload["status"]), payload, tuple(payload.get("evidence_references", ())), {"transcript_schema": True})
