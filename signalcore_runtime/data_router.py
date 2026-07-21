from __future__ import annotations

import json
import math
import random
import re
from dataclasses import asdict, dataclass
from statistics import fmean
from typing import Any, Iterable, Mapping, Sequence

from .util import sha256_bytes


_PRIORITY_KEYS = (
    "error", "errors", "status", "message", "path", "source", "url", "id",
    "name", "score", "distance", "rank", "count", "total", "next", "page",
)
_TEXT_KEYS = ("text", "content", "document", "body", "snippet", "summary")
_TOKEN_RE = re.compile(r"[^\W_]+|[A-Za-z0-9_./:-]+", re.UNICODE)


@dataclass(frozen=True)
class DataRoutePolicy:
    budget_bytes: int = 8192
    max_rows: int = 8
    max_columns: int = 12
    max_depth: int = 5
    max_list_items: int = 12
    max_string_chars: int = 640
    reservoir_size: int = 128
    distinct_registers: int = 64

    def __post_init__(self) -> None:
        if self.budget_bytes < 512:
            raise ValueError("budget_bytes must be at least 512")
        if min(
            self.max_rows, self.max_columns, self.max_depth, self.max_list_items,
            self.max_string_chars, self.reservoir_size, self.distinct_registers,
        ) < 1:
            raise ValueError("data route limits must be positive")
        if self.distinct_registers & (self.distinct_registers - 1):
            raise ValueError("distinct_registers must be a power of two")


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
    continuation: str = ""


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _tokens(value: str) -> set[str]:
    return {item.casefold() for item in _TOKEN_RE.findall(value) if len(item) > 1}


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


class _HLL:
    def __init__(self, registers: int = 64):
        self.m = registers
        self.bits = int(math.log2(registers))
        self.values = [0] * registers

    def add(self, value: Any) -> None:
        digest = int.from_bytes(__import__("hashlib").sha256(str(value).encode("utf-8", errors="replace")).digest()[:8], "big")
        index = digest & (self.m - 1)
        remainder = digest >> self.bits
        rank = 1
        while rank <= 64 - self.bits and remainder & 1 == 0:
            rank += 1
            remainder >>= 1
        self.values[index] = max(self.values[index], rank)

    def estimate(self) -> int:
        alpha = 0.709 if self.m == 64 else 0.7213 / (1 + 1.079 / self.m)
        raw = alpha * self.m * self.m / sum(2.0 ** (-value) for value in self.values)
        zeros = self.values.count(0)
        if zeros and raw <= 2.5 * self.m:
            raw = self.m * math.log(self.m / zeros)
        return max(0, int(round(raw)))


class _ColumnSketch:
    def __init__(self, registers: int):
        self.count = 0
        self.nulls = 0
        self.numeric_count = 0
        self.minimum: float | None = None
        self.maximum: float | None = None
        self.mean = 0.0
        self.m2 = 0.0
        self.distinct = _HLL(registers)
        self.top: dict[str, int] = {}

    def add(self, value: Any) -> None:
        self.count += 1
        if value is None:
            self.nulls += 1
            return
        self.distinct.add(value)
        text = str(value)
        if len(text) <= 128:
            self.top[text] = self.top.get(text, 0) + 1
            if len(self.top) > 128:
                for key in list(self.top):
                    self.top[key] -= 1
                    if self.top[key] <= 0:
                        del self.top[key]
        number = _finite_number(value)
        if number is not None:
            self.numeric_count += 1
            self.minimum = number if self.minimum is None else min(self.minimum, number)
            self.maximum = number if self.maximum is None else max(self.maximum, number)
            delta = number - self.mean
            self.mean += delta / self.numeric_count
            self.m2 += delta * (number - self.mean)

    def summary(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "count": self.count,
            "nulls": self.nulls,
            "null_ratio": self.nulls / max(1, self.count),
            "approx_distinct": self.distinct.estimate(),
        }
        if self.numeric_count:
            result["numeric"] = {
                "count": self.numeric_count,
                "min": self.minimum,
                "max": self.maximum,
                "mean": self.mean,
                "stddev": math.sqrt(self.m2 / max(1, self.numeric_count - 1)),
            }
        if self.top:
            result["top_values"] = sorted(self.top.items(), key=lambda item: (-item[1], item[0]))[:5]
        return result


class DataRouter:
    """Exact-preserving semantic router with an always-valid typed JSON envelope."""

    schema_version = 2

    def __init__(self, evidence: Any | None = None):
        self.evidence = evidence

    @staticmethod
    def detect(payload: Any, hint: str = "") -> str:
        normalized = hint.strip().casefold()
        if normalized in {"sql", "table", "dataframe", "csv", "arrow", "parquet"}:
            return "table"
        if normalized in {"rag", "vector", "search", "retrieval"}:
            return "rag"
        if normalized in {"graphql", "gql"}:
            return "graphql"
        if normalized in {"image", "audio", "video", "binary", "pdf", "archive"}:
            return "binary"
        if isinstance(payload, (bytes, bytearray, memoryview)):
            return "binary"
        if isinstance(payload, Mapping):
            keys = {str(key).casefold() for key in payload}
            data = payload.get("data")
            if "data" in keys and isinstance(data, Mapping):
                nested = {str(key).casefold() for key in data}
                if nested & {"pageinfo", "edges", "nodes"} or keys & {"errors", "extensions"}:
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

    @staticmethod
    def intent(query: str) -> str:
        tokens = _tokens(query)
        rules = (
            ("anomaly", {"anomaly", "outlier", "abnormal", "hata", "aykırı"}),
            ("trend", {"trend", "over", "time", "zaman", "artış", "azalış"}),
            ("count", {"count", "how", "many", "kaç", "sayı"}),
            ("compare", {"compare", "versus", "difference", "kıyasla", "karşılaştır"}),
            ("root-cause", {"root", "cause", "why", "neden", "sebep"}),
            ("lookup", {"find", "record", "id", "bul", "kayıt"}),
        )
        return next((name for name, markers in rules if tokens & markers), "overview")

    def route(
        self,
        payload: Any,
        *,
        hint: str = "",
        query: str = "",
        policy: DataRoutePolicy | None = None,
    ) -> DataRouteResult:
        policy = policy or DataRoutePolicy()
        raw = bytes(payload) if isinstance(payload, (bytes, bytearray, memoryview)) else (
            payload.encode("utf-8") if isinstance(payload, str) else _json_bytes(payload)
        )
        exact_hash = sha256_bytes(raw)
        family = self.detect(payload, hint)
        handle = ""
        if self.evidence is not None:
            handle = str(self.evidence.put(
                raw,
                kind="data-route-source",
                metadata={"family": family, "exact_hash": exact_hash, "query_hash": sha256_bytes(query.encode("utf-8"))},
                reference=f"data-route:{exact_hash}",
            ))

        if family == "table":
            compact, seen, shown = self._table(payload, query, policy)
        elif family == "rag":
            compact, seen, shown = self._rag(payload, query, policy)
        elif family == "binary":
            compact = {
                "media_type": hint or "application/octet-stream",
                "bytes": len(raw),
                "sha256": exact_hash,
                "preview": base64_preview(raw),
            }
            seen = shown = 1
        elif family in {"graphql", "json"}:
            compact = self._bounded(payload, policy, 0)
            seen = self._record_count(payload)
            shown = min(seen, policy.max_list_items)
        else:
            text = raw.decode("utf-8", errors="replace")
            compact = {"preview": text[: policy.max_string_chars], "characters": len(text)}
            seen = shown = 1

        visible, limitations, truncated = self._fit_envelope(
            family=family,
            data=compact,
            exact_hash=exact_hash,
            exact_handle=handle,
            query=query,
            policy=policy,
            records_seen=seen,
            records_visible=shown,
        )
        visible_bytes = len(visible.encode("utf-8"))
        continuation = f"sc-cont://sha256/{exact_hash}" if truncated or shown < seen else ""
        return DataRouteResult(
            family=family,
            route=f"typed-{family}",
            exact_hash=exact_hash,
            exact_handle=handle,
            original_bytes=len(raw),
            visible_bytes=visible_bytes,
            reduction_ratio=max(0.0, 1.0 - visible_bytes / max(1, len(raw))),
            records_seen=seen,
            records_visible=shown,
            visible=visible,
            limitations=limitations,
            continuation=continuation,
        )

    def route_rows(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        query: str = "",
        policy: DataRoutePolicy | None = None,
    ) -> DataRouteResult:
        policy = policy or DataRoutePolicy()
        reservoir: list[Mapping[str, Any]] = []
        sketches: dict[str, _ColumnSketch] = {}
        count = 0
        digest = __import__("hashlib").sha256()
        exact_chunks: list[bytes] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise TypeError("streaming table rows must be mappings")
            encoded = _json_bytes(row) + b"\n"
            digest.update(encoded)
            exact_chunks.append(encoded)
            count += 1
            for key, value in row.items():
                sketches.setdefault(str(key), _ColumnSketch(policy.distinct_registers)).add(value)
            if len(reservoir) < policy.reservoir_size:
                reservoir.append(dict(row))
            else:
                index = random.randrange(count)
                if index < policy.reservoir_size:
                    reservoir[index] = dict(row)
        raw = b"".join(exact_chunks)
        handle = ""
        if self.evidence is not None:
            handle = str(self.evidence.put_stream(exact_chunks, kind="data-route-row-stream", reference=f"data-route-stream:{digest.hexdigest()}"))
        query_tokens = _tokens(query)
        columns = sorted(sketches, key=lambda name: (name.casefold() not in _PRIORITY_KEYS, not bool(_tokens(name) & query_tokens), name))
        compact = {
            "family": "table",
            "intent": self.intent(query),
            "row_count": count,
            "streaming": True,
            "columns": {name: sketches[name].summary() for name in columns[: policy.max_columns]},
            "sample_rows": [self._bounded(row, policy, 1) for row in reservoir[: policy.max_rows]],
        }
        exact_hash = digest.hexdigest()
        visible, limitations, truncated = self._fit_envelope(
            family="table", data=compact, exact_hash=exact_hash, exact_handle=handle,
            query=query, policy=policy, records_seen=count, records_visible=min(count, policy.max_rows),
        )
        visible_bytes = len(visible.encode("utf-8"))
        return DataRouteResult(
            "table", "streaming-table", exact_hash, handle, len(raw), visible_bytes,
            max(0.0, 1.0 - visible_bytes / max(1, len(raw))), count, min(count, policy.max_rows),
            visible, limitations, f"sc-cont://sha256/{exact_hash}" if truncated or count > policy.max_rows else "",
        )

    @staticmethod
    def _record_count(payload: Any) -> int:
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
        sketches: dict[str, _ColumnSketch] = {}
        for row in rows:
            for key, value in row.items():
                name = str(key)
                if name not in columns:
                    columns.append(name)
                sketches.setdefault(name, _ColumnSketch(policy.distinct_registers)).add(value)
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

        selected = sorted(enumerate(rows), key=lambda pair: row_score(pair[1], pair[0]), reverse=True)[: policy.max_rows]
        selected.sort(key=lambda pair: pair[0])
        samples = [{key: self._scalar(row.get(key), policy.max_string_chars) for key in prioritized if key in row} for _, row in selected]
        compact = {
            "family": "table",
            "intent": self.intent(query),
            "row_count": len(rows),
            "columns": columns,
            "selected_columns": prioritized,
            "column_profiles": {name: sketches[name].summary() for name in prioritized},
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

        for item in items:
            text = next((str(item.get(key)) for key in _TEXT_KEYS if item.get(key) not in (None, "")), "")
            identity = str(item.get("id") or item.get("source") or item.get("url") or sha256_bytes(text.encode("utf-8")))
            current = unique.get(identity)
            if current is None or score(item) > score(current):
                unique[identity] = item
        ranked = sorted(unique.values(), key=score, reverse=True)[: policy.max_rows]
        compact_rows: list[dict[str, Any]] = []
        for item in ranked:
            text = next((str(item.get(key)) for key in _TEXT_KEYS if item.get(key) not in (None, "")), "")
            row = {key: self._scalar(item[key], policy.max_string_chars) for key in ("id", "source", "path", "url", "title", "score", "distance", "rank") if key in item}
            row["snippet"] = text[: policy.max_string_chars]
            row["citation"] = {
                "source": str(item.get("source") or item.get("path") or item.get("url") or ""),
                "start": item.get("start") or item.get("line_start"),
                "end": item.get("end") or item.get("line_end"),
            }
            compact_rows.append(row)
        return {
            "family": "rag",
            "intent": self.intent(query),
            "result_count": len(items),
            "unique_count": len(unique),
            "results": compact_rows,
        }, len(items), len(compact_rows)

    def _bounded(self, value: Any, policy: DataRoutePolicy, depth: int) -> Any:
        if depth >= policy.max_depth:
            return {"bounded": True, "reason": "max-depth"}
        if isinstance(value, Mapping):
            keys = sorted(value, key=lambda key: (str(key).casefold() not in _PRIORITY_KEYS, str(key)))
            return {str(key): self._bounded(value[key], policy, depth + 1) for key in keys[: policy.max_columns]}
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

    @staticmethod
    def _encode(envelope: Mapping[str, Any]) -> str:
        return json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _fit_envelope(
        self,
        *,
        family: str,
        data: Any,
        exact_hash: str,
        exact_handle: str,
        query: str,
        policy: DataRoutePolicy,
        records_seen: int,
        records_visible: int,
    ) -> tuple[str, tuple[str, ...], bool]:
        limitations: list[str] = []
        working = json.loads(json.dumps(data, ensure_ascii=False, default=str))

        def envelope(payload: Any, *, truncated: bool) -> dict[str, Any]:
            meta = {
                "schema_version": self.schema_version,
                "family": family,
                "intent": self.intent(query),
                "truncated": truncated,
                "exact": {"sha256": exact_hash, "handle": exact_handle},
                "records": {"seen": records_seen, "visible": records_visible},
                "continuation": f"sc-cont://sha256/{exact_hash}" if truncated or records_visible < records_seen else "",
                "limitations": list(dict.fromkeys(limitations)),
            }
            value: dict[str, Any] = {"_signalcore": meta, "data": payload}
            # V5 compatibility: structured fields remain directly addressable.
            if isinstance(payload, Mapping):
                for key, item in payload.items():
                    if key not in value and key != "_signalcore":
                        value[key] = item
            return value

        text = self._encode(envelope(working, truncated=False))
        if len(text.encode("utf-8")) <= policy.budget_bytes:
            return text, (), False

        for key in ("sample_rows", "results", "items", "nodes", "edges", "columns", "selected_columns"):
            if isinstance(working, dict) and isinstance(working.get(key), list):
                while working[key]:
                    working[key].pop()
                    limitations.append(f"bounded-{key}")
                    text = self._encode(envelope(working, truncated=True))
                    if len(text.encode("utf-8")) <= policy.budget_bytes:
                        return text, tuple(dict.fromkeys(limitations)), True

        current_policy = policy
        for _ in range(8):
            current_policy = DataRoutePolicy(
                budget_bytes=policy.budget_bytes,
                max_rows=max(1, current_policy.max_rows // 2),
                max_columns=max(1, current_policy.max_columns // 2),
                max_depth=max(1, current_policy.max_depth - 1),
                max_list_items=max(1, current_policy.max_list_items // 2),
                max_string_chars=max(16, current_policy.max_string_chars // 2),
                reservoir_size=max(1, current_policy.reservoir_size // 2),
                distinct_registers=current_policy.distinct_registers,
            )
            working = self._bounded(working, current_policy, 0)
            limitations.append("bounded-nested-content")
            text = self._encode(envelope(working, truncated=True))
            if len(text.encode("utf-8")) <= policy.budget_bytes:
                return text, tuple(dict.fromkeys(limitations)), True

        limitations.append("data-omitted-for-budget")
        minimal = {
            "_signalcore": {
                "schema_version": self.schema_version,
                "family": family,
                "truncated": True,
                "exact": {"sha256": exact_hash, "handle": exact_handle},
                "continuation": f"sc-cont://sha256/{exact_hash}",
                "limitations": list(dict.fromkeys(limitations)),
            },
            "data": None,
        }
        text = self._encode(minimal)
        if len(text.encode("utf-8")) > policy.budget_bytes:
            minimal = {
                "_signalcore": {"schema_version": self.schema_version, "truncated": True, "sha256": exact_hash},
                "data": None,
            }
            text = self._encode(minimal)
        if len(text.encode("utf-8")) > policy.budget_bytes:
            raise ValueError("budget is too small for mandatory exact-reference envelope")
        return text, tuple(dict.fromkeys(limitations)), True


def base64_preview(data: bytes, limit: int = 96) -> str:
    import base64
    return base64.b64encode(data[:limit]).decode("ascii")


def result_dict(result: DataRouteResult) -> dict[str, Any]:
    return asdict(result)
