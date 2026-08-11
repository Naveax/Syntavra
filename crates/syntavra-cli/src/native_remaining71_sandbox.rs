#![forbid(unsafe_code)]
#![allow(clippy::too_many_lines)]

use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use rand::{rngs::OsRng, RngCore as _};
use serde_json::{json, Map, Value};
use sha2::{Digest as _, Sha256};

use super::native_evidence_store::NativeEvidenceStore;

const DIRECT_ENV_ALLOWLIST: &[&str] = &[
    "PATH",
    "HOME",
    "USERPROFILE",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "CI",
];
const PLATFORM_ENV_ALLOWLIST: &[&str] = &[
    "PATH",
    "HOME",
    "USER",
    "USERNAME",
    "TMP",
    "TEMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "SYSTEMROOT",
    "COMSPEC",
];

pub(crate) fn supports(command: &[String]) -> bool {
    matches!(command, [root, action]
        if (root == "sandbox" && matches!(action.as_str(), "backends" | "plan" | "execute"))
            || (root == "run" && matches!(action.as_str(), "sandbox-status" | "sandbox-run")))
}

fn sha256(value: &[u8]) -> String {
    format!("{:x}", Sha256::digest(value))
}

fn sorted(value: &Value) -> Value {
    match value {
        Value::Array(rows) => Value::Array(rows.iter().map(sorted).collect()),
        Value::Object(map) => {
            let mut keys = map.keys().collect::<Vec<_>>();
            keys.sort_unstable();
            let mut output = Map::new();
            for key in keys {
                output.insert(key.clone(), sorted(&map[key]));
            }
            Value::Object(output)
        }
        _ => value.clone(),
    }
}

fn canonical_json(value: &Value) -> Result<Vec<u8>, String> {
    serde_json::to_vec(&sorted(value)).map_err(|error| format!("SANDBOX_JSON_FAILED:{error}"))
}

fn now_seconds() -> Result<f64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_secs_f64())
        .map_err(|error| format!("SANDBOX_CLOCK_FAILED:{error}"))
}

fn now_iso() -> Result<String, String> {
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("SANDBOX_CLOCK_FAILED:{error}"))?;
    let seconds =
        i64::try_from(duration.as_secs()).map_err(|_| "SANDBOX_CLOCK_RANGE".to_owned())?;
    let days = seconds / 86_400;
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let mut year = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = mp + if mp < 10 { 3 } else { -9 };
    if month <= 2 {
        year += 1;
    }
    let sod = seconds % 86_400;
    Ok(format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}.{:06}+00:00",
        sod / 3600,
        (sod % 3600) / 60,
        sod % 60,
        duration.subsec_micros()
    ))
}

fn random_sandbox_id() -> String {
    let mut raw = [0u8; 16];
    OsRng.fill_bytes(&mut raw);
    let suffix = raw
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    format!("sb-{suffix}")
}

fn which(name: &str) -> Option<PathBuf> {
    let path = env::var_os("PATH")?;
    let suffixes: &[&str] = if cfg!(windows) {
        &[".exe", ".cmd", ".bat", ""]
    } else {
        &[""]
    };
    for directory in env::split_paths(&path) {
        for suffix in suffixes {
            let candidate = directory.join(format!("{name}{suffix}"));
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }
    None
}

fn backend_map() -> Value {
    json!({
        "docker": which("docker").map(|value| value.to_string_lossy().into_owned()),
        "podman": which("podman").map(|value| value.to_string_lossy().into_owned()),
        "bwrap": if cfg!(windows) { None::<String> } else { which("bwrap").map(|value| value.to_string_lossy().into_owned()) },
        "local-restricted": env::current_exe().ok().map(|value| value.to_string_lossy().into_owned()),
    })
}

fn option_value(arguments: &[String], flag: &str) -> Result<Option<String>, String> {
    let mut result = None;
    let mut index = 0usize;
    while index < arguments.len() {
        let item = &arguments[index];
        let found = if item == flag {
            index += 1;
            Some(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?
                    .clone(),
            )
        } else {
            item.strip_prefix(flag)
                .and_then(|tail| tail.strip_prefix('='))
                .map(str::to_owned)
        };
        if let Some(found) = found {
            if result.is_some() {
                return Err(format!("{flag}_DUPLICATE"));
            }
            result = Some(found);
        }
        index += 1;
    }
    Ok(result)
}

fn repeated_values(arguments: &[String], flag: &str) -> Result<Vec<String>, String> {
    let mut values = Vec::new();
    let mut index = 0usize;
    while index < arguments.len() {
        if arguments[index] == flag {
            index += 1;
            values.push(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?
                    .clone(),
            );
        } else if let Some(value) = arguments[index]
            .strip_prefix(flag)
            .and_then(|tail| tail.strip_prefix('='))
        {
            values.push(value.to_owned());
        }
        index += 1;
    }
    Ok(values)
}

fn has_flag(arguments: &[String], flag: &str) -> bool {
    arguments.iter().any(|item| item == flag)
}

fn load_json(value: &str) -> Result<Value, String> {
    let raw = if Path::new(value).is_file() {
        fs::read_to_string(value).map_err(|error| format!("SANDBOX_JSON_READ_FAILED:{error}"))?
    } else {
        value.to_owned()
    };
    serde_json::from_str(&raw).map_err(|error| format!("SANDBOX_JSON_INVALID:{error}"))
}

fn action_position(arguments: &[String], root: &str, action: &str) -> Result<usize, String> {
    arguments
        .windows(2)
        .position(|row| row[0] == root && row[1] == action)
        .map(|index| index + 2)
        .ok_or_else(|| format!("SANDBOX_ACTION_NOT_FOUND:{root}:{action}"))
}

fn direct_command(arguments: &[String], action: &str) -> Result<Vec<String>, String> {
    let start = action_position(arguments, "sandbox", action)?;
    if let Some(separator) = arguments[start..].iter().position(|item| item == "--") {
        let command = arguments[start + separator + 1..].to_vec();
        if command.is_empty() {
            return Err("sandbox command requires argv after --".to_owned());
        }
        return Ok(command);
    }
    Err("sandbox command requires argv after --".to_owned())
}

fn project_child(project: &Path, value: &str, require_exists: bool) -> Result<PathBuf, String> {
    let raw = Path::new(value);
    let candidate = if raw.is_absolute() {
        raw.to_path_buf()
    } else {
        project.join(raw)
    };
    let normalized = if require_exists {
        fs::canonicalize(&candidate)
            .map_err(|error| format!("SANDBOX_PATH_RESOLVE_FAILED:{error}"))?
    } else if let Some(parent) = candidate.parent() {
        let canonical_parent = fs::canonicalize(parent).unwrap_or_else(|_| parent.to_path_buf());
        canonical_parent.join(candidate.file_name().unwrap_or_default())
    } else {
        candidate.clone()
    };
    let project_canonical = fs::canonicalize(project)
        .map_err(|error| format!("SANDBOX_PROJECT_RESOLVE_FAILED:{error}"))?;
    if !normalized.starts_with(&project_canonical) {
        return Err(format!("path escapes project: {value}"));
    }
    Ok(normalized)
}

fn direct_policy(arguments: &[String], project: &Path) -> Result<Value, String> {
    let backend = option_value(arguments, "--backend")?.unwrap_or_else(|| "auto".to_owned());
    if !matches!(
        backend.as_str(),
        "auto" | "docker" | "podman" | "bwrap" | "local-restricted"
    ) {
        return Err("unsupported sandbox backend".to_owned());
    }
    let network = option_value(arguments, "--network")?.unwrap_or_else(|| "none".to_owned());
    if !matches!(network.as_str(), "none" | "inherit") {
        return Err("network must be none or inherit".to_owned());
    }
    let timeout = option_value(arguments, "--timeout")?
        .map(|v| {
            v.parse::<f64>()
                .map_err(|_| "SANDBOX_TIMEOUT_INVALID".to_owned())
        })
        .transpose()?
        .unwrap_or(1200.0);
    let memory_mb = option_value(arguments, "--memory-mb")?
        .map(|v| {
            v.parse::<i64>()
                .map_err(|_| "SANDBOX_MEMORY_INVALID".to_owned())
        })
        .transpose()?
        .unwrap_or(2048);
    let cpus = option_value(arguments, "--cpus")?
        .map(|v| {
            v.parse::<f64>()
                .map_err(|_| "SANDBOX_CPUS_INVALID".to_owned())
        })
        .transpose()?
        .unwrap_or(2.0);
    let pids = option_value(arguments, "--pids")?
        .map(|v| {
            v.parse::<i64>()
                .map_err(|_| "SANDBOX_PIDS_INVALID".to_owned())
        })
        .transpose()?
        .unwrap_or(256);
    if timeout <= 0.0 || memory_mb <= 0 || cpus <= 0.0 || pids <= 0 {
        return Err("sandbox limits must be positive".to_owned());
    }
    let writable = repeated_values(arguments, "--writable")?;
    for item in &writable {
        let _ = project_child(project, item, false)?;
    }
    Ok(json!({
        "backend": backend,
        "network": network,
        "read_only_repository": has_flag(arguments, "--read-only"),
        "timeout_seconds": timeout,
        "memory_mb": memory_mb,
        "cpu_count": cpus,
        "process_limit": pids,
        "env_allowlist": DIRECT_ENV_ALLOWLIST,
        "env_overrides": {},
        "writable_paths": writable,
        "strict": !has_flag(arguments, "--allow-degraded"),
    }))
}

fn select_direct_backend(policy: &Value) -> Result<(String, Vec<String>), String> {
    let requested = policy["backend"].as_str().unwrap_or("auto");
    let available = backend_map();
    let selected = if requested != "auto" {
        if available[requested].is_null() {
            return Err(format!("requested backend unavailable: {requested}"));
        }
        requested.to_owned()
    } else {
        ["docker", "podman", "bwrap"]
            .into_iter()
            .find(|name| !available[*name].is_null())
            .unwrap_or("local-restricted")
            .to_owned()
    };
    let mut reasons = Vec::new();
    if selected == "local-restricted" {
        reasons.push("network-isolation-unavailable".to_owned());
        reasons.push("filesystem-overlay-unavailable".to_owned());
        if policy["strict"].as_bool().unwrap_or(true) && policy["network"].as_str() == Some("none")
        {
            return Err(
                "strict network-disabled execution requires docker, podman, or bwrap".to_owned(),
            );
        }
    }
    Ok((selected, reasons))
}

fn direct_plan(arguments: &[String], project: &Path, action: &str) -> Result<Value, String> {
    let command = direct_command(arguments, action)?;
    let policy = direct_policy(arguments, project)?;
    let cwd_raw = option_value(arguments, "--cwd")?.unwrap_or_else(|| ".".to_owned());
    let cwd = project_child(project, &cwd_raw, true)?;
    let (backend, degraded) = select_direct_backend(&policy)?;
    let available = backend_map();
    let network_none = policy["network"].as_str() == Some("none");
    let guarantees = json!({
        "network_isolated": matches!(backend.as_str(), "docker" | "podman" | "bwrap") && network_none,
        "filesystem_isolated": matches!(backend.as_str(), "docker" | "podman" | "bwrap"),
        "resource_limited": matches!(backend.as_str(), "docker" | "podman" | "bwrap") || (!cfg!(windows) && !cfg!(target_os = "macos")),
        "secret_filtered": true,
        "process_tree_controlled": true,
    });
    let mut wrapped = Vec::<String>::new();
    let execution_cwd: String;
    if matches!(backend.as_str(), "docker" | "podman") {
        let executable = available[&backend]
            .as_str()
            .ok_or_else(|| "SANDBOX_BACKEND_PATH_INVALID".to_owned())?;
        let project_canonical = fs::canonicalize(project)
            .map_err(|error| format!("SANDBOX_PROJECT_RESOLVE_FAILED:{error}"))?;
        let relative = cwd
            .strip_prefix(&project_canonical)
            .unwrap_or(Path::new(""));
        let relative = relative.to_string_lossy().replace('\\', "/");
        let container_cwd = if relative.is_empty() {
            "/workspace".to_owned()
        } else {
            format!("/workspace/{relative}")
        };
        let mount_mode = if policy["read_only_repository"].as_bool().unwrap_or(false) {
            "ro"
        } else {
            "rw"
        };
        wrapped.extend([
            executable.to_owned(),
            "run".to_owned(),
            "--rm".to_owned(),
            "--init".to_owned(),
            "--network".to_owned(),
            if network_none {
                "none".to_owned()
            } else {
                "bridge".to_owned()
            },
            "--memory".to_owned(),
            format!("{}m", policy["memory_mb"].as_i64().unwrap_or(2048)),
            "--cpus".to_owned(),
            policy["cpu_count"].as_f64().unwrap_or(2.0).to_string(),
            "--pids-limit".to_owned(),
            policy["process_limit"].as_i64().unwrap_or(256).to_string(),
            "--read-only".to_owned(),
            "--tmpfs".to_owned(),
            "/tmp:rw,noexec,nosuid,size=256m".to_owned(),
            "--mount".to_owned(),
            format!(
                "type=bind,src={},dst=/workspace,{mount_mode}",
                project_canonical.to_string_lossy()
            ),
            "--workdir".to_owned(),
            container_cwd,
        ]);
        for writable in policy["writable_paths"].as_array().into_iter().flatten() {
            let raw = writable.as_str().unwrap_or_default();
            let host = project_child(project, raw, false)?;
            let relative = host
                .strip_prefix(&project_canonical)
                .unwrap_or(Path::new(""))
                .to_string_lossy()
                .replace('\\', "/");
            wrapped.extend([
                "--mount".to_owned(),
                format!(
                    "type=bind,src={},dst=/workspace/{relative},rw",
                    host.to_string_lossy()
                ),
            ]);
        }
        wrapped.push("python:3.13-slim".to_owned());
        wrapped.extend(command.clone());
        execution_cwd = project_canonical.to_string_lossy().into_owned();
    } else if backend == "bwrap" {
        let executable = available["bwrap"]
            .as_str()
            .ok_or_else(|| "SANDBOX_BACKEND_PATH_INVALID".to_owned())?;
        let project_canonical = fs::canonicalize(project)
            .map_err(|error| format!("SANDBOX_PROJECT_RESOLVE_FAILED:{error}"))?;
        let relative = cwd
            .strip_prefix(&project_canonical)
            .unwrap_or(Path::new(""))
            .to_string_lossy()
            .replace('\\', "/");
        let target_cwd = if relative.is_empty() {
            "/workspace".to_owned()
        } else {
            format!("/workspace/{relative}")
        };
        wrapped.extend([
            executable.to_owned(),
            "--die-with-parent".to_owned(),
            "--new-session".to_owned(),
            "--unshare-pid".to_owned(),
            "--unshare-ipc".to_owned(),
            "--unshare-uts".to_owned(),
            if policy["read_only_repository"].as_bool().unwrap_or(false) {
                "--ro-bind".to_owned()
            } else {
                "--bind".to_owned()
            },
            project_canonical.to_string_lossy().into_owned(),
            "/workspace".to_owned(),
            "--proc".to_owned(),
            "/proc".to_owned(),
            "--dev".to_owned(),
            "/dev".to_owned(),
            "--tmpfs".to_owned(),
            "/tmp".to_owned(),
            "--chdir".to_owned(),
            target_cwd,
        ]);
        if network_none {
            wrapped.push("--unshare-net".to_owned());
        }
        for system in ["/usr", "/bin", "/lib", "/lib64", "/etc"] {
            if Path::new(system).exists() {
                wrapped.extend(["--ro-bind".to_owned(), system.to_owned(), system.to_owned()]);
            }
        }
        wrapped.extend(command.clone());
        execution_cwd = project_canonical.to_string_lossy().into_owned();
    } else {
        wrapped = command.clone();
        execution_cwd = cwd.to_string_lossy().into_owned();
    }
    Ok(json!({
        "sandbox_id": random_sandbox_id(),
        "backend": backend,
        "command": wrapped,
        "cwd": execution_cwd,
        "guarantees": guarantees,
        "policy": policy,
        "degraded_reasons": degraded,
    }))
}

fn secret_like(key: &str) -> bool {
    let upper = key.to_ascii_uppercase();
    [
        "TOKEN",
        "SECRET",
        "PASSWORD",
        "PASSWD",
        "API_KEY",
        "PRIVATE_KEY",
        "CREDENTIAL",
    ]
    .iter()
    .any(|marker| upper.contains(marker))
}

fn filtered_environment(
    allowlist: &[&str],
    workspace: &Path,
    include_numeric_bounds: bool,
) -> BTreeMap<String, String> {
    let allowed = allowlist.iter().copied().collect::<BTreeSet<_>>();
    let mut output = BTreeMap::new();
    for (key, value) in env::vars() {
        if allowed.contains(key.as_str()) && !secret_like(&key) {
            output.insert(key, value);
        }
    }
    output.insert("SYNTAVRA_SANDBOX".to_owned(), "1".to_owned());
    output.insert(
        "SYNTAVRA_WORKSPACE".to_owned(),
        workspace.to_string_lossy().into_owned(),
    );
    if include_numeric_bounds {
        output
            .entry("OPENBLAS_NUM_THREADS".to_owned())
            .or_insert_with(|| "1".to_owned());
        output
            .entry("OMP_NUM_THREADS".to_owned())
            .or_insert_with(|| "1".to_owned());
        output
            .entry("MKL_NUM_THREADS".to_owned())
            .or_insert_with(|| "1".to_owned());
        output
            .entry("NUMEXPR_NUM_THREADS".to_owned())
            .or_insert_with(|| "1".to_owned());
    }
    output
}

struct ProcessResult {
    exit_code: i32,
    timed_out: bool,
    duration_ms: f64,
    stdout: Vec<u8>,
    stderr: Vec<u8>,
}

fn run_process(
    argv: &[String],
    cwd: &Path,
    timeout_seconds: f64,
    environment: &BTreeMap<String, String>,
) -> Result<ProcessResult, String> {
    if argv.is_empty() {
        return Err("command must be a non-empty argv sequence".to_owned());
    }
    let started = Instant::now();
    let mut command = Command::new(&argv[0]);
    command
        .args(&argv[1..])
        .current_dir(cwd)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    command.env_clear();
    for (key, value) in environment {
        command.env(key, value);
    }
    let mut child = command
        .spawn()
        .map_err(|error| format!("SANDBOX_PROCESS_SPAWN_FAILED:{error}"))?;
    let deadline = started + Duration::from_secs_f64(timeout_seconds.max(0.1));
    let mut timed_out = false;
    loop {
        if child
            .try_wait()
            .map_err(|error| format!("SANDBOX_PROCESS_WAIT_FAILED:{error}"))?
            .is_some()
        {
            break;
        }
        if Instant::now() >= deadline {
            timed_out = true;
            let _ = child.kill();
            break;
        }
        thread::sleep(Duration::from_millis(5));
    }
    let output = child
        .wait_with_output()
        .map_err(|error| format!("SANDBOX_PROCESS_OUTPUT_FAILED:{error}"))?;
    Ok(ProcessResult {
        exit_code: if timed_out {
            output.status.code().unwrap_or(124)
        } else {
            output.status.code().unwrap_or(1)
        },
        timed_out,
        duration_ms: started.elapsed().as_secs_f64() * 1000.0,
        stdout: output.stdout,
        stderr: output.stderr,
    })
}

fn stable_project_id(project: &Path) -> String {
    let raw = project.to_string_lossy();
    let normalized = if cfg!(windows) {
        raw.replace('/', "\\").to_lowercase()
    } else {
        raw.into_owned()
    };
    sha256(normalized.as_bytes())
}

fn firewall_summary(command: &[String], result: &ProcessResult, evidence_handle: &str) -> String {
    let sample = if result.stdout.is_empty() {
        &result.stderr
    } else {
        &result.stdout
    };
    let parser = if serde_json::from_slice::<Value>(sample).is_ok() {
        "json"
    } else if result.exit_code == 0 {
        "success"
    } else {
        "failure"
    };
    let raw_bytes = result.stdout.len() + result.stderr.len();
    let mut useful = String::from_utf8_lossy(sample).replace('\r', "");
    if useful.len() > 4096 {
        useful.truncate(4096);
    }
    let mut value = format!(
        "Command: {}\nExit code: {}\nDuration: {:.3} seconds\nParser: {}\nScanned: {} lines / {} bytes\nSuppressed: 0 low-value lines\nFull log: {}",
        command.join(" "), result.exit_code, result.duration_ms / 1000.0, parser,
        String::from_utf8_lossy(&[result.stdout.as_slice(), result.stderr.as_slice()].concat()).lines().count(), raw_bytes, evidence_handle
    );
    if !useful.trim().is_empty() {
        value.push_str("\nExcerpt:\n");
        value.push_str(useful.trim());
    }
    value
}

fn direct_execute(
    arguments: &[String],
    project: &Path,
    state_root: &Path,
) -> Result<(Value, u8), String> {
    let plan = direct_plan(arguments, project, "execute")?;
    let sandbox_id = plan["sandbox_id"].as_str().unwrap_or_default();
    let run_root = state_root.join("sandbox").join("runs").join(sandbox_id);
    fs::create_dir_all(&run_root).map_err(|error| format!("SANDBOX_RUN_CREATE_FAILED:{error}"))?;
    let pretty = serde_json::to_vec_pretty(&plan)
        .map_err(|error| format!("SANDBOX_PLAN_JSON_FAILED:{error}"))?;
    fs::write(run_root.join("plan.json"), [&pretty[..], b"\n"].concat())
        .map_err(|error| format!("SANDBOX_PLAN_WRITE_FAILED:{error}"))?;
    let command = plan["command"]
        .as_array()
        .ok_or_else(|| "SANDBOX_PLAN_COMMAND_INVALID".to_owned())?
        .iter()
        .map(|v| v.as_str().unwrap_or_default().to_owned())
        .collect::<Vec<_>>();
    let cwd = PathBuf::from(plan["cwd"].as_str().unwrap_or_default());
    let timeout = plan["policy"]["timeout_seconds"].as_f64().unwrap_or(1200.0);
    let env = filtered_environment(
        DIRECT_ENV_ALLOWLIST,
        project,
        plan["backend"].as_str() == Some("local-restricted"),
    );
    let started = now_seconds()?;
    let result = run_process(&command, &cwd, timeout, &env)?;
    fs::write(run_root.join("stdout.log"), &result.stdout)
        .map_err(|error| format!("SANDBOX_STDOUT_WRITE_FAILED:{error}"))?;
    fs::write(run_root.join("stderr.log"), &result.stderr)
        .map_err(|error| format!("SANDBOX_STDERR_WRITE_FAILED:{error}"))?;
    let project_id =
        stable_project_id(&fs::canonicalize(project).unwrap_or_else(|_| project.to_path_buf()));
    let evidence = NativeEvidenceStore::open(state_root, &project_id)?;
    let mut combined = result.stdout.clone();
    if !combined.is_empty() && !result.stderr.is_empty() {
        combined.push(b'\n');
    }
    combined.extend_from_slice(&result.stderr);
    let evidence_handle = evidence.put(&combined, "command-output", &json!({"command": direct_command(arguments, "execute")?, "exit_code": result.exit_code, "duration_seconds": result.duration_ms / 1000.0}))?;
    let original_command = direct_command(arguments, "execute")?;
    let summary = firewall_summary(&original_command, &result, &evidence_handle);
    let value = json!({
        "sandbox_id": sandbox_id,
        "backend": plan["backend"].clone(),
        "exit_code": result.exit_code,
        "duration_seconds": now_seconds()? - started,
        "timed_out": result.timed_out,
        "summary": summary,
        "evidence_handle": evidence_handle,
        "stdout_bytes": result.stdout.len(),
        "stderr_bytes": result.stderr.len(),
        "guarantees": plan["guarantees"].clone(),
        "degraded_reasons": plan["degraded_reasons"].clone(),
    });
    let rendered = serde_json::to_vec_pretty(&value)
        .map_err(|error| format!("SANDBOX_RESULT_JSON_FAILED:{error}"))?;
    fs::write(
        run_root.join("result.json"),
        [&rendered[..], b"\n"].concat(),
    )
    .map_err(|error| format!("SANDBOX_RESULT_WRITE_FAILED:{error}"))?;
    let code = if result.timed_out {
        124
    } else {
        u8::try_from(result.exit_code.clamp(0, 255)).unwrap_or(1)
    };
    Ok((value, code))
}

fn probe_backend(argv: &[&str]) -> (bool, String) {
    if argv.is_empty() {
        return (false, String::new());
    }
    let output = Command::new(argv[0])
        .args(&argv[1..])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output();
    match output {
        Ok(value) => {
            let detail = if value.stderr.is_empty() {
                &value.stdout
            } else {
                &value.stderr
            };
            (
                value.status.success(),
                String::from_utf8_lossy(detail)
                    .trim()
                    .chars()
                    .rev()
                    .take(1000)
                    .collect::<String>()
                    .chars()
                    .rev()
                    .collect(),
            )
        }
        Err(error) => (
            false,
            format!("{}: {error}", std::any::type_name::<std::io::Error>()),
        ),
    }
}

fn platform_backend(
    project: &Path,
    strict_native: bool,
    network_hosts: &[String],
    allow_child_processes: bool,
) -> Result<Value, String> {
    let system = if cfg!(windows) {
        "windows"
    } else if cfg!(target_os = "macos") {
        "darwin"
    } else if cfg!(target_os = "linux") {
        "linux"
    } else {
        env::consts::OS
    };
    let mut backend = if system == "linux" {
        if which("bwrap").is_some() {
            let (ok, detail) = probe_backend(&[
                "bwrap",
                "--die-with-parent",
                "--new-session",
                "--unshare-user",
                "--unshare-pid",
                "--ro-bind",
                "/",
                "/",
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "/bin/true",
            ]);
            if ok {
                json!({"name":"bubblewrap","platform":"linux","available":true,"enforced":["mount-namespace","pid-namespace","user-namespace","process-tree","filesystem-boundary"],"unsupported": if network_hosts.is_empty(){json!([])}else{json!(["domain-level-egress"])},"command_prefix":[],"detail":""})
            } else {
                json!({"name":"portable-process-boundary","platform":"linux","available":false,"enforced":["cwd-boundary","environment-filter","timeout","process-group"],"unsupported":["mount-namespace","network-namespace","seccomp","cgroup"],"command_prefix":[],"detail":format!("bubblewrap probe failed: {}", if detail.is_empty(){"kernel rejected namespace setup"}else{&detail})})
            }
        } else if which("unshare").is_some() {
            let (ok, detail) =
                probe_backend(&["unshare", "--fork", "--pid", "--mount-proc", "/bin/true"]);
            if ok {
                json!({"name":"unshare","platform":"linux","available":true,"enforced":["pid-namespace","process-tree"],"unsupported":["filesystem-boundary","seccomp","cgroup","domain-level-egress"],"command_prefix":[],"detail":"partial native backend; filesystem containment relies on workspace validation"})
            } else {
                json!({"name":"portable-process-boundary","platform":"linux","available":false,"enforced":["cwd-boundary","environment-filter","timeout","process-group"],"unsupported":["mount-namespace","network-namespace","seccomp","cgroup"],"command_prefix":[],"detail":format!("unshare probe failed: {}", if detail.is_empty(){"kernel rejected namespace setup"}else{&detail})})
            }
        } else {
            json!({"name":"portable-process-boundary","platform":"linux","available":false,"enforced":["cwd-boundary","environment-filter","timeout","process-group"],"unsupported":["mount-namespace","network-namespace","seccomp","cgroup"],"command_prefix":[],"detail":"install bubblewrap for full native isolation"})
        }
    } else if system == "darwin" {
        if which("sandbox-exec").is_some() {
            let (ok, detail) = probe_backend(&[
                "sandbox-exec",
                "-p",
                "(version 1) (allow default)",
                "/usr/bin/true",
            ]);
            if ok {
                json!({"name":"sandbox-exec","platform":"darwin","available":true,"enforced":["filesystem-boundary","process-policy","network-policy"],"unsupported":["domain-level-egress","memory-limit"],"command_prefix":[],"detail":""})
            } else {
                json!({"name":"portable-process-boundary","platform":"darwin","available":false,"enforced":["cwd-boundary","environment-filter","timeout","process-group"],"unsupported":["sandbox-profile","network-policy","keychain-policy"],"command_prefix":[],"detail":format!("sandbox-exec probe failed: {}", if detail.is_empty(){"sandbox profile execution failed"}else{&detail})})
            }
        } else {
            json!({"name":"portable-process-boundary","platform":"darwin","available":false,"enforced":["cwd-boundary","environment-filter","timeout","process-group"],"unsupported":["sandbox-profile","network-policy","keychain-policy"],"command_prefix":[],"detail":"sandbox-exec is unavailable on this host"})
        }
    } else if system == "windows" {
        json!({"name":"windows-process-boundary","platform":"windows","available":true,"enforced":["cwd-boundary","environment-filter","timeout","process-tree"],"unsupported":["job-object","restricted-token","appcontainer","network-policy","registry-boundary"],"command_prefix":[],"detail":"native helper required for Job Object and restricted-token enforcement"})
    } else {
        json!({"name":"portable-process-boundary","platform":system,"available":false,"enforced":["cwd-boundary","environment-filter","timeout"],"unsupported":["native-isolation"],"command_prefix":[],"detail":""})
    };
    if !allow_child_processes {
        let enforced = backend["enforced"].as_array().cloned().unwrap_or_default();
        if !enforced
            .iter()
            .any(|value| value.as_str() == Some("child-process-blocking"))
        {
            let mut unsupported = backend["unsupported"]
                .as_array()
                .cloned()
                .unwrap_or_default();
            if !unsupported
                .iter()
                .any(|value| value.as_str() == Some("child-process-blocking"))
            {
                unsupported.push(Value::String("child-process-blocking".to_owned()));
            }
            backend
                .as_object_mut()
                .expect("object")
                .insert("unsupported".to_owned(), Value::Array(unsupported));
            let existing = backend["detail"].as_str().unwrap_or_default().to_owned();
            backend.as_object_mut().expect("object").insert(
                "detail".to_owned(),
                Value::String(format!(
                    "{}{sep}backend cannot prove child-process prevention",
                    existing,
                    sep = if existing.is_empty() { "" } else { "; " }
                )),
            );
        }
    }
    if strict_native
        && (!backend["available"].as_bool().unwrap_or(false)
            || !backend["unsupported"].as_array().is_some_and(Vec::is_empty))
    {
        return Err(format!(
            "required native sandbox controls unavailable: {}",
            backend["unsupported"]
        ));
    }
    let _ = project;
    Ok(backend)
}

fn sandbox_profile_escape(value: &str) -> String {
    value.replace('\\', "\\\\").replace('"', "\\\"")
}

fn platform_wrapped_command(
    backend: &Value,
    command: &[String],
    project: &Path,
    writable: &[PathBuf],
    network_hosts: &[String],
) -> Result<Vec<String>, String> {
    let name = backend["name"]
        .as_str()
        .unwrap_or("portable-process-boundary");
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

fn platform_status(project: &Path) -> Result<Value, String> {
    let backend = platform_backend(project, false, &[], true)?;
    let strict_ready = backend["available"].as_bool().unwrap_or(false)
        && backend["unsupported"].as_array().is_some_and(Vec::is_empty);
    let probe_cached = matches!(
        backend["name"].as_str(),
        Some("bubblewrap" | "unshare" | "sandbox-exec")
    );
    Ok(
        json!({"ok":true,"backend":backend,"strict_ready":strict_ready,"fail_closed":true,"probe_cached":probe_cached}),
    )
}

fn platform_command(arguments: &[String]) -> Result<Vec<String>, String> {
    let start = action_position(arguments, "run", "sandbox-run")?;
    let flags_with_values = [
        "--cwd",
        "--timeout",
        "--network-host",
        "--writable-path",
        "--memory-bytes",
        "--cpu-seconds",
    ];
    let mut positional = Vec::new();
    let mut index = start;
    while index < arguments.len() {
        if flags_with_values.contains(&arguments[index].as_str()) {
            index += 2;
            continue;
        }
        if arguments[index].starts_with("--") {
            index += 1;
            continue;
        }
        positional.push(arguments[index].clone());
        index += 1;
    }
    let raw = positional
        .first()
        .ok_or_else(|| "sandbox command is required".to_owned())?;
    let value = load_json(raw)?;
    let rows = value
        .as_array()
        .ok_or_else(|| "sandbox command must be a non-empty JSON argv list".to_owned())?;
    if rows.is_empty()
        || rows
            .iter()
            .any(|row| row.as_str().is_none_or(|value| value.contains('\0')))
    {
        return Err("sandbox command must be a non-empty JSON argv list".to_owned());
    }
    Ok(rows
        .iter()
        .map(|row| row.as_str().unwrap_or_default().to_owned())
        .collect())
}

fn platform_run(
    arguments: &[String],
    project: &Path,
    state_root: &Path,
) -> Result<(Value, u8), String> {
    let command = platform_command(arguments)?;
    let project = fs::canonicalize(project)
        .map_err(|error| format!("SANDBOX_PROJECT_RESOLVE_FAILED:{error}"))?;
    let writable_raw = repeated_values(arguments, "--writable-path")?;
    let writable = if writable_raw.is_empty() {
        vec![project.clone()]
    } else {
        writable_raw
            .iter()
            .map(|value| project_child(&project, value, false))
            .collect::<Result<Vec<_>, _>>()?
    };
    let timeout = option_value(arguments, "--timeout")?
        .map(|v| {
            v.parse::<f64>()
                .map_err(|_| "SANDBOX_TIMEOUT_INVALID".to_owned())
        })
        .transpose()?
        .unwrap_or(300.0)
        .max(0.1);
    let strict = has_flag(arguments, "--strict-native");
    let network_hosts = repeated_values(arguments, "--network-host")?;
    let allow_children = !has_flag(arguments, "--no-child-processes");
    let backend = platform_backend(&project, strict, &network_hosts, allow_children)?;
    let cwd = match option_value(arguments, "--cwd")? {
        Some(value) => project_child(&project, &value, true)?,
        None => project.clone(),
    };
    let environment = filtered_environment(PLATFORM_ENV_ALLOWLIST, &project, false);
    let execution_command =
        platform_wrapped_command(&backend, &command, &project, &writable, &network_hosts)?;
    let started_at = now_iso()?;
    let result = run_process(&execution_command, &cwd, timeout, &environment)?;
    let policy = json!({
        "workspace": project.to_string_lossy(),
        "writable_paths": writable.iter().map(|value| value.to_string_lossy().into_owned()).collect::<Vec<_>>(),
        "network_hosts": network_hosts,
        "timeout_seconds": timeout,
        "memory_bytes": option_value(arguments,"--memory-bytes")?.and_then(|v|v.parse::<i64>().ok()),
        "cpu_seconds": option_value(arguments,"--cpu-seconds")?.and_then(|v|v.parse::<i64>().ok()),
        "allow_child_processes": allow_children,
        "strict_native": strict,
        "max_stdout_bytes": 32 * 1024 * 1024,
        "max_stderr_bytes": 8 * 1024 * 1024,
    });
    let body = json!({
        "command": command,
        "cwd": cwd.to_string_lossy(),
        "backend": backend,
        "started_at": started_at,
        "duration_ms": (result.duration_ms * 1000.0).round() / 1000.0,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "output_limit_exceeded": false,
        "stdout_bytes_seen": result.stdout.len(),
        "stderr_bytes_seen": result.stderr.len(),
        "stdout_sha256": sha256(&result.stdout),
        "stderr_sha256": sha256(&result.stderr),
    });
    let receipt_id = format!("sha256:{}", sha256(&canonical_json(&body)?));
    let value = json!({
        "receipt_id": receipt_id,
        "command": command,
        "cwd": cwd.to_string_lossy(),
        "backend": backend,
        "started_at": started_at,
        "duration_ms": (result.duration_ms * 1000.0).round() / 1000.0,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "output_limit_exceeded": false,
        "stdout_bytes_seen": result.stdout.len(),
        "stderr_bytes_seen": result.stderr.len(),
        "stdout": String::from_utf8_lossy(&result.stdout),
        "stderr": String::from_utf8_lossy(&result.stderr),
        "stdout_sha256": sha256(&result.stdout),
        "stderr_sha256": sha256(&result.stderr),
        "environment_keys": environment.keys().cloned().collect::<Vec<_>>(),
        "policy": policy,
        "ok": result.exit_code == 0 && !result.timed_out,
    });
    let destination = state_root
        .join("unified")
        .join("sandbox")
        .join("execution-receipts")
        .join(format!("{}.json", receipt_id.trim_start_matches("sha256:")));
    if let Some(parent) = destination.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("SANDBOX_RECEIPT_PARENT_FAILED:{error}"))?;
    }
    let rendered = serde_json::to_vec_pretty(&value)
        .map_err(|error| format!("SANDBOX_RECEIPT_JSON_FAILED:{error}"))?;
    fs::write(destination, [&rendered[..], b"\n"].concat())
        .map_err(|error| format!("SANDBOX_RECEIPT_WRITE_FAILED:{error}"))?;
    // The Python platform CLI returns 3 whenever the structured receipt
    // reports ok=false, including timeout. Keep direct `sandbox execute`'s 124
    // convention separate from the `run sandbox-run` public surface.
    let exit = if result.exit_code == 0 && !result.timed_out {
        0
    } else {
        3
    };
    Ok((value, exit))
}

#[cfg(test)]
mod platform_wrapper_tests {
    use super::platform_wrapped_command;
    use serde_json::json;
    use std::path::{Path, PathBuf};

    fn command() -> Vec<String> {
        vec!["tool".to_owned(), "arg".to_owned()]
    }

    #[test]
    fn portable_backend_executes_original_command() {
        let value = platform_wrapped_command(
            &json!({"name":"portable-process-boundary"}),
            &command(),
            Path::new("/workspace"),
            &[PathBuf::from("/workspace")],
            &[],
        )
        .expect("portable command");
        assert_eq!(value, command());
    }

    #[test]
    fn bubblewrap_backend_enforces_namespace_wrapper() {
        let value = platform_wrapped_command(
            &json!({"name":"bubblewrap"}),
            &command(),
            Path::new("/workspace"),
            &[PathBuf::from("/workspace/write")],
            &[],
        )
        .expect("bubblewrap command");
        assert_eq!(value.first().map(String::as_str), Some("bwrap"));
        assert!(value.iter().any(|item| item == "--unshare-user"));
        assert!(value.iter().any(|item| item == "--unshare-net"));
        assert!(value
            .windows(3)
            .any(|row| row == ["--bind", "/workspace/write", "/workspace/write"]));
        assert_eq!(&value[value.len() - 2..], ["tool", "arg"]);
    }

    #[test]
    fn unshare_backend_enforces_pid_and_network_namespaces() {
        let value = platform_wrapped_command(
            &json!({"name":"unshare"}),
            &command(),
            Path::new("/workspace"),
            &[PathBuf::from("/workspace")],
            &[],
        )
        .expect("unshare command");
        assert_eq!(value.first().map(String::as_str), Some("unshare"));
        assert!(value.iter().any(|item| item == "--pid"));
        assert!(value.iter().any(|item| item == "--net"));
    }

    #[test]
    fn sandbox_exec_profile_contains_write_boundary() {
        let value = platform_wrapped_command(
            &json!({"name":"sandbox-exec"}),
            &command(),
            Path::new("/workspace"),
            &[PathBuf::from("/workspace/write")],
            &["example.com".to_owned()],
        )
        .expect("sandbox-exec command");
        assert_eq!(value.first().map(String::as_str), Some("sandbox-exec"));
        let profile = value.get(2).expect("profile");
        assert!(profile.contains("/workspace/write"));
        assert!(profile.contains("allow network"));
    }
}

pub(crate) struct NativeExecution {
    pub(crate) value: Value,
    pub(crate) exit_code: u8,
}

pub(crate) fn execute(
    command: &[String],
    arguments: &[String],
    project: &Path,
    state_root: &Path,
) -> Result<Option<NativeExecution>, String> {
    if !supports(command) {
        return Ok(None);
    }
    match command {
        [root, action] if root == "sandbox" && action == "backends" => Ok(Some(NativeExecution {
            value: backend_map(),
            exit_code: 0,
        })),
        [root, action] if root == "sandbox" && action == "plan" => Ok(Some(NativeExecution {
            value: direct_plan(arguments, project, "plan")?,
            exit_code: 0,
        })),
        [root, action] if root == "sandbox" && action == "execute" => {
            let (value, exit_code) = direct_execute(arguments, project, state_root)?;
            Ok(Some(NativeExecution { value, exit_code }))
        }
        [root, action] if root == "run" && action == "sandbox-status" => {
            Ok(Some(NativeExecution {
                value: platform_status(project)?,
                exit_code: 0,
            }))
        }
        [root, action] if root == "run" && action == "sandbox-run" => {
            let (value, exit_code) = platform_run(arguments, project, state_root)?;
            Ok(Some(NativeExecution { value, exit_code }))
        }
        _ => Ok(None),
    }
}
