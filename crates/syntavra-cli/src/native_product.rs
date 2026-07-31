#![forbid(unsafe_code)]

use std::path::Path;

use serde_json::Value;

#[path = "native_product_legacy.rs"]
mod legacy;
#[path = "migration_plan_read_only_contract.rs"]
mod migration_plan_read_only_contract;
#[path = "native_cache_amortize.rs"]
mod native_cache_amortize;
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

pub fn supports(command: &[String]) -> bool {
    native_read_only_product::supports(command)
        || legacy::supports(command)
        || (command.len() == 2 && command[0] == "run" && command[1] == "cache-amortize")
        || (command.len() == 2 && command[0] == "run" && command[1] == "route")
        || (command.len() == 2 && command[0] == "prove" && command[1] == "plan")
}

pub fn execute(
    command: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Option<Value>, String> {
    let arguments = std::env::args().skip(1).collect::<Vec<_>>();
    if native_read_only_product::supports(command) {
        return native_read_only_product::execute(command, &arguments, project_root, state_root)
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
