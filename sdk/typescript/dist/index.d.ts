export type Json = null | boolean | number | string | Json[] | {
    [key: string]: Json;
};
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
export declare class SignalCoreClient {
    readonly baseUrl: URL;
    private readonly staticControlToken;
    private readonly controlTokenProvider?;
    private readonly fetchImpl;
    private readonly timeoutMs;
    private readonly retryPolicy;
    private readonly logger?;
    constructor(options?: SignalCoreClientOptions);
    private providerUrl;
    private providerHeaders;
    private fetchWithRetry;
    invoke<T = Json>(path: string, request: Json, init?: RequestInit): Promise<SignalCoreResponse<T>>;
    invokeStream(path: string, request: Json, init?: RequestInit): Promise<Response>;
    streamEvents<T = Json>(path: string, request: Json, init?: RequestInit): AsyncGenerator<SignalCoreStreamEvent<T>>;
    openAI<T = Json>(request: OpenAIResponsesRequest): Promise<SignalCoreResponse<T>>;
    openAIChat<T = Json>(request: OpenAIChatRequest): Promise<SignalCoreResponse<T>>;
    anthropic<T = Json>(request: AnthropicMessagesRequest): Promise<SignalCoreResponse<T>>;
    gemini<T = Json>(model: string, request: Json): Promise<SignalCoreResponse<T>>;
    private token;
    private control;
    live<T = Json>(): Promise<T>;
    health<T = Json>(): Promise<T>;
    ready<T = Json>(): Promise<T>;
    verify<T = Json>(): Promise<T>;
}
