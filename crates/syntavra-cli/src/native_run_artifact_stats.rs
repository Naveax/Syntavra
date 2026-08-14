#![forbid(unsafe_code)]

use std::path::Path;

use rusqlite::Connection;
use serde_json::{json, Value};

use super::native_artifact_store::NativeArtifactStore;

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "run" && action == "artifact-stats")
}

fn validate_arguments(arguments: &[String]) -> Result<(), String> {
    let start = arguments
        .windows(2)
        .position(|row| row[0] == "run" && row[1] == "artifact-stats")
        .map(|index| index + 2)
        .ok_or_else(|| "ARTIFACT_STATS_COMMAND_MISSING".to_owned())?;
    if let Some(value) = arguments.get(start) {
        return Err(format!("ARTIFACT_STATS_ARGUMENT_UNEXPECTED:{value}"));
    }
    Ok(())
}

fn open_database(path: &Path) -> Result<Connection, String> {
    let connection = Connection::open(path)
        .map_err(|error| format!("ARTIFACT_STATS_DATABASE_OPEN_FAILED:{error}"))?;
    connection
        .execute_batch(
            "PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA synchronous=FULL; PRAGMA busy_timeout=30000;",
        )
        .map_err(|error| format!("ARTIFACT_STATS_DATABASE_PRAGMA_FAILED:{error}"))?;
    Ok(connection)
}

pub fn execute(arguments: &[String], state_root: &Path) -> Result<Value, String> {
    validate_arguments(arguments)?;
    let store = NativeArtifactStore::open(state_root)?;
    let connection = open_database(&store.root().join("artifacts.sqlite3"))?;

    let (artifacts, exact_bytes) = connection
        .query_row(
            "SELECT COUNT(*) count, COALESCE(SUM(byte_count), 0) bytes FROM artifacts",
            [],
            |row| Ok((row.get::<_, i64>(0)?, row.get::<_, i64>(1)?)),
        )
        .map_err(|error| format!("ARTIFACT_STATS_TOTALS_FAILED:{error}"))?;

    let mut statement = connection
        .prepare(
            "SELECT kind, COUNT(*) count, SUM(byte_count) bytes FROM artifacts GROUP BY kind ORDER BY kind",
        )
        .map_err(|error| format!("ARTIFACT_STATS_KINDS_PREPARE_FAILED:{error}"))?;
    let rows = statement
        .query_map([], |row| {
            Ok(json!({
                "kind": row.get::<_, String>(0)?,
                "count": row.get::<_, i64>(1)?,
                "bytes": row.get::<_, i64>(2)?,
            }))
        })
        .map_err(|error| format!("ARTIFACT_STATS_KINDS_QUERY_FAILED:{error}"))?;
    let mut kinds = Vec::new();
    for row in rows {
        kinds.push(row.map_err(|error| format!("ARTIFACT_STATS_KIND_ROW_FAILED:{error}"))?);
    }

    Ok(json!({
        "artifacts": artifacts,
        "exact_bytes": exact_bytes,
        "kinds": kinds,
    }))
}

#[cfg(test)]
mod tests {
    use super::{supports, validate_arguments};

    #[test]
    fn routes_artifact_stats_only() {
        assert!(supports(&["run".to_owned(), "artifact-stats".to_owned()]));
        assert!(!supports(&["run".to_owned(), "artifact-query".to_owned()]));
    }

    #[test]
    fn rejects_trailing_arguments() {
        let arguments = [
            "syntavra".to_owned(),
            "run".to_owned(),
            "artifact-stats".to_owned(),
            "unexpected".to_owned(),
        ];
        assert!(validate_arguments(&arguments).is_err());
    }
}
