from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from .util import canonical_json, sha256_bytes


REQUIRED_TRACE_DECISIONS = frozenset({"include", "omit", "compress", "retrieve", "reset", "abstain"})
TRACE_DECISIONS = frozenset((*REQUIRED_TRACE_DECISIONS, "branch"))
ZERO_HASH = "0" * 64

_ACTION_TO_DECISION = {
    "KEEP": "include",
    "SUMMARIZE": "compress",
    "COMPRESS": "compress",
    "EXTERNALIZE": "omit",
    "RESET": "reset",
    "ABSTAIN": "abstain",
    "BRANCH": "branch",
    "RETRIEVE": "retrieve",
}
_FORBIDDEN_REFERENCE_KEYS = frozenset({"content", "payload", "raw_text", "body", "secret", "text"})


def _require_sha256(value: str, *, name: str) -> str:
    normalized = str(value).casefold()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError(f"{name} must be a lowercase sha256")
    return normalized


def _reference_only(value: Any, *, path: str = "reference") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).casefold() in _FORBIDDEN_REFERENCE_KEYS:
                raise ValueError(f"{path} cannot carry context payload authority: {key}")
            _reference_only(nested, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _reference_only(nested, path=f"{path}[{index}]")


def _decision(action: str) -> str:
    try:
        return _ACTION_TO_DECISION[str(action)]
    except KeyError as exc:
        raise ValueError(f"unsupported traced policy action: {action}") from exc


@dataclass(frozen=True)
class ContextDecisionTraceEvent:
    sequence: int
    scope: str
    identity: str
    recommended_decision: str
    effective_decision: str
    recommended_action: str
    effective_action: str
    reason_codes: tuple[str, ...]
    source_refs: tuple[str, ...]
    namespace_uri: str
    item_id: str
    input_tokens: int
    visible_tokens: int
    previous_event_hash: str
    event_hash: str

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("trace sequence must be positive")
        if self.scope not in {"item", "session", "retrieval"}:
            raise ValueError(f"unsupported trace scope: {self.scope}")
        if not self.identity:
            raise ValueError("trace identity is required")
        if self.recommended_decision not in TRACE_DECISIONS:
            raise ValueError(f"unsupported recommended trace decision: {self.recommended_decision}")
        if self.effective_decision not in TRACE_DECISIONS:
            raise ValueError(f"unsupported effective trace decision: {self.effective_decision}")
        if self.input_tokens < 0 or self.visible_tokens < 0:
            raise ValueError("trace token counts must be non-negative")
        _require_sha256(self.previous_event_hash, name="previous_event_hash")
        _require_sha256(self.event_hash, name="event_hash")


class ContextDecisionTrace:
    """Deterministic, reference-only trace over context policy decisions.

    The trace records what policy recommended and what actually took effect.
    It does not own policy snapshots, evidence persistence, context payloads,
    or side effects. Retrieval is recorded explicitly as a later reference-only
    event instead of pretending EXTERNALIZE already performed a retrieval.
    """

    schema_version = 1

    @staticmethod
    def _verify_policy_receipt(result: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
        receipt = result.get("receipt")
        if not isinstance(receipt, Mapping):
            raise ValueError("context decision trace requires a policy receipt")
        basis = dict(receipt)
        observed = str(basis.pop("receipt_hash", ""))
        _require_sha256(observed, name="policy receipt hash")
        expected = sha256_bytes(canonical_json(basis))
        if observed != expected:
            raise ValueError("policy receipt hash mismatch")
        _reference_only(basis.get("signals") or [], path="policy_receipt.signals")
        return basis, observed

    @staticmethod
    def _seal_event(
        *,
        sequence: int,
        scope: str,
        identity: str,
        recommended_action: str,
        effective_action: str,
        reason_codes: Iterable[str] = (),
        source_refs: Iterable[str] = (),
        namespace_uri: str = "",
        item_id: str = "",
        input_tokens: int = 0,
        visible_tokens: int = 0,
        previous_event_hash: str = ZERO_HASH,
    ) -> ContextDecisionTraceEvent:
        refs = tuple(sorted(set(str(value) for value in source_refs if str(value))))
        reasons = tuple(dict.fromkeys(str(value) for value in reason_codes if str(value)))
        basis = {
            "schema_version": 1,
            "sequence": int(sequence),
            "scope": str(scope),
            "identity": str(identity),
            "recommended_decision": _decision(recommended_action),
            "effective_decision": _decision(effective_action),
            "recommended_action": str(recommended_action),
            "effective_action": str(effective_action),
            "reason_codes": list(reasons),
            "source_refs": list(refs),
            "namespace_uri": str(namespace_uri),
            "item_id": str(item_id),
            "input_tokens": max(0, int(input_tokens)),
            "visible_tokens": max(0, int(visible_tokens)),
            "previous_event_hash": _require_sha256(previous_event_hash, name="previous_event_hash"),
        }
        event_hash = sha256_bytes(canonical_json(basis))
        return ContextDecisionTraceEvent(
            sequence=basis["sequence"],
            scope=basis["scope"],
            identity=basis["identity"],
            recommended_decision=basis["recommended_decision"],
            effective_decision=basis["effective_decision"],
            recommended_action=basis["recommended_action"],
            effective_action=basis["effective_action"],
            reason_codes=reasons,
            source_refs=refs,
            namespace_uri=basis["namespace_uri"],
            item_id=basis["item_id"],
            input_tokens=basis["input_tokens"],
            visible_tokens=basis["visible_tokens"],
            previous_event_hash=basis["previous_event_hash"],
            event_hash=event_hash,
        )

    @classmethod
    def _seal_trace(cls, policy_receipt_hash: str, events: Iterable[ContextDecisionTraceEvent]) -> dict[str, Any]:
        receipt_hash = _require_sha256(policy_receipt_hash, name="policy_receipt_hash")
        rows = [asdict(event) for event in events]
        counts = Counter(str(row["recommended_decision"]) for row in rows)
        basis = {
            "schema_version": cls.schema_version,
            "policy_receipt_hash": receipt_hash,
            "events": rows,
            "event_count": len(rows),
            "decision_counts": dict(sorted(counts.items())),
        }
        return {
            **basis,
            "trace_hash": sha256_bytes(canonical_json(basis)),
        }

    @classmethod
    def from_policy_result(cls, result: Mapping[str, Any]) -> dict[str, Any]:
        receipt, receipt_hash = cls._verify_policy_receipt(result)
        decisions = receipt.get("decisions") or []
        if not isinstance(decisions, list):
            raise ValueError("policy receipt decisions must be a list")

        events: list[ContextDecisionTraceEvent] = []
        previous = ZERO_HASH
        for row in decisions:
            if not isinstance(row, Mapping):
                raise ValueError("policy decision row must be an object")
            event = cls._seal_event(
                sequence=len(events) + 1,
                scope="item",
                identity=str(row.get("identity") or ""),
                recommended_action=str(row.get("recommended_action") or ""),
                effective_action=str(row.get("effective_action") or ""),
                reason_codes=row.get("reason_codes") or (),
                source_refs=row.get("source_refs") or (),
                namespace_uri=str(row.get("namespace_uri") or ""),
                item_id=str(row.get("item_id") or ""),
                input_tokens=int(row.get("input_tokens") or 0),
                visible_tokens=int(row.get("visible_tokens") or 0),
                previous_event_hash=previous,
            )
            events.append(event)
            previous = event.event_hash

        metrics = result.get("metrics") if isinstance(result.get("metrics"), Mapping) else {}
        session_event = cls._seal_event(
            sequence=len(events) + 1,
            scope="session",
            identity="session",
            recommended_action=str(receipt.get("recommended_session_action") or ""),
            effective_action=str(receipt.get("effective_session_action") or ""),
            reason_codes=receipt.get("session_reason_codes") or (),
            input_tokens=int(metrics.get("input_tokens") or 0),
            visible_tokens=int(metrics.get("effective_visible_tokens") or 0),
            previous_event_hash=previous,
        )
        events.append(session_event)
        trace = cls._seal_trace(receipt_hash, events)
        cls.verify(trace)
        return trace

    @classmethod
    def append_retrieval(
        cls,
        trace: Mapping[str, Any],
        *,
        identity: str,
        source_refs: Iterable[str],
        namespace_uri: str = "",
        item_id: str = "",
        reason_codes: Iterable[str] = ("RECOVERY_HANDLE_RETRIEVED",),
        visible_tokens: int = 0,
    ) -> dict[str, Any]:
        cls.verify(trace)
        rows = trace.get("events") or []
        events = [ContextDecisionTraceEvent(**dict(row)) for row in rows]
        previous = events[-1].event_hash if events else ZERO_HASH
        event = cls._seal_event(
            sequence=len(events) + 1,
            scope="retrieval",
            identity=str(identity),
            recommended_action="RETRIEVE",
            effective_action="RETRIEVE",
            reason_codes=reason_codes,
            source_refs=source_refs,
            namespace_uri=namespace_uri,
            item_id=item_id,
            visible_tokens=visible_tokens,
            previous_event_hash=previous,
        )
        events.append(event)
        result = cls._seal_trace(str(trace.get("policy_receipt_hash") or ""), events)
        cls.verify(result)
        return result

    @classmethod
    def verify(cls, trace: Mapping[str, Any]) -> bool:
        if trace.get("schema_version") != cls.schema_version:
            raise ValueError("context decision trace schema drift")
        receipt_hash = _require_sha256(str(trace.get("policy_receipt_hash") or ""), name="policy_receipt_hash")
        rows = trace.get("events")
        if not isinstance(rows, list) or not rows:
            raise ValueError("context decision trace requires events")
        if int(trace.get("event_count", -1)) != len(rows):
            raise ValueError("context decision trace event count drift")

        previous = ZERO_HASH
        events: list[ContextDecisionTraceEvent] = []
        for index, raw in enumerate(rows, start=1):
            if not isinstance(raw, Mapping):
                raise ValueError("context decision trace event must be an object")
            row = dict(raw)
            observed_hash = str(row.pop("event_hash", ""))
            if int(row.get("sequence", -1)) != index:
                raise ValueError("context decision trace sequence drift")
            if str(row.get("previous_event_hash") or "") != previous:
                raise ValueError("context decision trace previous hash drift")
            expected_hash = sha256_bytes(canonical_json({"schema_version": 1, **row}))
            if observed_hash != expected_hash:
                raise ValueError("context decision trace event hash mismatch")
            event = ContextDecisionTraceEvent(event_hash=observed_hash, **row)
            events.append(event)
            previous = observed_hash

        expected_counts = dict(sorted(Counter(event.recommended_decision for event in events).items()))
        if trace.get("decision_counts") != expected_counts:
            raise ValueError("context decision trace decision counts drift")
        basis = {
            "schema_version": cls.schema_version,
            "policy_receipt_hash": receipt_hash,
            "events": [asdict(event) for event in events],
            "event_count": len(events),
            "decision_counts": expected_counts,
        }
        expected_trace_hash = sha256_bytes(canonical_json(basis))
        if str(trace.get("trace_hash") or "") != expected_trace_hash:
            raise ValueError("context decision trace hash mismatch")
        return True


__all__ = [
    "REQUIRED_TRACE_DECISIONS",
    "TRACE_DECISIONS",
    "ContextDecisionTraceEvent",
    "ContextDecisionTrace",
]
