from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tools" / "export_python_surface.py"
SPEC = importlib.util.spec_from_file_location("export_python_surface", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_installed_python_surface_is_authoritative_and_exact() -> None:
    surface = MODULE.export_surface()
    commands = surface["cli_commands"]
    digest = hashlib.sha256(
        json.dumps(commands, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    assert surface["module_count"] == 195
    assert len(commands) == 280
    assert digest == "f6dfe1be5dba106bfcb30c92724889e2389aa8fe2d36dac6c00ddd7786f8f896"
    assert surface["authoritative"]["cli_commands"] is True
    assert surface["authoritative"]["source_cli_commands"] is False


def test_installed_surface_contains_reachable_paths_only() -> None:
    commands = set(MODULE.export_surface()["cli_commands"])
    assert {
        "provider capabilities",
        "run adapter-certify",
        "fabric cache-align",
        "engine route config.show",
        "evidence gc",
        "evidence rotate-key",
        "evidence stats",
    } <= commands
    assert {
        "arm validate",
        "capabilities",
        "adapter-certify",
        "service",
        "policy",
        "data-route",
    }.isdisjoint(commands)
