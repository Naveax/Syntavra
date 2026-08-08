#![forbid(unsafe_code)]
#![allow(clippy::pedantic)]

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection};
use serde_json::{json, Map, Value};
use syntavra_core::sha256_hex;

const VERSION: &str = "0.0.1";
const CHANNEL: &str = "pre-release";
const TEXT_BEGIN: &str = "<!-- SYNTAVRA:BEGIN managed-host-integration -->";
const TEXT_END: &str = "<!-- SYNTAVRA:END managed-host-integration -->";

#[derive(Clone, Copy)]
struct HostSpec {
    host: &'static str,
    display_name: &'static str,
    pre_hook: bool,
    post_hook: bool,
    result_replacement: bool,
    mcp: bool,
    proxy: bool,
    session_events: bool,
    usage_telemetry: bool,
    background_jobs: bool,
    native_skill: bool,
    verified: bool,
    project_markers: &'static [&'static str],
    user_markers: &'static [&'static str],
    config_path: &'static str,
    skill_path: &'static str,
    stream_capture: bool,
    notes: &'static [&'static str],
}

const HOSTS: &[HostSpec] = &[
    HostSpec {
        host: "claude-code",
        display_name: "Claude Code",
        pre_hook: true,
        post_hook: true,
        result_replacement: true,
        mcp: true,
        proxy: false,
        session_events: true,
        usage_telemetry: false,
        background_jobs: true,
        native_skill: true,
        verified: true,
        project_markers: &[".claude"],
        user_markers: &[".claude"],
        config_path: ".claude/settings.json",
        skill_path: ".claude/skills/syntavra",
        stream_capture: true,
        notes: &["hook-enforced", "mcp", "stream-capture"],
    },
    HostSpec {
        host: "codex",
        display_name: "OpenAI Codex",
        pre_hook: false,
        post_hook: false,
        result_replacement: false,
        mcp: true,
        proxy: false,
        session_events: true,
        usage_telemetry: true,
        background_jobs: true,
        native_skill: true,
        verified: true,
        project_markers: &[".codex"],
        user_markers: &[".codex"],
        config_path: ".codex/mcp.json",
        skill_path: ".codex/skills/syntavra",
        stream_capture: false,
        notes: &["mcp", "native-skill", "session-events"],
    },
    HostSpec {
        host: "gemini-cli",
        display_name: "Gemini CLI",
        pre_hook: true,
        post_hook: true,
        result_replacement: true,
        mcp: true,
        proxy: false,
        session_events: true,
        usage_telemetry: true,
        background_jobs: true,
        native_skill: true,
        verified: true,
        project_markers: &[".gemini", "gemini-extension.json"],
        user_markers: &[".gemini"],
        config_path: ".gemini/settings.json",
        skill_path: ".gemini/skills/syntavra",
        stream_capture: true,
        notes: &["hook-enforced", "usage-telemetry", "stream-capture"],
    },
    HostSpec {
        host: "vscode-copilot",
        display_name: "VS Code / GitHub Copilot",
        pre_hook: false,
        post_hook: false,
        result_replacement: true,
        mcp: true,
        proxy: false,
        session_events: true,
        usage_telemetry: false,
        background_jobs: true,
        native_skill: true,
        verified: true,
        project_markers: &[".vscode", ".github/copilot-instructions.md"],
        user_markers: &[],
        config_path: ".vscode/mcp.json",
        skill_path: ".github/skills/syntavra",
        stream_capture: false,
        notes: &["mcp", "repository-instructions"],
    },
    HostSpec {
        host: "jetbrains-copilot",
        display_name: "JetBrains / GitHub Copilot",
        pre_hook: false,
        post_hook: false,
        result_replacement: true,
        mcp: true,
        proxy: false,
        session_events: true,
        usage_telemetry: false,
        background_jobs: true,
        native_skill: false,
        verified: false,
        project_markers: &[".idea"],
        user_markers: &[".config/JetBrains"],
        config_path: ".idea/mcp.json",
        skill_path: ".github/skills/syntavra",
        stream_capture: false,
        notes: &["mcp", "repository-instructions"],
    },
    HostSpec {
        host: "cursor",
        display_name: "Cursor",
        pre_hook: false,
        post_hook: false,
        result_replacement: true,
        mcp: true,
        proxy: false,
        session_events: true,
        usage_telemetry: false,
        background_jobs: true,
        native_skill: false,
        verified: true,
        project_markers: &[".cursor"],
        user_markers: &[".cursor"],
        config_path: ".cursor/mcp.json",
        skill_path: ".cursor/rules/syntavra.mdc",
        stream_capture: false,
        notes: &["mcp", "rules"],
    },
    HostSpec {
        host: "windsurf",
        display_name: "Windsurf",
        pre_hook: false,
        post_hook: false,
        result_replacement: true,
        mcp: true,
        proxy: false,
        session_events: true,
        usage_telemetry: false,
        background_jobs: true,
        native_skill: true,
        verified: true,
        project_markers: &[".windsurf"],
        user_markers: &[".codeium/windsurf"],
        config_path: ".windsurf/mcp.json",
        skill_path: ".windsurf/skills/syntavra",
        stream_capture: false,
        notes: &["mcp", "native-skill"],
    },
    HostSpec {
        host: "opencode",
        display_name: "OpenCode",
        pre_hook: false,
        post_hook: false,
        result_replacement: true,
        mcp: true,
        proxy: true,
        session_events: true,
        usage_telemetry: true,
        background_jobs: true,
        native_skill: true,
        verified: true,
        project_markers: &[".opencode", "opencode.json"],
        user_markers: &[".config/opencode"],
        config_path: ".opencode/opencode.json",
        skill_path: ".opencode/skills/syntavra",
        stream_capture: true,
        notes: &["mcp", "proxy", "stream-capture"],
    },
    HostSpec {
        host: "cline",
        display_name: "Cline",
        pre_hook: false,
        post_hook: false,
        result_replacement: true,
        mcp: true,
        proxy: false,
        session_events: true,
        usage_telemetry: false,
        background_jobs: true,
        native_skill: false,
        verified: true,
        project_markers: &[".cline", ".clinerules"],
        user_markers: &[".cline"],
        config_path: ".cline/mcp_settings.json",
        skill_path: ".clinerules/00-syntavra.md",
        stream_capture: false,
        notes: &["mcp", "rules"],
    },
    HostSpec {
        host: "roo-code",
        display_name: "Roo Code",
        pre_hook: false,
        post_hook: false,
        result_replacement: true,
        mcp: true,
        proxy: false,
        session_events: true,
        usage_telemetry: false,
        background_jobs: true,
        native_skill: false,
        verified: false,
        project_markers: &[".roo", ".roomodes"],
        user_markers: &[".roo"],
        config_path: ".roo/mcp.json",
        skill_path: "AGENTS.md",
        stream_capture: false,
        notes: &["mcp", "agents-instructions"],
    },
    HostSpec {
        host: "qwen-code",
        display_name: "Qwen Code",
        pre_hook: false,
        post_hook: false,
        result_replacement: true,
        mcp: true,
        proxy: false,
        session_events: true,
        usage_telemetry: true,
        background_jobs: true,
        native_skill: true,
        verified: false,
        project_markers: &[".qwen"],
        user_markers: &[".qwen"],
        config_path: ".qwen/mcp.json",
        skill_path: ".qwen/skills/syntavra",
        stream_capture: false,
        notes: &["mcp", "native-skill", "usage-telemetry"],
    },
    HostSpec {
        host: "kiro",
        display_name: "Kiro CLI",
        pre_hook: false,
        post_hook: false,
        result_replacement: true,
        mcp: true,
        proxy: false,
        session_events: true,
        usage_telemetry: false,
        background_jobs: true,
        native_skill: true,
        verified: false,
        project_markers: &[".kiro"],
        user_markers: &[".kiro"],
        config_path: ".kiro/settings/mcp.json",
        skill_path: ".kiro/skills/syntavra",
        stream_capture: false,
        notes: &["mcp", "native-skill", "steering"],
    },
    HostSpec {
        host: "zed",
        display_name: "Zed",
        pre_hook: false,
        post_hook: false,
        result_replacement: true,
        mcp: true,
        proxy: false,
        session_events: true,
        usage_telemetry: false,
        background_jobs: true,
        native_skill: false,
        verified: false,
        project_markers: &[".zed"],
        user_markers: &[".config/zed"],
        config_path: ".zed/settings.json",
        skill_path: "AGENTS.md",
        stream_capture: false,
        notes: &["mcp", "agents-instructions"],
    },
    HostSpec {
        host: "pi",
        display_name: "Pi Coding Agent",
        pre_hook: false,
        post_hook: false,
        result_replacement: false,
        mcp: false,
        proxy: false,
        session_events: false,
        usage_telemetry: false,
        background_jobs: false,
        native_skill: true,
        verified: false,
        project_markers: &[".pi"],
        user_markers: &[".pi/agent"],
        config_path: ".pi/settings.json",
        skill_path: ".pi/skills/syntavra",
        stream_capture: false,
        notes: &[
            "native-skill",
            "extension-capable",
            "instruction-only-adapter",
        ],
    },
    HostSpec {
        host: "omp",
        display_name: "Oh My Pi",
        pre_hook: false,
        post_hook: false,
        result_replacement: false,
        mcp: false,
        proxy: false,
        session_events: false,
        usage_telemetry: false,
        background_jobs: false,
        native_skill: true,
        verified: false,
        project_markers: &[".omp"],
        user_markers: &[".omp/agent"],
        config_path: ".omp/agent/config.yml",
        skill_path: ".omp/skills/syntavra",
        stream_capture: false,
        notes: &[
            "native-skill",
            "mcp-capable-host",
            "instruction-only-adapter",
        ],
    },
    HostSpec {
        host: "openclaw",
        display_name: "OpenClaw",
        pre_hook: false,
        post_hook: false,
        result_replacement: false,
        mcp: false,
        proxy: false,
        session_events: false,
        usage_telemetry: false,
        background_jobs: false,
        native_skill: true,
        verified: false,
        project_markers: &[".openclaw", "openclaw.json"],
        user_markers: &[".openclaw"],
        config_path: "openclaw.json",
        skill_path: "skills/syntavra",
        stream_capture: false,
        notes: &[
            "workspace-skill",
            "plugin-compatible",
            "instruction-only-adapter",
        ],
    },
    HostSpec {
        host: "aider",
        display_name: "Aider",
        pre_hook: false,
        post_hook: false,
        result_replacement: false,
        mcp: false,
        proxy: false,
        session_events: false,
        usage_telemetry: false,
        background_jobs: false,
        native_skill: false,
        verified: false,
        project_markers: &[".aider.conf.yml"],
        user_markers: &[],
        config_path: "",
        skill_path: "AGENTS.md",
        stream_capture: false,
        notes: &["instruction-only"],
    },
    HostSpec {
        host: "continue",
        display_name: "Continue",
        pre_hook: false,
        post_hook: false,
        result_replacement: true,
        mcp: true,
        proxy: false,
        session_events: true,
        usage_telemetry: false,
        background_jobs: true,
        native_skill: false,
        verified: true,
        project_markers: &[".continue"],
        user_markers: &[".continue"],
        config_path: ".continue/mcp.json",
        skill_path: ".continue/rules/00-syntavra.md",
        stream_capture: false,
        notes: &["mcp", "rules"],
    },
];

const MINIMAL_TOOLS: &[&str] = &[
    "syntavra.status",
    "syntavra.inspect.map",
    "syntavra.output.capture",
    "syntavra.output.search",
    "syntavra.output.reveal",
    "syntavra.session.semantic_context",
    "syntavra.fabric.route",
    "syntavra.fabric.doctor",
];

const BALANCED_TOOLS: &[&str] = &[
    "syntavra.status",
    "syntavra.inspect.map",
    "syntavra.output.capture",
    "syntavra.output.search",
    "syntavra.output.reveal",
    "syntavra.session.semantic_context",
    "syntavra.fabric.route",
    "syntavra.fabric.doctor",
    "syntavra.host.detect",
    "syntavra.inspect.impact",
    "syntavra.inspect.source",
    "syntavra.inspect.range",
    "syntavra.context.evaluate",
    "syntavra.output.verify",
    "syntavra.output.stats",
    "syntavra.session.open",
    "syntavra.session.append",
    "syntavra.session.search",
    "syntavra.session.context",
    "syntavra.session.compact",
    "syntavra.session.verify",
    "syntavra.sandbox.plan",
    "syntavra.sandbox.execute",
    "syntavra.process.submit",
    "syntavra.process.completions",
    "syntavra.fabric.profile",
    "syntavra.fabric.insights",
    "syntavra.provider.capabilities",
    "syntavra.provider.prepare",
    "syntavra.provider.capture",
    "syntavra.provider.replay",
    "syntavra.provider.verify",
    "syntavra.provider.stats",
    "syntavra.data.route",
    "syntavra.ecosystem.capabilities",
    "syntavra.context.pack",
];

#[derive(Clone)]
struct Change {
    path: String,
    kind: String,
    action: String,
    existed: bool,
    before_hash: String,
    after_hash: String,
    backup_path: String,
}

impl Change {
    fn value(&self) -> Value {
        json!({
            "path": self.path,
            "kind": self.kind,
            "action": self.action,
            "existed": self.existed,
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
            "backup_path": self.backup_path,
        })
    }
}

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root] if root == "install")
}

fn now() -> Result<f64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_secs_f64())
        .map_err(|_| "INSTALL_SYSTEM_TIME_INVALID".to_owned())
}

fn has_flag(arguments: &[String], flag: &str) -> bool {
    arguments.iter().any(|value| value == flag)
}

fn option_value(arguments: &[String], flag: &str) -> Result<Option<String>, String> {
    let prefix = format!("{flag}=");
    let mut result = None;
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

fn host_spec(host: &str) -> Option<&'static HostSpec> {
    HOSTS.iter().find(|item| item.host == host)
}

fn detected_hosts(project: &Path) -> Vec<String> {
    let mut values = HOSTS
        .iter()
        .filter(|spec| {
            spec.project_markers
                .iter()
                .any(|marker| project.join(marker).exists())
        })
        .map(|spec| spec.host.to_owned())
        .collect::<Vec<_>>();
    values.sort();
    values.dedup();
    values
}

fn skill_root(project: &Path) -> PathBuf {
    let repository = project.join("skills").join("syntavra");
    if repository.join("SKILL.md").is_file() {
        repository
    } else {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .and_then(Path::parent)
            .unwrap_or_else(|| Path::new("."))
            .join("syntavra_runtime")
            .join("bundled_skill")
    }
}

fn profile(profile: &str) -> Result<Value, String> {
    let (tools, max_active, budget, timeout) = match profile {
        "minimal" => (Value::from(MINIMAL_TOOLS), MINIMAL_TOOLS.len(), 800, 120),
        "balanced" => (
            Value::from(BALANCED_TOOLS),
            BALANCED_TOOLS.len(),
            2_000,
            180,
        ),
        "audit" => (json!(["*"]), 128, 16_000, 300),
        _ => return Err(format!("unknown MCP profile: {profile}")),
    };
    Ok(json!({
        "name": profile,
        "exposed_tools": tools,
        "max_active_tools": max_active,
        "tool_description_budget_tokens": budget,
        "default_timeout_seconds": timeout,
        "require_routing_receipt": true,
        "require_exact_evidence": true,
        "allow_unknown_tools": false,
    }))
}

fn capabilities(spec: &HostSpec) -> Value {
    json!({
        "host": spec.host,
        "display_name": spec.display_name,
        "supports_pre_tool_hook": spec.pre_hook,
        "supports_post_tool_hook": spec.post_hook,
        "supports_result_replacement": spec.result_replacement,
        "supports_mcp": spec.mcp,
        "supports_proxy": spec.proxy,
        "supports_session_events": spec.session_events,
        "supports_usage_telemetry": spec.usage_telemetry,
        "supports_background_jobs": spec.background_jobs,
        "supports_native_skill": spec.native_skill,
        "verified": spec.verified,
        "project_markers": spec.project_markers,
        "user_markers": spec.user_markers,
        "config_path": spec.config_path,
        "skill_path": spec.skill_path,
        "supports_stream_capture": spec.stream_capture,
        "integration_notes": spec.notes,
    })
}

fn negotiation(spec: &HostSpec, project: &Path, installed: bool) -> Result<Value, String> {
    let mut arguments = vec![
        "host".to_owned(),
        "negotiate".to_owned(),
        "--host-name".to_owned(),
        spec.host.to_owned(),
    ];
    if installed {
        arguments.push("--installed".to_owned());
    }
    super::native_host::execute(
        &["host".to_owned(), "negotiate".to_owned()],
        &arguments,
        project,
    )
}

fn overlay(spec: &HostSpec) -> Value {
    let mut root = Map::new();
    root.insert(
        "mcpServers".to_owned(),
        json!({"syntavra": {"command": "syntavra", "args": ["mcp"]}}),
    );
    if spec.host == "claude-code" {
        root.insert(
            "statusLine".to_owned(),
            json!({"type": "command", "command": "syntavra run statusline"}),
        );
    }
    if spec.pre_hook || spec.post_hook {
        root.insert(
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
    Value::Object(root)
}

fn recursive_merge(base: &Value, overlay: &Value) -> Value {
    match (base, overlay) {
        (Value::Object(base), Value::Object(overlay)) => {
            let mut result = base.clone();
            for (key, value) in overlay {
                let merged = result.get(key).map_or_else(
                    || value.clone(),
                    |existing| recursive_merge(existing, value),
                );
                result.insert(key.clone(), merged);
            }
            Value::Object(result)
        }
        (_, value) => value.clone(),
    }
}

fn platform_plan(spec: &HostSpec, project: &Path) -> Result<Value, String> {
    let negotiation = negotiation(spec, project, false)?;
    let mut files = Vec::<Value>::new();
    if !spec.config_path.is_empty() {
        files.push(json!({"path": spec.config_path, "merge": overlay(spec)}));
    }
    if !spec.skill_path.is_empty() {
        let path = if spec.skill_path.ends_with(".md") {
            spec.skill_path.to_owned()
        } else {
            format!("{}/SKILL.md", spec.skill_path.trim_end_matches('/'))
        };
        files.push(json!({"path": path, "source": "bundled syntavra skill"}));
    }
    Ok(json!({
        "host": spec.host,
        "display_name": spec.display_name,
        "scope": "project",
        "project": project.to_string_lossy(),
        "mode": negotiation["mode"],
        "enforced": negotiation["enforced"],
        "verified_adapter": spec.verified,
        "files": files,
        "capabilities": capabilities(spec),
        "validation": [
            "syntavra doctor",
            format!("syntavra host negotiate --host-name {}", spec.host),
            "syntavra status",
        ],
    }))
}

fn action(action: &str, target: &str, path: PathBuf, reversible: bool, reason: &str) -> Value {
    json!({
        "action": action,
        "target": target,
        "path": path.to_string_lossy(),
        "reversible": reversible,
        "reason": reason,
    })
}

fn install_plan(
    project: &Path,
    state: &Path,
    all_hosts: bool,
    profile_name: &str,
) -> Result<Value, String> {
    profile(profile_name)?;
    let detected = detected_hosts(project);
    let targets = if all_hosts {
        let mut values = HOSTS
            .iter()
            .map(|spec| spec.host.to_owned())
            .collect::<Vec<_>>();
        values.sort();
        values
    } else {
        detected.clone()
    };
    let mut actions = vec![
        action(
            "backup",
            "existing-config",
            state.join("host-installations"),
            true,
            "per-host backup-first transaction",
        ),
        action(
            "write",
            "runtime-config",
            state.join("config.json"),
            true,
            "canonical pre-release config",
        ),
        action(
            "write",
            "product-surface",
            state.join("product.json"),
            true,
            "four-command mental model",
        ),
        action(
            "write",
            &format!("mcp-profile:{profile_name}"),
            state.join("mcp-profile.json"),
            true,
            "bounded tool visibility",
        ),
        action(
            "write",
            "platform-adapters",
            state.join("platform-adapters.json"),
            true,
            "concrete host config candidates",
        ),
        action(
            "install",
            "local-proxy",
            state.join("proxy"),
            true,
            "credential-isolated provider gateway",
        ),
    ];
    for host in &targets {
        actions.push(action(
            "configure-and-verify",
            host,
            project.to_path_buf(),
            true,
            "atomic native hook/MCP/skill integration",
        ));
    }
    actions.push(action(
        "verify",
        "doctor",
        project.to_path_buf(),
        false,
        "post-install verification",
    ));
    actions.push(action(
        "record",
        "installation-receipt",
        state.join("install-receipt.json"),
        false,
        "measured onboarding and rollback evidence",
    ));
    let estimated = (5.0 + actions.len() as f64 * 1.5).min(59.0);
    Ok(json!({
        "version": VERSION,
        "channel": CHANNEL,
        "project_root": project.to_string_lossy(),
        "actions": actions,
        "detected_hosts": detected,
        "installable_hosts": targets,
        "contract_only_hosts": [],
        "estimated_seconds": estimated,
        "one_command": true,
        "mental_model": ["setup", "status", "run", "prove"],
    }))
}

fn canonical_bytes(value: &Value) -> Result<Vec<u8>, String> {
    let mut bytes = serde_json::to_string_pretty(value)
        .map_err(|error| format!("INSTALL_JSON_SERIALIZE_FAILED:{error}"))?
        .into_bytes();
    bytes.push(b'\n');
    Ok(bytes)
}

fn atomic_write(path: &Path, bytes: &[u8]) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("INSTALL_DIRECTORY_CREATE_FAILED:{error}"))?;
    }
    let temporary = path.with_file_name(format!(
        ".{}.{}",
        path.file_name().unwrap_or_default().to_string_lossy(),
        std::process::id()
    ));
    let mut file = fs::File::create(&temporary)
        .map_err(|error| format!("INSTALL_TEMP_CREATE_FAILED:{error}"))?;
    file.write_all(bytes)
        .map_err(|error| format!("INSTALL_TEMP_WRITE_FAILED:{error}"))?;
    file.sync_all()
        .map_err(|error| format!("INSTALL_TEMP_SYNC_FAILED:{error}"))?;
    fs::rename(&temporary, path)
        .map_err(|error| format!("INSTALL_ATOMIC_RENAME_FAILED:{error}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o600))
            .map_err(|error| format!("INSTALL_PERMISSIONS_FAILED:{error}"))?;
    }
    Ok(())
}

fn digest(path: &Path) -> Result<String, String> {
    if !path.exists() {
        return Ok(String::new());
    }
    if path.is_file() {
        return fs::read(path)
            .map(|bytes| sha256_hex(&bytes))
            .map_err(|error| format!("INSTALL_DIGEST_READ_FAILED:{error}"));
    }
    let mut rows = Vec::<Value>::new();
    fn visit(root: &Path, path: &Path, rows: &mut Vec<Value>) -> Result<(), String> {
        let mut entries = fs::read_dir(path)
            .map_err(|error| format!("INSTALL_DIGEST_DIRECTORY_FAILED:{error}"))?
            .filter_map(Result::ok)
            .collect::<Vec<_>>();
        entries.sort_by_key(|entry| entry.path());
        for entry in entries {
            let candidate = entry.path();
            if candidate.is_dir() {
                visit(root, &candidate, rows)?;
            } else if candidate.is_file() {
                let relative = candidate
                    .strip_prefix(root)
                    .map_err(|_| "INSTALL_DIGEST_RELATIVE_FAILED".to_owned())?
                    .to_string_lossy()
                    .replace('\\', "/");
                let bytes = fs::read(&candidate)
                    .map_err(|error| format!("INSTALL_DIGEST_READ_FAILED:{error}"))?;
                rows.push(json!([relative, sha256_hex(&bytes)]));
            }
        }
        Ok(())
    }
    visit(path, path, &mut rows)?;
    let bytes = serde_json::to_vec(&rows)
        .map_err(|error| format!("INSTALL_DIGEST_SERIALIZE_FAILED:{error}"))?;
    Ok(sha256_hex(&bytes))
}

fn copy_tree(source: &Path, target: &Path) -> Result<(), String> {
    if target.exists() {
        fs::remove_dir_all(target)
            .map_err(|error| format!("INSTALL_TARGET_REMOVE_FAILED:{error}"))?;
    }
    fs::create_dir_all(target).map_err(|error| format!("INSTALL_TARGET_CREATE_FAILED:{error}"))?;
    let mut entries = fs::read_dir(source)
        .map_err(|error| format!("INSTALL_SOURCE_READ_FAILED:{error}"))?
        .filter_map(Result::ok)
        .collect::<Vec<_>>();
    entries.sort_by_key(|entry| entry.path());
    for entry in entries {
        let source_path = entry.path();
        let target_path = target.join(entry.file_name());
        if source_path.is_dir() {
            copy_tree(&source_path, &target_path)?;
        } else if source_path.is_file() {
            if let Some(parent) = target_path.parent() {
                fs::create_dir_all(parent)
                    .map_err(|error| format!("INSTALL_TARGET_CREATE_FAILED:{error}"))?;
            }
            fs::copy(&source_path, &target_path)
                .map_err(|error| format!("INSTALL_COPY_FAILED:{error}"))?;
        }
    }
    Ok(())
}

fn managed_text(existing: &str, block: &str) -> String {
    let managed = format!("{TEXT_BEGIN}\n{}\n{TEXT_END}", block.trim_end());
    if let (Some(start), Some(end)) = (existing.find(TEXT_BEGIN), existing.find(TEXT_END)) {
        let prefix = existing[..start].trim_end();
        let suffix = &existing[end + TEXT_END.len()..];
        return format!("{prefix}\n\n{managed}{suffix}");
    }
    if existing.trim().is_empty() {
        format!("{managed}\n")
    } else {
        format!("{}\n\n{managed}\n", existing.trim_end())
    }
}

fn initialize_database(path: &Path) -> Result<Connection, String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("INSTALL_DATABASE_DIRECTORY_FAILED:{error}"))?;
    }
    let connection =
        Connection::open(path).map_err(|error| format!("INSTALL_DATABASE_OPEN_FAILED:{error}"))?;
    connection
        .busy_timeout(Duration::from_secs(30))
        .map_err(|error| format!("INSTALL_DATABASE_TIMEOUT_FAILED:{error}"))?;
    connection.execute_batch(
        "PRAGMA journal_mode=WAL;\
         PRAGMA foreign_keys=ON;\
         PRAGMA synchronous=NORMAL;\
         CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);\
         CREATE TABLE IF NOT EXISTS host_install_transactions(\
           transaction_id TEXT PRIMARY KEY,host TEXT NOT NULL,scope TEXT NOT NULL,root TEXT NOT NULL,\
           status TEXT NOT NULL,manifest_json TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL);\
         CREATE INDEX IF NOT EXISTS host_install_host_idx ON host_install_transactions(host,scope,created_at);"
    ).map_err(|error| format!("INSTALL_DATABASE_INITIALIZE_FAILED:{error}"))?;
    Ok(connection)
}

fn transaction_id(host: &str) -> Result<String, String> {
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "INSTALL_SYSTEM_TIME_INVALID".to_owned())?;
    let seed = format!("{host}:{}:{}", duration.as_nanos(), std::process::id());
    Ok(format!(
        "host-{}-{}",
        duration.as_secs(),
        &sha256_hex(seed.as_bytes())[..12]
    ))
}

fn backup(source: &Path, destination: &Path) -> Result<String, String> {
    if !source.exists() {
        return Ok(String::new());
    }
    if source.is_dir() {
        copy_tree(source, destination)?;
    } else {
        if let Some(parent) = destination.parent() {
            fs::create_dir_all(parent)
                .map_err(|error| format!("INSTALL_BACKUP_DIRECTORY_FAILED:{error}"))?;
        }
        fs::copy(source, destination)
            .map_err(|error| format!("INSTALL_BACKUP_COPY_FAILED:{error}"))?;
    }
    Ok(destination.to_string_lossy().into_owned())
}

fn verify_host(spec: &HostSpec, project: &Path) -> Result<Value, String> {
    let mut reasons = Vec::<Value>::new();
    let mut details = Map::new();
    if !spec.config_path.is_empty() {
        let target = project.join(spec.config_path);
        if !target.is_file() {
            reasons.push(Value::from("missing-config"));
        } else {
            match fs::read_to_string(&target)
                .ok()
                .and_then(|text| serde_json::from_str::<Value>(&text).ok())
            {
                None => reasons.push(Value::from("invalid-config-json")),
                Some(value) => {
                    if value
                        .get("mcpServers")
                        .and_then(|row| row.get("syntavra"))
                        .and_then(|row| row.get("command"))
                        .and_then(Value::as_str)
                        != Some("syntavra")
                    {
                        reasons.push(Value::from("missing-syntavra-mcp"));
                    }
                    if (spec.pre_hook || spec.post_hook)
                        && value.get("hooks").and_then(Value::as_object).is_none()
                    {
                        reasons.push(Value::from("missing-hooks"));
                    }
                }
            }
        }
        details.insert(
            "config".to_owned(),
            json!({"path": spec.config_path, "hash": digest(&target)?}),
        );
    }
    if !spec.skill_path.is_empty() {
        let target = project.join(spec.skill_path);
        if !target.exists() {
            reasons.push(Value::from("missing-skill"));
        } else if target.is_file() {
            let text = fs::read_to_string(&target).unwrap_or_default();
            if !text.contains(TEXT_BEGIN) || !text.contains(TEXT_END) {
                reasons.push(Value::from("unmanaged-skill-file"));
            }
        } else if !target.join("SKILL.md").is_file() {
            reasons.push(Value::from("missing-skill-entrypoint"));
        }
        details.insert(
            "skill".to_owned(),
            json!({"path": spec.skill_path, "hash": digest(&target)?}),
        );
    }
    let ok = reasons.is_empty();
    let negotiation = negotiation(spec, project, ok)?;
    Ok(json!({
        "ok": ok,
        "host": spec.host,
        "scope": "project",
        "root": project.to_string_lossy(),
        "mode": negotiation["mode"],
        "reasons": reasons,
        "details": details,
    }))
}

fn apply_host(
    spec: &HostSpec,
    project: &Path,
    state: &Path,
    source_skill: &Path,
    dry_run: bool,
) -> Result<Value, String> {
    let id = transaction_id(spec.host)?;
    let created_at = now()?;
    let plan = platform_plan(spec, project)?;
    let mut staged = Vec::<(
        PathBuf,
        String,
        Option<Vec<u8>>,
        Option<PathBuf>,
        bool,
        String,
        String,
    )>::new();

    if !spec.config_path.is_empty() {
        let target = project.join(spec.config_path);
        if target.exists() && !target.is_file() {
            return Err(format!("IsADirectoryError: {}", target.to_string_lossy()));
        }
        let existing = if target.is_file() {
            let text = fs::read_to_string(&target).map_err(|error| {
                format!(
                    "ValueError: host config is not valid JSON: {}: {error}",
                    target.to_string_lossy()
                )
            })?;
            let value: Value = serde_json::from_str(&text).map_err(|error| {
                format!(
                    "ValueError: host config is not valid JSON: {}: {error}",
                    target.to_string_lossy()
                )
            })?;
            if !value.is_object() {
                return Err(format!(
                    "TypeError: host config root must be an object: {}",
                    target.to_string_lossy()
                ));
            }
            value
        } else {
            json!({})
        };
        let payload = canonical_bytes(&recursive_merge(&existing, &overlay(spec)))?;
        staged.push((
            target.clone(),
            "json-config".to_owned(),
            Some(payload),
            None,
            target.exists(),
            digest(&target)?,
            spec.config_path.to_owned(),
        ));
    }

    if !spec.skill_path.is_empty() {
        let target = project.join(spec.skill_path);
        let text_target = target
            .extension()
            .and_then(|value| value.to_str())
            .is_some_and(|value| {
                matches!(value.to_ascii_lowercase().as_str(), "md" | "mdc" | "txt")
            })
            || target.file_name().and_then(|value| value.to_str()) == Some("AGENTS.md");
        if text_target {
            let existing = if target.is_file() {
                fs::read_to_string(&target).unwrap_or_default()
            } else {
                String::new()
            };
            let source = fs::read_to_string(source_skill.join("SKILL.md")).map_err(|error| {
                format!("FileNotFoundError: Syntavra bundled skill is unavailable: {error}")
            })?;
            staged.push((
                target.clone(),
                "managed-text".to_owned(),
                Some(managed_text(&existing, &source).into_bytes()),
                None,
                target.exists(),
                digest(&target)?,
                spec.skill_path.to_owned(),
            ));
        } else {
            staged.push((
                target.clone(),
                "skill-directory".to_owned(),
                None,
                Some(source_skill.to_path_buf()),
                target.exists(),
                digest(&target)?,
                spec.skill_path.to_owned(),
            ));
        }
    }

    let mut changes = Vec::<Change>::new();
    if dry_run {
        for (_, kind, bytes, source, existed, before_hash, relative) in staged {
            let after_hash = if let Some(bytes) = bytes {
                sha256_hex(&bytes)
            } else {
                digest(
                    source
                        .as_deref()
                        .ok_or_else(|| "INSTALL_SKILL_SOURCE_MISSING".to_owned())?,
                )?
            };
            changes.push(Change {
                path: relative,
                kind,
                action: if existed {
                    "would-update"
                } else {
                    "would-create"
                }
                .to_owned(),
                existed,
                before_hash,
                after_hash,
                backup_path: String::new(),
            });
        }
        return Ok(json!({
            "transaction_id": id,
            "host": spec.host,
            "scope": "project",
            "root": project.to_string_lossy(),
            "status": "dry-run",
            "changes": changes.iter().map(Change::value).collect::<Vec<_>>(),
            "verification": {"ok": true, "dry_run": true, "plan": plan},
            "created_at": created_at,
        }));
    }

    let transaction = state.join("host-installations").join(&id);
    fs::create_dir_all(&transaction)
        .map_err(|error| format!("INSTALL_TRANSACTION_CREATE_FAILED:{error}"))?;
    let mut applied = Vec::<(PathBuf, bool, String)>::new();
    for (target, kind, bytes, source, existed, before_hash, relative) in staged {
        let backup_path = backup(&target, &transaction.join("backup").join(&relative))?;
        let result = if let Some(bytes) = bytes {
            atomic_write(&target, &bytes)
        } else {
            copy_tree(
                source
                    .as_deref()
                    .ok_or_else(|| "INSTALL_SKILL_SOURCE_MISSING".to_owned())?,
                &target,
            )
        };
        if let Err(error) = result {
            for (path, previously_existed, saved) in applied.into_iter().rev() {
                if path.is_dir() {
                    let _ = fs::remove_dir_all(&path);
                } else {
                    let _ = fs::remove_file(&path);
                }
                if previously_existed && !saved.is_empty() {
                    let backup_path = Path::new(&saved);
                    if backup_path.is_dir() {
                        let _ = copy_tree(backup_path, &path);
                    } else {
                        let _ = fs::copy(backup_path, &path);
                    }
                }
            }
            let _ = fs::remove_dir_all(&transaction);
            return Err(error);
        }
        let after_hash = digest(&target)?;
        changes.push(Change {
            path: relative,
            kind,
            action: if existed { "updated" } else { "created" }.to_owned(),
            existed,
            before_hash,
            after_hash,
            backup_path: backup_path.clone(),
        });
        applied.push((target, existed, backup_path));
    }
    let verification = verify_host(spec, project)?;
    if verification["ok"].as_bool() != Some(true) {
        return Err(format!(
            "RuntimeError: installation verification failed: {}",
            verification["reasons"]
        ));
    }
    let result = json!({
        "transaction_id": id,
        "host": spec.host,
        "scope": "project",
        "root": project.to_string_lossy(),
        "status": "applied",
        "changes": changes.iter().map(Change::value).collect::<Vec<_>>(),
        "verification": verification,
        "created_at": created_at,
    });
    atomic_write(
        &transaction.join("manifest.json"),
        &canonical_bytes(&result)?,
    )?;
    let connection = initialize_database(&state.join("host-installations.sqlite3"))?;
    connection.execute(
        "INSERT INTO host_install_transactions(transaction_id,host,scope,root,status,manifest_json,created_at,updated_at) VALUES(?1,?2,'project',?3,'applied',?4,?5,?6)",
        params![id, spec.host, project.to_string_lossy(), serde_json::to_string(&result).map_err(|error| format!("INSTALL_JSON_SERIALIZE_FAILED:{error}"))?, created_at, now()?],
    ).map_err(|error| format!("INSTALL_TRANSACTION_RECORD_FAILED:{error}"))?;
    Ok(result)
}

fn rollback_transaction(state: &Path, transaction_id: &str) -> Result<(), String> {
    let connection = initialize_database(&state.join("host-installations.sqlite3"))?;
    let manifest: String = connection
        .query_row(
            "SELECT manifest_json FROM host_install_transactions WHERE transaction_id=?1",
            [transaction_id],
            |row| row.get(0),
        )
        .map_err(|error| format!("INSTALL_ROLLBACK_LOOKUP_FAILED:{error}"))?;
    let value: Value = serde_json::from_str(&manifest)
        .map_err(|error| format!("INSTALL_ROLLBACK_MANIFEST_INVALID:{error}"))?;
    for change in value["changes"].as_array().into_iter().flatten().rev() {
        let target = Path::new(value["root"].as_str().unwrap_or_default())
            .join(change["path"].as_str().unwrap_or_default());
        if target.is_dir() {
            let _ = fs::remove_dir_all(&target);
        } else {
            let _ = fs::remove_file(&target);
        }
        if change["existed"].as_bool() == Some(true) {
            let saved = Path::new(change["backup_path"].as_str().unwrap_or_default());
            if saved.is_dir() {
                copy_tree(saved, &target)?;
            } else if saved.is_file() {
                if let Some(parent) = target.parent() {
                    fs::create_dir_all(parent)
                        .map_err(|error| format!("INSTALL_ROLLBACK_DIRECTORY_FAILED:{error}"))?;
                }
                fs::copy(saved, target)
                    .map_err(|error| format!("INSTALL_ROLLBACK_COPY_FAILED:{error}"))?;
            }
        }
    }
    connection.execute(
        "UPDATE host_install_transactions SET status='rolled-back',updated_at=?1 WHERE transaction_id=?2",
        params![now()?, transaction_id],
    ).map_err(|error| format!("INSTALL_ROLLBACK_RECORD_FAILED:{error}"))?;
    Ok(())
}

fn platform_adapter_records() -> Vec<Value> {
    let rows = [
        (
            "claude-code",
            vec!["claude"],
            vec!["~/.claude/settings.json", ".claude/settings.json"],
            "plugin+hooks",
            true,
            true,
            true,
            "primary-certification-target",
        ),
        (
            "codex",
            vec!["codex"],
            vec!["~/.codex/config.toml", "AGENTS.md"],
            "skill+mcp",
            true,
            false,
            true,
            "primary-certification-target",
        ),
        (
            "gemini-cli",
            vec!["gemini"],
            vec!["~/.gemini/settings.json", "GEMINI.md"],
            "extension+mcp",
            true,
            false,
            true,
            "contract-tested",
        ),
        (
            "vscode-copilot",
            vec![],
            vec![".vscode/mcp.json"],
            "instructions+mcp",
            true,
            false,
            false,
            "host-specific-marker-contract-tested",
        ),
        (
            "jetbrains-copilot",
            vec![],
            vec![".idea/mcp.json"],
            "instructions+mcp",
            true,
            false,
            false,
            "host-specific-marker-contract-tested",
        ),
        (
            "cursor",
            vec!["cursor"],
            vec![".cursor/rules/syntavra.mdc", ".cursor/mcp.json"],
            "rules+mcp",
            true,
            false,
            true,
            "primary-certification-target",
        ),
        (
            "windsurf",
            vec!["windsurf"],
            vec![".windsurfrules", ".codeium/windsurf/mcp_config.json"],
            "rules+mcp",
            true,
            false,
            true,
            "contract-tested",
        ),
        (
            "opencode",
            vec!["opencode"],
            vec!["opencode.json", "~/.config/opencode/opencode.json"],
            "config+mcp",
            true,
            true,
            true,
            "contract-tested",
        ),
        (
            "cline",
            vec![],
            vec![".clinerules", ".vscode/mcp.json"],
            "rules+mcp",
            true,
            false,
            true,
            "contract-tested",
        ),
        (
            "roo-code",
            vec![],
            vec![".roo/rules/syntavra.md", ".vscode/mcp.json"],
            "rules+mcp",
            true,
            false,
            true,
            "contract-tested",
        ),
        (
            "qwen-code",
            vec!["qwen", "qwen-code"],
            vec!["QWEN.md", "~/.qwen/settings.json"],
            "agents+mcp",
            true,
            false,
            true,
            "contract-tested",
        ),
        (
            "kiro",
            vec!["kiro", "kiro-cli", "q"],
            vec![".kiro/settings/mcp.json", ".kiro/skills/syntavra/SKILL.md"],
            "mcp+native-skill",
            true,
            true,
            true,
            "official-path-contract-tested",
        ),
        (
            "zed",
            vec!["zed"],
            vec![".zed/settings.json", "~/.config/zed/settings.json"],
            "rules+mcp",
            true,
            false,
            false,
            "contract-tested",
        ),
        (
            "pi",
            vec!["pi"],
            vec![".pi/settings.json", ".pi/skills/syntavra/SKILL.md"],
            "native-skill+extension-capable",
            false,
            true,
            true,
            "official-skill-path-contract-tested",
        ),
        (
            "omp",
            vec!["omp"],
            vec![".omp/agent/config.yml", ".omp/skills/syntavra/SKILL.md"],
            "native-skill+mcp-capable-host",
            false,
            true,
            true,
            "official-skill-path-contract-tested",
        ),
        (
            "openclaw",
            vec!["openclaw"],
            vec![
                "skills/syntavra/SKILL.md",
                ".openclaw/skills/syntavra/SKILL.md",
            ],
            "workspace-skill+plugin-compatible",
            false,
            true,
            true,
            "official-skill-path-contract-tested",
        ),
        (
            "aider",
            vec!["aider"],
            vec![".aider.conf.yml", "~/.aider.conf.yml"],
            "env+wrapper",
            false,
            false,
            true,
            "contract-tested",
        ),
        (
            "continue",
            vec!["continue"],
            vec![".continue/config.yaml", "~/.continue/config.yaml"],
            "rules+mcp",
            true,
            false,
            true,
            "contract-tested",
        ),
    ];
    rows.into_iter()
        .map(
            |(host, commands, configs, mode, mcp, hooks, continuity, maturity)| {
                json!({
                    "host": host,
                    "detection_commands": commands,
                    "config_candidates": configs,
                    "integration_mode": mode,
                    "supports_mcp": mcp,
                    "supports_hooks": hooks,
                    "supports_session_continuity": continuity,
                    "maturity": maturity,
                })
            },
        )
        .collect()
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
}

fn integration_validation() -> Value {
    json!({
        "ok": true,
        "reasons": [],
        "providers": 10,
        "frameworks": 15,
        "hosts": 18,
        "automatic_hosts": 18,
        "live_certification_boundary": "external receipts are required before VERIFIED_LIVE",
    })
}

fn product_manifest(profile_name: &str) -> Result<Value, String> {
    Ok(json!({
        "version": VERSION,
        "channel": CHANNEL,
        "role": "token-and-context-optimization-skill",
        "not_a_replacement_agent": true,
        "optimization_surfaces": ["repository-context", "tool-output", "mcp-schema", "session-memory", "provider-cache"],
        "measurement_levels": ["PROVIDER_OBSERVED", "LOCALLY_TOKENIZED", "ESTIMATED", "UNKNOWN"],
        "mental_model": [
            {"command": "setup", "purpose": "install or repair integrations", "output": "reversible install receipt"},
            {"command": "status", "purpose": "show health, savings and continuity", "output": "one product health snapshot"},
            {"command": "run", "purpose": "enforce routing and execute through Syntavra", "output": "auditable execution plan"},
            {"command": "prove", "purpose": "validate measured external evidence", "output": "fail-closed proof decision"},
        ],
        "default_mcp_profile": profile(profile_name)?,
        "platform_adapters": adapter_validation(),
        "integration_matrix": integration_validation(),
        "proxy": {
            "surface": "OpenAI-compatible local control plane plus Python and TypeScript clients",
            "credential_policy": "transport-only",
            "stream_policy": "commit-before-forward",
            "usage_policy": "provider receipt required",
            "status": "pre-release",
        },
        "proof": {
            "workloads": ["coding-agent", "repository-task", "swe-bench", "oolong-long-context", "session-continuity", "tool-routing"],
            "measured_fields": [
                "provider fresh/cached/output/reasoning tokens", "provider cost", "wall time",
                "quality", "success", "source-level token attribution"
            ],
            "primary_metric": "provider-observed cost per verified successful task",
            "external_claim": "fail-closed",
        },
    }))
}

fn write_bundle(project: &Path, state: &Path, profile_name: &str) -> Result<Value, String> {
    let product = state.join("product.json");
    let mcp = state.join("mcp-profile.json");
    let adapters = state.join("platform-adapters.json");
    atomic_write(
        &product,
        &canonical_bytes(&product_manifest(profile_name)?)?,
    )?;
    atomic_write(&mcp, &canonical_bytes(&profile(profile_name)?)?)?;
    atomic_write(
        &adapters,
        &canonical_bytes(&json!({"adapters": platform_adapter_records()}))?,
    )?;
    Ok(json!({
        "ok": true,
        "project_root": project.to_string_lossy(),
        "profile": profile_name,
        "files": [product.to_string_lossy(), mcp.to_string_lossy(), adapters.to_string_lossy()],
    }))
}

pub(super) fn repair_bundle(
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

pub fn execute(
    arguments: &[String],
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
    let profile_name =
        option_value(arguments, "--mcp-profile")?.unwrap_or_else(|| "minimal".to_owned());
    profile(&profile_name)?;
    let all_hosts = has_flag(arguments, "--all");
    let dry_run = has_flag(arguments, "--dry-run") || !has_flag(arguments, "--apply");
    let plan = install_plan(&project, &state, all_hosts, &profile_name)?;
    let started_at = now()?;
    let monotonic = std::time::Instant::now();
    let targets = plan["installable_hosts"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .collect::<Vec<_>>();
    let source_skill = skill_root(&project);
    if !source_skill.join("SKILL.md").is_file() {
        return Ok(json!({
            "ok": false,
            "dry_run": dry_run,
            "profile": profile_name,
            "plan": plan,
            "host_results": [],
            "error": "FileNotFoundError: Syntavra bundled skill is unavailable",
            "rolled_back_transactions": [],
            "wall_time_ms": monotonic.elapsed().as_secs_f64() * 1000.0,
        }));
    }
    let mut host_results = Vec::<Value>::new();
    let mut transactions = Vec::<String>::new();
    for host in targets {
        let spec = host_spec(host).ok_or_else(|| format!("unsupported concrete host: {host}"))?;
        match apply_host(spec, &project, &state, &source_skill, dry_run) {
            Ok(value) => {
                if !dry_run {
                    transactions.push(
                        value["transaction_id"]
                            .as_str()
                            .unwrap_or_default()
                            .to_owned(),
                    );
                }
                host_results.push(value);
            }
            Err(error) => {
                for transaction in transactions.iter().rev() {
                    let _ = rollback_transaction(&state, transaction);
                }
                return Ok(json!({
                    "ok": false,
                    "dry_run": dry_run,
                    "profile": profile_name,
                    "plan": plan,
                    "host_results": host_results,
                    "error": error,
                    "rolled_back_transactions": transactions,
                    "wall_time_ms": monotonic.elapsed().as_secs_f64() * 1000.0,
                }));
            }
        }
    }

    let setup_bundle = if dry_run {
        Value::Null
    } else {
        let config = json!({
            "version": VERSION,
            "channel": CHANNEL,
            "project_root": project.to_string_lossy(),
            "hosts": plan["installable_hosts"],
            "host_transactions": transactions,
            "mcp_profile": profile_name,
            "product_commands": ["setup", "status", "run", "prove"],
            "installed_at": started_at,
        });
        atomic_write(&state.join("config.json"), &canonical_bytes(&config)?)?;
        let bundle = write_bundle(&project, &state, &profile_name)?;
        let elapsed = monotonic.elapsed().as_secs_f64() * 1000.0;
        let receipt = json!({
            "plan": plan,
            "applied": true,
            "profile": profile_name,
            "started_at": started_at,
            "completed_at": now()?,
            "wall_time_ms": elapsed,
            "setup_bundle": bundle,
            "host_results": host_results,
            "host_transactions": transactions,
            "onboarding_claim": "MEASURED_LOCAL_INSTALL_AND_HOST_VERIFICATION",
        });
        atomic_write(
            &state.join("install-receipt.json"),
            &canonical_bytes(&receipt)?,
        )?;
        bundle
    };

    Ok(json!({
        "ok": true,
        "dry_run": dry_run,
        "profile": profile_name,
        "plan": plan,
        "setup_bundle": setup_bundle,
        "host_results": host_results,
        "host_transactions": transactions,
        "wall_time_ms": monotonic.elapsed().as_secs_f64() * 1000.0,
    }))
}

#[cfg(test)]
mod tests {
    use super::{detected_hosts, profile, supports};
    use std::fs;

    #[test]
    fn routes_install_and_validates_profiles() {
        assert!(supports(&["install".to_owned()]));
        assert_eq!(profile("minimal").expect("minimal")["name"], "minimal");
        assert!(profile("unknown").is_err());
    }

    #[test]
    fn detects_only_project_markers() {
        let root =
            std::env::temp_dir().join(format!("syntavra-install-detect-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join(".codex")).expect("marker");
        assert_eq!(detected_hosts(&root), vec!["codex"]);
        let _ = fs::remove_dir_all(root);
    }
}
