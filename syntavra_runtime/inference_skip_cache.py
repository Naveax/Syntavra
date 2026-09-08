from __future__ import annotations

import hashlib
import importlib.metadata as importlib_metadata
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .util import stable_project_id


_SCHEMA_VERSION = 2
_CACHE_VERSION = "inference-skip-v2"
_EXCLUDED_DIRS = {".git", ".syntavra", ".venv", "venv", "node_modules", "dist", "build", "__pycache__"}
_DEPENDENCY_FILES = (
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements.lock",
    "uv.lock",
    "poetry.lock",
    "Pipfile",
    "Pipfile.lock",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Cargo.toml",
    "Cargo.lock",
    "go.mod",
    "go.sum",
    "pom.xml",
    "gradle.lockfile",
    "build.gradle",
    "build.gradle.kts",
    "composer.json",
    "composer.lock",
)
_TOOL_SCHEMA_SOURCES = (
    "agent_runtime.py",
    "agent_retrieval.py",
    "tool_externalization.py",
    "tool_externalization_types.py",
    "model_gateway.py",
)
_SECURITY_SOURCES = (
    "autonomous_agent.py",
    "execution_sandbox.py",
    "security_scan.py",
)
_VERIFIER_SOURCES = (
    "project_model.py",
    "execution_sandbox.py",
)
_IDENTITY_COMPARE_FIELDS = (
    "repository_fingerprint",
    "verifier_hash",
    "policy_hash",
    "task_family_hash",
    "dependency_hash",
    "toolchain_hash",
    "environment_hash",
    "tool_schema_hash",
    "security_hash",
    "verifier_contract_hash",
)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_fingerprint(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) == 64 and all(char in "0123456789abcdefABCDEF" for char in text):
        return text.casefold()
    return _sha(text.encode("utf-8"))


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
    task_family_hash: str
    dependency_hash: str
    toolchain_hash: str
    environment_hash: str
    tool_schema_hash: str
    security_hash: str
    verifier_contract_hash: str


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
    """Fingerprint the exact execution base and disable cache on ambiguous dirty git state."""

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


def _dependency_fingerprint(project: Path) -> str:
    manifests: list[tuple[str, int, str]] = []
    try:
        for relative in _DEPENDENCY_FILES:
            path = project / relative
            if not path.is_file():
                continue
            data = path.read_bytes()
            manifests.append((relative, len(data), _sha(data)))
        installed: list[tuple[str, str]] = []
        for distribution in importlib_metadata.distributions():
            name = str(distribution.metadata.get("Name") or "").strip().casefold()
            version = str(distribution.version or "").strip()
            if name:
                installed.append((name, version))
        installed = sorted(set(installed))
    except Exception:
        return ""
    return _sha(_canonical({"manifests": manifests, "installed_distributions": installed}))


def _environment_fingerprint() -> str:
    try:
        payload = {
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "python_executable": str(Path(sys.executable).resolve(strict=False)),
            "sys_prefix": str(Path(sys.prefix).resolve(strict=False)),
            "base_prefix": str(Path(getattr(sys, "base_prefix", sys.prefix)).resolve(strict=False)),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "architecture": platform.architecture()[0],
            "os_name": os.name,
        }
    except Exception:
        return ""
    return _sha(_canonical(payload))


def _resolve_tool(project: Path, command: str) -> Path | None:
    candidate = Path(str(command))
    if candidate.is_absolute() or candidate.parent != Path("."):
        path = candidate if candidate.is_absolute() else project / candidate
        resolved = path.resolve(strict=False)
        return resolved if resolved.is_file() else None
    found = shutil.which(str(command))
    if not found:
        return None
    resolved = Path(found).resolve(strict=False)
    return resolved if resolved.is_file() else None


def _toolchain_fingerprint(project: Path, verifier: Sequence[str]) -> str:
    if not verifier:
        return ""
    executable = _resolve_tool(project, str(verifier[0]))
    if executable is None:
        return ""
    try:
        stat = executable.stat()
        payload = {
            "command": str(verifier[0]),
            "resolved": str(executable),
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
            "sha256": _hash_file(executable),
        }
    except (OSError, PermissionError):
        return ""
    return _sha(_canonical(payload))


def _source_group_fingerprint(names: Sequence[str]) -> str:
    root = Path(__file__).resolve().parent
    rows: list[tuple[str, int, str]] = []
    try:
        for name in names:
            path = root / str(name)
            if not path.is_file():
                return ""
            stat = path.stat()
            rows.append((str(name), int(stat.st_size), _hash_file(path)))
    except (OSError, PermissionError):
        return ""
    return _sha(_canonical(rows))


def build_identity(
    *,
    project: Path,
    repository_fingerprint: RepositoryFingerprint,
    instruction: str,
    verifier: Sequence[str],
    mode: str,
    policy_fingerprint: str = "",
    runtime_fingerprint: str = _CACHE_VERSION,
    task_family: str = "coding-agent",
    dependency_fingerprint: str = "",
    toolchain_fingerprint: str = "",
    environment_fingerprint: str = "",
    tool_schema_fingerprint: str = "",
    security_fingerprint: str = "",
    verifier_contract_fingerprint: str = "",
) -> InferenceSkipIdentity:
    if not repository_fingerprint.cacheable or not repository_fingerprint.digest:
        raise ValueError("repository state is not inference-skip cacheable")
    project = Path(project).resolve(strict=True)
    normalized_instruction = instruction.replace("\r\n", "\n").replace("\r", "\n").strip()
    normalized_family = "-".join(str(task_family or "").strip().casefold().split())
    if not normalized_instruction or not verifier or not normalized_family:
        raise ValueError("instruction, verifier and task family are required for inference-skip identity")

    dependency_hash = _normalized_fingerprint(dependency_fingerprint) or _dependency_fingerprint(project)
    toolchain_hash = _normalized_fingerprint(toolchain_fingerprint) or _toolchain_fingerprint(project, verifier)
    environment_hash = _normalized_fingerprint(environment_fingerprint) or _environment_fingerprint()
    tool_schema_hash = _normalized_fingerprint(tool_schema_fingerprint) or _source_group_fingerprint(_TOOL_SCHEMA_SOURCES)
    security_hash = _normalized_fingerprint(security_fingerprint) or _source_group_fingerprint(_SECURITY_SOURCES)
    verifier_contract_hash = _normalized_fingerprint(verifier_contract_fingerprint) or _sha(
        _canonical(
            {
                "argv": [str(item) for item in verifier],
                "runtime": _source_group_fingerprint(_VERIFIER_SOURCES),
            }
        )
    )
    required_components = {
        "dependencies": dependency_hash,
        "toolchain": toolchain_hash,
        "environment": environment_hash,
        "tool_schema": tool_schema_hash,
        "security": security_hash,
        "verifier_contract": verifier_contract_hash,
    }
    missing = sorted(name for name, value in required_components.items() if not value)
    if missing:
        raise ValueError("incomplete inference-skip fingerprints: " + ",".join(missing))

    project_id = stable_project_id(project)
    instruction_hash = _sha(normalized_instruction.encode("utf-8"))
    verifier_hash = _sha(_canonical([str(item) for item in verifier]))
    task_family_hash = _sha(normalized_family.encode("utf-8"))
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
    identity_material = {
        "schema_version": _SCHEMA_VERSION,
        "cache_version": _CACHE_VERSION,
        "project_id": project_id,
        "repository_fingerprint": repository_fingerprint.digest,
        "instruction_hash": instruction_hash,
        "verifier_hash": verifier_hash,
        "policy_hash": policy_hash,
        "task_family_hash": task_family_hash,
        "dependency_hash": dependency_hash,
        "toolchain_hash": toolchain_hash,
        "environment_hash": environment_hash,
        "tool_schema_hash": tool_schema_hash,
        "security_hash": security_hash,
        "verifier_contract_hash": verifier_contract_hash,
    }
    key = _sha(_canonical(identity_material))
    return InferenceSkipIdentity(key=key, **{key: value for key, value in identity_material.items() if key not in {"schema_version", "cache_version"}})


def _identity_material(identity: InferenceSkipIdentity) -> dict[str, Any]:
    return {"schema_version": _SCHEMA_VERSION, "cache_version": _CACHE_VERSION, **asdict(identity)}


class InferenceSkipCache:
    """Exact-state cache containing only patches that already passed verification.

    The cache is deliberately semantic-free. Near matches are not hints. When an old
    row shares the same project + normalized instruction but fails an exact identity
    component, the refusal is persisted as a prevented semantic-reuse receipt.
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

    @staticmethod
    def _ensure_column(db: sqlite3.Connection, table: str, name: str, declaration: str) -> None:
        columns = {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}
        if name not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

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
                    invalid_reason TEXT NOT NULL DEFAULT '',
                    identity_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS inference_skip_project_idx
                    ON inference_skip_cache(project_id, repository_fingerprint, valid);
                CREATE INDEX IF NOT EXISTS inference_skip_instruction_idx
                    ON inference_skip_cache(project_id, instruction_hash, valid);
                CREATE TABLE IF NOT EXISTS inference_skip_preventions(
                    prevention_key TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    requested_cache_key TEXT NOT NULL,
                    candidate_cache_key TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    instruction_hash TEXT NOT NULL,
                    reasons_json TEXT NOT NULL,
                    first_seen REAL NOT NULL,
                    last_seen REAL NOT NULL,
                    occurrences INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS inference_skip_prevention_project_idx
                    ON inference_skip_preventions(project_id, instruction_hash, last_seen DESC);
                """
            )
            self._ensure_column(db, "inference_skip_cache", "identity_json", "TEXT NOT NULL DEFAULT '{}'")

    @staticmethod
    def _decode_identity(raw: Any) -> dict[str, Any]:
        try:
            value = json.loads(str(raw or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _mismatch_reasons(requested: Mapping[str, Any], candidate: Mapping[str, Any]) -> list[str]:
        if not candidate:
            return ["LEGACY_IDENTITY_INCOMPLETE"]
        reasons = [
            field.upper() + "_MISMATCH"
            for field in _IDENTITY_COMPARE_FIELDS
            if str(requested.get(field) or "") != str(candidate.get(field) or "")
        ]
        return reasons or ["CACHE_KEY_MISMATCH"]

    def _record_prevention(
        self,
        db: sqlite3.Connection,
        *,
        event_type: str,
        requested_cache_key: str,
        candidate_cache_key: str,
        project_id: str,
        instruction_hash: str,
        reasons: Sequence[str],
    ) -> None:
        normalized_reasons = tuple(sorted(dict.fromkeys(str(reason) for reason in reasons if str(reason))))
        if not normalized_reasons:
            return
        now = time.time()
        prevention_key = _sha(
            _canonical(
                {
                    "event_type": event_type,
                    "requested": requested_cache_key,
                    "candidate": candidate_cache_key,
                    "reasons": normalized_reasons,
                }
            )
        )
        db.execute(
            """
            INSERT INTO inference_skip_preventions(
                prevention_key,event_type,requested_cache_key,candidate_cache_key,project_id,
                instruction_hash,reasons_json,first_seen,last_seen,occurrences
            ) VALUES(?,?,?,?,?,?,?,?,?,1)
            ON CONFLICT(prevention_key) DO UPDATE SET
                last_seen=excluded.last_seen,
                occurrences=inference_skip_preventions.occurrences+1
            """,
            (
                prevention_key,
                event_type,
                requested_cache_key,
                candidate_cache_key,
                project_id,
                instruction_hash,
                json.dumps(normalized_reasons, sort_keys=True),
                now,
                now,
            ),
        )

    def _record_near_miss_preventions(self, db: sqlite3.Connection, identity: InferenceSkipIdentity) -> int:
        requested = _identity_material(identity)
        rows = db.execute(
            """
            SELECT cache_key,identity_json FROM inference_skip_cache
            WHERE project_id=? AND instruction_hash=? AND valid=1 AND cache_key<>?
            ORDER BY created_at DESC LIMIT 32
            """,
            (identity.project_id, identity.instruction_hash, identity.key),
        ).fetchall()
        count = 0
        for row in rows:
            candidate = self._decode_identity(row["identity_json"])
            reasons = self._mismatch_reasons(requested, candidate)
            self._record_prevention(
                db,
                event_type="EXACT_IDENTITY_NEAR_MISS",
                requested_cache_key=identity.key,
                candidate_cache_key=str(row["cache_key"]),
                project_id=identity.project_id,
                instruction_hash=identity.instruction_hash,
                reasons=reasons,
            )
            count += 1
        return count

    def lookup(self, identity: InferenceSkipIdentity) -> InferenceSkipHit | None:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM inference_skip_cache WHERE cache_key=? AND valid=1",
                (identity.key,),
            ).fetchone()
            if row is None:
                if self._record_near_miss_preventions(db, identity):
                    db.commit()
                else:
                    db.rollback()
                return None
            patch = str(row["patch"])
            patch_hash = _sha(patch.encode("utf-8"))
            if not patch.strip() or patch_hash != str(row["patch_hash"]):
                db.execute(
                    "UPDATE inference_skip_cache SET valid=0,invalid_reason=? WHERE cache_key=?",
                    ("patch-integrity-failed", identity.key),
                )
                self._record_prevention(
                    db,
                    event_type="UNSAFE_EXACT_HIT_REJECTED",
                    requested_cache_key=identity.key,
                    candidate_cache_key=identity.key,
                    project_id=identity.project_id,
                    instruction_hash=identity.instruction_hash,
                    reasons=("PATCH_INTEGRITY_FAILED",),
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
        identity_json = json.dumps(_identity_material(identity), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """
                INSERT INTO inference_skip_cache(
                    cache_key,project_id,repository_fingerprint,instruction_hash,verifier_hash,policy_hash,
                    patch,patch_hash,rationale,verification_hash,created_at,last_hit_at,hit_count,valid,invalid_reason,identity_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0,1,'',?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    patch=excluded.patch,patch_hash=excluded.patch_hash,rationale=excluded.rationale,
                    verification_hash=excluded.verification_hash,created_at=excluded.created_at,
                    last_hit_at=NULL,hit_count=0,valid=1,invalid_reason='',identity_json=excluded.identity_json
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
                    identity_json,
                ),
            )
            db.commit()

    def invalidate(self, cache_key: str, reason: str) -> None:
        if not cache_key:
            return
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT project_id,instruction_hash FROM inference_skip_cache WHERE cache_key=?",
                (str(cache_key),),
            ).fetchone()
            db.execute(
                "UPDATE inference_skip_cache SET valid=0,invalid_reason=? WHERE cache_key=?",
                (str(reason)[:512], str(cache_key)),
            )
            if row is not None and str(reason) in {"cached-patch-apply-failed", "cached-patch-verifier-failed"}:
                self._record_prevention(
                    db,
                    event_type="REPLAY_REJECTED",
                    requested_cache_key=str(cache_key),
                    candidate_cache_key=str(cache_key),
                    project_id=str(row["project_id"]),
                    instruction_hash=str(row["instruction_hash"]),
                    reasons=(str(reason).upper().replace("-", "_"),),
                )
            db.commit()

    def inspect(self, cache_key: str) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM inference_skip_cache WHERE cache_key=?", (cache_key,)).fetchone()
        return dict(row) if row is not None else None

    def preventions(self, *, limit: int = 100) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 1000))
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM inference_skip_preventions ORDER BY last_seen DESC,prevention_key LIMIT ?",
                (bounded,),
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            value = dict(row)
            value["reasons"] = list(json.loads(str(value.pop("reasons_json"))))
            output.append(value)
        return output

    def prevention_stats(self) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute(
                "SELECT COUNT(*) events,COALESCE(SUM(occurrences),0) occurrences FROM inference_skip_preventions"
            ).fetchone()
            by_type = {
                str(item[0]): int(item[1])
                for item in db.execute(
                    "SELECT event_type,SUM(occurrences) FROM inference_skip_preventions GROUP BY event_type"
                )
            }
        return {
            "events": int(row["events"]),
            "occurrences": int(row["occurrences"]),
            "by_type": by_type,
        }


__all__ = [
    "InferenceSkipCache",
    "InferenceSkipHit",
    "InferenceSkipIdentity",
    "RepositoryFingerprint",
    "build_identity",
    "repository_state_fingerprint",
]
