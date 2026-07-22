import assert from "node:assert/strict";
import test from "node:test";
import { SignalCoreClient } from "../dist/index.js";

function jsonResponse(data, status = 200, headers = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json", ...headers }
  });
}

test("rejects insecure remote proxy URLs", () => {
  assert.throws(
    () => new SignalCoreClient({ baseUrl: "http://example.com:8787", allowRemote: true }),
    /require HTTPS/
  );
});

test("rejects provider credentials in payloads and headers", async () => {
  let called = false;
  const client = new SignalCoreClient({
    fetchImpl: async () => {
      called = true;
      return jsonResponse({ ok: true });
    }
  });
  await assert.rejects(
    client.invoke("/v1/responses", { model: "test", api_key: "secret" }),
    /credentials are transport-only/
  );
  await assert.rejects(
    client.invoke("/v1/responses", { model: "test" }, {
      headers: { authorization: "Bearer secret" }
    }),
    /credentials must not be sent/
  );
  assert.equal(called, false);
});

test("retries transient responses and preserves receipt headers", async () => {
  let attempts = 0;
  const events = [];
  const client = new SignalCoreClient({
    timeoutMs: 1_000,
    retry: { maxAttempts: 2, baseDelayMs: 1, maxDelayMs: 1 },
    logger: (event) => events.push(event),
    fetchImpl: async () => {
      attempts += 1;
      if (attempts === 1) return jsonResponse({ error: "busy" }, 503);
      return jsonResponse(
        { id: "response-1" },
        200,
        {
          "x-signalcore-replay": "miss",
          "x-signalcore-request-handle": "request:1",
          "x-signalcore-evidence": "evidence:1",
          "x-request-id": "provider-request-1"
        }
      );
    }
  });
  const result = await client.invoke("/v1/responses", { model: "test", input: "hello" });
  assert.equal(attempts, 2);
  assert.equal(result.ok, true);
  assert.equal(result.requestHandle, "request:1");
  assert.equal(result.evidenceHandle, "evidence:1");
  assert.equal(result.requestId, "provider-request-1");
  assert.ok(events.some((event) => event.type === "retry"));
});

test("parses multi-line SSE and the done marker", async () => {
  const payload = [
    "event: response.output_text.delta",
    "id: 1",
    "data: {\"delta\":",
    "data: \"hello\"}",
    "",
    "data: [DONE]",
    "",
    ""
  ].join("\n");
  const client = new SignalCoreClient({
    fetchImpl: async () => new Response(payload, {
      status: 200,
      headers: { "content-type": "text/event-stream" }
    })
  });
  const events = [];
  for await (const event of client.streamEvents("/v1/responses", { model: "test", input: "hello" })) {
    events.push(event);
  }
  assert.equal(events.length, 2);
  assert.equal(events[0].event, "response.output_text.delta");
  assert.deepEqual(events[0].data, { delta: "hello" });
  assert.equal(events[1].done, true);
});
