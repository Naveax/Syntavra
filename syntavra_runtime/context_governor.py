from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .models import ContextDecision, ContextItem, ContextPack
from .provider_token_envelope import (
    NecessityLease,
    ProviderTokenEnvelope,
    ProviderTokenEnvelopeCompiler,
    TokenEnvelopePolicy,
)
from .util import canonical_json, sha256_bytes


DEFAULT_THRESHOLDS = (
    (0.50, ("evict_duplicates", "drop_raw_success_logs")),
    (0.60, ("externalize_evidence",)),
    (0.70, ("write_phase_capsule",)),
    (0.78, ("update_context_dag",)),
    (0.84, ("prepare_controlled_handoff",)),
    (0.88, ("mandatory_session_split",)),
)


def evaluate(
    used: int,
    window: int,
    *,
    thresholds=DEFAULT_THRESHOLDS,
    churn: float = 0.0,
    evidence_pressure: float = 0.0,
) -> ContextDecision:
    if used < 0 or window <= 0:
        raise ValueError("used must be nonnegative and window positive")
    utilization = used / window
    pressure = min(1.5, max(0.0, utilization + 0.12 * max(0.0, churn) + 0.08 * max(0.0, evidence_pressure)))
    actions: list[str] = []
    level = 0
    for threshold, names in thresholds:
        if pressure >= threshold:
            level += 1
            actions.extend(names)
    return ContextDecision(utilization, level, tuple(actions), pressure >= thresholds[-1][0], pressure)


def stable_prefix_hash(sections: list[tuple[str, str]]) -> str:
    return sha256_bytes(canonical_json(sorted(sections, key=lambda item: item[0])))


def _closure(item_id: str, by_id: dict[str, ContextItem], selected: set[str]) -> set[str]:
    required: set[str] = set()
    stack = [item_id]
    while stack:
        current = stack.pop()
        if current in selected or current in required:
            continue
        item = by_id.get(current)
        if item is None:
            raise KeyError(f"missing context dependency: {current}")
        required.add(current)
        stack.extend(item.dependencies)
    return required


def pack_context(
    items: Iterable[ContextItem],
    *,
    budget: int,
    mandatory_roles: Iterable[str] = (),
) -> ContextPack:
    """Deterministic dependency-aware context packing.

    Mandatory roles and explicit mandatory items fail closed. Optional items are
    selected by marginal utility per token with a local replacement pass. The
    function returns stable-prefix sections rather than only advisory actions.
    """

    if budget <= 0:
        raise ValueError("budget must be positive")
    rows = tuple(items)
    by_id = {item.item_id: item for item in rows}
    if len(by_id) != len(rows):
        raise ValueError("duplicate context item id")
    roles = set(mandatory_roles)
    selected: set[str] = set()
    reasons: list[str] = []

    required_ids = {item.item_id for item in rows if item.mandatory or item.role in roles}
    expanded: set[str] = set()
    for item_id in sorted(required_ids):
        expanded.update(_closure(item_id, by_id, expanded))
    required_cost = sum(max(0, by_id[item_id].tokens) for item_id in expanded)
    if required_cost > budget:
        missing_roles = sorted(roles - {by_id[item_id].role for item_id in expanded})
        reasons.append(f"mandatory-over-budget:{required_cost}>{budget}")
        if missing_roles:
            reasons.append("missing-roles:" + ",".join(missing_roles))
        return ContextPack(
            budget,
            0,
            (),
            tuple(sorted(by_id)),
            stable_prefix_hash([]),
            False,
            0.0,
            (),
            tuple(reasons),
        )
    selected.update(expanded)
    used = required_cost

    def marginal(item: ContextItem) -> tuple[float, int, set[str]]:
        closure = _closure(item.item_id, by_id, selected)
        cost = sum(max(0, by_id[value].tokens) for value in closure)
        utility = sum(
            max(0.0, by_id[value].utility) * max(0.0, min(1.0, by_id[value].confidence))
            for value in closure
        )
        return utility, cost, closure

    candidates = [item for item in rows if item.item_id not in selected]
    while candidates:
        scored = []
        for item in candidates:
            utility, cost, closure = marginal(item)
            density = utility / max(1, cost)
            scored.append((-density, -utility, cost, item.item_id, closure))
        scored.sort()
        added = False
        for _, _, cost, item_id, closure in scored:
            if cost <= budget - used:
                selected.update(closure)
                used += cost
                candidates = [item for item in candidates if item.item_id not in selected]
                added = True
                break
        if not added:
            break

    # Local replacement: one dropped item may replace one lower-value optional item.
    optional_selected = [by_id[value] for value in selected if value not in expanded]
    optional_selected.sort(key=lambda item: (item.utility * item.confidence / max(1, item.tokens), item.item_id))
    dropped = [item for item in rows if item.item_id not in selected]
    for incoming in sorted(dropped, key=lambda item: (-item.utility * item.confidence, item.item_id)):
        utility, cost, closure = marginal(incoming)
        for outgoing in optional_selected:
            if outgoing.item_id not in selected:
                continue
            outgoing_value = outgoing.utility * outgoing.confidence
            if utility <= outgoing_value:
                continue
            freed = outgoing.tokens
            if cost <= budget - used + freed and not any(
                outgoing.item_id in by_id[item_id].dependencies for item_id in selected if item_id != outgoing.item_id
            ):
                selected.remove(outgoing.item_id)
                selected.update(closure)
                used = used - freed + cost
                break

    selected_rows = [by_id[value] for value in selected]
    selected_rows.sort(key=lambda item: (not item.stable, item.role, item.item_id))
    sections = tuple((item.item_id, item.text) for item in selected_rows)
    selected_roles = {item.role for item in selected_rows}
    mandatory_satisfied = roles.issubset(selected_roles) and expanded.issubset(selected)
    total_utility = sum(item.utility * item.confidence for item in selected_rows)
    return ContextPack(
        budget,
        used,
        tuple(item.item_id for item in selected_rows),
        tuple(sorted(set(by_id) - selected)),
        stable_prefix_hash(list(sections)),
        mandatory_satisfied,
        total_utility,
        sections,
        tuple(reasons),
    )


def compile_provider_token_envelope(
    *,
    original_input_tokens: int,
    original_output_budget_tokens: int,
    input_leases: Iterable[NecessityLease],
    output_leases: Iterable[NecessityLease] = (),
    policy: TokenEnvelopePolicy | None = None,
) -> ProviderTokenEnvelope:
    """Canonical context-governor entry point for provider input/output admission."""

    return ProviderTokenEnvelopeCompiler(policy).compile_from_leases(
        original_input_tokens=original_input_tokens,
        original_output_budget_tokens=original_output_budget_tokens,
        input_leases=input_leases,
        output_leases=output_leases,
    )


# U5-P0-02 remains exposed through the canonical context-governor surface while
# its accounting engine stays isolated from context packing/persistence owners.
from .reacquisition_tax import (  # noqa: E402
    ContextCostEstimate,
    ReacquisitionEvent,
    ReacquisitionTaxDecision,
    ReacquisitionTaxGovernor,
    ReacquisitionTaxPolicy,
)

# U5-P0-05 Global Token Budget Auction is a decision-only overlay on the
# canonical context/provider admission surface. Existing lane owners remain
# responsible for retrieval, context selection, reasoning policy, and output.
import math
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping


LANES = ("retrieval", "instructions_context", "schema_tool_output", "reasoning", "output")
INPUT_LANES = frozenset(LANES[:3])
OUTPUT_LANES = frozenset(LANES[3:])
OVERHEAD_KINDS = frozenset(("retry", "recall", "repair", "fallback"))


def _uint(value: int, name: str) -> int:
    value = int(value)
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _gain(value: float) -> float:
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError("estimated gain must be finite and non-negative")
    return value


def _sha(value: str, name: str) -> str:
    value = str(value).strip().casefold()
    if value and (len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value)):
        raise ValueError(f"{name} must be empty or lowercase sha256")
    return value


@dataclass(frozen=True)
class MarginalTokenBid:
    bid_id: str
    lane: str
    tokens: int
    estimated_verified_gain: float
    evidence_ref: str
    ordinal: int = 0

    def __post_init__(self) -> None:
        if not str(self.bid_id).strip() or not str(self.evidence_ref).strip():
            raise ValueError("bid_id and evidence_ref are required")
        if self.lane not in LANES:
            raise ValueError(f"unsupported lane: {self.lane}")
        if int(self.tokens) <= 0:
            raise ValueError("bid tokens must be positive")
        if int(self.ordinal) < 0:
            raise ValueError("bid ordinal must be non-negative")
        object.__setattr__(self, "tokens", int(self.tokens))
        object.__setattr__(self, "ordinal", int(self.ordinal))
        object.__setattr__(self, "estimated_verified_gain", _gain(self.estimated_verified_gain))

    @property
    def density(self) -> float:
        return self.estimated_verified_gain / self.tokens


@dataclass(frozen=True)
class LaneBudgetRequest:
    lane: str
    fixed_tokens: int
    hard_min_tokens: int
    max_tokens: int
    hard_min_evidence_ref: str
    bids: tuple[MarginalTokenBid, ...] = ()

    def __post_init__(self) -> None:
        if self.lane not in LANES:
            raise ValueError(f"unsupported lane: {self.lane}")
        fixed = _uint(self.fixed_tokens, "fixed_tokens")
        hard = _uint(self.hard_min_tokens, "hard_min_tokens")
        maximum = _uint(self.max_tokens, "max_tokens")
        if not hard <= fixed <= maximum:
            raise ValueError("lane budgets must satisfy hard_min <= fixed <= max")
        if hard and not str(self.hard_min_evidence_ref).strip():
            raise ValueError("non-zero hard minimum requires evidence")
        bids = tuple(self.bids)
        if any(bid.lane != self.lane for bid in bids):
            raise ValueError("bid lane mismatch")
        if len({bid.bid_id for bid in bids}) != len(bids):
            raise ValueError("duplicate bid id")
        if list(bids) != sorted(bids, key=lambda bid: (bid.ordinal, bid.bid_id)):
            raise ValueError("bids must be ordered by ordinal then id")
        if any(b.density + 1e-15 < n.density for b, n in zip(bids, bids[1:])):
            raise ValueError("per-lane marginal gain must be non-increasing")
        if sum(bid.tokens for bid in bids) > maximum - hard:
            raise ValueError("bid tokens exceed auctionable lane capacity")
        object.__setattr__(self, "fixed_tokens", fixed)
        object.__setattr__(self, "hard_min_tokens", hard)
        object.__setattr__(self, "max_tokens", maximum)
        object.__setattr__(self, "bids", bids)


@dataclass(frozen=True)
class AuctionContext:
    provider: str
    model: str
    workload_family: str
    tokenizer: str
    frozen_task_ref: str
    verifier_ref: str
    mode: str = "shadow"

    def __post_init__(self) -> None:
        for name in ("provider", "model", "workload_family", "tokenizer", "frozen_task_ref", "verifier_ref"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        mode = str(self.mode).casefold()
        if mode not in {"shadow", "offline"}:
            raise ValueError("U5-P0-05 permits shadow or offline allocation only")
        object.__setattr__(self, "mode", mode)


@dataclass(frozen=True)
class BidAllocation:
    bid_id: str
    lane: str
    offered_tokens: int
    allocated_tokens: int
    density: float
    allocated_gain: float
    evidence_ref: str


@dataclass(frozen=True)
class TokenBudgetAuctionDecision:
    provider: str
    model: str
    workload_family: str
    tokenizer: str
    frozen_task_ref: str
    verifier_ref: str
    mode: str
    envelope_receipt_hash: str
    input_budget_tokens: int
    output_budget_tokens: int
    fixed_allocation: dict[str, int]
    recommended_allocation: dict[str, int]
    effective_allocation: dict[str, int]
    hard_minima: dict[str, int]
    bid_allocations: tuple[BidAllocation, ...]
    fixed_estimated_verified_gain: float
    recommended_estimated_verified_gain: float
    input_unallocated_tokens: int
    output_unallocated_tokens: int
    reason_codes: tuple[str, ...]
    provider_savings_claim: bool
    receipt_hash: str

    def receipt(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def verify_receipt(receipt: Mapping[str, Any]) -> bool:
        body = dict(receipt)
        claimed = str(body.pop("receipt_hash", ""))
        if not claimed or sha256_bytes(canonical_json(body)) != claimed:
            raise ValueError("auction receipt hash mismatch")
        return True


@dataclass(frozen=True)
class AuctionCostEvent:
    event_id: str
    kind: str
    lane: str
    provider_visible_tokens: int
    evidence_ref: str
    usage_receipt_hash: str = ""
    token_receipt_hash: str = ""
    event_hash: str = ""

    def __post_init__(self) -> None:
        if not str(self.event_id).strip() or not str(self.evidence_ref).strip():
            raise ValueError("event id and evidence are required")
        if self.kind not in OVERHEAD_KINDS or self.lane not in LANES:
            raise ValueError("unsupported overhead kind or lane")
        tokens = _uint(self.provider_visible_tokens, "provider_visible_tokens")
        usage = _sha(self.usage_receipt_hash, "usage_receipt_hash")
        token = _sha(self.token_receipt_hash, "token_receipt_hash")
        if bool(usage) != bool(token):
            raise ValueError("usage/token provider receipts must be paired")
        body = {
            "event_id": self.event_id, "kind": self.kind, "lane": self.lane,
            "provider_visible_tokens": tokens, "evidence_ref": self.evidence_ref,
            "usage_receipt_hash": usage, "token_receipt_hash": token,
        }
        digest = sha256_bytes(canonical_json(body))
        if self.event_hash and self.event_hash != digest:
            raise ValueError("event hash mismatch")
        object.__setattr__(self, "provider_visible_tokens", tokens)
        object.__setattr__(self, "usage_receipt_hash", usage)
        object.__setattr__(self, "token_receipt_hash", token)
        object.__setattr__(self, "event_hash", digest)

    @property
    def provider_evidence_complete(self) -> bool:
        return bool(self.usage_receipt_hash and self.token_receipt_hash)


class TokenBudgetAllocator:
    """Decision-only global allocator; existing lane owners remain execution authorities."""

    schema_version = 1

    def __init__(self) -> None:
        self._events: list[AuctionCostEvent] = []

    @classmethod
    def status(cls) -> dict[str, Any]:
        return {
            "lanes": LANES,
            "provider_envelope_remains_budget_authority": True,
            "allocator_owns_provider_admission": False,
            "allocator_owns_context_selection": False,
            "allocator_owns_reasoning_policy": False,
            "allocator_owns_output_rendering": False,
            "hard_minima_preserved": True,
            "fixed_lane_baseline_required": True,
            "shadow_or_offline_only": True,
            "overhead_kinds_counted": tuple(sorted(OVERHEAD_KINDS)),
            "provider_savings_claim": False,
        }

    @staticmethod
    def _normalize(requests: Iterable[LaneBudgetRequest]) -> tuple[LaneBudgetRequest, ...]:
        rows = tuple(requests)
        by_lane = {row.lane: row for row in rows}
        if len(by_lane) != len(rows) or set(by_lane) != set(LANES):
            raise ValueError("auction requires exactly one request for each canonical lane")
        return tuple(by_lane[lane] for lane in LANES)

    @staticmethod
    def _gain_at(row: LaneBudgetRequest, allocation: int) -> float:
        remaining = max(0, int(allocation) - row.hard_min_tokens)
        result = 0.0
        for bid in row.bids:
            used = min(remaining, bid.tokens)
            result += bid.estimated_verified_gain * used / bid.tokens
            remaining -= used
            if remaining <= 0:
                break
        return result

    def allocate(self, *, envelope: ProviderTokenEnvelope, context: AuctionContext,
                 requests: Iterable[LaneBudgetRequest]) -> TokenBudgetAuctionDecision:
        if not envelope.provider_call_admissible:
            raise ValueError("ProviderTokenEnvelope rejected provider admission")
        rows = self._normalize(requests)
        fixed = {row.lane: row.fixed_tokens for row in rows}
        minima = {row.lane: row.hard_min_tokens for row in rows}
        for lanes, cap, mandatory, label in (
            (INPUT_LANES, envelope.provider_input_budget_tokens, envelope.mandatory_input_tokens, "input"),
            (OUTPUT_LANES, envelope.provider_output_budget_tokens, envelope.mandatory_output_tokens, "output"),
        ):
            if sum(fixed[lane] for lane in lanes) != cap:
                raise ValueError(f"fixed {label} budgets must exactly partition ProviderTokenEnvelope")
            hard_total = sum(minima[lane] for lane in lanes)
            if hard_total > cap:
                raise ValueError(f"hard {label} minima exceed ProviderTokenEnvelope")
            if hard_total < mandatory:
                raise ValueError(f"hard {label} minima do not preserve mandatory tokens")

        recommended = dict(minima)
        remaining = {
            "input": envelope.provider_input_budget_tokens - sum(minima[l] for l in INPUT_LANES),
            "output": envelope.provider_output_budget_tokens - sum(minima[l] for l in OUTPUT_LANES),
        }
        max_by_lane = {row.lane: row.max_tokens for row in rows}
        candidates = sorted(
            (-bid.density, bid.lane, bid.ordinal, bid.bid_id, bid)
            for row in rows for bid in row.bids
        )
        allocations: list[BidAllocation] = []
        for _, lane, _, _, bid in candidates:
            side = "input" if lane in INPUT_LANES else "output"
            take = min(bid.tokens, max_by_lane[lane] - recommended[lane], remaining[side])
            take = max(0, take)
            recommended[lane] += take
            remaining[side] -= take
            allocations.append(BidAllocation(
                bid.bid_id, lane, bid.tokens, take, round(bid.density, 12),
                round(bid.estimated_verified_gain * take / bid.tokens, 12), bid.evidence_ref
            ))

        fixed_gain = sum(self._gain_at(row, fixed[row.lane]) for row in rows)
        recommended_gain = sum(self._gain_at(row, recommended[row.lane]) for row in rows)
        effective = dict(fixed if context.mode == "shadow" else recommended)
        reasons = ["HARD_MINIMA_RESERVED", "ENVELOPE_AUTHORITY_PRESERVED"]
        reasons.append("SHADOW_RECOMMENDATION_NOT_APPLIED" if context.mode == "shadow"
                       else "OFFLINE_RECOMMENDATION_APPLIED")
        reasons.append("MARGINAL_VALUE_NON_INFERIOR_TO_FIXED_BASELINE"
                       if recommended_gain + 1e-12 >= fixed_gain
                       else "MARGINAL_VALUE_BELOW_FIXED_BASELINE")
        body = {
            "provider": context.provider, "model": context.model,
            "workload_family": context.workload_family, "tokenizer": context.tokenizer,
            "frozen_task_ref": context.frozen_task_ref, "verifier_ref": context.verifier_ref,
            "mode": context.mode, "envelope_receipt_hash": envelope.receipt_hash,
            "input_budget_tokens": envelope.provider_input_budget_tokens,
            "output_budget_tokens": envelope.provider_output_budget_tokens,
            "fixed_allocation": fixed, "recommended_allocation": recommended,
            "effective_allocation": effective, "hard_minima": minima,
            "bid_allocations": [asdict(item) for item in allocations],
            "fixed_estimated_verified_gain": round(fixed_gain, 12),
            "recommended_estimated_verified_gain": round(recommended_gain, 12),
            "input_unallocated_tokens": remaining["input"],
            "output_unallocated_tokens": remaining["output"],
            "reason_codes": tuple(reasons), "provider_savings_claim": False,
        }
        receipt_hash = sha256_bytes(canonical_json(body))
        return TokenBudgetAuctionDecision(
            context.provider, context.model, context.workload_family, context.tokenizer,
            context.frozen_task_ref, context.verifier_ref, context.mode, envelope.receipt_hash,
            envelope.provider_input_budget_tokens, envelope.provider_output_budget_tokens,
            fixed, recommended, effective, minima, tuple(allocations),
            round(fixed_gain, 12), round(recommended_gain, 12),
            remaining["input"], remaining["output"], tuple(reasons), False, receipt_hash
        )

    def record_overhead(self, event: AuctionCostEvent) -> AuctionCostEvent:
        if any(row.event_id == event.event_id for row in self._events):
            raise ValueError("duplicate overhead event id")
        self._events.append(event)
        return event

    def accounting_receipt(self, decision: TokenBudgetAuctionDecision, *,
                           baseline_usage_receipt_hash: str = "",
                           baseline_token_receipt_hash: str = "",
                           optimized_usage_receipt_hash: str = "",
                           optimized_token_receipt_hash: str = "") -> dict[str, Any]:
        TokenBudgetAuctionDecision.verify_receipt(decision.receipt())
        hashes = [
            _sha(baseline_usage_receipt_hash, "baseline_usage_receipt_hash"),
            _sha(baseline_token_receipt_hash, "baseline_token_receipt_hash"),
            _sha(optimized_usage_receipt_hash, "optimized_usage_receipt_hash"),
            _sha(optimized_token_receipt_hash, "optimized_token_receipt_hash"),
        ]
        if bool(hashes[0]) != bool(hashes[1]) or bool(hashes[2]) != bool(hashes[3]):
            raise ValueError("provider usage/token receipts must be paired")
        by_kind = {kind: 0 for kind in sorted(OVERHEAD_KINDS)}
        by_lane = {lane: 0 for lane in LANES}
        verified_overhead = 0
        for event in self._events:
            by_kind[event.kind] += event.provider_visible_tokens
            by_lane[event.lane] += event.provider_visible_tokens
            if event.provider_evidence_complete:
                verified_overhead += event.provider_visible_tokens
        overhead = sum(by_kind.values())
        baseline = sum(decision.fixed_allocation.values())
        recommended = sum(decision.recommended_allocation.values())
        body = {
            "auction_receipt_hash": decision.receipt_hash,
            "baseline_total_tokens": baseline,
            "recommended_total_tokens": recommended,
            "overhead_tokens_by_kind": by_kind, "overhead_tokens_by_lane": by_lane,
            "overhead_provider_visible_tokens": overhead,
            "provider_verified_overhead_tokens": verified_overhead,
            "estimated_net_token_delta_including_overhead": recommended + overhead - baseline,
            "baseline_usage_receipt_hash": hashes[0], "baseline_token_receipt_hash": hashes[1],
            "optimized_usage_receipt_hash": hashes[2], "optimized_token_receipt_hash": hashes[3],
            "paired_execution_evidence_complete": all(hashes),
            "event_hashes": tuple(event.event_hash for event in self._events),
            "provider_savings_claim": False,
        }
        body["receipt_hash"] = sha256_bytes(canonical_json(body))
        return body

    @staticmethod
    def verify_accounting_receipt(receipt: Mapping[str, Any]) -> bool:
        body = dict(receipt)
        claimed = str(body.pop("receipt_hash", ""))
        if not claimed or sha256_bytes(canonical_json(body)) != claimed:
            raise ValueError("accounting receipt hash mismatch")
        if body.get("provider_savings_claim") is not False:
            raise ValueError("U5-P0-05 cannot promote provider savings")
        return True
