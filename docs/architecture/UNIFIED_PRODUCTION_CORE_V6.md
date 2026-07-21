# SignalCore 0.6.0 — Unified Production Core

## Canonical pipeline

```text
Host Event
  -> Canonical Request Envelope
  -> Identity / authorization / configuration provenance
  -> Security and DLP scan
  -> Exact encrypted evidence capture
  -> Structural and long-session context assembly
  -> Provider gateway with revision-bound cache identity
  -> Exact response or stream commitment
  -> Semantic data routing using valid typed envelopes
  -> Output governance
  -> Host delivery and structured observability
```

V6 makes this pipeline canonical. Older V2–V5 components remain compatibility implementations but are invoked behind the same evidence, configuration, policy and delivery boundaries.

## P0 guarantees

- Evidence is encrypted at rest with per-project HKDF-derived keys and AES-256-GCM authenticated encryption.
- Large evidence uses independently authenticated chunks, allowing bounded reads and corruption localization.
- Data routing always returns valid JSON. It never byte-slices a JSON document.
- Proxy control endpoints require authentication even on loopback.
- Remote proxy binding requires TLS certificate and key material.
- Default streaming mode commits evidence and completes DLP scanning before response headers are delivered.
- Container sandboxes require digest-pinned images, a non-root UID, dropped capabilities and `no-new-privileges`.
- Evidence write, quota, disk-full, authentication, migration and stream failures are fail closed.

## P1 production core

- One precedence-aware configuration schema with provenance and last-known-good rollback.
- Transactional SQLite migrations with pre-migration backup and integrity verification.
- Structured local traces, metrics, Prometheus export and redacted diagnostic bundles.
- Signed policy observations with shadow/canary/staged/full rollout and automatic rollback.
- Durable priority/dependency scheduler with leases, retries, dead-letter handling and recovery.
- Encrypted state backup, verification and transactional restore.
- Exact retention, pinning, GC and key rotation for evidence.
- Hybrid lexical/vector/graph/temporal retrieval with explainable ranking.

## P2 extensibility and scale foundations

- Permissioned, optionally signed and failure-quarantined plugin registry.
- Versioned schema registry and migration contracts.
- Modular MCP tool definitions with permission, approval, timeout, cost and sandbox metadata.
- Provider/model capability registry and revision/security/tool bound cache identity.
- Binary and multimodal exact envelopes.
- Central janitor and retention rule interface.
- Storage, provider, parser, verifier and sandbox extension boundaries are explicit rather than implicit monkeypatch contracts.

## A–Z / A²–Z² status

The detailed user requirement map is implemented as a release gate, not a marketing claim:

- **Closed in V6:** all listed P0 defects and the P1 foundations required to operate them safely.
- **Implemented as production interfaces:** configuration, migrations, observability, policy rollout, scheduler, retention, backup, identity, plugin and schema systems.
- **Still capability-dependent:** HTTP/2/3, cloud-native authentication, full semantic parsers for every language, distributed HA, native IDE UIs and cross-device synchronization require external runtimes or platform-specific releases. V6 exposes contracts and fail-closed capability reporting; it does not falsely report unavailable guarantees.

## Claim boundary

Internal benchmarks prove the committed mechanisms and invariants only. They do not establish superiority over another product or provider.


## Cryptographic boundary

Evidence objects use AES-256-GCM with project-scoped HKDF-derived keys. Encrypted backup bundles use the independently tested XChaCha20-Poly1305 utility. Integrity handles remain plaintext SHA-256 identifiers; raw object bytes are encrypted at rest.
