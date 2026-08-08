#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / "crates" / "syntavra-cli" / "src" / "native_status.rs"
EXPANSION = ROOT / "crates" / "syntavra-cli" / "src" / "native_expansion.rs"

ADAPTER_TOKEN = "fn detected_adapters() -> Vec<String> {"
DOCTOR_TOKEN = "doctor.value.as_object_mut()"

ADAPTER_HELPER = r'''fn executable_exists(name: &str) -> bool {
    let path = env::var_os("PATH").unwrap_or_default();
    let suffixes: &[&str] = if cfg!(windows) { &[".exe", ".cmd", ".bat", ""] } else { &[""] };
    env::split_paths(&path).any(|directory| suffixes.iter().any(|suffix| directory.join(format!("{name}{suffix}")).is_file()))
}

fn home_dir() -> Option<std::path::PathBuf> {
    env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" }).map(std::path::PathBuf::from)
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
        .filter(|(_, commands, candidates)| commands.iter().any(|command| executable_exists(command)) || candidates.iter().any(|candidate| candidate_exists(candidate)))
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


def add_once(source: str, token: str, anchor: str, addition: str, label: str) -> tuple[str, bool]:
    count = source.count(token)
    if count == 1:
        return source, False
    if count != 0 or source.count(anchor) != 1:
        raise RuntimeError(f"{label} invariant failed: token={count}, anchor={source.count(anchor)}")
    return source.replace(anchor, anchor + addition, 1), True


def repair_status() -> bool:
    source = STATUS.read_text(encoding="utf-8")
    rendered = source
    changed = False
    if "use std::env;\n" not in rendered:
        if rendered.count("use std::fs;\n") != 1:
            raise RuntimeError("status fs import anchor missing")
        rendered = rendered.replace("use std::fs;\n", "use std::env;\nuse std::fs;\n", 1)
        changed = True
    if ADAPTER_TOKEN not in rendered:
        if rendered.count("pub fn execute(\n") != 1:
            raise RuntimeError("status execute anchor missing")
        rendered = rendered.replace("pub fn execute(\n", ADAPTER_HELPER + "pub fn execute(\n", 1)
        changed = True
    if DOCTOR_TOKEN not in rendered:
        if rendered.count(LEGACY_DOCTOR) != 1:
            raise RuntimeError("legacy status doctor block missing")
        rendered = rendered.replace(LEGACY_DOCTOR, CANONICAL_DOCTOR, 1)
        changed = True
    for token in ("use std::env;\n", ADAPTER_TOKEN, DOCTOR_TOKEN):
        if rendered.count(token) != 1:
            raise RuntimeError(f"status semantic token count invalid: {token}={rendered.count(token)}")
    if changed:
        STATUS.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_expansion() -> bool:
    source = EXPANSION.read_text(encoding="utf-8")
    rendered = source
    changed = False
    rows = (
        (
            "mod native_status;",
            '#[path = "native_stats.rs"]\nmod native_stats;\n',
            '#[path = "native_status.rs"]\nmod native_status;\n',
            "status module",
        ),
        (
            "        || native_status::supports(command)\n",
            "        || native_stats::supports(command)\n",
            "        || native_status::supports(command)\n",
            "status support",
        ),
        (
            "if native_status::supports(command) {",
            '''    if native_stats::supports(command) {
        return native_stats::execute(project_root, state_root);
    }
''',
            '''    if native_status::supports(command) {
        let decision = native_status::execute(arguments, project_root, state_root)?;
        if decision.exit_code != 0 {
            emit_failed_value(&decision.value, decision.exit_code);
        }
        return Ok(decision.value);
    }
''',
            "status execute",
        ),
        (
            'vec!["status"]',
            '            vec!["repair"],\n',
            '            vec!["status"],\n',
            "status route test",
        ),
    )
    for token, anchor, addition, label in rows:
        rendered, applied = add_once(rendered, token, anchor, addition, label)
        changed = changed or applied
    for token, _, _, _ in rows:
        if rendered.count(token) != 1:
            raise RuntimeError(f"expansion status token count invalid: {token}={rendered.count(token)}")
    if changed:
        EXPANSION.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def main() -> int:
    changed = repair_status() | repair_expansion()
    print(json.dumps({"changed": changed, "ok": True, "surface": "status-v2"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
