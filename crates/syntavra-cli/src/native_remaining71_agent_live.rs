#![forbid(unsafe_code)]
#![allow(clippy::too_many_lines, clippy::cast_precision_loss)]

use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, OpenOptions};
use std::io::Write as _;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{SystemTime, UNIX_EPOCH};

use rand::{rngs::OsRng, RngCore as _};
use serde_json::{json, Map, Value};
use syntavra_core::sha256_hex;

const SYSTEM_PROMPT: &str = r#"You are the patch-planning component of Syntavra.
Return exactly one JSON object and no markdown.
Allowed actions:
- {"action":"search","query":"..."}
- {"action":"inspect","paths":["relative/path.py"]}
- {"action":"diff"}
- {"action":"impact","node_id":"..."}
- {"action":"verifiers"}
- {"action":"run_verifier","name":"..."}
- {"action":"edit","edits":[{"path":"...","operation":"replace","old":"...","new":"...","count":1}],"rationale":"..."}
- {"action":"patch","patch":"unified diff","rationale":"..."}
Use search, inspect, impact or a verifier when evidence is insufficient. Never invent file contents.
Patch or structured edits must stay inside the repository and must be suitable for git apply.
"#;

#[derive(Debug, Clone)]
struct Verifier {
    name: String,
    argv: Vec<String>,
    stage: String,
    confidence: f64,
    reason: String,
    source: String,
}
impl Verifier {
    fn json(&self) -> Value {
        json!({"name":self.name,"argv":self.argv,"stage":self.stage,"confidence":self.confidence,"reason":self.reason,"source":self.source})
    }
}

#[derive(Debug, Clone)]
struct ModelResult {
    text: String,
    provider: String,
    model: String,
    usage: BTreeMap<String, i64>,
    response_id: String,
    finish_reason: String,
}

pub(crate) fn supports(command: &[String]) -> bool {
    matches!(command,[root,action] if root=="agent"&&action=="run")
}

pub(crate) fn execute(
    command: &[String],
    arguments: &[String],
    project: &Path,
    state_root: &Path,
) -> Result<Option<Value>, String> {
    if !supports(command) {
        return Ok(None);
    }
    agent_run(arguments, project, state_root).map(Some)
}

fn agent_run(arguments: &[String], project: &Path, state_root: &Path) -> Result<Value, String> {
    let task = positional_after(
        arguments,
        "agent",
        "run",
        0,
        &[
            "--provider",
            "--model",
            "--endpoint",
            "--api-key-env",
            "--api-mode",
            "--mode",
            "--attempts",
            "--timeout",
            "--token-budget",
            "--cost-budget",
            "--session-id",
            "--delivery",
            "--branch-name",
            "--commit-message",
            "--pr-title",
            "--pr-body",
            "--events-jsonl",
        ],
    )?;
    if task.trim().is_empty() {
        return Err("agent instruction cannot be empty".to_owned());
    }
    let provider =
        option_value(arguments, "--provider")?.unwrap_or_else(|| "openai-compatible".to_owned());
    let model = required_option(arguments, "--model")?;
    let endpoint = option_value(arguments, "--endpoint")?.unwrap_or_default();
    let api_key_env = option_value(arguments, "--api-key-env")?
        .unwrap_or_else(|| default_api_key_env(&provider).to_owned());
    let api_mode = option_value(arguments, "--api-mode")?.unwrap_or_else(|| "auto".to_owned());
    let mode = option_value(arguments, "--mode")?.unwrap_or_else(|| "review-required".to_owned());
    let attempts = option_i64(arguments, "--attempts", 3)?.clamp(1, 20);
    let timeout = option_f64(arguments, "--timeout", 900.0)?.max(1.0);
    let token_budget = option_value(arguments, "--token-budget")?;
    let cost_budget = option_value(arguments, "--cost-budget")?;
    let authorized = has_flag(arguments, "--authorized");
    let session_id = option_value(arguments, "--session-id")?;
    let delivery = option_value(arguments, "--delivery")?.unwrap_or_else(|| "diff".to_owned());
    if !matches!(
        delivery.as_str(),
        "diff" | "worktree" | "apply" | "commit" | "pr"
    ) {
        return Err("AGENT_DELIVERY_INVALID".to_owned());
    }
    let mut verifiers = discover_verifiers(project)?;
    if verifiers.is_empty() {
        return Err(
            "agent cannot run safely because no project verifier was discovered".to_owned(),
        );
    }
    verifiers.sort_by(|left, right| {
        right
            .confidence
            .partial_cmp(&left.confidence)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| stage_rank(&left.stage).cmp(&stage_rank(&right.stage)))
            .then_with(|| left.name.cmp(&right.name))
    });
    let primary = verifiers[0].clone();
    let graph_index = vec!["run".to_owned(), "graph-index".to_owned()];
    let _ =
        super::native_remaining71_graph::execute(&graph_index, &graph_index, project, state_root)?;
    let initial = query_graph(project, state_root, task, 20)?;
    let context = assemble_context(project, task, &initial)?;
    let mut messages = vec![json!({"role":"user","content":canonical_json(&context)?})];
    let mut trace = Vec::<Value>::new();
    let mut usage = BTreeMap::<String, i64>::new();
    let mut result_provider = provider.clone();
    let mut result_model = model.clone();
    let mut proposal = None::<(String, String)>;
    for round in 1..=8 {
        emit_event(arguments, "model-requested", json!({"round":round}))?;
        let result = model_complete(
            &provider,
            &model,
            &endpoint,
            &api_key_env,
            &api_mode,
            timeout.min(180.0),
            &messages,
            SYSTEM_PROMPT,
            state_root,
        )?;
        result_provider = result.provider.clone();
        result_model = result.model.clone();
        for (key, value) in result.usage {
            *usage.entry(key).or_default() += value.max(0)
        }
        let action = parse_action(&result.text)?;
        let name = action["action"].as_str().unwrap_or_default().to_lowercase();
        emit_event(
            arguments,
            "model-action",
            json!({"round":round,"action":name,"response_id":result.response_id,"finish_reason":result.finish_reason}),
        )?;
        match name.as_str() {
            "search" => {
                let query = action["query"].as_str().unwrap_or(task);
                let rows = query_graph(project, state_root, query, 20)?;
                trace.push(
                    json!({"round":round,"action":"search","query":query,"results":rows.len()}),
                );
                append_tool(
                    &mut messages,
                    &action,
                    json!({"tool":"repo.search","query":query,"results":rows}),
                )?;
            }
            "inspect" => {
                let paths = action["paths"]
                    .as_array()
                    .ok_or_else(|| "inspect action paths must be a list".to_owned())?
                    .iter()
                    .filter_map(Value::as_str)
                    .map(str::to_owned)
                    .collect::<Vec<_>>();
                let files = inspect_files(project, &paths)?;
                trace.push(json!({"round":round,"action":"inspect","paths":files.iter().filter_map(|row|row["path"].as_str()).collect::<Vec<_>>()}));
                append_tool(
                    &mut messages,
                    &action,
                    json!({"tool":"repo.read","files":files}),
                )?;
            }
            "impact" => {
                let node = action["node_id"]
                    .as_str()
                    .filter(|value| !value.is_empty())
                    .ok_or_else(|| "impact action requires node_id".to_owned())?;
                let impact = graph_impact(project, state_root, node)?;
                trace.push(json!({"round":round,"action":"impact","node_id":node}));
                append_tool(
                    &mut messages,
                    &action,
                    json!({"tool":"repo.impact","result":impact}),
                )?;
            }
            "verifiers" => {
                let rows = verifiers.iter().map(Verifier::json).collect::<Vec<_>>();
                trace.push(json!({"round":round,"action":"verifiers","count":rows.len()}));
                append_tool(
                    &mut messages,
                    &action,
                    json!({"tool":"test.discover","verifiers":rows}),
                )?;
            }
            "patch" => {
                let patch = action["patch"].as_str().unwrap_or_default().to_owned();
                if patch.trim().is_empty() {
                    return Err("model returned an empty patch".to_owned());
                }
                let rationale = action["rationale"].as_str().unwrap_or_default().to_owned();
                trace.push(
                    json!({"round":round,"action":"patch","patch_bytes":patch.as_bytes().len()}),
                );
                proposal = Some((patch, rationale));
                break;
            }
            other => return Err(format!("unsupported model action: {other}")),
        }
    }
    let Some((patch, rationale)) = proposal else {
        return Err("model exhausted the bounded tool loop without producing a patch".to_owned());
    };
    let estimated_tokens = usage.get("input_tokens").copied().unwrap_or(0)
        + usage.get("output_tokens").copied().unwrap_or(0);
    let proposals = json!([{"patch":patch,"rationale":rationale,"estimated_tokens":estimated_tokens,"estimated_cost":0.0}]);
    let replay_command = vec!["agent".to_owned(), "replay".to_owned()];
    let mut replay_args = vec![
        "agent".to_owned(),
        "replay".to_owned(),
        task.to_owned(),
        canonical_json(&proposals)?,
        canonical_json(&Value::Array(
            primary.argv.iter().cloned().map(Value::String).collect(),
        ))?,
        "--mode".to_owned(),
        mode.clone(),
        "--attempts".to_owned(),
        "1".to_owned(),
        "--timeout".to_owned(),
        timeout.to_string(),
    ];
    if authorized {
        replay_args.push("--authorized".to_owned())
    }
    if let Some(value) = token_budget {
        replay_args.extend(["--token-budget".to_owned(), value]);
    }
    if let Some(value) = cost_budget {
        replay_args.extend(["--cost-budget".to_owned(), value]);
    }
    if let Some(value) = session_id {
        replay_args.extend(["--session-id".to_owned(), value]);
    }
    let mut run = super::native_remaining71_agent::execute(
        &replay_command,
        &replay_args,
        project,
        state_root,
    )?
    .ok_or_else(|| "AGENT_REPLAY_NATIVE_UNAVAILABLE".to_owned())?;
    run["task"]["metadata"] = json!({
        "verifier_discovery": verifiers.iter().map(Verifier::json).collect::<Vec<_>>(),
        "semantic_results": initial.clone(),
    });
    super::native_remaining71_agent::persist_receipt(state_root, &run)?;
    let workspace = PathBuf::from(run["workspace"].as_str().unwrap_or_default());
    let mut post = Vec::<Value>::new();
    let mut verification_complete = run["ok"].as_bool().unwrap_or(false);
    if verification_complete && !has_flag(arguments, "--no-post-verifiers") {
        for verifier in verifiers.iter().skip(1) {
            let receipt = sandbox_run(&workspace, state_root, &verifier.argv, timeout.min(1800.0))?;
            let ok = receipt["ok"].as_bool().unwrap_or(false);
            post.push(json!({"name":verifier.name,"argv":verifier.argv,"ok":ok,"exit_code":receipt["exit_code"],"timed_out":receipt["timed_out"],"stdout":tail(receipt["stdout"].as_str().unwrap_or_default(),24000),"stderr":tail(receipt["stderr"].as_str().unwrap_or_default(),24000)}));
            if !ok {
                verification_complete = false;
                break;
            }
        }
    }
    let delivery_receipt = deliver(project, &run, &delivery, authorized, arguments)?;
    let limitations = if delivery == "pr" && !command_exists("gh") {
        vec!["gh CLI is required for PR delivery"]
    } else {
        Vec::new()
    };
    let run_ok = run["ok"].as_bool().unwrap_or(false);
    let ok = run_ok
        && verification_complete
        && post.iter().all(|row| row["ok"].as_bool().unwrap_or(false))
        && delivery_receipt["ok"].as_bool().unwrap_or(false);
    let mut events = Vec::<Value>::new();
    push_journal_event(
        &mut events,
        "agent-started",
        json!({"instruction":task,"mode":mode,"delivery":delivery}),
    )?;
    push_journal_event(
        &mut events,
        "verification-plan",
        json!({
            "primary": primary.json(),
            "post": verifiers.iter().skip(1).map(Verifier::json).collect::<Vec<_>>(),
        }),
    )?;
    push_journal_event(
        &mut events,
        "model-loop-started",
        json!({"attempt":1,"workspace":run["workspace"]}),
    )?;
    for row in &trace {
        let round = row["round"].as_i64().unwrap_or(0);
        let action = row["action"].as_str().unwrap_or_default();
        push_journal_event(&mut events, "model-requested", json!({"round":round}))?;
        push_journal_event(
            &mut events,
            "model-action",
            json!({"round":round,"action":action}),
        )?;
        if action == "patch" {
            push_journal_event(
                &mut events,
                "patch-proposed",
                json!({
                    "round":round,
                    "source":"unified-diff",
                    "bytes":row["patch_bytes"],
                }),
            )?;
        }
    }
    push_journal_event(
        &mut events,
        "primary-run-finished",
        json!({"ok":run_ok,"state":run["state"],"stop_reason":run["stop_reason"]}),
    )?;
    for row in &post {
        push_journal_event(
            &mut events,
            "post-verifier-started",
            json!({"name":row["name"]}),
        )?;
        push_journal_event(&mut events, "post-verifier-finished", row.clone())?;
    }
    push_journal_event(
        &mut events,
        "delivery-finished",
        json!({
            "mode":delivery_receipt["mode"],
            "ok":delivery_receipt["ok"],
            "branch":delivery_receipt["branch"],
            "commit":delivery_receipt["commit"],
        }),
    )?;
    if let Some(object) = run.as_object_mut() {
        object.remove("ok");
        object.remove("surface");
    }
    Ok(
        json!({"run":run,"provider":result_provider,"model":result_model,"verifier":primary.json(),"post_verifiers":post,"tool_trace":trace,"usage":usage,"delivery":delivery_receipt,"events":events,"verification_complete":verification_complete,"delivery_options":["diff","worktree","apply","commit","pr"],"limitations":limitations,"ok":ok}),
    )
}

fn default_api_key_env(provider: &str) -> &'static str {
    match provider.trim().to_lowercase().as_str() {
        "openai" => "OPENAI_API_KEY",
        "nvidia-nim" => "NVIDIA_API_KEY",
        "anthropic" | "claude" => "ANTHROPIC_API_KEY",
        "gemini" | "google" => "GEMINI_API_KEY",
        _ => "",
    }
}
fn default_endpoint(provider: &str) -> &'static str {
    match provider.trim().to_lowercase().as_str() {
        "openai" => "https://api.openai.com/v1",
        "nvidia-nim" => "https://integrate.api.nvidia.com/v1",
        "anthropic" | "claude" => "https://api.anthropic.com/v1",
        "gemini" | "google" => "https://generativelanguage.googleapis.com/v1beta",
        _ => "",
    }
}
fn provider_family(provider: &str) -> Result<&'static str, String> {
    match provider.trim().to_lowercase().as_str() {
        "openai" | "openai-compatible" | "local" | "lm-studio" | "localai" | "nvidia-nim"
        | "vllm" => Ok("openai"),
        "anthropic" | "claude" => Ok("anthropic"),
        "gemini" | "google" => Ok("gemini"),
        other => Err(format!("unsupported model gateway provider: {other}")),
    }
}

fn model_complete(
    provider: &str,
    model: &str,
    endpoint: &str,
    key_env: &str,
    api_mode: &str,
    timeout: f64,
    messages: &[Value],
    system: &str,
    state_root: &Path,
) -> Result<ModelResult, String> {
    if !command_exists("curl") {
        return Err("model endpoint request failed: curl is unavailable".to_owned());
    }
    let family = provider_family(provider)?;
    let base = if endpoint.trim().is_empty() {
        default_endpoint(provider).to_owned()
    } else {
        endpoint.trim_end_matches('/').to_owned()
    };
    if !base.starts_with("https://") && !base.starts_with("http://") {
        return Err("model endpoint must be an absolute http(s) URL".to_owned());
    }
    let key = if key_env.is_empty() {
        String::new()
    } else {
        std::env::var(key_env)
            .map_err(|_| format!("required API key environment variable is missing: {key_env}"))?
    };
    let mode = if family == "openai" {
        if api_mode == "auto" {
            if base == "https://api.openai.com/v1" {
                "responses"
            } else {
                "chat"
            }
        } else {
            api_mode
        }
    } else {
        api_mode
    };
    let (url, payload, headers) = match family {
        "openai" if mode == "responses" => {
            let mut input = Vec::new();
            if !system.is_empty() {
                input.push(json!({"role":"developer","content":system}));
            }
            input.extend(messages.iter().cloned());
            (
                format!("{base}/responses"),
                json!({"model":model,"input":input,"max_output_tokens":8192,"temperature":0.1,"store":false}),
                if key.is_empty() {
                    Vec::new()
                } else {
                    vec![format!("Authorization: Bearer {key}")]
                },
            )
        }
        "openai" if mode == "chat" => {
            let mut rows = Vec::new();
            if !system.is_empty() {
                rows.push(json!({"role":"system","content":system}));
            }
            rows.extend(messages.iter().cloned());
            (
                format!("{base}/chat/completions"),
                json!({"model":model,"messages":rows,"max_tokens":8192,"temperature":0.1,"stream":false}),
                if key.is_empty() {
                    Vec::new()
                } else {
                    vec![format!("Authorization: Bearer {key}")]
                },
            )
        }
        "openai" => return Err(format!("unsupported OpenAI-compatible api_mode: {mode}")),
        "anthropic" => {
            let rows = messages
                .iter()
                .filter(|row| matches!(row["role"].as_str(), Some("user") | Some("assistant")))
                .cloned()
                .collect::<Vec<_>>();
            (
                format!("{base}/messages"),
                json!({"model":model,"system":system,"messages":rows,"max_tokens":8192,"temperature":0.1}),
                vec![
                    format!("x-api-key: {key}"),
                    "anthropic-version: 2023-06-01".to_owned(),
                ],
            )
        }
        "gemini" => {
            let contents=messages.iter().map(|row|json!({"role":if row["role"].as_str()==Some("assistant"){"model"}else{"user"},"parts":[{"text":row["content"].as_str().unwrap_or_default()}]})).collect::<Vec<_>>();
            let mut payload = json!({"contents":contents,"generationConfig":{"maxOutputTokens":8192,"temperature":0.1,"responseMimeType":"application/json"}});
            if !system.is_empty() {
                payload["systemInstruction"] = json!({"parts":[{"text":system}]});
            }
            (
                format!("{base}/models/{}:generateContent", percent_encode(model)),
                payload,
                vec![format!("x-goog-api-key: {key}")],
            )
        }
        _ => unreachable!(),
    };
    let temp_root = state_root.join("unified/model-http");
    fs::create_dir_all(&temp_root)
        .map_err(|error| format!("MODEL_HTTP_TEMP_ROOT_FAILED:{error}"))?;
    let nonce = random_hex(8);
    let payload_path = temp_root.join(format!("{nonce}.request.json"));
    let headers_path = temp_root.join(format!("{nonce}.headers"));
    let response_path = temp_root.join(format!("{nonce}.response.json"));
    fs::write(&payload_path, canonical_json(&payload)?)
        .map_err(|error| format!("MODEL_HTTP_PAYLOAD_WRITE_FAILED:{error}"))?;
    let mut header_text = "Content-Type: application/json\nUser-Agent: Syntavra/0.0.1\n".to_owned();
    for header in &headers {
        header_text.push_str(header);
        header_text.push('\n');
    }
    fs::write(&headers_path, &header_text)
        .map_err(|error| format!("MODEL_HTTP_HEADER_WRITE_FAILED:{error}"))?;
    set_private(&headers_path);
    let output = Command::new("curl")
        .args([
            "--silent",
            "--show-error",
            "--fail-with-body",
            "--max-time",
            &timeout.ceil().to_string(),
            "--header",
            &format!("@{}", headers_path.display()),
            "--data-binary",
            &format!("@{}", payload_path.display()),
            "--output",
            response_path.to_str().unwrap_or_default(),
            &url,
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output();
    let _ = fs::remove_file(&payload_path);
    let _ = fs::remove_file(&headers_path);
    let output =
        output.map_err(|error| format!("model endpoint request failed: {}", error.kind()))?;
    let raw = fs::read(&response_path).unwrap_or_default();
    let _ = fs::remove_file(&response_path);
    if !output.status.success() {
        return Err(format!(
            "model endpoint request failed: curl exit {}: {}",
            output.status.code().unwrap_or(1),
            tail(&String::from_utf8_lossy(&output.stderr), 16384)
        ));
    }
    let value = serde_json::from_slice::<Value>(&raw)
        .map_err(|_| "model endpoint returned invalid JSON".to_owned())?;
    if !value.is_object() {
        return Err("model endpoint response must be a JSON object".to_owned());
    }
    let text = match family {
        "openai" if mode == "responses" => {
            let direct = value["output_text"].as_str().unwrap_or_default().to_owned();
            if !direct.is_empty() {
                direct
            } else {
                value["output"]
                    .as_array()
                    .cloned()
                    .unwrap_or_default()
                    .iter()
                    .flat_map(|item| item["content"].as_array().cloned().unwrap_or_default())
                    .filter_map(|item| item["text"].as_str().map(str::to_owned))
                    .collect::<Vec<_>>()
                    .join("\n")
            }
        }
        "openai" => value["choices"][0]["message"]["content"]
            .as_str()
            .unwrap_or_default()
            .to_owned(),
        "anthropic" => value["content"]
            .as_array()
            .cloned()
            .unwrap_or_default()
            .iter()
            .filter(|item| item["type"].as_str() == Some("text"))
            .filter_map(|item| item["text"].as_str())
            .collect::<Vec<_>>()
            .join("\n"),
        "gemini" => value["candidates"][0]["content"]["parts"]
            .as_array()
            .cloned()
            .unwrap_or_default()
            .iter()
            .filter_map(|item| item["text"].as_str())
            .collect::<Vec<_>>()
            .join("\n"),
        _ => String::new(),
    };
    if text.is_empty() {
        return Err(format!("{family} model endpoint returned no text output"));
    }
    let usage = extract_usage(&value);
    let (response_id, finish) = match family {
        "openai" => (
            value["id"].as_str().unwrap_or_default().to_owned(),
            if mode == "responses" {
                value["status"].as_str().unwrap_or_default().to_owned()
            } else {
                value["choices"][0]["finish_reason"]
                    .as_str()
                    .unwrap_or_default()
                    .to_owned()
            },
        ),
        "anthropic" => (
            value["id"].as_str().unwrap_or_default().to_owned(),
            value["stop_reason"].as_str().unwrap_or_default().to_owned(),
        ),
        "gemini" => (
            String::new(),
            value["candidates"][0]["finishReason"]
                .as_str()
                .unwrap_or_default()
                .to_owned(),
        ),
        _ => (String::new(), String::new()),
    };
    Ok(ModelResult {
        text,
        provider: provider.to_owned(),
        model: model.to_owned(),
        usage,
        response_id,
        finish_reason: finish,
    })
}
fn extract_usage(value: &Value) -> BTreeMap<String, i64> {
    fn visit(value: &Value, out: &mut BTreeMap<String, i64>) {
        match value {
            Value::Object(map) => {
                for (key, child) in map {
                    let normalized = match key.to_lowercase().as_str() {
                        "prompt_tokens" | "input_tokens" => Some("input_tokens"),
                        "completion_tokens" | "output_tokens" => Some("output_tokens"),
                        "total_tokens" => Some("total_tokens"),
                        "cached_tokens" | "cache_read_input_tokens" => Some("cached_tokens"),
                        "reasoning_tokens" => Some("reasoning_tokens"),
                        _ => None,
                    };
                    if let (Some(name), Some(number)) = (normalized, child.as_i64()) {
                        if number >= 0 {
                            out.entry(name.to_owned())
                                .and_modify(|current| *current = (*current).max(number))
                                .or_insert(number);
                        }
                    } else {
                        visit(child, out)
                    }
                }
            }
            Value::Array(rows) => {
                for row in rows {
                    visit(row, out)
                }
            }
            _ => {}
        }
    }
    let mut output = BTreeMap::new();
    visit(value, &mut output);
    output
}
fn percent_encode(value: &str) -> String {
    value
        .bytes()
        .map(|byte| {
            if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b'~') {
                (byte as char).to_string()
            } else {
                format!("%{byte:02X}")
            }
        })
        .collect()
}
fn parse_action(text: &str) -> Result<Value, String> {
    let mut cleaned = text.trim().to_owned();
    if cleaned.starts_with("```json") {
        cleaned = cleaned.trim_start_matches("```json").trim().to_owned();
    } else if cleaned.starts_with("```") {
        cleaned = cleaned.trim_start_matches("```").trim().to_owned();
    }
    if cleaned.ends_with("```") {
        cleaned = cleaned.trim_end_matches("```").trim().to_owned();
    }
    let direct = serde_json::from_str::<Value>(&cleaned);
    let value = match direct {
        Ok(value) => value,
        Err(_) => {
            let start = cleaned
                .find('{')
                .ok_or_else(|| "model response is not a JSON action".to_owned())?;
            let end = cleaned
                .rfind('}')
                .filter(|end| *end > start)
                .ok_or_else(|| "model response is not a JSON action".to_owned())?;
            serde_json::from_str::<Value>(&cleaned[start..=end])
                .map_err(|error| format!("MODEL_ACTION_JSON_INVALID:{error}"))?
        }
    };
    if !value.is_object() || value["action"].as_str().unwrap_or_default().is_empty() {
        return Err("model action must be a JSON object with an action".to_owned());
    }
    Ok(value)
}
fn append_tool(messages: &mut Vec<Value>, action: &Value, payload: Value) -> Result<(), String> {
    messages.push(json!({"role":"assistant","content":canonical_json(action)?}));
    messages.push(json!({"role":"user","content":canonical_json(&payload)?}));
    Ok(())
}

fn assemble_context(
    project: &Path,
    instruction: &str,
    semantic: &[Value],
) -> Result<Value, String> {
    let project = fs::canonicalize(project)
        .map_err(|error| format!("AGENT_PROJECT_RESOLVE_FAILED:{error}"))?;
    let mut remaining = 120_000usize;
    let mut candidates = vec![
        "AGENTS.md",
        "CLAUDE.md",
        "GEMINI.md",
        "README.md",
        ".github/copilot-instructions.md",
        "pyproject.toml",
        "package.json",
        "pnpm-workspace.yaml",
        "Cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "CMakeLists.txt",
        "Makefile",
    ]
    .into_iter()
    .map(str::to_owned)
    .collect::<Vec<_>>();
    candidates.extend(
        semantic
            .iter()
            .take(12)
            .filter_map(|row| row["path"].as_str())
            .map(str::to_owned),
    );
    let mut seen = BTreeSet::new();
    let mut files = Vec::new();
    for relative in candidates {
        if !seen.insert(relative.clone()) || remaining == 0 {
            continue;
        }
        let path = project.join(&relative);
        if !path.is_file() {
            continue;
        }
        let data = fs::read(&path).map_err(|error| format!("AGENT_CONTEXT_READ_FAILED:{error}"))?;
        let bounded = &data[..data.len().min(remaining)];
        files.push(json!({"path":relative,"bytes":data.len(),"truncated":data.len()>bounded.len(),"sha256":sha256_hex(&data),"content":String::from_utf8_lossy(bounded)}));
        remaining = remaining.saturating_sub(bounded.len());
    }
    Ok(
        json!({"instruction":instruction,"repository":{"root":project.to_string_lossy(),"project_files":files.iter().filter_map(|row|row["path"].as_str()).filter(|path|!["AGENTS.md","CLAUDE.md","GEMINI.md","README.md",".github/copilot-instructions.md"].contains(path)).collect::<Vec<_>>()},"semantic_results":semantic,"files":files,"bounded":remaining==0,"max_bytes":120000}),
    )
}
fn inspect_files(project: &Path, paths: &[String]) -> Result<Vec<Value>, String> {
    let root = fs::canonicalize(project)
        .map_err(|error| format!("AGENT_PROJECT_RESOLVE_FAILED:{error}"))?;
    let mut output = Vec::new();
    let mut total = 0usize;
    for relative in paths.iter().take(24) {
        let candidate = Path::new(relative);
        if candidate.is_absolute()
            || candidate
                .components()
                .any(|part| matches!(part, std::path::Component::ParentDir))
        {
            continue;
        }
        let path = root.join(candidate);
        if !path.is_file() {
            continue;
        }
        let data = fs::read(&path).map_err(|error| format!("AGENT_INSPECT_READ_FAILED:{error}"))?;
        let keep = data.len().min(120_000usize.saturating_sub(total));
        if keep == 0 {
            break;
        }
        output.push(json!({"path":relative,"bytes":data.len(),"truncated":data.len()>keep,"sha256":sha256_hex(&data),"content":String::from_utf8_lossy(&data[..keep])}));
        total += keep;
    }
    Ok(output)
}
fn query_graph(
    project: &Path,
    state_root: &Path,
    query: &str,
    limit: i64,
) -> Result<Vec<Value>, String> {
    let command = vec!["run".to_owned(), "graph-query".to_owned()];
    let args = vec![
        "run".to_owned(),
        "graph-query".to_owned(),
        query.to_owned(),
        "--limit".to_owned(),
        limit.to_string(),
    ];
    Ok(
        super::native_remaining71_graph::execute(&command, &args, project, state_root)?
            .and_then(|value| value["results"].as_array().cloned())
            .unwrap_or_default(),
    )
}
fn graph_impact(project: &Path, state_root: &Path, node: &str) -> Result<Value, String> {
    let command = vec!["run".to_owned(), "graph-impact".to_owned()];
    let args = vec![
        "run".to_owned(),
        "graph-impact".to_owned(),
        node.to_owned(),
        "--max-depth".to_owned(),
        "6".to_owned(),
    ];
    super::native_remaining71_graph::execute(&command, &args, project, state_root)?
        .ok_or_else(|| "AGENT_GRAPH_IMPACT_UNAVAILABLE".to_owned())
}

fn discover_verifiers(project: &Path) -> Result<Vec<Verifier>, String> {
    let root = fs::canonicalize(project)
        .map_err(|error| format!("AGENT_PROJECT_RESOLVE_FAILED:{error}"))?;
    let mut rows = Vec::new();
    let pyproject = root.join("pyproject.toml");
    let pytext = fs::read_to_string(&pyproject).unwrap_or_default();
    if root.join("tests").is_dir()
        || root.join("pytest.ini").is_file()
        || root.join("tox.ini").is_file()
        || pytext.contains("[tool.pytest")
    {
        let python = python_executable();
        if !python.is_empty() {
            rows.push(Verifier {
                name: "pytest".to_owned(),
                argv: vec![
                    python.clone(),
                    "-m".to_owned(),
                    "pytest".to_owned(),
                    "-q".to_owned(),
                ],
                stage: "test".to_owned(),
                confidence: 0.95,
                reason: "Python tests discovered".to_owned(),
                source: "pyproject/tests".to_owned(),
            });
        }
    }
    if pytext.contains("[tool.ruff") || root.join("ruff.toml").is_file() {
        let python = python_executable();
        if !python.is_empty() {
            rows.push(Verifier {
                name: "ruff".to_owned(),
                argv: vec![
                    python,
                    "-m".to_owned(),
                    "ruff".to_owned(),
                    "check".to_owned(),
                    ".".to_owned(),
                ],
                stage: "lint".to_owned(),
                confidence: 0.9,
                reason: "Ruff configuration discovered".to_owned(),
                source: "pyproject/ruff.toml".to_owned(),
            });
        }
    }
    if root.join("package.json").is_file() {
        let package = serde_json::from_slice::<Value>(
            &fs::read(root.join("package.json")).unwrap_or_default(),
        )
        .unwrap_or_else(|_| json!({}));
        let scripts = package["scripts"].as_object().cloned().unwrap_or_default();
        let manager = if root.join("pnpm-lock.yaml").is_file() {
            "pnpm"
        } else if root.join("yarn.lock").is_file() {
            "yarn"
        } else {
            "npm"
        };
        for (name, stage, confidence) in [
            ("test", "test", 0.95),
            ("typecheck", "typecheck", 0.9),
            ("lint", "lint", 0.86),
            ("build", "build", 0.8),
        ] {
            if scripts.contains_key(name) && command_exists(manager) {
                let mut argv = vec![manager.to_owned()];
                if manager != "yarn" {
                    argv.push("run".to_owned())
                }
                argv.push(name.to_owned());
                rows.push(Verifier {
                    name: format!("{manager}-{name}"),
                    argv,
                    stage: stage.to_owned(),
                    confidence,
                    reason: format!("package.json script '{name}'"),
                    source: "package.json".to_owned(),
                });
            }
        }
    }
    for (file, name, argv, stage, confidence, reason) in [
        (
            "Cargo.toml",
            "cargo-test",
            vec!["cargo", "test", "--workspace"],
            "test",
            0.96,
            "Cargo workspace discovered",
        ),
        (
            "go.mod",
            "go-test",
            vec!["go", "test", "./..."],
            "test",
            0.96,
            "Go module discovered",
        ),
        (
            "pom.xml",
            "maven-test",
            vec![
                if root.join("mvnw").is_file() {
                    "./mvnw"
                } else {
                    "mvn"
                },
                "test",
            ],
            "test",
            0.94,
            "Maven project discovered",
        ),
        (
            "CMakeLists.txt",
            "ctest",
            vec!["ctest", "--output-on-failure"],
            "test",
            0.68,
            "CMake project discovered; assumes configured build tree",
        ),
        (
            "Makefile",
            "make-test",
            vec!["make", "test"],
            "test",
            0.55,
            "Makefile discovered; target availability is candidate evidence",
        ),
    ] {
        if root.join(file).exists() && command_exists(argv[0]) {
            rows.push(Verifier {
                name: name.to_owned(),
                argv: argv.into_iter().map(str::to_owned).collect(),
                stage: stage.to_owned(),
                confidence,
                reason: reason.to_owned(),
                source: file.to_owned(),
            });
        }
    }
    if root.join("build.gradle").exists() || root.join("build.gradle.kts").exists() {
        let exe = if root.join("gradlew").is_file() {
            "./gradlew"
        } else {
            "gradle"
        };
        if command_exists(exe) {
            rows.push(Verifier {
                name: "gradle-test".to_owned(),
                argv: vec![exe.to_owned(), "test".to_owned()],
                stage: "test".to_owned(),
                confidence: 0.94,
                reason: "Gradle project discovered".to_owned(),
                source: "build.gradle".to_owned(),
            });
        }
    }
    let mut seen = BTreeSet::new();
    rows.retain(|row| seen.insert(row.argv.clone()));
    Ok(rows)
}
fn stage_rank(value: &str) -> i32 {
    match value {
        "test" => 0,
        "typecheck" => 1,
        "lint" => 2,
        "build" => 3,
        _ => 9,
    }
}
fn python_executable() -> String {
    for name in ["python", "python3"] {
        if command_exists(name) {
            return name.to_owned();
        }
    }
    String::new()
}

fn sandbox_run(
    workspace: &Path,
    state_root: &Path,
    argv: &[String],
    timeout: f64,
) -> Result<Value, String> {
    let command = vec!["run".to_owned(), "sandbox-run".to_owned()];
    let args = vec![
        "run".to_owned(),
        "sandbox-run".to_owned(),
        canonical_json(&Value::Array(
            argv.iter().cloned().map(Value::String).collect(),
        ))?,
        "--timeout".to_owned(),
        timeout.to_string(),
    ];
    Ok(
        super::native_remaining71_sandbox::execute(&command, &args, workspace, state_root)?
            .ok_or_else(|| "AGENT_SANDBOX_UNAVAILABLE".to_owned())?
            .value,
    )
}
fn deliver(
    project: &Path,
    run: &Value,
    mode: &str,
    authorized: bool,
    arguments: &[String],
) -> Result<Value, String> {
    let workspace = PathBuf::from(run["workspace"].as_str().unwrap_or_default());
    let changed = run["changed_files"].as_array().cloned().unwrap_or_default();
    if mode == "diff" || mode == "worktree" {
        return Ok(
            json!({"mode":mode,"ok":true,"workspace":workspace.to_string_lossy(),"branch":"","commit":"","pull_request_url":"","applied_files":changed,"error":""}),
        );
    }
    if !authorized {
        return Ok(
            json!({"mode":mode,"ok":false,"workspace":workspace.to_string_lossy(),"branch":"","commit":"","pull_request_url":"","applied_files":[],"error":"explicit authorization required for repository delivery"}),
        );
    }
    if !run["ok"].as_bool().unwrap_or(false) {
        return Ok(
            json!({"mode":mode,"ok":false,"workspace":workspace.to_string_lossy(),"branch":"","commit":"","pull_request_url":"","applied_files":[],"error":"unverified agent run cannot be delivered"}),
        );
    }
    if mode == "apply" {
        let diff = run["final_diff"].as_str().unwrap_or_default();
        if diff.trim().is_empty() {
            return Ok(
                json!({"mode":mode,"ok":false,"workspace":workspace.to_string_lossy(),"error":"verified run produced no diff"}),
            );
        }
        let check = git_apply_stdin(project, diff, true)?;
        if !check.0 {
            return Ok(
                json!({"mode":mode,"ok":false,"workspace":workspace.to_string_lossy(),"error":check.1}),
            );
        }
        let applied = git_apply_stdin(project, diff, false)?;
        return Ok(
            json!({"mode":mode,"ok":applied.0,"workspace":workspace.to_string_lossy(),"branch":"","commit":"","pull_request_url":"","applied_files":changed,"error":if applied.0{""}else{&applied.1}}),
        );
    }
    let branch = option_value(arguments, "--branch-name")?
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| {
            format!(
                "syntavra/agent-{}",
                run["run_id"]
                    .as_str()
                    .unwrap_or("run")
                    .rsplit(':')
                    .next()
                    .unwrap_or("run")
                    .chars()
                    .take(12)
                    .collect::<String>()
            )
        });
    if !safe_branch(&branch) {
        return Err("delivery branch name is invalid".to_owned());
    }
    let switch = run_command(&["git", "switch", "-c", &branch], &workspace, 120.0)?;
    if !switch.0 {
        return Ok(
            json!({"mode":mode,"ok":false,"workspace":workspace.to_string_lossy(),"branch":branch,"error":switch.1}),
        );
    }
    let add = run_command(&["git", "add", "-A"], &workspace, 120.0)?;
    if !add.0 {
        return Ok(
            json!({"mode":mode,"ok":false,"workspace":workspace.to_string_lossy(),"branch":branch,"error":add.1}),
        );
    }
    let message = option_value(arguments, "--commit-message")?
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| {
            format!(
                "fix: {}",
                run["task"]["instruction"]
                    .as_str()
                    .unwrap_or("Syntavra agent change")
                    .chars()
                    .take(72)
                    .collect::<String>()
            )
        });
    let commit = run_command(&["git", "commit", "-m", &message], &workspace, 120.0)?;
    if !commit.0 {
        return Ok(
            json!({"mode":mode,"ok":false,"workspace":workspace.to_string_lossy(),"branch":branch,"error":commit.1}),
        );
    }
    let sha = run_command_capture(&["git", "rev-parse", "HEAD"], &workspace, 120.0)?
        .0
        .trim()
        .to_owned();
    if mode == "commit" {
        return Ok(
            json!({"mode":mode,"ok":true,"workspace":workspace.to_string_lossy(),"branch":branch,"commit":sha,"pull_request_url":"","applied_files":changed,"error":""}),
        );
    }
    if !command_exists("gh") {
        return Ok(
            json!({"mode":mode,"ok":false,"workspace":workspace.to_string_lossy(),"branch":branch,"commit":sha,"pull_request_url":"","applied_files":changed,"error":"gh CLI is required for PR delivery"}),
        );
    }
    let push = run_command(&["git", "push", "-u", "origin", &branch], &workspace, 300.0)?;
    if !push.0 {
        return Ok(
            json!({"mode":mode,"ok":false,"workspace":workspace.to_string_lossy(),"branch":branch,"commit":sha,"error":push.1}),
        );
    }
    let title = option_value(arguments, "--pr-title")?
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| message.clone());
    let body = option_value(arguments, "--pr-body")?
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| "Created by Syntavra after all discovered verifiers passed.".to_owned());
    let (out, err, ok) = run_command_capture_full(
        &[
            "gh", "pr", "create", "--draft", "--title", &title, "--body", &body, "--head", &branch,
        ],
        &workspace,
        300.0,
    )?;
    let url = out
        .lines()
        .find(|line| line.trim().starts_with("http"))
        .unwrap_or_default()
        .trim()
        .to_owned();
    Ok(
        json!({"mode":mode,"ok":ok&&!url.is_empty(),"workspace":workspace.to_string_lossy(),"branch":branch,"commit":sha,"pull_request_url":url,"applied_files":changed,"error":if ok{""}else{err.as_str()}}),
    )
}
fn safe_branch(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 120
        && !value.contains("..")
        && !value.ends_with('/')
        && !value.ends_with(".lock")
        && value
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '/' | '-'))
}
fn git_apply_stdin(project: &Path, diff: &str, check: bool) -> Result<(bool, String), String> {
    let mut command = Command::new("git");
    command.arg("apply");
    if check {
        command.arg("--check");
    }
    command
        .arg("-")
        .current_dir(project)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = command
        .spawn()
        .map_err(|error| format!("GIT_APPLY_SPAWN_FAILED:{error}"))?;
    if let Some(stdin) = child.stdin.as_mut() {
        stdin
            .write_all(diff.as_bytes())
            .map_err(|error| format!("GIT_APPLY_STDIN_FAILED:{error}"))?;
    }
    let output = child
        .wait_with_output()
        .map_err(|error| format!("GIT_APPLY_WAIT_FAILED:{error}"))?;
    Ok((
        output.status.success(),
        String::from_utf8_lossy(&output.stderr).trim().to_owned(),
    ))
}
fn run_command(argv: &[&str], cwd: &Path, timeout: f64) -> Result<(bool, String), String> {
    let (_, err, ok) = run_command_capture_full(argv, cwd, timeout)?;
    Ok((ok, err))
}
fn run_command_capture(
    argv: &[&str],
    cwd: &Path,
    timeout: f64,
) -> Result<(String, String), String> {
    let (out, err, _) = run_command_capture_full(argv, cwd, timeout)?;
    Ok((out, err))
}
fn run_command_capture_full(
    argv: &[&str],
    cwd: &Path,
    _timeout: f64,
) -> Result<(String, String, bool), String> {
    let (first, rest) = argv
        .split_first()
        .ok_or_else(|| "COMMAND_EMPTY".to_owned())?;
    let output = Command::new(first)
        .args(rest)
        .current_dir(cwd)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .map_err(|error| format!("COMMAND_RUN_FAILED:{error}"))?;
    Ok((
        String::from_utf8_lossy(&output.stdout).into_owned(),
        String::from_utf8_lossy(&output.stderr).into_owned(),
        output.status.success(),
    ))
}

fn push_journal_event(events: &mut Vec<Value>, event: &str, payload: Value) -> Result<(), String> {
    let sequence = events.len() + 1;
    events.push(json!({
        "sequence":sequence,
        "event_type":event,
        "created_at":now_seconds()?,
        "payload":payload,
    }));
    Ok(())
}
fn emit_event(arguments: &[String], event: &str, payload: Value) -> Result<(), String> {
    let Some(target) = option_value(arguments, "--events-jsonl")? else {
        return Ok(());
    };
    let body = json!({"event_type":event,"created_at":now_seconds()?,"payload":payload});
    let line = canonical_json(&body)? + "\n";
    if target == "-" {
        eprint!("{line}");
        return Ok(());
    }
    let path = Path::new(&target);
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| format!("AGENT_EVENT_PARENT_FAILED:{error}"))?;
    }
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|error| format!("AGENT_EVENT_OPEN_FAILED:{error}"))?;
    file.write_all(line.as_bytes())
        .map_err(|error| format!("AGENT_EVENT_WRITE_FAILED:{error}"))
}
fn set_private(path: &Path) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if let Ok(metadata) = fs::metadata(path) {
            let mut permissions = metadata.permissions();
            permissions.set_mode(0o600);
            let _ = fs::set_permissions(path, permissions);
        }
    }
}
fn random_hex(bytes: usize) -> String {
    let mut raw = vec![0u8; bytes];
    OsRng.fill_bytes(&mut raw);
    raw.iter().map(|byte| format!("{byte:02x}")).collect()
}
fn tail(value: &str, limit: usize) -> String {
    if value.len() <= limit {
        return value.to_owned();
    }
    let mut start = value.len() - limit;
    while start < value.len() && !value.is_char_boundary(start) {
        start += 1
    }
    value[start..].to_owned()
}
fn command_exists(name: &str) -> bool {
    let candidate = Path::new(name);
    if candidate.components().count() > 1 {
        return candidate.exists();
    }
    std::env::var_os("PATH").is_some_and(|path| {
        std::env::split_paths(&path).any(|dir| {
            dir.join(name).is_file()
                || cfg!(windows)
                    && [".exe", ".cmd", ".bat"]
                        .iter()
                        .any(|suffix| dir.join(format!("{name}{suffix}")).is_file())
        })
    })
}
fn canonical_json(value: &Value) -> Result<String, String> {
    serde_json::to_string(&sort_json(value))
        .map_err(|error| format!("AGENT_JSON_SERIALIZE_FAILED:{error}"))
}
fn sort_json(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            let mut keys = map.keys().collect::<Vec<_>>();
            keys.sort_unstable();
            let mut output = Map::new();
            for key in keys {
                output.insert(key.clone(), sort_json(&map[key]));
            }
            Value::Object(output)
        }
        Value::Array(rows) => Value::Array(rows.iter().map(sort_json).collect()),
        _ => value.clone(),
    }
}
fn now_seconds() -> Result<f64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_secs_f64())
        .map_err(|error| format!("AGENT_CLOCK_FAILED:{error}"))
}
fn has_flag(arguments: &[String], flag: &str) -> bool {
    arguments.iter().any(|value| value == flag)
}
fn option_value(arguments: &[String], flag: &str) -> Result<Option<String>, String> {
    let mut output = None;
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
            output = Some(found);
        }
        index += 1;
    }
    Ok(output)
}
fn required_option(arguments: &[String], flag: &str) -> Result<String, String> {
    option_value(arguments, flag)?
        .filter(|value| !value.is_empty())
        .ok_or_else(|| format!("{flag}_REQUIRED"))
}
fn option_i64(arguments: &[String], flag: &str, default: i64) -> Result<i64, String> {
    option_value(arguments, flag)?
        .map(|value| value.parse::<i64>().map_err(|_| format!("{flag}_INVALID")))
        .transpose()
        .map(|value| value.unwrap_or(default))
}
fn option_f64(arguments: &[String], flag: &str, default: f64) -> Result<f64, String> {
    option_value(arguments, flag)?
        .map(|value| value.parse::<f64>().map_err(|_| format!("{flag}_INVALID")))
        .transpose()
        .map(|value| value.unwrap_or(default))
}
fn positional_after<'a>(
    arguments: &'a [String],
    root: &str,
    action: &str,
    position: usize,
    value_flags: &[&str],
) -> Result<&'a str, String> {
    let mut index = arguments
        .windows(2)
        .position(|row| row[0] == root && row[1] == action)
        .map(|index| index + 2)
        .ok_or_else(|| format!("AGENT_ACTION_NOT_FOUND:{root}:{action}"))?;
    let mut values = Vec::new();
    while index < arguments.len() {
        if value_flags.contains(&arguments[index].as_str()) {
            index += 2;
            continue;
        }
        if arguments[index].starts_with("--") {
            index += 1;
            continue;
        }
        values.push(arguments[index].as_str());
        index += 1;
    }
    values
        .get(position)
        .copied()
        .ok_or_else(|| format!("AGENT_POSITIONAL_MISSING:{root}:{action}:{position}"))
}
