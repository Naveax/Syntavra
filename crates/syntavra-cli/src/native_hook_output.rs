#![forbid(unsafe_code)]
#![allow(clippy::pedantic, clippy::too_many_arguments, clippy::too_many_lines)]

use std::fs;
use std::path::Path;

use rusqlite::{params, OptionalExtension as _};
use serde_json::{json, Map, Value};
use syntavra_core::sha256_hex;

use super::native_hook_evidence::{evidence_put, externalization_schema, hash_json, now, policy};

const TEXT_KEYS: &[&str] = &[
    "stdout",
    "stderr",
    "output",
    "content",
    "text",
    "body",
    "log",
    "logs",
    "diff",
    "patch",
    "trace",
    "traceback",
    "message",
    "data",
];
const ROOT_CAPTURE_PATHS: &[&str] = &["result", "stdout", "stderr", "output", "content", "text"];

#[derive(Clone)]
struct Capture {
    path: String,
    artifact_id: String,
    family: String,
    mode: String,
    original_bytes: usize,
    visible_bytes: usize,
    exact_handle: String,
    merkle_root: String,
    injection_risk: bool,
    quality_gate_passed: bool,
}

impl Capture {
    fn value(&self) -> Value {
        json!({
            "path": self.path,
            "artifact_id": self.artifact_id,
            "family": self.family,
            "mode": self.mode,
            "original_bytes": self.original_bytes,
            "visible_bytes": self.visible_bytes,
            "exact_handle": self.exact_handle,
            "merkle_root": self.merkle_root,
            "injection_risk": self.injection_risk,
            "quality_gate_passed": self.quality_gate_passed,
        })
    }
}

fn normalize_command(value: &str) -> String {
    value.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn payload_command(payload: &Map<String, Value>) -> String {
    match payload.get("command").or_else(|| payload.get("argv")) {
        Some(Value::Array(values)) => values
            .iter()
            .map(|value| {
                value
                    .as_str()
                    .map_or_else(|| value.to_string(), str::to_owned)
            })
            .collect::<Vec<_>>()
            .join(" "),
        Some(Value::String(value)) => value.clone(),
        Some(value) => value.to_string(),
        None => String::new(),
    }
}

fn payload_string(payload: &Map<String, Value>, first: &str, second: &str) -> String {
    payload
        .get(first)
        .or_else(|| payload.get(second))
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned()
}

fn tool(payload: &Map<String, Value>) -> String {
    let value = payload_string(payload, "tool", "name");
    if value.is_empty() {
        "tool".to_owned()
    } else {
        value
    }
}

fn scope(payload: &Map<String, Value>, tool: &str) -> String {
    for key in ["scope_key", "session_id"] {
        if let Some(value) = payload
            .get(key)
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
        {
            return value.to_owned();
        }
    }
    format!("host:{tool}")
}

fn injection(text: &str) -> bool {
    let value = text.to_lowercase();
    [
        "ignore previous instructions",
        "ignore all previous instructions",
        "system message",
        "developer message",
        "<system>",
        "<assistant>",
        "<developer>",
        "<tool>",
        "you are chatgpt",
        "do not follow",
        "reveal the prompt",
        "reveal the secret",
    ]
    .iter()
    .any(|needle| value.contains(needle))
}

fn redact(text: &str) -> String {
    let mut output = text.to_owned();
    for key in [
        "api_key",
        "api-key",
        "access_token",
        "access-token",
        "authorization",
        "password",
        "secret",
        "bearer",
        "private_key",
        "private-key",
    ] {
        let lower = output.to_lowercase();
        let Some(start) = lower.find(key) else {
            continue;
        };
        let bytes = output.as_bytes();
        let mut separator = start + key.len();
        while separator < bytes.len() && bytes[separator].is_ascii_whitespace() {
            separator += 1;
        }
        if separator >= bytes.len() || !matches!(bytes[separator], b':' | b'=') {
            continue;
        }
        let mut value_start = separator + 1;
        while value_start < bytes.len() && bytes[value_start].is_ascii_whitespace() {
            value_start += 1;
        }
        let mut value_end = value_start;
        while value_end < bytes.len()
            && !bytes[value_end].is_ascii_whitespace()
            && !matches!(bytes[value_end], b',' | b';')
        {
            value_end += 1;
        }
        if value_end > value_start {
            output.replace_range(value_start..value_end, "[REDACTED]");
        }
    }
    output
}

fn family(raw: &[u8], command: &str, path: &str) -> &'static str {
    let text = String::from_utf8_lossy(raw);
    let command = normalize_command(command).to_lowercase();
    let suffix = Path::new(path)
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or_default()
        .to_lowercase();
    if command.contains("git status") {
        "git-status"
    } else if command.contains("git diff") || command.contains("gh pr diff") {
        "diff"
    } else if command.contains("git log") {
        "git-log"
    } else if command.contains("pytest")
        || command.contains("cargo test")
        || command.contains("npm test")
    {
        "test-output"
    } else if [
        "py", "js", "jsx", "ts", "tsx", "rs", "go", "java", "cs", "c", "cpp", "h", "hpp", "rb",
        "php", "lua", "luau",
    ]
    .contains(&suffix.as_str())
    {
        "code"
    } else if suffix == "json"
        || suffix == "jsonl"
        || text.trim_start().starts_with('{')
        || text.trim_start().starts_with('[')
    {
        "json"
    } else if text.lines().count() > 120 {
        "log"
    } else {
        "text"
    }
}

fn artifact_ids(
    tool: &str,
    command: &str,
    path: &str,
    scope: &str,
    content: &str,
) -> Result<(String, String, String, String), String> {
    let policy_hash = hash_json(&policy())?;
    let stream = hash_json(&json!({
        "tool": tool,
        "command": normalize_command(command),
        "path": path
    }))?;
    let identity = hash_json(&json!({
        "stream": stream.clone(),
        "content": content,
        "policy": policy_hash.clone()
    }))?;
    let artifact_hash = hash_json(&json!({
        "scope": scope,
        "identity": identity.clone()
    }))?;
    Ok((
        format!("ext-{}", &artifact_hash[..32]),
        stream,
        identity,
        policy_hash,
    ))
}

fn payload_metadata(payload: &Map<String, Value>, field_path: &str) -> Map<String, Value> {
    let ignored = [
        "result",
        "stdout",
        "stderr",
        "output",
        "content",
        "text",
        "body",
        "provider_response",
        "response",
        "usage",
        "provider_usage",
    ];
    let mut value = Map::new();
    for (key, item) in payload {
        if !ignored.contains(&key.as_str())
            && matches!(
                item,
                Value::String(_) | Value::Number(_) | Value::Bool(_) | Value::Null
            )
        {
            value.insert(key.clone(), item.clone());
        }
    }
    value.insert(
        "host_field_path".to_owned(),
        Value::String(field_path.to_owned()),
    );
    value
}

fn capture_text(
    payload: &Map<String, Value>,
    field_path: &str,
    text: &str,
    project_root: &Path,
    state_root: &Path,
    tool: &str,
    command: &str,
    path: &str,
    scope: &str,
) -> Result<(Value, Vec<Capture>), String> {
    if text.is_empty() {
        return Ok((Value::String(String::new()), Vec::new()));
    }
    if text.len() < 256 && !ROOT_CAPTURE_PATHS.contains(&field_path) {
        return Ok((Value::String(text.to_owned()), Vec::new()));
    }

    let raw = text.as_bytes();
    let content_hash = sha256_hex(raw);
    let family = family(raw, command, path);
    let (artifact_id, stream, identity, policy_hash) =
        artifact_ids(tool, command, path, scope, &content_hash)?;
    fs::create_dir_all(state_root).map_err(|error| format!("HOOK_STATE_CREATE_FAILED:{error}"))?;
    let db = externalization_schema(&state_root.join("tool-externalization.sqlite3"))?;

    let duplicate = db
        .query_row(
            "SELECT a.artifact_id,a.family,a.original_bytes,a.exact_handle,a.merkle_root,
                    a.injection_risk,a.quality_gate_passed,s.seen_count
             FROM ext_seen s JOIN ext_artifacts a ON a.artifact_id=s.artifact_id
             WHERE s.scope_key=?1 AND s.identity_key=?2",
            params![scope, identity],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, i64>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, i64>(5)?,
                    row.get::<_, i64>(6)?,
                    row.get::<_, i64>(7)?,
                ))
            },
        )
        .optional()
        .map_err(|error| format!("HOOK_EXT_DUPLICATE_QUERY_FAILED:{error}"))?;
    if let Some((id, family, original, handle, merkle, risk, quality, seen)) = duplicate {
        let next = seen + 1;
        db.execute(
            "UPDATE ext_seen SET seen_count=?3,last_seen=?4
             WHERE scope_key=?1 AND identity_key=?2",
            params![scope, identity, next, now()?],
        )
        .map_err(|error| format!("HOOK_EXT_DUPLICATE_UPDATE_FAILED:{error}"))?;
        let preview =
            format!("[Syntavra externalized duplicate artifact={id} seen={next} exact={handle}]");
        let capture = Capture {
            path: field_path.to_owned(),
            artifact_id: id,
            family,
            mode: "dedup-reference".to_owned(),
            original_bytes: original as usize,
            visible_bytes: preview.len(),
            exact_handle: handle,
            merkle_root: merkle,
            injection_risk: risk != 0,
            quality_gate_passed: quality != 0,
        };
        return Ok((Value::String(preview), vec![capture]));
    }

    let handle = evidence_put(
        state_root,
        project_root,
        raw,
        &format!("tool-output:{family}"),
        json!({"artifact_id": artifact_id.clone(), "path": path}),
    )?;
    let risk = injection(text);
    let mut preview = if raw.len() <= 768 {
        redact(text)
    } else {
        let first = text
            .lines()
            .find(|line| !line.trim().is_empty())
            .unwrap_or_default();
        format!(
            "[SCX v2 artifact={artifact_id} family={family} raw={} segments=1 merkle={} exact={handle}]\nSummary:\n{}",
            raw.len(),
            &content_hash[..16],
            first.trim()
        )
    };
    if preview.len() > 4096 {
        preview.truncate(4096);
    }
    let mode = if raw.len() <= 768 {
        "passthrough-captured"
    } else {
        "externalized"
    };
    let created = now()?;
    let facets = json!({
        "lines": text.lines().count(),
        "critical_segments": 0,
        "warning_lines": 0,
        "location_lines": 0
    });
    let mut metadata = json!({
        "schema_version": 2,
        "tool_name": tool,
        "command": normalize_command(command),
        "path": path,
        "scope_key": scope,
        "policy": policy(),
        "changed_segment_indexes": [0],
        "unchanged_segment_ratio": 0.0,
        "security_scan": {
            "secret_types": [],
            "injection_reasons": [],
            "encoded_payloads_checked": 0
        }
    });
    metadata
        .as_object_mut()
        .expect("metadata is an object")
        .extend(payload_metadata(payload, field_path));

    db.execute(
        "INSERT OR REPLACE INTO ext_artifacts VALUES(
            ?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,1,?5,?12,1,NULL,?13,?14,?15,?16)",
        params![
            artifact_id,
            scope,
            stream,
            identity,
            content_hash,
            family,
            mode,
            preview,
            raw.len() as i64,
            preview.len() as i64,
            handle,
            policy_hash,
            i64::from(risk),
            serde_json::to_string(&facets)
                .map_err(|error| format!("HOOK_EXT_FACETS_FAILED:{error}"))?,
            serde_json::to_string(&metadata)
                .map_err(|error| format!("HOOK_EXT_METADATA_FAILED:{error}"))?,
            created,
        ],
    )
    .map_err(|error| format!("HOOK_EXT_ARTIFACT_INSERT_FAILED:{error}"))?;
    db.execute(
        "DELETE FROM ext_segments WHERE artifact_id=?1",
        params![artifact_id],
    )
    .map_err(|error| format!("HOOK_EXT_SEGMENT_DELETE_FAILED:{error}"))?;
    db.execute(
        "INSERT INTO ext_segments VALUES(
            ?1,0,0,?2,1,?3,?4,?5,'text-window',1.0,0,?6)",
        params![
            artifact_id,
            raw.len() as i64,
            text.lines().count().max(1) as i64,
            content_hash,
            handle,
            redact(text),
        ],
    )
    .map_err(|error| format!("HOOK_EXT_SEGMENT_INSERT_FAILED:{error}"))?;
    db.execute(
        "INSERT INTO ext_seen VALUES(?1,?2,?3,1,?4,?4)",
        params![scope, identity, artifact_id, created],
    )
    .map_err(|error| format!("HOOK_EXT_SEEN_INSERT_FAILED:{error}"))?;

    let capture = Capture {
        path: field_path.to_owned(),
        artifact_id,
        family: family.to_owned(),
        mode: mode.to_owned(),
        original_bytes: raw.len(),
        visible_bytes: preview.len(),
        exact_handle: handle,
        merkle_root: content_hash,
        injection_risk: risk,
        quality_gate_passed: true,
    };
    Ok((Value::String(preview), vec![capture]))
}

fn walk(
    payload: &Map<String, Value>,
    value: &Value,
    field_path: &str,
    project_root: &Path,
    state_root: &Path,
    tool: &str,
    command: &str,
    path: &str,
    scope: &str,
) -> Result<(Value, Vec<Capture>), String> {
    match value {
        Value::String(text) => capture_text(
            payload,
            field_path,
            text,
            project_root,
            state_root,
            tool,
            command,
            path,
            scope,
        ),
        Value::Object(values) => {
            let mut output = Map::new();
            let mut captures = Vec::new();
            for (key, item) in values {
                let child_path = format!("{field_path}.{key}");
                let lower = key.to_lowercase();
                let descend = TEXT_KEYS.contains(&lower.as_str())
                    || matches!(item, Value::Object(_) | Value::Array(_))
                    || item.as_str().is_some_and(|text| text.len() >= 256);
                if descend {
                    let (rendered, nested) = walk(
                        payload,
                        item,
                        &child_path,
                        project_root,
                        state_root,
                        tool,
                        command,
                        path,
                        scope,
                    )?;
                    output.insert(key.clone(), rendered);
                    captures.extend(nested);
                } else {
                    output.insert(key.clone(), item.clone());
                }
            }
            Ok((Value::Object(output), captures))
        }
        Value::Array(values) => {
            let mut output = Vec::with_capacity(values.len());
            let mut captures = Vec::new();
            for (index, item) in values.iter().enumerate() {
                let (rendered, nested) = walk(
                    payload,
                    item,
                    &format!("{field_path}.{index}"),
                    project_root,
                    state_root,
                    tool,
                    command,
                    path,
                    scope,
                )?;
                output.push(rendered);
                captures.extend(nested);
            }
            Ok((Value::Array(output), captures))
        }
        _ => Ok((value.clone(), Vec::new())),
    }
}

pub(super) fn post_tool(
    payload: &Map<String, Value>,
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    let tool = tool(payload);
    let result = payload.get("result").cloned().unwrap_or(Value::Null);
    if ["syntavra.output.", "syntavra.usage.", "syntavra.evidence."]
        .iter()
        .any(|prefix| tool.starts_with(prefix))
    {
        return Ok(json!({
            "mode": "pass-through",
            "result": result,
            "captures": [],
            "usage_receipt_hash": Value::Null,
            "usage_chain_hash": Value::Null,
        }));
    }

    let command = payload_command(payload);
    let path = payload_string(payload, "path", "file");
    let scope = scope(payload, &tool);
    let (rendered, captures) = walk(
        payload,
        &result,
        "result",
        project_root,
        state_root,
        &tool,
        &command,
        &path,
        &scope,
    )?;
    Ok(json!({
        "mode": if captures.is_empty() { "pass-through" } else { "externalized" },
        "result": rendered,
        "captures": captures.iter().map(Capture::value).collect::<Vec<_>>(),
        "usage_receipt_hash": Value::Null,
        "usage_chain_hash": Value::Null,
    }))
}
