from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from .execution_sandbox import ExecutionReceipt, NativeSandboxBroker, SandboxPolicy
from .inference_skip_cache import (
    InferenceSkipCache,
    InferenceSkipIdentity,
    RepositoryFingerprint,
    build_identity,
    repository_state_fingerprint,
)
from .retry_economics import RetryEconomicsGovernor, workspace_state_fingerprint


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AgentMode(StrEnum):
    PLAN_ONLY = "plan-only"
    REVIEW_REQUIRED = "review-required"
    SAFE_AUTONOMOUS = "safe-autonomous"
    HEADLESS = "headless"
    CI = "ci"
    REMOTE = "remote"


class AgentState(StrEnum):
    CREATED = "created"
    UNDERSTANDING = "understanding"
    PLANNING = "planning"
    RETRIEVING = "retrieving"
    AWAITING_AUTHORIZATION = "awaiting-authorization"
    EXECUTING = "executing"
    OBSERVING = "observing"
    VERIFYING = "verifying"
    DIAGNOSING = "diagnosing"
    REPAIRING = "repairing"
    RETESTING = "retesting"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    ROLLED_BACK = "rolled-back"


@dataclass(frozen=True)
class AgentTask:
    instruction: str
    verifier: tuple[str, ...]
    mode: AgentMode = AgentMode.REVIEW_REQUIRED
    max_attempts: int = 3
    timeout_seconds: float = 900.0
    token_budget: int | None = None
    cost_budget: float | None = None
    retain_workspace: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PatchProposal:
    patch: str
    rationale: str = ""
    estimated_tokens: int = 0
    estimated_cost: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class PatchProvider(Protocol):
    def propose(
        self,
        task: AgentTask,
        context: Mapping[str, Any],
        previous_failure: Mapping[str, Any] | None,
    ) -> PatchProposal: ...


@dataclass(frozen=True)
class AgentAttempt:
    number: int
    patch_sha256: str
    patch_applied: bool
    verifier: ExecutionReceipt | None
    failure_fingerprint: str
    rationale: str
    tokens: int
    cost: float
    state: AgentState


@dataclass(frozen=True)
class AgentRunReceipt:
    run_id: str
    task: AgentTask
    state: AgentState
    started_at: str
    finished_at: str
    duration_ms: float
    workspace: str
    attempts: tuple[AgentAttempt, ...]
    total_tokens: int
    total_cost: float
    changed_files: tuple[str, ...]
    final_diff: str
    rollback_complete: bool
    stop_reason: str
    context: dict[str, Any]

    @property
    def ok(self) -> bool:
        return self.state == AgentState.COMPLETED


class CallablePatchProvider:
    def __init__(self, function: Callable[[AgentTask, Mapping[str, Any], Mapping[str, Any] | None], PatchProposal]):
        self.function = function

    def propose(self, task: AgentTask, context: Mapping[str, Any], previous_failure: Mapping[str, Any] | None) -> PatchProposal:
        return self.function(task, context, previous_failure)


class AutonomousCodingAgent:
    """Bounded mutate/test/repair loop with isolated workspaces and exact rollback.

    The model/provider is injected through `PatchProvider`. The runtime owns graph
    retrieval, patch application, test execution, anti-loop decisions and receipts.
    Exact-state inference reuse is admitted only for a single complete verifier plan;
    cached patches are still applied and verified before a run may succeed.
    """

    def __init__(
        self,
        project: Path,
        state_root: Path,
        *,
        graph: Any | None = None,
        memory: Any | None = None,
        sandbox: NativeSandboxBroker | None = None,
        inference_skip_cache: InferenceSkipCache | None = None,
    ):
        self.project = project.resolve(strict=True)
        self.state_root = state_root.resolve(strict=False)
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.graph = graph
        self.memory = memory
        self.sandbox = sandbox or NativeSandboxBroker(self.state_root)
        self.inference_skip_cache = inference_skip_cache or InferenceSkipCache(self.state_root / "inference-skip.sqlite3")

    def _workspace(self) -> tuple[Path, bool]:
        root = self.state_root / "agent-workspaces"
        root.mkdir(parents=True, exist_ok=True)
        destination = Path(tempfile.mkdtemp(prefix="run-", dir=root))
        if (self.project / ".git").exists() and shutil.which("git"):
            result = subprocess.run(
                ["git", "-C", str(self.project), "worktree", "add", "--detach", str(destination), "HEAD"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                return destination, True
            shutil.rmtree(destination, ignore_errors=True)
            destination = Path(tempfile.mkdtemp(prefix="run-", dir=root))
        shutil.copytree(
            self.project,
            destination,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(".git", ".syntavra", ".venv", "venv", "node_modules", "dist", "build", "__pycache__"),
        )
        return destination, False

    def _cleanup(self, workspace: Path, git_worktree: bool) -> bool:
        if git_worktree and shutil.which("git"):
            result = subprocess.run(
                ["git", "-C", str(self.project), "worktree", "remove", "--force", str(workspace)],
                capture_output=True,
                check=False,
            )
            subprocess.run(["git", "-C", str(self.project), "worktree", "prune"], capture_output=True, check=False)
            return result.returncode == 0
        shutil.rmtree(workspace, ignore_errors=True)
        return not workspace.exists()

    def _context(self, task: AgentTask) -> dict[str, Any]:
        symbols = self.graph.query(task.instruction, limit=20) if self.graph is not None else []
        return {
            "instruction": task.instruction,
            "project": str(self.project),
            "mode": task.mode.value,
            "semantic_results": symbols,
            "budgets": {"tokens": task.token_budget, "cost": task.cost_budget, "attempts": task.max_attempts},
        }

    @staticmethod
    def _failure(receipt: ExecutionReceipt | None, apply_error: str = "") -> dict[str, Any]:
        if apply_error:
            text = apply_error
            return {"kind": "patch-apply", "text": text, "fingerprint": _digest(text)}
        if receipt is None:
            return {"kind": "unknown", "text": "no verifier receipt", "fingerprint": _digest("no verifier receipt")}
        text = "\n".join((receipt.stderr[-8000:], receipt.stdout[-8000:])).strip()
        normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        return {
            "kind": "timeout" if receipt.timed_out else "verifier",
            "exit_code": receipt.exit_code,
            "text": normalized,
            "fingerprint": _digest(f"{receipt.exit_code}\0{normalized}"),
        }

    @staticmethod
    def _diff(workspace: Path) -> tuple[str, tuple[str, ...]]:
        if (workspace / ".git").exists() and shutil.which("git"):
            diff = subprocess.run(
                ["git", "-C", str(workspace), "diff", "--binary", "--no-ext-diff"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout
            names = subprocess.run(
                ["git", "-C", str(workspace), "diff", "--name-only"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.splitlines()
            return diff, tuple(sorted(set(names)))
        return "", ()

    def _apply(self, workspace: Path, patch: str, timeout: float) -> ExecutionReceipt:
        policy = SandboxPolicy(workspace=workspace, timeout_seconds=min(timeout, 120.0), strict_native=False)
        return self.sandbox.run(
            ("git", "apply", "--whitespace=nowarn", "-"),
            policy=policy,
            input_bytes=patch.encode("utf-8"),
        )

    def _record_memory(self, session_id: str | None, event_type: str, payload: Mapping[str, Any]) -> None:
        if not self.memory:
            return
        try:
            if session_id:
                self.memory.append(session_id, event_type, dict(payload))
        except Exception:
            # Agent execution must not claim memory persistence; callers receive the
            # result through the run receipt even when optional memory is unavailable.
            return

    @staticmethod
    def _single_complete_verifier(task: AgentTask) -> bool:
        discovered = task.metadata.get("verifier_discovery")
        if not isinstance(discovered, Sequence) or isinstance(discovered, (str, bytes)):
            return False
        return len(discovered) == 1

    def _inference_identity(
        self,
        task: AgentTask,
        repository_fingerprint: RepositoryFingerprint,
    ) -> InferenceSkipIdentity | None:
        if not self._single_complete_verifier(task) or not repository_fingerprint.cacheable:
            return None
        policy_material = {
            "security_policy_fingerprint": task.metadata.get("security_policy_fingerprint", ""),
            "provider_policy_fingerprint": task.metadata.get("provider_policy_fingerprint", ""),
            "verifier_discovery": task.metadata.get("verifier_discovery"),
        }
        policy_fingerprint = _digest(json.dumps(policy_material, ensure_ascii=False, sort_keys=True, default=str))
        try:
            return build_identity(
                project=self.project,
                repository_fingerprint=repository_fingerprint,
                instruction=task.instruction,
                verifier=task.verifier,
                mode=task.mode.value,
                policy_fingerprint=policy_fingerprint,
            )
        except ValueError:
            return None

    @staticmethod
    def _verification_hash(task: AgentTask, receipt: ExecutionReceipt) -> str:
        payload = {
            "verifier": list(task.verifier),
            "exit_code": receipt.exit_code,
            "timed_out": receipt.timed_out,
            "stdout_sha256": _digest(receipt.stdout),
            "stderr_sha256": _digest(receipt.stderr),
        }
        return _digest(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    def execute(
        self,
        task: AgentTask,
        provider: PatchProvider,
        *,
        session_id: str | None = None,
        authorized: bool = False,
    ) -> AgentRunReceipt:
        if not task.instruction.strip() or not task.verifier:
            raise ValueError("task instruction and verifier argv are required")
        if task.max_attempts < 1 or task.max_attempts > 20:
            raise ValueError("max_attempts must be between 1 and 20")
        started_at = _now()
        started = time.monotonic()
        source_fingerprint = repository_state_fingerprint(self.project)
        inference_identity = self._inference_identity(task, source_fingerprint)
        workspace, git_worktree = self._workspace()
        context = self._context(task)
        context["inference_skip"] = {
            "eligible": inference_identity is not None,
            "repository_fingerprint_method": source_fingerprint.method,
            "repository_cacheable": source_fingerprint.cacheable,
            "repository_cache_reason": source_fingerprint.reason,
            "hit": False,
        }
        run_id = "sha256:" + _digest(json.dumps({"task": task.instruction, "workspace": str(workspace), "started": started_at}, sort_keys=True))
        self._record_memory(session_id, "agent-run-started", {"run_id": run_id, "task": task.instruction, "mode": task.mode.value})

        if task.mode == AgentMode.PLAN_ONLY:
            cleanup = True if task.retain_workspace else self._cleanup(workspace, git_worktree)
            return AgentRunReceipt(
                run_id=run_id,
                task=task,
                state=AgentState.COMPLETED,
                started_at=started_at,
                finished_at=_now(),
                duration_ms=round((time.monotonic() - started) * 1000, 3),
                workspace=str(workspace),
                attempts=(),
                total_tokens=0,
                total_cost=0.0,
                changed_files=(),
                final_diff="",
                rollback_complete=cleanup,
                stop_reason="plan-only",
                context=context,
            )
        if task.mode == AgentMode.REVIEW_REQUIRED and not authorized:
            cleanup = True if task.retain_workspace else self._cleanup(workspace, git_worktree)
            return AgentRunReceipt(
                run_id=run_id,
                task=task,
                state=AgentState.BLOCKED,
                started_at=started_at,
                finished_at=_now(),
                duration_ms=round((time.monotonic() - started) * 1000, 3),
                workspace=str(workspace),
                attempts=(),
                total_tokens=0,
                total_cost=0.0,
                changed_files=(),
                final_diff="",
                rollback_complete=cleanup,
                stop_reason="explicit authorization required",
                context=context,
            )

        seen_patches: set[str] = set()
        retry_governor = RetryEconomicsGovernor()
        attempts: list[AgentAttempt] = []
        previous_failure: dict[str, Any] | None = None
        total_tokens = 0
        total_cost = 0.0
        final_state = AgentState.FAILED
        stop_reason = "attempt limit reached"

        for number in range(1, task.max_attempts + 1):
            current_diff, current_changed_files = self._diff(workspace)
            context = {
                **context,
                "workspace": str(workspace),
                "attempt": number,
                "current_diff": current_diff[-120_000:],
                "changed_files": current_changed_files,
                "previous_failure": previous_failure,
            }

            workspace_fingerprint = workspace_state_fingerprint(workspace)
            retry_decision = retry_governor.assess(previous_failure, workspace_fingerprint)
            if not retry_decision.allow_provider:
                stop_reason = retry_decision.reason
                break

            cached_key = ""
            cache_hit = None
            if number == 1 and previous_failure is None and not current_diff and inference_identity is not None:
                cache_hit = self.inference_skip_cache.lookup(inference_identity)
            if cache_hit is not None:
                cached_key = cache_hit.identity.key
                context["inference_skip"] = {
                    **dict(context.get("inference_skip") or {}),
                    "hit": True,
                    "cache_key": cached_key,
                    "verification_hash": cache_hit.verification_hash,
                    "provider_calls_avoided": 1,
                }
                proposal = PatchProposal(
                    patch=cache_hit.patch,
                    rationale=f"verified exact inference-skip replay: {cache_hit.rationale}",
                    estimated_tokens=0,
                    estimated_cost=0.0,
                    metadata={
                        "inference_skipped": True,
                        "inference_skip_cache_key": cached_key,
                        "verification_hash": cache_hit.verification_hash,
                    },
                )
            else:
                proposal = provider.propose(task, context, previous_failure)
            patch_hash = _digest(proposal.patch)
            if not proposal.patch.strip():
                stop_reason = "empty patch proposal"
                break
            if patch_hash in seen_patches:
                stop_reason = "anti-loop: repeated patch"
                break
            seen_patches.add(patch_hash)
            total_tokens += max(0, int(proposal.estimated_tokens))
            total_cost += max(0.0, float(proposal.estimated_cost))
            if task.token_budget is not None and total_tokens > task.token_budget:
                stop_reason = "token budget exceeded"
                break
            if task.cost_budget is not None and total_cost > task.cost_budget:
                stop_reason = "cost budget exceeded"
                break

            apply_receipt = self._apply(workspace, proposal.patch, task.timeout_seconds)
            if not apply_receipt.ok:
                if cached_key:
                    self.inference_skip_cache.invalidate(cached_key, "cached-patch-apply-failed")
                previous_failure = self._failure(None, apply_receipt.stderr or apply_receipt.stdout)
                fingerprint = str(previous_failure["fingerprint"])
                attempts.append(
                    AgentAttempt(number, patch_hash, False, apply_receipt, fingerprint, proposal.rationale, proposal.estimated_tokens, proposal.estimated_cost, AgentState.DIAGNOSING)
                )
                continue

            verifier = self.sandbox.run(
                task.verifier,
                policy=SandboxPolicy(workspace=workspace, timeout_seconds=task.timeout_seconds, strict_native=False),
            )
            if verifier.ok:
                attempts.append(
                    AgentAttempt(number, patch_hash, True, verifier, "", proposal.rationale, proposal.estimated_tokens, proposal.estimated_cost, AgentState.COMPLETED)
                )
                if inference_identity is not None and not cached_key:
                    self.inference_skip_cache.record_verified(
                        inference_identity,
                        patch=proposal.patch,
                        rationale=proposal.rationale,
                        verification_hash=self._verification_hash(task, verifier),
                        verification_complete=True,
                    )
                    context["inference_skip"] = {
                        **dict(context.get("inference_skip") or {}),
                        "recorded_verified": True,
                        "cache_key": inference_identity.key,
                    }
                final_state = AgentState.COMPLETED
                stop_reason = "verifier passed after exact inference-skip" if cached_key else "verifier passed"
                break
            if cached_key:
                self.inference_skip_cache.invalidate(cached_key, "cached-patch-verifier-failed")
            previous_failure = self._failure(verifier)
            fingerprint = str(previous_failure["fingerprint"])
            attempts.append(
                AgentAttempt(number, patch_hash, True, verifier, fingerprint, proposal.rationale, proposal.estimated_tokens, proposal.estimated_cost, AgentState.REPAIRING)
            )
            context = {**context, "previous_failure": previous_failure}

        final_diff, changed_files = self._diff(workspace)
        context = {**context, "retry_economics": retry_governor.summary()}
        rollback_complete = True
        if final_state != AgentState.COMPLETED or not task.retain_workspace:
            rollback_complete = self._cleanup(workspace, git_worktree)
            if final_state != AgentState.COMPLETED and rollback_complete:
                final_state = AgentState.ROLLED_BACK
        receipt = AgentRunReceipt(
            run_id=run_id,
            task=task,
            state=final_state,
            started_at=started_at,
            finished_at=_now(),
            duration_ms=round((time.monotonic() - started) * 1000, 3),
            workspace=str(workspace),
            attempts=tuple(attempts),
            total_tokens=total_tokens,
            total_cost=round(total_cost, 8),
            changed_files=changed_files,
            final_diff=final_diff,
            rollback_complete=rollback_complete,
            stop_reason=stop_reason,
            context=context,
        )
        destination = self.state_root / "agent-receipts" / f"{run_id.split(':', 1)[1]}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(asdict(receipt), ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        self._record_memory(session_id, "agent-run-finished", {"run_id": run_id, "state": receipt.state.value, "stop_reason": stop_reason})
        return receipt


__all__ = [
    "AgentAttempt",
    "AgentMode",
    "AgentRunReceipt",
    "AgentState",
    "AgentTask",
    "AutonomousCodingAgent",
    "CallablePatchProvider",
    "PatchProposal",
    "PatchProvider",
]
