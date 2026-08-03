#![forbid(unsafe_code)]

use std::collections::{BTreeMap, BTreeSet};
use std::fmt::Write as _;
use std::fs;
use std::path::Path;

use serde_json::{json, Map, Value};

#[derive(Clone, Debug)]
pub(super) struct ContextItem {
    pub item_id: String,
    pub role: String,
    pub text: String,
    pub tokens: i64,
    pub utility: f64,
    pub confidence: f64,
    pub mandatory: bool,
    pub stable: bool,
    pub dependencies: Vec<String>,
}

pub(super) struct PackInput {
    pub budget: i64,
    pub by_id: BTreeMap<String, ContextItem>,
    pub roles: BTreeSet<String>,
}

pub(super) fn option_value(arguments: &[String], name: &str) -> Option<String> {
    arguments
        .windows(2)
        .find(|window| window[0] == name)
        .map(|window| window[1].clone())
}

pub(super) fn repeated_values(arguments: &[String], name: &str) -> Vec<String> {
    arguments
        .windows(2)
        .filter(|window| window[0] == name)
        .map(|window| window[1].clone())
        .collect()
}

pub(super) fn parse_i64(arguments: &[String], name: &str) -> Result<i64, String> {
    option_value(arguments, name)
        .ok_or_else(|| format!("CONTEXT_ARGUMENT_MISSING:{name}"))?
        .parse::<i64>()
        .map_err(|_| format!("CONTEXT_ARGUMENT_INVALID:{name}"))
}

pub(super) fn parse_f64(
    arguments: &[String],
    name: &str,
    default: f64,
) -> Result<f64, String> {
    match option_value(arguments, name) {
        Some(value) => value
            .parse::<f64>()
            .map_err(|_| format!("CONTEXT_ARGUMENT_INVALID:{name}")),
        None => Ok(default),
    }
}

pub(super) fn integer_as_f64(value: i64) -> Result<f64, String> {
    value
        .to_string()
        .parse::<f64>()
        .map_err(|_| "CONTEXT_NUMBER_CONVERSION_FAILED".to_owned())
}

fn required_string(row: &Map<String, Value>, key: &str) -> Result<String, String> {
    row.get(key)
        .and_then(Value::as_str)
        .map(str::to_owned)
        .ok_or_else(|| format!("CONTEXT_ITEM_FIELD_INVALID:{key}"))
}

fn required_i64(row: &Map<String, Value>, key: &str) -> Result<i64, String> {
    row.get(key)
        .and_then(Value::as_i64)
        .ok_or_else(|| format!("CONTEXT_ITEM_FIELD_INVALID:{key}"))
}

fn required_f64(row: &Map<String, Value>, key: &str) -> Result<f64, String> {
    row.get(key)
        .and_then(Value::as_f64)
        .filter(|value| value.is_finite())
        .ok_or_else(|| format!("CONTEXT_ITEM_FIELD_INVALID:{key}"))
}

fn parse_dependencies(row: &Map<String, Value>) -> Result<Vec<String>, String> {
    match row.get("dependencies") {
        None => Ok(Vec::new()),
        Some(Value::Array(values)) => values
            .iter()
            .map(|value| {
                value
                    .as_str()
                    .map(str::to_owned)
                    .ok_or_else(|| "CONTEXT_ITEM_DEPENDENCY_INVALID".to_owned())
            })
            .collect(),
        Some(_) => Err("CONTEXT_ITEM_DEPENDENCIES_INVALID".to_owned()),
    }
}

fn parse_item(value: &Value) -> Result<ContextItem, String> {
    let row = value
        .as_object()
        .ok_or_else(|| "CONTEXT_ITEM_INVALID".to_owned())?;
    Ok(ContextItem {
        item_id: required_string(row, "item_id")?,
        role: required_string(row, "role")?,
        text: required_string(row, "text")?,
        tokens: required_i64(row, "tokens")?,
        utility: required_f64(row, "utility")?,
        confidence: row.get("confidence").and_then(Value::as_f64).unwrap_or(1.0),
        mandatory: row.get("mandatory").and_then(Value::as_bool).unwrap_or(false),
        stable: row.get("stable").and_then(Value::as_bool).unwrap_or(false),
        dependencies: parse_dependencies(row)?,
    })
}

pub(super) fn parse_pack_input(arguments: &[String]) -> Result<PackInput, String> {
    let input = option_value(arguments, "--input")
        .ok_or_else(|| "CONTEXT_ARGUMENT_MISSING:--input".to_owned())?;
    let budget = parse_i64(arguments, "--budget")?;
    if budget <= 0 {
        return Err("budget must be positive".to_owned());
    }
    let payload = fs::read_to_string(Path::new(&input))
        .map_err(|error| format!("CONTEXT_INPUT_READ_FAILED:{error}"))?;
    let document: Value = serde_json::from_str(&payload)
        .map_err(|error| format!("CONTEXT_INPUT_JSON_INVALID:{error}"))?;
    let rows = document
        .as_object()
        .and_then(|value| value.get("items"))
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let items = rows.iter().map(parse_item).collect::<Result<Vec<_>, _>>()?;
    let mut by_id = BTreeMap::<String, ContextItem>::new();
    for item in items {
        if by_id.insert(item.item_id.clone(), item).is_some() {
            return Err("duplicate context item id".to_owned());
        }
    }
    Ok(PackInput {
        budget,
        by_id,
        roles: repeated_values(arguments, "--mandatory-role")
            .into_iter()
            .collect(),
    })
}

pub(super) fn stable_prefix_hash(sections: &[(String, String)]) -> Result<String, String> {
    let mut sorted = sections.to_vec();
    sorted.sort_by(|left, right| left.0.cmp(&right.0));
    let bytes = serde_json::to_vec(&sorted).map_err(|_| "CONTEXT_HASH_JSON_FAILED".to_owned())?;
    let digest = syntavra_core::sha256(&bytes);
    let mut output = String::with_capacity(64);
    for byte in digest {
        write!(&mut output, "{byte:02x}").expect("writing to a String cannot fail");
    }
    Ok(output)
}

pub(super) fn over_budget_result(
    input: &PackInput,
    expanded: &BTreeSet<String>,
    required_cost: i64,
) -> Result<Value, String> {
    let selected_roles = expanded
        .iter()
        .map(|item_id| input.by_id[item_id].role.clone())
        .collect::<BTreeSet<_>>();
    let missing_roles = input
        .roles
        .difference(&selected_roles)
        .cloned()
        .collect::<Vec<_>>();
    let mut reasons = vec![format!(
        "mandatory-over-budget:{required_cost}>{}",
        input.budget
    )];
    if !missing_roles.is_empty() {
        reasons.push(format!("missing-roles:{}", missing_roles.join(",")));
    }
    Ok(json!({
        "budget": input.budget,
        "used": 0,
        "selected_ids": [],
        "dropped_ids": input.by_id.keys().cloned().collect::<Vec<_>>(),
        "stable_prefix_hash": stable_prefix_hash(&[])?,
        "mandatory_satisfied": false,
        "utility": 0.0,
        "sections": [],
        "reasons": reasons,
    }))
}

pub(super) fn render_pack(
    input: &PackInput,
    expanded: &BTreeSet<String>,
    selected: &BTreeSet<String>,
    used: i64,
) -> Result<Value, String> {
    let mut rows = selected
        .iter()
        .map(|item_id| input.by_id[item_id].clone())
        .collect::<Vec<_>>();
    rows.sort_by(|left, right| {
        (!left.stable)
            .cmp(&(!right.stable))
            .then_with(|| left.role.cmp(&right.role))
            .then_with(|| left.item_id.cmp(&right.item_id))
    });
    let sections = rows
        .iter()
        .map(|item| (item.item_id.clone(), item.text.clone()))
        .collect::<Vec<_>>();
    let selected_roles = rows
        .iter()
        .map(|item| item.role.clone())
        .collect::<BTreeSet<_>>();
    Ok(json!({
        "budget": input.budget,
        "used": used,
        "selected_ids": rows.iter().map(|item| item.item_id.clone()).collect::<Vec<_>>(),
        "dropped_ids": input.by_id.keys().filter(|item_id| !selected.contains(*item_id)).cloned().collect::<Vec<_>>(),
        "stable_prefix_hash": stable_prefix_hash(&sections)?,
        "mandatory_satisfied": input.roles.is_subset(&selected_roles) && expanded.is_subset(selected),
        "utility": rows.iter().map(|item| item.utility * item.confidence).sum::<f64>(),
        "sections": sections,
        "reasons": [],
    }))
}
