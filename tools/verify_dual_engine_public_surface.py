#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from export_python_surface import export_surface as export_python_surface
from export_rust_surface import export_surface as export_rust_surface

CONTRACT = ROOT / "contracts" / "engine" / "dual-engine-public-surface-v2.json"
FULL_CLAIM = "FULL_DUAL_ENGINE_PARITY_PROVEN"
INCOMPLETE_CLAIM = "DUAL_ENGINE_PARITY_INCOMPLETE"
EXPECTED_PUBLIC_COMMAND_COUNT = 245
FROZEN_NATIVE_COMMAND_COUNT = 174
FROZEN_BRIDGE_COMMAND_COUNT = 0
PROMOTED_NATIVE_COMMAND_COUNT = 245
PROMOTED_BRIDGE_COMMAND_COUNT = 0

# Capabilities 243-270 and 276-280 are deliberately composed behind the existing
# public surface. These modules are internal implementation owners and therefore
# must not create synthetic dual-engine public-module growth. Keep this list
# exact so any unrelated Python module addition still fails the snapshot closed.
_INTERNAL_POST_COMPLETION_MODULES = frozenset(
    {
        "syntavra_runtime.post_completion_common",
        "syntavra_runtime.post_completion_context",
        "syntavra_runtime.post_completion_evidence",
        "syntavra_runtime.post_completion_product",
        "syntavra_runtime.post_completion_runtime",
    }
)


def _command_digest(commands: list[str]) -> str:
    payload = json.dumps(
        commands,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _inventory_state(total: int, native_count: int, bridge_count: int) -> str:
    if total != EXPECTED_PUBLIC_COMMAND_COUNT:
        raise RuntimeError(
            "dual-engine public command count drift: "
            f"expected {EXPECTED_PUBLIC_COMMAND_COUNT}, got {total}"
        )
    if (native_count, bridge_count) == (
        FROZEN_NATIVE_COMMAND_COUNT,
        FROZEN_BRIDGE_COMMAND_COUNT,
    ):
        return "frozen"
    if (native_count, bridge_count) == (
        PROMOTED_NATIVE_COMMAND_COUNT,
        PROMOTED_BRIDGE_COMMAND_COUNT,
    ):
        return "promoted"
    raise RuntimeError(
        "dual-engine inventory must remain atomic: "
        f"native={native_count}, bridged={bridge_count}; accepted states are "
        f"{FROZEN_NATIVE_COMMAND_COUNT}/{FROZEN_BRIDGE_COMMAND_COUNT} or "
        f"{PROMOTED_NATIVE_COMMAND_COUNT}/{PROMOTED_BRIDGE_COMMAND_COUNT}"
    )


def _public_python_module_count(python_surface: dict[str, object]) -> int:
    modules = python_surface.get("modules")
    if not isinstance(modules, list) or not all(isinstance(value, str) for value in modules):
        raise RuntimeError("Python module inventory is invalid")
    module_set = set(modules)
    missing_internal = sorted(_INTERNAL_POST_COMPLETION_MODULES - module_set)
    if missing_internal:
        raise RuntimeError(
            "internal post-completion module inventory drift: "
            f"missing {missing_internal!r}"
        )
    return len(modules) - len(_INTERNAL_POST_COMPLETION_MODULES)


def verify(*, require_full: bool = False) -> dict[str, object]:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    python_surface = export_python_surface()
    rust_surface = export_rust_surface()

    if contract.get("schema_version") != 2:
        raise RuntimeError("dual-engine contract schema must be 2")
    if contract.get("product") != "Syntavra":
        raise RuntimeError("dual-engine contract product mismatch")
    if contract.get("product_version") != "0.0.1":
        raise RuntimeError("product version must remain 0.0.1")
    if contract.get("release_channel") != "pre-release":
        raise RuntimeError("release channel must remain pre-release")

    commands = list(python_surface.get("cli_commands", []))
    python_row = contract.get("python_surface")
    rust_row = contract.get("rust_surface")
    if not isinstance(python_row, dict) or not isinstance(rust_row, dict):
        raise RuntimeError("dual-engine surface summaries are missing")

    public_python_module_count = _public_python_module_count(python_surface)
    expected_python = {
        "module_count": public_python_module_count,
        "public_command_count": len(commands),
        "command_paths_sha256": _command_digest(commands),
        "digest_encoding": "canonical-json-array-utf8",
    }
    for key, value in expected_python.items():
        if python_row.get(key) != value:
            raise RuntimeError(
                f"Python public surface drift for {key}: expected {value!r}, got {python_row.get(key)!r}"
            )

    native = rust_row.get("native_public_commands")
    bridged = rust_row.get("python_launcher_bridge_commands")
    if not isinstance(native, list) or not all(isinstance(value, str) for value in native):
        raise RuntimeError("native Rust command inventory is invalid")
    if not isinstance(bridged, list) or not all(isinstance(value, str) for value in bridged):
        raise RuntimeError("Python-launcher bridge inventory is invalid")
    if native != sorted(set(native)):
        raise RuntimeError("native Rust command inventory must be sorted and unique")
    if bridged != sorted(set(bridged)):
        raise RuntimeError("bridge command inventory must be sorted and unique")

    command_set = set(commands)
    native_set = set(native)
    bridge_set = set(bridged)
    if native_set - command_set:
        raise RuntimeError(f"unknown native Rust commands: {sorted(native_set - command_set)!r}")
    if bridge_set - command_set:
        raise RuntimeError(f"unknown bridge commands: {sorted(bridge_set - command_set)!r}")
    if native_set & bridge_set:
        raise RuntimeError("native and bridge command inventories overlap")

    total = len(commands)
    native_count = len(native)
    bridge_count = len(bridged)
    inventory_state = _inventory_state(total, native_count, bridge_count)
    missing_set = command_set - native_set
    if inventory_state == "frozen":
        if bridge_set:
            raise RuntimeError("frozen Remaining-71 state must not use a Python launcher bridge")
        if len(missing_set) != total - FROZEN_NATIVE_COMMAND_COUNT:
            raise RuntimeError("frozen Remaining-71 identity count drift")
    else:
        if native_set != command_set or bridge_set:
            raise RuntimeError("promoted dual-engine state must be the exact Python public set with zero bridge")

    expected_rust = {
        "module_count": rust_surface["module_count"],
        "native_public_command_count": native_count,
        "python_launcher_bridge_command_count": bridge_count,
        "missing_native_public_command_count": total - native_count,
        "native_coverage_ppm": native_count * 1_000_000 // total,
    }
    for key, value in expected_rust.items():
        if rust_row.get(key) != value:
            raise RuntimeError(
                f"Rust public surface drift for {key}: expected {value!r}, got {rust_row.get(key)!r}"
            )

    full = inventory_state == "promoted"
    claim = contract.get("claim")
    if full and claim != FULL_CLAIM:
        raise RuntimeError("complete dual-engine coverage must carry the full claim")
    if not full and claim != INCOMPLETE_CLAIM:
        raise RuntimeError("incomplete dual-engine coverage must fail closed")
    if require_full and not full:
        raise RuntimeError(
            f"full dual-engine parity not reached: native={native_count}/{total}, "
            f"bridged={bridge_count}, missing_native={total - native_count}"
        )

    return {
        "ok": True,
        "claim": claim,
        "full": full,
        "inventory_state": inventory_state,
        "python": {
            "module_count": public_python_module_count,
            "internal_post_completion_module_count": len(_INTERNAL_POST_COMPLETION_MODULES),
            "public_command_count": total,
            "command_paths_sha256": _command_digest(commands),
        },
        "rust": {
            "module_count": rust_surface["module_count"],
            "native_public_command_count": native_count,
            "launcher_bridge_command_count": bridge_count,
            "missing_native_public_command_count": total - native_count,
            "native_coverage_ppm": native_count * 1_000_000 // total,
        },
        "policy": contract["policy"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the complete Python/Rust public command inventory."
    )
    parser.add_argument(
        "--require-full",
        action="store_true",
        help="fail unless every Python public command is independently native in Rust",
    )
    args = parser.parse_args()
    print(json.dumps(verify(require_full=args.require_full), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
