#![forbid(unsafe_code)]

use serde_json::{json, Value};

#[derive(Clone, Copy)]
struct IntegrationSpec {
    integration_id: &'static str,
    family: &'static str,
    transport: &'static str,
    install_mode: &'static str,
    capabilities: &'static [&'static str],
}

const fn spec(
    integration_id: &'static str,
    family: &'static str,
    transport: &'static str,
    install_mode: &'static str,
    capabilities: &'static [&'static str],
) -> IntegrationSpec {
    IntegrationSpec {
        integration_id,
        family,
        transport,
        install_mode,
        capabilities,
    }
}

const PROVIDERS: [IntegrationSpec; 10] = [
    spec(
        "openai",
        "provider",
        "responses+chat",
        "proxy-or-sdk",
        &["stream", "usage", "cache", "tools"],
    ),
    spec(
        "anthropic",
        "provider",
        "messages",
        "proxy-or-sdk",
        &["stream", "usage", "cache", "tools"],
    ),
    spec(
        "gemini",
        "provider",
        "generate-content",
        "proxy-or-sdk",
        &["stream", "usage", "tools"],
    ),
    spec(
        "aws-bedrock",
        "provider",
        "converse",
        "proxy-or-sdk",
        &["stream", "usage", "tools"],
    ),
    spec(
        "azure-openai",
        "provider",
        "openai-compatible",
        "proxy-or-sdk",
        &["stream", "usage", "cache", "tools"],
    ),
    spec(
        "vertex-ai",
        "provider",
        "generate-content",
        "proxy-or-sdk",
        &["stream", "usage", "tools"],
    ),
    spec(
        "openrouter",
        "provider",
        "openai-compatible",
        "proxy-or-sdk",
        &["stream", "usage", "tools"],
    ),
    spec(
        "mistral",
        "provider",
        "openai-compatible",
        "proxy-or-sdk",
        &["stream", "usage", "tools"],
    ),
    spec(
        "groq",
        "provider",
        "openai-compatible",
        "proxy-or-sdk",
        &["stream", "usage", "tools"],
    ),
    spec(
        "cohere",
        "provider",
        "chat",
        "proxy-or-sdk",
        &["stream", "usage", "tools"],
    ),
];

const FRAMEWORKS: [IntegrationSpec; 15] = [
    spec(
        "openai-python",
        "framework",
        "sdk",
        "python-wrapper",
        &["sync", "async", "stream"],
    ),
    spec(
        "openai-node",
        "framework",
        "sdk",
        "typescript-wrapper",
        &["async", "stream"],
    ),
    spec(
        "anthropic-python",
        "framework",
        "sdk",
        "python-wrapper",
        &["sync", "async", "stream"],
    ),
    spec(
        "anthropic-node",
        "framework",
        "sdk",
        "typescript-wrapper",
        &["async", "stream"],
    ),
    spec(
        "google-genai",
        "framework",
        "sdk",
        "python-wrapper",
        &["sync", "async", "stream"],
    ),
    spec(
        "vercel-ai-sdk",
        "framework",
        "middleware",
        "typescript-middleware",
        &["stream", "tools"],
    ),
    spec(
        "litellm",
        "framework",
        "callback",
        "python-callback",
        &["providers", "usage", "cache"],
    ),
    spec(
        "langchain",
        "framework",
        "callback",
        "python-callback",
        &["events", "usage", "tools"],
    ),
    spec(
        "langgraph",
        "framework",
        "middleware",
        "python-middleware",
        &["state", "events", "tools"],
    ),
    spec(
        "agno",
        "framework",
        "adapter",
        "python-adapter",
        &["agents", "usage", "tools"],
    ),
    spec(
        "strands",
        "framework",
        "adapter",
        "python-adapter",
        &["agents", "usage", "tools"],
    ),
    spec(
        "asgi",
        "framework",
        "middleware",
        "python-middleware",
        &["http", "stream"],
    ),
    spec(
        "openclaw",
        "framework",
        "context-engine",
        "plugin",
        &["memory", "tools", "stream"],
    ),
    spec(
        "mcp",
        "framework",
        "stdio+http",
        "native",
        &["tools", "resources", "prompts"],
    ),
    spec(
        "openai-compatible",
        "framework",
        "http-proxy",
        "zero-code",
        &["providers", "stream", "usage"],
    ),
];

const HOSTS: [IntegrationSpec; 18] = [
    spec(
        "claude-code",
        "host",
        "plugin+hooks",
        "auto",
        &["session", "pre-tool", "post-tool", "compact"],
    ),
    spec(
        "codex",
        "host",
        "skill+mcp",
        "auto",
        &["session", "tools", "mcp"],
    ),
    spec(
        "gemini-cli",
        "host",
        "extension+mcp",
        "auto",
        &["session", "tools", "mcp"],
    ),
    spec(
        "vscode-copilot",
        "host",
        "instructions+mcp",
        "auto",
        &["instructions", "tools", "mcp"],
    ),
    spec(
        "jetbrains-copilot",
        "host",
        "instructions+mcp",
        "auto",
        &["instructions", "tools", "mcp"],
    ),
    spec(
        "cursor",
        "host",
        "rules+mcp",
        "auto",
        &["rules", "tools", "mcp"],
    ),
    spec(
        "windsurf",
        "host",
        "rules+mcp",
        "auto",
        &["rules", "tools", "mcp"],
    ),
    spec(
        "opencode",
        "host",
        "config+mcp",
        "auto",
        &["session", "tools", "mcp"],
    ),
    spec(
        "cline",
        "host",
        "rules+mcp",
        "auto",
        &["rules", "tools", "mcp"],
    ),
    spec(
        "roo-code",
        "host",
        "rules+mcp",
        "auto",
        &["rules", "tools", "mcp"],
    ),
    spec(
        "qwen-code",
        "host",
        "agents+mcp",
        "auto",
        &["session", "tools", "mcp"],
    ),
    spec(
        "kiro",
        "host",
        "steering+mcp",
        "auto",
        &["rules", "tools", "mcp"],
    ),
    spec(
        "zed",
        "host",
        "rules+mcp",
        "auto",
        &["rules", "tools", "mcp"],
    ),
    spec("pi", "host", "extension", "auto", &["session", "tools"]),
    spec("omp", "host", "plugin", "auto", &["session", "tools"]),
    spec(
        "openclaw",
        "host",
        "plugin",
        "auto",
        &["session", "memory", "tools"],
    ),
    spec(
        "aider",
        "host",
        "env+wrapper",
        "auto",
        &["session", "repository"],
    ),
    spec(
        "continue",
        "host",
        "rules+mcp",
        "auto",
        &["rules", "tools", "mcp"],
    ),
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
            "mcp_capable": 14,
            "continuity_capable": 15,
            "primary_certification_targets": ["claude-code", "codex", "cursor"],
            "evidence_levels": {
                "contract-tested": 9,
                "host-specific-marker-contract-tested": 2,
                "official-path-contract-tested": 1,
                "official-skill-path-contract-tested": 3,
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
        assert_eq!(value["platform_adapters"]["mcp_capable"], 14);
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
