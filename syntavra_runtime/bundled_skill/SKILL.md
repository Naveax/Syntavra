---
name: syntavra
version: "0.0.1"
description: >
  Reduce coding-agent token and context overhead with exact repository retrieval,
  bounded MCP schemas, reversible tool-output views and progressive session memory.
compatibility: "Codex, Claude Code, Gemini CLI, OpenCode, Cursor, Windsurf, Copilot, Cline, Roo Code, Continue, Qwen Code, Antigravity and MCP clients."
metadata:
  author: Naveax
  role: token-context-optimization-skill
  distribution: bundled-runtime
  status: pre-release
  version_locked: true
---

# Syntavra bundled skill

Syntavra augments the active coding agent; it is not a replacement agent. Run `syntavra status --doctor` before relying on enforcement. Prefer exact task-scoped repository context, keep full tool output in local evidence, preserve recovery handles, use the smallest sufficient MCP profile, keep active context bounded and never claim savings without provider-observed usage plus verifier success.

## Competitive runtime controls

- Use `syntavra run mode <full|lite|ultra|commit|review|compress>` for an explicit session mode.
- Apply pre-tool rewrites only when the rewriter returns `safe=true`; preserve explicit user formatting and reject shell composition.
- Keep exact output before secret redaction, compaction, or lossless wire encoding.
- Use the cache plan refresh/expiry boundary rather than assuming a provider cache hit.
- Use repository watcher and code-intelligence results as candidate evidence; require exact source before editing or deletion.
- Treat provider presets, host contracts, registry manifests, and benchmark templates as unverified until external receipts exist.
