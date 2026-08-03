#![forbid(unsafe_code)]

use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::Path;

use serde_json::{json, Map, Value};

const THRESHOLDS: [(f64, &[&str]); 6] = [
    (0.50, &["evict_duplicates", "drop_raw_success_logs"]),
    (0.60, &["externalize_evidence"]),
    (0.70, &["write_phase_capsule"]),
    (0.78, &["update_context_dag"]),
    (0.84, &["prepare_controlled_handoff"]),
    (0.88, &["mandatory_session_split"]),
];

#[derive(Clone, Debug)]
struct ContextItem {
    item_id: String,
    role: String,
    text: String,
    tokens: i64,
    utility: f64,
    confidence: f64,
    mandatory: bool,
    stable: bool,
    dependencies: Vec<String>,
}

fn option_value(arguments: &[String], name: &str) -> Option<String> {
    arguments
        .windows(2)
        .find(|window| window[0] == name)
        .map(|window| window[1].clone())
}

fn repeated_values(arguments: &[String], name: &str) -> Vec<String> {
    arguments
        .windows(2)
        .filter(|window| window[0] == name)
        .map(|window| window[1].clone())
        .collect()
}

fn parse_i64(arguments: &[String], name: &str) -> Result<i64, String> {
    option_value(arguments, name)
        .ok_or_else(|| format!("CONTEXT_ARGUMENT_MISSING:{name}"))?
        .parse::<i64>()
        .map_err(|_| format!("CONTEXT_ARGUMENT_INVALID:{name}"))
}

fn parse_f64(arguments: &[String], name: &str, default: f64) -> Result<f64, String> {
    match option_value(arguments, name) {
        Some(value) => value
            .parse::<f64>()
            .map_err(|_| format!("CONTEXT_ARGUMENT_INVALID:{name}")),
        None => Ok(default),
    }
}

fn evaluate(arguments: &[String]) -> Result<Value, String> {
    let used = parse_i64(arguments, "--used")?;
    let window = parse_i64(arguments, "--window")?;
    if used < 0 || window <= 0 {
        return Err("used must be nonnegative and window positive".to_owned());
    }
    let churn = parse_f64(arguments, "--churn", 0.0)?;
    let evidence_pressure = parse_f64(arguments, "--evidence-pressure", 0.0)?;
    let utilization = (used as f64) / (window as f64);
    let pressure =
        (utilization + 0.12 * churn.max(0.0) + 0.08 * evidence_pressure.max(0.0)).clamp(0.0, 1.5);
    let mut level = 0_u64;
    let mut actions = Vec::<Value>::new();
    for (threshold, names) in THRESHOLDS {
        if pressure >= threshold {
            level += 1;
            actions.extend(names.iter().map(|name| Value::String((*name).to_owned())));
        }
    }
    Ok(json!({
        "utilization": utilization,
        "level": level,
        "actions": actions,
        "mandatory_split": pressure >= THRESHOLDS[THRESHOLDS.len() - 1].0,
        "pressure_score": pressure,
    }))
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

fn parse_item(value: &Value) -> Result<ContextItem, String> {
    let row = value
        .as_object()
        .ok_or_else(|| "CONTEXT_ITEM_INVALID".to_owned())?;
    let dependencies = match row.get("dependencies") {
        None => Vec::new(),
        Some(Value::Array(values)) => values
            .iter()
            .map(|value| {
                value
                    .as_str()
                    .map(str::to_owned)
                    .ok_or_else(|| "CONTEXT_ITEM_DEPENDENCY_INVALID".to_owned())
            })
            .collect::<Result<Vec<_>, _>>()?,
        Some(_) => return Err("CONTEXT_ITEM_DEPENDENCIES_INVALID".to_owned()),
    };
    Ok(ContextItem {
        item_id: required_string(row, "item_id")?,
        role: required_string(row, "role")?,
        text: required_string(row, "text")?,
        tokens: required_i64(row, "tokens")?,
        utility: required_f64(row, "utility")?,
        confidence: row.get("confidence").and_then(Value::as_f64).unwrap_or(1.0),
        mandatory: row
            .get("mandatory")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        stable: row.get("stable").and_then(Value::as_bool).unwrap_or(false),
        dependencies,
    })
}

fn dependency_closure(
    item_id: &str,
    by_id: &BTreeMap<String, ContextItem>,
    selected: &BTreeSet<String>,
) -> Result<BTreeSet<String>, String> {
    let mut required = BTreeSet::<String>::new();
    let mut stack = vec![item_id.to_owned()];
    while let Some(current) = stack.pop() {
        if selected.contains(&current) || required.contains(&current) {
            continue;
        }
        let item = by_id
            .get(&current)
            .ok_or_else(|| format!("missing context dependency: {current}"))?;
        required.insert(current);
        stack.extend(item.dependencies.iter().cloned());
    }
    Ok(required)
}

fn nonnegative_tokens(item: &ContextItem) -> i64 {
    item.tokens.max(0)
}

fn marginal(
    item_id: &str,
    by_id: &BTreeMap<String, ContextItem>,
    selected: &BTreeSet<String>,
) -> Result<(f64, i64, BTreeSet<String>), String> {
    let closure = dependency_closure(item_id, by_id, selected)?;
    let cost = closure
        .iter()
        .map(|value| nonnegative_tokens(&by_id[value]))
        .sum();
    let utility = closure
        .iter()
        .map(|value| {
            let item = &by_id[value];
            item.utility.max(0.0) * item.confidence.clamp(0.0, 1.0)
        })
        .sum();
    Ok((utility, cost, closure))
}

fn float_order(left: f64, right: f64) -> Ordering {
    left.partial_cmp(&right).unwrap_or(Ordering::Equal)
}

fn stable_prefix_hash(sections: &[(String, String)]) -> Result<String, String> {
    let mut sorted = sections.to_vec();
    sorted.sort_by(|left, right| left.0.cmp(&right.0));
    let bytes = serde_json::to_vec(&sorted).map_err(|_| "CONTEXT_HASH_JSON_FAILED".to_owned())?;
    let digest = syntavra_core::sha256(&bytes);
    let mut output = String::with_capacity(64);
    for byte in digest {
        use std::fmt::Write as _;
        write!(&mut output, "{byte:02x}").expect("writing to a String cannot fail");
    }
    Ok(output)
}

fn over_budget_result(
    budget: i64,
    by_id: &BTreeMap<String, ContextItem>,
    roles: &BTreeSet<String>,
    expanded: &BTreeSet<String>,
    required_cost: i64,
) -> Result<Value, String> {
    let selected_roles = expanded
        .iter()
        .map(|item_id| by_id[item_id].role.clone())
        .collect::<BTreeSet<_>>();
    let missing_roles = roles
        .difference(&selected_roles)
        .cloned()
        .collect::<Vec<_>>();
    let mut reasons = vec![format!("mandatory-over-budget:{required_cost}>{budget}")];
    if !missing_roles.is_empty() {
        reasons.push(format!("missing-roles:{}", missing_roles.join(",")));
    }
    Ok(json!({
        "budget": budget,
        "used": 0,
        "selected_ids": [],
        "dropped_ids": by_id.keys().cloned().collect::<Vec<_>>(),
        "stable_prefix_hash": stable_prefix_hash(&[])?,
        "mandatory_satisfied": false,
        "utility": 0.0,
        "sections": [],
        "reasons": reasons,
    }))
}

fn pack(arguments: &[String]) -> Result<Value, String> {
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
        .get("items")
        .and_then(Value::as_array)
        .ok_or_else(|| "CONTEXT_ITEMS_MISSING".to_owned())?;
    let items = rows.iter().map(parse_item).collect::<Result<Vec<_>, _>>()?;
    let mut by_id = BTreeMap::<String, ContextItem>::new();
    for item in items {
        if by_id.insert(item.item_id.clone(), item).is_some() {
            return Err("duplicate context item id".to_owned());
        }
    }
    let roles = repeated_values(arguments, "--mandatory-role")
        .into_iter()
        .collect::<BTreeSet<_>>();
    let required_ids = by_id
        .values()
        .filter(|item| item.mandatory || roles.contains(&item.role))
        .map(|item| item.item_id.clone())
        .collect::<BTreeSet<_>>();
    let mut expanded = BTreeSet::<String>::new();
    for item_id in required_ids {
        let closure = dependency_closure(&item_id, &by_id, &expanded)?;
        expanded.extend(closure);
    }
    let required_cost: i64 = expanded
        .iter()
        .map(|item_id| nonnegative_tokens(&by_id[item_id]))
        .sum();
    if required_cost > budget {
        return over_budget_result(budget, &by_id, &roles, &expanded, required_cost);
    }

    let mut selected = expanded.clone();
    let mut used = required_cost;
    let mut candidates = by_id
        .keys()
        .filter(|item_id| !selected.contains(*item_id))
        .cloned()
        .collect::<Vec<_>>();
    while !candidates.is_empty() {
        let mut scored = Vec::new();
        for item_id in &candidates {
            let (utility, cost, closure) = marginal(item_id, &by_id, &selected)?;
            let density = utility / (cost.max(1) as f64);
            scored.push((density, utility, cost, item_id.clone(), closure));
        }
        scored.sort_by(|left, right| {
            float_order(right.0, left.0)
                .then_with(|| float_order(right.1, left.1))
                .then_with(|| left.2.cmp(&right.2))
                .then_with(|| left.3.cmp(&right.3))
        });
        let mut added = false;
        for (_, _, cost, _, closure) in scored {
            if cost <= budget - used {
                selected.extend(closure);
                used += cost;
                candidates.retain(|item_id| !selected.contains(item_id));
                added = true;
                break;
            }
        }
        if !added {
            break;
        }
    }

    let mut optional_selected = selected
        .difference(&expanded)
        .map(|item_id| by_id[item_id].clone())
        .collect::<Vec<_>>();
    optional_selected.sort_by(|left, right| {
        let left_density = left.utility * left.confidence / (left.tokens.max(1) as f64);
        let right_density = right.utility * right.confidence / (right.tokens.max(1) as f64);
        float_order(left_density, right_density).then_with(|| left.item_id.cmp(&right.item_id))
    });
    let mut dropped = by_id
        .values()
        .filter(|item| !selected.contains(&item.item_id))
        .cloned()
        .collect::<Vec<_>>();
    dropped.sort_by(|left, right| {
        float_order(
            right.utility * right.confidence,
            left.utility * left.confidence,
        )
        .then_with(|| left.item_id.cmp(&right.item_id))
    });
    for incoming in dropped {
        let (utility, cost, closure) = marginal(&incoming.item_id, &by_id, &selected)?;
        for outgoing in &optional_selected {
            if !selected.contains(&outgoing.item_id) {
                continue;
            }
            if utility <= outgoing.utility * outgoing.confidence {
                continue;
            }
            let depended_on = selected.iter().any(|item_id| {
                item_id != &outgoing.item_id
                    && by_id[item_id].dependencies.contains(&outgoing.item_id)
            });
            if cost <= budget - used + outgoing.tokens && !depended_on {
                selected.remove(&outgoing.item_id);
                selected.extend(closure);
                used = used - outgoing.tokens + cost;
                break;
            }
        }
    }

    let mut selected_rows = selected
        .iter()
        .map(|item_id| by_id[item_id].clone())
        .collect::<Vec<_>>();
    selected_rows.sort_by(|left, right| {
        (!left.stable)
            .cmp(&(!right.stable))
            .then_with(|| left.role.cmp(&right.role))
            .then_with(|| left.item_id.cmp(&right.item_id))
    });
    let sections = selected_rows
        .iter()
        .map(|item| (item.item_id.clone(), item.text.clone()))
        .collect::<Vec<_>>();
    let selected_roles = selected_rows
        .iter()
        .map(|item| item.role.clone())
        .collect::<BTreeSet<_>>();
    let mandatory_satisfied = roles.is_subset(&selected_roles) && expanded.is_subset(&selected);
    let utility: f64 = selected_rows
        .iter()
        .map(|item| item.utility * item.confidence)
        .sum();
    Ok(json!({
        "budget": budget,
        "used": used,
        "selected_ids": selected_rows.iter().map(|item| item.item_id.clone()).collect::<Vec<_>>(),
        "dropped_ids": by_id.keys().filter(|item_id| !selected.contains(*item_id)).cloned().collect::<Vec<_>>(),
        "stable_prefix_hash": stable_prefix_hash(&sections)?,
        "mandatory_satisfied": mandatory_satisfied,
        "utility": utility,
        "sections": sections,
        "reasons": [],
    }))
}

pub fn supports(command: &[String]) -> bool {
    matches!(command, [group] if group == "context")
        || matches!(command, [group, action]
            if group == "context" && matches!(action.as_str(), "evaluate" | "pack"))
}

pub fn execute(command: &[String], arguments: &[String]) -> Result<Value, String> {
    match command {
        [group] if group == "context" => evaluate(arguments),
        [group, action] if group == "context" && action == "evaluate" => evaluate(arguments),
        [group, action] if group == "context" && action == "pack" => pack(arguments),
        _ => Err("RUST_CONTEXT_COMMAND_UNSUPPORTED".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::evaluate;

    #[test]
    fn threshold_actions_match_reference_order() {
        let args = [
            "--used".to_owned(),
            "900".to_owned(),
            "--window".to_owned(),
            "1000".to_owned(),
        ];
        let value = evaluate(&args).expect("context evaluation");
        assert_eq!(value["level"], 6);
        assert_eq!(value["mandatory_split"], true);
        assert_eq!(value["actions"][0], "evict_duplicates");
        assert_eq!(value["actions"][6], "mandatory_session_split");
    }
}
