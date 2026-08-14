#![forbid(unsafe_code)]

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::Path;
use std::process::ExitCode;

use serde_json::{json, Value};

#[path = "../native_product.rs"]
mod native_product;
#[path = "../native_product_legacy.rs"]
mod native_product_legacy;

// Dependency closure required when the ownership audit reuses the exact
// Remaining-71 family modules' supports() predicates. These support modules do
// not define route identity; route identities still come only from the
// parser-derived inventory report supplied at runtime.
#[path = "../native_artifact_store.rs"]
mod native_artifact_store;
#[path = "../native_backup.rs"]
mod native_backup;
#[path = "../native_evidence_store.rs"]
mod native_evidence_store;
#[path = "../native_structural.rs"]
mod native_structural;
#[path = "../state_snapshot_contract.rs"]
mod state_snapshot_contract;

#[path = "../native_remaining71_agent.rs"]
mod native_remaining71_agent;
#[path = "../native_remaining71_agent_live.rs"]
mod native_remaining71_agent_live;
#[path = "../native_remaining71_competitive.rs"]
mod native_remaining71_competitive;
#[path = "../native_remaining71_context.rs"]
mod native_remaining71_context;
#[path = "../native_remaining71_graph.rs"]
mod native_remaining71_graph;
#[path = "../native_remaining71_headless.rs"]
mod native_remaining71_headless;
#[path = "../native_remaining71_memory.rs"]
mod native_remaining71_memory;
#[path = "../native_remaining71_platform_misc.rs"]
mod native_remaining71_platform_misc;
#[path = "../native_remaining71_proof.rs"]
mod native_remaining71_proof;
#[path = "../native_remaining71_proxy.rs"]
mod native_remaining71_proxy;
#[path = "../native_remaining71_sandbox.rs"]
mod native_remaining71_sandbox;
#[path = "../native_remaining71_security.rs"]
mod native_remaining71_security;

const EXPECTED_PUBLIC_ROUTE_COUNT: u64 = 245;
const FROZEN_NATIVE_ROUTE_COUNT: u64 = 174;
const FROZEN_REMAINING_ROUTE_COUNT: usize = 71;
const PROMOTED_NATIVE_ROUTE_COUNT: u64 = 245;
const PROMOTED_REMAINING_ROUTE_COUNT: usize = 0;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum InventoryState {
    Frozen,
    Promoted,
}

impl InventoryState {
    fn label(self) -> &'static str {
        match self {
            Self::Frozen => "frozen-174-71",
            Self::Promoted => "promoted-245-0",
        }
    }
}

struct InventoryReport {
    state: InventoryState,
    native_count: u64,
    public_routes: Vec<String>,
    remaining_routes: Vec<String>,
}

fn selector_path(route: &str) -> Vec<String> {
    // The public selector owns these remaining routes at two components. The
    // third component on language/proxy-service routes is an action consumed by
    // the already-selected native family, not a separate engine selector path.
    route
        .split_whitespace()
        .take(2)
        .map(str::to_owned)
        .collect()
}

fn remaining71_owner_modules(command: &[String]) -> Vec<&'static str> {
    // Probe the production supports() predicates themselves. Most frozen
    // Remaining-71 routes live in the dedicated family modules, while
    // provider capture is already implemented by native_product_legacy and is
    // intentionally still unpromoted in the public 174/245 contract.
    let candidates: [(&str, fn(&[String]) -> bool); 13] = [
        (
            "native_remaining71_memory",
            native_remaining71_memory::supports,
        ),
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
        (
            "native_remaining71_proxy",
            native_remaining71_proxy::supports,
        ),
        (
            "native_remaining71_proof",
            native_remaining71_proof::supports,
        ),
        (
            "native_remaining71_graph",
            native_remaining71_graph::supports,
        ),
        (
            "native_remaining71_agent",
            native_remaining71_agent::supports,
        ),
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
        ("native_product_legacy", native_product_legacy::supports),
    ];
    candidates
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

fn inventory_state(
    public_count: u64,
    native_count: u64,
    remaining_count: u64,
) -> Result<InventoryState, String> {
    match (public_count, native_count, remaining_count) {
        (EXPECTED_PUBLIC_ROUTE_COUNT, FROZEN_NATIVE_ROUTE_COUNT, remaining)
            if remaining == FROZEN_REMAINING_ROUTE_COUNT as u64 =>
        {
            Ok(InventoryState::Frozen)
        }
        (EXPECTED_PUBLIC_ROUTE_COUNT, PROMOTED_NATIVE_ROUTE_COUNT, remaining)
            if remaining == PROMOTED_REMAINING_ROUTE_COUNT as u64 =>
        {
            Ok(InventoryState::Promoted)
        }
        _ => Err(format!(
            "inventory count contract mismatch: public={public_count} native={native_count} remaining={remaining_count}; accepted states are {EXPECTED_PUBLIC_ROUTE_COUNT}/{FROZEN_NATIVE_ROUTE_COUNT}/{FROZEN_REMAINING_ROUTE_COUNT} or {EXPECTED_PUBLIC_ROUTE_COUNT}/{PROMOTED_NATIVE_ROUTE_COUNT}/{PROMOTED_REMAINING_ROUTE_COUNT}"
        )),
    }
}

fn string_routes(rows: &[Value], field_name: &str) -> Result<Vec<String>, String> {
    let routes = rows
        .iter()
        .map(|value| {
            value
                .as_str()
                .filter(|route| !route.trim().is_empty())
                .map(str::to_owned)
                .ok_or_else(|| format!("inventory {field_name} contains a non-string/empty route"))
        })
        .collect::<Result<Vec<_>, _>>()?;
    let unique = routes.iter().collect::<BTreeSet<_>>();
    if unique.len() != routes.len() {
        return Err(format!(
            "inventory {field_name} contains duplicates: rows={} unique={}",
            routes.len(),
            unique.len()
        ));
    }
    Ok(routes)
}

fn load_report(path: &Path) -> Result<InventoryReport, String> {
    let data = fs::read(path).map_err(|error| {
        format!(
            "failed to read inventory report {}: {error}",
            path.display()
        )
    })?;
    let report: Value = serde_json::from_slice(&data).map_err(|error| {
        format!(
            "failed to parse inventory report {}: {error}",
            path.display()
        )
    })?;

    if report.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err("inventory report is not canonical (ok != true)".to_owned());
    }

    let public_count = report_u64(&report, "python", "derived_count")?;
    let native_count = report_u64(&report, "rust", "native_count")?;
    let remaining_count = report_u64(&report, "rust", "missing_count")?;
    let state = inventory_state(public_count, native_count, remaining_count)?;

    let missing_rows = report
        .get("missing_routes")
        .and_then(Value::as_array)
        .ok_or_else(|| "inventory report missing array missing_routes".to_owned())?;
    let remaining_routes = string_routes(missing_rows, "missing_routes")?;
    if remaining_routes.len() != remaining_count as usize {
        return Err(format!(
            "inventory route identity count mismatch: got {}, report says {remaining_count}",
            remaining_routes.len()
        ));
    }

    let manifest_rows = report
        .get("python")
        .and_then(|value| value.get("manifest"))
        .and_then(Value::as_array)
        .ok_or_else(|| "inventory report missing array python.manifest".to_owned())?;
    let public_routes = manifest_rows
        .iter()
        .map(|value| {
            value
                .get("route")
                .and_then(Value::as_str)
                .filter(|route| !route.trim().is_empty())
                .map(str::to_owned)
                .ok_or_else(|| {
                    "inventory python.manifest contains a missing/non-string/empty route".to_owned()
                })
        })
        .collect::<Result<Vec<_>, _>>()?;
    if public_routes.len() != EXPECTED_PUBLIC_ROUTE_COUNT as usize {
        return Err(format!(
            "inventory public manifest count mismatch: got {}, expected {EXPECTED_PUBLIC_ROUTE_COUNT}",
            public_routes.len()
        ));
    }
    let unique_public = public_routes.iter().collect::<BTreeSet<_>>();
    if unique_public.len() != public_routes.len() {
        return Err(format!(
            "inventory python.manifest contains duplicate routes: rows={} unique={}",
            public_routes.len(),
            unique_public.len()
        ));
    }

    let extra_native = report
        .get("rust")
        .and_then(|value| value.get("extra_native_routes"))
        .and_then(Value::as_array)
        .ok_or_else(|| "inventory report missing array rust.extra_native_routes".to_owned())?;
    if !extra_native.is_empty() {
        return Err(format!(
            "inventory contains {} extra native routes",
            extra_native.len()
        ));
    }

    match state {
        InventoryState::Frozen if remaining_routes.len() != FROZEN_REMAINING_ROUTE_COUNT => {
            return Err(format!(
                "frozen inventory must contain exactly {FROZEN_REMAINING_ROUTE_COUNT} remaining routes"
            ));
        }
        InventoryState::Promoted if !remaining_routes.is_empty() => {
            return Err("promoted inventory must contain zero remaining routes".to_owned());
        }
        _ => {}
    }

    Ok(InventoryReport {
        state,
        native_count,
        public_routes,
        remaining_routes,
    })
}

fn fail(message: &str) -> ExitCode {
    eprintln!(
        "{}",
        serde_json::to_string_pretty(&json!({
            "ok": false,
            "error": message,
            "claim_boundary": "selector/lower-module ownership in the frozen state and exact public/native set equality in the promoted state; behavioral parity still requires differential execution",
        }))
        .unwrap_or_else(|_| "{\"ok\":false}".to_owned())
    );
    ExitCode::from(2)
}

fn main() -> ExitCode {
    std::env::set_var("SYNTAVRA_BULK_PARITY_PROBE", "1");

    let Some(report_path) = std::env::args_os().nth(1) else {
        return fail("usage: syntavra-remaining71-ownership <inventory-report.json>");
    };
    let report_path = Path::new(&report_path);
    let report = match load_report(report_path) {
        Ok(report) => report,
        Err(error) => return fail(&error),
    };
    let routes = &report.remaining_routes;

    let mut unowned = Vec::<String>::new();
    let mut ownership = BTreeMap::<String, bool>::new();
    let mut selector_paths = BTreeMap::<String, Vec<String>>::new();
    let mut owner_modules = BTreeMap::<String, String>::new();
    let mut owner_candidates = BTreeMap::<String, Vec<&'static str>>::new();
    let mut duplicate_owner_routes = Vec::<String>::new();
    let mut module_unowned_routes = Vec::<String>::new();

    for route in routes {
        let path = selector_path(route);
        let owned = native_product::supports(&path);
        let candidates = remaining71_owner_modules(&path);
        ownership.insert(route.clone(), owned);
        selector_paths.insert(route.clone(), path);
        owner_candidates.insert(route.clone(), candidates.clone());

        if !owned {
            unowned.push(route.clone());
        }
        match candidates.as_slice() {
            [owner] => {
                owner_modules.insert(route.clone(), (*owner).to_owned());
            }
            [] => module_unowned_routes.push(route.clone()),
            _ => duplicate_owner_routes.push(route.clone()),
        }
    }

    let promoted_set_equality = report.state == InventoryState::Promoted
        && report.native_count == EXPECTED_PUBLIC_ROUTE_COUNT
        && report.public_routes.len() == EXPECTED_PUBLIC_ROUTE_COUNT as usize
        && routes.is_empty();
    let ok = match report.state {
        InventoryState::Frozen => {
            unowned.is_empty()
                && module_unowned_routes.is_empty()
                && duplicate_owner_routes.is_empty()
                && ownership.len() == FROZEN_REMAINING_ROUTE_COUNT
                && owner_modules.len() == FROZEN_REMAINING_ROUTE_COUNT
        }
        InventoryState::Promoted => promoted_set_equality,
    };

    println!(
        "{}",
        serde_json::to_string_pretty(&json!({
            "ok": ok,
            "authority": "tools/report_missing_native_public_routes.py canonical report",
            "module_authority": "production Rust modules' supports() predicates for frozen remaining routes",
            "inventory_state": report.state.label(),
            "public_route_count": EXPECTED_PUBLIC_ROUTE_COUNT,
            "native_route_count": report.native_count,
            "report_derived_remaining_count": routes.len(),
            "promoted_public_native_set_equality": promoted_set_equality,
            "owned_count": ownership.values().filter(|value| **value).count(),
            "unowned_count": unowned.len(),
            "unowned_routes": unowned,
            "selector_paths": selector_paths,
            "owner_module_count": owner_modules.len(),
            "owner_modules": owner_modules,
            "owner_candidates": owner_candidates,
            "module_unowned_count": module_unowned_routes.len(),
            "module_unowned_routes": module_unowned_routes,
            "duplicate_owner_count": duplicate_owner_routes.len(),
            "duplicate_owner_routes": duplicate_owner_routes,
            "probe_environment": "SYNTAVRA_BULK_PARITY_PROBE=1",
            "claim_boundary": "selector/lower-module ownership in the frozen state and exact public/native set equality in the promoted state; behavioral parity still requires differential execution",
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
    use super::{inventory_state, remaining71_owner_modules, selector_path, InventoryState};

    #[test]
    fn inventory_state_accepts_only_atomic_endpoints() {
        assert_eq!(inventory_state(245, 174, 71), Ok(InventoryState::Frozen));
        assert_eq!(inventory_state(245, 245, 0), Ok(InventoryState::Promoted));
        for (native, remaining) in [(175, 70), (200, 45), (244, 1), (245, 1), (244, 0)] {
            assert!(
                inventory_state(245, native, remaining).is_err(),
                "unexpected intermediate state accepted: native={native} remaining={remaining}"
            );
        }
        assert!(inventory_state(244, 174, 71).is_err());
        assert!(inventory_state(246, 245, 0).is_err());
    }

    #[test]
    fn selector_collapses_family_actions_after_two_components() {
        assert_eq!(
            selector_path("run language inventory"),
            vec!["run", "language"]
        );
        assert_eq!(
            selector_path("run proxy-service install"),
            vec!["run", "proxy-service"]
        );
        assert_eq!(selector_path("provider proxy"), vec!["provider", "proxy"]);
    }

    #[test]
    fn known_remaining_selectors_have_single_lower_module_owner() {
        for route in [
            "run memory-search",
            "run language inventory",
            "run capability-decide",
            "run sandbox-run",
            "provider capture",
            "provider proxy",
            "run benchmark-gate",
            "run graph-query",
            "run agent-plan",
            "agent run",
            "run rewrite",
            "run context evaluate",
            "run headless-run",
        ] {
            let path = selector_path(route);
            let owners = remaining71_owner_modules(&path);
            assert_eq!(owners.len(), 1, "route={route} owners={owners:?}");
        }
        assert_eq!(
            remaining71_owner_modules(&selector_path("provider capture")),
            vec!["native_product_legacy"]
        );
    }
}
