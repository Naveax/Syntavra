#![forbid(unsafe_code)]

use std::path::Path;

use serde_json::{json, Value};

const PHASE: &str = "R24";
const SCHEMA_VERSION: u64 = 12;
const CERTIFIED_ROUTES: &[&str] = &[
    "config.explain",
    "config.resolve",
    "config.show",
    "config.validate",
    "migration.plan",
    "pipeline.describe",
    "plugins.list",
    "receipt.inspect",
    "scheduler.list",
    "scheduler.stats",
    "state.broker-live-snapshot",
    "state.broker-snapshot",
    "state.inspect",
    "state.layout",
    "status",
    "telemetry.metrics",
    "version",
];
const UNSUPPORTED_ERROR_ROUTES: &[&str] = &[
    "config.resolve",
    "receipt.inspect",
    "state.broker-live-snapshot",
    "state.broker-snapshot",
    "state.inspect",
    "state.layout",
    "status",
    "version",
];

pub struct Decision {
    pub value: Value,
    pub exit_code: u8,
}

pub fn supports(command: &[String]) -> bool {
    matches!(command, [engine, route, _] if engine == "engine" && route == "route")
}

fn normalize_route(value: &str) -> String {
    value.trim().to_ascii_lowercase()
}

fn unsupported(route: &str) -> Decision {
    Decision {
        value: json!({
            "ok": false,
            "error": {
                "code": "ENGINE_ROUTE_UNSUPPORTED_R14",
                "message": "The selected R14 route is not in the read-only capability whitelist",
                "details": {
                    "phase": PHASE,
                    "schema_version": SCHEMA_VERSION,
                    "command": if route.is_empty() { "<missing>" } else { route },
                    "supported": UNSUPPORTED_ERROR_ROUTES,
                    "fallback_policy": "none",
                    "fallback_attempted": false,
                },
            },
        }),
        exit_code: 4,
    }
}

pub fn execute(
    command: &[String],
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Decision, String> {
    let raw_route = command
        .get(2)
        .map(String::as_str)
        .ok_or_else(|| "ENGINE_ROUTE_COMMAND_MISSING".to_owned())?;
    let route = normalize_route(raw_route);
    if !CERTIFIED_ROUTES.contains(&route.as_str()) {
        return Ok(unsupported(&route));
    }

    let normalized = vec!["engine".to_owned(), "route".to_owned(), route];
    let value = if super::native_engine_routes::supports(&normalized) {
        super::native_engine_routes::execute(
            &normalized,
            arguments,
            project_root,
            state_root,
        )?
    } else if super::native_engine_state_routes::supports(&normalized) {
        super::native_engine_state_routes::execute(&normalized, arguments, project_root)?
    } else {
        return Err("ENGINE_ROUTE_CERTIFIED_DISPATCH_MISSING".to_owned());
    };
    Ok(Decision {
        value,
        exit_code: 0,
    })
}

#[cfg(test)]
mod tests {
    use super::{
        normalize_route, supports, CERTIFIED_ROUTES, UNSUPPORTED_ERROR_ROUTES,
    };

    #[test]
    fn owns_generic_engine_route_surface() {
        assert!(supports(&[
            "engine".to_owned(),
            "route".to_owned(),
            "unknown.route".to_owned(),
        ]));
        assert!(!supports(&["engine".to_owned(), "status".to_owned()]));
    }

    #[test]
    fn normalizes_ascii_route_names_like_the_public_python_vocabulary() {
        assert_eq!(normalize_route("  VERSION  "), "version");
        assert_eq!(normalize_route("Config.Show"), "config.show");
    }

    #[test]
    fn route_catalogs_are_sorted_and_unique() {
        for routes in [CERTIFIED_ROUTES, UNSUPPORTED_ERROR_ROUTES] {
            let mut expected = routes.to_vec();
            expected.sort_unstable();
            expected.dedup();
            assert_eq!(expected, routes);
        }
    }
}
