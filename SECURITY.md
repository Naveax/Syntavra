# Security policy

Syntavra is **0.0.1 pre-release** software. Security boundaries are fail-closed, but the project has not yet established long-term production maturity.

## Supported version

| Version | Status |
|---|---|
| 0.0.1 pre-release | Security fixes accepted |
| Any other version | Not an authorized Syntavra release |

The version remains locked until the repository owner explicitly authorizes a change.

## Private reporting

Use a private GitHub security advisory. Do not place working exploits, credentials, private keys, provider tokens, private repository content or unredacted logs in a public issue.

Include:

- affected commit and operating system;
- minimal reproduction;
- expected and actual security boundary;
- whether credentials, remote execution, sandbox escape, receipt forgery or data exposure is involved;
- a proposed mitigation when available.

## Response targets

These are project targets, not a service-level guarantee:

- acknowledge a valid private report within 7 days;
- classify severity and reproduction status within 14 days;
- coordinate disclosure after a fix or mitigation is available.

## Design constraints

- no `eval`, `exec`, pickle or shell-string execution for untrusted data;
- external content is data, not trusted instructions;
- provider credentials remain transport-only and are excluded from analytics;
- remote proxy bindings require TLS;
- control-plane authentication is separate from provider authentication;
- large evidence uses bounded reads and content hashes;
- SQLite uses parameterized queries, migrations, WAL and bounded transactions;
- platform installation is backup-first, verified and rollback-capable;
- MCP visibility is not trusted as authorization; every tool call is rechecked;
- unsandboxed execution is disabled by default;
- benchmark and integration claims require validated external receipts.

## Supply-chain controls

Pull requests run deterministic dependency installation, TypeScript and installer tests, Python platform matrices, CodeQL, dependency review, manifest verification and package validation. Pre-release artifacts are built with checksums, SBOMs, reproducibility checks and GitHub artifact provenance.

No workflow may commit or push generated manifest changes directly to `main`.
