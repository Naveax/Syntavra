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
        '#[path = "native_expansion.rs"]\nmod native_expansion;\n',
        '#[path = "native_expansion.rs"]\nmod native_expansion;\n#[path = "native_remaining71_memory.rs"]\nmod native_remaining71_memory;\n',
        "module",
    )
    text = one(
        text,
        'pub fn supports(command: &[String]) -> bool {\n',
        'fn bulk_parity_probe_enabled() -> bool {\n    std::env::var_os("SYNTAVRA_BULK_PARITY_PROBE").is_some_and(|value| value == "1")\n}\n\npub fn supports(command: &[String]) -> bool {\n',
        "probe-fn",
    )
    anchor = '        || native_expansion::supports(command)\n'
    text = one(
        text,
        anchor,
        anchor + '        || (bulk_parity_probe_enabled() && native_remaining71_memory::supports(command))\n',
        "supports",
    )
    anchor = '    let arguments = std::env::args().skip(1).collect::<Vec<_>>();\n'
    text = one(
        text,
        anchor,
        anchor + '    if bulk_parity_probe_enabled() && native_remaining71_memory::supports(command) {\n        return native_remaining71_memory::execute(command, &arguments, project_root, state_root);\n    }\n',
        "execute",
    )
    PRODUCT.write_text(text, encoding="utf-8")
    print("memory closure wired behind SYNTAVRA_BULK_PARITY_PROBE=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
