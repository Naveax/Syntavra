# Syntavra Token Economy Competitive Gap V1

Status: RESEARCH-ADMITTED / RECONCILIATION-FIRST / NO NEW CAP NAMESPACE
Date: 2026-09-07
Authority: existing HyperEfficiency + Token Elimination + Context Execution Compiler + SWIR roadmap

## 1. Objective

Syntavra should minimize **verified provider work per successful task**, not merely make stored text look shorter.

The practical priority order is:

`do not create -> do not retrieve -> do not resend -> do not reason again -> do not generate -> compact only the irreducible residual`

This overlay records token-economy mechanisms found in current agent systems, papers and repositories that are materially stronger than Syntavra in one narrow area. It does not create a parallel product architecture. Every item must reuse or harden an existing owner when one exists.

Public savings claims remain forbidden until paired provider-observed baseline/candidate receipts demonstrate equivalent work and non-inferior verifier success.

## 2. Main finding

Syntavra is no longer missing a generic compressor. Its largest remaining opportunity is **pre-provider elimination**.

The current repository already has:

- Provider Token Envelope;
- Semantic Wire IR;
- ToolOutputExternalizer with exact recovery/dedup/delta support;
- deferred tool discovery and schema budgets;
- prompt cache planning and provider gateway support;
- session semantic retrieval;
- output governor;
- context governance, evidence and usage ledgers.

The highest-value gaps are where those mechanisms are not yet enforced at the exact point where token mass is created.

The current coding-agent path is the clearest leak: a repository packet can still reach roughly 120k bytes, inspections can return large whole-file payloads, verifier stdout/stderr can remain large, and raw tool results accumulate in the model message history across rounds. Those bytes should be intercepted before they become recurring provider context.

## 3. External evidence and lessons

### Qwen Code: prompt-prefix stability is an optimization surface

Qwen Code documented that identical tool sets can serialize in different orders when MCP discovery is asynchronous. Because prompt caches are byte-sensitive, this causes avoidable cache misses. Their design canonicalizes tool declarations by name and recommends explicit cache-break telemetry.

Syntavra action: harden `ToolSchemaCompiler` and `PromptCacheOptimizer`; tool order must never carry ranking semantics. Same visible tool set + same schemas must produce the same serialized tools prefix.

### Anthropic: tool search and server-side context editing

Anthropic's Tool Search defers tool schemas and reports roughly 85% tool-token reduction in its internal evaluation while improving tool-selection accuracy on large catalogs. Anthropic context editing can clear old tool-result and thinking blocks before they are processed again, and exposes cleared-token statistics.

Syntavra action: provider-native adapters should be preferred when available. Do not emulate provider-native behavior with an extra MCP hop unless the hop wins an end-to-end benchmark.

### PayPal SCOUT: tool exposure is retrieval, not listing

SCOUT reports a production reduction from about 140.2k tool-schema tokens to 1.3k across 2,000+ tools by exposing `tool_search` and `execute_tool`, with BM25 + dense retrieval fused by reciprocal rank fusion.

Syntavra action: evolve DeferredToolDiscovery from deterministic lexical/family selection into a hybrid retrieval lane with fixed top-k and authorization/risk filtering before schema materialization. Keep deterministic fallback.

### context-fold / observation masking: deterministic removal before summarization

`context-fold` demonstrates a useful ingestion gate: sufficiently large tool results are stored exactly and are born in model context as small reversible pointers. Its design also batches history mutation to preserve provider cache prefixes.

JetBrains' observation-masking work found simple deterministic masking can reduce cost by about half and can match or outperform LLM-based summarization on coding-agent tasks.

Syntavra action: deterministic supersession/masking/externalization must precede LLM summarization. Summary is a fallback for semantic residue, never the default compactor.

### SWE-Pruner: residual code context should be task-aware

SWE-Pruner reports 23-54% token reduction on coding-agent workloads using a lightweight task-aware line selector, after formulating an explicit pruning goal. SWE-Pruner Pro reports further savings for open-weight models using internal representations.

Syntavra action: after exact graph/range retrieval and deterministic pruning, optionally run a local task-aware line pruner on large residual code blocks. Never prune exact edit anchors or verifier-critical evidence.

### AutoCompact / SelfCompact: timing matters

Recent work shows compaction triggered by trajectory/task state can outperform fixed threshold compaction. A context can fit inside the model window and still be harmful because stale exploration remains active.

Syntavra action: harden Automatic Phase Rotation + AdaptiveContextPolicy with semantic compaction triggers at resolved-subtask, localized-target, edit-complete and verifier-complete boundaries. Learned policies remain shadow-mode until quality gates pass.

### Mem0 + LightMem reproduction: retrieval beats summary-only memory

Mem0's current memory stack uses semantic + BM25 + entity + temporal signals under a bounded retrieval budget. A 2026 LightMem reproduction found raw-turn retrieval often beats constructed-memory retrieval at equal depth and that construction can remove answer-relevant information.

Syntavra action: memory becomes dual-track: immutable/exact raw events remain truth; constructed memory is only an accelerator. Retrieve from both, rerank jointly, and spend a fixed token budget.

### GPTCache: semantic reuse can remove inference entirely, but safety must be stronger for code

Semantic response caching increases cache-hit coverage beyond exact request matching. Blind semantic reuse is unsafe for coding agents because small changes in repository state, policy or verifier configuration can invalidate an apparently similar answer.

Syntavra action: add verifier-gated inference skipping with exact/fingerprint matching first. Semantic reuse is allowed only for task families with a complete state fingerprint and an optional verifier/entailment gate.

### Reasoning-token compression

Sketch-of-Thought reports large reasoning-token reductions through task-specific compact reasoning styles. TokenSkip shows controllable CoT shortening for open-weight models. Current providers also expose native reasoning-effort and output-verbosity controls.

Syntavra action: a Reasoning Sketch Governor chooses native provider controls first, compact reasoning contracts second, and local/open-model pruning adapters last. Reasoning tokens and answer tokens are budgeted separately.

## 4. Competitive gap registry

| Priority | Mechanism | Classification | Canonical owner | Required behavior |
|---|---|---|---|---|
| P0 | Pre-Model Ingestion Fold Gate | HARDEN/UNIFY | `ToolOutputExternalizer` + `agent_runtime` | Externalize oversized tool output before first provider visibility; emit <= bounded preview + exact handle + critical lines. |
| P0 | Active Context Supersession Graph | NEW/HARDEN | context/session state | A newer exact read/diff/verifier result explicitly supersedes older payloads; old payload becomes handle-only. |
| P0 | Useless / No-Change Result Elision | NEW/HARDEN | tool/output pipeline | Empty, identical, no-change and already-known results collapse to a typed receipt instead of prose/raw payload. |
| P0 | Causal History Skeleton | HARDEN | `agent_runtime` + checkpoint/handoff | Retain user intent, actions, outcomes, hashes, failures and exact handles; remove stale payload bodies. |
| P0 | Stable Tool Schema Canonicalization | HARDEN | `ToolSchemaCompiler` | Same effective tool set must serialize byte-identically regardless of registration/discovery order. |
| P0 | Prompt Cache Break Detector | HARDEN | `PromptCacheOptimizer` + provider receipts | Attribute cache misses to model/system/tools/schema/order/cache-control/TTL changes; provider diagnostics outrank guesses. |
| P0 | Cache Break-Even Eviction Governor | NEW/HARDEN | prompt cache + context policy | Do not prune/mutate warm prefix unless expected avoided future tokens exceed cache-rewrite penalty or safety requires it. |
| P0 | Tool Result Query Pushdown | NEW/HARDEN | MCP application/tool pipeline | Field projection, row limit, filtering, grouping/count/sum/sample run locally/server-side before provider sees result. |
| P0 | Delta Tool Response Protocol | HARDEN | ToolOutputExternalizer + cache | Repeated/polling results emit only changed fields/segments + baseline handle when exact delta is valid. |
| P0 | Verifier-Gated Inference Skip Cache | NEW | provider gateway + evidence + verifier graph | Exact/fingerprint cache hit may return 0-provider-token result; semantic hit requires state/policy/model/task-family gate and verifier where needed. |
| P0 | Workflow Skill / Macro Compiler | UNIFY/HARDEN | admitted Macro Factory / deterministic workflow compiler | Repeated verified tool graphs become parameterized local macros; model chooses macro, not re-plans every step. |
| P0 | Provider-Native Context Editing Adapter | PROVIDER-GATED | provider gateway | Use native tool/thinking clearing when supported; reconcile provider cleared-token receipts. |
| P0 | Provider-Native Tool Search Adapter | PROVIDER-GATED | DeferredToolDiscovery/MCP | Prefer native deferred tool search where it wins; do not double-wrap with a gateway by default. |
| P0 | OpenAI Prompt Cache Modernization | HARDEN | provider gateway | Prefer current prompt-cache options/breakpoints/diagnostics where supported; preserve compatibility adapters for older endpoints. |
| P1 | Hybrid Tool Search (BM25+dense+RRF) | HARDEN | DeferredToolDiscovery | Retrieve top-k tools by sparse+dense signals, then apply auth/risk/health filters and materialize schemas. |
| P1 | Multi-Level Tool Detail | HARDEN | DeferredToolDiscovery | L0 name, L1 name+description, L2 full selected schema; every level tokenizer-budgeted. |
| P1 | Task-Aware Line Pruner | OPTIONAL ADAPTER | repository retrieval | Local lightweight skimmer only on residual large code after exact structural selection; fidelity gated. |
| P1 | Phase/Trajectory-Aware Compaction | HARDEN | AdaptiveContextPolicy + phase rotation | Trigger at semantic boundaries, not only context percentage. Preserve current derivation and failure evidence. |
| P1 | Dual-Track Raw + Constructed Memory | HARDEN | session memory/retrieval | Exact raw events remain authority; constructed facts accelerate retrieval but can never erase raw truth. |
| P1 | Multi-Signal Memory Reranker | HARDEN | SessionSemanticRetriever | Semantic + lexical/BM25 + entity + temporal/current-state signals under fixed provider-token budget. |
| P1 | Reasoning Sketch Governor | NEW/HARDEN | Reasoning Effort Governor | Task risk decides native effort/verbosity and compact reasoning style. Receipt tracks reasoning tokens separately. |
| P1 | Dynamic Output Contract | HARDEN | SWIR + OutputGovernor | Select minimal answer schema before inference; local renderer expands only deterministic presentation. |
| P1 | Semantic Novelty Gate | HARDEN | OutputGovernor | Provider answer fields must be new fact/action/decision/evidence/uncertainty or requested content; filler/repetition is disallowed. |
| P2 | Hidden-State Code Pruning | LOCAL/OPEN-WEIGHT EXPERIMENT | local inference adapter | Use SWE-Pruner-Pro-like relevance head only where model internals are available. |
| P2 | Resource-Wise KV Cache | LOCAL COMPUTE OPTIMIZATION | local inference | Reuse KV for stable resources/tools/skills; classify as latency/compute savings, not billed provider-token savings. |
| P2 | TokenSkip / Learned Reasoning Pruner | LOCAL/OPEN-WEIGHT EXPERIMENT | local model training | Optional LoRA/training lane; never assumed to transfer across models/tasks. |
| P2 | Native SWIR Vocabulary | LOCAL/OPEN-WEIGHT EXPERIMENT | SWIR tokenizer adapter | Add true one-token semantic operators only when tokenizer/model can be modified and round-trip behavior is trained/evaluated. |

## 5. P0 implementation order

### TE-0: Measure the leak before changing behavior

Add per-turn attribution for:

- tool schema fresh/cache-read/cache-write tokens;
- repository/context tokens;
- active raw tool-result tokens;
- stale/superseded tool-result tokens;
- reasoning tokens;
- answer tokens;
- cache miss/break reason;
- exact recovery/recall round trips;
- inference-skip hits/misses/false-hit preventions.

Exit: paired baseline/candidate traces can explain >=95% of provider-visible token mass by source.

### TE-1: Stop payloads at ingestion

Wire `ToolOutputExternalizer` directly into coding-agent result ingestion.

Rules:

- raw tool payload is persisted before model admission;
- large successful output is born folded;
- failures get larger visible headroom and critical lines pinned;
- exact recovery handle is mandatory before eviction;
- a recall has a hard token/range budget so it cannot re-flood context;
- if the model repeatedly recalls most of a folded payload, policy learns that this tool/result family should remain warm longer.

Exit: no oversized tool payload can silently enter every subsequent provider turn.

### TE-2: Constant-context tool loop

After each model/tool round:

- active result may remain raw within its lease;
- older tool payloads become causal receipts + handles;
- newer exact reads supersede older reads of the same identity;
- identical/no-change outputs become one typed marker;
- current diff is represented as changed hunks/symbols + recoverable artifact, not a repeated 120k-character tail;
- successful verifier logs become status + counts + receipt handle; failures retain minimal failure evidence.

Exit: provider-visible context is approximately O(active evidence), not O(number of tool rounds).

### TE-3: Make prompt cache stability explicit

- canonical-sort visible tool declarations;
- canonicalize semantically unordered arrays where protocol permits;
- keep static instructions/tool schemas in stable prefix;
- move timestamps, usage, request IDs and volatile state to tail or omit them;
- record prefix component hashes;
- emit a cache-break event when observed provider cached-token use drops unexpectedly;
- choose eviction only after a break-even calculation.

Exit: same model + same stable state + same visible tool set yields same prefix fingerprint.

### TE-4: Push computation below the provider boundary

For structured tool results, allow an execution plan such as:

`tool -> filter(fields) -> where(predicate) -> aggregate(group/count/sum/min/max) -> sort/top-k -> sample -> exact artifact + compact result`

The provider should ask for the answer shape, not receive 10,000 rows and then discover it only needed a count.

Exit: raw structured result size is no longer proportional to provider-visible result size.

### TE-5: Turn repeated work into zero/near-zero inference

Create deterministic macro identity from:

- workflow/macro version;
- task family and normalized parameters;
- repository/worktree/dependency digest;
- tool schemas/versions;
- environment/toolchain fingerprint;
- policy/security fingerprint;
- verifier contract.

A verified exact macro/cache hit can bypass frontier inference. Semantic similarity alone cannot.

Exit: eligible repeated easy tasks can legitimately consume zero provider tokens.

### TE-6: Retrieval/memory residual optimization

- hybrid tool retrieval;
- graph/range selection first;
- optional task-aware local line pruning second;
- raw+constructed memory retrieval under a fixed token budget;
- no summary-only truth.

### TE-7: Reasoning/output residual optimization

- native provider reasoning effort and verbosity first;
- SWIR compact answer schema before generation;
- local deterministic render;
- optional Sketch-of-Thought style routing for compatible tasks;
- local learned reasoning-pruning only for open models.

## 6. Provider-specific opportunities

### OpenAI

Current provider APIs expose prompt-cache controls/diagnostics, reasoning controls, output verbosity and conversation state. Syntavra should version provider adapters so deprecated and current fields do not become one forever-growing compatibility prompt/request.

### Anthropic

Use native Tool Search and context editing when capability discovery confirms support. Native tool-result clearing is particularly valuable because removal occurs before repeated model processing.

### Gemini/Google

Maintain stable common prefixes and explicit/implicit cache accounting where supported. Provider cache hits lower repeated processing cost but do **not** substitute for context-capacity reduction; Syntavra still removes unnecessary material.

## 7. Rejected or constrained ideas

Do not promote these as universal strategies:

1. **Naive word -> integer dictionaries.** Hosted model tokenizers already tokenize; dictionary overhead can expand input.
2. **Always-on LLM summarization.** Deterministic masking/externalization is cheaper and safer when semantics are recoverable mechanically.
3. **Always-on MCP meta-gateway.** A gateway can save schema tokens at scale but add extra tool/search turns and may increase end-to-end tokens on small catalogs.
4. **Blind semantic response cache.** Similar wording is not equivalent repository state.
5. **Post-generation truncation counted as output savings.** The provider already generated the tokens.
6. **Lossy compression of security policy, user constraints, exact edit anchors or verifier evidence.** Exactness beats cosmetic ratio.
7. **Prompt caching counted as context reduction.** Cache reduces repeated compute/cost, but cached tokens can still occupy context capacity.
8. **Summaries replacing raw memory.** Raw/exact lineage remains authority.
9. **Adding every competitor dependency.** Borrow mechanisms; adapters remain optional; Syntavra owns policy/evidence.

## 8. Benchmark design

Every candidate mechanism must be tested at three levels:

### Component

Measure only its owned surface, such as tool schemas or tool output. This permits comparison with external component claims without pretending they are whole-session savings.

### End-to-end task

Frozen model/provider/temperature/reasoning, same repository state, same task, same tool permissions, same verifier and same delivery requirement.

### Long-session trajectory

Measure repeated context reacquisition, prompt-cache reuse, tool-result replay, compaction frequency, recovery/recall turns and total provider tokens per verified task sequence.

Required metrics:

- fresh input tokens;
- cached input read/write tokens where reported;
- output tokens;
- reasoning tokens;
- tool-schema tokens;
- tool-output tokens;
- context/repository tokens;
- provider calls and tool calls;
- recovery recalls and recall tokens;
- cache-break count/reason;
- inference-skip hit rate;
- false semantic-hit prevention count;
- verifier success/regression;
- latency and provider cost per successful task.

## 9. Non-additive savings model

Component percentages may overlap and **must not be added arithmetically**.

For a representative 100k-token candidate context, the intended mature flow is:

`100k candidate -> deterministic exclusion/supersession/folding -> task-scoped retrieval -> schema-on-demand -> query pushdown/delta -> SWIR -> provider envelope`

Engineering targets remain:

- easy/repetitive eligible tasks: 0-3k provider-visible tokens, including true 0-token inference-skip cases;
- normal mixed coding tasks: 3-8k when irreducible evidence fits;
- long sessions: active context should remain roughly constant after warm-up rather than grow linearly with tool rounds;
- >=85% avoidable input and output reduction as promotion floor, >=95% stretch on eligible workloads;
- quality/verifier non-inferiority;
- unexplained provider-envelope overflow = 0.

These are engineering targets, not measured Syntavra product claims.

## 10. Immediate acceptance gates

P0 promotion requires all of the following:

1. No provider-savings claim without provider receipt.
2. No lossy transform without exact recovery or an explicit fidelity verifier.
3. Stable tool-set order produces stable serialized tool prefix.
4. Large tool output is externalized before repeated provider visibility.
5. Successful/no-change tool output can collapse to a receipt.
6. Cache mutation has break-even accounting.
7. Inference skip requires a complete task/state/policy/verifier fingerprint.
8. Semantic cache false positives fail closed.
9. Raw memory remains retrievable after constructed-memory consolidation.
10. Output savings are enforced before inference/generation.

The dominant product KPI remains:

`Verified Provider Cost / Successful Task`

Secondary optimization target:

`Provider Tokens / New Verified Information`
