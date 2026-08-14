#![forbid(unsafe_code)]
#![allow(clippy::too_many_lines)]

use std::collections::{BTreeMap, HashMap, HashSet};
use std::fs;
use std::io::Read as _;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use rand::{rngs::OsRng, RngCore as _};
use regex::{Captures, Regex};
use rusqlite::{params, Connection, TransactionBehavior};
use serde_json::{json, Map, Value};
use sha2::{Digest as _, Sha256};

use super::native_evidence_store::NativeEvidenceStore;

const CHUNK_SIZE: usize = 64 * 1024;
const LOSS_POLICY: &str = "exact-externalized";
const TRUNCATION_SUFFIX: &str = "\n[visible view truncated; use CCR handle for exact restoration]";

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "compress" && action == "put")
}

#[derive(Debug)]
struct PutArguments {
    input: Option<PathBuf>,
    text: Option<String>,
    hint: String,
    path: String,
    budget_bytes: i64,
}

fn next_value(tail: &[String], index: &mut usize, option: &str) -> Result<String, String> {
    *index += 1;
    tail.get(*index)
        .cloned()
        .ok_or_else(|| format!("COMPRESSION_OPTION_VALUE_MISSING:{option}"))
}

fn parse_arguments(arguments: &[String]) -> Result<PutArguments, String> {
    let start = arguments
        .windows(2)
        .position(|row| row[0] == "compress" && row[1] == "put")
        .map(|index| index + 2)
        .ok_or_else(|| "COMPRESSION_PUT_COMMAND_MISSING".to_owned())?;
    let tail = &arguments[start..];
    let mut input = None;
    let mut text = None;
    let mut hint = String::new();
    let mut path = String::new();
    let mut budget_bytes = 8192_i64;
    let mut index = 0_usize;
    while index < tail.len() {
        let value = &tail[index];
        if value == "--input" {
            input = Some(PathBuf::from(next_value(tail, &mut index, "--input")?));
        } else if let Some(value) = value.strip_prefix("--input=") {
            input = Some(PathBuf::from(value));
        } else if value == "--text" {
            text = Some(next_value(tail, &mut index, "--text")?);
        } else if let Some(value) = value.strip_prefix("--text=") {
            text = Some(value.to_owned());
        } else if value == "--hint" {
            hint = next_value(tail, &mut index, "--hint")?;
        } else if let Some(value) = value.strip_prefix("--hint=") {
            hint = value.to_owned();
        } else if value == "--path" {
            path = next_value(tail, &mut index, "--path")?;
        } else if let Some(value) = value.strip_prefix("--path=") {
            path = value.to_owned();
        } else if value == "--budget-bytes" {
            budget_bytes = next_value(tail, &mut index, "--budget-bytes")?
                .parse::<i64>()
                .map_err(|error| format!("COMPRESSION_BUDGET_INVALID:{error}"))?;
        } else if let Some(value) = value.strip_prefix("--budget-bytes=") {
            budget_bytes = value
                .parse::<i64>()
                .map_err(|error| format!("COMPRESSION_BUDGET_INVALID:{error}"))?;
        } else {
            return Err(format!("COMPRESSION_OPTION_UNKNOWN:{value}"));
        }
        index += 1;
    }
    Ok(PutArguments {
        input,
        text,
        hint,
        path,
        budget_bytes,
    })
}

fn read_input(arguments: &PutArguments) -> Result<(Vec<u8>, String), String> {
    if let Some(input) = &arguments.input {
        let data =
            fs::read(input).map_err(|error| format!("COMPRESSION_INPUT_READ_FAILED:{error}"))?;
        let path = if arguments.path.is_empty() {
            input.to_string_lossy().into_owned()
        } else {
            arguments.path.clone()
        };
        return Ok((data, path));
    }
    if let Some(text) = &arguments.text {
        return Ok((text.as_bytes().to_vec(), arguments.path.clone()));
    }
    let mut text = String::new();
    std::io::stdin()
        .read_to_string(&mut text)
        .map_err(|error| format!("COMPRESSION_STDIN_READ_FAILED:{error}"))?;
    Ok((text.into_bytes(), arguments.path.clone()))
}

fn now_seconds() -> Result<f64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_secs_f64())
        .map_err(|error| format!("COMPRESSION_CLOCK_FAILED:{error}"))
}

fn hex(bytes: &[u8]) -> String {
    let mut rendered = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        use std::fmt::Write as _;
        write!(&mut rendered, "{byte:02x}").expect("writing to String cannot fail");
    }
    rendered
}

fn random_compression_id() -> String {
    let mut bytes = [0_u8; 16];
    OsRng.fill_bytes(&mut bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    format!("ccr-{}", hex(&bytes))
}

fn canonical_json(value: &Value) -> Result<Vec<u8>, String> {
    serde_json::to_vec(value).map_err(|error| format!("COMPRESSION_JSON_SERIALIZE_FAILED:{error}"))
}

fn receipt_hash(
    compression_id: &str,
    content_type: &str,
    original_bytes: usize,
    visible_text: &str,
    exact_handle: &str,
    chunk_handles: &[String],
    metadata: &Value,
) -> Result<String, String> {
    let mut payload = BTreeMap::<String, Value>::new();
    payload.insert(
        "chunk_handles".to_owned(),
        Value::Array(chunk_handles.iter().cloned().map(Value::String).collect()),
    );
    payload.insert("chunk_size".to_owned(), Value::from(CHUNK_SIZE));
    payload.insert(
        "compression_id".to_owned(),
        Value::String(compression_id.to_owned()),
    );
    payload.insert(
        "content_type".to_owned(),
        Value::String(content_type.to_owned()),
    );
    payload.insert(
        "exact_handle".to_owned(),
        Value::String(exact_handle.to_owned()),
    );
    payload.insert(
        "loss_policy".to_owned(),
        Value::String(LOSS_POLICY.to_owned()),
    );
    payload.insert("metadata".to_owned(), metadata.clone());
    payload.insert("original_bytes".to_owned(), Value::from(original_bytes));
    payload.insert("visible_bytes".to_owned(), Value::from(visible_text.len()));
    let value = serde_json::to_value(payload)
        .map_err(|error| format!("COMPRESSION_RECEIPT_VALUE_FAILED:{error}"))?;
    Ok(hex(&Sha256::digest(canonical_json(&value)?)))
}

fn bounded(text: &str, budget: i64) -> String {
    let encoded = text.as_bytes();
    if budget >= 0 && i64::try_from(encoded.len()).is_ok_and(|length| length <= budget) {
        return text.to_owned();
    }
    let suffix_bytes = TRUNCATION_SUFFIX.as_bytes().len();
    let mut keep = if budget <= 0 {
        0
    } else {
        usize::try_from(budget)
            .unwrap_or(usize::MAX)
            .saturating_sub(suffix_bytes)
            .min(encoded.len())
    };
    while keep > 0 && std::str::from_utf8(&encoded[..keep]).is_err() {
        keep -= 1;
    }
    let prefix = std::str::from_utf8(&encoded[..keep])
        .unwrap_or("")
        .trim_end();
    format!("{prefix}{TRUNCATION_SUFFIX}")
}

fn secret_regex() -> Regex {
    Regex::new(
        r"(?i)\b(api[_-]?key|access[_-]?token|authorization|password|secret|bearer)\b\s*[:=]\s*([^\s,;]+)",
    )
    .expect("secret regex")
}

fn error_regex() -> Regex {
    Regex::new(r"(?i)\b(error|failed|failure|panic|assertion|traceback|exception|fatal|denied)\b")
        .expect("error regex")
}

fn stack_regex() -> Regex {
    Regex::new(
        r#"(?:File "[^"]+", line \d+|\bat [\w.$<>]+\([^)]*:\d+\)|[^\s:]+\.(?:py|rs|js|ts|java|cs|go|rb|php):\d+)"#,
    )
    .expect("stack regex")
}

fn redact(text: &str) -> String {
    secret_regex()
        .replace_all(text, |captures: &Captures<'_>| {
            format!(
                "{}=<redacted>",
                captures.get(1).map_or("", |value| value.as_str())
            )
        })
        .into_owned()
}

fn lines(text: &str) -> Vec<&str> {
    text.lines()
        .map(|line| line.strip_suffix('\r').unwrap_or(line))
        .collect()
}

fn detect(data: &[u8], hint: &str, path: &str) -> String {
    let lower_hint = hint.to_lowercase();
    if !lower_hint.is_empty() {
        let alias = match lower_hint.as_str() {
            "yaml" | "yml" => Some("yaml"),
            "json" => Some("json"),
            "csv" | "tsv" => Some("table"),
            "code" => Some("code"),
            "diff" => Some("diff"),
            "log" => Some("log"),
            "stack" => Some("stack-trace"),
            "xml" | "html" => Some("xml"),
            "text" => Some("text"),
            "rag" => Some("rag"),
            _ => None,
        };
        if let Some(alias) = alias {
            return alias.to_owned();
        }
    }
    let suffix = Path::new(path)
        .extension()
        .and_then(|value| value.to_str())
        .map(|value| format!(".{}", value.to_lowercase()))
        .unwrap_or_default();
    let sample = String::from_utf8_lossy(&data[..data.len().min(65_536)]);
    let stripped = sample.trim_start();
    if matches!(suffix.as_str(), ".json" | ".jsonl")
        || stripped.starts_with('{')
        || stripped.starts_with('[')
    {
        if serde_json::from_str::<Value>(&sample).is_ok() {
            return "json".to_owned();
        }
        if suffix == ".jsonl" {
            return "jsonl".to_owned();
        }
    }
    if matches!(suffix.as_str(), ".csv" | ".tsv") {
        return "table".to_owned();
    }
    if matches!(
        suffix.as_str(),
        ".py"
            | ".js"
            | ".jsx"
            | ".ts"
            | ".tsx"
            | ".rs"
            | ".go"
            | ".java"
            | ".cs"
            | ".c"
            | ".cpp"
            | ".h"
            | ".hpp"
            | ".rb"
            | ".php"
            | ".lua"
            | ".luau"
    ) {
        return "code".to_owned();
    }
    let sample_lines = lines(&sample);
    if sample_lines.iter().take(20).any(|line| {
        line.starts_with("diff --git")
            || line.starts_with("index ")
            || line.starts_with("--- ")
            || line.starts_with("+++ ")
            || line.starts_with("@@ ")
    }) {
        return "diff".to_owned();
    }
    let stack = stack_regex();
    if sample_lines
        .iter()
        .take(100)
        .filter(|line| stack.is_match(line))
        .count()
        >= 2
    {
        return "stack-trace".to_owned();
    }
    if stripped.starts_with('<') && stripped.chars().take(500).any(|value| value == '>') {
        return "xml".to_owned();
    }
    let errors = error_regex();
    if sample_lines
        .iter()
        .take(100)
        .filter(|line| errors.is_match(line))
        .count()
        >= 2
        || sample_lines.len() > 200
    {
        return "log".to_owned();
    }
    "text".to_owned()
}

fn summarize_json(value: &Value, depth: usize) -> Value {
    if depth >= 5 {
        return match value {
            Value::Object(items) => Value::String(format!("<dict:{}>", items.len())),
            Value::Array(items) => Value::String(format!("<list:{}>", items.len())),
            _ => value.clone(),
        };
    }
    match value {
        Value::Object(items) => {
            let mut output = Map::new();
            for (index, (key, item)) in items.iter().enumerate() {
                if index >= 40 {
                    output.insert(
                        "<omitted_keys>".to_owned(),
                        Value::from(items.len() - index),
                    );
                    break;
                }
                output.insert(key.clone(), summarize_json(item, depth + 1));
            }
            Value::Object(output)
        }
        Value::Array(items) if items.len() > 12 => json!({
            "<array_length>": items.len(),
            "<head>": items.iter().take(5).map(|item| summarize_json(item, depth + 1)).collect::<Vec<_>>(),
            "<tail>": items.iter().skip(items.len() - 3).map(|item| summarize_json(item, depth + 1)).collect::<Vec<_>>(),
        }),
        Value::Array(items) => Value::Array(
            items
                .iter()
                .map(|item| summarize_json(item, depth + 1))
                .collect(),
        ),
        Value::String(text) if text.chars().count() > 500 => {
            let length = text.chars().count();
            let head = text.chars().take(240).collect::<String>();
            let tail = text
                .chars()
                .rev()
                .take(120)
                .collect::<String>()
                .chars()
                .rev()
                .collect::<String>();
            Value::String(format!("{head}…<{length} chars>…{tail}"))
        }
        _ => value.clone(),
    }
}

fn compress_json(text: &str, budget: i64) -> Result<(String, Value), String> {
    let value = serde_json::from_str::<Value>(text)
        .map_err(|error| format!("COMPRESSION_JSON_INVALID:{error}"))?;
    let records = match &value {
        Value::Object(items) => items.len(),
        Value::Array(items) => items.len(),
        _ => 1,
    };
    let visible = serde_json::to_string_pretty(&summarize_json(&value, 0))
        .map_err(|error| format!("COMPRESSION_JSON_RENDER_FAILED:{error}"))?;
    Ok((
        bounded(&redact(&visible), budget),
        json!({"records": records}),
    ))
}

fn compress_jsonl(text: &str, budget: i64) -> Result<(String, Value), String> {
    let mut rows = Vec::new();
    let mut invalid = 0_usize;
    for line in lines(text) {
        if line.trim().is_empty() {
            continue;
        }
        match serde_json::from_str::<Value>(line) {
            Ok(value) => rows.push(value),
            Err(_) => invalid += 1,
        }
    }
    let payload = serde_json::to_string(&rows)
        .map_err(|error| format!("COMPRESSION_JSONL_RENDER_FAILED:{error}"))?;
    let (visible, _) = compress_json(&payload, budget)?;
    Ok((
        visible,
        json!({"records": rows.len(), "invalid_records": invalid}),
    ))
}

fn parse_delimited(text: &str, delimiter: char) -> Vec<Vec<String>> {
    if text.is_empty() {
        return Vec::new();
    }
    let mut rows = Vec::<Vec<String>>::new();
    let mut row = Vec::<String>::new();
    let mut field = String::new();
    let mut quoted = false;
    let mut chars = text.chars().peekable();
    while let Some(value) = chars.next() {
        if quoted {
            if value == '"' {
                if chars.peek() == Some(&'"') {
                    field.push('"');
                    chars.next();
                } else {
                    quoted = false;
                }
            } else {
                field.push(value);
            }
            continue;
        }
        if value == '"' && field.is_empty() {
            quoted = true;
        } else if value == delimiter {
            row.push(std::mem::take(&mut field));
        } else if value == '\n' {
            row.push(std::mem::take(&mut field));
            rows.push(std::mem::take(&mut row));
        } else if value != '\r' {
            field.push(value);
        }
    }
    if !field.is_empty() || !row.is_empty() || !(text.ends_with('\n') || text.ends_with('\r')) {
        row.push(field);
        rows.push(row);
    }
    rows
}

fn compress_table(text: &str, path: &str, budget: i64) -> (String, Value) {
    let delimiter = if Path::new(path)
        .extension()
        .and_then(|value| value.to_str())
        .is_some_and(|value| value.eq_ignore_ascii_case("tsv"))
    {
        '\t'
    } else {
        ','
    };
    let rows = parse_delimited(text, delimiter);
    if rows.is_empty() {
        return ("<empty table>".to_owned(), json!({"rows": 0, "columns": 0}));
    }
    let header = &rows[0];
    let body = &rows[1..];
    let mut sample_indexes = (0..body.len().min(5)).collect::<Vec<_>>();
    if body.len() > 7 {
        sample_indexes.push(body.len() - 2);
        sample_indexes.push(body.len() - 1);
    }
    let mut widths = Vec::new();
    for column in 0..header.len() {
        let mut width = header[column].chars().count();
        for index in &sample_indexes {
            width = width.max(
                body[*index]
                    .get(column)
                    .map_or(0, |value| value.chars().count()),
            );
        }
        widths.push(width);
    }
    let mut rendered = vec![
        format!("Rows: {} | Columns: {}", body.len(), header.len()),
        header.join(" | "),
        widths
            .iter()
            .map(|width| "-".repeat((*width).clamp(3, 30)))
            .collect::<Vec<_>>()
            .join(" | "),
    ];
    for index in sample_indexes {
        rendered.push(
            body[index]
                .iter()
                .take(header.len())
                .cloned()
                .collect::<Vec<_>>()
                .join(" | "),
        );
    }
    let samples = rendered.len().saturating_sub(3);
    if body.len() > samples {
        rendered.push(format!("… {} rows externalized …", body.len() - samples));
    }
    (
        bounded(&redact(&rendered.join("\n")), budget),
        json!({"rows": body.len(), "columns": header.len()}),
    )
}

fn unique_preserve(values: impl IntoIterator<Item = String>) -> Vec<String> {
    let mut seen = HashSet::new();
    let mut output = Vec::new();
    for value in values {
        if seen.insert(value.clone()) {
            output.push(value);
        }
    }
    output
}

fn compress_log(text: &str, budget: i64) -> (String, Value) {
    let errors = error_regex();
    let number = Regex::new(r"\b\d+(?:\.\d+)?\b").expect("number regex");
    let all_lines = lines(text);
    let mut counts = HashMap::<String, usize>::new();
    let mut order = Vec::<String>::new();
    let mut critical = Vec::<String>::new();
    for raw in &all_lines {
        let line = redact(raw.trim());
        if line.is_empty() {
            continue;
        }
        let normalized = number.replace_all(&line, "<n>").into_owned();
        if !counts.contains_key(&normalized) {
            order.push(normalized.clone());
        }
        *counts.entry(normalized).or_default() += 1;
        if errors.is_match(&line) && critical.len() < 40 {
            critical.push(line);
        }
    }
    let mut visible = vec![format!(
        "Log lines: {} | Unique event shapes: {}",
        all_lines.len(),
        counts.len()
    )];
    if !critical.is_empty() {
        visible.push("Critical:".to_owned());
        visible.extend(unique_preserve(critical.clone()));
    }
    visible.push("Event shapes:".to_owned());
    for shape in order.iter().take(50) {
        visible.push(format!("[{}x] {shape}", counts[shape]));
    }
    if order.len() > 50 {
        visible.push(format!(
            "… {} event shapes externalized …",
            order.len() - 50
        ));
    }
    (
        bounded(&visible.join("\n"), budget),
        json!({
            "lines": all_lines.len(),
            "unique_shapes": counts.len(),
            "critical": critical.len(),
        }),
    )
}

fn compress_stack_trace(text: &str, budget: i64) -> (String, Value) {
    let errors = error_regex();
    let stack = stack_regex();
    let filtered = lines(text)
        .into_iter()
        .filter(|line| !line.trim().is_empty())
        .map(|line| redact(line.trim_end()))
        .collect::<Vec<_>>();
    let root = filtered
        .iter()
        .rev()
        .find(|line| errors.is_match(line))
        .cloned()
        .or_else(|| filtered.last().cloned())
        .unwrap_or_default();
    let frames = filtered
        .iter()
        .filter(|line| stack.is_match(line))
        .cloned()
        .collect::<Vec<_>>();
    let unique = unique_preserve(frames.clone());
    let mut visible = vec![
        format!("Root cause: {root}"),
        format!("Frames: {} ({} unique)", frames.len(), unique.len()),
    ];
    visible.extend(unique.iter().take(40).cloned());
    (
        bounded(&visible.join("\n"), budget),
        json!({"frames": frames.len(), "unique_frames": unique.len()}),
    )
}

fn is_diff_header(line: &str) -> bool {
    line.starts_with("diff --git")
        || line.starts_with("index ")
        || line.starts_with("--- ")
        || line.starts_with("+++ ")
        || line.starts_with("@@ ")
}

fn compress_diff(text: &str, budget: i64) -> (String, Value) {
    let all_lines = lines(text);
    let headers = all_lines
        .iter()
        .filter(|line| is_diff_header(line))
        .copied()
        .collect::<Vec<_>>();
    let changes = all_lines
        .iter()
        .filter(|line| {
            (line.starts_with('+') || line.starts_with('-'))
                && !line.starts_with("+++")
                && !line.starts_with("---")
        })
        .copied()
        .collect::<Vec<_>>();
    let mut visible = vec![format!(
        "Diff lines: {} | Changed lines: {}",
        all_lines.len(),
        changes.len()
    )];
    visible.extend(headers.iter().take(80).map(ToString::to_string));
    visible.extend(changes.iter().take(120).map(ToString::to_string));
    if changes.len() > 120 {
        visible.push(format!(
            "… {} changed lines externalized …",
            changes.len() - 120
        ));
    }
    (
        bounded(&redact(&visible.join("\n")), budget),
        json!({"lines": all_lines.len(), "changed_lines": changes.len()}),
    )
}

#[derive(Clone)]
struct CodeSymbol {
    line: usize,
    end_line: usize,
    kind: String,
    qualified_name: String,
    signature: String,
}

struct CodeParse {
    language: String,
    parser: String,
    symbols: Vec<CodeSymbol>,
    edges: usize,
    diagnostics: Vec<String>,
}

struct CodeProfile {
    language: &'static str,
    definitions: Vec<(&'static str, &'static str)>,
    imports: Vec<&'static str>,
    inheritance: Vec<&'static str>,
    calls: Vec<&'static str>,
    keywords: &'static [&'static str],
}

fn code_profile(suffix: &str) -> Option<CodeProfile> {
    match suffix {
        ".rs" => Some(CodeProfile {
            language: "rust",
            definitions: vec![
                (
                    "function",
                    r"\b(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+([A-Za-z_][\w]*)\s*(?:<[^>]+>)?\s*\(",
                ),
                ("struct", r"\b(?:pub\s+)?struct\s+([A-Za-z_][\w]*)"),
                ("enum", r"\b(?:pub\s+)?enum\s+([A-Za-z_][\w]*)"),
                ("trait", r"\b(?:pub\s+)?trait\s+([A-Za-z_][\w]*)"),
                ("type", r"\b(?:pub\s+)?type\s+([A-Za-z_][\w]*)\s*="),
                ("module", r"\b(?:pub\s+)?mod\s+([A-Za-z_][\w]*)"),
            ],
            imports: vec![r"\buse\s+([^;]+);", r"\bextern\s+crate\s+([A-Za-z_][\w]*)"],
            inheritance: vec![r"\bimpl(?:<[^>]+>)?\s+([^\s{]+)\s+for\s+([^\s{]+)"],
            calls: vec![r"\b([A-Za-z_][\w:]*)\s*!?\s*\("],
            keywords: &[
                "if", "for", "while", "match", "loop", "return", "Some", "Ok", "Err",
            ],
        }),
        ".js" | ".jsx" | ".mjs" | ".cjs" | ".ts" | ".tsx" => Some(CodeProfile {
            language: "javascript",
            definitions: vec![
                (
                    "class",
                    r"\b(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)",
                ),
                (
                    "interface",
                    r"\b(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)",
                ),
                ("type", r"\b(?:export\s+)?type\s+([A-Za-z_$][\w$]*)\s*="),
                ("enum", r"\b(?:export\s+)?enum\s+([A-Za-z_$][\w$]*)"),
                (
                    "function",
                    r"\b(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(",
                ),
            ],
            imports: vec![
                r#"\bimport(?:[^'"]*?from\s*)?['"]([^'"]+)['"]"#,
                r#"\brequire\(\s*['"]([^'"]+)['"]\s*\)"#,
            ],
            inheritance: vec![r"\bclass\s+\w+\s+extends\s+([A-Za-z_$][\w$.]*)"],
            calls: vec![r"\b([A-Za-z_$][\w$.:]*)\s*\("],
            keywords: &[
                "if", "for", "while", "switch", "catch", "return", "typeof", "new", "function",
            ],
        }),
        ".go" => Some(CodeProfile {
            language: "go",
            definitions: vec![
                (
                    "function",
                    r"\bfunc\s+(?:\([^)]*\)\s*)?([A-Za-z_][\w]*)\s*\(",
                ),
                (
                    "type",
                    r"\btype\s+([A-Za-z_][\w]*)\s+(?:struct|interface|=)",
                ),
                ("variable", r"\bvar\s+([A-Za-z_][\w]*)\s+"),
            ],
            imports: vec![r#"(?m)^\s*import\s+(?:[A-Za-z_.]+\s+)?"([^"]+)""#],
            inheritance: vec![],
            calls: vec![r"\b([A-Za-z_$][\w$.:]*)\s*\("],
            keywords: &[
                "if", "for", "switch", "select", "return", "go", "defer", "make", "new", "append",
                "len", "cap",
            ],
        }),
        ".py" => Some(CodeProfile {
            language: "python",
            definitions: vec![
                ("class", r"(?m)^\s*class\s+([A-Za-z_][\w]*)"),
                (
                    "function",
                    r"(?m)^\s*(?:async\s+)?def\s+([A-Za-z_][\w]*)\s*\(",
                ),
            ],
            imports: vec![
                r"(?m)^\s*import\s+([^\n]+)",
                r"(?m)^\s*from\s+([^\s]+)\s+import\s+",
            ],
            inheritance: vec![r"(?m)^\s*class\s+\w+\s*\(([^)]*)\)"],
            calls: vec![r"\b([A-Za-z_][\w.]*)\s*\("],
            keywords: &[
                "if", "for", "while", "return", "class", "def", "print", "len", "range",
            ],
        }),
        _ => None,
    }
}

fn code_line(text: &str, offset: usize) -> usize {
    text[..offset].bytes().filter(|byte| *byte == b'\n').count() + 1
}

fn parse_code_lexical(path: &str, text: &str) -> CodeParse {
    let suffix = Path::new(path)
        .extension()
        .and_then(|value| value.to_str())
        .map(|value| format!(".{}", value.to_lowercase()))
        .unwrap_or_default();
    let Some(profile) = code_profile(&suffix) else {
        return CodeParse {
            language: "unknown".to_owned(),
            parser: "unsupported".to_owned(),
            symbols: Vec::new(),
            edges: 0,
            diagnostics: vec!["unsupported-language".to_owned()],
        };
    };
    let mut symbols = Vec::<CodeSymbol>::new();
    let mut positions = Vec::<(usize, String)>::new();
    let mut edges = 0_usize;
    for (kind, pattern) in &profile.definitions {
        let regex = Regex::new(pattern).expect("code definition regex");
        for found in regex.captures_iter(text) {
            let whole = found.get(0).expect("whole match");
            let name = found.get(1).map_or("", |value| value.as_str()).trim();
            if name.is_empty() || profile.keywords.contains(&name) {
                continue;
            }
            let line = code_line(text, whole.start());
            let signature = whole
                .as_str()
                .trim()
                .split('{')
                .next()
                .unwrap_or("")
                .chars()
                .take(300)
                .collect::<String>();
            let qualified = name.replace("::", ".").replace(':', ".");
            symbols.push(CodeSymbol {
                line,
                end_line: line,
                kind: (*kind).to_owned(),
                qualified_name: qualified.clone(),
                signature,
            });
            positions.push((whole.start(), qualified));
            edges += 1;
        }
    }
    positions.sort_by_key(|item| item.0);
    let source_for = |offset: usize| -> String {
        let mut source = "<file>".to_owned();
        for (position, candidate) in &positions {
            if *position > offset {
                break;
            }
            source.clone_from(candidate);
        }
        source
    };
    for pattern in &profile.imports {
        let regex = Regex::new(pattern).expect("code import regex");
        for found in regex.captures_iter(text) {
            let target = found
                .get(1)
                .or_else(|| found.get(0))
                .map_or("", |value| value.as_str())
                .trim();
            if !target.is_empty() {
                let _ = source_for(found.get(0).expect("whole match").start());
                edges += 1;
            }
        }
    }
    let splitter = Regex::new(r"\s*,\s*|\s+").expect("inheritance splitter");
    for pattern in &profile.inheritance {
        let regex = Regex::new(pattern).expect("code inheritance regex");
        for found in regex.captures_iter(text) {
            for group in found.iter().skip(1).flatten() {
                for target in splitter.split(group.as_str().trim()) {
                    let target = target
                        .trim_matches(|value: char| matches!(value, '{' | '}' | ':' | '(' | ')'));
                    if !target.is_empty()
                        && !matches!(
                            target,
                            "public" | "private" | "protected" | "implements" | "extends" | "for"
                        )
                    {
                        edges += 1;
                    }
                }
            }
        }
    }
    let mut seen_calls = HashSet::<(String, String, usize)>::new();
    for pattern in &profile.calls {
        let regex = Regex::new(pattern).expect("code call regex");
        for found in regex.captures_iter(text) {
            let whole = found.get(0).expect("whole match");
            let target = found.get(1).map_or("", |value| value.as_str()).trim();
            let short = target
                .rsplit('.')
                .next()
                .unwrap_or(target)
                .rsplit("::")
                .next()
                .unwrap_or(target)
                .rsplit(':')
                .next()
                .unwrap_or(target);
            if short.is_empty() || profile.keywords.contains(&short) {
                continue;
            }
            let line = code_line(text, whole.start());
            let source = source_for(whole.start());
            if seen_calls.insert((source, target.to_owned(), line)) {
                edges += 1;
                if short != target {
                    edges += 1;
                }
            }
        }
    }
    CodeParse {
        language: profile.language.to_owned(),
        parser: if profile.language == "python" {
            "python-ast-v3".to_owned()
        } else {
            format!("language-lexical-v3:{}", profile.language)
        },
        symbols,
        edges,
        diagnostics: Vec::new(),
    }
}

fn semantic_snapshot(project_root: &Path, path: &str) -> Option<CodeParse> {
    let safe = path.replace('\\', "__").replace('/', "__");
    let snapshot = project_root
        .join(".syntavra/semantic")
        .join(format!("{safe}.json"));
    let value = serde_json::from_slice::<Value>(&fs::read(snapshot).ok()?).ok()?;
    let rows = value["symbols"].as_array()?;
    if rows.is_empty() {
        return None;
    }
    let symbols = rows
        .iter()
        .map(|row| CodeSymbol {
            line: row["line"]
                .as_u64()
                .and_then(|value| usize::try_from(value).ok())
                .unwrap_or(1),
            end_line: row["end_line"]
                .as_u64()
                .and_then(|value| usize::try_from(value).ok())
                .unwrap_or(1),
            kind: row["kind"].as_str().unwrap_or("").to_owned(),
            qualified_name: row["qualified_name"].as_str().unwrap_or("").to_owned(),
            signature: row["signature"].as_str().unwrap_or("").to_owned(),
        })
        .collect::<Vec<_>>();
    Some(CodeParse {
        language: value["language"].as_str().unwrap_or("semantic").to_owned(),
        parser: "semantic-snapshot-v1".to_owned(),
        symbols,
        edges: value["edges"].as_array().map_or(0, Vec::len),
        diagnostics: Vec::new(),
    })
}

fn compress_code(text: &str, path: &str, budget: i64, project_root: &Path) -> (String, Value) {
    if path.is_empty() {
        return compress_text(text, budget);
    }
    let parsed =
        semantic_snapshot(project_root, path).unwrap_or_else(|| parse_code_lexical(path, text));
    let mut rendered = vec![format!(
        "Language: {} | Parser: {} | Symbols: {} | Edges: {}",
        parsed.language,
        parsed.parser,
        parsed.symbols.len(),
        parsed.edges
    )];
    for symbol in parsed.symbols.iter().take(100) {
        let row = format!(
            "{}:{} {} {} {}",
            symbol.line, symbol.end_line, symbol.kind, symbol.qualified_name, symbol.signature
        );
        rendered.push(row.trim_end().to_owned());
    }
    if !parsed.diagnostics.is_empty() {
        rendered.push(format!("Diagnostics: {}", parsed.diagnostics.join("; ")));
    }
    (
        bounded(&rendered.join("\n"), budget),
        json!({
            "language": parsed.language,
            "parser": parsed.parser,
            "symbols": parsed.symbols.len(),
            "edges": parsed.edges,
        }),
    )
}

fn compress_xml(text: &str, budget: i64) -> (String, Value) {
    let tags_regex = Regex::new(r"</?([A-Za-z_:][\w:.-]*)").expect("xml tags regex");
    let all_tags = Regex::new(r"<[^>]+>").expect("xml strip regex");
    let whitespace = Regex::new(r"\s+").expect("whitespace regex");
    let mut tags = BTreeMap::<String, usize>::new();
    for found in tags_regex.captures_iter(text) {
        let name = found.get(1).expect("tag name").as_str().to_owned();
        *tags.entry(name).or_default() += 1;
    }
    let without_tags = all_tags.replace_all(text, " ");
    let plain = whitespace.replace_all(&without_tags, " ").trim().to_owned();
    let tag_summary = tags
        .iter()
        .take(50)
        .map(|(key, value)| format!("{key}={value}"))
        .collect::<Vec<_>>()
        .join(", ");
    let excerpt = plain.chars().take(4000).collect::<String>();
    (
        bounded(
            &redact(&format!("Tags: {tag_summary}\nText: {excerpt}")),
            budget,
        ),
        json!({"tags": tags.len()}),
    )
}

fn compress_rag(text: &str, budget: i64) -> (String, Value) {
    let blocks_regex = Regex::new(r"\n{2,}").expect("rag blocks regex");
    let words = Regex::new(r"\w+").expect("rag words regex");
    let blocks = blocks_regex
        .split(text)
        .map(str::trim)
        .filter(|block| !block.is_empty())
        .map(ToOwned::to_owned)
        .collect::<Vec<_>>();
    let mut scored = blocks
        .iter()
        .enumerate()
        .map(|(index, block)| {
            let lowered = block.to_lowercase();
            let unique = words
                .find_iter(&lowered)
                .map(|value| value.as_str().to_owned())
                .collect::<HashSet<_>>()
                .len();
            (index, unique, block.clone())
        })
        .collect::<Vec<_>>();
    scored.sort_by(|left, right| right.1.cmp(&left.1).then(left.0.cmp(&right.0)));
    let selected = scored
        .iter()
        .take(10)
        .map(|item| item.2.clone())
        .collect::<Vec<_>>();
    (
        bounded(&redact(&selected.join("\n\n---\n\n")), budget),
        json!({"blocks": blocks.len(), "selected": selected.len()}),
    )
}

fn sentence_segments(text: &str) -> Vec<String> {
    let separator = Regex::new(r"[.!?]\s+|\n+").expect("sentence separator regex");
    let mut output = Vec::new();
    let mut start = 0_usize;
    for found in separator.find_iter(text) {
        let matched = found.as_str();
        let end =
            if matched.starts_with('.') || matched.starts_with('!') || matched.starts_with('?') {
                found.start() + 1
            } else {
                found.start()
            };
        output.push(text[start..end].to_owned());
        start = found.end();
    }
    output.push(text[start..].to_owned());
    output
}

fn compress_text(text: &str, budget: i64) -> (String, Value) {
    let errors = error_regex();
    let sentences = sentence_segments(text)
        .into_iter()
        .map(|item| redact(item.trim()))
        .filter(|item| !item.is_empty())
        .collect::<Vec<_>>();
    let unique = unique_preserve(sentences.clone());
    let critical = unique
        .iter()
        .filter(|sentence| errors.is_match(sentence))
        .take(20)
        .cloned()
        .collect::<Vec<_>>();
    let selected = unique_preserve(
        critical
            .into_iter()
            .chain(unique.iter().take(20).cloned())
            .chain(unique.iter().skip(unique.len().saturating_sub(8)).cloned()),
    );
    let mut visible = selected.join("\n");
    if unique.len() > selected.len() {
        use std::fmt::Write as _;
        write!(
            &mut visible,
            "\n… {} segments externalized …",
            unique.len() - selected.len()
        )
        .expect("writing to String cannot fail");
    }
    (
        bounded(&visible, budget),
        json!({"segments": sentences.len(), "unique_segments": unique.len()}),
    )
}

fn compress_content(
    raw: &[u8],
    content_type: &str,
    path: &str,
    budget: i64,
    project_root: &Path,
) -> Result<(String, Value), String> {
    let text = String::from_utf8_lossy(raw);
    match content_type {
        "json" => compress_json(&text, budget),
        "jsonl" => compress_jsonl(&text, budget),
        "table" => Ok(compress_table(&text, path, budget)),
        "log" => Ok(compress_log(&text, budget)),
        "stack-trace" => Ok(compress_stack_trace(&text, budget)),
        "diff" => Ok(compress_diff(&text, budget)),
        "code" => Ok(compress_code(&text, path, budget, project_root)),
        "xml" => Ok(compress_xml(&text, budget)),
        "rag" => Ok(compress_rag(&text, budget)),
        _ => Ok(compress_text(&text, budget)),
    }
}

fn metadata_value(path: &str, hint: &str, details: &Value) -> Result<Value, String> {
    let mut output = Map::new();
    output.insert("path".to_owned(), Value::String(path.to_owned()));
    output.insert("hint".to_owned(), Value::String(hint.to_owned()));
    let object = details
        .as_object()
        .ok_or_else(|| "COMPRESSION_DETAILS_INVALID".to_owned())?;
    for (key, value) in object {
        output.insert(key.clone(), value.clone());
    }
    Ok(Value::Object(output))
}

fn put_record(
    database_path: &Path,
    evidence: &NativeEvidenceStore,
    raw: &[u8],
    content_type: &str,
    provisional_visible: &str,
    metadata: &Value,
    budget: i64,
) -> Result<Value, String> {
    let compression_id = random_compression_id();
    let exact_handle = evidence.put(raw, &format!("compressed-source:{content_type}"), metadata)?;
    let chunks = if raw.is_empty() {
        vec![&raw[..]]
    } else {
        raw.chunks(CHUNK_SIZE).collect::<Vec<_>>()
    };
    let mut chunk_handles = Vec::with_capacity(chunks.len());
    for (index, chunk) in chunks.iter().enumerate() {
        chunk_handles.push(evidence.put(
            chunk,
            "compression-chunk",
            &json!({"compression_id": compression_id, "chunk_index": index}),
        )?);
    }
    let provisional_receipt = receipt_hash(
        &compression_id,
        content_type,
        raw.len(),
        provisional_visible,
        &exact_handle,
        &chunk_handles,
        metadata,
    )?;
    let created_at = now_seconds()?;
    let metadata_json = serde_json::to_string(metadata)
        .map_err(|error| format!("COMPRESSION_METADATA_SERIALIZE_FAILED:{error}"))?;
    let mut connection = Connection::open(database_path)
        .map_err(|error| format!("COMPRESSION_DATABASE_OPEN_FAILED:{error}"))?;
    connection
        .execute_batch(
            "PRAGMA foreign_keys=ON; PRAGMA busy_timeout=30000; PRAGMA synchronous=FULL;",
        )
        .map_err(|error| format!("COMPRESSION_DATABASE_PRAGMA_FAILED:{error}"))?;
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|error| format!("COMPRESSION_TRANSACTION_FAILED:{error}"))?;
    transaction
        .execute(
            r#"
            INSERT INTO compressions(
                compression_id,content_type,exact_handle,original_bytes,visible_text,
                chunk_size,chunk_count,metadata_json,receipt_hash,created_at
            ) VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10)
            "#,
            params![
                compression_id,
                content_type,
                exact_handle,
                i64::try_from(raw.len())
                    .map_err(|_| "COMPRESSION_ORIGINAL_SIZE_INVALID".to_owned())?,
                provisional_visible,
                i64::try_from(CHUNK_SIZE)
                    .map_err(|_| "COMPRESSION_CHUNK_SIZE_INVALID".to_owned())?,
                i64::try_from(chunks.len())
                    .map_err(|_| "COMPRESSION_CHUNK_COUNT_INVALID".to_owned())?,
                metadata_json,
                provisional_receipt,
                created_at,
            ],
        )
        .map_err(|error| format!("COMPRESSION_INSERT_FAILED:{error}"))?;
    for (index, (handle, chunk)) in chunk_handles.iter().zip(chunks.iter()).enumerate() {
        transaction
            .execute(
                r#"
                INSERT INTO compression_chunks(
                    compression_id,chunk_index,chunk_handle,chunk_bytes
                ) VALUES(?1,?2,?3,?4)
                "#,
                params![
                    compression_id,
                    i64::try_from(index)
                        .map_err(|_| "COMPRESSION_CHUNK_INDEX_INVALID".to_owned())?,
                    handle,
                    i64::try_from(chunk.len())
                        .map_err(|_| "COMPRESSION_CHUNK_BYTES_INVALID".to_owned())?,
                ],
            )
            .map_err(|error| format!("COMPRESSION_CHUNK_INSERT_FAILED:{error}"))?;
    }
    transaction
        .commit()
        .map_err(|error| format!("COMPRESSION_COMMIT_FAILED:{error}"))?;

    let header = format!(
        "[Syntavra CCR {content_type}: {compression_id} | exact={exact_handle} | chunks={}]",
        chunks.len()
    );
    let final_visible = bounded(&format!("{header}\n{provisional_visible}"), budget);
    let (visible_text, receipt) = if final_visible == provisional_visible {
        (provisional_visible.to_owned(), provisional_receipt)
    } else {
        let receipt = receipt_hash(
            &compression_id,
            content_type,
            raw.len(),
            &final_visible,
            &exact_handle,
            &chunk_handles,
            metadata,
        )?;
        connection
            .execute(
                "UPDATE compressions SET visible_text=?1,receipt_hash=?2 WHERE compression_id=?3",
                params![final_visible, receipt, compression_id],
            )
            .map_err(|error| format!("COMPRESSION_FINALIZE_FAILED:{error}"))?;
        (final_visible, receipt)
    };
    Ok(json!({
        "compression_id": compression_id,
        "content_type": content_type,
        "visible_text": visible_text,
        "original_bytes": raw.len(),
        "visible_bytes": visible_text.len(),
        "exact_handle": exact_handle,
        "chunk_count": chunks.len(),
        "chunk_size": CHUNK_SIZE,
        "reversible": true,
        "loss_policy": LOSS_POLICY,
        "metadata": metadata,
        "receipt_hash": receipt,
    }))
}

pub fn execute(
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    let parsed = parse_arguments(arguments)?;
    let (raw, path) = read_input(&parsed)?;
    let project_id =
        super::state_snapshot_contract::project_id_for_root(&project_root.to_string_lossy())?;
    let evidence = NativeEvidenceStore::open(state_root, &project_id)?;
    let database_path = super::native_compress_describe::initialize_database(state_root)?;
    let content_type = detect(&raw, &parsed.hint, &path);
    let (visible, details) = compress_content(
        &raw,
        &content_type,
        &path,
        parsed.budget_bytes,
        project_root,
    )?;
    let metadata = metadata_value(&path, &parsed.hint, &details)?;
    put_record(
        &database_path,
        &evidence,
        &raw,
        &content_type,
        &visible,
        &metadata,
        parsed.budget_bytes,
    )
}

#[cfg(test)]
mod tests {
    use super::{bounded, detect, parse_delimited, supports, TRUNCATION_SUFFIX};

    #[test]
    fn routes_compress_put_only() {
        assert!(supports(&["compress".to_owned(), "put".to_owned()]));
        assert!(!supports(&["compress".to_owned(), "describe".to_owned()]));
    }

    #[test]
    fn detects_json_and_rust_sources() {
        assert_eq!(detect(br#"{"ok":true}"#, "", "value.json"), "json");
        assert_eq!(detect(b"pub fn main() {}", "", "main.rs"), "code");
    }

    #[test]
    fn tiny_budget_matches_python_suffix_behavior() {
        assert_eq!(bounded("payload", 0), TRUNCATION_SUFFIX);
    }

    #[test]
    fn quoted_table_fields_are_preserved() {
        let rows = parse_delimited("name,value\nalpha,\"one,two\"\n", ',');
        assert_eq!(rows[1][1], "one,two");
    }
}
