export type Json = null | boolean | number | string | Json[] | {
    [key: string]: Json;
};
export interface RetryPolicy {
    maxAttempts?: number;
    baseDelayMs?: number;
    maxDelayMs?: number;
    retryStatuses?: readonly number[];
}
export interface SyntavraClientOptions {
    baseUrl?: string;
    controlToken?: string;
    controlTokenProvider?: () => string | Promise<string>;
    allowRemote?: boolean;
    timeoutMs?: number;
    retry?: RetryPolicy;
    fetchImpl?: typeof fetch;
    logger?: (event: SyntavraClientEvent) => void;
}
export interface SyntavraClientEvent {
    type: "request" | "response" | "retry" | "error";
    requestId: string;
    path: string;
    attempt: number;
    status?: number;
    durationMs?: number;
    error?: string;
}
export interface SyntavraResponse<T = Json> {
    status: number;
    ok: boolean;
    data: T;
    replay: "hit" | "miss" | "unknown";
    requestHandle: string;
    evidenceHandle: string;
    requestId: string;
    headers: Headers;
}
export interface SyntavraStreamEvent<T = Json> {
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
export declare class SyntavraClient {
    readonly baseUrl: URL;
    private readonly staticControlToken;
    private readonly controlTokenProvider?;
    private readonly fetchImpl;
    private readonly timeoutMs;
    private readonly retryPolicy;
    private readonly logger?;
    constructor(options?: SyntavraClientOptions);
    private providerUrl;
    private providerHeaders;
    private fetchWithRetry;
    invoke<T = Json>(path: string, request: Json, init?: RequestInit): Promise<SyntavraResponse<T>>;
    invokeStream(path: string, request: Json, init?: RequestInit): Promise<Response>;
    streamEvents<T = Json>(path: string, request: Json, init?: RequestInit): AsyncGenerator<SyntavraStreamEvent<T>>;
    openAI<T = Json>(request: OpenAIResponsesRequest): Promise<SyntavraResponse<T>>;
    openAIChat<T = Json>(request: OpenAIChatRequest): Promise<SyntavraResponse<T>>;
    anthropic<T = Json>(request: AnthropicMessagesRequest): Promise<SyntavraResponse<T>>;
    gemini<T = Json>(model: string, request: Json): Promise<SyntavraResponse<T>>;
    private token;
    private control;
    live<T = Json>(): Promise<T>;
    health<T = Json>(): Promise<T>;
    ready<T = Json>(): Promise<T>;
    verify<T = Json>(): Promise<T>;
}
