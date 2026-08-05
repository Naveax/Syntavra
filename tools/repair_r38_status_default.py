#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "crates" / "syntavra-cli" / "src" / "native_status.rs"
MODULE = ROOT / "crates" / "syntavra-cli" / "src" / "native_status_default.rs"

MODULE_TOKEN = "mod native_status_default;"
CALL_TOKEN = "native_status_default::snapshot("
MODULE_ANCHOR = "const VERSION: &str = \"0.0.1\";\n"
MODULE_INSERT = '''#[path = "native_status_default.rs"]
mod native_status_default;

'''
MODULE_SESSION_ARGUMENT = "    session_memory: Value,\n"
MODULE_SESSION_FIELD = '        "session_memory": session_memory,\n'
STATUS_SESSION_ARGUMENT = "            memory_status(state_root, &stats)?,\n"
LEGACY_DEFAULT = '''    let value = if focused.is_empty() {
        json!({
            "product": "Syntavra",
            "version": VERSION,
            "channel": CHANNEL,
            "role": "token-and-context-optimization-skill",
            "doctor": doctor.value,
            "stats": stats,
            "savings": evidence["token_attribution"],
            "profile": profile,
            "readiness": {
                "ok": false,
                "claim": "DAILY_CODING_AGENT_READINESS_NOT_PROVEN",
            },
            "evidence": evidence,
            "primary_workflow": ["setup", "status", "run", "prove"],
        })
    } else {
'''
CANONICAL_DEFAULT = '''    let value = if focused.is_empty() {
        native_status_default::snapshot(
            project_root,
            state_root,
            doctor.value.clone(),
            stats.clone(),
            profile.clone(),
            evidence.clone(),
        )
    } else {
'''


def repair_module() -> bool:
    source = MODULE.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for token in (MODULE_SESSION_ARGUMENT, MODULE_SESSION_FIELD):
        count = rendered.count(token)
        if count == 1:
            rendered = rendered.replace(token, "", 1)
            changed = True
        elif count != 0:
            raise RuntimeError(f"default status session token count invalid: {token!r}={count}")
    if "session_memory" in rendered:
        raise RuntimeError("default status must not expose session_memory")
    if changed:
        MODULE.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_target() -> bool:
    source = TARGET.read_text(encoding="utf-8")
    rendered = source
    changed = False

    module_count = rendered.count(MODULE_TOKEN)
    if module_count == 0:
        if rendered.count(MODULE_ANCHOR) != 1:
            raise RuntimeError("native status default module anchor missing")
        rendered = rendered.replace(MODULE_ANCHOR, MODULE_INSERT + MODULE_ANCHOR, 1)
        changed = True
    elif module_count != 1:
        raise RuntimeError(f"native status default module count invalid: {module_count}")

    if STATUS_SESSION_ARGUMENT in rendered:
        rendered = rendered.replace(STATUS_SESSION_ARGUMENT, "", 1)
        changed = True

    legacy_count = rendered.count(LEGACY_DEFAULT)
    canonical_count = rendered.count(CANONICAL_DEFAULT)
    if legacy_count == 1 and canonical_count == 0:
        rendered = rendered.replace(LEGACY_DEFAULT, CANONICAL_DEFAULT, 1)
        changed = True
    elif legacy_count != 0 or canonical_count != 1:
        if rendered.count(CALL_TOKEN) != 1:
            raise RuntimeError(
                "native status default branch must be legacy or canonical; "
                f"legacy={legacy_count}, canonical={canonical_count}, call={rendered.count(CALL_TOKEN)}"
            )

    if rendered.count(MODULE_TOKEN) != 1 or rendered.count(CALL_TOKEN) != 1:
        raise RuntimeError("native default status wiring invariant failed")
    if STATUS_SESSION_ARGUMENT in rendered:
        raise RuntimeError("native default status call still passes session memory")
    if changed:
        TARGET.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair() -> bool:
    if not MODULE.is_file():
        raise RuntimeError("native default status module is missing")
    return repair_module() | repair_target()


def main() -> int:
    changed = repair()
    print(json.dumps({"changed": changed, "ok": True, "surface": "status-default"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
