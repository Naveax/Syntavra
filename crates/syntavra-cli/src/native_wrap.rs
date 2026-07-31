#![forbid(unsafe_code)]

use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{json, Value};

const VERSION: &str = "0.0.1";
const CHANNEL: &str = "pre-release";
const HOSTS: [&str; 18] = [
    "codex",
    "claude-code",
    "cursor",
    "vscode",
    "openai-mcp",
    "claude-mcp",
    "github-copilot",
    "jetbrains-ai",
    "gemini-cli",
    "gemini-mcp",
    "antigravity-ide",
    "opencode",
    "kilo-code",
    "aider",
    "continue-dev",
    "cline",
    "roo-code",
    "generic-mcp",
];

fn argument_after<'a>(arguments: &'a [String], name: &str) -> Option<&'a str> {
    arguments
        .iter()
        .position(|argument| argument == name)
        .and_then(|index| arguments.get(index + 1))
        .map(String::as_str)
}

fn host_argument(arguments: &[String]) -> Result<&str, String> {
    arguments
        .windows(2)
        .find(|window| window[0] == "wrap")
        .map(|window| window[1].as_str())
        .ok_or_else(|| "WRAP_HOST_MISSING".to_owned())
}

fn wrapper_text(host: &str) -> Result<String, String> {
    if !HOSTS.contains(&host) {
        return Err(format!("WRAP_HOST_UNKNOWN:{host}"));
    }
    if cfg!(windows) {
        Ok(format!(
            "@echo off\r\nset \"SYNTAVRA_HOST={host}\"\r\nset \"SYNTAVRA_CHANNEL={CHANNEL}\"\r\n%*\r\n"
        ))
    } else {
        Ok(format!(
            "#!/usr/bin/env sh\nexport SYNTAVRA_HOST=\"{host}\"\nexport SYNTAVRA_CHANNEL=\"{CHANNEL}\"\nexec \"$@\"\n"
        ))
    }
}

fn default_output(state_root: &Path, host: &str) -> PathBuf {
    let suffix = if cfg!(windows) { ".cmd" } else { "" };
    state_root.join("wrappers").join(format!("{host}{suffix}"))
}

#[cfg(unix)]
fn make_executable(path: &Path) -> Result<(), String> {
    use std::os::unix::fs::PermissionsExt;

    let metadata = fs::metadata(path).map_err(|error| format!("WRAP_METADATA_FAILED:{error}"))?;
    let mut permissions = metadata.permissions();
    permissions.set_mode(permissions.mode() | 0o100);
    fs::set_permissions(path, permissions)
        .map_err(|error| format!("WRAP_PERMISSION_FAILED:{error}"))
}

#[cfg(not(unix))]
fn make_executable(_path: &Path) -> Result<(), String> {
    Ok(())
}

pub fn execute(arguments: &[String], state_root: &Path) -> Result<Value, String> {
    let host = host_argument(arguments)?;
    let output = argument_after(arguments, "--output")
        .map_or_else(|| default_output(state_root, host), PathBuf::from);
    let text = wrapper_text(host)?;
    if let Some(parent) = output
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        fs::create_dir_all(parent).map_err(|error| format!("WRAP_DIRECTORY_FAILED:{error}"))?;
    }
    fs::write(&output, text.as_bytes()).map_err(|error| format!("WRAP_WRITE_FAILED:{error}"))?;
    make_executable(&output)?;
    Ok(json!({
        "ok": true,
        "host": host,
        "path": output,
        "version": VERSION,
    }))
}

#[cfg(test)]
mod tests {
    use super::{execute, wrapper_text};
    use std::fs;

    #[test]
    fn rejects_unknown_hosts() {
        assert_eq!(
            wrapper_text("unknown").expect_err("unknown host"),
            "WRAP_HOST_UNKNOWN:unknown"
        );
    }

    #[test]
    fn writes_default_wrapper() {
        let root = std::env::temp_dir().join(format!("syntavra-wrap-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        let arguments = vec!["wrap".to_owned(), "codex".to_owned()];
        let value = execute(&arguments, &root).expect("wrapper");
        assert_eq!(value["ok"], true);
        assert_eq!(value["host"], "codex");
        assert!(value["path"]
            .as_str()
            .is_some_and(|path| path.contains("codex")));
        let _ = fs::remove_dir_all(&root);
    }
}
