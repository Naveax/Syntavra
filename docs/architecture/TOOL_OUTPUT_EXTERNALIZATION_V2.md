# Tool-Output Externalization V2

## Status

This subsystem is an exact-first, local-first tool-output virtualization layer. It does not claim superiority over external products until identical-model external arms are executed through SignalBench with provider usage receipts.

## Design goals

1. Keep raw tool output outside model context without losing a byte.
2. Make omitted evidence searchable and progressively revealable.
3. Preserve critical failures, locations and changed code in the first view.
4. Avoid sending identical or mostly unchanged output repeatedly.
5. Treat tool output as untrusted data, not model-control text.
6. Permit selective integrity verification without restoring the whole artifact.

## Architecture

### Exact artifact ledger

Every capture receives:

- a content-addressed exact evidence handle;
- deterministic artifact and stream identities;
- byte-exact, contiguous segment ranges;
- per-segment SHA-256 hashes;
- an artifact Merkle root;
- a policy hash and metadata receipt.

`verify()` reconstructs the complete artifact, checks contiguous byte ranges, validates every segment hash and recomputes the Merkle root.

### Semantic segmentation

Segmentation remains byte-exact while preferring useful boundaries:

- diff file and hunk boundaries;
- test failure boundaries;
- critical log records;
- code symbol boundaries;
- target-size grouping for large search results;
- fixed byte slices for binary content and pathological single lines.

The search-list strategy deliberately avoids one-segment-per-result behavior. An 18,000-result fixture exposed that design as an extreme retrieval-performance regression; target-size grouping removed it.

### Multi-resolution reveal

Supported lenses:

- `salient`
- `critical` / `failures`
- `changes` / `delta`
- `head`
- `tail`
- `query`
- `facets` / `schema`
- `all`

Large reveals use expiring, single-use continuation tokens. Tokens store server-side continuation state and cannot be altered to skip into arbitrary evidence.

### Search and context packing

Search supports artifact-local and scope-wide retrieval. Ranking combines:

- FTS5/BM25 candidate selection when available;
- exact phrase matches;
- lexical overlap;
- criticality and segment salience;
- `kind:`, `path:`, `error:` and `scope:` filters.

`search_pack()` assembles non-duplicate evidence into a strict model-visible byte budget and returns the exact segment handles used in the pack.

### Delta externalization

Captures from the same scope, tool, normalized command and path form a lineage. Segment-hash sequence comparison identifies unchanged and changed regions.

When at least half of the current segments are unchanged, the first model view becomes a delta view referencing the previous artifact while the full current artifact remains independently restorable.

Exact duplicates use a stable dedup reference instead of resending content.

### Untrusted-output firewall

The visible/indexed view:

- redacts common secret assignments;
- detects instruction-like prompt injection patterns;
- marks such output as untrusted;
- never modifies the exact local evidence object.

Binary data is never treated as control text. Its preview is bounded metadata plus a short hex head.

### Selective Merkle proof

`segment_proof()` returns the sibling path needed to prove that one revealed segment belongs to the artifact Merkle root. This permits verification of a selected result without reading the full output.

## Profiles

| Profile | Preview | Segment target | Reveal page | Use |
|---|---:|---:|---:|---|
| compact | 2 KiB | 8 KiB | 4 KiB | Maximum context economy |
| balanced | 4 KiB | 16 KiB | 8 KiB | Default coding-agent use |
| audit | 8 KiB | 32 KiB | 16 KiB | Larger first view and forensic review |

## CLI

```bash
python tools/tool_externalization.py capture --command "pytest -q" --input pytest.log
python tools/tool_externalization.py search "AssertionError path:tests" --scope-key default --pack-budget 4096
python tools/tool_externalization.py reveal --artifact-id ext-... --lens critical
python tools/tool_externalization.py proof ext-... 3
python tools/tool_externalization.py verify ext-...
python tools/tool_externalization.py restore ext-... --output original.bin
```

## Claim boundary

Internal byte-reduction fixtures measure model-visible bytes, exact reconstruction, retrieval and integrity behavior. They are not provider-token measurements and do not prove that SignalCore beats Context Mode, Headroom, Token Savior, Volt or Caveman Code.
