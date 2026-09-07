from __future__ import annotations

import json

from syntavra_runtime.provider_token_envelope import (
    NecessityLease,
    ProviderTokenEnvelopeCompiler,
)


def main() -> int:
    compiler = ProviderTokenEnvelopeCompiler()
    envelope = compiler.compile_from_leases(
        original_input_tokens=100_000,
        original_output_budget_tokens=10_000,
        input_leases=(
            NecessityLease("system", "system_instruction", 800, "request:system", True),
            NecessityLease("user", "user_instruction", 1_200, "request:user", True),
        ),
        output_leases=(
            NecessityLease("answer", "requested_output", 400, "request:answer", True),
        ),
    )
    assert envelope.provider_input_budget_tokens == 6_000
    assert envelope.provider_output_budget_tokens == 2_000
    assert envelope.provider_total_budget_tokens == 8_000
    assert envelope.minimum_avoidable_reduction_satisfied
    assert envelope.necessity_proof_complete
    assert envelope.provider_call_admissible
    assert envelope.input_avoidable_reduction_ratio >= 0.80
    assert envelope.output_avoidable_reduction_ratio >= 0.80

    overflow = compiler.compile_from_leases(
        original_input_tokens=100_000,
        original_output_budget_tokens=10_000,
        input_leases=(
            NecessityLease(
                "exact",
                "exact_source",
                25_000,
                "artifact:sha256:exact",
                True,
            ),
        ),
    )
    assert overflow.overflow_tokens == 19_000
    assert not overflow.target_zone_satisfied
    assert overflow.provider_call_admissible

    unproved = compiler.compile(
        original_input_tokens=100_000,
        original_output_budget_tokens=10_000,
        mandatory_input_tokens=2_000,
    )
    assert not unproved.provider_call_admissible
    assert "MANDATORY_TOKENS_LACK_NECESSITY_PROOF" in unproved.reason_codes

    print(
        json.dumps(
            {
                "status": "PASS",
                "ordinary": envelope.to_dict(),
                "irreducible_overflow": overflow.to_dict(),
                "unproved_mandatory_admissible": unproved.provider_call_admissible,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
