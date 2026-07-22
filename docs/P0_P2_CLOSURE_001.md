# Syntavra 0.0.1 Pre-Release — P0–P2 Closure Record

This record applies to the unified `main` product line through the `agent/v001-unified-hardening` integration branch. Syntavra remains **0.0.1 / pre-release**.

## P0 — Repository, CI and installation

Implemented in the integration branch:

- one-command installer package: `npx @syntavra/install`;
- repository fallback before registry publication: `npx github:Naveax/Syntavra`;
- Python 3.11+ detection, argv-only installation, detected-host setup and final status verification;
- deterministic root and TypeScript npm lockfiles;
- real installer and TypeScript SDK tests;
- manifest verification without GitHub Actions writing directly to `main`;
- CodeQL, dependency review and Dependabot configuration;
- SBOM, checksums, reproducibility and build-provenance workflows;
- contribution, support, security, issue and pull-request policies;
- version checks covering Python, installer, TypeScript, skills, extensions and release metadata.

## P1 — Product surface and integration quality

The unified runtime contains:

- four-command onboarding: `setup`, `status`, `run`, `prove`;
- provider proxy planning and user-service lifecycle;
- Python and TypeScript client surfaces;
- bounded MCP profiles with per-call enforcement;
- host-specific adapter detection and transactional setup;
- exact session history, asynchronous compaction and continuity receipts;
- content-free local metrics and analytics;
- provider usage, external benchmark, live-integration and maturity receipt validation.

## P2 — Evidence infrastructure

The repository contains fail-closed contracts for:

- paired real coding-agent token, cost, wall-time, success and quality measurement;
- SWE-bench and repository-task receipts;
- OOLONG-like long-context quality and continuity evaluation;
- real competitor arms under pinned and equivalent conditions;
- live provider, framework and coding-agent integration certification;
- onboarding population, package adoption and 90-day operational maturity.

## Historical red commits

The red icons on historical commits represent intermediate snapshots that were pushed before the full change set or manifest refresh was complete. Rewriting public history to make those historical snapshots appear green would reduce auditability.

The corrective integration strategy is:

1. use one branch based on the latest `main`;
2. collect all P0–P2 hardening changes in one pull request;
3. validate the final pull-request SHA with all required checks;
4. squash-merge the validated state into `main`;
5. treat that merged SHA as the supported 0.0.1 pre-release state.

## External gates that remain open

The following cannot be closed by repository code or synthetic fixtures:

```text
EXTERNAL_SUPERIORITY_NOT_PROVEN
LONG_CONTEXT_QUALITY_NOT_PROVEN
MEASURED_AGENT_BENCHMARK_NOT_PROVEN
LIVE_INTEGRATION_CERTIFICATION_NOT_PROVEN
DAILY_CODING_AGENT_READINESS_NOT_PROVEN
PUBLIC_PRODUCT_MATURITY_NOT_PROVEN
```

They require real provider receipts, public or independently reproducible harnesses, live installations, real users and elapsed operating history. Their validators are implemented, but no result is claimed until valid evidence is collected.
