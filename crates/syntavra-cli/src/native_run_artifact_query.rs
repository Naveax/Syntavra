#![forbid(unsafe_code)]

use std::path::Path;

use regex::{Captures, Regex};
use serde_json::{json, Value};

use super::native_artifact_store::NativeArtifactStore;

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "run" && action == "artifact-query")
}

#[derive(Debug, PartialEq, Eq)]
struct QueryArguments {
    artifact_id: String,
    mode: String,
    expression: String,
    limit: i64,
}

fn next_value(tail: &[String], index: &mut usize, option: &str) -> Result<String, String> {
    *index += 1;
    tail.get(*index)
        .cloned()
        .ok_or_else(|| format!("ARTIFACT_QUERY_OPTION_VALUE_MISSING:{option}"))
}

fn parse_limit(value: &str) -> Result<i64, String> {
    value
        .parse::<i64>()
        .map_err(|_| format!("ARTIFACT_QUERY_LIMIT_INVALID:{value}"))
}

fn validate_mode(value: String) -> Result<String, String> {
    if matches!(
        value.as_str(),
        "head" | "tail" | "errors" | "failures" | "regex" | "json"
    ) {
        Ok(value)
    } else {
        Err(format!("ARTIFACT_QUERY_MODE_INVALID:{value}"))
    }
}

fn parse_arguments(arguments: &[String]) -> Result<QueryArguments, String> {
    let start = arguments
        .windows(2)
        .position(|row| row[0] == "run" && row[1] == "artifact-query")
        .map(|index| index + 2)
        .ok_or_else(|| "ARTIFACT_QUERY_COMMAND_MISSING".to_owned())?;
    let tail = &arguments[start..];
    let mut artifact_id = None;
    let mut mode = "head".to_owned();
    let mut expression = String::new();
    let mut limit = 80_i64;
    let mut positional_only = false;
    let mut index = 0_usize;
    while index < tail.len() {
        let value = &tail[index];
        if !positional_only && value == "--" {
            positional_only = true;
        } else if !positional_only && value == "--mode" {
            mode = validate_mode(next_value(tail, &mut index, "--mode")?)?;
        } else if !positional_only {
            if let Some(option) = value.strip_prefix("--mode=") {
                mode = validate_mode(option.to_owned())?;
            } else if value == "--expression" {
                expression = next_value(tail, &mut index, "--expression")?;
            } else if let Some(option) = value.strip_prefix("--expression=") {
                expression = option.to_owned();
            } else if value == "--limit" {
                limit = parse_limit(&next_value(tail, &mut index, "--limit")?)?;
            } else if let Some(option) = value.strip_prefix("--limit=") {
                limit = parse_limit(option)?;
            } else if value.starts_with('-') {
                return Err(format!("ARTIFACT_QUERY_OPTION_UNKNOWN:{value}"));
            } else if artifact_id.replace(value.clone()).is_some() {
                return Err(format!("ARTIFACT_QUERY_ARGUMENT_UNEXPECTED:{value}"));
            }
        } else if artifact_id.replace(value.clone()).is_some() {
            return Err(format!("ARTIFACT_QUERY_ARGUMENT_UNEXPECTED:{value}"));
        }
        index += 1;
    }
    Ok(QueryArguments {
        artifact_id: artifact_id.ok_or_else(|| "ARTIFACT_QUERY_ID_MISSING".to_owned())?,
        mode,
        expression,
        limit,
    })
}

fn is_python_line_break(character: char) -> bool {
    matches!(
        character,
        '\n' | '\r' | '\u{000b}' | '\u{000c}' | '\u{001c}' | '\u{001d}' | '\u{001e}' | '\u{0085}' | '\u{2028}' | '\u{2029}'
    )
}

fn splitlines_python(text: &str) -> Vec<String> {
    let mut output = Vec::new();
    let mut current = String::new();
    let mut characters = text.chars().peekable();
    while let Some(character) = characters.next() {
        if is_python_line_break(character) {
            output.push(std::mem::take(&mut current));
            if character == '\r' && characters.peek() == Some(&'\n') {
                characters.next();
            }
        } else {
            current.push(character);
        }
    }
    if !current.is_empty() {
        output.push(current);
    }
    output
}

fn redact(text: &str) -> Result<String, String> {
    let pattern = Regex::new(
        r"(?i)(?:api[_-]?key|authorization|access[_-]?token|password|secret|bearer)\s*[:=]\s*([^\s,;]+)",
    )
    .map_err(|error| format!("ARTIFACT_QUERY_REDACT_PATTERN_FAILED:{error}"))?;
    Ok(pattern
        .replace_all(text, |captures: &Captures<'_>| {
            let full = captures.get(0).map_or("", |value| value.as_str());
            let secret = captures.get(1).map_or("", |value| value.as_str());
            let prefix_len = full.len().saturating_sub(secret.len());
            format!("{}<redacted>", &full[..prefix_len])
        })
        .into_owned())
}

fn estimate_tokens(text: &str) -> usize {
    let bytes = text.len();
    std::cmp::max(1, (bytes.saturating_mul(2).saturating_add(6)) / 7)
}

fn select_json(text: &str, expression: &str, limit: usize) -> Result<Vec<String>, String> {
    let mut current = serde_json::from_str::<Value>(text)
        .map_err(|error| format!("ARTIFACT_QUERY_JSON_PARSE_FAILED:{error}"))?;
    for part in expression.split('.').filter(|item| !item.is_empty()) {
        current = match current {
            Value::Object(values) => values
                .get(part)
                .cloned()
                .ok_or_else(|| format!("ARTIFACT_QUERY_JSON_KEY_MISSING:{expression}"))?,
            Value::Array(values) => {
                let raw = part
                    .parse::<i64>()
                    .map_err(|_| format!("ARTIFACT_QUERY_JSON_INDEX_INVALID:{expression}"))?;
                let length = i64::try_from(values.len())
                    .map_err(|_| "ARTIFACT_QUERY_JSON_ARRAY_TOO_LARGE".to_owned())?;
                let normalized = if raw < 0 { length + raw } else { raw };
                if normalized < 0 || normalized >= length {
                    return Err(format!("ARTIFACT_QUERY_JSON_INDEX_MISSING:{expression}"));
                }
                values[usize::try_from(normalized)
                    .map_err(|_| "ARTIFACT_QUERY_JSON_INDEX_RANGE".to_owned())?]
                    .clone()
            }
            _ => return Err(format!("ARTIFACT_QUERY_JSON_PATH_INVALID:{expression}")),
        };
    }
    let rendered = serde_json::to_string_pretty(&current)
        .map_err(|error| format!("ARTIFACT_QUERY_JSON_RENDER_FAILED:{error}"))?;
    Ok(splitlines_python(&rendered).into_iter().take(limit).collect())
}

fn select_lines(
    text: &str,
    mode: &str,
    expression: &str,
    limit: usize,
) -> Result<Vec<String>, String> {
    let lines = splitlines_python(text);
    match mode {
        "head" => Ok(lines.into_iter().take(limit).collect()),
        "tail" => {
            let start = lines.len().saturating_sub(limit);
            Ok(lines.into_iter().skip(start).collect())
        }
        "errors" => {
            let error_pattern = Regex::new(
                r"(?i)\b(error|failed|failure|panic|assertion|traceback|exception|fatal|denied|timeout)\b",
            )
            .map_err(|error| format!("ARTIFACT_QUERY_ERROR_PATTERN_FAILED:{error}"))?;
            let location_pattern = Regex::new(
                r"(?:[A-Za-z]:)?[^\s:]+\.(?:py|rs|ts|tsx|js|jsx|go|java|cs|cpp|c|h|rb|php):\d+(?::\d+)?",
            )
            .map_err(|error| format!("ARTIFACT_QUERY_LOCATION_PATTERN_FAILED:{error}"))?;
            Ok(lines
                .into_iter()
                .filter(|line| error_pattern.is_match(line) || location_pattern.is_match(line))
                .take(limit)
                .collect())
        }
        "regex" => {
            let pattern = Regex::new(expression)
                .map_err(|error| format!("ARTIFACT_QUERY_REGEX_INVALID:{error}"))?;
            Ok(lines
                .into_iter()
                .filter(|line| pattern.is_match(line))
                .take(limit)
                .collect())
        }
        "failures" => {
            let pattern = Regex::new(r"(?i)(fail|error|traceback|panic|assert)")
                .map_err(|error| format!("ARTIFACT_QUERY_FAILURE_PATTERN_FAILED:{error}"))?;
            Ok(lines
                .into_iter()
                .filter(|line| pattern.is_match(line))
                .take(limit)
                .collect())
        }
        "json" => select_json(text, expression, limit),
        _ => Err(format!("ARTIFACT_QUERY_MODE_INVALID:{mode}")),
    }
}

pub fn execute(arguments: &[String], state_root: &Path) -> Result<Value, String> {
    let parsed = parse_arguments(arguments)?;
    let store = NativeArtifactStore::open(state_root)?;
    let record = store.record(&parsed.artifact_id)?;
    let data = store.read(&parsed.artifact_id)?;
    let text = String::from_utf8_lossy(&data);
    let limit = usize::try_from(parsed.limit.clamp(1, 1000))
        .map_err(|_| "ARTIFACT_QUERY_LIMIT_RANGE".to_owned())?;
    let selected = select_lines(&text, &parsed.mode, &parsed.expression, limit)?;
    let rendered = redact(&selected.join("\n"))?;
    Ok(json!({
        "ok": true,
        "artifact": record.value(),
        "mode": parsed.mode,
        "expression": parsed.expression,
        "matched_lines": selected.len(),
        "view": rendered,
        "view_tokens": estimate_tokens(&rendered),
    }))
}

#[cfg(test)]
mod tests {
    use super::{estimate_tokens, parse_arguments, redact, splitlines_python, supports, QueryArguments};

    #[test]
    fn routes_artifact_query_only() {
        assert!(supports(&["run".to_owned(), "artifact-query".to_owned()]));
        assert!(!supports(&["run".to_owned(), "artifact-put".to_owned()]));
    }

    #[test]
    fn repeated_options_use_last_value() {
        let parsed = parse_arguments(&[
            "run".to_owned(),
            "artifact-query".to_owned(),
            "sha256:abc".to_owned(),
            "--mode".to_owned(),
            "head".to_owned(),
            "--mode=tail".to_owned(),
            "--expression".to_owned(),
            "first".to_owned(),
            "--expression=last".to_owned(),
            "--limit".to_owned(),
            "1".to_owned(),
            "--limit=7".to_owned(),
        ])
        .expect("parse");
        assert_eq!(
            parsed,
            QueryArguments {
                artifact_id: "sha256:abc".to_owned(),
                mode: "tail".to_owned(),
                expression: "last".to_owned(),
                limit: 7,
            }
        );
    }

    #[test]
    fn python_splitlines_boundaries_match_expected() {
        assert_eq!(
            splitlines_python("a\r\nb\rc\nd\u{000b}e\u{2028}f\n"),
            vec!["a", "b", "c", "d", "e", "f"]
        );
        assert_eq!(splitlines_python("\n"), vec![""]);
        assert!(splitlines_python("").is_empty());
    }

    #[test]
    fn redaction_preserves_prefix_and_hides_value() {
        assert_eq!(
            redact("authorization = BearerToken, password: hunter2").expect("redact"),
            "authorization = <redacted>, password: <redacted>"
        );
    }

    #[test]
    fn generic_token_estimate_matches_ratio() {
        assert_eq!(estimate_tokens(""), 1);
        assert_eq!(estimate_tokens("1234567"), 2);
        assert_eq!(estimate_tokens("12345678"), 3);
    }
}
