#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "crates" / "syntavra-cli" / "src" / "native_hook.rs"

MODULES = '''#[path = "native_hook_evidence.rs"]
mod native_hook_evidence;
#[path = "native_hook_output.rs"]
mod native_hook_output;
'''
MODULE_ANCHOR = "use serde_json::{json, Map, Value};\n"
LEGACY_POST = re.compile(
    r"\nfn post_tool\(payload: &Map<String, Value>\) -> Value \{.*?\n\}\n\nfn cache_health",
    re.DOTALL,
)
LEGACY_EXECUTE = '        "post" => Ok(post_tool(&payload)),\n'
CANONICAL_EXECUTE = (
    '        "post" => native_hook_output::post_tool(&payload, project_root, state_root),\n'
)
LEGACY_STATE = "    _state_root: &Path,\n"
CANONICAL_STATE = "    state_root: &Path,\n"


def repair() -> bool:
    source = TARGET.read_text(encoding="utf-8")
    rendered = source
    changed = False

    module_count = rendered.count(MODULES)
    if module_count == 0:
        if rendered.count(MODULE_ANCHOR) != 1:
            raise RuntimeError("hook output module anchor must be unique")
        rendered = rendered.replace(MODULE_ANCHOR, MODULE_ANCHOR + "\n" + MODULES, 1)
        changed = True
    elif module_count != 1:
        raise RuntimeError(f"hook output module block count invalid: {module_count}")

    legacy_matches = len(LEGACY_POST.findall(rendered))
    if legacy_matches == 1:
        rendered = LEGACY_POST.sub("\nfn cache_health", rendered, count=1)
        changed = True
    elif legacy_matches != 0:
        raise RuntimeError(f"legacy hook post block count invalid: {legacy_matches}")

    if LEGACY_EXECUTE in rendered:
        if rendered.count(LEGACY_EXECUTE) != 1:
            raise RuntimeError("legacy hook post execute count invalid")
        rendered = rendered.replace(LEGACY_EXECUTE, CANONICAL_EXECUTE, 1)
        changed = True
    elif rendered.count(CANONICAL_EXECUTE) != 1:
        raise RuntimeError("canonical hook post execute branch missing")

    if LEGACY_STATE in rendered:
        if rendered.count(LEGACY_STATE) != 1:
            raise RuntimeError("legacy hook state parameter count invalid")
        rendered = rendered.replace(LEGACY_STATE, CANONICAL_STATE, 1)
        changed = True
    elif rendered.count(CANONICAL_STATE) != 1:
        raise RuntimeError("canonical hook state parameter missing")

    invariants = {
        "modules": rendered.count(MODULES),
        "execute": rendered.count(CANONICAL_EXECUTE),
        "state": rendered.count(CANONICAL_STATE),
        "legacy_post": len(LEGACY_POST.findall(rendered)),
    }
    if invariants != {"modules": 1, "execute": 1, "state": 1, "legacy_post": 0}:
        raise RuntimeError(f"hook output wiring invariant failed: {invariants}")

    if changed:
        TARGET.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def main() -> int:
    changed = repair()
    print(
        json.dumps(
            {"changed": changed, "ok": True, "surface": "hook-output"},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
