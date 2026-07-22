# Syntavra Universal Language Platform

Syntavra does not define language support as a closed list. Every decodable text file can enter the repository graph, while exact semantic claims are enabled only by verifiable evidence.

## Evidence ladder

```text
Binary / unreadable
  → metadata only; never represented as source

Unknown text language
  → lexical candidates and file-level navigation

Registered descriptor
  → stable language identity and lexical structure

Validated parser or sandboxed analyzer
  → syntax or semantic graph according to declared capability

Hash-pinned generic LSP
  → semantic document symbols and server-backed evidence

Fresh LSIF / SCIP index
  → exact imported semantic relations

Stale LSIF / SCIP index
  → rejected by default; candidate-only when explicitly allowed
```

`universal` means Syntavra does not discard a new text language and does not require a product release before that language can be described or connected to an analyzer. It does not mean an unknown grammar receives fabricated type or call-graph precision.

## Discovery order

Language identity may be discovered from:

1. exact filename;
2. multi-part suffix;
3. shebang;
4. editor modeline;
5. content probes for ambiguous shared suffixes;
6. repository descriptor;
7. safe unknown-text fallback.

When several languages share the same suffix and content evidence cannot resolve the ambiguity, Syntavra returns an `ambiguous:` identity and disables exact semantic claims. It never chooses the first registered language arbitrarily.

## Repository language descriptors

Place JSON descriptors in:

```text
.syntavra/languages/*.json
```

Example:

```json
{
  "id": "novalang",
  "suffixes": [".nova"],
  "filenames": ["NovaProject"],
  "shebangs": ["nova"],
  "aliases": ["nova"],
  "capabilities": ["lexical"]
}
```

Schema:

```text
schemas/language-descriptor.json
```

A descriptor establishes identity and detection. It does not execute code and does not independently establish semantic precision.

## In-process language adapters

Python entry points may use the group:

```text
syntavra.languages
```

Entry-point code is disabled by default. Loading requires:

```text
SYNTAVRA_ALLOW_LANGUAGE_PLUGINS=1
```

This path is intended for trusted, installed extensions. A failed extension is isolated to diagnostics; universal fallback remains available.

## Sandboxed analyzer protocol

A new language can provide a hash-pinned executable through:

```text
.syntavra/language-services/*.json
```

Example:

```json
{
  "id": "novalang-analyzer",
  "languages": ["novalang"],
  "command": ["./tools/novalang-analyzer", "--json"],
  "executable_sha256": "<64 hexadecimal characters>",
  "capabilities": ["syntax", "semantic", "definitions", "references"],
  "timeout_seconds": 30,
  "max_output_bytes": 8388608,
  "max_nodes": 250000,
  "max_edges": 1000000,
  "strict_native": true
}
```

Execution requires:

```text
SYNTAVRA_ALLOW_LANGUAGE_SERVICES=1
```

The executable receives one JSON request on standard input and returns one JSON graph on standard output. The command is argv-only; shell strings are forbidden. The executable hash, manifest hash, output size, graph size, network policy, environment and timeout are enforced.

Schema:

```text
schemas/language-service-manifest.json
```

## Generic LSP bridge

Any language server that implements standard LSP over stdio can be connected without language-specific Syntavra code:

```text
.syntavra/lsp-services/*.json
```

Example:

```json
{
  "id": "novalang-lsp",
  "languages": ["novalang"],
  "server_command": ["./tools/novalang-language-server", "--stdio"],
  "server_executable_sha256": "<64 hexadecimal characters>",
  "initialization_options": {},
  "timeout_seconds": 30,
  "max_message_bytes": 16777216,
  "max_output_bytes": 16777216,
  "strict_native": true
}
```

Execution requires:

```text
SYNTAVRA_ALLOW_LSP_SERVICES=1
```

The bridge validates JSON-RPC framing, message sizes, executable identity, document paths and normalized graph output. It runs through Syntavra's bounded process and sandbox layers.

Schema:

```text
schemas/lsp-service-manifest.json
```

## LSIF and SCIP

Syntavra accepts:

```text
LSIF JSONL
SCIP JSON export
```

Imported graph data has source ownership. Re-importing the same source atomically replaces only that source's prior nodes and edges. Local parser, LSP and runtime graph material remains intact.

The index repository commit is compared with the active repository commit:

- matching commit: exact semantic evidence;
- mismatching commit: rejected by default;
- explicit stale allowance: confidence-capped candidate evidence;
- stale data can never retain `exact_semantic: true`.

Binary `.scip` input requires a hash-pinned conversion service and is not parsed through an untrusted implicit converter.

Receipt schema:

```text
schemas/semantic-index-receipt.json
```

## Security invariants

- Unknown text remains navigable but not falsely exact.
- Binary input is never decoded as source.
- Ambiguous suffixes remain ambiguous without sufficient evidence.
- Shell command strings are never accepted for analyzers or LSP servers.
- Executable SHA-256 is mandatory.
- Dynamic execution requires explicit authorization.
- Provider or repository credentials are not inherited by analyzer processes.
- Network access is disabled unless a future explicit policy grants it.
- Standard output and error streams are bounded.
- Timeouts terminate the process tree.
- Node and edge counts are bounded.
- Paths escaping the repository are rejected.
- Imported node identities cannot overwrite unowned local graph nodes.
- Stale indexes cannot create exact semantic claims.

## Future-language onboarding

A language released after the current Syntavra build can be supported immediately in progressive steps:

1. Add a data-only descriptor for correct detection.
2. Use universal fallback for navigation and repository search.
3. Connect an existing LSP server through a hash-pinned manifest.
4. Connect a dedicated analyzer through the sandboxed protocol.
5. Import fresh LSIF or SCIP output when available.
6. Add conformance fixtures and receipts before declaring certified semantic support.

No step requires adding the language to a hard-coded product whitelist.
