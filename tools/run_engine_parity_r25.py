#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from run_engine_parity_r24 import verify as verify_r0_r24
from verify_r25_config_last_good_plan import verify as verify_r25_plan


def verify() -> dict[str, object]:
    previous = verify_r0_r24()
    lifecycle_plan = verify_r25_plan()

    if previous.get("ok") is not True or previous.get("phase") != "R0-R24":
        raise RuntimeError("R0-R24 aggregate parity regression")
    if (
        lifecycle_plan.get("ok") is not True
        or lifecycle_plan.get("phase") != "R25"
        or lifecycle_plan.get("command") != "config.last-good.lifecycle.plan"
        or lifecycle_plan.get("stage") != "shadow"
        or lifecycle_plan.get("apply_authority") != "blocked"
    ):
        raise RuntimeError("R25 config last-good lifecycle plan regression")

    return {
        "ok": True,
        "phase": "R0-R25-plan",
        "reference_engine": "python",
        "candidate_engine": "rust",
        "previous": previous,
        "config_last_good_lifecycle_plan": lifecycle_plan,
        "claim": "CONFIG_LAST_GOOD_PLAN_PARITY_PROVEN_R25",
        "full_parity_claim": "FULL_PARITY_NOT_PROVEN",
        "mutation_authority": "blocked",
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
