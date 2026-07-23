from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .code_intelligence import CodeIntelligenceIndex
from .memory_intelligence import MemoryIntelligenceStore
from .notifications import NotificationFeed
from .repository_watcher import RepositoryWatcher
from .util import atomic_write_json, read_json, sha256_bytes, canonical_json


class BackgroundIntelligenceWorker:
    """One portable worker for incremental code indexing and memory embeddings."""

    def __init__(self, *, project: Path, state_root: Path):
        self.project = Path(project).resolve(strict=True)
        self.state_root = Path(state_root)
        self.status_path = self.state_root / "workers" / "intelligence.json"
        self.watcher = RepositoryWatcher(self.project, self.state_root)
        self.memory = MemoryIntelligenceStore(
            self.state_root / "memory-intelligence.sqlite3",
            notification_feed=NotificationFeed(self.state_root),
        )

    def _cycle(self) -> dict[str, Any]:
        def index_callback(changes):
            graph = CodeIntelligenceIndex(self.project).build()
            return {"changed": list(changes.changed), "files": len(graph.files), "symbols": len(graph.symbols), "edges": len(graph.edges)}
        changes = self.watcher.poll(callback=index_callback)
        embeddings = self.memory.backfill_embeddings(limit=500)
        body = {
            "timestamp": time.time(),
            "changes": asdict(changes),
            "embeddings": embeddings,
            "memory": self.memory.stats(),
        }
        body["cycle_hash"] = sha256_bytes(canonical_json(body))
        return body

    def run(self, *, iterations: int | None = 1, interval_seconds: float = 2.0) -> dict[str, Any]:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        atomic_write_json(self.status_path, {"running": True, "pid": os.getpid(), "started_at": time.time(), "interval_seconds": interval_seconds})
        rows: list[dict[str, Any]] = []
        count = 0
        try:
            while iterations is None or count < iterations:
                row = self._cycle(); rows.append(row); count += 1
                atomic_write_json(self.status_path, {"running": True, "pid": os.getpid(), "iterations": count, "last_cycle": row, "interval_seconds": interval_seconds})
                if iterations is None or count < iterations:
                    time.sleep(interval_seconds)
        finally:
            status = read_json(self.status_path, {}) or {}
            status.update({"running": False, "stopped_at": time.time(), "iterations": count})
            atomic_write_json(self.status_path, status)
        return {"ok": True, "iterations": count, "cycles": rows, "status": self.status()}

    def status(self) -> dict[str, Any]:
        return read_json(self.status_path, {"running": False, "initialized": False}) or {"running": False}

    @staticmethod
    def spawn(*, project: Path, state_root: Path, interval_seconds: float = 2.0) -> dict[str, Any]:
        argv = [
            os.environ.get("PYTHON", "python"), "-m", "syntavra_runtime.worker_entry",
            "--project", str(Path(project).resolve(strict=True)),
            "--state-root", str(Path(state_root).resolve(strict=False)),
            "--interval", str(interval_seconds),
        ]
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return {"ok": True, "pid": process.pid, "argv": argv, "mode": "background"}
