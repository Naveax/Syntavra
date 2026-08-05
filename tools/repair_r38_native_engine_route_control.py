#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"

MODULE = '''#[path = "native_engine_route_control.rs"]
mod native_engine_route_control;
'''
MODULE_ANCHOR = '''#[path = "native_engine_routes.rs"]
mod native_engine_routes;
'''
SUPPORT = "        || native_engine_route_control::supports(command)\n"
SUPPORT_ANCHOR = "        || native_engine_routes::supports(command)\n"
EXECUTE = '''    if native_engine_route_control::supports(command) {
        let decision = native_engine_route_control::execute(
            command,
            &arguments,
            project_root,
            state_root,
        )?;
        if decision.exit_code != 0 {
            emit_failed_decision(&decision.value, decision.exit_code);
        }
        return Ok(Some(decision.value));
    }
'''
EXECUTE_ANCHOR = '''    if native_engine_routes::supports(command) {
        return native_engine_routes::execute(command, &arguments, project_root, state_root)
            .map(Some);
    }
'''


def insert_before(source: str, token: str, anchor: str, label: str) -> tuple[str, bool]:
    count = source.count(token)
    if count == 1:
        return source, False
    if count != 0:
        raise RuntimeError(f"{label} count invalid: {count}")
    if source.count(anchor) != 1:
        raise RuntimeError(f"{label} anchor must be unique")
    return source.replace(anchor, token + anchor, 1), True


def repair() -> bool:
    source = TARGET.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for token, anchor, label in (
        (MODULE, MODULE_ANCHOR, "engine route control module"),
        (SUPPORT, SUPPORT_ANCHOR, "engine route control support"),
        (EXECUTE, EXECUTE_ANCHOR, "engine route control execute"),
    ):
        rendered, applied = insert_before(rendered, token, anchor, label)
        changed = changed or applied

    invariants = {
        "module": rendered.count(MODULE),
        "support": rendered.count(SUPPORT),
        "execute": rendered.count(EXECUTE),
    }
    if invariants != {"module": 1, "support": 1, "execute": 1}:
        raise RuntimeError(f"engine route control wiring invariant failed: {invariants}")
    if rendered.index(SUPPORT) > rendered.index(SUPPORT_ANCHOR):
        raise RuntimeError("generic engine route support must precede exact route support")
    if rendered.index(EXECUTE) > rendered.index(EXECUTE_ANCHOR):
        raise RuntimeError("generic engine route execution must precede exact route execution")
    if changed:
        TARGET.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def main() -> int:
    changed = repair()
    print(
        json.dumps(
            {
                "changed": changed,
                "ok": True,
                "surface": "native-engine-route-control",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
