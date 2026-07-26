# ADR 0005: Canonical Primitive Parity

- Status: accepted for R5
- Product: Syntavra `0.0.1 / pre-release`
- Reference engine: Python
- Candidate engine: Rust

## Context

Cross-engine routing cannot be trusted until both implementations produce identical bytes and hashes for the same logical input. Platform path syntax and line-ending conversion are especially dangerous because a result can appear valid while producing a different digest on Windows, Linux, or macOS.

## Decision

R5 freezes a dependency-free primitive boundary shared by Python and Rust:

1. SHA-256 operates on exact input bytes and emits lowercase hexadecimal.
2. Repository paths are lexical, relative, slash-separated paths.
3. Backslashes are converted to `/`; duplicate separators and `.` segments are removed.
4. absolute paths, drive prefixes, parent traversal, empty paths, and NUL fail closed.
5. path case and Unicode code points are preserved; R5 performs no case folding or Unicode normalization.
6. UTF-8 text converts CRLF and lone CR to LF before manifest hashing.
7. NUL-containing and invalid UTF-8 payloads remain byte-exact.
8. `benchmarks/results/real-tasks/**` remains byte-exact even when a payload is valid UTF-8.

The authoritative vectors live in `parity/fixtures/primitives-v1.json`. Python validates the fixture directly, and `tools/verify_primitive_parity.py` builds the Rust binary and compares both engines against every vector.

## Scope boundary

The Rust primitive commands are diagnostic parity probes. They do not enable general Rust command routing, shared database access, mutation, or automatic selection. Python remains the reference and `auto` remains Python.

## Consequences

- Manifest hashes are deterministic across supported operating systems.
- Future state, evidence, receipt, and configuration formats can reuse a proven byte/path layer.
- Any change to normalization semantics requires a new fixture schema or fixture generation, not an in-place reinterpretation of existing vectors.
- Filesystem resolution, symlink handling, platform case sensitivity, and canonical JSON are separate future contracts.
