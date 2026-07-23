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
    supports_stream_capture: bool = False
    integration_notes: tuple[str, ...] = ()


KNOWN_HOSTS: dict[str, HostCapabilities] = {
    "codex": HostCapabilities(
        "codex", "OpenAI Codex", supports_mcp=True, supports_session_events=True,
        supports_usage_telemetry=True, supports_background_jobs=True,
        supports_native_skill=True, verified=True, project_markers=(".codex",),
        user_markers=(".codex",), config_path=".codex/mcp.json", skill_path=".codex/skills/syntavra",
        integration_notes=("mcp", "native-skill", "session-events"),
    ),
    "claude-code": HostCapabilities(
        "claude-code", "Claude Code", True, True, True, True, False, True, False, True, True,
        True, (".claude",), (".claude",), ".claude/settings.json", ".claude/skills/syntavra",
        True, ("hook-enforced", "mcp", "stream-capture"),
    ),
    "gemini-cli": HostCapabilities(
        "gemini-cli", "Gemini CLI", True, True, True, True, False, True, True, True, True,
        True, (".gemini", "gemini-extension.json"), (".gemini",), ".gemini/settings.json", ".gemini/skills/syntavra",
        True, ("hook-enforced", "usage-telemetry", "stream-capture"),
    ),
    "opencode": HostCapabilities(
        "opencode", "OpenCode", False, False, True, True, True, True, True, True, True,
        True, (".opencode", "opencode.json"), (".config/opencode",), ".opencode/opencode.json", ".opencode/skills/syntavra",
        True, ("mcp", "proxy", "stream-capture"),
    ),
    "cursor": HostCapabilities(
        "cursor", "Cursor", False, False, True, True, False, True, False, True, False,
        True, (".cursor",), (".cursor",), ".cursor/mcp.json", ".cursor/rules/syntavra.mdc",
        False, ("mcp", "rules"),
    ),
    "windsurf": HostCapabilities(
        "windsurf", "Windsurf", False, False, True, True, False, True, False, True, True,
        True, (".windsurf",), (".codeium/windsurf",), ".windsurf/mcp.json", ".windsurf/skills/syntavra",
        False, ("mcp", "native-skill"),
    ),
    "vscode-copilot": HostCapabilities(
        "vscode-copilot", "VS Code / GitHub Copilot", False, False, True, True, False, True, False, True, True,
        True, (".vscode", ".github/copilot-instructions.md"), (), ".vscode/mcp.json", ".github/skills/syntavra",
        False, ("mcp", "repository-instructions"),
    ),
    "cline": HostCapabilities(
        "cline", "Cline", False, False, True, True, False, True, False, True, False,
        True, (".cline", ".clinerules"), (".cline",), ".cline/mcp_settings.json", ".clinerules/00-syntavra.md",
        False, ("mcp", "rules"),
    ),
    "roo-code": HostCapabilities(
        "roo-code", "Roo Code", False, False, True, True, False, True, False, True, False,
        False, (".roo", ".roomodes"), (".roo",), ".roo/mcp.json", "AGENTS.md",
        False, ("mcp", "agents-instructions"),
    ),
    "continue": HostCapabilities(
        "continue", "Continue", False, False, True, True, False, True, False, True, False,
        True, (".continue",), (".continue",), ".continue/mcp.json", ".continue/rules/00-syntavra.md",
        False, ("mcp", "rules"),
    ),
    "qwen-code": HostCapabilities(
        "qwen-code", "Qwen Code", False, False, True, True, False, True, True, True, True,
        False, (".qwen",), (".qwen",), ".qwen/mcp.json", ".qwen/skills/syntavra",
        False, ("mcp", "native-skill", "usage-telemetry"),
    ),
    "kiro": HostCapabilities(
        "kiro", "Kiro CLI", supports_result_replacement=True, supports_mcp=True,
        supports_session_events=True, supports_background_jobs=True, supports_native_skill=True,
        project_markers=(".kiro",), user_markers=(".kiro",),
        config_path=".kiro/settings/mcp.json", skill_path=".kiro/skills/syntavra",
        integration_notes=("mcp", "native-skill", "steering"),
    ),
    "antigravity": HostCapabilities(
        "antigravity", "Google Antigravity", False, False, True, True, False, True, False, True, True,
        True, (".agents",), (".gemini/config",), ".agents/mcp.json", ".agents/skills/syntavra",
        False, ("mcp", "native-skill"),
    ),
    "antigravity-cli": HostCapabilities(
        "antigravity-cli", "Google Antigravity CLI", False, False, True, True, False, True, False, True, True,
        True, (".agent",), (".gemini/antigravity-cli",), ".agent/mcp.json", ".agent/skills/syntavra",
        False, ("mcp", "native-skill"),
    ),
    "zed": HostCapabilities(
        "zed", "Zed", supports_result_replacement=True, supports_mcp=True,
        supports_session_events=True, supports_background_jobs=True,
        project_markers=(".zed",), user_markers=(".config/zed",),
        config_path=".zed/settings.json", skill_path="AGENTS.md",
        integration_notes=("mcp", "agents-instructions"),
    ),
    "pi": HostCapabilities(
        "pi", "Pi Coding Agent", supports_native_skill=True,
        project_markers=(".pi",), user_markers=(".pi/agent",),
        config_path=".pi/settings.json", skill_path=".pi/skills/syntavra",
        integration_notes=("native-skill", "extension-capable", "instruction-only-adapter"),
    ),
    "omp": HostCapabilities(
        "omp", "Oh My Pi", supports_native_skill=True,
        project_markers=(".omp",), user_markers=(".omp/agent",),
        config_path=".omp/agent/config.yml", skill_path=".omp/skills/syntavra",
        integration_notes=("native-skill", "mcp-capable-host", "instruction-only-adapter"),
    ),
    "openclaw": HostCapabilities(
        "openclaw", "OpenClaw", supports_native_skill=True,
        project_markers=(".openclaw", "openclaw.json"), user_markers=(".openclaw",),
        config_path="openclaw.json", skill_path="skills/syntavra",
        integration_notes=("workspace-skill", "plugin-compatible", "instruction-only-adapter"),
    ),
    "kilo-code": HostCapabilities(
        "kilo-code", "Kilo Code", supports_result_replacement=True, supports_mcp=True,
        supports_session_events=True, supports_background_jobs=True,
        project_markers=(".kilocode", ".kilocodemodes"), user_markers=(".kilocode",),
        config_path=".kilocode/mcp.json", skill_path=".kilocode/rules/00-syntavra.md",
        integration_notes=("mcp", "rules"),
    ),
    "jetbrains-copilot": HostCapabilities(
        "jetbrains-copilot", "JetBrains / GitHub Copilot", supports_result_replacement=True,
        supports_mcp=True, supports_session_events=True, supports_background_jobs=True,
        project_markers=(".idea",), user_markers=(".config/JetBrains",),
        config_path=".idea/mcp.json", skill_path=".github/skills/syntavra",
        integration_notes=("mcp", "repository-instructions"),
    ),
    "sourcegraph-cody": HostCapabilities(
        "sourcegraph-cody", "Sourcegraph Cody", supports_result_replacement=True,
        supports_mcp=True, supports_session_events=True, supports_background_jobs=True,
        project_markers=(".sourcegraph",), user_markers=(".config/sourcegraph",),
        config_path=".sourcegraph/mcp.json", skill_path="AGENTS.md",
        integration_notes=("mcp", "agents-instructions"),
    ),
    "goose": HostCapabilities(
        "goose", "Block Goose", supports_result_replacement=True, supports_mcp=True,
        supports_proxy=True, supports_session_events=True, supports_usage_telemetry=True,
        supports_background_jobs=True, supports_native_skill=True,
        project_markers=(".goose",), user_markers=(".config/goose",),
        config_path=".goose/config.yaml", skill_path=".goose/skills/syntavra",
        supports_stream_capture=True,
        integration_notes=("mcp", "proxy", "native-skill", "stream-capture"),
    ),
    "generic-mcp": HostCapabilities(
        "generic-mcp", "Generic MCP client", supports_mcp=True,
        supports_result_replacement=True, integration_notes=("mcp",)
    ),
    "aider": HostCapabilities(
        "aider", "Aider", False, False, False, False, False, False, False, False, False,
        False, (".aider.conf.yml",), (), "", "AGENTS.md",
        False, ("instruction-only",),
    ),
}


# Additional installable host contracts.  These entries provide concrete project
# config/skill destinations and executable aliases; ``verified`` remains false
# until a live multi-OS certification receipt exists.
KNOWN_HOSTS.update({
    "amazon-q": HostCapabilities("amazon-q", "Amazon Q Developer", supports_result_replacement=True, supports_mcp=True, supports_session_events=True, supports_background_jobs=True, supports_native_skill=True, project_markers=(".amazonq",), user_markers=(".aws/amazonq",), config_path=".amazonq/mcp.json", skill_path=".amazonq/rules/syntavra.md", integration_notes=("mcp", "rules")),
    "copilot-cli": HostCapabilities("copilot-cli", "GitHub Copilot CLI", supports_result_replacement=True, supports_mcp=True, supports_session_events=True, supports_background_jobs=True, supports_native_skill=True, project_markers=(".github",), user_markers=(".config/github-copilot",), config_path=".github/copilot/mcp.json", skill_path=".github/copilot-instructions.md", integration_notes=("mcp", "repository-instructions")),
    "trae": HostCapabilities("trae", "Trae", supports_result_replacement=True, supports_mcp=True, supports_session_events=True, supports_background_jobs=True, project_markers=(".trae",), user_markers=(".trae",), config_path=".trae/mcp.json", skill_path=".trae/rules/syntavra.md", integration_notes=("mcp", "rules")),
    "void": HostCapabilities("void", "Void", supports_result_replacement=True, supports_mcp=True, supports_session_events=True, supports_background_jobs=True, project_markers=(".void",), user_markers=(".config/void",), config_path=".void/mcp.json", skill_path=".void/rules/syntavra.md", integration_notes=("mcp", "rules")),
    "warp": HostCapabilities("warp", "Warp Agent", supports_result_replacement=True, supports_mcp=True, supports_proxy=True, supports_session_events=True, supports_usage_telemetry=True, supports_background_jobs=True, project_markers=(".warp",), user_markers=(".warp",), config_path=".warp/mcp.json", skill_path="WARP.md", integration_notes=("mcp", "proxy", "rules")),
    "openhands": HostCapabilities("openhands", "OpenHands", supports_result_replacement=True, supports_mcp=True, supports_proxy=True, supports_session_events=True, supports_background_jobs=True, supports_native_skill=True, project_markers=(".openhands",), user_markers=(".openhands",), config_path=".openhands/mcp.json", skill_path=".openhands/skills/syntavra", integration_notes=("mcp", "proxy", "native-skill")),
    "swe-agent": HostCapabilities("swe-agent", "SWE-agent", supports_result_replacement=True, supports_mcp=True, supports_session_events=True, supports_background_jobs=True, project_markers=(".swe-agent",), user_markers=(".config/swe-agent",), config_path=".swe-agent/mcp.json", skill_path=".swe-agent/instructions/syntavra.md", integration_notes=("mcp", "instructions")),
    "mentat": HostCapabilities("mentat", "Mentat", supports_result_replacement=True, supports_mcp=True, supports_session_events=True, supports_background_jobs=True, project_markers=(".mentat",), user_markers=(".mentat",), config_path=".mentat/mcp.json", skill_path=".mentat/rules/syntavra.md", integration_notes=("mcp", "rules")),
    "plandex": HostCapabilities("plandex", "Plandex", supports_result_replacement=True, supports_mcp=True, supports_proxy=True, supports_session_events=True, supports_background_jobs=True, project_markers=(".plandex",), user_markers=(".plandex",), config_path=".plandex/mcp.json", skill_path=".plandex/skills/syntavra", integration_notes=("mcp", "proxy", "skill")),
    "tabby": HostCapabilities("tabby", "Tabby", supports_result_replacement=True, supports_mcp=True, supports_proxy=True, project_markers=(".tabby",), user_markers=(".tabby",), config_path=".tabby/mcp.json", skill_path=".tabby/instructions/syntavra.md", integration_notes=("mcp", "proxy")),
    "pearai": HostCapabilities("pearai", "PearAI", supports_result_replacement=True, supports_mcp=True, supports_session_events=True, supports_background_jobs=True, project_markers=(".pearai",), user_markers=(".pearai",), config_path=".pearai/mcp.json", skill_path=".pearai/rules/syntavra.md", integration_notes=("mcp", "rules")),
    "replit-agent": HostCapabilities("replit-agent", "Replit Agent", supports_result_replacement=True, supports_mcp=True, supports_proxy=True, supports_session_events=True, supports_background_jobs=True, project_markers=(".replit", "replit.nix"), user_markers=(), config_path=".replit/mcp.json", skill_path="replit.md", integration_notes=("mcp", "proxy", "instructions")),
    "bolt": HostCapabilities("bolt", "Bolt", supports_result_replacement=True, supports_mcp=True, supports_proxy=True, project_markers=(".bolt",), user_markers=(".bolt",), config_path=".bolt/mcp.json", skill_path=".bolt/rules/syntavra.md", integration_notes=("mcp", "proxy")),
    "devin": HostCapabilities("devin", "Devin", supports_result_replacement=True, supports_mcp=True, supports_session_events=True, supports_background_jobs=True, project_markers=(".devin",), user_markers=(".devin",), config_path=".devin/mcp.json", skill_path=".devin/knowledge/syntavra.md", integration_notes=("mcp", "knowledge")),
    "codeium-cli": HostCapabilities("codeium-cli", "Codeium CLI", supports_result_replacement=True, supports_mcp=True, supports_session_events=True, supports_background_jobs=True, project_markers=(".codeium",), user_markers=(".codeium",), config_path=".codeium/mcp.json", skill_path=".codeium/rules/syntavra.md", integration_notes=("mcp", "rules")),
    "aider-desk": HostCapabilities("aider-desk", "AiderDesk", supports_result_replacement=True, supports_mcp=True, supports_session_events=True, supports_background_jobs=True, project_markers=(".aider-desk",), user_markers=(".aider-desk",), config_path=".aider-desk/mcp.json", skill_path="AGENTS.md", integration_notes=("mcp", "agents-instructions")),
    "neovim-avante": HostCapabilities("neovim-avante", "Neovim Avante", supports_result_replacement=True, supports_mcp=True, project_markers=(".nvim",), user_markers=(".config/nvim",), config_path=".nvim/syntavra-mcp.json", skill_path="AGENTS.md", integration_notes=("mcp", "agents-instructions")),
    "emacs-copilot": HostCapabilities("emacs-copilot", "Emacs Copilot", supports_result_replacement=True, supports_mcp=True, project_markers=(".dir-locals.el",), user_markers=(".emacs.d",), config_path=".emacs.d/syntavra-mcp.json", skill_path="AGENTS.md", integration_notes=("mcp", "agents-instructions")),
    "lapce": HostCapabilities("lapce", "Lapce", supports_result_replacement=True, supports_mcp=True, project_markers=(".lapce",), user_markers=(".config/lapce",), config_path=".lapce/mcp.json", skill_path=".lapce/rules/syntavra.md", integration_notes=("mcp", "rules")),
    "helix-agent": HostCapabilities("helix-agent", "Helix Agent", supports_result_replacement=True, supports_mcp=True, project_markers=(".helix",), user_markers=(".config/helix",), config_path=".helix/mcp.json", skill_path="AGENTS.md", integration_notes=("mcp", "agents-instructions")),
})


def host_spec(host: str) -> HostCapabilities:
    return KNOWN_HOSTS.get(host.casefold(), HostCapabilities(host.casefold(), host))


def integration_tier(spec: HostCapabilities) -> str:
    if (
        spec.supports_pre_tool_hook
        and spec.supports_post_tool_hook
        and spec.supports_result_replacement
    ):
        return "HOOK_ENFORCED"
    if spec.supports_mcp and spec.supports_proxy:
        return "MCP_PLUS_PROXY"
    if spec.supports_mcp:
        return "MCP_CONTROLLED"
    if spec.supports_proxy:
        return "PROXY_CONTROLLED"
    if spec.supports_native_skill:
        return "INSTRUCTION_ONLY"
    return "UNSUPPORTED"


def negotiate(host: str, *, runtime_available: bool = True, installed: bool | None = None) -> dict[str, Any]:
    capabilities = host_spec(host)
    tier = integration_tier(capabilities)
    if not runtime_available:
        mode = "INSTRUCTION_ONLY" if capabilities.supports_native_skill else "UNSUPPORTED"
    else:
        mode = tier
    if installed is False and mode not in {"UNSUPPORTED", "INSTRUCTION_ONLY"}:
        mode = "RUNTIME_PARTIAL"
    return {
        "mode": mode,
        "integration_tier": tier,
        "enforced": mode in {"HOOK_ENFORCED", "MCP_CONTROLLED", "MCP_PLUS_PROXY", "PROXY_CONTROLLED"},
        "installed": installed,
        "verified_adapter": capabilities.verified,
        "stream_capture": capabilities.supports_stream_capture,
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
                "detection_confidence": "strong" if executable or project_hits else "user-config",
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
        "vscode-copilot": ("code",),
        "cline": (),
        "roo-code": (),
        "continue": (),
        "qwen-code": ("qwen", "qwen-code"),
        "kiro": ("kiro", "kiro-cli", "q"),
        "antigravity": ("antigravity",),
        "antigravity-cli": ("antigravity",),
        "zed": ("zed",),
        "pi": ("pi",),
        "omp": ("omp",),
        "openclaw": ("openclaw",),
        "kilo-code": ("kilo", "kilocode"),
        "jetbrains-copilot": ("idea", "pycharm", "webstorm"),
        "sourcegraph-cody": ("cody",),
        "goose": ("goose",),
        "aider": ("aider",),
        "amazon-q": ("q", "amazon-q"),
        "copilot-cli": ("github-copilot", "copilot"),
        "trae": ("trae",),
        "void": ("void",),
        "warp": ("warp",),
        "openhands": ("openhands",),
        "swe-agent": ("swe-agent",),
        "mentat": ("mentat",),
        "plandex": ("plandex",),
        "tabby": ("tabby",),
        "pearai": ("pearai",),
        "replit-agent": ("replit",),
        "bolt": ("bolt",),
        "devin": ("devin",),
        "codeium-cli": ("codeium",),
        "aider-desk": ("aider-desk",),
        "neovim-avante": ("nvim",),
        "emacs-copilot": ("emacs",),
        "lapce": ("lapce",),
        "helix-agent": ("hx",),
    }
    for name in aliases.get(host, (host,)):
        if candidate := shutil.which(name):
            return candidate
    return None


def coverage_report() -> dict[str, Any]:
    tiers: dict[str, int] = {}
    verified = 0
    stream_capture = 0
    for spec in KNOWN_HOSTS.values():
        tier = integration_tier(spec)
        tiers[tier] = tiers.get(tier, 0) + 1
        verified += int(spec.verified)
        stream_capture += int(spec.supports_stream_capture)
    total = len(KNOWN_HOSTS)
    controlled = sum(
        count for tier, count in tiers.items()
        if tier in {"HOOK_ENFORCED", "MCP_CONTROLLED", "MCP_PLUS_PROXY", "PROXY_CONTROLLED"}
    )
    return {
        "hosts": total,
        "controlled_hosts": controlled,
        "verified_hosts": verified,
        "stream_capture_hosts": stream_capture,
        "coverage": controlled / total if total else 0.0,
        "tiers": dict(sorted(tiers.items())),
        "claim_boundary": "registry coverage is implementation coverage, not live host certification",
    }


def environment_capabilities() -> dict[str, Any]:
    return {
        "platform": os.name,
        "coverage": coverage_report(),
        "hosts": {name: asdict(spec) for name, spec in KNOWN_HOSTS.items()},
    }
