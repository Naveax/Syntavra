from __future__ import annotations

import os
from dataclasses import asdict
from typing import Any

from .arm_runner import SecureArmRunner
from .data_router import DataRoutePolicy, DataRouter, result_dict
from .policy_tuner import AdaptivePolicyTuner, PolicyObservation
from .service_manager import ProviderProxyServiceManager, ServiceSpec


_PRODUCT_NAMES = frozenset({
    "syntavra.product.capabilities",
    "syntavra.data.route",
    "syntavra.policy.record",
    "syntavra.policy.recommend",
    "syntavra.policy.active",
    "syntavra.service.plan",
    "syntavra.service.verify",
    "syntavra.arm.validate_result",
})


def _tool(name: str, description: str, properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties or {}}
    if required:
        schema["required"] = required
    return {"name": name, "description": description, "inputSchema": schema}


def product_tools() -> list[dict[str, Any]]:
    return [
        _tool("syntavra.product.capabilities", "Inspect product-parity V5 data, policy, service, SDK and arm-runner surfaces"),
        _tool(
            "syntavra.data.route",
            "Route and compact SQL/table, RAG, GraphQL and generic JSON while preserving exact evidence",
            {
                "payload": {}, "hint": {"type": "string"}, "query": {"type": "string"},
                "budget_bytes": {"type": "integer"}, "max_rows": {"type": "integer"},
                "max_columns": {"type": "integer"},
            },
            ["payload"],
        ),
        _tool(
            "syntavra.policy.record",
            "Record one quality-gated local policy observation",
            {
                "family": {"type": "string"}, "host": {"type": "string"}, "model": {"type": "string"},
                "raw_bytes": {"type": "integer"}, "visible_bytes": {"type": "integer"},
                "latency_ms": {"type": "number"}, "success": {"type": "boolean"},
                "quality": {"type": "number"}, "cache_hit": {"type": "boolean"},
                "security_regressions": {"type": "integer"},
            },
            ["family", "raw_bytes", "visible_bytes", "latency_ms", "success"],
        ),
        _tool(
            "syntavra.policy.recommend",
            "Recommend a conservative canary policy without weakening security or quality gates",
            {
                "family": {"type": "string"}, "host": {"type": "string"}, "model": {"type": "string"},
                "minimum_samples": {"type": "integer"}, "window": {"type": "integer"},
            },
            ["family"],
        ),
        _tool(
            "syntavra.policy.active",
            "Read the latest promoted policy for one workload scope",
            {"family": {"type": "string"}, "host": {"type": "string"}, "model": {"type": "string"}},
            ["family"],
        ),
        _tool(
            "syntavra.service.plan",
            "Render a user-scoped systemd, launchd or Windows Task Scheduler proxy service",
            {
                "name": {"type": "string"}, "command": {"type": "array", "items": {"type": "string"}},
                "platform": {"type": "string"}, "working_directory": {"type": "string"},
                "environment_file": {"type": "string"},
            },
            ["name", "command"],
        ),
        _tool(
            "syntavra.service.verify",
            "Verify a generated service descriptor byte-for-byte",
            {
                "name": {"type": "string"}, "command": {"type": "array", "items": {"type": "string"}},
                "platform": {"type": "string"}, "working_directory": {"type": "string"},
                "environment_file": {"type": "string"},
            },
            ["name", "command"],
        ),
        _tool(
            "syntavra.arm.validate_result",
            "Validate an external benchmark-arm result and provider receipt without executing it",
            {
                "result": {"type": "object"}, "pair_key": {"type": "string"},
                "arm_id": {"type": "string"}, "require_receipt": {"type": "boolean"},
            },
            ["result", "pair_key", "arm_id"],
        ),
    ]


def install() -> None:
    from .mcp_server import MCPServer

    if getattr(MCPServer, "_syntavra_product_extension_v5", False):
        return
    original_init = MCPServer.__init__
    original_tools = MCPServer.tools
    original_exposed_tools = MCPServer.exposed_tools
    original_call_tool = MCPServer.call_tool

    def extended_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self.data_router = DataRouter(self.evidence)
        self.policy_tuner = AdaptivePolicyTuner(self.state_root / "adaptive-policy.sqlite3")
        self.proxy_service_manager = ProviderProxyServiceManager()
        self.secure_arm_runner = SecureArmRunner(self.state_root / "arm-runs", evidence=self.evidence)

    def extended_tools() -> list[dict[str, Any]]:
        catalog = list(original_tools())
        known = {row["name"] for row in catalog}
        catalog.extend(row for row in product_tools() if row["name"] not in known)
        return catalog

    def extended_exposed_tools(self: Any) -> list[dict[str, Any]]:
        selected = list(original_exposed_tools(self))
        profile = os.environ.get("SYNTAVRA_MCP_PROFILE", "optimized").strip().casefold() or "optimized"
        if profile not in {"optimized", "full"}:
            return selected
        known = {row["name"] for row in selected}
        selected.extend(row for row in product_tools() if row["name"] not in known)
        return selected

    def extended_call_tool(self: Any, name: str, arguments: dict[str, Any]) -> Any:
        if name == "syntavra.product.capabilities":
            return {
                "schema_version": 1,
                "typescript_sdk": {"dependency_free": True, "proxy_transport": True, "streaming": True},
                "data_routing": ["table", "rag", "graphql", "json", "text"],
                "adaptive_policy": {"quality_gated": True, "security_fail_closed": True, "rollback": True},
                "service_management": ["systemd-user", "launchd-user", "windows-task-user"],
                "arm_runner": {"argv_only": True, "environment_allowlist": True, "receipt_required": True},
            }
        if name == "syntavra.data.route":
            policy = DataRoutePolicy(
                budget_bytes=int(arguments.get("budget_bytes", 8192)),
                max_rows=int(arguments.get("max_rows", 8)),
                max_columns=int(arguments.get("max_columns", 12)),
            )
            return result_dict(self.data_router.route(
                arguments["payload"], hint=str(arguments.get("hint", "")),
                query=str(arguments.get("query", "")), policy=policy,
            ))
        if name == "syntavra.policy.record":
            sequence = self.policy_tuner.record(PolicyObservation(
                family=str(arguments["family"]), host=str(arguments.get("host", "unknown")),
                model=str(arguments.get("model", "unknown")), raw_bytes=int(arguments["raw_bytes"]),
                visible_bytes=int(arguments["visible_bytes"]), latency_ms=float(arguments["latency_ms"]),
                success=bool(arguments["success"]), quality=float(arguments.get("quality", 1.0)),
                cache_hit=bool(arguments.get("cache_hit", False)),
                security_regressions=int(arguments.get("security_regressions", 0)),
            ))
            return {"ok": True, "sequence": sequence}
        if name == "syntavra.policy.recommend":
            return asdict(self.policy_tuner.recommend(
                str(arguments["family"]), host=str(arguments.get("host", "unknown")),
                model=str(arguments.get("model", "unknown")),
                minimum_samples=int(arguments.get("minimum_samples", 12)),
                window=int(arguments.get("window", 200)),
            ))
        if name == "syntavra.policy.active":
            return self.policy_tuner.active(
                str(arguments["family"]), host=str(arguments.get("host", "unknown")),
                model=str(arguments.get("model", "unknown")),
            ) or {"ok": False, "reason": "no-promoted-policy"}
        if name in {"syntavra.service.plan", "syntavra.service.verify"}:
            spec = ServiceSpec(
                name=str(arguments["name"]), command=tuple(str(item) for item in arguments["command"]),
                working_directory=str(arguments.get("working_directory", "")),
                environment_file=str(arguments.get("environment_file", "")),
            )
            if name.endswith("plan"):
                return asdict(self.proxy_service_manager.plan(spec, platform_name=arguments.get("platform")))
            return self.proxy_service_manager.verify(spec, platform_name=arguments.get("platform"))
        if name == "syntavra.arm.validate_result":
            valid, receipt_valid, reasons = SecureArmRunner.validate_result(
                arguments["result"], pair_key=str(arguments["pair_key"]), arm_id=str(arguments["arm_id"]),
                require_receipt=bool(arguments.get("require_receipt", True)),
            )
            return {"ok": valid, "provider_receipt_valid": receipt_valid, "reasons": reasons}
        return original_call_tool(self, name, arguments)

    MCPServer.__init__ = extended_init
    MCPServer.tools = staticmethod(extended_tools)
    MCPServer.exposed_tools = extended_exposed_tools
    MCPServer.call_tool = extended_call_tool
    MCPServer._syntavra_product_extension_v5 = True
    MCPServer.PRODUCT_V5_TOOLS = _PRODUCT_NAMES
