# Syntavra Architecture

```text
Context Compiler · Output Firewall · Artifact Store
Semantic Intelligence · Runtime Evidence · Session Memory
Coding Agent · Capability Security · Execution Sandbox
Provider Gateway · Adapter Platform · Headless Runtime
Interactive Console · Reliability Laboratory · Distribution
SignalBench · Receipts · Metrics · Recovery
```

All components share one artifact model, session model, semantic graph, policy engine, receipt envelope, adapter contract, configuration hierarchy, metrics schema and recovery boundary.

## Invariants

1. Raw evidence is never silently discarded.
2. Compact views retain exact artifact handles.
3. Summary loss cannot destroy exact history.
4. Unknown or unauthorized tools fail closed.
5. Provider credentials never enter agent-visible state.
6. Mutations are bounded and receipt-producing.
7. External claims remain closed until reproducible evidence passes.
