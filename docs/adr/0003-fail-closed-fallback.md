# ADR 0003: Fallback Is Preflight-Only and Fail-Closed

- Status: Accepted
- Date: 2026-07-26

## Context

A dual-engine router can accidentally execute one mutating command twice if the first engine partially changes state and the router retries with the other engine.

## Decision

Fallback is allowed only when all conditions hold:

1. capability preflight reports the selected engine does not support the operation;
2. execution has not started;
3. no file, database, process or remote state has been mutated;
4. the fallback is reported to the user and recorded in a receipt.

A runtime error after execution begins is final for that attempt and fails closed.

## Consequences

- Hidden fallback is prohibited.
- Mutating operations require explicit preflight and an engine lease.
- Receipt schemas include `from`, `to`, `reason` and `state_mutated=false`.
- Recovery is a separate explicit operation, not an automatic retry in another engine.
