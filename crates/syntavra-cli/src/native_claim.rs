#![forbid(unsafe_code)]

use std::fs;
use std::path::Path;

use serde_json::{json, Map, Value};
use syntavra_core::sha256_hex;

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root] if root == "claim")
}

fn argument_after<'a>(arguments: &'a [String], command: &str) -> Result<&'a str, String> {
    let index = arguments
        .iter()
        .position(|value| value == command)
        .ok_or_else(|| "CLAIM_COMMAND_MISSING".to_owned())?;
    arguments
        .get(index + 1)
        .map(String::as_str)
        .ok_or_else(|| "CLAIM_RECEIPT_MISSING".to_owned())
}

fn regular_file_bytes(path: &Path, code: &str) -> Result<Vec<u8>, String> {
    let metadata = fs::metadata(path).map_err(|_| code.to_owned())?;
    if !metadata.is_file() {
        return Err(code.to_owned());
    }
    fs::read(path).map_err(|_| code.to_owned())
}

fn canonical_bytes(value: &Value) -> Result<Vec<u8>, String> {
    serde_json::to_vec(value).map_err(|_| "CLAIM_CANONICAL_JSON_FAILED".to_owned())
}

fn artifact_hashes(value: &Map<String, Value>) -> Vec<(String, Option<String>)> {
    match value.get("artifact_hashes").and_then(Value::as_object) {
        Some(rows) => rows
            .iter()
            .map(|(name, digest)| (name.clone(), digest.as_str().map(str::to_owned)))
            .collect(),
        None => Vec::new(),
    }
}

fn verify(path: &Path) -> Result<Value, String> {
    let receipt_bytes = regular_file_bytes(path, "CLAIM_RECEIPT_INVALID")?;
    let mut value: Value = serde_json::from_slice(&receipt_bytes)
        .map_err(|_| "CLAIM_RECEIPT_JSON_INVALID".to_owned())?;
    let saved = value
        .as_object_mut()
        .ok_or_else(|| "CLAIM_RECEIPT_OBJECT_REQUIRED".to_owned())?
        .remove("receipt_hash");
    let mut reasons = Vec::<String>::new();
    let calculated = sha256_hex(&canonical_bytes(&value)?);
    if saved.as_ref().and_then(Value::as_str) != Some(calculated.as_str()) {
        reasons.push("receipt-hash-mismatch".to_owned());
    }
    let parent = path.parent().unwrap_or_else(|| Path::new(""));
    let object = value
        .as_object()
        .ok_or_else(|| "CLAIM_RECEIPT_OBJECT_REQUIRED".to_owned())?;
    for (name, expected) in artifact_hashes(object) {
        let candidate = parent.join(&name);
        let valid = expected.is_some_and(|expected| {
            regular_file_bytes(&candidate, "CLAIM_ARTIFACT_INVALID")
                .map(|bytes| sha256_hex(&bytes) == expected)
                .unwrap_or(false)
        });
        if !valid {
            reasons.push(format!("artifact-invalid:{name}"));
        }
    }
    let claim = object.get("claim").cloned().unwrap_or(Value::Null);
    let status = object.get("status").cloned().unwrap_or(Value::Null);
    if status.as_str() == Some("PASS") && claim.as_str() == Some("5X_NOT_PROVEN") {
        reasons.push("contradictory-status".to_owned());
    }
    Ok(json!({
        "ok": reasons.is_empty(),
        "reasons": reasons,
        "claim": claim,
        "status": status,
    }))
}

fn emit_and_exit(value: &Value, exit_code: i32) -> ! {
    println!(
        "{}",
        serde_json::to_string_pretty(value).unwrap_or_else(|_| "{\"ok\":false}".to_owned())
    );
    std::process::exit(exit_code)
}

pub fn execute(arguments: &[String]) -> Result<Value, String> {
    let path = Path::new(argument_after(arguments, "claim")?);
    let value = verify(path)?;
    if value.get("ok").and_then(Value::as_bool) == Some(false) {
        emit_and_exit(&value, 3);
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn claim_command_is_supported() {
        assert!(supports(&["claim".to_owned()]));
    }
}
