# Syntavra Claims Policy

Syntavra 0.0.1 is a pre-release project. Public statements must distinguish implementation, internal verification, simulation, provider-observed measurement, and independent external validation.

## Allowed evidence labels

| Label | Meaning | Public performance claim allowed |
|---|---|---:|
| `IMPLEMENTED` | Source exists and can be inspected | No |
| `INTERNALLY_VERIFIED` | Repository tests exercise the stated behavior | No |
| `SIMULATED` | External systems or workloads are simulated | No |
| `PROVIDER_OBSERVED` | Usage comes from provider receipts for a completed task | Only for that measured setup |
| `INDEPENDENTLY_REPRODUCED` | A third party reproduced a frozen protocol | Yes, within the reproduced scope |

## Required boundary

A savings or competitor statement requires equal verified work, a frozen task and environment, provider-observed usage, linked receipts, no skipped verifier, no security regression, declared failures included, and uncertainty reported. Internal fixture size, rule count, adapter count, parser count, byte reduction, or synthetic token estimates must not be presented as product superiority.

The repository currently provides no bundled provider-billed competitor result and no independent reproduction. Therefore the active public boundary remains:

```text
EXTERNAL_SUPERIORITY_NOT_PROVEN
MEASURED_AGENT_BENCHMARK_NOT_PROVEN
LIVE_INTEGRATION_CERTIFICATION_NOT_PROVEN
PUBLIC_PRODUCT_MATURITY_NOT_PROVEN
```
