#![forbid(unsafe_code)]

use serde_json::{json, Value};

#[path = "native_context_model.rs"]
mod model;
#[path = "native_context_selection.rs"]
mod selection;

use model::{
    integer_as_f64, over_budget_result, parse_f64, parse_i64, parse_pack_input, render_pack,
};
use selection::{greedy_selection, replacement_selection, required_selection};

const THRESHOLDS: [(f64, &[&str]); 6] = [
    (0.50, &["evict_duplicates", "drop_raw_success_logs"]),
    (0.60, &["externalize_evidence"]),
    (0.70, &["write_phase_capsule"]),
    (0.78, &["update_context_dag"]),
    (0.84, &["prepare_controlled_handoff"]),
    (0.88, &["mandatory_session_split"]),
];

fn evaluate(arguments: &[String]) -> Result<Value, String> {
    let used = parse_i64(arguments, "--used")?;
    let window = parse_i64(arguments, "--window")?;
    if used < 0 || window <= 0 {
        return Err("used must be nonnegative and window positive".to_owned());
    }
    let churn = parse_f64(arguments, "--churn", 0.0)?;
    let evidence_pressure = parse_f64(arguments, "--evidence-pressure", 0.0)?;
    let utilization = integer_as_f64(used)? / integer_as_f64(window)?;
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

fn pack(arguments: &[String]) -> Result<Value, String> {
    let input = parse_pack_input(arguments)?;
    let (expanded, required_cost) = required_selection(&input)?;
    if required_cost > input.budget {
        return over_budget_result(&input, &expanded, required_cost);
    }
    let mut selected = expanded.clone();
    let mut used = required_cost;
    greedy_selection(&input, &mut selected, &mut used)?;
    replacement_selection(&input, &expanded, &mut selected, &mut used)?;
    render_pack(&input, &expanded, &selected, used)
}

pub fn supports(command: &[String]) -> bool {
    matches!(command, [group] if group == "context")
        || matches!(command, [group, action]
            if group == "context" && matches!(action.as_str(), "evaluate" | "pack"))
}

pub fn execute(command: &[String], arguments: &[String]) -> Result<Value, String> {
    match command {
        [group] if group == "context" => {
            if arguments
                .windows(2)
                .any(|window| window[0] == "context" && window[1] == "pack")
            {
                pack(arguments)
            } else {
                evaluate(arguments)
            }
        }
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
        assert!(value["mandatory_split"].as_bool().unwrap_or(false));
        assert_eq!(value["actions"][0], "evict_duplicates");
        assert_eq!(value["actions"][6], "mandatory_session_split");
    }
}
