# Syntavra 0.0.1 — Competitive Gap Reassessment 002

Status: **all executable technical actions implemented; external evidence actions remain fail-closed**.

## Scope

This reassessment follows the final remediation of CodeQL alert #6 (`py/redos`). It compares the current Syntavra technical surface against Token Savior, jCodeMunch, RTK, 9Router, Claude-specific cache tools, Caveman, and Cavemem. It separates implementation gaps from evidence, credential, adoption, and time-dependent gaps.

## P0 — Security closure

1. Keep agent-configuration path discovery free of nested quantified regular expressions.
2. Make the scanner linear-time and bounded-space for untrusted million-character lines.
3. Reject oversized path-like tokens before slicing or splitting them.
4. Export every open, fixed, and dismissed CodeQL alert through paginated API traversal.
5. Require default-branch CodeQL analysis to report alert #6 as `fixed`, rule `py/redos`, at `syntavra_runtime/agent_config_auditor.py`.
6. Emit a machine-readable closure receipt linked to the analyzed main commit.

**Implementation:** bounded manual scanner, dedicated adversarial regression tests, dynamic security-alert export, eventual-consistency polling, and a main-only closure gate.

## P1 — Token Savior and RTK execution parity

Observed competitor advantages were command breadth, transparent pre-tool rewriting, transcript-derived coverage, and low-overhead output compaction.

Actions applied:

- Maintain at least 120 command-specific compactors.
- Maintain at least 110 fail-closed rewrite rules.
- Parse safe wrappers and environment assignments.
- Reject ambiguous shell composition and wrapper options.
- Preserve exact stdout and stderr before compact presentation.
- Measure rewrite and compactor coverage from transcripts.

**Current internal gate:** 131 compactors and 118 rewrite rules, exact recovery, and wrapper-aware fail-closed behavior.

## P2 — jCodeMunch repository-intelligence depth

Observed competitor advantages were parser fidelity, language breadth, implementation resolution, blast-radius analysis, and incremental indexing.

Actions applied:

- Maintain a broad language registry with an optional real tree-sitter backend.
- Attach parser backend and confidence to indexed symbols.
- Expose implementation discovery and blast-radius queries.
- Cache per-file parsing and reuse unchanged results.
- Label lexical fallback explicitly instead of presenting it as AST-exact.

**Current internal gate:** at least 25 declared languages, incremental cache reuse, implementation discovery, and blast-radius coverage.

## P3 — 9Router provider operation

Observed competitor advantages were account rotation, subscription priority, quota reset tracking, rate-limit fallback, and health-aware routing.

Actions applied:

- Store credential references only; reject raw provider secrets.
- Track subscription, priority, quota reset, rate limit, latency, health, model allowlists, and circuit-breaker state.
- Route to healthy backup accounts after repeated failure.
- Expose account-pool controls through CLI and MCP audit surfaces.

**Current internal gate:** deterministic account failover and raw-secret rejection. Live OAuth certification remains external.

## P4 — Claude-specific cache and session depth

Observed competitor advantages were native hook coverage, cache-expiry guidance, statusline visibility, and session restoration.

Actions applied:

- Cover `PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `PreCompact`, `SessionStart`, `Stop`, and `SessionEnd`.
- Surface cache health and recommended action at prompt and session boundaries.
- Keep operation multi-host rather than binding the runtime to Claude only.
- Expose the Syntavra statusline through native host configuration.

## P5 — Product evidence and distribution

The remaining areas where competitors are stronger are not missing code features:

1. Published provider-billed benchmark results.
2. Independent third-party reproduction.
3. Public package-registry availability.
4. Live OAuth and provider-account certification.
5. Long-running user adoption and operational maturity.

These cannot be converted into implementation claims. They require external credentials, provider budget, third-party participation, publication authorization, and elapsed production usage.

The repository therefore retains these gates:

```text
REGISTRY_PUBLICATION_NOT_PERFORMED
PROVIDER_BILLED_SIGNALBENCH_NOT_EXECUTED
INDEPENDENT_VALIDATION_NOT_AVAILABLE
LIVE_PROVIDER_CERTIFICATION_NOT_AVAILABLE
PUBLIC_PRODUCT_MATURITY_NOT_PROVEN
```

## Three-round verification protocol

### Round 1 — Focused security verification

- CodeQL regression tests.
- Million-character adversarial scanner input.
- Competitive-gap validator.
- Security workflow syntax and manifest integrity.

### Round 2 — Full pull-request verification

- Repository, runtime, release, and deterministic-manifest validators.
- Complete Python test matrix.
- npm installer, TypeScript SDK, VS Code extension, Python distribution, and native Rust checks.
- Pull-request CodeQL analysis with no new alert.

### Round 3 — Final-main verification

- Squash or clean promotion to `main`.
- Push-triggered CodeQL analysis on the exact final commit.
- Security-triage workflow confirms alert #6 is `fixed`.
- Closure artifact contains the alert number, rule, fixed timestamp, analyzed commit, and location.
- Final main CI and branch inventory are clean.

## Completion boundary

Technical completion requires all three verification rounds and the alert #6 closure receipt. External superiority remains unproven until provider-billed and independently reproducible evidence exists.
