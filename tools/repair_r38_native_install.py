#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "crates" / "syntavra-cli" / "src" / "native_install.rs"
EXPANSION = ROOT / "crates" / "syntavra-cli" / "src" / "native_expansion.rs"
UNUSED_IMPORT = "use std::collections::{BTreeMap, BTreeSet};\n"

CANONICAL_TOKENS = (
    '"mcp_capable": 14',
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
        "mcp_capable": 14,
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

REPAIR_HELPERS = '''pub(super) fn repair_bundle(
    project_root: &Path,
    state_root: &Path,
    profile_name: &str,
) -> Result<Value, String> {
    fs::create_dir_all(project_root)
        .map_err(|error| format!("INSTALL_PROJECT_CREATE_FAILED:{error}"))?;
    fs::create_dir_all(state_root)
        .map_err(|error| format!("INSTALL_STATE_CREATE_FAILED:{error}"))?;
    let project = fs::canonicalize(project_root)
        .map_err(|error| format!("INSTALL_PROJECT_RESOLVE_FAILED:{error}"))?;
    let state = fs::canonicalize(state_root)
        .map_err(|error| format!("INSTALL_STATE_RESOLVE_FAILED:{error}"))?;
    profile(profile_name)?;
    write_bundle(&project, &state, profile_name)
}

pub(super) fn reapply_host(
    host: &str,
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    fs::create_dir_all(project_root)
        .map_err(|error| format!("INSTALL_PROJECT_CREATE_FAILED:{error}"))?;
    fs::create_dir_all(state_root)
        .map_err(|error| format!("INSTALL_STATE_CREATE_FAILED:{error}"))?;
    let project = fs::canonicalize(project_root)
        .map_err(|error| format!("INSTALL_PROJECT_RESOLVE_FAILED:{error}"))?;
    let state = fs::canonicalize(state_root)
        .map_err(|error| format!("INSTALL_STATE_RESOLVE_FAILED:{error}"))?;
    let spec = host_spec(host).ok_or_else(|| format!("unsupported concrete host: {host}"))?;
    let source = skill_root(&project);
    apply_host(spec, &project, &state, &source, false)
}

'''

EXPANSION_MODULE_ANCHOR = '''#[path = "native_session_status.rs"]
mod native_session_status;
'''
EXPANSION_MODULE_CANONICAL = '''#[path = "native_session_status.rs"]
mod native_session_status;
#[path = "native_setup_repair.rs"]
mod native_setup_repair;
'''
EXPANSION_SUPPORT_ANCHOR = '''        || native_session_status::supports(command)
        || native_stats::supports(command)
'''
EXPANSION_SUPPORT_CANONICAL = '''        || native_session_status::supports(command)
        || native_setup_repair::supports(command)
        || native_stats::supports(command)
'''
EXPANSION_EXECUTE_ANCHOR = '''    if native_install::supports(command) {
        return native_install::execute(arguments, project_root, state_root);
    }
    if native_job_mutations::supports(command) {
'''
EXPANSION_EXECUTE_CANONICAL = '''    if native_install::supports(command) {
        return native_install::execute(arguments, project_root, state_root);
    }
    if native_setup_repair::supports(command) {
        let decision = native_setup_repair::execute(
            command,
            arguments,
            project_root,
            state_root,
        )?;
        if decision.exit_code != 0 {
            emit_failed_value(&decision.value, decision.exit_code);
        }
        return Ok(decision.value);
    }
    if native_job_mutations::supports(command) {
'''
EXPANSION_TEST_ANCHOR = '''            vec!["session", "import"],
            vec!["uninstall"],
'''
EXPANSION_TEST_CANONICAL = '''            vec!["session", "import"],
            vec!["setup"],
            vec!["repair"],
            vec!["uninstall"],
'''


def replace_once(source: str, old: str, new: str, label: str) -> tuple[str, bool]:
    old_count = source.count(old)
    new_count = source.count(new)
    if old_count == 0 and new_count == 1:
        return source, False
    if old_count != 1 or new_count != 0:
        raise RuntimeError(
            f"{label}: expected one legacy or canonical fragment; "
            f"legacy={old_count}, canonical={new_count}"
        )
    return source.replace(old, new, 1), True


def repair_install(path: Path = TARGET) -> bool:
    source = path.read_text(encoding="utf-8")
    rendered = source
    changed = False
    if UNUSED_IMPORT in rendered:
        rendered = rendered.replace(UNUSED_IMPORT, "", 1)
        changed = True
    if not all(token in rendered for token in CANONICAL_TOKENS):
        rendered, count = PATTERN.subn(RECORDS, rendered, count=1)
        if count != 1:
            raise RuntimeError(f"native install adapter contract boundary missing in {path}")
        changed = True
    if "pub(super) fn repair_bundle(" not in rendered:
        anchor = "pub fn execute(\n"
        if rendered.count(anchor) != 1:
            raise RuntimeError(f"native install execute anchor missing in {path}")
        rendered = rendered.replace(anchor, REPAIR_HELPERS + anchor, 1)
        changed = True
    if UNUSED_IMPORT in rendered:
        raise RuntimeError(f"unused native install imports remain in {path}")
    if not all(token in rendered for token in CANONICAL_TOKENS):
        missing = [token for token in CANONICAL_TOKENS if token not in rendered]
        raise RuntimeError(f"native install adapter contract incomplete: {missing}")
    for token in ("pub(super) fn repair_bundle(", "pub(super) fn reapply_host("):
        if rendered.count(token) != 1:
            raise RuntimeError(f"native install repair helper invariant failed: {token}")
    if changed:
        path.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_expansion(path: Path = EXPANSION) -> bool:
    source = path.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for old, new, label in (
        (EXPANSION_MODULE_ANCHOR, EXPANSION_MODULE_CANONICAL, "setup repair module"),
        (EXPANSION_SUPPORT_ANCHOR, EXPANSION_SUPPORT_CANONICAL, "setup repair support"),
        (EXPANSION_EXECUTE_ANCHOR, EXPANSION_EXECUTE_CANONICAL, "setup repair execute"),
        (EXPANSION_TEST_ANCHOR, EXPANSION_TEST_CANONICAL, "setup repair route test"),
    ):
        rendered, applied = replace_once(rendered, old, new, label)
        changed = changed or applied
    for token in (
        'mod native_setup_repair;',
        'native_setup_repair::supports(command)',
        'native_setup_repair::execute(',
        'vec!["setup"]',
        'vec!["repair"]',
    ):
        if token not in rendered:
            raise RuntimeError(f"native expansion setup/repair invariant missing: {token}")
    if changed:
        path.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair(path: Path = TARGET) -> bool:
    return repair_install(path) | repair_expansion()


def main() -> int:
    changed = repair()
    print("repaired: native install/setup lifecycle" if changed else "Native install/setup lifecycle already canonical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
