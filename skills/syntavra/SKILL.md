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
3. Resolve the active project from the host working directory or an explicit project argument. Never infer the target repository from Syntavra's installation directory.
4. Keep Syntavra runtime, installer and analytics state outside the target repository unless the user explicitly supplies `--state-root` inside it.
5. Retrieve exact definitions, transitive impact, tests and verifiers before broad repository reads.
6. Pack mandatory evidence before likely and optional context; keep omitted paths recoverable.
7. Route long commands through the durable broker rather than spending model turns polling.
8. Save complete tool output as exact local evidence and inject only bounded views with recovery handles.
9. Keep active model context bounded while exact external session history remains searchable.
10. Treat MCP schema, repository, tool-output, memory and conversation tokens as separate cost sources.
11. Distinguish provider-observed, locally tokenized, estimated and unknown measurements.
12. Reuse verification only when repository tree, environment, dependencies, toolchain and affected paths match.
13. Reject efficiency, maturity or competitor-superiority claims without real external receipts and a passing gate.
14. Keep the version at 0.0.1 and the release channel pre-release until the owner explicitly changes them.

## Codex MCP-controlled execution contract

Codex has MCP, session-event and background-job integration, but no Syntavra pre-tool hook or host result-replacement authority. Therefore routing must be explicit rather than pretending an unavailable hook exists.

- At the start of repository work, call `syntavra.status` and compare `details.project_root` with the active workspace's canonical repository root. If they differ, fail closed: do not call Syntavra repository-retrieval or process-execution tools until the integration is repaired/restarted for the correct workspace.
- For a long-running build, test, lint or analysis command, use `syntavra.process.submit` instead of running the original command directly when the current user request authorizes command execution. Include `_syntavra_authorization: {"user_authorized": true, "exact_evidence": true}` in that MCP call.
- After `JOB_ACCEPTED`, do not execute the original command a second time. Read completion events through `syntavra.process.completions`; do not spend model turns shell-polling the process.
- If `syntavra.fabric.route` returns a non-empty `replacement_argv`, that replacement is the selected execution path. Do not silently ignore it and then run the original command.
- If routing returns `blocked`, stop that command. If the selected replacement cannot be used, report the optimization route as unavailable instead of claiming it was enforced.
- Before trusting repository retrieval, verify that returned paths belong to the task repository. If a task for repository A returns Syntavra's own `syntavra_runtime/`, `skills/`, `tests/` or `tools/` tree unexpectedly, treat that as a project-binding fault and do not consume the map as task context.

## Savings claim boundary

- Tool-output bytes/tokens removed from the model-visible view are local context savings, not proof of provider-billed or whole-session savings.
- Schema compilation savings are reported separately and must not be added to provider usage unless provider receipts prove the effect.
- Net token, cost or latency superiority requires paired baseline/candidate provider-observed receipts under the same workload, cache mode, model/environment boundary and verification gate.
- A local savings ledger without paired provider receipts may be described only as `LOCAL_MODEL_VISIBLE_ESTIMATE`.

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