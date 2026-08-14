#![forbid(unsafe_code)]

use serde_json::{json, Value};
use syntavra_core::sha256_hex;

fn swe_bench() -> Value {
    json!({
        "suite_id": "swe-bench",
        "display_name": "SWE-bench",
        "task_domain": "real-repository-coding",
        "upstream": "https://github.com/SWE-bench/SWE-bench",
        "primary_reference": "https://arxiv.org/abs/2310.06770",
        "required_metrics": [
            "resolved",
            "tests_passed",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "wall_time_ms"
        ],
        "quality_direction": "higher-is-better",
        "requires_repository": true,
        "requires_provider_receipt": true
    })
}

fn oolong() -> Value {
    json!({
        "suite_id": "oolong",
        "display_name": "Oolong",
        "task_domain": "long-context-analysis-and-aggregation",
        "upstream": "https://github.com/bartbussmann/oolong",
        "primary_reference": "https://arxiv.org/abs/2511.02817",
        "required_metrics": [
            "score",
            "required_fact_recall",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "wall_time_ms"
        ],
        "quality_direction": "higher-is-better",
        "requires_repository": false,
        "requires_provider_receipt": true
    })
}

fn longbench_v2() -> Value {
    json!({
        "suite_id": "longbench-v2",
        "display_name": "LongBench v2",
        "task_domain": "realistic-long-context-reasoning",
        "upstream": "https://github.com/THUDM/LongBench",
        "primary_reference": "https://aclanthology.org/2025.acl-long.183/",
        "required_metrics": [
            "accuracy",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "wall_time_ms"
        ],
        "quality_direction": "higher-is-better",
        "requires_repository": false,
        "requires_provider_receipt": true
    })
}

fn infinitebench() -> Value {
    json!({
        "suite_id": "infinitebench",
        "display_name": "InfiniteBench",
        "task_domain": "100k-plus-long-context",
        "upstream": "https://github.com/OpenBMB/InfiniteBench",
        "primary_reference": "https://aclanthology.org/2024.acl-long.814/",
        "required_metrics": [
            "score",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "wall_time_ms"
        ],
        "quality_direction": "higher-is-better",
        "requires_repository": false,
        "requires_provider_receipt": true
    })
}

fn recursive_long_context() -> Value {
    json!({
        "suite_id": "recursive-long-context",
        "display_name": "Recursive long-context paired tasks",
        "task_domain": "recursive-programmatic-context",
        "upstream": "https://github.com/alexzhang13/rlm",
        "primary_reference": "https://arxiv.org/abs/2512.24601",
        "required_metrics": [
            "quality_score",
            "success",
            "recursive_calls",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "wall_time_ms"
        ],
        "quality_direction": "higher-is-better",
        "requires_repository": false,
        "requires_provider_receipt": true
    })
}

fn suites() -> Value {
    Value::Array(vec![
        swe_bench(),
        oolong(),
        longbench_v2(),
        infinitebench(),
        recursive_long_context(),
    ])
}

pub fn execute() -> Value {
    let rows = suites();
    let encoded = serde_json::to_vec(&rows).unwrap_or_default();
    json!({
        "version": "0.0.1",
        "channel": "pre-release",
        "suites": rows,
        "suite_count": 5,
        "manifest_hash": sha256_hex(&encoded),
        "claim_boundary": "Configured suites and internal fixtures are not external benchmark results."
    })
}

#[cfg(test)]
mod tests {
    use super::execute;

    #[test]
    fn manifest_has_five_pinned_suites() {
        let value = execute();
        assert_eq!(value["suite_count"], 5);
        assert_eq!(value["suites"][0]["suite_id"], "swe-bench");
        assert_eq!(value["suites"][4]["suite_id"], "recursive-long-context");
        assert_eq!(value["manifest_hash"].as_str().map(str::len), Some(64));
    }
}
