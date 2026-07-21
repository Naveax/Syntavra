# SignalCore Long-Context and Recursive Evidence Basis 001

## Scope

This note records external research that motivates SignalCore's architecture and benchmark design. It does **not** claim that SignalCore reproduces a paper's results or inherits a paper's empirical performance. SignalCore must produce its own external receipts.

## Architectural basis

### Virtual context and tiered memory

**MemGPT: Towards LLMs as Operating Systems** introduces virtual context management inspired by operating-system memory hierarchies. It treats the model context window as a limited fast tier and moves information between context and external storage. It evaluates document analysis beyond the underlying model window and multi-session conversational memory.

SignalCore adopts the general systems boundary, not MemGPT's reported result:

- exact history is external;
- the model-visible active window is bounded;
- summaries and retrieval references navigate exact evidence;
- session continuity is measured across process/session boundaries.

Reference: Packer et al., *MemGPT: Towards LLMs as Operating Systems*, arXiv:2310.08560, https://arxiv.org/abs/2310.08560

### Recursive decomposition over external context

**Recursive Language Models** studies an inference strategy where the prompt is an external environment that a model examines programmatically, decomposes and processes with recursive model calls. This is relevant to SignalCore's bounded recursive workers and evidence-linked map/reduce execution.

SignalCore therefore requires every recursive worker to preserve task identity, parent/child provenance, exact evidence references, verifier output, duplicate-suppression identity, provider usage and wall-time. Recursion is an execution strategy, not a quality claim.

Reference: Zhang, Kraska, and Khattab, *Recursive Language Models*, arXiv:2512.24601, https://arxiv.org/abs/2512.24601

### Recursion is not sufficient by itself

**Recursive Language Models Meet Uncertainty** reports that recursive program selection can be a limiting factor and that self-reflective program search may outperform a fixed recursive strategy under the same time budget. It also reports settings where recursion can degrade performance relative to the base model.

SignalCore's recursive evidence gate consequently requires:

- baseline/recursive paired runs;
- identical provider and model;
- quality and success non-inferiority;
- bounded retries and fan-out;
- route-level uncertainty/verifier evidence;
- token, cost and wall-time accounting.

Reference: Alizadeh et al., *Recursive Language Models Meet Uncertainty: The Surprising Effectiveness of Self-Reflective Program Search for Long Context*, arXiv:2603.15653, https://arxiv.org/abs/2603.15653

## Why context-window size is not enough

### Position-sensitive failures

**Lost in the Middle** finds that long-context model performance can depend strongly on where relevant information appears, with reduced performance for information in the middle of long inputs. SignalCore's benchmark protocol therefore varies evidence position and does not accept a single needle at the beginning or end as proof of long-context quality.

Reference: Liu et al., *Lost in the Middle: How Language Models Use Long Contexts*, TACL 2024, DOI:10.1162/tacl_a_00638, https://aclanthology.org/2024.tacl-1.9/

### Broad long-context task diversity

**LongBench** provides multi-task long-context evaluation across document QA, multi-document QA, summarization, few-shot learning, synthetic tasks and code completion. **LongBench v2** extends evaluation toward realistic deep reasoning, including repository understanding, dialogue history and structured data with contexts extending to very large documents.

SignalCore uses this evidence to require multiple workload families rather than a retrieval-only score.

References:

- Bai et al., *LongBench: A Bilingual, Multitask Benchmark for Long Context Understanding*, ACL 2024, DOI:10.18653/v1/2024.acl-long.172, https://aclanthology.org/2024.acl-long.172/
- Bai et al., *LongBench v2: Towards Deeper Understanding and Reasoning on Realistic Long-context Multitasks*, ACL 2025, DOI:10.18653/v1/2025.acl-long.183, https://aclanthology.org/2025.acl-long.183/

### Evaluation beyond 100K

**∞Bench** includes synthetic and realistic tasks averaging more than 100K tokens and is designed so that retrieving a small number of passages is insufficient. SignalCore's 32K–10M virtual-history stress tiers are therefore separated from its quality gate: storing or planning 10M tokens is not evidence that the model reasoned correctly over them.

Reference: Zhang et al., *∞Bench: Extending Long Context Evaluation Beyond 100K Tokens*, ACL 2024, DOI:10.18653/v1/2024.acl-long.814, https://aclanthology.org/2024.acl-long.814/

### Aggregation-intensive evaluation

**Oolong** evaluates long-context tasks that require atomic analysis of many chunks followed by aggregation, including naturalistic synthetic tasks and real conversational data. This addresses a weakness of tests where most tokens are distractors and only a small passage must be retrieved.

SignalCore's Oolong-like gate measures required-fact recall, stale-fact rejection, evidence precision, exact recovery, continuity, provider tokens and wall-time. The committed internal protocol is not an Oolong score; a real Oolong harness run must be attached separately.

Reference: Bertsch et al., *Oolong: Evaluating Long Context Reasoning and Aggregation Capabilities*, arXiv:2511.02817, https://arxiv.org/abs/2511.02817

## Real repository-agent evidence

**SWE-bench** evaluates software-engineering agents on real GitHub issues and corresponding repository changes. Tasks require codebase understanding, environment interaction and coordinated multi-file changes. This is the appropriate class of evidence for SignalCore's daily coding-agent claim.

SignalCore must report:

- exact SWE-bench dataset/version and harness commit;
- model/provider/reasoning configuration;
- environment image and repository commit;
- resolved percentage and verifier outcome;
- provider token/cost receipt;
- wall-time;
- baseline and SignalCore paired schedule;
- failures, retries and timeouts without exclusion.

Reference: Jimenez et al., *SWE-bench: Can Language Models Resolve Real-World GitHub Issues?*, ICLR 2024 / arXiv:2310.06770, https://arxiv.org/abs/2310.06770

Official harness: https://github.com/SWE-bench/SWE-bench

## SignalCore claim mapping

| SignalCore claim | Required external evidence | Internal evidence alone |
|---|---|---|
| External-history architecture | integrity, recovery and bounded-window tests | can verify implementation |
| Long-context quality | Oolong/LongBench/∞Bench-style external runs | cannot prove quality |
| Recursive advantage | paired baseline/recursive task receipts | cannot prove advantage |
| Daily coding-agent readiness | real repository tasks and SWE-bench-compatible runs | cannot prove readiness |
| Token/cost saving | provider usage receipts | estimates are insufficient |
| Wall-time improvement | end-to-end measured receipt | component microbenchmarks are insufficient |
| Result preservation | task verifier and quality non-inferiority | token reduction alone is insufficient |

## Fail-closed interpretation

The following remain closed until external receipts exist:

```text
EXTERNAL_SUPERIORITY_NOT_PROVEN
LONG_CONTEXT_QUALITY_NOT_PROVEN
MEASURED_AGENT_BENCHMARK_NOT_PROVEN
DAILY_CODING_AGENT_READINESS_NOT_PROVEN
PUBLIC_PRODUCT_MATURITY_NOT_PROVEN
```

The repository version remains **0.0.1 pre-release** regardless of how many internal components are implemented.
