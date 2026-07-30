from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from .config_contract import MAX_CONFIG_WIRE_BYTES, decode_config_wire, resolve_config_phases
from .state_snapshot_contract import StateInspectionError, project_id_for_root

SCHEMA_VERSION = 1
CONTRACT_VERSION = 1
PLAN_ID = "syntavra-config-last-good-plan-v1"
TARGET_RELATIVE_PATH = ".syntavra/pre-release/config-last-good.json"
CLAIM = "RUST_CONFIG_LAST_GOOD_PLAN_PARITY_PROVEN_R25_FIXTURES"
PERSISTENT_SCOPES = ("user", "project", "environment")
EPHEMERAL_SCOPES = ("session", "task")
_LOWER_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class ConfigLastGoodPlanError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _canonical_payload(snapshot: Mapping[str, Any]) -> bytes:
    expected_keys = {
        "schema_version",
        "values",
        "provenance",
        "config_hash",
        "warnings",
    }
    actual_keys = {str(key) for key in snapshot}
    if actual_keys != expected_keys:
        raise ConfigLastGoodPlanError("CONFIG_LIFECYCLE_SNAPSHOT_KEYS_INVALID")
    return json.dumps(
        dict(snapshot),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _contains_ephemeral_scope(
    phases: Sequence[Mapping[str, Mapping[str, Any]]],
) -> bool:
    return any(bool(phase.get(scope)) for phase in phases for scope in EPHEMERAL_SCOPES)


def config_last_good_plan(
    *,
    project_root: str | Path,
    expected_project_id: str,
    config_wire: bytes,
) -> dict[str, Any]:
    if _LOWER_HEX_64.fullmatch(expected_project_id) is None:
        raise ConfigLastGoodPlanError("CONFIG_LIFECYCLE_EXPECTED_PROJECT_INVALID")

    raw_wire = bytes(config_wire)
    if len(raw_wire) > MAX_CONFIG_WIRE_BYTES:
        raise ConfigLastGoodPlanError("CONFIG_LIFECYCLE_WIRE_TOO_LARGE")

    try:
        actual_project_id = project_id_for_root(project_root)
    except StateInspectionError as exc:
        raise ConfigLastGoodPlanError(
            f"CONFIG_LIFECYCLE_{exc.code.removeprefix('STATE_')}"
        ) from exc
    if actual_project_id != expected_project_id:
        raise ConfigLastGoodPlanError("CONFIG_LIFECYCLE_PROJECT_MISMATCH")

    try:
        phases = decode_config_wire(raw_wire)
    except Exception as exc:
        raise ConfigLastGoodPlanError("CONFIG_LIFECYCLE_WIRE_INVALID") from exc
    if _contains_ephemeral_scope(phases):
        raise ConfigLastGoodPlanError("CONFIG_LIFECYCLE_EPHEMERAL_SCOPE_FORBIDDEN")

    try:
        snapshot = resolve_config_phases(phases)
    except Exception as exc:
        raise ConfigLastGoodPlanError("CONFIG_LIFECYCLE_CONFIG_INVALID") from exc

    payload = _canonical_payload(snapshot)
    warnings = [str(item) for item in snapshot.get("warnings", [])]
    fallback_used = bool(warnings)
    decision = "retain-existing" if fallback_used else "write"

    return {
        "apply_authority": "blocked",
        "candidate": {
            "config_hash": str(snapshot["config_hash"]),
            "payload_bytes": len(payload),
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
            "schema_version": int(snapshot["schema_version"]),
            "warnings": warnings,
        },
        "claim": CLAIM,
        "contract_version": CONTRACT_VERSION,
        "decision": decision,
        "fallback_used": fallback_used,
        "full_product_parity": "FULL_PARITY_NOT_PROVEN",
        "input": {
            "config_wire_bytes": len(raw_wire),
            "config_wire_sha256": hashlib.sha256(raw_wire).hexdigest(),
            "ephemeral_scopes_forbidden": list(EPHEMERAL_SCOPES),
            "format": "R6CFG1",
            "persistent_scopes": list(PERSISTENT_SCOPES),
        },
        "mutation": {
            "database_opened": False,
            "filesystem": False,
        },
        "ok": True,
        "plan_id": PLAN_ID,
        "project_binding": {
            "actual": actual_project_id,
            "expected": expected_project_id,
            "matched": True,
        },
        "project_id": actual_project_id,
        "schema_version": SCHEMA_VERSION,
        "target": {
            "atomic_replace_required": True,
            "file_mode": "0600",
            "relative_path": TARGET_RELATIVE_PATH,
        },
    }


def canonical_plan_json(plan: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(plan),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Produce the no-write R25 config last-good lifecycle plan."
    )
    parser.add_argument("expected_project_id")
    parser.add_argument("project_root", type=Path)
    parser.add_argument("config_wire_hex")
    args = parser.parse_args(argv)
    try:
        wire = bytes.fromhex(args.config_wire_hex)
        result = config_last_good_plan(
            project_root=args.project_root,
            expected_project_id=args.expected_project_id,
            config_wire=wire,
        )
    except (ValueError, ConfigLastGoodPlanError) as exc:
        code = exc.code if isinstance(exc, ConfigLastGoodPlanError) else "CONFIG_LIFECYCLE_WIRE_HEX_INVALID"
        print(code)
        return 2
    print(canonical_plan_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
