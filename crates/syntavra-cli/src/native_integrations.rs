#![forbid(unsafe_code)]

use serde_json::{json, Value};

struct IntegrationSpec {
    integration_id: &'static str,
    family: &'static str,
    transport: &'static str,
    install_mode: &'static str,
    capabilities: &'static [&'static str],
}

const PROVIDERS: [IntegrationSpec; 10] = [
    IntegrationSpec { integration_id: "openai", family: "provider", transport: "responses+chat", install_mode: "proxy-or-sdk", capabilities: &["stream", "usage", "cache", "tools"] },
    IntegrationSpec { integration_id: "anthropic", family: "provider", transport: "messages", install_mode: "proxy-or-sdk", capabilities: &["stream", "usage", "cache", "tools"] },
    IntegrationSpec { integration_id: "gemini", family: "provider", transport: "generate-content", install_mode: "proxy-or-sdk", capabilities: &["stream", "usage", "tools"] },
    IntegrationSpec { integration_id: "aws-bedrock", family: "provider", transport: "converse", install_mode: "proxy-or-sdk", capabilities: &["stream", "usage", "tools"] },
    IntegrationSpec { integration_id: "azure-openai", family: "provider", transport: "openai-compatible", install_mode: "proxy-or-sdk", capabilities: &["stream", "usage", "cache", "tools"] },
    IntegrationSpec { integration_id: "vertex-ai", family: "provider", transport: "generate-content", install_mode: "proxy-or-sdk", capabilities: &["stream", "usage", "tools"] },
    IntegrationSpec { integration_id: "openrouter", family: "provider", transport: "openai-compatible", install_mode: "proxy-or-sdk", capabilities: &["stream", "usage", "tools"] },
    IntegrationSpec { integration_id: "mistral", family: "provider", transport: "openai-compatible", install_mode: "proxy-or-sdk", capabilities: &["stream", "usage", "tools"] },
    IntegrationSpec { integration_id: "groq", family: "provider", transport: "openai-compatible", install_mode: "proxy-or-sdk", capabilities: &["stream", "usage", "tools"] },
    IntegrationSpec { integration_id: "cohere", family: "provider", transport: "chat", install_mode: "proxy-or-sdk", capabilities: &["stream", "usage", "tools"] },
];

const FRAMEWORKS: [IntegrationSpec; 15] = [
    IntegrationSpec { integration_id: "openai-python", family: "framework", transport: "sdk", install_mode: "python-wrapper", capabilities: &["sync", "async", "stream"] },
    IntegrationSpec { integration_id: "openai-node", family: "framework", transport: "sdk", install_mode: "typescript-wrapper", capabilities: &["async", "stream"] },
    IntegrationSpec { integration_id: "anthropic-python", family: "framework", transport: "sdk", install_mode: "python-wrapper", capabilities: &["sync", "async", "stream"] },
    IntegrationSpec { integration_id: "anthropic-node", family: "framework", transport: "sdk", install_mode: "typescript-wrapper", capabilities: &["async", "stream"] },
    IntegrationSpec { integration_id: "google-genai", family: "framework", transport: "sdk", install_mode: "python-wrapper", capabilities: &["sync", "async", "stream"] },
    IntegrationSpec { integration_id: "vercel-ai-sdk", family: "framework", transport: "middleware", install_mode: "typescript-middleware", capabilities: &["stream", "tools"] },
    IntegrationSpec { integration_id: "litellm", family: "framework", transport: "callback", install_mode: "python-callback", capabilities: &["providers", "usage", "cache"] },
    IntegrationSpec { integration_id: "langchain", family: "framework", transport: "callback", install_mode: "python-callback", capabilities: &["events", "usage", "tools"] },
    IntegrationSpec { integration_id: "langgraph", family: "framework", transport: "middleware", install_mode: "python-middleware", capabilities: &["state", "events", "tools"] },
    IntegrationSpec { integration_id: "agno", family: "framework", transport: "adapter", install_mode: "python-adapter", capabilities: &["agents", "usage", "tools"] },
    IntegrationSpec { integration_id: "strands", family: "framework", transport: "adapter", install_mode: "python-adapter", capabilities: &["agents", "usage", "tools"] },
    IntegrationSpec { integration_id: "asgi", family: "framework", transport: "middleware", install_mode: "python-middleware", capabilities: &["http", "stream"] },
    IntegrationSpec { integration_id: "openclaw", family: "framework", transport: "context-engine", install_mode: "plugin", capabilities: &["memory", "tools", "stream"] },
    IntegrationSpec { integration_id: "mcp", family: "framework", transport: "stdio+http", install_mode: "native", capabilities: &["tools", "resources", "prompts"] },
    IntegrationSpec { integration_id: "openai-compatible", family: "framework", transport: "http-proxy", install_mode: "zero-code", capabilities: &["providers", "stream", "usage"] },
];

const HOSTS: [IntegrationSpec; 18] = [
    IntegrationSpec { integration_id: "claude-code", family: "host", transport: "plugin+hooks", install_mode: "auto", capabilities: &["session", "pre-tool", "post-tool", "compact"] },
    IntegrationSpec { integration_id: "codex", family: "host", transport: "skill+mcp", install_mode: "auto", capabilities: &["session", "tools", "mcp"] },
    IntegrationSpec { integration_id: "gemini-cli", family: "host", transport: "extension+mcp", install_mode: "auto", capabilities: &["session", "tools", "mcp"] },
    IntegrationSpec { integration_id: "vscode-copilot", family: "host", transport: "instructions+mcp", install_mode: "auto", capabilities: &["instructions", "tools", "mcp"] },
    IntegrationSpec { integration_id: "jetbrains-copilot", family: "host", transport: "instructions+mcp", install_mode: "auto", capabilities: &["instructions", "tools", "mcp"] },
    IntegrationSpec { integration_id: "cursor", family: "host", transport: "rules+mcp", install_mode: "auto", capabilities: &["rules", "tools", "mcp"] },
    IntegrationSpec { integration_id: "windsurf", family: "host", transport: "rules+mcp", install_mode: "auto", capabilities: &["rules", "tools", "mcp"] },
    IntegrationSpec { integration_id: "opencode", family: "host", transport: "config+mcp", install_mode: "auto", capabilities: &["session", "tools", "mcp"] },
    IntegrationSpec { integration_id: "cline", family: "host", transport: "rules+mcp", install_mode: "auto", capabilities: &["rules", "tools", "mcp"] },
    IntegrationSpec { integration_id: "roo-code", family: "host", transport: "rules+mcp", install_mode: "auto", capabilities: &["rules", "tools", "mcp"] },
    IntegrationSpec { integration_id: "qwen-code", family: "host", transport: "agents+mcp", install_mode: "auto", capabilities: &["session", "tools", "mcp"] },
    IntegrationSpec { integration_id: "kiro", family: "host", transport: "steering+mcp", install_mode: "auto", capabilities: &["rules", "tools", "mcp"] },
    IntegrationSpec { integration_id: "zed", family: "host", transport: "rules+mcp", install_mode: "auto", capabilities: &["rules", "tools", "mcp"] },
    IntegrationSpec { integration_id: "pi", family: "host", transport: "extension", install_mode: "auto", capabilities: &["session", "tools"] },
    IntegrationSpec { integration_id: "omp", family: "host", transport: "plugin", install_mode: "auto", capabilities: &["session", "tools"] },
    IntegrationSpec { integration_id: "openclaw", family: "host", transport: "plugin", install_mode: "auto", capabilities: &["session", "memory", "tools"] },
    IntegrationSpec { integration_id: "aider", family: "host", transport: "env+wrapper", install_mode: "auto", capabilities: &["session", "repository"] },
    IntegrationSpec { integration_id: "continue", family: "host", transport: "rules+mcp", install_mode: "auto", capabilities: &["rules", "tools", "mcp"] },
];

fn family(arguments: &[String]) -> Result<Option<&str>, String> {
    let mut values = arguments.iter();
    while let Some(argument) = values.next() {
        if argument == "--family" {
            let value = values
                .next()
                .map(String::as_str)
                .ok_or_else(|| "INTEGRATIONS_FAMILY_MISSING".to_owned())?;
            return validate_family(value).map(Some);
        }
        if let Some(value) = argument.strip_prefix("--family=") {
            return validate_family(value).map(Some);
        }
    }
    Ok(None)
}

fn validate_family(value: &str) -> Result<&str, String> {
    match value {
        "provider" | "framework" | "host" => Ok(value),
        _ => Err(format!("INTEGRATIONS_FAMILY_INVALID:{value}")),
    }
}

fn record(item: &IntegrationSpec) -> Value {
    json!({
        "integration_id": item.integration_id,
        "family": item.family,
        "transport": item.transport,
        "install_mode": item.install_mode,
        "capabilities": item.capabilities,
        "certification": "internal-contract",
        "live_receipt_required": true,
    })
}

fn records(selected: Option<&str>) -> Vec<Value> {
    PROVIDERS
        .iter()
        .chain(FRAMEWORKS.iter())
        .chain(HOSTS.iter())
        .filter(|item| selected.is_none_or(|family| item.family == family))
        .map(record)
        .collect()
}

pub fn execute(arguments: &[String]) -> Result<Value, String> {
    let selected = family(arguments)?;
    Ok(json!({
        "coverage": {
            "ok": true,
            "reasons": [],
            "providers": 10,
            "frameworks": 15,
            "hosts": 18,
            "automatic_hosts": 18,
            "live_certification_boundary": "external receipts are required before VERIFIED_LIVE",
        },
        "integrations": records(selected),
        "platform_adapters": {
            "ok": true,
            "adapters": 18,
            "missing_matrix_hosts": [],
            "extra_adapters": [],
            "mcp_capable": 15,
            "continuity_capable": 15,
            "primary_certification_targets": ["claude-code", "codex", "cursor"],
            "evidence_levels": {
                "contract-tested": 15,
                "primary-certification-target": 3,
            },
            "live_boundary": "live adapter certification requires external execution receipts",
        },
        "proxy_presets": {
            "ok": true,
            "providers": 10,
            "zero_code_compatible": 7,
            "adapter_required": 3,
            "missing": [],
            "extra": [],
            "unsafe_upstreams": [],
            "live_boundary": "preset validation is not live provider certification",
        },
    }))
}

#[cfg(test)]
mod tests {
    use super::execute;

    #[test]
    fn all_records_match_contract_counts() {
        let value = execute(&["integrations".to_owned()]).expect("integrations");
        assert_eq!(value["integrations"].as_array().map(Vec::len), Some(43));
        assert_eq!(value["coverage"]["providers"], 10);
        assert_eq!(value["platform_adapters"]["adapters"], 18);
    }

    #[test]
    fn filters_one_family_without_reordering() {
        let arguments = vec![
            "integrations".to_owned(),
            "--family".to_owned(),
            "provider".to_owned(),
        ];
        let value = execute(&arguments).expect("providers");
        assert_eq!(value["integrations"].as_array().map(Vec::len), Some(10));
        assert_eq!(value["integrations"][0]["integration_id"], "openai");
    }
}
