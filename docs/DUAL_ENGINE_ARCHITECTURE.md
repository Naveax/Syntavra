# Syntavra Dual-Engine Architecture

Status: **R4 selector implemented / Python remains default**  
Product identity: **0.0.1 / pre-release / version locked**

## Objective

Syntavra remains one product with two independent runtime implementations:

- **Python engine** — maintained reference implementation.
- **Rust engine** — native implementation that must prove behavioral, protocol and state parity before becoming default.
- **Engine selector** — active R4 boundary for `python`, `rust` and `auto` modes.

The Python runtime is not removed. A user must always be able to select it explicitly.

## Non-negotiable rules

1. Product version and release channel are shared; engine versions are implementation metadata.
2. Public contracts live under `contracts/` and are language independent.
3. A mutating operation selects one engine before its first state write.
4. Fallback is allowed only after a preflight reports `unsupported` and before mutation.
5. Runtime errors after mutation fail closed; they never trigger hidden re-execution in the other engine.
6. Python remains the reference until Rust passes all stable gates.
7. Unknown contract, selector or state schema versions fail closed.
8. Logical SQLite records define parity; physical database page layout does not.
9. Cryptographic formats require byte-level cross-engine vectors.
10. `auto` may prefer Rust only after the command, platform, state schema and parity gates are compatible.

## Repository layout

```text
crates/
  syntavra-contracts/  Shared Rust-side contract constants
  syntavra-core/       Canonical primitives
  syntavra-cli/        Experimental `syntavra-rs` binary
contracts/
  engine/              Engine capability, descriptor and selector contracts
  cli/                 CLI result contracts
  mcp/                 MCP catalog contracts
  state/               Shared state ownership rules
  receipts/            Cross-engine receipt fields
parity/
  normalizers/         Strict nondeterministic-field normalization
syntavra_runtime/
  engine_entry.py      Installed command boundary
  engine_cli.py        R4 selector commands
  engine_selector.py   Resolution, persistence and verification
```

## Engine states

| State | Meaning |
|---|---|
| `reference` | Canonical Python behavior |
| `experimental` | Compiles and runs; no compatibility promise |
| `preview` | Selected read-only surfaces have parity evidence |
| `beta` | Mutating and recovery paths have multi-platform parity |
| `stable` | All mandatory gates pass and rollback is proven |

The Rust engine remains `experimental`. It exposes only:

```text
syntavra-rs version
syntavra-rs engine capabilities
syntavra-rs engine contract-hash
```

## R4 selection contract

Selection precedence is fixed:

```text
--engine
> SYNTAVRA_ENGINE
> project engine config
> user engine config
> builtin default
```

The project file is `.syntavra/engine.json`. The user file is resolved from the platform configuration directory. Both use `contracts/engine/selection.schema.json`.

R4 resolution is intentionally conservative:

| Requested | Resolved | General command behavior |
|---|---|---|
| `python` | `python` | Existing Python command path |
| `auto` | `python` | Existing Python command path |
| `rust` | `rust` | Fail closed until routed parity phases land |

Rust can be persisted only after the binary proves product identity, `0.0.1 / pre-release`, contract version, exact read-only capability rows and descriptor SHA-256. Missing binaries, malformed output, unknown selector values and unsupported schemas fail closed.

The installed surface is:

```text
syntavra engine list
syntavra engine status
syntavra engine use python|rust|auto
syntavra engine verify
```

## Shared state policy

The engines will share the existing `.syntavra` state only after explicit compatibility gates exist. Until then, the Rust bootstrap is read-only and does not open runtime databases.

Future mutating operations require:

- database-specific writer locks;
- common SQL migration files;
- backup-before-migration;
- contract and schema preflight;
- no fallback after the first mutation;
- cross-engine recovery tests.

## Contract authority

`contracts/engine/descriptor.txt` is the canonical engine descriptor. The Rust binary embeds exactly the same bytes and reports their SHA-256 digest. `tools/verify_dual_engine_contract.py` rejects descriptor drift.

`contracts/engine/selection.schema.json` is the canonical persisted selector format. `tools/verify_engine_selector.py` freezes precedence, Python-default auto policy and fail-closed handling.

The Python runtime remains the source of truth for the current command and MCP inventory. `tools/export_python_contract.py` exports that surface for snapshot and code-generation gates.

## Delivery phases

1. **R0** — architecture and fail-closed rules.
2. **R1** — Python reference inventory.
3. **R2** — schemas, normalizers and parity runner.
4. **R3** — Rust workspace and read-only bootstrap commands.
5. **R4** — engine selector with Python default. **Implemented.**
6. **R5** — canonical primitives and exact hashing parity.
7. **R6** — config, identity and status parity.
8. **R7** — MCP transport and read-only tools.
9. **R8+** — state, evidence, process, structural, session, sandbox and installer parity.

## Stable gate

Rust cannot become the `auto` preference until all of these pass on Windows, Linux and macOS:

- CLI contract parity;
- MCP discovery and error parity;
- config hash and provenance parity;
- shared-state migration and rollback;
- Python-to-Rust and Rust-to-Python evidence compatibility;
- process cancellation, timeout and orphan recovery;
- session continuation and lineage verification;
- installer rollback and explicit host targeting;
- no hidden fallback after mutation.

## Current claim boundary

```text
RUST_ENGINE_EXPERIMENTAL_NOT_DEFAULT
RUST_GENERAL_COMMAND_ROUTING_BLOCKED_R4
RUST_STATE_COMPATIBILITY_NOT_PROVEN
RUST_MCP_PARITY_NOT_PROVEN
RUST_CRYPTO_PARITY_NOT_PROVEN
RUST_PROCESS_PARITY_NOT_PROVEN
```
