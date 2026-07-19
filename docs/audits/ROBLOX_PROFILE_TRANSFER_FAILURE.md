# Roblox Profile Transfer Failure Audit

## Incident

PR #3 (`agent/roblox-studio-profile-direct`) attempted to transport a repository patch as segmented Base64-encoded XZ data instead of ordinary Git source files.

## Recorded failure state

- expected combined Base64 payload size: **67,412 bytes**
- observed combined Base64 payload size: **70,133 bytes**
- Base64 decoder exit marker: **1**
- XZ integrity-test exit marker: **1**
- decompression exit marker: **1**
- expected decoded patch size: **291,378 bytes**
- observed decoded patch size: **264,809 bytes**
- observed failed patch SHA-256: `6ccca83c61ded44156ee0d09d5b54f7b9a237f2343af529799751e694c711f7d`

The branch therefore did not contain reviewable Roblox profile source. Its README claims could not be traced to committed implementation, tests, or benchmark code.

## Root cause

Large encoded payload fragments were copied through interfaces that could truncate, transform, or reject segments. Diagnostic and materialization workflows then accumulated on the same branch and competed to modify it. This made the transfer non-reviewable and non-deterministic even when individual fragments appeared valid.

## Resolution

Encoded source transport was abandoned. The replacement branch is:

```text
fix/sovereign-runtime-materialized
```

The replacement method is direct source materialization: every Python module, schema, test, benchmark, result artifact, and document is committed as an ordinary Git file. No workflow reconstructs source from `.b64`, compressed patches, or temporary marker files.

PR #3 is retained only as incident history and specification context, then closed as superseded by the clean replacement PR.
