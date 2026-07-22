# Syntavra Unified Engineering Plan

One product, one architecture, one development branch, one pull request and one version identity.

## Integrated workstreams

- Universal Language Platform, Semantic Intelligence and Runtime Evidence
- Context Compiler, Output Firewall and exact Artifact Store
- Session Memory and long-context continuity
- Capability Security, Provider Gateway and Execution Sandbox
- Coding Agent, headless/remote execution and interactive operations
- Adapter Platform, provider/framework surfaces and live certification
- Reliability Laboratory, atomic distribution, rollback and SignalBench

All workstreams share the same configuration, artifact, receipt, policy, session, semantic and metrics contracts. They are not independent products or separately versioned components.

## Universal language completion boundary

The language workstream is complete only when all of the following remain true on the same final SHA:

- no product whitelist is required to index a text language;
- a never-before-seen language receives safe file, lexical and identifier navigation;
- ambiguous suffixes cannot silently select an incorrect language;
- repository descriptors can add a language without executing code;
- trusted in-process plugins require explicit authorization;
- external analyzers and LSP servers require executable hashes and explicit authorization;
- analyzer/LSP execution is time-, memory-, network-, output- and workspace-bounded;
- LSIF and SCIP imports are atomic, source-owned and commit-aware;
- stale indexes cannot create exact semantic evidence;
- binary data cannot be represented as source;
- local parser nodes cannot be overwritten by imported graph identities;
- future-language fixtures pass on Linux, Windows and macOS;
- exact and candidate evidence remain distinguishable through the CLI and receipts.

## Security remediation boundary

Existing CodeQL alerts are closed only after:

1. source-level remediation;
2. explicit regression tests;
3. CodeQL success on the pull-request final SHA;
4. merge to `main`;
5. CodeQL analysis on the merged `main` commit showing the alerts fixed.

Alerts must not be dismissed to obtain a green dashboard.

## Final merge boundary

The final user-authored SHA must pass unit, integration, cross-platform, package, portable, semantic, session, agent, sandbox, adapter, update, fault, manifest, CodeQL, dependency, version-lock and claim-boundary checks.

Internal or synthetic measurements cannot open external superiority, long-context quality, live integration, daily readiness or public maturity gates.
