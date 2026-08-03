#![forbid(unsafe_code)]

use std::fs;
use std::path::Path;

use serde_json::Value;

#[path = "config_contract.rs"]
pub mod config_contract;
#[path = "native_product_legacy.rs"]
mod legacy;
#[path = "migration_plan_read_only_contract.rs"]
mod migration_plan_read_only_contract;
#[path = "native_audit_config.rs"]
mod native_audit_config;
#[path = "native_cache_amortize.rs"]
mod native_cache_amortize;
#[path = "native_config_read_only.rs"]
mod native_config_read_only;
#[path = "native_context_stress.rs"]
mod native_context_stress;
#[path = "native_external_suite_gate.rs"]
mod native_external_suite_gate;
#[path = "native_external_suites.rs"]
mod native_external_suites;
#[path = "native_integrations.rs"]
mod native_integrations;
#[path = "native_long_context.rs"]
mod native_long_context;
#[path = "native_manifest.rs"]
mod native_manifest;
#[path = "native_mode.rs"]
mod native_mode;
#[path = "native_proof_evidence.rs"]
mod native_proof_evidence;
#[path = "native_proof_maturity.rs"]
mod native_proof_maturity;
#[path = "native_proof_status.rs"]
mod native_proof_status;
#[path = "native_prove_plan.rs"]
mod native_prove_plan;
#[path = "native_prove_schema.rs"]
mod native_prove_schema;
#[path = "native_proxy_plan.rs"]
mod native_proxy_plan;
#[path = "native_read_only_product.rs"]
mod native_read_only_product;
#[path = "native_redact.rs"]
mod native_redact;
#[path = "native_route.rs"]
mod native_route;
#[path = "native_semantic_demo.rs"]
mod native_semantic_demo;
#[path = "native_signalbench_gate.rs"]
mod native_signalbench_gate;
#[path = "native_signalbench_plan.rs"]
mod native_signalbench_plan;
#[path = "native_statusline.rs"]
mod native_statusline;
#[path = "native_upgrade.rs"]
mod native_upgrade;
#[path = "native_wire.rs"]
mod native_wire;
#[path = "native_wrap.rs"]
mod native_wrap;
#[path = "read_only_cli_contract.rs"]
mod read_only_cli_contract;
#[path = "scheduler_read_only_contract.rs"]
mod scheduler_read_only_contract;
#[path = "telemetry_metrics_contract.rs"]
mod telemetry_metrics_contract;

pub fn supports(command: &[String]) -> bool {
    native_config_read_only::supports(command)
        || native_audit_config::supports(command)
        || native_cache_amortize::supports(command)
        || native_context_stress::supports(command)
        || native_external_suite_gate::supports(command)
        || native_external_suites::supports(command)
        || native_integrations::supports(command)
        || native_long_context::supports(command)
        || native_manifest::supports(command)
        || native_mode::supports(command)
        || native_proof_evidence::supports(command)
        || native_proof_maturity::supports(command)
        || native_proof_status::supports(command)
        || native_prove_plan::supports(command)
        || native_prove_schema::supports(command)
        || native_proxy_plan::supports(command)
        || native_read_only_product::supports(command)
        || native_redact::supports(command)
        || native_route::supports(command)
        || native_semantic_demo::supports(command)
        || native_signalbench_gate::supports(command)
        || native_signalbench_plan::supports(command)
        || native_statusline::supports(command)
        || native_upgrade::supports(command)
        || native_wire::supports(command)
        || native_wrap::supports(command)
        || legacy::supports(command)
}

pub fn execute(
    command: &[String],
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<(Value, i32), String> {
    if native_config_read_only::supports(command) {
        return native_config_read_only::execute(command, arguments, project_root)
            .map(|value| (value, 0));
    }
    if native_audit_config::supports(command) {
        return native_audit_config::execute(arguments, project_root).map(|value| (value, 0));
    }
    if native_cache_amortize::supports(command) {
        return native_cache_amortize::execute(arguments).map(|value| (value, 0));
    }
    if native_context_stress::supports(command) {
        return native_context_stress::execute(arguments).map(|value| (value, 0));
    }
    if native_external_suite_gate::supports(command) {
        return native_external_suite_gate::execute(arguments);
    }
    if native_external_suites::supports(command) {
        return native_external_suites::execute(command, arguments).map(|value| (value, 0));
    }
    if native_integrations::supports(command) {
        return native_integrations::execute(arguments).map(|value| (value, 0));
    }
    if native_long_context::supports(command) {
        return native_long_context::execute(command, arguments).map(|value| (value, 0));
    }
    if native_manifest::supports(command) {
        return native_manifest::execute(project_root).map(|value| (value, 0));
    }
    if native_mode::supports(command) {
        return native_mode::execute(arguments, state_root).map(|value| (value, 0));
    }
    if native_proof_evidence::supports(command) {
        return native_proof_evidence::execute(command, arguments).map(|value| (value, 0));
    }
    if native_proof_maturity::supports(command) {
        return native_proof_maturity::execute(arguments);
    }
    if native_proof_status::supports(command) {
        return native_proof_status::execute(command).map(|value| (value, 0));
    }
    if native_prove_plan::supports(command) {
        return native_prove_plan::execute(command).map(|value| (value, 0));
    }
    if native_prove_schema::supports(command) {
        return native_prove_schema::execute(arguments).map(|value| (value, 0));
    }
    if native_proxy_plan::supports(command) {
        return native_proxy_plan::execute(arguments);
    }
    if native_read_only_product::supports(command) {
        return native_read_only_product::execute(command).map(|value| (value, 0));
    }
    if native_redact::supports(command) {
        return native_redact::execute(arguments).map(|value| (value, 0));
    }
    if native_route::supports(command) {
        return native_route::execute(arguments);
    }
    if native_semantic_demo::supports(command) {
        return native_semantic_demo::execute(command, arguments).map(|value| (value, 0));
    }
    if native_signalbench_gate::supports(command) {
        return native_signalbench_gate::execute(command, arguments);
    }
    if native_signalbench_plan::supports(command) {
        return native_signalbench_plan::execute(command, arguments).map(|value| (value, 0));
    }
    if native_statusline::supports(command) {
        return native_statusline::execute(arguments, state_root).map(|value| (value, 0));
    }
    if native_upgrade::supports(command) {
        return native_upgrade::execute(arguments).map(|value| (value, 0));
    }
    if native_wire::supports(command) {
        return native_wire::execute(arguments).map(|value| (value, 0));
    }
    if native_wrap::supports(command) {
        return native_wrap::execute(arguments, state_root).map(|value| (value, 0));
    }
    legacy::execute(command, arguments, project_root, state_root)
}

pub fn normalize_newlines(value: &str) -> String {
    value.replace("\r\n", "\n").replace('\r', "\n")
}

pub fn read_text(path: &Path) -> Result<String, String> {
    fs::read_to_string(path)
        .map(|value| normalize_newlines(&value))
        .map_err(|error| format!("NATIVE_PRODUCT_READ_FAILED:{error}"))
}

#[cfg(test)]
mod tests {
    use super::normalize_newlines;

    #[test]
    fn normalizes_windows_and_legacy_newlines() {
        assert_eq!(normalize_newlines("a\r\nb\rc\n"), "a\nb\nc\n");
    }
}
