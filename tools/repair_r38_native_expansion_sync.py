#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tools" / "sync_r38_native_command_count.py"

OLD = '''def _wire_native_expansion() -> None:
    source = NATIVE_PRODUCT.read_text(encoding="utf-8")
    source = _replace_literal_once(
        source,
        EXPANSION_MODULE_MARKER,
        EXPANSION_MODULE_REPLACEMENT,
        label="native expansion module",
    )
    source = _replace_literal_once(
        source,
        EXPANSION_SUPPORT_MARKER,
        EXPANSION_SUPPORT_REPLACEMENT,
        label="native expansion support",
    )
    source = _replace_literal_once(
        source,
        EXPANSION_EXECUTE_MARKER,
        EXPANSION_EXECUTE_REPLACEMENT,
        label="native expansion execution",
    )
    NATIVE_PRODUCT.write_text(source, encoding="utf-8", newline="\\n")
'''

NEW = '''def _wire_native_expansion() -> None:
    source = NATIVE_PRODUCT.read_text(encoding="utf-8")
    module = ''' + "'''" + '''#[path = "native_expansion.rs"]
mod native_expansion;
''' + "'''" + '''
    support = "        || native_expansion::supports(command)\\n"
    execute = ''' + "'''" + '''    if native_expansion::supports(command) {
        return native_expansion::execute(command, &arguments, project_root, state_root).map(Some);
    }
''' + "'''" + '''
    anchors = (
        (
            module,
            ''' + "'''" + '''#[path = "native_external_suite_gate.rs"]
mod native_external_suite_gate;
''' + "'''" + ''',
            False,
            "native expansion module",
        ),
        (
            support,
            "        || native_engine_state_routes::supports(command)\\n",
            True,
            "native expansion support",
        ),
        (
            execute,
            ''' + "'''" + '''    if native_engine_state_routes::supports(command) {
        return native_engine_state_routes::execute(command, &arguments, project_root).map(Some);
    }
''' + "'''" + ''',
            True,
            "native expansion execution",
        ),
    )
    changed = False
    for token, anchor, after, label in anchors:
        count = source.count(token)
        if count == 1:
            continue
        if count != 0:
            raise RuntimeError(f"expected at most one {label} token, found {count}")
        if source.count(anchor) != 1:
            raise RuntimeError(f"expected one {label} anchor")
        replacement = anchor + token if after else token + anchor
        source = source.replace(anchor, replacement, 1)
        changed = True
    if changed:
        NATIVE_PRODUCT.write_text(source, encoding="utf-8", newline="\\n")
'''


def repair() -> bool:
    source = TARGET.read_text(encoding="utf-8")
    old_count = source.count(OLD)
    new_count = source.count(NEW)
    if old_count == 1 and new_count == 0:
        TARGET.write_text(
            source.replace(OLD, NEW, 1),
            encoding="utf-8",
            newline="\n",
        )
        return True
    if old_count == 0 and new_count == 1:
        return False
    raise RuntimeError(
        "native expansion sync function is neither one legacy nor one semantic block: "
        f"legacy={old_count}, semantic={new_count}"
    )


def main() -> int:
    changed = repair()
    print(
        json.dumps(
            {
                "changed": changed,
                "ok": True,
                "surface": "native-expansion-generated-metadata-sync",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
