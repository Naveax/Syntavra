---
name: syntavra
version: "0.0.1"
description: >
  Reduce token and context overhead for existing AI coding agents through exact
  repository retrieval, bounded MCP schemas, reversible tool-output externalization,
  progressive session memory, cache-stable requests and provider-usage receipts.
compatibility: "Codex, Claude Code, Gemini CLI, Antigravity, Windsurf, OpenCode, VS Code Copilot, MCP clients, Agent Skills hosts, and rule/AGENTS.md bridges."
metadata:
  author: Naveax
  role: token-context-optimization-skill
  status: pre-release
  stability: pre-alpha
  version_locked: true
---

# Syntavra

Syntavra is a local-first token/context optimization Agent Skill and runtime middleware. It augments the current coding agent; it is not a replacement agent or model. Correctness, exact evidence, security boundaries and required verification outrank token reduction.

## Activation contract

1. Negotiate real host capabilities; never describe instruction-only behavior as runtime enforcement.
2. Freeze the canonical `minimal`, `balanced` or `audit` profile for the session.
3. Retrieve exact definitions, transitive impact, tests and verifiers before broad repository reads.
4. Pack mandatory evidence before likely and optional context; keep omitted paths recoverable.
5. Route long commands through the durable broker rather than spending model turns polling.
6. Save complete tool output as exact local evidence and inject only bounded views with recovery handles.
7. Keep active model context bounded while exact external session history remains searchable.
8. Treat MCP schema, repository, tool-output, memory and conversation tokens as separate cost sources.
9. Distinguish provider-observed, locally tokenized, estimated and unknown measurements.
10. Reuse verification only when repository tree, environment, dependencies, toolchain and affected paths match.
11. Reject efficiency, maturity or competitor-superiority claims without real external receipts and a passing gate.
12. Keep the version at 0.0.1 and the release channel pre-release until the owner explicitly changes them.

## Runtime entry points

```text
syntavra setup
syntavra status
syntavra run
syntavra prove
```

Use `syntavra status --doctor` before relying on runtime enforcement and `syntavra status --savings` for source-level token attribution.

## Competitive runtime controls

- Use `syntavra run mode <full|lite|ultra|commit|review|compress>` for an explicit session mode.
- Apply pre-tool rewrites only when the rewriter returns `safe=true`; preserve explicit user formatting and reject shell composition.
- Keep exact output before secret redaction, compaction, or lossless wire encoding.
- Use the cache plan refresh/expiry boundary rather than assuming a provider cache hit.
- Use repository watcher and code-intelligence results as candidate evidence; require exact source before editing or deletion.
- Treat provider presets, host contracts, registry manifests, and benchmark templates as unverified until external receipts exist.
