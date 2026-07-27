#![forbid(unsafe_code)]

use std::collections::BTreeMap;
use std::fmt::Write as _;

use syntavra_core::sha256_hex;

const WIRE_HEADER: &str = "R6CFG1";

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ConfigScalar {
    Null,
    Bool(bool),
    Number(String),
    String(String),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConfigValue {
    pub path: String,
    pub value: ConfigScalar,
    pub source: String,
    pub scope: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConfigSnapshot {
    pub schema_version: u32,
    pub values: BTreeMap<String, ConfigScalar>,
    pub provenance: Vec<ConfigValue>,
    pub config_hash: String,
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone)]
struct Assignment {
    scope: String,
    source: String,
    path: String,
    value: ConfigScalar,
}

#[derive(Debug, Clone)]
enum JsonNode {
    Object(BTreeMap<String, JsonNode>),
    Scalar(ConfigScalar),
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

fn scalar_json(value: &ConfigScalar) -> String {
    match value {
        ConfigScalar::Null => "null".to_owned(),
        ConfigScalar::Bool(value) => value.to_string(),
        ConfigScalar::Number(value) => value.clone(),
        ConfigScalar::String(value) => json_string(value),
    }
}

fn insert_parts(node: &mut JsonNode, parts: &[&str], value: ConfigScalar) -> Result<(), String> {
    let Some((head, tail)) = parts.split_first() else {
        return Err("CONFIG_PATH_EMPTY".to_owned());
    };
    let JsonNode::Object(children) = node else {
        return Err("CONFIG_PATH_COLLISION".to_owned());
    };

    if tail.is_empty() {
        if matches!(children.get(*head), Some(JsonNode::Object(_))) {
            return Err("CONFIG_PATH_COLLISION".to_owned());
        }
        children.insert((*head).to_owned(), JsonNode::Scalar(value));
        return Ok(());
    }

    let child = children
        .entry((*head).to_owned())
        .or_insert_with(|| JsonNode::Object(BTreeMap::new()));
    if matches!(child, JsonNode::Scalar(_)) {
        return Err("CONFIG_PATH_COLLISION".to_owned());
    }
    insert_parts(child, tail, value)
}

fn insert_node(root: &mut JsonNode, path: &str, value: ConfigScalar) -> Result<(), String> {
    let parts = path.split('.').collect::<Vec<_>>();
    if parts.is_empty() || parts.iter().any(|part| part.is_empty()) {
        return Err("CONFIG_PATH_EMPTY".to_owned());
    }
    insert_parts(root, &parts, value)
}

fn node_json(node: &JsonNode) -> String {
    match node {
        JsonNode::Scalar(value) => scalar_json(value),
        JsonNode::Object(children) => {
            let rows = children
                .iter()
                .map(|(key, value)| format!("{}:{}", json_string(key), node_json(value)))
                .collect::<Vec<_>>()
                .join(",");
            format!("{{{rows}}}")
        }
    }
}

fn canonical_values_json(values: &BTreeMap<String, ConfigScalar>) -> Result<String, String> {
    let mut root = JsonNode::Object(BTreeMap::new());
    for (path, value) in values {
        insert_node(&mut root, path, value.clone())?;
    }
    Ok(node_json(&root))
}

fn default_entries() -> Vec<ConfigValue> {
    let rows = [
        ("schema_version", ConfigScalar::Number("1".to_owned())),
        (
            "runtime.profile",
            ConfigScalar::String("balanced".to_owned()),
        ),
        ("runtime.fail_closed", ConfigScalar::Bool(true)),
        (
            "provider.cache_policy",
            ConfigScalar::String("auto".to_owned()),
        ),
        (
            "provider.timeout_seconds",
            ConfigScalar::Number("180.0".to_owned()),
        ),
        (
            "routing.budget_bytes",
            ConfigScalar::Number("8192".to_owned()),
        ),
        (
            "routing.table.max_rows",
            ConfigScalar::Number("8".to_owned()),
        ),
        (
            "routing.table.max_columns",
            ConfigScalar::Number("12".to_owned()),
        ),
        (
            "security.evidence_encryption",
            ConfigScalar::String("required".to_owned()),
        ),
        (
            "security.control_authentication",
            ConfigScalar::String("required".to_owned()),
        ),
        (
            "security.remote_tls",
            ConfigScalar::String("required".to_owned()),
        ),
        ("security.dlp", ConfigScalar::String("required".to_owned())),
        (
            "retention.evidence_ttl_days",
            ConfigScalar::Number("30".to_owned()),
        ),
        (
            "retention.max_store_bytes",
            ConfigScalar::Number("10737418240".to_owned()),
        ),
        ("sandbox.strict", ConfigScalar::Bool(true)),
        ("sandbox.network", ConfigScalar::String("none".to_owned())),
        ("sandbox.image", ConfigScalar::String(String::new())),
        ("observability.structured_logs", ConfigScalar::Bool(true)),
        ("observability.metrics", ConfigScalar::Bool(true)),
        (
            "observability.sample_rate",
            ConfigScalar::Number("1.0".to_owned()),
        ),
    ];

    rows.into_iter()
        .map(|(path, value)| ConfigValue {
            path: path.to_owned(),
            value,
            source: "builtin".to_owned(),
            scope: "default".to_owned(),
        })
        .collect()
}

fn hex_nibble(value: u8) -> Result<u8, String> {
    match value {
        b'0'..=b'9' => Ok(value - b'0'),
        b'a'..=b'f' => Ok(value - b'a' + 10),
        b'A'..=b'F' => Ok(value - b'A' + 10),
        _ => Err("WIRE_HEX_INVALID".to_owned()),
    }
}

fn decode_hex(value: &str) -> Result<Vec<u8>, String> {
    if value.len() % 2 != 0 {
        return Err("WIRE_HEX_ODD_LENGTH".to_owned());
    }
    let mut output = Vec::with_capacity(value.len() / 2);
    for pair in value.as_bytes().chunks_exact(2) {
        output.push((hex_nibble(pair[0])? << 4) | hex_nibble(pair[1])?);
    }
    Ok(output)
}

fn decode_text(value: &str) -> Result<String, String> {
    String::from_utf8(decode_hex(value)?).map_err(|_| "WIRE_UTF8_INVALID".to_owned())
}

fn parse_scalar(type_code: &str, encoded: &str) -> Result<ConfigScalar, String> {
    let raw = decode_text(encoded)?;
    match type_code {
        "n" if raw.is_empty() => Ok(ConfigScalar::Null),
        "b" if raw == "true" => Ok(ConfigScalar::Bool(true)),
        "b" if raw == "false" => Ok(ConfigScalar::Bool(false)),
        "i" => {
            let parsed = raw
                .parse::<i128>()
                .map_err(|_| "CONFIG_INTEGER_INVALID".to_owned())?;
            Ok(ConfigScalar::Number(parsed.to_string()))
        }
        "f" => {
            let parsed = raw
                .parse::<f64>()
                .map_err(|_| "CONFIG_FLOAT_INVALID".to_owned())?;
            if !parsed.is_finite() {
                return Err("CONFIG_FLOAT_NON_FINITE".to_owned());
            }
            Ok(ConfigScalar::Number(raw))
        }
        "s" => Ok(ConfigScalar::String(raw)),
        _ => Err("CONFIG_SCALAR_TYPE_INVALID".to_owned()),
    }
}

fn scope_rank(scope: &str) -> Option<u8> {
    match scope {
        "user" => Some(0),
        "project" => Some(1),
        "environment" => Some(2),
        "session" => Some(3),
        "task" => Some(4),
        _ => None,
    }
}

fn parse_wire(input: &[u8]) -> Result<Vec<Vec<Assignment>>, String> {
    let text = std::str::from_utf8(input).map_err(|_| "WIRE_UTF8_INVALID".to_owned())?;
    let mut lines = text.lines();
    if lines.next() != Some(WIRE_HEADER) {
        return Err("WIRE_HEADER_INVALID".to_owned());
    }

    let mut phases: Vec<Vec<Assignment>> = Vec::new();
    let mut current: Option<Vec<Assignment>> = None;
    let mut expected_phase = 0_usize;
    let mut last_rank = 0_u8;

    for line in lines {
        if line.is_empty() {
            continue;
        }
        let fields = line.split('\t').collect::<Vec<_>>();
        match fields.as_slice() {
            ["phase", index] => {
                let parsed = index
                    .parse::<usize>()
                    .map_err(|_| "WIRE_PHASE_INVALID".to_owned())?;
                if parsed != expected_phase {
                    return Err("WIRE_PHASE_ORDER_INVALID".to_owned());
                }
                if let Some(previous) = current.take() {
                    phases.push(previous);
                }
                current = Some(Vec::new());
                expected_phase += 1;
                last_rank = 0;
            }
            ["a", scope, source, path, type_code, value] => {
                let target = current
                    .as_mut()
                    .ok_or_else(|| "WIRE_ASSIGNMENT_BEFORE_PHASE".to_owned())?;
                let rank = scope_rank(scope).ok_or_else(|| "CONFIG_SCOPE_INVALID".to_owned())?;
                if !target.is_empty() && rank < last_rank {
                    return Err("CONFIG_SCOPE_ORDER_INVALID".to_owned());
                }
                last_rank = rank;
                target.push(Assignment {
                    scope: (*scope).to_owned(),
                    source: decode_text(source)?,
                    path: decode_text(path)?,
                    value: parse_scalar(type_code, value)?,
                });
            }
            _ => return Err("WIRE_LINE_INVALID".to_owned()),
        }
    }

    if let Some(previous) = current {
        phases.push(previous);
    }
    if phases.is_empty() {
        return Err("CONFIG_PHASE_REQUIRED".to_owned());
    }
    Ok(phases)
}

fn scalar_string<'a>(values: &'a BTreeMap<String, ConfigScalar>, path: &str) -> Option<&'a str> {
    match values.get(path) {
        Some(ConfigScalar::String(value)) => Some(value),
        _ => None,
    }
}

fn scalar_number(values: &BTreeMap<String, ConfigScalar>, path: &str) -> Option<f64> {
    match values.get(path) {
        Some(ConfigScalar::Number(value)) => value.parse::<f64>().ok(),
        _ => None,
    }
}

fn valid_secret_ref(value: &str) -> bool {
    let Some(rest) = value.strip_prefix("secret://") else {
        return false;
    };
    !rest.is_empty()
        && rest
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'/' | b'-'))
}

fn validate(values: &BTreeMap<String, ConfigScalar>) -> Result<(), String> {
    if scalar_number(values, "schema_version") != Some(1.0) {
        return Err("CONFIG_SCHEMA_UNSUPPORTED".to_owned());
    }

    let profile = scalar_string(values, "runtime.profile")
        .ok_or_else(|| "CONFIG_RUNTIME_PROFILE_INVALID".to_owned())?;
    if !matches!(
        profile,
        "compact" | "balanced" | "detailed" | "audit" | "terse"
    ) {
        return Err("CONFIG_RUNTIME_PROFILE_INVALID".to_owned());
    }

    for path in [
        "security.evidence_encryption",
        "security.control_authentication",
        "security.remote_tls",
        "security.dlp",
    ] {
        let value =
            scalar_string(values, path).ok_or_else(|| "CONFIG_SECURITY_MODE_INVALID".to_owned())?;
        if !matches!(value, "required" | "preferred" | "off") {
            return Err("CONFIG_SECURITY_MODE_INVALID".to_owned());
        }
    }

    if scalar_number(values, "routing.budget_bytes").unwrap_or(0.0) < 512.0 {
        return Err("CONFIG_ROUTING_BUDGET_INVALID".to_owned());
    }
    if scalar_number(values, "retention.evidence_ttl_days").unwrap_or(-1.0) < 0.0
        || scalar_number(values, "retention.max_store_bytes").unwrap_or(-1.0) < 0.0
    {
        return Err("CONFIG_RETENTION_INVALID".to_owned());
    }

    for (path, value) in values {
        let lowered = path.to_ascii_lowercase();
        if path.ends_with("credential_ref") {
            match value {
                ConfigScalar::String(reference) if valid_secret_ref(reference) => {}
                ConfigScalar::String(reference) if reference.is_empty() => {}
                _ => return Err("CONFIG_SECRET_REFERENCE_INVALID".to_owned()),
            }
        }
        if ["password", "api_key", "secret_value", "token_value"]
            .iter()
            .any(|token| lowered.contains(token))
        {
            return Err("CONFIG_RAW_SECRET_FORBIDDEN".to_owned());
        }
    }

    canonical_values_json(values)?;
    Ok(())
}

fn resolve_phase(assignments: &[Assignment]) -> Result<ConfigSnapshot, String> {
    let defaults = default_entries();
    let mut values = BTreeMap::new();
    let mut provenance = Vec::new();

    for entry in defaults {
        values.insert(entry.path.clone(), entry.value.clone());
        provenance.push(entry);
    }

    for assignment in assignments {
        if assignment.path.is_empty() || assignment.path.split('.').any(str::is_empty) {
            return Err("CONFIG_PATH_EMPTY".to_owned());
        }
        values.insert(assignment.path.clone(), assignment.value.clone());
        provenance.push(ConfigValue {
            path: assignment.path.clone(),
            value: if assignment.scope == "environment"
                && assignment.path.ends_with("credential_ref")
            {
                ConfigScalar::String("[secret-ref]".to_owned())
            } else {
                assignment.value.clone()
            },
            source: assignment.source.clone(),
            scope: assignment.scope.clone(),
        });
    }

    validate(&values)?;
    let canonical = canonical_values_json(&values)?;
    Ok(ConfigSnapshot {
        schema_version: 1,
        values,
        provenance,
        config_hash: sha256_hex(canonical.as_bytes()),
        warnings: Vec::new(),
    })
}

pub fn resolve_config_wire(input: &[u8]) -> Result<ConfigSnapshot, String> {
    let phases = parse_wire(input)?;
    let mut last_good: Option<ConfigSnapshot> = None;
    let mut current: Option<ConfigSnapshot> = None;

    for phase in phases {
        match resolve_phase(&phase) {
            Ok(snapshot) => {
                last_good = Some(snapshot.clone());
                current = Some(snapshot);
            }
            Err(error) => {
                let Some(mut fallback) = last_good.clone() else {
                    return Err(error);
                };
                fallback.warnings = vec!["invalid-current-config-fell-back:ConfigError".to_owned()];
                current = Some(fallback);
            }
        }
    }

    current.ok_or_else(|| "CONFIG_PHASE_REQUIRED".to_owned())
}

pub fn default_config_wire() -> &'static [u8] {
    b"R6CFG1\nphase\t0\n"
}

pub fn snapshot_json(snapshot: &ConfigSnapshot) -> Result<String, String> {
    let provenance = snapshot
        .provenance
        .iter()
        .map(|item| {
            format!(
                "{{\"path\":{},\"value\":{},\"source\":{},\"scope\":{}}}",
                json_string(&item.path),
                scalar_json(&item.value),
                json_string(&item.source),
                json_string(&item.scope)
            )
        })
        .collect::<Vec<_>>()
        .join(",");
    let warnings = snapshot
        .warnings
        .iter()
        .map(|item| json_string(item))
        .collect::<Vec<_>>()
        .join(",");
    Ok(format!(
        concat!(
            "{{\"schema_version\":{},",
            "\"values\":{},",
            "\"provenance\":[{}],",
            "\"config_hash\":{},",
            "\"warnings\":[{}]}}"
        ),
        snapshot.schema_version,
        canonical_values_json(&snapshot.values)?,
        provenance,
        json_string(&snapshot.config_hash),
        warnings
    ))
}

pub fn status_json(snapshot: &ConfigSnapshot) -> String {
    let warnings = snapshot
        .warnings
        .iter()
        .map(|item| json_string(item))
        .collect::<Vec<_>>()
        .join(",");
    format!(
        concat!(
            "{{\"product\":\"Syntavra\",",
            "\"product_version\":\"0.0.1\",",
            "\"release_channel\":\"pre-release\",",
            "\"stability\":\"pre-alpha\",",
            "\"version_locked\":true,",
            "\"reference_engine\":\"python\",",
            "\"candidate_engine\":\"rust\",",
            "\"candidate_stability\":\"experimental\",",
            "\"config_schema_version\":{},",
            "\"config_hash\":{},",
            "\"warnings\":[{}],",
            "\"general_command_routing\":\"blocked\",",
            "\"mutation\":\"read-only\"}}"
        ),
        snapshot.schema_version,
        json_string(&snapshot.config_hash),
        warnings
    )
}

#[cfg(test)]
mod tests {
    use super::{
        default_config_wire, resolve_config_wire, snapshot_json, status_json, ConfigScalar,
    };

    #[test]
    fn resolves_default_config_deterministically() {
        let first = resolve_config_wire(default_config_wire()).expect("default config");
        let second = resolve_config_wire(default_config_wire()).expect("default config");
        assert_eq!(first.config_hash, second.config_hash);
        assert_eq!(
            first.values.get("runtime.profile"),
            Some(&ConfigScalar::String("balanced".to_owned()))
        );
    }

    #[test]
    fn applies_scope_precedence() {
        let wire = concat!(
            "R6CFG1\n",
            "phase\t0\n",
            "a\tuser\t757365722d636f6e666967\t72756e74696d652e70726f66696c65\ts\t636f6d70616374\n",
            "a\tproject\t70726f6a6563742d636f6e666967\t72756e74696d652e70726f66696c65\ts\t64657461696c6564\n",
            "a\ttask\t7461736b2d6f76657272696465\t72756e74696d652e70726f66696c65\ts\t7465727365\n"
        );
        let snapshot = resolve_config_wire(wire.as_bytes()).expect("layered config");
        assert_eq!(
            snapshot.values.get("runtime.profile"),
            Some(&ConfigScalar::String("terse".to_owned()))
        );
    }

    #[test]
    fn falls_back_to_last_good() {
        let wire = concat!(
            "R6CFG1\n",
            "phase\t0\n",
            "a\tproject\t70726f6a6563742d636f6e666967\t72756e74696d652e70726f66696c65\ts\t636f6d70616374\n",
            "phase\t1\n",
            "a\tproject\t70726f6a6563742d636f6e666967\t72756e74696d652e70726f66696c65\ts\t696e76616c6964\n"
        );
        let snapshot = resolve_config_wire(wire.as_bytes()).expect("fallback");
        assert_eq!(
            snapshot.warnings,
            vec!["invalid-current-config-fell-back:ConfigError".to_owned()]
        );
    }

    #[test]
    fn emits_valid_contract_json() {
        let snapshot = resolve_config_wire(default_config_wire()).expect("default config");
        assert!(snapshot_json(&snapshot)
            .expect("snapshot json")
            .contains("\"config_hash\""));
        assert!(status_json(&snapshot).contains("\"general_command_routing\":\"blocked\""));
    }
}
