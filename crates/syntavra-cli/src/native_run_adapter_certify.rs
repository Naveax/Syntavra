#![forbid(unsafe_code)]

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Map, Value};
use sha2::{Digest as _, Sha256};

const CLAIM_BOUNDARY: &str = "Certified requires a live-host external execution receipt";
const REQUIRED: [&str; 8] = [
    "artifact_hash",
    "clean_install",
    "context_interception",
    "host",
    "host_version",
    "security_denial",
    "session_restore",
    "tool_interception",
];
const BOOLEAN_CHECKS: [&str; 5] = [
    "clean_install",
    "context_interception",
    "security_denial",
    "session_restore",
    "tool_interception",
];

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "run" && action == "adapter-certify")
}

fn command_start(arguments: &[String]) -> Result<usize, String> {
    arguments
        .windows(2)
        .position(|row| row[0] == "run" && row[1] == "adapter-certify")
        .map(|index| index + 2)
        .ok_or_else(|| "ADAPTER_CERTIFY_COMMAND_MISSING".to_owned())
}

fn parse_arguments(arguments: &[String]) -> Result<(String, String), String> {
    let start = command_start(arguments)?;
    let values = arguments[start..]
        .iter()
        .filter(|value| !value.starts_with("--"))
        .cloned()
        .collect::<Vec<_>>();
    if values.len() != 2 {
        return Err(format!(
            "ADAPTER_CERTIFY_ARGUMENT_COUNT_INVALID:{}",
            values.len()
        ));
    }
    Ok((values[0].clone(), values[1].clone()))
}

fn load_object(argument: &str) -> Result<Map<String, Value>, String> {
    let path = Path::new(argument);
    let source = if path.is_file() {
        fs::read_to_string(path)
            .map_err(|error| format!("ADAPTER_CERTIFY_RECEIPT_READ_FAILED:{error}"))?
    } else {
        argument.to_owned()
    };
    let value = serde_json::from_str::<Value>(&source)
        .map_err(|error| format!("ADAPTER_CERTIFY_RECEIPT_JSON_INVALID:{error}"))?;
    value
        .as_object()
        .cloned()
        .ok_or_else(|| "ADAPTER_CERTIFY_RECEIPT_MUST_BE_OBJECT".to_owned())
}

fn python_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_i64().map_or_else(
            || {
                value.as_u64().map_or_else(
                    || value.as_f64().is_some_and(|item| item != 0.0),
                    |item| item != 0,
                )
            },
            |item| item != 0,
        ),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

fn python_string(value: Option<&Value>) -> String {
    match value {
        None | Some(Value::Null) => "None".to_owned(),
        Some(Value::Bool(true)) => "True".to_owned(),
        Some(Value::Bool(false)) => "False".to_owned(),
        Some(Value::String(value)) => value.clone(),
        Some(Value::Number(value)) => value.to_string(),
        Some(value) => value.to_string(),
    }
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
        .map_err(|error| format!("ADAPTER_CERTIFY_CLOCK_FAILED:{error}"))?;
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
    ok: bool,
    created_at: &str,
    capabilities: &Value,
    checks: &Value,
) -> BTreeMap<String, Value> {
    BTreeMap::from([
        ("adapter_id".to_owned(), json!(adapter_id)),
        ("capabilities".to_owned(), capabilities.clone()),
        ("changed_paths".to_owned(), json!([])),
        ("checks".to_owned(), checks.clone()),
        ("created_at".to_owned(), json!(created_at)),
        ("detected".to_owned(), Value::Bool(true)),
        ("maturity".to_owned(), json!(maturity)),
        ("ok".to_owned(), Value::Bool(ok)),
        ("operation".to_owned(), json!("certify")),
    ])
}

fn receipt_hash(body: &BTreeMap<String, Value>) -> Result<String, String> {
    let canonical = serde_json::to_vec(body)
        .map_err(|error| format!("ADAPTER_CERTIFY_CANONICALIZE_FAILED:{error}"))?;
    Ok(format!("sha256:{:x}", Sha256::digest(canonical)))
}

fn write_receipt(state_root: &Path, receipt: &Value, receipt_id: &str) -> Result<(), String> {
    let digest = receipt_id
        .strip_prefix("sha256:")
        .ok_or_else(|| "ADAPTER_CERTIFY_RECEIPT_ID_INVALID".to_owned())?;
    let directory = state_root.join("unified").join("adapter-receipts");
    fs::create_dir_all(&directory)
        .map_err(|error| format!("ADAPTER_CERTIFY_RECEIPT_DIRECTORY_FAILED:{error}"))?;
    let rendered = serde_json::to_string_pretty(receipt)
        .map_err(|error| format!("ADAPTER_CERTIFY_RECEIPT_SERIALIZE_FAILED:{error}"))?
        + "\n";
    fs::write(directory.join(format!("{digest}.json")), rendered)
        .map_err(|error| format!("ADAPTER_CERTIFY_RECEIPT_WRITE_FAILED:{error}"))
}

pub fn execute(arguments: &[String], state_root: &Path) -> Result<Value, String> {
    super::native_platform_state::initialize(state_root)?;
    let (adapter_id, receipt_argument) = parse_arguments(arguments)?;
    let external_receipt = load_object(&receipt_argument)?;
    let contract = super::native_run_adapters::contract(&adapter_id)?;

    let present = external_receipt
        .keys()
        .map(String::as_str)
        .collect::<BTreeSet<_>>();
    let missing = REQUIRED
        .iter()
        .filter(|key| !present.contains(**key))
        .copied()
        .collect::<Vec<_>>();
    let booleans_valid = BOOLEAN_CHECKS
        .iter()
        .all(|key| external_receipt.get(*key).is_some_and(python_truthy));
    let artifact_hash = python_string(external_receipt.get("artifact_hash"));
    let hash_valid = artifact_hash.starts_with("sha256:") && artifact_hash.chars().count() == 71;
    let valid = missing.is_empty() && booleans_valid && hash_valid;
    let maturity = if valid { "Certified" } else { "Enforced" };
    let checks = json!({
        "missing": missing,
        "external_receipt": external_receipt,
    });
    let created_at = utc_now()?;
    let capabilities = contract["capabilities"].clone();
    let body = canonical_body(
        &adapter_id,
        maturity,
        valid,
        &created_at,
        &capabilities,
        &checks,
    );
    let receipt_id = receipt_hash(&body)?;
    let receipt = json!({
        "receipt_id": receipt_id,
        "adapter_id": adapter_id,
        "maturity": maturity,
        "operation": "certify",
        "ok": valid,
        "created_at": created_at,
        "detected": true,
        "changed_paths": [],
        "capabilities": capabilities,
        "checks": checks,
        "rollback": {},
        "claim_boundary": CLAIM_BOUNDARY,
    });
    write_receipt(
        state_root,
        &receipt,
        receipt["receipt_id"].as_str().unwrap_or_default(),
    )?;
    Ok(receipt)
}

#[cfg(test)]
mod tests {
    use super::{python_truthy, supports};
    use serde_json::json;

    #[test]
    fn matches_python_truthiness() {
        assert!(!python_truthy(&json!(null)));
        assert!(!python_truthy(&json!(0)));
        assert!(!python_truthy(&json!("")));
        assert!(python_truthy(&json!(1)));
        assert!(python_truthy(&json!("false")));
    }

    #[test]
    fn routes_adapter_certify_only() {
        assert!(supports(&["run".to_owned(), "adapter-certify".to_owned()]));
        assert!(!supports(&[
            "run".to_owned(),
            "adapter-conformance".to_owned()
        ]));
    }
}
