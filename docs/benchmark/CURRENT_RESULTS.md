# Current benchmark status

SignalCore 0.2.0 includes a committed internal implementation benchmark at `benchmarks/results/runtime-v02/internal.json`.

| Check | Earlier internal path | SignalCore 0.2.0 | Result |
|---|---:|---:|---:|
| Affected-path recall on an 81-file call chain | 2.47% direct-only | 100% transitive | 40.5× recall factor |
| Peak Python allocation while processing a 350,000-line / 6,538,913-byte log | 30,005,283 B full-read | 27,818 B bounded single-pass | 1,078.63× lower measured peak |
| Visible tool result | unbounded raw path | 268 B | exact evidence retained |
| Immutable-history recovery | n/a | 256/256 events | one canonical root |
| Context mandatory roles | n/a | satisfied | raw log excluded |

The output timing values are diagnostic only: the earlier baseline does not persist exact evidence, while the 0.2.0 path hashes, atomically stores and summarizes the log in one pass. No speed-superiority claim is derived from that comparison.

This benchmark compares SignalCore implementation generations. It is not a Token Savior, Context Mode, Headroom, Volt, Aider or Caveman comparison.

Current public claim ceiling: **`5X_NOT_PROVEN`**. No live identical-model paired provider/quota corpus is committed.
