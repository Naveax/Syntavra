#![forbid(unsafe_code)]

use serde_json::{json, Value};
use syntavra_core::sha256_hex;

use crate::config_contract::{resolve_config_wire, snapshot_json};
use crate::state_snapshot_contract::project_id_for_root;

const SCHEMA_VERSION: u32 = 1;
const CONTRACT_VERSION: u32 = 1;
const PLAN_ID: &str = "syntavra-config-last-good-plan-v1";
const TARGET_RELATIVE_PATH: &str = ".syntavra/pre-release/config-last-good.json";
const CLAIM: &str = "RUST_CONFIG_LAST_GOOD_PLAN_PARITY_PROVEN_R25_FIXTURES";
const MAX_CONFIG_WIRE_BYTES: usize = 256 * 1024;

fn valid_lower_hash(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn map_project_error(error: &str) -> String {
    let suffix = error.strip_prefix("STATE_").unwrap_or(error);
    format!("CONFIG_LIFECYCLE_{suffix}")
}

fn contains_ephemeral_scope(input: &[u8]) -> Result<bool, String> {
    let text =
        std::str::from_utf8(input).map_err(|_| "CONFIG_LIFECYCLE_WIRE_INVALID".to_owned())?;
    if !text.ends_with('\n') || text.lines().next() != Some("R6CFG1") {
        return Err("CONFIG_LIFECYCLE_WIRE_INVALID".to_owned());
    }
    for line in text.lines().skip(1) {
        let fields = line.split('\t').collect::<Vec<_>>();
        if fields.first() == Some(&"a") && matches!(fields.get(1), Some(&"session") | Some(&"task"))
        {
            return Ok(true);
        }
    }
    Ok(false)
}

fn canonical_snapshot_payload(
    snapshot: &crate::config_contract::ConfigSnapshot,
) -> Result<String, String> {
    let rendered = snapshot_json(snapshot)?;
    let value: Value = serde_json::from_str(&rendered)
        .map_err(|_| "CONFIG_LIFECYCLE_SNAPSHOT_JSON_INVALID".to_owned())?;
    let Some(object) = value.as_object() else {
        return Err("CONFIG_LIFECYCLE_SNAPSHOT_KEYS_INVALID".to_owned());
    };
    let expected = [
        "config_hash",
        "provenance",
        "schema_version",
        "values",
        "warnings",
    ];
    if object.len() != expected.len() || expected.iter().any(|key| !object.contains_key(*key)) {
        return Err("CONFIG_LIFECYCLE_SNAPSHOT_KEYS_INVALID".to_owned());
    }
    serde_json::to_string(&value).map_err(|_| "CONFIG_LIFECYCLE_SNAPSHOT_JSON_INVALID".to_owned())
}

pub fn config_last_good_plan_json(
    project_root: &str,
    expected_project_id: &str,
    config_wire: &[u8],
) -> Result<String, String> {
    if !valid_lower_hash(expected_project_id) {
        return Err("CONFIG_LIFECYCLE_EXPECTED_PROJECT_INVALID".to_owned());
    }
    if config_wire.len() > MAX_CONFIG_WIRE_BYTES {
        return Err("CONFIG_LIFECYCLE_WIRE_TOO_LARGE".to_owned());
    }

    let actual_project_id =
        project_id_for_root(project_root).map_err(|error| map_project_error(&error))?;
    if actual_project_id != expected_project_id {
        return Err("CONFIG_LIFECYCLE_PROJECT_MISMATCH".to_owned());
    }
    if contains_ephemeral_scope(config_wire)? {
        return Err("CONFIG_LIFECYCLE_EPHEMERAL_SCOPE_FORBIDDEN".to_owned());
    }

    let snapshot = resolve_config_wire(config_wire)
        .map_err(|_| "CONFIG_LIFECYCLE_CONFIG_INVALID".to_owned())?;
    let payload = canonical_snapshot_payload(&snapshot)?;
    let warnings = snapshot.warnings.clone();
    let fallback_used = !warnings.is_empty();
    let decision = if fallback_used {
        "retain-existing"
    } else {
        "write"
    };

    let plan = json!({
        "apply_authority": "blocked",
        "candidate": {
            "config_hash": snapshot.config_hash,
            "payload_bytes": payload.len(),
            "payload_sha256": sha256_hex(payload.as_bytes()),
            "schema_version": snapshot.schema_version,
            "warnings": warnings,
        },
        "claim": CLAIM,
        "contract_version": CONTRACT_VERSION,
        "decision": decision,
        "fallback_used": fallback_used,
        "full_product_parity": "FULL_PARITY_NOT_PROVEN",
        "input": {
            "config_wire_bytes": config_wire.len(),
            "config_wire_sha256": sha256_hex(config_wire),
            "ephemeral_scopes_forbidden": ["session", "task"],
            "format": "R6CFG1",
            "persistent_scopes": ["user", "project", "environment"],
        },
        "mutation": {
            "database_opened": false,
            "filesystem": false,
        },
        "ok": true,
        "plan_id": PLAN_ID,
        "project_binding": {
            "actual": actual_project_id,
            "expected": expected_project_id,
            "matched": true,
        },
        "project_id": expected_project_id,
        "schema_version": SCHEMA_VERSION,
        "target": {
            "atomic_replace_required": true,
            "file_mode": "0600",
            "relative_path": TARGET_RELATIVE_PATH,
        },
    });

    serde_json::to_string(&plan).map_err(|_| "CONFIG_LIFECYCLE_PLAN_JSON_INVALID".to_owned())
}

#[cfg(test)]
mod tests {
    use super::{contains_ephemeral_scope, valid_lower_hash};

    #[test]
    fn validates_lowercase_project_ids() {
        assert!(valid_lower_hash(&"a".repeat(64)));
        assert!(!valid_lower_hash(&"A".repeat(64)));
        assert!(!valid_lower_hash("abc"));
    }

    #[test]
    fn rejects_ephemeral_config_scopes() {
        let wire = b"R6CFG1\nphase\t0\na\tsession\t73\t70\ts\t76\n";
        assert_eq!(contains_ephemeral_scope(wire), Ok(true));
    }
}
