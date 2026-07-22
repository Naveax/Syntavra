#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from common import dump_json, git_branch, git_head, git_root, normalize, run, sha256_file
from routing import RouteDecision, classify

LANGUAGE_SUFFIXES = {
    ".py": "python", ".rs": "rust", ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".go": "go", ".java": "java",
    ".cs": "csharp", ".cpp": "cpp", ".cc": "cpp", ".c": "c", ".h": "c",
    ".kt": "kotlin", ".swift": "swift", ".rb": "ruby", ".php": "php",
    ".lua": "lua", ".sh": "shell", ".ps1": "powershell", ".sql": "sql",
}
EXCLUDED = {".git", ".syntavra", "node_modules", "target", "dist", "build", "vendor", ".venv", "venv", "__pycache__"}


@dataclass(frozen=True)
class RepositoryState:
    root: str
    commit: str
    branch: str
    dirty: bool
    fingerprint: str
    dominant_language: str
    code_files: int
    total_bytes: int
    size_bucket: str
    changed_files: tuple[str, ...]
    test_commands: tuple[str, ...]


@dataclass(frozen=True)
class SessionState:
    turns: int = 0
    context_window: int = 128000
    context_used: int = 0
    prior_evidence: int = 0
    duplicate_evidence: int = 0
    retries: int = 0
    cache_hit_rate: float = 0.0
    last_tool: str = ""

    @property
    def pressure(self) -> float:
        return min(1.0, max(0.0, self.context_used / max(1, self.context_window)))


@dataclass(frozen=True)
class TaskState:
    task: str
    route: RouteDecision
    repository: RepositoryState
    session: SessionState
    platform: str
    model: str
    active_engines: tuple[str, ...]
    task_family: str
    verifier_hints: tuple[str, ...]
    task_hash: str


def _changed_files(root: Path) -> tuple[str, ...]:
    result = run(["git", "-C", str(root), "status", "--porcelain=v1", "-z"], timeout=15)
    if result.returncode != 0:
        return ()
    values: list[str] = []
    for entry in result.stdout.split("\x00"):
        if not entry:
            continue
        path = entry[3:] if len(entry) > 3 else entry
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        values.append(path.replace("\\", "/"))
    return tuple(sorted(set(values)))


def _detect_tests(root: Path, languages: set[str]) -> tuple[str, ...]:
    commands: list[str] = []
    if (root / "pyproject.toml").exists() or (root / "pytest.ini").exists() or "python" in languages:
        commands.append("python -m pytest")
    if (root / "Cargo.toml").exists() or "rust" in languages:
        commands.append("cargo test")
    if (root / "package.json").exists() or languages & {"typescript", "javascript"}:
        commands.append("npm test -- --runInBand")
    if (root / "go.mod").exists() or "go" in languages:
        commands.append("go test ./...")
    if any(root.glob("*.sln")) or any(root.rglob("*.csproj")):
        commands.append("dotnet test")
    if (root / "CMakeLists.txt").exists():
        commands.append("ctest --test-dir build --output-on-failure")
    return tuple(dict.fromkeys(commands)) or ("project-specific verifier",)


def probe_repository(project: str | Path, *, max_files: int = 12000, max_bytes_per_file: int = 2 * 1024 * 1024) -> RepositoryState:
    root = git_root(Path(project))
    counts: dict[str, int] = {}
    total_bytes = 0
    code_files = 0
    digest = hashlib.sha256()
    scanned = 0
    for path in sorted(root.rglob("*")):
        if scanned >= max_files:
            break
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in EXCLUDED for part in rel.parts):
            continue
        suffix = path.suffix.casefold()
        language = LANGUAGE_SUFFIXES.get(suffix)
        if not language:
            continue
        scanned += 1
        size = min(path.stat().st_size, max_bytes_per_file)
        total_bytes += size
        code_files += 1
        counts[language] = counts.get(language, 0) + 1
        digest.update(rel.as_posix().encode("utf-8"))
        digest.update(str(path.stat().st_size).encode("ascii"))
        digest.update(str(path.stat().st_mtime_ns).encode("ascii"))
    changed = _changed_files(root)
    for rel in changed:
        path = root / rel
        digest.update(rel.encode("utf-8"))
        if path.is_file() and not path.is_symlink():
            try:
                digest.update(sha256_file(path).encode("ascii"))
            except OSError:
                pass
    commit = git_head(root)
    branch = git_branch(root)
    digest.update(commit.encode("ascii", errors="ignore"))
    dominant = max(counts, key=counts.get) if counts else "unknown"
    size_bucket = "large" if code_files > 5000 or total_bytes > 50_000_000 else "medium" if code_files > 500 or total_bytes > 5_000_000 else "small"
    return RepositoryState(
        root=str(root), commit=commit, branch=branch, dirty=bool(changed), fingerprint=digest.hexdigest(),
        dominant_language=dominant, code_files=code_files, total_bytes=total_bytes,
        size_bucket=size_bucket, changed_files=changed, test_commands=_detect_tests(root, set(counts)),
    )


def task_family(route: RouteDecision) -> str:
    priority = ("security", "bulk", "benchmark", "graph", "debug", "repository", "memory", "tooling", "minimal")
    primary = next((category for category in priority if category in route.categories), "general")
    return f"{primary}:{route.integrity.casefold()}:{route.complexity}"


def encode_task_state(
    task: str,
    project: str | Path,
    *,
    platform: str = "codex",
    model: str = "unknown",
    active_engines: Iterable[str] = (),
    session: SessionState | None = None,
    known_file_edit: bool = False,
) -> TaskState:
    route = classify(task, known_file_edit=known_file_edit)
    repository = probe_repository(project)
    session = session or SessionState()
    task_hash = hashlib.sha256((normalize(task) + "\0" + repository.fingerprint).encode("utf-8")).hexdigest()
    return TaskState(
        task=task, route=route, repository=repository, session=session,
        platform=platform, model=model, active_engines=tuple(sorted(set(active_engines))),
        task_family=task_family(route), verifier_hints=repository.test_commands, task_hash=task_hash,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Encode Syntavra task/repository/session state")
    parser.add_argument("task")
    parser.add_argument("--project", default=".")
    parser.add_argument("--platform", default="codex")
    parser.add_argument("--model", default="unknown")
    parser.add_argument("--active-engines", default="")
    parser.add_argument("--context-window", type=int, default=128000)
    parser.add_argument("--context-used", type=int, default=0)
    parser.add_argument("--turns", type=int, default=0)
    parser.add_argument("--prior-evidence", type=int, default=0)
    parser.add_argument("--duplicates", type=int, default=0)
    parser.add_argument("--retries", type=int, default=0)
    parser.add_argument("--cache-hit-rate", type=float, default=0.0)
    args = parser.parse_args()
    session = SessionState(args.turns, args.context_window, args.context_used, args.prior_evidence, args.duplicates, args.retries, args.cache_hit_rate)
    state = encode_task_state(args.task, args.project, platform=args.platform, model=args.model, active_engines=args.active_engines.split(","), session=session)
    print(dump_json(asdict(state)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
