# Syntavra Dual-Engine Architecture

Status: **R11 safe read-only routing implemented for `version` / Python remains default**  
Product identity: **0.0.1 / pre-release / version locked**

## Objective

Syntavra remains one product with two independent runtime implementations:

- **Python engine** — maintained reference implementation.
- **Rust engine** — native implementation that must prove behavioral, protocol and state parity before becoming default.
- **Engine selector** — active boundary for `python`, `rust` and `auto` modes.
- **Read-only router** — R11 capability whitelist for individually proven routes.

The Python runtime is not removed. A user must always be able to select it explicitly.

## Non-negotiable rules

1. Product version and release channel are shared; engine versions are implementation metadata.
2. Public contracts live under `contracts/` and are language independent.
3. A mutating operation selects one engine before its first state write.
4. Fallback is allowed only after a preflight reports `unsupported` and before mutation.
5. Runtime errors after mutation fail closed; they never trigger hidden re-execution in the other engine.
6. R11 read-only route failures also fail closed and are never re-executed in Python.
7. Python remains the reference until Rust passes all stable gates.
8. Unknown contract, selector, routing or state schema versions fail closed.
9. Logical SQLite records define parity; physical database page layout does not.
10. Cryptographic formats require byte-level cross-engine vectors.
11. `auto` may prefer Rust only after the command, platform, state schema and parity gates are compatible.

## Repository layout

```text
crates/
  syntavra-contracts/  Shared Rust-side contract constants
  syntavra-core/       Canonical primitives
  syntavra-cli/        Experimental `syntavra-rs` binary
contracts/
  engine/              Engine capability, descriptor, selector and routing contracts
  cli/                 CLI result contracts
  mcp/                 MCP catalog contracts
  state/               Shared state ownership rules
  receipts/            Cross-engine receipt fields
parity/
  normalizers/         Strict nondeterministic-field normalization
syntavra_runtime/
  engine_entry.py      Installed command boundary
  engine_cli.py        Selector and R11 route commands
  engine_selector.py   Resolution, persistence and verification
  read_only_router.py  Capability-whitelisted no-fallback router
```

## Engine states

| State | Meaning |
|---|---|
| `reference` | Canonical Python behavior |
| `experimental` | Compiles and runs; no general compatibility promise |
| `preview` | Selected read-only surfaces have parity evidence |
| `beta` | Mutating and recovery paths have multi-platform parity |
| `stable` | All mandatory gates pass and rollback is proven |

The Rust engine remains `experimental`. Its direct binary exposes these proven read-only capability rows:

```text
config.resolve
engine.capabilities
engine.contract-hash
receipt.inspect
state.broker-live-snapshot
state.broker-snapshot
state.inspect
state.layout
status
version
```

Direct Rust binary availability does not imply installed product-command routing. R11 initially routes only:

```text
syntavra --engine python engine route version
syntavra --engine rust engine route version
```

All other `engine route` commands fail closed as unsupported. Existing product commands continue through the Python reference runtime unless a later route contract explicitly admits them.

## Selection contract

Selection precedence is fixed:

```text
--engine
> SYNTAVRA_ENGINE
> project engine config
> user engine config
> builtin default
```

The project file is `.syntavra/engine.json`. The user file is resolved from the platform configuration directory. Both use `contracts/engine/selection.schema.json`.

Current resolution remains conservative:

| Requested | Resolved | General behavior |
|---|---|---|
| `python` | `python` | Python reference command path |
| `auto` | `python` | Python reference command path |
| `rust` | `rust` | Selector operations plus explicitly whitelisted R11 routes only |

Rust can be persisted only after the binary proves product identity, `0.0.1 / pre-release`, contract version, exact read-only capability rows and descriptor SHA-256. Missing binaries, malformed output, unknown selector values and unsupported schemas fail closed.

The selector surface is:

```text
syntavra engine list
syntavra engine status
syntavra engine use python|rust|auto
syntavra engine verify
syntavra engine route version
```

## R11 routing contract

`contracts/engine/read-only-routing-v1.json` is the authority for admitted routes. A route must define:

- exact command name;
- required Rust capability;
- `read-only` mutation classification;
- fixed Rust argv;
- exact result fields;
- exact success envelope;
- bounded response size;
- explicit no-fallback behavior.

The common success envelope records selection, capability, mutation class and:

```text
fallback.policy = none
fallback.attempted = false
```

Unsupported commands, unavailable or incompatible Rust binaries, execution failures, oversized output, malformed JSON and identity drift produce a structured error and stop. The router does not invoke Python after a Rust route begins.

## Shared state policy

R7–R10 prove selected read-only views of existing `.syntavra` state. Rust still has no state-writing authority.

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

`contracts/engine/read-only-routing-v1.json` freezes the R11 whitelist and envelopes. `tools/verify_read_only_routing_parity.py` proves shared identity fields, engine-specific metadata, exact envelopes and no-fallback behavior.

The Python runtime remains the source of truth for the complete command and MCP inventory. `tools/export_python_contract.py` exports that surface for snapshot and code-generation gates.

## Delivery phases

1. **R0** — architecture and fail-closed rules. Implemented.
2. **R1** — Python reference inventory. Implemented.
3. **R2** — schemas, normalizers and parity runner. Implemented.
4. **R3** — Rust workspace and read-only bootstrap commands. Implemented.
5. **R4** — engine selector with Python default. Implemented.
6. **R5** — canonical primitives and exact hashing parity. Implemented.
7. **R6** — config, identity and status parity. Implemented.
8. **R7** — state layout and receipt inspection parity. Implemented.
9. **R8** — bounded state-root inspection parity. Implemented.
10. **R9** — quiescent logical broker SQLite snapshot parity. Implemented.
11. **R10** — bounded live broker SQLite online-backup parity. Implemented.
12. **R11** — first installed read-only command route with no fallback. Implemented for `version`.
13. **R12+** — additional read-only routes, MCP transport, evidence, process, structural, session, sandbox, installer and eventually mutating parity.

## Stable gate

Rust cannot become the `auto` preference until all of these pass on Windows, Linux and macOS:

- complete CLI contract parity;
- MCP discovery and error parity;
- config hash and provenance parity;
- shared-state migration and rollback;
- Python-to-Rust and Rust-to-Python evidence compatibility;
- process cancellation, timeout and orphan recovery;
- session continuation and lineage verification;
- installer rollback and explicit host targeting;
- no hidden fallback after route start or mutation.

## Current claim boundary

```text
RUST_READ_ONLY_VERSION_ROUTING_PARITY_PROVEN_R11
RUST_ENGINE_EXPERIMENTAL_NOT_DEFAULT
RUST_GENERAL_PRODUCT_COMMAND_ROUTING_NOT_PROVEN
RUST_MUTATING_COMMAND_ROUTING_NOT_PROVEN
RUST_MCP_PARITY_NOT_PROVEN
RUST_PROCESS_PARITY_NOT_PROVEN
RUST_INSTALLER_PARITY_NOT_PROVEN
```
