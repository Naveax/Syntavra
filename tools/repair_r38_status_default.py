#!/usr/bin/env python3
from __future__ import annotations

import json
import re
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
SESSION_ARGUMENT_PATTERN = re.compile(r"(?m)^\s*session_memory\s*:\s*Value\s*,\s*\n")
SESSION_FIELD_PATTERN = re.compile(
    r'(?m)^\s*"session_memory"\s*:\s*session_memory\s*,\s*\n'
)


def repair_module() -> bool:
    source = MODULE.read_text(encoding="utf-8")
    rendered, argument_count = SESSION_ARGUMENT_PATTERN.subn("", source)
    rendered, field_count = SESSION_FIELD_PATTERN.subn("", rendered)
    if argument_count > 1 or field_count > 1:
        raise RuntimeError(
            "default status session-memory tokens must be absent or unique: "
            f"argument={argument_count}, field={field_count}"
        )
    if SESSION_ARGUMENT_PATTERN.search(rendered) or SESSION_FIELD_PATTERN.search(rendered):
        raise RuntimeError("default status still exposes a session-memory parameter or field")
    changed = rendered != source
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

    session_call_count = rendered.count(STATUS_SESSION_ARGUMENT)
    if session_call_count == 1:
        rendered = rendered.replace(STATUS_SESSION_ARGUMENT, "", 1)
        changed = True
    elif session_call_count > 1:
        raise RuntimeError(f"native default status session argument count invalid: {session_call_count}")

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
