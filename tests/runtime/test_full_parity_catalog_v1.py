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
from verify_full_parity_catalog import (
    EXPECTED_PHASES,
    REQUIRED_PYTHON_COMMAND_ROOTS,
    reachable_command_roots,
    verify,
)

CATALOG = ROOT / "contracts" / "parity" / "python-rust-full-parity-v1.json"


def test_full_parity_catalog_verifier_passes() -> None:
    result = verify()
    assert result["ok"] is True
    assert result["phase"] == "R37"
    assert result["program"] == "R23-R37"
    assert result["claim"] == "FULL_PARITY_PROVEN"
    assert result["workstreams"] == EXPECTED_PHASES
    assert all(row["complete"] is True for row in result["dimensions"].values())


def test_surface_exporters_are_deterministic() -> None:
    assert export_python_surface() == export_python_surface()
    assert export_rust_surface() == export_rust_surface()


def test_baseline_groups_have_reachable_command_paths() -> None:
    commands = set(export_python_surface()["cli_commands"])
    roots = reachable_command_roots(commands)
    assert REQUIRED_PYTHON_COMMAND_ROOTS <= roots

    # Required argparse groups are not independently runnable public commands.
    # Their reachable descendants, rather than synthetic roots, are certified.
    for root in REQUIRED_PYTHON_COMMAND_ROOTS:
        assert any(command.startswith(f"{root} ") for command in commands)


def test_every_workstream_is_complete() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    assert [row["phase"] for row in catalog["workstreams"]] == EXPECTED_PHASES
    assert all(row["status"] == "COMPLETE" for row in catalog["workstreams"])


def test_every_catalogued_feature_is_production_ready() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    assert catalog["features"]
    assert all(row["status"] == "RUST_PRODUCTION_READY" for row in catalog["features"])
    assert all(row["rust_owner"] for row in catalog["features"])
    assert all(row["contract"] for row in catalog["features"])
    assert all(row["parity_tests"] for row in catalog["features"])


def test_r37_certification_is_python_independent_and_four_platform() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    certification = catalog["certification"]
    assert catalog["claim"] == "FULL_PARITY_PROVEN"
    assert certification["phase"] == "R37"
    assert certification["python_invocation_by_rust"] is False
    assert certification["platforms"] == [
        "linux-x64",
        "windows-x64",
        "macos-x64",
        "macos-arm64",
    ]
