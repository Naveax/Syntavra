from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


_ALLOWED_FIELDS = (
    "node_id",
    "name",
    "qualified_name",
    "path",
    "kind",
    "language",
    "start_line",
    "end_line",
    "score",
    "query_backend",
)
_FILTER_FIELDS = {"path", "kind", "language", "name", "qualified_name"}
_ALLOWED_FILTER_FIELDS = frozenset((*_FILTER_FIELDS, "path_prefix"))
_NUMERIC_FIELDS = frozenset({"start_line", "end_line", "score"})
_GROUP_FIELDS = frozenset({"name", "qualified_name", "path", "kind", "language", "query_backend"})
_REDUCTION_OPERATORS = frozenset({"count", "sum", "min", "max", "group", "sort", "top_k", "sample"})


@dataclass(frozen=True)
class RetrievalResult:
    query: str
    rows: tuple[dict[str, Any], ...]
    raw_count: int
    filtered_count: int
    limit: int
    fields: tuple[str, ...]


@dataclass(frozen=True)
class FusedRetrievalResult:
    query: str
    status: str
    source: dict[str, Any] | None
    candidates: tuple[dict[str, Any], ...]
    raw_count: int
    selected_identity: str
    intermediate_rows_visible: bool


@dataclass(frozen=True)
class ReducedRetrievalResult:
    query: str
    operator: str
    value: Any
    raw_count: int
    filtered_count: int
    source_window_complete: bool
    source_window_limit: int
    fields: tuple[str, ...]
    field: str
    group_by: str
    exact_artifact_id: str
    exact_handle: str


class QueryPushdownEngine:
    """Strict local projection/filtering/reduction for coding-agent retrieval.

    The graph remains the canonical retrieval owner. Provider-controlled fields,
    filters and reduction operators are allow-listed before graph access. Lossy
    reductions additionally require an exact local capture of the filtered raw
    candidate window before any projection/aggregation is returned.
    """

    def __init__(self, graph: Any, *, max_limit: int = 20, default_limit: int = 8):
        self.graph = graph
        self.max_limit = max(1, min(int(max_limit), 64))
        self.default_limit = max(1, min(int(default_limit), self.max_limit))

    @staticmethod
    def _fields(requested: Sequence[str] | None) -> tuple[str, ...]:
        if not requested:
            return _ALLOWED_FIELDS
        fields = tuple(dict.fromkeys(str(item) for item in requested))
        unknown = [item for item in fields if item not in _ALLOWED_FIELDS]
        if unknown:
            raise ValueError(f"unsupported repository projection fields: {unknown}")
        return fields

    @staticmethod
    def _filters(filters: Mapping[str, Any] | None) -> dict[str, Any]:
        if not filters:
            return {}
        normalized = {str(key): value for key, value in filters.items()}
        unknown = [key for key in normalized if key not in _ALLOWED_FILTER_FIELDS]
        if unknown:
            raise ValueError(f"unsupported repository filters: {unknown}")
        return normalized

    @staticmethod
    def _matches(row: Mapping[str, Any], filters: Mapping[str, Any]) -> bool:
        if not filters:
            return True
        for name, expected in filters.items():
            if name == "path_prefix":
                if not str(row.get("path") or "").startswith(str(expected)):
                    return False
                continue
            actual = row.get(name)
            if isinstance(expected, Sequence) and not isinstance(expected, (str, bytes, bytearray)):
                if str(actual) not in {str(item) for item in expected}:
                    return False
            elif str(actual) != str(expected):
                return False
        return True

    @staticmethod
    def _canonical(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)

    @staticmethod
    def _sortable(value: Any) -> tuple[int, Any]:
        if isinstance(value, bool):
            return 2, str(value)
        if isinstance(value, (int, float)):
            return 0, float(value)
        if value is None:
            return 3, ""
        return 1, str(value).casefold()

    @staticmethod
    def _numeric_values(rows: Sequence[Mapping[str, Any]], field: str) -> list[float]:
        values: list[float] = []
        for row in rows:
            value = row.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"repository reduction field is not uniformly numeric: {field}")
            values.append(float(value))
        return values

    def _fetch_filtered(
        self,
        query: str,
        *,
        filters: Mapping[str, Any],
        fetch_limit: int,
    ) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
        raw = list(self.graph.query(query, limit=fetch_limit))
        mapped = [row for row in raw if isinstance(row, Mapping)]
        filtered = [row for row in mapped if self._matches(row, filters)]
        return mapped, filtered

    def search(
        self,
        query: str,
        *,
        limit: int | None = None,
        fields: Sequence[str] | None = None,
        filters: Mapping[str, Any] | None = None,
    ) -> RetrievalResult:
        normalized = str(query).strip()
        if not normalized:
            raise ValueError("repository query cannot be empty")
        bounded_limit = max(1, min(int(limit or self.default_limit), self.max_limit))
        selected_fields = self._fields(fields)
        selected_filters = self._filters(filters)
        # Fetch only after all provider-controlled projection/filter material has
        # passed the allow-list. This keeps fail-closed behavior independent of the
        # number of graph rows returned.
        fetch_limit = min(self.max_limit, max(bounded_limit, bounded_limit * 2))
        raw, filtered = self._fetch_filtered(normalized, filters=selected_filters, fetch_limit=fetch_limit)
        projected: list[dict[str, Any]] = []
        for row in filtered[:bounded_limit]:
            visible = {field: row[field] for field in selected_fields if field in row}
            if visible:
                projected.append(visible)
        return RetrievalResult(
            query=normalized,
            rows=tuple(projected),
            raw_count=len(raw),
            filtered_count=len(filtered),
            limit=bounded_limit,
            fields=selected_fields,
        )

    def reduce(
        self,
        query: str,
        *,
        operator: str,
        capture: Callable[[bytes, Mapping[str, Any]], Mapping[str, str]],
        limit: int | None = None,
        fields: Sequence[str] | None = None,
        filters: Mapping[str, Any] | None = None,
        field: str = "",
        group_by: str = "",
        descending: bool | None = None,
        sample_seed: str = "",
    ) -> ReducedRetrievalResult:
        normalized = str(query).strip()
        if not normalized:
            raise ValueError("repository query cannot be empty")
        op = str(operator or "").casefold()
        if op not in _REDUCTION_OPERATORS:
            raise ValueError(f"unsupported repository reduction operator: {operator}")
        if not callable(capture):
            raise ValueError("lossy repository reduction requires exact raw capture")

        selected_fields = self._fields(fields)
        selected_filters = self._filters(filters)
        selected_field = str(field or "")
        selected_group = str(group_by or "")
        if op in {"sum", "min", "max"}:
            if selected_field not in _NUMERIC_FIELDS:
                raise ValueError(f"unsupported numeric repository reduction field: {selected_field}")
        if op in {"sort", "top_k"}:
            if selected_field not in selected_fields:
                raise ValueError("repository sort/top-k field must be provider-visible and allow-listed")
        if op == "group" and selected_group not in _GROUP_FIELDS:
            raise ValueError(f"unsupported repository group field: {selected_group}")

        bounded_limit = max(1, min(int(limit or self.default_limit), self.max_limit))
        fetch_limit = self.max_limit
        raw, filtered = self._fetch_filtered(normalized, filters=selected_filters, fetch_limit=fetch_limit)
        source_window_complete = len(raw) < fetch_limit
        capture_payload = self._canonical(
            {
                "query": normalized,
                "filters": selected_filters,
                "source_window_limit": fetch_limit,
                "rows": [dict(row) for row in filtered],
            }
        ).encode("utf-8")
        capture_receipt = capture(
            capture_payload,
            {
                "query": normalized,
                "operator": op,
                "filters": selected_filters,
                "source_window_limit": fetch_limit,
                "source_window_complete": source_window_complete,
            },
        )
        artifact_id = str(capture_receipt.get("artifact_id") or "")
        exact_handle = str(capture_receipt.get("exact_handle") or "")
        if not artifact_id or not exact_handle:
            raise RuntimeError("exact repository reduction capture did not return artifact + handle")

        projected = [
            {name: row[name] for name in selected_fields if name in row}
            for row in filtered
        ]

        value: Any
        if op == "count":
            value = {"count": len(filtered)}
        elif op in {"sum", "min", "max"}:
            numeric = self._numeric_values(filtered, selected_field) if filtered else []
            if op == "sum":
                aggregate: float | None = sum(numeric)
            elif op == "min":
                aggregate = min(numeric) if numeric else None
            else:
                aggregate = max(numeric) if numeric else None
            value = {"field": selected_field, "value": aggregate}
        elif op == "group":
            counts: dict[str, int] = {}
            for row in filtered:
                key = str(row.get(selected_group) or "")
                counts[key] = counts.get(key, 0) + 1
            groups = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:bounded_limit]
            value = tuple({"value": key, "count": count} for key, count in groups)
        elif op in {"sort", "top_k"}:
            reverse = bool(descending) if descending is not None else op == "top_k"
            ranked = sorted(projected, key=lambda row: self._sortable(row.get(selected_field)), reverse=reverse)
            value = tuple(ranked[:bounded_limit])
        else:
            seed = str(sample_seed or "syntavra-deterministic-sample-v1")
            ranked = sorted(
                projected,
                key=lambda row: hashlib.sha256(
                    (seed + "\x00" + self._canonical(row)).encode("utf-8")
                ).hexdigest(),
            )
            value = tuple(ranked[:bounded_limit])

        return ReducedRetrievalResult(
            query=normalized,
            operator=op,
            value=value,
            raw_count=len(raw),
            filtered_count=len(filtered),
            source_window_complete=source_window_complete,
            source_window_limit=fetch_limit,
            fields=selected_fields,
            field=selected_field,
            group_by=selected_group,
            exact_artifact_id=artifact_id,
            exact_handle=exact_handle,
        )

    @staticmethod
    def _identity(row: Mapping[str, Any]) -> tuple[str, int | None, int | None]:
        path = str(row.get("path") or "")
        start = int(row["start_line"]) if row.get("start_line") is not None else None
        end = int(row["end_line"]) if row.get("end_line") is not None else None
        return path, start, end

    def search_inspect(
        self,
        query: str,
        *,
        reader: Callable[..., dict[str, Any]],
        limit: int | None = None,
        fields: Sequence[str] | None = None,
        filters: Mapping[str, Any] | None = None,
        context_lines: int = 8,
        max_bytes: int = 12_000,
    ) -> FusedRetrievalResult:
        # Selection requires path/range even if the caller asked for a smaller public
        # projection. The intermediate rows remain local and are never emitted on a
        # successful fusion.
        public_fields = self._fields(fields)
        selected_filters = self._filters(filters)
        selection_fields = tuple(dict.fromkeys((*public_fields, "path", "start_line", "end_line")))
        result = self.search(query, limit=limit, fields=selection_fields, filters=selected_filters)
        identities = {self._identity(row) for row in result.rows if str(row.get("path") or "")}
        if len(identities) != 1:
            candidates = tuple(
                {field: row[field] for field in public_fields if field in row}
                for row in result.rows
            )
            return FusedRetrievalResult(
                query=result.query,
                status="AMBIGUOUS" if identities else "NO_MATCH",
                source=None,
                candidates=candidates,
                raw_count=result.raw_count,
                selected_identity="",
                intermediate_rows_visible=True,
            )

        path, start, end = next(iter(identities))
        margin = max(0, min(int(context_lines), 32))
        read_start = max(1, int(start or 1) - margin)
        read_end = max(read_start, int(end or read_start) + margin)
        source = reader(
            path,
            start_line=read_start,
            end_line=read_end,
            max_bytes=max(1024, min(int(max_bytes), 32_000)),
        )
        identity = f"{path}:{source.get('start_line', read_start)}:{source.get('end_line', read_end)}"
        return FusedRetrievalResult(
            query=result.query,
            status="FUSED_EXACT",
            source=source,
            candidates=(),
            raw_count=result.raw_count,
            selected_identity=identity,
            intermediate_rows_visible=False,
        )


__all__ = [
    "FusedRetrievalResult",
    "QueryPushdownEngine",
    "ReducedRetrievalResult",
    "RetrievalResult",
]
