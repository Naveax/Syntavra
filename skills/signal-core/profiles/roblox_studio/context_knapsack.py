from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .errors import BudgetError, ValidationError

MANDATORY_ROLES = {
    "definition", "implementation", "caller", "dependency", "configuration",
    "security_boundary", "failure", "runtime_observation", "test", "validator",
    "rollback", "historical_decision",
}


@dataclass(frozen=True, slots=True)
class ContextCandidate:
    candidate_id: str
    role: str
    token_cost: int
    utility: float
    trust: float
    stale_risk: float
    exact_required: bool
    content: str
    recovery_handle: str


@dataclass(frozen=True, slots=True)
class ContextPackage:
    selected: tuple[ContextCandidate, ...]
    token_cost: int
    covered_roles: tuple[str, ...]


def select_context(candidates: Iterable[ContextCandidate], *, required_roles: Iterable[str], token_budget: int) -> ContextPackage:
    if token_budget < 0:
        raise BudgetError("negative token budget")
    items = tuple(candidates)
    required = set(required_roles)
    unknown_roles = required - MANDATORY_ROLES
    if unknown_roles:
        raise ValidationError(f"unknown evidence roles: {sorted(unknown_roles)}")
    selected: list[ContextCandidate] = []
    used = 0
    for role in sorted(required):
        options = [item for item in items if item.role == role]
        if not options:
            raise ValidationError(f"mandatory evidence role missing: {role}")
        option = max(options, key=lambda item: ((item.utility * item.trust) - item.stale_risk, item.exact_required, -item.token_cost))
        if used + option.token_cost > token_budget:
            raise BudgetError(f"token budget cannot satisfy mandatory role: {role}")
        if option not in selected:
            selected.append(option)
            used += option.token_cost
    remaining = [item for item in items if item not in selected]
    remaining.sort(key=lambda item: (((item.utility * item.trust) - item.stale_risk) / max(item.token_cost, 1), item.exact_required), reverse=True)
    for item in remaining:
        if used + item.token_cost <= token_budget:
            selected.append(item)
            used += item.token_cost
    covered = tuple(sorted({item.role for item in selected}))
    if not required.issubset(covered):
        raise ValidationError("context package omitted mandatory evidence")
    return ContextPackage(tuple(selected), used, covered)
