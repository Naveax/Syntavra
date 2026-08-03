#![forbid(unsafe_code)]

use std::fs;
use std::path::Path;

use serde_json::Value;

const HANDLE_PREFIX: &str = "sc://sha256/";
const MAX_METADATA_BYTES: u64 = 16 * 1024 * 1024;

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "evidence" && action == "describe")
}

fn argument_after<'a>(arguments: &'a [String], action: &str) -> Result<&'a str, String> {
    let index = arguments
        .iter()
        .position(|value| value == action)
        .ok_or_else(|| "EVIDENCE_DESCRIBE_ACTION_MISSING".to_owned())?;
    arguments
        .get(index + 1)
        .map(String::as_str)
        .ok_or_else(|| "EVIDENCE_HANDLE_MISSING".to_owned())
}

fn digest_from_handle(handle: &str) -> Result<&str, String> {
    let digest = handle
        .strip_prefix(HANDLE_PREFIX)
        .ok_or_else(|| "EVIDENCE_HANDLE_INVALID".to_owned())?;
    if digest.len() != 64
        || !digest
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err("EVIDENCE_DIGEST_INVALID".to_owned());
    }
    Ok(digest)
}

fn schema_version(value: &Value) -> Result<i64, String> {
    let value = value
        .get("schema_version")
        .ok_or_else(|| "EVIDENCE_METADATA_SCHEMA_MISSING".to_owned())?;
    match value {
        Value::Number(number) => number
            .as_i64()
            .ok_or_else(|| "EVIDENCE_METADATA_SCHEMA_INVALID".to_owned()),
        Value::String(text) => text
            .parse::<i64>()
            .map_err(|_| "EVIDENCE_METADATA_SCHEMA_INVALID".to_owned()),
        _ => Err("EVIDENCE_METADATA_SCHEMA_INVALID".to_owned()),
    }
}

pub fn execute(
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    let handle = argument_after(arguments, "describe")?;
    let digest = digest_from_handle(handle)?;
    let path = state_root
        .join("evidence")
        .join("metadata")
        .join(format!("{digest}.json"));
    let metadata = fs::symlink_metadata(&path)
        .map_err(|_| "EVIDENCE_METADATA_MISSING".to_owned())?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err("EVIDENCE_METADATA_INVALID_SOURCE".to_owned());
    }
    if metadata.len() > MAX_METADATA_BYTES {
        return Err("EVIDENCE_METADATA_TOO_LARGE".to_owned());
    }
    let value: Value = serde_json::from_slice(
        &fs::read(&path).map_err(|error| format!("EVIDENCE_METADATA_READ_FAILED:{error}"))?,
    )
    .map_err(|_| "EVIDENCE_METADATA_JSON_INVALID".to_owned())?;
    if !value.is_object() {
        return Err("EVIDENCE_METADATA_OBJECT_REQUIRED".to_owned());
    }
    let project = project_root.to_string_lossy();
    let expected_project_id =
        super::super::state_snapshot_contract::project_id_for_root(&project)?;
    if value.get("project_id").and_then(Value::as_str) != Some(expected_project_id.as_str()) {
        return Err("EVIDENCE_SCOPE_MISMATCH".to_owned());
    }
    if schema_version(&value)? != 3 {
        return Err("EVIDENCE_METADATA_SCHEMA_UNSUPPORTED".to_owned());
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::{digest_from_handle, supports};

    #[test]
    fn evidence_describe_command_is_supported() {
        assert!(supports(&["evidence".to_owned(), "describe".to_owned()]));
    }

    #[test]
    fn evidence_handle_is_strict_lower_hex() {
        let valid = format!("sc://sha256/{}", "a".repeat(64));
        assert_eq!(digest_from_handle(&valid), Ok("a".repeat(64).leak()));
        assert!(digest_from_handle("sc://sha256/ABC").is_err());
    }
}
