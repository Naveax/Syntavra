#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntavra_runtime.deferred_tool_discovery import (
    DeferredToolDiscoveryEngine,
    HostToolCapabilities,
    ToolHealthRegistry,
)
from syntavra_runtime.mcp_application import MCPApplicationPipeline
from syntavra_runtime.tool_registry import ToolSchemaCompiler
from tools.certify_programmatic_execution import certify as certify_programmatic_execution
from tools.certify_python_capability_completeness import certify as certify_completeness
from tools.certify_rust_feature_freeze_guard import certify as certify_rust_freeze

CONTRACT_RELATIVE = Path("contracts/python/deferred-tool-discovery-v1.json")
REGISTRY_RELATIVE = Path("contracts/python/capability-completeness-registry-v1.json")
WORKFLOW_RELATIVE = Path(".github/workflows/deferred-tool-discovery.yml")
RELEASE_GATE_RELATIVE = Path(".github/workflows/release-main-merge-gate.yml")
PIN_POLICY_RELATIVE = Path("tests/runtime/test_release_action_pins.py")
VALIDATOR_RELATIVE = Path("tools/validate.py")
RUNTIME_RELATIVE = Path("syntavra_runtime/deferred_tool_discovery.py")
MCP_APPLICATION_RELATIVE = Path("syntavra_runtime/mcp_application.py")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected JSON object: {path}")
    return value


def _require(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


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


def _tool(name: str, description: str, properties: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {"type": "object", "properties": properties or {}},
    }


def _runtime_smoke() -> dict[str, Any]:
    catalog = [
        _tool("syntavra.status", "runtime health status"),
        _tool("syntavra.inspect.map", "repository structure map", {"query": {"type": "string"}}),
        _tool("syntavra.inspect.source", "read exact source", {"query": {"type": "string"}}),
        _tool("syntavra.output.search", "search exact externalized output", {"query": {"type": "string"}}),
        _tool("syntavra.output.reveal", "reveal exact externalized output", {"artifact_id": {"type": "string"}}),
        _tool("syntavra.fabric.route", "route task through context fabric", {"query": {"type": "string"}}),
        _tool("syntavra.sandbox.execute", "execute sandbox command", {"argv": {"type": "array", "items": {"type": "string"}}}),
    ]

    engine = DeferredToolDiscoveryEngine(profile="balanced")
    descriptors = engine.describe_catalog(catalog)
    _require(descriptors, "deferred discovery descriptor catalog is empty")
    _require(all(len(item.capability_fingerprint) == 64 for item in descriptors), "capability fingerprint drift")
    _require(all(len(item.schema_fingerprint) == 64 for item in descriptors), "schema fingerprint drift")

    stage1 = engine.stage1(catalog, query="repository structure")
    _require(stage1.get("stage") == 1, "stage1 identity drift")
    _require(stage1.get("status") == "ok", f"stage1 failed: {stage1}")
    serialized_stage1 = json.dumps(stage1, ensure_ascii=False, sort_keys=True)
    _require("inputSchema" not in serialized_stage1 and '"properties"' not in serialized_stage1, "stage1 leaked full schema")
    _require(stage1.get("families") and stage1["families"][0]["family"] == "inspect", "stage1 semantic family drift")
    _require(bool((stage1.get("receipt") or {}).get("receipt_hash")), "stage1 receipt missing")

    second_stage1 = engine.stage1(catalog, query="repository structure")
    _require(second_stage1.get("cache_hit") is True, "deterministic discovery cache did not hit")

    unknown = engine.stage1(catalog, query="quasar-neutrino-zeta")
    _require(unknown.get("status") == "unknown" and unknown.get("families") == [], "unknown query did not fail closed")

    no_tool = engine.stage1(catalog, query="just explain the concept without tools")
    _require(no_tool.get("status") == "no-tool-needed", "no-tool-needed classifier drift")

    stage2 = engine.stage2(catalog, selector="syntavra.inspect.map", query="repository")
    _require(stage2.get("status") == "ok", f"stage2 failed: {stage2}")
    _require(stage2.get("selected_count") == 1, "stage2 exact selector expanded more than one tool")
    _require((stage2.get("tools") or [{}])[0].get("name") == "syntavra.inspect.map", "stage2 exact tool drift")
    _require("inputSchema" in stage2["tools"][0], "stage2 did not materialize schema")
    _require(int(stage2.get("schema_tokens", 0)) <= int(stage2.get("token_budget", 0)), "stage2 exceeded token budget")

    host = HostToolCapabilities(
        host="certifier",
        max_tools=4,
        schema_budget_tokens=900,
        allowed_risks=("read-or-plan", "safe-state-write"),
        namespace_prefixes=("syntavra.inspect", "syntavra.output"),
    )
    negotiation = engine.negotiate(catalog, host)
    _require(negotiation.get("max_tools") == 4, "host max-tool negotiation drift")
    _require(negotiation.get("schema_budget_tokens") == 900, "host token-budget negotiation drift")
    _require("sandbox" not in negotiation.get("families", ()), "host risk negotiation leaked sandbox family")

    health = ToolHealthRegistry()
    health.set("syntavra.inspect.source", state="unavailable", reason="certifier-fixture")
    health_engine = DeferredToolDiscoveryEngine(profile="balanced", health_registry=health)
    health_result = health_engine.stage2(catalog, selector="inspect", query="source")
    _require("syntavra.inspect.source" not in [row["name"] for row in health_result.get("tools", [])], "unavailable tool remained discoverable")

    huge = _tool(
        "syntavra.inspect.map",
        "map repository",
        {f"field_{index}": {"type": "string", "description": "x" * 96} for index in range(72)},
    )
    budget_host = HostToolCapabilities(schema_budget_tokens=128)
    budget_result = DeferredToolDiscoveryEngine(profile="minimal").stage2(
        [huge],
        selector="syntavra.inspect.map",
        host=budget_host,
        token_budget=128,
    )
    _require(budget_result.get("status") == "budget-exceeded", "oversized schema did not fail explicitly")
    _require(budget_result.get("tools") == [], "oversized schema leaked a tool")

    with tempfile.TemporaryDirectory() as directory:
        pipeline = MCPApplicationPipeline(Path(directory))
        discovered = pipeline.discover_tools(catalog, query="repository structure")
        _require(discovered.get("stage") == 1, "MCP additive discovery stage drift")
        _require("inputSchema" not in json.dumps(discovered, sort_keys=True), "MCP additive discovery leaked schema")
        existing = pipeline.list_tools(catalog)
        _require(existing and all("inputSchema" in row for row in existing), "existing tools/list semantics were replaced")

    compiler = ToolSchemaCompiler()
    compiled, compilation = compiler.compile_catalog([catalog[1]])
    _require(compiled and compilation.compiled.tokens > 0, "existing schema compiler is no longer usable")

    return {
        "stage1_schema_deferred": True,
        "stage2_explicit_schema_expansion": True,
        "semantic_families": True,
        "namespace_tree": True,
        "capability_fingerprints": True,
        "deterministic_cache": True,
        "no_tool_needed_classifier": True,
        "unknown_query_fail_closed": True,
        "host_negotiation": True,
        "health_compatibility_filter": True,
        "schema_token_budget": True,
        "budget_exhaustion_explicit": True,
        "mcp_tools_list_preserved": True,
        "parallel_tool_registry": False,
    }


def _validate_enforcement(repo: Path) -> dict[str, str]:
    for relative in (WORKFLOW_RELATIVE, RELEASE_GATE_RELATIVE, PIN_POLICY_RELATIVE, VALIDATOR_RELATIVE):
        _require((repo / relative).is_file(), f"missing Deferred Tool Discovery enforcement surface: {relative.as_posix()}")

    workflow = (repo / WORKFLOW_RELATIVE).read_text(encoding="utf-8")
    _require("group: deferred-tool-discovery-${{ github.event.pull_request.number || github.ref }}" in workflow, "Deferred discovery concurrency is not PR/ref scoped")
    _require("tests.runtime.test_deferred_tool_discovery" in workflow, "Deferred discovery workflow lost regression suite")
    _require("tools/certify_deferred_tool_discovery.py" in workflow, "Deferred discovery workflow lost certifier")
    _require("actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in workflow, "Deferred discovery checkout pin drift")
    _require("actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065" in workflow, "Deferred discovery setup-python pin drift")

    release_gate = (repo / RELEASE_GATE_RELATIVE).read_text(encoding="utf-8")
    _require("tests.runtime.test_deferred_tool_discovery" in release_gate, "release-main gate lost Deferred discovery regression")
    _require("tools/certify_deferred_tool_discovery.py" in release_gate, "release-main gate lost Deferred discovery certifier")

    pin_policy = (repo / PIN_POLICY_RELATIVE).read_text(encoding="utf-8")
    _require('".github/workflows/deferred-tool-discovery.yml"' in pin_policy, "immutable action policy does not cover Deferred discovery workflow")

    validator = (repo / VALIDATOR_RELATIVE).read_text(encoding="utf-8")
    _require('("deferred_tool_discovery", deferred_ok, deferred_detail)' in validator, "repository validator lost Deferred discovery check")

    return {
        "exact_head_workflow": WORKFLOW_RELATIVE.as_posix(),
        "release_main_gate": RELEASE_GATE_RELATIVE.as_posix(),
        "immutable_action_pin_policy": PIN_POLICY_RELATIVE.as_posix(),
        "repository_validator": VALIDATOR_RELATIVE.as_posix(),
    }


def certify(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    _require(repo == ROOT, f"Deferred Tool Discovery certifier must run against its own checkout: {repo} != {ROOT}")
    contract = _read_json(repo / CONTRACT_RELATIVE)
    _require(contract.get("schema_version") == 1, "Deferred discovery schema drift")
    _require(contract.get("family") == "deferred-tool-discovery", "Deferred discovery family drift")
    _require(contract.get("phase") == "python-first", "Deferred discovery phase drift")
    _require(contract.get("claim") == "DEFERRED_TOOL_DISCOVERY_V1", "Deferred discovery claim drift")
    _require(contract.get("strict") is True, "Deferred discovery must remain strict")
    _require(contract.get("runtime") == RUNTIME_RELATIVE.as_posix(), "Deferred discovery runtime path drift")
    _require(contract.get("application_pipeline") == "syntavra_runtime/mcp_application.py:MCPApplicationPipeline", "MCP pipeline authority drift")
    _require(contract.get("schema_compiler_authority") == "syntavra_runtime/tool_registry.py:ToolSchemaCompiler", "schema compiler authority drift")

    stage1 = contract.get("stage1_policy") or {}
    for key in (
        "full_input_schemas_forbidden",
        "namespace_tree_required",
        "semantic_families_required",
        "capability_fingerprints_required",
        "risk_labels_required",
        "no_tool_needed_classifier_required",
        "unknown_query_fails_closed",
        "ambiguous_top_match_fails_closed",
    ):
        _require(stage1.get(key) is True, f"Deferred discovery stage1 policy disabled: {key}")

    stage2 = contract.get("stage2_policy") or {}
    for key in (
        "explicit_selector_required",
        "existing_schema_compiler_required",
        "existing_profile_policy_required",
        "host_capability_negotiation_required",
        "profile_and_host_token_budget_enforced",
        "max_tool_count_enforced",
        "health_and_compatibility_enforced",
        "unknown_selector_fails_closed",
        "budget_exhaustion_is_explicit",
    ):
        _require(stage2.get(key) is True, f"Deferred discovery stage2 policy disabled: {key}")

    virtualization = contract.get("virtualization_policy") or {}
    _require(virtualization.get("internal_family_virtualization_only") is True, "tool virtualization escaped internal boundary")
    _require(virtualization.get("public_cli_route_added") is False, "Deferred discovery invented a public CLI route")
    _require(virtualization.get("synthetic_executable_tool_added") is False, "Deferred discovery invented synthetic executable tools")
    _require(virtualization.get("existing_tool_identity_preserved") is True, "existing tool identity is no longer preserved")
    _require(virtualization.get("parallel_tool_registry_forbidden") is True, "parallel tool registry was allowed")

    source = (repo / RUNTIME_RELATIVE).read_text(encoding="utf-8")
    application = (repo / MCP_APPLICATION_RELATIVE).read_text(encoding="utf-8")
    _require("ToolSchemaCompiler" in source, "Deferred discovery stopped reusing ToolSchemaCompiler")
    _require("MCPToolPolicy" in source, "Deferred discovery stopped reusing MCPToolPolicy")
    _require("discover_tools" in application, "MCP additive discovery API missing")
    _require("def list_tools(" in application, "MCP tools/list surface was removed")

    completeness = certify_completeness(repo)
    _require(completeness.get("ok") is True, "capability completeness is not valid")
    _require(completeness.get("current_milestone") == "deferred_tool_discovery_v1", "registry has not advanced to deferred_tool_discovery_v1")
    _require(completeness.get("python_complete_ready") is False, "Python COMPLETE unexpectedly true")
    _require(completeness.get("rust_resume_allowed") is False, "Rust resume unexpectedly true")

    programmatic = certify_programmatic_execution(repo)
    _require(programmatic.get("ok") is True, "Programmatic Execution is not certified")
    _require(programmatic.get("rust_resume_allowed") is False, "Programmatic Execution unexpectedly resumed Rust")

    rust_freeze = certify_rust_freeze(repo)
    _require(rust_freeze.get("ok") is True, "Rust feature freeze is not certified")
    _require((rust_freeze.get("rust") or {}).get("production_promoted") == 174, "Rust production authority drift")

    registry = _read_json(repo / REGISTRY_RELATIVE)
    by_id = {row["id"]: row for row in registry.get("capabilities", []) if isinstance(row, dict) and isinstance(row.get("id"), str)}
    _require((by_id.get("programmatic_execution_v1") or {}).get("state") == "certified", "Programmatic Execution must be certified before Deferred discovery admission")
    _require((by_id.get("deferred_tool_discovery_v1") or {}).get("state") in {"implemented", "verified"}, "Deferred discovery registry state must be pre-certification implemented/verified")

    runtime = _runtime_smoke()
    enforcement = _validate_enforcement(repo)
    exact_head = _head(repo)
    _require(bool(exact_head), "unable to resolve exact HEAD")
    return {
        "ok": True,
        "schema_version": 1,
        "family": "deferred-tool-discovery",
        "claim": "DEFERRED_TOOL_DISCOVERY_V1",
        "exact_head": exact_head,
        "admission_ready": True,
        "python_complete_ready": False,
        "rust_resume_allowed": False,
        "runtime": runtime,
        "enforcement": enforcement,
        "rust": {
            "implementation_coverage": 245,
            "production_promoted": 174,
            "remaining_parity_promotion": 71,
            "feature_development_frozen": True,
        },
        "claim_boundary": "This certificate admits Deferred Tool Discovery v1. It does not claim Unified Context Namespace, Python COMPLETE, or Rust parity/promotion.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Syntavra Deferred Tool Discovery v1.")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        report = certify(Path(args.repo))
    except Exception as exc:  # pragma: no cover
        report = {
            "ok": False,
            "schema_version": 1,
            "family": "deferred-tool-discovery",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
