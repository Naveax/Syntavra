#![forbid(unsafe_code)]
#![allow(clippy::too_many_lines)]

use std::fs;
use std::io::Write as _;
use std::path::{Component, Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection};
use serde_json::{json, Map, Value};
use syntavra_core::sha256_hex;

const TEXT_BEGIN: &str = "<!-- SYNTAVRA:BEGIN managed-host-integration -->";
const TEXT_END: &str = "<!-- SYNTAVRA:END managed-host-integration -->";

#[derive(Debug, Clone)]
struct Change {
    path: String,
    kind: String,
    action: String,
    existed: bool,
    before_hash: String,
    after_hash: String,
    backup_path: String,
}

impl Change {
    fn value(&self) -> Value {
        json!({
            "path": self.path,
            "kind": self.kind,
            "action": self.action,
            "existed": self.existed,
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
            "backup_path": self.backup_path,
        })
    }
}

#[derive(Debug)]
enum Payload {
    Bytes(Vec<u8>),
    Directory(PathBuf),
}

#[derive(Debug)]
struct Staged {
    target: PathBuf,
    kind: String,
    payload: Payload,
    existed: bool,
    before_hash: String,
    relative: String,
}

pub fn supports(command: &[String]) -> bool {
    matches!(command, [fabric, action] if fabric == "fabric" && action == "install")
}

fn command_start(arguments: &[String]) -> Result<usize, String> {
    arguments
        .windows(2)
        .position(|row| row[0] == "fabric" && row[1] == "install")
        .map(|index| index + 2)
        .ok_or_else(|| "FABRIC_INSTALL_COMMAND_MISSING".to_owned())
}

fn host_name(arguments: &[String]) -> Result<String, String> {
    let index = command_start(arguments)?;
    let value = arguments
        .get(index)
        .ok_or_else(|| "fabric install host_name is required".to_owned())?;
    if value.starts_with('-') {
        return Err("fabric install host_name is required".to_owned());
    }
    Ok(value.to_ascii_lowercase())
}

fn has_flag(arguments: &[String], name: &str) -> bool {
    arguments.iter().any(|value| value == name)
}

fn option_value(arguments: &[String], name: &str) -> Result<Option<String>, String> {
    let prefix = format!("{name}=");
    let mut found = None;
    let mut index = 0usize;
    while index < arguments.len() {
        if arguments[index] == name {
            index += 1;
            found = Some(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{name}_VALUE_MISSING"))?
                    .clone(),
            );
        } else if let Some(value) = arguments[index].strip_prefix(&prefix) {
            found = Some(value.to_owned());
        }
        index += 1;
    }
    Ok(found)
}

fn now() -> Result<f64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .map_err(|error| format!("FABRIC_INSTALL_CLOCK_FAILED:{error}"))
}

fn transaction_id(host: &str) -> Result<String, String> {
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("FABRIC_INSTALL_CLOCK_FAILED:{error}"))?;
    let seed = format!("{host}:{}:{}", duration.as_nanos(), std::process::id());
    Ok(format!(
        "host-{}-{}",
        duration.as_secs(),
        &sha256_hex(seed.as_bytes())[..12]
    ))
}

fn home(arguments: &[String]) -> Result<PathBuf, String> {
    if let Some(value) = option_value(arguments, "--home")? {
        return Ok(PathBuf::from(value));
    }
    std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" })
        .map(PathBuf::from)
        .ok_or_else(|| "FABRIC_INSTALL_HOME_MISSING".to_owned())
}

fn skill_root(arguments: &[String], project_root: &Path) -> Result<PathBuf, String> {
    let configured = option_value(arguments, "--skill-root")?.map(PathBuf::from);
    let project_candidate = project_root.join("skills").join("syntavra");
    let bundled = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .unwrap_or_else(|| Path::new("."))
        .join("skills")
        .join("syntavra");
    let selected = configured.unwrap_or_else(|| {
        if project_candidate.join("SKILL.md").is_file() {
            project_candidate
        } else {
            bundled
        }
    });
    if !selected.join("SKILL.md").is_file() {
        return Err(format!(
            "Syntavra skill source is incomplete: {}",
            selected.to_string_lossy()
        ));
    }
    Ok(selected)
}

fn canonical_bytes(value: &Value) -> Result<Vec<u8>, String> {
    serde_json::to_string_pretty(value)
        .map(|rendered| format!("{rendered}\n").into_bytes())
        .map_err(|error| format!("FABRIC_INSTALL_JSON_SERIALIZE_FAILED:{error}"))
}

fn recursive_merge(base: &Value, overlay: &Value) -> Value {
    match (base, overlay) {
        (Value::Object(base), Value::Object(overlay)) => {
            let mut result = base.clone();
            for (key, value) in overlay {
                let merged = result
                    .get(key)
                    .map_or_else(|| value.clone(), |current| recursive_merge(current, value));
                result.insert(key.clone(), merged);
            }
            Value::Object(result)
        }
        (_, value) => value.clone(),
    }
}

fn managed_text(existing: &str, block: &str) -> String {
    let managed = format!("{TEXT_BEGIN}\n{}\n{TEXT_END}", block.trim_end());
    if let (Some(start), Some(end)) = (existing.find(TEXT_BEGIN), existing.find(TEXT_END)) {
        let prefix = existing[..start].trim_end();
        let suffix = &existing[end + TEXT_END.len()..];
        return format!("{prefix}\n\n{managed}{suffix}");
    }
    if existing.trim().is_empty() {
        format!("{managed}\n")
    } else {
        format!("{}\n\n{managed}\n", existing.trim_end())
    }
}

fn safe_target(root: &Path, relative: &str) -> Result<PathBuf, String> {
    let relative_path = Path::new(relative);
    if relative_path.is_absolute() {
        return Err(format!("host path escapes installation root: {relative}"));
    }
    let mut cursor = root.to_path_buf();
    for component in relative_path.components() {
        match component {
            Component::CurDir => continue,
            Component::Normal(part) => cursor.push(part),
            Component::ParentDir => {
                return Err(format!("host path traversal rejected: {relative}"));
            }
            _ => return Err(format!("host path escapes installation root: {relative}")),
        }
        if fs::symlink_metadata(&cursor)
            .is_ok_and(|metadata| metadata.file_type().is_symlink())
        {
            return Err(format!("host path symlink rejected: {relative}"));
        }
    }
    let root = root
        .canonicalize()
        .unwrap_or_else(|_| root.to_path_buf());
    let parent = cursor
        .parent()
        .unwrap_or(root.as_path())
        .canonicalize()
        .unwrap_or_else(|_| cursor.parent().unwrap_or(root.as_path()).to_path_buf());
    if !parent.starts_with(&root) {
        return Err(format!("host path escapes installation root: {relative}"));
    }
    Ok(cursor)
}

fn digest(path: &Path) -> Result<String, String> {
    let Ok(metadata) = fs::symlink_metadata(path) else {
        return Ok(String::new());
    };
    if metadata.file_type().is_symlink() {
        let target = fs::read_link(path)
            .map_err(|error| format!("FABRIC_INSTALL_SYMLINK_READ_FAILED:{error}"))?;
        return Ok(sha256_hex(
            format!("symlink:{}", target.to_string_lossy()).as_bytes(),
        ));
    }
    if metadata.is_file() {
        return fs::read(path)
            .map(|bytes| sha256_hex(&bytes))
            .map_err(|error| format!("FABRIC_INSTALL_DIGEST_READ_FAILED:{error}"));
    }
    let mut rows = Vec::<(String, String)>::new();
    fn visit(root: &Path, current: &Path, rows: &mut Vec<(String, String)>) -> Result<(), String> {
        let mut entries = fs::read_dir(current)
            .map_err(|error| format!("FABRIC_INSTALL_DIGEST_DIRECTORY_FAILED:{error}"))?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|error| format!("FABRIC_INSTALL_DIGEST_DIRECTORY_FAILED:{error}"))?;
        entries.sort_by_key(|entry| entry.path());
        for entry in entries {
            let path = entry.path();
            let metadata = fs::symlink_metadata(&path)
                .map_err(|error| format!("FABRIC_INSTALL_DIGEST_METADATA_FAILED:{error}"))?;
            let relative = path
                .strip_prefix(root)
                .map_err(|_| "FABRIC_INSTALL_DIGEST_RELATIVE_FAILED".to_owned())?
                .to_string_lossy()
                .replace('\\', "/");
            if metadata.file_type().is_symlink() {
                let target = fs::read_link(&path)
                    .map_err(|error| format!("FABRIC_INSTALL_SYMLINK_READ_FAILED:{error}"))?;
                rows.push((relative, format!("symlink:{}", target.to_string_lossy())));
            } else if metadata.is_dir() {
                visit(root, &path, rows)?;
            } else if metadata.is_file() {
                let bytes = fs::read(&path)
                    .map_err(|error| format!("FABRIC_INSTALL_DIGEST_READ_FAILED:{error}"))?;
                rows.push((relative, sha256_hex(&bytes)));
            }
        }
        Ok(())
    }
    visit(path, path, &mut rows)?;
    let canonical = serde_json::to_vec(&rows)
        .map_err(|error| format!("FABRIC_INSTALL_DIGEST_SERIALIZE_FAILED:{error}"))?;
    Ok(sha256_hex(&canonical))
}

fn atomic_file(path: &Path, data: &[u8]) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "FABRIC_INSTALL_TARGET_PARENT_MISSING".to_owned())?;
    fs::create_dir_all(parent)
        .map_err(|error| format!("FABRIC_INSTALL_TARGET_PARENT_FAILED:{error}"))?;
    let seed = format!(
        "{}:{}:{}",
        path.to_string_lossy(),
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|error| format!("FABRIC_INSTALL_CLOCK_FAILED:{error}"))?
            .as_nanos()
    );
    let temporary = parent.join(format!(
        ".{}.{}",
        path.file_name().unwrap_or_default().to_string_lossy(),
        &sha256_hex(seed.as_bytes())[..12]
    ));
    let mut stream = fs::File::create(&temporary)
        .map_err(|error| format!("FABRIC_INSTALL_TEMP_CREATE_FAILED:{error}"))?;
    stream
        .write_all(data)
        .map_err(|error| format!("FABRIC_INSTALL_TEMP_WRITE_FAILED:{error}"))?;
    stream
        .sync_all()
        .map_err(|error| format!("FABRIC_INSTALL_TEMP_SYNC_FAILED:{error}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&temporary, fs::Permissions::from_mode(0o600))
            .map_err(|error| format!("FABRIC_INSTALL_TEMP_PERMISSIONS_FAILED:{error}"))?;
    }
    let result = fs::rename(&temporary, path)
        .map_err(|error| format!("FABRIC_INSTALL_ATOMIC_RENAME_FAILED:{error}"));
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

fn remove_target(path: &Path) -> Result<(), String> {
    let Ok(metadata) = fs::symlink_metadata(path) else {
        return Ok(());
    };
    if metadata.is_dir() && !metadata.file_type().is_symlink() {
        fs::remove_dir_all(path)
            .map_err(|error| format!("FABRIC_INSTALL_REMOVE_DIRECTORY_FAILED:{error}"))
    } else {
        fs::remove_file(path).map_err(|error| format!("FABRIC_INSTALL_REMOVE_FILE_FAILED:{error}"))
    }
}

fn copy_tree(source: &Path, target: &Path) -> Result<(), String> {
    if target.exists() || fs::symlink_metadata(target).is_ok() {
        remove_target(target)?;
    }
    fs::create_dir_all(target)
        .map_err(|error| format!("FABRIC_INSTALL_COPY_DIRECTORY_FAILED:{error}"))?;
    let mut entries = fs::read_dir(source)
        .map_err(|error| format!("FABRIC_INSTALL_COPY_READ_FAILED:{error}"))?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("FABRIC_INSTALL_COPY_READ_FAILED:{error}"))?;
    entries.sort_by_key(|entry| entry.path());
    for entry in entries {
        let source_path = entry.path();
        let target_path = target.join(entry.file_name());
        let metadata = fs::symlink_metadata(&source_path)
            .map_err(|error| format!("FABRIC_INSTALL_COPY_METADATA_FAILED:{error}"))?;
        if metadata.file_type().is_symlink() {
            let real = source_path
                .canonicalize()
                .map_err(|error| format!("FABRIC_INSTALL_COPY_SYMLINK_FAILED:{error}"))?;
            if real.is_dir() {
                copy_tree(&real, &target_path)?;
            } else {
                fs::copy(&real, &target_path)
                    .map_err(|error| format!("FABRIC_INSTALL_COPY_FILE_FAILED:{error}"))?;
            }
        } else if metadata.is_dir() {
            copy_tree(&source_path, &target_path)?;
        } else if metadata.is_file() {
            fs::copy(&source_path, &target_path)
                .map_err(|error| format!("FABRIC_INSTALL_COPY_FILE_FAILED:{error}"))?;
        }
    }
    Ok(())
}

fn copy_tree_atomic(source: &Path, target: &Path) -> Result<(), String> {
    let parent = target
        .parent()
        .ok_or_else(|| "FABRIC_INSTALL_TARGET_PARENT_MISSING".to_owned())?;
    fs::create_dir_all(parent)
        .map_err(|error| format!("FABRIC_INSTALL_TARGET_PARENT_FAILED:{error}"))?;
    let seed = format!(
        "{}:{}:{}",
        target.to_string_lossy(),
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|error| format!("FABRIC_INSTALL_CLOCK_FAILED:{error}"))?
            .as_nanos()
    );
    let temporary = parent.join(format!(
        ".{}.syntavra-{}",
        target.file_name().unwrap_or_default().to_string_lossy(),
        &sha256_hex(seed.as_bytes())[..16]
    ));
    copy_tree(source, &temporary)?;
    let result = (|| {
        remove_target(target)?;
        fs::rename(&temporary, target)
            .map_err(|error| format!("FABRIC_INSTALL_ATOMIC_DIRECTORY_RENAME_FAILED:{error}"))
    })();
    if result.is_err() {
        let _ = remove_target(&temporary);
    }
    result
}

fn backup(transaction: &Path, root: &Path, target: &Path) -> Result<String, String> {
    if fs::symlink_metadata(target).is_err() {
        return Ok(String::new());
    }
    let relative = target
        .strip_prefix(root)
        .map_err(|_| "FABRIC_INSTALL_BACKUP_RELATIVE_FAILED".to_owned())?;
    let destination = transaction.join("backup").join(relative);
    let metadata = fs::symlink_metadata(target)
        .map_err(|error| format!("FABRIC_INSTALL_BACKUP_METADATA_FAILED:{error}"))?;
    if metadata.file_type().is_symlink() {
        return Err("FABRIC_INSTALL_BACKUP_SYMLINK_UNSUPPORTED".to_owned());
    }
    if metadata.is_dir() {
        copy_tree(target, &destination)?;
    } else {
        if let Some(parent) = destination.parent() {
            fs::create_dir_all(parent)
                .map_err(|error| format!("FABRIC_INSTALL_BACKUP_PARENT_FAILED:{error}"))?;
        }
        fs::copy(target, &destination)
            .map_err(|error| format!("FABRIC_INSTALL_BACKUP_COPY_FAILED:{error}"))?;
    }
    Ok(destination.to_string_lossy().into_owned())
}

fn initialize_database(path: &Path) -> Result<Connection, String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("FABRIC_INSTALL_DATABASE_PARENT_FAILED:{error}"))?;
    }
    let connection = Connection::open(path)
        .map_err(|error| format!("FABRIC_INSTALL_DATABASE_OPEN_FAILED:{error}"))?;
    connection
        .busy_timeout(Duration::from_secs(30))
        .map_err(|error| format!("FABRIC_INSTALL_DATABASE_TIMEOUT_FAILED:{error}"))?;
    connection
        .execute_batch(
            "PRAGMA journal_mode=WAL;\
             PRAGMA foreign_keys=ON;\
             PRAGMA synchronous=NORMAL;\
             CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);\
             CREATE TABLE IF NOT EXISTS jobs(\
                job_id TEXT PRIMARY KEY,state TEXT NOT NULL,argv_json TEXT NOT NULL,cwd TEXT NOT NULL,\
                created_at REAL NOT NULL,started_at REAL,completed_at REAL,pid INTEGER,exit_code INTEGER,\
                timed_out INTEGER NOT NULL DEFAULT 0,cancelled INTEGER NOT NULL DEFAULT 0,\
                summary TEXT NOT NULL DEFAULT '',evidence_handle TEXT NOT NULL DEFAULT '',\
                error TEXT NOT NULL DEFAULT '',timeout_seconds REAL NOT NULL DEFAULT 0,\
                stdout_path TEXT NOT NULL DEFAULT '',stderr_path TEXT NOT NULL DEFAULT '',\
                repository_tree TEXT NOT NULL DEFAULT 'unknown',environment_hash TEXT NOT NULL DEFAULT 'unknown',\
                project_id TEXT NOT NULL DEFAULT ''\
             );\
             CREATE INDEX IF NOT EXISTS jobs_state_idx ON jobs(state, created_at DESC);\
             CREATE TABLE IF NOT EXISTS completion_events(\
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL UNIQUE,state TEXT NOT NULL,\
                exit_code INTEGER,completed_at REAL NOT NULL,evidence_handle TEXT NOT NULL,payload_json TEXT NOT NULL,\
                FOREIGN KEY(job_id) REFERENCES jobs(job_id)\
             );\
             CREATE TABLE IF NOT EXISTS verifier_results(\
                cache_key TEXT PRIMARY KEY,command_json TEXT NOT NULL,tree_hash TEXT NOT NULL,\
                environment_hash TEXT NOT NULL,dependency_hash TEXT NOT NULL,toolchain_hash TEXT NOT NULL,\
                success INTEGER NOT NULL,exit_code INTEGER NOT NULL,evidence_handle TEXT NOT NULL,\
                affected_paths_json TEXT NOT NULL,created_at REAL NOT NULL\
             );\
             CREATE TABLE IF NOT EXISTS host_install_transactions(\
                transaction_id TEXT PRIMARY KEY,host TEXT NOT NULL,scope TEXT NOT NULL,root TEXT NOT NULL,\
                status TEXT NOT NULL,manifest_json TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL\
             );\
             CREATE INDEX IF NOT EXISTS host_install_host_idx \
                ON host_install_transactions(host,scope,created_at);",
        )
        .map_err(|error| format!("FABRIC_INSTALL_DATABASE_SCHEMA_FAILED:{error}"))?;
    connection
        .execute(
            "INSERT INTO metadata(key,value) VALUES('schema_version','2') \
             ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            [],
        )
        .map_err(|error| format!("FABRIC_INSTALL_DATABASE_VERSION_FAILED:{error}"))?;
    Ok(connection)
}

fn overlay(contract: &Value) -> Value {
    contract["overlay"].clone()
}

fn stage(
    contract: &Value,
    root: &Path,
    source_skill: &Path,
) -> Result<Vec<Staged>, String> {
    let mut staged = Vec::new();
    let config_path = contract["config_path"].as_str().unwrap_or_default();
    if !config_path.is_empty() {
        let target = safe_target(root, config_path)?;
        if target.exists() && !target.is_file() {
            return Err(format!("IsADirectoryError: {}", target.to_string_lossy()));
        }
        let existing = if target.is_file() {
            let text = fs::read_to_string(&target).map_err(|error| {
                format!(
                    "ValueError: host config is not valid JSON: {}: {error}",
                    target.to_string_lossy()
                )
            })?;
            let value = serde_json::from_str::<Value>(&text).map_err(|error| {
                format!(
                    "ValueError: host config is not valid JSON: {}: {error}",
                    target.to_string_lossy()
                )
            })?;
            if !value.is_object() {
                return Err(format!(
                    "TypeError: host config root must be an object: {}",
                    target.to_string_lossy()
                ));
            }
            value
        } else {
            json!({})
        };
        let payload = canonical_bytes(&recursive_merge(&existing, &overlay(contract)))?;
        staged.push(Staged {
            target: target.clone(),
            kind: "json-config".to_owned(),
            payload: Payload::Bytes(payload),
            existed: fs::symlink_metadata(&target).is_ok(),
            before_hash: digest(&target)?,
            relative: config_path.to_owned(),
        });
    }

    let skill_path = contract["skill_path"].as_str().unwrap_or_default();
    if !skill_path.is_empty() {
        let target = safe_target(root, skill_path)?;
        let text_target = target
            .extension()
            .and_then(|value| value.to_str())
            .is_some_and(|value| {
                matches!(value.to_ascii_lowercase().as_str(), "md" | "mdc" | "txt")
            })
            || target.file_name().and_then(|value| value.to_str()) == Some("AGENTS.md");
        let payload = if text_target {
            let existing = if target.is_file() {
                fs::read_to_string(&target).unwrap_or_default()
            } else {
                String::new()
            };
            let source = fs::read_to_string(source_skill.join("SKILL.md"))
                .map_err(|error| format!("FABRIC_INSTALL_SKILL_READ_FAILED:{error}"))?;
            Payload::Bytes(managed_text(&existing, &source).into_bytes())
        } else {
            Payload::Directory(source_skill.to_path_buf())
        };
        staged.push(Staged {
            target: target.clone(),
            kind: if text_target {
                "managed-text".to_owned()
            } else {
                "skill-directory".to_owned()
            },
            payload,
            existed: fs::symlink_metadata(&target).is_ok(),
            before_hash: digest(&target)?,
            relative: skill_path.to_owned(),
        });
    }
    Ok(staged)
}

fn verify(contract: &Value, root: &Path, scope: &str) -> Result<Value, String> {
    let mut reasons = Vec::<Value>::new();
    let mut details = Map::new();
    let config_path = contract["config_path"].as_str().unwrap_or_default();
    if !config_path.is_empty() {
        let target = safe_target(root, config_path)?;
        if !target.is_file() {
            reasons.push(Value::from("missing-config"));
        } else {
            match fs::read_to_string(&target)
                .ok()
                .and_then(|text| serde_json::from_str::<Value>(&text).ok())
            {
                None => reasons.push(Value::from("invalid-config-json")),
                Some(config) => {
                    if config
                        .get("mcpServers")
                        .and_then(|row| row.get("syntavra"))
                        .and_then(|row| row.get("command"))
                        .and_then(Value::as_str)
                        != Some("syntavra")
                    {
                        reasons.push(Value::from("missing-syntavra-mcp"));
                    }
                    if contract["hooks_required"].as_bool() == Some(true)
                        && config.get("hooks").and_then(Value::as_object).is_none()
                    {
                        reasons.push(Value::from("missing-hooks"));
                    }
                }
            }
        }
        details.insert(
            "config".to_owned(),
            json!({"path": config_path, "hash": digest(&target)?}),
        );
    }
    let skill_path = contract["skill_path"].as_str().unwrap_or_default();
    if !skill_path.is_empty() {
        let target = safe_target(root, skill_path)?;
        if fs::symlink_metadata(&target).is_err() {
            reasons.push(Value::from("missing-skill"));
        } else if target.is_file() {
            let text = fs::read_to_string(&target).unwrap_or_default();
            if !text.contains(TEXT_BEGIN) || !text.contains(TEXT_END) {
                reasons.push(Value::from("unmanaged-skill-file"));
            }
        } else if !target.join("SKILL.md").is_file() {
            reasons.push(Value::from("missing-skill-entrypoint"));
        }
        details.insert(
            "skill".to_owned(),
            json!({"path": skill_path, "hash": digest(&target)?}),
        );
    }
    let ok = reasons.is_empty();
    let negotiation = if ok {
        &contract["negotiation_installed_true"]
    } else {
        &contract["negotiation_installed_false"]
    };
    Ok(json!({
        "ok": ok,
        "host": contract["host"],
        "scope": scope,
        "root": root.to_string_lossy(),
        "mode": negotiation["mode"],
        "reasons": reasons,
        "details": details,
    }))
}

fn rollback_applied(applied: &[(PathBuf, bool, String)]) -> Result<(), String> {
    for (target, existed, backup_path) in applied.iter().rev() {
        remove_target(target)?;
        if *existed && !backup_path.is_empty() {
            let saved = Path::new(backup_path);
            if saved.is_dir() {
                copy_tree(saved, target)?;
            } else if saved.is_file() {
                if let Some(parent) = target.parent() {
                    fs::create_dir_all(parent).map_err(|error| {
                        format!("FABRIC_INSTALL_ROLLBACK_PARENT_FAILED:{error}")
                    })?;
                }
                fs::copy(saved, target)
                    .map_err(|error| format!("FABRIC_INSTALL_ROLLBACK_COPY_FAILED:{error}"))?;
            }
        }
    }
    Ok(())
}

fn result_value(
    id: &str,
    contract: &Value,
    scope: &str,
    root: &Path,
    status: &str,
    changes: &[Change],
    verification: Value,
    created_at: f64,
) -> Value {
    json!({
        "transaction_id": id,
        "host": contract["host"],
        "scope": scope,
        "root": root.to_string_lossy(),
        "status": status,
        "changes": changes.iter().map(Change::value).collect::<Vec<_>>(),
        "verification": verification,
        "created_at": created_at,
    })
}

pub fn execute(
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    let host = host_name(arguments)?;
    let scope = option_value(arguments, "--scope")?.unwrap_or_else(|| "project".to_owned());
    if !matches!(scope.as_str(), "project" | "user") {
        return Err("scope must be project or user".to_owned());
    }
    let source_skill = skill_root(arguments, project_root)?;
    let home = home(arguments)?;
    let root = if scope == "project" {
        project_root.to_path_buf()
    } else {
        home
    };
    let contract = super::native_expansion::fabric_install_contract(
        &host,
        project_root,
        &scope,
    )?;
    let database_path = state_root.join("host-installations.sqlite3");
    let connection = initialize_database(&database_path)?;
    let storage = state_root.join("host-installations");
    fs::create_dir_all(&storage)
        .map_err(|error| format!("FABRIC_INSTALL_STORAGE_FAILED:{error}"))?;
    let staged = stage(&contract, &root, &source_skill)?;
    let id = transaction_id(&host)?;
    let created_at = now()?;

    if has_flag(arguments, "--dry-run") {
        let mut changes = Vec::new();
        for item in staged {
            let after_hash = match item.payload {
                Payload::Bytes(bytes) => sha256_hex(&bytes),
                Payload::Directory(path) => digest(&path)?,
            };
            changes.push(Change {
                path: item.relative,
                kind: item.kind,
                action: if item.existed {
                    "would-update"
                } else {
                    "would-create"
                }
                .to_owned(),
                existed: item.existed,
                before_hash: item.before_hash,
                after_hash,
                backup_path: String::new(),
            });
        }
        let value = result_value(
            &id,
            &contract,
            &scope,
            &root,
            "dry-run",
            &changes,
            json!({"ok": true, "dry_run": true, "plan": contract["plan"]}),
            created_at,
        );
        return option_value(arguments, "--output")?.map_or_else(
            || Ok(value.clone()),
            |path| super::native_fabric_doctor::write_json_output(&PathBuf::from(path), &value),
        );
    }

    let transaction = storage.join(&id);
    fs::create_dir(&transaction)
        .map_err(|error| format!("FABRIC_INSTALL_TRANSACTION_CREATE_FAILED:{error}"))?;
    let mut changes = Vec::<Change>::new();
    let mut applied = Vec::<(PathBuf, bool, String)>::new();
    let application = (|| -> Result<Value, String> {
        for item in staged {
            let backup_path = backup(&transaction, &root, &item.target)?;
            match item.payload {
                Payload::Bytes(bytes) => atomic_file(&item.target, &bytes)?,
                Payload::Directory(path) => copy_tree_atomic(&path, &item.target)?,
            }
            let after_hash = digest(&item.target)?;
            changes.push(Change {
                path: item.relative,
                kind: item.kind,
                action: if item.existed { "updated" } else { "created" }.to_owned(),
                existed: item.existed,
                before_hash: item.before_hash,
                after_hash,
                backup_path: backup_path.clone(),
            });
            applied.push((item.target, item.existed, backup_path));
        }
        let verification = verify(&contract, &root, &scope)?;
        if verification["ok"].as_bool() != Some(true) {
            return Err(format!(
                "installation verification failed: {}",
                verification["reasons"]
            ));
        }
        Ok(verification)
    })();

    let verification = match application {
        Ok(value) => value,
        Err(error) => {
            let rollback = rollback_applied(&applied);
            let _ = fs::remove_dir_all(&transaction);
            rollback?;
            return Err(error);
        }
    };
    let value = result_value(
        &id,
        &contract,
        &scope,
        &root,
        "applied",
        &changes,
        verification,
        created_at,
    );
    fs::write(transaction.join("manifest.json"), canonical_bytes(&value)?)
        .map_err(|error| format!("FABRIC_INSTALL_MANIFEST_WRITE_FAILED:{error}"))?;
    connection
        .execute(
            "INSERT INTO host_install_transactions(\
                transaction_id,host,scope,root,status,manifest_json,created_at,updated_at\
             ) VALUES(?,?,?,?,?,?,?,?)",
            params![
                id,
                host,
                scope,
                root.to_string_lossy(),
                "applied",
                serde_json::to_string(&value)
                    .map_err(|error| format!("FABRIC_INSTALL_JSON_SERIALIZE_FAILED:{error}"))?,
                created_at,
                now()?,
            ],
        )
        .map_err(|error| format!("FABRIC_INSTALL_TRANSACTION_RECORD_FAILED:{error}"))?;

    option_value(arguments, "--output")?.map_or_else(
        || Ok(value.clone()),
        |path| super::native_fabric_doctor::write_json_output(&PathBuf::from(path), &value),
    )
}

#[cfg(test)]
mod tests {
    use super::{managed_text, recursive_merge, supports};
    use serde_json::json;

    #[test]
    fn routes_fabric_install_only() {
        assert!(supports(&["fabric".to_owned(), "install".to_owned()]));
        assert!(!supports(&["install".to_owned()]));
    }

    #[test]
    fn recursive_merge_preserves_unrelated_keys() {
        let value = recursive_merge(
            &json!({"user": true, "mcpServers": {"other": {"command": "x"}}}),
            &json!({"mcpServers": {"syntavra": {"command": "syntavra"}}}),
        );
        assert_eq!(value["user"], true);
        assert_eq!(value["mcpServers"]["other"]["command"], "x");
        assert_eq!(value["mcpServers"]["syntavra"]["command"], "syntavra");
    }

    #[test]
    fn managed_text_replaces_one_owned_block() {
        let first = managed_text("header\n", "skill one\n");
        let second = managed_text(&first, "skill two\n");
        assert_eq!(second.matches(super::TEXT_BEGIN).count(), 1);
        assert!(second.contains("skill two"));
        assert!(!second.contains("skill one"));
    }
}
