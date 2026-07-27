use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::thread;
use std::time::{Duration, Instant, SystemTime};

use rusqlite::backup::{Backup, StepResult};
use rusqlite::{Connection, OpenFlags};
use serde_json::{Map, Number, Value};
use syntavra_core::sha256_hex;

use crate::broker_snapshot_contract::{
    broker_schema_version, canonical_project_root, contract as logical_contract, contract_array,
    contract_string, contract_u64, foreign_keys, indexes, percent_encode_path,
    relative_database_path, schema_objects, table_columns, table_rows,
};

const CONTRACT_JSON: &str = include_str!("../../../contracts/state/broker-live-snapshot-v1.json");
const DATABASE_NAME: &str = "broker.sqlite3";
const MAXIMUM_DATABASE_BYTES: u64 = 64 * 1024 * 1024;
const MAXIMUM_DURATION_MILLISECONDS: u64 = 5_000;
const PAGES_PER_STEP: i32 = 64;
const RETRY_SLEEP_MILLISECONDS: u64 = 10;

#[derive(Debug, Clone, PartialEq, Eq)]
struct ObservedIdentity {
    files: BTreeMap<String, (u64, Option<SystemTime>)>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct SidecarState {
    journal: bool,
    shm: bool,
    wal: bool,
}

fn error(code: &str) -> String {
    code.to_owned()
}

fn contract() -> Result<Value, String> {
    serde_json::from_str(CONTRACT_JSON).map_err(|_| error("BROKER_LIVE_CONTRACT_INVALID"))
}

fn regular_file(path: &Path, prefix: &str) -> Result<Option<fs::Metadata>, String> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => {
            if metadata.file_type().is_symlink() {
                return Err(error(&format!("{prefix}_SYMLINK")));
            }
            if !metadata.is_file() {
                return Err(error(&format!("{prefix}_NOT_FILE")));
            }
            Ok(Some(metadata))
        }
        Err(source) if source.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(_) => Err(error(&format!("{prefix}_METADATA_FAILED"))),
    }
}

fn sidecar_path(database: &Path, suffix: &str) -> Result<PathBuf, String> {
    let value = database
        .to_str()
        .ok_or_else(|| error("BROKER_DATABASE_PATH_UTF8_INVALID"))?;
    Ok(PathBuf::from(format!("{value}{suffix}")))
}

fn sidecar_state(database: &Path) -> Result<SidecarState, String> {
    let state = SidecarState {
        journal: regular_file(
            &sidecar_path(database, "-journal")?,
            "BROKER_LIVE_SIDECAR",
        )?
        .is_some(),
        shm: regular_file(&sidecar_path(database, "-shm")?, "BROKER_LIVE_SIDECAR")?
            .is_some(),
        wal: regular_file(&sidecar_path(database, "-wal")?, "BROKER_LIVE_SIDECAR")?
            .is_some(),
    };
    if state.journal {
        return Err(error("BROKER_LIVE_ROLLBACK_JOURNAL_PRESENT"));
    }
    if state.wal != state.shm {
        return Err(error("BROKER_LIVE_WAL_SHM_PAIR_INVALID"));
    }
    Ok(state)
}

fn observed_identity(database: &Path, sidecars: SidecarState) -> Result<ObservedIdentity, String> {
    let mut paths = vec![("database", database.to_path_buf())];
    if sidecars.shm {
        paths.push(("shm", sidecar_path(database, "-shm")?));
    }
    if sidecars.wal {
        paths.push(("wal", sidecar_path(database, "-wal")?));
    }
    let mut files = BTreeMap::new();
    for (name, path) in paths {
        let metadata = regular_file(&path, "BROKER_LIVE_SOURCE")?
            .ok_or_else(|| error("BROKER_LIVE_SOURCE_DISAPPEARED"))?;
        files.insert(name.to_owned(), (metadata.len(), metadata.modified().ok()));
    }
    Ok(ObservedIdentity { files })
}

fn open_source(database: &Path) -> Result<Connection, String> {
    let uri = format!("file:{}?mode=ro", percent_encode_path(database)?);
    let flags = OpenFlags::SQLITE_OPEN_READ_ONLY
        | OpenFlags::SQLITE_OPEN_URI
        | OpenFlags::SQLITE_OPEN_NO_MUTEX;
    let connection = Connection::open_with_flags(uri, flags)
        .map_err(|_| error("BROKER_LIVE_SOURCE_OPEN_FAILED"))?;
    connection
        .busy_timeout(Duration::ZERO)
        .map_err(|_| error("BROKER_LIVE_SOURCE_OPEN_FAILED"))?;
    connection
        .execute_batch("PRAGMA query_only=ON; PRAGMA trusted_schema=OFF;")
        .map_err(|_| error("BROKER_LIVE_QUERY_ONLY_FAILED"))?;
    let query_only: i64 = connection
        .query_row("PRAGMA query_only", [], |row| row.get(0))
        .map_err(|_| error("BROKER_LIVE_QUERY_ONLY_FAILED"))?;
    if query_only != 1 {
        return Err(error("BROKER_LIVE_QUERY_ONLY_FAILED"));
    }
    Ok(connection)
}

fn positive_pragma(connection: &Connection, name: &str) -> Result<u64, String> {
    let sql = format!("PRAGMA {name}");
    let value: i64 = connection
        .query_row(&sql, [], |row| row.get(0))
        .map_err(|_| error("BROKER_LIVE_SOURCE_METADATA_FAILED"))?;
    u64::try_from(value)
        .ok()
        .filter(|value| *value > 0)
        .ok_or_else(|| error("BROKER_LIVE_SOURCE_METADATA_FAILED"))
}

fn journal_mode(connection: &Connection) -> Result<String, String> {
    let value: String = connection
        .query_row("PRAGMA journal_mode", [], |row| row.get(0))
        .map_err(|_| error("BROKER_LIVE_SOURCE_METADATA_FAILED"))?;
    let normalized = value.to_ascii_lowercase();
    if !matches!(
        normalized.as_str(),
        "delete" | "memory" | "off" | "persist" | "truncate" | "wal"
    ) {
        return Err(error("BROKER_LIVE_JOURNAL_MODE_UNSUPPORTED"));
    }
    Ok(normalized)
}

fn validate_size(page_size: u64, page_count: u64) -> Result<u64, String> {
    if page_size == 0 || page_size > 65_536 || !page_size.is_power_of_two() {
        return Err(error("BROKER_LIVE_PAGE_SIZE_INVALID"));
    }
    if page_count == 0 {
        return Err(error("BROKER_LIVE_PAGE_COUNT_INVALID"));
    }
    let logical_bytes = page_size
        .checked_mul(page_count)
        .ok_or_else(|| error("BROKER_LIVE_DATABASE_TOO_LARGE"))?;
    if logical_bytes > MAXIMUM_DATABASE_BYTES {
        return Err(error("BROKER_LIVE_DATABASE_TOO_LARGE"));
    }
    Ok(logical_bytes)
}

fn logical_snapshot(
    connection: &Connection,
    expected_project_id: &str,
) -> Result<(u64, Map<String, Value>, Map<String, Value>), String> {
    connection
        .execute_batch("PRAGMA query_only=ON; PRAGMA trusted_schema=OFF;")
        .map_err(|_| error("BROKER_LIVE_DESTINATION_QUERY_ONLY_FAILED"))?;
    let logical = logical_contract()?;
    schema_objects(connection, &logical)?;
    for table in contract_array(&logical, "tables")? {
        table_columns(connection, table)?;
    }
    indexes(connection, &logical)?;
    foreign_keys(connection, &logical)?;
    let schema_version = broker_schema_version(connection, &logical)?;
    let mut tables = Map::new();
    let mut row_counts = Map::new();
    for table in contract_array(&logical, "tables")? {
        let name = contract_string(table, "name")?;
        let rows = table_rows(connection, table, expected_project_id)?;
        row_counts.insert(name.to_owned(), Value::Number(Number::from(rows.len())));
        tables.insert(name.to_owned(), Value::Array(rows));
    }
    Ok((schema_version, tables, row_counts))
}

fn insert_project_binding(
    payload: &mut Map<String, Value>,
    expected_project_id: &str,
    actual_project_id: &str,
) {
    let mut binding = Map::new();
    binding.insert(
        "actual".to_owned(),
        Value::String(actual_project_id.to_owned()),
    );
    binding.insert(
        "expected".to_owned(),
        Value::String(expected_project_id.to_owned()),
    );
    binding.insert("matched".to_owned(), Value::Bool(true));
    payload.insert("project_binding".to_owned(), Value::Object(binding));
}

/// Produces a canonical logical snapshot from a bounded in-memory online backup.
///
/// # Errors
///
/// Returns a stable `BROKER_*` error code when source binding, sidecar policy,
/// backup bounds, SQLite online backup, or R9 logical validation fails.
#[allow(clippy::too_many_lines)]
pub fn snapshot_live_broker_database_json(
    project_root: &str,
    database_path: &str,
    expected_project_id: &str,
) -> Result<String, String> {
    let live = contract()?;
    if contract_string(&live, "database_name")? != DATABASE_NAME {
        return Err(error("BROKER_LIVE_CONTRACT_INVALID"));
    }
    if contract_u64(&live, "broker_schema_version")? != 2 {
        return Err(error("BROKER_LIVE_CONTRACT_INVALID"));
    }

    let (root, actual_project_id) = canonical_project_root(project_root, expected_project_id)?;
    let (database, relative_path) = relative_database_path(&root, database_path)?;
    let sidecars_before = sidecar_state(&database)?;
    let identity_before = observed_identity(&database, sidecars_before)?;
    let source = open_source(&database)?;
    let journal = journal_mode(&source)?;
    let page_size = positive_pragma(&source, "page_size")?;
    let initial_page_count = positive_pragma(&source, "page_count")?;
    let initial_logical_bytes = validate_size(page_size, initial_page_count)?;

    let mut destination = Connection::open_in_memory()
        .map_err(|_| error("BROKER_LIVE_DESTINATION_OPEN_FAILED"))?;
    let started = Instant::now();
    let deadline = Duration::from_millis(MAXIMUM_DURATION_MILLISECONDS);
    let backup = Backup::new(&source, &mut destination)
        .map_err(|_| error("BROKER_LIVE_BACKUP_FAILED"))?;
    let mut steps = 0_u64;
    loop {
        if started.elapsed() > deadline {
            return Err(error("BROKER_LIVE_BACKUP_TIMEOUT"));
        }
        steps = steps
            .checked_add(1)
            .ok_or_else(|| error("BROKER_LIVE_BACKUP_PROGRESS_INVALID"))?;
        let result = backup
            .step(PAGES_PER_STEP)
            .map_err(|_| error("BROKER_LIVE_BACKUP_FAILED"))?;
        let progress = backup.progress();
        if progress.pagecount <= 0
            || progress.remaining < 0
            || progress.remaining > progress.pagecount
        {
            return Err(error("BROKER_LIVE_BACKUP_PROGRESS_INVALID"));
        }
        validate_size(
            page_size,
            u64::try_from(progress.pagecount)
                .map_err(|_| error("BROKER_LIVE_BACKUP_PROGRESS_INVALID"))?,
        )?;
        match result {
            StepResult::Done => break,
            StepResult::More => {}
            StepResult::Busy | StepResult::Locked => {
                thread::sleep(Duration::from_millis(RETRY_SLEEP_MILLISECONDS));
            }
            _ => return Err(error("BROKER_LIVE_BACKUP_FAILED")),
        }
    }
    let progress = backup.progress();
    if progress.remaining != 0 {
        return Err(error("BROKER_LIVE_BACKUP_INCOMPLETE"));
    }
    drop(backup);

    if started.elapsed() > deadline {
        return Err(error("BROKER_LIVE_BACKUP_TIMEOUT"));
    }
    let final_page_count = positive_pragma(&destination, "page_count")?;
    let final_logical_bytes = validate_size(page_size, final_page_count)?;
    let (schema_version, tables, row_counts) =
        logical_snapshot(&destination, expected_project_id)?;
    drop(destination);
    drop(source);

    let sidecars_after = sidecar_state(&database)?;
    let identity_after = observed_identity(&database, sidecars_after)?;
    let source_changed = sidecars_after != sidecars_before || identity_after != identity_before;

    let mut database_value = Map::new();
    database_value.insert(
        "backup_destination".to_owned(),
        Value::String("memory".to_owned()),
    );
    database_value.insert(
        "relative_path".to_owned(),
        Value::String(relative_path),
    );
    database_value.insert(
        "rollback_journal_present".to_owned(),
        Value::Bool(false),
    );
    database_value.insert("shm_present".to_owned(), Value::Bool(sidecars_before.shm));
    database_value.insert(
        "source_changed_during_backup".to_owned(),
        Value::Bool(source_changed),
    );
    database_value.insert(
        "source_journal_mode".to_owned(),
        Value::String(journal),
    );
    database_value.insert(
        "source_open_mode".to_owned(),
        Value::String("read-only-live".to_owned()),
    );
    database_value.insert("source_query_only".to_owned(), Value::Bool(true));
    database_value.insert("wal_present".to_owned(), Value::Bool(sidecars_before.wal));

    let mut backup_value = Map::new();
    backup_value.insert(
        "api".to_owned(),
        Value::String("sqlite-online-backup".to_owned()),
    );
    backup_value.insert("complete".to_owned(), Value::Bool(true));
    backup_value.insert(
        "final_logical_bytes".to_owned(),
        Value::Number(Number::from(final_logical_bytes)),
    );
    backup_value.insert(
        "final_page_count".to_owned(),
        Value::Number(Number::from(final_page_count)),
    );
    backup_value.insert(
        "initial_logical_bytes".to_owned(),
        Value::Number(Number::from(initial_logical_bytes)),
    );
    backup_value.insert(
        "initial_page_count".to_owned(),
        Value::Number(Number::from(initial_page_count)),
    );
    backup_value.insert(
        "maximum_database_bytes".to_owned(),
        Value::Number(Number::from(MAXIMUM_DATABASE_BYTES)),
    );
    backup_value.insert(
        "maximum_duration_milliseconds".to_owned(),
        Value::Number(Number::from(MAXIMUM_DURATION_MILLISECONDS)),
    );
    backup_value.insert(
        "page_size".to_owned(),
        Value::Number(Number::from(page_size)),
    );
    backup_value.insert(
        "pages_per_step".to_owned(),
        Value::Number(Number::from(PAGES_PER_STEP)),
    );
    backup_value.insert(
        "retry_sleep_milliseconds".to_owned(),
        Value::Number(Number::from(RETRY_SLEEP_MILLISECONDS)),
    );
    backup_value.insert("steps".to_owned(), Value::Number(Number::from(steps)));

    let mut mutation = Map::new();
    mutation.insert("checkpoint".to_owned(), Value::Bool(false));
    mutation.insert("destination_files".to_owned(), Value::Bool(false));
    mutation.insert("migration".to_owned(), Value::Bool(false));
    mutation.insert("persistent_project_state".to_owned(), Value::Bool(false));
    mutation.insert("source_connection_writes".to_owned(), Value::Bool(false));
    mutation.insert("vacuum".to_owned(), Value::Bool(false));

    let mut payload = Map::new();
    payload.insert("backup".to_owned(), Value::Object(backup_value));
    payload.insert(
        "broker_schema_version".to_owned(),
        Value::Number(Number::from(schema_version)),
    );
    payload.insert(
        "claim".to_owned(),
        Value::String("RUST_BROKER_SQLITE_LIVE_BACKUP_PARITY_PROVEN_R10_FIXTURES".to_owned()),
    );
    payload.insert(
        "contract_version".to_owned(),
        Value::Number(Number::from(contract_u64(&live, "contract_version")?)),
    );
    payload.insert("database".to_owned(), Value::Object(database_value));
    payload.insert("mutation".to_owned(), Value::Object(mutation));
    payload.insert("ok".to_owned(), Value::Bool(true));
    insert_project_binding(&mut payload, expected_project_id, &actual_project_id);
    payload.insert("project_id".to_owned(), Value::String(actual_project_id));
    payload.insert("row_counts".to_owned(), Value::Object(row_counts));
    payload.insert(
        "schema_version".to_owned(),
        Value::Number(Number::from(contract_u64(&live, "schema_version")?)),
    );
    payload.insert(
        "snapshot_id".to_owned(),
        Value::String(contract_string(&live, "snapshot_id")?.to_owned()),
    );
    payload.insert("tables".to_owned(), Value::Object(tables));

    let encoded = serde_json::to_vec(&Value::Object(payload.clone()))
        .map_err(|_| error("BROKER_ROW_TYPE_INVALID"))?;
    payload.insert(
        "snapshot_hash".to_owned(),
        Value::String(sha256_hex(&encoded)),
    );
    serde_json::to_string(&Value::Object(payload)).map_err(|_| error("BROKER_ROW_TYPE_INVALID"))
}

#[cfg(test)]
mod tests {
    use super::{contract, snapshot_live_broker_database_json};

    #[test]
    fn embeds_the_r10_contract() {
        let value = contract().expect("contract");
        assert_eq!(value["snapshot_id"], "syntavra-broker-live-snapshot-v1");
        assert_eq!(value["backup_policy"]["pages_per_step"], 64);
    }

    #[test]
    fn rejects_invalid_expected_project_id_before_open() {
        assert_eq!(
            snapshot_live_broker_database_json(".", "broker.sqlite3", "invalid"),
            Err("BROKER_EXPECTED_PROJECT_INVALID".to_owned())
        );
    }
}
