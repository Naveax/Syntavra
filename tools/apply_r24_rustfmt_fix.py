#!/usr/bin/env python3
from pathlib import Path

# Temporary assertion-locked formatter correction; removed after application.
path = Path(__file__).resolve().parents[1] / "crates" / "syntavra-cli" / "src" / "main.rs"
source = path.read_text(encoding="utf-8")
old = '''        [plugins, action] if plugins == "plugins" && action == "list" => {\n            Ok(Command::PluginsList)\n        }\n'''
new = '''        [plugins, action] if plugins == "plugins" && action == "list" => Ok(Command::PluginsList),\n'''
if new not in source:
    if source.count(old) != 1:
        raise RuntimeError("R24 rustfmt patch assertion failed")
    source = source.replace(old, new, 1)
path.write_text(source, encoding="utf-8", newline="\n")
