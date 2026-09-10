#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from syntavra_runtime.agent_context_runtime import ConstantContextState, ProviderTokenCounter
from syntavra_runtime.evidence import EvidenceStore
from syntavra_runtime.tool_externalization import ToolOutputExternalizer
from syntavra_runtime.tool_externalization_types import ExternalizationPolicy


ROUNDS = 50
TOOL_PAYLOAD_BYTES = 8192
TOKEN_BUDGET = 6000
BYTE_BUDGET = 24000


def _baseline(counter: ProviderTokenCounter) -> dict[str, int | float]:
    messages: list[dict[str, str]] = [{"role": "user", "content": json.dumps({"instruction": "synthetic scoped coding task"})}]
    per_turn: list[int] = []
    raw = "R" * TOOL_PAYLOAD_BYTES
    for index in range(1, ROUNDS + 1):
        action = {"action": "inspect", "path": f"file-{index % 4}.py"}
        payload = {"tool": "repo.read", "round": index, "content": raw}
        messages.extend(
            [
                {"role": "assistant", "content": json.dumps(action, separators=(",", ":"))},
                {"role": "user", "content": json.dumps(payload, separators=(",", ":"))},
            ]
        )
        per_turn.append(counter.count_messages(messages, system="agent").tokens)
    return {
        "rounds": ROUNDS,
        "total_input_tokens": sum(per_turn),
        "max_turn_tokens": max(per_turn),
        "last_turn_tokens": per_turn[-1],
    }


def _candidate(counter: ProviderTokenCounter) -> dict[str, int | float]:
    # Constant-context eviction is only safe when old bodies remain exactly
    # recoverable. Benchmark the production invariant instead of the old volatile
    # no-externalizer fixture, which correctly pins unrecoverable evidence.
    with tempfile.TemporaryDirectory(prefix="syntavra-token-economy-rc1-") as directory:
        root = Path(directory)
        evidence = EvidenceStore(root / "evidence", project_id="token-economy-rc1")
        externalizer = ToolOutputExternalizer(
            root / "externalizer.sqlite3",
            evidence=evidence,
            policy=ExternalizationPolicy.for_profile("compact"),
        )
        state = ConstantContextState(
            externalizer=externalizer,
            scope_key="token-economy-rc1",
            max_active=8,
            max_causal=16,
            preview_limit_bytes=1024,
        )
        per_turn: list[int] = []
        raw = "R" * TOOL_PAYLOAD_BYTES
        for index in range(1, ROUNDS + 1):
            # Four logical identities change over time. Every superseded body has an
            # exact evidence handle, so only current active previews remain visible.
            identity = index % 4
            state.ingest(
                key=f"repo.read:file-{identity}.py:1:200",
                tool="repo.read",
                raw=f"revision={index}\n{raw}",
                round_number=index,
                path=f"file-{identity}.py",
                metadata={
                    "start_line": 1,
                    "end_line": 200,
                    "revision": index,
                    "invalidation_fingerprint": f"synthetic-revision-{index}",
                },
            )
            compiled = state.compile(
                {"instruction": "synthetic scoped coding task"},
                counter=counter,
                token_budget=TOKEN_BUDGET,
                byte_budget=BYTE_BUDGET,
            )
            per_turn.append(counter.count_messages([{"role": "user", "content": compiled.text}], system="agent").tokens)

        active = state.active
        causal = state.causal
        return {
            "rounds": ROUNDS,
            "total_input_tokens": sum(per_turn),
            "max_turn_tokens": max(per_turn),
            "last_turn_tokens": per_turn[-1],
            "active_evidence": len(active),
            "causal_receipts": len(causal),
            "active_exact_recovery": all(bool(receipt.exact_handle) for receipt in active),
            "superseded_to_handle_observed": any(row.get("status") == "SUPERSEDED_TO_HANDLE" for row in causal),
        }


def run() -> dict[str, object]:
    counter = ProviderTokenCounter("sequence")
    baseline = _baseline(counter)
    candidate = _candidate(counter)
    baseline_total = int(baseline["total_input_tokens"])
    candidate_total = int(candidate["total_input_tokens"])
    ratio = 0.0 if baseline_total <= 0 else 1.0 - candidate_total / baseline_total
    report = {
        "schema_version": 1,
        "family": "syntavra-token-economy-rc1-local-structural-benchmark",
        "claim_boundary": "LOCAL_STRUCTURAL_REGRESSION_ONLY_NOT_PROVIDER_BILLED_PROOF",
        "counting_method": counter.count_text("probe").method,
        "baseline": baseline,
        "candidate": candidate,
        "local_structural_token_reduction_ratio": ratio,
        "gates": {
            "candidate_max_turn_within_budget": int(candidate["max_turn_tokens"]) <= TOKEN_BUDGET,
            "candidate_context_bounded": int(candidate["causal_receipts"]) <= 16 and int(candidate["active_evidence"]) <= 8,
            "candidate_exact_recovery_preserved": bool(candidate["active_exact_recovery"]),
            "candidate_supersession_to_handle_observed": bool(candidate["superseded_to_handle_observed"]),
            "candidate_total_lower_than_baseline": candidate_total < baseline_total,
            "no_provider_savings_claim": True,
        },
    }
    if not all(report["gates"].values()):
        raise AssertionError(report)
    return report


def main() -> int:
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
