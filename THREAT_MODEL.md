# SignalCore Threat Model

## Protected assets

- project source and exact evidence
- Studio process/project identity
- activation pairing material
- capability authorization
- raw tool output and recovery handles
- benchmark and claim integrity

## Principal threats

- prompt or rule based profile activation
- signed-envelope replay
- capability escalation through dependencies
- cross-project or cross-branch evidence/memory contamination
- stale or contradictory context selection
- external-engine result spoofing
- benchmark/result drift
- encoded or generated source hidden from review

## Controls

- strict signed activation and single-use nonce storage
- process, project, fingerprint and transport binding
- transitive capability authorization
- branch/project-scoped SQLite stores
- mandatory evidence roles and exact recovery handles
- independent validators and proof records
- claim registry with source-tree hash validation
- CI gate forbidding `.signalcore-direct` and `payload-*.b64`

## Residual risks

Live Studio transport, provider models, Creator Store, Blender, device simulation, and DataStore migration remain external. They require separate live certification and cannot inherit simulated benchmark maturity.
