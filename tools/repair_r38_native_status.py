#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / "crates" / "syntavra-cli" / "src" / "native_status.rs"
EXPANSION = ROOT / "crates" / "syntavra-cli" / "src" / "native_expansion.rs"

MODULE_TOKEN = 'mod native_status;'
SUPPORT_TOKEN = '        || native_status::supports(command)\n'
EXECUTE_TOKEN = 'if native_status::supports(command) {'
TEST_TOKEN = 'vec!["status"]'
ADAPTER_TOKEN = 'fn detected_adapters() -> Vec<String> {'
DOCTOR_TOKEN = 'doctor.value.as_object_mut()'

MODULE_ANCHOR = '''#[path = "native_stats.rs"]
mod native_stats;
'''
MODULE_INSERT = '''#[path = "native_status.rs"]
mod native_status;
'''
SUPPORT_ANCHOR = '        || native_stats::supports(command)\n'
SUPPORT_INSERT = '        || native_status::supports(command)\n'
EXECUTE_ANCHOR = '''    if native_stats::supports(command) {
        return native_stats::execute(project_root, state_root);
    }
'''
EXECUTE_INSERT = '''    if native_status::supports(command) {
        let decision = native_status::execute(arguments, project_root, state_root)?;
        if decision.exit_code != 0 {
            emit_failed_value(&decision.value, decision.exit_code);
        }
        return Ok(decision.value);
    }
'''
TEST_ANCHOR = '            vec!["repair"],\n'
TEST_INSERT = '            vec!["status"],\n'

STATUS_IMPORT_ANCHOR = 'use std::fs;\n'
STATUS_IMPORT_INSERT = 'use std::env;\n'
STATUS_EXECUTE_ANCHOR = 'pub fn execute(\n'
STATUS_ADAPTER_HELPER = r'''fn executable_exists(name: &str) -> bool {
    let path = env::var_os("PATH").unwrap_or_default();
    let suffixes: &[&str] = if cfg!(windows) {
        &[".exe", ".cmd", ".bat", ""]
    } else {
        &[""]
    };
    env::split_paths(&path).any(|directory| {
        suffixes
            .iter()
            .any(|suffix| directory.join(format!("{name}{suffix}")).is_file())
    })
}

fn home_dir() -> Option<std::path::PathBuf> {
    env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" })
        .map(std::path::PathBuf::from)
}

fn candidate_exists(candidate: &str) -> bool {
    if let Some(rest) = candidate.strip_prefix("~/") {
        return home_dir().is_some_and(|home| home.join(rest).exists());
    }
    Path::new(candidate).exists()
}

fn detected_adapters() -> Vec<String> {
    let rows: &[(&str, &[&str], &[&str])] = &[
        ("claude-code", &["claude"], &["~/.claude/settings.json", ".claude/settings.json"]),
        ("codex", &["codex"], &["~/.codex/config.toml", "AGENTS.md"]),
        ("gemini-cli", &["gemini"], &["~/.gemini/settings.json", "GEMINI.md"]),
        ("vscode-copilot", &[], &[".vscode/mcp.json"]),
        ("jetbrains-copilot", &[], &[".idea/mcp.json"]),
        ("cursor", &["cursor"], &[".cursor/rules/syntavra.mdc", ".cursor/mcp.json"]),
        ("windsurf", &["windsurf"], &[".windsurfrules", ".codeium/windsurf/mcp_config.json"]),
        ("opencode", &["opencode"], &["opencode.json", "~/.config/opencode/opencode.json"]),
        ("cline", &[], &[".clinerules", ".vscode/mcp.json"]),
        ("roo-code", &[], &[".roo/rules/syntavra.md", ".vscode/mcp.json"]),
        ("qwen-code", &["qwen", "qwen-code"], &["QWEN.md", "~/.qwen/settings.json"]),
        ("kiro", &["kiro", "kiro-cli", "q"], &[".kiro/settings/mcp.json", ".kiro/skills/syntavra/SKILL.md"]),
        ("zed", &["zed"], &[".zed/settings.json", "~/.config/zed/settings.json"]),
        ("pi", &["pi"], &[".pi/settings.json", ".pi/skills/syntavra/SKILL.md"]),
        ("omp", &["omp"], &[".omp/agent/config.yml", ".omp/skills/syntavra/SKILL.md"]),
        ("openclaw", &["openclaw"], &["skills/syntavra/SKILL.md", ".openclaw/skills/syntavra/SKILL.md"]),
        ("aider", &["aider"], &[".aider.conf.yml", "~/.aider.conf.yml"]),
        ("continue", &["continue"], &[".continue/config.yaml", "~/.continue/config.yaml"]),
    ];
    rows.iter()
        .filter(|(_, commands, candidates)| {
            commands.iter().any(|command| executable_exists(command))
                || candidates.iter().any(|candidate| candidate_exists(candidate))
        })
        .map(|(host, _, _)| (*host).to_owned())
        .collect()
}

'''
LEGACY_DOCTOR = '''    let doctor = super::native_operator_lifecycle::execute(
        &doctor_command,
        project_root,
        state_root,
    )?;
'''
CANONICAL_DOCTOR = '''    let mut doctor = super::native_operator_lifecycle::execute(
        &doctor_command,
        project_root,
        state_root,
    )?;
    if let Some(value) = doctor.value.as_object_mut() {
        value.insert("detected_adapters".to_owned(), json!(detected_adapters()));
    }
'''


def insert_once(source: str, *, token: str, anchor: str, addition: str, label: str) -> tuple[str, bool]:
    count = source.count(token)
    if count == 1:
        return source, False
    if count != 0:
        raise RuntimeError(f"{label}: token count must be 0 or 1, got {count}")
    anchor_count = source.count(anchor)
    if anchor_count != 1:
        raise RuntimeError(f"{label}: anchor count must be 1, got {anchor_count}")
    return source.replace(anchor, anchor + addition, 1), True


def repair_status_contract() -> bool:
    source = STATUS.read_text(encoding="utf-8")
    rendered = source
    changed = False
    if STATUS_IMPORT_INSERT not in rendered:
        if rendered.count(STATUS_IMPORT_ANCHOR) != 1:
            raise RuntimeError("status env import anchor missing")
        rendered = rendered.replace(STATUS_IMPORT_ANCHOR, STATUS_IMPORT_INSERT + STATUS_IMPORT_ANCHOR, 1)
        changed = True
    if ADAPTER_TOKEN not in rendered:
        if rendered.count(STATUS_EXECUTE_ANCHOR) != 1:
            raise RuntimeError("status execute anchor missing")
        rendered = rendered.replace(STATUS_EXECUTE_ANCHOR, STATUS_ADAPTER_HELPER + STATUS_EXECUTE_ANCHOR, 1)
        changed = True
    legacy_count = rendered.count(LEGACY_DOCTOR)
    canonical_count = rendered.count(CANONICAL_DOCTOR)
    if legacy_count == 1 and canonical_count == 0:
        rendered = rendered.replace(LEGACY_DOCTOR, CANONICAL_DOCTOR, 1)
        changed = True
    elif legacy_count != 0 or canonical_count != 1:
        raise RuntimeError(
            f"status doctor adapter block invalid: legacy={legacy_count}, canonical={canonical_count}"
        )
    invalid = {
        ADAPTER_TOKEN: rendered.count(ADAPTER_TOKEN),
        DOCTOR_TOKEN: rendered.count(DOCTOR_TOKEN),
        STATUS_IMPORT_INSERT: rendered.count(STATUS_IMPORT_INSERT),
    }
    invalid = {key: value for key, value in invalid.items() if value != 1}
    if invalid:
        raise RuntimeError(f"native status contract invariant failed: {invalid}")
    if changed:
        STATUS.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_expansion() -> bool:
    source = EXPANSION.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for token, anchor, addition, label in (
        (MODULE_TOKEN, MODULE_ANCHOR, MODULE_INSERT, "status module"),
        (SUPPORT_TOKEN, SUPPORT_ANCHOR, SUPPORT_INSERT, "status support"),
        (EXECUTE_TOKEN, EXECUTE_ANCHOR, EXECUTE_INSERT, "status execute"),
        (TEST_TOKEN, TEST_ANCHOR, TEST_INSERT, "status route test"),
    ):
        rendered, applied = insert_once(
            rendered,
            token=token,
            anchor=anchor,
            addition=addition,
            label=label,
        )
        changed = changed or applied
    invalid = {
        token: rendered.count(token)
        for token in (MODULE_TOKEN, SUPPORT_TOKEN, EXECUTE_TOKEN, TEST_TOKEN)
        if rendered.count(token) != 1
    }
    if invalid:
        raise RuntimeError(f"native status wiring invariant failed: {invalid}")
    if changed:
        EXPANSION.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def main() -> int:
    if not STATUS.is_file():
        raise RuntimeError("native status module is missing")
    changed = repair_status_contract() | repair_expansion()
    print(json.dumps({"changed": changed, "ok": True, "surface": "status"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
