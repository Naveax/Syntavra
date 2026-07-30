#!/usr/bin/env python3
from __future__ import annotations

# Assertion-locked source synchronization trigger for the R24 config.show slice.
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    source = target.read_text(encoding="utf-8")
    if new in source:
        return
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one config show patch anchor, found {count}")
    target.write_text(source.replace(old, new, 1), encoding="utf-8", newline="\n")


replace_once(
    "crates/syntavra-cli/src/main.rs",
    '    "  syntavra-rs config resolve <config-wire-hex>\\n",\n',
    '    "  syntavra-rs config resolve <config-wire-hex>\\n",\n    "  syntavra-rs config show <config-wire-hex>\\n",\n',
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    "    ConfigResolve(String),\n",
    "    ConfigResolve(String),\n    ConfigShow(String),\n",
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    '''        [config, action, wire] if config == "config" && action == "resolve" => {
            Ok(Command::ConfigResolve(wire.clone()))
        }
''',
    '''        [config, action, wire] if config == "config" && action == "resolve" => {
            Ok(Command::ConfigResolve(wire.clone()))
        }
        [config, action, wire] if config == "config" && action == "show" => {
            Ok(Command::ConfigShow(wire.clone()))
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
    '''        Command::ConfigResolve(encoded) => {
            let wire = decode_hex(&encoded)?;
            let snapshot = resolve_config_wire(&wire)?;
            println!("{}", snapshot_json(&snapshot)?);
        }
        Command::ConfigShow(encoded) => {
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
            parse_command(&args(&["config", "show", "00"])),
            Ok(Command::ConfigShow("00".to_owned()))
        );
''',
)

replace_once(
    "crates/syntavra-contracts/src/lib.rs",
    '''    Capability {
        name: "config.resolve",
''',
    '''    Capability {
        name: "config.resolve",
        maturity: "preview",
        mutation: "read-only",
    },
    Capability {
        name: "config.show",
''',
)
replace_once(
    "crates/syntavra-contracts/src/lib.rs",
    '    "capability=config.resolve|preview|read-only\\n",\n',
    '    "capability=config.resolve|preview|read-only\\n",\n    "capability=config.show|preview|read-only\\n",\n',
)
replace_once(
    "crates/syntavra-contracts/src/lib.rs",
    '        assert!(capabilities_json().contains("\\"name\\":\\"config.resolve\\""));\n',
    '        assert!(capabilities_json().contains("\\"name\\":\\"config.resolve\\""));\n        assert!(capabilities_json().contains("\\"name\\":\\"config.show\\""));\n',
)

replace_once(
    "syntavra_runtime/engine_selector.py",
    '    "config.resolve",\n',
    '    "config.resolve",\n    "config.show",\n',
)
replace_once(
    "syntavra_runtime/engine_selector.py",
    '    "capability=config.resolve|preview|read-only\\n"\n',
    '    "capability=config.resolve|preview|read-only\\n"\n    "capability=config.show|preview|read-only\\n"\n',
)
replace_once(
    "contracts/engine/descriptor.txt",
    "capability=config.resolve|preview|read-only\n",
    "capability=config.resolve|preview|read-only\ncapability=config.show|preview|read-only\n",
)
replace_once(
    "tests/runtime/test_engine_selector_r4.py",
    '                    "config.resolve",\n',
    '                    "config.resolve",\n                    "config.show",\n',
)
replace_once(
    "tests/runtime/test_engine_selector_r4.py",
    '        "config.resolve",\n',
    '        "config.resolve",\n        "config.show",\n',
)
replace_once(
    "tools/run_engine_parity.py",
    '        "config.resolve",\n',
    '        "config.resolve",\n        "config.show",\n',
)
