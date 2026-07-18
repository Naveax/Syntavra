# SignalCore Benchmarks

This document publishes the measured internal engineering results for **SignalCore 0.0.1 — Context Intelligence Extreme v2 + Runtime Optimizer v3**.

> These results compare the Runtime v3 release candidate with the repository's internal v1 baseline. They are **not** OpenAI, Anthropic, Token Savior, Aider, Context Mode, SWE-bench, BEIR, or independent third-party superiority results.

## Release identity

| Field | Value |
|---|---|
| Build | `Context Intelligence Extreme v2 + Runtime Optimizer v3` |
| Version | `0.0.1` |
| Release ZIP SHA-256 | `141b6b34fabb25de2d1d308386a15a43e1fca1712c7e315e1101ff4bd5604d79` |
| Package tests | `1,200 / 1,200 PASS` |
| Release validator | `19 / 19 PASS` |
| Manifest entries | `103` |

The full source release candidate may be ahead of the current `main` source tree until its complete source merge is finished. The raw benchmark artifacts below identify the tested build explicitly.

## Headline results

| Benchmark | Cases | Baseline | Runtime v3 | Measured difference |
|---|---:|---:|---:|---:|
| Hard exact/stale/adversarial evidence | 600 | 0% complete; 1,037 tokens | 100% complete; 174 tokens | **83.23% less selected context** |
| Exact-free semantic multi-hop | 120 | 11.67% complete; 1,586 tokens | 100% complete; 77 tokens | **95.15% less selected context** |
| Runtime output compaction | 400 | Raw tool output | 86.02% mean estimated reduction | **100% injected-marker preservation** |
| Capsule-only controlled repairs | 4 | ~5,231 repository tokens | ~139 capsule tokens; 4/4 verifier PASS | **97.35% less context** |

## 1. Hard adversarial evidence benchmark

Six isolated 100-case processes test:

- exact error preservation
- stale versus current evidence
- conflicting documents
- prompt-injection decoys
- repeated exact-term spam
- definition, caller, failure, and verifier coverage
- bounded context selection

| Metric | v1 | Runtime v3 / Extreme v2 |
|---|---:|---:|
| Complete-task rate | 0% | **100%** |
| Mean role recall | 100% | **100%** |
| Exact preservation | 100% | **100%** |
| Stale-selection rate | 100% | **0%** |
| Mean selected context | 1,037.38 tokens | **173.92 tokens** |
| Mean latency | 17.28 ms | 95.26 ms |
| Conservative p95 latency | 23.27 ms | 146.43 ms |

Runtime v3 spends more local CPU on evidence validation and ranking. The benchmark does not claim that end-to-end provider latency is lower; it demonstrates higher evidence completeness and much smaller model-facing context in this controlled suite.

Raw result: [`benchmarks/results/runtime-v3/hard-600.json`](benchmarks/results/runtime-v3/hard-600.json)

## 2. Exact-free semantic multi-hop benchmark

Four isolated 30-case processes remove:

- exact markers
- file names
- symbol names
- role metadata
- direct error codes

The system must recover implementation, caller/dependency, failure, and verifier evidence from natural-language descriptions.

| Metric | v1 | Runtime v3 / Extreme v2 |
|---|---:|---:|
| Complete rate | 11.67% | **100%** |
| Role recall | 71.25% | **100%** |
| Mean selected context | 1,586.28 tokens | **77 tokens** |
| Mean latency | 11.15 ms | 68.50 ms |
| Conservative p95 latency | 12.48 ms | 86.25 ms |

Raw result: [`benchmarks/results/runtime-v3/semantic-120.json`](benchmarks/results/runtime-v3/semantic-120.json)

## 3. Runtime output benchmark

Four generated output families are tested with exact marker preservation:

| Family | Cases | Mean estimated reduction | Marker preservation | p95 local processing |
|---|---:|---:|---:|---:|
| Pytest | 100 | **72.70%** | **100%** | 1.52 ms |
| Git | 100 | **75.46%** | **100%** | 0.46 ms |
| Logs | 100 | **96.58%** | **100%** | 1.50 ms |
| JSON | 100 | **99.33%** | **100%** | 1.95 ms |
| **Combined** | **400** | **86.02%** | **100%** | **1.90 ms** |

Additional controlled policy checks:

- model-route decision accuracy: 100%
- budget-decision accuracy: 100%
- documented hook result-replacement matrix: 100%

These are controlled generated outputs, not a guarantee of API-billing reduction.

Raw result: [`benchmarks/results/runtime-v3/runtime-output-400.json`](benchmarks/results/runtime-v3/runtime-output-400.json)

## 4. Capsule-only controlled repair benchmark

Four generated Python mini-repositories contain:

1. schema-aware cache-key bug
2. authorization conjunction bug
3. generation equality bug
4. inclusive slice bug

The repair decision is made from the selected evidence capsule only. The benchmark records zero additional source reads before patch selection and runs a real `pytest` verifier after each patch.

| Metric | Result |
|---|---:|
| Verified repairs | **4 / 4 PASS** |
| Mean full-repository context | ~5,231 tokens |
| Mean evidence capsule | ~139 tokens |
| Mean context reduction | **97.35%** |
| Additional source reads before patch selection | **0** |

Raw result: [`benchmarks/results/runtime-v3/capsule-repair-4.json`](benchmarks/results/runtime-v3/capsule-repair-4.json)

## What is proven

Within the published controlled suites, Runtime v3:

- preserves required exact markers
- rejects stale evidence in the hard suite
- retrieves complete multi-role evidence in the semantic suite
- substantially reduces selected/model-facing context versus the internal v1 baseline
- produces repairable evidence capsules for four controlled Python defects
- compacts generated tool output while retaining injected critical markers

## What is not proven

The results do **not** prove:

- market leadership
- OpenAI or Anthropic internal-system superiority
- Token Savior, Context Mode, Aider, or LiteLLM superiority
- SWE-bench or BEIR performance
- production API cost reduction of the same percentages
- end-to-end latency improvement on every host
- independent third-party reproducibility

## Required public proof gates

Before publishing a market-leadership claim, SignalCore still needs:

1. held-out public task corpora
2. identical-model and identical-commit provider A/B runs
3. deterministic verifiers
4. cold/warm cache separation
5. raw usage and failure logs
6. bootstrap confidence intervals
7. monorepo, long-session, multilingual, output-heavy, and security task families
8. independent reproduction

## Raw artifacts

- [`hard-600.json`](benchmarks/results/runtime-v3/hard-600.json)
- [`semantic-120.json`](benchmarks/results/runtime-v3/semantic-120.json)
- [`runtime-output-400.json`](benchmarks/results/runtime-v3/runtime-output-400.json)
- [`capsule-repair-4.json`](benchmarks/results/runtime-v3/capsule-repair-4.json)
- [`release.json`](benchmarks/results/runtime-v3/release.json)
