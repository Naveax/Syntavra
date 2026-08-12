#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import traceback
from collections import Counter
from pathlib import Path
from typing import Any

from syntavra_runtime.integration_matrix import IntegrationMatrix
from syntavra_runtime.mcp_server import MCPServer
from syntavra_runtime.tool_registry import BALANCED_TOOLS, MINIMAL_TOOLS, normalize_profile
from syntavra_runtime.util import canonical_json
from tools import report_missing_native_public_routes as public_surface
from tools import report_python_public_execution_contract as execution_contract

FIXTURE_RELATIVE = Path("contracts/python/mcp-integration-reference-v1.json")


def _head(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _run(
    repo: Path,
    project: Path,
    state: Path,
    argv: list[str],
    *,
    env_extra: dict[str, str] | None = None,
) -> dict[str, Any]:
    env = os.environ.copy()
    env.update({"PYTHONPATH": str(repo), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    if env_extra:
        env.update(env_extra)
    env.pop("SYNTAVRA_BULK_PARITY_PROBE", None)
    proc = subprocess.run(
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
            *argv,
        ],
        cwd=repo,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=90,
        check=False,
    )
    try:
        value = json.loads(proc.stdout) if proc.stdout.strip() else None
    except json.JSONDecodeError:
        value = None
    return {"exit": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr, "value": value}


def _json(label: str, result: dict[str, Any], exit_code: int = 0) -> dict[str, Any]:
    if result["exit"] != exit_code or result["stderr"]:
        raise AssertionError(f"{label}: expected clean exit {exit_code}, got {result}")
    if not isinstance(result["value"], dict):
        raise AssertionError(f"{label}: expected JSON object stdout, got {result}")
    return result["value"]


def _routes(fixture: dict[str, Any]) -> dict[str, Any]:
    expected = fixture["public_routes"]
    expected_set = set(expected)
    observed = sorted(route for route in public_surface.python_public_route_sources() if route in expected_set)
    if observed != expected:
        raise AssertionError(f"MCP/integration route inventory drift: observed={observed}, expected={expected}")

    execution = {row["route"]: row for row in execution_contract.route_execution_manifest()}
    owners: dict[str, str] = {}
    for route in expected:
        row = execution.get(route)
        if not row or len(row["entrypoints"]) != 1 or row["unknown_sources"]:
            raise AssertionError(f"MCP/integration execution ownership drift: {row}")
        owners[route] = row["entrypoint"]
    return {
        "routes": expected,
        "route_count": len(expected),
        "route_sha256": public_surface._digest(expected),
        "ownership": owners,
    }


def _catalog_snapshot(repo: Path, project: Path, state: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    server = MCPServer(
        project=project,
        state_root=state,
        skill_root=repo / "skills" / "syntavra",
        codex_home=project / ".codex",
        host="codex",
    )
    catalog = server.tools()
    names = [str(row.get("name") or "") for row in catalog]
    if len(names) != len(set(names)) or any(not name for name in names):
        raise AssertionError("MCP full tool catalog contains duplicate/empty names")

    full_sha = hashlib.sha256(canonical_json(catalog)).hexdigest()
    name_sha = hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest()
    expected = fixture["tool_inventory"]
    if expected.get("count") is not None and expected["count"] != len(catalog):
        raise AssertionError(f"MCP tool inventory count drift: {len(catalog)} != {expected['count']}")
    if expected.get("sha256") is not None and expected["sha256"] != full_sha:
        raise AssertionError(f"MCP tool inventory digest drift: {full_sha} != {expected['sha256']}")

    by_name = {row["name"]: row for row in catalog}
    selected: dict[str, Any] = {}
    for name in (
        "syntavra.status",
        "syntavra.context.evaluate",
        "syntavra.sandbox.execute",
        "syntavra.session.open",
    ):
        row = by_name.get(name)
        if row is None:
            raise AssertionError(f"MCP selected tool disappeared: {name}")
        schema = row.get("inputSchema") or {}
        selected[name] = {
            "description": row.get("description"),
            "input_schema_type": schema.get("type"),
            "required": list(schema.get("required") or []),
            "property_keys": sorted((schema.get("properties") or {}).keys()),
        }

    profiles = fixture["profiles"]
    if len(MINIMAL_TOOLS) != profiles["minimal_exposed_tools"]:
        raise AssertionError("MCP minimal profile tool-count drift")
    if len(BALANCED_TOOLS) != profiles["balanced_exposed_tools"]:
        raise AssertionError("MCP balanced profile tool-count drift")
    for alias, canonical in profiles["legacy_alias"].items():
        observed_alias = normalize_profile(alias)
        if observed_alias != canonical:
            raise AssertionError(f"MCP legacy profile alias drift: {alias} -> {observed_alias} != {canonical}")

    return {
        "count": len(catalog),
        "sha256": full_sha,
        "name_sha256": name_sha,
        "first_names": names[:5],
        "last_names": names[-5:],
        "selected_schemas": selected,
        "profile_counts": {"minimal": len(MINIMAL_TOOLS), "balanced": len(BALANCED_TOOLS)},
    }


def _mcp_stdio(repo: Path, project: Path, state: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    valid_requests = [
        {"jsonrpc": "2.0", "id": "init", "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": "ping", "method": "ping"},
        {"jsonrpc": "2.0", "id": "list", "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": "call", "method": "tools/call", "params": {"name": "syntavra.status", "arguments": {}}},
        {"jsonrpc": "2.0", "id": "denied", "method": "tools/call", "params": {"name": "syntavra.sandbox.execute", "arguments": {"argv": ["echo", "fixture"]}}},
        {"jsonrpc": "2.0", "id": "unknown", "method": "syntavra/no-such-method", "params": {}},
    ]
    raw_lines = [json.dumps(row, separators=(",", ":")) for row in valid_requests]
    raw_lines.extend(
        [
            '{"jsonrpc":',
            "[]",
            json.dumps({"jsonrpc": "2.0", "id": "bad-init", "method": "initialize", "params": []}, separators=(",", ":")),
            json.dumps({"jsonrpc": "2.0", "id": "post-errors-ping", "method": "ping"}, separators=(",", ":")),
        ]
    )
    stdin = "\n".join(raw_lines) + "\n"

    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(repo),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "SYNTAVRA_MCP_PROFILE": "minimal",
            "SYNTAVRA_SCHEMA_MODE": "compact",
            "SYNTAVRA_WIRE_MODE": "off",
        }
    )
    proc = subprocess.run(
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
            "--skill-root",
            str(repo / "skills" / "syntavra"),
            "--codex-home",
            str(project / ".codex"),
            "--host",
            "codex",
            "mcp",
            "serve",
        ],
        cwd=repo,
        env=env,
        input=stdin,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=90,
        check=False,
    )
    if proc.returncode != 0 or proc.stderr:
        raise AssertionError(
            f"MCP stdio lifecycle drift: exit={proc.returncode}, stderr={proc.stderr!r}, stdout={proc.stdout!r}"
        )

    responses = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    if len(responses) != 10:
        raise AssertionError(f"MCP notification/response count drift: {responses}")
    by_id = {row.get("id"): row for row in responses if isinstance(row, dict) and row.get("id") is not None}
    null_id_errors = [row for row in responses if isinstance(row, dict) and row.get("id") is None and "error" in row]

    init = by_id.get("init") or {}
    if init.get("result", {}).get("protocolVersion") != "2025-06-18":
        raise AssertionError(f"MCP initialize protocol drift: {init}")
    if init.get("result", {}).get("serverInfo") != {"name": "syntavra", "version": "0.0.1"}:
        raise AssertionError(f"MCP initialize serverInfo drift: {init}")
    if by_id.get("ping", {}).get("result") != {}:
        raise AssertionError(f"MCP ping drift: {by_id.get('ping')}")

    listed = by_id.get("list", {}).get("result", {}).get("tools") or []
    listed_names = [row.get("name") for row in listed]
    if listed_names != list(MINIMAL_TOOLS):
        raise AssertionError(f"MCP minimal tools/list ordering drift: {listed_names}")

    call = by_id.get("call") or {}
    result = call.get("result") or {}
    meta = result.get("_meta") or {}
    content = result.get("content") or []
    if not content or content[0].get("type") != "text":
        raise AssertionError(f"MCP tools/call content drift: {call}")
    parsed_status = json.loads(content[0]["text"])
    if not isinstance(parsed_status, dict):
        raise AssertionError(f"MCP status payload drift: {call}")
    if meta.get("syntavra_profile") != "minimal" or meta.get("syntavra_risk") != "read-or-plan":
        raise AssertionError(f"MCP status call metadata drift: {call}")

    denied = by_id.get("denied") or {}
    denied_error = denied.get("error") or {}
    denied_reason = (denied_error.get("data") or {}).get("reason")
    if denied_error.get("code") != fixture["jsonrpc_errors"]["policy_denied"]:
        raise AssertionError(f"MCP policy-denial code drift: {denied}")
    if denied_reason != "tool-not-exposed-by-active-profile":
        raise AssertionError(f"MCP policy-denial reason drift: {denied}")

    unknown = by_id.get("unknown") or {}
    if unknown.get("error") != {"code": fixture["jsonrpc_errors"]["method_not_found"], "message": "Method not found"}:
        raise AssertionError(f"MCP unknown-method drift: {unknown}")

    parse_error = next(
        (row for row in null_id_errors if row.get("error", {}).get("code") == fixture["jsonrpc_errors"]["parse_error"]),
        None,
    )
    invalid_request = next(
        (row for row in null_id_errors if row.get("error", {}).get("code") == fixture["jsonrpc_errors"]["invalid_request"]),
        None,
    )
    if parse_error is None or parse_error.get("error") != {"code": -32700, "message": "Parse error"}:
        raise AssertionError(f"MCP parse-error drift: {parse_error}")
    if invalid_request is None or invalid_request.get("error") != {"code": -32600, "message": "Invalid Request"}:
        raise AssertionError(f"MCP invalid-request drift: {invalid_request}")

    bad_init = by_id.get("bad-init") or {}
    if bad_init.get("error") != {"code": fixture["jsonrpc_errors"]["invalid_parameters"], "message": "Invalid params"}:
        raise AssertionError(f"MCP initialize invalid-params drift: {bad_init}")
    if by_id.get("post-errors-ping", {}).get("result") != {}:
        raise AssertionError(f"MCP server did not survive malformed request sequence: {by_id.get('post-errors-ping')}")

    return {
        "exit": proc.returncode,
        "response_count": len(responses),
        "notification_response_count": 0,
        "initialize": {
            "protocol_version": init["result"]["protocolVersion"],
            "capabilities": init["result"]["capabilities"],
            "server_info": init["result"]["serverInfo"],
            "instructions": init["result"]["instructions"],
        },
        "ping_result": by_id["ping"]["result"],
        "tools_list": {"count": len(listed), "names": listed_names},
        "status_call": {
            "content_type": content[0]["type"],
            "profile": meta["syntavra_profile"],
            "risk": meta["syntavra_risk"],
            "schema_mode": meta["syntavra_schema_mode"],
            "route_receipt_shape": len(str(meta.get("syntavra_route_receipt") or "")) == 64,
            "status_object": True,
        },
        "denied_call": {"code": denied_error["code"], "reason": denied_reason},
        "unknown_method": unknown["error"],
        "parse_error": parse_error["error"],
        "invalid_request": invalid_request["error"],
        "invalid_parameters": bad_init["error"],
        "post_error_ping_result": by_id["post-errors-ping"]["result"],
    }


def _integrations(repo: Path, project: Path, state: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    records = IntegrationMatrix.records()
    family_counts = dict(sorted(Counter(str(row["family"]) for row in records).items()))
    digest = hashlib.sha256(canonical_json(records)).hexdigest()
    expected = fixture["integration_inventory"]
    if expected.get("count") is not None and expected["count"] != len(records):
        raise AssertionError(f"integration count drift: {len(records)} != {expected['count']}")
    if expected.get("family_counts") is not None and expected["family_counts"] != family_counts:
        raise AssertionError(f"integration family-count drift: {family_counts} != {expected['family_counts']}")
    if expected.get("sha256") is not None and expected["sha256"] != digest:
        raise AssertionError(f"integration digest drift: {digest} != {expected['sha256']}")

    all_output = _json("integrations all", _run(repo, project, state, ["integrations"]))
    if all_output.get("integrations") != records:
        raise AssertionError("integrations all record output drift")
    if all_output.get("coverage") != IntegrationMatrix.validate():
        raise AssertionError("integrations coverage output drift")

    filtered_counts: dict[str, int] = {}
    for family in ("provider", "framework", "host"):
        output = _json(f"integrations {family}", _run(repo, project, state, ["integrations", "--family", family]))
        expected_rows = IntegrationMatrix.records(family)
        if output.get("integrations") != expected_rows:
            raise AssertionError(f"integration family filter drift: {family}")
        filtered_counts[family] = len(expected_rows)
    if filtered_counts != family_counts:
        raise AssertionError(f"integration filtered-count drift: {filtered_counts} / {family_counts}")

    return {
        "count": len(records),
        "family_counts": family_counts,
        "sha256": digest,
        "first_ids": [row["integration_id"] for row in records[:5]],
        "last_ids": [row["integration_id"] for row in records[-5:]],
        "coverage": all_output["coverage"],
        "platform_adapters": all_output["platform_adapters"],
        "proxy_presets": all_output["proxy_presets"],
        "family_filters": filtered_counts,
    }


def _route_policy(repo: Path, project: Path, state: Path) -> dict[str, Any]:
    status = _json(
        "route minimal status",
        _run(repo, project, state, ["run", "route", "syntavra.status", "--profile", "minimal"]),
    )
    if status.get("allowed") is not True or status.get("reason") != "policy-allowed" or status.get("category") != "read":
        raise AssertionError(f"run route status drift: {status}")

    hidden = _json(
        "route minimal execute",
        _run(repo, project, state, ["run", "route", "syntavra.sandbox.execute", "--profile", "minimal"]),
    )
    if hidden.get("allowed") is not False or hidden.get("reason") != "tool-not-in-active-profile":
        raise AssertionError(f"run route profile denial drift: {hidden}")

    no_auth = _json(
        "route balanced no auth",
        _run(
            repo,
            project,
            state,
            ["run", "route", "syntavra.sandbox.execute", "--profile", "balanced", "--sandboxed"],
        ),
    )
    if no_auth.get("allowed") is not False or no_auth.get("reason") != "explicit-user-authorization-required":
        raise AssertionError(f"run route authorization denial drift: {no_auth}")

    allowed = _json(
        "route balanced allowed",
        _run(
            repo,
            project,
            state,
            [
                "run",
                "route",
                "syntavra.sandbox.execute",
                "--profile",
                "balanced",
                "--sandboxed",
                "--user-authorized",
            ],
        ),
    )
    if allowed.get("allowed") is not True or allowed.get("reason") != "policy-allowed" or allowed.get("category") != "execute":
        raise AssertionError(f"run route allowed execute drift: {allowed}")

    unknown = _json(
        "route unknown tool",
        _run(repo, project, state, ["run", "route", "mystery.tool", "--profile", "balanced"]),
    )
    if unknown.get("allowed") is not False:
        raise AssertionError(f"run route unknown-tool fail-closed drift: {unknown}")

    return {
        "minimal_status": status,
        "minimal_execute_denied": hidden,
        "balanced_execute_no_auth": no_auth,
        "balanced_execute_allowed": allowed,
        "unknown_tool": unknown,
    }


def certify(repo: Path) -> dict[str, Any]:
    fixture_path = repo / FIXTURE_RELATIVE
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    if fixture["jsonrpc_methods"] != ["initialize", "notifications/initialized", "ping", "tools/call", "tools/list"]:
        raise AssertionError("MCP JSON-RPC method fixture drift")

    with tempfile.TemporaryDirectory(prefix="syntavra-python-mcp-integration-") as directory:
        root = Path(directory)
        project = root / "project"
        state = root / "state"
        project.mkdir()
        state.mkdir()
        (project / ".git").mkdir()
        (project / ".codex").mkdir()
        result = {
            "routes": _routes(fixture),
            "tool_inventory": _catalog_snapshot(repo, project, state, fixture),
            "stdio": _mcp_stdio(repo, project, state, fixture),
            "integrations": _integrations(repo, project, state, fixture),
            "route_policy": _route_policy(repo, project, state),
        }

    return {
        "ok": True,
        "schema_version": 1,
        "family": "mcp-integration",
        "engine": "python",
        "exact_head": _head(repo),
        "fixture": str(FIXTURE_RELATIVE).replace("\\", "/"),
        "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        **result,
        "jsonrpc_methods": fixture["jsonrpc_methods"],
        "jsonrpc_errors": fixture["jsonrpc_errors"],
        "exit_policy": fixture["exit_policy"],
        "network_boundary": fixture["network_boundary"],
        "ownership_notes": fixture["ownership_notes"],
        "nondeterministic_fields": [
            "MCP route receipt hashes when authorization input/state changes",
            "status payload timestamps/runtime state nested below successful tool calls",
            "temporary project/state paths",
            "host/platform adapter environment probes nested under integration metadata where applicable",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Python MCP/integration reference behavior")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output")
    args = parser.parse_args()
    repo = Path(args.repo).resolve(strict=True)
    try:
        result = certify(repo)
    except Exception as exc:
        result = {
            "ok": False,
            "schema_version": 1,
            "family": "mcp-integration",
            "engine": "python",
            "exact_head": _head(repo),
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
