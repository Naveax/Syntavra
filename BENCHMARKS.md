# SignalCore benchmarks

## Runtime 0.2 internal benchmark

```bash
python benchmarks/runtime_v02_benchmark.py \
  --output benchmarks/results/runtime-v02/internal.json
```

The committed run creates an 81-file call chain, a 350,000-line repeated test log, 256 immutable history events and a constrained context pack.

Measured internal deltas:

- affected-path recall: **2.47% direct-only → 100% transitive**;
- peak Python allocation: **30,005,283 B full-read → 27,818 B bounded single-pass**;
- visible output: **268 B**, with exact content-addressed evidence retained;
- summary-DAG recovery: **256/256 events from one root**;
- mandatory context roles: **satisfied**, with the raw log dropped.

The timing values are not a fair speed comparison because only the 0.2.0 path persists exact evidence. The result is internal implementation evidence only and must retain `"claim": "5X_NOT_PROVEN"`.

## Public paired benchmark

Use `signalcore benchmark compare` with observed axes and real quota telemetry. See `docs/benchmark/PROTOCOL.md`. A configured 20X/30X/100X file cannot pass a public claim by itself.
