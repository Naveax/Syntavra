#![forbid(unsafe_code)]

use std::fs::{self, OpenOptions};
use std::io::Write as _;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use rand::{rngs::OsRng, RngCore as _};
use rusqlite::{params, Connection, Row};
use serde_json::{json, Map, Value};
use sha2::{Digest as _, Sha256};

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct ArtifactRecord {
    pub(crate) artifact_id: String,
    pub(crate) sha256: String,
    pub(crate) media_type: String,
    pub(crate) kind: String,
    pub(crate) byte_count: u64,
    pub(crate) created_at: String,
    pub(crate) object_path: String,
    pub(crate) metadata: Value,
}

impl ArtifactRecord {
    pub(crate) fn value(&self) -> Value {
        json!({
            "artifact_id": self.artifact_id,
            "sha256": self.sha256,
            "media_type": self.media_type,
            "kind": self.kind,
            "byte_count": self.byte_count,
            "created_at": self.created_at,
            "object_path": self.object_path,
            "metadata": self.metadata,
        })
    }
}

pub(crate) struct NativeArtifactStore {
    root: PathBuf,
    objects: PathBuf,
    db_path: PathBuf,
}

impl NativeArtifactStore {
    pub(crate) fn open(state_root: &Path) -> Result<Self, String> {
        let root = state_root.join("unified").join("artifacts");
        let objects = root.join("objects");
        fs::create_dir_all(&objects)
            .map_err(|error| format!("ARTIFACT_OBJECT_DIRECTORY_CREATE_FAILED:{error}"))?;
        let db_path = root.join("artifacts.sqlite3");
        let connection = open_database(&db_path)?;
        connection
            .execute_batch(
                r#"
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    sha256 TEXT NOT NULL UNIQUE,
                    media_type TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    byte_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    object_path TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_artifacts_kind
                    ON artifacts(kind, created_at);
                "#,
            )
            .map_err(|error| format!("ARTIFACT_SCHEMA_INITIALIZE_FAILED:{error}"))?;
        Ok(Self {
            root,
            objects,
            db_path,
        })
    }

    pub(crate) fn root(&self) -> &Path {
        &self.root
    }

    pub(crate) fn put(
        &self,
        data: &[u8],
        media_type: &str,
        kind: &str,
        metadata: &Value,
    ) -> Result<ArtifactRecord, String> {
        let digest = format!("{:x}", Sha256::digest(data));
        let artifact_id = format!("sha256:{digest}");
        let target = self.object_path(&digest)?;
        if !target.exists() {
            write_object_once(&target, data)?;
        }
        let byte_count = u64::try_from(data.len())
            .map_err(|_| "ARTIFACT_BYTE_COUNT_INVALID".to_owned())?;
        let created_at = utc_now()?;
        let metadata_json = canonical_json(metadata)?;
        let connection = open_database(&self.db_path)?;
        connection
            .execute(
                r#"
                INSERT OR IGNORE INTO artifacts(
                    artifact_id, sha256, media_type, kind, byte_count,
                    created_at, object_path, metadata_json
                ) VALUES(?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)
                "#,
                params![
                    artifact_id,
                    digest,
                    media_type,
                    kind,
                    i64::try_from(byte_count)
                        .map_err(|_| "ARTIFACT_BYTE_COUNT_SQL_INVALID".to_owned())?,
                    created_at,
                    target.to_string_lossy(),
                    metadata_json,
                ],
            )
            .map_err(|error| format!("ARTIFACT_METADATA_WRITE_FAILED:{error}"))?;
        let mut statement = connection
            .prepare("SELECT * FROM artifacts WHERE artifact_id=?1")
            .map_err(|error| format!("ARTIFACT_METADATA_QUERY_PREPARE_FAILED:{error}"))?;
        statement
            .query_row(params![artifact_id], record_from_row)
            .map_err(|error| format!("ARTIFACT_METADATA_QUERY_FAILED:{error}"))
    }

    pub(crate) fn record(&self, artifact_id: &str) -> Result<ArtifactRecord, String> {
        let connection = open_database(&self.db_path)?;
        let mut statement = connection
            .prepare("SELECT * FROM artifacts WHERE artifact_id=?1")
            .map_err(|error| format!("ARTIFACT_METADATA_QUERY_PREPARE_FAILED:{error}"))?;
        statement
            .query_row(params![artifact_id], record_from_row)
            .map_err(|error| format!("ARTIFACT_NOT_FOUND:{error}"))
    }

    pub(crate) fn read(&self, artifact_id: &str) -> Result<Vec<u8>, String> {
        let record = self.record(artifact_id)?;
        let data = fs::read(&record.object_path)
            .map_err(|error| format!("ARTIFACT_OBJECT_READ_FAILED:{error}"))?;
        let digest = format!("{:x}", Sha256::digest(&data));
        if digest != record.sha256 {
            return Err(format!("ARTIFACT_INTEGRITY_FAILURE:{artifact_id}"));
        }
        Ok(data)
    }

    fn object_path(&self, digest: &str) -> Result<PathBuf, String> {
        if digest.len() != 64 || !digest.bytes().all(|byte| byte.is_ascii_hexdigit()) {
            return Err("ARTIFACT_DIGEST_INVALID".to_owned());
        }
        Ok(self
            .objects
            .join(&digest[..2])
            .join(&digest[2..4])
            .join(digest))
    }
}

fn open_database(path: &Path) -> Result<Connection, String> {
    let connection = Connection::open(path)
        .map_err(|error| format!("ARTIFACT_DATABASE_OPEN_FAILED:{error}"))?;
    connection
        .execute_batch(
            "PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA synchronous=FULL; PRAGMA busy_timeout=30000;",
        )
        .map_err(|error| format!("ARTIFACT_DATABASE_PRAGMA_FAILED:{error}"))?;
    Ok(connection)
}

fn record_from_row(row: &Row<'_>) -> rusqlite::Result<ArtifactRecord> {
    let metadata_json = row.get::<_, String>("metadata_json")?;
    let metadata = serde_json::from_str::<Value>(&metadata_json).map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(
            metadata_json.len(),
            rusqlite::types::Type::Text,
            Box::new(error),
        )
    })?;
    let byte_count = row.get::<_, i64>("byte_count")?;
    let byte_count = u64::try_from(byte_count).map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(
            4,
            rusqlite::types::Type::Integer,
            Box::new(error),
        )
    })?;
    Ok(ArtifactRecord {
        artifact_id: row.get("artifact_id")?,
        sha256: row.get("sha256")?,
        media_type: row.get("media_type")?,
        kind: row.get("kind")?,
        byte_count,
        created_at: row.get("created_at")?,
        object_path: row.get("object_path")?,
        metadata,
    })
}

fn canonical_json(value: &Value) -> Result<String, String> {
    fn sorted(value: &Value) -> Value {
        match value {
            Value::Array(values) => Value::Array(values.iter().map(sorted).collect()),
            Value::Object(values) => {
                let mut keys = values.keys().collect::<Vec<_>>();
                keys.sort_unstable();
                let mut output = Map::new();
                for key in keys {
                    output.insert(key.clone(), sorted(&values[key]));
                }
                Value::Object(output)
            }
            _ => value.clone(),
        }
    }
    serde_json::to_string(&sorted(value))
        .map_err(|error| format!("ARTIFACT_METADATA_SERIALIZE_FAILED:{error}"))
}

fn write_object_once(target: &Path, data: &[u8]) -> Result<(), String> {
    let parent = target
        .parent()
        .ok_or_else(|| "ARTIFACT_OBJECT_PARENT_MISSING".to_owned())?;
    fs::create_dir_all(parent)
        .map_err(|error| format!("ARTIFACT_OBJECT_PARENT_CREATE_FAILED:{error}"))?;
    let mut random = [0_u8; 6];
    OsRng.fill_bytes(&mut random);
    let random = random
        .iter()
        .map(|value| format!("{value:02x}"))
        .collect::<String>();
    let name = target
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| "ARTIFACT_OBJECT_NAME_INVALID".to_owned())?;
    let temporary = parent.join(format!(".{name}.{random}.tmp"));
    let result = (|| -> std::io::Result<()> {
        let mut output = OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&temporary)?;
        output.write_all(data)?;
        output.flush()?;
        output.sync_all()?;
        fs::rename(&temporary, target)
    })();
    if let Err(error) = result {
        let target_exists = target.is_file();
        let _ = fs::remove_file(&temporary);
        if target_exists {
            return Ok(());
        }
        return Err(format!("ARTIFACT_OBJECT_WRITE_FAILED:{error}"));
    }
    Ok(())
}

fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let shifted = days + 719_468;
    let era = if shifted >= 0 {
        shifted
    } else {
        shifted - 146_096
    } / 146_097;
    let day_of_era = shifted - era * 146_097;
    let year_of_era =
        (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_prime = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_prime + 2) / 5 + 1;
    let month = month_prime + if month_prime < 10 { 3 } else { -9 };
    let year = year + if month <= 2 { 1 } else { 0 };
    (year, month as u32, day as u32)
}

fn utc_now() -> Result<String, String> {
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("ARTIFACT_CLOCK_FAILED:{error}"))?;
    let seconds = i64::try_from(duration.as_secs())
        .map_err(|_| "ARTIFACT_CLOCK_RANGE_INVALID".to_owned())?;
    let days = seconds / 86_400;
    let seconds_of_day = seconds % 86_400;
    let hour = seconds_of_day / 3_600;
    let minute = (seconds_of_day % 3_600) / 60;
    let second = seconds_of_day % 60;
    let (year, month, day) = civil_from_days(days);
    Ok(format!(
        "{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}.{:06}Z",
        duration.subsec_micros()
    ))
}
