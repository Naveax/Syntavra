# Fusion Runtime architecture

SignalCore separates the probabilistic model from deterministic control. The model requests work; the runtime owns process lifetime, exact output storage, structural indexes, context state, verifier identity and benchmark receipts.

Mandatory runtime health requires the state store, evidence store, broker, output firewall, context governor and host adapter. Instruction-only installation is reported separately and receives no enforced-runtime guarantees.
