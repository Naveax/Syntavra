#![forbid(unsafe_code)]

use std::env;
use std::fs;
use std::io::ErrorKind;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode, Stdio};

use serde_json::{json, Value};

#[path = "../native_product.rs"]
mod native_product;

const PRODUCT_VERSION: &str = "0.0.1";
const RELEASE_CHANNEL: &str = "pre-release";
const PUBLIC_COMMAND_COUNT: u64 = 245;
const NATIVE_COMMAND_COUNT: u64 = 170;

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

fn command_path(arguments: &[String]) -> Vec<String> {
    let mut positional = Vec::new();
    let mut index = 0usize;
    while index < arguments.len() {
        let value = &arguments[index];
        if matches!(
            value.as_str(),
            "--project"
                | "--state-root"
                | "--skill-root"
                | "--host"
                | "--mcp-profile"
                | "--budget"
                | "--max-tier"
                | "--codex-home"
                | "--rollout"
                | "--state-file"
                | "--session-hint"
        ) {
            index += 2;
            continue;
        }
        if value.starts_with("--project=")
            || value.starts_with("--state-root=")
            || value.starts_with("--skill-root=")
            || value.starts_with("--host=")
            || value.starts_with("--mcp-profile=")
            || value.starts_with("--codex-home=")
            || value.starts_with("--rollout=")
            || value.starts_with("--state-file=")
            || value.starts_with("--session-hint=")
            || value.starts_with("--budget=")
            || value.starts_with("--max-tier=")
        {
            index += 1;
            continue;
        }
        if value.starts_with('-') {
            index += 1;
            continue;
        }
        positional.push(value.clone());
        index += 1;
    }
    if matches!(
        positional.first().map(String::as_str),
        Some("rollout-tail" | "context-stress" | "claim" | "context" | "init" | "hook" | "mcp")
    ) {
        positional.truncate(1);
    } else if positional.first().map(String::as_str) == Some("engine")
        && positional.get(1).map(String::as_str) == Some("route")
    {
        positional.truncate(3);
    } else {
        positional.truncate(2);
    }
    positional
}

fn capability_missing_positionals(
    arguments: &[String],
    path: &[String],
) -> Option<(&'static str, usize)> {
    let (action, required) = match path {
        [root, action] if root == "run" && action == "capability-decide" => {
            ("capability-decide", 2usize)
        }
        [root, action] if root == "run" && action == "capability-issue" => {
            ("capability-issue", 3usize)
        }
        [root, action] if root == "run" && action == "capability-verify" => {
            ("capability-verify", 3usize)
        }
        _ => return None,
    };
    let action_index = arguments.iter().position(|value| value == action)?;
    let mut count = 0usize;
    let mut index = action_index + 1;
    while index < arguments.len() {
        let value = &arguments[index];
        if matches!(
            value.as_str(),
            "--resource" | "--network-host" | "--permission" | "--ttl"
        ) {
            index = (index + 2).min(arguments.len());
            continue;
        }
        if value.starts_with("--resource=")
            || value.starts_with("--network-host=")
            || value.starts_with("--permission=")
            || value.starts_with("--ttl=")
            || matches!(
                value.as_str(),
                "--sandboxed" | "--user-authorized" | "--no-consume"
            )
        {
            index += 1;
            continue;
        }
        if value.starts_with('-') {
            index += 1;
            continue;
        }
        count += 1;
        index += 1;
    }
    (count < required).then_some((action, required - count))
}

fn capability_parser_error(arguments: &[String], path: &[String]) -> Option<ExitCode> {
    let (action, missing) = capability_missing_positionals(arguments, path)?;
    eprintln!("usage: syntavra run {action} [options] ...");
    eprintln!("syntavra run {action}: error: missing {missing} required positional argument(s)");
    Some(ExitCode::from(2))
}

fn python_public_error(path: &[String], error: &str) -> String {
    if matches!(path, [root, action] if root == "provider" && matches!(action.as_str(), "capabilities" | "prepare"))
    {
        if let Some(provider) = error.strip_prefix("PROVIDER_UNSUPPORTED:") {
            return format!("ValueError: unsupported provider: {provider}");
        }
        if error.starts_with("unsupported provider:")
            || error.starts_with("credential field is transport-only:")
        {
            return format!("ValueError: {error}");
        }
    }
    if matches!(path, [root, action] if root == "run" && action == "provider-pool")
        && error.starts_with("credential_ref must be a non-secret")
    {
        return format!("ValueError: {error}");
    }
    error.to_owned()
}

fn executable_exists(path: &Path) -> bool {
    path.is_file()
}

fn discover_selector_dir() -> PathBuf {
    env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(Path::to_path_buf))
        .unwrap_or_else(|| lexical_absolute("."))
}

fn discover_python_program() -> Option<Program> {
    if let Ok(value) = env::var("SYNTAVRA_PYTHON_BIN") {
        let path = lexical_absolute(value);
        if executable_exists(&path) {
            return Some(Program {
                executable: path,
                prefix: vec!["-m".to_owned(), "syntavra_runtime.engine_entry".to_owned()],
            });
        }
    }
    let directory = discover_selector_dir();
    let suffix = env::consts::EXE_SUFFIX;
    let compatibility = directory.join(format!("syntavra-python{suffix}"));
    if executable_exists(&compatibility) {
        return Some(Program {
            executable: compatibility,
            prefix: Vec::new(),
        });
    }
    for name in ["python3", "python"] {
        if let Ok(path) = which(name) {
            return Some(Program {
                executable: path,
                prefix: vec!["-m".to_owned(), "syntavra_runtime.engine_entry".to_owned()],
            });
        }
    }
    None
}

fn which(name: &str) -> Result<PathBuf, std::io::Error> {
    let path = env::var_os("PATH").unwrap_or_default();
    let suffixes: Vec<&str> = if cfg!(windows) {
        vec![".exe", ".cmd", ".bat", ""]
    } else {
        vec![""]
    };
    for directory in env::split_paths(&path) {
        for suffix in &suffixes {
            let candidate = directory.join(format!("{name}{suffix}"));
            match fs::metadata(&candidate) {
                Ok(metadata) if metadata.is_file() => return Ok(candidate),
                Ok(_) => {}
                Err(error) if error.kind() == ErrorKind::NotFound => {}
                Err(error) => return Err(error),
            }
        }
    }
    Err(std::io::Error::new(ErrorKind::NotFound, name.to_owned()))
}

fn read_engine_file(path: &Path) -> Option<Engine> {
    let value = fs::read_to_string(path).ok()?;
    let parsed: Value = serde_json::from_str(&value).ok()?;
    parsed
        .get("engine")
        .and_then(Value::as_str)
        .and_then(|value| Engine::parse(value).ok())
}

fn resolve_engine(parsed: &Parsed) -> (Engine, &'static str) {
    if let Some(value) = parsed.engine_override {
        return (value, "command");
    }
    if let Ok(value) = env::var("SYNTAVRA_ENGINE") {
        if let Ok(engine) = Engine::parse(&value) {
            return (engine, "environment");
        }
    }
    if let Some(value) =
        read_engine_file(&parsed.project_root.join(".syntavra").join("engine.json"))
    {
        return (value, "project");
    }
    if let Some(home) = home_dir() {
        if let Some(value) = read_engine_file(&home.join(".syntavra").join("engine.json")) {
            return (value, "user");
        }
    }
    (Engine::Auto, "default")
}

fn home_dir() -> Option<PathBuf> {
    env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" }).map(PathBuf::from)
}

fn engine_status(parsed: &Parsed) -> Value {
    let (selected, source) = resolve_engine(parsed);
    json!({
        "product": "Syntavra",
        "version": PRODUCT_VERSION,
        "channel": RELEASE_CHANNEL,
        "selector": "native-rust",
        "selected": selected.as_str(),
        "source": source,
        "fallback": "forbidden",
        "engines": {
            "python": {
                "available": discover_python_program().is_some(),
                "independent": true,
            },
            "rust": {
                "available": true,
                "independent": true,
                "native_public_commands": NATIVE_COMMAND_COUNT,
                "missing_public_commands": PUBLIC_COMMAND_COUNT - NATIVE_COMMAND_COUNT,
            }
        }
    })
}

fn use_engine(parsed: &Parsed, scope: &str, engine: Engine) -> Result<Value, String> {
    let path = match scope {
        "project" => parsed.project_root.join(".syntavra").join("engine.json"),
        "user" => home_dir()
            .ok_or_else(|| "USER_HOME_UNAVAILABLE".to_owned())?
            .join(".syntavra")
            .join("engine.json"),
        _ => return Err("ENGINE_SCOPE_INVALID".to_owned()),
    };
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("ENGINE_SCOPE_CREATE_FAILED:{error}"))?;
    }
    let payload = json!({
        "engine": engine.as_str(),
        "scope": scope,
        "version": 1,
    });
    fs::write(
        &path,
        format!(
            "{}\n",
            serde_json::to_string_pretty(&payload)
                .map_err(|error| format!("ENGINE_SCOPE_SERIALIZE_FAILED:{error}"))?
        ),
    )
    .map_err(|error| format!("ENGINE_SCOPE_WRITE_FAILED:{error}"))?;
    Ok(json!({"ok": true, "path": path, "selection": payload}))
}

fn verify_engine(engine: Engine) -> Value {
    let available = match engine {
        Engine::Auto | Engine::Python => discover_python_program().is_some(),
        Engine::Rust => true,
    };
    json!({
        "ok": available,
        "engine": engine.as_str(),
        "available": available,
        "fallback": "forbidden",
    })
}

fn engine_command(parsed: &Parsed) -> Option<Result<Value, String>> {
    if parsed.forwarded.first().map(String::as_str) != Some("engine") {
        return None;
    }
    let action = parsed.forwarded.get(1).map_or("status", String::as_str);
    if action == "route" {
        return None;
    }
    let result = match action {
        "status" | "list" => Ok(engine_status(parsed)),
        "verify" => match parsed.forwarded.get(2) {
            Some(value) => Engine::parse(value).map(verify_engine),
            None => Ok(engine_status(parsed)),
        },
        "use" => {
            let scope = parsed
                .forwarded
                .iter()
                .position(|value| value == "--scope")
                .and_then(|index| parsed.forwarded.get(index + 1))
                .map_or("project", String::as_str);
            parsed
                .forwarded
                .get(2)
                .ok_or_else(|| "ENGINE_USE_MISSING_ENGINE".to_owned())
                .and_then(|value| Engine::parse(value))
                .and_then(|engine| use_engine(parsed, scope, engine))
        }
        _ => Err("ENGINE_MANAGEMENT_ACTION_INVALID".to_owned()),
    };
    Some(result)
}

fn execute_program(
    program: &Program,
    forwarded: &[String],
    engine: Engine,
) -> Result<ExitCode, String> {
    let mut command = Command::new(&program.executable);
    if engine == Engine::Python {
        command
            .env("PYTHONIOENCODING", "utf-8")
            .env("PYTHONUTF8", "1");
    }
    command
        .args(&program.prefix)
        .arg("--engine")
        .arg(engine.as_str())
        .args(forwarded)
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());
    let status = command
        .status()
        .map_err(|error| format!("ENGINE_EXECUTION_FAILED:{error}"))?;
    let code = status
        .code()
        .and_then(|value| u8::try_from(value).ok())
        .unwrap_or(1);
    Ok(ExitCode::from(code))
}

fn run_selected(parsed: &Parsed, selected: Engine) -> ExitCode {
    let path = command_path(&parsed.forwarded);
    match selected {
        Engine::Rust => {
            if let Some(exit) = capability_parser_error(&parsed.forwarded, &path) {
                return exit;
            }
            if native_product::supports(&path) {
                match native_product::execute(&path, &parsed.project_root, &parsed.state_root) {
                    Ok(Some(value)) => {
                        let blocked_agent = value["state"].as_str() == Some("blocked")
                            && matches!(path.as_slice(), [root, action] if
                                (root == "run" && action == "agent-execute")
                                    || (root == "agent" && action == "replay"));
                        let integrity_failure = value["ok"].as_bool() == Some(false)
                            && matches!(path.as_slice(), [root, action] if
                                root == "run" && action == "memory-verify");
                        let proxy_service_verify_failure = value["ok"].as_bool() == Some(false)
                            && matches!(path.as_slice(), [root, action] if
                                root == "run" && action == "proxy-service")
                            && parsed
                                .forwarded
                                .windows(2)
                                .any(|items| items[0] == "proxy-service" && items[1] == "verify");
                        emit(&value);
                        if blocked_agent || integrity_failure || proxy_service_verify_failure {
                            ExitCode::from(3)
                        } else {
                            ExitCode::SUCCESS
                        }
                    }
                    Ok(None) => fail(
                        "RUST_PUBLIC_COMMAND_NOT_IMPLEMENTED",
                        "The selected Rust engine does not implement this public command.",
                        json!({"command_path": path, "fallback": "forbidden"}),
                    ),
                    Err(error) => {
                        let error = python_public_error(&path, &error);
                        fail(
                            "RUST_PUBLIC_COMMAND_FAILED",
                            "The selected Rust engine failed while executing the public command.",
                            json!({"command_path": path, "error": error, "fallback": "forbidden"}),
                        )
                    }
                }
            } else {
                fail(
                    "RUST_PUBLIC_COMMAND_NOT_IMPLEMENTED",
                    "The selected Rust engine does not implement this public command.",
                    json!({"command_path": path, "fallback": "forbidden"}),
                )
            }
        }
        Engine::Python => match discover_python_program() {
            Some(program) => execute_program(&program, &parsed.forwarded, Engine::Python)
                .unwrap_or_else(|error| {
                    fail(
                        "PYTHON_ENGINE_EXECUTION_FAILED",
                        "The selected Python engine could not be executed.",
                        json!({"error": error, "fallback": "forbidden"}),
                    )
                }),
            None => fail(
                "PYTHON_ENGINE_NOT_AVAILABLE",
                "The selected Python engine is not available.",
                json!({"fallback": "forbidden"}),
            ),
        },
        Engine::Auto => {
            if matches!(path.as_slice(), [engine, route, ..] if engine == "engine" && route == "route")
            {
                return match discover_python_program() {
                    Some(program) => execute_program(&program, &parsed.forwarded, Engine::Python)
                        .unwrap_or_else(|error| {
                            fail(
                                "AUTO_ENGINE_EXECUTION_FAILED",
                                "The automatic engine policy could not execute the selected Python engine.",
                                json!({"error": error, "fallback": "forbidden"}),
                            )
                        }),
                    None => fail(
                        "AUTO_ENGINE_NOT_AVAILABLE",
                        "The automatic engine policy has no available engine for this command.",
                        json!({"command_path": path, "fallback": "forbidden"}),
                    ),
                };
            }
            if native_product::supports(&path) {
                run_selected(parsed, Engine::Rust)
            } else {
                match discover_python_program() {
                    Some(program) => execute_program(&program, &parsed.forwarded, Engine::Python)
                        .unwrap_or_else(|error| {
                            fail(
                                "AUTO_ENGINE_EXECUTION_FAILED",
                                "The automatic engine policy could not execute the selected Python engine.",
                                json!({"error": error, "fallback": "forbidden"}),
                            )
                        }),
                    None => fail(
                        "AUTO_ENGINE_NOT_AVAILABLE",
                        "The automatic engine policy has no available engine for this command.",
                        json!({"command_path": path, "fallback": "forbidden"}),
                    ),
                }
            }
        }
    }
}

fn main() -> ExitCode {
    let arguments = env::args().skip(1).collect::<Vec<_>>();
    let parsed = match parse_arguments(&arguments) {
        Ok(value) => value,
        Err(error) => {
            return fail(
                "SELECTOR_ARGUMENT_INVALID",
                "The native selector could not parse the command arguments.",
                json!({"error": error}),
            );
        }
    };
    if let Some(result) = engine_command(&parsed) {
        return match result {
            Ok(value) => {
                emit(&value);
                ExitCode::SUCCESS
            }
            Err(error) => fail(
                "ENGINE_MANAGEMENT_FAILED",
                "The engine management command failed.",
                json!({"error": error}),
            ),
        };
    }
    let (selected, _) = resolve_engine(&parsed);
    run_selected(&parsed, selected)
}

#[cfg(test)]
mod tests {
    use super::{
        capability_missing_positionals, command_path, engine_command, parse_arguments,
        python_public_error, Engine,
    };

    #[test]
    fn parses_command_override() {
        let parsed = parse_arguments(&[
            "--engine".to_owned(),
            "rust".to_owned(),
            "version".to_owned(),
        ])
        .expect("parse");
        assert_eq!(parsed.engine_override, Some(Engine::Rust));
        assert_eq!(parsed.forwarded, vec!["version"]);
    }

    #[test]
    fn command_path_ignores_global_paths() {
        let path = command_path(&[
            "--project".to_owned(),
            "repo".to_owned(),
            "run".to_owned(),
            "cache-health".to_owned(),
        ]);
        assert_eq!(path, vec!["run", "cache-health"]);
    }

    #[test]
    fn capability_parser_counts_required_positionals_without_option_values() {
        let missing = capability_missing_positionals(
            &[
                "run".to_owned(),
                "capability-decide".to_owned(),
                "repo.read".to_owned(),
                "--resource".to_owned(),
                "workspace:/module.py".to_owned(),
            ],
            &["run".to_owned(), "capability-decide".to_owned()],
        );
        assert_eq!(missing, Some(("capability-decide", 1)));

        let complete = capability_missing_positionals(
            &[
                "run".to_owned(),
                "capability-decide".to_owned(),
                "repo.read".to_owned(),
                "{\"path\":\"module.py\"}".to_owned(),
                "--resource=workspace:/module.py".to_owned(),
            ],
            &["run".to_owned(), "capability-decide".to_owned()],
        );
        assert_eq!(complete, None);
    }

    #[test]
    fn provider_public_errors_match_python_exception_strings() {
        assert_eq!(
            python_public_error(
                &["provider".to_owned(), "capabilities".to_owned()],
                "PROVIDER_UNSUPPORTED:not-a-provider",
            ),
            "ValueError: unsupported provider: not-a-provider",
        );
        assert_eq!(
            python_public_error(
                &["provider".to_owned(), "prepare".to_owned()],
                "credential field is transport-only: request.authorization",
            ),
            "ValueError: credential field is transport-only: request.authorization",
        );
        assert_eq!(
            python_public_error(
                &["run".to_owned(), "provider-pool".to_owned()],
                "credential_ref must be a non-secret env/file/keyring/oauth-profile reference",
            ),
            "ValueError: credential_ref must be a non-secret env/file/keyring/oauth-profile reference",
        );
    }

    #[test]
    fn aggregate_engine_verify_returns_status() {
        let parsed = parse_arguments(&["engine".to_owned(), "verify".to_owned()]).expect("parse");
        let result = engine_command(&parsed)
            .expect("engine command")
            .expect("engine status");
        assert_eq!(result["selector"], "native-rust");
        assert_eq!(result["fallback"], "forbidden");
        assert_eq!(result["engines"]["rust"]["available"], true);
    }
}
