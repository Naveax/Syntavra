# SignalCore 0.0.1

**Cross-platform Context Intelligence and runtime-optimization layer for AI coding agents.**

SignalCore targets OpenAI Codex, Claude Code, Gemini CLI, Google Antigravity, Windsurf, OpenCode, VS Code/GitHub Copilot, Cursor, Cline, Continue, Junie, Agent Skills-compatible hosts, and universal Markdown/Python bridges.

It is designed to reduce model-facing context and tool-output waste **without sacrificing exact evidence, verifier coverage, security boundaries, or repairability**.

> **Proof policy:** SignalCore publishes raw internal results, test scope, seeds, latency, and limitations. It does not claim OpenAI, Anthropic, Token Savior, Aider, Context Mode, or market-wide superiority until identical-model public provider benchmarks and independent reproduction pass.

## Measured internal results

Benchmarked build: **Context Intelligence Extreme v2 + Runtime Optimizer v3**  
Release identity: `SHA-256 141b6b34fabb25de2d1d308386a15a43e1fca1712c7e315e1101ff4bd5604d79`

| Benchmark | Cases | Internal v1 baseline | Runtime v3 result | Difference |
|---|---:|---:|---:|---:|
| Hard exact/stale/adversarial evidence | 600 | 0% complete; 1,037 tokens | **100% complete; 174 tokens** | **83.23% less selected context** |
| Exact-free semantic multi-hop | 120 | 11.67% complete; 1,586 tokens | **100% complete; 77 tokens** | **95.15% less selected context** |
| Runtime tool-output compaction | 400 | Raw output | **86.02% mean estimated reduction** | **100% injected-marker preservation** |
| Capsule-only controlled repairs | 4 | ~5,231 repository tokens | **~139 tokens; 4/4 verifier PASS** | **97.35% less context** |

### Hard benchmark details

- six isolated 100-case processes
- exact-error preservation: **100%**
- stale-selection rate: **100% → 0%**
- mean selected context: **1,037.38 → 173.92 tokens**
- Runtime v3 local p95: **146.43 ms**

### Semantic benchmark details

The semantic suite removes exact markers, file names, symbol names, role metadata, and direct error codes.

- complete rate: **11.67% → 100%**
- role recall: **71.25% → 100%**
- mean selected context: **1,586.28 → 77 tokens**
- Runtime v3 local p95: **86.25 ms**

### Runtime-output details

| Output family | Cases | Mean estimated reduction | Critical-marker preservation |
|---|---:|---:|---:|
| Pytest | 100 | **72.70%** | **100%** |
| Git | 100 | **75.46%** | **100%** |
| Logs | 100 | **96.58%** | **100%** |
| JSON | 100 | **99.33%** | **100%** |
| **Combined** | **400** | **86.02%** | **100%** |

Combined local processing p95: **1.90 ms**.

### Package gates

- **1,200 / 1,200 tests PASS**
- **19 / 19 release-validator gates PASS**
- **103 manifest entries verified**
- Python compilation: **PASS**
- secret scan: **PASS**
- locked Roblox Studio activation gate: **PASS**

Read the complete methodology and limitations in [BENCHMARKS.md](BENCHMARKS.md).

Raw artifacts:

- [`hard-600.json`](benchmarks/results/runtime-v3/hard-600.json)
- [`semantic-120.json`](benchmarks/results/runtime-v3/semantic-120.json)
- [`runtime-output-400.json`](benchmarks/results/runtime-v3/runtime-output-400.json)
- [`capsule-repair-4.json`](benchmarks/results/runtime-v3/capsule-repair-4.json)
- [`release.json`](benchmarks/results/runtime-v3/release.json)

> These are controlled internal engineering benchmarks against SignalCore's own v1 baseline. They are not SWE-bench, BEIR, API-billing guarantees, or independent competitor comparisons. The complete Runtime v3 source candidate may be ahead of the current `main` source tree until the full source merge tracked in [issue #1](https://github.com/Naveax/SignalCore/issues/1) is completed.

## What makes SignalCore different

SignalCore is not only a “respond more briefly” prompt.

| Capability | Minimal prompt/rule | Single-purpose compactor | SignalCore target architecture |
|---|---:|---:|---:|
| Query-aware repository evidence | No | Limited | **Yes** |
| Exact evidence preservation | No guarantee | Output-dependent | **Hash and recovery-handle based** |
| Stale/current contradiction handling | No | No | **Yes** |
| Semantic multi-hop evidence recovery | No | No | **Yes** |
| Tool-output compaction | Limited | **Yes** | **Yes** |
| Hard token and cost budgets | No | No | **Yes** |
| Capability-aware model routing | No | No | **Yes** |
| Project decision and rejected-approach memory | No | No | **Yes** |
| Context firewall and untrusted-content quarantine | No | Limited | **Yes** |
| Bounded subagent return capsules | No | No | **Yes** |
| Validation-gated self-optimization | No | No | **Yes** |
| Cross-platform delivery | Usually one host | Usually one hook | **22 platform records** |

## Core architecture

```text
Task understanding
→ deterministic query planning
→ hierarchical document / section / symbol indexing
→ corpus-aware strategy selection
→ lexical + semantic retrieval
→ Reciprocal Rank Fusion
→ bounded late interaction
→ evidence and dependency graph
→ stale / contradiction / trust analysis
→ role-specific evidence recovery
→ exact-preserving compression
→ constrained context budget allocation
→ source fusion
→ model-aware ordering
→ sufficiency simulation
→ content-addressed cache and memory
→ runtime output compaction
→ verifier and telemetry
```

## Why SignalCore

- **Success before savings:** correctness, exact evidence, and a runnable verifier outrank lower token count.
- **Measured context selection:** publishes raw benchmark artifacts instead of unsupported product percentages.
- **Runtime output control:** compacts test, Git, JSON, and log output while retaining critical markers.
- **Progressive disclosure:** complex workflows activate only when their expected value exceeds overhead.
- **Cross-platform:** one canonical behavior layer with native and bridge-based delivery adapters.
- **Local-first:** Python 3.11+ standard library and SQLite WAL; no hosted service required.
- **Exact evidence:** content-addressed storage, hashes, bounded reads, and recovery handles.
- **Project memory:** decisions, supersession, rejected approaches, and commit validity.
- **Fail-closed domain profiles:** restricted profiles cannot be activated by ordinary prompts.
- **Non-destructive installation:** adapters preserve existing instruction and rule content.

## Compatibility

### Native Agent Skill targets

- OpenAI Codex
- Claude Code
- Gemini CLI
- Google Antigravity IDE and CLI
- Windsurf Cascade
- OpenCode
- VS Code / GitHub Copilot
- hosts implementing the Agent Skills standard through `.agents/skills/`

### Rule and instruction bridges

- Cursor
- Cline
- Continue
- JetBrains Junie and integrated agents
- Roo Code, Aider, Zed, Kiro, Qwen Code, Kimi CLI, Goose, and other Markdown/AGENTS.md-capable agents through the universal bridge

See [COMPATIBILITY.md](COMPATIBILITY.md) for exact support levels, installation paths, invocation syntax, and limitations.

## Locked Roblox Studio domain profile

SignalCore includes a hidden `roblox_studio` profile foundation that cannot be activated from a normal CLI, IDE prompt, or Agent Skill invocation.

Activation requires:

- a short-lived signed envelope from an authorized Roblox Studio bridge
- live Studio process attestation
- project identity
- an explicit capability subset
- a single-use nonce

See [ROBLOX_STUDIO_MODE.md](ROBLOX_STUDIO_MODE.md).

## Install

### Install every verified native project target

```bash
python tools/install.py install --platforms all-native --scope project --project .
```

### Install selected platforms

```bash
python tools/install.py install \
  --platforms codex,claude-code,gemini-cli,antigravity,antigravity-cli,windsurf,opencode,vscode-copilot \
  --scope project --project .
```

### Install rule and instruction bridges

```bash
python tools/install.py install \
  --platforms cursor,cline,continue,junie,generic-agents-md \
  --scope project --project .
```

### User-wide installation

```bash
python tools/install.py install \
  --platforms codex,claude-code,opencode,windsurf \
  --scope user
```

### Detect and inspect

```bash
python tools/install.py list
python tools/install.py detect --project .
python tools/install.py install --platforms all-verified --scope project --dry-run
python tools/install.py status --platforms all-verified --scope project
```

### Remove SignalCore-managed adapters

```bash
python tools/install.py uninstall \
  --platforms cursor,cline,continue,junie \
  --scope project
```

## Platform-specific distribution

### Claude Code marketplace

```text
/plugin marketplace add Naveax/SignalCore
/plugin install signal-core@signalcore
```

### Gemini CLI extension

```bash
gemini extensions install https://github.com/Naveax/SignalCore
```

### Windsurf

Install to `.windsurf/skills/signal-core`, then invoke with `@signal-core` or let Cascade activate it.

### Cursor

The installer creates `.cursor/rules/signal-core.mdc`. Cursor currently uses a rule bridge rather than SignalCore claiming native Agent Skill discovery.

## Quick checks

```bash
python tools/validate.py
python -m unittest discover -s tests -q
python -m compileall -q skills/signal-core/scripts skills/signal-core/profiles tools
python skills/signal-core/scripts/routing.py \
  "Find the exact root cause, callers, impact boundary, and narrow verifier"
```

## Search and machine discovery

SignalCore includes:

- `llms.txt` for AI/search ingestion
- `codemeta.json` for software metadata
- `AGENTS.md` for cross-agent repository instructions
- `.claude-plugin/marketplace.json` for Claude Code
- `gemini-extension.json` for Gemini CLI
- `.github/copilot-instructions.md` for VS Code/GitHub Copilot
- explicit keywords covering Agent Skills, Codex, Claude Code, Gemini CLI, Antigravity, Windsurf, OpenCode, Cursor, Cline, Continue, context engineering, token optimization, evidence selection, model routing, runtime output compression, and coding-agent memory

## Design rules

1. Correctness and verifier coverage outrank token reduction.
2. Exact and security evidence is never silently summarized.
3. Large evidence is stored by hash and retrieved through bounded handles.
4. SQLite state uses WAL, migrations, bounded transactions, and no pickle/eval.
5. Provider usage is normalized before efficiency comparisons.
6. Platform adapters remain thin; canonical behavior stays in one core.
7. Internal benchmarks are published with scope and limitations.
8. Forecasts and synthetic tests are not market-dominance proof.
9. Third-party licenses and attribution may not be concealed.

## Status

- Version: **0.0.1**
- Stage: **pre-release / Context Intelligence Runtime candidate**
- Runtime: Python 3.11+ standard library
- External engines: optional; not vendored
- Public provider superiority: **not yet established**
- Independent reproduction: **pending**
- Repository: `Naveax/SignalCore`
