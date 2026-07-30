#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from run_engine_parity_r25 import verify as verify_r0_r25_plan
from verify_r25_config_last_good_apply import verify as verify_r25_apply


def verify() -> dict[str, object]:
    previous = verify_r0_r25_plan()
    atomic_apply = verify_r25_apply()
    if previous.get("ok") is not True or previous.get("phase") != "R0-R25-plan":
        raise RuntimeError("R0-R25 plan aggregate parity regression")
    if (
        atomic_apply.get("ok") is not True
        or atomic_apply.get("phase") != "R25"
        or atomic_apply.get("command") != "config.last-good.lifecycle.apply"
        or atomic_apply.get("stage") != "shadow"
        or atomic_apply.get("public_cli_exposed") is not False
    ):
        raise RuntimeError("R25 atomic apply parity regression")
    return {
        "ok": True,
        "phase": "R0-R25-atomic-apply",
        "reference_engine": "python",
        "candidate_engine": "rust",
        "previous": previous,
        "config_last_good_atomic_apply": atomic_apply,
        "claim": "CONFIG_LAST_GOOD_ATOMIC_APPLY_PARITY_PROVEN_R25",
        "full_parity_claim": "FULL_PARITY_NOT_PROVEN",
        "public_mutation_route": "blocked",
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
