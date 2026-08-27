#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
SURFACE = ROOT / "contracts/engine/dual-engine-public-surface-v2.json"

surface = json.loads(SURFACE.read_text(encoding="utf-8"))
python_surface = surface.get("python_surface") or {}
rust_surface = surface.get("rust_surface") or {}
count = int(python_surface.get("public_command_count", -1))
native_count = int(rust_surface.get("native_public_command_count", -1))
missing_count = int(rust_surface.get("missing_native_public_command_count", -1))
claim = str(surface.get("claim") or "")
if count != 245:
    raise AssertionError(f"unexpected canonical Python public route count: {count}")
if native_count != 245 or missing_count != 0:
    raise AssertionError(
        f"unexpected canonical Rust route inventory: native={native_count} missing={missing_count}"
    )
if claim != "FULL_DUAL_ENGINE_PARITY_PROVEN":
    raise AssertionError(f"unexpected canonical dual-engine claim: {claim!r}")

text = README.read_text(encoding="utf-8")
old_surface = (
    "The complete Python surface currently contains 257 public command paths. "
    "Full dual-engine parity is tracked against all 257 paths in `contracts/engine/dual-engine-public-surface-v2.json`; "
)
new_surface = (
    "The canonical installed Python surface currently contains 245 executable public command paths. "
    "Full dual-engine parity inventory is tracked against those 245 paths in `contracts/engine/dual-engine-public-surface-v2.json`; "
)
if text.count(old_surface) != 1:
    raise AssertionError("README public-surface count text drift")
text = text.replace(old_surface, new_surface, 1)

old_claim = "The full claim remains:\n\n```text\nDUAL_ENGINE_PARITY_INCOMPLETE\n```"
new_claim = (
    "The canonical route-coverage claim is:\n\n"
    "```text\nFULL_DUAL_ENGINE_PARITY_PROVEN\n```\n\n"
    "This route-level parity claim does not reactivate Rust feature/parity development and does not grant production-promotion credit. "
    "Rust development/resume authority and the 174/245 production-promotion boundary are governed separately."
)
if text.count(old_claim) != 1:
    raise AssertionError("README dual-engine claim text drift")
text = text.replace(old_claim, new_claim, 1)
README.write_text(text, encoding="utf-8")

print(json.dumps({
    "canonical_python_public_command_count": count,
    "canonical_rust_native_public_command_count": native_count,
    "canonical_missing_native_public_command_count": missing_count,
    "canonical_dual_engine_claim": claim,
    "source": "contracts/engine/dual-engine-public-surface-v2.json",
    "readme_synchronized": True,
}, indent=2))
