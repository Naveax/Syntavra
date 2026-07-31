#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from run_engine_parity import verify as verify_r0_r22
from verify_full_parity_catalog import verify as verify_r23_catalog


def verify() -> dict[str, object]:
    engine = verify_r0_r22()
    catalog = verify_r23_catalog()
    if engine.get("ok") is not True or engine.get("phase") != "R0-R22":
        raise RuntimeError("R0-R22 aggregate parity regression")
    catalog_phase = catalog.get("phase")
    if catalog.get("ok") is not True or catalog_phase not in {"R23", "R37"}:
        raise RuntimeError("R23 full parity catalog regression")
    return {
        "ok": True,
        "phase": "R0-R23",
        "reference_engine": "python",
        "candidate_engine": "rust",
        "engine_parity": engine,
        "full_parity_program": catalog,
        "catalog_certification_phase": catalog_phase,
        "claim": "FULL_PARITY_PROGRAM_ACTIVE_R23_R37",
        "full_parity_claim": "FULL_PARITY_NOT_PROVEN",
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
