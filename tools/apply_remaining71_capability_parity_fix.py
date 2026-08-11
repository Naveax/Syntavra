#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "crates/syntavra-cli/src/native_product.rs"
DIFF = ROOT / "tools/validate_remaining71_capability_differential.py"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one old match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    replace_once(
        PRODUCT,
        '''    if bulk_parity_probe_enabled() && native_remaining71_security::supports(command) {\n        return native_remaining71_security::execute(command, &arguments, state_root);\n    }\n''',
        '''    if bulk_parity_probe_enabled() && native_remaining71_security::supports(command) {\n        if let Some(value) = native_remaining71_security::execute(command, &arguments, state_root)? {\n            if command.len() == 2\n                && command[0] == "run"\n                && command[1] == "capability-verify"\n                && value["ok"].as_bool() == Some(false)\n            {\n                emit_failed_decision(&value, 3);\n            }\n            return Ok(Some(value));\n        }\n        return Ok(None);\n    }\n''',
        "Rust capability verify exit semantics",
    )

    text = DIFF.read_text(encoding="utf-8")
    text = text.replace('"read_file"', '"workspace.read"')
    text = text.replace('"write_file"', '"workspace.write"')
    for section in ("consumed_verify", "binding_mismatch", "malformed"):
        old = f'        "{section}": {{"exit": 0, '
        new = f'        "{section}": {{"exit": 3, '
        if new not in text:
            if text.count(old) != 1:
                raise SystemExit(f"{section} exit expectation: expected one old match, found {text.count(old)}")
            text = text.replace(old, new, 1)
    DIFF.write_text(text, encoding="utf-8")

    print("capability parity repair present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
