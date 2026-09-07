from __future__ import annotations

import hashlib
import json
from pathlib import Path

from syntavra_runtime.semantic_wire_ir import SemanticAtom, SemanticWireCompiler, WireGrammar


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/python/semantic-wire-ir-v1.json"
PLAN = ROOT / "docs/plans/SYNTAVRA_SEMANTIC_WIRE_IR_V1.md"


def tokens(text: str) -> int:
    # Deterministic smoke counter only. Production admission must use the actual
    # provider tokenizer; the contract explicitly forbids promoting estimates to
    # provider-savings claims.
    return len(text.split())


def main() -> int:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    plan = PLAN.read_text(encoding="utf-8")

    assert contract["schema_version"] == 1
    assert contract["protocol_version"] == 1
    assert contract["family"] == "syntavra-semantic-wire-ir"
    assert contract["no_new_capability_namespace"] is True
    assert contract["targets"]["avoidable_input_reduction_floor"] >= 0.85
    assert contract["targets"]["avoidable_output_reduction_floor"] >= 0.85
    assert contract["targets"]["unexplained_envelope_overflow"] == 0
    assert "TOKENIZER_MEASURED_NO_EXPANSION" in contract["hard_invariants"]
    assert "PAIRED_PROVIDER_RECEIPTS_REQUIRED_FOR_PUBLIC_SAVINGS_CLAIM" in contract["hard_invariants"]
    assert "targets are engineering promotion gates" in contract["claim_boundary"]
    assert "No Expansion" in plan
    assert "Output Savings Happen Before Generation" in plan
    assert "85-95%+" in plan

    compiler = SemanticWireCompiler(
        tokens,
        grammar=WireGrammar(operators={"I": "inspect", "S": "symbol", "V": "verify"}),
    )
    packet = compiler.compile(
        [
            SemanticAtom(
                "TARGET",
                "Inspect only the exact repository symbol required to repair the current authentication failure.",
                "I S22",
            ),
            SemanticAtom(
                "VERIFY",
                "After the edit run only the targeted verifier and preserve the existing public API.",
                "V V3",
            ),
        ],
        grammar_already_in_fixed_prefix=True,
    )
    assert packet.mode == "wire"
    assert packet.selected_tokens < packet.natural_tokens
    assert not packet.expanded

    receipt = {
        "schema_version": 1,
        "family": "syntavra-semantic-wire-ir-certification",
        "contract_sha256": hashlib.sha256(CONTRACT.read_bytes()).hexdigest(),
        "plan_sha256": hashlib.sha256(PLAN.read_bytes()).hexdigest(),
        "smoke": {
            "mode": packet.mode,
            "natural_tokens": packet.natural_tokens,
            "selected_tokens": packet.selected_tokens,
            "saved_tokens": packet.saved_tokens,
            "no_expansion": not packet.expanded,
        },
        "claim_boundary": contract["claim_boundary"],
    }
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
