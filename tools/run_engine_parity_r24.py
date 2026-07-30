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
from verify_r24_config_explain import verify as verify_r24_config_explain
from verify_r24_config_show import verify as verify_r24_config_show
from verify_r24_config_validate import verify as verify_r24_config_validate
from verify_r24_static_cli_parity import verify as verify_r24_static_cli


def verify() -> dict[str, object]:
    previous = verify_r0_r23()
    static_cli = verify_r24_static_cli()
    config_validate = verify_r24_config_validate()
    config_explain = verify_r24_config_explain()
    config_show = verify_r24_config_show()
    if previous.get("ok") is not True or previous.get("phase") != "R0-R23":
        raise RuntimeError("R0-R23 aggregate parity regression")
    if static_cli.get("ok") is not True or static_cli.get("phase") != "R24":
        raise RuntimeError("R24 static read-only CLI parity regression")
    if (
        config_validate.get("ok") is not True
        or config_validate.get("phase") != "R24"
        or config_validate.get("command") != "config.validate"
    ):
        raise RuntimeError("R24 config.validate parity regression")
    if (
        config_explain.get("ok") is not True
        or config_explain.get("phase") != "R24"
        or config_explain.get("command") != "config.explain"
        or config_explain.get("capability") != "config.explain"
    ):
        raise RuntimeError("R24 config.explain parity regression")
    if (
        config_show.get("ok") is not True
        or config_show.get("phase") != "R24"
        or config_show.get("command") != "config.show"
        or config_show.get("capability") != "config.show"
    ):
        raise RuntimeError("R24 config.show parity regression")
    return {
        "ok": True,
        "phase": "R0-R24",
        "reference_engine": "python",
        "candidate_engine": "rust",
        "previous": previous,
        "static_read_only_cli": static_cli,
        "config_validate": config_validate,
        "config_explain": config_explain,
        "config_show": config_show,
        "claim": "RUST_READ_ONLY_CLI_PARITY_EXPANDED_R24",
        "full_parity_claim": "FULL_PARITY_NOT_PROVEN",
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
