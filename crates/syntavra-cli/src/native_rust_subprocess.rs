#![forbid(unsafe_code)]

use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use serde_json::Value;

const MAX_RESPONSE_BYTES: usize = 1024 * 1024;

fn executable_name() -> String {
    format!("syntavra-rs{}", env::consts::EXE_SUFFIX)
}

fn validate_binary(path: &Path) -> Result<PathBuf, String> {
    let metadata =
        fs::symlink_metadata(path).map_err(|_| "RUST_SUBENGINE_BINARY_NOT_FOUND".to_owned())?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err("RUST_SUBENGINE_BINARY_INVALID".to_owned());
    }
    Ok(path.to_path_buf())
}

fn discover_binary() -> Result<PathBuf, String> {
    if let Some(value) = env::var_os("SYNTAVRA_NATIVE_RUNTIME_BIN") {
        return validate_binary(Path::new(&value));
    }
    let current = env::current_exe()
        .map_err(|_| "RUST_SUBENGINE_CURRENT_EXECUTABLE_UNAVAILABLE".to_owned())?;
    let directory = current
        .parent()
        .ok_or_else(|| "RUST_SUBENGINE_DIRECTORY_UNAVAILABLE".to_owned())?;
    validate_binary(&directory.join(executable_name()))
}

pub fn execute_json(arguments: &[String]) -> Result<Value, String> {
    let binary = discover_binary()?;
    let output = Command::new(&binary)
        .args(arguments)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .map_err(|_| "RUST_SUBENGINE_EXECUTION_FAILED".to_owned())?;
    if !output.status.success() {
        let code = output.status.code().unwrap_or(1);
        return Err(format!("RUST_SUBENGINE_EXITED:{code}"));
    }
    if output.stdout.len() > MAX_RESPONSE_BYTES {
        return Err("RUST_SUBENGINE_RESPONSE_TOO_LARGE".to_owned());
    }
    serde_json::from_slice::<Value>(&output.stdout)
        .map_err(|_| "RUST_SUBENGINE_RESPONSE_INVALID".to_owned())
}

#[cfg(test)]
mod tests {
    use super::executable_name;

    #[test]
    fn runtime_binary_name_is_platform_specific() {
        assert!(executable_name().starts_with("syntavra-rs"));
    }
}
