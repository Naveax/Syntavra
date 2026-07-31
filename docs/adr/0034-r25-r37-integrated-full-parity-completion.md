# ADR 0034: Integrated R25-R37 full-parity completion

## Status

Accepted for the owner-directed R25-R37 completion program.

## Context

The parity catalog originally required one pull request per phase. The owner explicitly directed completion of every unfinished R architecture in one delivery. A flat monolithic implementation would still be unsafe: state mutation, process execution, networking, host changes, packaging and certification have different authority and rollback boundaries.

## Decision

Use one integration pull request while preserving the R25-R37 dependency graph as independently testable internal slices.

The integrated runtime is defined by `contracts/parity/full-parity-runtime-v1.json` and implemented independently by Python and native Rust. Every request is project-bound, canonical, size-bounded and phase-tagged. Rust may not invoke Python or use hidden fallback.

The delivery contains these authority boundaries:

- R25-R27 grant only project-local profile, state, receipt and SQLite mutation.
- R28 grants only bounded self-child process modes; arbitrary shell execution is not part of the contract.
- R31 grants loopback networking only.
- R33 grants only project-local host-plan artifacts with transaction-bound rollback.
- R36 packages a Python-free Rust binary and rejects Python files or libpython dependencies.
- R37 may emit `FULL_PARITY_PROVEN` only after exact cross-engine, resilience, upgrade, standalone and four-platform package gates pass.

Each phase remains separately attributable through operation inventories, stable error codes, mutation flags, receipts and CI evidence. Failure in a later phase does not authorize or conceal failure in an earlier phase.

## Consequences

The historical one-PR-per-phase rule is superseded only for this owner-directed completion PR. Future parity work returns to phase-scoped pull requests unless the owner directs otherwise.

Product identity remains `0.0.1` pre-release. The Python engine remains the reference implementation until the final R37 catalog update. No version, release-channel or automatic engine-selection change is authorized by this ADR.
