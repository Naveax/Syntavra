#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from syntavra_runtime.adapter_platform import AdapterRegistry

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "contracts" / "engine" / "r38-native-adapter-catalog-v1.json"


def payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "product": "Syntavra",
        "source": "syntavra_runtime.adapter_platform.AdapterRegistry",
        "records": AdapterRegistry.records(),
        "validation": AdapterRegistry.validate(),
    }


def sync() -> bool:
    rendered = json.dumps(payload(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    current = TARGET.read_text(encoding="utf-8") if TARGET.is_file() else ""
    if current == rendered:
        return False
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(rendered, encoding="utf-8", newline="\n")
    return True


def main() -> int:
    changed = sync()
    value = json.loads(TARGET.read_text(encoding="utf-8"))
    print(json.dumps({
        "adapter_count": len(value["records"]),
        "changed": changed,
        "ok": True,
        "target": str(TARGET.relative_to(ROOT)),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
