from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class TelemetryEvent:
    task_id: str
    phase: str
    status: str
    timestamp_ns: int
    metrics: dict[str, Any]


class TelemetryRecorder:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, task_id: str, phase: str, status: str, **metrics: Any) -> TelemetryEvent:
        event = TelemetryEvent(task_id, phase, status, time.time_ns(), metrics)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event), sort_keys=True, ensure_ascii=False) + "\n")
        return event

    def replay(self, task_id: str) -> tuple[TelemetryEvent, ...]:
        if not self.path.exists():
            return ()
        events = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            if value["task_id"] == task_id:
                events.append(TelemetryEvent(**value))
        return tuple(events)
