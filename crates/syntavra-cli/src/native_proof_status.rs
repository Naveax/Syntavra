#![forbid(unsafe_code)]

use serde_json::{json, Value};

const VERSION: &str = "0.0.1";
const CHANNEL: &str = "pre-release";
const WORKLOAD_MANIFEST_HASH: &str =
    "9e5e427c0b0ef72b6a5c06880e3649f887332d327a6107dd8172b85a5c8e90c3";

fn workload(workload_id: &str, family: &str, quality_verifier: &str) -> Value {
    json!({
        "workload_id": workload_id,
        "family": family,
        "quality_verifier": quality_verifier,
        "repetitions": 30,
        "competitor_arms": [
            "baseline",
            "syntavra",
            "headroom",
            "context-mode",
            "token-savior",
            "volt-lcm",
        ],
        "requires_provider_receipt": true,
    })
}

fn workloads() -> Vec<Value> {
    vec![
        workload("code-search", "coding", "symbol-and-answer-verifier"),
        workload(
            "repository-exploration",
            "coding",
            "repository-map-verifier",
        ),
        workload(
            "large-build-log",
            "tool-output",
            "failure-root-cause-verifier",
        ),
        workload("test-failure-triage", "coding", "test-repair-verifier"),
        workload(
            "sre-incident",
            "operations",
            "timeline-and-remediation-verifier",
        ),
        workload(
            "github-issue-triage",
            "operations",
            "classification-verifier",
        ),
        workload("sql-analytics", "structured-data", "query-answer-verifier"),
        workload("rag-qa", "retrieval", "citation-grounding-verifier"),
        workload(
            "api-response-analysis",
            "structured-data",
            "schema-and-answer-verifier",
        ),
        workload("multi-agent-handoff", "agents", "state-continuity-verifier"),
        workload(
            "long-coding-session",
            "long-context",
            "project-goal-verifier",
        ),
        workload(
            "multimodal-document",
            "multimodal",
            "cross-modal-grounding-verifier",
        ),
    ]
}

fn distributions() -> Vec<Value> {
    vec![
        json!({"channel": "pypi", "artifact": "syntavra-runtime", "signed": true, "provenance": true, "status": "configured"}),
        json!({"channel": "npm", "artifact": "@syntavra/sdk", "signed": true, "provenance": true, "status": "configured"}),
        json!({"channel": "ghcr", "artifact": "syntavra/runtime", "signed": true, "provenance": true, "status": "configured"}),
        json!({"channel": "homebrew", "artifact": "syntavra", "signed": true, "provenance": true, "status": "configured"}),
        json!({"channel": "winget", "artifact": "Syntavra.Syntavra", "signed": true, "provenance": true, "status": "configured"}),
        json!({"channel": "standalone", "artifact": "windows-linux-macos", "signed": true, "provenance": true, "status": "configured"}),
    ]
}

pub fn execute() -> Value {
    let workload_rows = workloads();
    json!({
        "release": {
            "ok": false,
            "version": VERSION,
            "channel": CHANNEL,
            "checks": {
                "sbom": false,
                "provenance": false,
                "reproducible_build": false,
                "signed_tags": false,
                "migration_guides": true,
                "rollback": true,
                "version_locked_0_0_1": true,
                "pre_release_channel": true,
            },
            "distributions": distributions(),
        },
        "workloads": {
            "version": VERSION,
            "channel": CHANNEL,
            "workloads": workload_rows,
            "workload_count": 12,
            "manifest_hash": WORKLOAD_MANIFEST_HASH,
        },
        "maturity": "PUBLIC_PRODUCT_MATURITY_NOT_PROVEN",
    })
}

#[cfg(test)]
mod tests {
    use super::{execute, WORKLOAD_MANIFEST_HASH};

    #[test]
    fn emits_fail_closed_public_proof_status() {
        let value = execute();
        assert_eq!(value["release"]["ok"], false);
        assert_eq!(value["workloads"]["workload_count"], 12);
        assert_eq!(value["workloads"]["manifest_hash"], WORKLOAD_MANIFEST_HASH);
        assert_eq!(value["maturity"], "PUBLIC_PRODUCT_MATURITY_NOT_PROVEN");
    }
}
