from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


class JanitorError(RuntimeError):
    pass


@dataclass(frozen=True)
class RetentionRule:
    name: str
    root: str
    ttl_seconds: float
    max_delete_bytes: int = 1024 * 1024 * 1024
    patterns: tuple[str, ...] = ("*",)
    protect_names: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.name or self.ttl_seconds < 0 or self.max_delete_bytes < 0:
            raise JanitorError("invalid retention rule")


@dataclass(frozen=True)
class JanitorAction:
    rule: str
    path: str
    bytes: int
    age_seconds: float
    deleted: bool


class RuntimeJanitor:
    """Central retention/housekeeping coordinator.

    Component-specific cleaners can be registered for databases/evidence. Generic file
    rules never follow symlinks and are rooted under explicitly configured directories.
    """

    def __init__(self):
        self._cleaners: dict[str, Callable[[bool], dict[str, Any]]] = {}

    def register(self, name: str, cleaner: Callable[[bool], dict[str, Any]]) -> None:
        if not name or name in self._cleaners:
            raise JanitorError("duplicate or invalid cleaner name")
        self._cleaners[name] = cleaner

    def run_components(self, *, dry_run: bool = True) -> dict[str, Any]:
        results: dict[str, Any] = {}
        failures: dict[str, str] = {}
        for name in sorted(self._cleaners):
            try:
                results[name] = self._cleaners[name](dry_run)
            except Exception as exc:
                failures[name] = type(exc).__name__
        return {"ok": not failures, "dry_run": dry_run, "results": results, "failures": failures}

    def apply_rules(
        self,
        rules: Iterable[RetentionRule],
        *,
        dry_run: bool = True,
        now: float | None = None,
    ) -> dict[str, Any]:
        current = time.time() if now is None else float(now)
        actions: list[JanitorAction] = []
        for rule in rules:
            rule.validate()
            root = Path(rule.root).resolve(strict=False)
            if not root.is_dir() or root.is_symlink():
                continue
            candidates: dict[Path, os.stat_result] = {}
            for pattern in rule.patterns:
                for path in root.rglob(pattern):
                    try:
                        if path.is_symlink() or not path.is_file():
                            continue
                        resolved = path.resolve(strict=True)
                        resolved.relative_to(root)
                        if resolved.name in rule.protect_names:
                            continue
                        stat = resolved.stat()
                    except (OSError, ValueError):
                        continue
                    if current - stat.st_mtime >= rule.ttl_seconds:
                        candidates[resolved] = stat
            used = 0
            for path, stat in sorted(candidates.items(), key=lambda pair: pair[1].st_mtime):
                if used + stat.st_size > rule.max_delete_bytes:
                    break
                deleted = False
                if not dry_run:
                    try:
                        path.unlink()
                        deleted = True
                    except OSError:
                        deleted = False
                used += stat.st_size
                actions.append(JanitorAction(rule.name, str(path), stat.st_size, current - stat.st_mtime, deleted))
        return {
            "dry_run": dry_run,
            "actions": [asdict(action) for action in actions],
            "bytes_selected": sum(action.bytes for action in actions),
            "files_selected": len(actions),
            "files_deleted": sum(1 for action in actions if action.deleted),
        }
