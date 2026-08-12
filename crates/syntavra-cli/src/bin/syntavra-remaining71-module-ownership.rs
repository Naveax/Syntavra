#![forbid(unsafe_code)]

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::Path;
use std::process::ExitCode;

use serde_json::{json, Value};

// Dependency closure required by the Remaining-71 family modules. These are
// support modules only; route identity still comes exclusively from the
// parser-derived inventory report supplied at runtime.
#[path = "../native_backup.rs"]
mod native_backup;
#[path = "../native_evidence_store.rs"]
mod native_evidence_store;
#[path = "../native_artifact_store.rs"]
mod native_artifact_store;
#[path = "../native_structural.rs"]
mod native_structural;

#[path = "../native_remaining71_memory.rs"]
mod native_remaining71_memory;
#[path = "../native_remaining71_platform_misc.rs"]
mod native_remaining71_platform_misc;
#[path = "../native_remaining71_security.rs"]
mod native_remaining71_security;
#[path = "../native_remaining71_sandbox.rs"]
mod native_remaining71_sandbox;
#[path = "../native_remaining71_proxy.rs"]
mod native_remaining71_proxy;
#[path = "../native_remaining71_proof.rs"]
mod native_remaining71_proof;
#[path = "../native_remaining71_graph.rs"]
mod native_remaining71_graph;
#[path = "../native_remaining71_agent.rs"]
mod native_remaining71_agent;
#[path = "../native_remaining71_agent_live.rs"]
mod native_remaining71_agent_live;
#[path = "../native_remaining71_competitive.rs"]
mod native_remaining71_competitive;
#[path = "../native_remaining71_context.rs"]
mod native_remaining71_context;
#[path = "../native_remaining71_headless.rs"]
mod native_remaining71_headless;

const EXPECTED_PUBLIC_ROUTE_COUNT: u64 = 245;
const EXPECTED_NATIVE_ROUTE_COUNT: u64 = 174;
const EXPECTED_REMAINING_ROUTE_COUNT: usize = 71;

type Supports = fn(&[String]) -> bool;

const OWNER_PREDICATES: [(&str, Supports); 12] = [
    ("native_remaining71_memory", native_remaining71_memory::supports),
    (
        "native_remaining71_platform_misc",
        native_remaining71_platform_misc::supports,
    ),
    (
        "native_remaining71_security",
        native_remaining71_security::supports,
    ),
    (
        "native_remaining71_sandbox",
        native_remaining71_sandbox::supports,
    ),
    ("native_remaining71_proxy", native_remaining71_proxy::supports),
    ("native_remaining71_proof", native_remaining71_proof::supports),
    ("native_remaining71_graph", native_remaining71_graph::supports),
    ("native_remaining71_agent", native_remaining71_agent::supports),
    (
        "native_remaining71_agent_live",
        native_remaining71_agent_live::supports,
    ),
    (
        "native_remaining71_competitive",
        native_remaining71_competitive::supports,
    ),
    (
        "native_remaining71_context",
        native_remaining71_context::supports,
    ),
    (
        "native_remaining71_headless",
        native_remaining71_headless::supports,
    ),
];

fn selector_path(route: &str) -> Vec<String> {
    route
        .split_whitespace()
        .take(2)
        .map(str::to_owned)
        .collect()
}

fn owner_candidates(command: &[String]) -> Vec<&'static str> {
    OWNER_PREDICATES
        .into_iter()
        .filter_map(|(name, supports)| supports(command).then_some(name))
        .collect()
}

fn report_u64(report: &Value, section: &str, field: &str) -> Result<u64, String> {
    report
        .get(section)
        .and_then(|value| value.get(field))
        .and_then(Value::as_u64)
        .ok_or_else(|| format!("inventory report missing integer {section}.{field}"))
}

fn load_report_routes(path: &Path) -> Result<Vec<String>, String> {
    let raw = fs::read(path)
        .map_err(|error| format!("failed to read inventory report {}: {error}", path.display()))?;
    let report: Value = serde_json::from_slice(&raw)
        .map_err(|error| format!("failed to parse inventory report {}: {error}", path.display()))?;
    if report.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err("inventory report is not canonical (ok != true)".to_owned());
    }

    let public_count = report_u64(&report, "python", "derived_count")?;
    let native_count = report_u64(&report, "rust", "native_count")?;
    let remaining_count = report_u64(&report, "rust", "missing_count")?;
    if public_count != EXPECTED_PUBLIC_ROUTE_COUNT
        || native_count != EXPECTED_NATIVE_ROUTE_COUNT
        || remaining_count != EXPECTED_REMAINING_ROUTE_COUNT as u64
    {
        return Err(format!(
            "inventory count contract mismatch: public={public_count} native={native_count} remaining={remaining_count}"
        ));
    }

    let rows = report
        .get("missing_routes")
        .and_then(Value::as_array)
        .ok_or_else(|| "inventory report missing array missing_routes".to_owned())?;
    let routes = rows
        .iter()
        .map(|value| {
            value
                .as_str()
                .filter(|route| !route.trim().is_empty())
                .map(str::to_owned)
                .ok_or_else(|| "inventory missing_routes contains invalid route".to_owned())
        })
        .collect::<Result<Vec<_>, _>>()?;
    let unique = routes.iter().collect::<BTreeSet<_>>();
    if routes.len() != EXPECTED_REMAINING_ROUTE_COUNT || unique.len() != routes.len() {
        return Err(format!(
            "inventory route identity mismatch: rows={} unique={} expected={EXPECTED_REMAINING_ROUTE_COUNT}",
            routes.len(),
            unique.len()
        ));
    }
    Ok(routes)
}

fn fail(message: &str) -> ExitCode {
    eprintln!(
        "{}",
        serde_json::to_string_pretty(&json!({
            "ok": false,
            "error": message,
            "claim_boundary": "lower Rust family-module ownership only; behavioral parity still requires differential execution",
        }))
        .unwrap_or_else(|_| "{\"ok\":false}".to_owned())
    );
    ExitCode::from(2)
}

fn main() -> ExitCode {
    let Some(report_path) = std::env::args_os().nth(1) else {
        return fail("usage: syntavra-remaining71-module-ownership <inventory-report.json>");
    };
    let routes = match load_report_routes(Path::new(&report_path)) {
        Ok(routes) => routes,
        Err(error) => return fail(&error),
    };

    let mut owner_modules = BTreeMap::<String, String>::new();
    let mut candidates_by_route = BTreeMap::<String, Vec<&'static str>>::new();
    let mut unowned_routes = Vec::<String>::new();
    let mut duplicate_routes = Vec::<String>::new();
    for route in &routes {
        let selector = selector_path(route);
        let candidates = owner_candidates(&selector);
        candidates_by_route.insert(route.clone(), candidates.clone());
        match candidates.as_slice() {
            [owner] => {
                owner_modules.insert(route.clone(), (*owner).to_owned());
            }
            [] => unowned_routes.push(route.clone()),
            _ => duplicate_routes.push(route.clone()),
        }
    }

    let ok = owner_modules.len() == EXPECTED_REMAINING_ROUTE_COUNT
        && unowned_routes.is_empty()
        && duplicate_routes.is_empty();
    println!(
        "{}",
        serde_json::to_string_pretty(&json!({
            "ok": ok,
            "authority": "tools/report_missing_native_public_routes.py missing_routes",
            "module_authority": "Remaining-71 Rust family modules' supports() predicates",
            "public_route_count": EXPECTED_PUBLIC_ROUTE_COUNT,
            "frozen_native_route_count": EXPECTED_NATIVE_ROUTE_COUNT,
            "report_derived_remaining_count": routes.len(),
            "owner_module_count": owner_modules.len(),
            "owner_modules": owner_modules,
            "owner_candidates": candidates_by_route,
            "module_unowned_count": unowned_routes.len(),
            "module_unowned_routes": unowned_routes,
            "duplicate_owner_count": duplicate_routes.len(),
            "duplicate_owner_routes": duplicate_routes,
            "claim_boundary": "lower Rust family-module ownership only; behavioral parity still requires differential execution",
        }))
        .unwrap_or_else(|_| "{\"ok\":false}".to_owned())
    );

    if ok {
        ExitCode::SUCCESS
    } else {
        ExitCode::from(2)
    }
}

#[cfg(test)]
mod tests {
    use super::{owner_candidates, selector_path};

    #[test]
    fn representative_remaining_selectors_have_one_owner() {
        for route in [
            "run memory-search",
            "run output-capture",
            "run capability-decide",
            "run sandbox-run",
            "provider proxy",
            "benchmark compare",
            "run graph-query",
            "run agent-plan",
            "agent run",
            "run rewrite",
            "run context-compile",
            "run headless-run",
        ] {
            let selector = selector_path(route);
            let owners = owner_candidates(&selector);
            assert_eq!(owners.len(), 1, "route={route} owners={owners:?}");
        }
    }
}
