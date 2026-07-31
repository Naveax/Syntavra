#![forbid(unsafe_code)]

use serde_json::{json, Value};

const VERSION: &str = "0.0.1";
const CHANNEL: &str = "pre-release";

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
    live_certification: &'static str,
}

pub struct ProxyPlanDecision {
    pub value: Value,
    pub exit_code: u8,
}

const PRESETS: [ProxyPreset; 10] = [
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
        live_certification: "external-receipt-required",
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
        live_certification: "external-receipt-required",
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
        live_certification: "external-receipt-required",
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
        live_certification: "external-receipt-required",
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
        live_certification: "external-receipt-required",
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
        live_certification: "external-receipt-required",
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
        live_certification: "external-receipt-required",
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
        live_certification: "external-receipt-required",
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
        live_certification: "external-receipt-required",
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
        live_certification: "external-receipt-required",
    },
];

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
        "live_certification": item.live_certification,
    })
}

fn argument_after<'a>(arguments: &'a [String], name: &str) -> Option<&'a str> {
    arguments
        .iter()
        .position(|argument| argument == name)
        .and_then(|index| arguments.get(index + 1))
        .map(String::as_str)
}

fn provider_argument(arguments: &[String]) -> Result<&str, String> {
    arguments
        .windows(3)
        .find(|window| window[0] == "run" && window[1] == "proxy-plan")
        .map(|window| window[2].as_str())
        .ok_or_else(|| "PROXY_PLAN_PROVIDER_MISSING".to_owned())
}

pub fn execute(arguments: &[String]) -> Result<ProxyPlanDecision, String> {
    let provider = provider_argument(arguments)?.trim().to_lowercase();
    let item = PRESETS
        .iter()
        .copied()
        .find(|item| item.provider == provider)
        .ok_or_else(|| format!("PROXY_PLAN_PROVIDER_UNKNOWN:{provider}"))?;
    let upstream = argument_after(arguments, "--upstream").unwrap_or("");
    let resolved_upstream = if upstream.is_empty() {
        item.default_upstream
    } else {
        upstream
    };
    let mut reasons = Vec::new();
    if resolved_upstream.is_empty() {
        reasons.push("explicit-upstream-required");
    }
    if !resolved_upstream.is_empty() && !resolved_upstream.starts_with("https://") {
        reasons.push("https-upstream-required");
    }
    if !item.zero_code_compatible {
        reasons.push(item.install_mode);
    }
    let ready = reasons.is_empty();
    Ok(ProxyPlanDecision {
        value: json!({
            "ok": ready,
            "version": VERSION,
            "channel": CHANNEL,
            "provider": preset_json(item),
            "resolved_upstream": resolved_upstream,
            "control_token_required": true,
            "stream_mode": "commit-before-forward",
            "credential_policy": "transport-only",
            "reasons": reasons,
            "live_certification": if item.live_certification.is_empty() { "UNKNOWN" } else { "NOT_CERTIFIED" },
        }),
        exit_code: if ready { 0 } else { 3 },
    })
}

#[cfg(test)]
mod tests {
    use super::execute;

    #[test]
    fn openai_default_is_ready() {
        let arguments = vec![
            "run".to_owned(),
            "proxy-plan".to_owned(),
            "openai".to_owned(),
        ];
        let decision = execute(&arguments).expect("plan");
        assert_eq!(decision.exit_code, 0);
        assert_eq!(decision.value["resolved_upstream"], "https://api.openai.com");
        assert_eq!(decision.value["ok"], true);
    }

    #[test]
    fn signed_provider_requires_adapter_and_upstream() {
        let arguments = vec![
            "run".to_owned(),
            "proxy-plan".to_owned(),
            "aws-bedrock".to_owned(),
        ];
        let decision = execute(&arguments).expect("plan");
        assert_eq!(decision.exit_code, 3);
        assert_eq!(
            decision.value["reasons"],
            json!(["explicit-upstream-required", "signed-adapter-required"])
        );
    }
}
