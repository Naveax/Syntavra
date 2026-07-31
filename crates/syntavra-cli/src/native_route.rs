#![forbid(unsafe_code)]

use serde_json::{json, Value};
use syntavra_core::sha256_hex;

const READ_PREFIXES: &[&str] = &["read", "search", "grep", "find", "list", "fetch", "inspect"];
const WRITE_PREFIXES: &[&str] = &[
    "write", "edit", "patch", "update", "create", "delete", "move", "rename",
];
const EXEC_PREFIXES: &[&str] = &[
    "run", "exec", "shell", "terminal", "bash", "powershell", "cmd",
];
const NETWORK_PREFIXES: &[&str] = &["http", "web", "browser", "download", "upload", "request"];
const PROFILES: &[&str] = &["minimal", "balanced", "audit"];

#[derive(Debug, Clone, PartialEq)]
pub struct RouteDecision {
    pub value: Value,
    pub exit_code: u8,
}

fn category(tool: &str) -> &'static str {
    let normalized = tool
        .trim()
        .to_lowercase()
        .replace(['-', '_'], ".");
    let leaf = normalized.rsplit('.').next().unwrap_or(normalized.as_str());
    if READ_PREFIXES.iter().any(|prefix| leaf.starts_with(prefix)) {
        "read"
    } else if WRITE_PREFIXES
        .iter()
        .any(|prefix| leaf.starts_with(prefix))
    {
        "write"
    } else if EXEC_PREFIXES
        .iter()
        .any(|prefix| leaf.starts_with(prefix))
    {
        "execute"
    } else if NETWORK_PREFIXES
        .iter()
        .any(|prefix| leaf.starts_with(prefix))
    {
        "network"
    } else {
        "unknown"
    }
}

fn string_flag(arguments: &[String], flag: &str, default: &str) -> Result<String, String> {
    let mut result = None;
    let mut index = 0usize;
    while index < arguments.len() {
        let item = &arguments[index];
        let raw = if item == flag {
            index += 1;
            Some(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_MISSING"))?
                    .as_str(),
            )
        } else {
            item.strip_prefix(flag)
                .and_then(|suffix| suffix.strip_prefix('='))
        };
        if let Some(value) = raw {
            if result.is_some() {
                return Err(format!("{flag}_DUPLICATE"));
            }
            result = Some(value.to_owned());
        }
        index += 1;
    }
    Ok(result.unwrap_or_else(|| default.to_owned()))
}

fn has_flag(arguments: &[String], flag: &str) -> bool {
    arguments.iter().any(|value| value == flag)
}

fn decide(
    tool: &str,
    profile: &str,
    sandboxed: bool,
    exact_evidence: bool,
    explicit_user_authorization: bool,
) -> Result<RouteDecision, String> {
    if !PROFILES.contains(&profile) {
        return Err("ROUTE_PROFILE_INVALID".to_owned());
    }
    let category = category(tool);
    let mut requirements = vec!["routing-receipt".to_owned()];
    let mut allowed = true;
    let mut reason = "policy-allowed";
    if category == "unknown" {
        allowed = false;
        reason = "unknown-tool-fail-closed";
    }
    if matches!(category, "write" | "execute" | "network") {
        requirements.extend([
            "exact-evidence".to_owned(),
            "explicit-user-authorization".to_owned(),
        ]);
        if !exact_evidence {
            allowed = false;
            reason = "exact-evidence-required";
        } else if !explicit_user_authorization {
            allowed = false;
            reason = "explicit-user-authorization-required";
        }
    }
    if category == "execute" {
        requirements.push("sandbox".to_owned());
        if !sandboxed {
            allowed = false;
            reason = "sandbox-required";
        }
    }
    let body = json!({
        "tool": tool,
        "category": category,
        "profile": profile,
        "allowed": allowed,
        "reason": reason,
        "requirements": requirements,
    });
    let canonical =
        serde_json::to_vec(&body).map_err(|_| "ROUTE_DECISION_RENDER_FAILED".to_owned())?;
    let mut output = body
        .as_object()
        .cloned()
        .ok_or_else(|| "ROUTE_DECISION_RENDER_FAILED".to_owned())?;
    output.insert(
        "receipt_hash".to_owned(),
        Value::String(sha256_hex(&canonical)),
    );
    Ok(RouteDecision {
        value: Value::Object(output),
        exit_code: if allowed { 0 } else { 5 },
    })
}

pub fn execute(arguments: &[String]) -> Result<RouteDecision, String> {
    let action = arguments
        .iter()
        .position(|value| value == "route")
        .ok_or_else(|| "ROUTE_ACTION_MISSING".to_owned())?;
    let tool = arguments
        .get(action + 1)
        .ok_or_else(|| "ROUTE_TOOL_MISSING".to_owned())?;
    let profile = string_flag(arguments, "--profile", "minimal")?;
    decide(
        tool,
        &profile,
        has_flag(arguments, "--sandboxed"),
        !has_flag(arguments, "--no-exact-evidence"),
        has_flag(arguments, "--user-authorized"),
    )
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{category, decide};

    #[test]
    fn categories_match_python_prefix_rules() {
        assert_eq!(category("syntavra.output.search"), "read");
        assert_eq!(category("repo_file-update"), "write");
        assert_eq!(category("host.shell"), "execute");
        assert_eq!(category("browser.request"), "network");
        assert_eq!(category("opaque.tool"), "unknown");
    }

    #[test]
    fn read_route_is_allowed_with_exact_receipt() {
        let decision = decide("syntavra.output.search", "minimal", false, true, false)
            .expect("decision");
        assert_eq!(decision.exit_code, 0);
        assert_eq!(decision.value["allowed"], true);
        assert_eq!(decision.value["category"], "read");
        assert_eq!(decision.value["requirements"], json!(["routing-receipt"]));
        assert_eq!(
            decision.value["receipt_hash"].as_str().map(str::len),
            Some(64)
        );
    }

    #[test]
    fn execute_route_requires_authorization_and_sandbox() {
        let unauthorized = decide("host.shell", "audit", false, true, false)
            .expect("unauthorized decision");
        assert_eq!(unauthorized.exit_code, 5);
        assert_eq!(
            unauthorized.value["reason"],
            "sandbox-required"
        );
        assert_eq!(
            unauthorized.value["requirements"],
            json!([
                "routing-receipt",
                "exact-evidence",
                "explicit-user-authorization",
                "sandbox"
            ])
        );

        let allowed = decide("host.shell", "audit", true, true, true)
            .expect("allowed decision");
        assert_eq!(allowed.exit_code, 0);
        assert_eq!(allowed.value["allowed"], true);
    }
}
