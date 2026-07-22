from __future__ import annotations

import math
import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence


_TOKEN_RE = re.compile(r"[^\W_]+|[A-Za-z0-9_./:-]+", re.UNICODE)


def tokenize(text: str) -> tuple[str, ...]:
    return tuple(token.casefold() for token in _TOKEN_RE.findall(text) if len(token) > 1)


@dataclass(frozen=True)
class RetrievalCandidate:
    candidate_id: str
    text: str
    source: str = ""
    timestamp: float = 0.0
    authority: float = 0.5
    graph_score: float = 0.0
    vector: tuple[float, ...] = ()
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class RankedCandidate:
    candidate: RetrievalCandidate
    score: float
    lexical: float
    vector: float
    graph: float
    temporal: float
    authority: float
    diversity_penalty: float
    reasons: tuple[str, ...]


class HybridRetriever:
    """Explainable lexical/vector/graph/temporal retriever with diversity control."""

    def __init__(
        self,
        *,
        embed_query: Callable[[str], Sequence[float]] | None = None,
        lexical_weight: float = 0.35,
        vector_weight: float = 0.30,
        graph_weight: float = 0.15,
        temporal_weight: float = 0.10,
        authority_weight: float = 0.10,
        half_life_seconds: float = 30 * 24 * 60 * 60,
    ):
        weights = (lexical_weight, vector_weight, graph_weight, temporal_weight, authority_weight)
        if any(value < 0 for value in weights) or sum(weights) <= 0:
            raise ValueError("retrieval weights must be non-negative and non-zero")
        total = sum(weights)
        self.weights = tuple(value / total for value in weights)
        self.embed_query = embed_query
        self.half_life_seconds = max(1.0, float(half_life_seconds))

    def rank(
        self,
        query: str,
        candidates: Iterable[RetrievalCandidate],
        *,
        limit: int = 10,
        now: float | None = None,
        source_diversity: float = 0.15,
    ) -> list[RankedCandidate]:
        rows = list(candidates)
        if not rows:
            return []
        query_tokens = tokenize(query)
        query_counts = {token: query_tokens.count(token) for token in set(query_tokens)}
        document_tokens = [tokenize(row.text + " " + row.source) for row in rows]
        document_frequency: dict[str, int] = {}
        for tokens in document_tokens:
            for token in set(tokens):
                document_frequency[token] = document_frequency.get(token, 0) + 1
        query_vector = tuple(float(value) for value in self.embed_query(query)) if self.embed_query else ()
        current = time.time() if now is None else float(now)
        scored: list[RankedCandidate] = []
        for row, tokens in zip(rows, document_tokens, strict=True):
            lexical = self._bm25(query_counts, tokens, document_frequency, len(rows), document_tokens)
            vector = self._cosine(query_vector, row.vector) if query_vector and row.vector else 0.0
            graph = self._unit(row.graph_score)
            temporal = 0.0 if row.timestamp <= 0 else math.exp(-math.log(2) * max(0.0, current - row.timestamp) / self.half_life_seconds)
            authority = self._unit(row.authority)
            wlex, wvec, wgraph, wtime, wauth = self.weights
            score = wlex * lexical + wvec * vector + wgraph * graph + wtime * temporal + wauth * authority
            reasons = []
            if lexical > 0: reasons.append("lexical-match")
            if vector > 0: reasons.append("semantic-match")
            if graph > 0: reasons.append("graph-proximity")
            if temporal > 0.5: reasons.append("recent")
            if authority > 0.7: reasons.append("authoritative")
            scored.append(RankedCandidate(row, score, lexical, vector, graph, temporal, authority, 0.0, tuple(reasons)))
        selected: list[RankedCandidate] = []
        used_sources: dict[str, int] = {}
        remaining = scored[:]
        while remaining and len(selected) < max(1, limit):
            adjusted: list[RankedCandidate] = []
            for item in remaining:
                count = used_sources.get(item.candidate.source, 0) if item.candidate.source else 0
                penalty = source_diversity * count
                adjusted.append(RankedCandidate(
                    item.candidate, item.score - penalty, item.lexical, item.vector, item.graph,
                    item.temporal, item.authority, penalty,
                    item.reasons + (("source-diversity-penalty",) if penalty else ()),
                ))
            best = max(adjusted, key=lambda item: (item.score, item.candidate.candidate_id))
            selected.append(best)
            if best.candidate.source:
                used_sources[best.candidate.source] = used_sources.get(best.candidate.source, 0) + 1
            remaining = [item for item in remaining if item.candidate.candidate_id != best.candidate.candidate_id]
        return selected

    @staticmethod
    def _bm25(
        query: Mapping[str, int],
        document: Sequence[str],
        df: Mapping[str, int],
        count: int,
        documents: Sequence[Sequence[str]],
    ) -> float:
        if not query or not document:
            return 0.0
        average = sum(len(item) for item in documents) / max(1, len(documents))
        frequencies = {token: document.count(token) for token in set(document)}
        k1, b = 1.2, 0.75
        score = 0.0
        for token, query_frequency in query.items():
            frequency = frequencies.get(token, 0)
            if not frequency:
                continue
            inverse = math.log(1 + (count - df.get(token, 0) + 0.5) / (df.get(token, 0) + 0.5))
            denominator = frequency + k1 * (1 - b + b * len(document) / max(1.0, average))
            score += inverse * (frequency * (k1 + 1) / denominator) * min(2, query_frequency)
        return 1 - math.exp(-score)

    @staticmethod
    def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
        if len(left) != len(right) or not left:
            return 0.0
        dot = sum(a * b for a, b in zip(left, right, strict=True))
        norm_left = math.sqrt(sum(value * value for value in left))
        norm_right = math.sqrt(sum(value * value for value in right))
        if norm_left == 0 or norm_right == 0:
            return 0.0
        return max(0.0, min(1.0, (dot / (norm_left * norm_right) + 1.0) / 2.0))

    @staticmethod
    def _unit(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    @staticmethod
    def serializable(results: Iterable[RankedCandidate]) -> list[dict[str, Any]]:
        return [asdict(item) for item in results]
