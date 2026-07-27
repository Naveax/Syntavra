#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one {label} marker, found {count}")
    return text.replace(old, new, 1)


def expose_r9_helpers() -> None:
    path = ROOT / "crates" / "syntavra-cli" / "src" / "broker_snapshot_contract.rs"
    source = path.read_text(encoding="utf-8")
    for name in (
        "contract",
        "contract_string",
        "contract_u64",
        "contract_array",
        "canonical_project_root",
        "relative_database_path",
        "percent_encode_path",
        "schema_objects",
        "table_columns",
        "indexes",
        "foreign_keys",
        "broker_schema_version",
        "table_rows",
    ):
        source = replace_once(
            source,
            f"fn {name}(",
            f"pub(crate) fn {name}(",
            f"R9 helper {name}",
        )
    path.write_text(source, encoding="utf-8", newline="\n")


def enable_backup_feature() -> None:
    path = ROOT / "crates" / "syntavra-cli" / "Cargo.toml"
    source = path.read_text(encoding="utf-8")
    source = replace_once(
        source,
        'rusqlite = { version = "0.32.1", features = ["bundled"] }',
        'rusqlite = { version = "0.32.1", features = ["backup", "bundled"] }',
        "rusqlite backup feature",
    )
    path.write_text(source, encoding="utf-8", newline="\n")


def wire_cli() -> None:
    path = ROOT / "crates" / "syntavra-cli" / "src" / "main.rs"
    source = path.read_text(encoding="utf-8")
    source = replace_once(
        source,
        "mod broker_snapshot_contract;\n",
        "mod broker_live_snapshot_contract;\nmod broker_snapshot_contract;\n",
        "R10 module",
    )
    source = replace_once(
        source,
        "use broker_snapshot_contract::snapshot_broker_database_json;\n",
        "use broker_live_snapshot_contract::snapshot_live_broker_database_json;\n"
        "use broker_snapshot_contract::snapshot_broker_database_json;\n",
        "R10 import",
    )
    source = replace_once(
        source,
        '    "  syntavra-rs state broker-snapshot <expected-project-id> <project-root> <database-path>\\n",\n',
        '    "  syntavra-rs state broker-live-snapshot <expected-project-id> <project-root> <database-path>\\n",\n'
        '    "  syntavra-rs state broker-snapshot <expected-project-id> <project-root> <database-path>\\n",\n',
        "R10 usage",
    )
    source = replace_once(
        source,
        "    BrokerSnapshot {\n",
        "    BrokerLiveSnapshot {\n"
        "        expected_project_id: String,\n"
        "        project_root: String,\n"
        "        database_path: String,\n"
        "    },\n"
        "    BrokerSnapshot {\n",
        "R10 command variant",
    )
    source = replace_once(
        source,
        '''        [state, action, expected_project_id, project_root, database_path]
            if state == "state" && action == "broker-snapshot" =>
        {
''',
        '''        [state, action, expected_project_id, project_root, database_path]
            if state == "state" && action == "broker-live-snapshot" =>
        {
            Ok(Command::BrokerLiveSnapshot {
                expected_project_id: expected_project_id.clone(),
                project_root: project_root.clone(),
                database_path: database_path.clone(),
            })
        }
        [state, action, expected_project_id, project_root, database_path]
            if state == "state" && action == "broker-snapshot" =>
        {
''',
        "R10 parser",
    )
    source = replace_once(
        source,
        '''        Command::BrokerSnapshot {
            expected_project_id,
            project_root,
            database_path,
        } => println!(
''',
        '''        Command::BrokerLiveSnapshot {
            expected_project_id,
            project_root,
            database_path,
        } => println!(
            "{}",
            snapshot_live_broker_database_json(
                &project_root,
                &database_path,
                &expected_project_id,
            )?
        ),
        Command::BrokerSnapshot {
            expected_project_id,
            project_root,
            database_path,
        } => println!(
''',
        "R10 runner",
    )
    source = replace_once(
        source,
        '''        assert_eq!(
            parse_command(&args(&[
                "state",
                "broker-snapshot",
''',
        '''        assert_eq!(
            parse_command(&args(&[
                "state",
                "broker-live-snapshot",
                "aa",
                ".",
                ".syntavra/runtime-v3/broker.sqlite3",
            ])),
            Ok(Command::BrokerLiveSnapshot {
                expected_project_id: "aa".to_owned(),
                project_root: ".".to_owned(),
                database_path: ".syntavra/runtime-v3/broker.sqlite3".to_owned(),
            })
        );
        assert_eq!(
            parse_command(&args(&[
                "state",
                "broker-snapshot",
''',
        "R10 parser test",
    )
    path.write_text(source, encoding="utf-8", newline="\n")


def wire_capabilities() -> None:
    path = ROOT / "crates" / "syntavra-contracts" / "src" / "lib.rs"
    source = path.read_text(encoding="utf-8")
    capability = '''    Capability {
        name: "state.broker-live-snapshot",
        maturity: "preview",
        mutation: "read-only",
    },
'''
    source = replace_once(
        source,
        '''    Capability {
        name: "state.broker-snapshot",
''',
        capability + '''    Capability {
        name: "state.broker-snapshot",
''',
        "R10 capability",
    )
    source = replace_once(
        source,
        '    "capability=state.broker-snapshot|preview|read-only\\n",\n',
        '    "capability=state.broker-live-snapshot|preview|read-only\\n",\n'
        '    "capability=state.broker-snapshot|preview|read-only\\n",\n',
        "R10 descriptor capability",
    )
    source = replace_once(
        source,
        '        assert!(capabilities_json().contains("\\\"name\\\":\\\"state.broker-snapshot\\\""));\n',
        '        assert!(capabilities_json().contains("\\\"name\\\":\\\"state.broker-live-snapshot\\\""));\n'
        '        assert!(capabilities_json().contains("\\\"name\\\":\\\"state.broker-snapshot\\\""));\n',
        "R10 capability assertion",
    )
    path.write_text(source, encoding="utf-8", newline="\n")

    descriptor = ROOT / "contracts" / "engine" / "rust-contract-v1.txt"
    text = descriptor.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "capability=state.broker-snapshot|preview|read-only\n",
        "capability=state.broker-live-snapshot|preview|read-only\n"
        "capability=state.broker-snapshot|preview|read-only\n",
        "R10 text capability",
    )
    descriptor.write_text(text, encoding="utf-8", newline="\n")


def wire_aggregate_phase() -> None:
    path = ROOT / "tools" / "run_engine_parity.py"
    source = path.read_text(encoding="utf-8")
    source = replace_once(
        source,
        '        "state.broker-snapshot",\n',
        '        "state.broker-live-snapshot",\n        "state.broker-snapshot",\n',
        "R10 aggregate capability",
    )
    source = replace_once(
        source,
        '        "phase": "R0-R9",\n',
        '        "phase": "R0-R10",\n',
        "R10 aggregate phase",
    )
    path.write_text(source, encoding="utf-8", newline="\n")


def main() -> int:
    expose_r9_helpers()
    enable_backup_feature()
    wire_cli()
    wire_capabilities()
    wire_aggregate_phase()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
