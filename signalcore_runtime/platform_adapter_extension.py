from __future__ import annotations

from dataclasses import replace


def install() -> None:
    from . import host_adapters
    from . import product_surface

    if getattr(host_adapters, "_signalcore_v001_platform_paths", False):
        return

    # These hosts load Agent Skills natively. Their project adapters should copy
    # SignalCore's skill and must not invent unsupported MCP/settings keys.
    for host in ("pi", "omp", "openclaw"):
        current = host_adapters.KNOWN_HOSTS[host]
        host_adapters.KNOWN_HOSTS[host] = replace(current, config_path="")

    replacements = {
        "kiro": product_surface.PlatformAdapter(
            "kiro",
            ("kiro", "kiro-cli", "q"),
            (".kiro/settings/mcp.json", ".kiro/skills/signal-core/SKILL.md"),
            "mcp+native-skill",
            True,
            True,
            True,
            "official-path-contract-tested",
        ),
        "pi": product_surface.PlatformAdapter(
            "pi",
            ("pi",),
            (".pi/settings.json", ".pi/skills/signal-core/SKILL.md"),
            "native-skill+extension-capable",
            False,
            True,
            True,
            "official-skill-path-contract-tested",
        ),
        "omp": product_surface.PlatformAdapter(
            "omp",
            ("omp",),
            (".omp/agent/config.yml", ".omp/skills/signal-core/SKILL.md"),
            "native-skill+mcp-capable-host",
            False,
            True,
            True,
            "official-skill-path-contract-tested",
        ),
        "openclaw": product_surface.PlatformAdapter(
            "openclaw",
            ("openclaw",),
            ("skills/signal-core/SKILL.md", ".openclaw/skills/signal-core/SKILL.md"),
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
    host_adapters._signalcore_v001_platform_paths = True
