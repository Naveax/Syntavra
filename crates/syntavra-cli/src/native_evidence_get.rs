#![forbid(unsafe_code)]

use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{json, Value};

use super::native_evidence_store::NativeEvidenceStore;

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "evidence" && action == "get")
}

#[derive(Debug, PartialEq, Eq)]
struct GetArguments {
    handle: String,
    max_bytes: Option<i128>,
    output: Option<String>,
}

fn next_value(tail: &[String], index: &mut usize, option: &str) -> Result<String, String> {
    *index += 1;
    tail.get(*index)
        .cloned()
        .ok_or_else(|| format!("EVIDENCE_OPTION_VALUE_MISSING:{option}"))
}

fn parse_max_bytes(value: &str) -> Result<i128, String> {
    value
        .parse::<i128>()
        .map_err(|error| format!("EVIDENCE_MAX_BYTES_INVALID:{error}"))
}

fn parse_arguments(arguments: &[String]) -> Result<GetArguments, String> {
    let start = arguments
        .windows(2)
        .position(|row| row[0] == "evidence" && row[1] == "get")
        .map(|index| index + 2)
        .ok_or_else(|| "EVIDENCE_GET_COMMAND_MISSING".to_owned())?;
    let tail = &arguments[start..];
    let mut handle = None;
    let mut max_bytes = None;
    let mut output = None;
    let mut index = 0_usize;
    while index < tail.len() {
        let value = &tail[index];
        if value == "--max-bytes" {
            max_bytes = Some(parse_max_bytes(&next_value(
                tail,
                &mut index,
                "--max-bytes",
            )?)?);
        } else if let Some(value) = value.strip_prefix("--max-bytes=") {
            max_bytes = Some(parse_max_bytes(value)?);
        } else if value == "--output" {
            output = Some(next_value(tail, &mut index, "--output")?);
        } else if let Some(value) = value.strip_prefix("--output=") {
            output = Some(value.to_owned());
        } else if value.starts_with('-') {
            return Err(format!("EVIDENCE_OPTION_UNKNOWN:{value}"));
        } else if handle.replace(value.clone()).is_some() {
            return Err(format!("EVIDENCE_ARGUMENT_UNEXPECTED:{value}"));
        }
        index += 1;
    }
    Ok(GetArguments {
        handle: handle.ok_or_else(|| "EVIDENCE_HANDLE_MISSING".to_owned())?,
        max_bytes,
        output,
    })
}

fn write_output(path: &Path, data: &[u8]) -> Result<(), String> {
    fs::write(path, data).map_err(|error| format!("EVIDENCE_OUTPUT_WRITE_FAILED:{error}"))
}

pub fn execute(
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    let parsed = parse_arguments(arguments)?;
    let project_id =
        super::state_snapshot_contract::project_id_for_root(&project_root.to_string_lossy())?;
    let evidence = NativeEvidenceStore::open(state_root, &project_id)?;
    let data = evidence.get_with_max_bytes(&parsed.handle, parsed.max_bytes)?;
    if let Some(output) = parsed.output.filter(|value| !value.is_empty()) {
        write_output(&PathBuf::from(&output), &data)?;
        return Ok(json!({
            "handle": parsed.handle,
            "bytes": data.len(),
            "output": output,
        }));
    }
    Ok(json!({
        "handle": parsed.handle,
        "bytes": data.len(),
        "text": String::from_utf8_lossy(&data),
    }))
}

#[cfg(test)]
mod tests {
    use super::{parse_arguments, supports, GetArguments};

    #[test]
    fn routes_evidence_get_only() {
        assert!(supports(&["evidence".to_owned(), "get".to_owned()]));
        assert!(!supports(&["evidence".to_owned(), "describe".to_owned()]));
    }

    #[test]
    fn repeated_options_use_python_last_value_semantics() {
        let parsed = parse_arguments(&[
            "evidence".to_owned(),
            "get".to_owned(),
            "--max-bytes".to_owned(),
            "1".to_owned(),
            "sc://sha256/abc".to_owned(),
            "--max-bytes=9".to_owned(),
            "--output=first.bin".to_owned(),
            "--output".to_owned(),
            "second.bin".to_owned(),
        ])
        .expect("parse");
        assert_eq!(
            parsed,
            GetArguments {
                handle: "sc://sha256/abc".to_owned(),
                max_bytes: Some(9),
                output: Some("second.bin".to_owned()),
            }
        );
    }

    #[test]
    fn accepts_negative_limit_for_runtime_parity() {
        let parsed = parse_arguments(&[
            "evidence".to_owned(),
            "get".to_owned(),
            "sc://sha256/abc".to_owned(),
            "--max-bytes=-1".to_owned(),
        ])
        .expect("parse");
        assert_eq!(parsed.max_bytes, Some(-1));
    }
}
