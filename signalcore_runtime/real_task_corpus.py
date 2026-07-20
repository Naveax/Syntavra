from __future__ import annotations

import json
import random
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .util import canonical_json, sha256_bytes


_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{1,127}$")
_CACHE_MODES = {"cold", "warm", "provider-native", "off"}


def _argv(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{field} must be an argv array; shell strings are forbidden")
    result = tuple(str(item) for item in value)
    if not result or any(not item for item in result):
        raise ValueError(f"{field} must contain non-empty argv entries")
    return result


@dataclass(frozen=True)
class RealTaskSpec:
    task_id: str
    repository: str
    commit: str
    issue: str
    setup_argv: tuple[str, ...]
    test_argv: tuple[str, ...]
    verification_argv: tuple[str, ...]
    timeout_seconds: float
    expected_paths: tuple[str, ...]
    language: str
    difficulty: str
    metadata: dict[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RealTaskSpec":
        task_id = str(value.get("task_id") or "")
        repository = str(value.get("repository") or "")
        commit = str(value.get("commit") or "").casefold()
        if not _ID_RE.match(task_id):
            raise ValueError("task_id must be stable and machine-safe")
        if repository.count("/") != 1 or any(part in {"", ".", ".."} for part in repository.split("/")):
            raise ValueError(f"invalid repository identity for {task_id}")
        if not _COMMIT_RE.match(commit):
            raise ValueError(f"{task_id}: commit must be a full 40-character lowercase SHA")
        timeout = float(value.get("timeout_seconds", 1200))
        if timeout <= 0 or timeout > 14_400:
            raise ValueError(f"{task_id}: timeout_seconds out of bounds")
        expected_paths = tuple(str(item) for item in value.get("expected_paths", ()))
        if any(path.startswith(("/", "\\")) or ".." in Path(path).parts for path in expected_paths):
            raise ValueError(f"{task_id}: expected_paths must remain repository-relative")
        return cls(
            task_id=task_id,
            repository=repository,
            commit=commit,
            issue=str(value.get("issue") or ""),
            setup_argv=_argv(value.get("setup_argv", ("python", "-m", "pip", "install", "-e", ".")), "setup_argv"),
            test_argv=_argv(value.get("test_argv"), "test_argv"),
            verification_argv=_argv(value.get("verification_argv", value.get("test_argv")), "verification_argv"),
            timeout_seconds=timeout,
            expected_paths=expected_paths,
            language=str(value.get("language") or "unknown"),
            difficulty=str(value.get("difficulty") or "unrated"),
            metadata=dict(value.get("metadata") or {}),
        )


@dataclass(frozen=True)
class CompetitorArmSpec:
    arm_id: str
    executable_argv: tuple[str, ...]
    provider: str
    model: str
    tool_permissions: tuple[str, ...]
    cache_modes: tuple[str, ...]
    environment_fingerprint: str
    version: str
    metadata: dict[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CompetitorArmSpec":
        arm_id = str(value.get("arm_id") or "")
        if not _ID_RE.match(arm_id):
            raise ValueError("arm_id must be stable and machine-safe")
        cache_modes = tuple(str(item).casefold() for item in value.get("cache_modes", ("cold", "warm")))
        if not cache_modes or any(mode not in _CACHE_MODES for mode in cache_modes):
            raise ValueError(f"{arm_id}: unsupported cache mode")
        fingerprint = str(value.get("environment_fingerprint") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise ValueError(f"{arm_id}: environment_fingerprint must be SHA-256")
        permissions = tuple(sorted(dict.fromkeys(str(item) for item in value.get("tool_permissions", ()))))
        if not permissions:
            raise ValueError(f"{arm_id}: tool_permissions cannot be empty")
        return cls(
            arm_id=arm_id,
            executable_argv=_argv(value.get("executable_argv"), "executable_argv"),
            provider=str(value.get("provider") or ""),
            model=str(value.get("model") or ""),
            tool_permissions=permissions,
            cache_modes=cache_modes,
            environment_fingerprint=fingerprint,
            version=str(value.get("version") or "unknown"),
            metadata=dict(value.get("metadata") or {}),
        )


class RealTaskCorpus:
    """Strict, deterministic corpus and executable-arm manifest builder.

    This module validates benchmark identity and parity. It does not execute
    third-party products and never treats internal fixtures as superiority proof.
    """

    def __init__(
        self,
        tasks: Iterable[RealTaskSpec],
        arms: Iterable[CompetitorArmSpec],
    ):
        self.tasks = tuple(tasks)
        self.arms = tuple(arms)

    @classmethod
    def from_values(
        cls,
        tasks: Iterable[Mapping[str, Any]],
        arms: Iterable[Mapping[str, Any]],
    ) -> "RealTaskCorpus":
        return cls(
            (RealTaskSpec.from_mapping(value) for value in tasks),
            (CompetitorArmSpec.from_mapping(value) for value in arms),
        )

    @classmethod
    def load(cls, tasks_path: Path, arms_path: Path) -> "RealTaskCorpus":
        tasks = json.loads(tasks_path.read_text(encoding="utf-8"))
        arms = json.loads(arms_path.read_text(encoding="utf-8"))
        if isinstance(tasks, Mapping):
            tasks = tasks.get("tasks", [])
        if isinstance(arms, Mapping):
            arms = arms.get("arms", [])
        if not isinstance(tasks, list) or not isinstance(arms, list):
            raise ValueError("tasks and arms documents must contain arrays")
        return cls.from_values(tasks, arms)

    def parity_report(self) -> dict[str, Any]:
        providers = {arm.provider for arm in self.arms}
        models = {arm.model for arm in self.arms}
        permissions = {arm.tool_permissions for arm in self.arms}
        environment = {arm.environment_fingerprint for arm in self.arms}
        shared_cache_modes = set.intersection(*(set(arm.cache_modes) for arm in self.arms)) if self.arms else set()
        reasons: list[str] = []
        if len(providers) != 1:
            reasons.append("provider-mismatch")
        if len(models) != 1:
            reasons.append("model-mismatch")
        if len(permissions) != 1:
            reasons.append("tool-permission-mismatch")
        if len(environment) != 1:
            reasons.append("environment-fingerprint-mismatch")
        if not shared_cache_modes:
            reasons.append("no-shared-cache-mode")
        return {
            "ok": not reasons,
            "provider": next(iter(providers)) if len(providers) == 1 else "",
            "model": next(iter(models)) if len(models) == 1 else "",
            "tool_permissions": list(next(iter(permissions))) if len(permissions) == 1 else [],
            "environment_fingerprint": next(iter(environment)) if len(environment) == 1 else "",
            "shared_cache_modes": sorted(shared_cache_modes),
            "reasons": reasons,
        }

    def validate(
        self,
        *,
        minimum_tasks: int = 50,
        minimum_arms: int = 3,
        minimum_repetitions: int = 30,
    ) -> dict[str, Any]:
        reasons: list[str] = []
        task_ids = [task.task_id for task in self.tasks]
        arm_ids = [arm.arm_id for arm in self.arms]
        if len(task_ids) != len(set(task_ids)):
            reasons.append("duplicate-task-id")
        if len(arm_ids) != len(set(arm_ids)):
            reasons.append("duplicate-arm-id")
        identities = [(task.repository, task.commit, task.issue) for task in self.tasks]
        if len(identities) != len(set(identities)):
            reasons.append("duplicate-task-identity")
        if len(self.tasks) < minimum_tasks:
            reasons.append(f"real-task-corpus:{len(self.tasks)}/{minimum_tasks}")
        if len(self.arms) < minimum_arms:
            reasons.append(f"competitor-arms:{len(self.arms)}/{minimum_arms}")
        parity = self.parity_report()
        reasons.extend(parity["reasons"])
        return {
            "ok": not reasons,
            "claimable": not reasons,
            "tasks": len(self.tasks),
            "arms": len(self.arms),
            "minimum_repetitions": minimum_repetitions,
            "parity": parity,
            "reasons": list(dict.fromkeys(reasons)),
            "claim_boundary": (
                "manifest readiness only; superiority requires completed paired runs, "
                "provider receipts, quality judgments, and statistical release gates"
            ),
        }

    def paired_schedule(
        self,
        *,
        repetitions: int = 30,
        cache_modes: Iterable[str] | None = None,
        seed: int = 1337,
    ) -> list[dict[str, Any]]:
        if repetitions < 1:
            raise ValueError("repetitions must be positive")
        parity = self.parity_report()
        if not parity["ok"]:
            raise ValueError("cannot schedule non-parity arms: " + ", ".join(parity["reasons"]))
        modes = tuple(cache_modes or parity["shared_cache_modes"])
        if not modes or any(mode not in parity["shared_cache_modes"] for mode in modes):
            raise ValueError("requested cache modes are not shared by every arm")
        rows = [
            {
                "task_id": task.task_id,
                "repository": task.repository,
                "commit": task.commit,
                "arm_id": arm.arm_id,
                "repetition": repetition,
                "cache_mode": mode,
            }
            for task in self.tasks
            for repetition in range(1, repetitions + 1)
            for mode in modes
            for arm in self.arms
        ]
        random.Random(seed).shuffle(rows)
        for index, row in enumerate(rows, 1):
            row["schedule_index"] = index
            row["pair_key"] = sha256_bytes(canonical_json({
                "task_id": row["task_id"],
                "repetition": row["repetition"],
                "cache_mode": row["cache_mode"],
            }))[:24]
        return rows

    def manifest(
        self,
        *,
        repetitions: int = 30,
        cache_modes: Iterable[str] | None = None,
        seed: int = 1337,
    ) -> dict[str, Any]:
        schedule = self.paired_schedule(
            repetitions=repetitions,
            cache_modes=cache_modes,
            seed=seed,
        )
        payload = {
            "schema_version": 1,
            "tasks": [asdict(task) for task in self.tasks],
            "arms": [asdict(arm) for arm in self.arms],
            "parity": self.parity_report(),
            "repetitions": repetitions,
            "seed": seed,
            "schedule": schedule,
            "claim": "EXTERNAL_SUPERIORITY_NOT_PROVEN",
        }
        payload["manifest_hash"] = sha256_bytes(canonical_json(payload))
        return payload
