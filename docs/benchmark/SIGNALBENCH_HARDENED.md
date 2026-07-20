# SignalBench hardened comparison

The hardened comparator closes four claim-governance gaps:

1. **Failure-inclusive utility:** failed attempts continue to consume quota. Aggregate efficiency is verified work divided by total quota, not a ratio over survivors only.
2. **Pair identity enforcement:** repository tree, prompt, verifier, permissions, cache mode, model, reasoning, context window and hardware hash must match.
3. **Usage receipts:** quota can be bound to request/response hashes and hardware identity through a tamper-evident receipt.
4. **Strict claim gate:** pass rate, security regression, verifier skip, successful-pair confidence interval, aggregate utility and receipt validity all gate superiority.

A receipt hash detects local modification but is not a provider cryptographic signature. Public claims should preserve raw provider usage responses or stronger signed attestations when available.

## CLI

```bash
python tools/signalbench_hardened.py \
  --results signalbench-results/results.json \
  --receipts signalbench-results/usage-receipts.json \
  --baseline-arm plain-host \
  --candidate-arm signalcore-adaptive \
  --output signalbench-results/comparison-hardened.json
```

A non-zero exit means superiority is not claimable; it does not necessarily mean the candidate is slower.
