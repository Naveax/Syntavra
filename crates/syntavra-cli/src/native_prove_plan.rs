#![forbid(unsafe_code)]

use serde_json::{json, Value};

pub fn execute() -> Value {
    json!({
        "product": "Syntavra",
        "version": "0.0.1",
        "channel": "pre-release",
        "receipt_schema": "syntavra prove schema",
        "workloads": [
            "coding-agent",
            "repository-task",
            "swe-bench",
            "oolong-long-context",
            "session-continuity",
            "tool-routing"
        ],
        "long_context": {
            "version": "0.0.1",
            "channel": "pre-release",
            "name": "Syntavra Long-Context Quality Protocol",
            "style": "OOLONG-like evidence-intensive long-context evaluation",
            "tiers": [32_000, 64_000, 128_000, 256_000, 512_000, 1_000_000, 2_000_000, 10_000_000],
            "task_families": [
                "needle-retrieval",
                "temporal-supersession",
                "multi-hop-evidence",
                "repository-history",
                "cross-session-continuity",
                "recursive-map-reduce"
            ],
            "measured": [
                "answer quality",
                "required fact recall",
                "stale fact rejection",
                "evidence precision",
                "exact recovery",
                "forced restart",
                "session continuity",
                "provider tokens",
                "wall time"
            ],
            "claim_boundary": "A manifest or synthetic run never proves long-context quality."
        },
        "maturity": {
            "minimum_days": 90,
            "minimum_onboarding_receipts": 1000,
            "minimum_users": 50,
            "minimum_repositories": 100,
            "minimum_public_downloads": 1000,
            "minimum_verified_releases": 4
        },
        "measured_fields": [
            "provider fresh/cached/output/reasoning tokens",
            "provider cost",
            "wall time",
            "quality",
            "success",
            "source-level token attribution"
        ],
        "minimums": {
            "paired_runs": 30,
            "repositories": 5,
            "tasks": 10,
            "workload_families": 3
        },
        "claim": "EXTERNAL_SUPERIORITY_NOT_PROVEN"
    })
}

#[cfg(test)]
mod tests {
    use super::execute;

    #[test]
    fn emits_fail_closed_proof_plan() {
        let value = execute();
        assert_eq!(value["product"], "Syntavra");
        assert_eq!(value["version"], "0.0.1");
        assert_eq!(value["claim"], "EXTERNAL_SUPERIORITY_NOT_PROVEN");
        assert_eq!(value["minimums"]["paired_runs"], 30);
        assert_eq!(value["long_context"]["tiers"][7], 10_000_000);
    }
}
