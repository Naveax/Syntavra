# ADR 0001: Dual Python and Rust Engines

- Status: Accepted
- Date: 2026-07-26

## Context

Syntavra has a broad Python runtime and needs a native implementation without removing the existing product or forcing a risky flag-day rewrite.

## Decision

Maintain two independent implementations behind one product identity:

- Python is the reference engine.
- Rust is introduced as an experimental native engine.
- Users will eventually select `python`, `rust` or `auto`.
- The engines share language-independent contracts rather than source code.

## Consequences

- Python remains fully supported.
- Rust may ship incrementally.
- Every public behavior needs parity fixtures.
- Maintenance cost is controlled through common schemas, generated catalogs and differential tests.
- Rust is not selected automatically until stable gates pass.
