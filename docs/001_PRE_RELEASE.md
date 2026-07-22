# Syntavra 0.0.1 Pre-Release

```text
product: Syntavra
version: 0.0.1
channel: pre-release
surface: setup / status / run / prove
```

No component carries an independent product version. Compatibility is represented by schema hashes, migration identifiers and capability sets.

## Active universal language foundation

The active development branch implements a language-agnostic repository intelligence foundation:

- every decodable text language can be indexed through a conservative lexical fallback;
- unknown and future languages do not require a product release for initial navigation;
- ambiguous suffixes remain candidate-only without stronger evidence;
- repository language descriptors are data-only;
- in-process plugins are disabled unless explicitly authorized;
- hash-pinned analyzers execute through the sandbox and bounded process transport;
- hash-pinned generic LSP servers use bounded JSON-RPC over stdio;
- LSIF JSONL and SCIP JSON imports are source-owned and atomically replaceable;
- stale semantic indexes are rejected by default and cannot remain exact when explicitly allowed;
- binary SCIP requires an explicit hash-pinned conversion service.

The language foundation does not claim exact type resolution for every language. Exact definitions, references, implementations, overrides and call relations require validated parser, analyzer, LSP, LSIF, SCIP or runtime evidence.

Canonical contract: `docs/UNIVERSAL_LANGUAGE_PLATFORM.md`.

## Security remediation in progress

Code scanning alerts 1, 4 and 5 have root-cause remediations on the active pull request:

- clear-text secret-derived benchmark output was replaced with safe boolean verification fields;
- HTTP header names and values are validated before transport or response forwarding;
- CR/LF/NUL/control injection is rejected;
- client request IDs are not reflected;
- dynamic process output and execution time are bounded.

These alerts are not considered fixed on `main` until the pull request is merged and CodeQL analyzes the resulting `main` commit.

## Evidence gates

```text
EXTERNAL_SUPERIORITY_NOT_PROVEN
MEASURED_AGENT_BENCHMARK_NOT_PROVEN
LONG_CONTEXT_QUALITY_NOT_PROVEN
LIVE_INTEGRATION_CERTIFICATION_NOT_PROVEN
DAILY_CODING_AGENT_READINESS_NOT_PROVEN
PUBLIC_PRODUCT_MATURITY_NOT_PROVEN
```

The version and channel may not change without explicit owner instruction.
