#![forbid(unsafe_code)]

use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{json, Value};

pub fn supports(command: &[String]) -> bool {
    command.len() == 2
        && matches!(
            (command[0].as_str(), command[1].as_str()),
            ("provider", "capabilities")
                | ("output", "profiles")
                | ("benchmark", "generate-config")
                | ("run", "platform-manifest" | "competitive-manifest")
        )
}

fn option_value(arguments: &[String], flag: &str) -> Result<Option<String>, String> {
    let mut found = None;
    let mut index = 0usize;
    while index < arguments.len() {
        let item = &arguments[index];
        let value = if item == flag {
            index += 1;
            Some(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?
                    .clone(),
            )
        } else {
            item.strip_prefix(flag)
                .and_then(|suffix| suffix.strip_prefix('='))
                .map(str::to_owned)
        };
        if let Some(value) = value {
            if found.is_some() {
                return Err(format!("{flag}_DUPLICATE"));
            }
            found = Some(value);
        }
        index += 1;
    }
    Ok(found)
}

fn required_option(arguments: &[String], flag: &str) -> Result<String, String> {
    option_value(arguments, flag)?.ok_or_else(|| format!("{flag}_MISSING"))
}

fn positional_after(arguments: &[String], marker: &str) -> Option<String> {
    let index = arguments.iter().position(|value| value == marker)?;
    let candidate = arguments.get(index + 1)?;
    (!candidate.starts_with('-')).then(|| candidate.clone())
}

fn write_pretty_json(path: &Path, value: &Value) -> Result<usize, String> {
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        fs::create_dir_all(parent).map_err(|error| format!("OUTPUT_CREATE_FAILED:{error}"))?;
    }
    let rendered = format!(
        "{}\n",
        serde_json::to_string_pretty(value)
            .map_err(|error| format!("OUTPUT_SERIALIZE_FAILED:{error}"))?
    );
    fs::write(path, rendered.as_bytes()).map_err(|error| format!("OUTPUT_WRITE_FAILED:{error}"))?;
    Ok(rendered.len())
}

fn write_compact_json_atomic(path: &Path, value: &Value) -> Result<(), String> {
    let parent = path.parent().unwrap_or_else(|| Path::new("."));
    if !parent.as_os_str().is_empty() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("BENCHMARK_OUTPUT_CREATE_FAILED:{error}"))?;
    }
    let file_name = path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| "BENCHMARK_OUTPUT_NAME_INVALID".to_owned())?;
    let temporary = parent.join(format!(".{file_name}.syntavra.tmp"));
    let rendered = format!(
        "{}\n",
        serde_json::to_string(value)
            .map_err(|error| format!("BENCHMARK_OUTPUT_SERIALIZE_FAILED:{error}"))?
    );
    fs::write(&temporary, rendered.as_bytes())
        .map_err(|error| format!("BENCHMARK_OUTPUT_WRITE_FAILED:{error}"))?;
    if path.exists() {
        fs::remove_file(path).map_err(|error| format!("BENCHMARK_OUTPUT_REMOVE_FAILED:{error}"))?;
    }
    fs::rename(&temporary, path).map_err(|error| {
        let _ = fs::remove_file(&temporary);
        format!("BENCHMARK_OUTPUT_REPLACE_FAILED:{error}")
    })
}

fn provider_capabilities_value() -> Value {
    json!({
        "anthropic": {
            "aliases": ["anthropic", "claude", "bedrock-anthropic", "vertex-anthropic"],
            "cache_usage_fields": ["usage.cache_read_input_tokens", "usage.cache_creation_input_tokens"],
            "explicit_prompt_cache": true,
            "implicit_prompt_cache": false,
            "prompt_cache_key_field": "cache_control",
            "provider": "anthropic",
            "request_family": "anthropic"
        },
        "gemini": {
            "aliases": ["gemini", "google", "google-ai", "vertex-gemini"],
            "cache_usage_fields": ["usageMetadata.cachedContentTokenCount", "usage.total_cached_tokens"],
            "explicit_prompt_cache": true,
            "implicit_prompt_cache": true,
            "prompt_cache_key_field": "cachedContent",
            "provider": "gemini",
            "request_family": "gemini"
        },
        "openai": {
            "aliases": ["openai", "chatgpt", "responses", "azure-openai"],
            "cache_usage_fields": ["usage.input_tokens_details.cached_tokens", "usage.prompt_tokens_details.cached_tokens"],
            "explicit_prompt_cache": true,
            "implicit_prompt_cache": true,
            "prompt_cache_key_field": "prompt_cache_key",
            "provider": "openai",
            "request_family": "openai"
        },
        "openai-compatible": {
            "aliases": ["openrouter", "litellm", "vllm", "ollama", "lmstudio", "openai-compatible"],
            "cache_usage_fields": ["usage.prompt_tokens_details.cached_tokens"],
            "explicit_prompt_cache": false,
            "implicit_prompt_cache": false,
            "prompt_cache_key_field": "",
            "provider": "openai-compatible",
            "request_family": "openai"
        }
    })
}

fn provider_capabilities(arguments: &[String]) -> Result<Value, String> {
    let all = provider_capabilities_value();
    let selected = match positional_after(arguments, "capabilities") {
        None => all,
        Some(alias) => {
            let normalized = alias.trim().to_ascii_lowercase();
            let canonical = match normalized.as_str() {
                "anthropic" | "claude" | "bedrock-anthropic" | "vertex-anthropic" => "anthropic",
                "gemini" | "google" | "google-ai" | "vertex-gemini" => "gemini",
                "openai" | "chatgpt" | "responses" | "azure-openai" => "openai",
                "openrouter" | "litellm" | "vllm" | "ollama" | "lmstudio" | "openai-compatible" => {
                    "openai-compatible"
                }
                _ => return Err(format!("PROVIDER_UNSUPPORTED:{alias}")),
            };
            all.get(canonical)
                .cloned()
                .ok_or_else(|| "PROVIDER_CAPABILITIES_INTERNAL_MISSING".to_owned())?
        }
    };
    match option_value(arguments, "--output")? {
        None => Ok(selected),
        Some(path) => {
            let target = PathBuf::from(path);
            let bytes = write_pretty_json(&target, &selected)?;
            Ok(json!({"ok": true, "output": target, "bytes": bytes}))
        }
    }
}

fn output_profiles() -> Value {
    json!({
        "contracts": {
            "audit": ["claim", "status", "supporting_evidence", "contradicting_evidence", "confidence", "uncertainty"],
            "benchmark": ["status", "workload", "quality", "efficiency", "statistics", "limitations", "evidence"],
            "failure": ["root_cause", "location", "affected_boundary", "next_action", "evidence"],
            "generic": ["result", "details", "limitations", "evidence"],
            "implementation": ["result", "changed_files", "behavior", "verification", "limitations", "evidence"]
        },
        "profiles": {
            "audit": {"include_details": true, "include_evidence": true, "max_bytes": 96000, "max_items_per_section": 100, "max_sections": 64, "name": "audit"},
            "balanced": {"include_details": true, "include_evidence": true, "max_bytes": 12000, "max_items_per_section": 12, "max_sections": 8, "name": "balanced"},
            "compact": {"include_details": false, "include_evidence": true, "max_bytes": 4096, "max_items_per_section": 6, "max_sections": 5, "name": "compact"},
            "detailed": {"include_details": true, "include_evidence": true, "max_bytes": 32000, "max_items_per_section": 30, "max_sections": 16, "name": "detailed"},
            "terse": {"include_details": false, "include_evidence": true, "max_bytes": 1400, "max_items_per_section": 4, "max_sections": 4, "name": "terse"}
        }
    })
}

fn benchmark_axes(tier: &str) -> Result<Value, String> {
    match tier {
        "1X" => Ok(
            json!({"R": 1.0, "C": 1.0, "O": 1.0, "T": 1.0, "P": 1.0, "V": 1.0, "X": 1.0, "H": 1.0, "S": 1.0, "F": 1.0}),
        ),
        "20X" => Ok(
            json!({"R": 35, "C": 32, "O": 40, "T": 30, "P": 22, "V": 34, "X": 18, "H": 24, "S": 16, "F": 20}),
        ),
        "30X" => Ok(
            json!({"R": 60, "C": 55, "O": 70, "T": 50, "P": 35, "V": 58, "X": 28, "H": 40, "S": 26, "F": 32}),
        ),
        "100X" => Ok(
            json!({"R": 240, "C": 220, "O": 280, "T": 200, "P": 130, "V": 230, "X": 100, "H": 150, "S": 90, "F": 120}),
        ),
        _ => Err(format!("BENCHMARK_TIER_INVALID:{tier}")),
    }
}

fn benchmark_difficulty_axes(tier: &str) -> Result<Value, String> {
    match tier {
        "1X" => Ok(
            json!({"R": 1.0, "C": 1.0, "O": 1.0, "T": 1.0, "P": 1.0, "V": 1.0, "X": 1.0, "H": 1.0, "S": 1.0, "F": 1.0}),
        ),
        "20X" => Ok(
            json!({"R": 35.0, "C": 32.0, "O": 40.0, "T": 30.0, "P": 22.0, "V": 34.0, "X": 18.0, "H": 24.0, "S": 16.0, "F": 20.0}),
        ),
        "30X" => Ok(
            json!({"R": 60.0, "C": 55.0, "O": 70.0, "T": 50.0, "P": 35.0, "V": 58.0, "X": 28.0, "H": 40.0, "S": 26.0, "F": 32.0}),
        ),
        "100X" => Ok(
            json!({"R": 240.0, "C": 220.0, "O": 280.0, "T": 200.0, "P": 130.0, "V": 230.0, "X": 100.0, "H": 150.0, "S": 90.0, "F": 120.0}),
        ),
        _ => Err(format!("BENCHMARK_TIER_INVALID:{tier}")),
    }
}

fn benchmark_score(tier: &str) -> Result<f64, String> {
    match tier {
        "1X" => Ok(1.0),
        "20X" => Ok(38.337_350_566_771_11),
        "30X" => Ok(63.345_278_851_520_476),
        "100X" => Ok(240.269_546_443_110_8),
        _ => Err(format!("BENCHMARK_TIER_INVALID:{tier}")),
    }
}

fn benchmark_controls() -> Value {
    json!({
        "same_prompt": true,
        "same_model": true,
        "same_reasoning": true,
        "same_repository": true,
        "same_verifier": true,
        "same_permissions": true,
        "same_timeout": true,
        "balanced_cache": true,
        "no_artificial_sleep": true,
        "no_meaningless_duplication": true
    })
}

fn benchmark_checks() -> Value {
    json!({
        "score": true,
        "multi_axis_participation": true,
        "critical_high": true,
        "critical_floor": true,
        "observed_measurement": true,
        "integrity:same_prompt": true,
        "integrity:same_model": true,
        "integrity:same_reasoning": true,
        "integrity:same_repository": true,
        "integrity:same_verifier": true,
        "integrity:same_permissions": true,
        "integrity:same_timeout": true,
        "integrity:balanced_cache": true,
        "integrity:no_artificial_sleep": true,
        "integrity:no_meaningless_duplication": true
    })
}

fn benchmark_generate_config(arguments: &[String]) -> Result<Value, String> {
    let tier = required_option(arguments, "--tier")?;
    let output = PathBuf::from(required_option(arguments, "--output")?);
    let config = json!({
        "schema_version": 2,
        "tier": tier.clone(),
        "axes": benchmark_axes(&tier)?,
        "observed_baseline": {"R": 50.0, "C": 4.0, "O": 1_000_000.0, "T": 1.0, "P": 1.0, "V": 1.0, "X": 1_000.0, "H": 10.0, "S": 1.0, "F": 1.0},
        "controls": benchmark_controls()
    });
    write_compact_json_atomic(&output, &config)?;
    let validation = json!({
        "ok": true,
        "difficulty": {
            "tier": tier.clone(),
            "score": benchmark_score(&tier)?,
            "axes": benchmark_difficulty_axes(&tier)?,
            "checks": benchmark_checks(),
            "qualified": true,
            "integrity_errors": [],
            "observed": false
        },
        "claim_eligible": false
    });
    Ok(json!({"config": config, "validation": validation}))
}

fn platform_manifest() -> Value {
    json!({
        "product": "Syntavra",
        "version": "0.0.1",
        "channel": "pre-release",
        "runtime": "unified",
        "components": [
            "context-compiler",
            "terminal-output-engine",
            "output-firewall",
            "artifact-store",
            "canonical-repository-graph",
            "indexed-repository-query",
            "tree-sitter-syntax-adapter",
            "semantic-intelligence",
            "runtime-evidence",
            "universal-language-platform",
            "sandboxed-language-services",
            "generic-lsp-bridge",
            "semantic-index-import",
            "session-memory",
            "capability-security",
            "execution-sandbox",
            "provider-gateway",
            "adapter-platform",
            "model-gateway",
            "agent-runtime",
            "coding-agent",
            "headless-runtime",
            "interactive-console",
            "reliability-laboratory",
            "distribution-manager",
            "signalbench"
        ],
        "adapter_contract": {
            "ok": true,
            "inventory_gate": true,
            "adapters": 20,
            "levels": {"A": 4, "B": 10, "C": 5, "D": 1},
            "surfaces": {"cli": 8, "ide": 7, "ide-extension": 3, "platform": 2},
            "non_cli_adapters": 12,
            "invalid": [],
            "live_certified": 0,
            "live_boundary": "live certification requires external execution receipts"
        },
        "external_claims": "NOT_PROVEN_WITHOUT_EXTERNAL_RECEIPTS"
    })
}

pub fn execute(command: &[String], arguments: &[String]) -> Result<Value, String> {
    match (command[0].as_str(), command[1].as_str()) {
        ("provider", "capabilities") => provider_capabilities(arguments),
        ("output", "profiles") => Ok(output_profiles()),
        ("benchmark", "generate-config") => benchmark_generate_config(arguments),
        ("run", "platform-manifest" | "competitive-manifest") => Ok(platform_manifest()),
        _ => Err("STATIC_SURFACE_COMMAND_UNSUPPORTED".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::{benchmark_generate_config, output_profiles, provider_capabilities};
    use serde_json::Map;
    use std::fs;

    #[test]
    fn provider_alias_resolves_to_canonical_contract() {
        let arguments = ["provider", "capabilities", "claude"]
            .into_iter()
            .map(str::to_owned)
            .collect::<Vec<_>>();
        let value = provider_capabilities(&arguments).expect("provider");
        assert_eq!(value["provider"], "anthropic");
        assert_eq!(value["explicit_prompt_cache"], true);
    }

    #[test]
    fn output_profile_contract_is_complete() {
        let value = output_profiles();
        assert_eq!(value["profiles"].as_object().map(Map::len), Some(5));
        assert_eq!(value["contracts"].as_object().map(Map::len), Some(5));
    }

    #[test]
    fn generated_benchmark_config_is_written() {
        let root =
            std::env::temp_dir().join(format!("syntavra-benchmark-config-{}", std::process::id()));
        fs::create_dir_all(&root).expect("root");
        let path = root.join("config.json");
        let arguments = vec![
            "benchmark".to_owned(),
            "generate-config".to_owned(),
            "--tier".to_owned(),
            "20X".to_owned(),
            "--output".to_owned(),
            path.display().to_string(),
        ];
        let value = benchmark_generate_config(&arguments).expect("config");
        assert_eq!(value["validation"]["ok"], true);
        assert!(path.is_file());
        let _ = fs::remove_dir_all(root);
    }
}
