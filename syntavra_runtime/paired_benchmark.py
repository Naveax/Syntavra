from __future__ import annotations

import itertools
import math
import random
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Iterator

from .util import canonical_json, sha256_bytes

LANGUAGES = ("python", "typescript", "rust", "go", "java", "cpp", "csharp", "ruby", "php", "luau")
CATEGORY_COUNTS = {
    "bug-fix": 30,
    "feature": 25,
    "refactor": 20,
    "test-repair": 15,
    "performance": 10,
    "security": 10,
    "api-migration": 10,
    "cross-file-reasoning": 15,
    "repository-exploration": 15,
}
DEFAULT_ARMS = ("plain-baseline", "syntavra", "token-savior", "context-mode", "headroom", "volt-lcm")


@dataclass(frozen=True)
class CodingTaskSlot:
    task_id: str
    category: str
    language: str
    repository: str
    repository_tree: str
    prompt: str
    verifier: tuple[str, ...]
    source_kind: str = "corpus-slot"
    live_materialized: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ArmIdentity:
    arm_id: str
    command: tuple[str, ...]
    model: str
    provider: str
    reasoning: str
    context_window: int
    permissions: tuple[str, ...] = ("read", "write", "execute")


@dataclass(frozen=True)
class PairedRun:
    task_id: str
    arm_id: str
    repetition: int
    pair_key: str
    order: int


class CodingCorpusPlanner:
    task_target = 150

    @staticmethod
    def generate_slots() -> list[CodingTaskSlot]:
        rows: list[CodingTaskSlot] = []
        language_cycle = itertools.cycle(LANGUAGES)
        index = 0
        for category, count in CATEGORY_COUNTS.items():
            for offset in range(count):
                language = next(language_cycle)
                index += 1
                task_id = f"coding-{index:03d}-{category}-{language}"
                rows.append(CodingTaskSlot(
                    task_id=task_id,
                    category=category,
                    language=language,
                    repository=f"<materialize:{category}:{language}:{offset + 1}>",
                    repository_tree="REQUIRED_AT_MATERIALIZATION",
                    prompt=f"Execute the verified {category} task for {language} corpus slot {offset + 1}.",
                    verifier=("<external-verifier-required>",),
                    metadata={"slot_index": index, "claim_eligible": False},
                ))
        if len(rows) != CodingCorpusPlanner.task_target:
            raise AssertionError(len(rows))
        return rows

    @staticmethod
    def validate_live_corpus(tasks: Iterable[CodingTaskSlot]) -> dict[str, Any]:
        tasks = list(tasks)
        reasons: list[str] = []
        if len(tasks) < CodingCorpusPlanner.task_target:
            reasons.append("insufficient-task-count")
        seen: set[str] = set()
        for task in tasks:
            if task.task_id in seen: reasons.append(f"duplicate-task:{task.task_id}")
            seen.add(task.task_id)
            if not task.live_materialized: reasons.append(f"not-live:{task.task_id}")
            if not task.repository_tree or task.repository_tree == "REQUIRED_AT_MATERIALIZATION": reasons.append(f"missing-tree:{task.task_id}")
            if not task.verifier or task.verifier == ("<external-verifier-required>",): reasons.append(f"missing-verifier:{task.task_id}")
        return {"ok": not reasons, "tasks": len(tasks), "reasons": reasons[:100]}


class PairedSchedule:
    def __init__(self, tasks: Iterable[CodingTaskSlot], arms: Iterable[ArmIdentity], *, repetitions: int = 30, seed: int = 1337):
        self.tasks = tuple(tasks)
        self.arms = tuple(arms)
        self.repetitions = repetitions
        self.seed = seed
        if repetitions < 30:
            raise ValueError("public comparison requires at least 30 paired repetitions")
        identities = {(arm.model, arm.provider, arm.reasoning, arm.context_window, arm.permissions) for arm in self.arms}
        if len(identities) > 1:
            raise ValueError("arm identities are not identical")

    @property
    def count(self) -> int:
        return len(self.tasks) * len(self.arms) * self.repetitions

    def iter_runs(self) -> Iterator[PairedRun]:
        randomizer = random.Random(self.seed)
        order = 0
        for repetition in range(1, self.repetitions + 1):
            task_order = list(self.tasks)
            randomizer.shuffle(task_order)
            for task in task_order:
                arms = list(self.arms)
                randomizer.shuffle(arms)
                pair_key = sha256_bytes(canonical_json({"task": task.task_id, "repetition": repetition, "seed": self.seed}))
                for arm in arms:
                    order += 1
                    yield PairedRun(task.task_id, arm.arm_id, repetition, pair_key, order)

    def manifest(self) -> dict[str, Any]:
        value = {
            "schema_version": 2,
            "tasks": [asdict(task) for task in self.tasks],
            "arms": [asdict(arm) for arm in self.arms],
            "repetitions": self.repetitions,
            "seed": self.seed,
            "run_count": self.count,
        }
        value["manifest_hash"] = sha256_bytes(canonical_json(value))
        return value


def _mean(values: list[float]) -> float:
    return sum(values) / max(1, len(values))


def _percentile(values: list[float], fraction: float) -> float:
    values = sorted(values)
    if not values: return 0.0
    index = min(len(values) - 1, max(0, math.ceil(len(values) * fraction) - 1))
    return values[index]


class SuperiorityGate:
    required_tasks = 150
    required_repetitions = 30
    minimum_success = 0.985
    maximum_token_ratio = 0.18
    maximum_wall_ratio = 0.15
    maximum_security_regressions = 0

    @classmethod
    def evaluate(cls, receipts: Iterable[dict[str, Any]], *, candidate: str = "syntavra", baseline: str = "plain-baseline") -> dict[str, Any]:
        rows = list(receipts)
        reasons: list[str] = []
        if any(row.get("synthetic") or row.get("source_kind") != "live-external-arm" for row in rows):
            reasons.append("non-live-receipt")
        task_ids = {str(row.get("task_id")) for row in rows}
        repetitions = {int(row.get("repetition", 0)) for row in rows}
        if len(task_ids) < cls.required_tasks: reasons.append("insufficient-tasks")
        if len(repetitions) < cls.required_repetitions: reasons.append("insufficient-repetitions")
        by_arm: dict[str, list[dict[str, Any]]] = {}
        for row in rows: by_arm.setdefault(str(row.get("arm_id")), []).append(row)
        if candidate not in by_arm or baseline not in by_arm: reasons.append("missing-required-arm")
        candidate_rows = by_arm.get(candidate, [])
        baseline_rows = by_arm.get(baseline, [])
        success = _mean([1.0 if row.get("success") else 0.0 for row in candidate_rows])
        candidate_tokens = _mean([float(row.get("active_tokens", 0)) for row in candidate_rows])
        baseline_tokens = _mean([float(row.get("active_tokens", 0)) for row in baseline_rows])
        candidate_wall = _mean([float(row.get("wall_seconds", 0)) for row in candidate_rows])
        baseline_wall = _mean([float(row.get("wall_seconds", 0)) for row in baseline_rows])
        token_ratio = candidate_tokens / baseline_tokens if baseline_tokens > 0 else math.inf
        wall_ratio = candidate_wall / baseline_wall if baseline_wall > 0 else math.inf
        security = sum(int(row.get("security_regressions", 0)) for row in candidate_rows)
        if success < cls.minimum_success: reasons.append("success-floor-missed")
        if token_ratio > cls.maximum_token_ratio: reasons.append("token-target-missed")
        if wall_ratio > cls.maximum_wall_ratio: reasons.append("wall-target-missed")
        if security > cls.maximum_security_regressions: reasons.append("security-regression")
        pair_keys = {str(row.get("pair_key")) for row in rows}
        if len(pair_keys) < cls.required_tasks * cls.required_repetitions: reasons.append("paired-coverage-incomplete")
        return {
            "ok": not reasons,
            "claim": "SUPERIORITY_PROVEN" if not reasons else "EXTERNAL_SUPERIORITY_NOT_PROVEN",
            "reasons": reasons,
            "metrics": {
                "tasks": len(task_ids), "repetitions": len(repetitions), "success": success,
                "token_ratio": token_ratio, "wall_ratio": wall_ratio, "security_regressions": security,
                "candidate_wall_p95": _percentile([float(row.get("wall_seconds", 0)) for row in candidate_rows], 0.95),
            },
        }


def default_arms() -> tuple[ArmIdentity, ...]:
    return tuple(ArmIdentity(arm, (f"<external:{arm}>",), "IDENTICAL_MODEL", "IDENTICAL_PROVIDER", "IDENTICAL_REASONING", 200_000) for arm in DEFAULT_ARMS)
