#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "crates" / "syntavra-cli" / "src" / "native_session_public.rs"

HELPER_TOKEN = "fn python_export_payload_hash("
IMPORT_TOKEN = 'let calculated = python_export_payload_hash(raw, saved)?;'

HELPER = r'''fn python_export_payload_hash(raw: &str, saved_hash: &str) -> Result<String, String> {
    if saved_hash.len() != 64 || !saved_hash.bytes().all(|value| value.is_ascii_hexdigit()) {
        return Err("SESSION_EXPORT_HASH_INVALID".to_owned());
    }
    let candidates = [
        (
            format!(",\"export_hash\":\"{saved_hash}\","),
            ",",
        ),
        (
            format!("{{\"export_hash\":\"{saved_hash}\","),
            "{",
        ),
        (
            format!(",\"export_hash\":\"{saved_hash}\"}}"),
            "}",
        ),
    ];
    let mut stripped = None;
    for (needle, replacement) in candidates {
        let count = raw.match_indices(&needle).count();
        if count > 1 || (count == 1 && stripped.is_some()) {
            return Err("SESSION_EXPORT_HASH_FIELD_AMBIGUOUS".to_owned());
        }
        if count == 1 {
            stripped = Some(raw.replacen(&needle, replacement, 1));
        }
    }
    let payload = stripped.ok_or_else(|| "SESSION_EXPORT_HASH_FIELD_NOT_CANONICAL".to_owned())?;
    Ok(sha256_hex(payload.as_bytes()))
}

'''

OLD_IMPORT = '''    let bytes = fs::read(&input).map_err(|error| format!("SESSION_IMPORT_READ_FAILED:{error}"))?;
    let mut value: Value =
        serde_json::from_slice(&bytes).map_err(|_| "SESSION_IMPORT_JSON_INVALID".to_owned())?;
    let object = value
        .as_object_mut()
        .ok_or_else(|| "SESSION_IMPORT_OBJECT_REQUIRED".to_owned())?;
    let saved_hash = object.remove("export_hash");
    let calculated = sha256_hex(&canonical_bytes(&value)?);
    if saved_hash.as_ref().and_then(Value::as_str) != Some(calculated.as_str()) {
        return Err("SESSION_EXPORT_HASH_MISMATCH".to_owned());
    }
'''

NEW_IMPORT = '''    let bytes = fs::read(&input).map_err(|error| format!("SESSION_IMPORT_READ_FAILED:{error}"))?;
    let raw = std::str::from_utf8(&bytes)
        .map_err(|_| "SESSION_IMPORT_UTF8_INVALID".to_owned())?
        .trim();
    let mut value: Value =
        serde_json::from_slice(&bytes).map_err(|_| "SESSION_IMPORT_JSON_INVALID".to_owned())?;
    let object = value
        .as_object_mut()
        .ok_or_else(|| "SESSION_IMPORT_OBJECT_REQUIRED".to_owned())?;
    let saved_hash = object.remove("export_hash");
    let saved = saved_hash
        .as_ref()
        .and_then(Value::as_str)
        .ok_or_else(|| "SESSION_EXPORT_HASH_MISSING".to_owned())?;
    let calculated = python_export_payload_hash(raw, saved)?;
    if saved != calculated {
        return Err("SESSION_EXPORT_HASH_MISMATCH".to_owned());
    }
'''

IMPORT_ANCHOR = "fn import(\n"


def repair() -> bool:
    source = TARGET.read_text(encoding="utf-8")
    rendered = source
    changed = False

    helper_count = rendered.count(HELPER_TOKEN)
    if helper_count == 0:
        anchor_count = rendered.count(IMPORT_ANCHOR)
        if anchor_count != 1:
            raise RuntimeError(f"session import anchor count must be 1, got {anchor_count}")
        rendered = rendered.replace(IMPORT_ANCHOR, HELPER + IMPORT_ANCHOR, 1)
        changed = True
    elif helper_count != 1:
        raise RuntimeError(f"session export hash helper count invalid: {helper_count}")

    old_count = rendered.count(OLD_IMPORT)
    new_count = rendered.count(NEW_IMPORT)
    if old_count == 1 and new_count == 0:
        rendered = rendered.replace(OLD_IMPORT, NEW_IMPORT, 1)
        changed = True
    elif old_count != 0 or new_count != 1:
        raise RuntimeError(
            "session import hash block must be exactly legacy or canonical; "
            f"legacy={old_count}, canonical={new_count}"
        )

    invariants = {
        HELPER_TOKEN: rendered.count(HELPER_TOKEN),
        IMPORT_TOKEN: rendered.count(IMPORT_TOKEN),
        "SESSION_EXPORT_HASH_FIELD_AMBIGUOUS": rendered.count("SESSION_EXPORT_HASH_FIELD_AMBIGUOUS"),
    }
    invalid = {key: value for key, value in invariants.items() if value != 1}
    if invalid:
        raise RuntimeError(f"session export hash repair invariant failed: {invalid}")

    if changed:
        TARGET.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def main() -> int:
    changed = repair()
    print(json.dumps({"changed": changed, "ok": True, "surface": "session-export-hash"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
