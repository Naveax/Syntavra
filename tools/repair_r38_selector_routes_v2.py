#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "crates" / "syntavra-cli" / "src" / "bin" / "syntavra.rs"

OLD_PATTERN = 'Some("rollout-tail" | "context-stress" | "claim" | "context" | "init")'
NEW_PATTERN = 'Some("rollout-tail" | "context-stress" | "claim" | "context" | "init" | "hook" | "mcp")'
SINGLE_ROUTES = (
    "rollout-tail",
    "context-stress",
    "claim",
    "context",
    "init",
    "hook",
    "mcp",
)
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


def command_path(source: str) -> str:
    start = source.find("fn command_path(arguments: &[String]) -> Vec<String> {")
    end = source.find("\nfn executable_exists", start)
    if start < 0 or end < 0:
        raise RuntimeError("selector command_path boundary missing")
    return source[start:end]


def repair(path: Path = TARGET) -> bool:
    source = path.read_text(encoding="utf-8")
    rendered = source
    old_count = rendered.count(OLD_PATTERN)
    new_count = rendered.count(NEW_PATTERN)
    if old_count == 1 and new_count == 0:
        rendered = rendered.replace(OLD_PATTERN, NEW_PATTERN, 1)
    elif old_count != 0 or new_count != 1:
        raise RuntimeError(
            "selector single-route pattern must be old or canonical: "
            f"old={old_count}, canonical={new_count}"
        )
    block = command_path(rendered)
    missing_options = [value for value in VALUE_OPTIONS if value not in block]
    if missing_options:
        raise RuntimeError(f"selector value options missing: {missing_options}")
    missing_routes = [route for route in SINGLE_ROUTES if block.count(f'"{route}"') != 1]
    if missing_routes:
        raise RuntimeError(f"selector single routes missing or duplicated: {missing_routes}")
    if block.count("positional.truncate(1);") != 1:
        raise RuntimeError("selector single-route truncation branch must be unique")
    changed = rendered != source
    if changed:
        path.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def main() -> int:
    changed = repair()
    print("repaired: selector single routes" if changed else "Selector single routes already canonical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
