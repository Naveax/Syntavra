#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
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


def canonicalize_language_aliases(value: Any) -> Any:
    if isinstance(value, dict):
        rendered = {
            str(key): canonicalize_language_aliases(item)
            for key, item in value.items()
        }
        tree_sitter = rendered.get("tree_sitter")
        if isinstance(tree_sitter, dict):
            available = tree_sitter.get("available_languages")
            if isinstance(available, list):
                tree_sitter["available_languages"] = sorted(
                    {
                        "csharp" if str(item) == "c_sharp" else str(item)
                        for item in available
                    }
                )
        return rendered
    if isinstance(value, list):
        return [canonicalize_language_aliases(item) for item in value]
    return value


def run_public(
    action: str,
    *,
    project: Path,
    state: Path,
    home: Path,
) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["HOME"] = str(home)
    environment["USERPROFILE"] = str(home)
    environment["PATH"] = ""
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "syntavra_runtime.engine_entry",
            "--engine",
            "python",
            "--project",
            str(project),
            "--state-root",
            str(state),
            "run",
            action,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="strict",
        timeout=600,
        env=environment,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            json.dumps(
                {
                    "action": action,
                    "code": "PLATFORM_HEALTH_PUBLIC_EXPORT_FAILED",
                    "returncode": completed.returncode,
                    "stderr": completed.stderr[-4000:],
                    "stdout": completed.stdout[-4000:],
                },
                sort_keys=True,
            )
        )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError(f"platform health public route must return an object: {action}")
    return value


def build() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="syntavra-platform-health-") as temporary:
        root = Path(temporary)
        project = root / "project"
        project.mkdir()
        state = root / "state"
        home = root / "home"
        home.mkdir()
        status = canonicalize_language_aliases(
            normalize(
                run_public(
                    "platform-status",
                    project=project,
                    state=state,
                    home=home,
                ),
                project=project,
                state=state,
            )
        )
        doctor = canonicalize_language_aliases(
            normalize(
                run_public(
                    "platform-doctor",
                    project=project,
                    state=state,
                    home=home,
                ),
                project=project,
                state=state,
            )
        )
    return {
        "schema_version": 1,
        "product": "Syntavra",
        "source": "syntavra_runtime.engine_entry:python",
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
