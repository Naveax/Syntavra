# Runtime threat model

## Protected boundaries

- command arguments and working-directory containment
- destructive-command hook policy
- detached child-process trees
- environment and output secrets
- exact-log integrity and project scope
- memory project/user scope and expiry
- immutable history hashes and summary references
- verifier cache identity
- benchmark identity, observed difficulty and quota artifacts

## Fail-closed behavior

SignalCore rejects corrupt evidence, missing summary parents/events, stale verifier identities, cross-project memory/evidence access, incomplete paired identity, configured-only difficulty and missing quota telemetry. Instruction-only operation receives no enforcement claims.

## Explicit non-goals

SignalCore does not claim resistance to a fully privileged local operating-system attacker. It does not sandbox arbitrary untrusted binaries, replace OS access control or guarantee that host-provided telemetry is truthful. MCP or hook control is only as strong as the host integration that invokes it.
