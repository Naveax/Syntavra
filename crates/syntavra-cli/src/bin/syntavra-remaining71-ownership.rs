#![forbid(unsafe_code)]

use std::collections::BTreeMap;
use std::process::ExitCode;

use serde_json::json;

#[path = "../native_product.rs"]
mod native_product;

const CANONICAL_REMAINING_71: &[&str] = &[
    "agent replay",
    "agent run",
    "benchmark compare",
    "prove integrations",
    "provider capture",
    "provider proxy",
    "run agent-execute",
    "run agent-plan",
    "run cache-plan",
    "run capability-decide",
    "run capability-issue",
    "run capability-verify",
    "run code-intel",
    "run console",
    "run context-compile",
    "run dashboard",
    "run gateway-plan",
    "run graph-impact",
    "run graph-index",
    "run graph-query",
    "run headless-cancel",
    "run headless-events",
    "run headless-export",
    "run headless-import",
    "run headless-resume",
    "run headless-run",
    "run headless-status",
    "run headless-submit",
    "run language detect",
    "run language doctor",
    "run language import-index",
    "run language index",
    "run language inventory",
    "run language query",
    "run language remove-index",
    "run memory-add",
    "run memory-append",
    "run memory-backfill",
    "run memory-checkpoint",
    "run memory-compact",
    "run memory-export",
    "run memory-extract",
    "run memory-fork",
    "run memory-intelligence-status",
    "run memory-merge",
    "run memory-open",
    "run memory-restore",
    "run memory-retrieve",
    "run memory-search",
    "run memory-verify",
    "run notify",
    "run output-capture",
    "run provider-pool",
    "run proxy-service install",
    "run proxy-service plan",
    "run proxy-service uninstall",
    "run proxy-service verify",
    "run reliability-run",
    "run rewrite",
    "run sandbox-run",
    "run sandbox-status",
    "run semantic-import",
    "run semantic-services",
    "run transcript-mine",
    "run update-install",
    "run update-rollback",
    "run watch",
    "run worker",
    "sandbox backends",
    "sandbox execute",
    "sandbox plan",
];

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

fn main() -> ExitCode {
    std::env::set_var("SYNTAVRA_BULK_PARITY_PROBE", "1");

    let mut missing = Vec::<String>::new();
    let mut ownership = BTreeMap::<String, bool>::new();
    let mut selector_paths = BTreeMap::<String, Vec<String>>::new();
    for route in CANONICAL_REMAINING_71 {
        let path = selector_path(route);
        let owned = native_product::supports(&path);
        ownership.insert((*route).to_owned(), owned);
        selector_paths.insert((*route).to_owned(), path);
        if !owned {
            missing.push((*route).to_owned());
        }
    }

    let ok = missing.is_empty() && ownership.len() == 71;
    println!(
        "{}",
        serde_json::to_string_pretty(&json!({
            "ok": ok,
            "canonical_remaining_count": CANONICAL_REMAINING_71.len(),
            "owned_count": ownership.values().filter(|value| **value).count(),
            "missing_count": missing.len(),
            "missing_routes": missing,
            "selector_paths": selector_paths,
            "probe_environment": "SYNTAVRA_BULK_PARITY_PROBE=1",
            "claim_boundary": "selector ownership only; behavioral parity still requires differential execution",
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
    use super::{selector_path, CANONICAL_REMAINING_71};

    #[test]
    fn canonical_inventory_has_exactly_71_routes() {
        assert_eq!(CANONICAL_REMAINING_71.len(), 71);
    }

    #[test]
    fn selector_collapses_family_actions_after_two_components() {
        assert_eq!(selector_path("run language inventory"), vec!["run", "language"]);
        assert_eq!(selector_path("run proxy-service install"), vec!["run", "proxy-service"]);
        assert_eq!(selector_path("provider proxy"), vec!["provider", "proxy"]);
    }
}
