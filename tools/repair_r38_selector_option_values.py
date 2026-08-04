#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "crates/syntavra-cli/src/bin/syntavra.rs"

LEGACY_OPTION_BLOCK = """        if matches!(
            value.as_str(),
            "--project" | "--state-root" | "--budget" | "--max-tier"
        ) {
"""

CANONICAL_OPTION_BLOCK = """        if matches!(
            value.as_str(),
            "--project"
                | "--state-root"
                | "--budget"
                | "--max-tier"
                | "--codex-home"
                | "--rollout"
                | "--state-file"
        ) {
"""

LEGACY_CONTEXT_TRUNCATION = """    if positional.first().map(String::as_str) == Some("context-stress") {
        positional.truncate(1);
"""

DUPLICATE_TRUNCATION = """    if positional.first().map(String::as_str) == Some("rollout-tail") {
        positional.truncate(1);
    } else if positional.first().map(String::as_str) == Some("context-stress") {
        positional.truncate(1);
"""

CANONICAL_TRUNCATION = """    if matches!(
        positional.first().map(String::as_str),
        Some("rollout-tail") | Some("context-stress")
    ) {
        positional.truncate(1);
"""

VALUE_OPTIONS = (
    "--project",
    "--state-root",
    "--budget",
    "--max-tier",
    "--codex-home",
    "--rollout",
    "--state-file",
)


def _replace_optional_once(source: str, legacy: str, canonical: str, label: str) -> tuple[str, bool]:
    legacy_count = source.count(legacy)
    if legacy_count > 1:
        raise RuntimeError(f"multiple legacy {label} fragments found: {legacy_count}")
    if legacy_count == 1:
        return source.replace(legacy, canonical, 1), True
    return source, False


def _command_path_source(source: str, path: Path) -> str:
    start = source.find("fn command_path(arguments: &[String]) -> Vec<String> {")
    end = source.find("\nfn executable_exists", start)
    if start < 0 or end < 0:
        raise RuntimeError(f"command_path function boundary missing in {path}")
    return source[start:end]


def _validate_canonical(source: str, path: Path) -> None:
    command_path = _command_path_source(source, path)
    missing_options = [option for option in VALUE_OPTIONS if option not in command_path]
    if missing_options:
        raise RuntimeError(
            f"selector value options missing from command_path in {path}: {missing_options}"
        )
    if command_path.count('Some("rollout-tail")') != 1:
        raise RuntimeError(f"rollout-tail truncation invariant failed in {path}")
    if command_path.count('Some("context-stress")') != 1:
        raise RuntimeError(f"context-stress truncation invariant failed in {path}")
    if command_path.count("positional.truncate(1);") != 1:
        raise RuntimeError(f"single-segment truncation must have one branch in {path}")
    if 'Some("rollout-tail") | Some("context-stress")' not in command_path:
        raise RuntimeError(f"combined selector truncation pattern missing in {path}")


def repair(path: Path | None = None) -> bool:
    path = path or TARGET
    source = path.read_text(encoding="utf-8")
    rendered = source
    changed = False

    rendered, applied = _replace_optional_once(
        rendered,
        LEGACY_OPTION_BLOCK,
        CANONICAL_OPTION_BLOCK,
        "selector option-value",
    )
    changed = changed or applied

    rendered, applied = _replace_optional_once(
        rendered,
        DUPLICATE_TRUNCATION,
        CANONICAL_TRUNCATION,
        "duplicate selector truncation",
    )
    changed = changed or applied

    rendered, applied = _replace_optional_once(
        rendered,
        LEGACY_CONTEXT_TRUNCATION,
        CANONICAL_TRUNCATION,
        "legacy selector truncation",
    )
    changed = changed or applied

    _validate_canonical(rendered, path)
    if changed:
        path.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def main() -> int:
    changed = repair()
    print("repaired: syntavra.rs" if changed else "Selector option parsing already canonical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
