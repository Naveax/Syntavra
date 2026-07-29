# ADR 0016: Read-only session and task override routing

## Status

Accepted for R16.

## Context

R15 proves bounded, Python-owned discovery of user, project and environment configuration. The canonical R6 configuration contract also defines transient `session` and `task` layers with higher precedence, but those layers are not yet admitted by the installed router.

Using `ConfigManager.load(session=..., task=...)` is not suitable for this route because the manager can persist `config-last-good.json`. The routing boundary must remain strictly read-only and must not expose raw transient values to Rust or to diagnostics.

## Decision

Add the `live-config-session-task-v1` input profile to `status` and `config.resolve`.

The installed CLI accepts optional:

- `--session-override-json-hex <hex>`
- `--task-override-json-hex <hex>`

These options are valid only with `--live-config`.

The Python router:

1. decodes each override as at most 65,536 bytes of lowercase hexadecimal;
2. requires UTF-8 canonical JSON with an object at the top level;
3. rejects duplicate keys, non-finite numbers, arrays and non-scalar leaves;
4. discovers user, project and environment configuration through the R15 boundary;
5. appends session and task layers in canonical precedence order;
6. constructs one immutable canonical `R6CFG1` wire;
7. resolves that wire in Python before engine selection;
8. sends the exact same final wire to Rust when Rust is selected;
9. compares the complete Python and Rust result objects.

Rust never decodes the transient JSON and never reads configuration files or process environment state.

## Fixed bounds

- each configuration file: at most 131,072 bytes;
- each session or task JSON override: at most 65,536 bytes;
- combined canonical `R6CFG1` wire: at most 262,144 bytes;
- Rust response: at most 1 MiB;
- raw override JSON and raw wire: absent from success and error envelopes;
- malformed, duplicate-key, noncanonical or unsupported override values: fail closed before engine selection;
- explicit config wire plus transient override: rejected;
- transient override without live discovery: rejected.

## Consequences

The complete configuration precedence is now represented at the read-only installed routing boundary:

```text
default < user < project < environment < session < task
```

Python remains the reference and default engine. `auto` remains Python. R16 grants no state write, last-good write, migration, recovery, MCP, installer, process or general mutation authority to Rust.
