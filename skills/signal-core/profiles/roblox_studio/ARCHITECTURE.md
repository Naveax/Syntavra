# Roblox Studio Profile Architecture

Maturity: **INTERNALLY_VERIFIED** for the local control plane and **SIMULATED** for external Studio execution.

```text
signed activation
→ RobloxTaskState V2
→ transitive capability authorization
→ evidence ledger and mandatory-role context selection
→ budgeted workflow DAG
→ simulated/transcript/live adapter boundary
→ independent validators
→ checkpoint and telemetry
```

The profile does not implement Roblox Studio, Studio MCP, Luau compilation, Creator Store, Blender, device emulation, DataStore migration, or provider models. Those remain external engines behind typed contracts.

## Trust boundaries

1. Ordinary prompts and IDE rules cannot activate the profile.
2. Signed activation binds transport, process, place, project, fingerprint, capabilities, issue/expiry time, and a single-use nonce.
3. Capability dependencies are included in authorization checks; implicit escalation is rejected.
4. Required evidence roles must fit the context budget or execution stops.
5. A model or engine response is never sufficient proof by itself.
6. Live adapters are disabled without explicit configuration and an authorized session.
