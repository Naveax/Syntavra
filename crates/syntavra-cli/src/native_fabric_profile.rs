#![forbid(unsafe_code)]

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use regex::Regex;
use serde_json::{json, Value};
use syntavra_core::sha256_hex;

const CATALOG: &str = include_str!("../../../contracts/engine/mcp-native-catalog-v1.json");

const MINIMAL_TOOLS: &[&str] = &[
    "syntavra.status",
    "syntavra.inspect.map",
    "syntavra.output.capture",
    "syntavra.output.search",
    "syntavra.output.reveal",
    "syntavra.session.semantic_context",
    "syntavra.fabric.route",
    "syntavra.fabric.doctor",
];

const BALANCED_TOOLS: &[&str] = &[
    "syntavra.status",
    "syntavra.inspect.map",
    "syntavra.output.capture",
    "syntavra.output.search",
    "syntavra.output.reveal",
    "syntavra.session.semantic_context",
    "syntavra.fabric.route",
    "syntavra.fabric.doctor",
    "syntavra.host.detect",
    "syntavra.inspect.impact",
    "syntavra.inspect.source",
    "syntavra.inspect.range",
    "syntavra.context.evaluate",
    "syntavra.output.verify",
    "syntavra.output.stats",
    "syntavra.session.open",
    "syntavra.session.append",
    "syntavra.session.search",
    "syntavra.session.context",
    "syntavra.session.compact",
    "syntavra.session.verify",
    "syntavra.sandbox.plan",
    "syntavra.sandbox.execute",
    "syntavra.process.submit",
    "syntavra.process.completions",
    "syntavra.fabric.profile",
    "syntavra.fabric.insights",
    "syntavra.provider.capabilities",
    "syntavra.provider.prepare",
    "syntavra.provider.capture",
    "syntavra.provider.replay",
    "syntavra.provider.verify",
    "syntavra.provider.stats",
    "syntavra.data.route",
    "syntavra.ecosystem.capabilities",
    "syntavra.context.pack",
];

const INTENTS: &[(&str, &[&str])] = &[
    (
        r"(?i)\b(test|pytest|jest|vitest|cargo test|go test|ci|build|lint)\b",
        &[
            "syntavra.process.submit",
            "syntavra.process.completions",
            "syntavra.output.search",
        ],
    ),
    (
        r"(?i)\b(symbol|function|class|call graph|impact|dependency|codebase|repository)\b",
        &["syntavra.inspect.map", "syntavra.inspect.impact"],
    ),
    (
        r"(?i)\b(log|trace|output|stdout|stderr|search output|reveal)\b",
        &[
            "syntavra.output.capture",
            "syntavra.output.search",
            "syntavra.output.reveal",
            "syntavra.output.verify",
        ],
    ),
    (
        r"(?i)\b(memory|remember|session|decision|previous|history|compact)\b",
        &[
            "syntavra.session.open",
            "syntavra.session.append",
            "syntavra.session.search",
            "syntavra.session.semantic_context",
        ],
    ),
    (
        r"(?i)\b(untrusted|sandbox|network|download|curl|wget|security)\b",
        &[
            "syntavra.sandbox.plan",
            "syntavra.sandbox.execute",
            "syntavra.output.verify",
        ],
    ),
    (
        r"(?i)\b(token|context|cache|profile|manifest|compression)\b",
        &[
            "syntavra.compress",
            "syntavra.expand",
            "syntavra.context.evaluate",
            "syntavra.fabric.profile",
            "syntavra.fabric.cache_align",
        ],
    ),
];

pub fn supports(command: &[String]) -> bool {
    matches!(command, [fabric, action] if fabric == "fabric" && action == "profile")
}

fn option_value(arguments: &[String], name: &str) -> Result<Option<String>, String> {
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
        } else if let Some(value) = arguments[index].strip_prefix(&prefix) {
            found = Some(value.to_owned());
        }
        index += 1;
    }
    Ok(found)
}

fn catalog_names() -> Result<Vec<String>, String> {
    let document = serde_json::from_str::<Value>(CATALOG)
        .map_err(|error| format!("FABRIC_PROFILE_CATALOG_INVALID:{error}"))?;
    let rows = document["profiles"]["audit"]["compiled_tools"]
        .as_array()
        .ok_or_else(|| "FABRIC_PROFILE_CATALOG_TOOLS_MISSING".to_owned())?;
    let mut seen = BTreeSet::new();
    let mut names = Vec::new();
    for row in rows {
        let name = row["name"]
            .as_str()
            .ok_or_else(|| "FABRIC_PROFILE_CATALOG_TOOL_NAME_INVALID".to_owned())?;
        if seen.insert(name.to_owned()) {
            names.push(name.to_owned());
        }
    }
    Ok(names)
}

fn intent_matches(task: &str) -> Result<Vec<Vec<String>>, String> {
    let mut matched = Vec::new();
    for (pattern, tools) in INTENTS {
        let matcher = Regex::new(pattern)
            .map_err(|error| format!("FABRIC_PROFILE_INTENT_REGEX_INVALID:{error}"))?;
        if matcher.is_match(task) {
            matched.push(tools.iter().map(|value| (*value).to_owned()).collect());
        }
    }
    Ok(matched)
}

fn normalized_profile(requested: &str, task: &str) -> Result<String, String> {
    match requested {
        "tiny" => Ok("minimal".to_owned()),
        "optimized" => Ok("balanced".to_owned()),
        "full" => Ok("audit".to_owned()),
        "auto" => {
            let word_count = task.split_whitespace().count();
            let matched = intent_matches(task)?.len();
            Ok(if word_count <= 12 && matched <= 1 {
                "minimal"
            } else {
                "balanced"
            }
            .to_owned())
        }
        _ => Err(format!(
            "invalid choice: {requested} (choose from 'auto', 'tiny', 'optimized', 'full')"
        )),
    }
}

fn selected_tools(
    profile: &str,
    task: &str,
    available: &[String],
) -> Result<Vec<String>, String> {
    if profile == "audit" {
        return Ok(available.to_vec());
    }
    let mut wanted = BTreeSet::<String>::new();
    let base = if profile == "minimal" {
        MINIMAL_TOOLS
    } else {
        BALANCED_TOOLS
    };
    wanted.extend(base.iter().map(|value| (*value).to_owned()));
    for tools in intent_matches(task)? {
        wanted.extend(tools);
    }
    let mut selected = available
        .iter()
        .filter(|name| wanted.contains(name.as_str()))
        .cloned()
        .collect::<Vec<_>>();
    let mut seen = selected.iter().cloned().collect::<BTreeSet<_>>();
    for name in available {
        if name.starts_with("syntavra.fabric.") && seen.insert(name.clone()) {
            selected.push(name.clone());
        }
    }
    Ok(selected)
}

fn estimated_tokens(names: &[String]) -> usize {
    names
        .iter()
        .map(|name| 8usize.max((name.len() + 48 + 3) / 4))
        .sum()
}

fn profile_hash(profile: &str, tools: &[String]) -> Result<String, String> {
    let profile_json = serde_json::to_string(profile)
        .map_err(|error| format!("FABRIC_PROFILE_HASH_SERIALIZE_FAILED:{error}"))?;
    let tools_json = serde_json::to_string(tools)
        .map_err(|error| format!("FABRIC_PROFILE_HASH_SERIALIZE_FAILED:{error}"))?;
    Ok(sha256_hex(
        format!("{{\"profile\":{profile_json},\"tools\":{tools_json}}}").as_bytes(),
    ))
}

pub fn execute(
    arguments: &[String],
    state_root: &Path,
) -> Result<Value, String> {
    let task = option_value(arguments, "--task")?
        .ok_or_else(|| "the following arguments are required: --task".to_owned())?;
    let requested = option_value(arguments, "--profile")?.unwrap_or_else(|| "auto".to_owned());
    let profile = normalized_profile(&requested, &task)?;
    let _database = super::native_fabric_doctor::open_database(
        &state_root.join("competitive-fabric.sqlite3"),
    )?;
    let available = catalog_names()?;
    let selected = selected_tools(&profile, &task, &available)?;
    let estimated = estimated_tokens(&selected);
    let (purpose, budget) = match profile.as_str() {
        "minimal" => ("Minimal hot-loop surface for small coding tasks.", 800usize),
        "balanced" => (
            "Default Pareto surface: structural navigation, exact output economy, memory, and safety.",
            2_000usize,
        ),
        "audit" => ("Expose every installed Syntavra tool for auditing.", 16_000usize),
        _ => return Err("FABRIC_PROFILE_INTERNAL_PROFILE_INVALID".to_owned()),
    };
    let host = option_value(arguments, "--host")?.unwrap_or_else(|| "codex".to_owned());
    let host_contract = super::native_expansion::doctor_host_contract(&host);
    let result = json!({
        "profile": profile,
        "purpose": purpose,
        "selected_tools": selected,
        "selected_count": selected.len(),
        "available_count": available.len(),
        "omitted_count": available.len().saturating_sub(selected.len()),
        "estimated_manifest_tokens": estimated,
        "manifest_budget": budget,
        "within_budget": estimated <= budget || profile == "audit",
        "host": host,
        "host_mode": host_contract["negotiation"]["mode"],
        "profile_hash": profile_hash(&profile, &selected)?,
    });
    option_value(arguments, "--output")?.map_or_else(
        || Ok(result.clone()),
        |path| super::native_fabric_doctor::write_json_output(&PathBuf::from(path), &result),
    )
}

#[cfg(test)]
mod tests {
    use super::{normalized_profile, supports};

    #[test]
    fn routes_profile_only() {
        assert!(supports(&["fabric".to_owned(), "profile".to_owned()]));
        assert!(!supports(&["fabric".to_owned(), "route".to_owned()]));
    }

    #[test]
    fn auto_profile_uses_task_pressure() {
        assert_eq!(normalized_profile("auto", "fix typo").unwrap(), "minimal");
        assert_eq!(
            normalized_profile("auto", "test repository logs").unwrap(),
            "balanced"
        );
    }
}
