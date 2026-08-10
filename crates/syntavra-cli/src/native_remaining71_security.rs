#![forbid(unsafe_code)]

use std::collections::BTreeSet;
use std::fs::{self, OpenOptions};
use std::io::Write as _;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use rand::{rngs::OsRng, RngCore as _};
use regex::Regex;
use rusqlite::{params, Connection, OptionalExtension};
use serde_json::{json, Map, Value};
use sha2::{Digest as _, Sha256};

const VERSION: &str = "0.0.1";
const CHANNEL: &str = "pre-release";

pub(crate) fn supports(command: &[String]) -> bool {
    command.len() == 2
        && command[0] == "run"
        && matches!(
            command[1].as_str(),
            "capability-decide" | "capability-issue" | "capability-verify"
        )
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
    serde_json::to_vec(&sorted(value)).map_err(|error| format!("CAPABILITY_JSON_FAILED:{error}"))
}

fn sha256(value: &[u8]) -> String {
    format!("{:x}", Sha256::digest(value))
}

fn hmac_sha256(key: &[u8], message: &[u8]) -> [u8; 32] {
    const BLOCK: usize = 64;
    let mut normalized = [0u8; BLOCK];
    if key.len() > BLOCK {
        let digest = Sha256::digest(key);
        normalized[..32].copy_from_slice(&digest);
    } else {
        normalized[..key.len()].copy_from_slice(key);
    }
    let mut inner_key = [0x36u8; BLOCK];
    let mut outer_key = [0x5cu8; BLOCK];
    for index in 0..BLOCK {
        inner_key[index] ^= normalized[index];
        outer_key[index] ^= normalized[index];
    }
    let mut inner = Sha256::new();
    inner.update(inner_key);
    inner.update(message);
    let inner_digest = inner.finalize();
    let mut outer = Sha256::new();
    outer.update(outer_key);
    outer.update(inner_digest);
    let digest = outer.finalize();
    let mut output = [0u8; 32];
    output.copy_from_slice(&digest);
    output
}

fn constant_time_equal(left: &[u8], right: &[u8]) -> bool {
    if left.len() != right.len() {
        return false;
    }
    let mut diff = 0u8;
    for (a, b) in left.iter().zip(right.iter()) {
        diff |= a ^ b;
    }
    diff == 0
}

fn unix_seconds() -> Result<i64, String> {
    let seconds = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("CAPABILITY_CLOCK_FAILED:{error}"))?
        .as_secs();
    i64::try_from(seconds).map_err(|_| "CAPABILITY_CLOCK_RANGE".to_owned())
}

fn now_iso() -> Result<String, String> {
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("CAPABILITY_CLOCK_FAILED:{error}"))?;
    let seconds =
        i64::try_from(duration.as_secs()).map_err(|_| "CAPABILITY_CLOCK_RANGE".to_owned())?;
    let days = seconds / 86_400;
    let shifted = days + 719_468;
    let era = if shifted >= 0 {
        shifted
    } else {
        shifted - 146_096
    } / 146_097;
    let doe = shifted - era * 146_097;
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
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}.{:06}Z",
        sod / 3600,
        (sod % 3600) / 60,
        sod % 60,
        duration.subsec_micros()
    ))
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
    let mut output = Vec::new();
    let mut index = 0usize;
    while index < arguments.len() {
        if arguments[index] == flag {
            index += 1;
            output.push(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?
                    .clone(),
            );
        } else if let Some(value) = arguments[index]
            .strip_prefix(flag)
            .and_then(|tail| tail.strip_prefix('='))
        {
            output.push(value.to_owned());
        }
        index += 1;
    }
    Ok(output)
}

fn has_flag(arguments: &[String], flag: &str) -> bool {
    arguments.iter().any(|item| item == flag)
}

fn action_positional(arguments: &[String], action: &str) -> Result<Vec<String>, String> {
    let start = arguments
        .windows(2)
        .position(|row| row[0] == "run" && row[1] == action)
        .map(|index| index + 2)
        .ok_or_else(|| format!("CAPABILITY_ACTION_NOT_FOUND:{action}"))?;
    let flags_with_values = ["--resource", "--network-host", "--permission", "--ttl"];
    let mut values = Vec::new();
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
        values.push(arguments[index].clone());
        index += 1;
    }
    Ok(values)
}

fn load_json(value: &str) -> Result<Value, String> {
    let raw = if Path::new(value).is_file() {
        fs::read_to_string(value)
            .map_err(|error| format!("CAPABILITY_ARGUMENTS_READ_FAILED:{error}"))?
    } else {
        value.to_owned()
    };
    serde_json::from_str(&raw).map_err(|error| format!("CAPABILITY_ARGUMENTS_INVALID:{error}"))
}

fn rendered_arguments(arguments: &Value) -> Result<Vec<u8>, String> {
    if let Some(value) = arguments.as_str() {
        Ok(value.as_bytes().to_vec())
    } else {
        canonical_json(arguments)
    }
}

fn category(tool: &str) -> &'static str {
    let normalized = tool.to_lowercase().replace(['-', '_'], ".");
    let leaf = normalized.rsplit('.').next().unwrap_or_default();
    for prefix in ["read", "search", "list", "find", "inspect", "grep"] {
        if leaf.starts_with(prefix) {
            return "read";
        }
    }
    for prefix in [
        "write", "edit", "patch", "delete", "move", "create", "update",
    ] {
        if leaf.starts_with(prefix) {
            return "write";
        }
    }
    for prefix in [
        "run",
        "exec",
        "shell",
        "terminal",
        "bash",
        "powershell",
        "cmd",
    ] {
        if leaf.starts_with(prefix) {
            return "execute";
        }
    }
    for prefix in [
        "web", "http", "fetch", "download", "upload", "request", "browser",
    ] {
        if leaf.starts_with(prefix) {
            return "network";
        }
    }
    "unknown"
}

fn destructive_regex() -> Result<Regex, String> {
    Regex::new(r"(?i)(?:\brm\s+-rf\b|\bgit\s+reset\s+--hard\b|\bgit\s+clean\s+-[a-z]*[fdx]|\bmkfs\.|\bformat\s+[a-z]:|remove-item\s+.*-recurse.*-force)")
        .map_err(|error| format!("CAPABILITY_REGEX_FAILED:{error}"))
}

fn decide(
    tool: &str,
    arguments: &Value,
    resource: &str,
    sandboxed: bool,
    user_authorized: bool,
    network_allowlist: &[String],
) -> Result<Value, String> {
    let category = category(tool);
    let rendered = rendered_arguments(arguments)?;
    let policy_text = arguments
        .as_object()
        .and_then(|map| map.get("argv"))
        .and_then(Value::as_array)
        .map(|rows| {
            rows.iter()
                .map(|value| {
                    value
                        .as_str()
                        .map(str::to_owned)
                        .unwrap_or_else(|| value.to_string())
                })
                .collect::<Vec<_>>()
                .join(" ")
        })
        .unwrap_or_else(|| String::from_utf8_lossy(&rendered).into_owned());
    let args_hash = sha256(&rendered);
    let mut requirements = vec!["signed-capability", "exact-evidence"];
    let mut allowed = true;
    let mut reason = "policy-allowed";
    if category == "unknown" {
        allowed = false;
        reason = "unknown-tool-fail-closed";
    }
    if matches!(category, "write" | "execute" | "network") {
        requirements.push("explicit-user-authorization");
        if !user_authorized {
            allowed = false;
            reason = "authorization-required";
        }
    }
    if category == "execute" {
        requirements.push("sandbox");
        if !sandboxed {
            allowed = false;
            reason = "sandbox-required";
        } else if destructive_regex()?.is_match(&policy_text) {
            allowed = false;
            reason = "destructive-command-denied";
        }
    }
    if !resource.starts_with("workspace:") && matches!(category, "write" | "execute") {
        allowed = false;
        reason = "resource-outside-workspace";
    }
    if category == "network" && !network_allowlist.is_empty() {
        let host = arguments
            .as_object()
            .and_then(|map| map.get("host"))
            .and_then(Value::as_str)
            .unwrap_or_default();
        if !network_allowlist.iter().any(|value| value == host) {
            allowed = false;
            reason = "network-host-not-allowlisted";
        }
    }
    Ok(json!({
        "allowed": allowed,
        "category": category,
        "reason": reason,
        "requirements": requirements,
        "arguments_hash": args_hash,
        "resource": resource,
    }))
}

struct CapabilityStore {
    key: Vec<u8>,
    db: Connection,
}

impl CapabilityStore {
    fn open(state_root: &Path) -> Result<Self, String> {
        fs::create_dir_all(state_root)
            .map_err(|error| format!("CAPABILITY_STATE_CREATE_FAILED:{error}"))?;
        let key_path = state_root.join("capability.key");
        let legacy_key = state_root.join("capability-v2.key");
        if !key_path.exists() && legacy_key.exists() {
            fs::rename(&legacy_key, &key_path)
                .map_err(|error| format!("CAPABILITY_LEGACY_KEY_MOVE_FAILED:{error}"))?;
        }
        if !key_path.exists() {
            let temporary = key_path.with_extension("tmp");
            let mut key = [0u8; 32];
            OsRng.fill_bytes(&mut key);
            let mut output = OpenOptions::new()
                .create_new(true)
                .write(true)
                .open(&temporary)
                .map_err(|error| format!("CAPABILITY_KEY_CREATE_FAILED:{error}"))?;
            output
                .write_all(&key)
                .map_err(|error| format!("CAPABILITY_KEY_WRITE_FAILED:{error}"))?;
            output
                .flush()
                .map_err(|error| format!("CAPABILITY_KEY_FLUSH_FAILED:{error}"))?;
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                fs::set_permissions(&temporary, fs::Permissions::from_mode(0o600))
                    .map_err(|error| format!("CAPABILITY_KEY_MODE_FAILED:{error}"))?;
            }
            fs::rename(&temporary, &key_path)
                .map_err(|error| format!("CAPABILITY_KEY_RENAME_FAILED:{error}"))?;
        }
        let key =
            fs::read(&key_path).map_err(|error| format!("CAPABILITY_KEY_READ_FAILED:{error}"))?;
        let db_path = state_root.join("capability.sqlite3");
        let legacy_db = state_root.join("capability-v2.sqlite3");
        if !db_path.exists() && legacy_db.exists() {
            fs::rename(&legacy_db, &db_path)
                .map_err(|error| format!("CAPABILITY_LEGACY_DB_MOVE_FAILED:{error}"))?;
            for suffix in ["-wal", "-shm"] {
                let legacy =
                    Path::new(&format!("{}{}", legacy_db.to_string_lossy(), suffix)).to_path_buf();
                if legacy.exists() {
                    let target = Path::new(&format!("{}{}", db_path.to_string_lossy(), suffix))
                        .to_path_buf();
                    let _ = fs::rename(legacy, target);
                }
            }
        }
        let db = Connection::open(db_path)
            .map_err(|error| format!("CAPABILITY_DB_OPEN_FAILED:{error}"))?;
        db.execute_batch("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA synchronous=FULL; PRAGMA busy_timeout=30000; CREATE TABLE IF NOT EXISTS consumed (nonce TEXT PRIMARY KEY, consumed_at TEXT NOT NULL);")
            .map_err(|error| format!("CAPABILITY_DB_INIT_FAILED:{error}"))?;
        Ok(Self { key, db })
    }

    fn issue(
        &self,
        session_id: &str,
        tool: &str,
        arguments: &Value,
        resource: &str,
        permissions: &[String],
        ttl: i64,
        single_use: bool,
    ) -> Result<String, String> {
        let now = unix_seconds()?;
        let mut nonce_raw = [0u8; 18];
        OsRng.fill_bytes(&mut nonce_raw);
        let permissions = permissions
            .iter()
            .cloned()
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect::<Vec<_>>();
        let body = json!({
            "version": VERSION,
            "channel": CHANNEL,
            "session_id": session_id,
            "tool": tool,
            "arguments_hash": sha256(&rendered_arguments(arguments)?),
            "resource": resource,
            "permissions": permissions,
            "issued_at": now,
            "expires_at": now + ttl.max(1),
            "single_use": single_use,
            "nonce": URL_SAFE_NO_PAD.encode(nonce_raw),
        });
        let payload = URL_SAFE_NO_PAD.encode(canonical_json(&body)?);
        let signature = URL_SAFE_NO_PAD.encode(hmac_sha256(&self.key, payload.as_bytes()));
        Ok(format!("{payload}.{signature}"))
    }

    fn verify(
        &self,
        token: &str,
        tool: &str,
        arguments: &Value,
        resource: &str,
        consume: bool,
    ) -> Result<Value, String> {
        let Some((payload_text, signature_text)) = token.split_once('.') else {
            return Ok(json!({"ok": false, "reason": "malformed-token"}));
        };
        let supplied = match URL_SAFE_NO_PAD.decode(signature_text) {
            Ok(value) => value,
            Err(_) => return Ok(json!({"ok": false, "reason": "malformed-token"})),
        };
        let expected = hmac_sha256(&self.key, payload_text.as_bytes());
        if !constant_time_equal(&expected, &supplied) {
            return Ok(json!({"ok": false, "reason": "invalid-signature"}));
        }
        let body_raw = match URL_SAFE_NO_PAD.decode(payload_text) {
            Ok(value) => value,
            Err(_) => return Ok(json!({"ok": false, "reason": "malformed-token"})),
        };
        let body: Value = match serde_json::from_slice(&body_raw) {
            Ok(value) => value,
            Err(_) => return Ok(json!({"ok": false, "reason": "malformed-token"})),
        };
        if body["expires_at"].as_i64().unwrap_or_default() < unix_seconds()? {
            return Ok(json!({"ok": false, "reason": "expired", "capability": body}));
        }
        let args_hash = sha256(&rendered_arguments(arguments)?);
        if body["tool"].as_str() != Some(tool)
            || body["arguments_hash"].as_str() != Some(&args_hash)
            || body["resource"].as_str() != Some(resource)
        {
            return Ok(json!({"ok": false, "reason": "binding-mismatch", "capability": body}));
        }
        let nonce = body["nonce"].as_str().unwrap_or_default();
        let used: Option<i64> = self
            .db
            .query_row("SELECT 1 FROM consumed WHERE nonce=?1", [nonce], |row| {
                row.get(0)
            })
            .optional()
            .map_err(|error| format!("CAPABILITY_CONSUMED_QUERY_FAILED:{error}"))?;
        if used.is_some() {
            return Ok(json!({"ok": false, "reason": "already-consumed", "capability": body}));
        }
        if consume && body["single_use"].as_bool().unwrap_or(true) {
            self.db
                .execute(
                    "INSERT INTO consumed VALUES(?1,?2)",
                    params![nonce, now_iso()?],
                )
                .map_err(|error| format!("CAPABILITY_CONSUME_FAILED:{error}"))?;
        }
        Ok(json!({"ok": true, "reason": "verified", "capability": body}))
    }
}

pub(crate) fn execute(
    command: &[String],
    arguments: &[String],
    state_root: &Path,
) -> Result<Option<Value>, String> {
    if !supports(command) {
        return Ok(None);
    }
    let action = command[1].as_str();
    let positional = action_positional(arguments, action)?;
    let security_root = state_root.join("unified").join("security");
    let value = match action {
        "capability-decide" => {
            if positional.len() < 2 {
                return Err("CAPABILITY_DECIDE_ARGUMENTS_REQUIRED".to_owned());
            }
            let args = load_json(&positional[1])?;
            decide(
                &positional[0],
                &args,
                option_value(arguments, "--resource")?
                    .as_deref()
                    .unwrap_or("workspace:/"),
                has_flag(arguments, "--sandboxed"),
                has_flag(arguments, "--user-authorized"),
                &repeated_values(arguments, "--network-host")?,
            )?
        }
        "capability-issue" => {
            if positional.len() < 3 {
                return Err("CAPABILITY_ISSUE_ARGUMENTS_REQUIRED".to_owned());
            }
            let args = load_json(&positional[2])?;
            let store = CapabilityStore::open(&security_root)?;
            let ttl = option_value(arguments, "--ttl")?
                .map(|value| {
                    value
                        .parse::<i64>()
                        .map_err(|_| "CAPABILITY_TTL_INVALID".to_owned())
                })
                .transpose()?
                .unwrap_or(300);
            let single_use = !has_flag(arguments, "--reusable");
            let token = store.issue(
                &positional[0],
                &positional[1],
                &args,
                option_value(arguments, "--resource")?
                    .as_deref()
                    .unwrap_or("workspace:/"),
                &repeated_values(arguments, "--permission")?,
                ttl,
                single_use,
            )?;
            json!({"ok": true, "token": token, "single_use": single_use})
        }
        "capability-verify" => {
            if positional.len() < 3 {
                return Err("CAPABILITY_VERIFY_ARGUMENTS_REQUIRED".to_owned());
            }
            let args = load_json(&positional[2])?;
            let store = CapabilityStore::open(&security_root)?;
            store.verify(
                &positional[0],
                &positional[1],
                &args,
                option_value(arguments, "--resource")?
                    .as_deref()
                    .unwrap_or("workspace:/"),
                !has_flag(arguments, "--no-consume"),
            )?
        }
        _ => return Ok(None),
    };
    Ok(Some(value))
}
