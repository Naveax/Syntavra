# Roblox Studio Mode — locked domain profile

This profile is intentionally hidden from normal Agent Skill discovery and cannot
be activated by a CLI flag, IDE prompt, or direct profile name.

## Activation contract

The external Roblox Studio bridge must:

1. Pair once with Syntavra and provision a private local pairing key.
2. Confirm a live Roblox Studio process and obtain Studio/project identity.
3. Mint a short-lived HMAC-SHA256 activation envelope.
4. Send the envelope through the `roblox_studio_bridge` transport.
5. Request only capabilities listed in `profile.json`.

Syntavra verifies the signature, Studio host/transport, process identity, Place
and project identity, TTL, capability subset, and a single-use nonce. The profile
fails closed when any check fails.

## Important security boundary

This gate prevents accidental or ordinary activation from Codex, Claude Code,
Cursor, VS Code, terminals, and other IDE/CLI hosts. It is not a DRM system and
cannot protect against a local operating-system user who can read the pairing
secret or modify Syntavra's source. Stronger local-adversary resistance requires
OS keychain storage and native process attestation in the external bridge.

The profile does not implement a Roblox plugin, MCP server, Luau compiler,
playtest engine, spatial solver, or content generator. Those remain external.
