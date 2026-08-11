#![forbid(unsafe_code)]

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::Path;
use std::process::ExitCode;

use serde_json::{json, Value};

#[path = "../native_product.rs"]
mod native_product;

const EXPECTED_PUBLIC_ROUTE_COUNT: u64 = 245;
const EXPECTED_NATIVE_ROUTE_COUNT: u64 = 174;
const EXPECTED_REMAINING_ROUTE_COUNT: usize = 71;

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

fn report_u64(report: &Value, section: &str, field: &str) -> Result<u64, String> {
    report
        .get(section)
        .and_then(|value| value.get(field))
        .and_then(Value::as_u64)
        .ok_or_else(|| format!("inventory report missing integer {section}.{field}"))
}

fn load_report_routes(path: &Path) -> Result<Vec<String>, String> {
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
    if public_count != EXPECTED_PUBLIC_ROUTE_COUNT
        || native_count != EXPECTED_NATIVE_ROUTE_COUNT
        || remaining_count != EXPECTED_REMAINING_ROUTE_COUNT as u64
    {
        return Err(format!(
            "inventory count contract mismatch: public={public_count} native={native_count} remaining={remaining_count}; expected {EXPECTED_PUBLIC_ROUTE_COUNT}/{EXPECTED_NATIVE_ROUTE_COUNT}/{EXPECTED_REMAINING_ROUTE_COUNT}"
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
                .ok_or_else(|| {
                    "inventory missing_routes contains a non-string/empty route".to_owned()
                })
        })
        .collect::<Result<Vec<_>, _>>()?;

    if routes.len() != EXPECTED_REMAINING_ROUTE_COUNT {
        return Err(format!(
            "inventory route identity count mismatch: got {}, expected {EXPECTED_REMAINING_ROUTE_COUNT}",
            routes.len()
        ));
    }
    let unique = routes.iter().collect::<BTreeSet<_>>();
    if unique.len() != routes.len() {
        return Err(format!(
            "inventory missing_routes contains duplicates: rows={} unique={}",
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
            "claim_boundary": "selector ownership only; behavioral parity still requires differential execution",
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
    let routes = match load_report_routes(report_path) {
        Ok(routes) => routes,
        Err(error) => return fail(&error),
    };

    let mut unowned = Vec::<String>::new();
    let mut ownership = BTreeMap::<String, bool>::new();
    let mut selector_paths = BTreeMap::<String, Vec<String>>::new();
    for route in &routes {
        let path = selector_path(route);
        let owned = native_product::supports(&path);
        ownership.insert(route.clone(), owned);
        selector_paths.insert(route.clone(), path);
        if !owned {
            unowned.push(route.clone());
        }
    }

    let ok = unowned.is_empty() && ownership.len() == EXPECTED_REMAINING_ROUTE_COUNT;
    println!(
        "{}",
        serde_json::to_string_pretty(&json!({
            "ok": ok,
            "authority": "tools/report_missing_native_public_routes.py missing_routes",
            "public_route_count": EXPECTED_PUBLIC_ROUTE_COUNT,
            "frozen_native_route_count": EXPECTED_NATIVE_ROUTE_COUNT,
            "report_derived_remaining_count": routes.len(),
            "owned_count": ownership.values().filter(|value| **value).count(),
            "unowned_count": unowned.len(),
            "unowned_routes": unowned,
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
    use super::selector_path;

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
}
