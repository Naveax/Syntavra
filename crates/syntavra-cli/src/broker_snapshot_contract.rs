use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Component, Path, PathBuf};
use std::time::SystemTime;

use rusqlite::types::ValueRef;
use rusqlite::{Connection, OpenFlags};
use serde_json::{Map, Number, Value};
use syntavra_core::sha256_hex;

use crate::state_snapshot_contract::project_id_for_root;

const CONTRACT_JSON: &str = include_str!("../../../contracts/state/broker-snapshot-v1.json");
const DATABASE_NAME: &str = "broker.sqlite3";
const FORBIDDEN_SIDECARS: &[&str] = &["-journal", "-shm", "-wal"];

#[derive(Debug, Clone, PartialEq, Eq)]
struct FileIdentity {
    length: u64,
    modified: Option<SystemTime>,
}

fn error(code: &str) -> String {
    code.to_owned()
}

fn contract() -> Result<Value, String> {
    serde_json::from_str(CONTRACT_JSON).map_err(|_| error("BROKER_CONTRACT_INVALID"))
}

fn contract_string<'a>(value: &'a Value, key: &str) -> Result<&'a str, String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| error("BROKER_CONTRACT_INVALID"))
}

fn contract_bool(value: &Value, key: &str) -> Result<bool, String> {
    value
        .get(key)
        .and_then(Value::as_bool)
        .ok_or_else(|| error("BROKER_CONTRACT_INVALID"))
}

fn contract_u64(value: &Value, key: &str) -> Result<u64, String> {
    value
        .get(key)
        .and_then(Value::as_u64)
        .ok_or_else(|| error("BROKER_CONTRACT_INVALID"))
}

fn contract_array<'a>(value: &'a Value, key: &str) -> Result<&'a [Value], String> {
    value
        .get(key)
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .ok_or_else(|| error("BROKER_CONTRACT_INVALID"))
}

fn valid_lower_hash(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn canonical_project_root(
    project_root: &str,
    expected_project_id: &str,
) -> Result<(PathBuf, String), String> {
    if !valid_lower_hash(expected_project_id) {
        return Err(error("BROKER_EXPECTED_PROJECT_INVALID"));
    }
    let actual = project_id_for_root(project_root)
        .map_err(|_| error("BROKER_PROJECT_ROOT_INVALID"))?;
    let root = fs::canonicalize(project_root)
        .map_err(|_| error("BROKER_PROJECT_ROOT_INVALID"))?;
    if actual != expected_project_id {
        return Err(error("BROKER_PROJECT_MISMATCH"));
    }
    Ok((root, actual))
}

fn relative_database_path(root: &Path, database_path: &str) -> Result<(PathBuf, String), String> {
    let supplied = Path::new(database_path);
    let candidate = if supplied.is_absolute() {
        supplied.to_path_buf()
    } else {
        root.join(supplied)
    };
    if candidate.file_name().and_then(|value| value.to_str()) != Some(DATABASE_NAME) {
        return Err(error("BROKER_DATABASE_NAME_INVALID"));
    }
    let relative = candidate
        .strip_prefix(root)
        .map_err(|_| error("BROKER_DATABASE_PATH_ESCAPE"))?;
    if relative.as_os_str().is_empty()
        || relative
            .components()
            .any(|part| !matches!(part, Component::Normal(_)))
    {
        return Err(error("BROKER_DATABASE_PATH_ESCAPE"));
    }

    let mut current = root.to_path_buf();
    let total = relative.components().count();
    for (position, part) in relative.components().enumerate() {
        current.push(part.as_os_str());
        let metadata = fs::symlink_metadata(&current).map_err(|source| {
            if source.kind() == std::io::ErrorKind::NotFound {
                if position + 1 == total {
                    error("BROKER_DATABASE_MISSING")
                } else {
                    error("BROKER_DATABASE_PARENT_MISSING")
                }
            } else {
                error("BROKER_DATABASE_METADATA_FAILED")
            }
        })?;
        if metadata.file_type().is_symlink() {
            return Err(error("BROKER_DATABASE_SYMLINK"));
        }
        if position + 1 < total && !metadata.is_dir() {
            return Err(error("BROKER_DATABASE_PARENT_INVALID"));
        }
        if position + 1 == total && !metadata.is_file() {
            return Err(error("BROKER_DATABASE_NOT_FILE"));
        }
    }

    let relative_text = relative
        .to_str()
        .ok_or_else(|| error("BROKER_DATABASE_PATH_UTF8_INVALID"))?
        .replace('\\', "/");
    Ok((candidate, relative_text))
}

fn sidecar_path(database: &Path, suffix: &str) -> Result<PathBuf, String> {
    let value = database
        .to_str()
        .ok_or_else(|| error("BROKER_DATABASE_PATH_UTF8_INVALID"))?;
    Ok(PathBuf::from(format!("{value}{suffix}")))
}

fn assert_no_sidecars(database: &Path) -> Result<(), String> {
    for suffix in FORBIDDEN_SIDECARS {
        let sidecar = sidecar_path(database, suffix)?;
        match fs::symlink_metadata(sidecar) {
            Ok(_) => return Err(error("BROKER_DATABASE_SIDECAR_PRESENT")),
            Err(source) if source.kind() == std::io::ErrorKind::NotFound => {}
            Err(_) => return Err(error("BROKER_DATABASE_METADATA_FAILED")),
        }
    }
    Ok(())
}

fn file_identity(path: &Path) -> Result<FileIdentity, String> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|_| error("BROKER_DATABASE_METADATA_FAILED"))?;
    Ok(FileIdentity {
        length: metadata.len(),
        modified: metadata.modified().ok(),
    })
}

fn percent_encode_path(path: &Path) -> Result<String, String> {
    let normalized = path
        .to_str()
        .ok_or_else(|| error("BROKER_DATABASE_PATH_UTF8_INVALID"))?
        .replace('\\', "/");
    let mut output = String::with_capacity(normalized.len());
    for byte in normalized.bytes() {
        match byte {
            b'%' => output.push_str("%25"),
            b'#' => output.push_str("%23"),
            b'?' => output.push_str("%3F"),
            _ => output.push(char::from(byte)),
        }
    }
    Ok(output)
}

fn open_database(path: &Path) -> Result<Connection, String> {
    let uri = format!(
        "file:{}?mode=ro&immutable=1",
        percent_encode_path(path)?
    );
    let flags = OpenFlags::SQLITE_OPEN_READ_ONLY
        | OpenFlags::SQLITE_OPEN_URI
        | OpenFlags::SQLITE_OPEN_NO_MUTEX;
    let connection =
        Connection::open_with_flags(uri, flags).map_err(|_| error("BROKER_DATABASE_OPEN_FAILED"))?;
    connection
        .execute_batch("PRAGMA query_only=ON; PRAGMA trusted_schema=OFF;")
        .map_err(|_| error("BROKER_QUERY_ONLY_FAILED"))?;
    let query_only: i64 = connection
        .query_row("PRAGMA query_only", [], |row| row.get(0))
        .map_err(|_| error("BROKER_QUERY_ONLY_FAILED"))?;
    if query_only != 1 {
        return Err(error("BROKER_QUERY_ONLY_FAILED"));
    }
    Ok(connection)
}

fn schema_objects(connection: &Connection, contract: &Value) -> Result<(), String> {
    let tables = contract_array(contract, "tables")?
        .iter()
        .map(|table| contract_string(table, "name").map(str::to_owned))
        .collect::<Result<BTreeSet<_>, _>>()?;
    let indexes = contract_array(contract, "indexes")?
        .iter()
        .map(|index| contract_string(index, "name").map(str::to_owned))
        .collect::<Result<BTreeSet<_>, _>>()?;

    let mut statement = connection
        .prepare(
            "SELECT type,name FROM sqlite_master \
             WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name",
        )
        .map_err(|_| error("BROKER_SCHEMA_READ_FAILED"))?;
    let rows = statement
        .query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)))
        .map_err(|_| error("BROKER_SCHEMA_READ_FAILED"))?;
    let mut actual_tables = BTreeSet::new();
    let mut actual_indexes = BTreeSet::new();
    for row in rows {
        let (kind, name) = row.map_err(|_| error("BROKER_SCHEMA_READ_FAILED"))?;
        match kind.as_str() {
            "table" => {
                actual_tables.insert(name);
            }
            "index" => {
                actual_indexes.insert(name);
            }
            "trigger" | "view" => return Err(error("BROKER_SCHEMA_OBJECT_MISMATCH")),
            _ => return Err(error("BROKER_SCHEMA_OBJECT_MISMATCH")),
        }
    }
    if actual_tables != tables {
        return Err(error("BROKER_SCHEMA_OBJECT_MISMATCH"));
    }
    if actual_indexes != indexes {
        return Err(error("BROKER_SCHEMA_INDEX_MISMATCH"));
    }
    Ok(())
}

fn default_value(value: &Value) -> Result<Option<String>, String> {
    if value.is_null() {
        Ok(None)
    } else {
        value
            .as_str()
            .map(|item| Some(item.to_owned()))
            .ok_or_else(|| error("BROKER_CONTRACT_INVALID"))
    }
}

fn table_columns(connection: &Connection, table: &Value) -> Result<(), String> {
    let table_name = contract_string(table, "name")?;
    let expected = contract_array(table, "columns")?;
    let sql = format!("PRAGMA table_info(\"{table_name}\")");
    let mut statement = connection
        .prepare(&sql)
        .map_err(|_| error("BROKER_SCHEMA_READ_FAILED"))?;
    let rows = statement
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, i64>(3)? != 0,
                row.get::<_, Option<String>>(4)?,
                row.get::<_, i64>(5)?,
            ))
        })
        .map_err(|_| error("BROKER_SCHEMA_READ_FAILED"))?;
    let actual = rows
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| error("BROKER_SCHEMA_READ_FAILED"))?;
    if actual.len() != expected.len() {
        return Err(error("BROKER_SCHEMA_COLUMN_MISMATCH"));
    }
    for (row, column) in actual.iter().zip(expected) {
        let expected_row = (
            contract_string(column, "name")?.to_owned(),
            contract_string(column, "type")?.to_owned(),
            contract_bool(column, "not_null")?,
            default_value(
                column
                    .get("default")
                    .ok_or_else(|| error("BROKER_CONTRACT_INVALID"))?,
            )?,
            i64::try_from(contract_u64(column, "primary_key_position")?)
                .map_err(|_| error("BROKER_CONTRACT_INVALID"))?,
        );
        if row != &expected_row {
            return Err(error("BROKER_SCHEMA_COLUMN_MISMATCH"));
        }
    }
    Ok(())
}

fn indexes(connection: &Connection, contract: &Value) -> Result<(), String> {
    for index in contract_array(contract, "indexes")? {
        let name = contract_string(index, "name")?;
        let table = contract_string(index, "table")?;
        let unique = contract_bool(index, "unique")?;
        let sql = format!("PRAGMA index_list(\"{table}\")");
        let mut statement = connection
            .prepare(&sql)
            .map_err(|_| error("BROKER_SCHEMA_READ_FAILED"))?;
        let rows = statement
            .query_map([], |row| {
                Ok((row.get::<_, String>(1)?, row.get::<_, i64>(2)? != 0))
            })
            .map_err(|_| error("BROKER_SCHEMA_READ_FAILED"))?;
        let listed = rows
            .collect::<Result<Vec<_>, _>>()
            .map_err(|_| error("BROKER_SCHEMA_READ_FAILED"))?;
        if !listed.iter().any(|row| row == &(name.to_owned(), unique)) {
            return Err(error("BROKER_SCHEMA_INDEX_MISMATCH"));
        }

        let sql = format!("PRAGMA index_xinfo(\"{name}\")");
        let mut statement = connection
            .prepare(&sql)
            .map_err(|_| error("BROKER_SCHEMA_READ_FAILED"))?;
        let rows = statement
            .query_map([], |row| {
                Ok((
                    row.get::<_, Option<String>>(2)?,
                    row.get::<_, i64>(3)? != 0,
                    row.get::<_, i64>(5)? != 0,
                ))
            })
            .map_err(|_| error("BROKER_SCHEMA_READ_FAILED"))?;
        let actual = rows
            .collect::<Result<Vec<_>, _>>()
            .map_err(|_| error("BROKER_SCHEMA_READ_FAILED"))?
            .into_iter()
            .filter_map(|(column, descending, key)| key.then_some((column, descending)))
            .collect::<Vec<_>>();
        let expected = contract_array(index, "columns")?
            .iter()
            .map(|column| {
                Ok((
                    Some(contract_string(column, "name")?.to_owned()),
                    contract_bool(column, "descending")?,
                ))
            })
            .collect::<Result<Vec<_>, String>>()?;
        if actual != expected {
            return Err(error("BROKER_SCHEMA_INDEX_MISMATCH"));
        }
    }
    Ok(())
}

fn foreign_keys(connection: &Connection, contract: &Value) -> Result<(), String> {
    let mut expected = BTreeMap::<String, Vec<(String, String, String, String, String)>>::new();
    for foreign_key in contract_array(contract, "foreign_keys")? {
        expected
            .entry(contract_string(foreign_key, "table")?.to_owned())
            .or_default()
            .push((
                contract_string(foreign_key, "to_table")?.to_owned(),
                contract_string(foreign_key, "from")?.to_owned(),
                contract_string(foreign_key, "to")?.to_owned(),
                contract_string(foreign_key, "on_update")?.to_owned(),
                contract_string(foreign_key, "on_delete")?.to_owned(),
            ));
    }
    for table in contract_array(contract, "tables")? {
        let table_name = contract_string(table, "name")?;
        let sql = format!("PRAGMA foreign_key_list(\"{table_name}\")");
        let mut statement = connection
            .prepare(&sql)
            .map_err(|_| error("BROKER_SCHEMA_READ_FAILED"))?;
        let rows = statement
            .query_map([], |row| {
                Ok((
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, String>(5)?,
                    row.get::<_, String>(6)?,
                ))
            })
            .map_err(|_| error("BROKER_SCHEMA_READ_FAILED"))?;
        let actual = rows
            .collect::<Result<Vec<_>, _>>()
            .map_err(|_| error("BROKER_SCHEMA_READ_FAILED"))?;
        if actual != expected.get(table_name).cloned().unwrap_or_default() {
            return Err(error("BROKER_SCHEMA_FOREIGN_KEY_MISMATCH"));
        }
    }
    let violation = connection
        .query_row("PRAGMA foreign_key_check", [], |_| Ok(()))
        .optional()
        .map_err(|_| error("BROKER_SCHEMA_READ_FAILED"))?;
    if violation.is_some() {
        return Err(error("BROKER_FOREIGN_KEY_INVALID"));
    }
    Ok(())
}

fn broker_schema_version(connection: &Connection, contract: &Value) -> Result<u64, String> {
    let mut statement = connection
        .prepare("SELECT value FROM metadata WHERE key='schema_version'")
        .map_err(|_| error("BROKER_SCHEMA_VERSION_MISSING"))?;
    let values = statement
        .query_map([], |row| row.get::<_, String>(0))
        .map_err(|_| error("BROKER_SCHEMA_VERSION_MISSING"))?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| error("BROKER_SCHEMA_VERSION_MISSING"))?;
    if values.len() != 1 {
        return Err(error("BROKER_SCHEMA_VERSION_MISSING"));
    }
    let version = values[0]
        .parse::<u64>()
        .map_err(|_| error("BROKER_SCHEMA_VERSION_UNSUPPORTED"))?;
    if version != contract_u64(contract, "broker_schema_version")? {
        return Err(error("BROKER_SCHEMA_VERSION_UNSUPPORTED"));
    }
    Ok(version)
}

fn float_tag(value: f64) -> Result<Value, String> {
    if !value.is_finite() {
        return Err(error("BROKER_ROW_TYPE_INVALID"));
    }
    let mut output = Map::new();
    output.insert(
        "$f64".to_owned(),
        Value::String(format!("{:016x}", value.to_bits())),
    );
    Ok(Value::Object(output))
}

fn normalize_json(value: Value) -> Result<Value, String> {
    match value {
        Value::Null | Value::Bool(_) | Value::String(_) => Ok(value),
        Value::Number(number) => {
            if number.is_i64() || number.is_u64() {
                Ok(Value::Number(number))
            } else {
                number
                    .as_f64()
                    .ok_or_else(|| error("BROKER_JSON_INVALID"))
                    .and_then(float_tag)
            }
        }
        Value::Array(values) => values
            .into_iter()
            .map(normalize_json)
            .collect::<Result<Vec<_>, _>>()
            .map(Value::Array),
        Value::Object(values) => values
            .into_iter()
            .map(|(key, value)| normalize_json(value).map(|normalized| (key, normalized)))
            .collect::<Result<Map<_, _>, _>>()
            .map(Value::Object),
    }
}

fn json_column(value: &str) -> Result<Value, String> {
    let parsed = serde_json::from_str(value).map_err(|_| error("BROKER_JSON_INVALID"))?;
    normalize_json(parsed)
}

fn normalized_cell(
    value: ValueRef<'_>,
    declared_type: &str,
    nullable: bool,
    boolean: bool,
    json: bool,
) -> Result<Value, String> {
    if matches!(value, ValueRef::Null) {
        return if nullable {
            Ok(Value::Null)
        } else {
            Err(error("BROKER_ROW_TYPE_INVALID"))
        };
    }
    if boolean {
        return match value {
            ValueRef::Integer(0) => Ok(Value::Bool(false)),
            ValueRef::Integer(1) => Ok(Value::Bool(true)),
            _ => Err(error("BROKER_ROW_TYPE_INVALID")),
        };
    }
    if json {
        return match value {
            ValueRef::Text(text) => std::str::from_utf8(text)
                .map_err(|_| error("BROKER_ROW_TYPE_INVALID"))
                .and_then(json_column),
            _ => Err(error("BROKER_ROW_TYPE_INVALID")),
        };
    }
    match (declared_type, value) {
        ("TEXT", ValueRef::Text(text)) => std::str::from_utf8(text)
            .map(str::to_owned)
            .map(Value::String)
            .map_err(|_| error("BROKER_ROW_TYPE_INVALID")),
        ("INTEGER", ValueRef::Integer(number)) => Ok(Value::Number(Number::from(number))),
        ("REAL", ValueRef::Real(number)) => float_tag(number),
        ("REAL", ValueRef::Integer(number)) => float_tag(number as f64),
        (_, ValueRef::Blob(_)) => Err(error("BROKER_ROW_TYPE_INVALID")),
        _ => Err(error("BROKER_ROW_TYPE_INVALID")),
    }
}

fn table_rows(
    connection: &Connection,
    table: &Value,
    expected_project_id: &str,
) -> Result<Vec<Value>, String> {
    let table_name = contract_string(table, "name")?;
    let columns = contract_array(table, "columns")?;
    let order_by = contract_array(table, "order_by")?
        .iter()
        .map(|value| {
            value
                .as_str()
                .map(|name| format!("\"{name}\""))
                .ok_or_else(|| error("BROKER_CONTRACT_INVALID"))
        })
        .collect::<Result<Vec<_>, _>>()?
        .join(",");
    let column_names = columns
        .iter()
        .map(|column| contract_string(column, "name"))
        .collect::<Result<Vec<_>, _>>()?;
    let select = column_names
        .iter()
        .map(|name| format!("\"{name}\""))
        .collect::<Vec<_>>()
        .join(",");
    let json_columns = contract_array(table, "json_columns")?
        .iter()
        .map(|value| {
            value
                .as_str()
                .map(str::to_owned)
                .ok_or_else(|| error("BROKER_CONTRACT_INVALID"))
        })
        .collect::<Result<BTreeSet<_>, _>>()?;
    let boolean_columns = contract_array(table, "boolean_columns")?
        .iter()
        .map(|value| {
            value
                .as_str()
                .map(str::to_owned)
                .ok_or_else(|| error("BROKER_CONTRACT_INVALID"))
        })
        .collect::<Result<BTreeSet<_>, _>>()?;
    let sql = format!("SELECT {select} FROM \"{table_name}\" ORDER BY {order_by}");
    let mut statement = connection
        .prepare(&sql)
        .map_err(|_| error("BROKER_ROW_READ_FAILED"))?;
    let mut query = statement
        .query([])
        .map_err(|_| error("BROKER_ROW_READ_FAILED"))?;
    let mut output = Vec::new();
    while let Some(row) = query
        .next()
        .map_err(|_| error("BROKER_ROW_READ_FAILED"))?
    {
        let mut normalized = Map::new();
        for (index, column) in columns.iter().enumerate() {
            let name = column_names[index];
            let declared_type = contract_string(column, "type")?;
            let nullable = !contract_bool(column, "not_null")?
                && contract_u64(column, "primary_key_position")? == 0;
            let value = row
                .get_ref(index)
                .map_err(|_| error("BROKER_ROW_READ_FAILED"))?;
            normalized.insert(
                name.to_owned(),
                normalized_cell(
                    value,
                    declared_type,
                    nullable,
                    boolean_columns.contains(name),
                    json_columns.contains(name),
                )?,
            );
        }
        if table_name == "jobs"
            && normalized.get("project_id").and_then(Value::as_str)
                != Some(expected_project_id)
        {
            return Err(error("BROKER_JOB_PROJECT_MISMATCH"));
        }
        output.push(Value::Object(normalized));
    }
    Ok(output)
}

/// Produces a canonical read-only logical snapshot of one quiescent broker database.
///
/// # Errors
///
/// Returns a stable `BROKER_*` error code when project binding, path confinement,
/// sidecar policy, SQLite open mode, schema validation, row normalization, or
/// post-read mutation checks fail.
#[allow(clippy::too_many_lines)]
pub fn snapshot_broker_database_json(
    project_root: &str,
    database_path: &str,
    expected_project_id: &str,
) -> Result<String, String> {
    let contract = contract()?;
    if contract_string(&contract, "database_name")? != DATABASE_NAME {
        return Err(error("BROKER_CONTRACT_INVALID"));
    }
    let (root, actual_project_id) =
        canonical_project_root(project_root, expected_project_id)?;
    let (database, relative_path) = relative_database_path(&root, database_path)?;
    assert_no_sidecars(&database)?;
    let before = file_identity(&database)?;
    let connection = open_database(&database)?;

    schema_objects(&connection, &contract)?;
    for table in contract_array(&contract, "tables")? {
        table_columns(&connection, table)?;
    }
    indexes(&connection, &contract)?;
    foreign_keys(&connection, &contract)?;
    let schema_version = broker_schema_version(&connection, &contract)?;

    let mut tables = Map::new();
    let mut row_counts = Map::new();
    for table in contract_array(&contract, "tables")? {
        let name = contract_string(table, "name")?;
        let rows = table_rows(&connection, table, expected_project_id)?;
        row_counts.insert(name.to_owned(), Value::Number(Number::from(rows.len())));
        tables.insert(name.to_owned(), Value::Array(rows));
    }
    drop(connection);

    let after = file_identity(&database)?;
    assert_no_sidecars(&database)?;
    if before != after {
        return Err(error("BROKER_DATABASE_CHANGED_DURING_READ"));
    }

    let mut project_binding = Map::new();
    project_binding.insert(
        "actual".to_owned(),
        Value::String(actual_project_id.clone()),
    );
    project_binding.insert(
        "expected".to_owned(),
        Value::String(expected_project_id.to_owned()),
    );
    project_binding.insert("matched".to_owned(), Value::Bool(true));

    let mut database_value = Map::new();
    database_value.insert(
        "open_mode".to_owned(),
        Value::String("read-only-immutable".to_owned()),
    );
    database_value.insert("query_only".to_owned(), Value::Bool(true));
    database_value.insert("quiescent".to_owned(), Value::Bool(true));
    database_value.insert("relative_path".to_owned(), Value::String(relative_path));
    database_value.insert("sidecars_present".to_owned(), Value::Bool(false));

    let mut mutation = Map::new();
    mutation.insert("database".to_owned(), Value::Bool(false));
    mutation.insert("filesystem".to_owned(), Value::Bool(false));
    mutation.insert("sidecars".to_owned(), Value::Bool(false));

    let mut payload = Map::new();
    payload.insert(
        "broker_schema_version".to_owned(),
        Value::Number(Number::from(schema_version)),
    );
    payload.insert(
        "claim".to_owned(),
        Value::String(
            "RUST_BROKER_SQLITE_LOGICAL_READ_PARITY_PROVEN_R9_FIXTURES".to_owned(),
        ),
    );
    payload.insert(
        "contract_version".to_owned(),
        Value::Number(Number::from(contract_u64(&contract, "contract_version")?)),
    );
    payload.insert("database".to_owned(), Value::Object(database_value));
    payload.insert("mutation".to_owned(), Value::Object(mutation));
    payload.insert("ok".to_owned(), Value::Bool(true));
    payload.insert(
        "project_binding".to_owned(),
        Value::Object(project_binding),
    );
    payload.insert(
        "project_id".to_owned(),
        Value::String(actual_project_id),
    );
    payload.insert("row_counts".to_owned(), Value::Object(row_counts));
    payload.insert(
        "schema_version".to_owned(),
        Value::Number(Number::from(contract_u64(&contract, "schema_version")?)),
    );
    payload.insert(
        "snapshot_id".to_owned(),
        Value::String(contract_string(&contract, "snapshot_id")?.to_owned()),
    );
    payload.insert("tables".to_owned(), Value::Object(tables));

    let encoded = serde_json::to_vec(&Value::Object(payload.clone()))
        .map_err(|_| error("BROKER_ROW_TYPE_INVALID"))?;
    payload.insert(
        "snapshot_hash".to_owned(),
        Value::String(sha256_hex(&encoded)),
    );
    serde_json::to_string(&Value::Object(payload))
        .map_err(|_| error("BROKER_ROW_TYPE_INVALID"))
}

#[cfg(test)]
mod tests {
    use super::{contract, snapshot_broker_database_json};

    #[test]
    fn embeds_the_r9_contract() {
        let value = contract().expect("contract");
        assert_eq!(value["snapshot_id"], "syntavra-broker-snapshot-v1");
        assert_eq!(value["broker_schema_version"], 2);
    }

    #[test]
    fn rejects_invalid_expected_project_id_before_open() {
        assert_eq!(
            snapshot_broker_database_json(".", "broker.sqlite3", "invalid"),
            Err("BROKER_EXPECTED_PROJECT_INVALID".to_owned())
        );
    }
}
