# Syntavra Provider Token Envelope v1

Status: RUNTIME ENFORCEMENT IN PROGRESS / FAIL-CLOSED POLICY

This document hardens the Context Execution Compiler with one enforceable objective:

> Provider-visible input and generated output must contain only information that has a current necessity proof.

The optimization target is not a dishonest universal percentage. A task that genuinely requires 30K exact tokens cannot be losslessly represented in 8K tokens. Syntavra therefore separates **avoidable tokens** from **irreducible tokens** and treats the 3K-8K band as the default provider target zone.

## Default target

For a large ordinary request, the default envelope is:

```text
raw input candidate                 100,000
provider input target                <=6,000
baseline output budget               10,000
provider output target               <=2,000
provider input + output target        <=8,000
```

With 2,000 mandatory input tokens and 400 mandatory output tokens, the v1 compiler produces:

```text
input budget   6,000
output budget  2,000
total budget   8,000

avoidable input removed   ~=95.9%
avoidable output removed  ~=83.3%
baseline-budget reduction ~=92.7%
```

The public product must not claim those percentages until paired provider receipts and task/verifier parity exist.

## Hard invariant

Syntavra optimizes the part that can actually be optimized:

```text
avoidable = original - lease-backed mandatory

required:
removed(avoidable_input)  >= 80%
removed(avoidable_output) >= 80%
```

If a task contains 25K tokens of genuinely exact required source, those 25K tokens are not called "waste" merely to make a dashboard green. The call may exceed the 8K target only when every irreducible segment is backed by a bounded necessity lease.

## Necessity lease

A lease is a short-lived proof that a token segment may cross the provider boundary.

Allowed classes:

- `system_instruction`
- `user_instruction`
- `security_policy`
- `exact_source`
- `current_failure`
- `verifier_evidence`
- `selected_tool_schema`
- `dependency`
- `recovery_handle`
- `requested_output`

Every non-empty lease carries:

- stable lease id;
- token count;
- evidence reference;
- exact-required flag where relevant;
- short expiry.

"Maybe useful", "the agent read it earlier", "the tool returned it", and "the repository is small" are not necessity classes.

## Zero-waste execution order

```text
TASK
  ↓
intent / answer contract
  ↓
necessity leases
  ↓
generated + vendor exclusion
  ↓
monorepo/workspace scope
  ↓
exact duplicate suppression
  ↓
replay / stale-state rejection
  ↓
schema-on-demand
  ↓
lexical → structural → semantic retrieval escalation
  ↓
verifier-sufficient evidence selection
  ↓
externalize recoverable raw payloads
  ↓
field projection
  ↓
tokenizer-aware serialization
  ↓
optional fidelity-gated compression
  ↓
PROVIDER INPUT ENVELOPE
  ↓
bounded generation / answer contract / early stop
  ↓
OUTPUT NOVELTY + EVIDENCE FILTER
  ↓
provider receipt + verifier
  ↓
adaptive learning
```

The ordering remains:

`exclude → scope → deduplicate → retrieve/select → externalize → serialize/compress → route`

Compression never gets authority to hide bad retrieval.

## Input side

### 1. Context Replay Breaker

Old turns are not replayed merely because they exist. Persist decisions, artifacts, exact hashes, unresolved failures, and verifier state. Re-admit only current deltas or expired dependencies.

Target behavior:

- identical unchanged evidence: zero new payload tokens;
- superseded evidence: zero payload tokens;
- recoverable old evidence: handle only;
- active unresolved evidence: bounded exact payload.

### 2. Repository sufficiency compiler

A coding agent must not read whole files by default.

Escalation:

1. persistent state/cache;
2. exact lexical match;
3. Tree-sitter symbol/signature;
4. LSP definitions/references/types;
5. token-cost-aware graph neighborhood;
6. bounded exact source slice;
7. whole file only with an explicit necessity lease;
8. broad repository search only after narrower lanes fail.

Retrieval stops when the next action/verifier is evidence-sufficient. "More context" is not automatically higher quality.

### 3. Ephemeral tool results

Raw tool outputs are single-use by default.

After the consuming decision:

- success noise is discarded;
- failure evidence is pinned;
- full raw payload moves to an exact-recovery artifact;
- context retains a receipt, hash, key fields, and recovery handle;
- repeated command/read requests resolve from content-addressed state when work-equivalent.

This prevents tool-loop context from growing quadratically.

### 4. MCP surface

Only the meta discovery surface and task-selected tool schemas enter context. The full catalog does not.

The target is SCOUT-like schema behavior without copying an external architecture as Syntavra's core identity.

### 5. Prompt-prefix stability

Static system policy, stable repository instruction digests, and provider-cache-compatible prefixes stay byte-stable. Dynamic evidence is appended after the stable prefix. Caching may reduce cost/latency, but cached tokens are not falsely counted as removed context tokens.

## Output side

Output optimization is not "truncate everything to 500 tokens".

### Answer contract compiler

Before generation, determine the minimum sufficient answer surface:

- direct answer/result;
- required evidence;
- changed files / patch contract where applicable;
- verification;
- limitations only when material;
- exact requested content.

### Output necessity test

A generated unit survives only if it contributes at least one unique:

- fact;
- decision;
- action;
- constraint;
- evidence reference;
- code/diff payload explicitly requested;
- uncertainty/limitation that changes interpretation.

Pure restatement, filler, duplicated explanation, repeated headings, redundant examples, and progress narration are removed.

### Generation budget

The output budget is set before inference. Post-hoc trimming cannot recover tokens already billed.

Machine-action turns should use very small structured budgets. Human answers use the smallest budget compatible with the answer contract. Explicit requests for long/exact output create `requested_output` leases and may legitimately exceed 2K.

## 3K-8K target zone

For large ordinary workloads:

- target provider input: up to 6K;
- target provider output: up to 2K;
- target combined: up to 8K.

Simple tasks should often be far below 3K. The band is an upper target, not a minimum spend.

A call above 8K must explain the overflow with necessity leases. Repeated unexplained overflow is a policy regression.

## Runtime wiring state

The envelope is now a runtime object, not only a roadmap concept.

Implemented in this slice:

1. `provider_token_envelope.py`
   - compiles the 6K input / 2K output / 8K combined target;
   - measures avoidable input/output reduction independently;
   - requires evidence-backed necessity leases for mandatory material;
   - marks irreducible overflow instead of silently truncating it.

2. `context_governor.py`
   - exposes the canonical `compile_provider_token_envelope` entry point;
   - keeps provider-budget ownership in the existing context-governor authority instead of creating a parallel policy plane.

3. `model_gateway.py`
   - accepts a compiled envelope;
   - requires tokenizer-observed `prepared_input_tokens` before strict dispatch;
   - rejects prepared input above the compiled input budget before HTTP;
   - clamps provider generation limits to the compiled output budget before inference;
   - refuses envelopes that failed admission.

Remaining priority wiring:

4. `adaptive_context_policy.py`
   - every `KEEP` decision must map to a lease or bounded optional allocation;
   - stale/recoverable items lose provider admission.

5. `agent_runtime.py`
   - reduce initial repository packet;
   - line/symbol-ranged inspection instead of whole-file default;
   - keep only the active tool result raw;
   - compact previous tool turns to deterministic receipts;
   - success verifier logs become status + artifact handle;
   - prevent quadratic message-history growth;
   - tokenize the final prepared request and pass that count into the gateway envelope.

6. `output_governor.py`
   - add novelty/necessity filtering after structured answer assembly;
   - never remove critical evidence just to meet a cosmetic percentage.

7. `deferred_tool_discovery.py`
   - enforce schema-on-demand as the default large-catalog path.

8. `tool_externalization.py` / evidence store
   - guarantee exact recovery before raw tool payload eviction.

9. `SavingsLedger` / provider receipt ledger
   - report avoidable-token reduction separately from total reduction;
   - provider receipts outrank local byte/token estimates.

## Promotion gates

A policy cannot become the default merely because it saves tokens.

Required:

- task success non-inferior;
- verifier success non-inferior;
- exact recovery passes;
- security behavior non-inferior;
- no-expansion passes;
- stale-cache rejection passes;
- avoidable input reduction >=80%;
- avoidable output reduction >=80%;
- unexplained >8K provider calls = 0;
- provider-observed receipts available for any public savings claim.

## Research alignment

Current evidence supports selection and lifecycle control before blind compression:

- PayPal SCOUT reports a production MCP tool-schema reduction from 140.2K to 1.3K tokens by exposing a tiny discovery/execute surface rather than injecting every schema.
- Anthropic's 2026 context-engineering guidance composes memory, compaction, and tool-result clearing rather than treating one compressor as sufficient.
- AutoCompact reports better coding-agent task solving when compaction timing is adaptive instead of a fixed threshold.
- LLMLingua-2 supports aggressive text compression on eligible natural-language material, but it remains a fidelity-gated late stage rather than an authority source for exact code/evidence.

These external results motivate design choices. They are not Syntavra benchmark claims.

## Primary optimization objective

```text
minimize
    provider_input_tokens
  + provider_output_tokens
  + reasoning_tokens
  + duplicated_tool_work
  + reacquisition_work

subject to
    task_success >= baseline
    verifier_success >= baseline
    security >= baseline
    exact_recovery == valid
```

The headline product KPI remains:

`Verified Provider Tokens / Successful Task`

and the anti-gaming metric is:

`Avoidable Tokens Crossing Provider Boundary`.

The desired steady state is not merely "compressed context". It is a system where unnecessary context is never created, never re-read, never re-sent, and never regenerated.
