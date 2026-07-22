from __future__ import annotations

import json
import os
from typing import Any

from .mcp_policy import MCPToolPolicy
from .product_surface import SessionAnalyticsStore


def _installed_profile(state_root: Any) -> str:
    path = state_root / "mcp-profile.json"
    if not path.is_file():
        return "minimal"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "minimal"
    if not isinstance(value, dict):
        return "minimal"
    return str(value.get("name") or "minimal")


def _sanitized_call_message(message: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    sanitized_arguments = dict(arguments)
    sanitized_arguments.pop("_syntavra_authorization", None)
    sanitized_arguments.pop("_approved", None)
    params = dict(message.get("params") or {})
    params["arguments"] = sanitized_arguments
    sanitized = dict(message)
    sanitized["params"] = params
    return sanitized


def install() -> None:
    from .mcp_server import MCPServer

    if getattr(MCPServer, "_syntavra_v001_mcp_enforcement", False):
        return

    original_init = MCPServer.__init__
    original_exposed = MCPServer.exposed_tools
    original_handle = MCPServer.handle

    def extended_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        requested = os.environ.get("SYNTAVRA_MCP_PROFILE") or _installed_profile(self.state_root)
        self.product_mcp_policy = MCPToolPolicy(requested)
        self.product_mcp_analytics = SessionAnalyticsStore(self.state_root / "analytics" / "events.jsonl")

    def extended_exposed(self: Any) -> list[dict[str, Any]]:
        requested = os.environ.get("SYNTAVRA_MCP_PROFILE") or self.product_mcp_policy.profile
        policy = MCPToolPolicy(requested)
        previous = os.environ.get("SYNTAVRA_MCP_PROFILE")
        os.environ["SYNTAVRA_MCP_PROFILE"] = policy.legacy_profile
        try:
            if policy.product_profile() in {"minimal", "balanced", "audit"}:
                discovered = list(self.tools())
            else:
                discovered = list(original_exposed(self))
        finally:
            if previous is None:
                os.environ.pop("SYNTAVRA_MCP_PROFILE", None)
            else:
                os.environ["SYNTAVRA_MCP_PROFILE"] = previous
        selected = policy.filter_catalog(discovered)
        self.product_mcp_policy = policy
        return selected

    def extended_handle(self: Any, message: dict[str, Any]) -> dict[str, Any] | None:
        if message.get("method") != "tools/call":
            return original_handle(self, message)

        request_id = message.get("id")
        params = message.get("params") or {}
        tool_name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}
        exposed = self.exposed_tools()
        decision = self.product_mcp_policy.authorize(
            tool_name,
            arguments,
            exposed_tools=(row["name"] for row in exposed),
        )
        self.product_mcp_analytics.record({
            "kind": "mcp-tool-route",
            "repository_hash": getattr(self.evidence, "project_id", ""),
            "tool_route_allowed": decision.allowed,
            "success": decision.allowed,
            "metadata": {
                "tool": decision.tool,
                "profile": decision.profile,
                "risk": decision.risk,
                "reason": decision.reason,
                "receipt_hash": decision.receipt_hash,
            },
        })
        if not decision.allowed:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32001,
                    "message": f"PermissionError: {decision.reason}",
                    "data": MCPToolPolicy.serializable(decision),
                },
            }

        response = original_handle(self, _sanitized_call_message(message, arguments))
        if response and "result" in response and isinstance(response["result"], dict):
            metadata = response["result"].setdefault("_meta", {})
            if isinstance(metadata, dict):
                metadata["syntavra_route_receipt"] = decision.receipt_hash
                metadata["syntavra_profile"] = decision.profile
                metadata["syntavra_risk"] = decision.risk
        return response

    MCPServer.__init__ = extended_init
    MCPServer.exposed_tools = extended_exposed
    MCPServer.handle = extended_handle
    MCPServer._syntavra_v001_mcp_enforcement = True
