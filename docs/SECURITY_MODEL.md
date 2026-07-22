# Syntavra Security Model

```text
agent request → policy evaluation → signed capability
→ native sandbox → constrained execution → artifact and receipt
```

Capabilities are tool-, argument-, resource- and session-bound, expiring, single-use by default, replay-protected and revocable. Provider credentials remain transport-only. Unsupported enforcement is reported honestly and fails closed when required.
