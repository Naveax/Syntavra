#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "syntavra_runtime/platform_cli.py"
DIRECT = ROOT / "syntavra_runtime/sandbox.py"
RUST = ROOT / "crates/syntavra-cli/src/native_remaining71_sandbox.rs"


def one(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one old match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    one(
        PLATFORM,
        '    sandbox_run.add_argument("command", help="JSON argv or path")\n',
        '    sandbox_run.add_argument("sandbox_command", help="JSON argv or path")\n',
        "platform sandbox positional name",
    )
    one(
        PLATFORM,
        '        command = _argv(args.command, "sandbox command")\n',
        '        command = _argv(args.sandbox_command, "sandbox command")\n',
        "platform sandbox argv field",
    )
    one(
        DIRECT,
        '        allowed["SYNTAVRA_SANDBOX"] = "1"\n        # Numerical libraries may otherwise spawn one worker per host CPU before\n',
        '        allowed["SYNTAVRA_SANDBOX"] = "1"\n        allowed["SYNTAVRA_WORKSPACE"] = str(self.project)\n        # Numerical libraries may otherwise spawn one worker per host CPU before\n',
        "direct sandbox workspace environment",
    )

    wrapper_anchor = '''fn platform_status(project: &Path) -> Result<Value, String> {\n'''
    wrapper_code = r'''fn sandbox_profile_escape(value: &str) -> String {
    value.replace('\\', "\\\\").replace('"', "\\\"")
}

fn platform_wrapped_command(
    backend: &Value,
    command: &[String],
    project: &Path,
    writable: &[PathBuf],
    network_hosts: &[String],
) -> Result<Vec<String>, String> {
    let name = backend["name"].as_str().unwrap_or("portable-process-boundary");
    let mut wrapped = Vec::<String>::new();
    match name {
        "bubblewrap" => {
            wrapped.extend([
                "bwrap".to_owned(),
                "--die-with-parent".to_owned(),
                "--new-session".to_owned(),
                "--unshare-user".to_owned(),
                "--unshare-pid".to_owned(),
                "--unshare-uts".to_owned(),
                "--unshare-ipc".to_owned(),
                "--ro-bind".to_owned(),
                "/".to_owned(),
                "/".to_owned(),
                "--proc".to_owned(),
                "/proc".to_owned(),
                "--dev".to_owned(),
                "/dev".to_owned(),
                "--chdir".to_owned(),
                project.to_string_lossy().into_owned(),
            ]);
            for path in writable {
                let value = path.to_string_lossy().into_owned();
                wrapped.extend(["--bind".to_owned(), value.clone(), value]);
            }
            if network_hosts.is_empty() {
                wrapped.push("--unshare-net".to_owned());
            }
        }
        "unshare" => {
            wrapped.extend([
                "unshare".to_owned(),
                "--fork".to_owned(),
                "--pid".to_owned(),
                "--mount-proc".to_owned(),
            ]);
            if network_hosts.is_empty() {
                wrapped.push("--net".to_owned());
            }
        }
        "sandbox-exec" => {
            let mut profile =
                "(version 1) (deny default) (import \"system.sb\") (allow file-read*)".to_owned();
            for path in writable {
                profile.push_str(&format!(
                    " (allow file-write* (subpath \"{}\"))",
                    sandbox_profile_escape(&path.to_string_lossy())
                ));
            }
            profile.push_str(" (allow process-exec)");
            if !network_hosts.is_empty() {
                profile.push_str(" (allow network*)");
            }
            wrapped.extend(["sandbox-exec".to_owned(), "-p".to_owned(), profile]);
        }
        _ => {}
    }
    wrapped.extend(command.iter().cloned());
    if wrapped.is_empty() {
        return Err("sandbox command must be a non-empty argv list".to_owned());
    }
    Ok(wrapped)
}

'''
    text = RUST.read_text(encoding="utf-8")
    if "fn platform_wrapped_command(" not in text:
        count = text.count(wrapper_anchor)
        if count != 1:
            raise SystemExit(f"Rust wrapper anchor: expected one match, found {count}")
        text = text.replace(wrapper_anchor, wrapper_code + wrapper_anchor, 1)
        RUST.write_text(text, encoding="utf-8")

    one(
        RUST,
        '''    let environment = filtered_environment(PLATFORM_ENV_ALLOWLIST, &project, false);\n    let started_at = now_iso()?;\n    let result = run_process(&command, &cwd, timeout, &environment)?;\n''',
        '''    let environment = filtered_environment(PLATFORM_ENV_ALLOWLIST, &project, false);\n    let execution_command = platform_wrapped_command(\n        &backend,\n        &command,\n        &project,\n        &writable,\n        &network_hosts,\n    )?;\n    let started_at = now_iso()?;\n    let result = run_process(&execution_command, &cwd, timeout, &environment)?;\n''',
        "Rust platform native wrapper execution",
    )
    one(
        RUST,
        '''    let exit = if result.timed_out {\n        124\n    } else {\n        u8::try_from(result.exit_code.clamp(0, 255)).unwrap_or(1)\n    };\n    Ok((value, exit))\n}\n\npub(crate) struct NativeExecution {\n''',
        '''    // The Python platform CLI returns 3 whenever the structured receipt\n    // reports ok=false, including timeout. Keep direct `sandbox execute`'s 124\n    // convention separate from the `run sandbox-run` public surface.\n    let exit = if result.exit_code == 0 && !result.timed_out { 0 } else { 3 };\n    Ok((value, exit))\n}\n\n#[cfg(test)]\nmod platform_wrapper_tests {\n    use super::platform_wrapped_command;\n    use serde_json::json;\n    use std::path::{Path, PathBuf};\n\n    fn command() -> Vec<String> {\n        vec!["tool".to_owned(), "arg".to_owned()]\n    }\n\n    #[test]\n    fn portable_backend_executes_original_command() {\n        let value = platform_wrapped_command(\n            &json!({"name":"portable-process-boundary"}),\n            &command(),\n            Path::new("/workspace"),\n            &[PathBuf::from("/workspace")],\n            &[],\n        )\n        .expect("portable command");\n        assert_eq!(value, command());\n    }\n\n    #[test]\n    fn bubblewrap_backend_enforces_namespace_wrapper() {\n        let value = platform_wrapped_command(\n            &json!({"name":"bubblewrap"}),\n            &command(),\n            Path::new("/workspace"),\n            &[PathBuf::from("/workspace/write")],\n            &[],\n        )\n        .expect("bubblewrap command");\n        assert_eq!(value.first().map(String::as_str), Some("bwrap"));\n        assert!(value.iter().any(|item| item == "--unshare-user"));\n        assert!(value.iter().any(|item| item == "--unshare-net"));\n        assert!(value.windows(3).any(|row| row == ["--bind", "/workspace/write", "/workspace/write"]));\n        assert_eq!(&value[value.len() - 2..], ["tool", "arg"]);\n    }\n\n    #[test]\n    fn unshare_backend_enforces_pid_and_network_namespaces() {\n        let value = platform_wrapped_command(\n            &json!({"name":"unshare"}),\n            &command(),\n            Path::new("/workspace"),\n            &[PathBuf::from("/workspace")],\n            &[],\n        )\n        .expect("unshare command");\n        assert_eq!(value.first().map(String::as_str), Some("unshare"));\n        assert!(value.iter().any(|item| item == "--pid"));\n        assert!(value.iter().any(|item| item == "--net"));\n    }\n\n    #[test]\n    fn sandbox_exec_profile_contains_write_boundary() {\n        let value = platform_wrapped_command(\n            &json!({"name":"sandbox-exec"}),\n            &command(),\n            Path::new("/workspace"),\n            &[PathBuf::from("/workspace/write")],\n            &["example.com".to_owned()],\n        )\n        .expect("sandbox-exec command");\n        assert_eq!(value.first().map(String::as_str), Some("sandbox-exec"));\n        let profile = value.get(2).expect("profile");\n        assert!(profile.contains("/workspace/write"));\n        assert!(profile.contains("allow network"));\n    }\n}\n\npub(crate) struct NativeExecution {\n''',
        "Rust platform timeout exit and wrapper tests",
    )

    print("sandbox parity repair present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
