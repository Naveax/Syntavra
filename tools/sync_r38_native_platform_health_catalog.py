#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "contracts" / "engine" / "r38-native-platform-health-v1.json"
PROJECT_PLACEHOLDER = "<project>"


def normalize(value: Any, *, project: Path, state: Path) -> Any:
    if isinstance(value, dict):
        return {
            str(key): normalize(item, project=project, state=state)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [normalize(item, project=project, state=state) for item in value]
    if isinstance(value, Path):
        value = str(value)
    if isinstance(value, str):
        return value.replace(str(project), PROJECT_PLACEHOLDER).replace(
            str(state),
            "<state>",
        )
    return value


def build() -> dict[str, Any]:
    from syntavra_runtime.platform import SyntavraPlatform

    previous_path = os.environ.get("PATH")
    os.environ["PATH"] = ""
    try:
        with tempfile.TemporaryDirectory(prefix="syntavra-platform-health-") as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            state = root / "state"
            platform = SyntavraPlatform(project, state / "unified")
            status = normalize(platform.status(), project=project, state=state)
            doctor = normalize(platform.doctor(), project=project, state=state)
    finally:
        if previous_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = previous_path
    return {
        "schema_version": 1,
        "product": "Syntavra",
        "source": "syntavra_runtime.platform.SyntavraPlatform",
        "environment_contract": {
            "path": "empty",
            "project": PROJECT_PLACEHOLDER,
            "state": "<state>",
        },
        "routes": {
            "run competitive-doctor": "doctor",
            "run competitive-status": "status",
            "run platform-doctor": "doctor",
            "run platform-status": "status",
        },
        "status": status,
        "doctor": doctor,
    }


def sync() -> bool:
    value = build()
    rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.is_file() else ""
    if current == rendered:
        return False
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered, encoding="utf-8", newline="\n")
    return True


def main() -> int:
    changed = sync()
    value = json.loads(OUTPUT.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "changed": changed,
                "ok": True,
                "output": str(OUTPUT.relative_to(ROOT)),
                "routes": sorted(value["routes"]),
                "schema_version": value["schema_version"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
