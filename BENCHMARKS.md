# Syntavra benchmarks

## v0.3 internal component benchmark

```bash
python benchmarks/runtime_v03_benchmark.py \
  --output benchmarks/results/runtime-v03/internal.json
```

It verifies multi-language parser fixtures, transitive structural impact, token-budgeted repository maps, reversible content restoration, session-DAG exact recovery, installer idempotency, sandbox-policy disclosure, output contracts and the SignalBench protocol.

This is not a competitor benchmark. It must retain `"claim": "5X_NOT_PROVEN"`.

## SignalBench external-arm benchmark

Use `benchmarks/signalbench/tasks.example.json` and `arms.example.json` as schemas. Pin real repository trees, product versions, model identity and verifiers before running:

```bash
syntavra signalbench validate --tasks tasks.json --arms arms.json
syntavra signalbench manifest --tasks tasks.json --arms arms.json --output manifest.json
syntavra signalbench run --tasks tasks.json --arms arms.json --repetitions 10
syntavra signalbench compare --results results.json --baseline plain --candidate syntavra-v030
```

A public claim requires equal verified work, no verifier skip, no security regression, actual quota data, at least ten paired repetitions and the configured confidence-interval gate.
