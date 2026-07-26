use std::fmt::Write as _;
use std::fs;
use std::io::ErrorKind;
use std::path::{Path, PathBuf};

use syntavra_core::sha256_hex;

const SCHEMA_VERSION: u32 = 1;
const CONTRACT_VERSION: u32 = 1;
const INSPECTION_ID: &str = "syntavra-state-inspection-v1";
const MAX_FILE_BYTES: u64 = 1024 * 1024;

#[derive(Clone, Copy)]
struct PathSpec {
    id: &'static str,
    path: &'static str,
    expected_kind: &'static str,
}

const KNOWN_PATHS: &[PathSpec] = &[
    PathSpec {
        id: "state-root",
        path: ".syntavra",
        expected_kind: "directory",
    },
    PathSpec {
        id: "project-config",
        path: ".syntavra/config.toml",
        expected_kind: "file",
    },
    PathSpec {
        id: "engine-selection",
        path: ".syntavra/engine.json",
        expected_kind: "file",
    },
    PathSpec {
        id: "pre-release-state",
        path: ".syntavra/pre-release",
        expected_kind: "directory",
    },
    PathSpec {
        id: "runtime-v3-state",
        path: ".syntavra/runtime-v3",
        expected_kind: "directory",
    },
];

fn json_string(value: &str) -> String {
    let mut output = String::with_capacity(value.len() + 2);
    output.push('"');
    for character in value.chars() {
        match character {
            '"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            value if value.is_control() => {
                write!(&mut output, "\\u{:04x}", u32::from(value))
                    .expect("writing to a String cannot fail");
            }
            value => output.push(value),
        }
    }
    output.push('"');
    output
}

fn valid_lower_hash(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn normalize_absolute_path(path: &Path) -> Result<String, String> {
    let value = path
        .to_str()
        .ok_or_else(|| "STATE_PROJECT_ROOT_UTF8_INVALID".to_owned())?;
    let mut normalized = value.replace('\\', "/");
    if let Some(rest) = normalized.strip_prefix("//?/UNC/") {
        normalized = format!("//{rest}");
    } else if let Some(rest) = normalized.strip_prefix("//?/") {
        normalized = rest.to_owned();
    }
    let bytes = normalized.as_bytes();
    if bytes.len() >= 3 && bytes[1] == b':' && bytes[2] == b'/' && bytes[0].is_ascii_uppercase() {
        normalized.replace_range(0..1, &char::from(bytes[0]).to_ascii_lowercase().to_string());
    }
    Ok(normalized)
}

fn canonical_root(project_root: &str) -> Result<(PathBuf, String), String> {
    let input = Path::new(project_root);
    let metadata = fs::symlink_metadata(input).map_err(|error| match error.kind() {
        ErrorKind::NotFound => "STATE_PROJECT_ROOT_MISSING".to_owned(),
        _ => "STATE_PROJECT_ROOT_METADATA_FAILED".to_owned(),
    })?;
    if metadata.file_type().is_symlink() {
        return Err("STATE_PROJECT_ROOT_SYMLINK".to_owned());
    }
    if !metadata.is_dir() {
        return Err("STATE_PROJECT_ROOT_NOT_DIRECTORY".to_owned());
    }
    let canonical =
        fs::canonicalize(input).map_err(|_| "STATE_PROJECT_ROOT_RESOLVE_FAILED".to_owned())?;
    let normalized = normalize_absolute_path(&canonical)?;
    Ok((canonical, normalized))
}

pub fn project_id_for_root(project_root: &str) -> Result<String, String> {
    let (_, normalized) = canonical_root(project_root)?;
    Ok(sha256_hex(normalized.as_bytes()))
}

fn missing_row(spec: PathSpec) -> String {
    format!(
        concat!(
            "{{\"id\":{},",
            "\"path\":{},",
            "\"expected_kind\":{},",
            "\"observed_kind\":\"missing\",",
            "\"exists\":false,",
            "\"size_bytes\":null,",
            "\"sha256\":null}}"
        ),
        json_string(spec.id),
        json_string(spec.path),
        json_string(spec.expected_kind),
    )
}

fn inspect_path(root: &Path, spec: PathSpec) -> Result<String, String> {
    let mut current = root.to_path_buf();
    let mut metadata = None;
    for part in spec.path.split('/') {
        current.push(part);
        match fs::symlink_metadata(&current) {
            Ok(value) => {
                if value.file_type().is_symlink() {
                    return Err("STATE_PATH_SYMLINK".to_owned());
                }
                metadata = Some(value);
            }
            Err(error) if error.kind() == ErrorKind::NotFound => return Ok(missing_row(spec)),
            Err(_) => return Err("STATE_PATH_METADATA_FAILED".to_owned()),
        }
    }

    let metadata = metadata.expect("known paths are never empty");
    let observed_kind = if metadata.is_dir() {
        "directory"
    } else if metadata.is_file() {
        "file"
    } else {
        return Err("STATE_PATH_TYPE_UNSUPPORTED".to_owned());
    };
    if observed_kind != spec.expected_kind {
        return Err("STATE_PATH_KIND_MISMATCH".to_owned());
    }

    let (size_json, hash_json) = if observed_kind == "file" {
        if metadata.len() > MAX_FILE_BYTES {
            return Err("STATE_FILE_SIZE_LIMIT".to_owned());
        }
        let before_modified = metadata.modified().ok();
        let payload = fs::read(&current).map_err(|_| "STATE_FILE_READ_FAILED".to_owned())?;
        let after =
            fs::symlink_metadata(&current).map_err(|_| "STATE_FILE_READ_FAILED".to_owned())?;
        if after.file_type().is_symlink() {
            return Err("STATE_PATH_SYMLINK".to_owned());
        }
        if !after.is_file()
            || after.len() != metadata.len()
            || after.modified().ok() != before_modified
            || u64::try_from(payload.len()).ok() != Some(metadata.len())
        {
            return Err("STATE_PATH_CHANGED_DURING_READ".to_owned());
        }
        (
            payload.len().to_string(),
            json_string(&sha256_hex(&payload)),
        )
    } else {
        ("null".to_owned(), "null".to_owned())
    };

    Ok(format!(
        concat!(
            "{{\"id\":{},",
            "\"path\":{},",
            "\"expected_kind\":{},",
            "\"observed_kind\":{},",
            "\"exists\":true,",
            "\"size_bytes\":{},",
            "\"sha256\":{}}}"
        ),
        json_string(spec.id),
        json_string(spec.path),
        json_string(spec.expected_kind),
        json_string(observed_kind),
        size_json,
        hash_json,
    ))
}

pub fn inspect_state_root_json(
    project_root: &str,
    expected_project_id: &str,
) -> Result<String, String> {
    if !valid_lower_hash(expected_project_id) {
        return Err("STATE_EXPECTED_PROJECT_INVALID".to_owned());
    }
    let (canonical, normalized) = canonical_root(project_root)?;
    let actual_project_id = sha256_hex(normalized.as_bytes());
    if actual_project_id != expected_project_id {
        return Err("STATE_PROJECT_MISMATCH".to_owned());
    }

    let paths = KNOWN_PATHS
        .iter()
        .copied()
        .map(|spec| inspect_path(&canonical, spec))
        .collect::<Result<Vec<_>, _>>()?
        .join(",");

    Ok(format!(
        concat!(
            "{{\"ok\":true,",
            "\"schema_version\":{},",
            "\"contract_version\":{},",
            "\"inspection_id\":{},",
            "\"project_id\":{},",
            "\"project_binding\":{{\"expected\":{},\"actual\":{},\"matched\":true}},",
            "\"paths\":[{}],",
            "\"mutation\":{{\"filesystem\":false,\"database_opened\":false}},",
            "\"claim\":\"RUST_STATE_ROOT_READ_PARITY_PROVEN_R8_FIXTURES\"}}"
        ),
        SCHEMA_VERSION,
        CONTRACT_VERSION,
        json_string(INSPECTION_ID),
        json_string(&actual_project_id),
        json_string(expected_project_id),
        json_string(&actual_project_id),
        paths,
    ))
}

#[cfg(test)]
mod tests {
    use super::{inspect_state_root_json, project_id_for_root};

    #[test]
    fn rejects_invalid_expected_project_id() {
        assert_eq!(
            inspect_state_root_json(".", "bad"),
            Err("STATE_EXPECTED_PROJECT_INVALID".to_owned())
        );
    }

    #[test]
    fn current_directory_can_be_inspected_without_mutation() {
        let project_id = project_id_for_root(".").expect("project id");
        let value = inspect_state_root_json(".", &project_id).expect("inspection");
        assert!(value.contains("\"inspection_id\":\"syntavra-state-inspection-v1\""));
        assert!(value.contains("\"filesystem\":false"));
        assert!(value.contains("\"database_opened\":false"));
    }
}
