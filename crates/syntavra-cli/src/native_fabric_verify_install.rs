#![forbid(unsafe_code)]

use std::path::{Path, PathBuf};

use serde_json::Value;

pub struct Decision {
    pub value: Value,
    pub exit_code: u8,
}

pub fn supports(command: &[String]) -> bool {
    matches!(command, [fabric, action]
        if fabric == "fabric" && action == "verify-install")
}

fn command_start(arguments: &[String]) -> Result<usize, String> {
    arguments
        .windows(2)
        .position(|row| row[0] == "fabric" && row[1] == "verify-install")
        .map(|index| index + 2)
        .ok_or_else(|| "FABRIC_VERIFY_INSTALL_COMMAND_MISSING".to_owned())
}

fn host_name(arguments: &[String]) -> Result<String, String> {
    let index = command_start(arguments)?;
    let value = arguments
        .get(index)
        .ok_or_else(|| "fabric verify-install host_name is required".to_owned())?;
    if value.starts_with('-') {
        return Err("fabric verify-install host_name is required".to_owned());
    }
    Ok(value.to_ascii_lowercase())
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

fn home(arguments: &[String]) -> Result<PathBuf, String> {
    if let Some(value) = option_value(arguments, "--home")? {
        return Ok(PathBuf::from(value));
    }
    std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" })
        .map(PathBuf::from)
        .ok_or_else(|| "FABRIC_VERIFY_INSTALL_HOME_MISSING".to_owned())
}

fn verify_skill_source(arguments: &[String], project_root: &Path) -> Result<(), String> {
    let selected = option_value(arguments, "--skill-root")?
        .map(PathBuf::from)
        .unwrap_or_else(|| project_root.join("skills").join("syntavra"));
    if !selected.join("SKILL.md").is_file() {
        return Err(format!(
            "Syntavra skill source is incomplete: {}",
            selected.to_string_lossy()
        ));
    }
    Ok(())
}

fn public_verification_shape(mut verification: Value) -> Value {
    let missing_config = verification["reasons"]
        .as_array()
        .is_some_and(|reasons| reasons.iter().any(|reason| reason == "missing-config"));
    if missing_config {
        if let Some(details) = verification
            .get_mut("details")
            .and_then(Value::as_object_mut)
        {
            details.remove("config");
        }
    }
    verification
}

pub fn execute(
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Decision, String> {
    let host = host_name(arguments)?;
    let scope = option_value(arguments, "--scope")?.unwrap_or_else(|| "project".to_owned());
    if !matches!(scope.as_str(), "project" | "user") {
        return Err("scope must be project or user".to_owned());
    }
    verify_skill_source(arguments, project_root)?;
    let root = if scope == "project" {
        project_root.to_path_buf()
    } else {
        home(arguments)?
    };
    let contract = super::native_expansion::fabric_install_contract(&host, project_root, &scope)?;
    let _connection = super::native_fabric_install::initialize_database(
        &state_root.join("host-installations.sqlite3"),
    )?;
    let verification = public_verification_shape(super::native_fabric_install::verify(
        &contract, &root, &scope,
    )?);
    let ok = verification["ok"].as_bool() == Some(true);
    let value = option_value(arguments, "--output")?.map_or_else(
        || Ok(verification.clone()),
        |path| super::native_fabric_doctor::write_json_output(&PathBuf::from(path), &verification),
    )?;
    Ok(Decision {
        value,
        exit_code: if ok { 0 } else { 3 },
    })
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn routes_verify_install_only() {
        assert!(supports(&[
            "fabric".to_owned(),
            "verify-install".to_owned()
        ]));
        assert!(!supports(&["fabric".to_owned(), "install".to_owned()]));
    }
}
