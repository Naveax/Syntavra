# Python reference contracts

This directory stores small, explicit behavioral vocabulary fixtures for the Python reference phase.

## Authority rule

Fixtures here must not duplicate the canonical 245-route public command list.

The public-route authority remains the live parser graph consumed by `tools/report_missing_native_public_routes.py`, with the persisted route-count/path digest contract in `contracts/engine/dual-engine-public-surface-v2.json`.

Family fixtures may freeze stable schema keys, status/reason vocabularies, aliases, and other compact behavioral contracts. Their certification scripts must derive route membership and ownership from the canonical parser/reporting authorities rather than maintaining another route list.

`capability-inventory-reference-v1.json` follows this rule: it freezes capability vocabulary and inventory record shapes, while route count, route paths, command-path digest, and Python execution ownership are derived and verified at certification time.
