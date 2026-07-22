import assert from "node:assert/strict";
import test from "node:test";
import {
  SYNTAVRA_CHANNEL,
  SYNTAVRA_VERSION,
  assertProviderUsageReceipt,
  validateProviderUsageReceipt
} from "../dist/receipts.js";

function receipt(overrides = {}) {
  return {
    receipt_id: "receipt-1",
    provider: "openai",
    model: "test-model",
    request_id: "request-1",
    session_id: "session-1",
    repository_hash: "a".repeat(64),
    integration_id: "codex",
    observed_at: "2026-07-22T00:00:00Z",
    wall_time_ms: 1250,
    input_tokens: 1000,
    cached_input_tokens: 200,
    output_tokens: 300,
    cost_usd: 0.01,
    quality_score: 1,
    success: true,
    synthetic: false,
    raw_usage_hash: "b".repeat(64),
    workload: "coding-agent",
    arm: "syntavra",
    task_id: "task-1",
    repetition: 1,
    ...overrides
  };
}

test("keeps the package identity locked", () => {
  assert.equal(SYNTAVRA_VERSION, "0.0.1");
  assert.equal(SYNTAVRA_CHANNEL, "pre-release");
});

test("calculates billable and total token use", () => {
  const result = validateProviderUsageReceipt(receipt());
  assert.equal(result.ok, true);
  assert.equal(result.billableInputTokens, 800);
  assert.equal(result.totalTokens, 1100);
});

test("fails closed on invalid receipt data", () => {
  const invalid = receipt({
    observed_at: "not-a-date",
    cached_input_tokens: 1001,
    raw_usage_hash: "short",
    quality_score: 2
  });
  const result = validateProviderUsageReceipt(invalid);
  assert.equal(result.ok, false);
  assert.ok(result.reasons.includes("invalid-observed-at"));
  assert.ok(result.reasons.includes("cached-input-exceeds-input"));
  assert.ok(result.reasons.includes("weak-raw-usage-hash"));
  assert.ok(result.reasons.includes("invalid-quality-score"));
  assert.throws(() => assertProviderUsageReceipt(invalid), /invalid Syntavra receipt/);
});
