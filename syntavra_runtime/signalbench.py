from __future__ import annotations

import hashlib
import json
import os
import platform
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
from .signalbench_hardened import HardenedSignalBench, HardwareIdentity, UsageReceipt
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
    repository_commit: str = ""


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
    inherit_environment: tuple[str, ...] = ()


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
    provider_observed: bool = False
    provider: str = ""
    model: str = ""
    request_id_hash: str = ""
    provider_receipt_hash: str = ""
    arm_version: str = ""
    reasoning: str = ""
    context_window: int = 0
    hardware_hash: str = ""
    provider_response_hash: str = ""
    usage_receipt_hash: str = ""
    repository_commit: str = ""
    task_hash: str = ""
    timeout_seconds: float = 0.0

    def usage_receipt(self) -> UsageReceipt | None:
        if not self.provider_observed or not self.usage_receipt_hash:
            return None
        return UsageReceipt(
            task_id=self.task_id,
            arm_id=self.arm_id,
            repetition=self.repetition,
            cache_mode=self.cache_mode,
            provider=self.provider,
            request_id_hash=self.request_id_hash,
            provider_response_hash=self.provider_response_hash,
            fresh_input_tokens=self.fresh_input_tokens,
            cached_input_tokens=self.cached_input_tokens,
            output_tokens=self.output_tokens,
            reasoning_tokens=self.reasoning_tokens,
            quota_cost=float(self.quota_cost or 0.0),
            hardware_hash=self.hardware_hash,
            receipt_hash=self.usage_receipt_hash,
        )


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


def _is_exact_hex(value: str, lengths: tuple[int, ...]) -> bool:
    return len(value) in lengths and all(ch in "0123456789abcdef" for ch in value)


def _is_placeholder(value: str) -> bool:
    lowered = value.strip().casefold()
    return not lowered or any(marker in lowered for marker in ("pin-", "replace-", "placeholder", "<", ">"))


def _memory_bytes() -> int:
    try:
        pages = int(os.sysconf("SC_PHYS_PAGES"))
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        return max(0, pages * page_size)
    except (AttributeError, OSError, TypeError, ValueError):
        return 0


def _current_hardware_identity() -> HardwareIdentity:
    return HardwareIdentity(
        os=platform.system().casefold() or os.name,
        architecture=platform.machine().casefold() or "unknown",
        cpu=platform.processor() or platform.machine() or "unknown",
        logical_cores=max(1, int(os.cpu_count() or 1)),
        memory_bytes=_memory_bytes(),
        accelerator=os.environ.get("SIGNALBENCH_ACCELERATOR", ""),
        runtime=f"python-{platform.python_version()}",
    )


_HOST_ENV_ALLOWLIST = (
    "PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT",
    "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "VIRTUAL_ENV", "SSL_CERT_FILE", "SSL_CERT_DIR",
)
_RESERVED_ENV_KEYS = frozenset({
    "SIGNALBENCH_REQUEST", "SIGNALBENCH_OUTPUT", "SIGNALBENCH_WORKSPACE",
    "SIGNALBENCH_AGENT_RESULT", "SIGNALBENCH_PRODUCT", "SIGNALBENCH_PROFILE",
})


def _valid_env_name(value: str) -> bool:
    return bool(value) and (value[0].isalpha() or value[0] == "_") and all(
        ch.isalnum() or ch == "_" for ch in value
    )


def _safe_environment(
    explicit: Mapping[str, str] | None = None,
    inherit: Iterable[str] = (),
    *,
    reserved: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environment = {
        key: value
        for key in _HOST_ENV_ALLOWLIST
        if (value := os.environ.get(key)) is not None
    }
    explicit = dict(explicit or {})
    for key in tuple(inherit):
        if not isinstance(key, str) or not _valid_env_name(key):
            raise SignalBenchError(f"invalid inherited environment key: {key!r}")
        if key in _RESERVED_ENV_KEYS:
            raise SignalBenchError(f"reserved inherited environment key: {key}")
        if key in explicit:
            raise SignalBenchError(f"environment key cannot be both explicit and inherited: {key}")
        if key not in os.environ:
            raise SignalBenchError(f"missing inherited environment key: {key}")
        environment[key] = os.environ[key]
    for key, value in explicit.items():
        if not isinstance(key, str) or not _valid_env_name(key):
            raise SignalBenchError(f"invalid explicit environment key: {key!r}")
        if key in _RESERVED_ENV_KEYS:
            raise SignalBenchError(f"reserved explicit environment key: {key}")
        if not isinstance(value, str):
            raise SignalBenchError(f"environment value must be text: {key}")
        environment[key] = value
    environment.update(dict(reserved or {}))
    return environment


def _safe_git_environment() -> dict[str, str]:
    return _safe_environment(reserved={
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    })


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
    def pair_identity(cls, task: TaskSpec, arm: ArmSpec, *, cache_mode: str, hardware_hash: str = "") -> dict[str, Any]:
        return {
            "task_hash": cls.task_hash(task),
            "repository_tree": task.repository_tree,
            "repository_commit": task.repository_commit,
            "arm_version": arm.version,
            "model": arm.model,
            "reasoning": arm.reasoning,
            "context_window": arm.context_window,
            "hardware_hash": hardware_hash,
            "prompt_hash": sha256_bytes(task.prompt.encode()),
            "verifier_hash": cls.verifier_hash(task),
            "permissions_hash": cls.permissions_hash(task),
            "timeout_seconds": task.timeout_seconds,
            "cache_mode": cache_mode,
        }



class SignalBenchRunner:
    """External-arm benchmark runner with frozen tasks and raw artifacts.

    Arms communicate through a small JSON request/result contract. Competitor
    source is never imported into Syntavra; each product is installed and
    executed independently by its adapter command.
    """

    def __init__(self, root: Path, *, seed: int = 1337, hardware_identity: HardwareIdentity | None = None):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.seed = seed
        self.hardware_identity = hardware_identity or _current_hardware_identity()
        self.hardware_hash = self.hardware_identity.digest

    @staticmethod
    def load_tasks(path: Path) -> list[TaskSpec]:
        value = json.loads(path.read_text(encoding="utf-8"))
        rows = value if isinstance(value, list) else value.get("tasks", [])
        return [TaskSpec(**{**row, "verifier": tuple(row["verifier"]), "permissions": tuple(row.get("permissions", ("read", "write", "execute")))}) for row in rows]

    @staticmethod
    def load_arms(path: Path) -> list[ArmSpec]:
        value = json.loads(path.read_text(encoding="utf-8"))
        rows = value if isinstance(value, list) else value.get("arms", [])
        return [ArmSpec(**{**row, "command": tuple(row["command"]), "inherit_environment": tuple(row.get("inherit_environment", ()))}) for row in rows]

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
    def _frozen_repository_reasons(task: TaskSpec) -> list[str]:
        reasons: list[str] = []
        declared_tree = str(task.repository_tree or "").casefold()
        declared_commit = str(task.repository_commit or "").casefold()
        if not _is_exact_hex(declared_tree, (40, 64)):
            reasons.append("repository-tree-not-exact")
        if not _is_exact_hex(declared_commit, (40, 64)):
            reasons.append("repository-commit-not-exact")
        if reasons:
            return reasons
        try:
            source = Path(task.repository).resolve(strict=True)
        except (OSError, RuntimeError):
            return ["repository-missing"]
        if not source.is_dir():
            return ["repository-not-directory"]
        environment = _safe_git_environment()
        try:
            actual_commit = subprocess.check_output(
                ["git", "-C", str(source), "rev-parse", "HEAD"],
                stderr=subprocess.STDOUT, text=True, env=environment,
            ).strip().casefold()
            actual_tree = subprocess.check_output(
                ["git", "-C", str(source), "rev-parse", "HEAD^{tree}"],
                stderr=subprocess.STDOUT, text=True, env=environment,
            ).strip().casefold()
            status = subprocess.check_output(
                ["git", "-C", str(source), "status", "--porcelain", "--untracked-files=all"],
                stderr=subprocess.STDOUT, text=True, env=environment,
            )
            staged = subprocess.check_output(
                ["git", "-C", str(source), "ls-files", "--stage"],
                stderr=subprocess.STDOUT, text=True, env=environment,
            )
        except (OSError, subprocess.CalledProcessError):
            return ["repository-not-git"]
        if actual_commit != declared_commit:
            reasons.append("repository-commit-mismatch")
        if actual_tree != declared_tree:
            reasons.append("repository-tree-mismatch")
        if status.strip():
            reasons.append("repository-worktree-dirty")
        if any(line.startswith("160000 ") for line in staged.splitlines()):
            reasons.append("repository-submodules-unsupported")
        return reasons

    def validate_product(self, tasks: Iterable[TaskSpec], arms: Iterable[ArmSpec]) -> dict[str, Any]:
        tasks = list(tasks)
        arms = list(arms)
        base = self.validate(tasks, arms)
        reasons = list(base["reasons"])
        for task in tasks:
            reasons.extend(f"task:{task.task_id}:{reason}" for reason in self._frozen_repository_reasons(task))
        for arm in arms:
            for field, value in (("version", arm.version), ("model", arm.model), ("reasoning", arm.reasoning)):
                if _is_placeholder(str(value)):
                    reasons.append(f"arm:{arm.arm_id}:{field}-not-exact")
        for arm in arms:
            explicit_keys = set(arm.environment)
            inherited_keys = list(arm.inherit_environment)
            if len(inherited_keys) != len(set(inherited_keys)):
                reasons.append(f"arm:{arm.arm_id}:duplicate-inherited-environment")
            for key in [*explicit_keys, *inherited_keys]:
                if not isinstance(key, str) or not _valid_env_name(key):
                    reasons.append(f"arm:{arm.arm_id}:environment-key-invalid")
                elif key in _RESERVED_ENV_KEYS:
                    reasons.append(f"arm:{arm.arm_id}:reserved-environment-key")
            for key, value in arm.environment.items():
                if not isinstance(value, str):
                    reasons.append(f"arm:{arm.arm_id}:environment-value-invalid:{key}")
            for key in inherited_keys:
                if key in explicit_keys:
                    reasons.append(f"arm:{arm.arm_id}:environment-inherit-conflict:{key}")
                elif key not in os.environ:
                    reasons.append(f"arm:{arm.arm_id}:inherited-environment-missing:{key}")
        return {"ok": not reasons, "reasons": list(dict.fromkeys(reasons)), "tasks": len(tasks), "arms": len(arms), "frozen_product": True}

    @staticmethod
    def _copy_repository(task: TaskSpec, source: Path, destination: Path) -> None:
        environment = _safe_git_environment()
        subprocess.run(
            ["git", "clone", "--no-local", "--no-checkout", "--quiet", "--", str(source), str(destination)],
            env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, check=True,
        )
        subprocess.run(
            ["git", "-C", str(destination), "-c", f"core.hooksPath={os.devnull}",
             "checkout", "--detach", "--quiet", task.repository_commit],
            env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, check=True,
        )
        subprocess.run(
            ["git", "-C", str(destination), "config", "--local", "core.hooksPath", os.devnull],
            env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, check=True,
        )
        subprocess.run(
            ["git", "-C", str(destination), "remote", "remove", "origin"],
            env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, check=True,
        )
        actual_commit = subprocess.check_output(
            ["git", "-C", str(destination), "rev-parse", "HEAD"], text=True, env=environment,
        ).strip().casefold()
        actual_tree = subprocess.check_output(
            ["git", "-C", str(destination), "rev-parse", "HEAD^{tree}"], text=True, env=environment,
        ).strip().casefold()
        status = subprocess.check_output(
            ["git", "-C", str(destination), "status", "--porcelain", "--untracked-files=all"],
            text=True, env=environment,
        )
        if actual_commit != task.repository_commit.casefold():
            raise SignalBenchError("workspace-repository-commit-mismatch")
        if actual_tree != task.repository_tree.casefold():
            raise SignalBenchError("workspace-repository-tree-mismatch")
        if status.strip():
            raise SignalBenchError("workspace-not-clean-after-checkout")
        root = destination.resolve()
        for candidate in destination.rglob("*"):
            relative = candidate.relative_to(destination)
            if ".git" in relative.parts or not candidate.is_symlink():
                continue
            try:
                candidate.resolve(strict=False).relative_to(root)
            except ValueError as exc:
                raise SignalBenchError(f"workspace-symlink-escapes-root:{relative.as_posix()}") from exc

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
        reasons = self.validate_product([task], [arm])["reasons"]
        run_id = f"run-{task.task_id}-{arm.arm_id}-{repetition}-{uuid.uuid4().hex[:8]}"
        artifact_dir = self.root / "runs" / run_id
        artifact_dir.mkdir(parents=True, exist_ok=False)
        if reasons:
            return self._failure(run_id, task, arm, repetition, cache_mode, artifact_dir, ";".join(reasons))
        source = Path(task.repository).resolve(strict=True)
        workspace = artifact_dir / "workspace"
        self._copy_repository(task, source, workspace)
        identity = SignalBenchProtocol.pair_identity(task, arm, cache_mode=cache_mode, hardware_hash=self.hardware_hash)
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
        environment = _safe_environment(
            arm.environment,
            arm.inherit_environment,
            reserved={
                "SIGNALBENCH_REQUEST": str(request_path),
                "SIGNALBENCH_OUTPUT": str(result_path),
                "SIGNALBENCH_WORKSPACE": str(workspace),
            },
        )
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
                    env=_safe_environment(reserved={"SIGNALBENCH_WORKSPACE": str(workspace)}),
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

        raw_metrics = arm_result.get("metrics", {}) if isinstance(arm_result.get("metrics", {}), dict) else {}
        provider_receipt = arm_result.get("provider_receipt") if isinstance(arm_result.get("provider_receipt"), dict) else {}
        expected_arm_identity = {
            "arm_id": arm.arm_id,
            "version": arm.version,
            "model": arm.model,
            "reasoning": arm.reasoning,
            "context_window": arm.context_window,
        }
        arm_identity = arm_result.get("arm_identity") if isinstance(arm_result.get("arm_identity"), dict) else {}
        arm_identity_matches = arm_identity == expected_arm_identity
        provider_response_hash = str(provider_receipt.get("response_hash") or "").casefold()
        request_id = str(provider_receipt.get("request_id") or "")
        request_id_hash = sha256_bytes(request_id.encode()) if request_id else ""
        try:
            fresh_input_tokens = int(raw_metrics.get("fresh_input_tokens", 0))
            cached_input_tokens = int(raw_metrics.get("cached_input_tokens", 0))
            output_tokens = int(raw_metrics.get("output_tokens", 0))
            provider_reasoning_tokens = int(raw_metrics.get("reasoning_tokens", 0))
            quota_cost = float(raw_metrics["quota_cost"]) if raw_metrics.get("quota_cost") is not None else None
        except (TypeError, ValueError, OverflowError):
            fresh_input_tokens = cached_input_tokens = output_tokens = provider_reasoning_tokens = 0
            quota_cost = None
        usage_values_valid = all(value >= 0 for value in (fresh_input_tokens, cached_input_tokens, output_tokens, provider_reasoning_tokens))
        provider_observed = bool(
            provider_receipt.get("provider")
            and str(provider_receipt.get("model") or "") == arm.model
            and request_id
            and _is_exact_hex(provider_response_hash, (64,))
            and quota_cost is not None and quota_cost > 0
            and usage_values_valid
            and arm_identity_matches
        )
        if not arm_identity_matches:
            error = error or "arm-identity-mismatch"
        elif str(provider_receipt.get("model") or "") != arm.model:
            error = error or "provider-model-mismatch"
        elif provider_response_hash and not _is_exact_hex(provider_response_hash, (64,)):
            error = error or "provider-response-hash-invalid"
        elif not usage_values_valid:
            error = error or "negative-provider-usage"
        elif not provider_observed:
            error = error or "missing-provider-observed-receipt"
        success = exit_code == 0 and verifier_success and bool(arm_result.get("success", True)) and provider_observed
        verified_work = task.expected_work if success else 0.0
        sealed_usage = None
        if provider_observed and quota_cost is not None:
            sealed_usage = UsageReceipt.seal(
                task_id=task.task_id,
                arm_id=arm.arm_id,
                repetition=repetition,
                cache_mode=cache_mode,
                provider=str(provider_receipt.get("provider") or ""),
                request_id_hash=request_id_hash,
                provider_response_hash=provider_response_hash,
                fresh_input_tokens=fresh_input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=provider_reasoning_tokens,
                quota_cost=quota_cost,
                hardware_hash=self.hardware_hash,
            )
        result = RunResult(
            run_id=run_id,
            task_id=task.task_id,
            arm_id=arm.arm_id,
            repetition=repetition,
            success=success,
            verifier_success=verifier_success,
            verified_work=verified_work,
            wall_seconds=wall,
            exit_code=exit_code,
            fresh_input_tokens=fresh_input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=provider_reasoning_tokens,
            quota_cost=quota_cost,
            model_turns=int(raw_metrics.get("model_turns", 0)),
            tool_calls=int(raw_metrics.get("tool_calls", 0)),
            wait_calls=int(raw_metrics.get("wait_calls", 0)),
            compactions=int(raw_metrics.get("compactions", 0)),
            security_regressions=int(raw_metrics.get("security_regressions", 0)),
            verifier_skips=int(raw_metrics.get("verifier_skips", 0)),
            repository_tree=task.repository_tree,
            prompt_hash=identity["prompt_hash"],
            verifier_hash=identity["verifier_hash"],
            permissions_hash=identity["permissions_hash"],
            cache_mode=cache_mode,
            artifact_dir=str(artifact_dir),
            error=error,
            provider_observed=provider_observed,
            provider=str(provider_receipt.get("provider") or ""),
            model=arm.model,
            request_id_hash=request_id_hash,
            provider_receipt_hash=sha256_bytes(canonical_json(provider_receipt)) if provider_receipt else "",
            arm_version=arm.version,
            reasoning=arm.reasoning,
            context_window=arm.context_window,
            hardware_hash=self.hardware_hash,
            provider_response_hash=provider_response_hash,
            usage_receipt_hash=sealed_usage.receipt_hash if sealed_usage else "",
            repository_commit=task.repository_commit,
            task_hash=identity["task_hash"],
            timeout_seconds=float(task.timeout_seconds),
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
        validation = self.validate_product(tasks, arms)
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
            "hardware": {**asdict(self.hardware_identity), "digest": self.hardware_hash},
            "results": [asdict(result) for result in results],
        }
        output["result_hash"] = sha256_bytes(canonical_json(output))
        atomic_write_json(self.root / "results.json", output, mode=0o600)
        return output

    @staticmethod
    def compare(results: Iterable[RunResult], *, baseline_arm: str, candidate_arm: str) -> dict[str, Any]:
        rows = list(results)
        receipts = [receipt for row in rows if (receipt := row.usage_receipt()) is not None]
        result = HardenedSignalBench.compare(
            rows,
            baseline_arm=baseline_arm,
            candidate_arm=candidate_arm,
            receipts=receipts,
            minimum_pairs=10,
            require_receipts=True,
        )
        keyed = {(row.task_id, row.repetition, row.cache_mode, row.arm_id): row for row in rows}
        pair_keys = sorted({(task, repetition, cache) for task, repetition, cache, arm in keyed if arm == baseline_arm})
        observed = sum(
            1 for task, repetition, cache in pair_keys
            if (base := keyed.get((task, repetition, cache, baseline_arm))) is not None
            and (candidate := keyed.get((task, repetition, cache, candidate_arm))) is not None
            and base.provider_observed and candidate.provider_observed
        )
        result["valid_pairs"] = result["successful_equal_work_pairs"]
        result["invalid_pairs"] = result["invalid"]
        result["provider_observed_pairs"] = observed
        result["provider_unobserved_pairs"] = max(0, result["matched_pairs"] - observed)
        result["median_efficiency_ratio"] = result["median_success_pair_ratio"]
        result["comparison_authority"] = "HardenedSignalBench.compare"
        return result



def load_results(path: Path) -> list[RunResult]:
    value = json.loads(path.read_text(encoding="utf-8"))
    rows = value if isinstance(value, list) else value.get("results", [])
    return [RunResult(**row) for row in rows]
