#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUST = ROOT / "crates" / "syntavra-cli" / "src" / "native_fabric_installations.rs"
TEST = ROOT / "tests" / "runtime" / "test_native_fabric_installations_r38.py"

HOST_SCHEMA_START = "CREATE TABLE IF NOT EXISTS host_install_transactions("
HOST_SCHEMA_END = "ON host_install_transactions(host,scope,created_at);\\\n"
HOST_SCHEMA_FUNCTION = '''
fn initialize_host_schema(connection: &Connection) -> Result<(), String> {
    connection
        .execute_batch(
            "CREATE TABLE IF NOT EXISTS host_install_transactions(\
                transaction_id TEXT PRIMARY KEY,host TEXT NOT NULL,scope TEXT NOT NULL,root TEXT NOT NULL,\
                status TEXT NOT NULL,manifest_json TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL\
             );\
             CREATE INDEX IF NOT EXISTS host_install_host_idx \
                ON host_install_transactions(host,scope,created_at);",
        )
        .map_err(|error| format!("FABRIC_INSTALLATIONS_HOST_SCHEMA_FAILED:{error}"))
}

'''
ROWS_ANCHOR = "fn rows(\n"
OLD_EXECUTE = '''    let selected = configured.unwrap_or_else(|| bundled_skill_root(project_root));
    validate_skill_root(&selected)?;

    let requested_limit = option_value(arguments, "--limit")?
'''
NEW_EXECUTE = '''    let selected = configured.unwrap_or_else(|| bundled_skill_root(project_root));
    validate_skill_root(&selected)?;
    initialize_host_schema(&database)?;

    let requested_limit = option_value(arguments, "--limit")?
'''
EXECUTE_SIGNATURE = "initialize_host_schema(&database)?;"
OLD_TEST = '''        assert _snapshot(project)["count"] == 0
'''
NEW_TEST = '''        with sqlite3.connect(_database(project)) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert "host_install_transactions" not in tables
            assert connection.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            ).fetchone()[0] == "2"
'''


def remove_early_host_schema(source: str) -> tuple[str, bool]:
    helper_index = source.find("fn initialize_host_schema(")
    search_end = helper_index if helper_index >= 0 else len(source)
    schema_index = source.find(HOST_SCHEMA_START, 0, search_end)
    if schema_index < 0:
        return source, False
    line_start = source.rfind("\n", 0, schema_index) + 1
    end_index = source.find(HOST_SCHEMA_END, schema_index, search_end)
    if end_index < 0:
        raise RuntimeError("fabric installations early host schema end marker missing")
    end_index += len(HOST_SCHEMA_END)
    return source[:line_start] + source[end_index:], True


def repair_rust() -> bool:
    source = RUST.read_text(encoding="utf-8")
    rendered, changed = remove_early_host_schema(source)

    if "fn initialize_host_schema(" not in rendered:
        if rendered.count(ROWS_ANCHOR) != 1:
            raise RuntimeError("fabric installations rows anchor must be unique")
        rendered = rendered.replace(ROWS_ANCHOR, HOST_SCHEMA_FUNCTION + ROWS_ANCHOR, 1)
        changed = True
    elif rendered.count("fn initialize_host_schema(") != 1:
        raise RuntimeError("fabric installations host schema helper count invalid")

    if EXECUTE_SIGNATURE not in rendered:
        if rendered.count(OLD_EXECUTE) != 1:
            raise RuntimeError("fabric installations constructor ordering contract not found")
        rendered = rendered.replace(OLD_EXECUTE, NEW_EXECUTE, 1)
        changed = True
    elif rendered.count(EXECUTE_SIGNATURE) != 1:
        raise RuntimeError("host transaction schema initialization count invalid")

    base_prefix = rendered.split("fn initialize_host_schema(", 1)[0]
    if HOST_SCHEMA_START in base_prefix:
        raise RuntimeError("host transaction schema still initializes before skill validation")
    if rendered.count(HOST_SCHEMA_START) != 1:
        raise RuntimeError("host transaction schema definition count invalid")
    if rendered.count(EXECUTE_SIGNATURE) != 1:
        raise RuntimeError("host transaction schema initialization count invalid")

    if changed:
        RUST.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_test() -> bool:
    source = TEST.read_text(encoding="utf-8")
    if NEW_TEST in source:
        if OLD_TEST in source:
            raise RuntimeError("legacy incomplete-skill assertion remains")
        return False
    if source.count(OLD_TEST) != 1:
        raise RuntimeError("incomplete-skill assertion contract not found")
    TEST.write_text(source.replace(OLD_TEST, NEW_TEST, 1), encoding="utf-8", newline="\n")
    return True


def repair() -> bool:
    rust_changed = repair_rust()
    test_changed = repair_test()
    return rust_changed or test_changed


def main() -> int:
    changed = repair()
    print(
        json.dumps(
            {
                "changed": changed,
                "ok": True,
                "surface": "native-fabric-installations-constructor-ordering",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
