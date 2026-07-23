from __future__ import annotations

import fnmatch
import json
import os
import time
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping

from .util import atomic_write_json, read_json, sha256_file


_DEFAULT_IGNORES = (".git/*", ".syntavra/*", "node_modules/*", "dist/*", "build/*", "__pycache__/*", ".venv/*", "venv/*")


def _jsonable(value: object) -> object:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(child) for child in value]
    if isinstance(value, Path):
        return str(value)
    return value


@dataclass(frozen=True)
class FileState:
    path: str
    size: int
    mtime_ns: int
    sha256: str


@dataclass(frozen=True)
class ChangeSet:
    added: tuple[str, ...]
    modified: tuple[str, ...]
    deleted: tuple[str, ...]
    scanned: int
    timestamp: float

    @property
    def changed(self) -> tuple[str, ...]:
        return (*self.added, *self.modified, *self.deleted)


class RepositoryWatcher:
    """Portable polling watcher with deterministic incremental index callbacks."""

    def __init__(self, project: Path, state_root: Path, *, ignores: Iterable[str] = _DEFAULT_IGNORES):
        self.project = Path(project).resolve(strict=True)
        self.state_root = Path(state_root)
        self.snapshot_path = self.state_root / "watcher" / "snapshot.json"
        self.status_path = self.state_root / "watcher" / "status.json"
        dynamic = list(ignores)
        try:
            state_relative = self.state_root.resolve(strict=False).relative_to(self.project).as_posix().rstrip("/")
        except ValueError:
            state_relative = ""
        if state_relative:
            dynamic.extend((state_relative, f"{state_relative}/*"))
        self.ignores = tuple(dict.fromkeys(dynamic))

    def _ignored(self, relative: str) -> bool:
        normalized = relative.replace(os.sep, "/")
        return any(fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(normalized + "/", pattern) for pattern in self.ignores)

    def scan(self) -> dict[str, FileState]:
        rows: dict[str, FileState] = {}
        for path in self.project.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(self.project).as_posix()
            if self._ignored(relative):
                continue
            try:
                stat = path.stat()
                rows[relative] = FileState(relative, stat.st_size, stat.st_mtime_ns, sha256_file(path))
            except OSError:
                continue
        return rows

    def poll(self, *, callback: Callable[[ChangeSet], object] | None = None) -> ChangeSet:
        previous_raw = read_json(self.snapshot_path, {}) or {}
        previous = {path: FileState(**row) for path, row in previous_raw.items() if isinstance(row, Mapping)}
        current = self.scan()
        added = tuple(sorted(current.keys() - previous.keys()))
        deleted = tuple(sorted(previous.keys() - current.keys()))
        modified = tuple(sorted(path for path in current.keys() & previous.keys() if current[path].sha256 != previous[path].sha256))
        changes = ChangeSet(added, modified, deleted, len(current), time.time())
        atomic_write_json(self.snapshot_path, {path: asdict(row) for path, row in current.items()})
        callback_result = callback(changes) if callback and changes.changed else None
        atomic_write_json(self.status_path, {"running": False, "last_poll": asdict(changes), "callback_result": _jsonable(callback_result)})
        return changes

    def watch(self, *, interval_seconds: float = 1.0, iterations: int | None = None, callback: Callable[[ChangeSet], object] | None = None) -> list[ChangeSet]:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        results: list[ChangeSet] = []
        count = 0
        atomic_write_json(self.status_path, {"running": True, "pid": os.getpid(), "started_at": time.time(), "interval_seconds": interval_seconds})
        try:
            while iterations is None or count < iterations:
                changes = self.poll(callback=callback)
                results.append(changes)
                count += 1
                if iterations is None or count < iterations:
                    time.sleep(interval_seconds)
        finally:
            status = read_json(self.status_path, {}) or {}
            status.update({"running": False, "stopped_at": time.time(), "iterations": count})
            atomic_write_json(self.status_path, status)
        return results

    def status(self) -> dict[str, object]:
        return read_json(self.status_path, {"running": False, "initialized": self.snapshot_path.is_file()})
