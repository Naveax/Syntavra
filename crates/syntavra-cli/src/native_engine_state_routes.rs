#![forbid(unsafe_code)]

use std::path::Path;

use serde_json::{json, Value};

const PHASE: &str = "R24";
const SCHEMA_VERSION: u64 = 12;
const MAX_FILE_BYTES: u64 = 1024 * 1024;

pub fn supports(command: &[String]) -> bool {
    matches!(command, [engine, route, name]
        if engine == "engine" && route == "route" && name == "state.inspect")
}

fn selection() -> Value {
    json!({
        "auto_policy": "python",
        "fallback_policy": "fail-closed",
        "reason": "EXPLICIT_SELECTION",
        "requested": "rust",
        "resolved": "rust",
        "schema_version": 1,
        "scope": "command",
        "source": "--engine",
        "source_path": "",
    })
}

pub fn execute(project_root: &Path) -> Result<Value, String> {
    let project = project_root.to_string_lossy();
    let project_id = super::state_snapshot_contract::project_id_for_root(&project)?;
    let rendered = super::state_snapshot_contract::inspect_state_root_json(
        &project,
        &project_id,
    )?;
    let result: Value = serde_json::from_str(&rendered)
        .map_err(|_| "ENGINE_ROUTE_STATE_INSPECT_RESULT_INVALID".to_owned())?;
    Ok(json!({
        "ok": true,
        "phase": PHASE,
        "schema_version": SCHEMA_VERSION,
        "command": "state.inspect",
        "capability": "state.inspect",
        "mutation": "read-only",
        "selection": selection(),
        "input": {
            "profile": "project-bound-state-root-v1",
            "format": "sha256-normalized-absolute-path-v1",
            "bytes": 32,
            "sha256": project_id,
        },
        "fallback": {"policy": "none", "attempted": false},
        "result": result,
        "limits": {"maximum_file_bytes": MAX_FILE_BYTES},
    }))
}

#[cfg(test)]
mod tests {
    use super::{execute, supports};
    use std::path::Path;

    #[test]
    fn recognizes_only_state_inspect() {
        let command = ["engine", "route", "state.inspect"]
            .into_iter()
            .map(str::to_owned)
            .collect::<Vec<_>>();
        assert!(supports(&command));
    }

    #[test]
    fn current_directory_is_inspected_without_mutation() {
        let value = execute(Path::new(".")).expect("state inspection");
        assert_eq!(value["command"], "state.inspect");
        assert_eq!(value["result"]["mutation"]["filesystem"], false);
    }
}
