#!/usr/bin/env python3
# Canonical source for contract, selector, inventory and manifest synchronization.
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from export_python_surface import export_surface as export_python_surface
from export_rust_surface import export_surface as export_rust_surface

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "engine" / "dual-engine-public-surface-v2.json"
SELECTOR = ROOT / "crates" / "syntavra-cli" / "src" / "bin" / "syntavra.rs"
INVENTORY_TEST = ROOT / "tests" / "runtime" / "test_dual_engine_public_surface_r38.py"
PUBLIC_PATTERN = re.compile(r"const PUBLIC_COMMAND_COUNT: u64 = (?P<count>[0-9]+);")
NATIVE_PATTERN = re.compile(r"const NATIVE_COMMAND_COUNT: u64 = (?P<count>[0-9]+);")


def _replace_once(pattern: re.Pattern[str], replacement: str, source: str) -> str:
    rendered, changes = pattern.subn(replacement, source, count=1)
    if changes != 1:
        raise RuntimeError(f"expected one match for {pattern.pattern!r}, found {changes}")
    return rendered


def _command_digest(commands: list[str]) -> str:
    payload = json.dumps(
        commands,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sync() -> int:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    python_surface = export_python_surface()
    rust_surface = export_rust_surface()
    commands = list(python_surface["cli_commands"])

    rust_row = contract["rust_surface"]
    native = list(rust_row["native_public_commands"])
    bridged = list(rust_row["python_launcher_bridge_commands"])
    if native != sorted(set(native)):
        raise RuntimeError("native Rust command inventory must be sorted and unique")
    if bridged != sorted(set(bridged)):
        raise RuntimeError("Python launcher bridge inventory must be sorted and unique")

    total = len(commands)
    native_count = len(native)
    bridge_count = len(bridged)
    if native_count > total:
        raise RuntimeError(f"native command count exceeds Python surface: {native_count}>{total}")

    contract["python_surface"] = {
        "command_paths_sha256": _command_digest(commands),
        "digest_encoding": "canonical-json-array-utf8",
        "module_count": int(python_surface["module_count"]),
        "public_command_count": total,
    }
    rust_row["module_count"] = int(rust_surface["module_count"])
    rust_row["native_public_command_count"] = native_count
    rust_row["python_launcher_bridge_command_count"] = bridge_count
    rust_row["missing_native_public_command_count"] = total - native_count
    rust_row["native_coverage_ppm"] = native_count * 1_000_000 // total
    CONTRACT.write_text(
        json.dumps(contract, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    selector = SELECTOR.read_text(encoding="utf-8")
    selector = _replace_once(
        PUBLIC_PATTERN,
        f"const PUBLIC_COMMAND_COUNT: u64 = {total};",
        selector,
    )
    selector = _replace_once(
        NATIVE_PATTERN,
        f"const NATIVE_COMMAND_COUNT: u64 = {native_count};",
        selector,
    )
    SELECTOR.write_text(selector, encoding="utf-8", newline="\n")

    test_source = INVENTORY_TEST.read_text(encoding="utf-8")
    replacements = (
        (
            re.compile(r'assert result\["python"\]\["public_command_count"\] == [0-9]+'),
            f'assert result["python"]["public_command_count"] == {total}',
        ),
        (
            re.compile(r'assert result\["rust"\]\["native_public_command_count"\] == [0-9]+'),
            f'assert result["rust"]["native_public_command_count"] == {native_count}',
        ),
        (
            re.compile(r'assert result\["rust"\]\["missing_native_public_command_count"\] == [0-9]+'),
            f'assert result["rust"]["missing_native_public_command_count"] == {total - native_count}',
        ),
    )
    for pattern, replacement in replacements:
        test_source = _replace_once(pattern, replacement, test_source)
    INVENTORY_TEST.write_text(test_source, encoding="utf-8", newline="\n")

    print(
        json.dumps(
            {
                "python_public_commands": total,
                "native_public_commands": native_count,
                "missing_native_public_commands": total - native_count,
                "python_modules": python_surface["module_count"],
                "rust_modules": rust_surface["module_count"],
                "bridge_commands": bridge_count,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(sync())
