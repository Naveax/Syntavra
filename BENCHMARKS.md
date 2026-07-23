# Syntavra benchmarks

Syntavra is evaluated as a token and context optimization layer attached to an existing coding agent. Internal component checks verify mechanics; only externally executed paired arms may support a competitor claim.

## Internal component verification

```bash
python benchmarks/runtime_v03_benchmark.py \
  --output benchmarks/results/runtime-v03/internal.json
```

This verifies structural retrieval, token-budgeted repository context, reversible tool-output compaction, exact session recovery, profile enforcement, installer idempotency and SignalBench contracts. It is **not** a competitor benchmark and must retain a fail-closed claim such as `5X_NOT_PROVEN`.

## SignalBench external-arm benchmark

Freeze real repository trees, prompts, product versions, model identity, reasoning mode, cache policy, permissions and verifiers before execution:

```bash
syntavra signalbench validate --tasks tasks.json --arms arms.json
syntavra signalbench manifest --tasks tasks.json --arms arms.json --output manifest.json
syntavra signalbench run --tasks tasks.json --arms arms.json --repetitions 10
syntavra signalbench compare --results results.json --baseline plain-host --candidate syntavra-minimal
```

The provided task and arm files are templates. A public claim requires provider-reported usage, equal verified work, no skipped verifier, no security regression, every declared competitor arm completed, at least ten paired repetitions and the configured confidence-interval gate.

## Provider-billed evidence gate

A SignalBench result is claimable only when every valid baseline/candidate pair carries provider-observed usage and a linked receipt hash. Locally tokenized, estimated, or synthetic usage can test the harness but cannot open a public superiority claim. Cache writes, cache reads, fresh input, output, reasoning, quota cost, wall time, retries, tool calls, verifier success, and security regressions must all remain in the paired receipt.

```bash
syntavra prove provider-billed results.json --baseline plain-host --candidate syntavra-minimal
```

No provider-billed competitor result is bundled with the repository.
