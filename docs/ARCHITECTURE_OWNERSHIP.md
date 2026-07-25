# Syntavra Runtime Ownership

This document is normative for the 0.0.1 pre-release runtime. Compatibility modules may delegate to these owners, but they must not establish a second source of truth.

| Capability | Canonical owner | Compatibility boundary |
|---|---|---|
| Repository nodes and edges | `CanonicalRepositoryGraph` | Legacy structural/code graph classes are analysis adapters only. |
| Indexed repository search | `RepositoryQueryEngine` | Full-table Python scans are forbidden on the product query path. |
| Language syntax | Built-in Python AST and `TreeSitterLanguageAdapter` | Regex discovery is candidate evidence only. |
| Cross-file semantic evidence | LSP, LSIF and SCIP adapters | Syntax adapters may not upgrade candidate calls or references to exact semantic facts. |
| Terminal output | `TerminalOutputEngine` | Older compactors are parser adapters behind the canonical engine. |
| Exact output recovery | `ArtifactStore` | Compact views never replace exact artifacts. |
| Project/test discovery | `ProjectModel` | File-extension-only verifier selection is candidate evidence. |
| Model transport | `ModelGateway` | Provider-specific payloads remain behind the gateway contract. |
| Coding-agent orchestration | `AgentRuntime` | `agent replay` remains a deterministic patch-replay/debugging surface. |
| Patch/test/rollback safety | `AutonomousCodingAgent` | Model gateways cannot bypass authorization, sandboxing, budgets or receipts. |

## Required invariants

1. Compact terminal output must never be larger than the sanitized original output when the original fits the visible budget.
2. Exact artifacts are content-addressed and recoverable after every lossy view.
3. Unknown or syntax-only language evidence is never labelled exact semantic evidence.
4. Repository query execution is indexed and bounded; it does not load the complete node table into Python.
5. Streaming table and terminal paths do not retain the complete payload in memory.
6. A model-backed agent discovers a project verifier before mutation and cannot execute an unverified patch.
7. Every repair attempt receives the current workspace diff and changed-file set.
8. Adapter counts and command-registration counts are inventory, not proof of live integration or independent superiority.
