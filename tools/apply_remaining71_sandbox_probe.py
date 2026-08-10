#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "crates/syntavra-cli/src/native_product.rs"


def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    text = PRODUCT.read_text(encoding="utf-8")
    text = one(
        text,
        '#[path = "native_remaining71_security.rs"]\nmod native_remaining71_security;\n',
        '#[path = "native_remaining71_security.rs"]\nmod native_remaining71_security;\n#[path = "native_remaining71_sandbox.rs"]\nmod native_remaining71_sandbox;\n',
        "module",
    )
    text = one(
        text,
        '        || (bulk_parity_probe_enabled() && native_remaining71_security::supports(command))\n',
        '        || (bulk_parity_probe_enabled() && native_remaining71_security::supports(command))\n        || (bulk_parity_probe_enabled() && native_remaining71_sandbox::supports(command))\n',
        "supports",
    )
    anchor = '    if bulk_parity_probe_enabled() && native_remaining71_security::supports(command) {\n        return native_remaining71_security::execute(command, &arguments, state_root);\n    }\n'
    addition = '''    if bulk_parity_probe_enabled() && native_remaining71_sandbox::supports(command) {\n        if let Some(decision) = native_remaining71_sandbox::execute(command, &arguments, project_root, state_root)? {\n            if decision.exit_code != 0 {\n                emit_failed_decision(&decision.value, decision.exit_code);\n            }\n            return Ok(Some(decision.value));\n        }\n    }\n'''
    text = one(text, anchor, anchor + addition, "execute")
    PRODUCT.write_text(text, encoding="utf-8")
    print("sandbox closure wired behind SYNTAVRA_BULK_PARITY_PROBE=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
