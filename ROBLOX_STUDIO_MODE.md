# SignalCore Roblox Studio Mode

The Roblox Studio domain profile is **locked by default**. It is not a normal
cross-platform Agent Skill entry point.

## What can activate it

Only an authorized external Roblox Studio bridge can activate the profile by
presenting a short-lived signed session envelope containing:

- Roblox Studio session ID
- Place/project identity
- project fingerprint
- Roblox Studio process ID
- explicit capability subset
- issue and expiry timestamps
- single-use nonce
- HMAC-SHA256 signature

## What cannot activate it

- `--profile roblox_studio`
- a Codex/Claude/Cursor/VS Code prompt
- raw JSON without a valid signature
- an expired or replayed envelope
- a non-Roblox host or transport
- a request for unapproved capabilities
- a session without Roblox Studio process attestation

## Pairing key

The pairing secret is generated locally under SignalCore state and is never
committed to the repository. The external bridge owns the pairing workflow.
SignalCore exposes library functions but no CLI command that prints or accepts the
secret.

## Flow

```text
Roblox Studio
    │
    ▼
Authorized Studio bridge
    │  signed, short-lived, single-use envelope
    ▼
SignalCore profile loader
    │  signature + process + project + capability + replay checks
    ▼
profiles/roblox_studio
```

This is an activation boundary, not a replacement for the Roblox Studio plugin,
MCP, or external execution system.
