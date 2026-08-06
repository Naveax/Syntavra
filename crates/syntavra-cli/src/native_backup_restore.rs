#![forbid(unsafe_code)]

use std::env;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

use super::native_backup::{initialize_evidence_state, initialize_roots, unique_temp_root};
use super::native_backup_verify::{open_sealed_file, safe_extract, safe_path, verify_extracted};

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action] if root == "backup" && action == "restore")
}

fn parse_arguments(arguments: &[String]) -> Result<(PathBuf, bool, bool), String> {
    let start = arguments
        .windows(2)
        .position(|row| row[0] == "backup" && row[1] == "restore")
        .map(|index| index + 2)
        .ok_or_else(|| "BACKUP_RESTORE_COMMAND_MISSING".to_owned())?;
    let tail = &arguments[start..];
    let plaintext = tail.iter().any(|value| value == "--plaintext");
    let apply = tail.iter().any(|value| value == "--apply");
    let path = tail
        .iter()
        .find(|value| !value.starts_with('-'))
        .ok_or_else(|| "BACKUP_RESTORE_PATH_MISSING".to_owned())?;
    let candidate = PathBuf::from(path);
    let absolute = if candidate.is_absolute() {
        candidate
    } else {
        env::current_dir()
            .map_err(|error| format!("BACKUP_RESTORE_CWD_FAILED:{error}"))?
            .join(candidate)
    };
    Ok((absolute, !plaintext, apply))
}

fn unix_seconds() -> Result<u64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_secs())
        .map_err(|error| format!("BACKUP_RESTORE_CLOCK_FAILED:{error}"))
}

fn materialize_and_extract(
    source: &Path,
    encrypted: bool,
    temporary: &Path,
    state_root: &Path,
    project_id: &str,
) -> Result<PathBuf, String> {
    let archive = if encrypted {
        let path = temporary.join("state.tar");
        open_sealed_file(source, &path, state_root, project_id)?;
        path
    } else {
        source.to_path_buf()
    };
    let extracted = temporary.join("extracted");
    safe_extract(&archive, &extracted)?;
    Ok(extracted)
}

fn verification_pass(
    source: &Path,
    encrypted: bool,
    state_root: &Path,
    project_id: &str,
) -> Result<Value, String> {
    let temporary = unique_temp_root()?;
    let result = (|| -> Result<Value, String> {
        let extracted =
            materialize_and_extract(source, encrypted, &temporary, state_root, project_id)?;
        verify_extracted(&extracted, project_id)
    })();
    let _ = fs::remove_dir_all(temporary);
    result
}

fn manifest_files(extracted: &Path) -> Result<Vec<String>, String> {
    let manifest_path = extracted.join("BACKUP_MANIFEST.json");
    let manifest = serde_json::from_slice::<Value>(
        &fs::read(manifest_path)
            .map_err(|error| format!("BACKUP_RESTORE_MANIFEST_READ_FAILED:{error}"))?,
    )
    .map_err(|error| format!("BACKUP_RESTORE_MANIFEST_INVALID:{error}"))?;
    let files = manifest["files"]
        .as_object()
        .ok_or_else(|| "BACKUP_RESTORE_MANIFEST_FILES_INVALID".to_owned())?;
    let mut relative = files.keys().cloned().collect::<Vec<_>>();
    relative.sort();
    Ok(relative)
}

fn scoped_target(state_root: &Path, relative: &Path) -> Result<PathBuf, String> {
    if !safe_path(relative) {
        return Err("BACKUP_RESTORE_PATH_ESCAPE".to_owned());
    }
    let target = state_root.join(relative);
    let parent = target
        .parent()
        .ok_or_else(|| "BACKUP_RESTORE_TARGET_PARENT_MISSING".to_owned())?;
    fs::create_dir_all(parent)
        .map_err(|error| format!("BACKUP_RESTORE_TARGET_PARENT_FAILED:{error}"))?;
    let canonical_root = state_root
        .canonicalize()
        .map_err(|error| format!("BACKUP_RESTORE_ROOT_CANONICALIZE_FAILED:{error}"))?;
    let canonical_parent = parent
        .canonicalize()
        .map_err(|error| format!("BACKUP_RESTORE_PARENT_CANONICALIZE_FAILED:{error}"))?;
    if !canonical_parent.starts_with(&canonical_root) {
        return Err("BACKUP_RESTORE_TARGET_SCOPE_MISMATCH".to_owned());
    }
    Ok(target)
}

fn copy_permissions(source: &Path, destination: &Path) -> Result<(), String> {
    let permissions = fs::metadata(source)
        .map_err(|error| format!("BACKUP_RESTORE_SOURCE_METADATA_FAILED:{error}"))?
        .permissions();
    fs::set_permissions(destination, permissions)
        .map_err(|error| format!("BACKUP_RESTORE_PERMISSION_FAILED:{error}"))
}

fn atomic_restore_file(source: &Path, target: &Path) -> Result<(), String> {
    if !source.is_file() {
        return Err("BACKUP_RESTORE_SOURCE_FILE_MISSING".to_owned());
    }
    let name = target
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| "BACKUP_RESTORE_TARGET_NAME_INVALID".to_owned())?;
    let temporary = target.with_file_name(format!("{name}.restore-tmp"));
    if fs::symlink_metadata(&temporary).is_ok() {
        fs::remove_file(&temporary)
            .map_err(|error| format!("BACKUP_RESTORE_TEMP_REMOVE_FAILED:{error}"))?;
    }
    let result = (|| -> Result<(), String> {
        let mut input = File::open(source)
            .map_err(|error| format!("BACKUP_RESTORE_SOURCE_OPEN_FAILED:{error}"))?;
        let mut output = OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&temporary)
            .map_err(|error| format!("BACKUP_RESTORE_TEMP_CREATE_FAILED:{error}"))?;
        let mut buffer = vec![0_u8; 1024 * 1024];
        loop {
            let read = input
                .read(&mut buffer)
                .map_err(|error| format!("BACKUP_RESTORE_SOURCE_READ_FAILED:{error}"))?;
            if read == 0 {
                break;
            }
            output
                .write_all(&buffer[..read])
                .map_err(|error| format!("BACKUP_RESTORE_TEMP_WRITE_FAILED:{error}"))?;
        }
        output
            .flush()
            .and_then(|_| output.sync_all())
            .map_err(|error| format!("BACKUP_RESTORE_TEMP_SYNC_FAILED:{error}"))?;
        copy_permissions(source, &temporary)?;
        if target.is_dir() {
            return Err("BACKUP_RESTORE_TARGET_IS_DIRECTORY".to_owned());
        }
        #[cfg(windows)]
        if fs::symlink_metadata(target).is_ok() {
            fs::remove_file(target)
                .map_err(|error| format!("BACKUP_RESTORE_TARGET_REMOVE_FAILED:{error}"))?;
        }
        fs::rename(&temporary, target)
            .map_err(|error| format!("BACKUP_RESTORE_REPLACE_FAILED:{error}"))?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

fn create_rollback(project_root: &Path, state_root: &Path) -> Result<PathBuf, String> {
    let rollback = state_root
        .join("backups")
        .join(format!("pre-restore-{}.scbackup", unix_seconds()?));
    let arguments = vec![
        "backup".to_owned(),
        "create".to_owned(),
        rollback.to_string_lossy().into_owned(),
    ];
    super::native_backup::execute(&arguments, project_root, state_root)?;
    Ok(rollback)
}

pub fn execute(
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    initialize_evidence_state(state_root)?;
    initialize_roots(state_root)?;
    let (source, encrypted, apply) = parse_arguments(arguments)?;
    if !source.is_file() {
        return Err("BACKUP_FILE_MISSING".to_owned());
    }
    let project_id =
        super::state_snapshot_contract::project_id_for_root(&project_root.to_string_lossy())?;
    let verification = verification_pass(&source, encrypted, state_root, &project_id)?;
    if verification["ok"].as_bool() != Some(true) {
        return Err("BACKUP_VERIFICATION_FAILED".to_owned());
    }
    if !apply {
        return Ok(json!({
            "ok": true,
            "dry_run": true,
            "files": verification["files"],
            "failures": verification["failures"],
        }));
    }

    let temporary = unique_temp_root()?;
    let result = (|| -> Result<Value, String> {
        let extracted =
            materialize_and_extract(&source, encrypted, &temporary, state_root, &project_id)?;
        let rollback = create_rollback(project_root, state_root)?;
        let files = manifest_files(&extracted)?;
        let mut restored = 0_usize;
        for relative in files {
            let relative_path = Path::new(&relative);
            let source_path = extracted.join(relative_path);
            let target = scoped_target(state_root, relative_path)?;
            atomic_restore_file(&source_path, &target)?;
            restored += 1;
        }
        Ok(json!({
            "ok": true,
            "dry_run": false,
            "restored": restored,
            "rollback": rollback.to_string_lossy(),
        }))
    })();
    let _ = fs::remove_dir_all(temporary);
    result
}

#[cfg(test)]
mod tests {
    use super::{parse_arguments, supports};

    #[test]
    fn routes_backup_restore_only() {
        assert!(supports(&["backup".to_owned(), "restore".to_owned()]));
        assert!(!supports(&["backup".to_owned(), "verify".to_owned()]));
    }

    #[test]
    fn defaults_to_encrypted_dry_run() {
        let (_, encrypted, apply) = parse_arguments(&[
            "backup".to_owned(),
            "restore".to_owned(),
            "snapshot.scbackup".to_owned(),
        ])
        .expect("parse");
        assert!(encrypted);
        assert!(!apply);
    }
}
