#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESCRIPTOR = ROOT / "contracts" / "engine" / "descriptor.txt"
RUST_CONTRACTS = ROOT / "crates" / "syntavra-contracts" / "src" / "lib.rs"
CURRENT_ROUTING = ROOT / "contracts" / "engine" / "read-only-routing-v7.json"
CONTRACT_JSON = (
    ROOT / "contracts" / "engine" / "capabilities.schema.json",
    ROOT / "contracts" / "engine" / "read-only-routing-v1.json",
    ROOT / "contracts" / "engine" / "read-only-routing-v2.json",
    ROOT / "contracts" / "engine" / "read-only-routing-v3.json",
    ROOT / "contracts" / "engine" / "read-only-routing-v4.json",
    ROOT / "contracts" / "engine" / "read-only-routing-v5.json",
    ROOT / "contracts" / "engine" / "read-only-routing-v6.json",
    CURRENT_ROUTING,
    ROOT / "contracts" / "engine" / "selection.schema.json",
    ROOT / "contracts" / "cli" / "result-envelope.schema.json",
    ROOT / "contracts" / "mcp" / "tool-catalog.schema.json",
    ROOT / "contracts" / "state" / "layout.json",
    ROOT / "contracts" / "receipts" / "common.schema.json",
    ROOT / "parity" / "normalizers" / "default.json",
)


def _rust_descriptor() -> str:
    source = RUST_CONTRACTS.read_text(encoding="utf-8")
    marker = "pub const CONTRACT_DESCRIPTOR: &str = concat!("
    if marker not in source:
        raise RuntimeError("Rust contract descriptor marker is missing")
    block = source.split(marker, 1)[1].split(");", 1)[0]
    literals = re.findall(r'"((?:\\.|[^"\\])*)"', block)
    if not literals:
        raise RuntimeError("Rust contract descriptor contains no string literals")
    return "".join(json.loads(f'"{value}"') for value in literals)


def _descriptor_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    capabilities: list[str] = []
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if not separator or not key or not value:
            raise RuntimeError(f"invalid descriptor line: {line!r}")
        if key == "capability":
            capabilities.append(value)
        elif key in fields:
            raise RuntimeError(f"duplicate descriptor key: {key}")
        else:
            fields[key] = value
    fields["capabilities"] = "\n".join(capabilities)
    return fields


def verify() -> dict[str, object]:
    descriptor = DESCRIPTOR.read_text(encoding="utf-8")
    if not descriptor.endswith("\n"):
        raise RuntimeError("contract descriptor must be newline terminated")
    rust_descriptor = _rust_descriptor()
    if descriptor != rust_descriptor:
        raise RuntimeError("Rust embedded descriptor differs from contracts/engine/descriptor.txt")

    fields = _descriptor_fields(descriptor)
    if fields.get("product") != "Syntavra":
        raise RuntimeError("unexpected product identity")
    if fields.get("product_version") != "0.0.1":
        raise RuntimeError("product version changed without owner authorization")
    if fields.get("release_channel") != "pre-release":
        raise RuntimeError("release channel changed without owner authorization")
    if fields.get("contract_version") != "1":
        raise RuntimeError("unexpected initial contract version")

    capability_rows = fields["capabilities"].splitlines()
    if capability_rows != sorted(capability_rows):
        raise RuntimeError("descriptor capabilities must be sorted")

    parsed_contracts: list[str] = []
    for path in CONTRACT_JSON:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError(f"contract must be a JSON object: {path}")
        parsed_contracts.append(path.relative_to(ROOT).as_posix())

    selection_path = ROOT / "contracts" / "engine" / "selection.schema.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    engine_enum = selection.get("properties", {}).get("engine", {}).get("enum")
    if engine_enum != ["auto", "python", "rust"]:
        raise RuntimeError("engine selection enum or order drifted")

    routing = json.loads(CURRENT_ROUTING.read_text(encoding="utf-8"))
    route_names = [
        row.get("command")
        for row in routing.get("routes", [])
        if isinstance(row, dict)
    ]
    if routing.get("schema_version") != 7 or routing.get("phase") != "R18":
        raise RuntimeError("current read-only routing schema or phase drifted")
    if route_names != [
        "config.resolve",
        "state.inspect",
        "state.layout",
        "status",
        "version",
    ]:
        raise RuntimeError("current read-only route inventory drifted")
    if routing.get("maximum_input_bytes") != 262144:
        raise RuntimeError("current read-only routing input bound drifted")
    if routing.get("maximum_config_file_bytes") != 131072:
        raise RuntimeError("current live config file bound drifted")
    if routing.get("maximum_override_json_bytes") != 65536:
        raise RuntimeError("current transient override bound drifted")
    if routing.get("maximum_state_file_bytes") != 1048576:
        raise RuntimeError("current state inspection file bound drifted")

    input_metadata = routing.get("input_metadata", {})
    if input_metadata.get("raw_input_forbidden") is not True:
        raise RuntimeError("current routing contract must forbid raw input metadata")
    if input_metadata.get("source_paths_forbidden") is not True:
        raise RuntimeError("current routing contract must forbid source paths")

    result_policy = routing.get("result_policy", {})
    if result_policy.get("parity_error_values_forbidden") is not True:
        raise RuntimeError("current routing contract must forbid parity error values")
    if result_policy.get("raw_override_forbidden") is not True:
        raise RuntimeError("current routing contract must forbid raw overrides")
    if result_policy.get("project_root_forbidden") is not True:
        raise RuntimeError("current routing contract must forbid project-root exposure")

    inspect_policy = routing.get("state_inspect_route", {})
    if inspect_policy.get("python_authority") != (
        "syntavra_runtime.state_snapshot_contract.inspect_state_root"
    ):
        raise RuntimeError("state.inspect Python authority drifted")
    if inspect_policy.get("rust_capability") != "state.inspect":
        raise RuntimeError("state.inspect Rust capability drifted")
    if inspect_policy.get("project_root_source") != "installed-cli---project":
        raise RuntimeError("state.inspect project-root source drifted")
    if inspect_policy.get("project_id_derivation") != (
        "sha256-normalized-canonical-absolute-path"
    ):
        raise RuntimeError("state.inspect project binding drifted")
    if inspect_policy.get("project_root_symlink") != "reject-before-selection":
        raise RuntimeError("state.inspect root-symlink policy drifted")
    if inspect_policy.get("known_paths_only") is not True:
        raise RuntimeError("state.inspect must remain limited to known paths")
    if inspect_policy.get("recursive_directory_read") is not False:
        raise RuntimeError("state.inspect must not recursively inspect directories")
    if inspect_policy.get("maximum_file_bytes") != 1048576:
        raise RuntimeError("state.inspect file bound drifted")
    if inspect_policy.get("database_access") is not False:
        raise RuntimeError("state.inspect must not access databases")
    if inspect_policy.get("mutation") is not False:
        raise RuntimeError("state.inspect must remain non-mutating")
    if inspect_policy.get("source_path_in_envelope") is not False:
        raise RuntimeError("state.inspect must not expose source paths")
    if inspect_policy.get("source_path_in_error") is not False:
        raise RuntimeError("state.inspect errors must not expose source paths")
    if inspect_policy.get("comparison") != "exact-complete-object":
        raise RuntimeError("state.inspect comparison policy drifted")

    layout_policy = routing.get("state_layout_route", {})
    if layout_policy.get("python_authority") != (
        "syntavra_runtime.state_receipt_contract.state_layout"
    ):
        raise RuntimeError("state.layout Python authority drifted")
    if layout_policy.get("rust_capability") != "state.layout":
        raise RuntimeError("state.layout Rust capability drifted")
    if layout_policy.get("filesystem_access") is not False:
        raise RuntimeError("state.layout must not access the filesystem")
    if layout_policy.get("database_access") is not False:
        raise RuntimeError("state.layout must not access databases")
    if layout_policy.get("mutation") is not False:
        raise RuntimeError("state.layout must remain non-mutating")
    if layout_policy.get("input_profile") != "none":
        raise RuntimeError("state.layout input profile drifted")
    if layout_policy.get("comparison") != "exact-complete-object":
        raise RuntimeError("state.layout comparison policy drifted")

    live_discovery = routing.get("live_discovery", {})
    if live_discovery.get("owner") != "python-router":
        raise RuntimeError("live config discovery owner drifted")
    if live_discovery.get("read_only") is not True:
        raise RuntimeError("live config discovery must remain read-only")
    if live_discovery.get("last_good_write") is not False:
        raise RuntimeError("live config discovery must not write last-good state")
    if live_discovery.get("rust_filesystem_access") is not False:
        raise RuntimeError("Rust must not discover configuration files")
    if live_discovery.get("rust_environment_access") is not False:
        raise RuntimeError("Rust must not discover configuration environment")

    overrides = routing.get("session_task_overrides", {})
    if overrides.get("owner") != "python-router":
        raise RuntimeError("session/task override owner drifted")
    if overrides.get("require_live_config") is not True:
        raise RuntimeError("session/task overrides must require live discovery")
    if overrides.get("format") != "canonical-json-hex":
        raise RuntimeError("session/task override format drifted")
    if overrides.get("scopes") != ["session", "task"]:
        raise RuntimeError("session/task override scopes drifted")
    if overrides.get("precedence") != ["session", "task"]:
        raise RuntimeError("session/task precedence drifted")
    if overrides.get("maximum_bytes_per_scope") != 65536:
        raise RuntimeError("session/task override size drifted")
    if overrides.get("raw_input_in_envelope") is not False:
        raise RuntimeError("raw session/task override input must remain hidden")
    if overrides.get("rust_decodes_override_json") is not False:
        raise RuntimeError("Rust must not decode transient override JSON")
    if overrides.get("rust_receives_final_r6cfg1_only") is not True:
        raise RuntimeError("Rust must receive only the final canonical config wire")

    route_by_name = {
        str(row.get("command")): row
        for row in routing.get("routes", [])
        if isinstance(row, dict)
    }
    config_profiles = route_by_name.get("config.resolve", {}).get(
        "accepted_input_profiles"
    )
    if config_profiles != [
        "explicit-config-wire-v1",
        "live-config-discovery-v1",
        "live-config-session-task-v1",
    ]:
        raise RuntimeError("config.resolve input profile drifted")
    inspect_profiles = route_by_name.get("state.inspect", {}).get(
        "accepted_input_profiles"
    )
    if inspect_profiles != ["project-bound-state-root-v1"]:
        raise RuntimeError("state.inspect input profile drifted")
    layout_profiles = route_by_name.get("state.layout", {}).get(
        "accepted_input_profiles"
    )
    if layout_profiles != ["none"]:
        raise RuntimeError("state.layout input profile drifted")
    status_profiles = route_by_name.get("status", {}).get(
        "accepted_input_profiles"
    )
    if status_profiles != [
        "default-config-only",
        "explicit-config-wire-v1",
        "live-config-discovery-v1",
        "live-config-session-task-v1",
    ]:
        raise RuntimeError("status input profile drifted")

    return {
        "ok": True,
        "contract_version": int(fields["contract_version"]),
        "descriptor_sha256": hashlib.sha256(descriptor.encode("utf-8")).hexdigest(),
        "capabilities": capability_rows,
        "engine_modes": engine_enum,
        "routing_schema_version": routing["schema_version"],
        "routing_phase": routing["phase"],
        "routing_routes": route_names,
        "routing_live_profiles": {
            "config.resolve": config_profiles,
            "state.inspect": inspect_profiles,
            "state.layout": layout_profiles,
            "status": status_profiles,
        },
        "routing_override_scopes": overrides["scopes"],
        "json_contracts": parsed_contracts,
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
