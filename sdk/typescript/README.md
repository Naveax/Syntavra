# @signalcore/client

Typed ESM/TypeScript client for SignalCore Unified Production Core.
Provider credentials remain in the proxy process environment; the client rejects
credential-shaped request fields and provider authorization headers.

```ts
import { SignalCoreClient } from "@signalcore/client";

const client = new SignalCoreClient({
  baseUrl: "http://127.0.0.1:8787",
  controlToken: process.env.SIGNALCORE_PROXY_CONTROL_TOKEN,
  timeoutMs: 180_000,
});

const response = await client.openAI({model: "gpt-5", input: "Inspect this repository"});
console.log(response.data, response.evidenceHandle, response.requestId);
```

Remote connections require HTTPS. The package provides bounded retries with
`Retry-After`, abort/timeout handling, typed SSE iteration, health and integrity
verification, and helpers for OpenAI Responses/Chat, Anthropic Messages and
Gemini generate-content. It does not bundle provider credentials.
