from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/python/token-economy-competitive-gap-v1.json"
PLAN = ROOT / "docs/plans/SYNTAVRA_TOKEN_ECONOMY_COMPETITIVE_GAP_V1.md"


def main() -> int:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    plan = PLAN.read_text(encoding="utf-8")

    assert value["schema_version"] == 1
    assert value["family"] == "syntavra-token-economy-competitive-gap"
    assert value["no_new_capability_namespace"] is True
    assert value["targets"]["avoidable_input_reduction_floor"] >= 0.85
    assert value["targets"]["avoidable_output_reduction_floor"] >= 0.85
    assert value["targets"]["avoidable_input_reduction_stretch"] >= 0.95
    assert value["targets"]["avoidable_output_reduction_stretch"] >= 0.95
    assert value["targets"]["eligible_easy_inference_skip_tokens"] == 0
    assert value["targets"]["unexplained_envelope_overflow"] == 0
    assert len(value["p0"]) >= 14
    assert len({row["id"] for row in value["p0"]}) == len(value["p0"])
    assert "PROVIDER_RECEIPT_REQUIRED_FOR_PROVIDER_SAVINGS_CLAIM" in value["hard_invariants"]
    assert "COMPONENT_PERCENTAGES_ARE_NON_ADDITIVE" in value["hard_invariants"]
    assert "TE-1: Stop payloads at ingestion" in plan
    assert "TE-5: Turn repeated work into zero/near-zero inference" in plan

    receipt = {
        "schema_version": 1,
        "family": "syntavra-token-economy-competitive-gap-certification",
        "contract_sha256": hashlib.sha256(CONTRACT.read_bytes()).hexdigest(),
        "plan_sha256": hashlib.sha256(PLAN.read_bytes()).hexdigest(),
        "p0_count": len(value["p0"]),
        "claim_boundary": value["claim_boundary"],
    }
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
