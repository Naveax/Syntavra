#!/usr/bin/env python3
"""Fair grouped benchmark contract. Unavailable third-party packs are never fabricated."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Configuration:
    name: str
    available: bool
    reason: str

CONFIGURATIONS = (
    Configuration("plain_agent", False, "provider/model execution not configured"),
    Configuration("context_efficiency_pack", False, "third-party pack execution not configured"),
    Configuration("enterprise_intelligence_pack", False, "LSP/SCIP and external search execution not configured"),
    Configuration("native_agent_pack", False, "provider agents are not configured in CI"),
    Configuration("roblox_production_pack", False, "live Roblox tools are not configured in CI"),
    Configuration("full_rival_mega_pack", False, "combined external pack unavailable"),
    Configuration("syntavra", True, "simulated local vertical slice is available"),
)

if __name__ == "__main__":
    print(json.dumps({"configurations":[asdict(item) for item in CONFIGURATIONS],"scoring_locked":True,"fabricated_results":False},indent=2))
