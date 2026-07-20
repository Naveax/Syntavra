# Context governor

SignalCore 0.2.0 uses two layers:

1. **Pressure policy** combines context utilization, churn and evidence pressure. Actions begin with duplicate/raw-success eviction and escalate through evidence externalization, phase capsule creation, summary-DAG updates, controlled handoff and mandatory split.
2. **Context packer** selects concrete sections under a token budget. Explicit mandatory items and mandatory roles fail closed. Dependencies are expanded before optional selection. Optional material is ranked by confidence-weighted utility per token with deterministic tie-breaking and a local replacement pass.

Stable sections are ordered first and hashed into a deterministic stable-prefix identity. Full logs remain in exact evidence and are represented by recovery handles rather than injected as raw context.
