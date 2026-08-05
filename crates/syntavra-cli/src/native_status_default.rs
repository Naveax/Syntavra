#![forbid(unsafe_code)]
#![allow(clippy::pedantic)]

use std::path::Path;

use serde_json::{json, Value};

const VERSION: &str = "0.0.1";
const CHANNEL: &str = "pre-release";

fn feature_groups() -> Value {
    json!({
        "pre_execution": [
            "pretool-command-rewrite",
            "instant-optimization-modes",
            "prompt-cache-layout",
            "prompt-cache-expiry",
        ],
        "output": [
            "exact-first-externalization",
            "command-specific-compaction",
            "secret-redaction",
            "lossless-wire-format",
        ],
        "repository": [
            "live-watcher",
            "incremental-reindex",
            "optional-tree-sitter-backend",
            "parser-confidence-receipts",
            "call-hierarchy",
            "class-hierarchy",
            "dead-code",
            "untested-symbols",
            "pagerank",
            "hotspots",
            "cycles",
            "coupling",
            "module-boundaries",
            "signal-chain",
            "duplicates",
            "provenance",
            "pr-risk",
            "delete-safe",
            "refactor-plan",
            "cross-language-anti-patterns",
            "cross-repo-contracts",
        ],
        "memory": [
            "llm-or-heuristic-extraction",
            "validity-roi-ranking",
            "bm25-cosine-rerank",
            "embedding-backfill",
            "jsonl-export",
            "critical-notifications",
        ],
        "routing": [
            "provider-account-pool",
            "credential-reference-only-storage",
            "subscription-priority",
            "circuit-breaker-failover",
            "quota-aware-fallback",
            "rate-limit-switching",
            "complexity-model-routing",
            "automatic-subtask-delegation",
            "short-handoff-subagents",
            "provider-gateway-presets",
        ],
        "experience": [
            "host-statusline",
            "live-savings-badge",
            "local-web-dashboard",
            "pwa-dashboard",
            "vscode-extension",
            "native-rust-companion",
            "agent-config-auditor",
            "transcript-opportunity-miner",
        ],
        "evidence": [
            "provider-billed-signalbench",
            "provider-receipt-gate",
            "registry-publication-readiness",
            "fail-closed-public-claims",
        ],
    })
}

fn competitive_features(project_root: &Path) -> Value {
    let artifact = |relative: &str| project_root.join(relative).exists();
    json!({
        "version": VERSION,
        "channel": CHANNEL,
        "feature_groups": feature_groups(),
        "feature_count": 57,
        "optimization_modes": ["commit", "compress", "full", "lite", "review", "ultra"],
        "rewrite_rules": 118,
        "compactors": 131,
        "hosts": 44,
        "controlled_hosts": 40,
        "provider_presets": 48,
        "artifacts": {
            "vscode_extension": artifact("extensions/vscode/package.json"),
            "native_binary": artifact("crates/syntavra-cli/Cargo.toml"),
            "publish_readiness": artifact("docs/release/publish-readiness.json"),
            "language_parsers": artifact("syntavra_runtime/language_parsers.py"),
            "provider_account_pool": artifact("syntavra_runtime/provider_accounts.py"),
            "competitive_gap_validator": artifact("tools/validate_competitive_gap.py"),
        },
        "external_claims": {
            "registry_published": false,
            "competitor_superiority": "EXTERNAL_SUPERIORITY_NOT_PROVEN",
            "live_certification": "LIVE_INTEGRATION_CERTIFICATION_NOT_PROVEN",
        },
        "ok": false,
    })
}

fn proxy_presets() -> Value {
    json!({
        "ok": true,
        "providers": 10,
        "zero_code_compatible": 7,
        "adapter_required": 3,
        "missing": [],
        "extra": [],
        "unsafe_upstreams": [],
        "live_boundary": "preset validation is not live provider certification",
    })
}

fn languages() -> Value {
    json!([
        "ada", "agda", "apex", "assembly", "astro", "awk", "batch", "bazel", "c", "cairo",
        "capnp", "clojure", "cmake", "cobol", "common-lisp", "coq", "cpp", "crystal", "csharp",
        "css", "cuda", "cue", "d", "dart", "dockerfile", "elixir", "elm", "erlang", "fish",
        "flatbuffers", "fortran", "fsharp", "gdscript", "go", "graphql", "groovy", "haskell",
        "hcl", "html", "idris", "ini", "java", "javascript", "json", "julia", "kotlin", "lean",
        "less", "llvm-ir", "lua", "luau", "make", "markdown", "matlab", "meson", "move", "nim",
        "ninja", "nix", "nushell", "objective-c", "ocaml", "octave", "opencl", "pascal", "perl",
        "php", "powershell", "prolog", "protobuf", "purescript", "python", "qsharp", "r", "racket",
        "raku", "reason", "rego", "renpy", "ruby", "rust", "sass", "scala", "scheme", "scss",
        "shell", "smalltalk", "solidity", "sql", "svelte", "swift", "systemverilog", "tcl",
        "terraform", "thrift", "toml", "typescript", "verilog", "vhdl", "visual-basic", "vue",
        "vyper", "webassembly-text", "xml", "yaml", "zig"
    ])
}

fn language_adapters() -> Value {
    json!([
        "bash", "c", "c_sharp", "cpp", "csharp", "dart", "elixir", "erlang", "fish", "fsharp",
        "go", "haskell", "java", "javascript", "julia", "kotlin", "lua", "ocaml", "php",
        "powershell", "r", "ruby", "rust", "scala", "solidity", "svelte", "swift", "typescript",
        "vue", "zig"
    ])
}

fn tree_sitter_available() -> Value {
    json!([
        "bash", "c", "cpp", "csharp", "dart", "elixir", "erlang", "fish", "fsharp", "go",
        "haskell", "java", "javascript", "julia", "kotlin", "lua", "ocaml", "php", "powershell",
        "r", "ruby", "rust", "scala", "solidity", "svelte", "swift", "typescript", "vue", "zig"
    ])
}

fn language_platform() -> Value {
    json!({
        "ok": true,
        "declared": 106,
        "available": 30,
        "canonical_graph": true,
        "universal_text_fallback": true,
        "evidence_levels": ["lexical", "syntax", "semantic"],
        "claim_boundary": "declared support is not live certification; unknown and future text languages remain navigable, while exact semantic claims require validated parser, analyzer, LSP, LSIF or SCIP evidence",
        "language_registry": {
            "registered_languages": 106,
            "languages": languages(),
            "adapters": language_adapters(),
            "universal_text_fallback": true,
            "diagnostics": ["entry-point-discovery-disabled: explicit SYNTAVRA_ALLOW_LANGUAGE_PLUGINS authorization required"],
            "entry_point_plugins_authorized": false,
        },
        "tree_sitter": {
            "adapter": "tree-sitter-language-pack",
            "installed": true,
            "available_languages": tree_sitter_available(),
            "capability_level": "syntax",
            "claim_boundary": "cross-file semantic identity requires LSP, LSIF or SCIP confirmation",
        },
        "lsp_services": {
            "services": 0,
            "service_ids": [],
            "languages": [],
            "diagnostics": [],
            "execution_authorized": false,
        },
        "sandboxed_analyzers": {
            "services": 0,
            "service_ids": [],
            "languages": [],
            "diagnostics": [],
            "execution_authorized": false,
        },
        "semantic_indexes": {
            "semantic_index_edges": 0,
            "semantic_index_formats": [],
            "semantic_index_nodes": 0,
            "semantic_index_sources": 0,
            "stale_semantic_index_sources": 0,
        },
        "repository_query": {
            "backend": "sqlite-fts5",
            "graph_nodes": 0,
            "indexed_nodes": 0,
        },
        "universal_claim_boundary": "unknown and future text languages remain navigable; exact type, call, implementation and override claims require validated parser, analyzer, LSP, LSIF or SCIP evidence",
    })
}

fn capabilities() -> Value {
    json!({
        "agent_event_stream": true,
        "atomic_lsif_scip_import": true,
        "atomic_update_manager": true,
        "bounded_autonomous_agent": true,
        "canonical_repository_graph": true,
        "cli_and_non_cli_adapters": true,
        "content_addressed_exact_recovery": true,
        "default_tree_sitter_syntax": true,
        "fault_injection_laboratory": true,
        "generic_hash_pinned_lsp": true,
        "headless_job_runtime": true,
        "incremental_semantic_graph": true,
        "indexed_fts_repository_query": true,
        "interactive_token_console": true,
        "model_backed_agent_runtime": true,
        "multi_view_session_memory": true,
        "pre_context_output_firewall": true,
        "probed_native_sandbox": true,
        "project_aware_verifier_discovery": true,
        "runtime_evidence_graph": true,
        "sandboxed_language_analyzers": true,
        "secretless_provider_gateway": true,
        "signed_single_use_capabilities": true,
        "streaming_terminal_output_engine": true,
        "structured_agent_edits": true,
        "terminal_never_worse_guard": true,
        "typed_context_compiler": true,
        "universal_future_language_fallback": true,
        "verified_agent_delivery_modes": true,
    })
}

fn sandbox() -> Value {
    let platform = std::env::consts::OS;
    let (detail, unsupported) = match platform {
        "linux" => (
            "install bubblewrap for full native isolation",
            json!(["mount-namespace", "network-namespace", "seccomp", "cgroup"]),
        ),
        "windows" => (
            "portable process boundary is active; native Windows isolation requires an authorized backend",
            json!(["app-container", "job-object-network-policy"]),
        ),
        "macos" => (
            "portable process boundary is active; native macOS isolation requires an authorized backend",
            json!(["sandbox-exec-profile", "network-policy"]),
        ),
        _ => (
            "portable process boundary is active",
            json!(["native-platform-isolation"]),
        ),
    };
    json!({
        "ok": true,
        "strict_ready": false,
        "fail_closed": true,
        "probe_cached": false,
        "backend": {
            "name": "portable-process-boundary",
            "platform": platform,
            "available": false,
            "command_prefix": [],
            "enforced": ["cwd-boundary", "environment-filter", "timeout", "process-group"],
            "unsupported": unsupported,
            "detail": detail,
        },
    })
}

fn platform(project_root: &Path) -> Value {
    json!({
        "product": "Syntavra",
        "version": VERSION,
        "channel": CHANNEL,
        "project": project_root.to_string_lossy(),
        "artifacts": {"artifacts": 0, "exact_bytes": 0, "kinds": []},
        "semantic_graph": {
            "canonical_graph": true,
            "capabilities": [],
            "detectors": [],
            "edges": 0,
            "files": 0,
            "languages": [],
            "nodes": 0,
            "repository_query": {"backend": "sqlite-fts5", "graph_nodes": 0, "indexed_nodes": 0},
            "semantic_index_edges": 0,
            "semantic_index_formats": [],
            "semantic_index_nodes": 0,
            "semantic_index_sources": 0,
            "stale_semantic_index_sources": 0,
            "universal_text_fallback": true,
            "unknown_language_files": 0,
        },
        "runtime_evidence": {"edges": 0, "nodes": 0, "ok": true, "relations": []},
        "language_platform": language_platform(),
        "memory": {
            "sessions": 0,
            "events": 0,
            "summaries": 0,
            "checkpoints": 0,
            "views": ["task", "decision", "change", "failure", "security", "dependency", "repository", "test", "provider", "handoff"],
        },
        "headless": {"jobs": 0, "states": {}, "ok": true},
        "sandbox": sandbox(),
        "adapters": {
            "ok": true,
            "adapters": 20,
            "levels": {"A": 4, "B": 10, "C": 5, "D": 1},
            "surfaces": {"cli": 8, "ide": 7, "ide-extension": 3, "platform": 2},
            "non_cli_adapters": 12,
            "live_certified": 0,
            "invalid": [],
            "inventory_gate": true,
            "live_boundary": "live certification requires external execution receipts",
        },
        "providers": ["anthropic", "azure-openai", "bedrock", "gemini", "groq", "local", "mistral", "openai", "openrouter", "vertex"],
        "capabilities": capabilities(),
        "claim_boundary": "functional capabilities are internally tested; external superiority and live certification remain receipt-gated",
    })
}

fn benchmark() -> Value {
    json!({
        "ok": false,
        "claim": "MEASURED_AGENT_BENCHMARK_NOT_PROVEN",
        "external_superiority": "EXTERNAL_SUPERIORITY_NOT_PROVEN",
        "reasons": [
            "insufficient-paired-runs",
            "insufficient-repositories",
            "insufficient-tasks",
            "insufficient-workload-diversity",
            "no-measurable-pairs",
            "quality-non-inferiority-failed",
            "receipt-validation-failed",
            "success-non-inferiority-failed",
        ],
        "metrics": {
            "pairs": 0,
            "repositories": 0,
            "tasks": 0,
            "workloads": 0,
            "mean_token_ratio": Value::Null,
            "median_token_ratio": Value::Null,
            "mean_wall_time_ratio": Value::Null,
            "mean_cost_ratio": Value::Null,
            "mean_quality_delta": Value::Null,
            "mean_success_delta": Value::Null,
        },
        "requirements": {
            "minimum_pairs": 30,
            "minimum_repositories": 5,
            "minimum_tasks": 10,
            "minimum_workload_families": 3,
            "quality_non_inferiority_margin": 0.01,
            "success_non_inferiority_margin": 0.02,
        },
    })
}

fn readiness(state_root: &Path) -> Value {
    let files = json!({
        "product.json": state_root.join("product.json").is_file(),
        "mcp-profile.json": state_root.join("mcp-profile.json").is_file(),
        "platform-adapters.json": state_root.join("platform-adapters.json").is_file(),
    });
    let setup_bundle = files
        .as_object()
        .is_some_and(|values| values.values().all(|value| value.as_bool() == Some(true)));
    let benchmark = benchmark();
    json!({
        "ok": false,
        "claim": "DAILY_CODING_AGENT_READINESS_NOT_PROVEN",
        "checks": {
            "narrow_product_surface": true,
            "platform_adapter_contracts": true,
            "integration_matrix": true,
            "setup_bundle": setup_bundle,
            "measured_agent_benchmark": false,
        },
        "files": files,
        "benchmark": benchmark,
        "version": VERSION,
        "channel": CHANNEL,
    })
}

pub fn snapshot(
    project_root: &Path,
    state_root: &Path,
    doctor: Value,
    stats: Value,
    profile: Value,
    evidence: Value,
    session_memory: Value,
) -> Value {
    json!({
        "product": "Syntavra",
        "version": VERSION,
        "channel": CHANNEL,
        "role": "token-and-context-optimization-skill",
        "doctor": doctor,
        "stats": stats,
        "savings": evidence["token_attribution"],
        "profile": profile,
        "readiness": readiness(state_root),
        "evidence": evidence,
        "session_memory": session_memory,
        "proxy_presets": proxy_presets(),
        "platform": platform(project_root),
        "competitive_features": competitive_features(project_root),
        "primary_workflow": ["setup", "status", "run", "prove"],
    })
}
