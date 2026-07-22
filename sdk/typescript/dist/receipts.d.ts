export declare const SYNTAVRA_VERSION: "0.0.1";
export declare const SYNTAVRA_CHANNEL: "pre-release";
export type SyntavraWorkload = "coding-agent" | "repository-task" | "swe-bench" | "oolong-long-context" | "session-continuity" | "tool-routing";
export type SyntavraArm = "baseline" | "syntavra" | "token-savior" | "context-mode" | "headroom" | "volt-lcm";
export interface ProviderUsageReceipt {
    receipt_id: string;
    provider: string;
    model: string;
    request_id: string;
    session_id: string;
    repository_hash: string;
    integration_id: string;
    observed_at: string;
    wall_time_ms: number;
    input_tokens: number;
    cached_input_tokens: number;
    output_tokens: number;
    cost_usd: number;
    quality_score: number;
    success: boolean;
    synthetic: boolean;
    raw_usage_hash: string;
    workload: SyntavraWorkload;
    arm: SyntavraArm;
    task_id: string;
    repetition: number;
    metadata?: Record<string, unknown>;
}
export interface ReceiptValidation {
    ok: boolean;
    reasons: string[];
    billableInputTokens: number;
    totalTokens: number;
}
export declare function validateProviderUsageReceipt(receipt: ProviderUsageReceipt): ReceiptValidation;
export declare function assertProviderUsageReceipt(receipt: ProviderUsageReceipt): ProviderUsageReceipt;
