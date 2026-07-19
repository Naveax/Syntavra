# SignalCore 0.1.0 — Fusion Runtime

SignalCore is a local-first runtime-control plane for AI coding agents. It reduces context, tool-output, polling, repeated retrieval and verification cost without treating lower token count as more important than correctness.

> **Current public efficiency claim: `5X_NOT_PROVEN`.** The 20X/30X/100X workload generator, paired-run validation and tamper-evident claim gate are implemented. No live identical-model, identical-task provider/quota corpus is committed yet, so SignalCore does not claim five-times superiority over competitors.

## Runtime pipeline

```text
truthful bootstrap and host negotiation
→ structural, memory and exact-evidence retrieval
→ stable-prefix context governor
→ durable zero-poll process broker
→ exact-preserving output firewall
→ incremental rollout telemetry and immutable history
→ verifier dependency graph
→ paired benchmark and claim receipts
```

## Implemented runtime capabilities

- Explicit activation states: `NOT_INSTALLED`, `INSTRUCTION_ONLY`, `RUNTIME_PARTIAL`, `RUNTIME_ACTIVE`, `RUNTIME_DEGRADED`, `RUNTIME_FAILED`.
- Durable SQLite/WAL job state and completion queue.
- Long-running command execution without model-mediated status polling.
- stdout/stderr streamed to disk rather than accumulated in model context.
- Content-addressed exact evidence handles with integrity verification.
- Bounded command summaries with secret redaction and critical-error preservation.
- Incremental rollout JSONL tailing with partial-line, rotation and duplicate handling.
- Content-hash-based structural indexing, symbol lookup and impact inspection.
- Scoped SQLite/FTS5 memory with provenance, deduplication and supersession.
- Immutable hash-chained event history and exact-source summary expansion.
- Context thresholds, stable-prefix hashing and controlled handoff actions.
- Verifier results bound to tree, environment, dependency and toolchain identities.
- Anti-gaming 1X/20X/30X/100X workload configuration and fail-closed 5X claim receipts.

## CLI

```bash
python -m signalcore_runtime --project . --host codex init "repair the failing authentication tests"
python -m signalcore_runtime --project . --host codex status
python -m signalcore_runtime --project . run --background -- python -m unittest discover -s tests -q
python -m signalcore_runtime --project . job list
python -m signalcore_runtime --project . inspect impact parse_header
python -m signalcore_runtime --project . memory add decision "Use SQLite WAL"
python -m signalcore_runtime benchmark generate-config --tier 20X --output benchmark-20x.json
```

## Validation

```bash
python -m compileall -q signalcore_runtime skills/signal-core tools tests benchmarks
python -m unittest discover -s tests/runtime -q
python -m unittest tests.test_platforms tests.test_roblox_profile_gate -q
python -m unittest discover -s tests/roblox_profile -q
python tools/validate.py
python tools/validate_runtime.py
python tools/validate_roblox_profile.py
python tools/verify_claims.py
python tools/validate_release.py --profile 5x --smoke --output fusion-release-smoke.json
```

GitHub CI runs the combined suite on Ubuntu, Windows and macOS with Python 3.11, 3.12 and 3.13.

## Benchmark boundary

Workload difficulty and performance are separate facts. A valid 20X/30X/100X configuration proves only that the workload construction satisfies multi-axis anti-gaming rules. A 5X claim additionally requires identical model, reasoning, prompt, repository, permissions, verifier and cache conditions; valid paired repetitions; actual quota telemetry; no quality/security regression; and a 95% bootstrap confidence-interval lower bound of at least 5.00X.

## Roblox Studio profile

The existing Roblox Studio orchestration profile remains hidden and fail-closed. Internal profile tests and simulated orchestration artifacts do not establish live Studio execution, generic provider savings or competitor superiority.

## Project status

- Version: **0.1.0 pre-release**
- Runtime machinery: **implemented and directly tested**
- Generic 5X superiority: **not proven**
- Independent reproduction: **pending**
