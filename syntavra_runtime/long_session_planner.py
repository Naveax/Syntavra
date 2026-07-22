from __future__ import annotations

import json
import math
import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from .session_runtime import SessionEvent, SessionRuntime
from .util import canonical_json, sha256_bytes


_WORD_RE = re.compile(r"[A-Za-z0-9_./:-]+", re.UNICODE)
_CRITICAL_TYPES = {
    "decision", "error", "failure", "security", "verification", "claim",
    "checkpoint", "session-fork", "session-merge", "task-result",
}
_SUBJECT_KEYS = ("subject", "decision_id", "key", "path", "task_id", "claim_id")
_SUPERSESSION_KEYS = ("supersedes", "replaces", "revokes")


@dataclass(frozen=True)
class ContextPlanPolicy:
    token_budget: int = 32_000
    recent_events: int = 24
    chars_per_token: float = 4.0
    summary_preview_chars: int = 12_000
    event_preview_chars: int = 2_000
    max_candidates: int = 512
    forced_event_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlannedSection:
    role: str
    reference: str
    estimated_tokens: int
    score: float
    mandatory: bool
    temporal_status: str
    text: str
    event_sequence: int | None = None
    summary_id: str | None = None


class LongSessionPlanner:
    """Query-aware context assembly over the exact immutable session runtime.

    Planning is lossy only in the visible preview. Every selected event or summary
    carries a stable exact reference; session history remains recoverable and
    hash-verifiable.
    """

    def __init__(self, runtime: SessionRuntime):
        self.runtime = runtime

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {token.casefold() for token in _WORD_RE.findall(text) if len(token) > 1}

    @staticmethod
    def _event_text(event: SessionEvent) -> str:
        return json.dumps(
            {"type": event.event_type, "payload": event.payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _subject(event: SessionEvent) -> str:
        for key in _SUBJECT_KEYS:
            value = event.payload.get(key)
            if value not in (None, ""):
                return f"{key}:{value}"
        return ""

    @staticmethod
    def _superseded_identifiers(events: Iterable[SessionEvent]) -> set[str]:
        values: set[str] = set()
        for event in events:
            for key in _SUPERSESSION_KEYS:
                raw = event.payload.get(key)
                if isinstance(raw, (list, tuple, set)):
                    values.update(str(item) for item in raw if item not in (None, ""))
                elif raw not in (None, ""):
                    values.add(str(raw))
        return values

    @classmethod
    def _temporal_status(
        cls,
        event: SessionEvent,
        *,
        latest_by_subject: dict[str, int],
        superseded_identifiers: set[str],
    ) -> str:
        subject = cls._subject(event)
        if subject and latest_by_subject.get(subject, event.sequence) != event.sequence:
            return "superseded"
        identifiers = {
            str(value)
            for key in ("decision_id", "id", "version", "claim_id")
            if (value := event.payload.get(key)) not in (None, "")
        }
        if identifiers & superseded_identifiers:
            return "superseded"
        if bool(event.payload.get("revoked") or event.payload.get("obsolete")):
            return "revoked"
        return "current"

    @staticmethod
    def _preview(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        marker = "\n[… exact section available by reference …]"
        return text[: max(0, limit - len(marker))].rstrip() + marker

    @staticmethod
    def _event_reference(event: SessionEvent) -> str:
        return f"sc://session/{event.session_id}/event/{event.sequence}/{event.event_hash}"

    @staticmethod
    def _summary_reference(session_id: str, summary_id: str) -> str:
        return f"sc://session/{session_id}/summary/{summary_id}"

    @staticmethod
    def _section_tokens(section: PlannedSection, chars_per_token: float) -> int:
        # Count the model-visible envelope, not only the preview text.
        payload = asdict(section)
        payload["estimated_tokens"] = 0
        serialized = canonical_json(payload)
        return max(1, math.ceil(len(serialized) / chars_per_token))

    def _summary_row(self, summary_id: str) -> dict[str, Any]:
        with self.runtime.state.read() as db:
            row = db.execute(
                "SELECT * FROM session_summaries WHERE summary_id=? AND invalidated_at IS NULL",
                (summary_id,),
            ).fetchone()
        if row is None:
            raise KeyError(summary_id)
        return dict(row)

    def plan(
        self,
        session_id: str,
        query: str,
        *,
        policy: ContextPlanPolicy | None = None,
    ) -> dict[str, Any]:
        policy = policy or ContextPlanPolicy()
        if policy.token_budget < 1:
            raise ValueError("token_budget must be positive")
        if policy.recent_events < 0:
            raise ValueError("recent_events cannot be negative")
        if policy.chars_per_token <= 0:
            raise ValueError("chars_per_token must be positive")

        started = time.perf_counter()
        events = self.runtime.events(session_id, limit=10_000_000)
        verification = self.runtime.verify(session_id)
        if not verification["ok"]:
            raise ValueError("session chain failed verification")
        if not events:
            return {
                "session_id": session_id,
                "query": query,
                "budget": policy.token_budget,
                "used": 0,
                "visible_estimated_tokens": 0,
                "sections": [],
                "exact_history_events": 0,
                "verification": verification,
                "plan_hash": sha256_bytes(canonical_json([])),
                "planning_ms": (time.perf_counter() - started) * 1000,
            }

        query_tokens = self._tokens(query)
        latest_by_subject: dict[str, int] = {}
        for event in events:
            subject = self._subject(event)
            if subject:
                latest_by_subject[subject] = event.sequence
        superseded = self._superseded_identifiers(events)
        tail_start = max(1, events[-1].sequence - policy.recent_events + 1)
        forced = {name.casefold() for name in policy.forced_event_types}

        candidates: list[PlannedSection] = []
        for event in events[-max(policy.max_candidates, policy.recent_events):]:
            text = self._event_text(event)
            event_tokens = self._tokens(text)
            overlap = len(query_tokens & event_tokens)
            union = len(query_tokens | event_tokens) or 1
            semantic = overlap / union
            recency = event.sequence / max(1, events[-1].sequence)
            temporal = self._temporal_status(
                event,
                latest_by_subject=latest_by_subject,
                superseded_identifiers=superseded,
            )
            current_bonus = 2.0 if temporal == "current" else -2.5
            type_bonus = 2.0 if event.event_type.casefold() in _CRITICAL_TYPES else 0.0
            mandatory = (
                event.event_type.casefold() in forced
                or event.sequence >= tail_start
                or (event.event_type.casefold() in _CRITICAL_TYPES and temporal == "current")
            )
            score = semantic * 10.0 + recency * 2.0 + current_bonus + type_bonus
            if temporal != "current" and not mandatory and overlap == 0:
                continue
            preview = self._preview(text, policy.event_preview_chars)
            candidate = PlannedSection(
                role="event",
                reference=self._event_reference(event),
                estimated_tokens=0,
                score=score,
                mandatory=mandatory,
                temporal_status=temporal,
                text=preview,
                event_sequence=event.sequence,
            )
            candidates.append(PlannedSection(
                **{**asdict(candidate), "estimated_tokens": self._section_tokens(candidate, policy.chars_per_token)}
            ))

        summary_id = self.runtime.compact(session_id) if len(events) > policy.recent_events else None
        if summary_id:
            row = self._summary_row(summary_id)
            summary_text = self._preview(str(row["content"]), policy.summary_preview_chars)
            candidate = PlannedSection(
                role="summary",
                reference=self._summary_reference(session_id, summary_id),
                estimated_tokens=0,
                score=1.5,
                mandatory=False,
                temporal_status="aggregate",
                text=summary_text,
                summary_id=summary_id,
            )
            candidates.append(PlannedSection(
                **{**asdict(candidate), "estimated_tokens": self._section_tokens(candidate, policy.chars_per_token)}
            ))

        mandatory = sorted(
            (item for item in candidates if item.mandatory),
            key=lambda item: (
    item.score,
    item.temporal_status == "current",
    item.event_sequence or 0,
),
            reverse=True,
        )
        optional = sorted(
            (item for item in candidates if not item.mandatory),
            key=lambda item: (item.score, item.event_sequence or 0),
            reverse=True,
        )
        selected: list[PlannedSection] = []
        # Reserve list punctuation and per-item separators so the serialized
        # envelope cannot drift above the advertised model-visible budget.
        used = 2
        for item in (*mandatory, *optional):
            if any(existing.reference == item.reference for existing in selected):
                continue
            item_cost = item.estimated_tokens + 1
            if used + item_cost > policy.token_budget:
                continue
            selected.append(item)
            used += item_cost
        selected.sort(key=lambda item: (
            0 if item.role == "summary" else 1,
            item.event_sequence or 0,
        ))

        payload = [asdict(item) for item in selected]
        visible_estimated_tokens = max(
            0,
            math.ceil(len(canonical_json(payload)) / policy.chars_per_token),
        )
        # Defensive exact-envelope correction. Normally the reserved separator
        # budget is sufficient; this loop handles digit-width and Unicode edges.
        while selected and visible_estimated_tokens > policy.token_budget:
            removable = [
                (index, item)
                for index, item in enumerate(selected)
                if not item.mandatory
            ]
            if not removable:
                break
            remove_index, _ = min(removable, key=lambda pair: (pair[1].score, pair[1].event_sequence or 0))
            selected.pop(remove_index)
            payload = [asdict(item) for item in selected]
            visible_estimated_tokens = max(
                0,
                math.ceil(len(canonical_json(payload)) / policy.chars_per_token),
            )
        used = visible_estimated_tokens
        return {
            "session_id": session_id,
            "query": query,
            "budget": policy.token_budget,
            "used": used,
            "visible_estimated_tokens": visible_estimated_tokens,
            "sections": payload,
            "selected_events": sum(1 for item in selected if item.role == "event"),
            "selected_summaries": sum(1 for item in selected if item.role == "summary"),
            "exact_history_events": len(events),
            "root_summary_id": summary_id,
            "verification": verification,
            "plan_hash": sha256_bytes(canonical_json(payload)),
            "planning_ms": (time.perf_counter() - started) * 1000,
        }

    def stress_report(
        self,
        session_id: str,
        queries: Iterable[str],
        *,
        policy: ContextPlanPolicy | None = None,
    ) -> dict[str, Any]:
        plans = [self.plan(session_id, query, policy=policy) for query in queries]
        verification = self.runtime.verify(session_id)
        latencies = sorted(float(plan["planning_ms"]) for plan in plans)
        used = [int(plan["used"]) for plan in plans]
        p95_index = max(0, math.ceil(len(latencies) * 0.95) - 1) if latencies else 0
        return {
            "session_id": session_id,
            "queries": len(plans),
            "exact_history_events": int(verification["events"]),
            "chain_ok": bool(verification["ok"]),
            "all_within_budget": all(
                plan["used"] <= plan["budget"]
                and plan["visible_estimated_tokens"] <= plan["budget"]
                for plan in plans
            ),
            "all_exactly_referenced": all(
                section["reference"].startswith("sc://session/")
                for plan in plans
                for section in plan["sections"]
            ),
            "average_used_tokens": sum(used) / len(used) if used else 0.0,
            "p95_planning_ms": latencies[p95_index] if latencies else 0.0,
            "plan_hashes": [plan["plan_hash"] for plan in plans],
        }
