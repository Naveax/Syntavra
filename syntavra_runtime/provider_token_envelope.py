from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from .util import canonical_json, sha256_bytes


NECESSITY_KINDS = frozenset(
    {
        "system_instruction",
        "user_instruction",
        "security_policy",
        "exact_source",
        "current_failure",
        "verifier_evidence",
        "selected_tool_schema",
        "dependency",
        "recovery_handle",
        "requested_output",
    }
)


@dataclass(frozen=True)
class NecessityLease:
    """Proof that a bounded token segment is allowed to cross the provider boundary."""

    lease_id: str
    kind: str
    tokens: int
    evidence_ref: str
    exact_required: bool = False
    expires_after_turns: int = 1

    def __post_init__(self) -> None:
        if not self.lease_id.strip():
            raise ValueError("necessity lease requires lease_id")
        if self.kind not in NECESSITY_KINDS:
            raise ValueError(f"unsupported necessity kind: {self.kind}")
        if int(self.tokens) < 0:
            raise ValueError("necessity lease tokens must be non-negative")
        if int(self.tokens) and not self.evidence_ref.strip():
            raise ValueError("non-empty necessity lease requires evidence_ref")
        if not 1 <= int(self.expires_after_turns) <= 8:
            raise ValueError("necessity lease expiry must be within 1..8 turns")
        object.__setattr__(self, "tokens", int(self.tokens))
        object.__setattr__(self, "expires_after_turns", int(self.expires_after_turns))


@dataclass(frozen=True)
class TokenEnvelopePolicy:
    """Default 3-8K provider zone with a hard proof boundary for overflow."""

    target_total_tokens: int = 8_000
    target_input_tokens: int = 6_000
    target_output_tokens: int = 2_000
    input_target_fraction: float = 0.06
    output_target_fraction: float = 0.20
    minimum_avoidable_reduction: float = 0.80
    minimum_input_budget_tokens: int = 256
    minimum_output_budget_tokens: int = 64

    def __post_init__(self) -> None:
        for name in (
            "target_total_tokens",
            "target_input_tokens",
            "target_output_tokens",
            "minimum_input_budget_tokens",
            "minimum_output_budget_tokens",
        ):
            if int(getattr(self, name)) < 1:
                raise ValueError(f"{name} must be positive")
        for name in ("input_target_fraction", "output_target_fraction"):
            value = float(getattr(self, name))
            if not 0.0 < value <= 1.0:
                raise ValueError(f"{name} must be within (0,1]")
        if not 0.0 <= float(self.minimum_avoidable_reduction) < 1.0:
            raise ValueError("minimum_avoidable_reduction must be within [0,1)")
        if self.target_input_tokens + self.target_output_tokens > self.target_total_tokens:
            raise ValueError("input/output targets cannot exceed total target")


@dataclass(frozen=True)
class ProviderTokenEnvelope:
    original_input_tokens: int
    original_output_budget_tokens: int
    mandatory_input_tokens: int
    mandatory_output_tokens: int
    provider_input_budget_tokens: int
    provider_output_budget_tokens: int
    provider_total_budget_tokens: int
    target_total_tokens: int
    overflow_tokens: int
    input_avoidable_reduction_ratio: float
    output_avoidable_reduction_ratio: float
    total_reduction_ratio: float
    minimum_avoidable_reduction_satisfied: bool
    necessity_proof_complete: bool
    target_zone_satisfied: bool
    provider_call_admissible: bool
    reason_codes: tuple[str, ...]
    receipt_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProviderTokenEnvelopeCompiler:
    """Compile the smallest safe provider budget from necessity proofs.

    This component does not claim an impossible fixed total reduction. It enforces
    a reduction target on *avoidable* tokens. Irreducible exact/user/security
    material may exceed the 3-8K target zone, but only through explicit leases.
    """

    def __init__(self, policy: TokenEnvelopePolicy | None = None) -> None:
        self.policy = policy or TokenEnvelopePolicy()

    @staticmethod
    def _normalize_leases(values: Iterable[NecessityLease]) -> tuple[NecessityLease, ...]:
        rows = tuple(values)
        ids = [row.lease_id for row in rows]
        if len(set(ids)) != len(ids):
            raise ValueError("necessity lease ids must be unique")
        return tuple(sorted(rows, key=lambda row: row.lease_id))

    @staticmethod
    def _avoidable_reduction(original: int, mandatory: int, budget: int) -> float:
        avoidable = max(0, original - mandatory)
        if avoidable == 0:
            return 1.0
        remaining_avoidable = max(0, budget - mandatory)
        return max(0.0, min(1.0, 1.0 - remaining_avoidable / avoidable))

    @staticmethod
    def _total_reduction(original_input: int, original_output: int, total_budget: int) -> float:
        baseline = original_input + original_output
        if baseline <= 0:
            return 0.0
        return max(0.0, min(1.0, 1.0 - total_budget / baseline))

    def _ideal_input_budget(self, original: int) -> int:
        if original <= 0:
            return 0
        target = min(
            self.policy.target_input_tokens,
            max(
                self.policy.minimum_input_budget_tokens,
                int(math.ceil(original * self.policy.input_target_fraction)),
            ),
        )
        max_for_minimum_cut = int(
            math.floor(
                original * (1.0 - self.policy.minimum_avoidable_reduction) + 1e-9
            )
        )
        if original >= self.policy.minimum_input_budget_tokens:
            target = min(target, max(self.policy.minimum_input_budget_tokens, max_for_minimum_cut))
        return min(original, max(1, target))

    def _ideal_output_budget(self, original: int) -> int:
        if original <= 0:
            return 0
        target = min(
            self.policy.target_output_tokens,
            max(
                self.policy.minimum_output_budget_tokens,
                int(math.ceil(original * self.policy.output_target_fraction)),
            ),
        )
        max_for_minimum_cut = int(
            math.floor(
                original * (1.0 - self.policy.minimum_avoidable_reduction) + 1e-9
            )
        )
        if original >= self.policy.minimum_output_budget_tokens:
            target = min(target, max(self.policy.minimum_output_budget_tokens, max_for_minimum_cut))
        return min(original, max(1, target))

    def compile(
        self,
        *,
        original_input_tokens: int,
        original_output_budget_tokens: int,
        mandatory_input_tokens: int = 0,
        mandatory_output_tokens: int = 0,
        input_leases: Iterable[NecessityLease] = (),
        output_leases: Iterable[NecessityLease] = (),
    ) -> ProviderTokenEnvelope:
        original_input_tokens = int(original_input_tokens)
        original_output_budget_tokens = int(original_output_budget_tokens)
        mandatory_input_tokens = int(mandatory_input_tokens)
        mandatory_output_tokens = int(mandatory_output_tokens)
        for name, value in (
            ("original_input_tokens", original_input_tokens),
            ("original_output_budget_tokens", original_output_budget_tokens),
            ("mandatory_input_tokens", mandatory_input_tokens),
            ("mandatory_output_tokens", mandatory_output_tokens),
        ):
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        if mandatory_input_tokens > original_input_tokens:
            raise ValueError("mandatory input cannot exceed original input")
        if mandatory_output_tokens > original_output_budget_tokens:
            raise ValueError("mandatory output cannot exceed original output budget")

        input_rows = self._normalize_leases(input_leases)
        output_rows = self._normalize_leases(output_leases)
        leased_input = sum(row.tokens for row in input_rows)
        leased_output = sum(row.tokens for row in output_rows)
        if input_rows and leased_input != mandatory_input_tokens:
            raise ValueError("mandatory_input_tokens must equal leased input tokens")
        if output_rows and leased_output != mandatory_output_tokens:
            raise ValueError("mandatory_output_tokens must equal leased output tokens")
        proof_complete = (
            (mandatory_input_tokens == 0 or leased_input == mandatory_input_tokens)
            and (mandatory_output_tokens == 0 or leased_output == mandatory_output_tokens)
        )

        ideal_input = self._ideal_input_budget(original_input_tokens)
        ideal_output = self._ideal_output_budget(original_output_budget_tokens)
        input_budget = min(original_input_tokens, max(mandatory_input_tokens, ideal_input))
        output_budget = min(
            original_output_budget_tokens,
            max(mandatory_output_tokens, ideal_output),
        )
        total_budget = input_budget + output_budget
        overflow = max(0, total_budget - self.policy.target_total_tokens)
        input_reduction = self._avoidable_reduction(
            original_input_tokens, mandatory_input_tokens, input_budget
        )
        output_reduction = self._avoidable_reduction(
            original_output_budget_tokens, mandatory_output_tokens, output_budget
        )
        minimum_satisfied = (
            input_reduction + 1e-12 >= self.policy.minimum_avoidable_reduction
            and output_reduction + 1e-12 >= self.policy.minimum_avoidable_reduction
        )

        reasons: list[str] = []
        if not proof_complete:
            reasons.append("MANDATORY_TOKENS_LACK_NECESSITY_PROOF")
        if overflow:
            reasons.append("IRREDUCIBLE_OVERFLOW_REQUIRES_NECESSITY_PROOF")
        if mandatory_input_tokens > self.policy.target_input_tokens:
            reasons.append("MANDATORY_INPUT_EXCEEDS_TARGET")
        if mandatory_output_tokens > self.policy.target_output_tokens:
            reasons.append("MANDATORY_OUTPUT_EXCEEDS_TARGET")
        if not minimum_satisfied:
            reasons.append("MINIMUM_AVOIDABLE_REDUCTION_NOT_MET")
        if not reasons:
            reasons.append("TARGET_ENVELOPE_SATISFIED")

        body = {
            "original_input_tokens": original_input_tokens,
            "original_output_budget_tokens": original_output_budget_tokens,
            "mandatory_input_tokens": mandatory_input_tokens,
            "mandatory_output_tokens": mandatory_output_tokens,
            "provider_input_budget_tokens": input_budget,
            "provider_output_budget_tokens": output_budget,
            "provider_total_budget_tokens": total_budget,
            "target_total_tokens": self.policy.target_total_tokens,
            "overflow_tokens": overflow,
            "input_avoidable_reduction_ratio": round(input_reduction, 6),
            "output_avoidable_reduction_ratio": round(output_reduction, 6),
            "total_reduction_ratio": round(
                self._total_reduction(
                    original_input_tokens,
                    original_output_budget_tokens,
                    total_budget,
                ),
                6,
            ),
            "minimum_avoidable_reduction_satisfied": minimum_satisfied,
            "necessity_proof_complete": proof_complete,
            "target_zone_satisfied": overflow == 0,
            "provider_call_admissible": minimum_satisfied and proof_complete,
            "reason_codes": tuple(reasons),
            "input_lease_ids": tuple(row.lease_id for row in input_rows),
            "output_lease_ids": tuple(row.lease_id for row in output_rows),
        }
        receipt_hash = sha256_bytes(canonical_json(body))
        return ProviderTokenEnvelope(
            original_input_tokens=original_input_tokens,
            original_output_budget_tokens=original_output_budget_tokens,
            mandatory_input_tokens=mandatory_input_tokens,
            mandatory_output_tokens=mandatory_output_tokens,
            provider_input_budget_tokens=input_budget,
            provider_output_budget_tokens=output_budget,
            provider_total_budget_tokens=total_budget,
            target_total_tokens=self.policy.target_total_tokens,
            overflow_tokens=overflow,
            input_avoidable_reduction_ratio=round(input_reduction, 6),
            output_avoidable_reduction_ratio=round(output_reduction, 6),
            total_reduction_ratio=round(
                self._total_reduction(
                    original_input_tokens,
                    original_output_budget_tokens,
                    total_budget,
                ),
                6,
            ),
            minimum_avoidable_reduction_satisfied=minimum_satisfied,
            necessity_proof_complete=proof_complete,
            target_zone_satisfied=overflow == 0,
            provider_call_admissible=minimum_satisfied and proof_complete,
            reason_codes=tuple(reasons),
            receipt_hash=receipt_hash,
        )

    def compile_from_leases(
        self,
        *,
        original_input_tokens: int,
        original_output_budget_tokens: int,
        input_leases: Iterable[NecessityLease],
        output_leases: Iterable[NecessityLease] = (),
    ) -> ProviderTokenEnvelope:
        input_rows = self._normalize_leases(input_leases)
        output_rows = self._normalize_leases(output_leases)
        return self.compile(
            original_input_tokens=original_input_tokens,
            original_output_budget_tokens=original_output_budget_tokens,
            mandatory_input_tokens=sum(row.tokens for row in input_rows),
            mandatory_output_tokens=sum(row.tokens for row in output_rows),
            input_leases=input_rows,
            output_leases=output_rows,
        )


__all__ = [
    "NECESSITY_KINDS",
    "NecessityLease",
    "ProviderTokenEnvelope",
    "ProviderTokenEnvelopeCompiler",
    "TokenEnvelopePolicy",
]
