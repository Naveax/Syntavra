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

Syntavra augments the active coding agent; it is not a replacement agent. Correctness and exact evidence outrank token reduction. Run `syntavra status --doctor` before relying on enforcement.

## Runtime invariants

- Resolve the active project from the host working directory or an explicit project argument, never from Syntavra's installation directory.
- Keep runtime, installer and analytics state outside the target repository unless the user explicitly supplies an in-repository `--state-root`.
- Prefer exact task-scoped repository context, keep full tool output in local evidence, preserve recovery handles and use the smallest sufficient MCP profile.

## Codex MCP-controlled execution contract

Codex supports MCP and background jobs but does not provide Syntavra pre-tool hooks or host result replacement. For long-running build, test, lint or analysis commands, use `syntavra.process.submit` when the current user request authorizes command execution and include `_syntavra_authorization: {"user_authorized": true, "exact_evidence": true}`. After `JOB_ACCEPTED`, do not execute the original command again; read `syntavra.process.completions` instead of shell-polling.

If `syntavra.fabric.route` returns a non-empty `replacement_argv`, use that replacement and do not silently fall back to the original command. If it returns `blocked`, stop. If repository retrieval unexpectedly returns Syntavra's own `syntavra_runtime/`, `skills/`, `tests/` or `tools/` tree for a different target repository, treat the result as a project-binding fault and do not consume it as task context.

## Savings claim boundary

Local tool-output or schema reductions are model-visible context estimates, not proof of provider-billed or whole-session savings. Net token, cost or latency superiority requires paired provider-observed baseline/candidate receipts under equivalent workload, cache, model/environment and verifier conditions. Without that evidence, report `LOCAL_MODEL_VISIBLE_ESTIMATE` rather than a net savings claim.

## Competitive runtime controls

- Use `syntavra run mode <full|lite|ultra|commit|review|compress>` for an explicit session mode.
- Apply pre-tool rewrites only when the rewriter returns `safe=true`; preserve explicit user formatting and reject shell composition.
- Keep exact output before secret redaction, compaction, or lossless wire encoding.
- Use the cache plan refresh/expiry boundary rather than assuming a provider cache hit.
- Use repository watcher and code-intelligence results as candidate evidence; require exact source before editing or deletion.
- Treat provider presets, host contracts, registry manifests, and benchmark templates as unverified until external receipts exist.
