#![forbid(unsafe_code)]

use std::collections::BTreeMap;
use std::path::Path;

use serde_json::{json, Value};
use sha2::{Digest as _, Sha256};

use super::native_evidence_store::NativeEvidenceStore;

const LOSS_POLICY: &str = "exact-externalized";

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "compress" && action == "verify")
}

fn parse_arguments(arguments: &[String]) -> Result<String, String> {
    let start = arguments
        .windows(2)
        .position(|row| row[0] == "compress" && row[1] == "verify")
        .map(|index| index + 2)
        .ok_or_else(|| "COMPRESSION_VERIFY_COMMAND_MISSING".to_owned())?;
    let tail = &arguments[start..];
    if tail.is_empty() {
        return Err("COMPRESSION_ID_MISSING".to_owned());
    }
    if tail.len() != 1 {
        return Err(format!("COMPRESSION_ARGUMENT_UNEXPECTED:{}", tail[1]));
    }
    let compression_id = tail[0].clone();
    if compression_id.is_empty() || compression_id.starts_with('-') {
        return Err("COMPRESSION_ID_INVALID".to_owned());
    }
    Ok(compression_id)
}

fn canonical_json(value: &Value) -> Result<Vec<u8>, String> {
    serde_json::to_vec(value).map_err(|error| format!("COMPRESSION_JSON_SERIALIZE_FAILED:{error}"))
}

fn hex(bytes: &[u8]) -> String {
    let mut rendered = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        use std::fmt::Write as _;
        write!(&mut rendered, "{byte:02x}").expect("writing to String cannot fail");
    }
    rendered
}

fn string_field(value: &Value, name: &str) -> Result<String, String> {
    value[name]
        .as_str()
        .map(ToOwned::to_owned)
        .ok_or_else(|| format!("COMPRESSION_{}_INVALID", name.to_ascii_uppercase()))
}

fn number_field(value: &Value, name: &str) -> Result<Value, String> {
    if value[name].is_number() {
        Ok(value[name].clone())
    } else {
        Err(format!(
            "COMPRESSION_{}_INVALID",
            name.to_ascii_uppercase()
        ))
    }
}

fn chunk_handles(description: &Value) -> Result<Vec<String>, String> {
    description["chunks"]
        .as_array()
        .ok_or_else(|| "COMPRESSION_CHUNKS_INVALID".to_owned())?
        .iter()
        .map(|row| {
            row["chunk_handle"]
                .as_str()
                .map(ToOwned::to_owned)
                .ok_or_else(|| "COMPRESSION_CHUNK_HANDLE_INVALID".to_owned())
        })
        .collect()
}

fn receipt_hash(description: &Value, handles: &[String]) -> Result<String, String> {
    let visible_text = string_field(description, "visible_text")?;
    let mut payload = BTreeMap::<String, Value>::new();
    payload.insert(
        "chunk_handles".to_owned(),
        Value::Array(handles.iter().cloned().map(Value::String).collect()),
    );
    payload.insert(
        "chunk_size".to_owned(),
        number_field(description, "chunk_size")?,
    );
    payload.insert(
        "compression_id".to_owned(),
        Value::String(string_field(description, "compression_id")?),
    );
    payload.insert(
        "content_type".to_owned(),
        Value::String(string_field(description, "content_type")?),
    );
    payload.insert(
        "exact_handle".to_owned(),
        Value::String(string_field(description, "exact_handle")?),
    );
    payload.insert(
        "loss_policy".to_owned(),
        Value::String(LOSS_POLICY.to_owned()),
    );
    payload.insert("metadata".to_owned(), description["metadata"].clone());
    payload.insert(
        "original_bytes".to_owned(),
        number_field(description, "original_bytes")?,
    );
    payload.insert(
        "visible_bytes".to_owned(),
        Value::from(visible_text.len()),
    );
    let value = serde_json::to_value(payload)
        .map_err(|error| format!("COMPRESSION_RECEIPT_VALUE_FAILED:{error}"))?;
    Ok(hex(&Sha256::digest(canonical_json(&value)?)))
}

fn verify_roundtrip(
    description: &Value,
    evidence: &NativeEvidenceStore,
) -> Result<bool, String> {
    let exact_handle = string_field(description, "exact_handle")?;
    let handles = chunk_handles(description)?;
    let full = evidence.get(&exact_handle)?;
    let mut rebuilt = Vec::new();
    for handle in &handles {
        rebuilt.extend_from_slice(&evidence.get(handle)?);
    }
    let original_bytes = description["original_bytes"]
        .as_i64()
        .ok_or_else(|| "COMPRESSION_ORIGINAL_BYTES_INVALID".to_owned())?;
    let length_matches = i64::try_from(full.len()).is_ok_and(|length| length == original_bytes);
    let expected_receipt = string_field(description, "receipt_hash")?;
    let actual_receipt = receipt_hash(description, &handles)?;
    Ok(full == rebuilt && length_matches && actual_receipt == expected_receipt)
}

pub fn execute(
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    let compression_id = parse_arguments(arguments)?;
    let database_path = super::native_compress_describe::initialize_database(state_root)?;
    let description =
        super::native_compress_describe::describe(&database_path, &compression_id)?;
    let project_id =
        super::state_snapshot_contract::project_id_for_root(&project_root.to_string_lossy())?;
    let evidence = NativeEvidenceStore::open(state_root, &project_id)?;
    let ok = verify_roundtrip(&description, &evidence)?;
    Ok(json!({
        "compression_id": compression_id,
        "ok": ok,
    }))
}

#[cfg(test)]
mod tests {
    use super::{parse_arguments, receipt_hash, supports};
    use serde_json::json;

    #[test]
    fn routes_compress_verify_only() {
        assert!(supports(&["compress".to_owned(), "verify".to_owned()]));
        assert!(!supports(&["compress".to_owned(), "get".to_owned()]));
    }

    #[test]
    fn parses_exactly_one_compression_identifier() {
        assert_eq!(
            parse_arguments(&[
                "compress".to_owned(),
                "verify".to_owned(),
                "ccr-example".to_owned(),
            ])
            .expect("parse"),
            "ccr-example"
        );
        assert!(parse_arguments(&[
            "compress".to_owned(),
            "verify".to_owned(),
            "ccr-example".to_owned(),
            "unexpected".to_owned(),
        ])
        .is_err());
    }

    #[test]
    fn receipt_uses_description_chunk_size_and_visible_bytes() {
        let description = json!({
            "compression_id": "ccr-example",
            "content_type": "text",
            "original_bytes": 3,
            "visible_text": "β",
            "exact_handle": "sc://sha256/exact",
            "chunk_size": 1024,
            "metadata": {"path": ""},
            "receipt_hash": "unused",
        });
        let first = receipt_hash(&description, &["sc://sha256/chunk".to_owned()])
            .expect("receipt");
        let mut changed = description.clone();
        changed["chunk_size"] = json!(2048);
        let second = receipt_hash(&changed, &["sc://sha256/chunk".to_owned()])
            .expect("receipt");
        assert_ne!(first, second);
    }
}
