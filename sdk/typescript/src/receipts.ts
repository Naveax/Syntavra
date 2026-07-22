export const SYNTAVRA_VERSION = "0.0.1" as const;
export const SYNTAVRA_CHANNEL = "pre-release" as const;

export type SyntavraWorkload =
  | "coding-agent"
  | "repository-task"
  | "swe-bench"
  | "oolong-long-context"
  | "session-continuity"
  | "tool-routing";

export type SyntavraArm =
  | "baseline"
  | "syntavra"
  | "token-savior"
  | "context-mode"
  | "headroom"
  | "volt-lcm";

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

const WORKLOADS = new Set<SyntavraWorkload>([
  "coding-agent",
  "repository-task",
  "swe-bench",
  "oolong-long-context",
  "session-continuity",
  "tool-routing"
]);

const ARMS = new Set<SyntavraArm>([
  "baseline",
  "syntavra",
  "token-savior",
  "context-mode",
  "headroom",
  "volt-lcm"
]);

export function validateProviderUsageReceipt(receipt: ProviderUsageReceipt): ReceiptValidation {
  const reasons: string[] = [];
  const required: Array<[string, string]> = [
    ["receipt_id", receipt.receipt_id],
    ["provider", receipt.provider],
    ["model", receipt.model],
    ["request_id", receipt.request_id],
    ["session_id", receipt.session_id],
    ["repository_hash", receipt.repository_hash],
    ["integration_id", receipt.integration_id],
    ["observed_at", receipt.observed_at],
    ["raw_usage_hash", receipt.raw_usage_hash],
    ["task_id", receipt.task_id]
  ];
  for (const [name, value] of required) {
    if (!value) reasons.push(`missing-${name.replaceAll("_", "-")}`);
  }
  if (!Number.isFinite(Date.parse(receipt.observed_at))) reasons.push("invalid-observed-at");
  if (!Number.isFinite(receipt.wall_time_ms) || receipt.wall_time_ms < 0) reasons.push("invalid-wall-time");
  for (const [name, value] of [
    ["input-tokens", receipt.input_tokens],
    ["cached-input-tokens", receipt.cached_input_tokens],
    ["output-tokens", receipt.output_tokens]
  ] as const) {
    if (!Number.isInteger(value) || value < 0) reasons.push(`invalid-${name}`);
  }
  if (receipt.cached_input_tokens > receipt.input_tokens) reasons.push("cached-input-exceeds-input");
  if (!Number.isFinite(receipt.cost_usd) || receipt.cost_usd < 0) reasons.push("invalid-cost");
  if (!Number.isFinite(receipt.quality_score) || receipt.quality_score < 0 || receipt.quality_score > 1) {
    reasons.push("invalid-quality-score");
  }
  if (!WORKLOADS.has(receipt.workload)) reasons.push("unsupported-workload");
  if (!ARMS.has(receipt.arm)) reasons.push("unsupported-arm");
  if (!Number.isInteger(receipt.repetition) || receipt.repetition < 1) reasons.push("invalid-repetition");
  if (receipt.raw_usage_hash.length < 32) reasons.push("weak-raw-usage-hash");
  const billableInputTokens = Math.max(0, receipt.input_tokens - receipt.cached_input_tokens);
  return {
    ok: reasons.length === 0,
    reasons: [...new Set(reasons)],
    billableInputTokens,
    totalTokens: billableInputTokens + receipt.output_tokens
  };
}

export function assertProviderUsageReceipt(receipt: ProviderUsageReceipt): ProviderUsageReceipt {
  const validation = validateProviderUsageReceipt(receipt);
  if (!validation.ok) throw new Error(`invalid Syntavra receipt: ${validation.reasons.join(", ")}`);
  return receipt;
}
