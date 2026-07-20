from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class HostCapabilities:
    host: str
    supports_pre_tool_hook: bool = False
    supports_post_tool_hook: bool = False
    supports_result_replacement: bool = False
    supports_mcp: bool = False
    supports_proxy: bool = False
    supports_session_events: bool = False
    supports_usage_telemetry: bool = False
    supports_background_jobs: bool = False
    supports_native_skill: bool = False


KNOWN_HOSTS: dict[str, HostCapabilities] = {
    "codex": HostCapabilities(
        "codex",
        supports_mcp=True,
        supports_session_events=True,
        supports_usage_telemetry=True,
        supports_background_jobs=True,
        supports_native_skill=True,
    ),
    "claude-code": HostCapabilities(
        "claude-code",
        supports_pre_tool_hook=True,
        supports_post_tool_hook=True,
        supports_result_replacement=True,
        supports_mcp=True,
        supports_session_events=True,
        supports_background_jobs=True,
        supports_native_skill=True,
    ),
    "gemini-cli": HostCapabilities(
        "gemini-cli",
        supports_pre_tool_hook=True,
        supports_post_tool_hook=True,
        supports_mcp=True,
        supports_native_skill=True,
    ),
    "opencode": HostCapabilities(
        "opencode",
        supports_mcp=True,
        supports_proxy=True,
        supports_session_events=True,
        supports_background_jobs=True,
    ),
    "generic-mcp": HostCapabilities("generic-mcp", supports_mcp=True),
}


def negotiate(host: str, *, runtime_available: bool = True) -> dict:
    capabilities = KNOWN_HOSTS.get(host.casefold(), HostCapabilities(host.casefold()))
    if not runtime_available:
        mode = "INSTRUCTION_ONLY" if capabilities.supports_native_skill else "UNSUPPORTED"
    elif capabilities.supports_pre_tool_hook and capabilities.supports_post_tool_hook:
        mode = "HOOK_ENFORCED"
    elif capabilities.supports_mcp:
        mode = "MCP_CONTROLLED"
    elif capabilities.supports_proxy:
        mode = "PROXY_CONTROLLED"
    elif capabilities.supports_native_skill:
        mode = "INSTRUCTION_ONLY"
    else:
        mode = "UNSUPPORTED"
    return {
        "mode": mode,
        "enforced": mode in {"HOOK_ENFORCED", "MCP_CONTROLLED", "PROXY_CONTROLLED"},
        "capabilities": asdict(capabilities),
    }
