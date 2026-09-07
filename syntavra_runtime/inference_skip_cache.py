from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .util import stable_project_id


_SCHEMA_VERSION = 1
_CACHE_VERSION = "inference-skip-v1"
_EXCLUDED_DIRS = {".git", ".syntavra", ".venv", "venv", "node_modules", "dist", "build", "__pycache__"}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _run_git(project: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(project), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )


@dataclass(frozen=True)
class RepositoryFingerprint:
    digest: str
    method: str
    cacheable: bool
    reason: str = ""


@dataclass(frozen=True)
class InferenceSkipIdentity:
    key: str
    project_id: str
    repository_fingerprint: str
    instruction_hash: str
    verifier_hash: str
    policy_hash: str


@dataclass(frozen=True)
class InferenceSkipHit:
    identity: InferenceSkipIdentity
    patch: str
    patch_hash: str
    rationale: str
    verification_hash: str
    created_at: float
    hit_count: int


def repository_state_fingerprint(project: Path) -> RepositoryFingerprint:
    """Fingerprint the exact execution base and disable cache on ambiguous dirty git state.

    Syntavra's git agent creates a detached worktree at HEAD, so cache reuse on a dirty
    source worktree would be surprising even when HEAD is stable. Fail closed instead.
    Non-git repositories use a deterministic full-file tree hash with generated/state
    directories excluded exactly like the agent copy path.
    """

    project = Path(project).resolve(strict=True)
    if (project / ".git").exists():
        head = _run_git(project, "rev-parse", "HEAD")
        if head.returncode != 0:
            return RepositoryFingerprint("", "git-head", False, "git-head-unavailable")
        status = _run_git(project, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        if status.returncode != 0:
            return RepositoryFingerprint("", "git-head", False, "git-status-unavailable")
        if status.stdout:
            return RepositoryFingerprint("", "git-head", False, "dirty-source-worktree")
        digest = _sha(b"git-head\0" + head.stdout.strip())
        return RepositoryFingerprint(digest, "git-clean-head", True)

    rows: list[tuple[str, int, str]] = []
    try:
        for path in sorted(project.rglob("*"), key=lambda item: item.as_posix()):
            relative = path.relative_to(project)
            if any(part in _EXCLUDED_DIRS for part in relative.parts):
                continue
            if path.is_symlink():
                return RepositoryFingerprint("", "file-tree", False, f"symlink:{relative.as_posix()}")
            if not path.is_file():
                continue
            data = path.read_bytes()
            rows.append((relative.as_posix(), len(data), _sha(data)))
    except (OSError, PermissionError) as exc:
        return RepositoryFingerprint("", "file-tree", False, f"unreadable:{type(exc).__name__}")
    return RepositoryFingerprint(_sha(_canonical(rows)), "file-tree-sha256", True)


def build_identity(
    *,
    project: Path,
    repository_fingerprint: RepositoryFingerprint,
    instruction: str,
    verifier: Sequence[str],
    mode: str,
    policy_fingerprint: str = "",
    runtime_fingerprint: str = _CACHE_VERSION,
) -> InferenceSkipIdentity:
    if not repository_fingerprint.cacheable or not repository_fingerprint.digest:
        raise ValueError("repository state is not inference-skip cacheable")
    normalized_instruction = instruction.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized_instruction or not verifier:
        raise ValueError("instruction and verifier are required for inference-skip identity")
    project_id = stable_project_id(project)
    instruction_hash = _sha(normalized_instruction.encode("utf-8"))
    verifier_hash = _sha(_canonical([str(item) for item in verifier]))
    policy_hash = _sha(
        _canonical(
            {
                "mode": str(mode),
                "policy_fingerprint": str(policy_fingerprint),
                "runtime_fingerprint": str(runtime_fingerprint),
                "cache_version": _CACHE_VERSION,
            }
        )
    )
    key = _sha(
        _canonical(
            {
                "project_id": project_id,
                "repository_fingerprint": repository_fingerprint.digest,
                "instruction_hash": instruction_hash,
                "verifier_hash": verifier_hash,
                "policy_hash": policy_hash,
            }
        )
    )
    return InferenceSkipIdentity(
        key=key,
        project_id=project_id,
        repository_fingerprint=repository_fingerprint.digest,
        instruction_hash=instruction_hash,
        verifier_hash=verifier_hash,
        policy_hash=policy_hash,
    )


class InferenceSkipCache:
    """Exact-state cache containing only patches that already passed verification.

    This cache is deliberately semantic-free. Similar task wording, approximate repo
    state, missing verifier identity or uncertain policy state are misses, not hints.
    Replayed patches must still be applied and verified by the agent before success.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        return db

    @contextmanager
    def _db(self):
        db = self._connect()
        try:
            yield db
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS inference_skip_cache(
                    cache_key TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    repository_fingerprint TEXT NOT NULL,
                    instruction_hash TEXT NOT NULL,
                    verifier_hash TEXT NOT NULL,
                    policy_hash TEXT NOT NULL,
                    patch TEXT NOT NULL,
                    patch_hash TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    verification_hash TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_hit_at REAL,
                    hit_count INTEGER NOT NULL DEFAULT 0,
                    valid INTEGER NOT NULL DEFAULT 1,
                    invalid_reason TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS inference_skip_project_idx
                    ON inference_skip_cache(project_id, repository_fingerprint, valid);
                """
            )

    def lookup(self, identity: InferenceSkipIdentity) -> InferenceSkipHit | None:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM inference_skip_cache WHERE cache_key=? AND valid=1",
                (identity.key,),
            ).fetchone()
            if row is None:
                db.rollback()
                return None
            patch = str(row["patch"])
            patch_hash = _sha(patch.encode("utf-8"))
            if not patch.strip() or patch_hash != str(row["patch_hash"]):
                db.execute(
                    "UPDATE inference_skip_cache SET valid=0,invalid_reason=? WHERE cache_key=?",
                    ("patch-integrity-failed", identity.key),
                )
                db.commit()
                return None
            now = time.time()
            db.execute(
                "UPDATE inference_skip_cache SET hit_count=hit_count+1,last_hit_at=? WHERE cache_key=?",
                (now, identity.key),
            )
            db.commit()
            return InferenceSkipHit(
                identity=identity,
                patch=patch,
                patch_hash=patch_hash,
                rationale=str(row["rationale"]),
                verification_hash=str(row["verification_hash"]),
                created_at=float(row["created_at"]),
                hit_count=int(row["hit_count"]) + 1,
            )

    def record_verified(
        self,
        identity: InferenceSkipIdentity,
        *,
        patch: str,
        rationale: str,
        verification_hash: str,
        verification_complete: bool,
    ) -> None:
        if not verification_complete:
            raise ValueError("only completely verified results may enter inference-skip cache")
        if not patch.strip():
            raise ValueError("verified cache patch cannot be empty")
        if len(verification_hash) != 64 or any(ch not in "0123456789abcdef" for ch in verification_hash.casefold()):
            raise ValueError("verification hash must be sha256")
        patch_hash = _sha(patch.encode("utf-8"))
        now = time.time()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """
                INSERT INTO inference_skip_cache(
                    cache_key,project_id,repository_fingerprint,instruction_hash,verifier_hash,policy_hash,
                    patch,patch_hash,rationale,verification_hash,created_at,last_hit_at,hit_count,valid,invalid_reason
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0,1,'')
                ON CONFLICT(cache_key) DO UPDATE SET
                    patch=excluded.patch,patch_hash=excluded.patch_hash,rationale=excluded.rationale,
                    verification_hash=excluded.verification_hash,created_at=excluded.created_at,
                    last_hit_at=NULL,hit_count=0,valid=1,invalid_reason=''
                """,
                (
                    identity.key,
                    identity.project_id,
                    identity.repository_fingerprint,
                    identity.instruction_hash,
                    identity.verifier_hash,
                    identity.policy_hash,
                    patch,
                    patch_hash,
                    str(rationale),
                    verification_hash.casefold(),
                    now,
                    None,
                ),
            )
            db.commit()

    def invalidate(self, cache_key: str, reason: str) -> None:
        if not cache_key:
            return
        with self._db() as db:
            db.execute(
                "UPDATE inference_skip_cache SET valid=0,invalid_reason=? WHERE cache_key=?",
                (str(reason)[:512], str(cache_key)),
            )

    def inspect(self, cache_key: str) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM inference_skip_cache WHERE cache_key=?", (cache_key,)).fetchone()
        return dict(row) if row is not None else None


__all__ = [
    "InferenceSkipCache",
    "InferenceSkipHit",
    "InferenceSkipIdentity",
    "RepositoryFingerprint",
    "build_identity",
    "repository_state_fingerprint",
]
