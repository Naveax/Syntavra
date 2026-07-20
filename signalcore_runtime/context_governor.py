from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .models import ContextDecision, ContextItem, ContextPack
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
