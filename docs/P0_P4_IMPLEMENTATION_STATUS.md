# P0–P4 Runtime Consolidation Status

## Implemented in this branch

- Canonical repository graph facade.
- Default Tree-sitter syntax adapter for supported non-Python languages.
- SQLite FTS5 repository query engine with bounded SQL fallback.
- Canonical terminal engine with exact disk spooling, streaming summaries and a never-worse guard.
- Deterministic, bounded-memory streaming table routing.
- Deep nested host-output capture rather than raw pass-through.
- Process environment inheritance repair.
- Project-aware verifier discovery.
- OpenAI-compatible, Anthropic and Gemini model gateway contracts.
- Model-backed bounded repository tool loop with search, inspect, impact, diff and verifier actions.
- Exact structured-edit compilation into `git apply` compatible patches.
- Current-diff handoff on every repair attempt.
- Event journal suitable for JSONL, TUI and dashboard transports.
- Explicit delivery modes: diff, retained worktree, apply, commit and draft pull request.
- Product `syntavra agent run` command and explicit `agent replay` compatibility path.

## Safety boundaries

- Source-repository apply, commit and pull-request delivery require explicit authorization.
- A delivery cannot run unless discovered verification passed.
- PR delivery requires an authenticated `git push` and `gh` CLI session.
- Structured edits are bounded, path-confined and exact-precondition checked.
- Model-selected verifier commands must come from project discovery; arbitrary model shell commands are not executed.

## Evidence boundary

These changes are internally verified implementation work. They do not prove external benchmark superiority, live certification for every listed provider/host, or complete semantic precision for every language. LSP/LSIF/SCIP receipts remain required before cross-file semantic facts are labelled exact. Live provider and PR-delivery certification remains receipt-gated.
