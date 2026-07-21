export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

export interface SignalCoreClientOptions {
  baseUrl?: string;
  controlToken?: string;
  allowRemote?: boolean;
  fetchImpl?: typeof fetch;
}

export interface SignalCoreResponse<T = Json> {
  status: number;
  ok: boolean;
  data: T;
  replay: "hit" | "miss" | "unknown";
  requestHandle: string;
  evidenceHandle: string;
  headers: Headers;
}

const CREDENTIAL_KEYS = new Set([
  "authorization", "api-key", "api_key", "apikey", "x-api-key", "x-goog-api-key",
  "access-token", "access_token", "bearer-token", "bearer_token", "openai_api_key",
  "anthropic_api_key", "google_api_key"
]);

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
  if (!allowRemote && !["127.0.0.1", "localhost", "::1", "[::1]"].includes(parsed.hostname)) {
    throw new Error("remote SignalCore proxy URLs require allowRemote=true");
  }
  if (!allowRemote && parsed.protocol !== "http:") {
    throw new Error("local SignalCore proxy must use http:// loopback transport");
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error("baseUrl cannot contain credentials, query parameters, or fragments");
  }
  return parsed;
}

export class SignalCoreClient {
  readonly baseUrl: URL;
  readonly controlToken: string;
  private readonly fetchImpl: typeof fetch;

  constructor(options: SignalCoreClientOptions = {}) {
    this.baseUrl = validateBaseUrl(options.baseUrl ?? "http://127.0.0.1:8787", Boolean(options.allowRemote));
    this.controlToken = options.controlToken ?? "";
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch;
    if (typeof this.fetchImpl !== "function") throw new Error("a fetch implementation is required");
  }

  async invoke<T = Json>(path: string, request: Json, init: RequestInit = {}): Promise<SignalCoreResponse<T>> {
    rejectCredentials(request);
    if (!path.startsWith("/") || path.startsWith("//") || path.includes("\\")) {
      throw new Error("provider path must be an origin-form absolute path");
    }
    const url = new URL(path, this.baseUrl);
    if (url.origin !== this.baseUrl.origin) throw new Error("provider path escaped the configured proxy origin");
    const headers = new Headers(init.headers);
    if (headers.has("authorization") || headers.has("x-api-key") || headers.has("api-key") || headers.has("x-goog-api-key")) {
      throw new Error("provider credentials must not be sent by the SignalCore client");
    }
    headers.set("content-type", "application/json");
    const response = await this.fetchImpl(url, {
      ...init,
      method: "POST",
      headers,
      body: JSON.stringify(request)
    });
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
      headers: response.headers
    };
  }

  async invokeStream(path: string, request: Json, init: RequestInit = {}): Promise<Response> {
    rejectCredentials(request);
    if (!path.startsWith("/") || path.startsWith("//") || path.includes("\\")) throw new Error("invalid provider path");
    const url = new URL(path, this.baseUrl);
    if (url.origin !== this.baseUrl.origin) throw new Error("provider path escaped the configured proxy origin");
    const headers = new Headers(init.headers);
    for (const key of ["authorization", "x-api-key", "api-key", "x-goog-api-key"]) {
      if (headers.has(key)) throw new Error("provider credentials must not be sent by the SignalCore client");
    }
    headers.set("content-type", "application/json");
    return this.fetchImpl(url, {...init, method: "POST", headers, body: JSON.stringify(request)});
  }

  openAI<T = Json>(request: Json): Promise<SignalCoreResponse<T>> {
    return this.invoke<T>("/v1/responses", request);
  }

  openAIChat<T = Json>(request: Json): Promise<SignalCoreResponse<T>> {
    return this.invoke<T>("/v1/chat/completions", request);
  }

  anthropic<T = Json>(request: Json): Promise<SignalCoreResponse<T>> {
    return this.invoke<T>("/v1/messages", request);
  }

  gemini<T = Json>(model: string, request: Json): Promise<SignalCoreResponse<T>> {
    if (!/^[A-Za-z0-9._-]+$/.test(model)) throw new Error("invalid Gemini model path segment");
    return this.invoke<T>(`/v1beta/models/${model}:generateContent`, request);
  }

  private async control<T = Json>(path: string): Promise<T> {
    const headers = new Headers();
    if (this.controlToken) headers.set("authorization", `Bearer ${this.controlToken}`);
    const response = await this.fetchImpl(new URL(path, this.baseUrl), {headers});
    if (!response.ok) throw new Error(`SignalCore control endpoint failed: ${response.status}`);
    return response.json() as Promise<T>;
  }

  health<T = Json>(): Promise<T> { return this.control<T>("/_signalcore/health"); }
  verify<T = Json>(): Promise<T> { return this.control<T>("/_signalcore/verify"); }
}
