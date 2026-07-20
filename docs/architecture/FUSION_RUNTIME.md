# SignalCore 0.2.0 runtime architecture

SignalCore separates probabilistic planning from deterministic runtime control. The model may request work, but the runtime owns process lifetime, exact output storage, structural evidence, context budgets, memory scope, history integrity, verifier identity and benchmark receipts.

## Control path

```text
host negotiation
→ project identity and state health
→ structural/exact/scoped-memory retrieval
→ mandatory context packing
→ hook or MCP process control
→ exact streaming output evidence
→ completion cursor and rollout telemetry
→ immutable summary DAG
→ verifier graph
→ observed benchmark receipt
```

## Activation truth

- `HOOK_ENFORCED`: the host exposes pre/post tool hooks and result replacement.
- `MCP_CONTROLLED`: SignalCore commands and evidence are routed through the MCP server.
- `PROXY_CONTROLLED`: a provider/runtime proxy owns the enforcement boundary.
- `INSTRUCTION_ONLY`: only the skill/rule text is active; no runtime guarantees apply.
- `UNSUPPORTED`: no usable integration surface was detected.

Codex is reported as `MCP_CONTROLLED`, not hook-enforced. Claude Code may be `HOOK_ENFORCED` when hooks are actually installed.

## Mandatory health

`RUNTIME_ACTIVE` requires the skill, runtime package, SQLite state, exact evidence, process broker, output firewall, context governor, hook engine, MCP server, host adapter and any explicitly required rollout source. Missing mandatory components produce `RUNTIME_DEGRADED` or `RUNTIME_FAILED`; they are never silently upgraded to active.
