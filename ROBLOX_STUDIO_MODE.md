# SignalCore Roblox Studio Mode

Status: **INTERNALLY_VERIFIED control plane / SIMULATED external execution**.

The hidden `roblox_studio` profile is a governance and verification layer for authorized Roblox Studio tooling. It cannot be activated by Codex, Claude Code, Cursor, Gemini CLI, Markdown rules, CLI flags, or unsigned JSON.

Activation requires a short-lived HMAC-SHA256 envelope binding the Studio process, transport identity, place, project, project fingerprint, explicit capability subset, issue/expiry times, and a single-use nonce.

See:

- [Architecture](skills/signal-core/profiles/roblox_studio/ARCHITECTURE.md)
- [Benchmark results](skills/signal-core/profiles/roblox_studio/BENCHMARK_RESULTS.md)
- [Completion report](skills/signal-core/profiles/roblox_studio/COMPLETION_REPORT.md)
- [Transfer failure audit](docs/audits/ROBLOX_PROFILE_TRANSFER_FAILURE.md)
