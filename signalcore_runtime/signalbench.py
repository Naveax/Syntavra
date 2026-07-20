from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .claim_governance import bootstrap_ci
from .util import atomic_write_json, canonical_json, sha256_bytes, sha256_file


class SignalBenchError(RuntimeError):
    pass


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    family: str
    prompt: str
    repository: str
    repository_tree: str
    verifier: tuple[str, ...]
    timeout_seconds: float = 1200.0
    permissions: tuple[str, ...] = ("read", "write", "execute")
    expected_work: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ArmSpec:
    arm_id: str
    category: str
    command: tuple[str, ...]
    version: str
    model: str
    reasoning: str
    context_window: int
    environment: dict[str, str] = field(default_factory=dict)
    adapter: str = "external-json-v1"


@dataclass(frozen=True)
class RunResult:
    run_id: str
    task_id: str
    arm_id: str
    repetition: int
    success: bool
    verifier_success: bool
    verified_work: float
    wall_seconds: float
    exit_code: int
    fresh_input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    quota_cost: float | None
    model_turns: int
    tool_calls: int
    wait_calls: int
    compactions: int
    security_regressions: int
    verifier_skips: int
    repository_tree: str
    prompt_hash: str
    verifier_hash: str
    permissions_hash: str
    cache_mode: str
    artifact_dir: str
    error: str = ""


TASK_FAMILIES = (
    "known-edit",
    "structural-navigation",
    "call-graph-impact",
    "multi-file-implementation",
    "bug-diagnosis",
    "security-repair",
    "output-heavy-verification",
    "long-running-process",
    "long-session-continuity",
    "context-recovery",
    "multi-language-repository",
    "repository-onboarding",
)


class SignalBenchProtocol:
    schema_version = 3

    @staticmethod
    def task_hash(task: TaskSpec) -> str:
        return sha256_bytes(canonical_json(asdict(task)))

    @staticmethod
    def arm_hash(arm: ArmSpec) -> str:
        sanitized = asdict(arm)
        sanitized["environment"] = {key: "<set>" for key in sorted(arm.environment)}
        return sha256_bytes(canonical_json(sanitized))

    @staticmethod
    def verifier_hash(task: TaskSpec) -> str:
        return sha256_bytes(canonical_json(task.verifier))

    @staticmethod
    def permissions_hash(task: TaskSpec) -> str:
        return sha256_bytes(canonical_json(task.permissions))

    @classmethod
    def validate_task(cls, task: TaskSpec) -> list[str]:
        reasons: list[str] = []
        if not task.task_id or not task.prompt or not task.repository or not task.repository_tree:
            reasons.append("task-identity-incomplete")
        if task.family not in TASK_FAMILIES:
            reasons.append("unknown-task-family")
        if not task.verifier:
            reasons.append("missing-verifier")
        if task.timeout_seconds <= 0 or task.expected_work <= 0:
            reasons.append("invalid-task-limits")
        return reasons

    @classmethod
    def validate_arm(cls, arm: ArmSpec) -> list[str]:
        reasons: list[str] = []
        if not arm.arm_id or not arm.command or not arm.version:
            reasons.append("arm-identity-incomplete")
        if not arm.model or not arm.reasoning or arm.context_window <= 0:
            reasons.append("model-identity-incomplete")
        return reasons

    @classmethod
    def pair_identity(cls, task: TaskSpec, arm: ArmSpec, *, cache_mode: str) -> dict[str, Any]:
        return {
            "task_hash": cls.task_hash(task),
            "repository_tree": task.repository_tree,
            "model": arm.model,
            "reasoning": arm.reasoning,
            "context_window": arm.context_window,
            "prompt_hash": sha256_bytes(task.prompt.encode()),
            "verifier_hash": cls.verifier_hash(task),
            "permissions_hash": cls.permissions_hash(task),
            "timeout_seconds": task.timeout_seconds,
            "cache_mode": cache_mode,
        }


class SignalBenchRunner:
    """External-arm benchmark runner with frozen tasks and raw artifacts.

    Arms communicate through a small JSON request/result contract. Competitor
    source is never imported into SignalCore; each product is installed and
    executed independently by its adapter command.
    """

    def __init__(self, root: Path, *, seed: int = 1337):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.seed = seed

    @staticmethod
    def load_tasks(path: Path) -> list[TaskSpec]:
        value = json.loads(path.read_text(encoding="utf-8"))
        rows = value if isinstance(value, list) else value.get("tasks", [])
        return [TaskSpec(**{**row, "verifier": tuple(row["verifier"]), "permissions": tuple(row.get("permissions", ("read", "write", "execute")))}) for row in rows]

    @staticmethod
    def load_arms(path: Path) -> list[ArmSpec]:
        value = json.loads(path.read_text(encoding="utf-8"))
        rows = value if isinstance(value, list) else value.get("arms", [])
        return [ArmSpec(**{**row, "command": tuple(row["command"])}) for row in rows]

    @staticmethod
    def write_manifest(path: Path, tasks: Iterable[TaskSpec], arms: Iterable[ArmSpec]) -> dict[str, Any]:
        task_rows = [asdict(task) for task in tasks]
        arm_rows = [asdict(arm) for arm in arms]
        value = {
            "schema_version": 3,
            "tasks": task_rows,
            "arms": arm_rows,
            "task_corpus_hash": sha256_bytes(canonical_json(task_rows)),
            "arm_registry_hash": sha256_bytes(canonical_json([{**row, "environment": sorted(row["environment"])} for row in arm_rows])),
        }
        value["manifest_hash"] = sha256_bytes(canonical_json(value))
        atomic_write_json(path, value, mode=0o644)
        return value

    def validate(self, tasks: Iterable[TaskSpec], arms: Iterable[ArmSpec]) -> dict[str, Any]:
        tasks = list(tasks)
        arms = list(arms)
        reasons: list[str] = []
        task_ids: set[str] = set()
        for task in tasks:
            reasons.extend(f"task:{task.task_id}:{reason}" for reason in SignalBenchProtocol.validate_task(task))
            if task.task_id in task_ids:
                reasons.append(f"duplicate-task:{task.task_id}")
            task_ids.add(task.task_id)
        arm_ids: set[str] = set()
        for arm in arms:
            reasons.extend(f"arm:{arm.arm_id}:{reason}" for reason in SignalBenchProtocol.validate_arm(arm))
            if arm.arm_id in arm_ids:
                reasons.append(f"duplicate-arm:{arm.arm_id}")
            arm_ids.add(arm.arm_id)
        if len({(arm.model, arm.reasoning, arm.context_window) for arm in arms}) > 1:
            reasons.append("arm-model-identity-mismatch")
        return {"ok": not reasons, "reasons": reasons, "tasks": len(tasks), "arms": len(arms)}

    @staticmethod
    def _copy_repository(source: Path, destination: Path) -> None:
        ignored = shutil.ignore_patterns(".git", ".signalcore", "node_modules", "target", "dist", "build", "__pycache__", ".pytest_cache")
        shutil.copytree(source, destination, ignore=ignored)

    @staticmethod
    def _substitute(command: tuple[str, ...], *, request: Path, output: Path, workspace: Path) -> tuple[str, ...]:
        return tuple(
            value.replace("{request}", str(request)).replace("{output}", str(output)).replace("{workspace}", str(workspace))
            for value in command
        )

    def run_one(
        self,
        task: TaskSpec,
        arm: ArmSpec,
        *,
        repetition: int,
        cache_mode: str,
    ) -> RunResult:
        reasons = [*SignalBenchProtocol.validate_task(task), *SignalBenchProtocol.validate_arm(arm)]
        run_id = f"run-{task.task_id}-{arm.arm_id}-{repetition}-{uuid.uuid4().hex[:8]}"
        artifact_dir = self.root / "runs" / run_id
        artifact_dir.mkdir(parents=True, exist_ok=False)
        if reasons:
            return self._failure(run_id, task, arm, repetition, cache_mode, artifact_dir, ";".join(reasons))
        source = Path(task.repository).resolve(strict=True)
        workspace = artifact_dir / "workspace"
        self._copy_repository(source, workspace)
        identity = SignalBenchProtocol.pair_identity(task, arm, cache_mode=cache_mode)
        request = {
            "schema_version": 3,
            "run_id": run_id,
            "task": asdict(task),
            "arm": {**asdict(arm), "environment": sorted(arm.environment)},
            "identity": identity,
            "workspace": str(workspace),
            "result_path": str(artifact_dir / "arm-result.json"),
        }
        request_path = artifact_dir / "request.json"
        result_path = artifact_dir / "arm-result.json"
        atomic_write_json(request_path, request, mode=0o600)
        command = self._substitute(arm.command, request=request_path, output=result_path, workspace=workspace)
        environment = dict(os.environ)
        environment.update(arm.environment)
        environment.update({
            "SIGNALBENCH_REQUEST": str(request_path),
            "SIGNALBENCH_OUTPUT": str(result_path),
            "SIGNALBENCH_WORKSPACE": str(workspace),
        })
        stdout_path = artifact_dir / "arm.stdout.log"
        stderr_path = artifact_dir / "arm.stderr.log"
        started = time.time()
        try:
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                process = subprocess.run(
                    command,
                    cwd=workspace,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    timeout=task.timeout_seconds,
                    check=False,
                )
            exit_code = process.returncode
            error = ""
        except subprocess.TimeoutExpired:
            exit_code = 124
            error = "arm-timeout"
        wall = time.time() - started

        arm_result: dict[str, Any] = {}
        if result_path.is_file():
            try:
                value = json.loads(result_path.read_text(encoding="utf-8"))
                arm_result = value if isinstance(value, dict) else {}
            except json.JSONDecodeError:
                error = error or "invalid-arm-result-json"
        else:
            error = error or "missing-arm-result"

        verifier_stdout = artifact_dir / "verifier.stdout.log"
        verifier_stderr = artifact_dir / "verifier.stderr.log"
        verifier_started = time.time()
        try:
            with verifier_stdout.open("wb") as stdout, verifier_stderr.open("wb") as stderr:
                verification = subprocess.run(
                    task.verifier,
                    cwd=workspace,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    timeout=task.timeout_seconds,
                    check=False,
                )
            verifier_success = verification.returncode == 0
        except subprocess.TimeoutExpired:
            verifier_success = False
        verifier_seconds = time.time() - verifier_started

        success = exit_code == 0 and verifier_success and bool(arm_result.get("success", True))
        verified_work = task.expected_work if success else 0.0
        raw_metrics = arm_result.get("metrics", {}) if isinstance(arm_result.get("metrics", {}), dict) else {}
        result = RunResult(
            run_id,
            task.task_id,
            arm.arm_id,
            repetition,
            success,
            verifier_success,
            verified_work,
            wall,
            exit_code,
            int(raw_metrics.get("fresh_input_tokens", 0)),
            int(raw_metrics.get("cached_input_tokens", 0)),
            int(raw_metrics.get("output_tokens", 0)),
            int(raw_metrics.get("reasoning_tokens", 0)),
            float(raw_metrics["quota_cost"]) if raw_metrics.get("quota_cost") is not None else None,
            int(raw_metrics.get("model_turns", 0)),
            int(raw_metrics.get("tool_calls", 0)),
            int(raw_metrics.get("wait_calls", 0)),
            int(raw_metrics.get("compactions", 0)),
            int(raw_metrics.get("security_regressions", 0)),
            int(raw_metrics.get("verifier_skips", 0)),
            task.repository_tree,
            identity["prompt_hash"],
            identity["verifier_hash"],
            identity["permissions_hash"],
            cache_mode,
            str(artifact_dir),
            error,
        )
        atomic_write_json(artifact_dir / "result.json", asdict(result), mode=0o600)
        atomic_write_json(artifact_dir / "receipt.json", {
            "result_hash": sha256_bytes(canonical_json(asdict(result))),
            "request_hash": sha256_file(request_path),
            "stdout_hash": sha256_file(stdout_path),
            "stderr_hash": sha256_file(stderr_path),
            "verifier_stdout_hash": sha256_file(verifier_stdout),
            "verifier_stderr_hash": sha256_file(verifier_stderr),
            "verifier_seconds": verifier_seconds,
        }, mode=0o600)
        return result

    def _failure(self, run_id: str, task: TaskSpec, arm: ArmSpec, repetition: int, cache_mode: str, artifact_dir: Path, error: str) -> RunResult:
        return RunResult(
            run_id, task.task_id, arm.arm_id, repetition, False, False, 0.0, 0.0, 2,
            0, 0, 0, 0, None, 0, 0, 0, 0, 0, 0, task.repository_tree,
            sha256_bytes(task.prompt.encode()), SignalBenchProtocol.verifier_hash(task),
            SignalBenchProtocol.permissions_hash(task), cache_mode, str(artifact_dir), error,
        )

    def run(
        self,
        tasks: Iterable[TaskSpec],
        arms: Iterable[ArmSpec],
        *,
        repetitions: int = 3,
        cache_modes: tuple[str, ...] = ("cold", "warm"),
        randomized: bool = True,
    ) -> dict[str, Any]:
        tasks = list(tasks)
        arms = list(arms)
        validation = self.validate(tasks, arms)
        if not validation["ok"]:
            raise SignalBenchError("; ".join(validation["reasons"]))
        work = [
            (task, arm, repetition, cache_mode)
            for repetition in range(1, repetitions + 1)
            for cache_mode in cache_modes
            for task in tasks
            for arm in arms
        ]
        if randomized:
            random.Random(self.seed).shuffle(work)
        results = [self.run_one(task, arm, repetition=repetition, cache_mode=cache_mode) for task, arm, repetition, cache_mode in work]
        output = {
            "schema_version": 3,
            "validation": validation,
            "repetitions": repetitions,
            "cache_modes": cache_modes,
            "randomized": randomized,
            "seed": self.seed,
            "results": [asdict(result) for result in results],
        }
        output["result_hash"] = sha256_bytes(canonical_json(output))
        atomic_write_json(self.root / "results.json", output, mode=0o600)
        return output

    @staticmethod
    def compare(results: Iterable[RunResult], *, baseline_arm: str, candidate_arm: str) -> dict[str, Any]:
        results = list(results)
        keyed = {(row.task_id, row.repetition, row.cache_mode, row.arm_id): row for row in results}
        ratios: list[float] = []
        invalid: list[dict[str, Any]] = []
        quality = {baseline_arm: 0, candidate_arm: 0}
        total = {baseline_arm: 0, candidate_arm: 0}
        for key, base in keyed.items():
            task_id, repetition, cache_mode, arm_id = key
            if arm_id != baseline_arm:
                continue
            candidate = keyed.get((task_id, repetition, cache_mode, candidate_arm))
            total[baseline_arm] += 1
            quality[baseline_arm] += int(base.success)
            if candidate:
                total[candidate_arm] += 1
                quality[candidate_arm] += int(candidate.success)
            if not candidate:
                invalid.append({"task": task_id, "repetition": repetition, "cache": cache_mode, "reason": "missing-candidate"})
                continue
            if not base.success or not candidate.success or base.verified_work != candidate.verified_work:
                invalid.append({"task": task_id, "repetition": repetition, "cache": cache_mode, "reason": "unequal-verified-work"})
                continue
            if base.quota_cost is None or candidate.quota_cost is None or candidate.quota_cost <= 0:
                invalid.append({"task": task_id, "repetition": repetition, "cache": cache_mode, "reason": "quota-unavailable"})
                continue
            ratios.append(base.quota_cost / candidate.quota_cost)
        ratios.sort()
        ci = bootstrap_ci(ratios) if ratios else None
        median = ratios[len(ratios) // 2] if ratios else None
        pass_rates = {arm: quality[arm] / total[arm] if total[arm] else 0.0 for arm in total}
        claimable = bool(
            len(ratios) >= 10
            and ci
            and ci[0] > 1.0
            and pass_rates[candidate_arm] >= pass_rates[baseline_arm]
            and not any(row.security_regressions or row.verifier_skips for row in results if row.arm_id == candidate_arm)
        )
        return {
            "baseline": baseline_arm,
            "candidate": candidate_arm,
            "valid_pairs": len(ratios),
            "invalid_pairs": invalid,
            "median_efficiency_ratio": median,
            "confidence_interval_95": ci,
            "pass_rates": pass_rates,
            "claimable_superiority": claimable,
            "claim": "SUPERIORITY_PROVEN" if claimable else "NOT_PROVEN",
        }


def load_results(path: Path) -> list[RunResult]:
    value = json.loads(path.read_text(encoding="utf-8"))
    rows = value if isinstance(value, list) else value.get("results", [])
    return [RunResult(**row) for row in rows]
