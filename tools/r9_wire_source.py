#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one {label} marker, found {count}")
    return text.replace(old, new, 1)


def regex_once(
    text: str,
    pattern: str,
    replacement: str,
    label: str,
    *,
    flags: int = 0,
) -> str:
    updated, count = re.subn(
        pattern,
        lambda _match: replacement,
        text,
        count=1,
        flags=flags,
    )
    if count != 1:
        raise RuntimeError(f"expected one {label} regex match, found {count}")
    return updated


def wire_python_engine() -> None:
    path = ROOT / "syntavra_runtime" / "broker_snapshot_contract.py"
    source = path.read_text(encoding="utf-8")
    source = replace_once(
        source,
        "import stat\n",
        "import stat\nimport struct\n",
        "Python struct import",
    )
    source = regex_once(
        source,
        r"def _json_value\(value: str\) -> Any:\n.*?\n\ndef _project_root\(",
        '''def _float_tag(value: float) -> dict[str, str]:
    if not math.isfinite(value):
        raise BrokerSnapshotError("BROKER_ROW_TYPE_INVALID")
    bits = struct.unpack(">Q", struct.pack(">d", value))[0]
    return {"$f64": f"{bits:016x}"}


def _normalize_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        if -(2**63) <= value <= 2**64 - 1:
            return value
        raise BrokerSnapshotError("BROKER_JSON_INVALID")
    if isinstance(value, float):
        return _float_tag(value)
    if isinstance(value, list):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {
            key: _normalize_json_value(item)
            for key, item in value.items()
        }
    raise BrokerSnapshotError("BROKER_JSON_INVALID")


def _json_value(value: str) -> Any:
    def reject_constant(_: str) -> None:
        raise ValueError("non-finite JSON constant")

    try:
        parsed = json.loads(value, parse_constant=reject_constant)
    except (TypeError, ValueError) as exc:
        raise BrokerSnapshotError("BROKER_JSON_INVALID") from exc
    return _normalize_json_value(parsed)


def _project_root(''',
        "Python typed JSON canonicalization",
        flags=re.DOTALL,
    )
    source = regex_once(
        source,
        r"        normalized = float\(value\)\n        if not math\.isfinite\(normalized\):\n            raise BrokerSnapshotError\(\"BROKER_ROW_TYPE_INVALID\"\)\n        return normalized",
        '''        normalized = float(value)
        return _float_tag(normalized)''',
        "Python REAL canonicalization",
    )
    path.write_text(source, encoding="utf-8", newline="\n")


def wire_python_tests() -> None:
    path = ROOT / "tests" / "runtime" / "test_broker_snapshot_contract_r9.py"
    source = path.read_text(encoding="utf-8")
    source = replace_once(
        source,
        "import sqlite3\n",
        "import sqlite3\nimport struct\n",
        "test struct import",
    )
    source = replace_once(
        source,
        'CONTRACT = ROOT / "contracts" / "state" / "broker-snapshot-v1.json"\n\n\n',
        '''CONTRACT = ROOT / "contracts" / "state" / "broker-snapshot-v1.json"


def _f64(value: float) -> dict[str, str]:
    bits = struct.unpack(">Q", struct.pack(">d", value))[0]
    return {"$f64": f"{bits:016x}"}


''',
        "test float helper",
    )
    source = replace_once(
        source,
        "'{\"state\":\"COMPLETED\",\"job_id\":\"job-1\"}',",
        "'{\"completed_at\":12.25,\"job_id\":\"job-1\",\"state\":\"COMPLETED\"}',",
        "completion float fixture",
    )
    source = replace_once(
        source,
        '''    assert value["tables"]["completion_events"][0]["payload_json"] == {
        "job_id": "job-1",
        "state": "COMPLETED",
    }
''',
        '''    assert value["tables"]["completion_events"][0]["payload_json"] == {
        "completed_at": _f64(12.25),
        "job_id": "job-1",
        "state": "COMPLETED",
    }
''',
        "completion float assertion",
    )
    path.write_text(source, encoding="utf-8", newline="\n")


def wire_contract() -> None:
    path = ROOT / "contracts" / "state" / "broker-snapshot-v1.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    canonicalization = value["canonicalization"]
    canonicalization["real_tag"] = "$f64"
    canonicalization["real_values"] = "ieee754-binary64-lowerhex-tag-v1"
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def wire_rust_engine() -> None:
    path = ROOT / "crates" / "syntavra-cli" / "src" / "broker_snapshot_contract.rs"
    source = path.read_text(encoding="utf-8")
    source = replace_once(
        source,
        "use std::fs;\n",
        "use std::fmt::Write as _;\nuse std::fs;\n",
        "Rust fmt import",
    )
    source = replace_once(
        source,
        "use rusqlite::{Connection, OpenFlags};\n",
        "use rusqlite::{Connection, OpenFlags, OptionalExtension};\n",
        "Rust OptionalExtension import",
    )
    source = regex_once(
        source,
        r"fn percent_encode_path\(path: &Path\) -> Result<String, String> \{.*?\n\}\n\nfn open_database",
        '''fn percent_encode_path(path: &Path) -> Result<String, String> {
    let normalized = path
        .to_str()
        .ok_or_else(|| error("BROKER_DATABASE_PATH_UTF8_INVALID"))?
        .replace('\\\\', "/");
    let mut output = String::with_capacity(normalized.len());
    for byte in normalized.bytes() {
        match byte {
            b'A'..=b'Z'
            | b'a'..=b'z'
            | b'0'..=b'9'
            | b'-'
            | b'.'
            | b'/'
            | b':'
            | b'_'
            | b'~' => output.push(char::from(byte)),
            _ => write!(&mut output, "%{byte:02X}")
                .expect("writing to a String cannot fail"),
        }
    }
    Ok(output)
}

fn open_database''',
        "Rust SQLite URI encoder",
        flags=re.DOTALL,
    )
    source = replace_once(
        source,
        '''        ("REAL", ValueRef::Real(number)) => float_tag(number),
        ("REAL", ValueRef::Integer(number)) => float_tag(number as f64),
''',
        '''        ("REAL", ValueRef::Real(number)) => float_tag(number),
''',
        "Rust strict REAL typing",
    )
    source = replace_once(
        source,
        '''            "trigger" | "view" => return Err(error("BROKER_SCHEMA_OBJECT_MISMATCH")),
            _ => return Err(error("BROKER_SCHEMA_OBJECT_MISMATCH")),
''',
        '''            _ => return Err(error("BROKER_SCHEMA_OBJECT_MISMATCH")),
''',
        "Rust schema object match arms",
    )
    source = replace_once(
        source,
        '''        (_, ValueRef::Blob(_)) => Err(error("BROKER_ROW_TYPE_INVALID")),
        _ => Err(error("BROKER_ROW_TYPE_INVALID")),
''',
        '''        _ => Err(error("BROKER_ROW_TYPE_INVALID")),
''',
        "Rust row-type match arms",
    )
    source = replace_once(
        source,
        "/// sidecar policy, SQLite open mode, schema validation, row normalization, or\n",
        "/// sidecar policy, `SQLite` open mode, schema validation, row normalization, or\n",
        "Rust SQLite documentation markup",
    )
    path.write_text(source, encoding="utf-8", newline="\n")


def wire_router() -> None:
    path = ROOT / "crates" / "syntavra-cli" / "src" / "main.rs"
    source = path.read_text(encoding="utf-8")
    source = replace_once(
        source,
        "mod config_contract;\n",
        "mod broker_snapshot_contract;\nmod config_contract;\n",
        "R9 module",
    )
    source = replace_once(
        source,
        "use config_contract::{default_config_wire, resolve_config_wire, snapshot_json, status_json};\n",
        "use broker_snapshot_contract::snapshot_broker_database_json;\nuse config_contract::{default_config_wire, resolve_config_wire, snapshot_json, status_json};\n",
        "R9 import",
    )
    source = replace_once(
        source,
        '    "  syntavra-rs state inspect <expected-project-id> <project-root>\\n",\n',
        '    "  syntavra-rs state inspect <expected-project-id> <project-root>\\n",\n    "  syntavra-rs state broker-snapshot <expected-project-id> <project-root> <database-path>\\n",\n',
        "R9 usage",
    )
    source = replace_once(
        source,
        "    StateLayout,\n    StateInspect {\n",
        "    StateLayout,\n    BrokerSnapshot {\n        expected_project_id: String,\n        project_root: String,\n        database_path: String,\n    },\n    StateInspect {\n",
        "R9 command variant",
    )
    source = replace_once(
        source,
        '        [state, action] if state == "state" && action == "layout" => Ok(Command::StateLayout),\n',
        '        [state, action] if state == "state" && action == "layout" => Ok(Command::StateLayout),\n        [state, action, expected_project_id, project_root, database_path]\n            if state == "state" && action == "broker-snapshot" =>\n        {\n            Ok(Command::BrokerSnapshot {\n                expected_project_id: expected_project_id.clone(),\n                project_root: project_root.clone(),\n                database_path: database_path.clone(),\n            })\n        }\n',
        "R9 parser",
    )
    source = replace_once(
        source,
        '        Command::StateLayout => println!("{}", state_layout_json()),\n',
        '        Command::StateLayout => println!("{}", state_layout_json()),\n        Command::BrokerSnapshot {\n            expected_project_id,\n            project_root,\n            database_path,\n        } => println!(\n            "{}",\n            snapshot_broker_database_json(\n                &project_root,\n                &database_path,\n                &expected_project_id,\n            )?\n        ),\n',
        "R9 runner",
    )
    source = replace_once(
        source,
        '''        assert_eq!(
            parse_command(&args(&["state", "inspect", "aa", "."])),
            Ok(Command::StateInspect {
                expected_project_id: "aa".to_owned(),
                project_root: ".".to_owned(),
            })
        );
''',
        '''        assert_eq!(
            parse_command(&args(&["state", "inspect", "aa", "."])),
            Ok(Command::StateInspect {
                expected_project_id: "aa".to_owned(),
                project_root: ".".to_owned(),
            })
        );
        assert_eq!(
            parse_command(&args(&[
                "state",
                "broker-snapshot",
                "aa",
                ".",
                ".syntavra/runtime-v3/broker.sqlite3",
            ])),
            Ok(Command::BrokerSnapshot {
                expected_project_id: "aa".to_owned(),
                project_root: ".".to_owned(),
                database_path: ".syntavra/runtime-v3/broker.sqlite3".to_owned(),
            })
        );
''',
        "R9 parser test",
    )
    path.write_text(source, encoding="utf-8", newline="\n")


def wire_aggregate_parity() -> None:
    path = ROOT / "tools" / "run_engine_parity.py"
    source = path.read_text(encoding="utf-8")
    source = replace_once(
        source,
        '        "receipt.inspect",\n        "state.inspect",\n',
        '        "receipt.inspect",\n        "state.broker-snapshot",\n        "state.inspect",\n',
        "R9 aggregate capability",
    )
    source = replace_once(
        source,
        '        "phase": "R0-R8",\n',
        '        "phase": "R0-R9",\n',
        "R9 aggregate phase",
    )
    path.write_text(source, encoding="utf-8", newline="\n")


def main() -> int:
    wire_python_engine()
    wire_python_tests()
    wire_contract()
    wire_rust_engine()
    wire_router()
    wire_aggregate_parity()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
