#![forbid(unsafe_code)]

use std::env;
use std::path::{Path, PathBuf};

use serde_json::Value;

#[path = "migration_plan_read_only_contract.rs"]
mod migration_plan_read_only_contract;
#[path = "native_cache_amortize.rs"]
mod native_cache_amortize;
#[path = "native_product_legacy.rs"]
mod legacy;
#[path = "native_prove_plan.rs"]
mod native_prove_plan;
#[path = "native_read_only_product.rs"]
mod native_read_only_product;
#[path = "native_route.rs"]
mod native_route;
#[path = "read_only_cli_contract.rs"]
mod read_only_cli_contract;
#[path = "scheduler_read_only_contract.rs"]
mod scheduler_read_only_contract;
#[path = "telemetry_metrics_contract.rs"]
mod telemetry_metrics_contract;

fn project_root(arguments: &[String]) -> PathBuf {
    let selected = arguments
        .iter()
        .position(|value| value == "--project")
        .and_then(|index| arguments.get(index + 1))
        .cloned()
        .or_else(|| {
            arguments
                .iter()
                .find_map(|value| value.strip_prefix("--project=").map(str::to_owned))
        })
        .unwrap_or_else(|| ".".to_owned());
    let path = PathBuf::from(selected);
    if path.is_absolute() {
        path
    } else {
        env::current_dir()
            .unwrap_or_else(|_| PathBuf::from("."))
            .join(path)
    }
}

pub fn supports(command: &[String]) -> bool {
    native_read_only_product::supports(command)
        || legacy::supports(command)
        || (command.len() == 2 && command[0] == "run" && command[1] == "cache-amortize")
        || (command.len() == 2 && command[0] == "run" && command[1] == "route")
        || (command.len() == 2 && command[0] == "prove" && command[1] == "plan")
}

pub fn execute(command: &[String], state_root: &Path) -> Result<Option<Value>, String> {
    let arguments = env::args().skip(1).collect::<Vec<_>>();
    if native_read_only_product::supports(command) {
        return native_read_only_product::execute(
            command,
            &arguments,
            &project_root(&arguments),
            state_root,
        )
        .map(Some);
    }
    if command.len() == 2 && command[0] == "run" && command[1] == "cache-amortize" {
        return native_cache_amortize::execute(&arguments).map(Some);
    }
    if command.len() == 2 && command[0] == "run" && command[1] == "route" {
        let decision = native_route::execute(&arguments)?;
        if decision.exit_code != 0 {
            println!(
                "{}",
                serde_json::to_string_pretty(&decision.value)
                    .unwrap_or_else(|_| "{\"ok\":false}".to_owned())
            );
            std::process::exit(i32::from(decision.exit_code));
        }
        return Ok(Some(decision.value));
    }
    if command.len() == 2 && command[0] == "prove" && command[1] == "plan" {
        return Ok(Some(native_prove_plan::execute()));
    }
    legacy::execute(command, state_root)
}

#[cfg(test)]
mod tests {
    use super::project_root;

    #[test]
    fn resolves_explicit_project_root() {
        let root = project_root(&["--project".to_owned(), "/tmp/syntavra".to_owned()]);
        assert!(root.is_absolute());
        assert!(root.ends_with("syntavra"));
    }
}
