# Syntavra 0.0.1 — Complete Competitive Feature Set

Status: **implemented and internally gated**. Release channel: **pre-release**.

This document records technical implementation evidence. It does not convert internal validation into external superiority, adoption, registry-publication, live-provider, or production-maturity claims.

## Execution-time optimization

- 118 fail-closed `PreToolUse` rewrite rules.
- 131 command-specific compactors with exact-output recovery.
- Safe wrapper handling for environment assignments, `env`, bare `sudo`, bare `time`, and `command`; ambiguous wrapper options fail closed.
- Destructive-command policy is evaluated against both original and rewritten commands.
- User-selected output formats, shell composition, redirection, and ambiguous commands disable rewriting rather than guessing.
- Optimization modes: `full`, `lite`, `ultra`, `commit`, `review`, and `compress`; `codex-ultra` selects the real `ultra` profile with a 1,500-token task-context ceiling.
- Live local statusline, source-attributed savings ledger, and transcript opportunity mining.

```bash
syntavra run mode codex-ultra
syntavra run statusline
syntavra run rewrite -- env CI=1 git status
syntavra run transcript-mine transcript.jsonl
```

## Prompt-cache and Claude lifecycle

- Stable/volatile message segmentation and stable-prefix planning.
- Refresh, expiry, amortization, and break-even reporting.
- Cache health/action data in session-start and prompt-submit hooks.
- Claude lifecycle contracts for `PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `PreCompact`, `SessionStart`, `Stop`, and `SessionEnd`.
- Tool/assistant messages are not reordered automatically because sequence carries execution semantics.

## Repository intelligence

- Portable watcher, context compiler, and code-intelligence analytics share one deterministic SQLite `StructuralIndex`; the duplicate JSON graph cache is retired.
- Path-scoped incremental updates hash and reparse only changed supported files, remove deleted rows, and resolve graph edges in the same database.
- Natural-language task text is deterministically mapped to symbol seeds, and emitted context packs are hard-capped at 1,500 estimated tokens.
- Python AST backend with confidence `1.0`.
- Optional `tree-sitter-language-pack` backend with confidence `0.9`.
- Explicit deterministic lexical fallback with confidence `0.45`; fallback output is not labelled exact AST.
- A 30-language registry spanning 50+ source suffixes.
- Call/class hierarchy, implementation discovery, blast radius, dead-code and untested-symbol candidates.
- PageRank, hotspots, cycles, coupling, inferred module boundaries, signal chains, duplicates, provenance, PR risk, delete-safe preflight, refactor plans, anti-patterns, and cross-repository contracts.

```bash
syntavra run watch --iterations 1
syntavra run worker run --iterations 1
syntavra run code-intel parser-manifest
syntavra run code-intel implementations --query Base
syntavra run code-intel blast-radius --query helper
```

## Provider accounts, routing and delegation

- Persistent multi-account provider pool.
- Only `env:`, `file:`, `keyring:`, and `oauth-profile:` credential references are persisted; raw credentials are rejected.
- Subscription preference, explicit account priority, model allowlists, quota resets, rate-limit state, latency EWMA, health ratio, and circuit-breaker failover.
- Adaptive model selection by task complexity, context window, quality, price, quota, latency, and account availability.
- Automatic capability-specialized short-handoff subtasks with bounded output.

```bash
syntavra run provider-pool add openai primary env:OPENAI_API_KEY --subscription --priority 10
syntavra run provider-pool list
syntavra run provider-pool route providers.json "security migration root cause"
```

Provider presets and credential references are installation/runtime contracts, not proof that an external account was connected or certified.

## Memory, security and wire efficiency

- Hybrid BM25/cosine memory, explicit LLM-or-heuristic extraction, ROI/validity ranking, embedding backfill, notifications, and JSONL export.
- Recursive redaction for authorization material, cloud credentials, JWTs, private keys, credential-bearing URIs, and high-entropy secrets.
- Linear-time agent-config path scanning; the former nested-quantifier expression identified by CodeQL alert #6 was removed.
- Lossless compact MCP wire encoding with integrity hash and minimum-savings gate.
- Exact provider/tool evidence is stored before bounded presentation.

## Product surfaces

- Local browser dashboard and installable PWA.
- VS Code extension with status badge, mode switcher, dashboard launcher, and save-triggered reindex.
- Dependency-free Rust companion source and three-OS build workflow.
- 44 controlled host contracts and 48 provider presets.
- npm, PyPI/uvx, VS Code Marketplace, and native publication workflows are prepared but not claimed as published.

## Benchmark and claim boundary

SignalBench requires equal verified work, real provider-observed receipts, no skipped verifier, no security regression, all declared competitor arms, and the configured paired confidence gate. Missing external execution cannot be replaced with synthetic evidence.

```text
EXTERNAL_SUPERIORITY_NOT_PROVEN
MEASURED_AGENT_BENCHMARK_NOT_PROVEN
LIVE_INTEGRATION_CERTIFICATION_NOT_PROVEN
PUBLIC_PRODUCT_MATURITY_NOT_PROVEN
REGISTRY_PUBLICATION_NOT_PERFORMED
```
