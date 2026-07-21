# SignalCore Measured Agent Proof Protocol 001

## Purpose

This protocol separates implementation evidence from external product evidence. Internal unit tests, synthetic workloads and configured integration matrices cannot prove user savings, agent quality, competitor superiority, live certification or public maturity.

## Receipt source

Every claim-bearing run must preserve the provider's raw usage object by hash and normalize it into `schemas/provider-usage-receipt-v1.json`.

Required fields include:

- provider, model and request identity;
- session and repository identity hashes;
- integration/host identity;
- provider input, cached-input and output tokens;
- provider cost;
- end-to-end wall-time;
- quality score and task success;
- workload, arm, task and repetition identity;
- synthetic/live flag;
- raw provider usage hash.

Missing or malformed fields fail closed.

## Pairing rule

A pair key is:

```text
(repository_hash, task_id, repetition, provider, model)
```

A valid pair contains exactly one baseline run and one SignalCore run under the same:

- repository tree;
- task definition;
- provider and model;
- reasoning mode;
- tool permissions;
- verifier;
- timeout and retry policy;
- environment class.

Competitor arms must be isolated and may not share SignalCore caches, summaries, memory or receipts.

## Primary measurements

For each run:

```text
billable_input = input_tokens - cached_input_tokens
total_tokens   = billable_input + output_tokens
```

For each pair:

```text
token_ratio = signalcore_total_tokens / baseline_total_tokens
wall_ratio  = signalcore_wall_time / baseline_wall_time
cost_ratio  = signalcore_cost / baseline_cost
quality_delta = signalcore_quality - baseline_quality
success_delta = signalcore_success - baseline_success
```

The committed gate requires quality non-inferiority and success non-inferiority. Token or cost reduction is not accepted when result quality materially declines.

## Coding-agent workload requirements

The minimum claim review set is:

- 30 valid pairs;
- 5 repositories;
- 10 tasks;
- 3 workload families.

Recommended external suites include real repository bug fixing, repository navigation, test repair, issue triage, long-session implementation and SWE-bench-compatible tasks. The repository does not claim a SWE-bench score until a public or reproducible external run is attached.

## Long-context quality requirements

The OOLONG-like long-context receipt model uses paired baseline/SignalCore runs across:

- needle retrieval;
- temporal supersession;
- multi-hop evidence;
- repository history;
- cross-session continuity;
- recursive map/reduce.

The quality gate requires:

- 30 valid pairs;
- 10 cases;
- 4 task families;
- 32K, 128K and 1M virtual-history tiers;
- mean required-fact recall of at least 0.98;
- mean stale-fact rejection of at least 0.98;
- mean evidence precision of at least 0.95;
- quality non-inferiority within 0.01;
- exact recovery;
- no forced restart;
- continuity restoration for continuity tasks;
- no synthetic receipts.

## Competitor protocol

The same receipt schema and verifier must be used for baseline, SignalCore, Token Savior, Context Mode, Headroom and Volt/LCM arms. A missing competitor executable, missing usage receipt, changed model, changed repository, failed verifier or hidden retry is included as a failure—not silently removed.

## Live integration certification

An integration is live-certified only when a sanitized external receipt identifies:

- integration and version;
- operating system and runtime version;
- installation path;
- successful request/response lifecycle;
- provider usage capture;
- tool-routing enforcement where applicable;
- session-continuity behavior where applicable;
- test timestamp and environment hash.

Contract tests remain `internal-contract`; they are not `VERIFIED_LIVE`.

## Public adoption and maturity

Package downloads, unique users, repositories and operational days must come from external distribution or opted-in telemetry sources. Repository-generated fixtures cannot count toward adoption.

SignalCore's public maturity gate remains closed until the configured external thresholds are met. Version remains **0.0.1 pre-release** regardless of internal feature count.

## Commands

```bash
signalcore prove plan
signalcore prove schema
signalcore prove receipts receipts.json
signalcore prove benchmark receipts.json
signalcore prove long-context long-context-receipts.json
python benchmarks/measured_agent_benchmark.py receipts.json --output result.json
```
