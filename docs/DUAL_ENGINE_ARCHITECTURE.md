# Syntavra Dual-Engine Architecture

Status: **R20 installed quiescent broker snapshot routing implemented / Python remains default**  
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
15. Parity diagnostics contain digests and mismatched field names, never resolved configuration or state values.
16. Transient session and task override JSON is decoded only by Python; Rust receives only the final canonical `R6CFG1` wire.
17. Static state metadata routing grants no filesystem or database access by implication.
18. Project-bound state inspection exposes the derived project identifier, never the selected project-root path.
19. Receipt inspection rejects cross-project replay before Rust selection.
20. Installed broker database routing is quiescent-only until live WAL backup parity is separately admitted.

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
  config_contract.py   Canonical R6CFG1 encoder/decoder and projections
  live_config_discovery.py
                       Bounded Python-owned live configuration and override transport
  read_only_router.py  Capability-whitelisted no-fallback router
  read_only_router_r15.py
                       User/project/environment live discovery wrapper
  read_only_router_r16.py
                       Session/task transient override wrapper
  read_only_router_r17.py
                       Static state-layout routing wrapper
  read_only_router_r18.py
                       Project-bound state-root inspection wrapper
  read_only_router_r19.py
                       Project-bound receipt inspection wrapper
  read_only_router_r20.py
                       Quiescent project-bound broker SQLite snapshot wrapper
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

Direct Rust binary availability does not imply installed product-command routing. R20 routes:

```text
syntavra --engine python engine route version
syntavra --engine rust engine route version

syntavra --engine python engine route state.layout
syntavra --engine rust engine route state.layout

syntavra --engine python --project <root> engine route state.inspect
syntavra --engine rust --project <root> engine route state.inspect

syntavra --engine python --project <root> engine route receipt.inspect \
  --receipt-wire-hex <hex>
syntavra --engine rust --project <root> engine route receipt.inspect \
  --receipt-wire-hex <hex>

syntavra --engine python --project <root> engine route state.broker-snapshot \
  --database-path <project-bound-broker.sqlite3>
syntavra --engine rust --project <root> engine route state.broker-snapshot \
  --database-path <project-bound-broker.sqlite3>

syntavra --engine python engine route status
syntavra --engine rust engine route status

syntavra --engine python engine route status --config-wire-hex <hex>
syntavra --engine rust engine route status --config-wire-hex <hex>

syntavra --engine python engine route config.resolve --config-wire-hex <hex>
syntavra --engine rust engine route config.resolve --config-wire-hex <hex>

syntavra --engine python engine route status --live-config
syntavra --engine rust engine route status --live-config

syntavra --engine python engine route config.resolve --live-config
syntavra --engine rust engine route config.resolve --live-config

syntavra --engine python engine route status --live-config \
  [--session-override-json-hex <hex>] [--task-override-json-hex <hex>]
syntavra --engine rust engine route status --live-config \
  [--session-override-json-hex <hex>] [--task-override-json-hex <hex>]

syntavra --engine python engine route config.resolve --live-config \
  [--session-override-json-hex <hex>] [--task-override-json-hex <hex>]
syntavra --engine rust engine route config.resolve --live-config \
  [--session-override-json-hex <hex>] [--task-override-json-hex <hex>]
```

All other `engine route` commands fail closed as unsupported.

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

## Read-only routing contract

`contracts/engine/read-only-routing-v9.json` is the current authority. The v1–v8 contracts remain historical evidence. A current route defines:

- exact command name;
- required Rust capability;
- `read-only` mutation classification;
- accepted input profiles;
- input format and maximum decoded size;
- deterministic Rust argv construction;
- exact top-level result fields;
- exact success envelope;
- bounded response size;
- explicit no-fallback behavior;
- diagnostic data-exposure policy.

The success envelope records selection, capability, mutation class and bounded input metadata:

```text
input.profile
input.format
input.bytes
input.sha256
fallback.policy = none
fallback.attempted = false
```

Raw input is never returned. Unsupported commands, unsupported or missing inputs, invalid wire data, unavailable or incompatible Rust binaries, execution failures, oversized output, malformed JSON, identity drift and parity drift produce a structured error and stop. Candidate execution error text is redacted. Parity errors contain only mismatched top-level keys and SHA-256 result digests. The router does not invoke Python after a Rust route begins.

### R11 route

`version` proves the installed routing mechanism, exact engine-specific identity result and fail-closed execution.

### R12 route

`status` with no explicit wire proves exact default-config Python/Rust result parity.

### R13 route input

`status --config-wire-hex <hex>` transports one immutable canonical `R6CFG1` wire to both engines.

### R14 route

`config.resolve --config-wire-hex <hex>` resolves the same immutable wire in both engines.

### R15 live discovery

`status --live-config` and `config.resolve --live-config` let Python read bounded user/project TOML and `SYNTAVRA_CFG__*` environment values, construct one canonical wire, and give the same wire to Rust. No last-good state is written.

### R16 transient overrides

R16 adds optional canonical JSON-hex session and task mappings to live discovery. Each mapping is limited to 65,536 bytes, must be a canonical JSON object with scalar leaves, and is decoded only by Python. Duplicate keys, arrays, non-finite numbers, noncanonical bytes and raw secret material fail closed. The final precedence is:

```text
default < user < project < environment < session < task
```

The final canonical wire remains limited to 262,144 bytes. Rust receives only that wire. Raw override JSON is absent from envelopes and diagnostics.

### R17 state layout

`state.layout` admits the static R7 state-layout contract to the installed router. It accepts no input and compares the complete Python `state_layout()` object with `syntavra-rs state layout`. The route performs no filesystem read, database access or mutation. Configuration input, live discovery and transient overrides are rejected before engine selection.

### R18 state inspection

`state.inspect` admits the bounded R8 project-state inspection contract. The installed CLI preserves the lexical `--project` path for a no-follow root check, while the selector continues to use its canonical resolved root for existing behavior. Python derives the project ID, inspects only five contract-declared paths, and rejects root/path symlinks, unsupported types, files larger than 1 MiB and concurrent changes before Rust selection. Rust receives the same derived project ID and selected root. The complete result objects must match exactly.

Success metadata contains only the derived project identifier and its fixed format. The project-root string is forbidden in route envelopes and diagnostics. The route opens no SQLite database and writes no state.

### R19 receipt inspection

`receipt.inspect` admits a bounded canonical `R7RCPT1` receipt wire. Python validates lowercase hexadecimal transport, the complete receipt hash chain and expected project binding before Rust selection. Cross-project replay, unknown fields and invalid fallback semantics fail closed. Rust receives the same canonical wire and derived project ID. Raw receipt values and project paths are absent from envelopes and diagnostics.

### R20 quiescent broker snapshot

`state.broker-snapshot` admits the R9 logical SQLite snapshot contract to the installed router. Python validates the lexical project root, project ID, project-contained `broker.sqlite3` path, symlink-free ancestry, absence of rollback/WAL sidecars, exact schema and project-bound logical rows before Rust selection. The database is opened with `mode=ro`, `immutable=1` and `query_only=ON`; any concurrent file identity change fails closed.

Rust receives the same derived project ID, selected project root and database path. The complete result objects must match exactly. The absolute project and database paths are forbidden in envelopes and diagnostics; the validated relative database path remains part of the canonical result. The route performs no database write, sidecar creation, migration or recovery.

## Shared state policy

R7–R10 prove selected read-only views of existing `.syntavra` state. R17 routes the static layout, R18 routes bounded project-state inspection, R19 routes project-bound receipt inspection, and R20 routes quiescent logical broker snapshots. Rust still has no installed live-WAL database access or state-writing authority.

Future mutating operations require:

- database-specific writer locks;
- common SQL migration files;
- backup-before-migration;
- contract and schema preflight;
- no fallback after the first mutation;
- cross-engine recovery tests.
