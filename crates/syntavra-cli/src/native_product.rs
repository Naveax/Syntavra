#![forbid(unsafe_code)]

use std::path::Path;

use serde_json::Value;

#[path = "native_product_legacy.rs"]
mod legacy;
#[path = "native_prove_plan.rs"]
mod native_prove_plan;
#[path = "native_route.rs"]
mod native_route;

pub fn supports(command: &[String]) -> bool {
    legacy::supports(command)
        || (command.len() == 2 && command[0] == "run" && command[1] == "route")
        || (command.len() == 2 && command[0] == "prove" && command[1] == "plan")
}

pub fn execute(command: &[String], state_root: &Path) -> Result<Option<Value>, String> {
    if command.len() == 2 && command[0] == "run" && command[1] == "route" {
        let arguments = std::env::args().skip(1).collect::<Vec<_>>();
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
