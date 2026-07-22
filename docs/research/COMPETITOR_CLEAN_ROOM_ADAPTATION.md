# Competitor clean-room adaptation record

Syntavra's adaptive-context work was designed from publicly documented behavior and independently implemented against Syntavra's own exact-evidence, SQLite/WAL and claim-governance primitives. No competitor source file is vendored or imported.

## Provenance and license boundary

| Project | Public license at review time | Behavior studied | Syntavra implementation decision |
|---|---|---|---|
| Token Savior | MIT | Ordered command-specific compactors, safe no-match fallback, repeat-read economy | Independent classifier and deterministic preview functions |
| Headroom | Apache-2.0 | Content routing, reversible retrieval, cache-stable references | Existing `EvidenceStore` retained as source of truth; stable handles and chunk retrieval added |
| Volt / LCM | MIT | Immutable history, summary lineage and exact expansion | Adaptive capture events attach to Syntavra sessions; no Volt database/runtime code used |
| Caveman Code | MIT | Tool budgets, read deduplication and bounded output | Per-profile byte budgets and session-scoped content deduplication implemented independently |
| Context Mode | Elastic License 2.0 | Externalize large outputs and search them on demand | **No source code copied.** Only the public behavior was reimplemented clean-room using FTS5 with an exact scan fallback |

## Deliberate differences

Syntavra does not silently discard raw data. Every capture has a project-scoped content-addressed evidence object, byte-exact chunk reconstruction and an integrity check. Small outputs remain passthrough so a pointer header cannot cost more than the original. Ambiguous shell syntax is not command-classified. Critical error evidence is bounded per line so one pathological record cannot defeat the preview budget.

## Claim boundary

The adaptive-context benchmark is deterministic internal evidence. It is not a live provider-token benchmark and does not establish superiority over any project above. External superiority remains `NOT_PROVEN` until identical-model external arms, provider usage receipts and hardened SignalBench gates pass.
