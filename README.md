# SignalCore 0.2.0 — Runtime Intelligence Plane

SignalCore is a local-first runtime control plane and Agent Skill for AI coding agents. It reduces repeated retrieval, model-mediated process polling, raw tool-output injection, context churn and redundant verification while keeping correctness, exact evidence and security gates above token savings.

> **Public efficiency claim: `5X_NOT_PROVEN`.** SignalCore 0.2.0 implements observed 20X/30X/100X workload qualification and fail-closed paired-claim receipts, but no live identical-model, identical-task, identical-provider quota corpus is committed. Internal implementation benchmarks are not competitor superiority evidence.

## What changed in 0.2.0

### Structural intelligence

- Incremental content-hash index for Python, JavaScript/TypeScript, Rust, Go, Java, C/C++, C#, Ruby, PHP and Luau/Lua source.
- Python AST definitions, qualified symbols, imports and calls.
- Conservative lexical adapters for other languages.
- Transitive reverse-call impact traversal instead of direct-only callers.
- Personalized graph ranking, affected paths and affected-test discovery.
- Changed-path-to-impact analysis for verifier selection.

### Runtime enforcement

- Durable SQLite/WAL process broker with project-scoped workers.
- Background submission returns `JOB_ACCEPTED` once; completion is consumed by an SQLite sequence cursor.
- Hook engine blocks destructive commands and rewrites long-running commands through the zero-poll broker where host hooks exist.
- Dependency-free MCP stdio server exposes status, process submission/completions, impact inspection and context pressure.
- Host negotiation distinguishes `HOOK_ENFORCED`, `MCP_CONTROLLED`, `PROXY_CONTROLLED`, `INSTRUCTION_ONLY` and `UNSUPPORTED`.

### Exact output and context control

- Full stdout/stderr is streamed into content-addressed exact evidence without loading the complete log into memory.
- Bounded output firewall preserves final summaries, error locations and recovery handles while suppressing repeated success noise.
- Dependency-aware context packer enforces mandatory task/evidence/impact roles and emits a deterministic stable prefix.
- Context pressure combines utilization, churn and evidence pressure before controlled handoff.

### Long-session continuity

- Project/user-scoped FTS5 memory with confidence, provenance, tags, expiry, supersession and weighted relations.
- Immutable hash-chained history.
- Recursive summary DAG compacts arbitrary event counts to one canonical root and expands back to exact source events.
- Rollout tailer handles partial JSONL, rotation/truncation and duplicate events; cached input is no longer miscounted as fresh input.

### Benchmark governance

- Workload configuration and observed difficulty are separate facts.
- A claim-bearing run requires observed workload axes, at least 10 valid paired repetitions, identical model/reasoning/prompt/tree/verifier/permissions/timeout/cache, actual quota telemetry, equal verified work, no required-verifier skips and no security regression.
- The median, geometric mean and 95% bootstrap confidence-interval lower bound must all be at least 5.00X.
- Configured difficulty alone can never pass a 5X claim.

## Runtime pipeline

```text
truthful host negotiation
→ structural + exact + scoped-memory retrieval
→ mandatory context packing and stable prefix
→ hook/MCP-controlled durable process broker
→ streaming exact-evidence output firewall
→ rollout telemetry + immutable summary DAG
→ verifier dependency binding
→ observed paired benchmark + fail-closed claim receipt
```

## CLI

```bash
python -m signalcore_runtime --project . --host codex status
python -m signalcore_runtime --project . init "repair authentication regressions"
python -m signalcore_runtime --project . run --background -- python -m unittest discover -s tests -q
python -m signalcore_runtime --project . job completions --after 0
python -m signalcore_runtime --project . inspect impact parse_header --max-depth 6
python -m signalcore_runtime --project . inspect paths src/auth.py tests/test_auth.py
python -m signalcore_runtime --project . memory add decision "Use SQLite WAL" --tag runtime
python -m signalcore_runtime --project . hook pre --payload '{"tool":"shell","command":["pytest","-q"]}'
python -m signalcore_runtime --project . mcp serve
python -m signalcore_runtime benchmark generate-config --tier 20X --output benchmark-20x.json
```

The legacy `context --used 60 --window 100` form remains supported. The explicit form is:

```bash
python -m signalcore_runtime context evaluate --used 60 --window 100
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
python benchmarks/runtime_v02_benchmark.py --output benchmarks/results/runtime-v02/internal.json
python tools/validate_release.py --profile 5x --smoke --output release-smoke.json
```

CI runs the combined suite on Ubuntu, Windows and macOS with Python 3.11, 3.12 and 3.13.

## Internal benchmark boundary

`benchmarks/runtime_v02_benchmark.py` compares earlier direct-only/full-read internal paths with the new transitive/streaming implementations. In the committed 350,000-line run, transitive affected-path recall rises from **2.47% to 100%** across an 81-file chain, while measured peak Python allocation for output processing falls from **30,005,283 bytes to 27,818 bytes**. The firewall preserves the final test summary, stores exact recoverable evidence and emits a 268-byte visible result. The benchmark also proves one-root exact recovery for 256 history events and mandatory context-role satisfaction.

These figures are internal implementation deltas, not an external competitor benchmark. The baseline timing does not persist exact evidence, so timing values are diagnostic rather than an apples-to-apples speed claim. It does **not** compare SignalCore with Token Savior, Context Mode, Headroom, Volt, Aider or Caveman Code.

## Roblox Studio profile

The existing Roblox Studio orchestration profile remains hidden and fail-closed. It requires an authorized bridge, a signed short-lived single-use envelope and process attestation. Existing internal/simulated Roblox results do not establish live Studio execution, generic provider savings or competitor superiority.

| Capability | Governed maturity | Boundary |
|---|---|---|
| Signed activation [claim:roblox.activation] | **INTERNALLY_VERIFIED** | Process attestation is exercised through the injected test contract. |
| TaskState V2 [claim:roblox.task_state] | **INTERNALLY_VERIFIED** | Deterministic schema and migration behavior are covered by profile tests. |
| Capability graph [claim:roblox.capabilities] | **INTERNALLY_VERIFIED** | 33 records include planner, execution and validation metadata. |
| Simulated orchestration [claim:roblox.simulated] | **SIMULATED** | The committed 50-case artifact uses simulated external engines. |
| Transcript adapter [claim:roblox.transcript] | **IMPLEMENTED** | Contract exists; no sanitized real transcript is claimed. |
| Live Studio bridge [claim:roblox.live] | **PLANNED** | Live execution remains disabled. |
| DataStore migration execution [claim:roblox.datastore] | **PLANNED** | External validation boundary only. |
| Asset, animation and Blender engines [claim:roblox.external_engines] | **PLANNED** | External engine contracts only. |
| Profile test suite [claim:roblox.tests] | **INTERNALLY_VERIFIED** | 119 tests are bound to the governed artifact registry. |

## Status

- Version: **0.2.0 pre-release**
- Runtime implementation: **directly tested**
- Internal implementation benchmark: **available with raw JSON artifact**
- Generic 5X superiority: **not proven**
- Independent reproduction: **pending**
