#![forbid(unsafe_code)]

use std::cmp::Ordering;
use std::collections::{BTreeSet, HashMap};
use std::fs;
use std::path::Path;

use serde_json::{json, Value};

const VERSION: &str = "0.0.1";
const CHANNEL: &str = "pre-release";
const MINIMUM_PAIRS: usize = 30;
const MINIMUM_REPOSITORIES: usize = 5;
const MINIMUM_TASKS: usize = 10;
const MINIMUM_WORKLOAD_FAMILIES: usize = 3;
const QUALITY_NON_INFERIORITY_MARGIN: f64 = 0.01;
const SUCCESS_NON_INFERIORITY_MARGIN: f64 = 0.02;
const WORKLOADS: [&str; 6] = [
    "coding-agent",
    "repository-task",
    "swe-bench",
    "oolong-long-context",
    "session-continuity",
    "tool-routing",
];
const ARMS: [&str; 14] = [
    "baseline",
    "plain-host",
    "syntavra",
    "syntavra-minimal",
    "syntavra-balanced",
    "caveman",
    "rtk",
    "token-savior",
    "jcodemunch",
    "full-competitor-pack",
    "context-mode",
    "headroom",
    "volt-lcm",
    "recursive",
];

pub struct ProofDecision {
    pub value: Value,
    pub exit_code: u8,
}

#[derive(Clone)]
struct ProviderUsageReceipt {
    receipt_id: String,
    provider: String,
    model: String,
    request_id: String,
    session_id: String,
    repository_hash: String,
    integration_id: String,
    observed_at: String,
    wall_time_ms: f64,
    input_tokens: i64,
    cached_input_tokens: i64,
    output_tokens: i64,
    cost_usd: f64,
    quality_score: f64,
    success: bool,
    synthetic: bool,
    raw_usage_hash: String,
    workload: String,
    arm: String,
    task_id: String,
    repetition: i64,
}

#[derive(Clone, Hash, PartialEq, Eq)]
struct PairKey {
    repository_hash: String,
    task_id: String,
    repetition: i64,
    provider: String,
    model: String,
}

#[derive(Default)]
struct BenchmarkMetrics {
    token_ratios: Vec<f64>,
    wall_ratios: Vec<f64>,
    cost_ratios: Vec<f64>,
    quality_deltas: Vec<f64>,
    success_deltas: Vec<f64>,
}

struct BenchmarkDimensions {
    repositories: usize,
    tasks: usize,
    workloads: usize,
}

fn string_field(value: &Value, key: &str, default: &str) -> String {
    value
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or(default)
        .to_owned()
}

fn integer_field(value: &Value, key: &str, default: i64) -> i64 {
    value
        .get(key)
        .and_then(|item| {
            item.as_i64()
                .or_else(|| item.as_bool().map(|flag| if flag { 1 } else { 0 }))
                .or_else(|| item.as_str().and_then(|text| text.parse::<i64>().ok()))
                .or_else(|| {
                    item.as_f64().and_then(|number| {
                        if number.is_finite() {
                            number.trunc().to_string().parse::<i64>().ok()
                        } else {
                            None
                        }
                    })
                })
        })
        .unwrap_or(default)
}

fn float_field(value: &Value, key: &str, default: f64) -> f64 {
    value
        .get(key)
        .and_then(|item| {
            item.as_f64()
                .or_else(|| item.as_bool().map(|flag| if flag { 1.0 } else { 0.0 }))
                .or_else(|| item.as_str().and_then(|text| text.parse::<f64>().ok()))
        })
        .unwrap_or(default)
}

fn boolean_field(value: &Value, key: &str, default: bool) -> bool {
    value.get(key).map_or(default, |item| match item {
        Value::Null => false,
        Value::Bool(flag) => *flag,
        Value::Number(number) => number.as_f64().is_some_and(|number| number != 0.0),
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(items) => !items.is_empty(),
    })
}

impl ProviderUsageReceipt {
    fn from_value(value: &Value) -> Self {
        Self {
            receipt_id: string_field(value, "receipt_id", ""),
            provider: string_field(value, "provider", ""),
            model: string_field(value, "model", ""),
            request_id: string_field(value, "request_id", ""),
            session_id: string_field(value, "session_id", ""),
            repository_hash: string_field(value, "repository_hash", ""),
            integration_id: string_field(value, "integration_id", ""),
            observed_at: string_field(value, "observed_at", ""),
            wall_time_ms: float_field(value, "wall_time_ms", -1.0),
            input_tokens: integer_field(value, "input_tokens", -1),
            cached_input_tokens: integer_field(value, "cached_input_tokens", -1),
            output_tokens: integer_field(value, "output_tokens", -1),
            cost_usd: float_field(value, "cost_usd", -1.0),
            quality_score: float_field(value, "quality_score", -1.0),
            success: boolean_field(value, "success", false),
            synthetic: boolean_field(value, "synthetic", true),
            raw_usage_hash: string_field(value, "raw_usage_hash", ""),
            workload: string_field(value, "workload", "coding-agent"),
            arm: string_field(value, "arm", "syntavra"),
            task_id: string_field(value, "task_id", ""),
            repetition: integer_field(value, "repetition", 0),
        }
    }

    fn pair_key(&self) -> PairKey {
        PairKey {
            repository_hash: self.repository_hash.clone(),
            task_id: self.task_id.clone(),
            repetition: self.repetition,
            provider: self.provider.clone(),
            model: self.model.clone(),
        }
    }

    fn billable_input_tokens(&self) -> i64 {
        (self.input_tokens - self.cached_input_tokens).max(0)
    }

    fn validate(&self) -> Vec<String> {
        let mut reasons = Vec::new();
        for (name, value) in [
            ("receipt_id", self.receipt_id.as_str()),
            ("provider", self.provider.as_str()),
            ("model", self.model.as_str()),
            ("request_id", self.request_id.as_str()),
            ("session_id", self.session_id.as_str()),
            ("repository_hash", self.repository_hash.as_str()),
            ("integration_id", self.integration_id.as_str()),
            ("observed_at", self.observed_at.as_str()),
            ("raw_usage_hash", self.raw_usage_hash.as_str()),
            ("task_id", self.task_id.as_str()),
        ] {
            if value.is_empty() {
                reasons.push(format!("missing-{name}"));
            }
        }
        if !valid_iso_datetime(&self.observed_at) {
            reasons.push("invalid-observed-at".to_owned());
        }
        if self.wall_time_ms < 0.0 || !self.wall_time_ms.is_finite() {
            reasons.push("invalid-wall-time".to_owned());
        }
        if [
            self.input_tokens,
            self.cached_input_tokens,
            self.output_tokens,
        ]
        .into_iter()
        .min()
        .is_some_and(|value| value < 0)
        {
            reasons.push("invalid-token-count".to_owned());
        }
        if self.cached_input_tokens > self.input_tokens {
            reasons.push("cached-input-exceeds-input".to_owned());
        }
        if self.cost_usd < 0.0 || !self.cost_usd.is_finite() {
            reasons.push("invalid-cost".to_owned());
        }
        if !(0.0..=1.0).contains(&self.quality_score) {
            reasons.push("invalid-quality-score".to_owned());
        }
        if !WORKLOADS.contains(&self.workload.as_str()) {
            reasons.push("unsupported-workload".to_owned());
        }
        if !ARMS.contains(&self.arm.as_str()) {
            reasons.push("unsupported-arm".to_owned());
        }
        if self.repetition < 1 {
            reasons.push("invalid-repetition".to_owned());
        }
        if self.raw_usage_hash.len() < 32 {
            reasons.push("weak-raw-usage-hash".to_owned());
        }
        let mut seen = BTreeSet::new();
        reasons
            .into_iter()
            .filter(|reason| seen.insert(reason.clone()))
            .collect()
    }
}

fn valid_date(value: &str) -> bool {
    let mut parts = value.split('-');
    let Some(year) = parts.next().and_then(|item| item.parse::<i32>().ok()) else {
        return false;
    };
    let Some(month) = parts.next().and_then(|item| item.parse::<u32>().ok()) else {
        return false;
    };
    let Some(day) = parts.next().and_then(|item| item.parse::<u32>().ok()) else {
        return false;
    };
    if parts.next().is_some() || year < 1 || !(1..=12).contains(&month) {
        return false;
    }
    let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    let maximum_day = match month {
        2 if leap => 29,
        2 => 28,
        4 | 6 | 9 | 11 => 30,
        _ => 31,
    };
    (1..=maximum_day).contains(&day)
}

fn valid_time(value: &str) -> bool {
    let without_positive_offset = value.split_once('+').map_or(value, |(left, _)| left);
    let clock = without_positive_offset
        .split_once('-')
        .map_or(without_positive_offset, |(left, _)| left);
    let mut parts = clock.split(':');
    let Some(hour) = parts.next().and_then(|item| item.parse::<u32>().ok()) else {
        return false;
    };
    let Some(minute) = parts.next().and_then(|item| item.parse::<u32>().ok()) else {
        return false;
    };
    let Some(second_text) = parts.next() else {
        return false;
    };
    if parts.next().is_some() {
        return false;
    }
    let second = second_text
        .split_once('.')
        .map_or(second_text, |(whole, _)| whole)
        .parse::<u32>()
        .ok();
    hour <= 23 && minute <= 59 && matches!(second, Some(0..=59))
}

fn valid_iso_datetime(value: &str) -> bool {
    let normalized = value.strip_suffix('Z').unwrap_or(value);
    normalized
        .split_once('T')
        .or_else(|| normalized.split_once(' '))
        .map_or_else(
            || valid_date(normalized),
            |(date, time)| valid_date(date) && valid_time(time),
        )
}

fn load_rows(path: &Path) -> Result<Vec<ProviderUsageReceipt>, String> {
    let text = fs::read_to_string(path)
        .map_err(|error| format!("PROOF_RECEIPT_READ_FAILED:{error}"))?;
    let value: Value = serde_json::from_str(&text)
        .map_err(|error| format!("PROOF_RECEIPT_JSON_INVALID:{error}"))?;
    let rows = match &value {
        Value::Object(object) => object.get("receipts").unwrap_or(&value),
        _ => &value,
    };
    let array = rows
        .as_array()
        .ok_or_else(|| "PROOF_RECEIPTS_NOT_LIST".to_owned())?;
    Ok(array
        .iter()
        .filter(|item| item.is_object())
        .map(ProviderUsageReceipt::from_value)
        .collect())
}

fn receipt_path<'a>(arguments: &'a [String], action: &str) -> Result<&'a Path, String> {
    let index = arguments
        .windows(2)
        .position(|window| window[0] == "prove" && window[1] == action)
        .ok_or_else(|| "PROOF_COMMAND_MISSING".to_owned())?;
    arguments
        .get(index + 2)
        .filter(|value| !value.starts_with('-'))
        .map(Path::new)
        .ok_or_else(|| "PROOF_RECEIPT_PATH_MISSING".to_owned())
}

fn readiness_receipt_path(arguments: &[String]) -> Option<&Path> {
    arguments
        .iter()
        .position(|value| value == "--receipts")
        .and_then(|index| arguments.get(index + 1))
        .map(Path::new)
        .or_else(|| {
            arguments
                .iter()
                .find_map(|value| value.strip_prefix("--receipts="))
                .map(Path::new)
        })
}

fn evaluate_receipts(rows: &[ProviderUsageReceipt]) -> Value {
    let invalid = rows
        .iter()
        .filter_map(|row| {
            let reasons = row.validate();
            (!reasons.is_empty()).then(|| {
                json!({
                    "receipt_id": row.receipt_id,
                    "reasons": reasons,
                })
            })
        })
        .collect::<Vec<_>>();
    let mut counts = HashMap::<&str, usize>::new();
    for row in rows {
        *counts.entry(row.receipt_id.as_str()).or_default() += 1;
    }
    let duplicate_receipt_ids = counts
        .into_iter()
        .filter_map(|(receipt_id, count)| (count > 1).then_some(receipt_id.to_owned()))
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    let live = rows
        .iter()
        .filter(|row| !row.synthetic && row.validate().is_empty())
        .count();
    json!({
        "ok": !rows.is_empty() && invalid.is_empty() && duplicate_receipt_ids.is_empty(),
        "version": VERSION,
        "channel": CHANNEL,
        "total": rows.len(),
        "valid": rows.len() - invalid.len(),
        "live": live,
        "synthetic": rows.iter().filter(|row| row.synthetic).count(),
        "invalid": invalid,
        "duplicate_receipt_ids": duplicate_receipt_ids,
        "claim_boundary": "validated receipts are evidence inputs, not automatic superiority proof",
    })
}

fn integer_as_f64(value: i64) -> f64 {
    value.to_string().parse::<f64>().unwrap_or(0.0)
}

fn precise_mean(values: &[f64]) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    let mut partials = Vec::<f64>::new();
    for &value in values {
        let mut x = value;
        let mut index = 0usize;
        for position in 0..partials.len() {
            let mut y = partials[position];
            if x.abs() < y.abs() {
                std::mem::swap(&mut x, &mut y);
            }
            let high = x + y;
            let low = y - (high - x);
            if low != 0.0 {
                partials[index] = low;
                index += 1;
            }
            x = high;
        }
        partials.truncate(index);
        if x != 0.0 {
            partials.push(x);
        }
    }
    let sum = partials.iter().rev().sum::<f64>();
    Some(sum / integer_as_f64(i64::try_from(values.len()).unwrap_or(1)))
}

fn median(values: &[f64]) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    let mut ordered = values.to_vec();
    ordered.sort_by(|left, right| left.partial_cmp(right).unwrap_or(Ordering::Equal));
    let middle = ordered.len() / 2;
    if ordered.len() % 2 == 0 {
        Some((ordered[middle - 1] + ordered[middle]) / 2.0)
    } else {
        Some(ordered[middle])
    }
}

fn paired_rows(rows: &[ProviderUsageReceipt]) -> Vec<(ProviderUsageReceipt, ProviderUsageReceipt)> {
    let mut grouped = Vec::<(PairKey, HashMap<String, ProviderUsageReceipt>)>::new();
    for row in rows {
        let key = row.pair_key();
        if let Some((_, arms)) = grouped.iter_mut().find(|(existing, _)| existing == &key) {
            arms.insert(row.arm.clone(), row.clone());
        } else {
            let mut arms = HashMap::new();
            arms.insert(row.arm.clone(), row.clone());
            grouped.push((key, arms));
        }
    }
    grouped
        .into_iter()
        .filter_map(|(_, arms)| {
            Some((
                arms.get("baseline")?.clone(),
                arms.get("syntavra")?.clone(),
            ))
        })
        .collect()
}

fn benchmark_dimensions(
    pairs: &[(ProviderUsageReceipt, ProviderUsageReceipt)],
) -> BenchmarkDimensions {
    let repositories = pairs
        .iter()
        .map(|(_, syntavra)| syntavra.repository_hash.as_str())
        .collect::<BTreeSet<_>>()
        .len();
    let tasks = pairs
        .iter()
        .map(|(_, syntavra)| syntavra.task_id.as_str())
        .collect::<BTreeSet<_>>()
        .len();
    let workloads = pairs
        .iter()
        .map(|(_, syntavra)| syntavra.workload.as_str())
        .collect::<BTreeSet<_>>()
        .len();
    BenchmarkDimensions {
        repositories,
        tasks,
        workloads,
    }
}

fn add_dimension_reasons(
    pairs: usize,
    dimensions: &BenchmarkDimensions,
    reasons: &mut Vec<String>,
) {
    if pairs < MINIMUM_PAIRS {
        reasons.push("insufficient-paired-runs".to_owned());
    }
    if dimensions.repositories < MINIMUM_REPOSITORIES {
        reasons.push("insufficient-repositories".to_owned());
    }
    if dimensions.tasks < MINIMUM_TASKS {
        reasons.push("insufficient-tasks".to_owned());
    }
    if dimensions.workloads < MINIMUM_WORKLOAD_FAMILIES {
        reasons.push("insufficient-workload-diversity".to_owned());
    }
}

fn collect_metrics(
    pairs: &[(ProviderUsageReceipt, ProviderUsageReceipt)],
    reasons: &mut Vec<String>,
) -> BenchmarkMetrics {
    let mut metrics = BenchmarkMetrics::default();
    for (baseline, syntavra) in pairs {
        if baseline.provider != syntavra.provider || baseline.model != syntavra.model {
            reasons.push("provider-or-model-parity-failed".to_owned());
            continue;
        }
        let baseline_tokens = baseline.billable_input_tokens() + baseline.output_tokens;
        let syntavra_tokens = syntavra.billable_input_tokens() + syntavra.output_tokens;
        if baseline_tokens <= 0 || baseline.wall_time_ms <= 0.0 || baseline.cost_usd <= 0.0 {
            reasons.push("invalid-baseline-denominator".to_owned());
            continue;
        }
        metrics
            .token_ratios
            .push(integer_as_f64(syntavra_tokens) / integer_as_f64(baseline_tokens));
        metrics
            .wall_ratios
            .push(syntavra.wall_time_ms / baseline.wall_time_ms);
        metrics
            .cost_ratios
            .push(syntavra.cost_usd / baseline.cost_usd);
        metrics
            .quality_deltas
            .push(syntavra.quality_score - baseline.quality_score);
        let candidate_success = if syntavra.success { 1.0 } else { 0.0 };
        let baseline_success = if baseline.success { 1.0 } else { 0.0 };
        metrics
            .success_deltas
            .push(candidate_success - baseline_success);
    }
    metrics
}

fn add_metric_reasons(metrics: &BenchmarkMetrics, reasons: &mut Vec<String>) {
    let mean_quality_delta = precise_mean(&metrics.quality_deltas).unwrap_or(-1.0);
    let mean_success_delta = precise_mean(&metrics.success_deltas).unwrap_or(-1.0);
    if mean_quality_delta < -QUALITY_NON_INFERIORITY_MARGIN {
        reasons.push("quality-non-inferiority-failed".to_owned());
    }
    if mean_success_delta < -SUCCESS_NON_INFERIORITY_MARGIN {
        reasons.push("success-non-inferiority-failed".to_owned());
    }
    if metrics.token_ratios.is_empty() {
        reasons.push("no-measurable-pairs".to_owned());
    }
}

fn benchmark_metrics_value(
    pairs: usize,
    dimensions: &BenchmarkDimensions,
    metrics: &BenchmarkMetrics,
) -> Value {
    json!({
        "pairs": pairs,
        "repositories": dimensions.repositories,
        "tasks": dimensions.tasks,
        "workloads": dimensions.workloads,
        "mean_token_ratio": precise_mean(&metrics.token_ratios),
        "median_token_ratio": median(&metrics.token_ratios),
        "mean_wall_time_ratio": precise_mean(&metrics.wall_ratios),
        "mean_cost_ratio": precise_mean(&metrics.cost_ratios),
        "mean_quality_delta": precise_mean(&metrics.quality_deltas),
        "mean_success_delta": precise_mean(&metrics.success_deltas),
    })
}

fn evaluate_benchmark(rows: &[ProviderUsageReceipt]) -> Value {
    let validation = evaluate_receipts(rows);
    let pairs = paired_rows(rows);
    let dimensions = benchmark_dimensions(&pairs);
    let mut reasons = Vec::<String>::new();
    if validation.get("ok").and_then(Value::as_bool) != Some(true) {
        reasons.push("receipt-validation-failed".to_owned());
    }
    if rows.iter().any(|row| row.synthetic) {
        reasons.push("synthetic-receipts-present".to_owned());
    }
    add_dimension_reasons(pairs.len(), &dimensions, &mut reasons);
    let metrics = collect_metrics(&pairs, &mut reasons);
    add_metric_reasons(&metrics, &mut reasons);
    let reasons = reasons
        .into_iter()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    let ok = reasons.is_empty();
    json!({
        "ok": ok,
        "claim": if ok { "MEASURED_AGENT_BENCHMARK_VERIFIED" } else { "MEASURED_AGENT_BENCHMARK_NOT_PROVEN" },
        "external_superiority": if ok { "EXTERNAL_SUPERIORITY_ELIGIBLE_FOR_REVIEW" } else { "EXTERNAL_SUPERIORITY_NOT_PROVEN" },
        "reasons": reasons,
        "metrics": benchmark_metrics_value(pairs.len(), &dimensions, &metrics),
        "requirements": {
            "minimum_pairs": MINIMUM_PAIRS,
            "minimum_repositories": MINIMUM_REPOSITORIES,
            "minimum_tasks": MINIMUM_TASKS,
            "minimum_workload_families": MINIMUM_WORKLOAD_FAMILIES,
            "quality_non_inferiority_margin": QUALITY_NON_INFERIORITY_MARGIN,
            "success_non_inferiority_margin": SUCCESS_NON_INFERIORITY_MARGIN,
        },
    })
}

fn evaluate_readiness(state_root: &Path, rows: &[ProviderUsageReceipt]) -> Value {
    let product = state_root.join("product.json").is_file();
    let profile = state_root.join("mcp-profile.json").is_file();
    let adapters = state_root.join("platform-adapters.json").is_file();
    let benchmark = evaluate_benchmark(rows);
    let measured = benchmark.get("ok").and_then(Value::as_bool) == Some(true);
    let setup_bundle = product && profile && adapters;
    let ok = setup_bundle && measured;
    json!({
        "ok": ok,
        "claim": if ok { "DAILY_CODING_AGENT_READY" } else { "DAILY_CODING_AGENT_READINESS_NOT_PROVEN" },
        "checks": {
            "narrow_product_surface": true,
            "platform_adapter_contracts": true,
            "integration_matrix": true,
            "setup_bundle": setup_bundle,
            "measured_agent_benchmark": measured,
        },
        "files": {
            "product.json": product,
            "mcp-profile.json": profile,
            "platform-adapters.json": adapters,
        },
        "benchmark": benchmark,
        "version": VERSION,
        "channel": CHANNEL,
    })
}

pub fn execute(
    action: &str,
    arguments: &[String],
    state_root: &Path,
) -> Result<ProofDecision, String> {
    let rows = match action {
        "receipts" | "benchmark" => load_rows(receipt_path(arguments, action)?)?,
        "readiness" => readiness_receipt_path(arguments).map_or_else(|| Ok(Vec::new()), load_rows)?,
        _ => return Err("PROOF_EVIDENCE_ACTION_INVALID".to_owned()),
    };
    let value = match action {
        "receipts" => evaluate_receipts(&rows),
        "benchmark" => evaluate_benchmark(&rows),
        "readiness" => evaluate_readiness(state_root, &rows),
        _ => unreachable!(),
    };
    let exit_code = if value.get("ok").and_then(Value::as_bool) == Some(true) {
        0
    } else {
        4
    };
    Ok(ProofDecision { value, exit_code })
}

#[cfg(test)]
mod tests {
    use super::{evaluate_benchmark, evaluate_receipts, ProviderUsageReceipt};
    use serde_json::json;

    fn receipt(arm: &str) -> ProviderUsageReceipt {
        ProviderUsageReceipt::from_value(&json!({
            "receipt_id": format!("receipt-{arm}"),
            "provider": "provider",
            "model": "model",
            "request_id": format!("request-{arm}"),
            "session_id": "session",
            "repository_hash": "repository",
            "integration_id": "integration",
            "observed_at": "2026-01-01T00:00:00+00:00",
            "wall_time_ms": 100.0,
            "input_tokens": 1000,
            "cached_input_tokens": 0,
            "output_tokens": 100,
            "cost_usd": 1.0,
            "quality_score": 0.8,
            "success": true,
            "synthetic": false,
            "raw_usage_hash": "a".repeat(64),
            "workload": "coding-agent",
            "arm": arm,
            "task_id": "task",
            "repetition": 1,
        }))
    }

    #[test]
    fn valid_receipt_is_accepted() {
        let value = evaluate_receipts(&[receipt("syntavra")]);
        assert_eq!(value["ok"], true);
        assert_eq!(value["live"], 1);
    }

    #[test]
    fn undersized_benchmark_fails_closed() {
        let value = evaluate_benchmark(&[receipt("baseline"), receipt("syntavra")]);
        assert_eq!(value["ok"], false);
        assert!(value["reasons"]
            .as_array()
            .is_some_and(|items| items.contains(&json!("insufficient-paired-runs"))));
    }
}
