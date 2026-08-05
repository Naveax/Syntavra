#![forbid(unsafe_code)]
#![allow(clippy::pedantic)]

use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;

use serde_json::{json, Map, Value};

#[derive(Debug, Clone)]
struct HostSpec {
    host: String,
    display_name: String,
    supports_pre_tool_hook: bool,
    supports_post_tool_hook: bool,
    supports_result_replacement: bool,
    supports_mcp: bool,
    supports_proxy: bool,
    supports_session_events: bool,
    supports_usage_telemetry: bool,
    supports_background_jobs: bool,
    supports_native_skill: bool,
    verified: bool,
    project_markers: Vec<String>,
    user_markers: Vec<String>,
    config_path: String,
    skill_path: String,
    supports_stream_capture: bool,
    integration_notes: Vec<String>,
}

#[allow(clippy::too_many_arguments)]
fn spec(
    host: &str,
    display_name: &str,
    flags: [bool; 10],
    project_markers: &[&str],
    user_markers: &[&str],
    config_path: &str,
    skill_path: &str,
    supports_stream_capture: bool,
    integration_notes: &[&str],
) -> HostSpec {
    HostSpec {
        host: host.to_owned(),
        display_name: display_name.to_owned(),
        supports_pre_tool_hook: flags[0],
        supports_post_tool_hook: flags[1],
        supports_result_replacement: flags[2],
        supports_mcp: flags[3],
        supports_proxy: flags[4],
        supports_session_events: flags[5],
        supports_usage_telemetry: flags[6],
        supports_background_jobs: flags[7],
        supports_native_skill: flags[8],
        verified: flags[9],
        project_markers: project_markers
            .iter()
            .map(|value| (*value).to_owned())
            .collect(),
        user_markers: user_markers
            .iter()
            .map(|value| (*value).to_owned())
            .collect(),
        config_path: config_path.to_owned(),
        skill_path: skill_path.to_owned(),
        supports_stream_capture,
        integration_notes: integration_notes
            .iter()
            .map(|value| (*value).to_owned())
            .collect(),
    }
}

fn host_specs() -> Vec<HostSpec> {
    vec![
        spec(
            "codex",
            "OpenAI Codex",
            [
                false, false, false, true, false, true, true, true, true, true,
            ],
            &[".codex"],
            &[".codex"],
            ".codex/mcp.json",
            ".codex/skills/syntavra",
            false,
            &["mcp", "native-skill", "session-events"],
        ),
        spec(
            "claude-code",
            "Claude Code",
            [true, true, true, true, false, true, false, true, true, true],
            &[".claude"],
            &[".claude"],
            ".claude/settings.json",
            ".claude/skills/syntavra",
            true,
            &["hook-enforced", "mcp", "stream-capture"],
        ),
        spec(
            "gemini-cli",
            "Gemini CLI",
            [true, true, true, true, false, true, true, true, true, true],
            &[".gemini", "gemini-extension.json"],
            &[".gemini"],
            ".gemini/settings.json",
            ".gemini/skills/syntavra",
            true,
            &["hook-enforced", "usage-telemetry", "stream-capture"],
        ),
        spec(
            "opencode",
            "OpenCode",
            [false, false, true, true, true, true, true, true, true, true],
            &[".opencode", "opencode.json"],
            &[".config/opencode"],
            ".opencode/opencode.json",
            ".opencode/skills/syntavra",
            true,
            &["mcp", "proxy", "stream-capture"],
        ),
        spec(
            "cursor",
            "Cursor",
            [
                false, false, true, true, false, true, false, true, false, true,
            ],
            &[".cursor"],
            &[".cursor"],
            ".cursor/mcp.json",
            ".cursor/rules/syntavra.mdc",
            false,
            &["mcp", "rules"],
        ),
        spec(
            "windsurf",
            "Windsurf",
            [
                false, false, true, true, false, true, false, true, true, true,
            ],
            &[".windsurf"],
            &[".codeium/windsurf"],
            ".windsurf/mcp.json",
            ".windsurf/skills/syntavra",
            false,
            &["mcp", "native-skill"],
        ),
        spec(
            "vscode-copilot",
            "VS Code / GitHub Copilot",
            [
                false, false, true, true, false, true, false, true, true, true,
            ],
            &[".vscode/mcp.json"],
            &[],
            ".vscode/mcp.json",
            ".github/skills/syntavra",
            false,
            &["mcp", "repository-instructions"],
        ),
        spec(
            "cline",
            "Cline",
            [
                false, false, true, true, false, true, false, true, false, true,
            ],
            &[".cline", ".clinerules"],
            &[".cline"],
            ".cline/mcp_settings.json",
            ".clinerules/00-syntavra.md",
            false,
            &["mcp", "rules"],
        ),
        spec(
            "roo-code",
            "Roo Code",
            [
                false, false, true, true, false, true, false, true, false, false,
            ],
            &[".roo", ".roomodes"],
            &[".roo"],
            ".roo/mcp.json",
            "AGENTS.md",
            false,
            &["mcp", "agents-instructions"],
        ),
        spec(
            "continue",
            "Continue",
            [
                false, false, true, true, false, true, false, true, false, true,
            ],
            &[".continue"],
            &[".continue"],
            ".continue/mcp.json",
            ".continue/rules/00-syntavra.md",
            false,
            &["mcp", "rules"],
        ),
        spec(
            "qwen-code",
            "Qwen Code",
            [
                false, false, true, true, false, true, true, true, true, false,
            ],
            &[".qwen"],
            &[".qwen"],
            ".qwen/mcp.json",
            ".qwen/skills/syntavra",
            false,
            &["mcp", "native-skill", "usage-telemetry"],
        ),
        spec(
            "kiro",
            "Kiro CLI",
            [
                false, false, true, true, false, true, false, true, true, false,
            ],
            &[".kiro"],
            &[".kiro"],
            ".kiro/settings/mcp.json",
            ".kiro/skills/syntavra",
            false,
            &["mcp", "native-skill", "steering"],
        ),
        spec(
            "antigravity",
            "Google Antigravity",
            [
                false, false, true, true, false, true, false, true, true, true,
            ],
            &[".agents"],
            &[".gemini/config"],
            ".agents/mcp.json",
            ".agents/skills/syntavra",
            false,
            &["mcp", "native-skill"],
        ),
        spec(
            "antigravity-cli",
            "Google Antigravity CLI",
            [
                false, false, true, true, false, true, false, true, true, true,
            ],
            &[".agent"],
            &[".gemini/antigravity-cli"],
            ".agent/mcp.json",
            ".agent/skills/syntavra",
            false,
            &["mcp", "native-skill"],
        ),
        spec(
            "zed",
            "Zed",
            [
                false, false, true, true, false, true, false, true, false, false,
            ],
            &[".zed"],
            &[".config/zed"],
            ".zed/settings.json",
            "AGENTS.md",
            false,
            &["mcp", "agents-instructions"],
        ),
        spec(
            "pi",
            "Pi Coding Agent",
            [
                false, false, false, false, false, false, false, false, true, false,
            ],
            &[".pi"],
            &[".pi/agent"],
            "",
            ".pi/skills/syntavra",
            false,
            &[
                "native-skill",
                "extension-capable",
                "instruction-only-adapter",
            ],
        ),
        spec(
            "omp",
            "Oh My Pi",
            [
                false, false, false, false, false, false, false, false, true, false,
            ],
            &[".omp"],
            &[".omp/agent"],
            "",
            ".omp/skills/syntavra",
            false,
            &[
                "native-skill",
                "mcp-capable-host",
                "instruction-only-adapter",
            ],
        ),
        spec(
            "openclaw",
            "OpenClaw",
            [
                false, false, false, false, false, false, false, false, true, false,
            ],
            &[".openclaw", "openclaw.json"],
            &[".openclaw"],
            "",
            "skills/syntavra",
            false,
            &[
                "workspace-skill",
                "plugin-compatible",
                "instruction-only-adapter",
            ],
        ),
        spec(
            "kilo-code",
            "Kilo Code",
            [
                false, false, true, true, false, true, false, true, false, false,
            ],
            &[".kilocode", ".kilocodemodes"],
            &[".kilocode"],
            ".kilocode/mcp.json",
            ".kilocode/rules/00-syntavra.md",
            false,
            &["mcp", "rules"],
        ),
        spec(
            "jetbrains-copilot",
            "JetBrains / GitHub Copilot",
            [
                false, false, true, true, false, true, false, true, false, false,
            ],
            &[".idea/mcp.json"],
            &[],
            ".idea/mcp.json",
            ".github/skills/syntavra",
            false,
            &["mcp", "repository-instructions"],
        ),
        spec(
            "sourcegraph-cody",
            "Sourcegraph Cody",
            [
                false, false, true, true, false, true, false, true, false, false,
            ],
            &[".sourcegraph"],
            &[".config/sourcegraph"],
            ".sourcegraph/mcp.json",
            "AGENTS.md",
            false,
            &["mcp", "agents-instructions"],
        ),
        spec(
            "goose",
            "Block Goose",
            [
                false, false, true, true, true, true, true, true, true, false,
            ],
            &[".goose"],
            &[".config/goose"],
            ".goose/config.yaml",
            ".goose/skills/syntavra",
            true,
            &["mcp", "proxy", "native-skill", "stream-capture"],
        ),
        spec(
            "generic-mcp",
            "Generic MCP client",
            [
                false, false, true, true, false, false, false, false, false, false,
            ],
            &[],
            &[],
            "",
            "",
            false,
            &["mcp"],
        ),
        spec(
            "aider",
            "Aider",
            [
                false, false, false, false, false, false, false, false, false, false,
            ],
            &[".aider.conf.yml"],
            &[],
            "",
            "AGENTS.md",
            false,
            &["instruction-only"],
        ),
        spec(
            "amazon-q",
            "Amazon Q Developer",
            [
                false, false, true, true, false, true, false, true, true, false,
            ],
            &[".amazonq"],
            &[".aws/amazonq"],
            ".amazonq/mcp.json",
            ".amazonq/rules/syntavra.md",
            false,
            &["mcp", "rules"],
        ),
        spec(
            "copilot-cli",
            "GitHub Copilot CLI",
            [
                false, false, true, true, false, true, false, true, true, false,
            ],
            &[".github"],
            &[".config/github-copilot"],
            ".github/copilot/mcp.json",
            ".github/copilot-instructions.md",
            false,
            &["mcp", "repository-instructions"],
        ),
        spec(
            "trae",
            "Trae",
            [
                false, false, true, true, false, true, false, true, false, false,
            ],
            &[".trae"],
            &[".trae"],
            ".trae/mcp.json",
            ".trae/rules/syntavra.md",
            false,
            &["mcp", "rules"],
        ),
        spec(
            "void",
            "Void",
            [
                false, false, true, true, false, true, false, true, false, false,
            ],
            &[".void"],
            &[".config/void"],
            ".void/mcp.json",
            ".void/rules/syntavra.md",
            false,
            &["mcp", "rules"],
        ),
        spec(
            "warp",
            "Warp Agent",
            [
                false, false, true, true, true, true, true, true, false, false,
            ],
            &[".warp"],
            &[".warp"],
            ".warp/mcp.json",
            "WARP.md",
            false,
            &["mcp", "proxy", "rules"],
        ),
        spec(
            "openhands",
            "OpenHands",
            [
                false, false, true, true, true, true, false, true, true, false,
            ],
            &[".openhands"],
            &[".openhands"],
            ".openhands/mcp.json",
            ".openhands/skills/syntavra",
            false,
            &["mcp", "proxy", "native-skill"],
        ),
        spec(
            "swe-agent",
            "SWE-agent",
            [
                false, false, true, true, false, true, false, true, false, false,
            ],
            &[".swe-agent"],
            &[".config/swe-agent"],
            ".swe-agent/mcp.json",
            ".swe-agent/instructions/syntavra.md",
            false,
            &["mcp", "instructions"],
        ),
        spec(
            "mentat",
            "Mentat",
            [
                false, false, true, true, false, true, false, true, false, false,
            ],
            &[".mentat"],
            &[".mentat"],
            ".mentat/mcp.json",
            ".mentat/rules/syntavra.md",
            false,
            &["mcp", "rules"],
        ),
        spec(
            "plandex",
            "Plandex",
            [
                false, false, true, true, true, true, false, true, false, false,
            ],
            &[".plandex"],
            &[".plandex"],
            ".plandex/mcp.json",
            ".plandex/skills/syntavra",
            false,
            &["mcp", "proxy", "skill"],
        ),
        spec(
            "tabby",
            "Tabby",
            [
                false, false, true, true, true, false, false, false, false, false,
            ],
            &[".tabby"],
            &[".tabby"],
            ".tabby/mcp.json",
            ".tabby/instructions/syntavra.md",
            false,
            &["mcp", "proxy"],
        ),
        spec(
            "pearai",
            "PearAI",
            [
                false, false, true, true, false, true, false, true, false, false,
            ],
            &[".pearai"],
            &[".pearai"],
            ".pearai/mcp.json",
            ".pearai/rules/syntavra.md",
            false,
            &["mcp", "rules"],
        ),
        spec(
            "replit-agent",
            "Replit Agent",
            [
                false, false, true, true, true, true, false, true, false, false,
            ],
            &[".replit", "replit.nix"],
            &[],
            ".replit/mcp.json",
            "replit.md",
            false,
            &["mcp", "proxy", "instructions"],
        ),
        spec(
            "bolt",
            "Bolt",
            [
                false, false, true, true, true, false, false, false, false, false,
            ],
            &[".bolt"],
            &[".bolt"],
            ".bolt/mcp.json",
            ".bolt/rules/syntavra.md",
            false,
            &["mcp", "proxy"],
        ),
        spec(
            "devin",
            "Devin",
            [
                false, false, true, true, false, true, false, true, false, false,
            ],
            &[".devin"],
            &[".devin"],
            ".devin/mcp.json",
            ".devin/knowledge/syntavra.md",
            false,
            &["mcp", "knowledge"],
        ),
        spec(
            "codeium-cli",
            "Codeium CLI",
            [
                false, false, true, true, false, true, false, true, false, false,
            ],
            &[".codeium"],
            &[".codeium"],
            ".codeium/mcp.json",
            ".codeium/rules/syntavra.md",
            false,
            &["mcp", "rules"],
        ),
        spec(
            "aider-desk",
            "AiderDesk",
            [
                false, false, true, true, false, true, false, true, false, false,
            ],
            &[".aider-desk"],
            &[".aider-desk"],
            ".aider-desk/mcp.json",
            "AGENTS.md",
            false,
            &["mcp", "agents-instructions"],
        ),
        spec(
            "neovim-avante",
            "Neovim Avante",
            [
                false, false, true, true, false, false, false, false, false, false,
            ],
            &[".nvim"],
            &[".config/nvim"],
            ".nvim/syntavra-mcp.json",
            "AGENTS.md",
            false,
            &["mcp", "agents-instructions"],
        ),
        spec(
            "emacs-copilot",
            "Emacs Copilot",
            [
                false, false, true, true, false, false, false, false, false, false,
            ],
            &[".dir-locals.el"],
            &[".emacs.d"],
            ".emacs.d/syntavra-mcp.json",
            "AGENTS.md",
            false,
            &["mcp", "agents-instructions"],
        ),
        spec(
            "lapce",
            "Lapce",
            [
                false, false, true, true, false, false, false, false, false, false,
            ],
            &[".lapce"],
            &[".config/lapce"],
            ".lapce/mcp.json",
            ".lapce/rules/syntavra.md",
            false,
            &["mcp", "rules"],
        ),
        spec(
            "helix-agent",
            "Helix Agent",
            [
                false, false, true, true, false, false, false, false, false, false,
            ],
            &[".helix"],
            &[".config/helix"],
            ".helix/mcp.json",
            "AGENTS.md",
            false,
            &["mcp", "agents-instructions"],
        ),
    ]
}

fn host_spec(host: &str) -> HostSpec {
    let folded = host.to_lowercase();
    host_specs()
        .into_iter()
        .find(|value| value.host == folded)
        .unwrap_or_else(|| spec(&folded, host, [false; 10], &[], &[], "", "", false, &[]))
}

fn capabilities(spec: &HostSpec) -> Value {
    json!({
        "host": spec.host,
        "display_name": spec.display_name,
        "supports_pre_tool_hook": spec.supports_pre_tool_hook,
        "supports_post_tool_hook": spec.supports_post_tool_hook,
        "supports_result_replacement": spec.supports_result_replacement,
        "supports_mcp": spec.supports_mcp,
        "supports_proxy": spec.supports_proxy,
        "supports_session_events": spec.supports_session_events,
        "supports_usage_telemetry": spec.supports_usage_telemetry,
        "supports_background_jobs": spec.supports_background_jobs,
        "supports_native_skill": spec.supports_native_skill,
        "verified": spec.verified,
        "project_markers": spec.project_markers,
        "user_markers": spec.user_markers,
        "config_path": spec.config_path,
        "skill_path": spec.skill_path,
        "supports_stream_capture": spec.supports_stream_capture,
        "integration_notes": spec.integration_notes,
    })
}

fn integration_tier(spec: &HostSpec) -> &'static str {
    if spec.supports_pre_tool_hook
        && spec.supports_post_tool_hook
        && spec.supports_result_replacement
    {
        "HOOK_ENFORCED"
    } else if spec.supports_mcp && spec.supports_proxy {
        "MCP_PLUS_PROXY"
    } else if spec.supports_mcp {
        "MCP_CONTROLLED"
    } else if spec.supports_proxy {
        "PROXY_CONTROLLED"
    } else if spec.supports_native_skill {
        "INSTRUCTION_ONLY"
    } else {
        "UNSUPPORTED"
    }
}

fn negotiate_value(host: &str, runtime_available: bool, installed: Option<bool>) -> Value {
    let active = host_spec(host);
    let tier = integration_tier(&active);
    let mut mode = if runtime_available {
        tier
    } else if active.supports_native_skill {
        "INSTRUCTION_ONLY"
    } else {
        "UNSUPPORTED"
    };
    if installed == Some(false) && !matches!(mode, "UNSUPPORTED" | "INSTRUCTION_ONLY") {
        mode = "RUNTIME_PARTIAL";
    }
    json!({
        "mode": mode,
        "integration_tier": tier,
        "enforced": matches!(mode, "HOOK_ENFORCED" | "MCP_CONTROLLED" | "MCP_PLUS_PROXY" | "PROXY_CONTROLLED"),
        "installed": installed,
        "verified_adapter": active.verified,
        "stream_capture": active.supports_stream_capture,
        "capabilities": capabilities(&active),
    })
}

fn environment_capabilities() -> Value {
    let specs = host_specs();
    let mut tiers = BTreeMap::<String, u64>::new();
    let mut verified = 0u64;
    let mut stream_capture = 0u64;
    let mut controlled = 0u64;
    let mut hosts = Map::new();
    for active in &specs {
        let tier = integration_tier(active);
        *tiers.entry(tier.to_owned()).or_default() += 1;
        verified += if active.verified { 1 } else { 0 };
        stream_capture += if active.supports_stream_capture { 1 } else { 0 };
        if matches!(
            tier,
            "HOOK_ENFORCED" | "MCP_CONTROLLED" | "MCP_PLUS_PROXY" | "PROXY_CONTROLLED"
        ) {
            controlled += 1;
        }
        hosts.insert(active.host.clone(), capabilities(active));
    }
    let total = specs.len() as u64;
    json!({
        "platform": if cfg!(windows) { "nt" } else { "posix" },
        "coverage": {
            "hosts": total,
            "controlled_hosts": controlled,
            "verified_hosts": verified,
            "stream_capture_hosts": stream_capture,
            "coverage": if total == 0 { 0.0 } else { controlled as f64 / total as f64 },
            "tiers": tiers,
            "claim_boundary": "registry coverage is implementation coverage, not live host certification",
        },
        "hosts": hosts,
    })
}

pub(crate) fn platform_plan_contract(
    host: &str,
    project: &Path,
    scope: &str,
) -> Result<Value, String> {
    if !matches!(scope, "project" | "user") {
        return Err("scope must be project or user".to_owned());
    }
    let active = host_specs()
        .into_iter()
        .find(|spec| spec.host == host)
        .ok_or_else(|| format!("unknown host: {host}"))?;
    if host != "generic-mcp" {
        return fabric_install_contract(host, project, scope)
            .map(|contract| contract["plan"].clone());
    }
    let negotiation = negotiate_value(host, true, None);
    Ok(json!({
        "host": active.host,
        "display_name": active.display_name,
        "scope": scope,
        "project": project.to_string_lossy(),
        "mode": negotiation["mode"],
        "enforced": negotiation["enforced"],
        "verified_adapter": active.verified,
        "files": [],
        "capabilities": capabilities(&active),
        "validation": [
            "syntavra doctor",
            format!("syntavra host negotiate --host-name {}", active.host),
            "syntavra status",
        ],
    }))
}

pub(crate) fn all_platform_plan_contracts(project: &Path, scope: &str) -> Result<Value, String> {
    if !matches!(scope, "project" | "user") {
        return Err("scope must be project or user".to_owned());
    }
    let mut hosts = host_specs()
        .into_iter()
        .map(|spec| spec.host)
        .filter(|host| host != "generic-mcp")
        .collect::<Vec<_>>();
    hosts.sort();
    let plans = hosts
        .iter()
        .map(|host| platform_plan_contract(host, project, scope))
        .collect::<Result<Vec<_>, _>>()?;
    Ok(json!({
        "host_count": plans.len(),
        "enforced_count": plans
            .iter()
            .filter(|plan| plan["enforced"].as_bool() == Some(true))
            .count(),
        "verified_count": plans
            .iter()
            .filter(|plan| plan["verified_adapter"].as_bool() == Some(true))
            .count(),
        "hosts": plans,
    }))
}

pub(crate) fn fabric_install_contract(
    host: &str,
    project: &Path,
    scope: &str,
) -> Result<Value, String> {
    let normalized = host.to_lowercase();
    let specs = host_specs();
    let active = specs
        .into_iter()
        .find(|spec| spec.host == normalized)
        .filter(|spec| spec.host != "generic-mcp")
        .ok_or_else(|| format!("unsupported concrete host: {host}"))?;

    let mut overlay = Map::new();
    overlay.insert(
        "mcpServers".to_owned(),
        json!({"syntavra": {"command": "syntavra", "args": ["mcp"]}}),
    );
    if active.host == "claude-code" {
        overlay.insert(
            "statusLine".to_owned(),
            json!({"type": "command", "command": "syntavra run statusline"}),
        );
    }
    if active.supports_pre_tool_hook || active.supports_post_tool_hook {
        overlay.insert(
            "hooks".to_owned(),
            json!({
                "PreToolUse": [{"type": "command", "command": "syntavra hook pre"}],
                "PostToolUse": [{"type": "command", "command": "syntavra hook post"}],
                "UserPromptSubmit": [{"type": "command", "command": "syntavra hook prompt"}],
                "PreCompact": [{"type": "command", "command": "syntavra hook pre-compact"}],
                "SessionStart": [{"type": "command", "command": "syntavra hook session-start"}],
                "Stop": [{"type": "command", "command": "syntavra hook stop"}],
                "SessionEnd": [{"type": "command", "command": "syntavra hook session-end"}],
            }),
        );
    }

    let negotiation = negotiate_value(&normalized, true, None);
    let mut files = Vec::<Value>::new();
    if !active.config_path.is_empty() {
        files.push(json!({"path": active.config_path, "merge": Value::Object(overlay.clone())}));
    }
    if !active.skill_path.is_empty() {
        let skill_plan_path = if active.skill_path.ends_with(".md") {
            active.skill_path.clone()
        } else {
            format!("{}/SKILL.md", active.skill_path.trim_end_matches('/'))
        };
        files.push(json!({"path": skill_plan_path, "source": "bundled syntavra skill"}));
    }
    let plan = json!({
        "host": active.host,
        "display_name": active.display_name,
        "scope": scope,
        "project": project.to_string_lossy(),
        "mode": negotiation["mode"],
        "enforced": negotiation["enforced"],
        "verified_adapter": active.verified,
        "files": files,
        "capabilities": capabilities(&active),
        "validation": [
            "syntavra doctor",
            format!("syntavra host negotiate --host-name {}", active.host),
            "syntavra status",
        ],
    });
    Ok(json!({
        "host": active.host,
        "config_path": active.config_path,
        "skill_path": active.skill_path,
        "hooks_required": active.supports_pre_tool_hook || active.supports_post_tool_hook,
        "overlay": Value::Object(overlay),
        "negotiation_installed_true": negotiate_value(&normalized, true, Some(true)),
        "negotiation_installed_false": negotiate_value(&normalized, true, Some(false)),
        "plan": plan,
    }))
}

pub(crate) fn doctor_contract(host: &str) -> Value {
    let active = host_spec(host);
    let specs = host_specs();
    let negotiation = negotiate_value(host, true, None);
    json!({
        "known_host": specs.iter().any(|spec| spec.host == host),
        "mcp_available": active.supports_mcp,
        "result_replacement": active.supports_result_replacement,
        "enforced_mode": negotiation
            .get("enforced")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        "platform_registry_size": specs.len(),
        "negotiation": negotiation,
    })
}

fn option_value(arguments: &[String], flag: &str) -> Result<Option<String>, String> {
    let mut result = None;
    let prefix = format!("{flag}=");
    let mut index = 0usize;
    while index < arguments.len() {
        if arguments[index] == flag {
            index += 1;
            result = Some(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?
                    .clone(),
            );
        } else if let Some(value) = arguments[index].strip_prefix(&prefix) {
            result = Some(value.to_owned());
        }
        index += 1;
    }
    Ok(result)
}

fn positional_host(arguments: &[String]) -> Option<String> {
    let start = arguments
        .windows(2)
        .position(|window| window[0] == "host" && window[1] == "negotiate")?
        + 2;
    arguments[start..]
        .iter()
        .find(|value| !value.starts_with('-'))
        .cloned()
}

fn lexical_absolute(path: &Path) -> Result<PathBuf, String> {
    let joined = if path.is_absolute() {
        path.to_path_buf()
    } else {
        env::current_dir()
            .map_err(|error| format!("HOST_CURRENT_DIR_FAILED:{error}"))?
            .join(path)
    };
    Ok(fs::canonicalize(&joined).unwrap_or(joined))
}

fn home_path(arguments: &[String]) -> Result<PathBuf, String> {
    if let Some(value) = option_value(arguments, "--home")? {
        return lexical_absolute(Path::new(&value));
    }
    #[cfg(windows)]
    {
        if let Some(value) = env::var_os("USERPROFILE") {
            return lexical_absolute(Path::new(&value));
        }
        if let (Some(drive), Some(path)) = (env::var_os("HOMEDRIVE"), env::var_os("HOMEPATH")) {
            let mut value = PathBuf::from(drive);
            value.push(path);
            return lexical_absolute(&value);
        }
    }
    #[cfg(not(windows))]
    {
        if let Some(value) = env::var_os("HOME") {
            return lexical_absolute(Path::new(&value));
        }
    }
    Err("HOST_HOME_UNAVAILABLE".to_owned())
}

fn executable_aliases(host: &str) -> &'static [&'static str] {
    match host {
        "codex" => &["codex"],
        "claude-code" => &["claude"],
        "gemini-cli" => &["gemini"],
        "opencode" => &["opencode"],
        "cursor" => &["cursor"],
        "windsurf" => &["windsurf"],
        "vscode-copilot" => &["code"],
        "cline" => &[],
        "roo-code" => &[],
        "continue" => &[],
        "qwen-code" => &["qwen", "qwen-code"],
        "kiro" => &["kiro", "kiro-cli", "q"],
        "antigravity" => &["antigravity"],
        "antigravity-cli" => &["antigravity"],
        "zed" => &["zed"],
        "pi" => &["pi"],
        "omp" => &["omp"],
        "openclaw" => &["openclaw"],
        "kilo-code" => &["kilo", "kilocode"],
        "jetbrains-copilot" => &["idea", "pycharm", "webstorm"],
        "sourcegraph-cody" => &["cody"],
        "goose" => &["goose"],
        "aider" => &["aider"],
        "amazon-q" => &["q", "amazon-q"],
        "copilot-cli" => &["github-copilot", "copilot"],
        "trae" => &["trae"],
        "void" => &["void"],
        "warp" => &["warp"],
        "openhands" => &["openhands"],
        "swe-agent" => &["swe-agent"],
        "mentat" => &["mentat"],
        "plandex" => &["plandex"],
        "tabby" => &["tabby"],
        "pearai" => &["pearai"],
        "replit-agent" => &["replit"],
        "bolt" => &["bolt"],
        "devin" => &["devin"],
        "codeium-cli" => &["codeium"],
        "aider-desk" => &["aider-desk"],
        "neovim-avante" => &["nvim"],
        "emacs-copilot" => &["emacs"],
        "lapce" => &["lapce"],
        "helix-agent" => &["hx"],
        _ => &[],
    }
}

fn is_executable(path: &Path) -> bool {
    let Ok(metadata) = fs::metadata(path) else {
        return false;
    };
    if !metadata.is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        metadata.permissions().mode() & 0o111 != 0
    }
    #[cfg(windows)]
    {
        true
    }
}

#[cfg(windows)]
fn candidate_names(name: &str) -> Vec<String> {
    if Path::new(name).extension().is_some() {
        return vec![name.to_owned()];
    }
    let extensions = env::var("PATHEXT").unwrap_or_else(|_| ".COM;.EXE;.BAT;.CMD".to_owned());
    extensions
        .split(';')
        .filter(|value| !value.is_empty())
        .map(|value| format!("{name}{value}"))
        .collect()
}

#[cfg(not(windows))]
fn candidate_names(name: &str) -> Vec<String> {
    vec![name.to_owned()]
}

fn find_executable(host: &str) -> Option<String> {
    let aliases = executable_aliases(host);
    if aliases.is_empty() {
        return None;
    }
    let search = env::var_os("PATH").unwrap_or_default();
    for alias in aliases {
        for directory in env::split_paths(&search) {
            for name in candidate_names(alias) {
                let candidate = directory.join(name);
                if is_executable(&candidate) {
                    return Some(candidate.to_string_lossy().into_owned());
                }
            }
        }
    }
    None
}

fn detect_hosts(project_root: &Path, arguments: &[String]) -> Result<Value, String> {
    let project = fs::canonicalize(project_root)
        .map_err(|error| format!("HOST_PROJECT_RESOLVE_FAILED:{error}"))?;
    let home = home_path(arguments)?;
    let mut detected = Vec::new();
    for active in host_specs() {
        if active.host == "generic-mcp" {
            continue;
        }
        let project_markers = active
            .project_markers
            .iter()
            .filter(|marker| project.join(marker).exists())
            .cloned()
            .collect::<Vec<_>>();
        let user_markers = active
            .user_markers
            .iter()
            .filter(|marker| home.join(marker).exists())
            .cloned()
            .collect::<Vec<_>>();
        let executable = find_executable(&active.host);
        if project_markers.is_empty() && user_markers.is_empty() && executable.is_none() {
            continue;
        }
        let confidence = if executable.is_some() || !project_markers.is_empty() {
            "strong"
        } else {
            "user-config"
        };
        detected.push(json!({
            "host": active.host,
            "display_name": active.display_name,
            "project_markers": project_markers,
            "user_markers": user_markers,
            "executable": executable,
            "detection_confidence": confidence,
            "negotiation": negotiate_value(&active.host, true, Some(true)),
        }));
    }
    Ok(json!({"hosts": detected}))
}

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root] if root == "host")
        || matches!(command, [root, action]
            if root == "host" && matches!(action.as_str(), "negotiate" | "detect" | "capabilities"))
}

pub fn execute(
    command: &[String],
    arguments: &[String],
    project_root: &Path,
) -> Result<Value, String> {
    let action = command.get(1).map_or("negotiate", String::as_str);
    match action {
        "capabilities" => Ok(environment_capabilities()),
        "detect" => detect_hosts(project_root, arguments),
        "negotiate" => {
            let host = positional_host(arguments)
                .or(option_value(arguments, "--host")?)
                .unwrap_or_else(|| "codex".to_owned());
            let runtime_available = !arguments
                .iter()
                .any(|value| value == "--runtime-unavailable");
            Ok(negotiate_value(&host, runtime_available, None))
        }
        _ => Err("HOST_ACTION_UNSUPPORTED".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::{environment_capabilities, host_spec, integration_tier, supports};

    #[test]
    fn known_and_unknown_tiers_match_contract() {
        assert_eq!(integration_tier(&host_spec("claude-code")), "HOOK_ENFORCED");
        assert_eq!(integration_tier(&host_spec("goose")), "MCP_PLUS_PROXY");
        assert_eq!(integration_tier(&host_spec("codex")), "MCP_CONTROLLED");
        assert_eq!(integration_tier(&host_spec("pi")), "INSTRUCTION_ONLY");
        assert_eq!(integration_tier(&host_spec("unknown")), "UNSUPPORTED");
    }

    #[test]
    fn capabilities_report_has_canonical_totals() {
        let value = environment_capabilities();
        assert_eq!(value["coverage"]["hosts"], 44);
        assert_eq!(value["coverage"]["controlled_hosts"], 40);
        assert_eq!(value["coverage"]["verified_hosts"], 11);
        assert_eq!(value["coverage"]["stream_capture_hosts"], 4);
    }

    #[test]
    fn routes_public_host_surface() {
        for command in [
            vec!["host".to_owned()],
            vec!["host".to_owned(), "negotiate".to_owned()],
            vec!["host".to_owned(), "detect".to_owned()],
            vec!["host".to_owned(), "capabilities".to_owned()],
        ] {
            assert!(supports(&command));
        }
    }
}
