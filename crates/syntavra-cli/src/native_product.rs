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
#[path = "native_analytics.rs"]
mod native_analytics;
#[path = "native_audit_config.rs"]
mod native_audit_config;
#[path = "native_cache_amortize.rs"]
mod native_cache_amortize;
#[path = "native_config_read_only.rs"]
mod native_config_read_only;
#[path = "native_context_governor.rs"]
mod native_context_governor;
#[path = "native_context_stress.rs"]
mod native_context_stress;
#[path = "native_engine_routes.rs"]
mod native_engine_routes;
#[path = "native_engine_state_routes.rs"]
mod native_engine_state_routes;
#[path = "native_expansion.rs"]
mod native_expansion;
#[path = "native_expansion.rs"]
mod native_expansion;
#[path = "native_expansion.rs"]
mod native_expansion;
#[path = "native_expansion.rs"]
mod native_expansion;
#[path = "native_expansion.rs"]
mod native_expansion;
#[path = "native_expansion.rs"]
mod native_expansion;
#[path = "native_expansion.rs"]
mod native_expansion;
#[path = "native_expansion.rs"]
mod native_expansion;
#[path = "native_expansion.rs"]
mod native_expansion;
#[path = "native_expansion.rs"]
mod native_expansion;
#[path = "native_expansion.rs"]
mod native_expansion;
#[path = "native_external_suite_gate.rs"]
mod native_external_suite_gate;
#[path = "native_external_suites.rs"]
mod native_external_suites;
#[path = "native_integrations.rs"]
mod native_integrations;
#[path = "native_live_config.rs"]
mod native_live_config;
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
#[path = "native_rust_subprocess.rs"]
mod native_rust_subprocess;
#[path = "native_semantic_demo.rs"]
mod native_semantic_demo;
#[path = "native_session_continuity.rs"]
mod native_session_continuity;
#[path = "native_session_mutations.rs"]
mod native_session_mutations;
#[path = "native_session_status.rs"]
mod native_session_status;
#[path = "native_signalbench_gate.rs"]
mod native_signalbench_gate;
#[path = "native_signalbench_plan.rs"]
mod native_signalbench_plan;
#[path = "native_static_surfaces.rs"]
mod native_static_surfaces;
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
#[path = "state_layout_contract.rs"]
mod state_layout_contract;
#[path = "state_snapshot_contract.rs"]
mod state_snapshot_contract;
#[path = "telemetry_metrics_contract.rs"]
mod telemetry_metrics_contract;

pub fn supports(command: &[String]) -> bool {
    native_analytics::supports(command)
        || native_config_read_only::supports(command)
        || native_context_governor::supports(command)
        || native_read_only_product::supports(command)
        || native_engine_routes::supports(command)
        || native_engine_state_routes::supports(command)
        || native_expansion::supports(command)
        || native_session_continuity::supports(command)
        || native_session_mutations::supports(command)
        || native_session_status::supports(command)
        || native_static_surfaces::supports(command)
        || legacy::supports(command)
        || command.first().is_some_and(|item| item == "wrap")
        || (command.len() == 1 && command[0] == "context-stress")
        || command
            .first()
            .is_some_and(|item| matches!(item.as_str(), "integrations" | "upgrade"))
        || (command.len() == 2
            && matches!(command[0].as_str(), "semantic-demo" | "structural-v2")
            && command[1] == "demo")
        || (command.len() == 2
            && matches!(command[0].as_str(), "signalbench" | "signalbench2")
            && matches!(command[1].as_str(), "plan" | "gate"))
        || (command.len() == 2
            && command[0] == "run"
            && matches!(
                command[1].as_str(),
                "audit-config" | "cache-amortize" | "manifest"
            ))
        || (command.len() == 2 && command[0] == "run" && command[1] == "mode")
        || (command.len() == 2 && command[0] == "run" && command[1] == "proxy-plan")
        || (command.len() == 2 && command[0] == "run" && command[1] == "redact")
        || (command.len() == 2 && command[0] == "run" && command[1] == "route")
        || (command.len() == 2 && command[0] == "run" && command[1] == "statusline")
        || (command.len() == 2 && command[0] == "run" && command[1] == "wire")
        || (command.len() == 2 && command[0] == "proof" && command[1] == "status")
        || (command.len() == 2
            && command[0] == "prove"
            && matches!(
                command[1].as_str(),
                "benchmark"
                    | "external-suite"
                    | "long-context"
                    | "maturity"
                    | "plan"
                    | "provider-billed"
                    | "readiness"
                    | "receipts"
                    | "schema"
                    | "suites"
            ))
}

fn normalize_universal_newlines(text: &str) -> String {
    if !text.contains('\r') {
        return text.to_owned();
    }
    let mut normalized = String::with_capacity(text.len());
    let mut characters = text.chars().peekable();
    while let Some(character) = characters.next() {
        if character == '\r' {
            if characters.peek() == Some(&'\n') {
                characters.next();
            }
            normalized.push('\n');
        } else {
            normalized.push(character);
        }
    }
    normalized
}

fn normalized_redact_arguments(arguments: &[String]) -> Result<Vec<String>, String> {
    let mut normalized = arguments.to_vec();
    let source_index = normalized
        .windows(2)
        .position(|window| window[0] == "run" && window[1] == "redact")
        .and_then(|index| index.checked_add(2))
        .ok_or_else(|| "REDACT_SOURCE_MISSING".to_owned())?;
    let source = normalized
        .get(source_index)
        .ok_or_else(|| "REDACT_SOURCE_MISSING".to_owned())?;
    let path = Path::new(source);
    if path.is_file() {
        let raw = fs::read_to_string(path)
            .map_err(|error| format!("REDACT_SOURCE_READ_FAILED:{error}"))?;
        normalized[source_index] = normalize_universal_newlines(&raw);
    }
    Ok(normalized)
}

fn execute_static_run(
    command: &[String],
    arguments: &[String],
    project_root: &Path,
) -> Result<Option<Value>, String> {
    if command.len() != 2 || command[0] != "run" {
        return Ok(None);
    }
    match command[1].as_str() {
        "audit-config" => native_audit_config::execute(project_root).map(Some),
        "cache-amortize" => native_cache_amortize::execute(arguments).map(Some),
        "manifest" => Ok(Some(native_manifest::execute(project_root))),
        _ => Ok(None),
    }
}

fn emit_failed_decision(value: &Value, exit_code: u8) -> ! {
    println!(
        "{}",
        serde_json::to_string_pretty(value).unwrap_or_else(|_| "{\"ok\":false}".to_owned())
    );
    std::process::exit(i32::from(exit_code));
}

fn execute_prove(
    command: &[String],
    arguments: &[String],
    state_root: &Path,
) -> Result<Option<Value>, String> {
    if command.len() != 2 || command[0] != "prove" {
        return Ok(None);
    }
    match command[1].as_str() {
        "benchmark" | "readiness" | "receipts" => {
            let decision = native_proof_evidence::execute(&command[1], arguments, state_root)?;
            if decision.exit_code != 0 {
                emit_failed_decision(&decision.value, decision.exit_code);
            }
            Ok(Some(decision.value))
        }
        "maturity" | "provider-billed" => {
            let decision = native_proof_maturity::execute(&command[1], arguments)?;
            if decision.exit_code != 0 {
                emit_failed_decision(&decision.value, decision.exit_code);
            }
            Ok(Some(decision.value))
        }
        "external-suite" => {
            let decision = native_external_suite_gate::execute(arguments)?;
            if decision.exit_code != 0 {
                emit_failed_decision(&decision.value, decision.exit_code);
            }
            Ok(Some(decision.value))
        }
        "long-context" => {
            let decision = native_long_context::execute(arguments)?;
            if decision.exit_code != 0 {
                emit_failed_decision(&decision.value, decision.exit_code);
            }
            Ok(Some(decision.value))
        }
        "plan" => Ok(Some(native_prove_plan::execute())),
        "schema" => native_prove_schema::execute(arguments).map(Some),
        "suites" => Ok(Some(native_external_suites::execute())),
        _ => Ok(None),
    }
}

pub fn execute(
    command: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Option<Value>, String> {
    let arguments = std::env::args().skip(1).collect::<Vec<_>>();
    if native_analytics::supports(command) {
        return native_analytics::execute(&arguments, state_root).map(Some);
    }
    if native_config_read_only::supports(command) {
        return native_config_read_only::execute(command, &arguments, project_root).map(Some);
    }
    if native_context_governor::supports(command) {
        return native_context_governor::execute(command, &arguments).map(Some);
    }
    if native_read_only_product::supports(command) {
        return native_read_only_product::execute(command, &arguments, project_root, state_root)
            .map(Some);
    }
    if native_engine_routes::supports(command) {
        return native_engine_routes::execute(command, &arguments, project_root, state_root)
            .map(Some);
    }
    if native_engine_state_routes::supports(command) {
        return native_engine_state_routes::execute(command, &arguments, project_root).map(Some);
    }
    if native_expansion::supports(command) {
        return native_expansion::execute(command, &arguments, project_root, state_root).map(Some);
    }
    if native_session_continuity::supports(command) {
        return native_session_continuity::execute(command, &arguments, project_root, state_root)
            .map(Some);
    }
    if native_session_mutations::supports(command) {
        return native_session_mutations::execute(command, &arguments, project_root, state_root)
            .map(Some);
    }
    if native_session_status::supports(command) {
        return native_session_status::execute(project_root, state_root).map(Some);
    }
    if native_static_surfaces::supports(command) {
        return native_static_surfaces::execute(command, &arguments).map(Some);
    }
    if command.first().is_some_and(|item| item == "wrap") {
        return native_wrap::execute(&arguments, state_root).map(Some);
    }
    if command.len() == 1 && command[0] == "context-stress" {
        let decision = native_context_stress::execute(&arguments)?;
        if decision.exit_code != 0 {
            emit_failed_decision(&decision.value, decision.exit_code);
        }
        return Ok(Some(decision.value));
    }
    if command.first().is_some_and(|item| item == "integrations") {
        return native_integrations::execute(&arguments).map(Some);
    }
    if command.first().is_some_and(|item| item == "upgrade") {
        return native_upgrade::execute(&arguments).map(Some);
    }
    if command.len() == 2
        && matches!(command[0].as_str(), "semantic-demo" | "structural-v2")
        && command[1] == "demo"
    {
        return native_semantic_demo::execute(&arguments).map(Some);
    }
    if command.len() == 2
        && matches!(command[0].as_str(), "signalbench" | "signalbench2")
        && command[1] == "plan"
    {
        return native_signalbench_plan::execute(&arguments).map(Some);
    }
    if command.len() == 2
        && matches!(command[0].as_str(), "signalbench" | "signalbench2")
        && command[1] == "gate"
    {
        let decision = native_signalbench_gate::execute(&arguments)?;
        if decision.exit_code != 0 {
            println!("{}", decision.rendered);
            std::process::exit(i32::from(decision.exit_code));
        }
        return Ok(Some(decision.value));
    }
    if let Some(value) = execute_static_run(command, &arguments, project_root)? {
        return Ok(Some(value));
    }
    if let Some(value) = execute_prove(command, &arguments, state_root)? {
        return Ok(Some(value));
    }
    if command.len() == 2 && command[0] == "run" && command[1] == "mode" {
        return native_mode::execute(&arguments, state_root).map(Some);
    }
    if command.len() == 2 && command[0] == "run" && command[1] == "proxy-plan" {
        let decision = native_proxy_plan::execute(&arguments)?;
        if decision.exit_code != 0 {
            emit_failed_decision(&decision.value, decision.exit_code);
        }
        return Ok(Some(decision.value));
    }
    if command.len() == 2 && command[0] == "run" && command[1] == "redact" {
        let normalized = normalized_redact_arguments(&arguments)?;
        return native_redact::execute(&normalized).map(Some);
    }
    if command.len() == 2 && command[0] == "run" && command[1] == "route" {
        let decision = native_route::execute(&arguments)?;
        if decision.exit_code != 0 {
            emit_failed_decision(&decision.value, decision.exit_code);
        }
        return Ok(Some(decision.value));
    }
    if command.len() == 2 && command[0] == "run" && command[1] == "statusline" {
        return native_statusline::execute(&arguments, state_root).map(Some);
    }
    if command.len() == 2 && command[0] == "run" && command[1] == "wire" {
        return native_wire::execute(&arguments).map(Some);
    }
    if command.len() == 2 && command[0] == "proof" && command[1] == "status" {
        return Ok(Some(native_proof_status::execute()));
    }
    legacy::execute(command, state_root)
}

#[cfg(test)]
mod tests {
    use super::normalize_universal_newlines;

    #[test]
    fn normalizes_windows_and_legacy_newlines() {
        assert_eq!(normalize_universal_newlines("a\r\nb\rc\n"), "a\nb\nc\n");
    }
}
