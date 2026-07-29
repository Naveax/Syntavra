#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from syntavra_runtime.config_contract import resolve_config_phases, status_projection
from syntavra_runtime.release_identity import identity
from syntavra_runtime.state_receipt_contract import state_layout
from syntavra_runtime.state_snapshot_contract import inspect_state_root, project_id_for_root
from verify_r15_live_config_routing import verify as verify_live_config_routing
from verify_r16_session_task_routing import verify as verify_session_task_routing
from verify_r17_state_layout_routing import verify as verify_state_layout_routing
from verify_r18_state_inspect_routing import verify as verify_state_inspect_routing
from verify_r19_receipt_inspect_routing import verify as verify_receipt_inspect_routing
from verify_r20_broker_snapshot_routing import verify as verify_broker_snapshot_routing
from verify_r21_live_broker_snapshot_routing import verify as verify_live_broker_snapshot_routing
from verify_r22_capability_aware_auto import verify as verify_capability_aware_auto
from verify_read_only_routing_parity import verify as verify_read_only_routing

ROOT = Path(__file__).resolve().parents[1]
DESCRIPTOR = ROOT / "contracts" / "engine" / "descriptor.txt"


def _rust_json(*arguments: str) -> dict[str, object]:
    completed = subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "--locked",
            "-p",
            "syntavra-cli",
            "--",
            *arguments,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Rust engine command failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("Rust engine output must be a JSON object")
    return value


def verify() -> dict[str, object]:
    reference = identity().to_dict()
    version = _rust_json("version")
    capabilities = _rust_json("engine", "capabilities")
    contract_hash = _rust_json("engine", "contract-hash")
    status = _rust_json("status")
    layout = _rust_json("state", "layout")
    project_id = project_id_for_root(ROOT)
    state_inspection = _rust_json("state", "inspect", project_id, str(ROOT))
    reference_state_inspection = inspect_state_root(
        ROOT,
        expected_project_id=project_id,
    )
    reference_status = status_projection(resolve_config_phases([{}]))
    routing = verify_read_only_routing()
    live_routing = verify_live_config_routing()
    session_task_routing = verify_session_task_routing()
    state_layout_routing = verify_state_layout_routing()
    state_inspect_routing = verify_state_inspect_routing()
    receipt_inspect_routing = verify_receipt_inspect_routing()
    broker_snapshot_routing = verify_broker_snapshot_routing()
    live_broker_snapshot_routing = verify_live_broker_snapshot_routing()
    capability_aware_auto = verify_capability_aware_auto()

    checks = {
        "product": version.get("product") == "Syntavra",
        "version": version.get("product_version") == reference["version"],
        "channel": version.get("release_channel") == reference["channel"],
        "rust_engine": version.get("engine") == "rust",
        "contract_version": version.get("contract_version") == 1,
        "capability_contract": capabilities.get("contract_version") == 1,
        "descriptor_hash": contract_hash.get("contract_hash")
        == hashlib.sha256(DESCRIPTOR.read_bytes()).hexdigest(),
        "default_status": status == reference_status,
        "state_layout": layout == state_layout(),
        "state_inspection": state_inspection == reference_state_inspection,
        "read_only_routing": routing.get("ok") is True
        and routing.get("phase") == "R14"
        and routing.get("routes") == ["config.resolve", "status", "version"]
        and routing.get("maximum_input_bytes") == 262144,
        "live_config_routing": live_routing.get("ok") is True
        and live_routing.get("phase") == "R15"
        and live_routing.get("routes") == ["config.resolve", "status", "version"]
        and live_routing.get("input_profile") == "live-config-discovery-v1",
        "session_task_routing": session_task_routing.get("ok") is True
        and session_task_routing.get("phase") == "R16"
        and session_task_routing.get("routes")
        == ["config.resolve", "status", "version"]
        and session_task_routing.get("input_profile")
        == "live-config-session-task-v1",
        "state_layout_routing": state_layout_routing.get("ok") is True
        and state_layout_routing.get("phase") == "R17"
        and state_layout_routing.get("routes")
        == ["config.resolve", "state.layout", "status", "version"]
        and state_layout_routing.get("input_profile") == "none",
        "state_inspect_routing": state_inspect_routing.get("ok") is True
        and state_inspect_routing.get("phase") == "R18"
        and state_inspect_routing.get("routes")
        == ["config.resolve", "state.inspect", "state.layout", "status", "version"]
        and state_inspect_routing.get("input_profile")
        == "project-bound-state-root-v1",
        "receipt_inspect_routing": receipt_inspect_routing.get("ok") is True
        and receipt_inspect_routing.get("phase") == "R19"
        and receipt_inspect_routing.get("routes")
        == [
            "config.resolve",
            "receipt.inspect",
            "state.inspect",
            "state.layout",
            "status",
            "version",
        ]
        and receipt_inspect_routing.get("input_profile")
        == "project-bound-receipt-wire-v1",
        "broker_snapshot_routing": broker_snapshot_routing.get("ok") is True
        and broker_snapshot_routing.get("phase") == "R20"
        and broker_snapshot_routing.get("routes")
        == [
            "config.resolve",
            "receipt.inspect",
            "state.broker-snapshot",
            "state.inspect",
            "state.layout",
            "status",
            "version",
        ]
        and broker_snapshot_routing.get("input_profile")
        == "project-bound-quiescent-broker-sqlite-v1",
        "live_broker_snapshot_routing": live_broker_snapshot_routing.get("ok") is True
        and live_broker_snapshot_routing.get("phase") == "R21"
        and live_broker_snapshot_routing.get("routes")
        == [
            "config.resolve",
            "receipt.inspect",
            "state.broker-live-snapshot",
            "state.broker-snapshot",
            "state.inspect",
            "state.layout",
            "status",
            "version",
        ]
        and live_broker_snapshot_routing.get("input_profile")
        == "project-bound-bounded-live-broker-sqlite-v1",
        "capability_aware_auto": capability_aware_auto.get("ok") is True
        and capability_aware_auto.get("phase") == "R22"
        and capability_aware_auto.get("routes")
        == [
            "config.resolve",
            "receipt.inspect",
            "state.broker-live-snapshot",
            "state.broker-snapshot",
            "state.inspect",
            "state.layout",
            "status",
            "version",
        ]
        and capability_aware_auto.get("auto_policy")
        == "route-scoped-capability-aware-r22"
        and capability_aware_auto.get("default_engine") == "python"
        and capability_aware_auto.get("selected_engine") == "rust",
    }
    if not all(checks.values()):
        raise RuntimeError(f"initial Python/Rust parity failed: {checks}")

    capability_names = [
        str(row.get("name"))
        for row in capabilities.get("capabilities", [])
        if isinstance(row, dict)
    ]
    expected = [
        "config.resolve",
        "engine.capabilities",
        "engine.contract-hash",
        "pipeline.describe",
        "plugins.list",
        "receipt.inspect",
        "state.broker-live-snapshot",
        "state.broker-snapshot",
        "state.inspect",
        "state.layout",
        "status",
        "version",
    ]
    if capability_names != expected:
        raise RuntimeError(
            f"unexpected Rust capability surface: {capability_names!r}"
        )

    return {
        "ok": True,
        "phase": "R0-R22",
        "reference_engine": "python",
        "candidate_engine": "rust",
        "checks": checks,
        "capabilities": capability_names,
        "routing": routing,
        "live_config_routing": live_routing,
        "session_task_routing": session_task_routing,
        "state_layout_routing": state_layout_routing,
        "state_inspect_routing": state_inspect_routing,
        "receipt_inspect_routing": receipt_inspect_routing,
        "broker_snapshot_routing": broker_snapshot_routing,
        "live_broker_snapshot_routing": live_broker_snapshot_routing,
        "capability_aware_auto": capability_aware_auto,
        "claim": "RUST_ROUTE_SCOPED_CAPABILITY_AWARE_AUTO_R22",
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
