#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    source = target.read_text(encoding="utf-8")
    if new in source:
        return
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one patch anchor, found {count}")
    target.write_text(source.replace(old, new, 1), encoding="utf-8", newline="\n")


replace_once(
    "crates/syntavra-cli/src/config_contract.rs",
    'const WIRE_HEADER: &str = "R6CFG1";\n',
    'const WIRE_HEADER: &str = "R6CFG1";\npub const MAX_EXPLAIN_PATH_BYTES: usize = 512;\n',
)

explain_function = r'''pub fn explain_config_wire_json(input: &[u8], path_input: &[u8]) -> Result<String, String> {
    if path_input.is_empty() {
        return Err("CONFIG_EXPLAIN_PATH_EMPTY".to_owned());
    }
    if path_input.len() > MAX_EXPLAIN_PATH_BYTES {
        return Err("CONFIG_EXPLAIN_PATH_TOO_LARGE".to_owned());
    }
    let path = std::str::from_utf8(path_input)
        .map_err(|_| "CONFIG_EXPLAIN_PATH_UTF8_INVALID".to_owned())?;
    if path.chars().any(char::is_control) {
        return Err("CONFIG_EXPLAIN_PATH_CONTROL_INVALID".to_owned());
    }
    if path.split('.').any(str::is_empty) {
        return Err("CONFIG_EXPLAIN_PATH_SEGMENT_EMPTY".to_owned());
    }

    let snapshot = resolve_config_wire(input)?;
    if let Some(item) = snapshot
        .provenance
        .iter()
        .rev()
        .find(|item| item.path == path)
    {
        return Ok(format!(
            "{{\"path\":{},\"value\":{},\"source\":{},\"scope\":{}}}",
            json_string(path),
            scalar_json(&item.value),
            json_string(&item.source),
            json_string(&item.scope)
        ));
    }
    Ok(format!(
        "{{\"found\":false,\"path\":{}}}",
        json_string(path)
    ))
}

'''
replace_once(
    "crates/syntavra-cli/src/config_contract.rs",
    "pub fn snapshot_json(snapshot: &ConfigSnapshot) -> Result<String, String> {\n",
    explain_function + "pub fn snapshot_json(snapshot: &ConfigSnapshot) -> Result<String, String> {\n",
)
replace_once(
    "crates/syntavra-cli/src/config_contract.rs",
    "        default_config_wire, resolve_config_wire, snapshot_json, status_json, ConfigScalar,\n",
    "        default_config_wire, explain_config_wire_json, resolve_config_wire, snapshot_json,\n        status_json, ConfigScalar,\n",
)
explain_tests = r'''    #[test]
    fn explains_latest_provenance_and_missing_paths() {
        let wire = concat!(
            "R6CFG1\n",
            "phase\t0\n",
            "a\tproject\t70726f6a6563742d636f6e666967\t72756e74696d652e70726f66696c65\ts\t636f6d70616374\n"
        );
        let found = explain_config_wire_json(wire.as_bytes(), b"runtime.profile")
            .expect("found explain result");
        assert!(found.contains("\"value\":\"compact\""));
        assert!(found.contains("\"source\":\"project-config\""));
        assert!(found.contains("\"scope\":\"project\""));

        let missing = explain_config_wire_json(wire.as_bytes(), b"missing.value")
            .expect("missing explain result");
        assert_eq!(missing, "{\"found\":false,\"path\":\"missing.value\"}");
    }

    #[test]
    fn rejects_invalid_explain_paths() {
        assert_eq!(
            explain_config_wire_json(default_config_wire(), b".runtime"),
            Err("CONFIG_EXPLAIN_PATH_SEGMENT_EMPTY".to_owned())
        );
        assert_eq!(
            explain_config_wire_json(default_config_wire(), b"runtime\nprofile"),
            Err("CONFIG_EXPLAIN_PATH_CONTROL_INVALID".to_owned())
        );
    }

'''
replace_once(
    "crates/syntavra-cli/src/config_contract.rs",
    "    #[test]\n    fn emits_valid_contract_json() {\n",
    explain_tests + "    #[test]\n    fn emits_valid_contract_json() {\n",
)

replace_once(
    "crates/syntavra-cli/src/main.rs",
    "use config_contract::{default_config_wire, resolve_config_wire, snapshot_json, status_json};\n",
    "use config_contract::{\n    default_config_wire, explain_config_wire_json, resolve_config_wire, snapshot_json, status_json,\n};\n",
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    '    "  syntavra-rs config resolve <config-wire-hex>\\n",\n',
    '    "  syntavra-rs config explain <config-wire-hex> <path-utf8-hex>\\n",\n    "  syntavra-rs config resolve <config-wire-hex>\\n",\n',
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    "    ConfigResolve(String),\n",
    "    ConfigExplain {\n        wire_hex: String,\n        path_hex: String,\n    },\n    ConfigResolve(String),\n",
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    '''        [config, action, wire] if config == "config" && action == "resolve" => {
            Ok(Command::ConfigResolve(wire.clone()))
        }
''',
    '''        [config, action, wire_hex, path_hex]
            if config == "config" && action == "explain" =>
        {
            Ok(Command::ConfigExplain {
                wire_hex: wire_hex.clone(),
                path_hex: path_hex.clone(),
            })
        }
        [config, action, wire] if config == "config" && action == "resolve" => {
            Ok(Command::ConfigResolve(wire.clone()))
        }
''',
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    '''        Command::ConfigResolve(encoded) => {
            let wire = decode_hex(&encoded)?;
            let snapshot = resolve_config_wire(&wire)?;
            println!("{}", snapshot_json(&snapshot)?);
        }
''',
    '''        Command::ConfigExplain { wire_hex, path_hex } => {
            let wire = decode_hex(&wire_hex)?;
            let path = decode_hex(&path_hex)?;
            println!("{}", explain_config_wire_json(&wire, &path)?);
        }
        Command::ConfigResolve(encoded) => {
            let wire = decode_hex(&encoded)?;
            let snapshot = resolve_config_wire(&wire)?;
            println!("{}", snapshot_json(&snapshot)?);
        }
''',
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    '''        assert_eq!(
            parse_command(&args(&["config", "resolve", "00"])),
            Ok(Command::ConfigResolve("00".to_owned()))
        );
''',
    '''        assert_eq!(
            parse_command(&args(&["config", "resolve", "00"])),
            Ok(Command::ConfigResolve("00".to_owned()))
        );
        assert_eq!(
            parse_command(&args(&["config", "explain", "00", "72756e74696d652e70726f66696c65"])),
            Ok(Command::ConfigExplain {
                wire_hex: "00".to_owned(),
                path_hex: "72756e74696d652e70726f66696c65".to_owned(),
            })
        );
''',
)

capability_block = '''    Capability {
        name: "config.explain",
        maturity: "preview",
        mutation: "read-only",
    },
    Capability {
        name: "config.resolve",
'''
replace_once(
    "crates/syntavra-contracts/src/lib.rs",
    '''    Capability {
        name: "config.resolve",
''',
    capability_block,
)
replace_once(
    "crates/syntavra-contracts/src/lib.rs",
    '    "capability=config.resolve|preview|read-only\\n",\n',
    '    "capability=config.explain|preview|read-only\\n",\n    "capability=config.resolve|preview|read-only\\n",\n',
)
replace_once(
    "crates/syntavra-contracts/src/lib.rs",
    '        assert!(capabilities_json().contains("\\"name\\":\\"config.resolve\\""));\n',
    '        assert!(capabilities_json().contains("\\"name\\":\\"config.explain\\""));\n        assert!(capabilities_json().contains("\\"name\\":\\"config.resolve\\""));\n',
)

replace_once(
    "syntavra_runtime/engine_selector.py",
    'RUST_CAPABILITIES = (\n    "config.resolve",\n',
    'RUST_CAPABILITIES = (\n    "config.explain",\n    "config.resolve",\n',
)
replace_once(
    "syntavra_runtime/engine_selector.py",
    '    "capability=config.resolve|preview|read-only\\n"\n',
    '    "capability=config.explain|preview|read-only\\n"\n    "capability=config.resolve|preview|read-only\\n"\n',
)
replace_once(
    "contracts/engine/descriptor.txt",
    "capability=config.resolve|preview|read-only\n",
    "capability=config.explain|preview|read-only\ncapability=config.resolve|preview|read-only\n",
)
replace_once(
    "tests/runtime/test_engine_selector_r4.py",
    '''                for name in (
                    "config.resolve",
''',
    '''                for name in (
                    "config.explain",
                    "config.resolve",
''',
)
replace_once(
    "tests/runtime/test_engine_selector_r4.py",
    '''    assert verification.capabilities == (
        "config.resolve",
''',
    '''    assert verification.capabilities == (
        "config.explain",
        "config.resolve",
''',
)
replace_once(
    "tools/run_engine_parity.py",
    '''    expected = [
        "config.resolve",
''',
    '''    expected = [
        "config.explain",
        "config.resolve",
''',
)
