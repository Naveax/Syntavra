#![forbid(unsafe_code)]

use std::collections::BTreeSet;
use std::fs;
use std::path::Path;

use serde_json::{json, Value};
use syntavra_core::sha256_hex;

#[derive(Debug, Clone, PartialEq, Eq)]
struct RedactionMatch {
    start: usize,
    end: usize,
    kind: &'static str,
    fingerprint: String,
}

fn is_word(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || byte == b'_'
}

fn is_alnum_dash_underscore(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-')
}

fn is_alnum_underscore(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || byte == b'_'
}

fn is_base64url(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-')
}

fn is_entropy_char(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'+' | b'/' | b'=' | b'-')
}

fn left_boundary(bytes: &[u8], start: usize) -> bool {
    start == 0 || !is_word(bytes[start - 1])
}

fn right_boundary(bytes: &[u8], end: usize) -> bool {
    end == bytes.len() || !is_word(bytes[end])
}

fn fingerprint(value: &[u8]) -> String {
    sha256_hex(value)[..12].to_owned()
}

fn push_match(
    matches: &mut Vec<RedactionMatch>,
    text: &str,
    start: usize,
    end: usize,
    kind: &'static str,
) {
    if start < end && end <= text.len() {
        matches.push(RedactionMatch {
            start,
            end,
            kind,
            fingerprint: fingerprint(&text.as_bytes()[start..end]),
        });
    }
}

fn scan_prefixed(
    text: &str,
    prefix: &str,
    minimum_tail: usize,
    exact_tail: Option<usize>,
    allowed: fn(u8) -> bool,
    kind: &'static str,
    matches: &mut Vec<RedactionMatch>,
) {
    let bytes = text.as_bytes();
    let prefix_bytes = prefix.as_bytes();
    for start in 0..bytes.len() {
        if !left_boundary(bytes, start)
            || bytes.get(start..start + prefix_bytes.len()) != Some(prefix_bytes)
        {
            continue;
        }
        let tail_start = start + prefix_bytes.len();
        let mut end = tail_start;
        while end < bytes.len() && allowed(bytes[end]) {
            end += 1;
        }
        let tail_length = end - tail_start;
        let accepted =
            exact_tail.map_or(tail_length >= minimum_tail, |length| tail_length == length);
        if accepted && right_boundary(bytes, end) {
            push_match(matches, text, start, end, kind);
        }
    }
}

fn scan_jwt(text: &str, matches: &mut Vec<RedactionMatch>) {
    let bytes = text.as_bytes();
    for start in 0..bytes.len() {
        if !left_boundary(bytes, start) || bytes.get(start..start + 3) != Some(b"eyJ") {
            continue;
        }
        let mut cursor = start + 3;
        let first_start = cursor;
        while cursor < bytes.len() && is_base64url(bytes[cursor]) {
            cursor += 1;
        }
        if cursor - first_start < 8 || bytes.get(cursor) != Some(&b'.') {
            continue;
        }
        cursor += 1;
        let second_start = cursor;
        while cursor < bytes.len() && is_base64url(bytes[cursor]) {
            cursor += 1;
        }
        if cursor - second_start < 8 || bytes.get(cursor) != Some(&b'.') {
            continue;
        }
        cursor += 1;
        let third_start = cursor;
        while cursor < bytes.len() && is_base64url(bytes[cursor]) {
            cursor += 1;
        }
        if cursor - third_start >= 8 && right_boundary(bytes, cursor) {
            push_match(matches, text, start, cursor, "jwt");
        }
    }
}

fn scan_private_keys(text: &str, matches: &mut Vec<RedactionMatch>) {
    const BEGINS: [&str; 4] = [
        "-----BEGIN PRIVATE KEY-----",
        "-----BEGIN RSA PRIVATE KEY-----",
        "-----BEGIN EC PRIVATE KEY-----",
        "-----BEGIN OPENSSH PRIVATE KEY-----",
    ];
    const ENDS: [&str; 4] = [
        "-----END PRIVATE KEY-----",
        "-----END RSA PRIVATE KEY-----",
        "-----END EC PRIVATE KEY-----",
        "-----END OPENSSH PRIVATE KEY-----",
    ];
    for begin in BEGINS {
        let mut offset = 0usize;
        while let Some(relative) = text[offset..].find(begin) {
            let start = offset + relative;
            let search_start = start + begin.len();
            let end = ENDS
                .iter()
                .filter_map(|marker| {
                    text[search_start..]
                        .find(marker)
                        .map(|relative_end| search_start + relative_end + marker.len())
                })
                .min();
            if let Some(end) = end {
                push_match(matches, text, start, end, "private-key");
                offset = end;
            } else {
                break;
            }
        }
    }
}

fn ascii_case_starts_with(bytes: &[u8], start: usize, candidate: &[u8]) -> bool {
    bytes
        .get(start..start + candidate.len())
        .is_some_and(|slice| slice.eq_ignore_ascii_case(candidate))
}

fn scan_authorization(text: &str, matches: &mut Vec<RedactionMatch>) {
    const LABELS: [&str; 8] = [
        "authorization",
        "api-key",
        "api_key",
        "api key",
        "apikey",
        "token",
        "secret",
        "password",
    ];
    let bytes = text.as_bytes();
    for start in 0..bytes.len() {
        if !left_boundary(bytes, start) {
            continue;
        }
        for label in LABELS {
            if !ascii_case_starts_with(bytes, start, label.as_bytes()) {
                continue;
            }
            let mut cursor = start + label.len();
            while bytes.get(cursor).is_some_and(u8::is_ascii_whitespace) {
                cursor += 1;
            }
            if !matches!(bytes.get(cursor), Some(b':') | Some(b'=')) {
                continue;
            }
            cursor += 1;
            while bytes.get(cursor).is_some_and(u8::is_ascii_whitespace) {
                cursor += 1;
            }
            if matches!(bytes.get(cursor), Some(b'\"') | Some(b'\'')) {
                cursor += 1;
            }
            let value_start = cursor;
            while bytes
                .get(cursor)
                .is_some_and(|byte| !byte.is_ascii_whitespace() && !matches!(byte, b'\"' | b'\''))
            {
                cursor += 1;
            }
            if cursor - value_start >= 8 {
                push_match(matches, text, start, cursor, "authorization");
            }
        }
    }
}

fn scan_connection_uris(text: &str, matches: &mut Vec<RedactionMatch>) {
    const SCHEMES: [&str; 6] = [
        "postgres://",
        "postgresql://",
        "mysql://",
        "mongodb://",
        "mongodb+srv://",
        "redis://",
    ];
    let bytes = text.as_bytes();
    for start in 0..bytes.len() {
        if !left_boundary(bytes, start) {
            continue;
        }
        for scheme in SCHEMES {
            if bytes.get(start..start + scheme.len()) != Some(scheme.as_bytes()) {
                continue;
            }
            let mut cursor = start + scheme.len();
            let user_start = cursor;
            while bytes
                .get(cursor)
                .is_some_and(|byte| !byte.is_ascii_whitespace() && !matches!(byte, b':' | b'@'))
            {
                cursor += 1;
            }
            if cursor == user_start || bytes.get(cursor) != Some(&b':') {
                continue;
            }
            cursor += 1;
            let password_start = cursor;
            while bytes
                .get(cursor)
                .is_some_and(|byte| !byte.is_ascii_whitespace() && *byte != b'@')
            {
                cursor += 1;
            }
            if cursor == password_start || bytes.get(cursor) != Some(&b'@') {
                continue;
            }
            cursor += 1;
            let host_start = cursor;
            while bytes
                .get(cursor)
                .is_some_and(|byte| !byte.is_ascii_whitespace())
            {
                cursor += 1;
            }
            if cursor > host_start {
                push_match(matches, text, start, cursor, "connection-uri");
            }
        }
    }
}

fn bounded_f64(value: usize) -> f64 {
    f64::from(u32::try_from(value).unwrap_or(u32::MAX))
}

fn entropy(value: &[u8]) -> f64 {
    if value.is_empty() {
        return 0.0;
    }
    let mut counts = [0usize; 256];
    for byte in value {
        counts[usize::from(*byte)] += 1;
    }
    let length = bounded_f64(value.len());
    counts
        .into_iter()
        .filter(|count| *count > 0)
        .map(|count| {
            let probability = bounded_f64(count) / length;
            -probability * probability.log2()
        })
        .sum()
}

fn scan_high_entropy(text: &str, existing: &[RedactionMatch], matches: &mut Vec<RedactionMatch>) {
    let bytes = text.as_bytes();
    let mut cursor = 0usize;
    while cursor < bytes.len() {
        if !is_entropy_char(bytes[cursor]) {
            cursor += 1;
            continue;
        }
        let run_start = cursor;
        while cursor < bytes.len() && is_entropy_char(bytes[cursor]) {
            cursor += 1;
        }
        let run_end = cursor;
        let mut start = run_start;
        while start < run_end && !is_word(bytes[start]) {
            start += 1;
        }
        let mut end = run_end;
        while end > start && !is_word(bytes[end - 1]) {
            end -= 1;
        }
        if end - start < 32 || !left_boundary(bytes, start) || !right_boundary(bytes, end) {
            continue;
        }
        if existing
            .iter()
            .any(|item| item.start <= start && start < item.end)
        {
            continue;
        }
        if entropy(&bytes[start..end]) >= 4.3 {
            push_match(matches, text, start, end, "high-entropy-secret");
        }
    }
}

fn collect_matches(text: &str) -> Vec<RedactionMatch> {
    let mut matches = Vec::new();
    scan_prefixed(
        text,
        "sk-proj-",
        20,
        None,
        is_alnum_dash_underscore,
        "openai-key",
        &mut matches,
    );
    scan_prefixed(
        text,
        "sk-",
        20,
        None,
        is_alnum_dash_underscore,
        "openai-key",
        &mut matches,
    );
    scan_prefixed(
        text,
        "sk-ant-",
        20,
        None,
        is_alnum_dash_underscore,
        "anthropic-key",
        &mut matches,
    );
    for prefix in ["ghp_", "github_pat_", "gho_", "ghu_", "ghs_"] {
        scan_prefixed(
            text,
            prefix,
            20,
            None,
            is_alnum_underscore,
            "github-token",
            &mut matches,
        );
    }
    for prefix in ["AKIA", "ASIA"] {
        scan_prefixed(
            text,
            prefix,
            16,
            Some(16),
            |byte| byte.is_ascii_uppercase() || byte.is_ascii_digit(),
            "aws-access-key",
            &mut matches,
        );
    }
    scan_prefixed(
        text,
        "AIza",
        30,
        None,
        is_alnum_dash_underscore,
        "google-api-key",
        &mut matches,
    );
    for prefix in ["xoxb-", "xoxa-", "xoxp-", "xoxr-", "xoxs-"] {
        scan_prefixed(
            text,
            prefix,
            10,
            None,
            |byte| byte.is_ascii_alphanumeric() || byte == b'-',
            "slack-token",
            &mut matches,
        );
    }
    scan_jwt(text, &mut matches);
    scan_private_keys(text, &mut matches);
    scan_authorization(text, &mut matches);
    scan_connection_uris(text, &mut matches);
    let existing = matches.clone();
    scan_high_entropy(text, &existing, &mut matches);
    matches.sort_by(|left, right| {
        left.start
            .cmp(&right.start)
            .then_with(|| (right.end - right.start).cmp(&(left.end - left.start)))
    });
    let mut selected = Vec::new();
    let mut cursor = 0usize;
    for item in matches {
        if item.start >= cursor {
            cursor = item.end;
            selected.push(item);
        }
    }
    selected
}

fn redact_text(text: &str) -> (String, Vec<RedactionMatch>) {
    let matches = collect_matches(text);
    let mut rendered = String::with_capacity(text.len());
    let mut cursor = 0usize;
    for item in &matches {
        rendered.push_str(&text[cursor..item.start]);
        rendered.push_str(&format!("<redacted:{}:{}>", item.kind, item.fingerprint));
        cursor = item.end;
    }
    rendered.push_str(&text[cursor..]);
    (rendered, matches)
}

fn visit(value: &Value, records: &mut Vec<RedactionMatch>) -> Value {
    match value {
        Value::String(text) => {
            let (redacted, found) = redact_text(text);
            records.extend(found);
            Value::String(redacted)
        }
        Value::Array(items) => {
            Value::Array(items.iter().map(|item| visit(item, records)).collect())
        }
        Value::Object(items) => Value::Object(
            items
                .iter()
                .map(|(key, value)| (key.clone(), visit(value, records)))
                .collect(),
        ),
        other => other.clone(),
    }
}

fn canonical_bytes(value: &Value) -> Result<Vec<u8>, String> {
    serde_json::to_vec(value).map_err(|error| format!("REDACT_JSON_RENDER_FAILED:{error}"))
}

fn source_argument(arguments: &[String]) -> Result<&str, String> {
    arguments
        .windows(3)
        .find(|window| window[0] == "run" && window[1] == "redact")
        .map(|window| window[2].as_str())
        .ok_or_else(|| "REDACT_SOURCE_MISSING".to_owned())
}

fn load_source(arguments: &[String]) -> Result<Value, String> {
    let source = source_argument(arguments)?;
    let path = Path::new(source);
    let raw = if path.is_file() {
        fs::read_to_string(path).map_err(|error| format!("REDACT_SOURCE_READ_FAILED:{error}"))?
    } else {
        source.to_owned()
    };
    Ok(serde_json::from_str(&raw).unwrap_or(Value::String(raw)))
}

pub fn execute(arguments: &[String]) -> Result<Value, String> {
    let original = load_source(arguments)?;
    let mut records = Vec::new();
    let redacted = visit(&original, &mut records);
    let types = records
        .iter()
        .map(|record| record.kind)
        .collect::<BTreeSet<_>>();
    let fingerprints = records
        .iter()
        .map(|record| record.fingerprint.clone())
        .collect::<BTreeSet<_>>();
    Ok(json!({
        "value": redacted,
        "receipt": {
            "redacted": !records.is_empty(),
            "count": records.len(),
            "types": types,
            "fingerprints": fingerprints,
            "original_hash": sha256_hex(&canonical_bytes(&original)?),
            "redacted_hash": sha256_hex(&canonical_bytes(&redacted)?),
        }
    }))
}

#[cfg(test)]
mod tests {
    use super::{execute, redact_text};

    #[test]
    fn redacts_prefixed_keys_and_preserves_fingerprints() {
        let text = "token sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890";
        let (redacted, records) = redact_text(text);
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].kind, "openai-key");
        assert!(redacted.starts_with("token <redacted:openai-key:"));
    }

    #[test]
    fn authorization_match_wins_over_nested_key() {
        let text = "api_key: sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890";
        let (redacted, records) = redact_text(text);
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].kind, "authorization");
        assert!(redacted.starts_with("<redacted:authorization:"));
    }

    #[test]
    fn nested_json_receipt_is_deterministic() {
        let source = r#"{"token":"AKIAABCDEFGHIJKLMNOP","safe":true}"#;
        let arguments = vec!["run".to_owned(), "redact".to_owned(), source.to_owned()];
        let value = execute(&arguments).expect("redact");
        assert_eq!(value["receipt"]["count"], 1);
        assert_eq!(value["receipt"]["types"][0], "aws-access-key");
        assert!(value["value"]["token"]
            .as_str()
            .is_some_and(|text| text.starts_with("<redacted:aws-access-key:")));
    }
}
