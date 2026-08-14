from __future__ import annotations

from dataclasses import asdict, replace


def install() -> None:
    from . import codex_integration
    from . import competitive_fabric
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

    # CompetitiveContextFabric historically emitted a generic JSON MCP plan for
    # every host. Codex now has a TOML + explicit workspace-bridge contract, so its
    # planning surface must use the same canonical entry as both installers. Keep
    # every non-Codex host on the original planner to avoid widening this repair.
    original_platform_plan = competitive_fabric.PlatformPlanBuilder.plan

    def current_platform_plan(self, host: str, *, project, scope: str = "project"):
        if host != "codex":
            return original_platform_plan(self, host, project=project, scope=scope)
        if scope not in {"project", "user"}:
            raise ValueError("scope must be project or user")
        spec = host_adapters.host_spec("codex")
        negotiation = host_adapters.negotiate("codex", runtime_available=True, installed=None)
        entry = codex_integration.mcp_entry(("syntavra",), project=project, scope=scope)
        return {
            "host": "codex",
            "display_name": spec.display_name,
            "scope": scope,
            "project": str(project.resolve(strict=False)),
            "mode": negotiation["mode"],
            "enforced": negotiation["enforced"],
            "verified_adapter": spec.verified,
            "files": [
                {
                    "path": codex_integration.CODEX_CONFIG_PATH,
                    "format": "toml",
                    "entry": entry,
                },
                {
                    "path": f"{codex_integration.CODEX_SKILL_PATH}/SKILL.md",
                    "source": "bundled syntavra skill",
                },
            ],
            "capabilities": {
                **asdict(spec),
                "config_path": codex_integration.CODEX_CONFIG_PATH,
                "skill_path": codex_integration.CODEX_SKILL_PATH,
            },
            "validation": ["codex mcp list", "syntavra status --doctor", "syntavra status"],
        }

    competitive_fabric.PlatformPlanBuilder.plan = current_platform_plan
    host_adapters._syntavra_v001_platform_paths = True
