from __future__ import annotations

import os
from typing import Any

from .mcp_policy import MCPToolPolicy
from .product_surface import SessionAnalyticsStore


def install() -> None:
    from .mcp_server import MCPServer

    if getattr(MCPServer, "_signalcore_v001_mcp_enforcement", False):
        return

    original_init = MCPServer.__init__
    original_exposed = MCPServer.exposed_tools
    original_handle = MCPServer.handle

    def extended_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self.product_mcp_policy = MCPToolPolicy()
        self.product_mcp_analytics = SessionAnalyticsStore(self.state_root / "analytics" / "events.jsonl")

    def extended_exposed(self: Any) -> list[dict[str, Any]]:
        requested = os.environ.get("SIGNALCORE_MCP_PROFILE", "minimal").strip().casefold() or "minimal"
        policy = MCPToolPolicy(requested)
        previous = os.environ.get("SIGNALCORE_MCP_PROFILE")
        os.environ["SIGNALCORE_MCP_PROFILE"] = policy.legacy_profile
        try:
            selected = list(original_exposed(self))
        finally:
            if previous is None:
                os.environ.pop("SIGNALCORE_MCP_PROFILE", None)
            else:
                os.environ["SIGNALCORE_MCP_PROFILE"] = previous
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

        response = original_handle(self, message)
        if response and "result" in response and isinstance(response["result"], dict):
            metadata = response["result"].setdefault("_meta", {})
            if isinstance(metadata, dict):
                metadata["signalcore_route_receipt"] = decision.receipt_hash
                metadata["signalcore_profile"] = decision.profile
                metadata["signalcore_risk"] = decision.risk
        return response

    MCPServer.__init__ = extended_init
    MCPServer.exposed_tools = extended_exposed
    MCPServer.handle = extended_handle
    MCPServer._signalcore_v001_mcp_enforcement = True
