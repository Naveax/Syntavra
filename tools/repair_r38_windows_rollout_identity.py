#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "crates/syntavra-cli/src/native_rollout_tail.rs"

LEGACY = """#[cfg(windows)]
fn file_numbers(metadata: &fs::Metadata) -> (u64, u64) {
    use std::os::windows::fs::MetadataExt;
    (
        metadata.file_index().unwrap_or(0),
        u64::from(metadata.volume_serial_number().unwrap_or(0)),
    )
}
"""

CANONICAL = """#[cfg(windows)]
fn file_numbers(metadata: &fs::Metadata) -> (u64, u64) {
    use std::os::windows::fs::MetadataExt;
    (metadata.creation_time(), 0)
}
"""


def repair(path: Path | None = None) -> bool:
    path = path or TARGET
    source = path.read_text(encoding="utf-8")
    legacy_count = source.count(LEGACY)
    canonical_count = source.count(CANONICAL)

    if legacy_count == 0 and canonical_count == 1:
        return False
    if legacy_count != 1 or canonical_count != 0:
        raise RuntimeError(
            "expected exactly one legacy or canonical Windows rollout identity "
            f"block in {path}; legacy={legacy_count}, canonical={canonical_count}"
        )

    rendered = source.replace(LEGACY, CANONICAL, 1)
    path.write_text(rendered, encoding="utf-8", newline="\n")
    return True


def main() -> int:
    changed = repair()
    print("repaired: native_rollout_tail.rs" if changed else "Windows rollout identity already canonical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
