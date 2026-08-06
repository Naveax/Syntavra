#![forbid(unsafe_code)]

use std::collections::BTreeMap;
use std::fs;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};
use sha2::{Digest as _, Sha256};

const CLAIM_BOUNDARY: &str = "Certified requires a live-host external execution receipt";

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action]
        if root == "run" && action == "adapter-conformance")
}

fn command_start(arguments: &[String]) -> Result<usize, String> {
    arguments
        .windows(2)
        .position(|row| row[0] == "run" && row[1] == "adapter-conformance")
        .map(|index| index + 2)
        .ok_or_else(|| "ADAPTER_CONFORMANCE_COMMAND_MISSING".to_owned())
}

fn adapter_id(arguments: &[String]) -> Result<String, String> {
    let index = command_start(arguments)?;
    let value = arguments
        .get(index)
        .ok_or_else(|| "adapter-conformance adapter_id is required".to_owned())?;
    if value.starts_with('-') {
        return Err("adapter-conformance adapter_id is required".to_owned());
    }
    Ok(value.clone())
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
        (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096)
            / 365;
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
        .map_err(|error| format!("ADAPTER_CONFORMANCE_CLOCK_FAILED:{error}"))?;
    let seconds = duration.as_secs() as i64;
    let days = seconds / 86_400;
    let seconds_of_day = seconds % 86_400;
    let hour = seconds_of_day / 3_600;
    let minute = (seconds_of_day % 3_600) / 60;
    let second = seconds_of_day % 60;
    let (year, month, day) = civil_from_days(days);
    Ok(format!(
        "{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}.{:06}+00:00",
        duration.subsec_micros()
    ))
}

fn canonical_body(
    adapter_id: &str,
    maturity: &str,
    created_at: &str,
    detection: &Value,
    capabilities: &Value,
) -> BTreeMap<String, Value> {
    BTreeMap::from([
        ("adapter_id".to_owned(), json!(adapter_id)),
        ("capabilities".to_owned(), capabilities.clone()),
        ("changed_paths".to_owned(), json!([])),
        ("checks".to_owned(), json!({"detection": detection})),
        ("created_at".to_owned(), json!(created_at)),
        (
            "detected".to_owned(),
            detection.get("detected").cloned().unwrap_or(Value::Bool(false)),
        ),
        ("maturity".to_owned(), json!(maturity)),
        ("ok".to_owned(), Value::Bool(true)),
        ("operation".to_owned(), json!("conformance")),
    ])
}

fn receipt_hash(body: &BTreeMap<String, Value>) -> Result<String, String> {
    let canonical = serde_json::to_vec(body)
        .map_err(|error| format!("ADAPTER_CONFORMANCE_CANONICALIZE_FAILED:{error}"))?;
    Ok(format!("sha256:{:x}", Sha256::digest(canonical)))
}

fn write_receipt(state_root: &Path, receipt: &Value, receipt_id: &str) -> Result<(), String> {
    let digest = receipt_id
        .strip_prefix("sha256:")
        .ok_or_else(|| "ADAPTER_CONFORMANCE_RECEIPT_ID_INVALID".to_owned())?;
    let directory = state_root.join("unified").join("adapter-receipts");
    fs::create_dir_all(&directory)
        .map_err(|error| format!("ADAPTER_CONFORMANCE_RECEIPT_DIRECTORY_FAILED:{error}"))?;
    let rendered = serde_json::to_string_pretty(receipt)
        .map_err(|error| format!("ADAPTER_CONFORMANCE_RECEIPT_SERIALIZE_FAILED:{error}"))?
        + "\n";
    fs::write(directory.join(format!("{digest}.json")), rendered)
        .map_err(|error| format!("ADAPTER_CONFORMANCE_RECEIPT_WRITE_FAILED:{error}"))
}

pub fn execute(
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    super::native_platform_state::initialize(state_root)?;
    let adapter_id = adapter_id(arguments)?;
    let contract = super::native_run_adapters::contract(&adapter_id)?;
    let detection = super::native_run_adapters::detection(&adapter_id, project_root)?;
    let detected = detection["detected"].as_bool().unwrap_or(false);
    let maturity = if detected { "Configured" } else { "Contract" };
    let created_at = utc_now()?;
    let capabilities = contract["capabilities"].clone();
    let body = canonical_body(
        &adapter_id,
        maturity,
        &created_at,
        &detection,
        &capabilities,
    );
    let receipt_id = receipt_hash(&body)?;
    let receipt = json!({
        "receipt_id": receipt_id,
        "adapter_id": adapter_id,
        "maturity": maturity,
        "operation": "conformance",
        "ok": true,
        "created_at": created_at,
        "detected": detected,
        "changed_paths": [],
        "capabilities": capabilities,
        "checks": {"detection": detection},
        "rollback": {},
        "claim_boundary": CLAIM_BOUNDARY,
    });
    write_receipt(state_root, &receipt, receipt["receipt_id"].as_str().unwrap_or_default())?;
    Ok(receipt)
}

#[cfg(test)]
mod tests {
    use super::{civil_from_days, supports};

    #[test]
    fn epoch_date_is_correct() {
        assert_eq!(civil_from_days(0), (1970, 1, 1));
    }

    #[test]
    fn routes_adapter_conformance_only() {
        assert!(supports(&[
            "run".to_owned(),
            "adapter-conformance".to_owned()
        ]));
        assert!(!supports(&["run".to_owned(), "adapters".to_owned()]));
    }
}
