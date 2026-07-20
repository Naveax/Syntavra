# Adaptive Context Engine

## Objective

Reduce model-visible tool output without losing exact recoverability, critical failure evidence or long-session lineage.

## Pipeline

1. Normalize only safe shell commands. Pipes, command substitution, heredocs and multiline shell expressions bypass command-aware classification.
2. Classify known output families: git status/diff/log, tests, search listings, JSON, tables, code, logs and generic text.
3. Build a deterministic redacted preview under a profile byte budget.
4. Run a critical-evidence gate. Errors and relevant locations must survive as bounded excerpts.
5. Store original bytes in SignalCore's project-scoped content-addressed `EvidenceStore`.
6. Split exact data into addressable chunks and index chunk text with FTS5 when available.
7. Deduplicate identical observations within a scope and return a stable reference on repeat reads.
8. Optionally append an `adaptive-context` event to the SignalCore session chain.

## Profiles

| Profile | Preview budget | Passthrough threshold | Externalization threshold |
|---|---:|---:|---:|
| compact | 2 KiB | 384 B | 4 KiB |
| balanced | 4 KiB | 768 B | 8 KiB |
| audit | 8 KiB | 1.5 KiB | 16 KiB |

## Invariants

- Exact evidence is authoritative; previews are cacheable views.
- `restore(capture_id)` must equal the original byte stream.
- Reassembled chunks must equal exact evidence.
- Secret-like values are redacted from previews but retained in local exact evidence.
- Small outputs must not grow in model-visible size.
- Unknown and ambiguous commands fall back to content routing.
- Search returns exact chunk excerpts, not generated summaries.

## CLI

```bash
python tools/adaptive_context.py capture --command "pytest -q" --input pytest.log
python tools/adaptive_context.py search CAPTURE_ID "AssertionError auth.py"
python tools/adaptive_context.py verify CAPTURE_ID
python tools/adaptive_context.py restore CAPTURE_ID --output original.log
```
