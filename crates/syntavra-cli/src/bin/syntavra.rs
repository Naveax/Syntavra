#![forbid(unsafe_code)]

use std::env;
use std::fs;
use std::io::ErrorKind;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode, Stdio};

use serde_json::{json, Value};

const PRODUCT_VERSION: &str = "0.0.1";
const RELEASE_CHANNEL: &str = "pre-release";
const PUBLIC_COMMAND_COUNT: u64 = 257;
const NATIVE_COMMAND_COUNT: u64 = 11;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Engine {
    Auto,
    Python,
    Rust,
}

impl Engine {
    fn parse(value: &str) -> Result<Self, String> {
        match value.trim().to_ascii_lowercase().as_str() {
            "auto" => Ok(Self::Auto),
            "python" => Ok(Self::Python),
            "rust" => Ok(Self::Rust),
            _ => Err("ENGINE_SELECTION_INVALID".to_owned()),
        }
    }

    const fn as_str(self) -> &'static str {
        match self {
            Self::Auto => "auto",
            Self::Python => "python",
            Self::Rust => "rust",
        }
    }
}

#[derive(Debug)]
struct Parsed {
    engine_override: Option<Engine>,
    forwarded: Vec<String>,
    project_root: PathBuf,
    state_root: PathBuf,
}

#[derive(Debug, Clone)]
struct Program {
    executable: PathBuf,
    prefix: Vec<String>,
}

fn emit(value: &Value) {
    println!(
        "{}",
        serde_json::to_string_pretty(value).unwrap_or_else(|_| "{\"ok\":false}".to_owned())
    );
}

fn fail(code: &str, message: &str, details: impl Into<Value>) -> ExitCode {
    let details = details.into();
    emit(&json!({
        "ok": false,
        "error": {
            "code": code,
            "message": message,
            "details": details,
        }
    }));
    ExitCode::from(4)
}

fn lexical_absolute(value: impl AsRef<Path>) -> PathBuf {
    let path = value.as_ref();
    if path.is_absolute() {
        path.to_path_buf()
    } else {
        env::current_dir()
            .unwrap_or_else(|_| PathBuf::from("."))
            .join(path)
    }
}

fn parse_arguments(arguments: &[String]) -> Result<Parsed, String> {
    let mut engine_override = None;
    let mut forwarded = Vec::with_capacity(arguments.len());
    let mut project_root = lexical_absolute(".");
    let mut state_root = None;
    let mut index = 0usize;
    while index < arguments.len() {
        let value = &arguments[index];
        if value == "--engine" {
            let selected = arguments
                .get(index + 1)
                .ok_or_else(|| "ENGINE_OVERRIDE_MISSING_VALUE".to_owned())?;
            if engine_override.is_some() {
                return Err("ENGINE_OVERRIDE_DUPLICATE".to_owned());
            }
            engine_override = Some(Engine::parse(selected)?);
            index += 2;
            continue;
        }
        if let Some(selected) = value.strip_prefix("--engine=") {
            if engine_override.is_some() {
                return Err("ENGINE_OVERRIDE_DUPLICATE".to_owned());
            }
            engine_override = Some(Engine::parse(selected)?);
            index += 1;
            continue;
        }
        if value == "--project" {
            let selected = arguments
                .get(index + 1)
                .ok_or_else(|| "PROJECT_ARGUMENT_MISSING".to_owned())?;
            project_root = lexical_absolute(selected);
            forwarded.push(value.clone());
            forwarded.push(selected.clone());
            index += 2;
            continue;
        }
        if let Some(selected) = value.strip_prefix("--project=") {
            project_root = lexical_absolute(selected);
            forwarded.push(value.clone());
            index += 1;
            continue;
        }
        if value == "--state-root" {
            let selected = arguments
                .get(index + 1)
                .ok_or_else(|| "STATE_ROOT_ARGUMENT_MISSING".to_owned())?;
            state_root = Some(lexical_absolute(selected));
            forwarded.push(value.clone());
            forwarded.push(selected.clone());
            index += 2;
            continue;
        }
        if let Some(selected) = value.strip_prefix("--state-root=") {
            state_root = Some(lexical_absolute(selected));
            forwarded.push(value.clone());
            index += 1;
            continue;
        }
        forwarded.push(value.clone());
        index += 1;
    }
    let state_root =
        state_root.unwrap_or_else(|| project_root.join(".syntavra").join("pre-release"));
    Ok(Parsed {
        engine_override,
        forwarded,
        project_root,
        state_root,
    })
}

fn command_tokens(arguments: &[String]) -> Vec<String> {
    let options_with_values = [
        "--project",
        "--state-root",
        "--skill-root",
        "--codex-home",
        "--host",
    ];
    let mut output = Vec::new();
    let mut index = 0usize;
    while index < arguments.len() {
        let value = &arguments[index];
        if options_with_values.contains(&value.as_str()) {
            index += 2;
            continue;
        }
        if value.starts_with("--project=")
            || value.starts_with("--state-root=")
            || value.starts_with("--skill-root=")
            || value.starts_with("--codex-home=")
            || value.starts_with("--host=")
            || value == "--json"
        {
            index += 1;
            continue;
        }
        if value.starts_with('-') {
            index += 1;
            continue;
        }
        output.push(value.clone());
        if output.len() == 2 {
            break;
        }
        index += 1;
    }
    output
}

fn project_engine_path(project_root: &Path) -> PathBuf {
    project_root.join(".syntavra").join("engine.json")
}

fn user_engine_path() -> PathBuf {
    if cfg!(windows) {
        if let Some(value) = env::var_os("APPDATA") {
            return PathBuf::from(value).join("Syntavra").join("engine.json");
        }
    }
    if let Some(value) = env::var_os("XDG_CONFIG_HOME") {
        return PathBuf::from(value).join("syntavra").join("engine.json");
    }
    let home = env::var_os("HOME")
        .or_else(|| env::var_os("USERPROFILE"))
        .map_or_else(|| PathBuf::from("."), PathBuf::from);
    home.join(".config").join("syntavra").join("engine.json")
}

fn read_engine(path: &Path) -> Result<Option<Engine>, String> {
    let raw = match fs::read(path) {
        Ok(value) => value,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err("ENGINE_CONFIG_READ_FAILED".to_owned()),
    };
    if raw.len() > 4096 {
        return Err("ENGINE_CONFIG_TOO_LARGE".to_owned());
    }
    let value: Value =
        serde_json::from_slice(&raw).map_err(|_| "ENGINE_CONFIG_INVALID".to_owned())?;
    if value.get("schema_version").and_then(Value::as_u64) != Some(1) {
        return Err("ENGINE_CONFIG_SCHEMA_UNSUPPORTED".to_owned());
    }
    let engine = value
        .get("engine")
        .and_then(Value::as_str)
        .ok_or_else(|| "ENGINE_CONFIG_INVALID".to_owned())?;
    Engine::parse(engine).map(Some)
}

fn persist_engine(path: &Path, engine: Engine) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "ENGINE_CONFIG_PARENT_INVALID".to_owned())?;
    fs::create_dir_all(parent).map_err(|_| "ENGINE_CONFIG_CREATE_FAILED".to_owned())?;
    let temporary = parent.join(".engine.json.tmp");
    let payload = serde_json::to_vec_pretty(&json!({
        "schema_version": 1,
        "engine": engine.as_str(),
    }))
    .map_err(|_| "ENGINE_CONFIG_RENDER_FAILED".to_owned())?;
    fs::write(&temporary, payload).map_err(|_| "ENGINE_CONFIG_WRITE_FAILED".to_owned())?;
    fs::rename(&temporary, path).map_err(|_| "ENGINE_CONFIG_REPLACE_FAILED".to_owned())?;
    Ok(())
}

fn selected_engine(parsed: &Parsed) -> Result<(Engine, &'static str), String> {
    if let Some(engine) = parsed.engine_override {
        return Ok((engine, "command"));
    }
    if let Ok(value) = env::var("SYNTAVRA_ENGINE") {
        if !value.trim().is_empty() {
            return Ok((Engine::parse(&value)?, "environment"));
        }
    }
    if let Some(engine) = read_engine(&project_engine_path(&parsed.project_root))? {
        return Ok((engine, "project"));
    }
    if let Some(engine) = read_engine(&user_engine_path())? {
        return Ok((engine, "user"));
    }
    Ok((Engine::Auto, "builtin"))
}

fn sibling_binary(name: &str) -> Option<PathBuf> {
    let executable = env::current_exe().ok()?;
    let directory = executable.parent()?;
    let filename = if cfg!(windows) {
        format!("{name}.exe")
    } else {
        name.to_owned()
    };
    let candidate = directory.join(filename);
    candidate.is_file().then_some(candidate)
}

fn rust_program() -> Program {
    if let Some(value) = env::var_os("SYNTAVRA_RUST_BIN") {
        let candidate = PathBuf::from(value);
        if candidate.is_file() {
            return Program {
                executable: candidate,
                prefix: Vec::new(),
            };
        }
    }
    if let Some(candidate) = sibling_binary("syntavra-rs") {
        return Program {
            executable: candidate,
            prefix: Vec::new(),
        };
    }
    Program {
        executable: PathBuf::from(if cfg!(windows) {
            "syntavra-rs.exe"
        } else {
            "syntavra-rs"
        }),
        prefix: Vec::new(),
    }
}

fn program_works(program: &Program, probe: &[&str]) -> bool {
    let mut command = Command::new(&program.executable);
    command.args(&program.prefix).args(probe);
    command
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|status| status.success())
}

fn python_program() -> Option<Program> {
    if let Some(value) = env::var_os("SYNTAVRA_PYTHON") {
        let program = Program {
            executable: PathBuf::from(value),
            prefix: Vec::new(),
        };
        if program_works(&program, &["--version"]) {
            return Some(program);
        }
    }
    let candidates: &[(&str, &[&str])] = if cfg!(windows) {
        &[("py", &["-3"]), ("python", &[])]
    } else {
        &[("python3", &[]), ("python", &[])]
    };
    for (executable, prefix) in candidates {
        let program = Program {
            executable: PathBuf::from(executable),
            prefix: prefix.iter().map(|value| (*value).to_owned()).collect(),
        };
        if program_works(&program, &["--version"]) {
            return Some(program);
        }
    }
    None
}

fn exit_code(status: std::process::ExitStatus) -> ExitCode {
    let value = status
        .code()
        .and_then(|code| u8::try_from(code).ok())
        .unwrap_or(1);
    ExitCode::from(value)
}

fn execute(program: &Program, arguments: &[String]) -> Result<ExitCode, String> {
    let status = Command::new(&program.executable)
        .args(&program.prefix)
        .args(arguments)
        .status()
        .map_err(|_| "ENGINE_EXECUTION_FAILED".to_owned())?;
    Ok(exit_code(status))
}

fn encode_hex(value: &[u8]) -> String {
    const TABLE: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(value.len() * 2);
    for byte in value {
        output.push(char::from(TABLE[usize::from(byte >> 4)]));
        output.push(char::from(TABLE[usize::from(byte & 0x0f)]));
    }
    output
}

fn parse_scheduler_list(arguments: &[String]) -> Result<(u64, Vec<String>), String> {
    let mut limit = 100u64;
    let mut states = Vec::new();
    let mut index = 0usize;
    while index < arguments.len() {
        let value = &arguments[index];
        if value == "--state" {
            let state = arguments
                .get(index + 1)
                .ok_or_else(|| "SCHEDULER_STATE_MISSING".to_owned())?;
            states.push(state.clone());
            index += 2;
            continue;
        }
        if let Some(state) = value.strip_prefix("--state=") {
            states.push(state.to_owned());
            index += 1;
            continue;
        }
        if value == "--limit" {
            let raw = arguments
                .get(index + 1)
                .ok_or_else(|| "SCHEDULER_LIMIT_MISSING".to_owned())?;
            limit = raw
                .parse()
                .map_err(|_| "SCHEDULER_LIMIT_INVALID".to_owned())?;
            index += 2;
            continue;
        }
        if let Some(raw) = value.strip_prefix("--limit=") {
            limit = raw
                .parse()
                .map_err(|_| "SCHEDULER_LIMIT_INVALID".to_owned())?;
            index += 1;
            continue;
        }
        index += 1;
    }
    Ok((limit, states))
}

fn value_after_command(arguments: &[String], command: &str, action: &str) -> Option<String> {
    let mut tokens = arguments.iter();
    while let Some(value) = tokens.next() {
        if value == command && tokens.next().is_some_and(|next| next == action) {
            return tokens
                .find(|item| !item.starts_with('-'))
                .map(ToOwned::to_owned);
        }
    }
    None
}

fn translate_rust(parsed: &Parsed) -> Result<Option<Vec<String>>, String> {
    let tokens = command_tokens(&parsed.forwarded);
    let key = tokens.join(" ");
    let translated = match key.as_str() {
        "version" => vec!["version".to_owned()],
        "pipeline describe" => vec!["pipeline".to_owned(), "describe".to_owned()],
        "plugins list" => vec!["plugins".to_owned(), "list".to_owned()],
        "telemetry metrics" => {
            let format = if parsed.forwarded.iter().any(|value| value == "--prometheus") {
                "prometheus"
            } else {
                "json"
            };
            vec![
                "telemetry".to_owned(),
                "metrics".to_owned(),
                format.to_owned(),
            ]
        }
        "scheduler stats" => vec![
            "scheduler".to_owned(),
            "stats".to_owned(),
            parsed.state_root.to_string_lossy().into_owned(),
        ],
        "scheduler list" => {
            let (limit, states) = parse_scheduler_list(&parsed.forwarded)?;
            let states_json =
                serde_json::to_vec(&states).map_err(|_| "SCHEDULER_STATES_INVALID".to_owned())?;
            vec![
                "scheduler".to_owned(),
                "list".to_owned(),
                parsed.state_root.to_string_lossy().into_owned(),
                limit.to_string(),
                encode_hex(&states_json),
            ]
        }
        "migrate plan" => {
            let database = value_after_command(&parsed.forwarded, "migrate", "plan")
                .ok_or_else(|| "MIGRATION_DATABASE_MISSING".to_owned())?;
            vec![
                "migration".to_owned(),
                "plan".to_owned(),
                parsed.project_root.to_string_lossy().into_owned(),
                encode_hex(database.as_bytes()),
            ]
        }
        _ => return Ok(None),
    };
    Ok(Some(translated))
}

fn run_python(parsed: &Parsed) -> Result<ExitCode, String> {
    let program = python_program().ok_or_else(|| "PYTHON_ENGINE_NOT_FOUND".to_owned())?;
    let mut arguments = vec![
        "-m".to_owned(),
        "syntavra_runtime.engine_entry".to_owned(),
        "--engine".to_owned(),
        "python".to_owned(),
    ];
    arguments.extend(parsed.forwarded.iter().cloned());
    execute(&program, &arguments)
}

fn run_rust(parsed: &Parsed) -> Result<ExitCode, String> {
    let arguments = translate_rust(parsed)?.ok_or_else(|| {
        format!(
            "RUST_PUBLIC_COMMAND_NOT_IMPLEMENTED:{}",
            command_tokens(&parsed.forwarded).join(" ")
        )
    })?;
    execute(&rust_program(), &arguments)
}

fn engine_management(parsed: &Parsed) -> Result<Option<ExitCode>, String> {
    let tokens = command_tokens(&parsed.forwarded);
    if tokens.first().map(String::as_str) != Some("engine") {
        return Ok(None);
    }
    match tokens.get(1).map(String::as_str) {
        Some("use") => {
            let position = parsed
                .forwarded
                .iter()
                .position(|value| value == "use")
                .ok_or_else(|| "ENGINE_USE_INVALID".to_owned())?;
            let engine = parsed
                .forwarded
                .get(position + 1)
                .ok_or_else(|| "ENGINE_USE_MISSING".to_owned())
                .and_then(|value| Engine::parse(value))?;
            let scope = parsed
                .forwarded
                .windows(2)
                .find(|window| window[0] == "--scope")
                .map(|window| window[1].as_str())
                .or_else(|| {
                    parsed
                        .forwarded
                        .iter()
                        .find_map(|value| value.strip_prefix("--scope="))
                })
                .unwrap_or("project");
            let path = match scope {
                "project" => project_engine_path(&parsed.project_root),
                "user" => user_engine_path(),
                _ => return Err("ENGINE_SCOPE_INVALID".to_owned()),
            };
            persist_engine(&path, engine)?;
            emit(&json!({
                "ok": true,
                "persisted": {
                    "engine": engine.as_str(),
                    "scope": scope,
                    "path": path,
                }
            }));
            Ok(Some(ExitCode::SUCCESS))
        }
        Some("list" | "status" | "verify") => {
            let (selected, source) = selected_engine(parsed)?;
            let python = python_program();
            let rust = rust_program();
            let rust_available = program_works(&rust, &["version"]);
            let value = json!({
                "ok": python.is_some() && rust_available,
                "product": "Syntavra",
                "product_version": PRODUCT_VERSION,
                "release_channel": RELEASE_CHANNEL,
                "selection": {
                    "requested": selected.as_str(),
                    "source": source,
                    "auto_policy": "rust-for-native-command-else-python",
                    "fallback": "forbidden",
                },
                "engines": {
                    "python": {
                        "available": python.is_some(),
                        "independent": true,
                    },
                    "rust": {
                        "available": rust_available,
                        "independent": true,
                        "native_public_commands": NATIVE_COMMAND_COUNT,
                        "target_public_commands": PUBLIC_COMMAND_COUNT,
                        "full_dual_engine_parity": false,
                    }
                }
            });
            emit(&value);
            Ok(Some(if value["ok"] == Value::Bool(true) {
                ExitCode::SUCCESS
            } else {
                ExitCode::from(4)
            }))
        }
        _ => Ok(None),
    }
}

fn run(arguments: &[String]) -> ExitCode {
    let parsed = match parse_arguments(arguments) {
        Ok(value) => value,
        Err(code) => return fail(&code, "invalid launcher arguments", json!({})),
    };
    match engine_management(&parsed) {
        Ok(Some(code)) => return code,
        Ok(None) => {}
        Err(code) => return fail(&code, "engine management failed", json!({})),
    }
    let (requested, source) = match selected_engine(&parsed) {
        Ok(value) => value,
        Err(code) => return fail(&code, "engine selection failed", json!({})),
    };
    let resolved = if requested == Engine::Auto {
        match translate_rust(&parsed) {
            Ok(Some(_)) if program_works(&rust_program(), &["version"]) => Engine::Rust,
            Ok(_) => Engine::Python,
            Err(code) => return fail(&code, "auto routing failed", json!({})),
        }
    } else {
        requested
    };
    let result = match resolved {
        Engine::Python => run_python(&parsed),
        Engine::Rust => run_rust(&parsed),
        Engine::Auto => unreachable!(),
    };
    match result {
        Ok(code) => code,
        Err(code) => fail(
            &code,
            "selected engine could not execute the command",
            json!({
                "requested": requested.as_str(),
                "resolved": resolved.as_str(),
                "source": source,
                "fallback": "forbidden",
                "command": command_tokens(&parsed.forwarded).join(" "),
            }),
        ),
    }
}

fn main() -> ExitCode {
    run(&env::args().skip(1).collect::<Vec<_>>())
}

#[cfg(test)]
mod tests {
    use super::{command_tokens, parse_arguments, translate_rust, Engine};

    fn values(items: &[&str]) -> Vec<String> {
        items.iter().map(|value| (*value).to_owned()).collect()
    }

    #[test]
    fn engine_override_is_removed_from_forwarded_arguments() {
        let parsed = parse_arguments(&values(&[
            "--engine",
            "rust",
            "--project",
            "demo",
            "pipeline",
            "describe",
        ]))
        .expect("arguments");
        assert_eq!(parsed.engine_override, Some(Engine::Rust));
        assert_eq!(
            command_tokens(&parsed.forwarded),
            values(&["pipeline", "describe"])
        );
    }

    #[test]
    fn scheduler_translation_uses_state_root() {
        let parsed = parse_arguments(&values(&[
            "--state-root",
            "state",
            "scheduler",
            "list",
            "--state",
            "queued",
            "--limit",
            "7",
        ]))
        .expect("arguments");
        let translated = translate_rust(&parsed)
            .expect("translation")
            .expect("native route");
        assert_eq!(&translated[..2], values(&["scheduler", "list"]));
        assert_eq!(translated[3], "7");
    }

    #[test]
    fn unsupported_rust_command_is_not_silently_fallbacked() {
        let parsed = parse_arguments(&values(&["run", "rewrite", "--", "git", "status"]))
            .expect("arguments");
        assert!(translate_rust(&parsed).expect("translation").is_none());
    }
}
