from __future__ import annotations

from dataclasses import replace


def install() -> None:
    from . import host_adapters
    from . import product_surface

    if getattr(host_adapters, "_syntavra_v001_platform_paths", False):
        return

    # These hosts load Agent Skills natively. Their project adapters should copy
    # Syntavra's skill and must not invent unsupported MCP/settings keys.
    for host in ("pi", "omp", "openclaw"):
        current = host_adapters.KNOWN_HOSTS[host]
        host_adapters.KNOWN_HOSTS[host] = replace(current, config_path="")

    # Generic editor executables, project directories and shared instruction files
    # do not prove that a particular Copilot host integration is installed.
    vscode = host_adapters.KNOWN_HOSTS["vscode-copilot"]
    host_adapters.KNOWN_HOSTS["vscode-copilot"] = replace(
        vscode,
        project_markers=(".vscode/mcp.json",),
        user_markers=(),
    )
    jetbrains = host_adapters.KNOWN_HOSTS["jetbrains-copilot"]
    host_adapters.KNOWN_HOSTS["jetbrains-copilot"] = replace(
        jetbrains,
        project_markers=(".idea/mcp.json",),
        user_markers=(),
    )

    original_find_executable = host_adapters._find_executable

    def strict_find_executable(host: str) -> str | None:
        if host in {"vscode-copilot", "jetbrains-copilot"}:
            return None
        return original_find_executable(host)

    host_adapters._find_executable = strict_find_executable

    replacements = {
        "vscode-copilot": product_surface.PlatformAdapter(
            "vscode-copilot",
            (),
            (".vscode/mcp.json",),
            "instructions+mcp",
            True,
            False,
            False,
            "host-specific-marker-contract-tested",
        ),
        "jetbrains-copilot": product_surface.PlatformAdapter(
            "jetbrains-copilot",
            (),
            (".idea/mcp.json",),
            "instructions+mcp",
            True,
            False,
            False,
            "host-specific-marker-contract-tested",
        ),
        "kiro": product_surface.PlatformAdapter(
            "kiro",
            ("kiro", "kiro-cli", "q"),
            (".kiro/settings/mcp.json", ".kiro/skills/syntavra/SKILL.md"),
            "mcp+native-skill",
            True,
            True,
            True,
            "official-path-contract-tested",
        ),
        "pi": product_surface.PlatformAdapter(
            "pi",
            ("pi",),
            (".pi/settings.json", ".pi/skills/syntavra/SKILL.md"),
            "native-skill+extension-capable",
            False,
            True,
            True,
            "official-skill-path-contract-tested",
        ),
        "omp": product_surface.PlatformAdapter(
            "omp",
            ("omp",),
            (".omp/agent/config.yml", ".omp/skills/syntavra/SKILL.md"),
            "native-skill+mcp-capable-host",
            False,
            True,
            True,
            "official-skill-path-contract-tested",
        ),
        "openclaw": product_surface.PlatformAdapter(
            "openclaw",
            ("openclaw",),
            ("skills/syntavra/SKILL.md", ".openclaw/skills/syntavra/SKILL.md"),
            "workspace-skill+plugin-compatible",
            False,
            True,
            True,
            "official-skill-path-contract-tested",
        ),
    }
    product_surface.PLATFORM_ADAPTERS = tuple(
        replacements.get(item.host, item)
        for item in product_surface.PLATFORM_ADAPTERS
    )
    host_adapters._syntavra_v001_platform_paths = True
