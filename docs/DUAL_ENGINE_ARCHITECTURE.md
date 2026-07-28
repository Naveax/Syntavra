# Syntavra Dual-Engine Architecture

Status: **R13 bounded explicit config-wire status routing implemented / Python remains default**  
Product identity: **0.0.1 / pre-release / version locked**

## Objective

Syntavra remains one product with two independent runtime implementations:

- **Python engine** — maintained reference implementation.
- **Rust engine** — native implementation that must prove behavioral, protocol and state parity before becoming default.
- **Engine selector** — active boundary for `python`, `rust` and `auto` modes.
- **Read-only router** — capability whitelist for individually proven installed routes.

The Python runtime is not removed. A user must always be able to select it explicitly.

## Non-negotiable rules

1. Product version and release channel are shared; engine versions are implementation metadata.
2. Public contracts live under `contracts/` and are language independent.
3. A mutating operation selects one engine before its first state write.
4. Fallback is allowed only after a preflight reports `unsupported` and before mutation.
5. Runtime errors after mutation fail closed; they never trigger hidden re-execution in the other engine.
6. Read-only route failures also fail closed and are never re-executed in Python.
7. Python remains the reference until Rust passes all stable gates.
8. Unknown contract, selector, routing or state schema versions fail closed.
9. Logical SQLite records define parity; physical database page layout does not.
10. Cryptographic formats require byte-level cross-engine vectors.
11. `auto` may prefer Rust only after the command, platform, state schema and parity gates are compatible.
12. Direct Rust capability availability does not authorize installed routing; every route requires an explicit contract row.
13. Cross-engine inputs are immutable, bounded and hashed before candidate execution.
14. Raw route input is forbidden in success and error envelopes.

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
  engine_cli.py        Selector and read-only route commands
  engine_selector.py   Resolution, persistence and verification
  config_contract.py   Canonical R6CFG1 encoder/decoder and status projection
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

Direct Rust binary availability does not imply installed product-command routing. R13 routes only:

```text
syntavra --engine python engine route version
syntavra --engine rust engine route version
syntavra --engine python engine route status
syntavra --engine rust engine route status
syntavra --engine python engine route status --config-wire-hex <hex>
syntavra --engine rust engine route status --config-wire-hex <hex>
```

The status route supports either the deterministic built-in default or an explicit canonical `R6CFG1` input. It does not independently read project files, user files, environment variables, session overrides or task overrides. All other `engine route` commands fail closed as unsupported.

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
| `rust` | `rust` | Selector operations plus explicitly whitelisted read-only routes only |

Rust can be persisted only after the binary proves product identity, `0.0.1 / pre-release`, contract version, exact read-only capability rows and descriptor SHA-256. Missing binaries, malformed output, unknown selector values and unsupported schemas fail closed.

The selector surface is:

```text
syntavra engine list
syntavra engine status
syntavra engine use python|rust|auto
syntavra engine verify
syntavra engine route version
syntavra engine route status [--config-wire-hex <hex>]
```

## Read-only routing contract

`contracts/engine/read-only-routing-v2.json` is the current authority for admitted routes. The R11–R12 v1 contract remains as historical evidence. A current route must define:

- exact command name;
- required Rust capability;
- `read-only` mutation classification;
- accepted input profiles;
- input format and maximum decoded size;
- deterministic Rust argv construction;
- exact result fields;
- exact success envelope;
- bounded response size;
- explicit no-fallback behavior.

The schema-v2 success envelope records selection, capability, mutation class and bounded input metadata:

```text
input.profile
input.format
input.bytes
input.sha256
fallback.policy = none
fallback.attempted = false
```

Raw input is never returned. Unsupported commands, unsupported inputs, invalid wire data, unavailable or incompatible Rust binaries, execution failures, oversized output, malformed JSON, identity drift and parity drift produce a structured error and stop. Candidate execution error text is redacted. The router does not invoke Python after a Rust route begins.

### R11 route

`version` proves the installed routing mechanism, exact engine-specific identity result and fail-closed execution.

### R12 route

`status` with no explicit wire proves exact default-config Python/Rust result parity.

### R13 route input

`status --config-wire-hex <hex>` transports one immutable canonical `R6CFG1` wire to both engines. Python independently decodes and resolves the wire. Rust receives the same normalized lower-case hexadecimal bytes. The complete status objects must be identical.

The decoded wire is limited to 262,144 bytes. The decoder rejects malformed hexadecimal input, invalid UTF-8, unknown headers/scopes/types, duplicate assignments, phase or scope-order drift, non-canonical encoding and first-phase configuration validation failure. Later invalid phases preserve the established R6 last-good fallback behavior.

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

`contracts/engine/read-only-routing-v2.json` freezes the current installed whitelist, bounded input metadata and schema-v2 envelopes. `tools/verify_read_only_routing_parity.py` proves route inventory, fixture-wide explicit-input parity, exact envelopes and no-fallback behavior.

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
13. **R12** — deterministic default `status` route with exact Python/Rust parity. Implemented.
14. **R13** — bounded explicit canonical config-wire transport for installed `status` routing. Implemented.
15. **R14+** — live config export/discovery, additional read-only routes, MCP transport, evidence, process, structural, session, sandbox, installer and eventually mutating parity.

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
RUST_READ_ONLY_STATUS_ROUTING_PARITY_PROVEN_R12
RUST_EXPLICIT_CONFIG_STATUS_ROUTING_PARITY_PROVEN_R13
RUST_AUTOMATIC_LIVE_CONFIG_DISCOVERY_NOT_PROVEN
RUST_ENGINE_EXPERIMENTAL_NOT_DEFAULT
RUST_GENERAL_PRODUCT_COMMAND_ROUTING_NOT_PROVEN
RUST_MUTATING_COMMAND_ROUTING_NOT_PROVEN
RUST_MCP_PARITY_NOT_PROVEN
RUST_PROCESS_PARITY_NOT_PROVEN
RUST_INSTALLER_PARITY_NOT_PROVEN
```
