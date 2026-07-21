# @signalcore/client

Dependency-free ESM/TypeScript client for a local SignalCore provider proxy.
Provider credentials remain in the proxy process environment; the client rejects
credential-shaped request fields and authorization headers.

```ts
import { SignalCoreClient } from "@signalcore/client";

const client = new SignalCoreClient({baseUrl: "http://127.0.0.1:8787"});
const response = await client.openAI({model: "gpt-5", input: "Inspect this repository"});
console.log(response.data, response.evidenceHandle);
```

The package exposes OpenAI Responses/Chat, Anthropic Messages, Gemini generate-content,
streaming pass-through, health and integrity verification surfaces. It does not bundle
provider SDKs or credentials.
