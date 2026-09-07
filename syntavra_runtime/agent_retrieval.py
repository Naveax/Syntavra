from __future__ import annotations

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


class QueryPushdownEngine:
    """Strict local projection/filtering for coding-agent repository retrieval.

    The graph remains the canonical retrieval owner. This engine only constrains the
    model-visible projection and performs deterministic selection before provider
    admission. Unknown requested fields/filters fail closed instead of accidentally
    serializing arbitrary graph metadata.
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
    def _matches(row: Mapping[str, Any], filters: Mapping[str, Any] | None) -> bool:
        if not filters:
            return True
        unknown = [str(key) for key in filters if str(key) not in _FILTER_FIELDS and str(key) != "path_prefix"]
        if unknown:
            raise ValueError(f"unsupported repository filters: {unknown}")
        for key, expected in filters.items():
            name = str(key)
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
        # Fetch only a bounded candidate window. Filtering then shrinks it further;
        # this is intentionally not an unbounded local scan disguised as pushdown.
        fetch_limit = min(self.max_limit, max(bounded_limit, bounded_limit * 2))
        raw = list(self.graph.query(normalized, limit=fetch_limit))
        filtered = [row for row in raw if isinstance(row, Mapping) and self._matches(row, filters)]
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
        selection_fields = tuple(dict.fromkeys((*self._fields(fields), "path", "start_line", "end_line")))
        result = self.search(query, limit=limit, fields=selection_fields, filters=filters)
        identities = {self._identity(row) for row in result.rows if str(row.get("path") or "")}
        if len(identities) != 1:
            public_fields = self._fields(fields)
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


__all__ = ["FusedRetrievalResult", "QueryPushdownEngine", "RetrievalResult"]
