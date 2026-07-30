#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from run_engine_parity_r25 import verify as verify_r0_r25
from verify_full_parity_catalog import verify as verify_catalog
from verify_r25_r37_full_parity import verify as verify_r25_r37


def verify() -> dict[str, object]:
    previous = verify_r0_r25()
    runtime = verify_r25_r37()
    catalog = verify_catalog()
    if previous.get("ok") is not True:
        raise RuntimeError("R0-R25 aggregate parity regression")
    if runtime.get("ok") is not True or runtime.get("claim") != "FULL_PARITY_PROVEN":
        raise RuntimeError("R25-R37 exact runtime parity regression")
    if catalog.get("ok") is not True or catalog.get("claim") != "FULL_PARITY_PROVEN":
        raise RuntimeError("R37 catalog certification regression")
    return {
        "ok": True,
        "phase": "R0-R37",
        "program": "PYTHON_RUST_FULL_PARITY",
        "reference_engine": "python",
        "candidate_engine": "rust",
        "previous": previous,
        "runtime": runtime,
        "catalog": catalog,
        "claim": "FULL_PARITY_PROVEN",
        "product_version": "0.0.1",
        "release_channel": "pre-release",
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
