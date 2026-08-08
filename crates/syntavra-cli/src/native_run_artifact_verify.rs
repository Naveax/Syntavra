#![forbid(unsafe_code)]

use std::fs;
use std::path::{Path, PathBuf};

use rusqlite::{params, Connection};
use serde_json::{json, Value};
use sha2::{Digest as _, Sha256};

use super::native_artifact_store::NativeArtifactStore;

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "run" && action == "artifact-verify")
}

fn parse_artifact_id(arguments: &[String]) -> Result<Option<String>, String> {
    let start = arguments
        .windows(2)
        .position(|row| row[0] == "run" && row[1] == "artifact-verify")
        .map(|index| index + 2)
        .ok_or_else(|| "ARTIFACT_VERIFY_COMMAND_MISSING".to_owned())?;
    let tail = &arguments[start..];
    let mut artifact_id = None;
    let mut positional_only = false;
    for value in tail {
        if !positional_only && value == "--" {
            positional_only = true;
            continue;
        }
        if !positional_only && value.starts_with('-') {
            return Err(format!("ARTIFACT_VERIFY_OPTION_UNKNOWN:{value}"));
        }
        if artifact_id.replace(value.clone()).is_some() {
            return Err(format!("ARTIFACT_VERIFY_ARGUMENT_UNEXPECTED:{value}"));
        }
    }
    Ok(artifact_id)
}

fn open_database(path: &Path) -> Result<Connection, String> {
    let connection = Connection::open(path)
        .map_err(|error| format!("ARTIFACT_VERIFY_DATABASE_OPEN_FAILED:{error}"))?;
    connection
        .execute_batch(
            "PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA synchronous=FULL; PRAGMA busy_timeout=30000;",
        )
        .map_err(|error| format!("ARTIFACT_VERIFY_DATABASE_PRAGMA_FAILED:{error}"))?;
    Ok(connection)
}

fn row_values(row: &rusqlite::Row<'_>) -> rusqlite::Result<(String, String, PathBuf)> {
    Ok((
        row.get::<_, String>(0)?,
        row.get::<_, String>(1)?,
        PathBuf::from(row.get::<_, String>(2)?),
    ))
}

fn verify_row(
    artifact_id: &str,
    expected_sha256: &str,
    object_path: &Path,
) -> Result<Option<String>, String> {
    if !object_path.is_file() {
        return Ok(Some(format!("missing:{artifact_id}")));
    }
    let data = fs::read(object_path)
        .map_err(|error| format!("ARTIFACT_VERIFY_OBJECT_READ_FAILED:{error}"))?;
    let actual = format!("{:x}", Sha256::digest(&data));
    if actual != expected_sha256 {
        return Ok(Some(format!("hash:{artifact_id}")));
    }
    Ok(None)
}

pub fn execute(arguments: &[String], state_root: &Path) -> Result<Value, String> {
    let artifact_id = parse_artifact_id(arguments)?;
    let store = NativeArtifactStore::open(state_root)?;
    let connection = open_database(&store.root().join("artifacts.sqlite3"))?;
    let sql = if artifact_id.is_some() {
        "SELECT artifact_id, sha256, object_path FROM artifacts WHERE artifact_id = ?1"
    } else {
        "SELECT artifact_id, sha256, object_path FROM artifacts"
    };
    let mut statement = connection
        .prepare(sql)
        .map_err(|error| format!("ARTIFACT_VERIFY_QUERY_PREPARE_FAILED:{error}"))?;

    let mut checked = 0_u64;
    let mut failures = Vec::new();
    if let Some(ref requested) = artifact_id {
        let rows = statement
            .query_map(params![requested], row_values)
            .map_err(|error| format!("ARTIFACT_VERIFY_QUERY_FAILED:{error}"))?;
        for row in rows {
            let (id, sha256, path) =
                row.map_err(|error| format!("ARTIFACT_VERIFY_ROW_FAILED:{error}"))?;
            checked += 1;
            if let Some(failure) = verify_row(&id, &sha256, &path)? {
                failures.push(failure);
            }
        }
    } else {
        let rows = statement
            .query_map([], row_values)
            .map_err(|error| format!("ARTIFACT_VERIFY_QUERY_FAILED:{error}"))?;
        for row in rows {
            let (id, sha256, path) =
                row.map_err(|error| format!("ARTIFACT_VERIFY_ROW_FAILED:{error}"))?;
            checked += 1;
            if let Some(failure) = verify_row(&id, &sha256, &path)? {
                failures.push(failure);
            }
        }
    }

    Ok(json!({
        "ok": failures.is_empty(),
        "checked": checked,
        "failures": failures,
    }))
}

#[cfg(test)]
mod tests {
    use super::{parse_artifact_id, supports};

    #[test]
    fn routes_artifact_verify_only() {
        assert!(supports(&["run".to_owned(), "artifact-verify".to_owned()]));
        assert!(!supports(&["run".to_owned(), "artifact-stats".to_owned()]));
    }

    #[test]
    fn accepts_optional_artifact_id_and_double_dash() {
        assert_eq!(
            parse_artifact_id(&[
                "syntavra".to_owned(),
                "run".to_owned(),
                "artifact-verify".to_owned(),
            ])
            .expect("parse"),
            None
        );
        assert_eq!(
            parse_artifact_id(&[
                "syntavra".to_owned(),
                "run".to_owned(),
                "artifact-verify".to_owned(),
                "--".to_owned(),
                "-value".to_owned(),
            ])
            .expect("parse"),
            Some("-value".to_owned())
        );
    }

    #[test]
    fn rejects_unknown_options_and_extra_positionals() {
        assert!(parse_artifact_id(&[
            "syntavra".to_owned(),
            "run".to_owned(),
            "artifact-verify".to_owned(),
            "--unknown".to_owned(),
        ])
        .is_err());
        assert!(parse_artifact_id(&[
            "syntavra".to_owned(),
            "run".to_owned(),
            "artifact-verify".to_owned(),
            "one".to_owned(),
            "two".to_owned(),
        ])
        .is_err());
    }
}
