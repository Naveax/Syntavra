#![forbid(unsafe_code)]

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::Path;

use serde_json::{json, Value};

const REQUIRED_TASKS: usize = 150;
const REQUIRED_REPETITIONS: usize = 30;
const MINIMUM_SUCCESS: f64 = 0.985;
const MAXIMUM_TOKEN_RATIO: f64 = 0.18;
const MAXIMUM_WALL_RATIO: f64 = 0.15;
const INFINITY_SENTINEL: &str = "__SYNTAVRA_JSON_INFINITY__";

pub struct GateDecision {
    pub value: Value,
    pub rendered: String,
    pub exit_code: u8,
}

struct ReceiptGroups<'a> {
    task_count: usize,
    repetition_count: usize,
    pair_count: usize,
    candidate: Vec<&'a Value>,
    baseline: Vec<&'a Value>,
}

struct GateMetrics {
    success: f64,
    token_ratio: f64,
    wall_ratio: f64,
    security_regressions: i64,
    candidate_wall_p95: f64,
}

fn receipts_path(arguments: &[String]) -> Result<&Path, String> {
    arguments
        .windows(3)
        .find(|window| {
            matches!(window[0].as_str(), "signalbench" | "signalbench2") && window[1] == "gate"
        })
        .map(|window| Path::new(&window[2]))
        .ok_or_else(|| "SIGNALBENCH_RECEIPTS_MISSING".to_owned())
}

fn python_truthy(value: Option<&Value>) -> bool {
    match value {
        None | Some(Value::Null) => false,
        Some(Value::Bool(value)) => *value,
        Some(Value::Number(value)) => value.as_f64().is_some_and(|number| number != 0.0),
        Some(Value::String(value)) => !value.is_empty(),
        Some(Value::Array(value)) => !value.is_empty(),
        Some(Value::Object(value)) => !value.is_empty(),
    }
}

fn number(row: &Value, key: &str) -> f64 {
    row.get(key).and_then(Value::as_f64).unwrap_or(0.0)
}

fn integer(row: &Value, key: &str) -> i64 {
    row.get(key).and_then(Value::as_i64).unwrap_or(0)
}

fn text(row: &Value, key: &str) -> String {
    match row.get(key) {
        None | Some(Value::Null) => "None".to_owned(),
        Some(Value::String(value)) => value.clone(),
        Some(Value::Bool(value)) => {
            if *value {
                "True".to_owned()
            } else {
                "False".to_owned()
            }
        }
        Some(Value::Number(value)) => value.to_string(),
        Some(value) => value.to_string(),
    }
}

fn mean(values: &[f64]) -> f64 {
    let denominator = u32::try_from(values.len().max(1)).unwrap_or(u32::MAX);
    values.iter().sum::<f64>() / f64::from(denominator)
}

fn mean_field(rows: &[&Value], key: &str) -> f64 {
    mean(
        &rows
            .iter()
            .map(|row| number(row, key))
            .collect::<Vec<_>>(),
    )
}

fn percentile_95(values: &[f64]) -> f64 {
    let mut values = values.to_vec();
    values.sort_by(f64::total_cmp);
    if values.is_empty() {
        return 0.0;
    }
    let rank = values.len().saturating_mul(95).saturating_add(99) / 100;
    values[rank.saturating_sub(1).min(values.len() - 1)]
}

fn ratio(numerator: f64, denominator: f64) -> f64 {
    if denominator > 0.0 {
        numerator / denominator
    } else {
        f64::INFINITY
    }
}

fn ratio_value(value: f64) -> Value {
    if value.is_finite() {
        json!(value)
    } else {
        Value::String(INFINITY_SENTINEL.to_owned())
    }
}

fn render(value: &Value) -> Result<String, String> {
    serde_json::to_string_pretty(value)
        .map(|rendered| rendered.replace(&format!("\"{INFINITY_SENTINEL}\""), "Infinity"))
        .map_err(|error| format!("SIGNALBENCH_RESULT_RENDER_FAILED:{error}"))
}

fn group_receipts<'a>(rows: &'a [Value], reasons: &mut Vec<&'static str>) -> ReceiptGroups<'a> {
    if rows.iter().any(|row| {
        python_truthy(row.get("synthetic"))
            || row.get("source_kind").and_then(Value::as_str) != Some("live-external-arm")
    }) {
        reasons.push("non-live-receipt");
    }

    let task_ids = rows
        .iter()
        .map(|row| text(row, "task_id"))
        .collect::<BTreeSet<_>>();
    let repetitions = rows
        .iter()
        .map(|row| integer(row, "repetition"))
        .collect::<BTreeSet<_>>();
    if task_ids.len() < REQUIRED_TASKS {
        reasons.push("insufficient-tasks");
    }
    if repetitions.len() < REQUIRED_REPETITIONS {
        reasons.push("insufficient-repetitions");
    }

    let mut by_arm: BTreeMap<String, Vec<&Value>> = BTreeMap::new();
    for row in rows {
        by_arm.entry(text(row, "arm_id")).or_default().push(row);
    }
    if !by_arm.contains_key("syntavra") || !by_arm.contains_key("plain-baseline") {
        reasons.push("missing-required-arm");
    }
    let pair_count = rows
        .iter()
        .map(|row| text(row, "pair_key"))
        .collect::<BTreeSet<_>>()
        .len();

    ReceiptGroups {
        task_count: task_ids.len(),
        repetition_count: repetitions.len(),
        pair_count,
        candidate: by_arm.remove("syntavra").unwrap_or_default(),
        baseline: by_arm.remove("plain-baseline").unwrap_or_default(),
    }
}

fn calculate_metrics(groups: &ReceiptGroups<'_>, reasons: &mut Vec<&'static str>) -> GateMetrics {
    let success = mean(
        &groups
            .candidate
            .iter()
            .map(|row| f64::from(u8::from(python_truthy(row.get("success")))))
            .collect::<Vec<_>>(),
    );
    let token_ratio = ratio(
        mean_field(&groups.candidate, "active_tokens"),
        mean_field(&groups.baseline, "active_tokens"),
    );
    let wall_ratio = ratio(
        mean_field(&groups.candidate, "wall_seconds"),
        mean_field(&groups.baseline, "wall_seconds"),
    );
    let security_regressions = groups
        .candidate
        .iter()
        .map(|row| integer(row, "security_regressions"))
        .sum::<i64>();

    if success < MINIMUM_SUCCESS {
        reasons.push("success-floor-missed");
    }
    if token_ratio > MAXIMUM_TOKEN_RATIO {
        reasons.push("token-target-missed");
    }
    if wall_ratio > MAXIMUM_WALL_RATIO {
        reasons.push("wall-target-missed");
    }
    if security_regressions > 0 {
        reasons.push("security-regression");
    }
    if groups.pair_count < REQUIRED_TASKS * REQUIRED_REPETITIONS {
        reasons.push("paired-coverage-incomplete");
    }

    GateMetrics {
        success,
        token_ratio,
        wall_ratio,
        security_regressions,
        candidate_wall_p95: percentile_95(
            &groups
                .candidate
                .iter()
                .map(|row| number(row, "wall_seconds"))
                .collect::<Vec<_>>(),
        ),
    }
}

fn evaluate(rows: &[Value]) -> Result<GateDecision, String> {
    let mut reasons = Vec::new();
    let groups = group_receipts(rows, &mut reasons);
    let metrics = calculate_metrics(&groups, &mut reasons);
    let ok = reasons.is_empty();
    let value = json!({
        "ok": ok,
        "claim": if ok { "SUPERIORITY_PROVEN" } else { "EXTERNAL_SUPERIORITY_NOT_PROVEN" },
        "reasons": reasons,
        "metrics": {
            "tasks": groups.task_count,
            "repetitions": groups.repetition_count,
            "success": metrics.success,
            "token_ratio": ratio_value(metrics.token_ratio),
            "wall_ratio": ratio_value(metrics.wall_ratio),
            "security_regressions": metrics.security_regressions,
            "candidate_wall_p95": metrics.candidate_wall_p95,
        },
    });
    Ok(GateDecision {
        rendered: render(&value)?,
        value,
        exit_code: if ok { 0 } else { 4 },
    })
}

pub fn execute(arguments: &[String]) -> Result<GateDecision, String> {
    let path = receipts_path(arguments)?;
    let raw = fs::read_to_string(path)
        .map_err(|error| format!("SIGNALBENCH_RECEIPTS_READ_FAILED:{error}"))?;
    let value: Value = serde_json::from_str(&raw)
        .map_err(|error| format!("SIGNALBENCH_RECEIPTS_JSON_INVALID:{error}"))?;
    let rows = value
        .as_array()
        .ok_or_else(|| "SIGNALBENCH_RECEIPTS_NOT_ARRAY".to_owned())?;
    evaluate(rows)
}

#[cfg(test)]
mod tests {
    use super::evaluate;
    use serde_json::json;

    #[test]
    fn incomplete_fixture_fails_closed() {
        let rows = vec![
            json!({
                "task_id": "one",
                "repetition": 1,
                "arm_id": "syntavra",
                "success": true,
                "active_tokens": 10,
                "wall_seconds": 1,
                "security_regressions": 0,
                "pair_key": "one",
                "synthetic": false,
                "source_kind": "live-external-arm",
            }),
            json!({
                "task_id": "one",
                "repetition": 1,
                "arm_id": "plain-baseline",
                "success": true,
                "active_tokens": 100,
                "wall_seconds": 10,
                "security_regressions": 0,
                "pair_key": "one",
                "synthetic": false,
                "source_kind": "live-external-arm",
            }),
        ];
        let decision = evaluate(&rows).expect("gate");
        assert_eq!(decision.value["ok"], false);
        assert_eq!(decision.exit_code, 4);
        assert_eq!(decision.value["claim"], "EXTERNAL_SUPERIORITY_NOT_PROVEN");
    }

    #[test]
    fn zero_baseline_renders_python_infinity_literal() {
        let decision = evaluate(&[]).expect("empty gate");
        assert!(decision.rendered.contains("Infinity"));
        assert_eq!(decision.exit_code, 4);
    }
}
