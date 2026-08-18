# Python reference contracts

This directory stores small, explicit behavioral vocabulary fixtures for the Python reference phase.

## Authority rule

Fixtures here must not duplicate the canonical 245-route public command list.

The public-route authority remains the live parser graph consumed by `tools/report_missing_native_public_routes.py`, with the persisted route-count/path digest contract in `contracts/engine/dual-engine-public-surface-v2.json`.

Family fixtures may freeze stable schema keys, status/reason vocabularies, aliases, and other compact behavioral contracts. Their certification scripts must derive route membership and ownership from the canonical parser/reporting authorities rather than maintaining another route list.

`capability-inventory-reference-v1.json` follows this rule: it freezes capability vocabulary and inventory record shapes, while route count, route paths, command-path digest, and Python execution ownership are derived and verified at certification time.

## Python-first development authority

`python-authority-v1.json` is the meta-authority for the Python-first product-development phase. It does not replace the route, behavior-freeze, Phase 1, dual-surface, or migration authorities above. It binds them together and makes their different meanings explicit.

The important split is:

- Rust native implementation coverage may be `245/245` in `dual-engine-public-surface-v2.json`.
- Rust production-promotion authority remains frozen at `174/245` by the Python behavior/acceptance and Phase 2 migration boundaries.
- The remaining `71` routes are therefore a parity/promotion set, not an implementation-missing set.

During the Python-first phase, Python is the feature-development and product-behavior authority. Rust feature development, production promotion, and native promotion-counter changes remain disabled until a later `PYTHON_COMPLETE` certificate explicitly opens the Rust resume gate.

`tools/certify_python_authority.py` certifies this boundary from the existing authorities without persisting another 245-route or 71-route identity list. The corresponding exact-head CI is `.github/workflows/python-authority.yml`.
