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
#[path = "native_job_queries.rs"]
mod native_job_queries;
#[path = "native_migrations.rs"]
mod native_migrations;
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
#[path = "native_verifier.rs"]
mod native_verifier;

pub fn supports(command: &[String]) -> bool {
    native_benchmark_tools::supports(command)
        || native_claim::supports(command)
        || native_evidence_describe::supports(command)
        || native_evidence_gc::supports(command)
        || native_evidence_stats::supports(command)
        || native_job_queries::supports(command)
        || native_migrations::supports(command)
        || native_scheduler_reap::supports(command)
        || native_session_archive::supports(command)
        || native_session_context::supports(command)
        || native_session_lifecycle::supports(command)
        || native_session_list::supports(command)
        || native_session_public::supports(command)
        || native_session_recover::supports(command)
        || native_session_verify::supports(command)
        || native_stats::supports(command)
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
    if native_job_queries::supports(command) {
        return native_job_queries::execute(command, arguments, state_root);
    }
    if native_migrations::supports(command) {
        return native_migrations::execute(command, arguments);
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
        return native_stats::execute(state_root);
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
    fn routes_expansion_commands() {
        assert!(supports(&[
            "benchmark".to_owned(),
            "generate-repo".to_owned(),
        ]));
        assert!(supports(&[
            "benchmark".to_owned(),
            "validate-config".to_owned(),
        ]));
        assert!(supports(&["claim".to_owned()]));
        assert!(supports(&["stats".to_owned()]));
        assert!(supports(&["migrate".to_owned(), "apply".to_owned()]));
        assert!(supports(&["scheduler".to_owned(), "reap".to_owned()]));
        assert!(supports(&["maintenance".to_owned(), "janitor".to_owned()]));
        assert!(supports(&["evidence".to_owned(), "describe".to_owned()]));
        assert!(supports(&["evidence".to_owned(), "gc".to_owned()]));
        assert!(supports(&["evidence".to_owned(), "stats".to_owned()]));
        assert!(supports(&["run".to_owned(), "evidence-stats".to_owned()]));
        assert!(supports(&["run".to_owned(), "evidence-neighbors".to_owned()]));
        assert!(supports(&["job".to_owned(), "list".to_owned()]));
        assert!(supports(&["job".to_owned(), "show".to_owned()]));
        assert!(supports(&["job".to_owned(), "completions".to_owned()]));
        assert!(supports(&["session".to_owned(), "append".to_owned()]));
        assert!(supports(&["session".to_owned(), "checkpoint".to_owned()]));
        assert!(supports(&["session".to_owned(), "close".to_owned()]));
        assert!(supports(&["session".to_owned(), "compact".to_owned()]));
        assert!(supports(&["session".to_owned(), "context".to_owned()]));
        assert!(supports(&["session".to_owned(), "export".to_owned()]));
        assert!(supports(&["session".to_owned(), "fork".to_owned()]));
        assert!(supports(&["session".to_owned(), "import".to_owned()]));
        assert!(supports(&["session".to_owned(), "list".to_owned()]));
        assert!(supports(&["session".to_owned(), "merge".to_owned()]));
        assert!(supports(&["session".to_owned(), "open".to_owned()]));
        assert!(supports(&["session".to_owned(), "recover".to_owned()]));
        assert!(supports(&["session".to_owned(), "verify".to_owned()]));
        assert!(supports(&["verifier".to_owned(), "lookup".to_owned()]));
        assert!(supports(&["verifier".to_owned(), "invalidated-by".to_owned()]));
        assert!(!supports(&["run".to_owned(), "unknown".to_owned()]));
    }
}
