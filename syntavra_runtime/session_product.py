from __future__ import annotations

import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .product_surface import SessionAnalyticsStore
from .release_identity import CHANNEL, VERSION
from .session_runtime import SessionRecord, SessionRuntime


class SessionContinuityController:
    """Product wrapper for exact sessions, asynchronous compaction and continuity receipts."""

    def __init__(self, path: Path, *, project_id: str, analytics_path: Path | None = None):
        self.runtime = SessionRuntime(path, project_id=project_id)
        self.analytics = SessionAnalyticsStore(analytics_path or path.with_name("session-analytics.jsonl"))
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._last_cycle: dict[str, Any] = {
            "state": "IDLE",
            "started_at": None,
            "completed_at": None,
            "wall_time_ms": 0.0,
            "compacted": 0,
            "failures": [],
        }
        self._lock = threading.RLock()

    def open_or_resume(self, session_id: str | None = None, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        restored = False
        if session_id:
            try:
                session = self.runtime.get_session(session_id)
                restored = True
            except KeyError:
                session = self.runtime.create_session(session_id=session_id, metadata=metadata)
        else:
            session = self.runtime.create_session(metadata=metadata)
        verification = self.runtime.verify(session.session_id)
        self.analytics.record({
            "session_id": session.session_id,
            "repository_hash": self.runtime.project_id,
            "kind": "session-open",
            "success": verification["ok"],
            "continuity_restored": restored,
            "metadata": {"events": verification["events"]},
        })
        return {
            "ok": verification["ok"],
            "session": asdict(session),
            "continuity_restored": restored,
            "verification": verification,
        }

    def append(self, session_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        event = self.runtime.append(session_id, event_type, payload)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self.analytics.record({
            "session_id": session_id,
            "repository_hash": self.runtime.project_id,
            "kind": "session-append",
            "wall_time_ms": elapsed_ms,
            "success": True,
            "metadata": {"event_type": event_type, "sequence": event.sequence},
        })
        return {"ok": True, "event": asdict(event), "wall_time_ms": elapsed_ms}

    def compact_once(self, session_id: str, *, force: bool = False) -> dict[str, Any]:
        started = time.perf_counter()
        root = self.runtime.compact(session_id, force=force)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        verification = self.runtime.verify(session_id)
        context = self.runtime.active_context(session_id)
        value = {
            "ok": bool(verification["ok"] and (root or verification["events"] == 0)),
            "session_id": session_id,
            "root_summary_id": root,
            "events": verification["events"],
            "active_context_tokens": context["used"],
            "exact_history_events": context["exact_history_events"],
            "wall_time_ms": elapsed_ms,
            "verification": verification,
        }
        self.analytics.record({
            "session_id": session_id,
            "repository_hash": self.runtime.project_id,
            "kind": "session-compaction",
            "compaction_ms": elapsed_ms,
            "wall_time_ms": elapsed_ms,
            "success": value["ok"],
            "metadata": {
                "events": verification["events"],
                "root_summary_id": root,
                "active_context_tokens": context["used"],
            },
        })
        return value

    def continuity_receipt(self, session_id: str, *, token_budget: int = 32_000) -> dict[str, Any]:
        started = time.perf_counter()
        session = self.runtime.get_session(session_id)
        verification = self.runtime.verify(session_id)
        context = self.runtime.active_context(session_id, token_budget=token_budget)
        exact_recovery = True
        if context["root_summary_id"]:
            try:
                expanded = self.runtime.expand_summary(context["root_summary_id"])
                exact_recovery = expanded["coverage"] == context["exact_history_events"]
            except (KeyError, ValueError):
                exact_recovery = False
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        receipt = {
            "version": VERSION,
            "channel": CHANNEL,
            "session_id": session_id,
            "project_id": session.project_id,
            "state": session.state,
            "parents": list(session.parent_ids),
            "events": verification["events"],
            "last_event_hash": verification["last_hash"],
            "active_context_tokens": context["used"],
            "token_budget": token_budget,
            "root_summary_id": context["root_summary_id"],
            "exact_recovery": exact_recovery,
            "forced_restart": False,
            "continuity_restored": verification["ok"],
            "wall_time_ms": elapsed_ms,
            "claim": "SESSION_CONTINUITY_INTERNALLY_VERIFIED" if verification["ok"] and exact_recovery else "SESSION_CONTINUITY_NOT_PROVEN",
        }
        self.analytics.record({
            "session_id": session_id,
            "repository_hash": session.project_id,
            "kind": "session-continuity-receipt",
            "wall_time_ms": elapsed_ms,
            "success": verification["ok"] and exact_recovery,
            "continuity_restored": verification["ok"],
            "metadata": {
                "events": verification["events"],
                "exact_recovery": exact_recovery,
                "active_context_tokens": context["used"],
            },
        })
        return receipt

    def _cycle(self, min_events: int) -> dict[str, Any]:
        started_at = time.time()
        started = time.perf_counter()
        compacted: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        for session in self.runtime.list_sessions(state="ACTIVE"):
            events = self.runtime.events(session.session_id, limit=10_000_000)
            if len(events) < min_events:
                continue
            try:
                compacted.append(self.compact_once(session.session_id))
            except Exception as error:
                failures.append({"session_id": session.session_id, "error": f"{type(error).__name__}: {error}"})
        value = {
            "state": "HEALTHY" if not failures else "DEGRADED",
            "started_at": started_at,
            "completed_at": time.time(),
            "wall_time_ms": (time.perf_counter() - started) * 1000.0,
            "compacted": len(compacted),
            "failures": failures,
        }
        with self._lock:
            self._last_cycle = value
        return value

    def start(self, *, interval_seconds: float = 5.0, min_events: int = 64) -> threading.Thread:
        if self._worker and self._worker.is_alive():
            return self._worker
        self._stop.clear()

        def worker() -> None:
            while not self._stop.wait(max(0.1, interval_seconds)):
                self._cycle(max(1, min_events))

        self._worker = threading.Thread(target=worker, name="syntavra-product-compactor", daemon=True)
        self._worker.start()
        return self._worker

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._worker:
            self._worker.join(timeout=max(0.0, timeout))

    def status(self) -> dict[str, Any]:
        with self._lock:
            cycle = dict(self._last_cycle)
        return {
            "version": VERSION,
            "channel": CHANNEL,
            "worker_alive": bool(self._worker and self._worker.is_alive()),
            "last_cycle": cycle,
            "analytics": self.analytics.report(),
            "sessions": [asdict(item) for item in self.runtime.list_sessions()],
        }
