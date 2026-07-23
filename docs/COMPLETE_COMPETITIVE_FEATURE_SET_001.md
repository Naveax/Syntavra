# Syntavra 0.0.1 — Complete Competitive Feature Set

Status: **implemented and internally gated**. Release channel: **pre-release**.

This document records the competitive feature expansion without converting implementation evidence into external superiority, adoption, or publication claims.

## Execution-time optimization

- Fail-closed `PreToolUse` command rewriting with at least 60 deterministic rules.
- At least 60 command-specific exact-preserving compactors; the current registry exposes 70.
- Destructive-command policy is evaluated against both the original and rewritten command.
- User-selected output formats, shell composition, redirection, and ambiguous commands disable rewriting rather than guessing.
- Optimization modes: `full`, `lite`, `ultra`, `commit`, `review`, and `compress`.
- Live local statusline and source-attributed savings ledger.

```bash
syntavra run mode ultra
syntavra run statusline
syntavra run rewrite -- git status
syntavra run transcript-mine transcript.jsonl
```

## Prompt-cache optimization

- Stable/volatile message segmentation.
- Safe stable-prefix normalization for system/developer messages.
- Provider-specific prompt-cache controls and TTL planning.
- Refresh and expiry scheduling.
- Cache write/read amortization and break-even reporting.
- Provider gateway plans expose cacheable/volatile token estimates and refresh boundaries.

Tool/assistant messages are never reordered automatically because their sequence carries execution semantics.

## Repository intelligence

- Portable polling watcher and incremental reindex worker.
- Python AST plus generic multi-language symbol index.
- Call and class hierarchy.
- Dead-code and untested-symbol candidates.
- PageRank importance.
- Git churn and complexity hotspots.
- Dependency cycles, coupling, instability, and inferred module boundaries.
- Signal-chain tracing and duplicate-symbol discovery.
- Symbol provenance and PR-risk reports.
- Delete-safe preflight and refactoring plans.
- Cross-language anti-pattern scanning and cross-repository contract discovery.

```bash
syntavra run watch --iterations 1
syntavra run worker run --iterations 1
syntavra run code-intel report
syntavra run code-intel call --query helper
syntavra run code-intel risk --paths src/a.py src/b.py
```

## Memory intelligence

- Heuristic extraction and optional external LLM extraction command.
- Observation validity, importance, confidence, reuse, outcome, and ROI ranking.
- Local hashed embeddings combined with BM25 and cosine reranking.
- Background embedding backfill.
- Critical observation notification feed.
- Exact JSONL export.

No remote model is silently invoked. LLM extraction activates only through an explicitly configured external command.

## Security and wire efficiency

- Recursive redaction for provider keys, authorization headers, cloud credentials, JWTs, private keys, credential-bearing URIs, and high-entropy secrets.
- Exact provider/tool evidence is stored before bounded presentation.
- Lossless compact MCP wire encoding with integrity hash and minimum-savings gate.
- The original object is recoverable byte-for-byte after canonical decoding.

## Routing and delegation

- 48 provider gateway presets.
- Quota, rate-limit, price, latency, quality, context-window, and task-complexity-aware ranking.
- Ordered fallback chain.
- Automatic decomposition into capability-specialized short-handoff subtasks.
- Bounded subtask outputs and explicit dependency edges.

Provider presets are installation contracts, not live provider certifications.

## Product surfaces

- Local browser dashboard and installable PWA surface.
- VS Code extension with status badge, mode switcher, dashboard launcher, and save-triggered reindex.
- Dependency-free Rust companion source and three-OS build workflow.
- More than 30 controlled host installation contracts; current registry has 44 host entries.
- Discord, Telegram, and local JSONL notification channels.
- npm, PyPI/uvx, VS Code Marketplace, and native artifact publication manifests.

## Benchmark boundary

SignalBench accepts provider-observed input, cached-input, output, reasoning, quota-cost, model, request, and receipt hashes. A competitor superiority claim is refused unless:

1. baseline and candidate perform equal verified work;
2. every repetition has real provider-observed receipts;
3. no verifier is skipped;
4. no security regression occurs;
5. all declared competitor arms complete;
6. the configured paired confidence gate passes.

The repository does **not** claim that registry publication or provider-billed external benchmark execution has occurred. Those actions require owner credentials, paid provider access, and retained external receipts.

```text
EXTERNAL_SUPERIORITY_NOT_PROVEN
LIVE_INTEGRATION_CERTIFICATION_NOT_PROVEN
PUBLIC_PRODUCT_MATURITY_NOT_PROVEN
REGISTRY_PUBLICATION_NOT_PERFORMED
```
