# SignalCore 0.3.0 unified runtime

The v0.3 runtime joins eight previously separate product concerns into one project-scoped pipeline:

```text
host detection + reversible installer
→ lifecycle hook or MCP enforcement
→ semantic/structural repository graph
→ secure sandbox or durable zero-poll broker
→ exact evidence + reversible content router
→ scoped memory + immutable session DAG
→ output contract governor
→ SignalBench claim governance
```

Each layer reports degraded operation. Instruction-only activation, local-restricted sandbox execution, lexical parser fallback, missing usage telemetry and incomplete benchmark identity are represented explicitly rather than promoted to full guarantees.

## Structural identity

Files use content hashes. Symbols carry language, parser, qualified name, signature and confidence. Edges are typed and may include target paths and metadata. Optional semantic snapshots supplied by an LSP/compiler outrank local parsers; otherwise Python AST or language-specific parsing is used.

## Exactness

Command outputs and compressed content are stored in the project evidence store before a bounded model-visible view is returned. Session summaries are materialized views over immutable events. Verifier and benchmark results retain exact hashes.
