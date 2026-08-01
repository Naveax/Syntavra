#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_exact(path: Path, old: str, new: str) -> None:
    source = path.read_text(encoding="utf-8")
    count = source.count(old)
    if count == 0 and new in source:
        return
    if count != 1:
        raise RuntimeError(f"expected one synthetic fixture in {path}, found {count}")
    path.write_text(source.replace(old, new), encoding="utf-8", newline="\n")


def main() -> int:
    rust = ROOT / "crates" / "syntavra-cli" / "src" / "native_redact.rs"
    replace_exact(
        rust,
        '        let text = "token sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890";\n'
        '        let (redacted, records) = redact_text(text);',
        '        let text = ["token sk", "-proj-", "ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"].concat();\n'
        '        let (redacted, records) = redact_text(&text);',
    )
    replace_exact(
        rust,
        '        let text = "api_key: sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890";\n'
        '        let (redacted, records) = redact_text(text);',
        '        let text = ["api_key: sk", "-proj-", "ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"].concat();\n'
        '        let (redacted, records) = redact_text(&text);',
    )

    python_test = ROOT / "tests" / "runtime" / "test_native_redact_r38.py"
    replace_exact(
        python_test,
        '        "openai": "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",',
        '        "openai": "sk" + "-proj-" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",',
    )
    replace_exact(
        python_test,
        '        "github": "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",',
        '        "github": "gh" + "p_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",',
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
