#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from run_engine_parity_r23 import verify as verify_r0_r23
from verify_r24_static_cli_parity import verify as verify_r24_static_cli


def verify() -> dict[str, object]:
    previous = verify_r0_r23()
    static_cli = verify_r24_static_cli()
    if previous.get("ok") is not True or previous.get("phase") != "R0-R23":
        raise RuntimeError("R0-R23 aggregate parity regression")
    if static_cli.get("ok") is not True or static_cli.get("phase") != "R24":
        raise RuntimeError("R24 static read-only CLI parity regression")
    return {
        "ok": True,
        "phase": "R0-R24",
        "reference_engine": "python",
        "candidate_engine": "rust",
        "previous": previous,
        "static_read_only_cli": static_cli,
        "claim": "RUST_STATIC_READ_ONLY_CLI_PARITY_PROVEN_R24",
        "full_parity_claim": "FULL_PARITY_NOT_PROVEN",
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
