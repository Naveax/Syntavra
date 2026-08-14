#![forbid(unsafe_code)]
#![allow(clippy::too_many_lines)]

use std::collections::{BTreeSet, HashSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use rand::{rngs::OsRng, RngCore as _};
use serde_json::{json, Map, Value};
use syntavra_core::sha256_hex;

const MODES: &[&str] = &[
    "plan-only",
    "review-required",
    "safe-autonomous",
    "headless",
    "ci",
    "remote",
];

#[derive(Debug, Clone)]
struct Proposal {
    patch: String,
    rationale: String,
    estimated_tokens: i64,
    estimated_cost: f64,
}

pub(crate) fn supports(command: &[String]) -> bool {
    matches!(command, [root, action]
        if (root == "run" && matches!(action.as_str(), "agent-plan" | "agent-execute"))
            || (root == "agent" && action == "replay"))
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
    match command {
        [root, action] if root == "run" && action == "agent-plan" => {
            agent_plan(arguments, project, state_root).map(Some)
        }
        [root, action] if root == "run" && action == "agent-execute" => {
            let value = execute_sequence(arguments, project, state_root, "agent-execute", false)?;
            Ok(Some(value))
        }
        [root, action] if root == "agent" && action == "replay" => {
            let value = execute_sequence(arguments, project, state_root, "agent-replay", true)?;
            Ok(Some(value))
        }
        _ => Ok(None),
    }
}

fn agent_plan(arguments: &[String], project: &Path, state_root: &Path) -> Result<Value, String> {
    let task = positional_after(
        arguments,
        "run",
        "agent-plan",
        0,
        &["--session-id", "--max-symbols"],
    )?;
    let max_symbols = option_i64(arguments, "--max-symbols", 12)?.clamp(1, 200);
    if has_flag(arguments, "--index") {
        let command = vec!["run".to_owned(), "graph-index".to_owned()];
        let synthetic = vec!["run".to_owned(), "graph-index".to_owned()];
        let _ =
            super::native_remaining71_graph::execute(&command, &synthetic, project, state_root)?;
    }
    let query_command = vec!["run".to_owned(), "graph-query".to_owned()];
    let query_arguments = vec![
        "run".to_owned(),
        "graph-query".to_owned(),
        task.to_owned(),
        "--limit".to_owned(),
        max_symbols.to_string(),
    ];
    let graph = super::native_remaining71_graph::execute(
        &query_command,
        &query_arguments,
        project,
        state_root,
    )?
    .unwrap_or_else(|| json!({"results":[]}));
    let symbols = graph["results"].as_array().cloned().unwrap_or_default();
    let mut seen = BTreeSet::new();
    let candidate_paths = symbols
        .iter()
        .filter_map(|row| row["path"].as_str())
        .filter(|path| seen.insert((*path).to_owned()))
        .map(str::to_owned)
        .collect::<Vec<_>>();

    let session_id = option_value(arguments, "--session-id")?
        .unwrap_or_else(|| format!("session-{}", random_hex(12)));
    let open_command = vec!["run".to_owned(), "memory-open".to_owned()];
    let open_arguments = vec![
        "run".to_owned(),
        "memory-open".to_owned(),
        "--session-id".to_owned(),
        session_id.clone(),
        "--metadata".to_owned(),
        json!({"goal":task,"agent":"reference-v2"}).to_string(),
    ];
    let _ = super::native_remaining71_memory::execute(
        &open_command,
        &open_arguments,
        project,
        state_root,
    );
    append_memory(
        state_root,
        project,
        &session_id,
        "task",
        &json!({"goal":task}),
    );

    let steps = vec![
        json!({"id":"understand","tool":"repo.search","arguments":{"query":task},"resource":"workspace:/","requires":[]}),
        json!({"id":"inspect","tool":"repo.read","arguments":{"paths":candidate_paths.iter().take(8).cloned().collect::<Vec<_>>()},"resource":"workspace:/","requires":["understand"]}),
        json!({"id":"patch","tool":"repo.patch","arguments":{"paths":candidate_paths.iter().take(8).cloned().collect::<Vec<_>>()},"resource":"workspace:/","requires":["inspect","explicit-user-authorization"]}),
        json!({"id":"test","tool":"test.run","arguments":{"strategy":"affected-tests"},"resource":"workspace:/","requires":["patch","sandbox","explicit-user-authorization"]}),
        json!({"id":"finish","tool":"task.finish","arguments":{"receipt":true},"resource":"workspace:/","requires":["test"]}),
    ];
    let mut decisions = Vec::new();
    for step in &steps {
        let tool = step["tool"].as_str().unwrap_or_default();
        let args = step["arguments"].clone();
        let command = vec!["run".to_owned(), "capability-decide".to_owned()];
        let mut synthetic = vec![
            "run".to_owned(),
            "capability-decide".to_owned(),
            tool.to_owned(),
            canonical_json(&args)?,
            "--resource".to_owned(),
            "workspace:/".to_owned(),
            "--sandboxed".to_owned(),
        ];
        if !matches!(tool, "repo.patch" | "test.run") {
            synthetic.push("--user-authorized".to_owned());
        }
        let decision =
            super::native_remaining71_security::execute(&command, &synthetic, state_root)?
                .unwrap_or_else(
                    || json!({"allowed":false,"reason":"native-capability-unavailable"}),
                );
        decisions.push(decision);
    }
    let mut plan = json!({
        "version":"0.0.1",
        "channel":"pre-release",
        "session_id":session_id,
        "task":task,
        "candidate_symbols":symbols,
        "candidate_paths":candidate_paths,
        "steps":steps,
        "preflight_decisions":decisions,
        "execution_mode":"plan-only-until-authorized",
        "worktree":{"required":true,"isolation":"git-worktree","rollback":"discard-worktree"},
    });
    let plan_hash = sha256_hex(canonical_json(&plan)?.as_bytes());
    plan["plan_hash"] = Value::String(plan_hash.clone());
    append_memory(
        state_root,
        project,
        &session_id,
        "plan",
        &json!({"plan_hash":plan_hash,"paths":candidate_paths,"steps":["understand","inspect","patch","test","finish"]}),
    );
    Ok(plan)
}

fn append_memory(state_root: &Path, project: &Path, session: &str, event: &str, payload: &Value) {
    let command = vec!["run".to_owned(), "memory-append".to_owned()];
    let arguments = vec![
        "run".to_owned(),
        "memory-append".to_owned(),
        session.to_owned(),
        event.to_owned(),
        payload.to_string(),
    ];
    let _ = super::native_remaining71_memory::execute(&command, &arguments, project, state_root);
}

fn execute_sequence(
    arguments: &[String],
    project: &Path,
    state_root: &Path,
    surface: &str,
    direct_replay: bool,
) -> Result<Value, String> {
    let (root, action) = if direct_replay {
        ("agent", "replay")
    } else {
        ("run", "agent-execute")
    };
    let value_flags = [
        "--mode",
        "--attempts",
        "--timeout",
        "--token-budget",
        "--cost-budget",
        "--session-id",
    ];
    let task = positional_after(arguments, root, action, 0, &value_flags)?;
    let patches_raw = positional_after(arguments, root, action, 1, &value_flags)?;
    let verifier_raw = positional_after(arguments, root, action, 2, &value_flags)?;
    let patches = load_proposals(patches_raw)?;
    let verifier = load_argv(verifier_raw, "agent verifier")?;
    let mode = option_value(arguments, "--mode")?.unwrap_or_else(|| "review-required".to_owned());
    if !MODES.contains(&mode.as_str()) {
        return Err(format!("AGENT_MODE_INVALID:{mode}"));
    }
    let attempts = option_i64(arguments, "--attempts", 3)?;
    if !(1..=20).contains(&attempts) {
        return Err("max_attempts must be between 1 and 20".to_owned());
    }
    let timeout = option_f64(arguments, "--timeout", 900.0)?.max(0.1);
    let token_budget = option_value(arguments, "--token-budget")?
        .map(|value| {
            value
                .parse::<i64>()
                .map_err(|_| "AGENT_TOKEN_BUDGET_INVALID".to_owned())
        })
        .transpose()?;
    let cost_budget = option_value(arguments, "--cost-budget")?
        .map(|value| {
            value
                .parse::<f64>()
                .map_err(|_| "AGENT_COST_BUDGET_INVALID".to_owned())
        })
        .transpose()?;
    let authorized = has_flag(arguments, "--authorized");
    let retain_workspace = if direct_replay {
        true
    } else {
        has_flag(arguments, "--retain-workspace")
    };
    let session_id = option_value(arguments, "--session-id")?;
    let mut value = run_agent(
        project,
        state_root,
        task,
        &verifier,
        &patches,
        &mode,
        attempts,
        timeout,
        token_budget,
        cost_budget,
        authorized,
        session_id.as_deref(),
        retain_workspace,
        surface,
    )?;
    value["ok"] = Value::Bool(value["state"].as_str() == Some("completed"));
    if direct_replay {
        value["surface"] = Value::String("agent-replay".to_owned());
    }
    Ok(value)
}

#[allow(clippy::too_many_arguments)]
fn run_agent(
    project: &Path,
    state_root: &Path,
    instruction: &str,
    verifier: &[String],
    patches: &[Proposal],
    mode: &str,
    max_attempts: i64,
    timeout: f64,
    token_budget: Option<i64>,
    cost_budget: Option<f64>,
    authorized: bool,
    session_id: Option<&str>,
    retain_workspace: bool,
    surface: &str,
) -> Result<Value, String> {
    if instruction.trim().is_empty() || verifier.is_empty() {
        return Err("task instruction and verifier argv are required".to_owned());
    }
    let started_at = now_iso()?;
    let started = Instant::now();
    let (workspace, git_worktree) = create_workspace(project, state_root)?;
    let semantic_results = agent_query(project, state_root, instruction, 20)?;
    let base_context = json!({
        "instruction":instruction,
        "project":fs::canonicalize(project).unwrap_or_else(|_|project.to_path_buf()).to_string_lossy(),
        "mode":mode,
        "semantic_results":semantic_results,
        "budgets":{"tokens":token_budget,"cost":cost_budget,"attempts":max_attempts},
    });
    let run_id = format!(
        "sha256:{}",
        sha256_hex(
            canonical_json(&json!({"task":instruction,"workspace":workspace.to_string_lossy(),"started":started_at}))?
                .as_bytes()
        )
    );
    if let Some(session) = session_id {
        append_memory(
            state_root,
            project,
            session,
            "agent-run-started",
            &json!({"run_id":run_id,"task":instruction,"mode":mode}),
        );
    }
    if mode == "plan-only" {
        let rollback_complete = if retain_workspace {
            true
        } else {
            cleanup_workspace(project, &workspace, git_worktree)
        };
        return finish_receipt(
            state_root,
            surface,
            &run_id,
            instruction,
            verifier,
            mode,
            max_attempts,
            timeout,
            token_budget,
            cost_budget,
            retain_workspace,
            &started_at,
            started,
            &workspace,
            Vec::new(),
            0,
            0.0,
            Vec::new(),
            "",
            rollback_complete,
            "completed",
            "plan-only",
            base_context,
        );
    }
    if mode == "review-required" && !authorized {
        let rollback_complete = if retain_workspace {
            true
        } else {
            cleanup_workspace(project, &workspace, git_worktree)
        };
        return finish_receipt(
            state_root,
            surface,
            &run_id,
            instruction,
            verifier,
            mode,
            max_attempts,
            timeout,
            token_budget,
            cost_budget,
            retain_workspace,
            &started_at,
            started,
            &workspace,
            Vec::new(),
            0,
            0.0,
            Vec::new(),
            "",
            rollback_complete,
            "blocked",
            "explicit authorization required",
            base_context,
        );
    }

    let mut seen_patches = HashSet::<String>::new();
    let mut seen_failures = HashSet::<String>::new();
    let mut attempts_out = Vec::<Value>::new();
    let mut total_tokens = 0i64;
    let mut total_cost = 0.0f64;
    let mut final_state = "failed".to_owned();
    let mut stop_reason = "attempt limit reached".to_owned();
    let mut previous_failure: Option<Value> = None;
    let mut context = base_context.clone();

    for number in 1..=max_attempts {
        let (current_diff, current_changed) = git_diff(&workspace);
        context = merge_object(
            &base_context,
            json!({
                "workspace":workspace.to_string_lossy(),
                "attempt":number,
                "current_diff":tail_chars(&current_diff,120_000),
                "changed_files":current_changed,
                "previous_failure":previous_failure,
            }),
        );
        let Some(proposal) = patches.get(usize::try_from(number - 1).unwrap_or(usize::MAX)) else {
            stop_reason = "empty patch proposal".to_owned();
            break;
        };
        if proposal.patch.trim().is_empty() {
            stop_reason = "empty patch proposal".to_owned();
            break;
        }
        let patch_hash = sha256_hex(proposal.patch.as_bytes());
        if !seen_patches.insert(patch_hash.clone()) {
            stop_reason = "anti-loop: repeated patch".to_owned();
            break;
        }
        total_tokens += proposal.estimated_tokens.max(0);
        total_cost += proposal.estimated_cost.max(0.0);
        if token_budget.is_some_and(|limit| total_tokens > limit) {
            stop_reason = "token budget exceeded".to_owned();
            break;
        }
        if cost_budget.is_some_and(|limit| total_cost > limit) {
            stop_reason = "cost budget exceeded".to_owned();
            break;
        }
        let apply = apply_patch(&workspace, state_root, &proposal.patch, timeout)?;
        if !sandbox_ok(&apply) {
            let failure_text = format!(
                "{}{}",
                apply["stderr"].as_str().unwrap_or_default(),
                apply["stdout"].as_str().unwrap_or_default()
            );
            let fingerprint = sha256_hex(failure_text.as_bytes());
            previous_failure = Some(json!({
                "kind":"patch-apply","text":failure_text,"fingerprint":fingerprint
            }));
            attempts_out.push(json!({
                "number":number,"patch_sha256":patch_hash,"patch_applied":false,
                "verifier":apply,"failure_fingerprint":fingerprint,"rationale":proposal.rationale,
                "tokens":proposal.estimated_tokens,"cost":proposal.estimated_cost,"state":"diagnosing"
            }));
            if !seen_failures.insert(fingerprint.clone()) {
                stop_reason = "anti-loop: repeated patch application failure".to_owned();
                break;
            }
            continue;
        }
        let verify = sandbox_run(&workspace, state_root, verifier, timeout)?;
        if sandbox_ok(&verify) {
            attempts_out.push(json!({
                "number":number,"patch_sha256":patch_hash,"patch_applied":true,
                "verifier":verify,"failure_fingerprint":"","rationale":proposal.rationale,
                "tokens":proposal.estimated_tokens,"cost":proposal.estimated_cost,"state":"completed"
            }));
            final_state = "completed".to_owned();
            stop_reason = "verifier passed".to_owned();
            break;
        }
        let text = format!(
            "{}\n{}",
            tail_chars(verify["stderr"].as_str().unwrap_or_default(), 8_000),
            tail_chars(verify["stdout"].as_str().unwrap_or_default(), 8_000)
        )
        .trim()
        .to_owned();
        let fingerprint = sha256_hex(format!("{}\0{}", verify["exit_code"], text).as_bytes());
        previous_failure = Some(json!({
            "kind":if verify["timed_out"].as_bool().unwrap_or(false){"timeout"}else{"verifier"},
            "exit_code":verify["exit_code"],"text":text,"fingerprint":fingerprint
        }));
        attempts_out.push(json!({
            "number":number,"patch_sha256":patch_hash,"patch_applied":true,
            "verifier":verify,"failure_fingerprint":fingerprint,"rationale":proposal.rationale,
            "tokens":proposal.estimated_tokens,"cost":proposal.estimated_cost,"state":"repairing"
        }));
        if !seen_failures.insert(fingerprint.clone()) {
            stop_reason = "anti-loop: repeated verifier failure".to_owned();
            break;
        }
    }
    let (final_diff, changed_files) = git_diff(&workspace);
    let mut rollback_complete = true;
    if final_state != "completed" || !retain_workspace {
        rollback_complete = cleanup_workspace(project, &workspace, git_worktree);
        if final_state != "completed" && rollback_complete {
            final_state = "rolled-back".to_owned();
        }
    }
    let receipt = finish_receipt(
        state_root,
        surface,
        &run_id,
        instruction,
        verifier,
        mode,
        max_attempts,
        timeout,
        token_budget,
        cost_budget,
        retain_workspace,
        &started_at,
        started,
        &workspace,
        attempts_out,
        total_tokens,
        total_cost,
        changed_files,
        &final_diff,
        rollback_complete,
        &final_state,
        &stop_reason,
        context,
    )?;
    if let Some(session) = session_id {
        append_memory(
            state_root,
            project,
            session,
            "agent-run-finished",
            &json!({"run_id":run_id,"state":final_state,"stop_reason":stop_reason}),
        );
    }
    Ok(receipt)
}

#[allow(clippy::too_many_arguments)]
fn finish_receipt(
    state_root: &Path,
    _surface: &str,
    run_id: &str,
    instruction: &str,
    verifier: &[String],
    mode: &str,
    max_attempts: i64,
    timeout: f64,
    token_budget: Option<i64>,
    cost_budget: Option<f64>,
    retain_workspace: bool,
    started_at: &str,
    started: Instant,
    workspace: &Path,
    attempts: Vec<Value>,
    total_tokens: i64,
    total_cost: f64,
    changed_files: Vec<String>,
    final_diff: &str,
    rollback_complete: bool,
    state: &str,
    stop_reason: &str,
    context: Value,
) -> Result<Value, String> {
    let task = json!({
        "instruction":instruction,"verifier":verifier,"mode":mode,"max_attempts":max_attempts,
        "timeout_seconds":timeout,"token_budget":token_budget,"cost_budget":cost_budget,
        "retain_workspace":retain_workspace,"metadata":{}
    });
    let receipt = json!({
        "run_id":run_id,"task":task,"state":state,"started_at":started_at,"finished_at":now_iso()?,
        "duration_ms":((started.elapsed().as_secs_f64()*1000.0)*1000.0).round()/1000.0,
        "workspace":workspace.to_string_lossy(),"attempts":attempts,"total_tokens":total_tokens,
        "total_cost":(total_cost*100_000_000.0).round()/100_000_000.0,"changed_files":changed_files,
        "final_diff":final_diff,"rollback_complete":rollback_complete,"stop_reason":stop_reason,"context":context,
    });
    if state != "blocked" && stop_reason != "plan-only" {
        persist_receipt(state_root, &receipt)?;
    }
    Ok(receipt)
}

pub(crate) fn persist_receipt(state_root: &Path, receipt: &Value) -> Result<(), String> {
    let run_id = receipt["run_id"]
        .as_str()
        .ok_or_else(|| "AGENT_RECEIPT_RUN_ID_MISSING".to_owned())?;
    let destination = state_root
        .join("unified")
        .join("agent-receipts")
        .join(format!("{}.json", run_id.trim_start_matches("sha256:")));
    if let Some(parent) = destination.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("AGENT_RECEIPT_PARENT_FAILED:{error}"))?;
    }
    let mut durable = receipt.clone();
    if let Some(object) = durable.as_object_mut() {
        object.remove("ok");
        object.remove("surface");
    }
    fs::write(
        &destination,
        serde_json::to_vec_pretty(&sort_json(&durable))
            .map_err(|error| format!("AGENT_RECEIPT_JSON_FAILED:{error}"))?,
    )
    .map_err(|error| format!("AGENT_RECEIPT_WRITE_FAILED:{error}"))
}
fn agent_query(
    project: &Path,
    state_root: &Path,
    instruction: &str,
    limit: i64,
) -> Result<Vec<Value>, String> {
    let command = vec!["run".to_owned(), "graph-query".to_owned()];
    let arguments = vec![
        "run".to_owned(),
        "graph-query".to_owned(),
        instruction.to_owned(),
        "--limit".to_owned(),
        limit.to_string(),
    ];
    Ok(
        super::native_remaining71_graph::execute(&command, &arguments, project, state_root)?
            .and_then(|value| value["results"].as_array().cloned())
            .unwrap_or_default(),
    )
}

fn apply_patch(
    workspace: &Path,
    state_root: &Path,
    patch: &str,
    timeout: f64,
) -> Result<Value, String> {
    let patch_path = workspace.join(format!(".syntavra-agent-{}.diff", random_hex(8)));
    fs::write(&patch_path, patch.as_bytes())
        .map_err(|error| format!("AGENT_PATCH_WRITE_FAILED:{error}"))?;
    let command = vec![
        "git".to_owned(),
        "apply".to_owned(),
        "--whitespace=nowarn".to_owned(),
        patch_path.to_string_lossy().into_owned(),
    ];
    let result = sandbox_run(workspace, state_root, &command, timeout.min(120.0));
    let _ = fs::remove_file(&patch_path);
    result
}

fn sandbox_run(
    workspace: &Path,
    state_root: &Path,
    argv: &[String],
    timeout: f64,
) -> Result<Value, String> {
    let command = vec!["run".to_owned(), "sandbox-run".to_owned()];
    let arguments = vec![
        "run".to_owned(),
        "sandbox-run".to_owned(),
        canonical_json(&Value::Array(
            argv.iter().cloned().map(Value::String).collect(),
        ))?,
        "--timeout".to_owned(),
        timeout.to_string(),
    ];
    let execution =
        super::native_remaining71_sandbox::execute(&command, &arguments, workspace, state_root)?
            .ok_or_else(|| "AGENT_SANDBOX_ROUTE_UNAVAILABLE".to_owned())?;
    let mut value = execution.value;
    if let Some(object) = value.as_object_mut() {
        object.remove("ok");
    }
    Ok(value)
}

fn sandbox_ok(value: &Value) -> bool {
    value["exit_code"].as_i64() == Some(0) && !value["timed_out"].as_bool().unwrap_or(false)
}

fn create_workspace(project: &Path, state_root: &Path) -> Result<(PathBuf, bool), String> {
    let project = fs::canonicalize(project)
        .map_err(|error| format!("AGENT_PROJECT_RESOLVE_FAILED:{error}"))?;
    let root = state_root.join("unified").join("agent-workspaces");
    fs::create_dir_all(&root).map_err(|error| format!("AGENT_WORKSPACE_ROOT_FAILED:{error}"))?;
    let destination = root.join(format!("run-{}", random_hex(12)));
    if project.join(".git").exists() && command_exists("git") {
        let output = Command::new("git")
            .args([
                "-C",
                project.to_str().unwrap_or_default(),
                "worktree",
                "add",
                "--detach",
                destination.to_str().unwrap_or_default(),
                "HEAD",
            ])
            .stdin(Stdio::null())
            .output();
        if output.as_ref().is_ok_and(|value| value.status.success()) {
            return Ok((destination, true));
        }
        let _ = fs::remove_dir_all(&destination);
    }
    fs::create_dir_all(&destination)
        .map_err(|error| format!("AGENT_WORKSPACE_CREATE_FAILED:{error}"))?;
    copy_tree(&project, &destination)?;
    Ok((destination, false))
}

fn copy_tree(source: &Path, destination: &Path) -> Result<(), String> {
    let ignored = [
        ".git",
        ".syntavra",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        "__pycache__",
    ];
    let mut entries = fs::read_dir(source)
        .map_err(|error| format!("AGENT_COPY_READ_FAILED:{}:{error}", source.display()))?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("AGENT_COPY_ENTRY_FAILED:{error}"))?;
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries {
        let name = entry.file_name();
        let name_text = name.to_string_lossy();
        if ignored.contains(&name_text.as_ref()) {
            continue;
        }
        let from = entry.path();
        let to = destination.join(&name);
        let kind = entry
            .file_type()
            .map_err(|error| format!("AGENT_COPY_TYPE_FAILED:{error}"))?;
        if kind.is_dir() {
            fs::create_dir_all(&to).map_err(|error| format!("AGENT_COPY_DIR_FAILED:{error}"))?;
            copy_tree(&from, &to)?;
        } else if kind.is_file() {
            fs::copy(&from, &to).map_err(|error| format!("AGENT_COPY_FILE_FAILED:{error}"))?;
        }
    }
    Ok(())
}

fn cleanup_workspace(project: &Path, workspace: &Path, git_worktree: bool) -> bool {
    if git_worktree && command_exists("git") {
        let removed = Command::new("git")
            .args([
                "-C",
                project.to_str().unwrap_or_default(),
                "worktree",
                "remove",
                "--force",
                workspace.to_str().unwrap_or_default(),
            ])
            .stdin(Stdio::null())
            .output()
            .is_ok_and(|value| value.status.success());
        let _ = Command::new("git")
            .args([
                "-C",
                project.to_str().unwrap_or_default(),
                "worktree",
                "prune",
            ])
            .stdin(Stdio::null())
            .output();
        return removed;
    }
    let _ = fs::remove_dir_all(workspace);
    !workspace.exists()
}

fn git_diff(workspace: &Path) -> (String, Vec<String>) {
    if !workspace.join(".git").exists() || !command_exists("git") {
        return (String::new(), Vec::new());
    }
    let diff = Command::new("git")
        .args([
            "-C",
            workspace.to_str().unwrap_or_default(),
            "diff",
            "--binary",
            "--no-ext-diff",
        ])
        .output()
        .ok()
        .map(|value| String::from_utf8_lossy(&value.stdout).into_owned())
        .unwrap_or_default();
    let names = Command::new("git")
        .args([
            "-C",
            workspace.to_str().unwrap_or_default(),
            "diff",
            "--name-only",
        ])
        .output()
        .ok()
        .map(|value| {
            String::from_utf8_lossy(&value.stdout)
                .lines()
                .map(str::to_owned)
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect()
        })
        .unwrap_or_default();
    (diff, names)
}
fn command_exists(name: &str) -> bool {
    if Path::new(name).is_absolute() {
        return Path::new(name).is_file();
    }
    std::env::var_os("PATH").is_some_and(|path| {
        std::env::split_paths(&path).any(|dir| {
            let direct = dir.join(name);
            if direct.is_file() {
                return true;
            }
            if cfg!(windows) {
                [".exe", ".cmd", ".bat"]
                    .iter()
                    .any(|suffix| dir.join(format!("{name}{suffix}")).is_file())
            } else {
                false
            }
        })
    })
}
fn tail_chars(value: &str, limit: usize) -> String {
    if value.len() <= limit {
        return value.to_owned();
    }
    let mut start = value.len() - limit;
    while start < value.len() && !value.is_char_boundary(start) {
        start += 1;
    }
    value[start..].to_owned()
}

fn load_proposals(value: &str) -> Result<Vec<Proposal>, String> {
    let value = load_json(value)?;
    let rows = value
        .as_array()
        .ok_or_else(|| "agent patches must be a JSON list".to_owned())?;
    rows.iter()
        .map(|row| {
            if let Some(text) = row.as_str() {
                Ok(Proposal {
                    patch: text.to_owned(),
                    rationale: String::new(),
                    estimated_tokens: 0,
                    estimated_cost: 0.0,
                })
            } else if let Some(object) = row.as_object() {
                Ok(Proposal {
                    patch: object
                        .get("patch")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .to_owned(),
                    rationale: object
                        .get("rationale")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .to_owned(),
                    estimated_tokens: object
                        .get("estimated_tokens")
                        .and_then(Value::as_i64)
                        .unwrap_or(0),
                    estimated_cost: object
                        .get("estimated_cost")
                        .and_then(Value::as_f64)
                        .unwrap_or(0.0),
                })
            } else {
                Err("agent patch proposal must be a string or object".to_owned())
            }
        })
        .collect()
}
fn load_argv(value: &str, label: &str) -> Result<Vec<String>, String> {
    let value = load_json(value)?;
    let rows = value
        .as_array()
        .ok_or_else(|| format!("{label} must be a non-empty JSON argv list"))?;
    if rows.is_empty() {
        return Err(format!("{label} must be a non-empty JSON argv list"));
    }
    rows.iter()
        .map(|row| {
            row.as_str()
                .filter(|item| !item.contains('\0'))
                .map(str::to_owned)
                .ok_or_else(|| format!("{label} must be a non-empty JSON argv list"))
        })
        .collect()
}
fn load_json(value: &str) -> Result<Value, String> {
    let raw = if Path::new(value).is_file() {
        fs::read_to_string(value).map_err(|error| format!("AGENT_JSON_READ_FAILED:{error}"))?
    } else {
        value.to_owned()
    };
    serde_json::from_str(&raw).map_err(|error| format!("AGENT_JSON_INVALID:{error}"))
}
fn random_hex(bytes: usize) -> String {
    let mut raw = vec![0u8; bytes];
    OsRng.fill_bytes(&mut raw);
    raw.iter().map(|byte| format!("{byte:02x}")).collect()
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
fn merge_object(left: &Value, right: Value) -> Value {
    let mut value = left.clone();
    if let (Value::Object(target), Value::Object(source)) = (&mut value, right) {
        for (key, item) in source {
            target.insert(key, item);
        }
    }
    value
}
fn now_iso() -> Result<String, String> {
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("AGENT_CLOCK_FAILED:{error}"))?;
    let seconds =
        i64::try_from(duration.as_secs()).map_err(|_| "AGENT_CLOCK_RANGE_FAILED".to_owned())?;
    let days = seconds / 86_400;
    let second_of_day = seconds % 86_400;
    let z = days + 719_468;
    let era = z / 146_097;
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
    let hour = second_of_day / 3_600;
    let minute = (second_of_day % 3_600) / 60;
    let second = second_of_day % 60;
    Ok(format!(
        "{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}.{:06}+00:00",
        duration.subsec_micros()
    ))
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
            if output.is_some() {
                return Err(format!("{flag}_DUPLICATE"));
            }
            output = Some(found);
        }
        index += 1;
    }
    Ok(output)
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
    let start = arguments
        .windows(2)
        .position(|row| row[0] == root && row[1] == action)
        .map(|index| index + 2)
        .ok_or_else(|| format!("AGENT_ACTION_NOT_FOUND:{root}:{action}"))?;
    let mut values = Vec::new();
    let mut index = start;
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
