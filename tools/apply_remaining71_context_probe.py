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
        '#[path = "native_remaining71_competitive.rs"]\nmod native_remaining71_competitive;\n',
        '#[path = "native_remaining71_competitive.rs"]\nmod native_remaining71_competitive;\n#[path = "native_remaining71_context.rs"]\nmod native_remaining71_context;\n',
        "module",
    )
    text = one(
        text,
        '        || (bulk_parity_probe_enabled() && native_remaining71_competitive::supports(command))\n',
        '        || (bulk_parity_probe_enabled() && native_remaining71_competitive::supports(command))\n        || (bulk_parity_probe_enabled() && native_remaining71_context::supports(command))\n',
        "supports",
    )
    anchor = '''    if bulk_parity_probe_enabled() && native_remaining71_competitive::supports(command) {\n        return native_remaining71_competitive::execute(command, &arguments, project_root, state_root);\n    }\n'''
    addition = '''    if bulk_parity_probe_enabled() && native_remaining71_context::supports(command) {\n        return native_remaining71_context::execute(command, &arguments, state_root);\n    }\n'''
    text = one(text, anchor, anchor + addition, "execute")
    PRODUCT.write_text(text, encoding="utf-8")
    print("context compiler closure wired behind SYNTAVRA_BULK_PARITY_PROBE=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
