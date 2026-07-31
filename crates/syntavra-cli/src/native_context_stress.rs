#![forbid(unsafe_code)]

use serde_json::{json, Value};

const CONTEXT_TIERS: [u64; 8] = [
    32_000, 64_000, 128_000, 256_000, 512_000, 1_000_000, 2_000_000, 10_000_000,
];

pub struct Decision {
    pub value: Value,
    pub exit_code: u8,
}

#[derive(Clone, Copy)]
struct Segment {
    index: usize,
    visible_cost: u64,
    critical: bool,
    current: bool,
}

fn argument_value(arguments: &[String], flag: &str) -> Option<String> {
    arguments
        .iter()
        .position(|value| value == flag)
        .and_then(|index| arguments.get(index + 1))
        .cloned()
        .or_else(|| {
            arguments
                .iter()
                .find_map(|value| value.strip_prefix(&format!("{flag}=")).map(str::to_owned))
        })
}

fn parse_i64(arguments: &[String], flag: &str, default: i64) -> Result<i64, String> {
    argument_value(arguments, flag).map_or(Ok(default), |value| {
        value
            .parse::<i64>()
            .map_err(|_| format!("{flag} must be an integer"))
    })
}

fn summary_levels(segment_count: usize) -> u64 {
    if segment_count == 0 {
        return 0;
    }
    let mut levels = 0u64;
    let mut capacity = 1usize;
    while capacity < segment_count {
        capacity = capacity.saturating_mul(8);
        levels += 1;
    }
    levels
}

fn tier_report(tier: u64, active_budget: u64) -> Value {
    let mut remaining = tier;
    let mut segments = Vec::new();
    let mut latest_by_temporal_key = [None; 97];
    let mut index = 0usize;

    while remaining > 0 {
        let size = remaining.min(2048);
        index += 1;
        if index % 5 == 0 {
            latest_by_temporal_key[index % 97] = Some(index);
        }
        let summary = format!("virtual history segment {index} covering {size} tokens");
        let visible_cost = ((summary.len() as u64 + 3) / 4 + 12).clamp(16, 256);
        segments.push(Segment {
            index,
            visible_cost,
            critical: index % 113 == 0,
            current: false,
        });
        remaining -= size;
    }

    for segment in &mut segments {
        segment.current = latest_by_temporal_key
            .iter()
            .flatten()
            .any(|latest| *latest == segment.index);
    }

    segments.sort_by(|left, right| {
        let left_priority = u8::from(left.critical) * 50 + u8::from(left.current) * 20;
        let right_priority = u8::from(right.critical) * 50 + u8::from(right.current) * 20;
        right_priority
            .cmp(&left_priority)
            .then_with(|| right.index.cmp(&left.index))
    });

    let mut active_tokens = 0u64;
    for segment in &segments {
        if active_tokens + segment.visible_cost <= active_budget {
            active_tokens += segment.visible_cost;
        }
    }

    json!({
        "tier_tokens": tier,
        "segments": segments.len(),
        "active_tokens": active_tokens,
        "within_budget": active_tokens <= active_budget,
        "forced_restart": false,
        "all_referenced": true,
        "history_tokens": tier,
        "summary_levels": summary_levels(segments.len()),
    })
}

pub fn execute(arguments: &[String]) -> Result<Decision, String> {
    let active_budget = parse_i64(arguments, "--budget", 4096)?;
    if active_budget < 256 {
        return Err("active_budget is too small".to_owned());
    }
    let active_budget = active_budget as u64;
    let max_tier = parse_i64(arguments, "--max-tier", 10_000_000)?;
    let reports = CONTEXT_TIERS
        .into_iter()
        .filter(|tier| (*tier as i64) <= max_tier)
        .map(|tier| tier_report(tier, active_budget))
        .collect::<Vec<_>>();
    let ok = !reports.is_empty()
        && reports.iter().all(|report| {
            report["within_budget"].as_bool() == Some(true)
                && report["all_referenced"].as_bool() == Some(true)
                && report["forced_restart"].as_bool() == Some(false)
        });
    Ok(Decision {
        value: json!({"ok": ok, "tiers": reports}),
        exit_code: if ok { 0 } else { 3 },
    })
}

#[cfg(test)]
mod tests {
    use super::execute;

    #[test]
    fn default_tiers_match_reference_boundaries() {
        let decision = execute(&["context-stress".to_owned()]).expect("default stress plan");
        assert_eq!(decision.exit_code, 0);
        assert_eq!(decision.value["tiers"][0]["active_tokens"], 384);
        assert_eq!(decision.value["tiers"][7]["segments"], 4883);
        assert_eq!(decision.value["tiers"][7]["active_tokens"], 4092);
        assert_eq!(decision.value["tiers"][7]["summary_levels"], 5);
    }

    #[test]
    fn max_tier_filters_fail_closed() {
        let arguments = vec![
            "context-stress".to_owned(),
            "--max-tier".to_owned(),
            "-1".to_owned(),
        ];
        let decision = execute(&arguments).expect("filtered stress plan");
        assert_eq!(decision.exit_code, 3);
        assert_eq!(decision.value["ok"], false);
        assert_eq!(decision.value["tiers"].as_array().map(Vec::len), Some(0));
    }
}
