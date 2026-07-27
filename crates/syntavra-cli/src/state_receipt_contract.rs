#![forbid(unsafe_code)]

use std::fmt::Write as _;

use syntavra_core::sha256_hex;

const WIRE_HEADER: &str = "R7RCPT1";
const PRODUCT_VERSION: &str = "0.0.1";
const CONTRACT_VERSION: u32 = 1;
const RECEIPT_SCHEMA_VERSION: u32 = 1;

pub const STATE_LAYOUT_JSON: &str = r#"{"schema_version":1,"contract_version":1,"layout_id":"syntavra-state-layout-v1","root":".syntavra","project_binding":{"algorithm":"sha256-normalized-absolute-path-v1","field":"project_id","required_for":["receipt-envelope"],"mismatch_policy":"fail-closed"},"engine_policy":{"single_writer":true,"fallback_after_mutation":false,"lock_root":".syntavra/locks","selection_precedence":["command","environment","project","user","builtin"],"environment_override":"SYNTAVRA_ENGINE","builtin_default":"python","auto_policy_r4":"python","unknown_selection":"fail-closed"},"shared_paths":[{"id":"project-config","path":".syntavra/config.toml","kind":"configuration","readers":["python","rust"],"writers":["python"],"rust_r7_access":"contract-metadata-only"},{"id":"engine-selection","path":".syntavra/engine.json","kind":"engine-selection","readers":["python","rust"],"writers":["python"],"rust_r7_access":"contract-metadata-only"},{"id":"pre-release-state","path":".syntavra/pre-release","kind":"product-state","readers":["python"],"writers":["python"],"rust_r7_access":"not-proven"},{"id":"runtime-v3-state","path":".syntavra/runtime-v3","kind":"runtime-state","readers":["python"],"writers":["python"],"rust_r7_access":"not-proven"}],"receipt_envelope":{"wire_header":"R7RCPT1","schema_version":1,"hash_algorithm":"sha256","hash_scope":"canonical-wire-excluding-receipt-hash","project_binding_required":true,"unknown_fields":"fail-closed","unknown_schema":"fail-closed"},"r7_access":{"rust":"contract-metadata-and-receipt-parse-only","filesystem_state_reads":false,"filesystem_mutation":false,"database_access":false},"compatibility_rules":["A mutating operation selects one engine before the first state write.","An engine may fall back only after capability preflight reports unsupported and before mutation.","Unknown schema versions, fields, paths, and engine values fail closed.","R4 auto mode resolves to Python.","Logical SQLite records, not physical page layout, define future database parity.","R7 proves only state-layout metadata and receipt-envelope parsing."]}"#;

#[derive(Debug, Clone, PartialEq, Eq)]
// Field names intentionally mirror the public receipt wire contract.
#[allow(clippy::struct_field_names)]
struct Receipt {
    schema_version: u32,
    product_version: String,
    contract_version: u32,
    engine: String,
    operation: String,
    created_at_ms: u64,
    project_id: String,
    receipt_id: String,
    payload_hash: String,
    previous_hash: Option<String>,
    fallback: Option<Fallback>,
    receipt_hash: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct Fallback {
    from: String,
    to: String,
    reason: String,
}

fn json_string(value: &str) -> String {
    let mut output = String::with_capacity(value.len() + 2);
    output.push('"');
    for character in value.chars() {
        match character {
            '"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\u{0008}' => output.push_str("\\b"),
            '\u{000c}' => output.push_str("\\f"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            value if value <= '\u{001f}' => {
                write!(&mut output, "\\u{:04x}", u32::from(value))
                    .expect("writing to a String cannot fail");
            }
            value => output.push(value),
        }
    }
    output.push('"');
    output
}

fn hex_nibble(value: u8) -> Result<u8, String> {
    match value {
        b'0'..=b'9' => Ok(value - b'0'),
        b'a'..=b'f' => Ok(value - b'a' + 10),
        b'A'..=b'F' => Ok(value - b'A' + 10),
        _ => Err("RECEIPT_HEX_INVALID".to_owned()),
    }
}

fn decode_hex(value: &str) -> Result<Vec<u8>, String> {
    if value.len() % 2 != 0 {
        return Err("RECEIPT_HEX_INVALID".to_owned());
    }
    let mut output = Vec::with_capacity(value.len() / 2);
    for pair in value.as_bytes().chunks_exact(2) {
        output.push((hex_nibble(pair[0])? << 4) | hex_nibble(pair[1])?);
    }
    Ok(output)
}

fn decode_text(value: &str) -> Result<String, String> {
    String::from_utf8(decode_hex(value)?).map_err(|_| "RECEIPT_UTF8_INVALID".to_owned())
}

fn valid_hash(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn valid_identifier(value: &str) -> bool {
    let bytes = value.as_bytes();
    if bytes.is_empty() || bytes.len() > 128 {
        return false;
    }
    let first = bytes[0];
    if !(first.is_ascii_lowercase() || first.is_ascii_digit()) {
        return false;
    }
    bytes.iter().copied().all(|byte| {
        byte.is_ascii_lowercase()
            || byte.is_ascii_digit()
            || matches!(byte, b'.' | b'_' | b':' | b'-')
    })
}

fn field<'a>(line: &'a str, expected: &str) -> Result<&'a str, String> {
    let Some((key, value)) = line.split_once('=') else {
        return Err("RECEIPT_FIELD_ORDER_INVALID".to_owned());
    };
    if key != expected {
        return Err("RECEIPT_FIELD_ORDER_INVALID".to_owned());
    }
    Ok(value)
}

// One ordered function keeps the fail-closed field sequence directly auditable.
#[allow(clippy::too_many_lines)]
fn parse_receipt(data: &[u8], expected_project_id: &str) -> Result<Receipt, String> {
    if !valid_hash(expected_project_id) {
        return Err("RECEIPT_EXPECTED_PROJECT_INVALID".to_owned());
    }
    let text = std::str::from_utf8(data).map_err(|_| "RECEIPT_UTF8_INVALID".to_owned())?;
    if !text.ends_with('\n') {
        return Err("RECEIPT_TRAILING_NEWLINE_REQUIRED".to_owned());
    }
    let lines = text.lines().collect::<Vec<_>>();
    if lines.len() != 16 || lines[0] != WIRE_HEADER {
        return Err("RECEIPT_WIRE_SHAPE_INVALID".to_owned());
    }

    let schema_version = field(lines[1], "schema_version")?
        .parse::<u32>()
        .map_err(|_| "RECEIPT_INTEGER_INVALID".to_owned())?;
    let product_version = field(lines[2], "product_version")?.to_owned();
    let contract_version = field(lines[3], "contract_version")?
        .parse::<u32>()
        .map_err(|_| "RECEIPT_INTEGER_INVALID".to_owned())?;
    let engine = field(lines[4], "engine")?.to_owned();
    let operation = decode_text(field(lines[5], "operation_hex")?)?;
    let created_at_ms = field(lines[6], "created_at_ms")?
        .parse::<u64>()
        .map_err(|_| "RECEIPT_INTEGER_INVALID".to_owned())?;
    let project_id = field(lines[7], "project_id")?.to_owned();
    let receipt_id = decode_text(field(lines[8], "receipt_id_hex")?)?;
    let payload_hash = field(lines[9], "payload_hash")?.to_owned();
    let previous_raw = field(lines[10], "previous_hash")?;
    let fallback_from = field(lines[11], "fallback_from")?;
    let fallback_to = field(lines[12], "fallback_to")?;
    let fallback_reason = decode_text(field(lines[13], "fallback_reason_hex")?)?;
    let fallback_mutated = field(lines[14], "fallback_state_mutated")?;
    let receipt_hash = field(lines[15], "receipt_hash")?.to_owned();

    if schema_version != RECEIPT_SCHEMA_VERSION {
        return Err("RECEIPT_SCHEMA_UNSUPPORTED".to_owned());
    }
    if product_version != PRODUCT_VERSION {
        return Err("RECEIPT_PRODUCT_VERSION_MISMATCH".to_owned());
    }
    if contract_version != CONTRACT_VERSION {
        return Err("RECEIPT_CONTRACT_VERSION_MISMATCH".to_owned());
    }
    if !matches!(engine.as_str(), "python" | "rust") {
        return Err("RECEIPT_ENGINE_INVALID".to_owned());
    }
    if !valid_identifier(&operation) {
        return Err("RECEIPT_OPERATION_INVALID".to_owned());
    }
    if !valid_identifier(&receipt_id) {
        return Err("RECEIPT_ID_INVALID".to_owned());
    }
    if !valid_hash(&project_id) {
        return Err("RECEIPT_PROJECT_ID_INVALID".to_owned());
    }
    if project_id != expected_project_id {
        return Err("RECEIPT_PROJECT_MISMATCH".to_owned());
    }
    if !valid_hash(&payload_hash) {
        return Err("RECEIPT_PAYLOAD_HASH_INVALID".to_owned());
    }

    let previous_hash = if previous_raw == "-" {
        None
    } else if valid_hash(previous_raw) {
        Some(previous_raw.to_owned())
    } else {
        return Err("RECEIPT_PREVIOUS_HASH_INVALID".to_owned());
    };

    let state_mutated = match fallback_mutated {
        "true" => true,
        "false" => false,
        _ => return Err("RECEIPT_FALLBACK_MUTATION_INVALID".to_owned()),
    };
    let fallback = if fallback_from == "-"
        && fallback_to == "-"
        && fallback_reason.is_empty()
        && !state_mutated
    {
        None
    } else {
        if !matches!(fallback_from, "python" | "rust") || !matches!(fallback_to, "python" | "rust")
        {
            return Err("RECEIPT_FALLBACK_ENGINE_INVALID".to_owned());
        }
        if fallback_from == fallback_to {
            return Err("RECEIPT_FALLBACK_DIRECTION_INVALID".to_owned());
        }
        if fallback_reason.is_empty() {
            return Err("RECEIPT_FALLBACK_REASON_REQUIRED".to_owned());
        }
        if state_mutated {
            return Err("RECEIPT_FALLBACK_AFTER_MUTATION".to_owned());
        }
        if engine != fallback_to {
            return Err("RECEIPT_FALLBACK_TARGET_MISMATCH".to_owned());
        }
        Some(Fallback {
            from: fallback_from.to_owned(),
            to: fallback_to.to_owned(),
            reason: fallback_reason,
        })
    };

    if !valid_hash(&receipt_hash) {
        return Err("RECEIPT_HASH_INVALID".to_owned());
    }
    let mut material = lines[..15].join("\n");
    material.push('\n');
    if sha256_hex(material.as_bytes()) != receipt_hash {
        return Err("RECEIPT_HASH_MISMATCH".to_owned());
    }

    Ok(Receipt {
        schema_version,
        product_version,
        contract_version,
        engine,
        operation,
        created_at_ms,
        project_id,
        receipt_id,
        payload_hash,
        previous_hash,
        fallback,
        receipt_hash,
    })
}

fn fallback_json(value: Option<&Fallback>) -> String {
    value.map_or_else(
        || "null".to_owned(),
        |fallback| {
            format!(
                concat!(
                    "{{\"from\":{},",
                    "\"to\":{},",
                    "\"reason\":{},",
                    "\"state_mutated\":false}}"
                ),
                json_string(&fallback.from),
                json_string(&fallback.to),
                json_string(&fallback.reason),
            )
        },
    )
}

fn optional_string_json(value: Option<&str>) -> String {
    value.map_or_else(|| "null".to_owned(), json_string)
}

fn receipt_json(receipt: &Receipt, expected_project_id: &str) -> String {
    format!(
        concat!(
            "{{\"ok\":true,",
            "\"schema_version\":{},",
            "\"product_version\":{},",
            "\"contract_version\":{},",
            "\"engine\":{},",
            "\"operation\":{},",
            "\"created_at_ms\":{},",
            "\"project_id\":{},",
            "\"receipt_id\":{},",
            "\"payload_hash\":{},",
            "\"previous_hash\":{},",
            "\"fallback\":{},",
            "\"receipt_hash\":{},",
            "\"project_binding\":{{\"expected\":{},\"actual\":{},\"matched\":true}},",
            "\"hash_valid\":true,",
            "\"claim\":\"RUST_STATE_LAYOUT_RECEIPT_PARITY_PROVEN_R7_FIXTURES\"}}"
        ),
        receipt.schema_version,
        json_string(&receipt.product_version),
        receipt.contract_version,
        json_string(&receipt.engine),
        json_string(&receipt.operation),
        receipt.created_at_ms,
        json_string(&receipt.project_id),
        json_string(&receipt.receipt_id),
        json_string(&receipt.payload_hash),
        optional_string_json(receipt.previous_hash.as_deref()),
        fallback_json(receipt.fallback.as_ref()),
        json_string(&receipt.receipt_hash),
        json_string(expected_project_id),
        json_string(&receipt.project_id),
    )
}

pub fn state_layout_json() -> &'static str {
    STATE_LAYOUT_JSON
}

pub fn inspect_receipt_json(data: &[u8], expected_project_id: &str) -> Result<String, String> {
    let receipt = parse_receipt(data, expected_project_id)?;
    Ok(receipt_json(&receipt, expected_project_id))
}

#[cfg(test)]
mod tests {
    use super::{inspect_receipt_json, state_layout_json};

    const PROJECT: &str = "484c3bd06ba2a5db766e9b36071ccd694b56b9a197907c09f0f501a58919a41f";
    const WIRE: &str = "R7RCPT1\nschema_version=1\nproduct_version=0.0.1\ncontract_version=1\nengine=python\noperation_hex=636f6e6669672e76616c6964617465\ncreated_at_ms=1785000000000\nproject_id=484c3bd06ba2a5db766e9b36071ccd694b56b9a197907c09f0f501a58919a41f\nreceipt_id_hex=726563656970743a636f6e6669672e76616c69646174653a30303031\npayload_hash=4062edaf750fb8074e7e83e0c9028c94e32468a8b6f1614774328ef045150f93\nprevious_hash=-\nfallback_from=-\nfallback_to=-\nfallback_reason_hex=\nfallback_state_mutated=false\nreceipt_hash=f3b886e69cf769bbc4fbcbb0e49f4075c21040e6380dbb2c38f8683220535630\n";

    #[test]
    fn state_layout_is_read_only_r7() {
        assert!(state_layout_json().contains("\"filesystem_mutation\":false"));
        assert!(state_layout_json().contains("\"database_access\":false"));
    }

    #[test]
    fn parses_valid_receipt() {
        let value = inspect_receipt_json(WIRE.as_bytes(), PROJECT).expect("valid receipt");
        assert!(value.contains("\"hash_valid\":true"));
        assert!(value.contains("\"matched\":true"));
    }

    #[test]
    fn rejects_project_mismatch() {
        let error = inspect_receipt_json(
            WIRE.as_bytes(),
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )
        .expect_err("project mismatch");
        assert_eq!(error, "RECEIPT_PROJECT_MISMATCH");
    }

    #[test]
    fn rejects_tampered_receipt() {
        let tampered = WIRE.replace("receipt_hash=f3", "receipt_hash=a3");
        let error =
            inspect_receipt_json(tampered.as_bytes(), PROJECT).expect_err("tampered receipt");
        assert_eq!(error, "RECEIPT_HASH_MISMATCH");
    }
}
