export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

export interface RetryPolicy {
  maxAttempts?: number;
  baseDelayMs?: number;
  maxDelayMs?: number;
  retryStatuses?: readonly number[];
}

export interface SignalCoreClientOptions {
  baseUrl?: string;
  controlToken?: string;
  controlTokenProvider?: () => string | Promise<string>;
  allowRemote?: boolean;
  timeoutMs?: number;
  retry?: RetryPolicy;
  fetchImpl?: typeof fetch;
  logger?: (event: SignalCoreClientEvent) => void;
}

export interface SignalCoreClientEvent {
  type: "request" | "response" | "retry" | "error";
  requestId: string;
  path: string;
  attempt: number;
  status?: number;
  durationMs?: number;
  error?: string;
}

export interface SignalCoreResponse<T = Json> {
  status: number;
  ok: boolean;
  data: T;
  replay: "hit" | "miss" | "unknown";
  requestHandle: string;
  evidenceHandle: string;
  requestId: string;
  headers: Headers;
}

export interface SignalCoreStreamEvent<T = Json> {
  event: string;
  data: T | string;
  id: string;
  retry?: number;
  raw: string;
  done: boolean;
}

export interface OpenAIResponsesRequest {
  model: string;
  input: Json;
  stream?: boolean;
  [key: string]: Json | undefined;
}

export interface OpenAIChatRequest {
  model: string;
  messages: Json[];
  stream?: boolean;
  [key: string]: Json | undefined;
}

export interface AnthropicMessagesRequest {
  model: string;
  messages: Json[];
  max_tokens: number;
  stream?: boolean;
  [key: string]: Json | undefined;
}

const CREDENTIAL_KEYS = new Set([
  "authorization", "api-key", "api_key", "apikey", "x-api-key", "x-goog-api-key",
  "access-token", "access_token", "bearer-token", "bearer_token", "openai_api_key",
  "anthropic_api_key", "google_api_key"
]);
const CREDENTIAL_HEADERS = ["authorization", "x-api-key", "api-key", "x-goog-api-key"];

function rejectCredentials(value: unknown, path = "request"): void {
  if (Array.isArray(value)) {
    value.forEach((item, index) => rejectCredentials(item, `${path}[${index}]`));
    return;
  }
  if (!value || typeof value !== "object") return;
  for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
    const normalized = key.toLowerCase().replaceAll("_", "-");
    if (CREDENTIAL_KEYS.has(normalized) || CREDENTIAL_KEYS.has(key.toLowerCase())) {
      throw new Error(`provider credentials are transport-only: ${path}.${key}`);
    }
    rejectCredentials(child, `${path}.${key}`);
  }
}

function validateBaseUrl(baseUrl: string, allowRemote: boolean): URL {
  const parsed = new URL(baseUrl);
  const loopback = ["127.0.0.1", "localhost", "::1", "[::1]"].includes(parsed.hostname);
  if (!loopback && !allowRemote) throw new Error("remote SignalCore proxy URLs require allowRemote=true");
  if (loopback && parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error("local SignalCore proxy must use HTTP or HTTPS");
  }
  if (!loopback && parsed.protocol !== "https:") {
    throw new Error("remote SignalCore proxy connections require HTTPS");
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error("baseUrl cannot contain credentials, query parameters, or fragments");
  }
  return parsed;
}

function requestId(): string {
  const random = globalThis.crypto?.randomUUID?.();
  return random ? `sc-${random.replaceAll("-", "")}` : `sc-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function parseRetryAfter(value: string | null): number | null {
  if (!value) return null;
  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) return seconds * 1000;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? Math.max(0, timestamp - Date.now()) : null;
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(timer);
      reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
    }, { once: true });
  });
}

function timeoutSignal(timeoutMs: number, external?: AbortSignal): { signal: AbortSignal; dispose: () => void } {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new DOMException("SignalCore request timed out", "TimeoutError")), timeoutMs);
  const onAbort = () => controller.abort(external?.reason ?? new DOMException("Aborted", "AbortError"));
  external?.addEventListener("abort", onAbort, { once: true });
  return {
    signal: controller.signal,
    dispose: () => {
      clearTimeout(timer);
      external?.removeEventListener("abort", onAbort);
    }
  };
}

export class SignalCoreClient {
  readonly baseUrl: URL;
  private readonly staticControlToken: string;
  private readonly controlTokenProvider?: () => string | Promise<string>;
  private readonly fetchImpl: typeof fetch;
  private readonly timeoutMs: number;
  private readonly retryPolicy: Required<RetryPolicy>;
  private readonly logger?: (event: SignalCoreClientEvent) => void;

  constructor(options: SignalCoreClientOptions = {}) {
    this.baseUrl = validateBaseUrl(options.baseUrl ?? "http://127.0.0.1:8787", Boolean(options.allowRemote));
    this.staticControlToken = options.controlToken ?? "";
    this.controlTokenProvider = options.controlTokenProvider;
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch;
    this.timeoutMs = options.timeoutMs ?? 180_000;
    this.logger = options.logger;
    this.retryPolicy = {
      maxAttempts: Math.max(1, options.retry?.maxAttempts ?? 3),
      baseDelayMs: Math.max(1, options.retry?.baseDelayMs ?? 250),
      maxDelayMs: Math.max(1, options.retry?.maxDelayMs ?? 5_000),
      retryStatuses: options.retry?.retryStatuses ?? [408, 409, 425, 429, 500, 502, 503, 504]
    };
    if (typeof this.fetchImpl !== "function") throw new Error("a fetch implementation is required");
    if (this.timeoutMs <= 0) throw new Error("timeoutMs must be positive");
  }

  private providerUrl(path: string): URL {
    if (!path.startsWith("/") || path.startsWith("//") || path.includes("\\")) {
      throw new Error("provider path must be an origin-form absolute path");
    }
    const url = new URL(path, this.baseUrl);
    if (url.origin !== this.baseUrl.origin) throw new Error("provider path escaped the configured proxy origin");
    return url;
  }

  private providerHeaders(init: RequestInit, id: string): Headers {
    const headers = new Headers(init.headers);
    for (const key of CREDENTIAL_HEADERS) {
      if (headers.has(key)) throw new Error("provider credentials must not be sent by the SignalCore client");
    }
    headers.set("content-type", "application/json");
    headers.set("x-request-id", id);
    return headers;
  }

  private async fetchWithRetry(url: URL, init: RequestInit, id: string, path: string): Promise<Response> {
    let lastError: unknown;
    for (let attempt = 1; attempt <= this.retryPolicy.maxAttempts; attempt += 1) {
      const started = performance.now();
      const timed = timeoutSignal(this.timeoutMs, init.signal ?? undefined);
      try {
        this.logger?.({ type: "request", requestId: id, path, attempt });
        const response = await this.fetchImpl(url, { ...init, signal: timed.signal });
        const durationMs = performance.now() - started;
        this.logger?.({ type: "response", requestId: id, path, attempt, status: response.status, durationMs });
        if (!this.retryPolicy.retryStatuses.includes(response.status) || attempt >= this.retryPolicy.maxAttempts) return response;
        await response.body?.cancel();
        const retryAfter = parseRetryAfter(response.headers.get("retry-after"));
        const backoff = Math.min(this.retryPolicy.maxDelayMs, this.retryPolicy.baseDelayMs * 2 ** (attempt - 1));
        const delay = retryAfter ?? Math.floor(backoff * (0.75 + Math.random() * 0.5));
        this.logger?.({ type: "retry", requestId: id, path, attempt, status: response.status, durationMs });
        await sleep(delay, init.signal ?? undefined);
      } catch (error) {
        lastError = error;
        this.logger?.({ type: "error", requestId: id, path, attempt, durationMs: performance.now() - started, error: String(error) });
        if (attempt >= this.retryPolicy.maxAttempts || (error instanceof DOMException && error.name === "AbortError")) throw error;
        const backoff = Math.min(this.retryPolicy.maxDelayMs, this.retryPolicy.baseDelayMs * 2 ** (attempt - 1));
        await sleep(Math.floor(backoff * (0.75 + Math.random() * 0.5)), init.signal ?? undefined);
      } finally {
        timed.dispose();
      }
    }
    throw lastError ?? new Error("SignalCore request failed");
  }

  async invoke<T = Json>(path: string, request: Json, init: RequestInit = {}): Promise<SignalCoreResponse<T>> {
    rejectCredentials(request);
    const id = requestId();
    const response = await this.fetchWithRetry(this.providerUrl(path), {
      ...init,
      method: "POST",
      headers: this.providerHeaders(init, id),
      body: JSON.stringify(request)
    }, id, path);
    const contentType = response.headers.get("content-type") ?? "";
    const data = contentType.toLowerCase().includes("json")
      ? await response.json() as T
      : await response.text() as unknown as T;
    return {
      status: response.status,
      ok: response.ok,
      data,
      replay: (response.headers.get("x-signalcore-replay") as "hit" | "miss" | null) ?? "unknown",
      requestHandle: response.headers.get("x-signalcore-request-handle") ?? "",
      evidenceHandle: response.headers.get("x-signalcore-evidence") ?? "",
      requestId: response.headers.get("x-request-id") ?? id,
      headers: response.headers
    };
  }

  async invokeStream(path: string, request: Json, init: RequestInit = {}): Promise<Response> {
    rejectCredentials(request);
    const id = requestId();
    return this.fetchWithRetry(this.providerUrl(path), {
      ...init,
      method: "POST",
      headers: this.providerHeaders(init, id),
      body: JSON.stringify(request)
    }, id, path);
  }

  async *streamEvents<T = Json>(path: string, request: Json, init: RequestInit = {}): AsyncGenerator<SignalCoreStreamEvent<T>> {
    const response = await this.invokeStream(path, request, init);
    if (!response.ok) throw new Error(`SignalCore stream failed: ${response.status}`);
    if (!response.body) throw new Error("SignalCore stream response has no body");
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    let eventName = "message";
    let eventId = "";
    let retry: number | undefined;
    let dataLines: string[] = [];
    const flush = (): SignalCoreStreamEvent<T> | null => {
      if (!dataLines.length) return null;
      const raw = dataLines.join("\n");
      const done = raw.trim() === "[DONE]";
      let data: T | string = raw;
      if (!done) {
        try { data = JSON.parse(raw) as T; } catch { /* keep text */ }
      }
      const value = { event: eventName, data, id: eventId, retry, raw, done };
      eventName = "message"; eventId = ""; retry = undefined; dataLines = [];
      return value;
    };
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += value;
        while (true) {
          const newline = buffer.indexOf("\n");
          if (newline < 0) break;
          const line = buffer.slice(0, newline).replace(/\r$/, "");
          buffer = buffer.slice(newline + 1);
          if (!line) {
            const event = flush();
            if (event) yield event;
            continue;
          }
          if (line.startsWith(":")) continue;
          const colon = line.indexOf(":");
          const field = colon < 0 ? line : line.slice(0, colon);
          const fieldValue = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
          if (field === "data") dataLines.push(fieldValue);
          else if (field === "event") eventName = fieldValue || "message";
          else if (field === "id") eventId = fieldValue;
          else if (field === "retry" && /^\d+$/.test(fieldValue)) retry = Number(fieldValue);
        }
      }
      if (buffer) dataLines.push(buffer);
      const final = flush();
      if (final) yield final;
    } finally {
      reader.releaseLock();
    }
  }

  openAI<T = Json>(request: OpenAIResponsesRequest): Promise<SignalCoreResponse<T>> {
    return this.invoke<T>("/v1/responses", request as unknown as Json);
  }
  openAIChat<T = Json>(request: OpenAIChatRequest): Promise<SignalCoreResponse<T>> {
    return this.invoke<T>("/v1/chat/completions", request as unknown as Json);
  }
  anthropic<T = Json>(request: AnthropicMessagesRequest): Promise<SignalCoreResponse<T>> {
    return this.invoke<T>("/v1/messages", request as unknown as Json);
  }
  gemini<T = Json>(model: string, request: Json): Promise<SignalCoreResponse<T>> {
    if (!/^[A-Za-z0-9._-]+$/.test(model)) throw new Error("invalid Gemini model path segment");
    return this.invoke<T>(`/v1beta/models/${model}:generateContent`, request);
  }

  private async token(): Promise<string> {
    return this.controlTokenProvider ? await this.controlTokenProvider() : this.staticControlToken;
  }

  private async control<T = Json>(path: string): Promise<T> {
    const token = await this.token();
    if (!token) throw new Error("SignalCore control endpoints require a control token");
    const headers = new Headers({ authorization: `Bearer ${token}`, "x-request-id": requestId() });
    const response = await this.fetchWithRetry(new URL(path, this.baseUrl), { headers }, headers.get("x-request-id")!, path);
    if (!response.ok) throw new Error(`SignalCore control endpoint failed: ${response.status}`);
    return response.json() as Promise<T>;
  }

  live<T = Json>(): Promise<T> { return this.control<T>("/_signalcore/live"); }
  health<T = Json>(): Promise<T> { return this.control<T>("/_signalcore/health"); }
  ready<T = Json>(): Promise<T> { return this.control<T>("/_signalcore/ready"); }
  verify<T = Json>(): Promise<T> { return this.control<T>("/_signalcore/verify"); }
}
