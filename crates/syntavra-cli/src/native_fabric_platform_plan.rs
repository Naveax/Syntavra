#![forbid(unsafe_code)]

use std::path::{Path, PathBuf};

use serde_json::Value;

pub fn supports(command: &[String]) -> bool {
    matches!(command, [fabric, action] if fabric == "fabric" && action == "platform-plan")
}

fn flag(arguments: &[String], name: &str) -> bool {
    arguments.iter().any(|value| value == name)
}

fn value(arguments: &[String], name: &str) -> Result<Option<String>, String> {
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
        } else if let Some(item) = arguments[index].strip_prefix(&prefix) {
            found = Some(item.to_owned());
        }
        index += 1;
    }
    Ok(found)
}

pub fn execute(
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    let _database =
        super::native_fabric_doctor::open_database(&state_root.join("competitive-fabric.sqlite3"))?;
    let scope = value(arguments, "--scope")?.unwrap_or_else(|| "project".to_owned());
    if scope != "project" && scope != "user" {
        return Err("scope must be project or user".to_owned());
    }
    let result = if flag(arguments, "--all") {
        super::native_expansion::all_platform_plan_contracts(project_root, &scope)?
    } else {
        let host = value(arguments, "--host-name")?
            .or(value(arguments, "--host")?)
            .unwrap_or_else(|| "unknown".to_owned());
        super::native_expansion::platform_plan_contract(&host, project_root, &scope)?
    };
    value(arguments, "--output")?.map_or_else(
        || Ok(result.clone()),
        |path| super::native_fabric_doctor::write_json_output(&PathBuf::from(path), &result),
    )
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn supports_only_platform_plan() {
        assert!(supports(&["fabric".to_owned(), "platform-plan".to_owned()]));
        assert!(!supports(&["fabric".to_owned(), "install".to_owned()]));
    }
}
