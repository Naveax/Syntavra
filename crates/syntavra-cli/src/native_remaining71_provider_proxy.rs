#![forbid(unsafe_code)]
#![allow(clippy::too_many_lines)]

use std::collections::BTreeMap;
use std::env;
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use rand::{rngs::OsRng, RngCore as _};
use regex::Regex;
use serde_json::{json, Value};
use syntavra_core::sha256_hex;

use super::super::native_evidence_store::NativeEvidenceStore;

const VERSION: &str = "0.0.1";
const CHANNEL: &str = "pre-release";
const MAX_HEADER_BYTES: usize = 64 * 1024;
const DEFAULT_MAX_REQUEST_BYTES: usize = 16 * 1024 * 1024;
const DEFAULT_MAX_RESPONSE_BYTES: usize = 64 * 1024 * 1024;
const DEFAULT_CONTROL_TOKEN_ENV: &str = "SYNTAVRA_PROXY_CONTROL_TOKEN";
const SAFE_FORWARD_HEADERS: &[&str] = &[
    "accept",
    "content-type",
    "user-agent",
    "openai-beta",
    "openai-organization",
    "openai-project",
    "anthropic-version",
    "anthropic-beta",
    "x-goog-user-project",
    "x-request-id",
    "traceparent",
    "tracestate",
    "idempotency-key",
];
const CREDENTIAL_HEADERS: &[&str] = &[
    "authorization",
    "x-api-key",
    "api-key",
    "x-goog-api-key",
    "openai-api-key",
    "anthropic-api-key",
];

static REQUEST_SEQUENCE: AtomicU64 = AtomicU64::new(1);

#[derive(Debug, Clone)]
struct ProxyConfig {
    provider: String,
    upstream_base: String,
    listen_host: String,
    listen_port: u16,
    credential_env: String,
    credential_header: String,
    credential_prefix: String,
    control_token_env: String,
    allow_remote: bool,
    allow_insecure_upstream: bool,
    cache_policy: String,
    replay_ttl_seconds: i64,
    prompt_cache_ttl_seconds: i64,
    timeout_seconds: f64,
    max_request_bytes: usize,
    max_response_bytes: usize,
    dry_run: bool,
}

#[derive(Debug)]
struct HttpRequest {
    method: String,
    target: String,
    headers: BTreeMap<String, String>,
    body: Vec<u8>,
}

#[derive(Debug)]
struct UpstreamResponse {
    status: u16,
    content_type: String,
    body: Vec<u8>,
}

#[derive(Debug)]
struct TempFiles {
    paths: Vec<PathBuf>,
}

impl TempFiles {
    fn new() -> Self {
        Self { paths: Vec::new() }
    }

    fn push(&mut self, path: PathBuf) -> PathBuf {
        self.paths.push(path.clone());
        path
    }
}

impl Drop for TempFiles {
    fn drop(&mut self) {
        for path in self.paths.drain(..) {
            let _ = fs::remove_file(path);
        }
    }
}

pub(crate) fn execute(
    arguments: &[String],
    project: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    let config = parse_config(arguments)?;
    validate_config(&config)?;
    if config.dry_run {
        return Ok(json!({
            "ok": true,
            "config": config_json(&config),
        }));
    }
    run_server(config, project, state_root)
}

fn parse_config(arguments: &[String]) -> Result<ProxyConfig, String> {
    let provider = required_option(arguments, "--provider")?;
    let upstream_base = required_option(arguments, "--upstream")?;
    let listen_host = option_value(arguments, "--listen-host")?
        .unwrap_or_else(|| "127.0.0.1".to_owned());
    let listen_port = integer_option(arguments, "--listen-port", 8787)?;
    let listen_port = u16::try_from(listen_port).map_err(|_| "PROXY_LISTEN_PORT_INVALID".to_owned())?;
    let credential_env = option_value(arguments, "--credential-env")?.unwrap_or_default();
    let credential_header = option_value(arguments, "--credential-header")?.unwrap_or_default();
    let credential_prefix = option_value(arguments, "--credential-prefix")?.unwrap_or_default();
    let control_token_env = option_value(arguments, "--control-token-env")?
        .unwrap_or_else(|| DEFAULT_CONTROL_TOKEN_ENV.to_owned());
    let cache_policy = option_value(arguments, "--cache-policy")?.unwrap_or_else(|| "auto".to_owned());
    let replay_ttl_seconds = integer_option(arguments, "--replay-ttl-seconds", 900)?;
    let prompt_cache_ttl_seconds = integer_option(arguments, "--prompt-cache-ttl-seconds", 300)?;
    let timeout_seconds = float_option(arguments, "--timeout-seconds", 180.0)?;
    let max_request_bytes = usize::try_from(integer_option(
        arguments,
        "--max-request-bytes",
        i64::try_from(DEFAULT_MAX_REQUEST_BYTES).unwrap_or(i64::MAX),
    )?)
    .map_err(|_| "PROXY_MAX_REQUEST_BYTES_INVALID".to_owned())?;
    let max_response_bytes = usize::try_from(integer_option(
        arguments,
        "--max-response-bytes",
        i64::try_from(DEFAULT_MAX_RESPONSE_BYTES).unwrap_or(i64::MAX),
    )?)
    .map_err(|_| "PROXY_MAX_RESPONSE_BYTES_INVALID".to_owned())?;
    Ok(ProxyConfig {
        provider,
        upstream_base,
        listen_host,
        listen_port,
        credential_env,
        credential_header,
        credential_prefix,
        control_token_env,
        allow_remote: has_flag(arguments, "--allow-remote"),
        allow_insecure_upstream: has_flag(arguments, "--allow-insecure-upstream"),
        cache_policy,
        replay_ttl_seconds,
        prompt_cache_ttl_seconds,
        timeout_seconds,
        max_request_bytes,
        max_response_bytes,
        dry_run: has_flag(arguments, "--dry-run"),
    })
}

fn config_json(config: &ProxyConfig) -> Value {
    json!({
        "provider": config.provider,
        "upstream_base": config.upstream_base,
        "listen_host": config.listen_host,
        "listen_port": config.listen_port,
        "credential_env": config.credential_env,
        "credential_header": config.credential_header,
        "credential_prefix": config.credential_prefix,
        "control_token_env": config.control_token_env,
        "allow_remote": config.allow_remote,
        "allow_insecure_upstream": config.allow_insecure_upstream,
        "tls_cert_file": "",
        "tls_key_file": "",
        "cache_policy": config.cache_policy,
        "replay_ttl_seconds": config.replay_ttl_seconds,
        "prompt_cache_ttl_seconds": config.prompt_cache_ttl_seconds,
        "timeout_seconds": config.timeout_seconds,
        "max_request_bytes": config.max_request_bytes,
        "max_buffered_response_bytes": config.max_response_bytes,
        "spool_memory_bytes": 2 * 1024 * 1024,
        "default_anthropic_version": "2023-06-01",
        "stream_mode": "commit-before-forward",
        "max_concurrent_requests": 64,
        "drain_timeout_seconds": 30.0,
        "block_secret_outputs": true,
        "block_prompt_injection_outputs": true,
    })
}

fn validate_config(config: &ProxyConfig) -> Result<(), String> {
    if config.max_request_bytes < 1024 || config.max_response_bytes < 1024 {
        return Err("proxy byte limits must be at least 1024".to_owned());
    }
    if !config.timeout_seconds.is_finite() || config.timeout_seconds <= 0.0 {
        return Err("proxy timeouts must be positive".to_owned());
    }
    if !matches!(
        config.cache_policy.as_str(),
        "off" | "auto" | "read" | "read-write"
    ) {
        return Err("invalid cache_policy".to_owned());
    }
    if config.replay_ttl_seconds < 1 || config.prompt_cache_ttl_seconds < 0 {
        return Err("proxy cache TTL values are invalid".to_owned());
    }
    validate_upstream(&config.upstream_base, config.allow_insecure_upstream)?;
    if config.control_token_env.trim().is_empty() {
        return Err("control_token_env is mandatory even for loopback bindings".to_owned());
    }
    let loopback = matches!(config.listen_host.as_str(), "127.0.0.1" | "::1" | "localhost");
    if !loopback {
        if !config.allow_remote {
            return Err("non-loopback proxy binding requires allow_remote".to_owned());
        }
        // The public v0.0.1 CLI intentionally exposes no TLS key/certificate
        // arguments. Keep the Python fail-closed boundary: remote binding is
        // therefore not certifiable through this surface.
        return Err("remote proxy binding requires TLS certificate and key".to_owned());
    }
    if !config.credential_header.is_empty() {
        validate_header_name(&config.credential_header)?;
    }
    validate_header_value(&config.credential_prefix)?;
    Ok(())
}

fn validate_upstream(value: &str, allow_insecure: bool) -> Result<(), String> {
    let (scheme, rest) = value
        .split_once("://")
        .ok_or_else(|| "upstream must be an absolute HTTP origin".to_owned())?;
    if scheme != "https" && !(allow_insecure && scheme == "http") {
        return Err("upstream must use HTTPS unless allow_insecure_upstream is explicit".to_owned());
    }
    if rest.is_empty() || rest.starts_with('/') || rest.contains('?') || rest.contains('#') {
        return Err("upstream_base must be an origin or fixed base path without credentials/query/fragment".to_owned());
    }
    let authority = rest.split('/').next().unwrap_or_default();
    if authority.is_empty() || authority.contains('@') {
        return Err("upstream_base must not contain credentials".to_owned());
    }
    Ok(())
}

fn run_server(config: ProxyConfig, project: &Path, state_root: &Path) -> Result<Value, String> {
    fs::create_dir_all(state_root.join("provider-proxy-spool"))
        .map_err(|error| format!("PROXY_SPOOL_CREATE_FAILED:{error}"))?;
    let listener = TcpListener::bind((config.listen_host.as_str(), config.listen_port))
        .map_err(|error| format!("PROXY_BIND_FAILED:{error}"))?;
    let address = listener
        .local_addr()
        .map_err(|error| format!("PROXY_LOCAL_ADDRESS_FAILED:{error}"))?;
    let ready = json!({
        "event": "PROVIDER_PROXY_READY",
        "provider": canonical_provider(&config.provider),
        "listen": {"host": address.ip().to_string(), "port": address.port()},
        "upstream_origin_hash": sha256_hex(config.upstream_base.as_bytes()),
        "cache_policy": config.cache_policy,
        "stream_mode": "commit-before-forward",
    });
    println!(
        "{}",
        serde_json::to_string(&ready).map_err(|error| format!("PROXY_READY_SERIALIZE_FAILED:{error}"))?
    );
    std::io::stdout()
        .flush()
        .map_err(|error| format!("PROXY_READY_FLUSH_FAILED:{error}"))?;

    for incoming in listener.incoming() {
        match incoming {
            Ok(mut stream) => {
                let _ = stream.set_read_timeout(Some(Duration::from_secs_f64(config.timeout_seconds)));
                let _ = stream.set_write_timeout(Some(Duration::from_secs_f64(config.timeout_seconds)));
                if let Err(error) = handle_connection(&mut stream, &config, project, state_root) {
                    let _ = write_json_error(&mut stream, 502, "proxy-request-failed", &error, None);
                }
            }
            Err(error) => return Err(format!("PROXY_ACCEPT_FAILED:{error}")),
        }
    }
    Err("PROXY_LISTENER_TERMINATED".to_owned())
}

fn handle_connection(
    stream: &mut TcpStream,
    config: &ProxyConfig,
    project: &Path,
    state_root: &Path,
) -> Result<(), String> {
    let request = read_request(stream, config.max_request_bytes)?;
    if request.method == "GET" && matches!(request.target.as_str(), "/_syntavra/health" | "/_syntavra/ready") {
        return handle_control(stream, config, &request);
    }
    if request.method != "POST" {
        write_json_error(stream, 405, "method-not-allowed", "POST is required", None)?;
        return Ok(());
    }
    validate_request_target(&request.target)?;
    let payload: Value = serde_json::from_slice(&request.body)
        .map_err(|error| format!("PROXY_REQUEST_JSON_INVALID:{error}"))?;
    if !payload.is_object() {
        return Err("provider request must be a JSON object".to_owned());
    }

    let mut temporary = TempFiles::new();
    let request_path = temporary.push(temp_path(state_root, "request", "json")?);
    write_private(&request_path, &request.body)?;
    let plan = native_prepare(config, project, state_root, &request_path)?;
    let plan_path = temporary.push(temp_path(state_root, "plan", "json")?);
    write_json_private(&plan_path, &plan)?;

    if plan["replay_hit"].as_bool().unwrap_or(false) {
        let replay = native_replay(config, project, state_root, &plan_path)?;
        let body = serde_json::to_vec(&replay)
            .map_err(|error| format!("PROXY_REPLAY_SERIALIZE_FAILED:{error}"))?;
        let mut headers = vec![("Content-Type".to_owned(), "application/json".to_owned())];
        headers.push(("X-Syntavra-Replay".to_owned(), "hit".to_owned()));
        if let Some(handle) = plan["replay_response_handle"].as_str().filter(|value| !value.is_empty()) {
            headers.push(("X-Syntavra-Evidence".to_owned(), handle.to_owned()));
        }
        write_response(stream, 200, &headers, &body)?;
        return Ok(());
    }

    let prepared = plan
        .get("prepared_request")
        .ok_or_else(|| "PROXY_PREPARED_REQUEST_MISSING".to_owned())?;
    let prepared_body = serde_json::to_vec(prepared)
        .map_err(|error| format!("PROXY_PREPARED_REQUEST_SERIALIZE_FAILED:{error}"))?;
    let body_path = temporary.push(temp_path(state_root, "upstream-request", "json")?);
    write_private(&body_path, &prepared_body)?;
    let upstream = forward_upstream(config, &request, &body_path, state_root, &mut temporary)?;
    let streaming = prepared.get("stream").and_then(Value::as_bool).unwrap_or(false);

    if streaming {
        let project_id = stable_project_id(project)?;
        let evidence = NativeEvidenceStore::open(state_root, &project_id)?;
        let transport_handle = evidence.put(
            &upstream.body,
            "provider-response-transport",
            &json!({
                "provider": canonical_provider(&config.provider),
                "request_hash": plan["request_hash"].clone(),
                "status_code": upstream.status,
                "content_type": upstream.content_type,
                "transport_hash": sha256_hex(&upstream.body),
                "stream": true,
                "delivery": "commit-before-forward",
            }),
        )?;
        let secret_types = scan_secret_types(&upstream.body)?;
        if !secret_types.is_empty() {
            write_json_error(
                stream,
                502,
                "stream-dlp-blocked",
                "provider stream blocked before delivery",
                Some(json!({
                    "evidence_handle": transport_handle,
                    "secret_types": secret_types,
                })),
            )?;
            return Ok(());
        }
        let headers = vec![
            ("Content-Type".to_owned(), upstream.content_type),
            ("X-Syntavra-Replay".to_owned(), "miss".to_owned()),
            ("X-Syntavra-Capture".to_owned(), "complete-before-delivery".to_owned()),
            ("X-Syntavra-Evidence".to_owned(), transport_handle),
        ];
        write_response(stream, upstream.status, &headers, &upstream.body)?;
        return Ok(());
    }

    let response_path = temporary.push(temp_path(state_root, "response", "json")?);
    write_private(&response_path, &upstream.body)?;
    let capture = native_capture(config, project, state_root, &plan_path, &response_path)?;
    let mut headers = vec![
        ("Content-Type".to_owned(), upstream.content_type),
        ("X-Syntavra-Replay".to_owned(), "miss".to_owned()),
        ("X-Syntavra-Capture".to_owned(), "complete-before-delivery".to_owned()),
    ];
    if let Some(handle) = capture["response_handle"].as_str().filter(|value| !value.is_empty()) {
        headers.push(("X-Syntavra-Evidence".to_owned(), handle.to_owned()));
    }
    write_response(stream, upstream.status, &headers, &upstream.body)
}

fn handle_control(stream: &mut TcpStream, config: &ProxyConfig, request: &HttpRequest) -> Result<(), String> {
    let expected = env::var(&config.control_token_env).unwrap_or_default();
    let supplied = request
        .headers
        .get("authorization")
        .and_then(|value| value.strip_prefix("Bearer "))
        .unwrap_or_default();
    if expected.len() < 32 || !constant_time_eq(expected.as_bytes(), supplied.as_bytes()) {
        let payload = serde_json::to_vec(&json!({"error":"invalid-control-token"}))
            .map_err(|error| format!("PROXY_CONTROL_SERIALIZE_FAILED:{error}"))?;
        write_response(
            stream,
            401,
            &[("Content-Type".to_owned(), "application/json".to_owned())],
            &payload,
        )?;
        return Ok(());
    }
    let value = if request.target == "/_syntavra/ready" {
        json!({"ok":true,"ready":true,"provider":canonical_provider(&config.provider)})
    } else {
        json!({"ok":true,"ready":true,"provider":canonical_provider(&config.provider),"stream_mode":"commit-before-forward"})
    };
    let payload = serde_json::to_vec(&value)
        .map_err(|error| format!("PROXY_CONTROL_SERIALIZE_FAILED:{error}"))?;
    write_response(
        stream,
        200,
        &[("Content-Type".to_owned(), "application/json".to_owned())],
        &payload,
    )
}

fn read_request(stream: &mut TcpStream, max_body: usize) -> Result<HttpRequest, String> {
    let mut buffer = Vec::<u8>::new();
    let mut chunk = [0_u8; 8192];
    let header_end = loop {
        if let Some(index) = find_bytes(&buffer, b"\r\n\r\n") {
            break index + 4;
        }
        if buffer.len() >= MAX_HEADER_BYTES {
            return Err("PROXY_HEADERS_TOO_LARGE".to_owned());
        }
        let read = stream
            .read(&mut chunk)
            .map_err(|error| format!("PROXY_REQUEST_READ_FAILED:{error}"))?;
        if read == 0 {
            return Err("PROXY_REQUEST_EOF".to_owned());
        }
        buffer.extend_from_slice(&chunk[..read]);
    };
    let header_text = std::str::from_utf8(&buffer[..header_end - 4])
        .map_err(|_| "PROXY_HEADERS_UTF8_INVALID".to_owned())?;
    let mut lines = header_text.split("\r\n");
    let request_line = lines.next().ok_or_else(|| "PROXY_REQUEST_LINE_MISSING".to_owned())?;
    let mut parts = request_line.split_whitespace();
    let method = parts.next().unwrap_or_default().to_owned();
    let target = parts.next().unwrap_or_default().to_owned();
    let version = parts.next().unwrap_or_default();
    if method.is_empty() || target.is_empty() || !version.starts_with("HTTP/1.") || parts.next().is_some() {
        return Err("PROXY_REQUEST_LINE_INVALID".to_owned());
    }
    let mut headers = BTreeMap::<String, String>::new();
    for line in lines {
        let (name, value) = line
            .split_once(':')
            .ok_or_else(|| "PROXY_HEADER_INVALID".to_owned())?;
        validate_header_name(name.trim())?;
        validate_header_value(value.trim())?;
        headers.insert(name.trim().to_ascii_lowercase(), value.trim().to_owned());
    }
    if headers
        .get("transfer-encoding")
        .is_some_and(|value| !value.eq_ignore_ascii_case("identity"))
    {
        return Err("PROXY_CHUNKED_REQUEST_UNSUPPORTED".to_owned());
    }
    let content_length = headers
        .get("content-length")
        .map(|value| value.parse::<usize>().map_err(|_| "PROXY_CONTENT_LENGTH_INVALID".to_owned()))
        .transpose()?
        .unwrap_or(0);
    if content_length > max_body {
        return Err("PROXY_REQUEST_TOO_LARGE".to_owned());
    }
    while buffer.len() - header_end < content_length {
        let read = stream
            .read(&mut chunk)
            .map_err(|error| format!("PROXY_REQUEST_BODY_READ_FAILED:{error}"))?;
        if read == 0 {
            return Err("PROXY_REQUEST_BODY_EOF".to_owned());
        }
        buffer.extend_from_slice(&chunk[..read]);
        if buffer.len() - header_end > max_body {
            return Err("PROXY_REQUEST_TOO_LARGE".to_owned());
        }
    }
    Ok(HttpRequest {
        method,
        target,
        headers,
        body: buffer[header_end..header_end + content_length].to_vec(),
    })
}

fn forward_upstream(
    config: &ProxyConfig,
    request: &HttpRequest,
    body_path: &Path,
    state_root: &Path,
    temporary: &mut TempFiles,
) -> Result<UpstreamResponse, String> {
    let curl = find_executable(if cfg!(windows) { "curl.exe" } else { "curl" })
        .ok_or_else(|| "PROXY_CURL_NOT_FOUND".to_owned())?;
    let response_path = temporary.push(temp_path(state_root, "curl-response", "bin")?);
    let headers_path = temporary.push(temp_path(state_root, "curl-headers", "txt")?);
    let config_path = temporary.push(temp_path(state_root, "curl-secret", "cfg")?);
    let credential = provider_credential(config)?;
    let mut curl_config = String::new();
    if let Some((name, value)) = credential {
        curl_config.push_str("header = \"");
        curl_config.push_str(&curl_escape(&format!("{name}: {value}")));
        curl_config.push_str("\"\n");
    }
    write_private(&config_path, curl_config.as_bytes())?;

    let url = joined_upstream(&config.upstream_base, &request.target)?;
    let mut command = Command::new(curl);
    command
        .env_clear()
        .args([
            "--silent",
            "--show-error",
            "--no-location",
            "--request",
            "POST",
            "--url",
            &url,
            "--config",
        ])
        .arg(&config_path)
        .args(["--data-binary"])
        .arg(format!("@{}", body_path.to_string_lossy()))
        .args(["--dump-header"])
        .arg(&headers_path)
        .args(["--output"])
        .arg(&response_path)
        .args(["--write-out", "%{http_code}", "--max-time"])
        .arg(format!("{:.3}", config.timeout_seconds));
    for (name, value) in &request.headers {
        if SAFE_FORWARD_HEADERS.contains(&name.as_str()) && !CREDENTIAL_HEADERS.contains(&name.as_str()) {
            command.arg("--header").arg(format!("{name}: {value}"));
        }
    }
    if !request.headers.contains_key("content-type") {
        command.arg("--header").arg("Content-Type: application/json");
    }
    copy_safe_environment(&mut command, config);
    let output = command
        .output()
        .map_err(|error| format!("PROXY_CURL_EXECUTION_FAILED:{error}"))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("PROXY_UPSTREAM_FAILED:{}", bounded(&stderr, 1024)));
    }
    let status_text = String::from_utf8_lossy(&output.stdout).trim().to_owned();
    let status = status_text
        .parse::<u16>()
        .map_err(|_| format!("PROXY_UPSTREAM_STATUS_INVALID:{status_text}"))?;
    let body = fs::read(&response_path)
        .map_err(|error| format!("PROXY_UPSTREAM_RESPONSE_READ_FAILED:{error}"))?;
    if body.len() > config.max_response_bytes {
        return Err("PROXY_UPSTREAM_RESPONSE_TOO_LARGE".to_owned());
    }
    let header_bytes = fs::read(&headers_path).unwrap_or_default();
    let content_type = response_content_type(&header_bytes)
        .unwrap_or_else(|| "application/json".to_owned());
    Ok(UpstreamResponse {
        status,
        content_type,
        body,
    })
}

fn native_prepare(
    config: &ProxyConfig,
    project: &Path,
    state_root: &Path,
    request_path: &Path,
) -> Result<Value, String> {
    let mut args = vec![
        "provider".to_owned(),
        "prepare".to_owned(),
        config.provider.clone(),
        "--input".to_owned(),
        request_path.to_string_lossy().into_owned(),
        "--cache-policy".to_owned(),
        config.cache_policy.clone(),
        "--replay-ttl-seconds".to_owned(),
        config.replay_ttl_seconds.to_string(),
        "--prompt-cache-ttl-seconds".to_owned(),
        config.prompt_cache_ttl_seconds.to_string(),
    ];
    run_native_child(config, project, state_root, &mut args)
}

fn native_replay(
    config: &ProxyConfig,
    project: &Path,
    state_root: &Path,
    plan_path: &Path,
) -> Result<Value, String> {
    let mut args = vec![
        "provider".to_owned(),
        "replay".to_owned(),
        "--plan".to_owned(),
        plan_path.to_string_lossy().into_owned(),
    ];
    run_native_child(config, project, state_root, &mut args)
}

fn native_capture(
    config: &ProxyConfig,
    project: &Path,
    state_root: &Path,
    plan_path: &Path,
    response_path: &Path,
) -> Result<Value, String> {
    let mut args = vec![
        "provider".to_owned(),
        "capture".to_owned(),
        "--plan".to_owned(),
        plan_path.to_string_lossy().into_owned(),
        "--response".to_owned(),
        response_path.to_string_lossy().into_owned(),
        "--replay-ttl-seconds".to_owned(),
        config.replay_ttl_seconds.to_string(),
    ];
    run_native_child(config, project, state_root, &mut args)
}

fn run_native_child(
    config: &ProxyConfig,
    project: &Path,
    state_root: &Path,
    args: &mut [String],
) -> Result<Value, String> {
    let executable = env::current_exe().map_err(|error| format!("PROXY_CURRENT_EXE_FAILED:{error}"))?;
    let mut command = Command::new(executable);
    command
        .arg("--engine")
        .arg("rust")
        .arg("--project")
        .arg(project)
        .arg("--state-root")
        .arg(state_root)
        .args(args)
        .env("SYNTAVRA_BULK_PARITY_PROBE", "1");
    if !config.credential_env.is_empty() {
        command.env_remove(&config.credential_env);
    }
    command.env_remove(&config.control_token_env);
    let output = command
        .output()
        .map_err(|error| format!("PROXY_NATIVE_CHILD_FAILED:{error}"))?;
    if !output.status.success() {
        return Err(format!(
            "PROXY_NATIVE_CHILD_EXIT:{}:{}",
            output.status.code().unwrap_or(1),
            bounded(&String::from_utf8_lossy(&output.stderr), 2048)
        ));
    }
    serde_json::from_slice(&output.stdout)
        .map_err(|error| format!("PROXY_NATIVE_CHILD_JSON_INVALID:{error}"))
}

fn provider_credential(config: &ProxyConfig) -> Result<Option<(String, String)>, String> {
    if config.credential_env.is_empty() {
        return Ok(None);
    }
    let secret = env::var(&config.credential_env)
        .map_err(|_| format!("missing provider credential environment variable: {}", config.credential_env))?;
    validate_header_value(&secret)?;
    let (default_header, default_prefix) = match canonical_provider(&config.provider).as_str() {
        "anthropic" => ("x-api-key", ""),
        "gemini" => ("x-goog-api-key", ""),
        _ => ("Authorization", "Bearer "),
    };
    let header = if config.credential_header.is_empty() {
        default_header.to_owned()
    } else {
        config.credential_header.clone()
    };
    validate_header_name(&header)?;
    let prefix = if config.credential_prefix.is_empty() {
        default_prefix
    } else {
        config.credential_prefix.as_str()
    };
    let value = format!("{prefix}{secret}");
    validate_header_value(&value)?;
    Ok(Some((header, value)))
}

fn copy_safe_environment(command: &mut Command, config: &ProxyConfig) {
    for name in [
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "HOME",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "CURL_CA_BUNDLE",
    ] {
        if name == config.credential_env || name == config.control_token_env {
            continue;
        }
        if let Some(value) = env::var_os(name) {
            command.env(name, value);
        }
    }
}

fn joined_upstream(base: &str, target: &str) -> Result<String, String> {
    validate_request_target(target)?;
    let (_, rest) = base
        .split_once("://")
        .ok_or_else(|| "PROXY_UPSTREAM_INVALID".to_owned())?;
    let authority_end = rest.find('/').unwrap_or(rest.len());
    let base_path = &rest[authority_end..];
    let origin = &base[..base.len() - base_path.len()];
    let target_path = target.strip_prefix('/').unwrap_or(target);
    if base_path.is_empty() || base_path == "/" {
        Ok(format!("{}/{}", origin.trim_end_matches('/'), target_path))
    } else {
        Ok(format!(
            "{}/{}/{}",
            origin.trim_end_matches('/'),
            base_path.trim_matches('/'),
            target_path
        ))
    }
}

fn validate_request_target(target: &str) -> Result<(), String> {
    if !target.starts_with('/')
        || target.starts_with("//")
        || target.contains("\\")
        || target.contains("\r")
        || target.contains("\n")
        || target.starts_with("http://")
        || target.starts_with("https://")
    {
        return Err("absolute proxy targets are forbidden".to_owned());
    }
    Ok(())
}

fn response_content_type(headers: &[u8]) -> Option<String> {
    let text = String::from_utf8_lossy(headers);
    let mut result = None;
    for line in text.lines() {
        if let Some(value) = line.strip_prefix("Content-Type:").or_else(|| line.strip_prefix("content-type:")) {
            result = Some(value.trim().to_owned());
        }
    }
    result
}

fn scan_secret_types(body: &[u8]) -> Result<Vec<String>, String> {
    let text = String::from_utf8_lossy(body);
    let patterns = [
        (
            "generic-assignment",
            r"(?i)\b(api[_-]?key|access[_-]?token|authorization|password|passwd|secret|bearer|private[_-]?key|client[_-]?secret|session[_-]?id|cookie)\b\s*[:=]\s*([^\s,;]+)",
        ),
        ("aws-access-key", r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
        ("github-token", r"\b(?:gh[pousr]_[A-Za-z0-9]{20,255}|github_pat_[A-Za-z0-9_]{20,255})\b"),
        ("jwt", r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
        ("private-key", r"(?s)-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----.*?-----END(?: [A-Z0-9]+)? PRIVATE KEY-----"),
    ];
    let mut found = Vec::<String>::new();
    for (name, pattern) in patterns {
        let regex = Regex::new(pattern).map_err(|error| format!("PROXY_DLP_REGEX_INVALID:{error}"))?;
        if regex.is_match(&text) {
            found.push(name.to_owned());
        }
    }
    Ok(found)
}

fn write_json_error(
    stream: &mut TcpStream,
    status: u16,
    code: &str,
    message: &str,
    details: Option<Value>,
) -> Result<(), String> {
    let mut value = json!({"error": code});
    if code != "stream-dlp-blocked" {
        value["message"] = Value::String(bounded(message, 512));
    }
    if let Some(details) = details {
        if let Some(map) = details.as_object() {
            for (key, child) in map {
                value[key] = child.clone();
            }
        }
    }
    let body = serde_json::to_vec(&value)
        .map_err(|error| format!("PROXY_ERROR_SERIALIZE_FAILED:{error}"))?;
    write_response(
        stream,
        status,
        &[("Content-Type".to_owned(), "application/json".to_owned())],
        &body,
    )
}

fn write_response(
    stream: &mut TcpStream,
    status: u16,
    headers: &[(String, String)],
    body: &[u8],
) -> Result<(), String> {
    let reason = status_reason(status);
    let mut head = format!("HTTP/1.1 {status} {reason}\r\nContent-Length: {}\r\nConnection: close\r\n", body.len());
    for (name, value) in headers {
        validate_header_name(name)?;
        validate_header_value(value)?;
        if name.eq_ignore_ascii_case("content-length") || name.eq_ignore_ascii_case("connection") {
            continue;
        }
        head.push_str(name);
        head.push_str(": ");
        head.push_str(value);
        head.push_str("\r\n");
    }
    head.push_str("\r\n");
    stream
        .write_all(head.as_bytes())
        .and_then(|_| stream.write_all(body))
        .and_then(|_| stream.flush())
        .map_err(|error| format!("PROXY_RESPONSE_WRITE_FAILED:{error}"))
}

fn status_reason(status: u16) -> &'static str {
    match status {
        200 => "OK",
        201 => "Created",
        202 => "Accepted",
        204 => "No Content",
        400 => "Bad Request",
        401 => "Unauthorized",
        403 => "Forbidden",
        404 => "Not Found",
        405 => "Method Not Allowed",
        408 => "Request Timeout",
        413 => "Payload Too Large",
        429 => "Too Many Requests",
        500 => "Internal Server Error",
        502 => "Bad Gateway",
        503 => "Service Unavailable",
        504 => "Gateway Timeout",
        _ => "Upstream Response",
    }
}

fn validate_header_name(value: &str) -> Result<(), String> {
    if value.is_empty()
        || !value.bytes().all(|byte| {
            byte.is_ascii_alphanumeric()
                || matches!(byte, b'!' | b'#' | b'$' | b'%' | b'&' | b'\'' | b'*' | b'+' | b'-' | b'.' | b'^' | b'_' | b'`' | b'|' | b'~')
        })
    {
        return Err("invalid HTTP header name".to_owned());
    }
    Ok(())
}

fn validate_header_value(value: &str) -> Result<(), String> {
    if value.contains(['\r', '\n', '\0']) || value.len() > 8192 {
        return Err("HTTP header value contains a prohibited control character".to_owned());
    }
    Ok(())
}

fn option_value(arguments: &[String], flag: &str) -> Result<Option<String>, String> {
    let mut result = None;
    let mut index = 0usize;
    while index < arguments.len() {
        let current = &arguments[index];
        let found = if current == flag {
            index += 1;
            Some(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?
                    .clone(),
            )
        } else {
            current
                .strip_prefix(flag)
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

fn required_option(arguments: &[String], flag: &str) -> Result<String, String> {
    option_value(arguments, flag)?.ok_or_else(|| format!("{flag}_REQUIRED"))
}

fn integer_option(arguments: &[String], flag: &str, default: i64) -> Result<i64, String> {
    option_value(arguments, flag)?
        .map(|value| value.parse::<i64>().map_err(|_| format!("{flag}_INVALID")))
        .transpose()
        .map(|value| value.unwrap_or(default))
}

fn float_option(arguments: &[String], flag: &str, default: f64) -> Result<f64, String> {
    option_value(arguments, flag)?
        .map(|value| value.parse::<f64>().map_err(|_| format!("{flag}_INVALID")))
        .transpose()
        .map(|value| value.unwrap_or(default))
}

fn has_flag(arguments: &[String], flag: &str) -> bool {
    arguments.iter().any(|value| value == flag)
}

fn temp_path(state_root: &Path, label: &str, extension: &str) -> Result<PathBuf, String> {
    let root = state_root.join("provider-proxy-spool");
    fs::create_dir_all(&root).map_err(|error| format!("PROXY_SPOOL_CREATE_FAILED:{error}"))?;
    let mut random = [0_u8; 8];
    OsRng.fill_bytes(&mut random);
    let nonce = random.iter().map(|value| format!("{value:02x}")).collect::<String>();
    let sequence = REQUEST_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    Ok(root.join(format!("{label}-{sequence}-{nonce}.{extension}")))
}

fn write_private(path: &Path, data: &[u8]) -> Result<(), String> {
    let mut file = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(path)
        .map_err(|error| format!("PROXY_TEMP_CREATE_FAILED:{error}"))?;
    file.write_all(data)
        .and_then(|_| file.flush())
        .and_then(|_| file.sync_all())
        .map_err(|error| format!("PROXY_TEMP_WRITE_FAILED:{error}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt as _;
        let _ = fs::set_permissions(path, fs::Permissions::from_mode(0o600));
    }
    Ok(())
}

fn write_json_private(path: &Path, value: &Value) -> Result<(), String> {
    let data = serde_json::to_vec(value).map_err(|error| format!("PROXY_TEMP_JSON_FAILED:{error}"))?;
    write_private(path, &data)
}

fn find_executable(name: &str) -> Option<PathBuf> {
    let candidate = Path::new(name);
    if candidate.is_absolute() && candidate.is_file() {
        return Some(candidate.to_path_buf());
    }
    let path = env::var_os("PATH")?;
    for directory in env::split_paths(&path) {
        let candidate = directory.join(name);
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    None
}

fn canonical_provider(value: &str) -> String {
    match value.trim().to_ascii_lowercase().as_str() {
        "chatgpt" | "responses" | "azure-openai" => "openai".to_owned(),
        "claude" | "bedrock-anthropic" | "vertex-anthropic" => "anthropic".to_owned(),
        "google" | "google-ai" | "vertex-gemini" => "gemini".to_owned(),
        "openrouter" | "litellm" | "vllm" | "ollama" | "lmstudio" | "openai-compatible" => {
            "openai-compatible".to_owned()
        }
        other => other.to_owned(),
    }
}

fn stable_project_id(project: &Path) -> Result<String, String> {
    let raw = project
        .to_str()
        .ok_or_else(|| "PROXY_PROJECT_UTF8_INVALID".to_owned())?;
    let normalized = if cfg!(windows) {
        let mut value = raw.to_owned();
        if let Some(rest) = value.strip_prefix(r"\\?\UNC\") {
            value = format!(r"\\{rest}");
        } else if let Some(rest) = value.strip_prefix(r"\\?\") {
            value = rest.to_owned();
        }
        value.replace('/', "\\").to_lowercase()
    } else {
        raw.to_owned()
    };
    Ok(sha256_hex(normalized.as_bytes()))
}

fn constant_time_eq(left: &[u8], right: &[u8]) -> bool {
    let mut difference = left.len() ^ right.len();
    let maximum = left.len().max(right.len());
    for index in 0..maximum {
        let a = left.get(index).copied().unwrap_or(0);
        let b = right.get(index).copied().unwrap_or(0);
        difference |= usize::from(a ^ b);
    }
    difference == 0
}

fn curl_escape(value: &str) -> String {
    value.replace('\\', "\\\\").replace('"', "\\\"")
}

fn bounded(value: &str, limit: usize) -> String {
    value.chars().take(limit).collect()
}

fn find_bytes(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    if needle.is_empty() || haystack.len() < needle.len() {
        return None;
    }
    haystack.windows(needle.len()).position(|window| window == needle)
}

#[cfg(test)]
mod tests {
    use super::{joined_upstream, scan_secret_types, validate_request_target, validate_upstream};

    #[test]
    fn fixed_origin_rejects_absolute_targets() {
        assert!(validate_request_target("http://attacker.invalid/v1/responses").is_err());
        assert!(validate_request_target("/v1/responses").is_ok());
    }

    #[test]
    fn insecure_upstream_requires_explicit_flag() {
        assert!(validate_upstream("http://127.0.0.1:9999", false).is_err());
        assert!(validate_upstream("http://127.0.0.1:9999", true).is_ok());
        assert!(validate_upstream("https://api.example.invalid/v1", false).is_ok());
    }

    #[test]
    fn joins_fixed_base_path_without_origin_escape() {
        assert_eq!(
            joined_upstream("https://api.example.invalid/base", "/v1/responses").unwrap(),
            "https://api.example.invalid/base/v1/responses"
        );
    }

    #[test]
    fn stream_dlp_detects_secret_assignments() {
        let found = scan_secret_types(b"data: {\"delta\":\"api_key=super-secret-value\"}\n\n").unwrap();
        assert!(found.iter().any(|value| value == "generic-assignment"));
    }
}
