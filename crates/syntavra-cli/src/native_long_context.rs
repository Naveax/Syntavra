#![forbid(unsafe_code)]

use std::collections::{HashMap, HashSet};
use std::fs;

use serde_json::{json, Value};

const VERSION: &str = "0.0.1";
const CHANNEL: &str = "pre-release";
const LONG_CONTEXT_TIERS: [i64; 8] = [
    32_000, 64_000, 128_000, 256_000, 512_000, 1_000_000, 2_000_000, 10_000_000,
];
const TASK_FAMILIES: [&str; 6] = [
    "needle-retrieval",
    "temporal-supersession",
    "multi-hop-evidence",
    "repository-history",
    "cross-session-continuity",
    "recursive-map-reduce",
];
const REQUIRED_TIERS: [i64; 3] = [32_000, 128_000, 1_000_000];

const MINIMUM_PAIRS: usize = 30;
const MINIMUM_CASES: usize = 10;
const MINIMUM_FAMILIES: usize = 4;
const QUALITY_NON_INFERIORITY_MARGIN: f64 = 0.01;
const MINIMUM_RECALL: f64 = 0.98;
const MINIMUM_STALE_REJECTION: f64 = 0.98;
const MINIMUM_EVIDENCE_PRECISION: f64 = 0.95;

type Pair = (Receipt, Receipt);

pub struct LongContextDecision {
    pub value: Value,
    pub exit_code: u8,
}

#[derive(Clone)]
struct Receipt {
    receipt_id: String,
    case_id: String,
    task_family: String,
    tier_tokens: i64,
    arm: String,
    repetition: i64,
    repository_hash: String,
    provider: String,
    model: String,
    answer_quality: f64,
    required_fact_recall: f64,
    stale_fact_rejection: f64,
    evidence_precision: f64,
    exact_recovery: bool,
    forced_restart: bool,
    continuity_restored: bool,
    wall_time_ms: f64,
    input_tokens: i64,
    output_tokens: i64,
    synthetic: bool,
}

#[derive(Clone, Hash, PartialEq, Eq)]
struct PairKey {
    case_id: String,
    tier_tokens: i64,
    repository_hash: String,
    repetition: i64,
    provider: String,
    model: String,
}

#[derive(Default)]
struct MetricRows {
    quality_deltas: Vec<f64>,
    recalls: Vec<f64>,
    stale_rejections: Vec<f64>,
    precisions: Vec<f64>,
    token_ratios: Vec<f64>,
    wall_ratios: Vec<f64>,
}

struct CoverageSets {
    cases: HashSet<String>,
    families: HashSet<String>,
    tiers: HashSet<i64>,
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

fn boolean_field(value: &Value, key: &str, default: bool) -> bool {
    value.get(key).and_then(Value::as_bool).unwrap_or(default)
}

fn numeric_as_f64(value: i64) -> f64 {
    value.to_string().parse::<f64>().unwrap_or(0.0)
}

fn length_as_f64(value: usize) -> f64 {
    value.to_string().parse::<f64>().unwrap_or(1.0)
}

impl Receipt {
    fn from_value(value: &Value) -> Self {
        Self {
            receipt_id: string_field(value, "receipt_id"),
            case_id: string_field(value, "case_id"),
            task_family: string_field(value, "task_family"),
            tier_tokens: integer_field(value, "tier_tokens", 0),
            arm: string_field(value, "arm"),
            repetition: integer_field(value, "repetition", 0),
            repository_hash: string_field(value, "repository_hash"),
            provider: string_field(value, "provider"),
            model: string_field(value, "model"),
            answer_quality: float_field(value, "answer_quality", -1.0),
            required_fact_recall: float_field(value, "required_fact_recall", -1.0),
            stale_fact_rejection: float_field(value, "stale_fact_rejection", -1.0),
            evidence_precision: float_field(value, "evidence_precision", -1.0),
            exact_recovery: boolean_field(value, "exact_recovery", false),
            forced_restart: boolean_field(value, "forced_restart", true),
            continuity_restored: boolean_field(value, "continuity_restored", false),
            wall_time_ms: float_field(value, "wall_time_ms", -1.0),
            input_tokens: integer_field(value, "input_tokens", -1),
            output_tokens: integer_field(value, "output_tokens", -1),
            synthetic: boolean_field(value, "synthetic", true),
        }
    }

    fn key(&self) -> PairKey {
        PairKey {
            case_id: self.case_id.clone(),
            tier_tokens: self.tier_tokens,
            repository_hash: self.repository_hash.clone(),
            repetition: self.repetition,
            provider: self.provider.clone(),
            model: self.model.clone(),
        }
    }

    fn validate(&self) -> Vec<String> {
        let mut reasons = Vec::new();
        for (key, value) in [
            ("receipt-id", self.receipt_id.as_str()),
            ("case-id", self.case_id.as_str()),
            ("arm", self.arm.as_str()),
            ("repository-hash", self.repository_hash.as_str()),
            ("provider", self.provider.as_str()),
            ("model", self.model.as_str()),
        ] {
            if value.is_empty() {
                reasons.push(format!("missing-{key}"));
            }
        }
        if !TASK_FAMILIES.contains(&self.task_family.as_str()) {
            reasons.push("unsupported-task-family".to_owned());
        }
        if !LONG_CONTEXT_TIERS.contains(&self.tier_tokens) {
            reasons.push("unsupported-tier".to_owned());
        }
        if !matches!(self.arm.as_str(), "baseline" | "syntavra") {
            reasons.push("unsupported-arm".to_owned());
        }
        if self.repetition < 1 {
            reasons.push("invalid-repetition".to_owned());
        }
        for (key, value) in [
            ("answer-quality", self.answer_quality),
            ("required-fact-recall", self.required_fact_recall),
            ("stale-fact-rejection", self.stale_fact_rejection),
            ("evidence-precision", self.evidence_precision),
        ] {
            if !(0.0..=1.0).contains(&value) {
                reasons.push(format!("invalid-{key}"));
            }
        }
        if self.wall_time_ms < 0.0 {
            reasons.push("invalid-wall-time".to_owned());
        }
        if self.input_tokens < 0 || self.output_tokens < 0 {
            reasons.push("invalid-token-count".to_owned());
        }
        let mut seen = HashSet::new();
        reasons
            .into_iter()
            .filter(|reason| seen.insert(reason.clone()))
            .collect()
    }
}

fn manifest() -> Value {
    json!({
        "version": VERSION,
        "channel": CHANNEL,
        "name": "Syntavra Long-Context Quality Protocol",
        "style": "OOLONG-like evidence-intensive long-context evaluation",
        "tiers": LONG_CONTEXT_TIERS,
        "task_families": TASK_FAMILIES,
        "measured": [
            "answer quality",
            "required fact recall",
            "stale fact rejection",
            "evidence precision",
            "exact recovery",
            "forced restart",
            "session continuity",
            "provider tokens",
            "wall time",
        ],
        "claim_boundary": "A manifest or synthetic run never proves long-context quality.",
    })
}

fn mean(values: &[f64]) -> Option<f64> {
    (!values.is_empty()).then(|| values.iter().sum::<f64>() / length_as_f64(values.len()))
}

fn invalid_receipts(rows: &[Receipt], reasons: &mut Vec<String>) -> Vec<Value> {
    let invalid = rows
        .iter()
        .filter_map(|row| {
            let row_reasons = row.validate();
            (!row_reasons.is_empty())
                .then(|| json!({"receipt_id": row.receipt_id, "reasons": row_reasons}))
        })
        .collect::<Vec<_>>();
    if !invalid.is_empty() {
        reasons.push("invalid-receipts".to_owned());
    }
    if rows.is_empty() {
        reasons.push("no-receipts".to_owned());
    }
    if rows.iter().any(|row| row.synthetic) {
        reasons.push("synthetic-receipts-present".to_owned());
    }
    invalid
}

fn paired_receipts(rows: &[Receipt], reasons: &mut Vec<String>) -> Vec<Pair> {
    let mut groups: HashMap<PairKey, HashMap<String, Receipt>> = HashMap::new();
    let mut key_order = Vec::new();
    for row in rows {
        let key = row.key();
        if !groups.contains_key(&key) {
            key_order.push(key.clone());
        }
        groups
            .entry(key)
            .or_default()
            .insert(row.arm.clone(), row.clone());
    }
    let pairs = key_order
        .iter()
        .filter_map(|key| {
            let group = groups.get(key)?;
            Some((
                group.get("baseline")?.clone(),
                group.get("syntavra")?.clone(),
            ))
        })
        .collect::<Vec<_>>();
    if pairs.len() < MINIMUM_PAIRS {
        reasons.push("insufficient-paired-runs".to_owned());
    }
    pairs
}

fn coverage_sets(pairs: &[Pair], reasons: &mut Vec<String>) -> CoverageSets {
    let coverage = CoverageSets {
        cases: pairs
            .iter()
            .map(|(_, syntavra)| syntavra.case_id.clone())
            .collect(),
        families: pairs
            .iter()
            .map(|(_, syntavra)| syntavra.task_family.clone())
            .collect(),
        tiers: pairs
            .iter()
            .map(|(_, syntavra)| syntavra.tier_tokens)
            .collect(),
    };
    if coverage.cases.len() < MINIMUM_CASES {
        reasons.push("insufficient-cases".to_owned());
    }
    if coverage.families.len() < MINIMUM_FAMILIES {
        reasons.push("insufficient-task-families".to_owned());
    }
    if !REQUIRED_TIERS
        .iter()
        .all(|tier| coverage.tiers.contains(tier))
    {
        reasons.push("required-tiers-missing".to_owned());
    }
    coverage
}

fn collect_metrics(pairs: &[Pair], reasons: &mut Vec<String>) -> MetricRows {
    let mut metrics = MetricRows::default();
    for (baseline, syntavra) in pairs {
        if baseline.provider != syntavra.provider || baseline.model != syntavra.model {
            reasons.push("provider-or-model-parity-failed".to_owned());
            continue;
        }
        metrics
            .quality_deltas
            .push(syntavra.answer_quality - baseline.answer_quality);
        metrics.recalls.push(syntavra.required_fact_recall);
        metrics.stale_rejections.push(syntavra.stale_fact_rejection);
        metrics.precisions.push(syntavra.evidence_precision);
        let baseline_tokens = baseline.input_tokens + baseline.output_tokens;
        let syntavra_tokens = syntavra.input_tokens + syntavra.output_tokens;
        if baseline_tokens > 0 {
            metrics
                .token_ratios
                .push(numeric_as_f64(syntavra_tokens) / numeric_as_f64(baseline_tokens));
        }
        if baseline.wall_time_ms > 0.0 {
            metrics
                .wall_ratios
                .push(syntavra.wall_time_ms / baseline.wall_time_ms);
        }
        if !syntavra.exact_recovery {
            reasons.push("exact-recovery-failed".to_owned());
        }
        if syntavra.forced_restart {
            reasons.push("forced-restart-observed".to_owned());
        }
        if syntavra.task_family == "cross-session-continuity" && !syntavra.continuity_restored {
            reasons.push("session-continuity-failed".to_owned());
        }
    }
    metrics
}

fn apply_metric_requirements(metrics: &MetricRows, reasons: &mut Vec<String>) {
    if mean(&metrics.quality_deltas).unwrap_or(-1.0) < -QUALITY_NON_INFERIORITY_MARGIN {
        reasons.push("quality-non-inferiority-failed".to_owned());
    }
    if mean(&metrics.recalls).unwrap_or(0.0) < MINIMUM_RECALL {
        reasons.push("required-fact-recall-failed".to_owned());
    }
    if mean(&metrics.stale_rejections).unwrap_or(0.0) < MINIMUM_STALE_REJECTION {
        reasons.push("stale-fact-rejection-failed".to_owned());
    }
    if mean(&metrics.precisions).unwrap_or(0.0) < MINIMUM_EVIDENCE_PRECISION {
        reasons.push("evidence-precision-failed".to_owned());
    }
}

fn result_value(
    pairs: &[Pair],
    coverage: CoverageSets,
    metrics: MetricRows,
    invalid: Vec<Value>,
    mut reasons: Vec<String>,
) -> Value {
    reasons.sort();
    reasons.dedup();
    let ok = reasons.is_empty();
    let mut tiers = coverage.tiers.into_iter().collect::<Vec<_>>();
    tiers.sort_unstable();
    json!({
        "ok": ok,
        "claim": if ok { "LONG_CONTEXT_QUALITY_VERIFIED" } else { "LONG_CONTEXT_QUALITY_NOT_PROVEN" },
        "architecture_claim": "UNBOUNDED_EXTERNAL_HISTORY_WITH_BOUNDED_ACTIVE_WINDOW",
        "version": VERSION,
        "channel": CHANNEL,
        "reasons": reasons,
        "invalid": invalid,
        "metrics": {
            "pairs": pairs.len(),
            "cases": coverage.cases.len(),
            "families": coverage.families.len(),
            "tiers": tiers,
            "mean_quality_delta": mean(&metrics.quality_deltas),
            "mean_required_fact_recall": mean(&metrics.recalls),
            "mean_stale_fact_rejection": mean(&metrics.stale_rejections),
            "mean_evidence_precision": mean(&metrics.precisions),
            "mean_token_ratio": mean(&metrics.token_ratios),
            "mean_wall_time_ratio": mean(&metrics.wall_ratios),
        },
        "requirements": {
            "minimum_pairs": MINIMUM_PAIRS,
            "minimum_cases": MINIMUM_CASES,
            "minimum_families": MINIMUM_FAMILIES,
            "required_tiers": REQUIRED_TIERS,
            "quality_non_inferiority_margin": QUALITY_NON_INFERIORITY_MARGIN,
            "minimum_recall": MINIMUM_RECALL,
            "minimum_stale_rejection": MINIMUM_STALE_REJECTION,
            "minimum_evidence_precision": MINIMUM_EVIDENCE_PRECISION,
        },
    })
}

fn evaluate(rows: &[Receipt]) -> Value {
    let mut reasons = Vec::new();
    let invalid = invalid_receipts(rows, &mut reasons);
    let pairs = paired_receipts(rows, &mut reasons);
    let coverage = coverage_sets(&pairs, &mut reasons);
    let metrics = collect_metrics(&pairs, &mut reasons);
    apply_metric_requirements(&metrics, &mut reasons);
    result_value(&pairs, coverage, metrics, invalid, reasons)
}

fn path_argument(arguments: &[String]) -> Option<&str> {
    arguments
        .windows(3)
        .find(|window| window[0] == "prove" && window[1] == "long-context")
        .map(|window| window[2].as_str())
        .filter(|value| !value.starts_with('-'))
}

fn load_receipts(path: &str) -> Result<Vec<Receipt>, String> {
    let text = fs::read_to_string(path)
        .map_err(|error| format!("LONG_CONTEXT_RECEIPT_READ_FAILED:{error}"))?;
    let value: Value = serde_json::from_str(&text)
        .map_err(|error| format!("LONG_CONTEXT_RECEIPT_JSON_INVALID:{error}"))?;
    let rows = match &value {
        Value::Object(object) => object.get("receipts").unwrap_or(&value),
        _ => &value,
    };
    let array = rows
        .as_array()
        .ok_or_else(|| "LONG_CONTEXT_RECEIPTS_NOT_LIST".to_owned())?;
    Ok(array
        .iter()
        .filter(|item| item.is_object())
        .map(Receipt::from_value)
        .collect())
}

pub fn execute(arguments: &[String]) -> Result<LongContextDecision, String> {
    let Some(path) = path_argument(arguments) else {
        return Ok(LongContextDecision {
            value: manifest(),
            exit_code: 0,
        });
    };
    let receipts = load_receipts(path)?;
    let value = evaluate(&receipts);
    let exit_code = if value["ok"] == Value::Bool(true) {
        0
    } else {
        4
    };
    Ok(LongContextDecision { value, exit_code })
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{evaluate, manifest, Receipt};

    #[test]
    fn manifest_has_required_tiers() {
        assert_eq!(manifest()["tiers"][0], 32_000);
        assert_eq!(manifest()["tiers"][7], 10_000_000);
    }

    #[test]
    fn empty_receipts_fail_closed() {
        let value = evaluate(&[]);
        assert_eq!(value["ok"], false);
        assert!(value["reasons"]
            .as_array()
            .is_some_and(|rows| rows.iter().any(|row| row == "no-receipts")));
    }

    #[test]
    fn receipt_defaults_are_fail_closed() {
        let receipt = Receipt::from_value(&json!({}));
        assert!(!receipt.validate().is_empty());
    }
}
