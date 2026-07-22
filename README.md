# SignalCore v0.0.1 — Pre-Release Coding-Agent Runtime

SignalCore is a local-first control plane for coding agents. It combines a credential-isolated provider proxy, bounded active context, exact external session history, enforced MCP routing, platform adapters, Python/TypeScript libraries and receipt-based benchmarking.

> **Version lock:** every active product and package identity remains **0.0.1 / pre-release** until the repository owner explicitly authorizes a version change.
>
> **Claim boundary:** external superiority, public adoption, live certification, SWE-bench performance, OOLONG performance and production maturity are not proven without valid external receipts.

## Install in one command

### Public pre-release package

```bash
npx @signalcore/install
```

The npm package is configured for the `next` distribution tag. Until it is published, the same installer can be executed directly from this repository:

```bash
npx github:Naveax/SignalCore
```

Inspect the exact executable and argument plan without changing the machine:

```bash
npx github:Naveax/SignalCore -- --plan
```

The installer:

- detects Python 3.11 or newer;
- installs SignalCore from the selected Git ref;
- applies the `minimal` MCP profile to detected coding-agent hosts;
- runs `signalcore status`;
- uses argv-only process execution rather than shell strings;
- never accepts provider credentials;
- preserves the 0.0.1 pre-release identity.

Manual repository installation remains available:

```bash
git clone https://github.com/Naveax/SignalCore.git
cd SignalCore
python -m pip install -e .
signalcore setup --apply --mcp-profile minimal
signalcore status
```

## Four-command product surface

```bash
signalcore setup   # detect, plan, install or repair
signalcore status  # health, sessions, metrics and proof gates
signalcore run     # proxy, routing and session operations
signalcore prove   # receipt and benchmark validation
```

Legacy commands remain available for compatibility, but these four operations are the public mental model.

`setup` is backup-first, transactional and measured. It changes only detected hosts unless `--all` is explicitly supplied. Every mutation is verified and recorded; a later failure in the same batch triggers rollback.

## Daily workflow

```bash
signalcore run manifest

signalcore run route repo.search
signalcore run route terminal.exec
signalcore run route terminal.exec --sandboxed --user-authorized

signalcore run proxy-plan openai
signalcore run proxy-service install openai --apply --activate
signalcore run proxy-service verify openai

signalcore run session-open --session-id my-session --metadata '{"goal":"repair repository"}'
signalcore run session-append my-session decision '{"decision":"run focused tests"}'
signalcore run session-compact my-session
signalcore run session-continuity my-session
```

## MCP profiles

| Profile | Enforced tools | Intended use |
|---|---:|---|
| `minimal` | exactly 8 | Daily coding-agent hot loop |
| `balanced` | exactly 36 | Repository work, sessions and provider telemetry |
| `audit` | complete registered catalog | Evidence, migration, release and security review |

Filtering `tools/list` is not a security boundary. Every `tools/call` is checked again. Unlisted tools fail closed. Destructive, network and execution operations require authorization receipts; unsandboxed execution is disabled unless separately enabled by the operator.

## Proxy product

SignalCore's proxy enforces:

- provider credentials remain transport-only;
- control endpoints use a separate control token;
- remote bindings require TLS;
- streams use commit-before-forward delivery;
- exact response evidence is committed before client delivery;
- provider token and cost usage can be bound to claim-bearing receipts;
- systemd, launchd and Windows Task Scheduler services are user-scoped and reversible.

Ten provider families have explicit presets. OpenAI, Anthropic, Gemini and compatible APIs can use direct proxy routes. SigV4, OAuth2 and non-compatible request families are marked adapter-required rather than being represented as zero-code support.

## Python library

```python
from signalcore_runtime import (
    ProviderUsageReceipt,
    ReceiptValidator,
    SessionContinuityController,
    SignalCoreClient,
    ToolRoutingEnforcer,
)

route = ToolRoutingEnforcer.decide("repo.search")
assert route.allowed

client = SignalCoreClient(".signalcore/sdk", project=".")
```

The Python SDK supports caller-provided synchronous or asynchronous transports, exact evidence, safe replay, sessions, routing and normalized provider usage.

## TypeScript library

```bash
cd sdk/typescript
npm ci
npm run check
npm test
```

```ts
import { SignalCoreClient } from "@signalcore/client";
import {
  validateProviderUsageReceipt,
  type ProviderUsageReceipt
} from "@signalcore/client/receipts";

const client = new SignalCoreClient({
  baseUrl: "http://127.0.0.1:8787",
  controlToken: process.env.SIGNALCORE_PROXY_CONTROL_TOKEN
});
```

The package includes typed proxy calls, SSE parsing, retries, timeouts, control-plane health methods and fail-closed receipt validation. Dependency installation is locked and tested on supported Node versions.

## Platform and framework coverage

The contract registry currently includes:

- **10 provider families**;
- **15 framework surfaces**;
- **18 coding-agent hosts**;
- **18 concrete platform-adapter contracts**.

Host detection uses host-specific executables or markers rather than generic repository files. Kiro uses project MCP plus native skills. Pi, Oh My Pi and OpenClaw use verified native-skill paths. A contract is not a live certification; `signalcore integrations` reports that boundary.

## Sessions and continuity

The session engine provides:

- append-only hash-chained events;
- recursive summary DAGs with exact source ranges;
- background compaction that does not block foreground appends;
- checkpoints, fork, merge, export and import;
- bounded active context over exact external history;
- measured compaction wall-time and continuity receipts;
- exact summary expansion without a forced-restart claim.

The architecture claim is:

```text
UNBOUNDED_EXTERNAL_HISTORY_WITH_BOUNDED_ACTIVE_WINDOW
```

It is not a claim that a provider accepts infinite prompt tokens.

## Metrics and analytics

`signalcore status` and `signalcore stats` expose onboarding wall-time, host verification, token categories, provider cost, request wall-time, session/repository counts, compaction time, continuity restores, denied routes and unresolved proof gates.

The default analytics stream is local and content-free. Prompt and response bodies are excluded.

## Benchmark and evidence commands

```bash
signalcore prove plan
signalcore prove schema
signalcore prove receipts receipts.json
signalcore prove benchmark receipts.json
signalcore prove long-context long-context-receipts.json
signalcore prove external-suite swe-bench-receipts.json --suite swe-bench
signalcore prove maturity maturity-evidence.json
python benchmarks/measured_agent_benchmark.py receipts.json --output result.json
```

Claim-bearing coding-agent runs require paired baseline/SignalCore receipts with measured tokens, cost, wall-time, success and quality. The committed gate requires at least 30 pairs, 5 repositories, 10 tasks and 3 workload families with quality and success non-inferiority.

The OOLONG-like gate measures required-fact recall, stale-fact rejection, evidence precision, exact recovery, continuity, tokens and wall-time. Synthetic fixtures test the gate but cannot open a public claim.

## Current public status

```text
EXTERNAL_SUPERIORITY_NOT_PROVEN
LONG_CONTEXT_QUALITY_NOT_PROVEN
MEASURED_AGENT_BENCHMARK_NOT_PROVEN
LIVE_INTEGRATION_CERTIFICATION_NOT_PROVEN
DAILY_CODING_AGENT_READINESS_NOT_PROVEN
PUBLIC_PRODUCT_MATURITY_NOT_PROVEN
```

Real competitor runs, SWE-bench, official OOLONG-family results, live integrations, provider receipts, onboarding population data, public adoption and 90-day operations remain external evidence tasks.

## Validation

```bash
python -m compileall -q signalcore_runtime skills/signal-core tools tests benchmarks
python -m unittest discover -s tests -q
python tools/check_repository_hygiene.py
python tools/refresh_manifest.py --check
python tools/validate.py
python tools/validate_runtime.py

npm ci
npm test

cd sdk/typescript
npm ci
npm run check
npm test
```

CI validates Python 3.11–3.13 across Ubuntu, Windows and macOS, installer behavior, the TypeScript SDK across supported Node versions, clean wheel installation, CodeQL, dependency review, deterministic manifests, artifacts, SBOMs, checksums, reproducibility and provenance.

## Repository policy

Development is performed on a focused branch and returned through a pull request. CI verifies `MANIFEST.sha256` but never commits directly to `main`. The supported hardening state is squash-merged after all required checks pass; old incomplete commit check icons are not rewritten.

See:

- `docs/operations/ONE_COMMAND_INSTALL.md`
- `docs/operations/CI_AND_BRANCH_POLICY.md`
- `docs/architecture/DAILY_AGENT_PRODUCT_001.md`
- `docs/benchmark/MEASURED_AGENT_PROOF_001.md`
- `docs/operations/EXTERNAL_EVIDENCE_RUNBOOK_001.md`

## Version policy

`VERSION`, Python, TypeScript, installer, skills, marketplace, extension, CodeMeta, CLI, workflows and artifact metadata remain synchronized at **0.0.1** and **pre-release** until the owner explicitly changes the policy.
