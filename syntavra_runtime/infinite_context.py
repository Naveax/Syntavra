from __future__ import annotations

import concurrent.futures
import math
import threading
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Sequence

from .util import canonical_json, sha256_bytes

CONTEXT_TIERS = (32_000, 64_000, 128_000, 256_000, 512_000, 1_000_000, 2_000_000, 10_000_000)


@dataclass(frozen=True)
class ExternalSegment:
    segment_id: str
    token_count: int
    evidence_ref: str
    summary: str
    query_terms: tuple[str, ...] = ()
    temporal_key: str = ""
    generation: int = 0
    critical: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ActiveContextPlan:
    total_history_tokens: int
    active_tokens: int
    budget: int
    selected_segment_ids: tuple[str, ...]
    exact_references: tuple[str, ...]
    covered_tokens: int
    forced_restart: bool
    summary_levels: int
    query: str


@dataclass(frozen=True)
class RecursiveTask:
    task_id: str
    payload: Any
    read_only: bool = True
    budget: int = 4096


@dataclass(frozen=True)
class RecursiveResult:
    task_id: str
    output: Any
    attempts: int
    evidence_hash: str


class UnboundedContextCoordinator:
    """Unbounded external history with a strictly bounded active model window."""

    def __init__(self, *, active_budget: int = 4096, summary_fanout: int = 8):
        if active_budget < 256: raise ValueError("active_budget is too small")
        if summary_fanout < 2: raise ValueError("summary_fanout must be at least two")
        self.active_budget = active_budget
        self.summary_fanout = summary_fanout
        self._segments: list[ExternalSegment] = []
        self._lock = threading.RLock()

    def append(self, segment: ExternalSegment) -> None:
        if segment.token_count <= 0 or not segment.evidence_ref:
            raise ValueError("exact segment identity is incomplete")
        with self._lock:
            if any(item.segment_id == segment.segment_id for item in self._segments):
                raise ValueError(f"duplicate segment: {segment.segment_id}")
            self._segments.append(segment)

    def append_virtual_history(self, total_tokens: int, *, chunk_tokens: int = 2048) -> int:
        if total_tokens <= 0: raise ValueError("total_tokens must be positive")
        remaining = total_tokens
        index = len(self._segments)
        while remaining:
            size = min(chunk_tokens, remaining)
            index += 1
            generation = index
            temporal_key = f"decision-{index % 97}" if index % 5 == 0 else ""
            critical = index % 113 == 0
            segment_id = f"seg-{index:08d}"
            self.append(ExternalSegment(
                segment_id, size, f"sc://evidence/{segment_id}",
                f"virtual history segment {index} covering {size} tokens",
                ("history", f"tier-{total_tokens}", f"slot-{index % 31}"),
                temporal_key, generation, critical,
                {"virtual": True, "index": index},
            ))
            remaining -= size
        return index

    @property
    def total_tokens(self) -> int:
        with self._lock:
            return sum(item.token_count for item in self._segments)

    @staticmethod
    def _terms(query: str) -> set[str]:
        return {item.casefold() for item in query.replace("/", " ").replace("-", " ").split() if item}

    def plan(self, query: str, *, budget: int | None = None) -> ActiveContextPlan:
        limit = budget or self.active_budget
        query_terms = self._terms(query)
        with self._lock:
            segments = tuple(self._segments)
        current_by_key: dict[str, ExternalSegment] = {}
        for item in segments:
            if item.temporal_key:
                previous = current_by_key.get(item.temporal_key)
                if previous is None or item.generation >= previous.generation:
                    current_by_key[item.temporal_key] = item
        current_ids = {item.segment_id for item in current_by_key.values()}

        def score(item: ExternalSegment) -> tuple[float, int, str]:
            overlap = len(query_terms & {term.casefold() for term in item.query_terms})
            value = overlap * 100.0 + (50.0 if item.critical else 0.0) + (20.0 if item.segment_id in current_ids else 0.0)
            value += math.log1p(max(0, item.generation)) / 5.0
            return value, item.generation, item.segment_id

        selected: list[ExternalSegment] = []
        used = 0
        for item in sorted(segments, key=score, reverse=True):
            visible_cost = max(16, min(256, math.ceil(len(item.summary) / 4) + 12))
            if used + visible_cost > limit:
                continue
            selected.append(item)
            used += visible_cost
        selected.sort(key=lambda item: item.generation)
        total = sum(item.token_count for item in segments)
        levels = 0 if not segments else math.ceil(math.log(max(1, len(segments)), self.summary_fanout))
        return ActiveContextPlan(
            total, used, limit, tuple(item.segment_id for item in selected),
            tuple(item.evidence_ref for item in selected),
            sum(item.token_count for item in selected), False, levels, query,
        )

    def exact_recovery_manifest(self) -> dict[str, Any]:
        with self._lock:
            segments = tuple(self._segments)
        rows = [{"segment_id": item.segment_id, "tokens": item.token_count, "evidence_ref": item.evidence_ref} for item in segments]
        return {
            "segments": len(rows),
            "total_tokens": sum(row["tokens"] for row in rows),
            "all_referenced": all(bool(row["evidence_ref"]) for row in rows),
            "manifest_hash": sha256_bytes(canonical_json(rows)),
        }

    @classmethod
    def stress_tiers(cls, *, active_budget: int = 4096) -> list[dict[str, Any]]:
        reports = []
        for tier in CONTEXT_TIERS:
            coordinator = cls(active_budget=active_budget)
            coordinator.append_virtual_history(tier)
            plan = coordinator.plan(f"current critical history tier {tier}")
            manifest = coordinator.exact_recovery_manifest()
            reports.append({
                "tier_tokens": tier,
                "segments": manifest["segments"],
                "active_tokens": plan.active_tokens,
                "within_budget": plan.active_tokens <= active_budget,
                "forced_restart": plan.forced_restart,
                "all_referenced": manifest["all_referenced"],
                "history_tokens": plan.total_history_tokens,
                "summary_levels": plan.summary_levels,
            })
        return reports


class RecursiveExecutionEngine:
    def __init__(self, *, workers: int = 8, retries: int = 2):
        self.workers = max(1, workers)
        self.retries = max(0, retries)

    def execute(
        self,
        tasks: Sequence[RecursiveTask],
        mapper: Callable[[RecursiveTask], Any],
        reducer: Callable[[list[RecursiveResult]], Any],
    ) -> dict[str, Any]:
        unique: dict[str, RecursiveTask] = {}
        for task in tasks:
            identity = sha256_bytes(canonical_json({"task_id": task.task_id, "payload": task.payload, "budget": task.budget}))
            unique.setdefault(identity, task)

        def run(task: RecursiveTask) -> RecursiveResult:
            last_error: Exception | None = None
            for attempt in range(1, self.retries + 2):
                try:
                    output = mapper(task)
                    evidence_hash = sha256_bytes(canonical_json({"task": task.task_id, "output": output}))
                    return RecursiveResult(task.task_id, output, attempt, evidence_hash)
                except Exception as exc:
                    last_error = exc
            raise RuntimeError(f"recursive task failed: {task.task_id}") from last_error

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as pool:
            results = list(pool.map(run, unique.values()))
        results.sort(key=lambda item: item.task_id)
        reduced = reducer(results)
        return {
            "ok": True,
            "tasks_submitted": len(tasks),
            "tasks_executed": len(results),
            "duplicates_suppressed": len(tasks) - len(results),
            "results": [asdict(item) for item in results],
            "reduced": reduced,
            "global_provenance_hash": sha256_bytes(canonical_json([item.evidence_hash for item in results])),
        }
