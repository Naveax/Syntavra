from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping


_EXCLUDED_DIRS = {".git", ".syntavra", ".venv", "venv", "node_modules", "dist", "build", "__pycache__"}


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _file_digest(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _run_git(workspace: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(workspace), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )


@dataclass(frozen=True)
class WorkspaceStateFingerprint:
    digest: str
    method: str
    complete: bool
    reason: str = ""


def _tree_fingerprint(workspace: Path) -> WorkspaceStateFingerprint:
    rows: list[tuple[str, str, int, str]] = []
    try:
        for path in sorted(workspace.rglob("*"), key=lambda item: item.as_posix()):
            relative = path.relative_to(workspace)
            if any(part in _EXCLUDED_DIRS for part in relative.parts):
                continue
            name = relative.as_posix()
            if path.is_symlink():
                rows.append((name, "symlink", 0, os.readlink(path)))
                continue
            if not path.is_file():
                continue
            size, digest = _file_digest(path)
            rows.append((name, "file", size, digest))
    except (OSError, PermissionError, subprocess.SubprocessError) as exc:
        return WorkspaceStateFingerprint("", "file-tree-sha256", False, f"unreadable:{type(exc).__name__}")
    return WorkspaceStateFingerprint(_sha_bytes(_canonical(rows)), "file-tree-sha256", True)


def workspace_state_fingerprint(workspace: Path) -> WorkspaceStateFingerprint:
    """Hash the exact mutable workspace without exposing source content.

    Git worktrees use the binary diff plus exact untracked-file content hashes. A
    file-tree fallback covers copied/non-git workspaces. If exact state cannot be
    proven, callers receive ``complete=False`` and must not suppress inference based
    on approximate equivalence.
    """

    workspace = Path(workspace).resolve(strict=True)
    if (workspace / ".git").exists() and shutil.which("git"):
        try:
            diff = _run_git(workspace, "diff", "--binary", "--no-ext-diff", "--no-renames")
            status = _run_git(workspace, "status", "--porcelain=v1", "-z", "--untracked-files=all")
            untracked = _run_git(workspace, "ls-files", "--others", "--exclude-standard", "-z")
        except (OSError, subprocess.SubprocessError) as exc:
            return WorkspaceStateFingerprint("", "git-worktree-delta-v1", False, f"git-error:{type(exc).__name__}")
        if diff.returncode != 0 or status.returncode != 0 or untracked.returncode != 0:
            return WorkspaceStateFingerprint("", "git-worktree-delta-v1", False, "git-state-unavailable")

        untracked_rows: list[tuple[str, str, int, str]] = []
        try:
            for raw in untracked.stdout.split(b"\0"):
                if not raw:
                    continue
                relative_text = raw.decode("utf-8", errors="surrogateescape")
                path = workspace / relative_text
                if path.is_symlink():
                    untracked_rows.append((relative_text, "symlink", 0, os.readlink(path)))
                    continue
                if not path.is_file():
                    return WorkspaceStateFingerprint(
                        "",
                        "git-worktree-delta-v1",
                        False,
                        f"untracked-nonfile:{relative_text}",
                    )
                size, digest = _file_digest(path)
                untracked_rows.append((relative_text, "file", size, digest))
        except (OSError, PermissionError) as exc:
            return WorkspaceStateFingerprint("", "git-worktree-delta-v1", False, f"untracked-unreadable:{type(exc).__name__}")

        payload = {
            "diff_sha256": _sha_bytes(diff.stdout),
            "status_sha256": _sha_bytes(status.stdout),
            "untracked": untracked_rows,
        }
        return WorkspaceStateFingerprint(_sha_bytes(_canonical(payload)), "git-worktree-delta-v1", True)

    return _tree_fingerprint(workspace)


class RetryAction(StrEnum):
    ALLOW_INITIAL = "ALLOW_INITIAL"
    ALLOW_REPAIR = "ALLOW_REPAIR"
    ALLOW_UNCERTAIN = "ALLOW_UNCERTAIN"
    STOP_EXACT_REPEAT = "STOP_EXACT_REPEAT"


@dataclass(frozen=True)
class RetryDecision:
    action: RetryAction
    allow_provider: bool
    reason: str
    failure_fingerprint: str
    state_fingerprint: str
    state_method: str
    state_complete: bool
    pair_key: str
    prior_presentations: int
    provider_calls_avoided: int = 0

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["action"] = self.action.value
        return value


class RetryEconomicsGovernor:
    """Exact pre-inference anti-loop gate.

    A failure/state pair gets one repair presentation to the provider. If the exact
    same verifier/apply failure is observed again while the exact workspace state is
    unchanged, another provider call cannot contain new state information and is
    suppressed. Different workspace states remain eligible even when the textual
    failure fingerprint is identical, avoiding the old false-positive anti-loop.
    """

    def __init__(self, *, max_presentations_per_pair: int = 1):
        if max_presentations_per_pair < 1:
            raise ValueError("max_presentations_per_pair must be positive")
        self.max_presentations_per_pair = int(max_presentations_per_pair)
        self._presented: dict[str, int] = {}
        self._decisions: list[RetryDecision] = []
        self.provider_calls_avoided = 0

    @staticmethod
    def _pair_key(failure_fingerprint: str, state_fingerprint: str) -> str:
        return _sha_bytes(
            _canonical(
                {
                    "failure_fingerprint": failure_fingerprint,
                    "state_fingerprint": state_fingerprint,
                    "policy": "retry-economics-exact-state-v1",
                }
            )
        )

    @property
    def decisions(self) -> tuple[RetryDecision, ...]:
        return tuple(self._decisions)

    def assess(
        self,
        previous_failure: Mapping[str, Any] | None,
        state: WorkspaceStateFingerprint,
    ) -> RetryDecision:
        if previous_failure is None:
            decision = RetryDecision(
                RetryAction.ALLOW_INITIAL,
                True,
                "initial provider decision",
                "",
                state.digest,
                state.method,
                state.complete,
                "",
                0,
            )
            self._decisions.append(decision)
            return decision

        failure_fingerprint = str(previous_failure.get("fingerprint") or "")
        if not failure_fingerprint or not state.complete or not state.digest:
            decision = RetryDecision(
                RetryAction.ALLOW_UNCERTAIN,
                True,
                "exact failure/state equivalence unavailable; preserve solve quality",
                failure_fingerprint,
                state.digest,
                state.method,
                state.complete,
                "",
                0,
            )
            self._decisions.append(decision)
            return decision

        pair_key = self._pair_key(failure_fingerprint, state.digest)
        prior = self._presented.get(pair_key, 0)
        if prior >= self.max_presentations_per_pair:
            self.provider_calls_avoided += 1
            decision = RetryDecision(
                RetryAction.STOP_EXACT_REPEAT,
                False,
                "retry-economics: exact failure already presented on unchanged workspace state",
                failure_fingerprint,
                state.digest,
                state.method,
                True,
                pair_key,
                prior,
                1,
            )
            self._decisions.append(decision)
            return decision

        self._presented[pair_key] = prior + 1
        decision = RetryDecision(
            RetryAction.ALLOW_REPAIR,
            True,
            "new exact failure/state pair admitted for one repair decision",
            failure_fingerprint,
            state.digest,
            state.method,
            True,
            pair_key,
            prior,
        )
        self._decisions.append(decision)
        return decision

    def summary(self) -> dict[str, Any]:
        return {
            "policy": "retry-economics-exact-state-v1",
            "max_presentations_per_pair": self.max_presentations_per_pair,
            "provider_calls_avoided": self.provider_calls_avoided,
            "decisions": [item.to_dict() for item in self._decisions],
        }


__all__ = [
    "RetryAction",
    "RetryDecision",
    "RetryEconomicsGovernor",
    "WorkspaceStateFingerprint",
    "workspace_state_fingerprint",
]
