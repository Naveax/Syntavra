#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "crates/syntavra-cli/src/bin/syntavra.rs"

REPLACEMENTS = (
    (
        """        if matches!(
            value.as_str(),
            "--project" | "--state-root" | "--budget" | "--max-tier"
        ) {
""",
        """        if matches!(
            value.as_str(),
            "--project"
                | "--state-root"
                | "--budget"
                | "--max-tier"
                | "--codex-home"
                | "--rollout"
                | "--state-file"
        ) {
""",
    ),
    (
        """    if positional.first().map(String::as_str) == Some("context-stress") {
        positional.truncate(1);
""",
        """    if positional.first().map(String::as_str) == Some("rollout-tail") {
        positional.truncate(1);
    } else if positional.first().map(String::as_str) == Some("context-stress") {
        positional.truncate(1);
""",
    ),
)


def repair(path: Path | None = None) -> bool:
    path = path or TARGET
    source = path.read_text(encoding="utf-8")
    rendered = source
    changed = False

    for legacy, canonical in REPLACEMENTS:
        legacy_count = rendered.count(legacy)
        canonical_count = rendered.count(canonical)
        if legacy_count == 0 and canonical_count == 1:
            continue
        if legacy_count != 1 or canonical_count != 0:
            raise RuntimeError(
                "expected exactly one legacy or canonical selector option block "
                f"in {path}; legacy={legacy_count}, canonical={canonical_count}"
            )
        rendered = rendered.replace(legacy, canonical, 1)
        changed = True

    if changed:
        path.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def main() -> int:
    changed = repair()
    print("repaired: syntavra.rs" if changed else "Selector option parsing already canonical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
