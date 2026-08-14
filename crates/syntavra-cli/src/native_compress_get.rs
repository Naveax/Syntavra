#![forbid(unsafe_code)]

use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{json, Value};

use super::native_evidence_store::NativeEvidenceStore;

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "compress" && action == "get")
}

#[derive(Debug, PartialEq, Eq)]
struct GetArguments {
    compression_id: String,
    chunk: Option<usize>,
    output: Option<String>,
}

fn next_value(tail: &[String], index: &mut usize, option: &str) -> Result<String, String> {
    *index += 1;
    tail.get(*index)
        .cloned()
        .ok_or_else(|| format!("COMPRESSION_OPTION_VALUE_MISSING:{option}"))
}

fn parse_chunk(value: &str) -> Result<usize, String> {
    let parsed = value
        .parse::<i64>()
        .map_err(|error| format!("COMPRESSION_CHUNK_INVALID:{error}"))?;
    usize::try_from(parsed).map_err(|_| "COMPRESSION_CHUNK_NEGATIVE".to_owned())
}

fn parse_arguments(arguments: &[String]) -> Result<GetArguments, String> {
    let start = arguments
        .windows(2)
        .position(|row| row[0] == "compress" && row[1] == "get")
        .map(|index| index + 2)
        .ok_or_else(|| "COMPRESSION_GET_COMMAND_MISSING".to_owned())?;
    let tail = &arguments[start..];
    let mut compression_id = None;
    let mut chunk = None;
    let mut output = None;
    let mut index = 0_usize;
    while index < tail.len() {
        let value = &tail[index];
        if value == "--chunk" {
            if chunk.is_some() {
                return Err("COMPRESSION_CHUNK_DUPLICATE".to_owned());
            }
            chunk = Some(parse_chunk(&next_value(tail, &mut index, "--chunk")?)?);
        } else if let Some(value) = value.strip_prefix("--chunk=") {
            if chunk.is_some() {
                return Err("COMPRESSION_CHUNK_DUPLICATE".to_owned());
            }
            chunk = Some(parse_chunk(value)?);
        } else if value == "--output" {
            if output.is_some() {
                return Err("COMPRESSION_OUTPUT_DUPLICATE".to_owned());
            }
            output = Some(next_value(tail, &mut index, "--output")?);
        } else if let Some(value) = value.strip_prefix("--output=") {
            if output.is_some() {
                return Err("COMPRESSION_OUTPUT_DUPLICATE".to_owned());
            }
            output = Some(value.to_owned());
        } else if value.starts_with('-') {
            return Err(format!("COMPRESSION_OPTION_UNKNOWN:{value}"));
        } else if compression_id.replace(value.clone()).is_some() {
            return Err(format!("COMPRESSION_ARGUMENT_UNEXPECTED:{value}"));
        }
        index += 1;
    }
    let compression_id = compression_id.ok_or_else(|| "COMPRESSION_ID_MISSING".to_owned())?;
    if compression_id.is_empty() {
        return Err("COMPRESSION_ID_INVALID".to_owned());
    }
    Ok(GetArguments {
        compression_id,
        chunk,
        output,
    })
}

fn selected_handle(description: &Value, chunk: Option<usize>) -> Result<String, String> {
    if let Some(index) = chunk {
        let chunks = description["chunks"]
            .as_array()
            .ok_or_else(|| "COMPRESSION_CHUNKS_INVALID".to_owned())?;
        let row = chunks
            .get(index)
            .ok_or_else(|| format!("COMPRESSION_CHUNK_OUT_OF_RANGE:{index}"))?;
        return row["chunk_handle"]
            .as_str()
            .map(ToOwned::to_owned)
            .ok_or_else(|| "COMPRESSION_CHUNK_HANDLE_INVALID".to_owned());
    }
    description["exact_handle"]
        .as_str()
        .map(ToOwned::to_owned)
        .ok_or_else(|| "COMPRESSION_EXACT_HANDLE_INVALID".to_owned())
}

fn write_output(path: &Path, data: &[u8]) -> Result<(), String> {
    fs::write(path, data).map_err(|error| format!("COMPRESSION_OUTPUT_WRITE_FAILED:{error}"))
}

pub fn execute(
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    let parsed = parse_arguments(arguments)?;
    let database_path = super::native_compress_describe::initialize_database(state_root)?;
    let description =
        super::native_compress_describe::describe(&database_path, &parsed.compression_id)?;
    let handle = selected_handle(&description, parsed.chunk)?;
    let project_id =
        super::state_snapshot_contract::project_id_for_root(&project_root.to_string_lossy())?;
    let evidence = NativeEvidenceStore::open(state_root, &project_id)?;
    let data = evidence.get(&handle)?;
    if let Some(output) = parsed.output {
        write_output(&PathBuf::from(&output), &data)?;
        return Ok(json!({
            "compression_id": parsed.compression_id,
            "bytes": data.len(),
            "output": output,
        }));
    }
    Ok(json!({
        "compression_id": parsed.compression_id,
        "bytes": data.len(),
        "text": String::from_utf8_lossy(&data),
    }))
}

#[cfg(test)]
mod tests {
    use super::{parse_arguments, selected_handle, supports, GetArguments};
    use serde_json::json;

    #[test]
    fn routes_compress_get_only() {
        assert!(supports(&["compress".to_owned(), "get".to_owned()]));
        assert!(!supports(&["compress".to_owned(), "put".to_owned()]));
    }

    #[test]
    fn parses_chunk_and_output() {
        let parsed = parse_arguments(&[
            "compress".to_owned(),
            "get".to_owned(),
            "ccr-example".to_owned(),
            "--chunk".to_owned(),
            "2".to_owned(),
            "--output=result.bin".to_owned(),
        ])
        .expect("parse");
        assert_eq!(
            parsed,
            GetArguments {
                compression_id: "ccr-example".to_owned(),
                chunk: Some(2),
                output: Some("result.bin".to_owned()),
            }
        );
    }

    #[test]
    fn rejects_negative_chunk() {
        assert!(parse_arguments(&[
            "compress".to_owned(),
            "get".to_owned(),
            "ccr-example".to_owned(),
            "--chunk".to_owned(),
            "-1".to_owned(),
        ])
        .is_err());
    }

    #[test]
    fn selects_exact_and_chunk_handles() {
        let description = json!({
            "exact_handle": "sc://sha256/exact",
            "chunks": [
                {"chunk_handle": "sc://sha256/zero"},
                {"chunk_handle": "sc://sha256/one"},
            ],
        });
        assert_eq!(
            selected_handle(&description, None).expect("exact"),
            "sc://sha256/exact"
        );
        assert_eq!(
            selected_handle(&description, Some(1)).expect("chunk"),
            "sc://sha256/one"
        );
        assert!(selected_handle(&description, Some(2)).is_err());
    }
}
