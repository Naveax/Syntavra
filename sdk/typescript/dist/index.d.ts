export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export interface SignalCoreClientOptions { baseUrl?: string; controlToken?: string; allowRemote?: boolean; fetchImpl?: typeof fetch; }
export interface SignalCoreResponse<T = Json> { status: number; ok: boolean; data: T; replay: "hit" | "miss" | "unknown"; requestHandle: string; evidenceHandle: string; headers: Headers; }
export declare class SignalCoreClient {
  readonly baseUrl: URL;
  readonly controlToken: string;
  constructor(options?: SignalCoreClientOptions);
  invoke<T = Json>(path: string, request: Json, init?: RequestInit): Promise<SignalCoreResponse<T>>;
  invokeStream(path: string, request: Json, init?: RequestInit): Promise<Response>;
  openAI<T = Json>(request: Json): Promise<SignalCoreResponse<T>>;
  openAIChat<T = Json>(request: Json): Promise<SignalCoreResponse<T>>;
  anthropic<T = Json>(request: Json): Promise<SignalCoreResponse<T>>;
  gemini<T = Json>(model: string, request: Json): Promise<SignalCoreResponse<T>>;
  health<T = Json>(): Promise<T>;
  verify<T = Json>(): Promise<T>;
}
