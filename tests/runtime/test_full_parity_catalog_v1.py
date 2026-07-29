from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from export_python_surface import export_surface as export_python_surface
from export_rust_surface import export_surface as export_rust_surface
from verify_full_parity_catalog import EXPECTED_PHASES, verify

CATALOG = ROOT / "contracts" / "parity" / "python-rust-full-parity-v1.json"


def test_full_parity_catalog_verifier_passes() -> None:
    result = verify()
    assert result["ok"] is True
    assert result["phase"] == "R23"
    assert result["program"] == "R23-R37"
    assert result["claim"] == "FULL_PARITY_NOT_PROVEN"
    assert result["workstreams"] == EXPECTED_PHASES


def test_surface_exporters_are_deterministic() -> None:
    assert export_python_surface() == export_python_surface()
    assert export_rust_surface() == export_rust_surface()


def test_catalog_starts_every_remaining_workstream() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    assert [row["phase"] for row in catalog["workstreams"]] == EXPECTED_PHASES
    assert all(row["status"] == "ACTIVE" for row in catalog["workstreams"])


def test_current_proven_routes_include_r22_and_r24_surfaces() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    proven = {
        row["id"]
        for row in catalog["features"]
        if row["status"] == "PARITY_PROVEN"
    }
    assert proven == {
        "route.version",
        "route.status",
        "route.config.resolve",
        "route.state.layout",
        "route.state.inspect",
        "route.receipt.inspect",
        "route.state.broker-snapshot",
        "route.state.broker-live-snapshot",
        "route.pipeline.describe",
        "route.plugins.list",
    }


def test_full_parity_is_not_claimed_early() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    assert catalog["claim"] == "FULL_PARITY_NOT_PROVEN"
    assert not any(
        row["status"] == "RUST_PRODUCTION_READY"
        for row in catalog["features"]
    )
