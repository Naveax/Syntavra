# Syntavra Semantic Wire IR V1

Status: architecture overlay + executable foundation
Date: 2026-09-07
Authority: existing HyperEfficiency / Token Elimination roadmap; no parallel CAP namespace

## 1. Goal

Syntavra should minimize provider-visible input and output, not merely shorten stored transcripts after the provider has already paid the token cost.

Primary target for eligible easy and repetitive work:

- avoidable input-token reduction: >= 85%, stretch >= 95%;
- avoidable output-token reduction: >= 85%, stretch >= 95%;
- easy-task provider-visible total target: 1,000-3,000 tokens;
- normal coding-agent provider-visible total target: 3,000-8,000 tokens;
- unexplained provider-envelope overflow: exactly zero;
- task/verifier quality: non-inferior to the natural-language baseline.

These are promotion targets, not current product claims. Exact or user-requested irreducible content can exceed the target and must be accounted for by the Provider Token Envelope necessity-lease mechanism.

## 2. Core idea

Do not replace every word with an arbitrary number. Hosted frontier models already tokenize text, and an ad-hoc dictionary can cost more tokens than it saves. Instead, compile repeated meaning and repository state into a small, versioned intermediate representation.

Pipeline:

`natural request -> intent/necessity compiler -> exact handles -> semantic atoms -> tokenizer-aware wire IR -> provider -> compact answer IR -> local renderer -> user answer`

The wire representation is permitted only when measured by the actual target tokenizer and when it preserves required semantics. Otherwise Syntavra passes natural text through unchanged.

## 3. Hard invariants

1. **No Expansion**: a wire form may not be selected when its measured provider-token cost is >= the natural alternative.
2. **No Unproven Dictionary Savings**: dictionary/grammar cost is included unless it is already part of the same fixed provider prefix being compared.
3. **Exact Data Stays Exact**: exact-required payloads can be replaced only by verified recovery handles whose content remains locally recoverable.
4. **Natural Escape Hatch**: ambiguity, unsupported semantics, protocol mismatch, recovery failure, stale handle, or verifier regression forces natural-language fallback.
5. **Output Savings Happen Before Generation**: local post-truncation does not count as provider-output savings. The model must be constrained to emit compact IR before inference.
6. **Human Rendering Is Local**: deterministic prose that adds no new facts can be expanded locally from compact answer IR.
7. **Provider Receipt Truth**: savings claims require paired provider-observed usage receipts and equal-or-better verifier success.
8. **Avoidable vs Irreducible**: percentage gates apply to avoidable tokens. Requested exact source, security policy, mandatory instructions, and required output are never silently discarded.

## 4. Semantic Wire vocabulary

V1 uses typed semantic atoms rather than free-form word IDs.

Suggested stable operators:

- `T` task/intent
- `F` file handle
- `S` symbol handle
- `L` line/range locator
- `E` evidence/failure handle
- `V` verifier handle
- `D` dependency set
- `P` policy/macro handle
- `A` requested action
- `R` retrieve
- `I` inspect
- `X` externalized artifact
- `H` exact recovery handle
- `C` current change/delta
- `?` unresolved item
- `!` critical evidence

Example natural context:

`Inspect AuthService.refresh_access_token in src/auth/service.py lines 177-239. The latest targeted verifier failure is stored in evidence artifact ext-a491. Preserve API compatibility and run only the targeted verifier.`

Possible wire form:

`I S22 F17 L177:239 E8 P4 V3`

The actual tokenizer, fixed grammar overhead, handle bindings, and exact-recovery cost decide whether this representation is admitted.

## 5. Six compression tiers

### SW-0 Pass-through

Natural text. Used when compaction is unsafe, ambiguous, or not smaller.

### SW-1 Structural projection

Remove unused fields, duplicated prose, formatting, generated/vendor data, repeated paths and known metadata. Preserve natural language where needed.

### SW-2 Stable handles

Replace repeated repository entities, evidence artifacts, tool schemas, policies and verifier identities with short versioned handles backed by exact local state.

### SW-3 Semantic macros

Replace repeated multi-token semantics with stable operators/macros. Examples: targeted-test-only, no-duplicate-read, preserve-public-api, exact-failure-reference.

### SW-4 Task-family DSL

For high-confidence task families such as coding-agent tool loops, emit a bounded DSL instead of verbose JSON or repeated prose. Unsupported fields escape to natural text.

### SW-5 Compact answer IR + local renderer

Provider emits facts/actions/status in the smallest verified schema. Syntavra renders human-friendly prose locally without asking the provider to spend tokens on deterministic wording.

### SW-6 Model-native vocabulary (local models only)

Optional future path for local/fine-tuned models: add real special tokens for common operators/macros. Hosted providers must not assume custom tokenizer vocabulary.

## 6. Input elimination architecture

Before the wire compiler, context must already pass:

`exclude -> scope -> deduplicate -> retrieve/select -> externalize -> project -> wire-compile -> optional fidelity-gated compression -> provider envelope`

The compiler must never be used as an excuse to send unnecessary data merely because the unnecessary data can be abbreviated.

Required components:

1. intent + answer-contract compiler;
2. necessity leases from Provider Token Envelope;
3. generated/vendor exclusion;
4. monorepo scope detector;
5. duplicate read/command suppression;
6. semantic repository graph (Tree-sitter/LSP/ripgrep owners reused);
7. symbol/range-first retrieval;
8. ToolOutputExternalizer exact handles and delta previews;
9. field-projection engine;
10. semantic handle table with TTL/invalidation/provenance;
11. tokenizer-aware no-expansion selector;
12. prompt grammar/version compatibility gate.

## 7. Output elimination architecture

The provider should not generate verbose prose that Syntavra later throws away.

Provider output is divided into:

- new facts;
- decisions/actions;
- requested content/code;
- constraints/uncertainty;
- evidence references;
- verifier state.

Everything else is a candidate for deterministic local rendering.

Example provider answer IR:

`S:OK V:PASS R:0 C:[auth.py]`

Possible local rendering:

`Düzeltme uygulandı. Hedef testler geçti ve regresyon saptanmadı. Değişen dosya: auth.py.`

The renderer may rephrase known fields but may not invent facts, causes, confidence, evidence or actions.

## 8. Dictionary and handle rules

Every binding contains:

- protocol version;
- short handle;
- semantic kind;
- canonical identity/digest;
- provenance;
- TTL/invalidation key;
- recovery reference when exact data is represented;
- optional human display label.

A handle is invalid after its dependency digest changes. Stale or missing handles force a lookup or natural fallback, never a guess.

Dynamic dictionaries must earn their cost. A new binding is admitted only when expected remaining reuse saves more measured tokens than introducing and maintaining the binding.

## 9. Easy-task fast path

Easy tasks should not pay the same orchestration tax as repository-scale work.

Classifier output:

- `DIRECT_LOCAL`: deterministic/local answer; no frontier call if safe.
- `DIRECT_FRONTIER`: one small natural request with minimal system surface.
- `WIRE_FRONTIER`: stable grammar already applicable and wire form wins measured tokens.
- `FULL_AGENT`: repository/tool workflow required.

Easy-task target:

- baseline candidate 5k-30k polluted session context;
- selected provider input <= 500-2,000 tokens where the task itself permits it;
- output <= 100-1,000 tokens unless the user requested long-form content;
- >=85% avoidable total reduction; >=95% stretch on replay/schema/history-heavy cases.

## 10. Long-session constant-context rule

The provider must not receive an ever-growing transcript of raw tool results.

For completed tool turns:

`raw result -> exact local artifact -> critical delta + receipt + handle`

Only the current unresolved evidence can remain raw. Older resolved evidence is represented by stable handles and can be revealed on demand.

Target: provider-visible working set remains approximately constant across tool rounds instead of growing with session length.

## 11. Benchmark matrix

Promotion requires paired baseline/candidate runs with identical model, provider, temperature, task, repository state and verifier.

Task families:

1. easy factual/direct instruction;
2. short code explanation;
3. one-symbol bug fix;
4. targeted test failure repair;
5. repeated tool-loop coding task;
6. schema-heavy MCP task;
7. long-session continuation;
8. exact-source transformation;
9. requested long-form output;
10. adversarial ambiguity / stale-handle case.

Required metrics:

- provider input tokens;
- provider output tokens;
- cached-input tokens separately;
- reasoning tokens when exposed;
- total provider tokens per successful task;
- avoidable-token denominator and reduction ratio;
- task success;
- verifier success;
- exact recovery success;
- semantic round-trip success;
- fallback rate;
- stale-handle rejection rate;
- local compile/render latency;
- provider latency/cost receipts.

## 12. Promotion gates

### Gate A: safety

- no semantic invention;
- no stale-handle acceptance;
- exact recovery 100% on exact-required fixtures;
- protocol mismatch falls back safely.

### Gate B: no expansion

For every candidate packet, measured selected tokens must be <= measured natural tokens. Any regression is a test failure.

### Gate C: easy-task efficiency

Across the easy-task benchmark suite:

- median avoidable input reduction >= 85%;
- median avoidable output reduction >= 85%;
- stretch target p50 >= 95% for replay/schema/history-heavy easy cases;
- task success and verifier success non-inferior to baseline.

### Gate D: normal-agent efficiency

- median provider-visible total <= 8k where irreducible requirements fit the envelope;
- unexplained overflow = 0;
- no higher regression rate than baseline.

### Gate E: provider proof

No public net-savings claim until paired provider receipts pass the existing proof boundary.

## 13. Implementation waves

### SWIR-0 Measurement and ownership reconciliation

Reuse existing ContextGovernor, ProviderTokenEnvelope, ToolOutputExternalizer, OutputGovernor, repository graph, cache, evidence and receipt owners. No duplicate subsystems.

### SWIR-1 Safe structural wins

Field projection, compact serializers, repeated-path aliases, schema projection, no-expansion tokenizer gate.

### SWIR-2 Stable handle plane

Versioned file/symbol/evidence/verifier/policy handles; digest invalidation; TTL; exact recovery.

### SWIR-3 Semantic macro registry

Tokenizer-measured admission, reuse forecast, macro eviction, grammar versioning and natural escape hatch.

### SWIR-4 Coding-agent wire DSL

Bounded action grammar for retrieve/inspect/edit/verify/diff/impact with explicit natural extension field.

### SWIR-5 Output bytecode

Compact provider answer contract plus deterministic local renderer; provider generation cap bound to output IR budget.

### SWIR-6 Constant-context agent loop

Completed tool turns collapse to artifact receipts; active unresolved evidence only; replay breaker and delta state.

### SWIR-7 Adaptive optimizer

Learn which encoding wins by task family/model/tokenizer. Never globally enable a transform merely because it won on another model.

### SWIR-8 Local model native tokens

Optional tokenizer extension/fine-tuning for Syntavra operators. This wave is separate from hosted-provider compatibility.

## 14. Non-goals

- claiming 85-95% savings for every arbitrary prompt;
- replacing requested long-form output with unreadable codes;
- relying on hidden provider tokenizer behavior;
- counting post-generation truncation as output-token savings;
- making prompt-cache billing discounts look like context-window reduction;
- introducing a second capability namespace.

## 15. Final objective

Syntavra should optimize **verified provider work per successful task**, not merely text compression ratio.

The desired steady-state behavior is:

`read once -> understand once -> store exact once -> reference cheaply -> reveal only when needed -> generate only novel information -> render deterministic prose locally`.

For eligible easy/repetitive tasks this architecture targets 85-95%+ avoidable token elimination and a 1k-3k provider-visible working set. For normal coding-agent work it targets 3k-8k. When the information-theoretic floor is higher, Syntavra must exceed the target transparently rather than damage correctness.