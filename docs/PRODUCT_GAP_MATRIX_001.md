# Syntavra v0.0.1 Pre-Release — Product Gap and Evidence Matrix

## Interpretation

- **IMPLEMENTED**: reviewable runtime/product code exists.
- **INTERNALLY_GATED**: deterministic tests and fail-closed validators exist; CI must still pass on the current commit.
- **EXTERNAL_EVIDENCE_REQUIRED**: repository code cannot legitimately manufacture this proof.
- **NOT CLAIMED**: no public success/superiority/adoption claim is opened.

The version remains **0.0.1 / pre-release** until the repository owner explicitly authorizes a change.

| Area | Current state | Evidence / command | Claim boundary |
|---|---|---|---|
| Installation and user experience | IMPLEMENTED + INTERNALLY_GATED | `syntavra setup --apply`, atomic host transactions, measured install receipt | Real population success rate requires external onboarding receipts |
| Proxy productization | IMPLEMENTED + INTERNALLY_GATED | Provider presets, fixed command, systemd/launchd/Task Scheduler lifecycle | Live provider certification requires external receipts |
| Python library | IMPLEMENTED | `SyntavraClient`, receipts, sessions, routing, proof APIs | Package adoption is external |
| TypeScript library | IMPLEMENTED + BUILD-GATED | `@syntavra/sdk`, `@syntavra/sdk/receipts`, TS check/build/package | npm adoption is external |
| Framework breadth | IMPLEMENTED AS CONTRACTS | 15 framework surfaces | Every framework needs live certification receipts |
| Provider breadth | IMPLEMENTED AS PRESETS/CONTRACTS | 10 provider families | SigV4/OAuth/non-compatible providers remain adapter-required |
| Workload variety | INTERNALLY_GATED | coding, repo tasks, SWE-bench, long context, continuity, routing | Real workload results require pinned harness receipts |
| Release frequency and maturity | EXTERNAL EVIDENCE REQUIRED | `syntavra prove maturity` | No maturity claim without 90-day external evidence |
| Metrics/observability UI | IMPLEMENTED | `syntavra status`, `stats`, content-free analytics | Real token/cost values require provider receipts |
| Academic long-context architecture | DOCUMENTED + IMPLEMENTED | exact external history, bounded active window, summary DAG | Literature motivates architecture; it does not prove Syntavra results |
| Real long-context benchmark | EXTERNAL EVIDENCE REQUIRED | `syntavra prove external-suite --suite oolong` | No Oolong/LongBench/InfiniteBench score claimed |
| Recursive paradigm evidence | INTERNALLY GATED + EXTERNAL REQUIRED | recursive suite contract, provenance, non-inferiority | Recursion advantage not claimed without paired runs |
| Full coding-agent UX | IMPLEMENTED | setup → status → run → prove | Daily readiness remains gated by real task evidence |
| Async compaction and continuity | IMPLEMENTED + INTERNALLY_GATED | continuity receipt, exact recovery, measured compaction | Cross-user operational reliability is external |
| Real coding-task evidence | EXTERNAL EVIDENCE REQUIRED | SWE-bench suite contract | No SWE-bench resolved percentage claimed |
| Measured token use | RECEIPT MODEL IMPLEMENTED | provider usage receipt | Estimates and synthetic receipts do not count |
| Measured wall-time | RECEIPT MODEL IMPLEMENTED | request/benchmark/session/install wall-time | Component timing alone is not end-to-end proof |
| Quality preservation/improvement | FAIL-CLOSED GATE IMPLEMENTED | quality/success non-inferiority | Savings cannot open a claim if quality declines |
| Narrow product surface | IMPLEMENTED | four top-level operations | Legacy commands remain compatibility-only |
| Optimized MCP profile | IMPLEMENTED + ENFORCED | minimal 8, balanced 36, audit full | `tools/call` rechecked; list filtering is not trusted |
| Real platform adapters | IMPLEMENTED FOR DETECTED HOSTS | atomic verified installation; Kiro MCP+skill; Pi/OMP/OpenClaw skill-only | Registry is not live certification |
| Doctor/stats/upgrade | IMPLEMENTED | doctor verifies hosts; stats reports receipts; upgrade version-locked | Upgrade never changes version without owner authorization |
| Tool routing enforcement | IMPLEMENTED + SECURITY-GATED | hard allowlists, authorization receipt, sandbox requirement | Unsandboxed process disabled by default |
| Session analytics | IMPLEMENTED | local content-free JSONL aggregate | Prompt/response content excluded |
| Simpler mental model | IMPLEMENTED | setup/status/run/prove | Internal compatibility layers remain hidden from onboarding |
| Daily coding-agent readiness | EXTERNAL EVIDENCE REQUIRED | measured agent + live integration + maturity gates | `DAILY_CODING_AGENT_READINESS_NOT_PROVEN` until all gates pass |
| Simplicity | IMPLEMENTED AT PUBLIC SURFACE | four commands, bounded MCP profiles | Internal engine remains sophisticated by design |
| Installation speed | MEASURED LOCALLY + EXTERNAL GATE | local install receipt; maturity p95 threshold | Public p95 requires external onboarding sample |
| Low maintenance cost | PARTIALLY IMPLEMENTED | deterministic manifests, generated adapters, service plans | Long-term maintenance cost requires operational history |
| Immediate comprehensibility | IMPLEMENTED IN README/CLI | four-command help and product manifest | User-study evidence is external |
| Measured real agent benchmark | GATE IMPLEMENTED | 30 pairs, 5 repos, 10 tasks, 3 families | No public result yet |
| Broad agent visibility | IMPLEMENTED WITH BOUNDS | 18 host contracts; exact active profile counts | More tools are not treated as automatically better |
| Real competitor benchmark | EXTERNAL EVIDENCE REQUIRED | isolated arms and exact suite receipts | Missing competitor run is a failure, not omitted |
| Real provider token/cost receipt | SCHEMA + VALIDATOR IMPLEMENTED | raw provider receipt hash and normalized usage | No receipt means no savings claim |
| Live integration certification | STRICT GATE IMPLEMENTED | 3 external receipts, 2 OS, pinned harness | Internal contract tests cannot certify live use |
| User count and operational history | EXTERNAL EVIDENCE REQUIRED | maturity evidence schema | Repository fixtures never count as users |
| Installation/onboarding proof | LOCAL RECEIPT + EXTERNAL GATE | install time, host verify, rollback, doctor | Population-level proof requires external receipts |
| OOLONG-like quality test | INTERNAL PROTOCOL + EXTERNAL SUITE CONTRACT | recall, stale rejection, evidence precision, exact recovery | No official Oolong result claimed |
| SWE-bench/repository success | EXTERNAL SUITE CONTRACT | exact dataset/harness/verifier/image/repo commit | No success rate claimed |
| Public package adoption | EXTERNAL EVIDENCE REQUIRED | PyPI/npm distribution receipts | Download/install counts must come from verified sources |

## Current fail-closed public status

```text
EXTERNAL_SUPERIORITY_NOT_PROVEN
LONG_CONTEXT_QUALITY_NOT_PROVEN
MEASURED_AGENT_BENCHMARK_NOT_PROVEN
LIVE_INTEGRATION_CERTIFICATION_NOT_PROVEN
DAILY_CODING_AGENT_READINESS_NOT_PROVEN
PUBLIC_PRODUCT_MATURITY_NOT_PROVEN
```

## Conditions that may change the status

A status may move only after all relevant receipt schemas validate, evidence-integrity checks pass, paired parity is satisfied, current CI passes, and a human reviews the external artifacts. Internal fixtures can test gates but cannot open public claims.
