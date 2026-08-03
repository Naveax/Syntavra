#![forbid(unsafe_code)]

use std::path::Path;

use serde_json::Value;

#[path = "native_migrations.rs"]
mod native_migrations;
#[path = "native_stats.rs"]
mod native_stats;

pub fn supports(command: &[String]) -> bool {
    native_migrations::supports(command) || native_stats::supports(command)
}

pub fn execute(
    command: &[String],
    arguments: &[String],
    _project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    if native_migrations::supports(command) {
        return native_migrations::execute(command, arguments);
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
        assert!(!supports(&["run".to_owned(), "unknown".to_owned()]));
    }
}
