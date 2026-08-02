#![forbid(unsafe_code)]

use std::collections::{HashMap, HashSet};
use std::fs;

use serde_json::{json, Value};

const VERSION: &str = "0.0.1";
const CHANNEL: &str = "pre-release";
const MINIMUM_PAIRS: usize = 30;
const QUALITY_NON_INFERIORITY_MARGIN: f64 = 0.01;
const SUCCESS_NON_INFERIORITY_MARGIN: f64 = 0.02;
const SUITES: [&str; 5] = [
    "swe-bench",
    "oolong",
    "longbench-v2",
    "infinitebench",
    "recursive-long-context",
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

pub struct ExternalSuiteDecision {
    pub value: Value,
    pub exit_code: u8,
}

#[derive(Clone, Copy)]
enum Flag {
    Enabled,
    Disabled,
}

impl Flag {
    const fn from_bool(value: bool) -> Self {
        if value {
            Self::Enabled
        } else {
            Self::Disabled
        }
    }

    const fn as_number(self) -> f64 {
        if matches!(self, Self::Enabled) {
            1.0
        } else {
            0.0
        }
    }

    const fn is_enabled(self) -> bool {
        matches!(self, Self::Enabled)
    }
}

#[derive(Clone)]
struct BenchmarkRow {
    receipt_id: String,
    suite_id: String,
    task_id: String,
    arm: String,
    repetition: i64,
    dataset_version: String,
    harness_commit: String,
    verifier_commit: String,
    environment_image_digest: String,
    repository_commit: String,
    provider: String,
    model: String,
    model_config_hash: String,
    result_artifact_hash: String,
    raw_provider_receipt_hash: String,
    quality_score: f64,
    success: Flag,
    input_tokens: i64,
    cached_input_tokens: i64,
    output_tokens: i64,
    cost_usd: f64,
    wall_time_ms: f64,
    recursive_calls: i64,
    synthetic: Flag,
}

#[derive(Clone, Hash, PartialEq, Eq)]
struct PairKey {
    suite_id: String,
    task_id: String,
    repetition: i64,
    dataset_version: String,
    harness_commit: String,
    verifier_commit: String,
    provider: String,
    model: String,
}

#[derive(Default)]
struct Metrics {
    quality_deltas: Vec<f64>,
    success_deltas: Vec<f64>,
    token_ratios: Vec<f64>,
    cost_ratios: Vec<f64>,
    wall_ratios: Vec<f64>,
}

fn string_field(value: &Value, key: &str) -> String {
    value
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_owned()
}

fn integer_field(value: &Value, key: &str, default: i64) -> i64 {
    value.get(key).and_then(Value::as_i64).unwrap_or(default)
}

fn float_field(value: &Value, key: &str, default: f64) -> f64 {
    value.get(key).and_then(Value::as_f64).unwrap_or(default)
}

fn boolean_field(value: &Value, key: &str, default: bool) -> Flag {
    Flag::from_bool(value.get(key).and_then(Value::as_bool).unwrap_or(default))
}

fn integer_as_f64(value: i64) -> f64 {
    value.to_string().parse::<f64>().unwrap_or(0.0)
}

impl BenchmarkRow {
    fn from_value(value: &Value) -> Self {
        Self {
            receipt_id: string_field(value, "receipt_id"),
            suite_id: string_field(value, "suite_id"),
            task_id: string_field(value, "task_id"),
            arm: string_field(value, "arm"),
            repetition: integer_field(value, "repetition", 0),
            dataset_version: string_field(value, "dataset_version"),
            harness_commit: string_field(value, "harness_commit"),
            verifier_commit: string_field(value, "verifier_commit"),
            environment_image_digest: string_field(value, "environment_image_digest"),
            repository_commit: string_field(value, "repository_commit"),
            provider: string_field(value, "provider"),
            model: string_field(value, "model"),
            model_config_hash: string_field(value, "model_config_hash"),
            result_artifact_hash: string_field(value, "result_artifact_hash"),
            raw_provider_receipt_hash: string_field(value, "raw_provider_receipt_hash"),
            quality_score: float_field(value, "quality_score", -1.0),
            success: boolean_field(value, "success", false),
            input_tokens: integer_field(value, "input_tokens", -1),
            cached_input_tokens: integer_field(value, "cached_input_tokens", -1),
            output_tokens: integer_field(value, "output_tokens", -1),
            cost_usd: float_field(value, "cost_usd", -1.0),
            wall_time_ms: float_field(value, "wall_time_ms", -1.0),
            recursive_calls: integer_field(value, "recursive_calls", 0),
            synthetic: boolean_field(value, "synthetic", true),
        }
    }

    fn pair_key(&self) -> PairKey {
        PairKey {
            suite_id: self.suite_id.clone(),
            task_id: self.task_id.clone(),
            repetition: self.repetition,
            dataset_version: self.dataset_version.clone(),
            harness_commit: self.harness_commit.clone(),
            verifier_commit: self.verifier_commit.clone(),
            provider: self.provider.clone(),
            model: self.model.clone(),
        }
    }

    fn total_billable_tokens(&self) -> i64 {
        (self.input_tokens - self.cached_input_tokens).max(0) + self.output_tokens
    }

    fn validate(&self) -> Vec<String> {
        let mut reasons = Vec::new();
        if !SUITES.contains(&self.suite_id.as_str()) {
            reasons.push("unknown-suite".to_owned());
        }
        for (name, value) in [
            ("receipt-id", self.receipt_id.as_str()),
            ("task-id", self.task_id.as_str()),
            ("dataset-version", self.dataset_version.as_str()),
            ("provider", self.provider.as_str()),
            ("model", self.model.as_str()),
            ("model-config-hash", self.model_config_hash.as_str()),
            ("result-artifact-hash", self.result_artifact_hash.as_str()),
            ("raw-provider-receipt-hash", self.raw_provider_receipt_hash.as_str()),
        ] {
            if value.is_empty() {
                reasons.push(format!("missing-{name}"));
            }
        }
        if !ARMS.contains(&self.arm.as_str()) {
            reasons.push("unsupported-arm".to_owned());
        }
        if self.repetition < 1 {
            reasons.push("invalid-repetition".to_owned());
        }
        if !is_lower_hex(&self.harness_commit, 40) {
            reasons.push("invalid-harness-commit".to_owned());
        }
        if !is_lower_hex(&self.verifier_commit, 40) {
            reasons.push("invalid-verifier-commit".to_owned());
        }
        if !self.environment_image_digest.starts_with("sha256:")
            || !is_lower_hex(&self.environment_image_digest[7..], 64)
        {
            reasons.push("invalid-environment-image-digest".to_owned());
        }
        if self.suite_id == "swe-bench" && !is_lower_hex(&self.repository_commit, 40) {
            reasons.push("invalid-repository-commit".to_owned());
        }
        for (name, value) in [
            ("model-config-hash", self.model_config_hash.as_str()),
            ("result-artifact-hash", self.result_artifact_hash.as_str()),
            ("provider-receipt-hash", self.raw_provider_receipt_hash.as_str()),
        ] {
            if !is_lower_hex(value, 64) {
                reasons.push(format!("invalid-{name}"));
            }
        }
        if !(0.0..=1.0).contains(&self.quality_score) {
            reasons.push("invalid-quality-score".to_owned());
        }
        if [
            self.input_tokens,
            self.cached_input_tokens,
            self.output_tokens,
            self.recursive_calls,
        ]
        .into_iter()
        .min()
        .is_some_and(|value| value < 0)
        {
            reasons.push("invalid-count".to_owned());
        }
        if self.cached_input_tokens > self.input_tokens {
            reasons.push("cached-input-exceeds-input".to_owned());
        }
        if self.cost_usd < 0.0 {
            reasons.push("invalid-cost".to_owned());
        }
        if self.wall_time_ms < 0.0 {
            reasons.push("invalid-wall-time".to_owned());
        }
        let mut seen = HashSet::new();
        reasons
            .into_iter()
            .filter(|reason| seen.insert(reason.clone()))
            .collect()
    }
}

fn is_lower_hex(value: &str, length: usize) -> bool {
    value.len() == length
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn load_rows(path: &str) -> Result<Vec<BenchmarkRow>, String> {
    let text = fs::read_to_string(path)
        .map_err(|error| format!("EXTERNAL_SUITE_RECEIPT_READ_FAILED:{error}"))?;
    let value: Value = serde_json::from_str(&text)
        .map_err(|error| format!("EXTERNAL_SUITE_RECEIPT_JSON_INVALID:{error}"))?;
    let rows = match &value {
        Value::Object(object) => object.get("receipts").unwrap_or(&value),
        _ => &value,
    };
    let array = rows
        .as_array()
        .ok_or_else(|| "EXTERNAL_SUITE_RECEIPTS_NOT_LIST".to_owned())?;
    Ok(array
        .iter()
        .filter(|item| item.is_object())
        .map(BenchmarkRow::from_value)
        .collect())
}

fn path_and_suite(arguments: &[String]) -> Result<(String, Option<String>), String> {
    let index = arguments
        .windows(2)
        .position(|window| window[0] == "prove" && window[1] == "external-suite")
        .ok_or_else(|| "EXTERNAL_SUITE_COMMAND_MISSING".to_owned())?;
    let path = arguments
        .get(index + 2)
        .filter(|value| !value.starts_with('-'))
        .cloned()
        .ok_or_else(|| "EXTERNAL_SUITE_PATH_MISSING".to_owned())?;
    let mut suite = None;
    let mut cursor = index + 3;
    while cursor < arguments.len() {
        let value = &arguments[cursor];
        if value == "--suite" {
            suite = Some(
                arguments
                    .get(cursor + 1)
                    .cloned()
                    .ok_or_else(|| "EXTERNAL_SUITE_FILTER_MISSING".to_owned())?,
            );
            break;
        }
        if let Some(selected) = value.strip_prefix("--suite=") {
            suite = Some(selected.to_owned());
            break;
        }
        cursor += 1;
    }
    Ok((path, suite))
}

fn paired_rows(rows: &[BenchmarkRow], reasons: &mut Vec<String>) -> Vec<(BenchmarkRow, BenchmarkRow)> {
    let mut grouped: HashMap<PairKey, HashMap<String, BenchmarkRow>> = HashMap::new();
    for row in rows {
        grouped
            .entry(row.pair_key())
            .or_default()
            .insert(row.arm.clone(), row.clone());
    }
    let pairs = grouped
        .into_values()
        .filter_map(|group| {
            Some((group.get("baseline")?.clone(), group.get("syntavra")?.clone()))
        })
        .collect::<Vec<_>>();
    if pairs.len() < MINIMUM_PAIRS {
        reasons.push("insufficient-paired-runs".to_owned());
    }
    pairs
}

fn collect_metrics(pairs: &[(BenchmarkRow, BenchmarkRow)], reasons: &mut Vec<String>) -> Metrics {
    let mut metrics = Metrics::default();
    for (baseline, syntavra) in pairs {
        let parity = baseline.dataset_version == syntavra.dataset_version
            && baseline.harness_commit == syntavra.harness_commit
            && baseline.verifier_commit == syntavra.verifier_commit
            && baseline.environment_image_digest == syntavra.environment_image_digest
            && baseline.repository_commit == syntavra.repository_commit
            && baseline.provider == syntavra.provider
            && baseline.model == syntavra.model
            && baseline.model_config_hash == syntavra.model_config_hash;
        if !parity {
            reasons.push("pair-parity-failed".to_owned());
            continue;
        }
        metrics
            .quality_deltas
            .push(syntavra.quality_score - baseline.quality_score);
        metrics
            .success_deltas
            .push(syntavra.success.as_number() - baseline.success.as_number());
        let baseline_tokens = baseline.total_billable_tokens();
        if baseline_tokens > 0 {
            metrics.token_ratios.push(
                integer_as_f64(syntavra.total_billable_tokens())
                    / integer_as_f64(baseline_tokens),
            );
        }
        if baseline.cost_usd > 0.0 {
            metrics.cost_ratios.push(syntavra.cost_usd / baseline.cost_usd);
        }
        if baseline.wall_time_ms > 0.0 {
            metrics
                .wall_ratios
                .push(syntavra.wall_time_ms / baseline.wall_time_ms);
        }
    }
    metrics
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

fn integer_as_f64(value: i64) -> f64 {
    value.to_string().parse::<f64>().unwrap_or(0.0)
}

fn evaluate(rows: &[BenchmarkRow]) -> Value {
    let mut reasons = Vec::new();
    let invalid = rows
        .iter()
        .filter_map(|row| {
            let row_reasons = row.validate();
            (!row_reasons.is_empty()).then(|| {
                json!({"receipt_id": row.receipt_id, "reasons": row_reasons})
            })
        })
        .collect::<Vec<_>>();
    if !invalid.is_empty() {
        reasons.push("invalid-receipts".to_owned());
    }
    if rows.is_empty() {
        reasons.push("no-receipts".to_owned());
    }
    if rows.iter().any(|row| row.synthetic.is_enabled()) {
        reasons.push("synthetic-receipts-present".to_owned());
    }
    let duplicates = rows
        .iter()
        .filter(|row| {
            rows.iter()
                .filter(|item| item.receipt_id == row.receipt_id)
                .count()
                > 1
        })
        .map(|row| row.receipt_id.clone())
        .collect::<HashSet<_>>();
    let mut duplicate_receipt_ids = duplicates.into_iter().collect::<Vec<_>>();
    duplicate_receipt_ids.sort();
    if !duplicate_receipt_ids.is_empty() {
        reasons.push("duplicate-receipt-ids".to_owned());
    }

    let pairs = paired_rows(rows, &mut reasons);
    let metrics = collect_metrics(&pairs, &mut reasons);
    let quality_delta = precise_mean(&metrics.quality_deltas).unwrap_or(-1.0);
    let success_delta = precise_mean(&metrics.success_deltas).unwrap_or(-1.0);
    if quality_delta < -QUALITY_NON_INFERIORITY_MARGIN {
        reasons.push("quality-non-inferiority-failed".to_owned());
    }
    if success_delta < -SUCCESS_NON_INFERIORITY_MARGIN {
        reasons.push("success-non-inferiority-failed".to_owned());
    }
    if metrics.token_ratios.is_empty() {
        reasons.push("no-measurable-token-pairs".to_owned());
    }
    reasons.sort();
    reasons.dedup();
    let mut suites = rows
        .iter()
        .map(|row| row.suite_id.clone())
        .collect::<HashSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    suites.sort();
    let tasks = rows
        .iter()
        .map(|row| row.task_id.clone())
        .collect::<HashSet<_>>()
        .len();
    let ok = reasons.is_empty();
    json!({
        "ok": ok,
        "claim": if ok { "EXTERNAL_SUITE_EVIDENCE_VERIFIED" } else { "EXTERNAL_SUITE_EVIDENCE_NOT_PROVEN" },
        "public_superiority": if ok { "ELIGIBLE_FOR_MANUAL_REVIEW" } else { "EXTERNAL_SUPERIORITY_NOT_PROVEN" },
        "version": VERSION,
        "channel": CHANNEL,
        "suites": suites,
        "reasons": reasons,
        "invalid": invalid,
        "duplicate_receipt_ids": duplicate_receipt_ids,
        "metrics": {
            "receipts": rows.len(),
            "pairs": pairs.len(),
            "tasks": tasks,
            "mean_quality_delta": precise_mean(&metrics.quality_deltas),
            "mean_success_delta": precise_mean(&metrics.success_deltas),
            "mean_token_ratio": precise_mean(&metrics.token_ratios),
            "mean_cost_ratio": precise_mean(&metrics.cost_ratios),
            "mean_wall_time_ratio": precise_mean(&metrics.wall_ratios),
        },
        "requirements": {
            "minimum_pairs": MINIMUM_PAIRS,
            "quality_non_inferiority_margin": QUALITY_NON_INFERIORITY_MARGIN,
            "success_non_inferiority_margin": SUCCESS_NON_INFERIORITY_MARGIN,
            "identical_harness_dataset_verifier_environment_provider_model": true,
        },
    })
}

pub fn execute(arguments: &[String]) -> Result<ExternalSuiteDecision, String> {
    let (path, suite) = path_and_suite(arguments)?;
    let mut rows = load_rows(&path)?;
    if let Some(selected) = suite {
        rows.retain(|row| row.suite_id == selected);
    }
    let value = evaluate(&rows);
    let exit_code = if value["ok"] == Value::Bool(true) { 0 } else { 4 };
    Ok(ExternalSuiteDecision { value, exit_code })
}

#[cfg(test)]
mod tests {
    use super::{evaluate, BenchmarkRow};
    use serde_json::json;

    #[test]
    fn empty_receipts_fail_closed() {
        let value = evaluate(&[]);
        assert_eq!(value["ok"], false);
        assert!(value["reasons"]
            .as_array()
            .is_some_and(|rows| rows.iter().any(|row| row == "no-receipts")));
    }

    #[test]
    fn defaults_are_invalid() {
        assert!(!BenchmarkRow::from_value(&json!({})).validate().is_empty());
    }
}
