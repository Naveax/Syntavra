# Runtime threat model

Protected boundaries include command arguments, child-process trees, environment secrets, repository containment, exact-log integrity, memory scope, verifier cache identity and benchmark artifacts. Corrupt evidence, stale verifier identities and incomplete quota telemetry fail closed. SignalCore does not claim resistance to a fully privileged local operating-system attacker.
