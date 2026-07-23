# Syntavra 0.0.1 — Pre-Release Token & Context Optimization Skill

Syntavra is a local-first **Agent Skill and runtime middleware** that reduces the token and context overhead of existing AI coding agents. It does not replace Codex, Claude Code, Cursor, OpenCode or another agent/model.

Syntavra optimizes five cost surfaces while preserving exact recovery:

| Surface | What Syntavra does |
|---|---|
| Repository context | Retrieves exact definitions, impact paths, tests and verifiers before broad file reads |
| Tool output | Stores full stdout/stderr as exact evidence and returns bounded task-relevant views |
| MCP schemas | Exposes a small profile and deterministically compiles verbose discovery schemas |
| Session memory | Keeps active context bounded while exact external history remains searchable and recoverable |
| Measurement | Separates provider-observed usage from locally tokenized or estimated attribution |

> The only active product identity is **0.0.1 / pre-release**. The owner explicitly authorizes any version or release-channel change. External superiority, live certification, long-context quality, adoption and production maturity remain evidence-gated.

## Install

The registry package is prepared but not published yet:

```bash
npx @syntavra/install
```

Until registry publication, use the repository installer:

```bash
npx github:Naveax/Syntavra
```

The installer prefers checksum-verified portable binaries, falls back to Python 3.11+, configures detected hosts transactionally and runs a final health check.

## Product surface

```bash
syntavra setup                 # plan installation
syntavra setup --apply         # install and configure detected hosts
syntavra setup --repair --apply
syntavra status                # health, profile, evidence and observed savings
syntavra status --savings
syntavra status --doctor
syntavra run manifest
syntavra prove plan
```

Normal daily work still happens in the existing coding agent. Syntavra is intended to activate through its skill, MCP integration and host hooks rather than requiring every command to be prefixed manually.

## MCP profiles

| Profile | Purpose | Maximum public surface |
|---|---|---:|
| `minimal` | Default hot-loop token saver | 8 tools |
| `balanced` | Repository context, output, memory and provider controls | 36 tools |
| `audit` | Full inspection and administration | Entire installed catalog |

`tiny`, `optimized` and `full` remain compatibility aliases only. The installed profile, listed tools, callable tools and benchmark profile are derived from the same canonical registry.

## Competitive feature set

Syntavra 0.0.1 now includes a fail-closed pre-tool command rewriter, 70 command-specific compactors, six instant optimization modes, a live savings statusline, prompt-cache layout/expiry planning, transcript opportunity mining, incremental repository watching, local browser/PWA dashboard, agent-config auditing, secret redaction, lossless MCP wire encoding, hybrid memory search, deep code-graph analytics, adaptive provider fallback, short-handoff delegation, a VS Code extension, and a dependency-free Rust companion source.

```bash
syntavra run mode ultra
syntavra run statusline
syntavra run rewrite -- git status
syntavra run dashboard --open
syntavra run code-intel report
syntavra run memory-search "cache decision"
syntavra run provider-route "security migration" providers.json
```

The implementation registry currently covers at least 60 rewrite/compaction surfaces, more than 30 controlled host contracts, and more than 40 provider presets. Installation contracts are not described as live certifications. Registry publication and provider-billed competitor results remain external, credential-gated actions. See `docs/COMPLETE_COMPETITIVE_FEATURE_SET_001.md`.

## Exact-recovery rule

Syntavra does not treat deletion as compression:

```text
full repository/tool/session evidence
  -> content-addressed local artifact
  -> bounded task-relevant view
  -> recovery handle
  -> exact reveal/verification when required
```

Correctness, verifier success and security boundaries outrank nominal token reduction.

## Measurement rule

The primary optimization metric is:

> **provider-observed cost per verified successful task**

Raw byte reduction is reported separately. Source-level attribution identifies schema, repository, tool-output, memory, conversation and output tokens with an explicit confidence level:

- `PROVIDER_OBSERVED`
- `LOCALLY_TOKENIZED`
- `ESTIMATED`
- `UNKNOWN`

Synthetic fixtures and internal component tests cannot open public superiority claims.

## Primary certification targets

The first live-certification targets are:

1. Codex
2. Claude Code
3. Cursor

Other adapters remain contract-tested or declared bridges until external execution receipts prove live behavior.

## Benchmarking

SignalBench compares independent external arms under the same frozen repository, prompt, model, reasoning mode, context window, verifier, permissions, timeout, cache policy and hardware class. Templates include plain host, Caveman, RTK, Token Savior, repository-context tools, a combined competitor pack and Syntavra minimal/balanced arms.

Missing competitors, provider usage or verifier output fail closed; they are never replaced with synthetic results.

## Canonical documentation

- `docs/001_PRE_RELEASE.md`
- `docs/ARCHITECTURE.md`
- `docs/TOKEN_SAVER_PLAN_001.md`
- `docs/SECURITY_MODEL.md`
- `docs/ADAPTER_PLATFORM.md`
- `docs/SIGNALBENCH.md`
- `docs/OPERATIONS.md`

## Current claim boundary

```text
EXTERNAL_SUPERIORITY_NOT_PROVEN
LONG_CONTEXT_QUALITY_NOT_PROVEN
MEASURED_AGENT_BENCHMARK_NOT_PROVEN
LIVE_INTEGRATION_CERTIFICATION_NOT_PROVEN
DAILY_CODING_AGENT_READINESS_NOT_PROVEN
PUBLIC_PRODUCT_MATURITY_NOT_PROVEN
```

<!--
Internal and simulated Roblox Studio evidence registry markers. These markers identify
claim records only; they do not upgrade any claim to live, public, or independent proof.
[claim:roblox.activation]
[claim:roblox.task_state]
[claim:roblox.capabilities]
[claim:roblox.simulated]
[claim:roblox.transcript]
[claim:roblox.live]
[claim:roblox.datastore]
[claim:roblox.external_engines]
[claim:roblox.tests]
-->
