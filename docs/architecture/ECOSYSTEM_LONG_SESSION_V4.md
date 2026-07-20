# Ecosystem and Long-Session V4

## Objective

This phase closes productization gaps without weakening SignalCore's exactness or
claim governance. It adds optional-framework SDK surfaces, query-aware long-session
planning, strict real-task corpus manifests, expanded host coverage, and a terse
output profile.

No external competitor implementation is copied or vendored.

## SDK and framework adapters

`SignalCoreClient` is a dependency-free facade over the existing provider gateway.
The caller supplies the actual provider transport. SignalCore remains responsible
for:

- stable request fingerprints and provider-native prompt-cache preparation;
- safe deterministic response replay;
- exact request and response evidence;
- redacted model-visible previews;
- normalized usage and optional HMAC-attested receipts;
- sync and async invocation.

Duck-typed adapters are included for OpenAI Responses, OpenAI Chat, Anthropic
Messages, Gemini generate-content, LiteLLM, a generic callable middleware, and a
dependency-free LangChain callback observer. Provider SDKs remain optional and are
never imported by the SignalCore package.

## Query-aware long sessions

`LongSessionPlanner` operates on the existing immutable `SessionRuntime`. It does
not replace or rewrite exact history.

Planning combines:

- query-token overlap;
- recency;
- critical event types;
- latest-authority temporal resolution through `supersedes`, `replaces`, revoked,
  and same-subject decisions;
- bounded summary and event previews;
- mandatory recent and current-critical evidence;
- stable `sc://session/...` exact references.

Every plan verifies the session hash chain first and exposes a deterministic plan
hash. The stress report records budget compliance, exact-reference coverage, chain
integrity, and p95 planning latency.

## Real-task corpus and executable arms

`RealTaskCorpus` rejects ambiguous benchmark definitions:

- repository commits must be full 40-character lowercase SHA values;
- setup, test, verification, and arm commands must be argv arrays, never shell
  strings;
- task and arm identifiers must be stable and unique;
- provider, model, tool permissions, environment fingerprint, and cache modes must
  match across arms;
- paired schedules are deterministic and randomized from a recorded seed.

Default readiness requires at least 50 real tasks, 3 executable arms, and 30
repetitions. Passing manifest validation is not a superiority result. Completed
runs, provider receipts, quality judgments, and statistical release gates remain
mandatory.

## Host and output productization

The host registry now includes Zed, Kilo Code, JetBrains Copilot, Sourcegraph Cody,
and Goose. New entries are not marked verified merely because configuration
contracts exist. Coverage reporting distinguishes implementation coverage from live
host certification.

The `terse` output profile uses a 1,400-byte budget while prioritizing failures,
source locations, statuses, commands, code, and diff lines. Exact evidence remains
available when the visible output is bounded.

## MCP surfaces

The ecosystem extension adds:

- `signalcore.ecosystem.capabilities`
- `signalcore.session.plan`
- `signalcore.session.stress`
- `signalcore.corpus.validate`
- `signalcore.corpus.manifest`

The extension is idempotent and preserves the existing MCP catalog and dispatch
logic.

## Claim boundary

Internal tests and the long-session benchmark validate implementation properties.
They do not establish that SignalCore beats Token Savior, Headroom, Volt/LCM,
Context Mode, Caveman, or any other external product.

The allowed result remains:

`EXTERNAL_SUPERIORITY_NOT_PROVEN`
