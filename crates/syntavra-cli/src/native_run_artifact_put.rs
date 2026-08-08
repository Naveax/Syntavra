#![forbid(unsafe_code)]

use std::fs;
use std::path::Path;

use serde_json::{json, Value};

use super::native_artifact_store::NativeArtifactStore;

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "run" && action == "artifact-put")
}

#[derive(Debug, PartialEq, Eq)]
struct PutArguments {
    input: String,
    kind: String,
    media_type: String,
}

fn next_value(tail: &[String], index: &mut usize, option: &str) -> Result<String, String> {
    *index += 1;
    tail.get(*index)
        .cloned()
        .ok_or_else(|| format!("ARTIFACT_OPTION_VALUE_MISSING:{option}"))
}

fn parse_arguments(arguments: &[String]) -> Result<PutArguments, String> {
    let start = arguments
        .windows(2)
        .position(|row| row[0] == "run" && row[1] == "artifact-put")
        .map(|index| index + 2)
        .ok_or_else(|| "ARTIFACT_PUT_COMMAND_MISSING".to_owned())?;
    let tail = &arguments[start..];
    let mut input = None;
    let mut kind = "generic".to_owned();
    let mut media_type = "text/plain".to_owned();
    let mut positional_only = false;
    let mut index = 0_usize;
    while index < tail.len() {
        let value = &tail[index];
        if !positional_only && value == "--" {
            positional_only = true;
        } else if !positional_only && value == "--kind" {
            kind = next_value(tail, &mut index, "--kind")?;
        } else if !positional_only {
            if let Some(option) = value.strip_prefix("--kind=") {
                kind = option.to_owned();
            } else if value == "--media-type" {
                media_type = next_value(tail, &mut index, "--media-type")?;
            } else if let Some(option) = value.strip_prefix("--media-type=") {
                media_type = option.to_owned();
            } else if value.starts_with('-') {
                return Err(format!("ARTIFACT_OPTION_UNKNOWN:{value}"));
            } else if input.replace(value.clone()).is_some() {
                return Err(format!("ARTIFACT_ARGUMENT_UNEXPECTED:{value}"));
            }
        } else if input.replace(value.clone()).is_some() {
            return Err(format!("ARTIFACT_ARGUMENT_UNEXPECTED:{value}"));
        }
        index += 1;
    }
    Ok(PutArguments {
        input: input.ok_or_else(|| "ARTIFACT_INPUT_MISSING".to_owned())?,
        kind,
        media_type,
    })
}

fn normalize_universal_newlines(text: &str) -> String {
    if !text.contains('\r') {
        return text.to_owned();
    }
    let mut normalized = String::with_capacity(text.len());
    let mut characters = text.chars().peekable();
    while let Some(character) = characters.next() {
        if character == '\r' {
            if characters.peek() == Some(&'\n') {
                characters.next();
            }
            normalized.push('\n');
        } else {
            normalized.push(character);
        }
    }
    normalized
}

fn load_input(argument: &str) -> Result<Vec<u8>, String> {
    let path = Path::new(argument);
    if path.is_file() {
        let raw = fs::read(path).map_err(|error| format!("ARTIFACT_INPUT_READ_FAILED:{error}"))?;
        let decoded = String::from_utf8_lossy(&raw);
        return Ok(normalize_universal_newlines(&decoded).into_bytes());
    }
    Ok(argument.as_bytes().to_vec())
}

pub fn execute(arguments: &[String], state_root: &Path) -> Result<Value, String> {
    let parsed = parse_arguments(arguments)?;
    let data = load_input(&parsed.input)?;
    let store = NativeArtifactStore::open(state_root)?;
    let record = store.put(&data, &parsed.media_type, &parsed.kind, &json!({}))?;
    Ok(record.value())
}

#[cfg(test)]
mod tests {
    use super::{normalize_universal_newlines, parse_arguments, supports, PutArguments};

    #[test]
    fn routes_artifact_put_only() {
        assert!(supports(&["run".to_owned(), "artifact-put".to_owned()]));
        assert!(!supports(&["run".to_owned(), "artifact-query".to_owned()]));
    }

    #[test]
    fn repeated_options_use_last_value() {
        let parsed = parse_arguments(&[
            "run".to_owned(),
            "artifact-put".to_owned(),
            "payload".to_owned(),
            "--kind".to_owned(),
            "first".to_owned(),
            "--kind=second".to_owned(),
            "--media-type".to_owned(),
            "application/first".to_owned(),
            "--media-type=application/second".to_owned(),
        ])
        .expect("parse");
        assert_eq!(
            parsed,
            PutArguments {
                input: "payload".to_owned(),
                kind: "second".to_owned(),
                media_type: "application/second".to_owned(),
            }
        );
    }

    #[test]
    fn positional_separator_allows_dash_input() {
        let parsed = parse_arguments(&[
            "run".to_owned(),
            "artifact-put".to_owned(),
            "--".to_owned(),
            "--literal".to_owned(),
        ])
        .expect("parse");
        assert_eq!(parsed.input, "--literal");
    }

    #[test]
    fn file_text_uses_python_universal_newlines() {
        assert_eq!(normalize_universal_newlines("a\r\nb\rc\n"), "a\nb\nc\n");
    }
}
