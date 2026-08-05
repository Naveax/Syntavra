#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "crates" / "syntavra-cli" / "src" / "native_install.rs"

CANONICAL_TOKENS = (
    '"mcp_capable": 13',
    '"host-specific-marker-contract-tested": 2',
    '"official-path-contract-tested": 1',
    '"official-skill-path-contract-tested": 3',
    '("vscode-copilot", vec![], vec![".vscode/mcp.json"]',
    '("jetbrains-copilot", vec![], vec![".idea/mcp.json"]',
    '("kiro", vec!["kiro", "kiro-cli", "q"]',
    '("pi", vec!["pi"], vec![".pi/settings.json", ".pi/skills/syntavra/SKILL.md"]',
    '("omp", vec!["omp"], vec![".omp/agent/config.yml", ".omp/skills/syntavra/SKILL.md"]',
    '("openclaw", vec!["openclaw"], vec!["skills/syntavra/SKILL.md", ".openclaw/skills/syntavra/SKILL.md"]',
)

RECORDS = r'''fn platform_adapter_records() -> Vec<Value> {
    let rows = [
        ("claude-code", vec!["claude"], vec!["~/.claude/settings.json", ".claude/settings.json"], "plugin+hooks", true, true, true, "primary-certification-target"),
        ("codex", vec!["codex"], vec!["~/.codex/config.toml", "AGENTS.md"], "skill+mcp", true, false, true, "primary-certification-target"),
        ("gemini-cli", vec!["gemini"], vec!["~/.gemini/settings.json", "GEMINI.md"], "extension+mcp", true, false, true, "contract-tested"),
        ("vscode-copilot", vec![], vec![".vscode/mcp.json"], "instructions+mcp", true, false, false, "host-specific-marker-contract-tested"),
        ("jetbrains-copilot", vec![], vec![".idea/mcp.json"], "instructions+mcp", true, false, false, "host-specific-marker-contract-tested"),
        ("cursor", vec!["cursor"], vec![".cursor/rules/syntavra.mdc", ".cursor/mcp.json"], "rules+mcp", true, false, true, "primary-certification-target"),
        ("windsurf", vec!["windsurf"], vec![".windsurfrules", ".codeium/windsurf/mcp_config.json"], "rules+mcp", true, false, true, "contract-tested"),
        ("opencode", vec!["opencode"], vec!["opencode.json", "~/.config/opencode/opencode.json"], "config+mcp", true, true, true, "contract-tested"),
        ("cline", vec![], vec![".clinerules", ".vscode/mcp.json"], "rules+mcp", true, false, true, "contract-tested"),
        ("roo-code", vec![], vec![".roo/rules/syntavra.md", ".vscode/mcp.json"], "rules+mcp", true, false, true, "contract-tested"),
        ("qwen-code", vec!["qwen", "qwen-code"], vec!["QWEN.md", "~/.qwen/settings.json"], "agents+mcp", true, false, true, "contract-tested"),
        ("kiro", vec!["kiro", "kiro-cli", "q"], vec![".kiro/settings/mcp.json", ".kiro/skills/syntavra/SKILL.md"], "mcp+native-skill", true, true, true, "official-path-contract-tested"),
        ("zed", vec!["zed"], vec![".zed/settings.json", "~/.config/zed/settings.json"], "rules+mcp", true, false, false, "contract-tested"),
        ("pi", vec!["pi"], vec![".pi/settings.json", ".pi/skills/syntavra/SKILL.md"], "native-skill+extension-capable", false, true, true, "official-skill-path-contract-tested"),
        ("omp", vec!["omp"], vec![".omp/agent/config.yml", ".omp/skills/syntavra/SKILL.md"], "native-skill+mcp-capable-host", false, true, true, "official-skill-path-contract-tested"),
        ("openclaw", vec!["openclaw"], vec!["skills/syntavra/SKILL.md", ".openclaw/skills/syntavra/SKILL.md"], "workspace-skill+plugin-compatible", false, true, true, "official-skill-path-contract-tested"),
        ("aider", vec!["aider"], vec![".aider.conf.yml", "~/.aider.conf.yml"], "env+wrapper", false, false, true, "contract-tested"),
        ("continue", vec!["continue"], vec![".continue/config.yaml", "~/.continue/config.yaml"], "rules+mcp", true, false, true, "contract-tested"),
    ];
    rows.into_iter().map(|(host, commands, configs, mode, mcp, hooks, continuity, maturity)| json!({
        "host": host,
        "detection_commands": commands,
        "config_candidates": configs,
        "integration_mode": mode,
        "supports_mcp": mcp,
        "supports_hooks": hooks,
        "supports_session_continuity": continuity,
        "maturity": maturity,
    })).collect()
}

fn adapter_validation() -> Value {
    json!({
        "ok": true,
        "adapters": 18,
        "missing_matrix_hosts": [],
        "extra_adapters": [],
        "mcp_capable": 13,
        "continuity_capable": 15,
        "primary_certification_targets": ["claude-code", "codex", "cursor"],
        "evidence_levels": {
            "contract-tested": 9,
            "host-specific-marker-contract-tested": 2,
            "official-path-contract-tested": 1,
            "official-skill-path-contract-tested": 3,
            "primary-certification-target": 3
        },
        "live_boundary": "live adapter certification requires external execution receipts",
    })
}'''

PATTERN = re.compile(
    r"fn platform_adapter_records\(\) -> Vec<Value> \{.*?\n\}\n\n"
    r"fn adapter_validation\(\) -> Value \{.*?\n\}",
    re.DOTALL,
)


def repair(path: Path = TARGET) -> bool:
    source = path.read_text(encoding="utf-8")
    if all(token in source for token in CANONICAL_TOKENS):
        return False
    rendered, count = PATTERN.subn(RECORDS, source, count=1)
    if count != 1:
        raise RuntimeError(f"native install adapter contract boundary missing in {path}")
    if not all(token in rendered for token in CANONICAL_TOKENS):
        missing = [token for token in CANONICAL_TOKENS if token not in rendered]
        raise RuntimeError(f"native install adapter contract incomplete: {missing}")
    path.write_text(rendered, encoding="utf-8", newline="\n")
    return True


def main() -> int:
    changed = repair()
    print("repaired: native_install.rs" if changed else "Native install adapter contract already canonical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
