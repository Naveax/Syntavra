#![forbid(unsafe_code)]

use std::path::Path;

use serde_json::Value;

#[path = "native_evidence_stats.rs"]
mod native_evidence_stats;
#[path = "native_migrations.rs"]
mod native_migrations;
#[path = "native_scheduler_reap.rs"]
mod native_scheduler_reap;
#[path = "native_stats.rs"]
mod native_stats;

pub fn supports(command: &[String]) -> bool {
    native_evidence_stats::supports(command)
        || native_migrations::supports(command)
        || native_scheduler_reap::supports(command)
        || native_stats::supports(command)
}

pub fn execute(
    command: &[String],
    arguments: &[String],
    _project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    if native_evidence_stats::supports(command) {
        return native_evidence_stats::execute(command, state_root);
    }
    if native_migrations::supports(command) {
        return native_migrations::execute(command, arguments);
    }
    if native_scheduler_reap::supports(command) {
        return native_scheduler_reap::execute(state_root);
    }
    if native_stats::supports(command) {
        return native_stats::execute(state_root);
    }
    Err("NATIVE_EXPANSION_COMMAND_UNSUPPORTED".to_owned())
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn routes_expansion_commands() {
        assert!(supports(&["stats".to_owned()]));
        assert!(supports(&["migrate".to_owned(), "apply".to_owned()]));
        assert!(supports(&["scheduler".to_owned(), "reap".to_owned()]));
        assert!(supports(&["evidence".to_owned(), "stats".to_owned()]));
        assert!(supports(&["run".to_owned(), "evidence-stats".to_owned()]));
        assert!(!supports(&["run".to_owned(), "unknown".to_owned()]));
    }
}
