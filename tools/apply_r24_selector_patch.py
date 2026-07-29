#!/usr/bin/env python3
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "syntavra_runtime" / "engine_selector.py"
source = path.read_text(encoding="utf-8")
old_caps = '''    "engine.contract-hash",\n    "receipt.inspect",\n'''
new_caps = '''    "engine.contract-hash",\n    "pipeline.describe",\n    "plugins.list",\n    "receipt.inspect",\n'''
old_desc = '''    "capability=engine.contract-hash|preview|read-only\\n"\n    "capability=receipt.inspect|preview|read-only\\n"\n'''
new_desc = '''    "capability=engine.contract-hash|preview|read-only\\n"\n    "capability=pipeline.describe|preview|read-only\\n"\n    "capability=plugins.list|preview|read-only\\n"\n    "capability=receipt.inspect|preview|read-only\\n"\n'''
for old, new in ((old_caps, new_caps), (old_desc, new_desc)):
    if new in source:
        continue
    if source.count(old) != 1:
        raise RuntimeError(f"engine_selector patch assertion failed for {old!r}")
    source = source.replace(old, new, 1)
path.write_text(source, encoding="utf-8", newline="\n")
