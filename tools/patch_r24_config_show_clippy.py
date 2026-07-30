#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "crates/syntavra-cli/src/main.rs"


def replace_once(old: str, new: str) -> None:
    source = TARGET.read_text(encoding="utf-8")
    if new in source:
        return
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"main.rs: expected one config snapshot refactor anchor, found {count}")
    TARGET.write_text(source.replace(old, new, 1), encoding="utf-8", newline="\n")


replace_once(
    '''fn run_config_explain(wire_hex: &str, path_hex: &str) -> Result<(), String> {
    let wire = decode_hex(wire_hex)?;
    let path = decode_hex(path_hex)?;
    println!("{}", explain_config_wire_json(&wire, &path)?);
    Ok(())
}

fn run(command: Command) -> Result<(), String> {
''',
    '''fn run_config_explain(wire_hex: &str, path_hex: &str) -> Result<(), String> {
    let wire = decode_hex(wire_hex)?;
    let path = decode_hex(path_hex)?;
    println!("{}", explain_config_wire_json(&wire, &path)?);
    Ok(())
}

fn run_config_snapshot(encoded: &str) -> Result<(), String> {
    let wire = decode_hex(encoded)?;
    let snapshot = resolve_config_wire(&wire)?;
    println!("{}", snapshot_json(&snapshot)?);
    Ok(())
}

fn run(command: Command) -> Result<(), String> {
''',
)
replace_once(
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
    '''        Command::ConfigResolve(encoded) | Command::ConfigShow(encoded) => {
            run_config_snapshot(&encoded)?;
        }
''',
)
