#![forbid(unsafe_code)]
#![allow(clippy::too_many_lines)]

use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use serde_json::{json, Map, Value};
use sha2::{Digest as _, Sha256};

#[path = "native_remaining71_provider_proxy.rs"]
mod native_remaining71_provider_proxy;

const VERSION: &str = "0.0.1";
const CHANNEL: &str = "pre-release";

#[derive(Clone, Copy)]
struct GatewaySpec {
    credential_env: &'static str,
    header: &'static str,
    prefix: &'static str,
    protocol: &'static str,
}

#[derive(Clone, Copy)]
struct ProxyPreset {
    provider: &'static str,
    gateway_provider: &'static str,
    protocol: &'static str,
    default_upstream: &'static str,
    credential_env: &'static str,
    credential_header: &'static str,
    credential_prefix: &'static str,
    auth_strategy: &'static str,
    install_mode: &'static str,
    zero_code_compatible: bool,
}

const PRESETS: &[ProxyPreset] = &[
    ProxyPreset {
        provider: "openai",
        gateway_provider: "openai",
        protocol: "responses+chat",
        default_upstream: "https://api.openai.com",
        credential_env: "OPENAI_API_KEY",
        credential_header: "Authorization",
        credential_prefix: "Bearer ",
        auth_strategy: "api-key",
        install_mode: "native-proxy",
        zero_code_compatible: true,
    },
    ProxyPreset {
        provider: "anthropic",
        gateway_provider: "anthropic",
        protocol: "messages",
        default_upstream: "https://api.anthropic.com",
        credential_env: "ANTHROPIC_API_KEY",
        credential_header: "x-api-key",
        credential_prefix: "",
        auth_strategy: "api-key",
        install_mode: "native-proxy",
        zero_code_compatible: true,
    },
    ProxyPreset {
        provider: "gemini",
        gateway_provider: "gemini",
        protocol: "generate-content",
        default_upstream: "https://generativelanguage.googleapis.com",
        credential_env: "GEMINI_API_KEY",
        credential_header: "x-goog-api-key",
        credential_prefix: "",
        auth_strategy: "api-key",
        install_mode: "native-proxy",
        zero_code_compatible: true,
    },
    ProxyPreset {
        provider: "azure-openai",
        gateway_provider: "openai",
        protocol: "openai-compatible",
        default_upstream: "",
        credential_env: "AZURE_OPENAI_API_KEY",
        credential_header: "api-key",
        credential_prefix: "",
        auth_strategy: "api-key+deployment-endpoint",
        install_mode: "configured-endpoint",
        zero_code_compatible: true,
    },
    ProxyPreset {
        provider: "openrouter",
        gateway_provider: "openai-compatible",
        protocol: "openai-compatible",
        default_upstream: "https://openrouter.ai/api",
        credential_env: "OPENROUTER_API_KEY",
        credential_header: "Authorization",
        credential_prefix: "Bearer ",
        auth_strategy: "api-key",
        install_mode: "native-proxy",
        zero_code_compatible: true,
    },
    ProxyPreset {
        provider: "mistral",
        gateway_provider: "openai-compatible",
        protocol: "openai-compatible",
        default_upstream: "https://api.mistral.ai",
        credential_env: "MISTRAL_API_KEY",
        credential_header: "Authorization",
        credential_prefix: "Bearer ",
        auth_strategy: "api-key",
        install_mode: "native-proxy",
        zero_code_compatible: true,
    },
    ProxyPreset {
        provider: "groq",
        gateway_provider: "openai-compatible",
        protocol: "openai-compatible",
        default_upstream: "https://api.groq.com/openai",
        credential_env: "GROQ_API_KEY",
        credential_header: "Authorization",
        credential_prefix: "Bearer ",
        auth_strategy: "api-key",
        install_mode: "native-proxy",
        zero_code_compatible: true,
    },
    ProxyPreset {
        provider: "cohere",
        gateway_provider: "openai-compatible",
        protocol: "chat",
        default_upstream: "https://api.cohere.com",
        credential_env: "COHERE_API_KEY",
        credential_header: "Authorization",
        credential_prefix: "Bearer ",
        auth_strategy: "api-key",
        install_mode: "adapter-required",
        zero_code_compatible: false,
    },
    ProxyPreset {
        provider: "aws-bedrock",
        gateway_provider: "anthropic",
        protocol: "converse",
        default_upstream: "",
        credential_env: "AWS_PROFILE",
        credential_header: "",
        credential_prefix: "",
        auth_strategy: "aws-sigv4",
        install_mode: "signed-adapter-required",
        zero_code_compatible: false,
    },
    ProxyPreset {
        provider: "vertex-ai",
        gateway_provider: "gemini",
        protocol: "generate-content",
        default_upstream: "",
        credential_env: "GOOGLE_APPLICATION_CREDENTIALS",
        credential_header: "",
        credential_prefix: "",
        auth_strategy: "google-oauth2",
        install_mode: "signed-adapter-required",
        zero_code_compatible: false,
    },
];

pub(crate) fn supports(command: &[String]) -> bool {
    command.len() == 2
        && ((command[0] == "run"
            && (command[1] == "gateway-plan" || command[1] == "proxy-service"))
            || (command[0] == "provider" && command[1] == "proxy"))
}

fn sha256(value: &[u8]) -> String {
    format!("{:x}", Sha256::digest(value))
}

fn sorted(value: &Value) -> Value {
    match value {
        Value::Array(rows) => Value::Array(rows.iter().map(sorted).collect()),
        Value::Object(map) => {
            let mut keys = map.keys().collect::<Vec<_>>();
            keys.sort_unstable();
            let mut output = Map::new();
            for key in keys {
                output.insert(key.clone(), sorted(&map[key]));
            }
            Value::Object(output)
        }
        _ => value.clone(),
    }
}

fn canonical_json(value: &Value) -> Result<Vec<u8>, String> {
    serde_json::to_vec(&sorted(value)).map_err(|error| format!("PROXY_JSON_FAILED:{error}"))
}

fn gateway_spec(provider: &str) -> Result<GatewaySpec, String> {
    match provider.trim().to_lowercase().as_str() {
        "openai" => Ok(GatewaySpec {
            credential_env: "OPENAI_API_KEY",
            header: "Authorization",
            prefix: "Bearer ",
            protocol: "openai",
        }),
        "anthropic" => Ok(GatewaySpec {
            credential_env: "ANTHROPIC_API_KEY",
            header: "x-api-key",
            prefix: "",
            protocol: "anthropic",
        }),
        "gemini" => Ok(GatewaySpec {
            credential_env: "GEMINI_API_KEY",
            header: "x-goog-api-key",
            prefix: "",
            protocol: "gemini",
        }),
        "mistral" => Ok(GatewaySpec {
            credential_env: "MISTRAL_API_KEY",
            header: "Authorization",
            prefix: "Bearer ",
            protocol: "openai-compatible",
        }),
        "groq" => Ok(GatewaySpec {
            credential_env: "GROQ_API_KEY",
            header: "Authorization",
            prefix: "Bearer ",
            protocol: "openai-compatible",
        }),
        "openrouter" => Ok(GatewaySpec {
            credential_env: "OPENROUTER_API_KEY",
            header: "Authorization",
            prefix: "Bearer ",
            protocol: "openai-compatible",
        }),
        "azure-openai" => Ok(GatewaySpec {
            credential_env: "AZURE_OPENAI_API_KEY",
            header: "api-key",
            prefix: "",
            protocol: "azure-openai",
        }),
        "bedrock" => Ok(GatewaySpec {
            credential_env: "AWS_PROFILE",
            header: "",
            prefix: "",
            protocol: "sigv4-adapter",
        }),
        "vertex" => Ok(GatewaySpec {
            credential_env: "GOOGLE_APPLICATION_CREDENTIALS",
            header: "",
            prefix: "",
            protocol: "oauth2-adapter",
        }),
        "local" => Ok(GatewaySpec {
            credential_env: "",
            header: "",
            prefix: "",
            protocol: "openai-compatible",
        }),
        _ => Err(format!("unsupported provider: {provider}")),
    }
}

fn option_value(arguments: &[String], flag: &str) -> Result<Option<String>, String> {
    let mut result = None;
    let mut index = 0usize;
    while index < arguments.len() {
        let current = &arguments[index];
        let found = if current == flag {
            index += 1;
            Some(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?
                    .clone(),
            )
        } else {
            current
                .strip_prefix(flag)
                .and_then(|tail| tail.strip_prefix('='))
                .map(str::to_owned)
        };
        if let Some(found) = found {
            if result.is_some() {
                return Err(format!("{flag}_DUPLICATE"));
            }
            result = Some(found);
        }
        index += 1;
    }
    Ok(result)
}

fn has_flag(arguments: &[String], flag: &str) -> bool {
    arguments.iter().any(|item| item == flag)
}

fn action_position(arguments: &[String], action: &str) -> Result<usize, String> {
    arguments
        .windows(2)
        .position(|row| row[0] == "run" && row[1] == action)
        .map(|index| index + 2)
        .ok_or_else(|| format!("PROXY_ACTION_NOT_FOUND:{action}"))
}

fn positional(
    arguments: &[String],
    action: &str,
    value_flags: &[&str],
) -> Result<Vec<String>, String> {
    let mut output = Vec::new();
    let mut index = action_position(arguments, action)?;
    while index < arguments.len() {
        if value_flags.contains(&arguments[index].as_str()) {
            index += 2;
            continue;
        }
        if arguments[index].starts_with("--") {
            index += 1;
            continue;
        }
        output.push(arguments[index].clone());
        index += 1;
    }
    Ok(output)
}

fn gateway_plan(arguments: &[String]) -> Result<Value, String> {
    let rows = positional(
        arguments,
        "gateway-plan",
        &["--upstream", "--credential-source"],
    )?;
    let provider = rows
        .first()
        .ok_or_else(|| "GATEWAY_PROVIDER_REQUIRED".to_owned())?
        .trim()
        .to_lowercase();
    let upstream = option_value(arguments, "--upstream")?.unwrap_or_default();
    let credential_source =
        option_value(arguments, "--credential-source")?.unwrap_or_else(|| "os-broker".to_owned());
    let spec = gateway_spec(&provider)?;
    let receipt = sha256(&canonical_json(&json!({
        "provider": provider,
        "protocol": spec.protocol,
        "credential_source": credential_source,
        "upstream": upstream,
    }))?);
    Ok(json!({
        "ok": true,
        "version": VERSION,
        "channel": CHANNEL,
        "provider": provider,
        "protocol": spec.protocol,
        "upstream": upstream,
        "agent_environment_contains_secret": false,
        "credential_source": credential_source,
        "transport_injection": {
            "credential_env": spec.credential_env,
            "header": spec.header,
            "prefix": spec.prefix,
            "visibility": "gateway-process-only",
        },
        "child_process_secret_inheritance": "denied",
        "logs": "redacted",
        "receipt": receipt,
    }))
}

fn preset(provider: &str) -> Result<ProxyPreset, String> {
    let normalized = provider.trim().to_lowercase();
    PRESETS
        .iter()
        .copied()
        .find(|item| item.provider == normalized)
        .ok_or_else(|| provider.to_owned())
}

fn preset_json(item: ProxyPreset) -> Value {
    json!({
        "provider": item.provider,
        "gateway_provider": item.gateway_provider,
        "protocol": item.protocol,
        "default_upstream": item.default_upstream,
        "credential_env": item.credential_env,
        "credential_header": item.credential_header,
        "credential_prefix": item.credential_prefix,
        "auth_strategy": item.auth_strategy,
        "install_mode": item.install_mode,
        "zero_code_compatible": item.zero_code_compatible,
        "live_certification": "external-receipt-required",
    })
}

fn product_plan(provider: &str, upstream: &str) -> Result<Value, String> {
    let item = preset(provider)?;
    let resolved = if upstream.is_empty() {
        item.default_upstream
    } else {
        upstream
    };
    let mut reasons = Vec::<String>::new();
    if resolved.is_empty() {
        reasons.push("explicit-upstream-required".to_owned());
    }
    if !resolved.is_empty() && !resolved.starts_with("https://") {
        reasons.push("https-upstream-required".to_owned());
    }
    if !item.zero_code_compatible {
        reasons.push(item.install_mode.to_owned());
    }
    Ok(json!({
        "ok": reasons.is_empty(),
        "version": VERSION,
        "channel": CHANNEL,
        "provider": preset_json(item),
        "resolved_upstream": resolved,
        "control_token_required": true,
        "stream_mode": "commit-before-forward",
        "credential_policy": "transport-only",
        "reasons": reasons,
        "live_certification": "NOT_CERTIFIED",
    }))
}

fn native_proxy_command(
    item: ProxyPreset,
    project: &Path,
    state_root: &Path,
    upstream: &str,
    listen_host: &str,
    listen_port: i64,
    cache_policy: &str,
) -> Result<Vec<String>, String> {
    let executable =
        env::current_exe().map_err(|error| format!("PROXY_CURRENT_EXE_FAILED:{error}"))?;
    let mut command = vec![
        executable.to_string_lossy().into_owned(),
        "--engine".to_owned(),
        "rust".to_owned(),
        "--project".to_owned(),
        project.to_string_lossy().into_owned(),
        "--state-root".to_owned(),
        state_root.to_string_lossy().into_owned(),
        "provider".to_owned(),
        "proxy".to_owned(),
        "--provider".to_owned(),
        item.gateway_provider.to_owned(),
        "--upstream".to_owned(),
        upstream.to_owned(),
        "--listen-host".to_owned(),
        listen_host.to_owned(),
        "--listen-port".to_owned(),
        listen_port.to_string(),
        "--credential-env".to_owned(),
        item.credential_env.to_owned(),
        "--credential-header".to_owned(),
        item.credential_header.to_owned(),
        "--cache-policy".to_owned(),
        cache_policy.to_owned(),
    ];
    if !item.credential_prefix.is_empty() {
        command.extend([
            "--credential-prefix".to_owned(),
            item.credential_prefix.to_owned(),
        ]);
    }
    Ok(command)
}

fn service_spec(arguments: &[String], project: &Path, state_root: &Path) -> Result<Value, String> {
    let rows = positional(
        arguments,
        "proxy-service",
        &[
            "--upstream",
            "--listen-host",
            "--listen-port",
            "--cache-policy",
            "--environment-file",
            "--platform",
            "--home",
        ],
    )?;
    if rows.len() < 2 {
        return Err("PROXY_SERVICE_ACTION_PROVIDER_REQUIRED".to_owned());
    }
    let provider = &rows[1];
    let item = preset(provider)?;
    let upstream_override = option_value(arguments, "--upstream")?.unwrap_or_default();
    let plan = product_plan(provider, &upstream_override)?;
    if !plan["ok"].as_bool().unwrap_or(false) {
        let reasons = plan["reasons"]
            .as_array()
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .map(|v| v.as_str().unwrap_or_default().to_owned())
            .collect::<Vec<_>>()
            .join(",");
        return Err(format!("proxy preset is not directly runnable: {reasons}"));
    }
    let upstream = plan["resolved_upstream"].as_str().unwrap_or_default();
    let listen_host =
        option_value(arguments, "--listen-host")?.unwrap_or_else(|| "127.0.0.1".to_owned());
    let listen_port = option_value(arguments, "--listen-port")?
        .map(|value| {
            value
                .parse::<i64>()
                .map_err(|_| "PROXY_LISTEN_PORT_INVALID".to_owned())
        })
        .transpose()?
        .unwrap_or(8787);
    let cache_policy =
        option_value(arguments, "--cache-policy")?.unwrap_or_else(|| "auto".to_owned());
    if !matches!(
        cache_policy.as_str(),
        "off" | "auto" | "read" | "read-write"
    ) {
        return Err("PROXY_CACHE_POLICY_INVALID".to_owned());
    }
    let environment_file = option_value(arguments, "--environment-file")?.unwrap_or_default();
    Ok(json!({
        "name": format!("syntavra-proxy-{}", provider.replace(['_', '.'], "-")),
        "command": native_proxy_command(item, project, state_root, upstream, &listen_host, listen_port, &cache_policy)?,
        "environment_file": environment_file,
        "working_directory": project.to_string_lossy(),
        "description": format!("Syntavra v{VERSION} pre-release {provider} provider proxy"),
        "restart_seconds": 3,
    }))
}

fn shell_quote(value: &str) -> String {
    if value
        .chars()
        .all(|ch| ch.is_ascii_alphanumeric() || "_@%+=:,./-".contains(ch))
    {
        return value.to_owned();
    }
    format!("'{}'", value.replace('\'', "'\"'\"'"))
}

fn xml_escape(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

fn windows_cmdline(arguments: &[String]) -> String {
    arguments
        .iter()
        .map(|value| {
            if value.is_empty() {
                return "\"\"".to_owned();
            }
            if !value.contains([' ', '\t', '"']) {
                return value.clone();
            }
            let mut out = String::from("\"");
            let mut slashes = 0usize;
            for ch in value.chars() {
                if ch == '\\' {
                    slashes += 1;
                    continue;
                }
                if ch == '"' {
                    out.push_str(&"\\".repeat(slashes * 2 + 1));
                    out.push('"');
                    slashes = 0;
                    continue;
                }
                out.push_str(&"\\".repeat(slashes));
                slashes = 0;
                out.push(ch);
            }
            out.push_str(&"\\".repeat(slashes * 2));
            out.push('"');
            out
        })
        .collect::<Vec<_>>()
        .join(" ")
}

fn service_platform(explicit: Option<String>) -> Result<String, String> {
    let normalized = explicit
        .unwrap_or_else(|| env::consts::OS.to_owned())
        .to_lowercase();
    if normalized.starts_with("linux") {
        Ok("linux".to_owned())
    } else if normalized.starts_with("mac") || normalized.starts_with("darwin") {
        Ok("darwin".to_owned())
    } else if normalized.starts_with("win") {
        Ok("windows".to_owned())
    } else {
        Err(format!("unsupported service platform: {normalized}"))
    }
}

fn service_plan(spec: &Value, home: &Path, platform: &str) -> Result<Value, String> {
    let name = spec["name"]
        .as_str()
        .ok_or_else(|| "PROXY_SPEC_NAME_INVALID".to_owned())?;
    let command = spec["command"]
        .as_array()
        .ok_or_else(|| "PROXY_SPEC_COMMAND_INVALID".to_owned())?
        .iter()
        .map(|value| value.as_str().unwrap_or_default().to_owned())
        .collect::<Vec<_>>();
    let working = spec["working_directory"].as_str().unwrap_or_default();
    let environment_file = spec["environment_file"].as_str().unwrap_or_default();
    let description = spec["description"]
        .as_str()
        .unwrap_or("Syntavra provider proxy");
    let restart = spec["restart_seconds"].as_i64().unwrap_or(3);
    let (path, descriptor, activation, deactivation) = if platform == "linux" {
        let path = home
            .join(".config/systemd/user")
            .join(format!("{name}.service"));
        let mut lines = vec![
            "[Unit]".to_owned(),
            format!("Description={description}"),
            "After=network-online.target".to_owned(),
            "".to_owned(),
            "[Service]".to_owned(),
            "Type=simple".to_owned(),
            format!(
                "ExecStart={}",
                command
                    .iter()
                    .map(|v| shell_quote(v))
                    .collect::<Vec<_>>()
                    .join(" ")
            ),
            "Restart=on-failure".to_owned(),
            format!("RestartSec={restart}"),
            "NoNewPrivileges=true".to_owned(),
            "PrivateTmp=true".to_owned(),
            "ProtectSystem=strict".to_owned(),
            "ProtectHome=read-only".to_owned(),
        ];
        if !working.is_empty() {
            lines.push(format!("WorkingDirectory={working}"));
        }
        if !environment_file.is_empty() {
            lines.push(format!("EnvironmentFile={environment_file}"));
        }
        lines.extend([
            "".to_owned(),
            "[Install]".to_owned(),
            "WantedBy=default.target".to_owned(),
            "".to_owned(),
        ]);
        (
            path,
            lines.join("\n"),
            vec![
                "systemctl",
                "--user",
                "enable",
                "--now",
                &format!("{name}.service"),
            ]
            .into_iter()
            .map(str::to_owned)
            .collect(),
            vec![
                "systemctl",
                "--user",
                "disable",
                "--now",
                &format!("{name}.service"),
            ]
            .into_iter()
            .map(str::to_owned)
            .collect(),
        )
    } else if platform == "darwin" {
        let label = format!("dev.syntavra.{name}");
        let path = home
            .join("Library/LaunchAgents")
            .join(format!("{label}.plist"));
        let args_xml = command
            .iter()
            .map(|item| format!("    <string>{}</string>", xml_escape(item)))
            .collect::<Vec<_>>()
            .join("\n");
        let working_xml = if working.is_empty() {
            String::new()
        } else {
            format!(
                "\n  <key>WorkingDirectory</key><string>{}</string>",
                xml_escape(working)
            )
        };
        let env_xml = if environment_file.is_empty() {
            String::new()
        } else {
            format!("\n  <key>EnvironmentVariables</key><dict><key>SYNTAVRA_ENV_FILE</key><string>{}</string></dict>", xml_escape(environment_file))
        };
        let descriptor = format!("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n<plist version=\"1.0\">\n<dict>\n  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>\n  <key>Label</key><string>{}</string>\n  <key>ProcessType</key><string>Background</string>\n  <key>ProgramArguments</key><array>\n{}\n  </array>\n  <key>RunAtLoad</key><true/>\n  <key>ThrottleInterval</key><integer>{}</integer>{}{}\n</dict>\n</plist>\n", xml_escape(&label), args_xml, restart, working_xml, env_xml);
        let uid = env::var("UID")
            .ok()
            .and_then(|v| v.parse::<i64>().ok())
            .unwrap_or(0);
        let domain = format!("gui/{uid}");
        (
            path.clone(),
            descriptor,
            vec![
                "launchctl".to_owned(),
                "bootstrap".to_owned(),
                domain.clone(),
                path.to_string_lossy().into_owned(),
            ],
            vec![
                "launchctl".to_owned(),
                "bootout".to_owned(),
                domain,
                path.to_string_lossy().into_owned(),
            ],
        )
    } else {
        let path = home
            .join("AppData/Local/Syntavra/services")
            .join(format!("{name}.xml"));
        let executable = xml_escape(command.first().map(String::as_str).unwrap_or_default());
        let args = xml_escape(&windows_cmdline(&command[1..]));
        let working_xml = if working.is_empty() {
            String::new()
        } else {
            format!(
                "<WorkingDirectory>{}</WorkingDirectory>",
                xml_escape(working)
            )
        };
        let descriptor = format!("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<Task version=\"1.4\" xmlns=\"http://schemas.microsoft.com/windows/2004/02/mit/task\">\n  <RegistrationInfo><Description>{}</Description></RegistrationInfo>\n  <Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>\n  <Principals><Principal id=\"Author\"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>\n  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><RestartOnFailure><Interval>PT{}S</Interval><Count>999</Count></RestartOnFailure><ExecutionTimeLimit>PT0S</ExecutionTimeLimit></Settings>\n  <Actions Context=\"Author\"><Exec><Command>{}</Command><Arguments>{}</Arguments>{}</Exec></Actions>\n</Task>\n", xml_escape(description), restart, executable, args, working_xml);
        (
            path.clone(),
            descriptor,
            vec![
                "schtasks".to_owned(),
                "/Create".to_owned(),
                "/TN".to_owned(),
                name.to_owned(),
                "/XML".to_owned(),
                path.to_string_lossy().into_owned(),
                "/F".to_owned(),
            ],
            vec![
                "schtasks".to_owned(),
                "/Delete".to_owned(),
                "/TN".to_owned(),
                name.to_owned(),
                "/F".to_owned(),
            ],
        )
    };
    Ok(json!({
        "platform": platform,
        "service_name": name,
        "descriptor_path": path.to_string_lossy(),
        "descriptor_hash": sha256(descriptor.as_bytes()),
        "descriptor": descriptor,
        "activation_argv": activation,
        "deactivation_argv": deactivation,
        "user_scoped": true,
    }))
}

fn validate_destination(home: &Path, path: &Path) -> Result<(), String> {
    let home = fs::canonicalize(home).unwrap_or_else(|_| home.to_path_buf());
    let resolved = path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
    if !resolved.starts_with(&home) {
        return Err("service descriptor must remain under the user home".to_owned());
    }
    let mut current = path.to_path_buf();
    while current != home && current != current.parent().unwrap_or(&current) {
        if current
            .symlink_metadata()
            .is_ok_and(|metadata| metadata.file_type().is_symlink())
        {
            return Err(format!(
                "symlink service path component is forbidden: {}",
                current.to_string_lossy()
            ));
        }
        let Some(parent) = current.parent() else {
            break;
        };
        current = parent.to_path_buf();
    }
    Ok(())
}

fn proxy_service(arguments: &[String], project: &Path, state_root: &Path) -> Result<Value, String> {
    let rows = positional(
        arguments,
        "proxy-service",
        &[
            "--upstream",
            "--listen-host",
            "--listen-port",
            "--cache-policy",
            "--environment-file",
            "--platform",
            "--home",
        ],
    )?;
    if rows.len() < 2 {
        return Err("PROXY_SERVICE_ACTION_PROVIDER_REQUIRED".to_owned());
    }
    let action = &rows[0];
    let spec = service_spec(arguments, project, state_root)?;
    let platform = service_platform(option_value(arguments, "--platform")?)?;
    let home = option_value(arguments, "--home")?
        .map(PathBuf::from)
        .or_else(|| {
            env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" }).map(PathBuf::from)
        })
        .ok_or_else(|| "PROXY_HOME_UNAVAILABLE".to_owned())?;
    let plan = service_plan(&spec, &home, &platform)?;
    let path = PathBuf::from(plan["descriptor_path"].as_str().unwrap_or_default());
    match action.as_str() {
        "plan" => Ok(json!({"ok":true,"action":"plan","spec":spec,"plan":plan})),
        "verify" => {
            let exists = path.is_file()
                && path
                    .symlink_metadata()
                    .is_ok_and(|metadata| !metadata.file_type().is_symlink());
            let actual = if exists {
                fs::read(&path).map_err(|error| format!("PROXY_SERVICE_READ_FAILED:{error}"))?
            } else {
                Vec::new()
            };
            Ok(
                json!({"ok": exists && sha256(&actual)==plan["descriptor_hash"].as_str().unwrap_or_default(), "exists":exists,"expected_hash":plan["descriptor_hash"].clone(),"actual_hash":if exists{sha256(&actual)}else{String::new()},"path":path.to_string_lossy()}),
            )
        }
        "install" => {
            validate_destination(&home, &path)?;
            let dry_run = !has_flag(arguments, "--apply");
            let activate = has_flag(arguments, "--activate");
            if !dry_run {
                if let Some(parent) = path.parent() {
                    fs::create_dir_all(parent)
                        .map_err(|error| format!("PROXY_SERVICE_PARENT_FAILED:{error}"))?;
                }
                let temporary = path.with_file_name(format!(
                    "{}.tmp",
                    path.file_name().unwrap_or_default().to_string_lossy()
                ));
                fs::write(&temporary, plan["descriptor"].as_str().unwrap_or_default())
                    .map_err(|error| format!("PROXY_SERVICE_WRITE_FAILED:{error}"))?;
                fs::rename(&temporary, &path)
                    .map_err(|error| format!("PROXY_SERVICE_RENAME_FAILED:{error}"))?;
                if activate && platform == "linux" {
                    Command::new("systemctl")
                        .args(["--user", "daemon-reload"])
                        .status()
                        .map_err(|error| format!("PROXY_SYSTEMCTL_FAILED:{error}"))?;
                }
                if activate {
                    let argv = plan["activation_argv"]
                        .as_array()
                        .cloned()
                        .unwrap_or_default()
                        .into_iter()
                        .map(|v| v.as_str().unwrap_or_default().to_owned())
                        .collect::<Vec<_>>();
                    if let Some((program, args)) = argv.split_first() {
                        let status = Command::new(program)
                            .args(args)
                            .status()
                            .map_err(|error| format!("PROXY_SERVICE_ACTIVATE_FAILED:{error}"))?;
                        if !status.success() {
                            return Err("PROXY_SERVICE_ACTIVATION_FAILED".to_owned());
                        }
                    }
                }
            }
            Ok(json!({"ok":true,"dry_run":dry_run,"activated":activate && !dry_run,"plan":plan}))
        }
        "uninstall" => {
            validate_destination(&home, &path)?;
            let dry_run = !has_flag(arguments, "--apply");
            let deactivate = has_flag(arguments, "--activate");
            if !dry_run {
                if deactivate && path.exists() {
                    let argv = plan["deactivation_argv"]
                        .as_array()
                        .cloned()
                        .unwrap_or_default()
                        .into_iter()
                        .map(|v| v.as_str().unwrap_or_default().to_owned())
                        .collect::<Vec<_>>();
                    if let Some((program, args)) = argv.split_first() {
                        let _ = Command::new(program).args(args).status();
                    }
                }
                if path.exists() {
                    fs::remove_file(&path)
                        .map_err(|error| format!("PROXY_SERVICE_REMOVE_FAILED:{error}"))?;
                }
                if deactivate && platform == "linux" {
                    let _ = Command::new("systemctl")
                        .args(["--user", "daemon-reload"])
                        .status();
                }
            }
            Ok(
                json!({"ok":true,"dry_run":dry_run,"removed":!path.exists(),"path":path.to_string_lossy()}),
            )
        }
        _ => Err(format!("unsupported proxy service action: {action}")),
    }
}

pub(crate) fn execute(
    command: &[String],
    arguments: &[String],
    project: &Path,
    state_root: &Path,
) -> Result<Option<Value>, String> {
    if !supports(command) {
        return Ok(None);
    }
    if command.len() == 2 && command[0] == "provider" && command[1] == "proxy" {
        return native_remaining71_provider_proxy::execute(arguments, project, state_root)
            .map(Some);
    }
    let value = match command[1].as_str() {
        "gateway-plan" => gateway_plan(arguments)?,
        "proxy-service" => proxy_service(arguments, project, state_root)?,
        _ => return Ok(None),
    };
    Ok(Some(value))
}
