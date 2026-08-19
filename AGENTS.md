# Syntavra repository instructions

Use `skills/syntavra/SKILL.md` for complex coding-agent work involving repository exploration, debugging, impact analysis, large outputs, long sessions, tool overload, or token/cost analysis.

- Preserve correctness, exact evidence, and a runnable verifier before optimizing token count.
- Keep the canonical skill host-independent; platform differences belong in `data/platforms.json` and `scripts/platforms.py`.
- Do not duplicate platform-specific copies inside this repository. Generate or install them through `tools/install.py`.
- Do not claim universal or Token Savior superiority without paired provider benchmarks.
- Run `python tools/validate.py` before publishing changes.

<!-- naveax-ci-execution-policy:v1 -->
# Agent Execution and CI Policy

The following rules are mandatory in addition to the Syntavra-specific instructions above.

- Before any workflow dispatch/rerun/retry, inspect active runs and deduplicate by repository + workflow + ref + HEAD SHA + normalized inputs.
- If an equivalent run is queued, waiting, pending, requested, or in progress, do not create another run. Track/poll the existing run ID. Never rerun as a polling mechanism.
- Same SHA + workflow + inputs has an automatic dispatch budget of 1. A second execution requires a concrete runner/infrastructure/flaky-dependency reason. Prefer rerunning only failed jobs.
- Never make empty/no-op commits merely to retrigger CI. If dispatch rate rises unexpectedly, stop new dispatches and diagnose the loop.
- CI is asynchronous. Maintain RUNNING/READY/BLOCKED/DONE work states. When CI blocks one task, switch to another independent READY task instead of idling or launching duplicate CI.
- Normal scheduler target: up to 10 active independent workstreams and up to 50 queued READY work items, without duplicating work.
- Only the coordinating workstream may authorize Actions dispatches. Parallel workers report validation needs to the coordinator.
- After a failure, collect complete evidence, determine root cause, make one coherent patch, then start at most one validation run for the new commit.
- When adding/editing workflows, preserve semantics and add top-level `concurrency` when absent. For ordinary branch-scoped validation prefer workflow + ref grouping with `cancel-in-progress: false` unless replacement behavior is explicitly intended.

Goal: bounded CI concurrency, no duplicate validation for one logical target, and continuous useful progress while external jobs run.
