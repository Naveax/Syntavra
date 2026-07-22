from __future__ import annotations

import json
import math
import re
import time
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Protocol


class SessionRuntimeLike(Protocol):
    def events(self, session_id: str, *, after: int = 0, limit: int = 1000) -> Iterable[Any]: ...


_WORD = re.compile(r"[\w./:-]+", re.UNICODE)
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_ALIASES: dict[str, tuple[str, ...]] = {
    "auth": ("authentication", "authorization", "credential", "login"),
    "authentication": ("auth", "credential", "login"),
    "authorization": ("auth", "permission", "access"),
    "crash": ("failure", "fatal", "panic", "exception", "segfault"),
    "failure": ("failed", "error", "crash", "fatal"),
    "refresh": ("renew", "rotate", "rotation"),
    "token": ("credential", "jwt", "bearer"),
    "memory": ("context", "history", "recall"),
    "slow": ("latency", "performance", "timeout"),
    "delete": ("remove", "purge", "drop"),
    "fix": ("repair", "resolve", "patch"),
    "old": ("previous", "prior", "superseded"),
    "new": ("latest", "current", "replacement"),
    "karar": ("decision", "choice"),
    "hata": ("error", "failure", "bug"),
    "çökme": ("crash", "failure", "panic"),
}
_HIGH_VALUE_KEYS = {
    "task", "decision", "result", "error", "claim", "path", "command", "reason",
    "subject", "key", "value", "status", "supersedes", "superseded_by", "artifact_id",
}


@dataclass(frozen=True)
class SemanticEventHit:
    session_id: str
    sequence: int
    event_type: str
    score: float
    temporal_status: str
    subject: str
    text: str
    payload: dict[str, Any]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class SemanticContextPack:
    session_id: str
    query: str
    text: str
    visible_bytes: int
    hits: tuple[SemanticEventHit, ...]
    complete: bool


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold()
    text = _CAMEL.sub(" ", text)
    return re.sub(r"[_-]+", " ", text)


def _tokens(value: str) -> set[str]:
    return {token.strip("./:-") for token in _WORD.findall(_normalize(value)) if len(token.strip("./:-")) > 1}


def _expanded_tokens(value: str) -> set[str]:
    tokens = _tokens(value)
    expanded = set(tokens)
    for token in tuple(tokens):
        expanded.update(_ALIASES.get(token, ()))
    return expanded


def _trigrams(value: str) -> set[str]:
    compact = re.sub(r"\s+", " ", _normalize(value)).strip()
    if len(compact) < 3:
        return {compact} if compact else set()
    return {compact[index:index + 3] for index in range(len(compact) - 2)}


def _payload_text(payload: Mapping[str, Any]) -> str:
    preferred: list[str] = []
    remainder: list[str] = []
    for key, value in payload.items():
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) if isinstance(value, (dict, list, tuple)) else str(value)
        target = preferred if str(key) in _HIGH_VALUE_KEYS else remainder
        target.append(f"{key}={rendered}")
    return "\n".join(preferred + remainder)


def _event_subject(event_type: str, payload: Mapping[str, Any]) -> str:
    for key in ("subject", "key", "decision_id", "setting", "path", "artifact_id"):
        value = payload.get(key)
        if value not in (None, ""):
            return f"{key}:{value}"
    return f"event_type:{event_type}"


def _reference_values(payload: Mapping[str, Any], key: str) -> set[str]:
    value = payload.get(key)
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        return {str(item) for item in value}
    return {str(value)}


class SessionSemanticRetriever:
    """Deterministic semantic and temporal retrieval over exact session events."""

    def __init__(self, runtime: SessionRuntimeLike):
        self.runtime = runtime

    def _statuses(self, events: list[Any]) -> dict[int, str]:
        status = {int(event.sequence): "current" for event in events}
        ids: dict[str, int] = {}
        by_subject: dict[str, list[int]] = {}
        for event in events:
            sequence = int(event.sequence)
            payload = dict(getattr(event, "payload", {}) or {})
            for key in ("event_id", "decision_id", "memory_id", "artifact_id"):
                value = payload.get(key)
                if value not in (None, ""):
                    ids[str(value)] = sequence
            ids[str(sequence)] = sequence
            subject = _event_subject(str(getattr(event, "event_type", "")), payload)
            by_subject.setdefault(subject, []).append(sequence)

        for event in events:
            sequence = int(event.sequence)
            payload = dict(getattr(event, "payload", {}) or {})
            explicit = str(payload.get("status", "")).casefold()
            if explicit in {"superseded", "revoked", "cancelled", "invalid", "obsolete"} or payload.get("superseded_by"):
                status[sequence] = "superseded"
            for reference in _reference_values(payload, "supersedes") | _reference_values(payload, "replaces"):
                target = ids.get(reference)
                if target is not None and target != sequence:
                    status[target] = "superseded"

        for sequences in by_subject.values():
            if len(sequences) < 2:
                continue
            live = [sequence for sequence in sequences if status.get(sequence) == "current"]
            if len(live) > 1:
                for sequence in live[:-1]:
                    status[sequence] = "shadowed-by-newer"
        return status

    def search(
        self,
        session_id: str,
        query: str,
        *,
        limit: int = 12,
        include_superseded: bool = False,
        half_life_days: float = 30.0,
    ) -> list[SemanticEventHit]:
        clean_query = query.strip()
        if not clean_query:
            return []
        events = list(self.runtime.events(session_id, limit=10_000_000))
        if not events:
            return []
        statuses = self._statuses(events)
        query_tokens = _tokens(clean_query)
        query_expanded = _expanded_tokens(clean_query)
        query_trigrams = _trigrams(clean_query)
        now = time.time()
        hits: list[SemanticEventHit] = []

        for event in events:
            sequence = int(event.sequence)
            temporal = statuses.get(sequence, "current")
            if temporal != "current" and not include_superseded:
                continue
            payload = dict(getattr(event, "payload", {}) or {})
            event_type = str(getattr(event, "event_type", ""))
            body = _payload_text(payload)
            haystack = f"{event_type}\n{body}"
            tokens = _tokens(haystack)
            expanded = _expanded_tokens(haystack)
            direct_overlap = query_tokens & tokens
            semantic_overlap = query_expanded & expanded
            reasons: list[str] = []
            score = 0.0
            if direct_overlap:
                score += 4.0 * len(direct_overlap)
                reasons.append("direct-token-overlap")
            alias_only = semantic_overlap - direct_overlap
            if alias_only:
                score += 1.75 * min(8, len(alias_only))
                reasons.append("semantic-alias-overlap")
            if _normalize(clean_query) in _normalize(haystack):
                score += 8.0
                reasons.append("exact-phrase")
            candidate_trigrams = _trigrams(haystack[:12000])
            if query_trigrams and candidate_trigrams:
                similarity = len(query_trigrams & candidate_trigrams) / max(1, len(query_trigrams | candidate_trigrams))
                if similarity >= 0.04:
                    score += min(4.0, similarity * 12.0)
                    reasons.append("character-semantic-similarity")
            event_type_folded = event_type.casefold()
            if any(marker in event_type_folded for marker in ("decision", "error", "failure", "result", "checkpoint")):
                score += 1.5
                reasons.append("high-value-event-type")
            if temporal == "current":
                score += 1.0
                reasons.append("current-temporal-truth")
            created_at = float(getattr(event, "created_at", now))
            age_days = max(0.0, (now - created_at) / 86400.0)
            recency = math.exp(-age_days / max(0.1, half_life_days))
            score += recency * 1.5
            if score <= 1.6:
                continue
            hits.append(SemanticEventHit(
                session_id=session_id,
                sequence=sequence,
                event_type=event_type,
                score=score,
                temporal_status=temporal,
                subject=_event_subject(event_type, payload),
                text=body[:8000],
                payload=payload,
                reasons=tuple(dict.fromkeys(reasons)),
            ))

        hits.sort(key=lambda hit: (-hit.score, -hit.sequence, hit.event_type))
        return hits[:max(1, limit)]

    def context_pack(
        self,
        session_id: str,
        query: str,
        *,
        budget_bytes: int = 8192,
        limit: int = 32,
        include_superseded: bool = False,
    ) -> SemanticContextPack:
        if budget_bytes < 256:
            raise ValueError("semantic context budget too small")
        hits = self.search(session_id, query, limit=limit, include_superseded=include_superseded)
        sections: list[str] = []
        selected: list[SemanticEventHit] = []
        used = 0
        complete = True
        for hit in hits:
            section = (
                f"[session={hit.session_id} sequence={hit.sequence} type={hit.event_type} "
                f"status={hit.temporal_status} subject={hit.subject} score={hit.score:.3f}]\n{hit.text}"
            )
            encoded = section.encode("utf-8")
            separator = b"\n---\n" if sections else b""
            if used + len(separator) + len(encoded) > budget_bytes:
                complete = False
                break
            sections.append(section)
            selected.append(hit)
            used += len(separator) + len(encoded)
        text = "\n---\n".join(sections)
        return SemanticContextPack(session_id, query, text, len(text.encode("utf-8")), tuple(selected), complete)

    @staticmethod
    def serializable(hits: Iterable[SemanticEventHit]) -> list[dict[str, Any]]:
        return [asdict(hit) for hit in hits]
