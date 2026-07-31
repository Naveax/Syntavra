#![forbid(unsafe_code)]

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::Path;

use serde_json::{json, Map, Value};
use syntavra_core::sha256_hex;

const VERSION: u64 = 1;

fn canonical_bytes(value: &Value) -> Result<Vec<u8>, String> {
    serde_json::to_vec(value).map_err(|error| format!("WIRE_JSON_RENDER_FAILED:{error}"))
}

fn collect_keys(value: &Value, counts: &mut BTreeMap<String, usize>) {
    match value {
        Value::Object(items) => {
            for (key, child) in items {
                *counts.entry(key.clone()).or_default() += 1;
                collect_keys(child, counts);
            }
        }
        Value::Array(items) => {
            for child in items {
                collect_keys(child, counts);
            }
        }
        _ => {}
    }
}

fn looks_path(value: &str) -> bool {
    value.len() > 8
        && (value.contains('/') || value.contains('\\'))
        && !value.starts_with("http://")
        && !value.starts_with("https://")
}

fn collect_paths(value: &Value, counts: &mut BTreeMap<String, usize>) {
    match value {
        Value::String(value) if looks_path(value) => {
            *counts.entry(value.clone()).or_default() += 1;
        }
        Value::Object(items) => {
            for child in items.values() {
                collect_paths(child, counts);
            }
        }
        Value::Array(items) => {
            for child in items {
                collect_paths(child, counts);
            }
        }
        _ => {}
    }
}

fn sorted_dictionary(counts: BTreeMap<String, usize>, minimum_key_length: usize) -> Vec<String> {
    let mut entries = counts
        .into_iter()
        .filter(|(key, count)| *count >= 2 && key.len() >= minimum_key_length)
        .collect::<Vec<_>>();
    entries.sort_by(|left, right| right.1.cmp(&left.1).then_with(|| left.0.cmp(&right.0)));
    entries.into_iter().map(|(key, _)| key).collect()
}

fn compact(
    value: &Value,
    key_index: &BTreeMap<String, usize>,
    path_index: &BTreeMap<String, usize>,
) -> Value {
    match value {
        Value::Object(items) => Value::Object(
            items
                .iter()
                .map(|(key, child)| {
                    let rendered = key_index
                        .get(key)
                        .map_or_else(|| key.clone(), usize::to_string);
                    (rendered, compact(child, key_index, path_index))
                })
                .collect::<Map<_, _>>(),
        ),
        Value::Array(items) => Value::Array(
            items
                .iter()
                .map(|child| compact(child, key_index, path_index))
                .collect(),
        ),
        Value::String(value) => path_index
            .get(value)
            .map_or_else(|| Value::String(value.clone()), |index| json!({"@path": index})),
        other => other.clone(),
    }
}

fn encode(value: &Value, minimum_savings: f64) -> Result<Value, String> {
    let mut key_counts = BTreeMap::new();
    collect_keys(value, &mut key_counts);
    let keys = sorted_dictionary(key_counts, 3);
    let key_index = keys
        .iter()
        .enumerate()
        .map(|(index, key)| (key.clone(), index))
        .collect::<BTreeMap<_, _>>();

    let mut path_counts = BTreeMap::new();
    collect_paths(value, &mut path_counts);
    let paths = sorted_dictionary(path_counts, 0);
    let path_index = paths
        .iter()
        .enumerate()
        .map(|(index, path)| (path.clone(), index))
        .collect::<BTreeMap<_, _>>();

    let original = canonical_bytes(value)?;
    let original_hash = sha256_hex(&original);
    let envelope = json!({
        "v": VERSION,
        "k": keys,
        "p": paths,
        "d": compact(value, &key_index, &path_index),
        "h": original_hash.clone(),
    });
    let encoded = canonical_bytes(&envelope)?;
    let ratio = if original.is_empty() {
        0.0
    } else {
        let original_len = u32::try_from(original.len()).unwrap_or(u32::MAX);
        let encoded_len = u32::try_from(encoded.len()).unwrap_or(u32::MAX);
        (f64::from(original_len.saturating_sub(encoded_len)) / f64::from(original_len)).max(0.0)
    };
    if ratio < minimum_savings {
        return Ok(json!({
            "encoding": "json",
            "payload": value,
            "original_bytes": original.len(),
            "encoded_bytes": original.len(),
            "savings_ratio": 0.0,
            "original_hash": original_hash,
        }));
    }
    Ok(json!({
        "encoding": "syntavra-wire-v1",
        "payload": envelope,
        "original_bytes": original.len(),
        "encoded_bytes": encoded.len(),
        "savings_ratio": ratio,
        "original_hash": original_hash,
    }))
}

fn index(value: &Value, limit: usize, code: &str) -> Result<usize, String> {
    let value = value
        .as_u64()
        .and_then(|value| usize::try_from(value).ok())
        .ok_or_else(|| code.to_owned())?;
    if value >= limit {
        return Err(code.to_owned());
    }
    Ok(value)
}

fn expand(value: &Value, keys: &[String], paths: &[String]) -> Result<Value, String> {
    match value {
        Value::Object(items) => {
            let names = items.keys().map(String::as_str).collect::<BTreeSet<_>>();
            if names == BTreeSet::from(["@path"]) {
                let path_index = index(&items["@path"], paths.len(), "WIRE_PATH_INDEX_INVALID")?;
                return Ok(Value::String(paths[path_index].clone()));
            }
            if names == BTreeSet::from(["@tuple"]) {
                let tuple = items["@tuple"]
                    .as_array()
                    .ok_or_else(|| "WIRE_TUPLE_INVALID".to_owned())?;
                return tuple
                    .iter()
                    .map(|child| expand(child, keys, paths))
                    .collect::<Result<Vec<_>, _>>()
                    .map(Value::Array);
            }
            items
                .iter()
                .map(|(key, child)| {
                    let rendered = key.parse::<usize>().ok().and_then(|index| keys.get(index));
                    let name = rendered.cloned().unwrap_or_else(|| key.clone());
                    expand(child, keys, paths).map(|child| (name, child))
                })
                .collect::<Result<Map<_, _>, _>>()
                .map(Value::Object)
        }
        Value::Array(items) => items
            .iter()
            .map(|child| expand(child, keys, paths))
            .collect::<Result<Vec<_>, _>>()
            .map(Value::Array),
        other => Ok(other.clone()),
    }
}

fn decode(encoded: &Value) -> Result<Value, String> {
    let encoded_object = encoded
        .as_object()
        .ok_or_else(|| "WIRE_DOCUMENT_INVALID".to_owned())?;
    if encoded_object.get("encoding").and_then(Value::as_str) == Some("json") {
        return Ok(encoded_object.get("payload").cloned().unwrap_or(Value::Null));
    }
    let envelope = encoded_object.get("payload").unwrap_or(encoded);
    let envelope = envelope
        .as_object()
        .ok_or_else(|| "WIRE_ENVELOPE_INVALID".to_owned())?;
    if envelope.get("v").and_then(Value::as_u64) != Some(VERSION) {
        return Err("WIRE_VERSION_UNSUPPORTED".to_owned());
    }
    let keys = envelope
        .get("k")
        .and_then(Value::as_array)
        .ok_or_else(|| "WIRE_KEYS_INVALID".to_owned())?
        .iter()
        .map(|value| {
            value
                .as_str()
                .map(str::to_owned)
                .ok_or_else(|| "WIRE_KEYS_INVALID".to_owned())
        })
        .collect::<Result<Vec<_>, _>>()?;
    let paths = envelope
        .get("p")
        .and_then(Value::as_array)
        .ok_or_else(|| "WIRE_PATHS_INVALID".to_owned())?
        .iter()
        .map(|value| {
            value
                .as_str()
                .map(str::to_owned)
                .ok_or_else(|| "WIRE_PATHS_INVALID".to_owned())
        })
        .collect::<Result<Vec<_>, _>>()?;
    let value = expand(envelope.get("d").unwrap_or(&Value::Null), &keys, &paths)?;
    let expected = envelope
        .get("h")
        .and_then(Value::as_str)
        .ok_or_else(|| "WIRE_HASH_MISSING".to_owned())?;
    if sha256_hex(&canonical_bytes(&value)?) != expected {
        return Err("WIRE_PAYLOAD_INTEGRITY_MISMATCH".to_owned());
    }
    Ok(value)
}

fn argument_after<'a>(arguments: &'a [String], name: &str) -> Option<&'a str> {
    arguments
        .iter()
        .position(|argument| argument == name)
        .and_then(|index| arguments.get(index + 1))
        .map(String::as_str)
}

fn positional(arguments: &[String]) -> Result<(&str, &str), String> {
    let start = arguments
        .windows(2)
        .position(|window| window[0] == "run" && window[1] == "wire")
        .ok_or_else(|| "WIRE_COMMAND_INVALID".to_owned())?;
    let action = arguments
        .get(start + 2)
        .map(String::as_str)
        .ok_or_else(|| "WIRE_ACTION_MISSING".to_owned())?;
    let source = arguments
        .get(start + 3)
        .map(String::as_str)
        .ok_or_else(|| "WIRE_SOURCE_MISSING".to_owned())?;
    Ok((action, source))
}

fn load_json(source: &str) -> Result<Value, String> {
    let path = Path::new(source);
    let raw = if path.is_file() {
        fs::read_to_string(path).map_err(|error| format!("WIRE_SOURCE_READ_FAILED:{error}"))?
    } else {
        source.to_owned()
    };
    serde_json::from_str(&raw).map_err(|error| format!("WIRE_SOURCE_JSON_INVALID:{error}"))
}

pub fn execute(arguments: &[String]) -> Result<Value, String> {
    let (action, source) = positional(arguments)?;
    let value = load_json(source)?;
    match action {
        "encode" => {
            let minimum_savings = argument_after(arguments, "--minimum-savings")
                .map_or(Ok(0.08), |value| {
                    value
                        .parse::<f64>()
                        .map_err(|error| format!("WIRE_SAVINGS_INVALID:{error}"))
                })?;
            encode(&value, minimum_savings)
        }
        "decode" => decode(&value),
        _ => Err("WIRE_ACTION_INVALID".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::{decode, encode};
    use serde_json::json;

    #[test]
    fn falls_back_when_wire_is_not_smaller() {
        let value = json!({"short": true});
        let encoded = encode(&value, 0.08).expect("encode");
        assert_eq!(encoded["encoding"], "json");
        assert_eq!(decode(&encoded).expect("decode"), value);
    }

    #[test]
    fn repeated_keys_and_paths_round_trip() {
        let path = "/workspace/project/src/module.rs";
        let value = json!([
            {"filename": path, "metadata": {"filename": path}},
            {"filename": path, "metadata": {"filename": path}}
        ]);
        let encoded = encode(&value, 0.0).expect("encode");
        assert_eq!(encoded["encoding"], "syntavra-wire-v1");
        assert_eq!(decode(&encoded).expect("decode"), value);
    }
}
