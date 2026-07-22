# Syntavra Security Model

```text
agent request → policy evaluation → signed capability
→ native sandbox → constrained execution → artifact and receipt
```

Capabilities are tool-, argument-, resource- and session-bound, expiring, single-use by default, replay-protected and revocable. Provider credentials remain transport-only. Unsupported enforcement is reported honestly and fails closed when required.

## Dynamic language tooling

Language extensibility is treated as executable supply-chain input, not harmless configuration.

```text
data-only descriptor
  → no code execution

trusted Python entry point
  → disabled by default
  → explicit SYNTAVRA_ALLOW_LANGUAGE_PLUGINS authorization

sandboxed analyzer
  → argv-only command
  → mandatory executable SHA-256
  → explicit SYNTAVRA_ALLOW_LANGUAGE_SERVICES authorization
  → no network, filtered environment, bounded output and timeout

generic LSP server
  → argv-only command
  → mandatory executable SHA-256
  → explicit SYNTAVRA_ALLOW_LSP_SERVICES authorization
  → bounded JSON-RPC, bounded stderr, workspace path validation

LSIF / SCIP import
  → no executable code
  → bounded parser
  → repository path validation
  → commit freshness validation
  → source-owned atomic graph update
```

A backend that cannot prove a requested isolation property reports it as unsupported. Under strict-native policy, unsupported child-process prevention, filesystem isolation or network isolation causes the operation to fail before execution.

## Process-output safety

All sandboxed tool and analyzer output is drained through a bounded transport:

- separate stdout and stderr limits;
- process-tree termination when a limit is exceeded;
- explicit `output_limit_exceeded` receipt field;
- total bytes observed recorded separately from retained bytes;
- timeout terminates the process tree;
- output truncation can never be reported as successful execution.

## HTTP header boundary

Provider proxy headers are validated before transport and before downstream forwarding:

- field names must use valid HTTP token characters;
- CR, LF, NUL and prohibited controls are rejected;
- values must be Latin-1 encodable and size-bounded;
- credential header names and prefixes are validated;
- client-supplied request IDs are not reflected;
- hostile upstream headers are dropped before evidence storage or response forwarding;
- exception details are not reflected verbatim to clients.
