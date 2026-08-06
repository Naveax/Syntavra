#![forbid(unsafe_code)]

use std::collections::BTreeMap;
use std::fs;
use std::path::Path;

use rusqlite::{Connection, OptionalExtension};
use serde_json::{json, Value};
use sha2::{Digest as _, Sha256};

const CATALOG: &str =
    include_str!("../../../contracts/engine/r38-native-platform-health-v1.json");
const VIEWS: [&str; 10] = [
    "task",
    "decision",
    "change",
    "failure",
    "security",
    "dependency",
    "repository",
    "test",
    "provider",
    "handoff",
];

pub fn supports(command: &[String]) -> bool {
    matches!(
        command,
        [root, action]
            if root == "run"
                && matches!(
                    action.as_str(),
                    "platform-status"
                        | "platform-doctor"
                        | "competitive-status"
                        | "competitive-doctor"
                )
    )
}

fn open(path: &Path, label: &str) -> Result<Connection, String> {
    Connection::open(path).map_err(|error| format!("PLATFORM_HEALTH_{label}_OPEN_FAILED:{error}"))
}

fn table_exists(connection: &Connection, table: &str) -> Result<bool, String> {
    connection
        .query_row(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?1 LIMIT 1",
            [table],
            |_| Ok(true),
        )
        .optional()
        .map(|value| value.unwrap_or(false))
        .map_err(|error| format!("PLATFORM_HEALTH_TABLE_LOOKUP_FAILED:{table}:{error}"))
}

fn column_exists(connection: &Connection, table: &str, column: &str) -> Result<bool, String> {
    if !table_exists(connection, table)? {
        return Ok(false);
    }
    let sql = format!("PRAGMA table_info(\"{}\")", table.replace('"', "\"\""));
    let mut statement = connection
        .prepare(&sql)
        .map_err(|error| format!("PLATFORM_HEALTH_COLUMN_PREPARE_FAILED:{table}:{error}"))?;
    let mut rows = statement
        .query([])
        .map_err(|error| format!("PLATFORM_HEALTH_COLUMN_QUERY_FAILED:{table}:{error}"))?;
    while let Some(row) = rows
        .next()
        .map_err(|error| format!("PLATFORM_HEALTH_COLUMN_ROW_FAILED:{table}:{error}"))?
    {
        let name = row
            .get::<_, String>(1)
            .map_err(|error| format!("PLATFORM_HEALTH_COLUMN_READ_FAILED:{table}:{error}"))?;
        if name == column {
            return Ok(true);
        }
    }
    Ok(false)
}

fn count(connection: &Connection, table: &str) -> Result<i64, String> {
    if !table_exists(connection, table)? {
        return Ok(0);
    }
    let sql = format!("SELECT COUNT(*) FROM \"{}\"", table.replace('"', "\"\""));
    connection
        .query_row(&sql, [], |row| row.get(0))
        .map_err(|error| format!("PLATFORM_HEALTH_COUNT_FAILED:{table}:{error}"))
}

fn grouped_rows(
    connection: &Connection,
    table: &str,
    key: &str,
    count_key: &str,
) -> Result<Vec<Value>, String> {
    if !column_exists(connection, table, key)? {
        return Ok(Vec::new());
    }
    let sql = format!(
        "SELECT \"{key}\", COUNT(*) FROM \"{table}\" GROUP BY \"{key}\" ORDER BY \"{key}\"",
        key = key.replace('"', "\"\""),
        table = table.replace('"', "\"\""),
    );
    let mut statement = connection
        .prepare(&sql)
        .map_err(|error| format!("PLATFORM_HEALTH_GROUP_PREPARE_FAILED:{table}:{error}"))?;
    let rows = statement
        .query_map([], |row| {
            let value = row.get::<_, String>(0)?;
            let amount = row.get::<_, i64>(1)?;
            Ok(json!({key: value, count_key: amount}))
        })
        .map_err(|error| format!("PLATFORM_HEALTH_GROUP_QUERY_FAILED:{table}:{error}"))?;
    rows.collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("PLATFORM_HEALTH_GROUP_ROW_FAILED:{table}:{error}"))
}

fn artifact_stats(state_root: &Path) -> Result<Value, String> {
    let connection = open(
        &state_root.join("unified/artifacts/artifacts.sqlite3"),
        "ARTIFACTS",
    )?;
    let artifacts = count(&connection, "artifacts")?;
    let byte_expression = if column_exists(&connection, "artifacts", "byte_count")? {
        "COALESCE(SUM(byte_count),0)"
    } else if column_exists(&connection, "artifacts", "payload")? {
        "COALESCE(SUM(length(payload)),0)"
    } else {
        "0"
    };
    let exact_bytes = connection
        .query_row(
            &format!("SELECT {byte_expression} FROM artifacts"),
            [],
            |row| row.get::<_, i64>(0),
        )
        .map_err(|error| format!("PLATFORM_HEALTH_ARTIFACT_BYTES_FAILED:{error}"))?;
    let kinds = if column_exists(&connection, "artifacts", "kind")? {
        let sql = format!(
            "SELECT kind,COUNT(*) count,{byte_expression} bytes FROM artifacts GROUP BY kind ORDER BY kind"
        );
        let mut statement = connection
            .prepare(&sql)
            .map_err(|error| format!("PLATFORM_HEALTH_ARTIFACT_KINDS_PREPARE_FAILED:{error}"))?;
        let rows = statement
            .query_map([], |row| {
                Ok(json!({
                    "kind": row.get::<_, String>(0)?,
                    "count": row.get::<_, i64>(1)?,
                    "bytes": row.get::<_, i64>(2)?,
                }))
            })
            .map_err(|error| format!("PLATFORM_HEALTH_ARTIFACT_KINDS_QUERY_FAILED:{error}"))?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|error| format!("PLATFORM_HEALTH_ARTIFACT_KINDS_ROW_FAILED:{error}"))?
    } else {
        Vec::new()
    };
    Ok(json!({
        "artifacts": artifacts,
        "exact_bytes": exact_bytes,
        "kinds": kinds,
    }))
}

fn artifact_verify(state_root: &Path) -> Result<Value, String> {
    let connection = open(
        &state_root.join("unified/artifacts/artifacts.sqlite3"),
        "ARTIFACTS",
    )?;
    let mut failures = Vec::new();
    let checked = count(&connection, "artifacts")?;
    if column_exists(&connection, "artifacts", "sha256")?
        && column_exists(&connection, "artifacts", "object_path")?
    {
        let mut statement = connection
            .prepare("SELECT artifact_id,sha256,object_path FROM artifacts ORDER BY artifact_id")
            .map_err(|error| format!("PLATFORM_HEALTH_ARTIFACT_VERIFY_PREPARE_FAILED:{error}"))?;
        let rows = statement
            .query_map([], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                ))
            })
            .map_err(|error| format!("PLATFORM_HEALTH_ARTIFACT_VERIFY_QUERY_FAILED:{error}"))?;
        for row in rows {
            let (artifact_id, expected, object_path) = row
                .map_err(|error| format!("PLATFORM_HEALTH_ARTIFACT_VERIFY_ROW_FAILED:{error}"))?;
            let path = Path::new(&object_path);
            if !path.is_file() {
                failures.push(format!("missing:{artifact_id}"));
                continue;
            }
            let payload = fs::read(path)
                .map_err(|error| format!("PLATFORM_HEALTH_ARTIFACT_READ_FAILED:{error}"))?;
            let actual = format!("{:x}", Sha256::digest(payload));
            if actual != expected {
                failures.push(format!("hash:{artifact_id}"));
            }
        }
    } else if column_exists(&connection, "artifacts", "content_hash")?
        && column_exists(&connection, "artifacts", "payload")?
    {
        let mut statement = connection
            .prepare("SELECT artifact_id,content_hash,payload FROM artifacts ORDER BY artifact_id")
            .map_err(|error| format!("PLATFORM_HEALTH_NATIVE_ARTIFACT_PREPARE_FAILED:{error}"))?;
        let rows = statement
            .query_map([], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, Vec<u8>>(2)?,
                ))
            })
            .map_err(|error| format!("PLATFORM_HEALTH_NATIVE_ARTIFACT_QUERY_FAILED:{error}"))?;
        for row in rows {
            let (artifact_id, expected, payload) = row
                .map_err(|error| format!("PLATFORM_HEALTH_NATIVE_ARTIFACT_ROW_FAILED:{error}"))?;
            let actual = format!("{:x}", Sha256::digest(payload));
            if actual != expected.trim_start_matches("sha256:") {
                failures.push(format!("hash:{artifact_id}"));
            }
        }
    }
    Ok(json!({
        "ok": failures.is_empty(),
        "checked": checked,
        "failures": failures,
    }))
}

fn semantic_index_stats(connection: &Connection) -> Result<Value, String> {
    let sources = count(connection, "semantic_sources")?;
    let stale = if column_exists(connection, "semantic_sources", "stale")? {
        connection
            .query_row(
                "SELECT COUNT(*) FROM semantic_sources WHERE stale=1",
                [],
                |row| row.get::<_, i64>(0),
            )
            .map_err(|error| format!("PLATFORM_HEALTH_SEMANTIC_STALE_FAILED:{error}"))?
    } else {
        0
    };
    let formats = grouped_rows(connection, "semantic_sources", "format", "sources")?;
    Ok(json!({
        "semantic_index_sources": sources,
        "stale_semantic_index_sources": stale,
        "semantic_index_nodes": count(connection, "semantic_source_nodes")?,
        "semantic_index_edges": count(connection, "semantic_source_edges")?,
        "semantic_index_formats": formats,
    }))
}

fn repository_query_stats(connection: &Connection, baseline: &Value) -> Result<Value, String> {
    let graph_nodes = if column_exists(connection, "nodes", "kind")? {
        connection
            .query_row(
                "SELECT COUNT(*) FROM nodes WHERE kind != 'external'",
                [],
                |row| row.get::<_, i64>(0),
            )
            .map_err(|error| format!("PLATFORM_HEALTH_QUERY_GRAPH_COUNT_FAILED:{error}"))?
    } else {
        count(connection, "nodes")?
    };
    let indexed_nodes = if table_exists(connection, "node_search")? {
        count(connection, "node_search")?
    } else {
        graph_nodes
    };
    Ok(json!({
        "backend": baseline["backend"],
        "graph_nodes": graph_nodes,
        "indexed_nodes": indexed_nodes,
    }))
}

fn semantic_stats(state_root: &Path, baseline: &Value) -> Result<Value, String> {
    let connection = open(
        &state_root.join("unified/semantic-graph.sqlite3"),
        "SEMANTIC",
    )?;
    let indexes = semantic_index_stats(&connection)?;
    let query = repository_query_stats(&connection, &baseline["repository_query"])?;
    let mut value = baseline.clone();
    value["files"] = json!(count(&connection, "files")?);
    value["nodes"] = json!(count(&connection, "nodes")?);
    value["edges"] = json!(count(&connection, "edges")?);
    value["languages"] = json!(grouped_rows(&connection, "files", "language", "files")?);
    value["capabilities"] = json!(grouped_rows(
        &connection,
        "files",
        "capability_level",
        "files"
    )?);
    value["detectors"] = json!(grouped_rows(&connection, "files", "detector", "files")?);
    value["unknown_language_files"] = if column_exists(&connection, "files", "language")? {
        json!(connection
            .query_row(
                "SELECT COUNT(*) FROM files WHERE language LIKE 'unknown:%'",
                [],
                |row| row.get::<_, i64>(0),
            )
            .map_err(|error| format!("PLATFORM_HEALTH_UNKNOWN_LANGUAGE_FAILED:{error}"))?)
    } else {
        json!(0)
    };
    for key in [
        "semantic_index_sources",
        "stale_semantic_index_sources",
        "semantic_index_nodes",
        "semantic_index_edges",
        "semantic_index_formats",
    ] {
        value[key] = indexes[key].clone();
    }
    value["repository_query"] = query;
    Ok(value)
}

fn language_platform(state_root: &Path, baseline: &Value) -> Result<Value, String> {
    let connection = open(
        &state_root.join("unified/semantic-graph.sqlite3"),
        "SEMANTIC",
    )?;
    let mut value = baseline.clone();
    value["semantic_indexes"] = semantic_index_stats(&connection)?;
    value["repository_query"] =
        repository_query_stats(&connection, &baseline["repository_query"])?;
    Ok(value)
}

fn runtime_evidence_stats(state_root: &Path) -> Result<Value, String> {
    let connection = open(
        &state_root.join("unified/runtime-evidence.sqlite3"),
        "EVIDENCE",
    )?;
    Ok(json!({
        "ok": true,
        "nodes": count(&connection, "nodes")?,
        "edges": count(&connection, "edges")?,
        "relations": grouped_rows(&connection, "edges", "relation", "count")?,
    }))
}

fn memory_stats(state_root: &Path) -> Result<Value, String> {
    let connection = open(
        &state_root.join("unified/session-memory.sqlite3"),
        "MEMORY",
    )?;
    Ok(json!({
        "sessions": count(&connection, "sessions")?,
        "events": count(&connection, "events")?,
        "summaries": count(&connection, "summaries")?,
        "checkpoints": count(&connection, "checkpoints")?,
        "views": VIEWS,
    }))
}

fn headless_stats(state_root: &Path) -> Result<Value, String> {
    let connection = open(&state_root.join("unified/headless.sqlite3"), "HEADLESS")?;
    let mut states = BTreeMap::new();
    if column_exists(&connection, "jobs", "state")? {
        let mut statement = connection
            .prepare("SELECT state,COUNT(*) FROM jobs GROUP BY state ORDER BY state")
            .map_err(|error| format!("PLATFORM_HEALTH_HEADLESS_STATES_PREPARE_FAILED:{error}"))?;
        let rows = statement
            .query_map([], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
            })
            .map_err(|error| format!("PLATFORM_HEALTH_HEADLESS_STATES_QUERY_FAILED:{error}"))?;
        for row in rows {
            let (state, amount) = row
                .map_err(|error| format!("PLATFORM_HEALTH_HEADLESS_STATES_ROW_FAILED:{error}"))?;
            states.insert(state, amount);
        }
    }
    Ok(json!({
        "ok": true,
        "jobs": count(&connection, "jobs")?,
        "states": states,
    }))
}

fn patch_status(
    value: &mut Value,
    project_root: &Path,
    state_root: &Path,
) -> Result<(), String> {
    value["project"] = json!(project_root.to_string_lossy());
    value["artifacts"] = artifact_stats(state_root)?;
    value["semantic_graph"] = semantic_stats(state_root, &value["semantic_graph"])?;
    value["runtime_evidence"] = runtime_evidence_stats(state_root)?;
    value["language_platform"] = language_platform(state_root, &value["language_platform"])?;
    value["memory"] = memory_stats(state_root)?;
    value["headless"] = headless_stats(state_root)?;
    Ok(())
}

fn patch_doctor(value: &mut Value, state_root: &Path) -> Result<(), String> {
    let artifact_integrity = artifact_verify(state_root)?;
    value["artifact_integrity"] = artifact_integrity.clone();
    value["semantic_graph"] = semantic_stats(state_root, &value["semantic_graph"])?;
    value["runtime_evidence"] = runtime_evidence_stats(state_root)?;
    value["language_platform"] = language_platform(state_root, &value["language_platform"])?;
    value["memory"] = memory_stats(state_root)?;
    value["headless"] = headless_stats(state_root)?;
    value["ok"] = json!(
        artifact_integrity["ok"].as_bool() == Some(true)
            && value["adapters"]["ok"].as_bool() == Some(true)
    );
    value["strict_native_sandbox_ready"] = value["sandbox"]["strict_ready"].clone();
    Ok(())
}

pub fn execute(
    command: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    super::native_platform_state::initialize(state_root)?;
    let catalog = serde_json::from_str::<Value>(CATALOG)
        .map_err(|error| format!("PLATFORM_HEALTH_CATALOG_INVALID:{error}"))?;
    let action = command
        .get(1)
        .ok_or_else(|| "PLATFORM_HEALTH_ACTION_MISSING".to_owned())?;
    let mut value = if action.ends_with("status") {
        catalog["status"].clone()
    } else {
        catalog["doctor"].clone()
    };
    if action.ends_with("status") {
        patch_status(&mut value, project_root, state_root)?;
    } else {
        patch_doctor(&mut value, state_root)?;
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn routes_four_platform_health_aliases() {
        for action in [
            "platform-status",
            "platform-doctor",
            "competitive-status",
            "competitive-doctor",
        ] {
            assert!(supports(&["run".to_owned(), action.to_owned()]));
        }
        assert!(!supports(&[
            "run".to_owned(),
            "platform-manifest".to_owned()
        ]));
    }
}
