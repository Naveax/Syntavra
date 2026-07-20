# Real repository task: python-attrs/attrs #1245

## Scope

SignalCore was used as the active execution layer for a real, open upstream bug:
`python-attrs/attrs#1245`, where `deep_iterable` and `deep_mapping`
reported only the top-level attribute name when a nested validator failed.

The selected source file was fetched from the current upstream `main` branch.
Its Git blob SHA, `0b1a294432d294c4f154be2d9439d825c3ec0781`, matched the local
baseline byte-for-byte.

## Result

- Baseline: 12 failed, 19 passed.
- Patched: 31 passed, 0 failed.
- Local patch commit: `5fcf0b2`.
- Exact evidence objects committed with the receipt: 4.
- Patch SHA-256: `37bfba6b76f02adedb1048bcbb442e826b866e16ae1e304b2d760e4df0e82208`.
- Claim: `FIXED_LOCALLY_VERIFIED`.

The accepted implementation preserves the original exception object and
structured arguments while adding paths such as `items[1]`,
`config["workers"][1]`, and `values.keys()[42]`.

## Performance rejection and accepted design

The first correct implementation created an evolved `Attribute` object for every
successful item and caused approximately 58x iterable and 55x mapping slowdown.
It was rejected.

The accepted implementation creates contextual metadata only after a validator
fails and uses an exact built-in `list` / `tuple` fast path. In seven alternating
process pairs with 100,000 successful items and eleven inner repetitions per arm:

| Path | Baseline median | Patched median | Relative change |
|---|---:|---:|---:|
| Deep iterable | 0.006177633 s | 0.007094554 s | +14.84% |
| Deep mapping | 0.017631619 s | 0.017885541 s | +1.44% |

The iterable overhead is approximately 0.92 ms per 100,000 validated items.

## SignalCore execution receipt

The complete development loop retained 67 measured command records:

- 61 commands exited successfully.
- 6 non-zero commands were retained rather than hidden.
- Sum of measured command wall time: 156.91 seconds.
- First-to-last measured command span: 1,253.54 seconds.
- Exact evidence verification: all records passed.
- Outputs at least 4 KiB: 80.51% model-visible byte reduction.

Non-zero records include intentional baseline failures, one development
regression, one third-party pytest plugin configuration failure, one whitespace
check failure, and one initial command-construction error.

## Claim boundary

This receipt counts as exactly one real repository task. It does not count as a
competitor arm or paired provider run and does not prove public superiority.
Provider input, output, reasoning-token, and dollar receipts were not exposed by
the execution environment and remain unset rather than estimated as facts.

The container could not resolve `github.com`, so an upstream clone and PR push
were impossible. Source and issue data were obtained through the GitHub
connector; the fix, tests, performance work, evidence capture, and local commit
were executed in the bash environment.
