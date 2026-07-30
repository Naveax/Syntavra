#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

path = Path(__file__).resolve().parents[1] / "crates" / "syntavra-cli" / "src" / "main.rs"
source = path.read_text(encoding="utf-8")

anchor = '''fn run(command: Command) -> Result<(), String> {
'''
helper = '''fn run_config_explain(wire_hex: &str, path_hex: &str) -> Result<(), String> {
    let wire = decode_hex(wire_hex)?;
    let path = decode_hex(path_hex)?;
    println!("{}", explain_config_wire_json(&wire, &path)?);
    Ok(())
}

fn run(command: Command) -> Result<(), String> {
'''
if helper not in source:
    if source.count(anchor) != 1:
        raise RuntimeError("config explain helper anchor mismatch")
    source = source.replace(anchor, helper, 1)

old_branch = '''        Command::ConfigExplain { wire_hex, path_hex } => {
            let wire = decode_hex(&wire_hex)?;
            let path = decode_hex(&path_hex)?;
            println!("{}", explain_config_wire_json(&wire, &path)?);
        }
'''
new_branch = '''        Command::ConfigExplain { wire_hex, path_hex } => {
            run_config_explain(&wire_hex, &path_hex)?;
        }
'''
if new_branch not in source:
    if source.count(old_branch) != 1:
        raise RuntimeError("config explain run branch anchor mismatch")
    source = source.replace(old_branch, new_branch, 1)

path.write_text(source, encoding="utf-8", newline="\n")
