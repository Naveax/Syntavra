from __future__ import annotations

import os
from dataclasses import asdict
from typing import Any

from .provider_gateway import ProviderGateway, ProviderPlan


_PROVIDER_NAMES = frozenset({
    "syntavra.provider.capabilities",
    "syntavra.provider.prepare",
    "syntavra.provider.capture",
    "syntavra.provider.replay",
    "syntavra.provider.stats",
    "syntavra.provider.verify",
})


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties or {}}
    if required:
        schema["required"] = required
    return {"name": name, "description": description, "inputSchema": schema}


def provider_tools() -> list[dict[str, Any]]:
    return [
        _tool(
            "syntavra.provider.capabilities",
            "Inspect provider cache, usage, and request-shape capabilities",
            {"provider": {"type": "string"}},
        ),
        _tool(
            "syntavra.provider.prepare",
            "Prepare a cache-stable exact provider request",
            {
                "provider": {"type": "string"},
                "request": {"type": "object"},
                "model": {"type": "string"},
                "cache_policy": {"type": "string"},
                "replay_ttl_seconds": {"type": "integer"},
                "prompt_cache_ttl_seconds": {"type": "integer"},
                "explicit_cache_name": {"type": "string"},
                "allow_tool_replay": {"type": "boolean"},
            },
            ["provider", "request"],
        ),
        _tool(
            "syntavra.provider.capture",
            "Capture an exact provider response with optional receipt and replay",
            {
                "plan": {"type": "object"},
                "response": {"type": "object"},
                "store_replay": {"type": "boolean"},
                "replay_ttl_seconds": {"type": "integer"},
                "preview_bytes": {"type": "integer"},
                "receipt": {"type": "object"},
            },
            ["plan", "response"],
        ),
        _tool(
            "syntavra.provider.replay",
            "Replay an exact deterministic provider response",
            {"plan": {"type": "object"}, "cache_key": {"type": "string"}},
        ),
        _tool("syntavra.provider.stats", "Inspect provider gateway request and replay statistics"),
        _tool("syntavra.provider.verify", "Verify provider request and response evidence integrity"),
    ]


def install() -> None:
    """Extend MCPServer without replacing its existing catalog or dispatch logic."""

    from .mcp_server import MCPServer

    if getattr(MCPServer, "_syntavra_provider_extension_v4", False):
        return

    original_init = MCPServer.__init__
    original_tools = MCPServer.tools
    original_exposed_tools = MCPServer.exposed_tools
    original_call_tool = MCPServer.call_tool

    def extended_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self.provider_gateway = ProviderGateway(
            self.state_root / "provider-gateway.sqlite3",
            evidence=self.evidence,
            usage_ledger=self.usage_ledger,
        )

    def extended_tools() -> list[dict[str, Any]]:
        catalog = list(original_tools())
        known = {row["name"] for row in catalog}
        catalog.extend(row for row in provider_tools() if row["name"] not in known)
        return catalog

    def extended_exposed_tools(self: Any) -> list[dict[str, Any]]:
        selected = list(original_exposed_tools(self))
        profile = os.environ.get("SYNTAVRA_MCP_PROFILE", "optimized").strip().casefold() or "optimized"
        if profile not in {"optimized", "full"}:
            return selected
        known = {row["name"] for row in selected}
        selected.extend(row for row in provider_tools() if row["name"] not in known)
        return selected

    def extended_call_tool(self: Any, name: str, arguments: dict[str, Any]) -> Any:
        if name == "syntavra.provider.capabilities":
            return self.provider_gateway.capabilities(arguments.get("provider"))
        if name == "syntavra.provider.prepare":
            return asdict(self.provider_gateway.prepare(
                str(arguments["provider"]),
                dict(arguments["request"]),
                model=str(arguments.get("model", "")),
                cache_policy=str(arguments.get("cache_policy", "auto")),
                replay_ttl_seconds=int(arguments.get("replay_ttl_seconds", 900)),
                prompt_cache_ttl_seconds=int(arguments.get("prompt_cache_ttl_seconds", 300)),
                explicit_cache_name=str(arguments.get("explicit_cache_name", "")),
                allow_tool_replay=bool(arguments.get("allow_tool_replay", False)),
            ))
        if name == "syntavra.provider.capture":
            return asdict(self.provider_gateway.capture(
                ProviderPlan(**dict(arguments["plan"])),
                dict(arguments["response"]),
                store_replay=bool(arguments.get("store_replay", True)),
                replay_ttl_seconds=int(arguments.get("replay_ttl_seconds", 900)),
                preview_bytes=int(arguments.get("preview_bytes", 4096)),
                receipt=arguments.get("receipt"),
            ))
        if name == "syntavra.provider.replay":
            target: ProviderPlan | str
            if arguments.get("plan"):
                target = ProviderPlan(**dict(arguments["plan"]))
            else:
                target = str(arguments.get("cache_key", ""))
            return self.provider_gateway.replay(target)
        if name == "syntavra.provider.stats":
            return self.provider_gateway.stats()
        if name == "syntavra.provider.verify":
            return self.provider_gateway.verify()
        return original_call_tool(self, name, arguments)

    MCPServer.__init__ = extended_init
    MCPServer.tools = staticmethod(extended_tools)
    MCPServer.exposed_tools = extended_exposed_tools
    MCPServer.call_tool = extended_call_tool
    MCPServer._syntavra_provider_extension_v4 = True
    MCPServer.PROVIDER_TOOLS = _PROVIDER_NAMES
