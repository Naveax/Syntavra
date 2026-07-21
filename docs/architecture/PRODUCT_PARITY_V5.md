# Product Parity V5

## Objective

This phase closes the remaining product-surface gaps identified against Headroom,
Context Mode, Token Savior, Volt/LCM and terse-output systems. It does not copy or
vendor competitor implementations and does not change the public claim boundary.

## TypeScript client

`sdk/typescript` is a dependency-free ESM/TypeScript client for the local provider
proxy. It provides OpenAI Responses/Chat, Anthropic Messages, Gemini generate-content,
streaming pass-through, health and verification calls. The client rejects credential-
shaped payload fields and provider authorization headers; credentials stay in the proxy
process environment.

## Structured data routing

`DataRouter` stores exact raw input before producing a bounded view. It detects and
specializes:

- SQL, dataframe and tabular results;
- vector/RAG/search results with deduplication and source retention;
- GraphQL nodes, edges and pagination envelopes;
- generic JSON and text.

Table routing preserves schema, selected columns, numeric summaries, query-relevant
rows and failure signals. RAG routing preserves identity, source, score/distance and a
bounded snippet. Every reduction reports original and visible bytes, exact hash,
evidence handle, record counts and limitations.

## Adaptive policy tuning

`AdaptivePolicyTuner` records local workload observations in SQLite/WAL. Recommendations
are deterministic and fail closed:

- sparse samples remain on the balanced baseline;
- any security regression disables aggressive caching and canary promotion;
- pass-rate, quality and p95 latency floors gate compact/terse policies;
- promoted policies are versioned by hash and can be rolled back.

The tuner never weakens secret scanning, exact evidence or sandbox requirements.

## User-scoped proxy services

`ProviderProxyServiceManager` renders user-level systemd, launchd and Windows Task
Scheduler descriptors. Installations remain under the user home, reject symlink path
components, use atomic replacement and keep activation opt-in. Verification compares
the installed descriptor byte-for-byte against the canonical rendered hash.

## Secure executable arms

`SecureArmRunner` executes competitor adapters without importing their source. The
runner requires argv arrays, a fixed workspace, an environment allowlist, time and
artifact limits, a pair-bound result document and, by default, a provider receipt. Raw
stdout and stderr can be stored as exact evidence. Result identity, usage metrics and
receipt fields are validated before a run can succeed.

## Surfaces

The dedicated `signalcore-product` CLI exposes data routing, policy observation and
promotion, service planning/install/verify/uninstall, and secure arm validation/run.
The MCP extension exposes safe non-interactive data, policy, service-plan and result-
validation operations. External command execution remains an explicit CLI operation.

## Claim boundary

Internal tests and benchmarks validate implementation behavior only. SignalCore still
requires completed identical-model competitor arms, real repository tasks, paired
repetitions, provider receipts and statistical release gates before an external
superiority claim is allowed.

`EXTERNAL_SUPERIORITY_NOT_PROVEN`
