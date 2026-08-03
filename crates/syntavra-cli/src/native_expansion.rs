#![forbid(unsafe_code)]

use std::path::Path;

use serde_json::Value;

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
#[path = "native_session_list.rs"]
mod native_session_list;
#[path = "native_session_verify.rs"]
mod native_session_verify;
#[path = "native_stats.rs"]
mod native_stats;
#[path = "native_verifier.rs"]
mod native_verifier;

pub fn supports(command: &[String]) -> bool {
    native_claim::supports(command)
        || native_evidence_describe::supports(command)
        || native_evidence_gc::supports(command)
        || native_evidence_stats::supports(command)
        || native_job_queries::supports(command)
        || native_migrations::supports(command)
        || native_scheduler_reap::supports(command)
        || native_session_list::supports(command)
        || native_session_verify::supports(command)
        || native_stats::supports(command)
        || native_verifier::supports(command)
}

pub fn execute(
    command: &[String],
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
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
    if native_session_list::supports(command) {
        return native_session_list::execute(arguments, project_root, state_root);
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
        assert!(supports(&["session".to_owned(), "list".to_owned()]));
        assert!(supports(&["session".to_owned(), "verify".to_owned()]));
        assert!(supports(&["verifier".to_owned(), "lookup".to_owned()]));
        assert!(supports(&["verifier".to_owned(), "invalidated-by".to_owned()]));
        assert!(!supports(&["run".to_owned(), "unknown".to_owned()]));
    }
}
