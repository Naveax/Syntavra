#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "crates/syntavra-cli/src/native_remaining71_proxy.rs"

MODULE = '#[path = "native_remaining71_provider_proxy.rs"]\nmod native_remaining71_provider_proxy;\n\n'
OLD_SUPPORTS = '''pub(crate) fn supports(command: &[String]) -> bool {\n    command.len() == 2\n        && command[0] == "run"\n        && (command[1] == "gateway-plan" || command[1] == "proxy-service")\n}\n'''
NEW_SUPPORTS = '''pub(crate) fn supports(command: &[String]) -> bool {\n    command.len() == 2\n        && ((command[0] == "run"\n            && (command[1] == "gateway-plan" || command[1] == "proxy-service"))\n            || (command[0] == "provider" && command[1] == "proxy"))\n}\n'''
EXEC_ANCHOR = '''    if !supports(command) {\n        return Ok(None);\n    }\n    let value = match command[1].as_str() {\n'''
EXEC_REPLACEMENT = '''    if !supports(command) {\n        return Ok(None);\n    }\n    if command.len() == 2 && command[0] == "provider" && command[1] == "proxy" {\n        return native_remaining71_provider_proxy::execute(arguments, project, state_root).map(Some);\n    }\n    let value = match command[1].as_str() {\n'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one old match, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    if MODULE not in text:
        anchor = 'use sha2::{Digest as _, Sha256};\n\n'
        count = text.count(anchor)
        if count != 1:
            raise SystemExit(f"module anchor: expected one match, found {count}")
        text = text.replace(anchor, anchor + MODULE, 1)
    text = replace_once(text, OLD_SUPPORTS, NEW_SUPPORTS, "supports")
    text = replace_once(text, EXEC_ANCHOR, EXEC_REPLACEMENT, "execute")
    TARGET.write_text(text, encoding="utf-8")
    print("provider proxy native probe wiring present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
