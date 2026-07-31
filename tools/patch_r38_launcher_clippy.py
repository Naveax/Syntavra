#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

# This deterministic patch is intentionally single-use and removes itself in CI.
ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "crates" / "syntavra-cli" / "src" / "bin" / "syntavra.rs"

source = TARGET.read_text(encoding="utf-8")

old_fail = 'fn fail(code: &str, message: &str, details: Value) -> ExitCode {\n    emit('
new_fail = (
    'fn fail(code: &str, message: &str, details: impl Into<Value>) -> ExitCode {\n'
    '    let details = details.into();\n'
    '    emit('
)
if old_fail not in source:
    raise SystemExit("R38 fail signature pattern was not found")
source = source.replace(old_fail, new_fail, 1)

old_home = '''    let home = env::var_os("HOME")
        .or_else(|| env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
'''
new_home = '''    let home = env::var_os("HOME")
        .or_else(|| env::var_os("USERPROFILE"))
        .map_or_else(|| PathBuf::from("."), PathBuf::from);
'''
if old_home not in source:
    raise SystemExit("R38 home resolution pattern was not found")
source = source.replace(old_home, new_home, 1)

TARGET.write_text(source, encoding="utf-8", newline="\n")
print(f"patched {TARGET.relative_to(ROOT)}")
