# Python–Rust Full Parity Program (R23–R37)

## Goal

Syntavra's Rust engine must expose the same user-observable product as the Python engine: commands, options, results, errors, configuration, state, SQLite behavior, receipts, process execution, MCP tools, setup/repair, host integrations, benchmarks and packaging.

Implementation language and internal architecture may differ. Observable behavior may not.

## Non-negotiable rules

- Python remains the reference engine until R37 certification.
- Rust must not shell out to Python or require a Python interpreter for a claimed Rust implementation.
- Hidden fallback is forbidden.
- A feature is not parity-proven without a shared contract, differential fixtures and a real Rust-binary test.
- A feature is not production-ready without Windows, Linux, macOS Intel and macOS ARM validation.
- Product version remains `0.0.1` and release channel remains `pre-release` until explicit owner instruction.
- Full parity is not an average. CLI, MCP, state mutation, host/setup and platform/package dimensions must each be complete.

## Program model

All R23–R37 workstreams are active together, but merges follow the dependency graph in `contracts/parity/python-rust-full-parity-v1.json`. A single monolithic implementation PR is forbidden because it would make security review, rollback and parity attribution unreliable.

Each workstream uses a dedicated branch and PR stacked on the latest validated dependency. The catalog is the canonical source of status.

## Workstreams

| Phase | Scope |
|---|---|
| R23 | Full surface catalog, exporters and freeze gate |
| R24 | Complete read-only CLI parity |
| R25 | Config and profile lifecycle parity |
| R26 | State and receipt write parity |
| R27 | Broker SQLite mutation parity |
| R28 | Process broker parity |
| R29 | Context rewrite and compaction parity |
| R30 | Memory and repository intelligence parity |
| R31 | Provider gateway and routing parity |
| R32 | Complete MCP parity |
| R33 | Setup, install, repair and host parity |
| R34 | Benchmark, SignalBench and evidence parity |
| R35 | Publication and registry parity |
| R36 | Python-free Rust standalone distribution |
| R37 | Production parity certification |

## Required package for every parity feature

1. Shared versioned contract.
2. Python reference fixture set.
3. Native Rust implementation.
4. Unit and property regressions.
5. Cross-engine differential verifier.
6. Real Cargo-backed verifier.
7. Failure, security and no-fallback fixtures.
8. ADR and catalog update.
9. Dedicated CI gate.
10. Aggregate parity integration.
11. Exact manifest update.
12. Windows/Linux/macOS package validation.

## Status model

- `PYTHON_ONLY`: no native Rust implementation.
- `RUST_SCAFFOLDED`: Rust ownership exists but behavior is incomplete.
- `RUST_SHADOW`: native Rust executes in non-authoritative comparison mode.
- `PARITY_PROVEN`: contract and differential fixtures pass.
- `RUST_PRODUCTION_READY`: parity plus complete platform, packaging and failure certification.

## Merge policy

A workstream PR remains draft until its exact final head passes all permanent workflow families. Mutating authority cannot be granted by documentation or capability declaration alone. Rust authority is enabled only after the corresponding write/process/MCP safety contract is proven.

## Final R37 acceptance

The `FULL_PARITY_PROVEN` claim may replace `FULL_PARITY_NOT_PROVEN` only when:

- every catalog feature is `RUST_PRODUCTION_READY`;
- every parity dimension is complete;
- Python-free Rust-only installation passes;
- differential, fuzz, fault-injection, crash-recovery and soak suites pass;
- upgrade and downgrade compatibility pass in both directions;
- no Rust implementation invokes Python.
