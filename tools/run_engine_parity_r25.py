#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import sys
from collections.abc import Callable
from contextlib import redirect_stdout
from pathlib import Path
from typing import TypeVar

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from run_engine_parity_r24 import verify as verify_r0_r24
from verify_r25_config_last_good_apply import verify as verify_r25_apply
from verify_r25_config_last_good_plan import verify as verify_r25_plan

T = TypeVar("T")


def _without_stdout(callback: Callable[[], T]) -> T:
    """Keep nested verifier diagnostics out of the canonical aggregate JSON stream."""
    with redirect_stdout(io.StringIO()):
        return callback()


def verify() -> dict[str, object]:
    previous = _without_stdout(verify_r0_r24)
    lifecycle_plan = _without_stdout(verify_r25_plan)
    lifecycle_apply = _without_stdout(verify_r25_apply)

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
    if (
        lifecycle_apply.get("ok") is not True
        or lifecycle_apply.get("phase") != "R25"
        or lifecycle_apply.get("command") != "config.last-good.lifecycle.apply"
        or lifecycle_apply.get("stage") != "bounded-shadow"
        or lifecycle_apply.get("apply_authority") != "bounded-shadow"
        or lifecycle_apply.get("public_routing") != "blocked"
    ):
        raise RuntimeError("R25 config last-good lifecycle apply regression")

    return {
        "ok": True,
        "phase": "R0-R25-apply",
        "reference_engine": "python",
        "candidate_engine": "rust",
        "previous": previous,
        "config_last_good_lifecycle_plan": lifecycle_plan,
        "config_last_good_lifecycle_apply": lifecycle_apply,
        "claim": "CONFIG_LAST_GOOD_APPLY_PARITY_PROVEN_R25",
        "full_parity_claim": "FULL_PARITY_NOT_PROVEN",
        "mutation_authority": "bounded-shadow",
        "public_routing": "blocked",
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
