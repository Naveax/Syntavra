# SignalCore v0.0.1 Pre-Release — Daily Agent Product Architecture

## Status and claim boundary

SignalCore remains locked to **0.0.1 / pre-release**. This document describes implemented architecture and proof gates. It does not claim external superiority, public adoption, live provider certification, SWE-bench success, OOLONG success, or production maturity without external receipts.

## Product model

The public mental model has four top-level operations:

1. `setup` — detect, plan, install, repair and write reversible receipts.
2. `status` — report health, onboarding time, integrations, sessions, token/cost telemetry and unresolved proof gates.
3. `run` — execute through policy-enforced proxy, tool-routing and session-continuity surfaces.
4. `prove` — validate provider receipts, paired agent tasks and long-context quality.

Historical commands remain compatibility surfaces; they are not the primary onboarding model.

## Control-plane decomposition

```text
coding-agent / framework / SDK
             |
             v
    platform adapter + MCP profile
             |
             v
       tool routing enforcement
             |
      +------+-------+
      |              |
      v              v
provider proxy   exact session runtime
      |              |
      v              v
usage receipts   summary DAG + exact history
      |              |
      +------+-------+
             v
       analytics + proof gates
```

The proxy keeps provider credentials transport-only. Remote bindings require TLS; control endpoints require an independent control token. Streaming uses commit-before-forward semantics so response bytes are scanned and committed before client delivery.

## Long-context model

SignalCore does **not** enlarge a provider's physical context window. It implements an exact external history with a bounded active model window.

Let the append-only event history at turn `t` be:

```text
H_t = (e_1, e_2, ..., e_t)
```

Each event commits to the preceding event:

```text
h_i = SHA256(session_id || i || type_i || payload_i || h_(i-1) || time_i)
```

This provides sequence, integrity and continuity verification. Exact history remains external to the model-visible prompt.

### Recursive summary DAG

Events are divided into deterministic leaf ranges of at most `L` events. A leaf summary is:

```text
s_(0,k) = Reduce(e_(kL+1) ... e_((k+1)L))
```

Higher levels recursively reduce at most `F` child summaries:

```text
s_(d,k) = Reduce(s_(d-1,kF) ... s_(d-1,(k+1)F-1))
```

Every summary records its source interval, child identifiers and source hash. A summary is therefore a navigation object, not a replacement for exact evidence.

For `N` events, summary depth is `O(log_F N)`. Exact storage is `O(N)`. A full compaction pass is `O(N)`; subsequent no-change compactions return the existing root. Retrieval remains budget-bounded.

### Active context selection

For query `q` and token budget `B`, the active context is selected from:

```text
C(q,t) = CurrentTruth(q,H_t)
       U RelevantEvidence(q,H_t)
       U RecentEvents(H_t)
       U SummaryPath(q,H_t)
```

subject to:

```text
EstimatedTokens(C(q,t)) <= B
```

Temporal supersession must prefer the newest valid fact while retaining exact references to superseded facts. No selected summary may remove the ability to restore its source interval.

### Asynchronous compaction

Foreground event appends are transactional and do not wait for the background compactor. The product compactor:

- scans only active sessions;
- compacts sessions above a configured event threshold;
- records wall-time and failures per cycle;
- never mutates the append-only event chain;
- fails independently without blocking foreground writes;
- exposes the latest cycle through session analytics.

A continuity receipt requires:

- an intact event hash chain;
- an active context within budget;
- exact expansion of the selected root summary;
- no forced restart;
- measured continuity wall-time.

## Recursive execution evidence

Recursive workers use bounded fan-out and retry limits. Each worker output must include:

- task identity;
- input evidence references;
- output evidence references;
- verifier result;
- parent/child provenance;
- duplicate-suppression identity;
- token and wall-time accounting when a provider is used.

A recursive paradigm claim is not opened by architecture alone. It requires external task receipts showing quality retention or improvement against an identical baseline.

## MCP profiles

The installed `mcp-profile.json` is the runtime default. An explicit `SIGNALCORE_MCP_PROFILE` environment value can override it for a process. Product profiles are applied after every extension has registered its tools, so extensions cannot silently enlarge the active tool surface.

| Profile | Enforced active tools | Description budget | Default use |
|---|---:|---:|---|
| `minimal` | exactly 8 | 700 tokens | Daily coding-agent hot loop |
| `balanced` | exactly 36 | 4,000 tokens | Repository work, provider receipts and sessions |
| `audit` | complete registered catalog, capped at 128 | 16,000 tokens | Evidence, migration, release and security review |

Legacy names remain compatibility aliases: `tiny → minimal`, `optimized → balanced`, and `full → audit`.

`tools/list` is not considered an authorization boundary. Every JSON-RPC `tools/call` is checked again against the active profile. A client cannot call an unlisted tool by guessing its name.

- Unknown or unlisted tools fail closed.
- State-writing tools require exact-evidence mode.
- Destructive, network and execution tools require exact evidence plus explicit user authorization.
- Sandbox execution additionally requires the sandbox route.
- Unsandboxed process submission remains disabled unless a separate operator override is present.
- Every accepted or denied call receives a route-receipt hash and is counted in content-free session analytics.

## Platform adapters

The adapter registry maps eighteen coding-agent hosts to concrete command detection and candidate configuration paths. `setup` only mutates hosts that are actually detected unless `--all` is explicitly used. Each mutation is backup-first, transactional and verified; a failed batch rolls back completed host transactions.

Registry validation proves contract coverage only. A host becomes live-certified only after an external execution receipt is recorded for that host/version combination.

## Provider and framework surfaces

SignalCore exposes:

- a credential-isolated provider proxy;
- a dependency-free Python client with sync and async transports;
- a typed TypeScript client with SSE parsing and receipt helpers;
- duck-typed OpenAI, Anthropic, Gemini and LiteLLM transports;
- LangChain callback and generic middleware surfaces;
- provider presets for ten declared provider families;
- user-scoped systemd, launchd and Windows Task Scheduler service lifecycle plans.

Providers requiring SigV4, OAuth2 or non-compatible request translation are explicitly marked adapter-required rather than presented as zero-code proxy support.

## Observability

The default analytics stream is local and content-free. It stores identifiers, counters, hashes, timing, cost, quality, continuity and routing decisions—not prompt or response bodies.

Required user-facing metrics include:

- input, cached-input, billable-input and output tokens;
- provider cost;
- request and compaction wall-time;
- session/repository counts;
- continuity restores;
- denied tool routes;
- onboarding and host-installation wall-time;
- host verification results;
- proof-gate reasons.

## External proof requirements

### Measured coding-agent benchmark

The gate requires at least:

- 30 baseline/SignalCore pairs;
- 5 repositories;
- 10 tasks;
- 3 workload families;
- identical provider and model within each pair;
- provider token, cost and wall-time receipts;
- quality non-inferiority within 0.01;
- success non-inferiority within 0.02;
- no synthetic receipts.

### Long-context quality benchmark

The OOLONG-like protocol measures:

- answer quality;
- required-fact recall;
- stale-fact rejection;
- evidence precision;
- exact recovery;
- forced restart;
- session continuity;
- provider tokens;
- wall-time.

The required tiers include 32K, 128K and 1M virtual-history tokens. The architecture also supports committed stress plans through 10M virtual-history tokens, but stress planning alone is not quality evidence.

### Product maturity

The external maturity gate measures onboarding success and p95 installation time, rollback verification, live integration diversity, operating-system coverage, public package downloads, unique installations, signed/provenance-bearing pre-release cadence and operational age.

User count, public package adoption, operational history, installation success and live integration certification cannot be generated by repository tests.

## Release policy

All package, CLI, extension, skill and artifact metadata remains **0.0.1** and **pre-release** until the repository owner explicitly authorizes a version change.
