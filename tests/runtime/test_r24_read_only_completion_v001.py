from __future__ import annotations

import json
from pathlib import Path

from syntavra_runtime.engine_cli import INSTALLED_READ_ONLY_ROUTE_COMMANDS
from syntavra_runtime.telemetry_metrics_router_r24 import TelemetryMetricsRouterR24
from tools.verify_r24_read_only_completion import CONTRACT_PATH, ROOT, verify


def test_r24_completion_contract_has_unique_routes() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    rows = contract["routes"]
    feature_ids = [row["feature_id"] for row in rows]
    commands = [row["python_command"] for row in rows]

    assert len(rows) == 17
    assert len(feature_ids) == len(set(feature_ids))
    assert len(commands) == len(set(commands))
    assert contract["claim"] == "READ_ONLY_CLI_PARITY_PROVEN"
    assert contract["full_product_parity"] == "FULL_PARITY_NOT_PROVEN"


def test_installed_route_inventory_matches_router() -> None:
    assert INSTALLED_READ_ONLY_ROUTE_COMMANDS == TelemetryMetricsRouterR24.supported_commands()


def test_r24_completion_verifier_passes_structural_gate() -> None:
    result = verify(run_real_verifiers=False)

    assert result["ok"] is True
    assert result["phase"] == "R24"
    assert result["certified_route_count"] == 17
    assert result["real_binary_verifiers_executed"] is False
    assert result["full_product_parity"] == "FULL_PARITY_NOT_PROVEN"


def test_r24_completion_real_verifier_paths_are_repository_bound() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    for relative in contract["real_binary_verifiers"]:
        path = (ROOT / relative).resolve()
        assert path.is_file()
        assert path.is_relative_to(ROOT.resolve())
        assert Path(relative).parts[0] == "tools"
