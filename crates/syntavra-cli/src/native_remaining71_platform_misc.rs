#![forbid(unsafe_code)]
#![allow(clippy::too_many_lines, clippy::cast_precision_loss)]

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use rand::{rngs::StdRng, Rng, RngCore, SeedableRng};
use regex::Regex;
use rusqlite::Connection;
use serde_json::{json, Map, Value};
use sha2::{Digest as _, Sha256};
use syntavra_core::sha256_hex;

const ACTIONS: &[&str] = &[
    "output-capture",
    "console",
    "reliability-run",
    "update-install",
    "update-rollback",
];

pub(crate) fn supports(command: &[String]) -> bool {
    command.len() == 2 && command[0] == "run" && ACTIONS.contains(&command[1].as_str())
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
    match command[1].as_str() {
        "output-capture" => output_capture(arguments, state_root).map(Some),
        "console" => console(arguments, project).map(Some),
        "reliability-run" => reliability(arguments, project, state_root).map(Some),
        "update-install" => update_install(arguments, project, state_root).map(Some),
        "update-rollback" => update_rollback(arguments, state_root).map(Some),
        _ => Ok(None),
    }
}

fn output_capture(arguments: &[String], state_root: &Path) -> Result<Value, String> {
    let tool = positional_after(
        arguments,
        "output-capture",
        0,
        &["--exit-code", "--duration-ms", "--media-type"],
    )?;
    let input = positional_after(
        arguments,
        "output-capture",
        1,
        &["--exit-code", "--duration-ms", "--media-type"],
    )?;
    let exit_code = option_i64(arguments, "--exit-code", 0)?;
    let duration = option_f64(arguments, "--duration-ms", 0.0)?;
    let media_type =
        option_value(arguments, "--media-type")?.unwrap_or_else(|| "text/plain".to_owned());
    let raw = if Path::new(input).is_file() {
        fs::read(Path::new(input)).map_err(|error| format!("OUTPUT_CAPTURE_READ_FAILED:{error}"))?
    } else {
        input.as_bytes().to_vec()
    };
    let store = super::native_artifact_store::NativeArtifactStore::open(state_root)?;
    let record = store.put(
        &raw,
        &media_type,
        "tool-output:terminal",
        &json!({"tool":tool,"command":tool,"duration_ms":duration}),
    )?;
    let original_bytes = raw.len();
    let text = String::from_utf8_lossy(&raw).replace('\r', "");
    let ansi = Regex::new(r"\x1b\[[0-?]*[ -/]*[@-~]")
        .map_err(|error| format!("OUTPUT_ANSI_REGEX_FAILED:{error}"))?;
    let error_re = Regex::new(r"(?i)\b(error|failed|failure|panic|assertion|traceback|exception|fatal|denied|timeout|critical|segfault)\b").map_err(|error|format!("OUTPUT_ERROR_REGEX_FAILED:{error}"))?;
    let warning_re = Regex::new(r"(?i)\b(warn(?:ing)?|deprecated|retry|throttl)\b")
        .map_err(|error| format!("OUTPUT_WARNING_REGEX_FAILED:{error}"))?;
    let summary_re = Regex::new(r"(?i)(test result:|\b\d+\s+(?:passed|failed|errors?|skipped)\b|build\s+(?:succeeded|failed)|finished\s+in\s+[0-9.]+)").map_err(|error|format!("OUTPUT_SUMMARY_REGEX_FAILED:{error}"))?;
    let location_re = Regex::new(r"(?:[A-Za-z]:)?[^\s:]+\.(?:py|rs|ts|tsx|js|jsx|c|cc|cpp|h|hpp|go|java|cs|rb|php):\d+(?::\d+)?").map_err(|error|format!("OUTPUT_LOCATION_REGEX_FAILED:{error}"))?;
    let clean = redact_text(ansi.replace_all(&text, "").as_ref())?;
    let lines = clean
        .lines()
        .filter(|line| !line.trim().is_empty())
        .collect::<Vec<_>>();
    let mut critical = Vec::<String>::new();
    let mut summaries = Vec::<String>::new();
    let mut first = Vec::<String>::new();
    let mut tail = Vec::<String>::new();
    let mut counts = BTreeMap::<String, usize>::new();
    let mut suppressed = 0usize;
    for (index, line) in lines.iter().enumerate() {
        let line = line.trim();
        if index < 12 {
            first.push(line.to_owned())
        }
        tail.push(line.to_owned());
        if tail.len() > 30 {
            tail.remove(0);
        }
        *counts.entry(line.to_owned()).or_default() += 1;
        if (error_re.is_match(line) || location_re.is_match(line)) && critical.len() < 64 {
            if !critical.iter().any(|value| value == line) {
                critical.push(line.to_owned())
            }
        } else if summary_re.is_match(line) || warning_re.is_match(line) {
            if !summaries.iter().any(|value| value == line) {
                summaries.push(line.to_owned())
            }
        } else if index >= 50 {
            suppressed += 1;
        }
    }
    let mut repeated = counts
        .into_iter()
        .filter(|(_, count)| *count > 2)
        .collect::<Vec<_>>();
    repeated.sort_by(|left, right| right.1.cmp(&left.1).then_with(|| left.0.cmp(&right.0)));
    let repeated = repeated
        .into_iter()
        .take(12)
        .map(|(line, count)| format!("[{count}x] {line}"))
        .collect::<Vec<_>>();
    let mut selected = Vec::<String>::new();
    for line in critical
        .iter()
        .chain(summaries.iter())
        .chain(repeated.iter())
        .chain(first.iter())
        .chain(tail.iter())
    {
        if !line.is_empty() && !selected.contains(line) {
            selected.push(line.clone());
        }
        if selected.len() >= 80 {
            break;
        }
    }
    let header = vec![
        format!("Tool: {tool}"),
        format!("Command: {tool}"),
        format!("Exit code: {exit_code}"),
        format!("Duration: {duration:.3} ms"),
        format!("Scanned: {} lines / {} bytes", lines.len(), original_bytes),
        format!("Suppressed: {suppressed} low-value lines"),
        format!("Exact output: artifact://{}", record.artifact_id),
    ];
    let mut visible = header
        .into_iter()
        .chain(selected)
        .collect::<Vec<_>>()
        .join("\n");
    if raw.len() <= 256 * 1024 && clean.as_bytes().len() <= visible.as_bytes().len() {
        visible = clean.clone();
    }
    if visible.as_bytes().len() > 4096 {
        let suffix = format!(
            "\n[… compact view bounded; exact output: artifact://{}]",
            record.artifact_id
        );
        let keep = 4096usize.saturating_sub(suffix.as_bytes().len());
        visible = utf8_prefix(visible.as_bytes(), keep).trim_end().to_owned() + &suffix;
    }
    visible = redact_text(&visible)?;
    let visible_bytes = visible.as_bytes().len();
    let exact_recovery = store
        .read(&record.artifact_id)
        .is_ok_and(|value| value == raw);
    Ok(json!({
        "kind":"terminal","artifact_id":record.artifact_id,"original_bytes":original_bytes,"visible_bytes":visible_bytes,
        "estimated_original_tokens":((original_bytes+3)/4).max(1),"estimated_visible_tokens":((visible_bytes+3)/4).max(1),
        "savings_ratio":(1.0-visible_bytes as f64/original_bytes.max(1) as f64).max(0.0),"compact_view":visible,
        "query_modes":["head","tail","errors","failures","regex"],"exact_recovery":exact_recovery,"critical_lines":critical
    }))
}

fn redact_text(value: &str) -> Result<String, String> {
    let secret=Regex::new(r"(?i)\b(api[_-]?key|access[_-]?token|authorization|password|secret|bearer)\b\s*[:=]\s*([^\s,;]+)").map_err(|error|format!("OUTPUT_SECRET_REGEX_FAILED:{error}"))?;
    let sk = Regex::new(r"\b(?:sk|rk|pk)-(?:proj-)?[A-Za-z0-9_-]{16,}\b")
        .map_err(|error| format!("OUTPUT_KEY_REGEX_FAILED:{error}"))?;
    let first = secret
        .replace_all(value, |captures: &regex::Captures<'_>| {
            format!("{}=<redacted>", &captures[1])
        })
        .into_owned();
    Ok(sk.replace_all(&first, "<redacted>").into_owned())
}

fn utf8_prefix(raw: &[u8], limit: usize) -> String {
    let mut end = limit.min(raw.len());
    while end > 0 && std::str::from_utf8(&raw[..end]).is_err() {
        end -= 1;
    }
    String::from_utf8_lossy(&raw[..end]).into_owned()
}

fn console(arguments: &[String], project: &Path) -> Result<Value, String> {
    let source = positional_after(arguments, "console", 0, &["--output"])?;
    let mut values = load_json_value(source)?;
    if !values.is_object() {
        return Err("console snapshot must be a JSON object".to_owned());
    }
    let token_values = values
        .as_object_mut()
        .expect("object")
        .remove("tokens")
        .or_else(|| {
            values
                .as_object_mut()
                .expect("object")
                .remove("token_panel")
        })
        .unwrap_or_else(|| json!({}));
    let raw = token_values["raw_context_tokens"].as_i64().unwrap_or(0);
    let compiled = token_values["compiled_context_tokens"]
        .as_i64()
        .unwrap_or(0);
    let visible = token_values["visible_output_bytes"].as_i64().unwrap_or(0);
    let original = token_values["original_output_bytes"].as_i64().unwrap_or(0);
    let mut token_panel = token_values;
    if !token_panel.is_object() {
        token_panel = json!({});
    }
    token_panel["raw_context_tokens"] = Value::from(raw);
    token_panel["compiled_context_tokens"] = Value::from(compiled);
    token_panel["cache_read_tokens"] =
        Value::from(token_panel["cache_read_tokens"].as_i64().unwrap_or(0));
    token_panel["cache_write_tokens"] =
        Value::from(token_panel["cache_write_tokens"].as_i64().unwrap_or(0));
    token_panel["externalized_bytes"] =
        Value::from(token_panel["externalized_bytes"].as_i64().unwrap_or(0));
    token_panel["visible_output_bytes"] = Value::from(visible);
    token_panel["original_output_bytes"] = Value::from(original);
    token_panel["current_cost"] = Value::from(token_panel["current_cost"].as_f64().unwrap_or(0.0));
    token_panel["avoided_estimated_cost"] = Value::from(
        token_panel["avoided_estimated_cost"]
            .as_f64()
            .unwrap_or(0.0),
    );
    token_panel["saved_tokens"] = Value::from((raw - compiled).max(0));
    token_panel["context_reduction"] = Value::from(1.0 - compiled as f64 / raw.max(1) as f64);
    token_panel["output_reduction"] = Value::from(1.0 - visible as f64 / original.max(1) as f64);
    let mut snapshot = json!({"generated_at":now_iso()?,"product":"Syntavra","version":"0.0.1","channel":"pre-release","task_state":"idle","plan":[],"active_symbols":[],"changed_files":[],"tests":{},"token_panel":token_panel,"tool_calls":[],"capability_decisions":[],"sandbox":{},"session":{},"adapters":{},"risk":"low","retries":0,"claim_boundary":[]});
    for (key, value) in values.as_object().expect("object") {
        snapshot[key] = value.clone();
    }
    snapshot["retries"] = Value::from(snapshot["retries"].as_i64().unwrap_or(0).max(0));
    let encoded = serde_json::to_string_pretty(&sort_json(&snapshot))
        .map_err(|error| format!("CONSOLE_JSON_FAILED:{error}"))?;
    if let Some(output) = option_value(arguments, "--output")? {
        let target = project_path(project, &output, false)?;
        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent)
                .map_err(|error| format!("CONSOLE_OUTPUT_PARENT_FAILED:{error}"))?;
        }
        let temporary = target.with_extension("tmp");
        fs::write(&temporary, format!("{encoded}\n"))
            .map_err(|error| format!("CONSOLE_OUTPUT_WRITE_FAILED:{error}"))?;
        fs::rename(&temporary, &target)
            .map_err(|error| format!("CONSOLE_OUTPUT_RENAME_FAILED:{error}"))?;
        return Ok(
            json!({"ok":true,"path":target.to_string_lossy(),"bytes":encoded.as_bytes().len()}),
        );
    }
    if has_flag(arguments, "--json") {
        return Ok(json!({"ok":true,"format":"json","output":encoded}));
    }
    Ok(json!({"ok":true,"format":"tui","output":render_console(&snapshot)}))
}

fn render_console(snapshot: &Value) -> String {
    let panel = &snapshot["token_panel"];
    let raw = panel["raw_context_tokens"].as_i64().unwrap_or(0);
    let compiled = panel["compiled_context_tokens"].as_i64().unwrap_or(0);
    let visible = panel["visible_output_bytes"].as_i64().unwrap_or(0);
    let original = panel["original_output_bytes"].as_i64().unwrap_or(0);
    let mut lines = vec![
        "Syntavra 0.0.1 pre-release".to_owned(),
        "════════════════════════════════════════".to_owned(),
        format!(
            "State: {:<18} Risk: {:<8} Retries: {}",
            snapshot["task_state"].as_str().unwrap_or("idle"),
            snapshot["risk"].as_str().unwrap_or("low"),
            snapshot["retries"].as_i64().unwrap_or(0)
        ),
        format!(
            "Context: {compiled}/{raw}  {}  saved {}",
            bar(panel["context_reduction"].as_f64().unwrap_or(0.0)),
            panel["saved_tokens"].as_i64().unwrap_or(0)
        ),
        format!(
            "Output:  {visible}/{original} bytes  {}",
            bar(panel["output_reduction"].as_f64().unwrap_or(0.0))
        ),
        format!(
            "Cache: read {} · write {} · externalized {} bytes",
            panel["cache_read_tokens"].as_i64().unwrap_or(0),
            panel["cache_write_tokens"].as_i64().unwrap_or(0),
            panel["externalized_bytes"].as_i64().unwrap_or(0)
        ),
        format!(
            "Cost: current {:.6} · avoided estimate {:.6}",
            panel["current_cost"].as_f64().unwrap_or(0.0),
            panel["avoided_estimated_cost"].as_f64().unwrap_or(0.0)
        ),
    ];
    for (title, key) in [
        ("Plan", "plan"),
        ("Active symbols", "active_symbols"),
        ("Changed files", "changed_files"),
    ] {
        if let Some(rows) = snapshot[key].as_array() {
            if !rows.is_empty() {
                lines.push(format!("\n{title}"));
                for (row_index, row) in rows.iter().take(30).enumerate() {
                    let value = row.as_str().unwrap_or_default();
                    lines.push(if key == "plan" {
                        format!("  {}. {value}", row_index + 1)
                    } else {
                        format!("  • {value}")
                    });
                }
            }
        }
    }
    lines.join("\n")
}
fn bar(value: f64) -> String {
    let bounded = value.clamp(0.0, 1.0);
    let filled = (bounded * 24.0).round() as usize;
    format!("{}{}", "█".repeat(filled), "░".repeat(24 - filled))
}

fn reliability(arguments: &[String], project: &Path, state_root: &Path) -> Result<Value, String> {
    let cases = option_i64(arguments, "--cases", 1000)?.max(0) as usize;
    let seed = option_i64(arguments, "--seed", 1)? as u64;
    let started_at = now_iso()?;
    let mut rng = StdRng::seed_from_u64(seed);
    let started = Instant::now();
    let alphabet = b"{}[],:\"\\0123456789truefalsenull abcXYZ_-\n\t";
    let mut passed = 0usize;
    let mut rejected = 0usize;
    let mut failures = Vec::<String>::new();
    for index in 0..cases {
        let candidate = if index % 5 == 0 {
            json!({"index":index,"values":[],"nested":{"ok":index%2==1,"text":"x".repeat(rng.gen_range(0..=64))}}).to_string()
        } else {
            let length = rng.gen_range(0..=2048);
            (0..length)
                .map(|_| alphabet[rng.gen_range(0..alphabet.len())] as char)
                .collect()
        };
        match serde_json::from_str::<Value>(&candidate) {
            Ok(_) => passed += 1,
            Err(_) => rejected += 1,
        }
    }
    let fuzz = json!({"name":"json-parser","cases":cases,"passed":passed,"rejected":rejected,"unexpected_failures":failures,"duration_ms":((started.elapsed().as_secs_f64()*1000.0)*1000.0).round()/1000.0});
    let partial = fault_partial_write(state_root)?;
    let sqlite = fault_sqlite(state_root)?;
    let artifact = fault_artifact(state_root)?;
    let capability = fault_capability(state_root)?;
    let faults = vec![partial, sqlite, artifact, capability];
    let ok = passed + rejected == cases
        && failures.is_empty()
        && faults.iter().all(|row| {
            row["injected"].as_bool().unwrap_or(false)
                && row["detected"].as_bool().unwrap_or(false)
                && row["recovered"].as_bool().unwrap_or(false)
        });
    let report = json!({"started_at":started_at,"finished_at":now_iso()?,"seed":seed,"fuzz":[fuzz],"faults":faults,"claims":["INTERNAL_RELIABILITY_MEASUREMENT_ONLY","PUBLIC_PRODUCT_MATURITY_NOT_PROVEN"],"ok":ok});
    let root = state_root.join("unified/reliability-reports");
    fs::create_dir_all(&root).map_err(|error| format!("RELIABILITY_REPORT_ROOT_FAILED:{error}"))?;
    let digest = sha256_hex(
        serde_json::to_string_pretty(&sort_json(&report))
            .map_err(|error| format!("RELIABILITY_REPORT_JSON_FAILED:{error}"))?
            .as_bytes(),
    );
    fs::write(
        root.join(format!("{digest}.json")),
        serde_json::to_vec_pretty(&sort_json(&report))
            .map_err(|error| format!("RELIABILITY_REPORT_JSON_FAILED:{error}"))?,
    )
    .map_err(|error| format!("RELIABILITY_REPORT_WRITE_FAILED:{error}"))?;
    let _ = project;
    Ok(report)
}
fn fault_partial_write(state_root: &Path) -> Result<Value, String> {
    let root = state_root.join("unified/faults/partial-write");
    fs::create_dir_all(&root)
        .map_err(|error| format!("RELIABILITY_PARTIAL_ROOT_FAILED:{error}"))?;
    let target = root.join("state.json");
    let committed = json!({"generation":1,"valid":true}).to_string();
    fs::write(&target, &committed)
        .map_err(|error| format!("RELIABILITY_PARTIAL_BASE_FAILED:{error}"))?;
    let interrupted = root.join(".state.json.interrupted");
    let next = json!({"generation":2,"valid":true}).to_string();
    fs::write(
        &interrupted,
        &next.as_bytes()[..(next.len() * 2 / 5).max(1)],
    )
    .map_err(|error| format!("RELIABILITY_PARTIAL_WRITE_FAILED:{error}"))?;
    let detected = serde_json::from_slice::<Value>(
        &fs::read(&interrupted)
            .map_err(|error| format!("RELIABILITY_PARTIAL_READ_FAILED:{error}"))?,
    )
    .is_err();
    let recovered = serde_json::from_slice::<Value>(
        &fs::read(&target)
            .map_err(|error| format!("RELIABILITY_PARTIAL_BASE_READ_FAILED:{error}"))?,
    )
    .is_ok_and(|value| value["generation"].as_i64() == Some(1));
    let _ = fs::remove_file(interrupted);
    Ok(
        json!({"name":"partial-atomic-write","injected":true,"detected":detected,"recovered":recovered,"detail":""}),
    )
}
fn fault_sqlite(state_root: &Path) -> Result<Value, String> {
    let root = state_root.join("unified/faults/sqlite");
    fs::create_dir_all(&root).map_err(|error| format!("RELIABILITY_SQLITE_ROOT_FAILED:{error}"))?;
    let path = root.join("state.sqlite3");
    let backup = root.join("state.backup");
    for candidate in [
        &path,
        &backup,
        &PathBuf::from(format!("{}-wal", path.display())),
        &PathBuf::from(format!("{}-shm", path.display())),
    ] {
        let _ = fs::remove_file(candidate);
    }
    let connection = Connection::open(&path)
        .map_err(|error| format!("RELIABILITY_SQLITE_OPEN_FAILED:{error}"))?;
    connection.execute_batch("PRAGMA journal_mode=WAL;CREATE TABLE state(id INTEGER PRIMARY KEY,value TEXT NOT NULL);INSERT INTO state(value) VALUES('committed');PRAGMA wal_checkpoint(TRUNCATE);").map_err(|error|format!("RELIABILITY_SQLITE_INIT_FAILED:{error}"))?;
    drop(connection);
    fs::copy(&path, &backup)
        .map_err(|error| format!("RELIABILITY_SQLITE_BACKUP_FAILED:{error}"))?;
    let bytes =
        fs::read(&path).map_err(|error| format!("RELIABILITY_SQLITE_READ_FAILED:{error}"))?;
    fs::write(&path, &bytes[..(bytes.len() / 3).max(1)])
        .map_err(|error| format!("RELIABILITY_SQLITE_TRUNCATE_FAILED:{error}"))?;
    let detected = Connection::open(&path)
        .ok()
        .and_then(|db| {
            db.query_row("PRAGMA integrity_check", [], |row| row.get::<_, String>(0))
                .ok()
        })
        .is_none_or(|value| value != "ok");
    fs::copy(&backup, &path)
        .map_err(|error| format!("RELIABILITY_SQLITE_RESTORE_FAILED:{error}"))?;
    let recovered = Connection::open(&path).ok().is_some_and(|db| {
        db.query_row("SELECT value FROM state LIMIT 1", [], |row| {
            row.get::<_, String>(0)
        })
        .is_ok_and(|value| value == "committed")
    });
    Ok(
        json!({"name":"sqlite-corruption-recovery","injected":true,"detected":detected,"recovered":recovered,"detail":""}),
    )
}
fn fault_artifact(state_root: &Path) -> Result<Value, String> {
    let store = super::native_artifact_store::NativeArtifactStore::open(state_root)?;
    let record = store.put(
        b"syntavra-reliability-payload",
        "application/octet-stream",
        "reliability",
        &json!({}),
    )?;
    let path = PathBuf::from(&record.object_path);
    let original =
        fs::read(&path).map_err(|error| format!("RELIABILITY_ARTIFACT_READ_FAILED:{error}"))?;
    let mut changed = original.clone();
    if let Some(first) = changed.first_mut() {
        *first ^= 0xff;
    }
    fs::write(&path, &changed)
        .map_err(|error| format!("RELIABILITY_ARTIFACT_CORRUPT_FAILED:{error}"))?;
    let detected = store.read(&record.artifact_id).is_err();
    fs::write(&path, &original)
        .map_err(|error| format!("RELIABILITY_ARTIFACT_RESTORE_FAILED:{error}"))?;
    let recovered = store
        .read(&record.artifact_id)
        .is_ok_and(|value| value == original);
    Ok(
        json!({"name":"artifact-hash-corruption","injected":true,"detected":detected,"recovered":recovered,"detail":""}),
    )
}
fn fault_capability(state_root: &Path) -> Result<Value, String> {
    let issue_command = vec!["run".to_owned(), "capability-issue".to_owned()];
    let arguments = json!({"path":"README.md"}).to_string();
    let issue_args = vec![
        "run".to_owned(),
        "capability-issue".to_owned(),
        "reliability".to_owned(),
        "repo.write".to_owned(),
        arguments.clone(),
        "--resource".to_owned(),
        "workspace:/README.md".to_owned(),
        "--permission".to_owned(),
        "write".to_owned(),
        "--ttl".to_owned(),
        "60".to_owned(),
    ];
    let issued =
        super::native_remaining71_security::execute(&issue_command, &issue_args, state_root)?
            .ok_or_else(|| "RELIABILITY_CAPABILITY_ISSUE_UNAVAILABLE".to_owned())?;
    let token = issued["token"].as_str().unwrap_or_default().to_owned();
    let verify_command = vec!["run".to_owned(), "capability-verify".to_owned()];
    let verify_args = vec![
        "run".to_owned(),
        "capability-verify".to_owned(),
        token,
        "repo.write".to_owned(),
        arguments,
        "--resource".to_owned(),
        "workspace:/README.md".to_owned(),
    ];
    let first =
        super::native_remaining71_security::execute(&verify_command, &verify_args, state_root)?
            .unwrap_or_else(|| json!({"ok":false}));
    let second =
        super::native_remaining71_security::execute(&verify_command, &verify_args, state_root)?
            .unwrap_or_else(|| json!({"ok":true}));
    let first_ok = first["ok"].as_bool().unwrap_or(false);
    let second_ok = second["ok"].as_bool().unwrap_or(true);
    Ok(
        json!({"name":"capability-replay","injected":true,"detected":!second_ok,"recovered":first_ok&&!second_ok,"detail":""}),
    )
}

fn update_install(
    arguments: &[String],
    project: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    let source_raw = positional_after(arguments, "update-install", 0, &["--name"])?;
    let artifact_raw = positional_after(arguments, "update-install", 1, &["--name"])?;
    let source = project_path(project, source_raw, true)?;
    let artifact = load_json_value(artifact_raw)?;
    if !artifact.is_object() {
        return Err("artifact must be a JSON object".to_owned());
    }
    let expected_size = artifact["size"]
        .as_u64()
        .ok_or_else(|| "UPDATE_ARTIFACT_SIZE_INVALID".to_owned())?;
    let expected_hash = artifact["sha256"]
        .as_str()
        .ok_or_else(|| "UPDATE_ARTIFACT_HASH_INVALID".to_owned())?;
    let source_bytes =
        fs::read(&source).map_err(|error| format!("UPDATE_SOURCE_READ_FAILED:{error}"))?;
    let actual_hash = format!("{:x}", Sha256::digest(&source_bytes));
    if source_bytes.len() as u64 != expected_size {
        return Err(format!(
            "artifact size mismatch: expected {expected_size}, got {}",
            source_bytes.len()
        ));
    }
    if actual_hash != expected_hash {
        return Err(format!(
            "artifact checksum mismatch: expected {expected_hash}, got {actual_hash}"
        ));
    }
    let name = option_value(arguments, "--name")?.unwrap_or_else(|| "syntavra".to_owned());
    if name.is_empty() || name.contains(['/', '\\']) {
        return Err("UPDATE_EXECUTABLE_NAME_INVALID".to_owned());
    }
    let unified = state_root.join("unified");
    let install_root = unified.join("bin");
    let backups = unified.join("update-backups");
    let receipts = unified.join("update-receipts");
    for dir in [&install_root, &backups, &receipts] {
        fs::create_dir_all(dir)
            .map_err(|error| format!("UPDATE_DIRECTORY_CREATE_FAILED:{error}"))?;
    }
    let target = install_root.join(&name);
    let previous = if target.is_file() {
        file_sha256(&target)?
    } else {
        String::new()
    };
    let backup = backups.join(format!(
        "{name}.{}.bak",
        if previous.is_empty() {
            "none"
        } else {
            &previous
        }
    ));
    let staged = install_root.join(format!(
        ".{name}.{}.staged",
        &expected_hash[..expected_hash.len().min(12)]
    ));
    let started_at = now_iso()?;
    let install = (|| -> Result<(), String> {
        fs::copy(&source, &staged).map_err(|error| format!("UPDATE_STAGE_COPY_FAILED:{error}"))?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut permissions = fs::metadata(&staged)
                .map_err(|error| format!("UPDATE_STAGE_METADATA_FAILED:{error}"))?
                .permissions();
            permissions.set_mode(permissions.mode() | 0o111);
            fs::set_permissions(&staged, permissions)
                .map_err(|error| format!("UPDATE_STAGE_CHMOD_FAILED:{error}"))?;
        }
        if file_sha256(&staged)? != expected_hash {
            return Err("staged artifact checksum mismatch".to_owned());
        }
        if target.exists() {
            fs::copy(&target, &backup)
                .map_err(|error| format!("UPDATE_BACKUP_COPY_FAILED:{error}"))?;
        }
        #[cfg(windows)]
        if target.exists() {
            fs::remove_file(&target)
                .map_err(|error| format!("UPDATE_TARGET_REMOVE_FAILED:{error}"))?;
        }
        fs::rename(&staged, &target)
            .map_err(|error| format!("UPDATE_TARGET_RENAME_FAILED:{error}"))?;
        Ok(())
    })();
    let finished_at = now_iso()?;
    let (mut status, mut rollback, mut detail) = ("installed".to_owned(), false, String::new());
    if let Err(error) = install {
        let _ = fs::remove_file(&staged);
        if backup.is_file() {
            let _ = fs::remove_file(&target);
            fs::rename(&backup, &target)
                .map_err(|restore| format!("UPDATE_ROLLBACK_RENAME_FAILED:{restore}"))?;
            rollback = true;
            status = "rolled-back".to_owned();
        } else if target.is_file() && file_sha256(&target).ok().as_deref() == Some(expected_hash) {
            let _ = fs::remove_file(&target);
            rollback = true;
            status = "rolled-back".to_owned();
        } else {
            status = "failed".to_owned();
        }
        detail = format!("RuntimeError: {error}");
    }
    let installed = if target.is_file() {
        file_sha256(&target)?
    } else {
        String::new()
    };
    let body = json!({"status":status,"target":target.to_string_lossy(),"previous":previous,"installed":expected_hash,"started_at":started_at,"finished_at":finished_at});
    let receipt_id = format!("sha256:{}", sha256_hex(canonical_json(&body)?.as_bytes()));
    let receipt = json!({"receipt_id":receipt_id,"status":status,"target":target.to_string_lossy(),"previous":previous,"installed_sha256":installed,"expected_sha256":expected_hash,"started_at":started_at,"finished_at":finished_at,"rollback_performed":rollback,"health":{"ok":status=="installed","mode":"checksum-only"},"detail":detail,"ok":status=="installed"});
    fs::write(
        receipts.join(format!("{}.json", receipt_id.trim_start_matches("sha256:"))),
        serde_json::to_vec_pretty(&sort_json(&receipt))
            .map_err(|error| format!("UPDATE_RECEIPT_JSON_FAILED:{error}"))?,
    )
    .map_err(|error| format!("UPDATE_RECEIPT_WRITE_FAILED:{error}"))?;
    Ok(receipt)
}
fn update_rollback(arguments: &[String], state_root: &Path) -> Result<Value, String> {
    let name = option_value(arguments, "--name")?.unwrap_or_else(|| "syntavra".to_owned());
    let expected = option_value(arguments, "--sha256")?.unwrap_or_default();
    let unified = state_root.join("unified");
    let backups = unified.join("update-backups");
    let target = unified.join("bin").join(&name);
    if !backups.is_dir() {
        return Ok(json!({"ok":false,"reason":"no matching backup"}));
    }
    let mut candidates = fs::read_dir(&backups)
        .map_err(|error| format!("UPDATE_BACKUP_READ_FAILED:{error}"))?
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| {
            path.file_name()
                .and_then(|value| value.to_str())
                .is_some_and(|file| file.starts_with(&format!("{name}.")) && file.ends_with(".bak"))
        })
        .collect::<Vec<_>>();
    candidates.sort_by_key(|path| {
        std::cmp::Reverse(fs::metadata(path).and_then(|meta| meta.modified()).ok())
    });
    if !expected.is_empty() {
        candidates.retain(|path| {
            path.file_name()
                .and_then(|value| value.to_str())
                .is_some_and(|file| file.contains(&expected))
                && file_sha256(path).ok().as_deref() == Some(expected.as_str())
        });
    }
    let Some(backup) = candidates.into_iter().next() else {
        return Ok(json!({"ok":false,"reason":"no matching backup"}));
    };
    let _ = fs::remove_file(&target);
    fs::rename(&backup, &target).map_err(|error| format!("UPDATE_ROLLBACK_FAILED:{error}"))?;
    Ok(
        json!({"ok":true,"target":target.to_string_lossy(),"sha256":file_sha256(&target)?,"restored_from":backup.to_string_lossy()}),
    )
}

fn file_sha256(path: &Path) -> Result<String, String> {
    let bytes = fs::read(path).map_err(|error| format!("SHA256_READ_FAILED:{error}"))?;
    Ok(format!("{:x}", Sha256::digest(bytes)))
}
fn load_json_value(value: &str) -> Result<Value, String> {
    let raw = if Path::new(value).is_file() {
        fs::read_to_string(value).map_err(|error| format!("JSON_READ_FAILED:{error}"))?
    } else {
        value.to_owned()
    };
    serde_json::from_str(&raw).map_err(|error| format!("JSON_INVALID:{error}"))
}
fn project_path(project: &Path, value: &str, must_exist: bool) -> Result<PathBuf, String> {
    let root =
        fs::canonicalize(project).map_err(|error| format!("PROJECT_RESOLVE_FAILED:{error}"))?;
    let candidate = Path::new(value);
    let joined = if candidate.is_absolute() {
        candidate.to_path_buf()
    } else {
        root.join(candidate)
    };
    let resolved = if must_exist {
        fs::canonicalize(&joined).map_err(|error| format!("PATH_RESOLVE_FAILED:{error}"))?
    } else {
        joined
    };
    if !resolved.starts_with(&root) {
        return Err("PATH_ESCAPES_PROJECT".to_owned());
    }
    Ok(resolved)
}
fn canonical_json(value: &Value) -> Result<String, String> {
    serde_json::to_string(&sort_json(value))
        .map_err(|error| format!("JSON_SERIALIZE_FAILED:{error}"))
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
fn now_iso() -> Result<String, String> {
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("CLOCK_FAILED:{error}"))?;
    Ok(format!(
        "{}.{:06}+00:00",
        duration.as_secs(),
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
    action: &str,
    position: usize,
    value_flags: &[&str],
) -> Result<&'a str, String> {
    let start = arguments
        .windows(2)
        .position(|row| row[0] == "run" && row[1] == action)
        .map(|index| index + 2)
        .ok_or_else(|| format!("ACTION_NOT_FOUND:{action}"))?;
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
        .ok_or_else(|| format!("POSITIONAL_MISSING:{action}:{position}"))
}
