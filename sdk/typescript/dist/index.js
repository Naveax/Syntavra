const CREDENTIAL_KEYS = new Set([
  "authorization", "api-key", "api_key", "apikey", "x-api-key", "x-goog-api-key",
  "access-token", "access_token", "bearer-token", "bearer_token", "openai_api_key",
  "anthropic_api_key", "google_api_key"
]);
function rejectCredentials(value, path = "request") {
  if (Array.isArray(value)) { value.forEach((item, index) => rejectCredentials(item, `${path}[${index}]`)); return; }
  if (!value || typeof value !== "object") return;
  for (const [key, child] of Object.entries(value)) {
    const normalized = key.toLowerCase().replaceAll("_", "-");
    if (CREDENTIAL_KEYS.has(normalized) || CREDENTIAL_KEYS.has(key.toLowerCase())) throw new Error(`provider credentials are transport-only: ${path}.${key}`);
    rejectCredentials(child, `${path}.${key}`);
  }
}
function validateBaseUrl(baseUrl, allowRemote) {
  const parsed = new URL(baseUrl);
  if (!allowRemote && !["127.0.0.1", "localhost", "::1", "[::1]"].includes(parsed.hostname)) throw new Error("remote SignalCore proxy URLs require allowRemote=true");
  if (!allowRemote && parsed.protocol !== "http:") throw new Error("local SignalCore proxy must use http:// loopback transport");
  if (parsed.username || parsed.password || parsed.search || parsed.hash) throw new Error("baseUrl cannot contain credentials, query parameters, or fragments");
  return parsed;
}
export class SignalCoreClient {
  constructor(options = {}) {
    this.baseUrl = validateBaseUrl(options.baseUrl ?? "http://127.0.0.1:8787", Boolean(options.allowRemote));
    this.controlToken = options.controlToken ?? "";
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch;
    if (typeof this.fetchImpl !== "function") throw new Error("a fetch implementation is required");
  }
  async invoke(path, request, init = {}) {
    rejectCredentials(request);
    if (!path.startsWith("/") || path.startsWith("//") || path.includes("\\")) throw new Error("provider path must be an origin-form absolute path");
    const url = new URL(path, this.baseUrl);
    if (url.origin !== this.baseUrl.origin) throw new Error("provider path escaped the configured proxy origin");
    const headers = new Headers(init.headers);
    for (const key of ["authorization", "x-api-key", "api-key", "x-goog-api-key"]) if (headers.has(key)) throw new Error("provider credentials must not be sent by the SignalCore client");
    headers.set("content-type", "application/json");
    const response = await this.fetchImpl(url, {...init, method: "POST", headers, body: JSON.stringify(request)});
    const contentType = response.headers.get("content-type") ?? "";
    const data = contentType.toLowerCase().includes("json") ? await response.json() : await response.text();
    return {status: response.status, ok: response.ok, data, replay: response.headers.get("x-signalcore-replay") ?? "unknown", requestHandle: response.headers.get("x-signalcore-request-handle") ?? "", evidenceHandle: response.headers.get("x-signalcore-evidence") ?? "", headers: response.headers};
  }
  async invokeStream(path, request, init = {}) {
    rejectCredentials(request);
    if (!path.startsWith("/") || path.startsWith("//") || path.includes("\\")) throw new Error("invalid provider path");
    const url = new URL(path, this.baseUrl);
    if (url.origin !== this.baseUrl.origin) throw new Error("provider path escaped the configured proxy origin");
    const headers = new Headers(init.headers);
    for (const key of ["authorization", "x-api-key", "api-key", "x-goog-api-key"]) if (headers.has(key)) throw new Error("provider credentials must not be sent by the SignalCore client");
    headers.set("content-type", "application/json");
    return this.fetchImpl(url, {...init, method: "POST", headers, body: JSON.stringify(request)});
  }
  openAI(request) { return this.invoke("/v1/responses", request); }
  openAIChat(request) { return this.invoke("/v1/chat/completions", request); }
  anthropic(request) { return this.invoke("/v1/messages", request); }
  gemini(model, request) {
    if (!/^[A-Za-z0-9._-]+$/.test(model)) throw new Error("invalid Gemini model path segment");
    return this.invoke(`/v1beta/models/${model}:generateContent`, request);
  }
  async control(path) {
    const headers = new Headers();
    if (this.controlToken) headers.set("authorization", `Bearer ${this.controlToken}`);
    const response = await this.fetchImpl(new URL(path, this.baseUrl), {headers});
    if (!response.ok) throw new Error(`SignalCore control endpoint failed: ${response.status}`);
    return response.json();
  }
  health() { return this.control("/_signalcore/health"); }
  verify() { return this.control("/_signalcore/verify"); }
}
