# SignalCore 0.3.0 — Unified Agent Runtime

SignalCore is a local-first control plane for AI coding agents. Version 0.3.0 unifies structural repository intelligence, native host enforcement, secure command execution, reversible context compression, durable long-session state, concise answer contracts and an external-arm public benchmark protocol.

> **Public efficiency claim: `5X_NOT_PROVEN`.** The implementation and fail-closed comparison machinery exist, but this repository does not yet contain a live identical-model, identical-task, identical-provider quota corpus with ten valid paired repetitions. Internal component results are not competitor-superiority evidence.

## Unified capabilities

### Structural navigation

- Incremental content-hash index for Python, JavaScript/TypeScript, Rust, Go, Java, C/C++, C#, Ruby, PHP and Lua/Luau.
- Python semantic AST plus language-specific parsers and optional LSP/compiler semantic snapshots.
- Qualified symbols, definitions, calls, imports, inheritance, implementation, overrides, reads, writes and instantiation edges.
- Transitive impact traversal, personalized ranking, affected-test discovery, verifier suggestions and token-budgeted repository maps.

### Host enforcement and one-command installation

- Native capability registry for Codex, Claude Code, Gemini CLI, OpenCode, Cursor, Windsurf, VS Code/Copilot, Cline, Roo Code, Continue, Qwen Code, Antigravity, generic MCP and Aider bridges.
- Backup-first, idempotent installation with configuration deep merge, host detection, doctor, dry-run and reversible uninstall.
- Lifecycle handling for session start/end, prompt submission, pre/post tool use, pre/post compact and stop events.
- Long-running commands route through the durable zero-poll process broker; untrusted commands can route through the sandbox plane.

### Secure sandbox plane

- Docker, Podman, bubblewrap and explicitly degraded local-restricted backends.
- Network, filesystem, CPU, memory, PID, timeout, environment and writable-path policies.
- Secret filtering, project-boundary validation, process-tree cleanup, exact output evidence and bounded model-visible summaries.
- Strict requests fail closed when the selected backend cannot provide the promised isolation.

### Reversible universal compression

- Content routing for JSON, JSONL, CSV/tables, code, diffs, logs, stack traces, XML/HTML, retrieval results and general text.
- Every compressed view includes a project-scoped exact evidence handle and chunk index.
- Full and chunk restoration are byte-exact and receipt-verified.
- Visible summaries redact secrets and preserve critical errors while raw source remains recoverable.

### Long-session runtime

- Project-scoped immutable hash-chained events in SQLite/WAL.
- Recursive summary DAG that reduces arbitrary histories to one canonical root while retaining exact leaf expansion.
- Active-context assembly, checkpoints, fork, merge, export/import, corruption quarantine, recovery and background compaction.

### Output governance

- `compact`, `balanced`, `detailed` and `audit` profiles.
- Typed implementation, failure, audit and benchmark contracts.
- Filler and duplicate removal while preserving errors, security limitations, file locations and evidence handles.

### SignalBench

- Runs baseline, SignalCore and competitor products as independent external adapter processes; competitor code is not imported.
- Freezes task, repository tree, model, reasoning, context window, verifier, permissions, timeout and cache mode.
- Records raw artifacts, provider usage, quota, model/tool/wait calls, verifier result and security regressions.
- Failed work receives zero credit. Public superiority requires at least ten valid paired runs and a 95% bootstrap confidence-interval lower bound above 1.0. The separate 5X gate remains stricter.

## Quick start

```bash
python -m signalcore_runtime --project . doctor
python -m signalcore_runtime --project . install --all --dry-run
python -m signalcore_runtime --project . install --host-name codex
python -m signalcore_runtime --project . --host codex status
```

Representative commands:

```bash
signalcore inspect map "authentication failure" --token-budget 2000
signalcore sandbox plan --network none -- pytest -q
signalcore compress put build.log --budget 4096
signalcore session open --metadata '{"goal":"release audit"}'
signalcore output govern --profile compact --contract implementation --payload result.json
signalcore signalbench validate --tasks tasks.json --arms arms.json
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
python benchmarks/runtime_v03_benchmark.py --output benchmarks/results/runtime-v03/internal.json
python tools/validate_release.py --profile 5x --smoke --output release-smoke.json
```

CI runs the combined suite on Ubuntu, Windows and macOS with Python 3.11, 3.12 and 3.13.

## Internal benchmark boundary

The committed v0.3 internal benchmark exercises ten language fixtures, transitive repository mapping, reversible compression, session recovery, installer idempotency, sandbox policy planning, output contracts and SignalBench protocol validation. In the committed smoke artifact, the synthetic repeated log is represented as a 321-byte visible view from 480,030 source bytes and restores byte-for-byte. These figures measure SignalCore components only and do not compare SignalCore with Token Savior, Context Mode, Headroom, Volt, Aider or Caveman Code.

## Roblox Studio profile

The existing Roblox Studio orchestration profile remains hidden and fail-closed. Its simulated/internal results do not establish live Studio execution, generic provider savings or competitor superiority.

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

- Version: **0.3.0**
- Main integration: **MERGED**
- Unified implementation: **directly tested**
- Cross-platform release validation: **PASS** on Ubuntu, Windows and macOS with Python 3.11, 3.12 and 3.13
- Public efficiency claim: **`5X_NOT_PROVEN`**


## Unified Production Core 0.6.0

SignalCore 0.6.0 unifies encrypted exact evidence, authenticated fail-closed
provider streaming, valid typed data envelopes, configuration provenance,
transactional migrations, structured observability, retention, backup,
policy rollout, durable scheduling and permissioned plugins behind one
canonical runtime pipeline. See
`docs/architecture/UNIFIED_PRODUCTION_CORE_V6.md`.
