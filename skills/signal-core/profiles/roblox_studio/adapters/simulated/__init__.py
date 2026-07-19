from __future__ import annotations

import hashlib
import json

from ..base import EngineRequest, EngineResponse


class SimulatedStudioAdapter:
    engine_id = "studio_bridge"
    engine_version = "1.0.0-simulated"
    mode = "SIMULATED"

    def execute(self, request: EngineRequest) -> EngineResponse:
        canonical = json.dumps(dict(request.payload), sort_keys=True, separators=(",", ":"))
        artifact_hash = hashlib.sha256((request.capability + canonical).encode()).hexdigest()
        payload = {
            "task_id": request.task_id,
            "capability": request.capability,
            "artifact_hash": artifact_hash,
            "mode": self.mode,
            "postcondition": "simulated-ok",
        }
        return EngineResponse(self.engine_id, self.engine_version, request.capability, "SUCCEEDED", payload, (f"simulated:{artifact_hash}",), {"response_schema": True, "artifact_hash": True})
