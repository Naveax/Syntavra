from __future__ import annotations

from .models import ContextDecision


DEFAULT_THRESHOLDS = (
    (0.50, ("evict_duplicates", "drop_raw_success_logs")),
    (0.60, ("externalize_evidence",)),
    (0.70, ("write_phase_capsule",)),
    (0.78, ("update_context_dag",)),
    (0.84, ("prepare_controlled_handoff",)),
    (0.88, ("mandatory_session_split",)),
)


def evaluate(used: int, window: int, *, thresholds=DEFAULT_THRESHOLDS) -> ContextDecision:
    if used < 0 or window <= 0:
        raise ValueError("used must be nonnegative and window positive")
    utilization = used / window
    actions: list[str] = []
    level = 0
    for threshold, names in thresholds:
        if utilization >= threshold:
            level += 1
            actions.extend(names)
    return ContextDecision(utilization, level, tuple(actions), utilization >= thresholds[-1][0])


def stable_prefix_hash(sections: list[tuple[str, str]]) -> str:
    from .util import canonical_json, sha256_bytes
    return sha256_bytes(canonical_json(sorted(sections, key=lambda item: item[0])))
