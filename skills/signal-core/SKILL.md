---
name: signal-core
version: "0.1.0"
description: >
  Run complex coding-agent work through SignalCore's verified runtime: durable
  zero-poll jobs, bounded exact-preserving outputs, structural impact analysis,
  persistent memory, evidence provenance, context governance, verifier binding,
  and normalized usage telemetry. Skip trivial requests where runtime overhead
  exceeds likely savings.
compatibility: "Codex, Claude Code, Gemini CLI, Antigravity, Windsurf, OpenCode, VS Code Copilot, Agent Skills hosts, MCP clients, and rule/AGENTS.md bridges."
metadata:
  author: Naveax
  status: pre-release
---

# SignalCore

SignalCore is a local-first runtime-control plane and Agent Skill. Token reduction is subordinate to correctness, exact evidence, security boundaries and required verification.

## Activation contract

1. Determine the actual host capability and report `INSTRUCTION_ONLY` when enforcement is unavailable.
2. Initialize the project-scoped state, evidence store, process broker and rollout telemetry.
3. Prefer exact and structural retrieval before semantic or broad scans.
4. Run long commands through the durable broker; model-mediated process polling is forbidden.
5. Store full tool output by hash and inject only bounded, critical-marker-preserving summaries.
6. Bind cached verifier results to the repository tree, environment, dependencies and toolchain.
7. Externalize repeated evidence and prepare a controlled handoff before emergency compaction.
8. Do not claim 5X, competitor or market superiority without a passing paired claim receipt.

## Runtime entry point

```bash
python -m signalcore_runtime --project . --host codex status
```

Use `python -m signalcore_runtime --help` for the complete command surface.

## Host compatibility

Use native Agent Skill discovery when available. Otherwise use the generated rule or `AGENTS.md` bridge. The maintained compatibility matrix is at [`../../COMPATIBILITY.md`](../../COMPATIBILITY.md).

## Locked domain profiles

The `roblox_studio` profile remains hidden and fail-closed. It requires an authorized bridge, a signed short-lived single-use envelope and process attestation. Ordinary CLI, IDE and prompt-based activation remains rejected.

## Version

Public pre-release version: **0.1.0**.
