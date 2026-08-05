#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "crates" / "syntavra-cli" / "src" / "native_fabric_profile.rs"

OLD_STD_IMPORTS = '''use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
'''
NEW_STD_IMPORTS = '''use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::time::{Instant, SystemTime, UNIX_EPOCH};
'''
OLD_CRATE_IMPORT = '''use regex::Regex;
use serde_json::{json, Value};
'''
NEW_CRATE_IMPORT = '''use regex::Regex;
use rusqlite::{params, Connection};
use serde_json::{json, Value};
'''
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
DATABASE_SIGNATURE = "let started = Instant::now();"
HOST_CONTRACT = '''    let host_contract = super::native_expansion::doctor_host_contract(&host);
'''
EVENT_CALL = '''    record_profile_event(
        &database,
        &profile,
        &host,
        selected.len(),
        available.len(),
        started.elapsed().as_secs_f64() * 1000.0,
    )?;
'''
EVENT_CALL_SIGNATURE = "record_profile_event(\n"


def replace_once(source: str, old: str, new: str, label: str) -> tuple[str, bool]:
    if new in source:
        if old in source:
            raise RuntimeError(f"legacy {label} remains beside canonical form")
        return source, False
    if source.count(old) != 1:
        raise RuntimeError(f"{label} token must be unique")
    return source.replace(old, new, 1), True


def repair() -> bool:
    source = PROFILE.read_text(encoding="utf-8")
    rendered = source
    changed = False

    for old, new, label in (
        (OLD_STD_IMPORTS, NEW_STD_IMPORTS, "profile std imports"),
        (OLD_CRATE_IMPORT, NEW_CRATE_IMPORT, "profile crate imports"),
    ):
        rendered, applied = replace_once(rendered, old, new, label)
        changed = changed or applied

    if rendered.count("fn record_profile_event(") == 0:
        if rendered.count(FUNCTION_ANCHOR) != 1:
            raise RuntimeError("profile event function anchor must be unique")
        rendered = rendered.replace(FUNCTION_ANCHOR, EVENT_FUNCTION + FUNCTION_ANCHOR, 1)
        changed = True
    elif rendered.count("fn record_profile_event(") != 1:
        raise RuntimeError("profile event function count invalid")

    if DATABASE_SIGNATURE not in rendered:
        if rendered.count(OLD_DATABASE) != 1:
            raise RuntimeError("profile database timing contract not found")
        rendered = rendered.replace(OLD_DATABASE, NEW_DATABASE, 1)
        changed = True
    elif rendered.count(DATABASE_SIGNATURE) != 1:
        raise RuntimeError("profile timing signature count invalid")

    if rendered.count(EVENT_CALL_SIGNATURE) == 0:
        if rendered.count(HOST_CONTRACT) != 1:
            raise RuntimeError("profile host contract event anchor must be unique")
        rendered = rendered.replace(HOST_CONTRACT, HOST_CONTRACT + EVENT_CALL, 1)
        changed = True
    elif rendered.count(EVENT_CALL_SIGNATURE) != 1:
        raise RuntimeError("profile event call count invalid")

    for token in (
        "use std::time::{Instant, SystemTime, UNIX_EPOCH};",
        "use rusqlite::{params, Connection};",
        "fn record_profile_event(",
        DATABASE_SIGNATURE,
        EVENT_CALL_SIGNATURE,
    ):
        if rendered.count(token) != 1:
            raise RuntimeError(f"profile event invariant failed: {token}")

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
