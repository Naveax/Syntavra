#![forbid(unsafe_code)]

use std::path::Path;

use serde_json::Value;

#[allow(clippy::pedantic)]
#[path = "native_benchmark_tools.rs"]
mod native_benchmark_tools;
#[path = "native_claim.rs"]
mod native_claim;
#[path = "native_evidence_describe.rs"]
mod native_evidence_describe;
#[path = "native_evidence_gc.rs"]
mod native_evidence_gc;
#[path = "native_evidence_stats.rs"]
mod native_evidence_stats;
#[allow(clippy::pedantic)]
#[path = "native_host.rs"]
mod native_host;
#[allow(clippy::pedantic)]
#[path = "native_job_mutations.rs"]
mod native_job_mutations;
#[path = "native_job_queries.rs"]
mod native_job_queries;
#[allow(clippy::pedantic)]
#[path = "native_memory.rs"]
mod native_memory;
#[path = "native_migrations.rs"]
mod native_migrations;
#[path = "native_operator_lifecycle.rs"]
mod native_operator_lifecycle;
#[allow(clippy::pedantic)]
#[path = "native_output_governor.rs"]
mod native_output_governor;
#[allow(clippy::pedantic)]
#[path = "native_rollout_tail.rs"]
mod native_rollout_tail;
#[path = "native_scheduler_reap.rs"]
mod native_scheduler_reap;
#[allow(clippy::pedantic, unused_imports)]
#[path = "native_session_archive.rs"]
mod native_session_archive;
#[allow(dead_code)]
#[path = "native_session_context.rs"]
mod native_session_context;
#[path = "native_session_lifecycle.rs"]
mod native_session_lifecycle;
#[path = "native_session_list.rs"]
mod native_session_list;
#[allow(clippy::pedantic, dead_code)]
#[path = "native_session_public.rs"]
mod native_session_public;
#[path = "native_session_recover.rs"]
mod native_session_recover;
#[path = "native_session_verify.rs"]
mod native_session_verify;
#[path = "native_stats.rs"]
mod native_stats;
#[allow(clippy::pedantic)]
#[path = "native_structural.rs"]
mod native_structural;
#[path = "native_telemetry_bundle.rs"]
mod native_telemetry_bundle;
#[path = "native_uninstall.rs"]
mod native_uninstall;
#[path = "native_verifier.rs"]
mod native_verifier;

pub fn supports(command: &[String]) -> bool {
    native_benchmark_tools::supports(command)
        || native_claim::supports(command)
        || native_evidence_describe::supports(command)
        || native_evidence_gc::supports(command)
        || native_evidence_stats::supports(command)
        || native_host::supports(command)
        || native_job_mutations::supports(command)
        || native_job_queries::supports(command)
        || native_memory::supports(command)
        || native_migrations::supports(command)
        || native_operator_lifecycle::supports(command)
        || native_output_governor::supports(command)
        || native_rollout_tail::supports(command)
        || native_scheduler_reap::supports(command)
        || native_session_archive::supports(command)
        || native_session_context::supports(command)
        || native_session_lifecycle::supports(command)
        || native_session_list::supports(command)
        || native_session_public::supports(command)
        || native_session_recover::supports(command)
        || native_session_verify::supports(command)
        || native_stats::supports(command)
        || native_structural::supports(command)
        || native_telemetry_bundle::supports(command)
        || native_uninstall::supports(command)
        || native_verifier::supports(command)
}

fn emit_failed_value(value: &Value, exit_code: u8) -> ! {
    println!(
        "{}",
        serde_json::to_string_pretty(value).unwrap_or_else(|_| "{\"ok\":false}".to_owned())
    );
    std::process::exit(i32::from(exit_code));
}

pub fn execute(
    command: &[String],
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    if native_benchmark_tools::supports(command) {
        let value = native_benchmark_tools::execute(command, arguments)?;
        if matches!(command, [root, action] if root == "benchmark" && action == "validate-config")
            && value.get("ok").and_then(Value::as_bool) == Some(false)
        {
            emit_failed_value(&value, 3);
        }
        return Ok(value);
    }
    if native_claim::supports(command) {
        return native_claim::execute(arguments);
    }
    if native_evidence_describe::supports(command) {
        return native_evidence_describe::execute(arguments, project_root, state_root);
    }
    if native_evidence_gc::supports(command) {
        return native_evidence_gc::execute(command, arguments, state_root);
    }
    if native_evidence_stats::supports(command) {
        return native_evidence_stats::execute(command, arguments, state_root);
    }
    if native_host::supports(command) {
        return native_host::execute(command, arguments, project_root);
    }
    if native_job_mutations::supports(command) {
        return native_job_mutations::execute(command, arguments, state_root);
    }
    if native_job_queries::supports(command) {
        return native_job_queries::execute(command, arguments, state_root);
    }
    if native_memory::supports(command) {
        return native_memory::execute(command, arguments, project_root, state_root);
    }
    if native_migrations::supports(command) {
        return native_migrations::execute(command, arguments);
    }
    if native_operator_lifecycle::supports(command) {
        let decision = native_operator_lifecycle::execute(command, project_root, state_root)?;
        if decision.exit_code != 0 {
            emit_failed_value(&decision.value, decision.exit_code);
        }
        return Ok(decision.value);
    }
    if native_output_governor::supports(command) {
        return native_output_governor::execute(command, arguments);
    }
    if native_rollout_tail::supports(command) {
        let value = native_rollout_tail::execute(arguments, state_root)?;
        if value.get("ok").and_then(Value::as_bool) == Some(false) {
            emit_failed_value(&value, 2);
        }
        return Ok(value);
    }
    if native_scheduler_reap::supports(command) {
        return native_scheduler_reap::execute(state_root);
    }
    if native_session_archive::supports(command) {
        return native_session_archive::execute(command, arguments, project_root, state_root);
    }
    if native_session_context::supports(command) {
        return native_session_context::execute(arguments, project_root, state_root);
    }
    if native_session_lifecycle::supports(command) {
        return native_session_lifecycle::execute(command, arguments, project_root, state_root);
    }
    if native_session_list::supports(command) {
        return native_session_list::execute(arguments, project_root, state_root);
    }
    if native_session_public::supports(command) {
        return native_session_public::execute(command, arguments, project_root, state_root);
    }
    if native_session_recover::supports(command) {
        return native_session_recover::execute(project_root, state_root);
    }
    if native_session_verify::supports(command) {
        return native_session_verify::execute(arguments, project_root, state_root);
    }
    if native_stats::supports(command) {
        return native_stats::execute(project_root, state_root);
    }
    if native_structural::supports(command) {
        return native_structural::execute(command, arguments, project_root, state_root);
    }
    if native_telemetry_bundle::supports(command) {
        return native_telemetry_bundle::execute(arguments, state_root);
    }
    if native_uninstall::supports(command) {
        return native_uninstall::execute(arguments, project_root);
    }
    if native_verifier::supports(command) {
        return native_verifier::execute(command, arguments, state_root);
    }
    Err("NATIVE_EXPANSION_COMMAND_UNSUPPORTED".to_owned())
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn routes_recent_expansion_commands() {
        for command in [
            vec!["doctor"],
            vec!["host"],
            vec!["host", "negotiate"],
            vec!["host", "detect"],
            vec!["host", "capabilities"],
            vec!["inspect", "symbol"],
            vec!["inspect", "impact"],
            vec!["inspect", "paths"],
            vec!["inspect", "map"],
            vec!["inspect", "stats"],
            vec!["job", "cancel"],
            vec!["memory", "add"],
            vec!["memory", "search"],
            vec!["memory", "link"],
            vec!["memory", "neighbors"],
            vec!["output", "compact"],
            vec!["output", "govern"],
            vec!["rollout-tail"],
            vec!["telemetry", "bundle"],
            vec!["session", "import"],
            vec!["uninstall"],
            vec!["verifier", "lookup"],
        ] {
            assert!(supports(
                &command.into_iter().map(str::to_owned).collect::<Vec<_>>()
            ));
        }
        assert!(!supports(&["run".to_owned(), "unknown".to_owned()]));
    }
}
