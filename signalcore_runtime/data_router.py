from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from statistics import fmean
from typing import Any, Mapping, Sequence

from .util import sha256_bytes


_PRIORITY_KEYS = (
    "error", "errors", "status", "message", "path", "source", "url", "id",
    "name", "score", "distance", "rank", "count", "total", "next", "page",
)
_TEXT_KEYS = ("text", "content", "document", "body", "snippet", "summary")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:-]+")


@dataclass(frozen=True)
class DataRoutePolicy:
    budget_bytes: int = 8192
    max_rows: int = 8
    max_columns: int = 12
    max_depth: int = 5
    max_list_items: int = 12
    max_string_chars: int = 640

    def __post_init__(self) -> None:
        if self.budget_bytes < 512:
            raise ValueError("budget_bytes must be at least 512")
        if min(self.max_rows, self.max_columns, self.max_depth, self.max_list_items, self.max_string_chars) < 1:
            raise ValueError("data route limits must be positive")


@dataclass(frozen=True)
class DataRouteResult:
    family: str
    route: str
    exact_hash: str
    exact_handle: str
    original_bytes: int
    visible_bytes: int
    reduction_ratio: float
    records_seen: int
    records_visible: int
    visible: str
    limitations: tuple[str, ...]


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _tokens(value: str) -> set[str]:
    return {item.casefold() for item in _TOKEN_RE.findall(value) if len(item) > 1}


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


class DataRouter:
    """Exact-preserving result router for tables, RAG results, GraphQL and JSON.

    The raw payload is never mutated. When an evidence store is supplied, raw bytes are
    stored before a bounded model-visible representation is created.
    """

    def __init__(self, evidence: Any | None = None):
        self.evidence = evidence

    @staticmethod
    def detect(payload: Any, hint: str = "") -> str:
        normalized = hint.strip().casefold()
        if normalized in {"sql", "table", "dataframe", "csv"}:
            return "table"
        if normalized in {"rag", "vector", "search", "retrieval"}:
            return "rag"
        if normalized in {"graphql", "gql"}:
            return "graphql"
        if isinstance(payload, Mapping):
            keys = {str(key).casefold() for key in payload}
            if "data" in keys and any(key in keys for key in {"pageinfo", "edges", "nodes"}):
                return "graphql"
            candidate = payload.get("results") or payload.get("matches") or payload.get("hits") or payload.get("documents")
            if isinstance(candidate, Sequence) and not isinstance(candidate, (str, bytes, bytearray)):
                if any(isinstance(item, Mapping) and any(key in item for key in ("score", "distance", "document", "content", "text")) for item in candidate[:8]):
                    return "rag"
            rows = payload.get("rows") or payload.get("records") or payload.get("items")
            if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes, bytearray)) and rows and all(isinstance(item, Mapping) for item in rows[:8]):
                return "table"
            return "json"
        if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
            if payload and all(isinstance(item, Mapping) for item in payload[:8]):
                return "table"
            return "json"
        return "text"

    def route(
        self,
        payload: Any,
        *,
        hint: str = "",
        query: str = "",
        policy: DataRoutePolicy | None = None,
    ) -> DataRouteResult:
        policy = policy or DataRoutePolicy()
        raw = payload if isinstance(payload, bytes) else (
            payload.encode("utf-8") if isinstance(payload, str) else _json_bytes(payload)
        )
        exact_hash = sha256_bytes(raw)
        family = self.detect(payload, hint)
        handle = ""
        if self.evidence is not None:
            handle = str(self.evidence.put(
                raw,
                kind="data-route-source",
                metadata={"family": family, "exact_hash": exact_hash, "query": query[:256]},
            ))
        if len(raw) <= policy.budget_bytes:
            visible = raw.decode("utf-8", errors="replace")
            return DataRouteResult(
                family, "passthrough", exact_hash, handle, len(raw), len(raw), 0.0,
                self._record_count(payload, family), self._record_count(payload, family), visible, (),
            )

        if family == "table":
            compact, seen, shown = self._table(payload, query, policy)
        elif family == "rag":
            compact, seen, shown = self._rag(payload, query, policy)
        elif family == "graphql":
            compact = self._bounded(payload, policy, 0)
            seen = self._record_count(payload, family)
            shown = min(seen, policy.max_list_items)
        elif family == "json":
            compact = self._bounded(payload, policy, 0)
            seen = self._record_count(payload, family)
            shown = min(seen, policy.max_list_items)
        else:
            text = raw.decode("utf-8", errors="replace")
            compact = {"preview": text[: policy.max_string_chars], "characters": len(text)}
            seen = 1
            shown = 1

        visible, limitations = self._fit(compact, policy)
        visible_bytes = len(visible.encode("utf-8"))
        return DataRouteResult(
            family=family,
            route=f"compact-{family}",
            exact_hash=exact_hash,
            exact_handle=handle,
            original_bytes=len(raw),
            visible_bytes=visible_bytes,
            reduction_ratio=max(0.0, 1.0 - (visible_bytes / max(1, len(raw)))),
            records_seen=seen,
            records_visible=shown,
            visible=visible,
            limitations=limitations,
        )

    @staticmethod
    def _record_count(payload: Any, family: str) -> int:
        if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
            return len(payload)
        if isinstance(payload, Mapping):
            for key in ("rows", "records", "items", "results", "matches", "hits", "documents", "nodes", "edges"):
                value = payload.get(key)
                if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                    return len(value)
        return 1

    @staticmethod
    def _rows(payload: Any) -> list[Mapping[str, Any]]:
        if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
            return [item for item in payload if isinstance(item, Mapping)]
        if isinstance(payload, Mapping):
            for key in ("rows", "records", "items"):
                value = payload.get(key)
                if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                    return [item for item in value if isinstance(item, Mapping)]
        return []

    def _table(self, payload: Any, query: str, policy: DataRoutePolicy) -> tuple[dict[str, Any], int, int]:
        rows = self._rows(payload)
        columns: list[str] = []
        for row in rows[:100]:
            for key in row:
                name = str(key)
                if name not in columns:
                    columns.append(name)
        query_tokens = _tokens(query)
        prioritized = sorted(
            columns,
            key=lambda name: (
                2 if name.casefold() in _PRIORITY_KEYS else 0,
                1 if _tokens(name) & query_tokens else 0,
                -columns.index(name),
            ),
            reverse=True,
        )[: policy.max_columns]

        def row_score(row: Mapping[str, Any], index: int) -> tuple[float, int]:
            text = " ".join(str(row.get(key, "")) for key in prioritized)
            score = float(len(_tokens(text) & query_tokens) * 20)
            if any(str(key).casefold() in {"error", "errors", "warning", "status"} and row.get(key) not in (None, "", "ok", "success") for key in row):
                score += 50
            for key in ("score", "rank"):
                value = _finite_number(row.get(key))
                if value is not None:
                    score += value
            return score, -index

        selected_pairs = sorted(enumerate(rows), key=lambda pair: row_score(pair[1], pair[0]), reverse=True)[: policy.max_rows]
        samples = [{key: self._scalar(row.get(key), policy.max_string_chars) for key in prioritized if key in row} for _, row in selected_pairs]
        numeric: dict[str, dict[str, float]] = {}
        for column in columns:
            values = [number for row in rows if (number := _finite_number(row.get(column))) is not None]
            if values:
                numeric[column] = {
                    "min": min(values),
                    "max": max(values),
                    "mean": fmean(values),
                }
        compact = {
            "family": "table",
            "row_count": len(rows),
            "columns": columns,
            "selected_columns": prioritized,
            "numeric_summary": dict(list(numeric.items())[: policy.max_columns]),
            "sample_rows": samples,
        }
        return compact, len(rows), len(samples)

    @staticmethod
    def _rag_items(payload: Any) -> list[Mapping[str, Any]]:
        if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
            return [item for item in payload if isinstance(item, Mapping)]
        if isinstance(payload, Mapping):
            for key in ("results", "matches", "hits", "documents", "items"):
                value = payload.get(key)
                if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                    return [item for item in value if isinstance(item, Mapping)]
        return []

    def _rag(self, payload: Any, query: str, policy: DataRoutePolicy) -> tuple[dict[str, Any], int, int]:
        items = self._rag_items(payload)
        query_tokens = _tokens(query)
        unique: dict[str, Mapping[str, Any]] = {}
        for item in items:
            text = next((str(item.get(key)) for key in _TEXT_KEYS if item.get(key) not in (None, "")), "")
            identity = str(item.get("id") or item.get("source") or item.get("url") or sha256_bytes(text.encode("utf-8")))
            unique.setdefault(identity, item)

        def score(item: Mapping[str, Any]) -> tuple[float, int]:
            text = " ".join(str(item.get(key, "")) for key in (*_TEXT_KEYS, "source", "path", "title"))
            relevance = float(len(_tokens(text) & query_tokens) * 10)
            direct = _finite_number(item.get("score"))
            distance = _finite_number(item.get("distance"))
            if direct is not None:
                relevance += direct
            if distance is not None:
                relevance -= distance
            return relevance, len(text)

        ranked = sorted(unique.values(), key=score, reverse=True)[: policy.max_rows]
        compact_rows: list[dict[str, Any]] = []
        for item in ranked:
            text = next((str(item.get(key)) for key in _TEXT_KEYS if item.get(key) not in (None, "")), "")
            row = {
                key: self._scalar(item[key], policy.max_string_chars)
                for key in ("id", "source", "path", "url", "title", "score", "distance", "rank")
                if key in item
            }
            row["snippet"] = text[: policy.max_string_chars]
            compact_rows.append(row)
        return {
            "family": "rag",
            "result_count": len(items),
            "unique_count": len(unique),
            "results": compact_rows,
        }, len(items), len(compact_rows)

    def _bounded(self, value: Any, policy: DataRoutePolicy, depth: int) -> Any:
        if depth >= policy.max_depth:
            return "[depth-bounded]"
        if isinstance(value, Mapping):
            keys = sorted(value, key=lambda key: (str(key).casefold() not in _PRIORITY_KEYS, str(key)))
            return {
                str(key): self._bounded(value[key], policy, depth + 1)
                for key in keys[: policy.max_columns]
            }
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return [self._bounded(item, policy, depth + 1) for item in value[: policy.max_list_items]]
        return self._scalar(value, policy.max_string_chars)

    @staticmethod
    def _scalar(value: Any, max_chars: int) -> Any:
        if isinstance(value, str):
            return value if len(value) <= max_chars else value[:max_chars] + "…"
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return str(value)[:max_chars]

    def _fit(self, value: dict[str, Any], policy: DataRoutePolicy) -> tuple[str, tuple[str, ...]]:
        limitations: list[str] = []
        working = json.loads(json.dumps(value, ensure_ascii=False, default=str))
        text = json.dumps(working, ensure_ascii=False, sort_keys=True, indent=2)
        for key in ("sample_rows", "results", "items", "nodes", "edges"):
            while len(text.encode("utf-8")) > policy.budget_bytes and isinstance(working.get(key), list) and len(working[key]) > 1:
                working[key].pop()
                limitations.append(f"bounded-{key}")
                text = json.dumps(working, ensure_ascii=False, sort_keys=True, indent=2)
        if len(text.encode("utf-8")) > policy.budget_bytes:
            working = self._bounded(working, DataRoutePolicy(
                budget_bytes=policy.budget_bytes,
                max_rows=max(1, policy.max_rows // 2),
                max_columns=max(2, policy.max_columns // 2),
                max_depth=max(2, policy.max_depth - 1),
                max_list_items=max(2, policy.max_list_items // 2),
                max_string_chars=max(80, policy.max_string_chars // 2),
            ), 0)
            limitations.append("bounded-nested-content")
            text = json.dumps(working, ensure_ascii=False, sort_keys=True, indent=2)
        if len(text.encode("utf-8")) > policy.budget_bytes:
            suffix = "\n[bounded; exact evidence available]"
            budget = policy.budget_bytes - len(suffix.encode("utf-8"))
            text = text.encode("utf-8")[:max(0, budget)].decode("utf-8", errors="ignore").rstrip() + suffix
            limitations.append("byte-truncated")
        return text, tuple(dict.fromkeys(limitations))


def result_dict(result: DataRouteResult) -> dict[str, Any]:
    return asdict(result)
