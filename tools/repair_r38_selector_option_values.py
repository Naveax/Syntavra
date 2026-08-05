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

PRE_SESSION_OPTION_BLOCK = """        if matches!(
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

PRE_INIT_OPTION_BLOCK = """        if matches!(
            value.as_str(),
            "--project"
                | "--state-root"
                | "--budget"
                | "--max-tier"
                | "--codex-home"
                | "--rollout"
                | "--state-file"
                | "--session-hint"
        ) {
"""

PRE_INSTALL_OPTION_BLOCK = """        if matches!(
            value.as_str(),
            "--project"
                | "--state-root"
                | "--skill-root"
                | "--host"
                | "--budget"
                | "--max-tier"
                | "--codex-home"
                | "--rollout"
                | "--state-file"
                | "--session-hint"
        ) {
"""

CANONICAL_OPTION_BLOCK = """        if matches!(
            value.as_str(),
            "--project"
                | "--state-root"
                | "--skill-root"
                | "--host"
                | "--mcp-profile"
                | "--budget"
                | "--max-tier"
                | "--codex-home"
                | "--rollout"
                | "--state-file"
                | "--session-hint"
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

UNNESTED_TRUNCATION = """    if matches!(
        positional.first().map(String::as_str),
        Some("rollout-tail") | Some("context-stress")
    ) {
        positional.truncate(1);
"""

PRE_INIT_TRUNCATION = """    if matches!(
        positional.first().map(String::as_str),
        Some("rollout-tail" | "context-stress" | "claim" | "context")
    ) {
        positional.truncate(1);
"""

CANONICAL_TRUNCATION = """    if matches!(
        positional.first().map(String::as_str),
        Some("rollout-tail" | "context-stress" | "claim" | "context" | "init")
    ) {
        positional.truncate(1);
"""

VALUE_OPTIONS = (
    "--project",
    "--state-root",
    "--skill-root",
    "--host",
    "--mcp-profile",
    "--budget",
    "--max-tier",
    "--codex-home",
    "--rollout",
    "--state-file",
    "--session-hint",
)

EQUALS_VALUE_OPTIONS = (
    "--project=",
    "--state-root=",
    "--skill-root=",
    "--host=",
    "--mcp-profile=",
    "--codex-home=",
    "--rollout=",
    "--state-file=",
    "--session-hint=",
    "--budget=",
    "--max-tier=",
)

SINGLE_SEGMENT_ROUTES = (
    "rollout-tail",
    "context-stress",
    "claim",
    "context",
    "init",
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
    missing_equals_options = [
        option for option in EQUALS_VALUE_OPTIONS if option not in command_path
    ]
    if missing_equals_options:
        raise RuntimeError(
            "selector equals-form value options missing from command_path "
            f"in {path}: {missing_equals_options}"
        )
    for route in SINGLE_SEGMENT_ROUTES:
        if command_path.count(f'"{route}"') != 1:
            raise RuntimeError(f"{route} truncation invariant failed in {path}")
    if command_path.count("positional.truncate(1);") != 1:
        raise RuntimeError(f"single-segment truncation must have one branch in {path}")
    if (
        'Some("rollout-tail" | "context-stress" | "claim" | "context" | "init")'
        not in command_path
    ):
        raise RuntimeError(f"canonical selector truncation pattern missing in {path}")


def repair(path: Path | None = None) -> bool:
    path = path or TARGET
    source = path.read_text(encoding="utf-8")
    rendered = source
    changed = False

    for legacy, label in (
        (LEGACY_OPTION_BLOCK, "legacy selector option-value"),
        (PRE_SESSION_OPTION_BLOCK, "pre-session selector option-value"),
        (PRE_INIT_OPTION_BLOCK, "pre-init selector option-value"),
        (PRE_INSTALL_OPTION_BLOCK, "pre-install selector option-value"),
    ):
        rendered, applied = _replace_optional_once(
            rendered,
            legacy,
            CANONICAL_OPTION_BLOCK,
            label,
        )
        changed = changed or applied

    for legacy, label in (
        (DUPLICATE_TRUNCATION, "duplicate selector truncation"),
        (UNNESTED_TRUNCATION, "unnested selector truncation"),
        (LEGACY_CONTEXT_TRUNCATION, "legacy selector truncation"),
        (PRE_INIT_TRUNCATION, "pre-init selector truncation"),
    ):
        rendered, applied = _replace_optional_once(
            rendered,
            legacy,
            CANONICAL_TRUNCATION,
            label,
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
