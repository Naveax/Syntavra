from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class HostCapabilities:
    host: str
    display_name: str = ""
    supports_pre_tool_hook: bool = False
    supports_post_tool_hook: bool = False
    supports_result_replacement: bool = False
    supports_mcp: bool = False
    supports_proxy: bool = False
    supports_session_events: bool = False
    supports_usage_telemetry: bool = False
    supports_background_jobs: bool = False
    supports_native_skill: bool = False
    verified: bool = False
    project_markers: tuple[str, ...] = ()
    user_markers: tuple[str, ...] = ()
    config_path: str = ""
    skill_path: str = ""


KNOWN_HOSTS: dict[str, HostCapabilities] = {
    "codex": HostCapabilities(
        "codex", "OpenAI Codex", supports_mcp=True, supports_session_events=True,
        supports_usage_telemetry=True, supports_background_jobs=True,
        supports_native_skill=True, verified=True, project_markers=(".codex",),
        user_markers=(".codex",), config_path=".codex/mcp.json", skill_path=".codex/skills/signal-core",
    ),
    "claude-code": HostCapabilities(
        "claude-code", "Claude Code", True, True, True, True, False, True, False, True, True,
        True, (".claude", ".mcp.json"), (".claude",), ".claude/settings.json", ".claude/skills/signal-core",
    ),
    "gemini-cli": HostCapabilities(
        "gemini-cli", "Gemini CLI", True, True, True, True, False, True, True, True, True,
        True, (".gemini", "gemini-extension.json"), (".gemini",), ".gemini/settings.json", ".gemini/skills/signal-core",
    ),
    "opencode": HostCapabilities(
        "opencode", "OpenCode", False, False, True, True, True, True, True, True, True,
        True, (".opencode", "opencode.json"), (".config/opencode",), ".opencode/opencode.json", ".opencode/skills/signal-core",
    ),
    "cursor": HostCapabilities(
        "cursor", "Cursor", False, False, True, True, False, True, False, True, False,
        True, (".cursor",), (".cursor",), ".cursor/mcp.json", ".cursor/rules/signal-core.mdc",
    ),
    "windsurf": HostCapabilities(
        "windsurf", "Windsurf", False, False, True, True, False, True, False, True, True,
        True, (".windsurf",), (".codeium/windsurf",), ".windsurf/mcp.json", ".windsurf/skills/signal-core",
    ),
    "vscode-copilot": HostCapabilities(
        "vscode-copilot", "VS Code / GitHub Copilot", False, False, True, True, False, True, False, True, True,
        True, (".vscode", ".github"), (".vscode",), ".vscode/mcp.json", ".github/skills/signal-core",
    ),
    "cline": HostCapabilities(
        "cline", "Cline", False, False, True, True, False, True, False, True, False,
        True, (".cline", ".clinerules"), (".cline",), ".cline/mcp_settings.json", ".clinerules/00-signal-core.md",
    ),
    "roo-code": HostCapabilities(
        "roo-code", "Roo Code", False, False, True, True, False, True, False, True, False,
        False, (".roo", ".roomodes"), (".roo",), ".roo/mcp.json", "AGENTS.md",
    ),
    "continue": HostCapabilities(
        "continue", "Continue", False, False, True, True, False, True, False, True, False,
        True, (".continue",), (".continue",), ".continue/mcp.json", ".continue/rules/00-signal-core.md",
    ),
    "qwen-code": HostCapabilities(
        "qwen-code", "Qwen Code", False, False, True, True, False, True, True, True, True,
        False, (".qwen", "AGENTS.md"), (".qwen",), ".qwen/mcp.json", ".qwen/skills/signal-core",
    ),
    "antigravity": HostCapabilities(
        "antigravity", "Google Antigravity", False, False, True, True, False, True, False, True, True,
        True, (".agents",), (".gemini/config",), ".agents/mcp.json", ".agents/skills/signal-core",
    ),
    "antigravity-cli": HostCapabilities(
        "antigravity-cli", "Google Antigravity CLI", False, False, True, True, False, True, False, True, True,
        True, (".agent",), (".gemini/antigravity-cli",), ".agent/mcp.json", ".agent/skills/signal-core",
    ),
    "generic-mcp": HostCapabilities("generic-mcp", "Generic MCP client", supports_mcp=True, supports_result_replacement=True),
    "aider": HostCapabilities(
        "aider", "Aider", False, False, False, False, False, False, False, False, False,
        False, (".aider.conf.yml", "AGENTS.md"), (), "", "AGENTS.md",
    ),
}


def host_spec(host: str) -> HostCapabilities:
    return KNOWN_HOSTS.get(host.casefold(), HostCapabilities(host.casefold(), host))


def negotiate(host: str, *, runtime_available: bool = True, installed: bool | None = None) -> dict[str, Any]:
    capabilities = host_spec(host)
    if not runtime_available:
        mode = "INSTRUCTION_ONLY" if capabilities.supports_native_skill else "UNSUPPORTED"
    elif capabilities.supports_pre_tool_hook and capabilities.supports_post_tool_hook and capabilities.supports_result_replacement:
        mode = "HOOK_ENFORCED"
    elif capabilities.supports_mcp:
        mode = "MCP_CONTROLLED"
    elif capabilities.supports_proxy:
        mode = "PROXY_CONTROLLED"
    elif capabilities.supports_native_skill:
        mode = "INSTRUCTION_ONLY"
    else:
        mode = "UNSUPPORTED"
    if installed is False and mode not in {"UNSUPPORTED", "INSTRUCTION_ONLY"}:
        mode = "RUNTIME_PARTIAL"
    return {
        "mode": mode,
        "enforced": mode in {"HOOK_ENFORCED", "MCP_CONTROLLED", "PROXY_CONTROLLED"},
        "installed": installed,
        "verified_adapter": capabilities.verified,
        "capabilities": asdict(capabilities),
    }


def detect_hosts(project: Path, *, home: Path | None = None) -> list[dict[str, Any]]:
    project = project.resolve(strict=False)
    home = (home or Path.home()).resolve(strict=False)
    detected: list[dict[str, Any]] = []
    for host, spec in KNOWN_HOSTS.items():
        if host == "generic-mcp":
            continue
        project_hits = [marker for marker in spec.project_markers if (project / marker).exists()]
        user_hits = [marker for marker in spec.user_markers if (home / marker).exists()]
        executable = _find_executable(host)
        if project_hits or user_hits or executable:
            detected.append({
                "host": host,
                "display_name": spec.display_name,
                "project_markers": project_hits,
                "user_markers": user_hits,
                "executable": executable,
                "negotiation": negotiate(host, installed=True),
            })
    return detected


def _find_executable(host: str) -> str | None:
    import shutil

    aliases = {
        "codex": ("codex",),
        "claude-code": ("claude",),
        "gemini-cli": ("gemini",),
        "opencode": ("opencode",),
        "cursor": ("cursor",),
        "windsurf": ("windsurf",),
        "vscode-copilot": ("code", "gh"),
        "cline": (),
        "roo-code": (),
        "continue": (),
        "qwen-code": ("qwen",),
        "antigravity": ("antigravity",),
        "antigravity-cli": ("antigravity",),
        "aider": ("aider",),
    }
    for name in aliases.get(host, (host,)):
        if candidate := shutil.which(name):
            return candidate
    return None


def environment_capabilities() -> dict[str, Any]:
    return {
        "platform": os.name,
        "hosts": {name: asdict(spec) for name, spec in KNOWN_HOSTS.items()},
    }
