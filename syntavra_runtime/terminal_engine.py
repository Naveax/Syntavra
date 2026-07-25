from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import tempfile
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .artifacts import ArtifactRecord, ArtifactStore, FirewallReceipt
from .competitive_fabric import CommandCompactor
from .security_scan import redact_text, scan_text


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ERROR_RE = re.compile(r"(?i)\b(error|failed|failure|panic|assertion|traceback|exception|fatal|denied|timeout|critical|segfault)\b")
_WARNING_RE = re.compile(r"(?i)\b(warn(?:ing)?|deprecated|retry|throttl)\b")
_LOCATION_RE = re.compile(r"(?:[A-Za-z]:)?[^\s:]+\.(?:py|rs|ts|tsx|js|jsx|c|cc|cpp|h|hpp|go|java|cs|rb|php):\d+(?::\d+)?")
_SUMMARY_RE = re.compile(r"(?i)(test result:|\b\d+\s+(?:passed|failed|errors?|skipped)\b|build\s+(?:succeeded|failed)|finished\s+in\s+[0-9.]+)")


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


def _clean(text: str) -> str:
    return redact_text(_ANSI_RE.sub("", text.replace("\r", "")))


class _StreamingArtifactWriter:
    def __init__(self, store: ArtifactStore, *, media_type: str, kind: str, metadata: dict[str, Any]) -> None:
        self.store = store
        self.media_type = media_type
        self.kind = kind
        self.metadata = metadata
        spool_root = store.root / ".spool"
        spool_root.mkdir(parents=True, exist_ok=True)
        self.path = spool_root / f"{secrets.token_hex(16)}.tmp"
        self.handle = self.path.open("wb")
        self.digest = hashlib.sha256()
        self.byte_count = 0

    def write(self, data: bytes) -> None:
        if not data:
            return
        self.handle.write(data)
        self.digest.update(data)
        self.byte_count += len(data)

    def finish(self) -> ArtifactRecord:
        self.handle.flush()
        os.fsync(self.handle.fileno())
        self.handle.close()
        digest = self.digest.hexdigest()
        artifact_id = f"sha256:{digest}"
        target = self.store._object_path(digest)  # package-private canonical object layout
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            self.path.unlink(missing_ok=True)
        else:
            os.replace(self.path, target)
        created = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat().replace("+00:00", "Z")
        with sqlite3.connect(self.store.db_path, timeout=30.0) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                """INSERT OR IGNORE INTO artifacts
                   (artifact_id,sha256,media_type,kind,byte_count,created_at,object_path,metadata_json)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    artifact_id,
                    digest,
                    self.media_type,
                    self.kind,
                    self.byte_count,
                    created,
                    str(target),
                    json.dumps(self.metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                ),
            )
        return self.store.record(artifact_id)

    def abort(self) -> None:
        try:
            self.handle.close()
        finally:
            self.path.unlink(missing_ok=True)


@dataclass(frozen=True)
class TerminalSnapshot:
    original_bytes: int
    scanned_lines: int
    suppressed_lines: int
    critical_lines: tuple[str, ...]
    summary_lines: tuple[str, ...]
    tail_lines: tuple[str, ...]


class TerminalCaptureSession:
    """Bounded-memory terminal capture with exact disk spooling."""

    def __init__(
        self,
        engine: "TerminalOutputEngine",
        *,
        tool: str,
        command: str,
        exit_code: int | None = None,
        duration_ms: float = 0.0,
        media_type: str = "text/plain",
        preview_budget_bytes: int = 4096,
    ) -> None:
        self.engine = engine
        self.tool = tool
        self.command = command or tool
        self.exit_code = exit_code
        self.duration_ms = duration_ms
        self.media_type = media_type
        self.preview_budget_bytes = max(256, int(preview_budget_bytes))
        self.writer = _StreamingArtifactWriter(
            engine.store,
            media_type=media_type,
            kind="tool-output:terminal",
            metadata={"tool": tool, "command": self.command, "duration_ms": duration_ms},
        )
        self.buffer = bytearray()
        self.raw_small = bytearray()
        self.small_complete = True
        self.first: list[str] = []
        self.tail: deque[str] = deque(maxlen=30)
        self.critical: list[str] = []
        self.summaries: deque[str] = deque(maxlen=24)
        self.repeated: Counter[str] = Counter()
        self.scanned_lines = 0
        self.suppressed_lines = 0
        self.closed = False

    def feed(self, chunk: bytes | str, *, stream: str = "stdout") -> None:
        if self.closed:
            raise RuntimeError("terminal capture session is closed")
        data = chunk.encode("utf-8") if isinstance(chunk, str) else bytes(chunk)
        if not data:
            return
        self.writer.write(data)
        if self.small_complete:
            if len(self.raw_small) + len(data) <= self.engine.small_output_limit_bytes:
                self.raw_small.extend(data)
            else:
                self.raw_small.clear()
                self.small_complete = False
        self.buffer.extend(data)
        while b"\n" in self.buffer:
            raw, _, remainder = self.buffer.partition(b"\n")
            self.buffer = bytearray(remainder)
            self._observe(raw.decode("utf-8", errors="replace"), stream=stream)
        if len(self.buffer) > self.engine.max_line_bytes:
            raw = bytes(self.buffer[: self.engine.max_line_bytes])
            del self.buffer[: self.engine.max_line_bytes]
            self._observe(raw.decode("utf-8", errors="replace") + " [… line fragment …]", stream=stream)

    def _observe(self, raw_line: str, *, stream: str) -> None:
        # Carriage-return progress lines keep only the final visible state.
        line = raw_line.rsplit("\r", 1)[-1]
        clean = _clean(line).strip()
        if not clean:
            return
        self.scanned_lines += 1
        rendered = f"[{stream}] {clean}" if stream == "stderr" else clean
        if len(self.first) < 12:
            self.first.append(rendered)
        self.tail.append(rendered)
        self.repeated[rendered] += 1
        if len(self.repeated) > 512:
            for key, _ in self.repeated.most_common()[256:]:
                del self.repeated[key]
        if (_ERROR_RE.search(clean) or _LOCATION_RE.search(clean)) and len(self.critical) < 64:
            if rendered not in self.critical:
                self.critical.append(rendered)
        elif _SUMMARY_RE.search(clean) or _WARNING_RE.search(clean):
            if rendered not in self.summaries:
                self.summaries.append(rendered)
        elif self.scanned_lines > 50:
            self.suppressed_lines += 1

    def snapshot(self) -> TerminalSnapshot:
        return TerminalSnapshot(
            original_bytes=self.writer.byte_count,
            scanned_lines=self.scanned_lines,
            suppressed_lines=self.suppressed_lines,
            critical_lines=tuple(self.critical),
            summary_lines=tuple(self.summaries),
            tail_lines=tuple(self.tail),
        )

    def finalize(self, *, exit_code: int | None = None, duration_ms: float | None = None) -> FirewallReceipt:
        if self.closed:
            raise RuntimeError("terminal capture session already finalized")
        self.closed = True
        if self.buffer:
            self._observe(self.buffer.decode("utf-8", errors="replace"), stream="stdout")
            self.buffer.clear()
        final_exit = int((self.exit_code if exit_code is None else exit_code) or 0)
        final_duration = float(self.duration_ms if duration_ms is None else duration_ms)
        record = self.writer.finish()

        repeated = [f"[{count}x] {line}" for line, count in self.repeated.most_common(12) if count > 2]
        selected: list[str] = []
        for line in [*self.critical, *self.summaries, *repeated, *self.first, *self.tail]:
            if line and line not in selected:
                selected.append(line)
            if len(selected) >= 80:
                break
        header = [
            f"Tool: {self.tool}",
            f"Command: {self.command}",
            f"Exit code: {final_exit}",
            f"Duration: {final_duration:.3f} ms",
            f"Scanned: {self.scanned_lines} lines / {record.byte_count} bytes",
            f"Suppressed: {self.suppressed_lines} low-value lines",
            f"Exact output: artifact://{record.artifact_id}",
        ]
        visible = "\n".join([*header, *selected])

        if self.small_complete:
            raw_text = self.raw_small.decode("utf-8", errors="replace")
            compact = self.engine.command_compactor.compact(
                self.command,
                raw_text,
                budget_bytes=self.preview_budget_bytes,
            ).visible_text
            if len(compact.encode("utf-8")) < len(visible.encode("utf-8")):
                visible = compact + f"\nExact output: artifact://{record.artifact_id}"
            sanitized_raw = _clean(raw_text)
            if len(visible.encode("utf-8")) >= len(sanitized_raw.encode("utf-8")):
                visible = sanitized_raw

        encoded = visible.encode("utf-8")
        if len(encoded) > self.preview_budget_bytes:
            suffix = f"\n[… compact view bounded; exact output: artifact://{record.artifact_id}]"
            keep = max(0, self.preview_budget_bytes - len(suffix.encode("utf-8")))
            visible = encoded[:keep].decode("utf-8", errors="ignore").rstrip() + suffix

        security = scan_text(visible)
        visible = security.redacted_text
        visible_bytes = len(visible.encode("utf-8"))
        return FirewallReceipt(
            kind="terminal",
            artifact_id=record.artifact_id,
            original_bytes=record.byte_count,
            visible_bytes=visible_bytes,
            estimated_original_tokens=max(1, (record.byte_count + 3) // 4),
            estimated_visible_tokens=_estimate_tokens(visible),
            savings_ratio=max(0.0, 1.0 - visible_bytes / max(1, record.byte_count)),
            compact_view=visible,
            query_modes=("head", "tail", "errors", "failures", "regex"),
            exact_recovery=self.engine.store.verify(record.artifact_id)["ok"],
            critical_lines=tuple(self.critical),
        )

    def abort(self) -> None:
        if not self.closed:
            self.closed = True
            self.writer.abort()


class TerminalOutputEngine:
    """Canonical terminal-output surface used by the platform and agent runtime."""

    def __init__(
        self,
        store: ArtifactStore,
        *,
        small_output_limit_bytes: int = 256 * 1024,
        max_line_bytes: int = 128 * 1024,
    ) -> None:
        self.store = store
        self.small_output_limit_bytes = max(4096, int(small_output_limit_bytes))
        self.max_line_bytes = max(4096, int(max_line_bytes))
        self.command_compactor = CommandCompactor()

    def open(
        self,
        *,
        tool: str,
        command: str = "",
        exit_code: int | None = None,
        duration_ms: float = 0.0,
        media_type: str = "text/plain",
        preview_budget_bytes: int = 4096,
    ) -> TerminalCaptureSession:
        return TerminalCaptureSession(
            self,
            tool=tool,
            command=command or tool,
            exit_code=exit_code,
            duration_ms=duration_ms,
            media_type=media_type,
            preview_budget_bytes=preview_budget_bytes,
        )

    def capture(
        self,
        tool: str,
        output: bytes | str,
        *,
        exit_code: int = 0,
        duration_ms: float = 0.0,
        media_type: str = "text/plain",
        max_lines: int = 60,
        command: str = "",
    ) -> FirewallReceipt:
        del max_lines  # compatibility: byte budgets replace line-count truncation.
        session = self.open(
            tool=tool,
            command=command or tool,
            exit_code=exit_code,
            duration_ms=duration_ms,
            media_type=media_type,
        )
        try:
            session.feed(output)
            return session.finalize()
        except Exception:
            session.abort()
            raise

    def manifest(self) -> dict[str, Any]:
        return {
            "engine": "canonical-terminal-output",
            "exact_disk_spooling": True,
            "bounded_memory": True,
            "never_worse": True,
            "command_compactors": self.command_compactor.registry.manifest(),
        }


__all__ = ["TerminalCaptureSession", "TerminalOutputEngine", "TerminalSnapshot"]
