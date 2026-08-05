#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "crates" / "syntavra-cli" / "src" / "native_fabric_profile.rs"

PATH_IMPORT = "use std::path::{Path, PathBuf};\n"
TIME_IMPORT = "use std::time::{Instant, SystemTime, UNIX_EPOCH};\n"
REGEX_IMPORT = "use regex::Regex;\n"
SQLITE_IMPORT = "use rusqlite::{params, Connection};\n"
EVENT_FUNCTION = '''fn record_profile_event(
    connection: &Connection,
    profile: &str,
    host: &str,
    selected: usize,
    available: usize,
    latency_ms: f64,
) -> Result<(), String> {
    let created_at = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("FABRIC_PROFILE_CLOCK_FAILED:{error}"))?
        .as_secs_f64();
    let metadata = format!(
        "{{\"available\": {available}, \"selected\": {selected}}}"
    );
    connection
        .execute(
            "INSERT INTO fabric_events(\
                event_type,family,host,raw_bytes,visible_bytes,latency_ms,\
                success,cache_hit,metadata_json,created_at\
             ) VALUES(?,?,?,?,?,?,?,?,?,?)",
            params![
                "profile",
                profile,
                host,
                0_i64,
                0_i64,
                latency_ms.max(0.0),
                1_i64,
                0_i64,
                metadata,
                created_at,
            ],
        )
        .map_err(|error| format!("FABRIC_PROFILE_EVENT_INSERT_FAILED:{error}"))?;
    Ok(())
}

'''
FUNCTION_SIGNATURE = "fn record_profile_event(\n"
FUNCTION_ANCHOR = "pub fn supports(command: &[String]) -> bool {\n"
OLD_DATABASE = '''    let _database = super::native_fabric_doctor::open_database(
        &state_root.join("competitive-fabric.sqlite3"),
    )?;
'''
NEW_DATABASE = '''    let database = super::native_fabric_doctor::open_database(
        &state_root.join("competitive-fabric.sqlite3"),
    )?;
    let started = Instant::now();
'''
DATABASE_SIGNATURE = "    let started = Instant::now();\n"
HOST_CONTRACT = "    let host_contract = super::native_expansion::doctor_host_contract(&host);\n"
EVENT_CALL = '''    record_profile_event(
        &database,
        &profile,
        &host,
        selected.len(),
        available.len(),
        started.elapsed().as_secs_f64() * 1000.0,
    )?;
'''
EVENT_CALL_SIGNATURE = "    record_profile_event(\n"


def ensure_line_after(
    source: str,
    *,
    line: str,
    anchor: str,
    label: str,
) -> tuple[str, bool]:
    count = source.count(line)
    if count == 1:
        return source, False
    if count != 0:
        raise RuntimeError(f"{label} count invalid: {count}")
    if source.count(anchor) != 1:
        raise RuntimeError(f"{label} anchor must be unique")
    return source.replace(anchor, anchor + line, 1), True


def repair() -> bool:
    source = PROFILE.read_text(encoding="utf-8")
    rendered = source
    changed = False

    rendered, applied = ensure_line_after(
        rendered,
        line=TIME_IMPORT,
        anchor=PATH_IMPORT,
        label="profile time import",
    )
    changed = changed or applied
    rendered, applied = ensure_line_after(
        rendered,
        line=SQLITE_IMPORT,
        anchor=REGEX_IMPORT,
        label="profile rusqlite import",
    )
    changed = changed or applied

    function_count = rendered.count(FUNCTION_SIGNATURE)
    if function_count == 0:
        if rendered.count(FUNCTION_ANCHOR) != 1:
            raise RuntimeError("profile event function anchor must be unique")
        rendered = rendered.replace(FUNCTION_ANCHOR, EVENT_FUNCTION + FUNCTION_ANCHOR, 1)
        changed = True
    elif function_count != 1:
        raise RuntimeError(f"profile event function count invalid: {function_count}")

    timing_count = rendered.count(DATABASE_SIGNATURE)
    if timing_count == 0:
        if rendered.count(OLD_DATABASE) != 1:
            raise RuntimeError("profile database timing contract not found")
        rendered = rendered.replace(OLD_DATABASE, NEW_DATABASE, 1)
        changed = True
    elif timing_count != 1:
        raise RuntimeError(f"profile timing signature count invalid: {timing_count}")

    call_count = rendered.count(EVENT_CALL_SIGNATURE)
    if call_count == 0:
        if rendered.count(HOST_CONTRACT) != 1:
            raise RuntimeError("profile host contract event anchor must be unique")
        rendered = rendered.replace(HOST_CONTRACT, HOST_CONTRACT + EVENT_CALL, 1)
        changed = True
    elif call_count != 1:
        raise RuntimeError(f"profile event call count invalid: {call_count}")

    invariants = {
        "time_import": rendered.count(TIME_IMPORT),
        "sqlite_import": rendered.count(SQLITE_IMPORT),
        "event_function": rendered.count(FUNCTION_SIGNATURE),
        "timing": rendered.count(DATABASE_SIGNATURE),
        "event_call": rendered.count(EVENT_CALL_SIGNATURE),
    }
    if invariants != {
        "time_import": 1,
        "sqlite_import": 1,
        "event_function": 1,
        "timing": 1,
        "event_call": 1,
    }:
        raise RuntimeError(f"profile event invariant failed: {invariants}")

    if changed:
        PROFILE.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def main() -> int:
    changed = repair()
    print(
        json.dumps(
            {
                "changed": changed,
                "ok": True,
                "surface": "native-fabric-profile-event",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
