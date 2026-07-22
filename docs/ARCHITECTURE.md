# Syntavra Architecture

```text
Context Compiler · Output Firewall · Artifact Store
Universal Language Platform · Semantic Intelligence · Runtime Evidence
Session Memory · Coding Agent · Capability Security · Execution Sandbox
Provider Gateway · Adapter Platform · Headless Runtime
Interactive Console · Reliability Laboratory · Distribution
SignalBench · Receipts · Metrics · Recovery
```

All components share one artifact model, session model, semantic graph, policy engine, receipt envelope, adapter contract, configuration hierarchy, metrics schema and recovery boundary.

## Universal language architecture

```text
File discovery
  → text / binary / encoding probe
  → filename / suffix / shebang / modeline / content detection
  → ambiguity-safe language identity
  → universal lexical fallback
  → trusted parser or sandboxed analyzer
  → hash-pinned generic LSP
  → fresh LSIF / SCIP semantic graph
  → runtime evidence and test impact
```

Language support is not a closed product whitelist. A new text language remains indexable and navigable before Syntavra knows its grammar. Exact semantic claims are upgraded only by validated evidence.

The semantic graph keeps evidence sources separate:

```text
local syntax ownership
sandboxed analyzer ownership
generic LSP ownership
LSIF / SCIP source ownership
runtime evidence ownership
```

Re-importing one semantic source cannot delete another source's graph. Imported node identities cannot overwrite unowned local nodes. Index commit mismatches are rejected by default and may only be imported as confidence-capped candidate evidence through an explicit stale override.

See `docs/UNIVERSAL_LANGUAGE_PLATFORM.md` for manifests, trust boundaries and future-language onboarding.

## Invariants

1. Raw evidence is never silently discarded.
2. Compact views retain exact artifact handles.
3. Summary loss cannot destroy exact history.
4. Unknown or unauthorized tools fail closed.
5. Provider credentials never enter agent-visible state.
6. Mutations are bounded and receipt-producing.
7. Unknown and future text languages remain navigable.
8. Ambiguous language identity cannot be presented as exact.
9. Dynamic analyzers and LSP servers require explicit authorization and executable hashes.
10. Process output, execution time, graph size and message size are bounded.
11. Stale semantic indexes cannot retain exact semantic status.
12. External claims remain closed until reproducible evidence passes.
