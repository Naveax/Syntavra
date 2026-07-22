from __future__ import annotations

import csv
import io
import json
import math
import re
import shlex
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

from .evidence import EvidenceStore
from .state import StateDB
from .util import canonical_json, sha256_bytes

_SECRET = re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|authorization|password|secret|bearer)\b\s*[:=]\s*([^\s,;]+)")
_ERROR = re.compile(r"(?i)\b(error|failed|failure|panic|assertion|traceback|exception|fatal|denied|timeout)\b")
_LOCATION = re.compile(r"(?:[^\s:]+\.(?:py|rs|js|ts|tsx|java|cs|go|rb|php|lua|luau|cpp|c|h):\d+|line \d+)")
_DIFF = re.compile(r"^(?:diff --git|index |--- |\+\+\+ |@@ )")
_TEST = re.compile(r"(?:^|\s)(?:pytest|py\.test|unittest|cargo\s+test|go\s+test|npm\s+test|pnpm\s+test|yarn\s+test|vitest|jest)(?:\s|$)", re.I)
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_.:/-]{1,}")
_CODE = {".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go", ".java", ".cs", ".c", ".cpp", ".h", ".hpp", ".rb", ".php", ".lua", ".luau"}
_UNSAFE_SHELL = ("|", "`", "$(", "<<", "\n", "\r")


class SessionLike(Protocol):
    def append(self, session_id: str, event_type: str, payload: dict[str, Any]) -> Any: ...
    def events(self, session_id: str, *, after: int = 0, limit: int = 1000) -> Iterable[Any]: ...


@dataclass(frozen=True)
class AdaptivePolicy:
    profile: str = "balanced"
    visible_budget_bytes: int = 4096
    passthrough_threshold_bytes: int = 768
    externalize_threshold_bytes: int = 8192
    chunk_size: int = 16 * 1024
    min_savings_ratio: float = 0.12
    deduplicate: bool = True
    search_window_lines: int = 2

    def __post_init__(self) -> None:
        if self.visible_budget_bytes < 256 or self.chunk_size < 1024:
            raise ValueError("invalid adaptive-context byte limits")
        if not 0 <= self.min_savings_ratio < 1:
            raise ValueError("min_savings_ratio must be in [0,1)")

    @classmethod
    def for_profile(cls, profile: str) -> "AdaptivePolicy":
        profiles = {
            "compact": cls("compact", 2048, 384, 4096, 8192, 0.08),
            "balanced": cls(),
            "audit": cls("audit", 8192, 1536, 16 * 1024, 32 * 1024, 0.18),
        }
        if profile not in profiles:
            raise ValueError(f"unknown adaptive profile: {profile}")
        return profiles[profile]

    @property
    def policy_hash(self) -> str:
        return sha256_bytes(canonical_json(asdict(self)))


@dataclass(frozen=True)
class ToolObservation:
    command: str = ""
    stdout: str = ""
    stderr: str = ""
    tool_name: str = "shell"
    path: str = ""
    scope_key: str = "default"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        if self.stdout and self.stderr:
            return self.stdout.rstrip("\n") + "\n[stderr]\n" + self.stderr
        return self.stdout or self.stderr


@dataclass(frozen=True)
class AdaptiveResult:
    capture_id: str
    family: str
    mode: str
    visible_text: str
    original_bytes: int
    visible_bytes: int
    savings_ratio: float
    exact_handle: str
    chunk_count: int
    content_hash: str
    quality_gate_passed: bool
    repeated: bool
    seen_count: int
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SearchHit:
    capture_id: str
    chunk_index: int
    start_line: int
    end_line: int
    score: float
    text: str
    exact_handle: str


class AdaptiveContextEngine:
    """Exact-first tool-output economy with deterministic previews and retrieval."""

    schema_version = 1

    def __init__(self, path: Path, *, evidence: EvidenceStore, policy: AdaptivePolicy | None = None):
        self.state = StateDB(path)
        self.evidence = evidence
        self.policy = policy or AdaptivePolicy()
        self._fts5 = False
        self._initialize()

    def _initialize(self) -> None:
        with self.state.transaction(immediate=True) as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS adaptive_captures(
              capture_id TEXT PRIMARY KEY,content_hash TEXT NOT NULL,family TEXT NOT NULL,
              mode TEXT NOT NULL,preview TEXT NOT NULL,original_bytes INTEGER NOT NULL,
              visible_bytes INTEGER NOT NULL,exact_handle TEXT NOT NULL,chunk_count INTEGER NOT NULL,
              policy_hash TEXT NOT NULL,quality_gate_passed INTEGER NOT NULL,metadata_json TEXT NOT NULL,
              created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS adaptive_chunks(
              capture_id TEXT NOT NULL,chunk_index INTEGER NOT NULL,exact_handle TEXT NOT NULL,
              chunk_bytes INTEGER NOT NULL,start_line INTEGER NOT NULL,end_line INTEGER NOT NULL,
              PRIMARY KEY(capture_id,chunk_index),
              FOREIGN KEY(capture_id) REFERENCES adaptive_captures(capture_id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS adaptive_seen(
              scope_key TEXT NOT NULL,identity_key TEXT NOT NULL,capture_id TEXT NOT NULL,
              seen_count INTEGER NOT NULL,first_seen REAL NOT NULL,last_seen REAL NOT NULL,
              PRIMARY KEY(scope_key,identity_key));
            """)
            try:
                db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS adaptive_search USING fts5(capture_id UNINDEXED,chunk_index UNINDEXED,start_line UNINDEXED,end_line UNINDEXED,content,tokenize='unicode61')")
                self._fts5 = True
            except Exception:
                self._fts5 = False

    @staticmethod
    def _redact(text: str) -> str:
        return _SECRET.sub(lambda match: f"{match.group(1)}=<redacted>", text)

    @staticmethod
    def _bounded(text: str, budget: int) -> str:
        raw = text.encode("utf-8")
        if len(raw) <= budget:
            return text
        suffix = "\n[… exact content available by capture id …]"
        keep = max(0, budget - len(suffix.encode("utf-8")))
        return raw[:keep].decode("utf-8", errors="ignore").rstrip() + suffix

    @staticmethod
    def _normalize_command(command: str) -> str:
        try:
            return " ".join(shlex.split(command, posix=True))
        except ValueError:
            return " ".join(command.split())

    @classmethod
    def meaningful_segment(cls, command: str) -> str:
        if not command or any(marker in command for marker in _UNSAFE_SHELL):
            return cls._normalize_command(command)
        parts = re.split(r"\s*(?:&&|;)\s*", command)
        return cls._normalize_command(parts[-1] if parts else command)

    @classmethod
    def classify(cls, observation: ToolObservation) -> str:
        command = "" if any(marker in observation.command for marker in _UNSAFE_SHELL) else cls.meaningful_segment(observation.command).casefold()
        suffix = Path(observation.path).suffix.casefold()
        text = observation.text.lstrip()
        if re.search(r"(?:^|\s)git\s+status(?:\s|$)", command): return "git-status"
        if re.search(r"(?:^|\s)(?:git|gh)\s+(?:diff|pr\s+diff)(?:\s|$)", command): return "diff"
        if re.search(r"(?:^|\s)git\s+log(?:\s|$)", command): return "git-log"
        if _TEST.search(command): return "test-output"
        if re.search(r"(?:^|\s)(?:grep|rg|find|fd|ls|tree)(?:\s|$)", command): return "search-list"
        if suffix in _CODE: return "code"
        if suffix in {".json", ".jsonl"} or text.startswith(("{", "[")): return "json"
        if suffix in {".csv", ".tsv"}: return "table"
        if any(_DIFF.match(line) for line in text.splitlines()[:20]): return "diff"
        lines = text.splitlines()
        if len(lines) > 120 or sum(bool(_ERROR.search(line)) for line in lines[:200]) >= 2: return "log"
        return "text"

    @staticmethod
    def _excerpt(line: str, max_bytes: int = 256) -> str:
        raw = line.encode("utf-8")
        if len(raw) <= max_bytes:
            return line
        marker = f" … <{len(raw)} bytes> … "
        remaining = max_bytes - len(marker.encode("utf-8"))
        head = raw[:max(64, remaining * 3 // 4)].decode("utf-8", errors="ignore")
        tail = raw[-max(32, remaining // 4):].decode("utf-8", errors="ignore")
        return head + marker + tail

    @classmethod
    def _critical(cls, text: str, family: str, budget: int) -> list[str]:
        errors: list[str] = []
        changed: list[str] = []
        for raw in text.splitlines():
            line = cls._redact(raw.rstrip())
            if not line: continue
            item = cls._excerpt(line)
            if (_ERROR.search(line) or (family in {"test-output", "log", "text"} and _LOCATION.search(line))) and item not in errors:
                errors.append(item)
            if family == "diff" and line.startswith(("+", "-")) and not line.startswith(("+++", "---")) and item not in changed:
                changed.append(item)
        sample = errors + (changed[:8] + changed[-4:] if family == "diff" else [])
        maximum = max(1, (budget - 384) // 280)
        return list(dict.fromkeys(sample))[:maximum]

    @classmethod
    def _preview(cls, family: str, text: str, path: str) -> str:
        lines = text.splitlines()
        if family == "git-status":
            kept = [line.strip() for line in lines if line.strip().startswith(("On branch ", "Your branch ", "modified:", "new file:", "deleted:", "Untracked files:", "Changes to be committed:", "Changes not staged"))]
            return "\n".join(dict.fromkeys(kept)) or text
        if family == "diff":
            changed = [line for line in lines if _DIFF.match(line) or (line.startswith(("+", "-")) and not line.startswith(("+++", "---")))]
            return f"Diff files={sum(line.startswith('diff --git') for line in lines)} changed_lines={sum(line.startswith(('+','-')) and not line.startswith(('+++','---')) for line in lines)}\n" + "\n".join(changed)
        if family == "git-log":
            out: list[str] = []; sha = ""
            for line in lines:
                if line.startswith("commit "): sha = line.split()[1][:12]
                elif line.startswith("    "): out.append(f"{sha} {line.strip()}".strip()); sha = ""
                elif re.match(r"^[0-9a-f]{7,40}\s", line): out.append(line.strip())
            return "\n".join(out) or text
        if family == "test-output":
            selected = [line for line in lines if _ERROR.search(line) or _LOCATION.search(line) or line.lstrip().startswith((">", "E ")) or re.search(r"\d+\s+(?:passed|failed|errors?|skipped)", line, re.I)]
            return "\n".join(dict.fromkeys(selected)) or text
        if family == "search-list":
            groups: dict[str, list[str]] = {}
            for line in lines:
                if not line.strip(): continue
                key = line.split(":", 1)[0] if ":" in line else str(Path(line).parent)
                groups.setdefault(key, []).append(line.strip())
            out = [f"Results={sum(map(len, groups.values()))} groups={len(groups)}"]
            for key, rows in list(groups.items())[:40]:
                out += [f"[{key}] {len(rows)}", *rows[:4]]
            return "\n".join(out)
        if family == "json":
            try: value = json.loads(text)
            except json.JSONDecodeError: return text
            def shrink(item: Any, depth: int = 0) -> Any:
                if depth >= 4 and isinstance(item, (dict, list)): return f"<{type(item).__name__}:{len(item)}>"
                if isinstance(item, dict):
                    keys = sorted(item, key=str); result = {str(k): shrink(item[k], depth + 1) for k in keys[:24]}
                    if len(keys) > 24: result["<omitted_keys>"] = len(keys) - 24
                    return result
                if isinstance(item, list):
                    return [shrink(v, depth + 1) for v in item] if len(item) <= 8 else {"<length>": len(item), "<head>": [shrink(v, depth + 1) for v in item[:4]], "<tail>": [shrink(v, depth + 1) for v in item[-2:]]}
                if isinstance(item, str) and len(item) > 300: return item[:160] + f"…<{len(item)} chars>…" + item[-60:]
                return item
            return json.dumps(shrink(value), ensure_ascii=False, sort_keys=True, indent=2)
        if family == "table":
            dialect = csv.excel_tab if Path(path).suffix.casefold() == ".tsv" else csv.excel
            rows = list(csv.reader(io.StringIO(text), dialect=dialect))
            if not rows: return "<empty table>"
            body = rows[1:]; sample = body[:5] + (body[-2:] if len(body) > 7 else [])
            return "\n".join([f"Rows={len(body)} Columns={len(rows[0])}", " | ".join(rows[0]), *(" | ".join(row[:len(rows[0])]) for row in sample)])
        if family == "code":
            pattern = re.compile(r"^\s*(?:class |def |async def |function |export |pub fn |fn |interface |type |struct |enum |impl |package |import |from )")
            out = [f"{number}: {line.rstrip()}" for number, line in enumerate(lines, 1) if pattern.search(line) or _ERROR.search(line) or "TODO" in line or "FIXME" in line]
            return "\n".join(out[:160]) or "\n".join(lines[:80])
        if family == "log":
            counts: dict[str, int] = {}; order: list[str] = []; critical: list[str] = []
            for raw in lines:
                line = cls._redact(raw.strip())
                if not line: continue
                shape = re.sub(r"\b(?:0x[0-9a-f]+|\d+(?:\.\d+)?)\b", "<n>", line, flags=re.I)
                if shape not in counts: counts[shape] = 0; order.append(shape)
                counts[shape] += 1
                if (_ERROR.search(line) or _LOCATION.search(line)) and line not in critical: critical.append(cls._excerpt(line))
            out = [f"Log lines={len(lines)} event_shapes={len(counts)}"]
            if critical: out += ["Critical:", *critical[:32]]
            out += ["Event shapes:", *(f"[{counts[s]}x] {s}" for s in order[:40])]
            return "\n".join(out)
        unique = list(dict.fromkeys(line.strip() for line in lines if line.strip()))
        return "\n".join(unique if len(unique) <= 40 else unique[:30] + unique[-10:])

    @staticmethod
    def _chunks(data: bytes, size: int) -> list[tuple[bytes, int, int]]:
        if not data: return [(b"", 1, 1)]
        output: list[tuple[bytes, int, int]] = []; line = 1
        for offset in range(0, len(data), size):
            chunk = data[offset:offset + size]; start = line; line += chunk.count(b"\n"); output.append((chunk, start, max(start, line)))
        return output

    def _seen(self, scope: str, identity: str) -> Mapping[str, Any] | None:
        with self.state.read() as db:
            row = db.execute("SELECT s.*,c.* FROM adaptive_seen s JOIN adaptive_captures c ON c.capture_id=s.capture_id WHERE s.scope_key=? AND s.identity_key=?", (scope, identity)).fetchone()
        return dict(row) if row else None

    def _touch_seen(self, scope: str, identity: str, capture_id: str) -> int:
        now = time.time()
        with self.state.transaction(immediate=True) as db:
            db.execute("INSERT INTO adaptive_seen VALUES(?,?,?,?,?,?) ON CONFLICT(scope_key,identity_key) DO UPDATE SET capture_id=excluded.capture_id,seen_count=adaptive_seen.seen_count+1,last_seen=excluded.last_seen", (scope, identity, capture_id, 1, now, now))
            return int(db.execute("SELECT seen_count FROM adaptive_seen WHERE scope_key=? AND identity_key=?", (scope, identity)).fetchone()[0])

    def process(self, observation: ToolObservation, *, session_runtime: SessionLike | None = None, session_id: str | None = None) -> AdaptiveResult:
        text = observation.text; raw = text.encode("utf-8"); content_hash = sha256_bytes(raw)
        family = self.classify(observation); command = self._normalize_command(observation.command)
        identity = sha256_bytes(canonical_json({"tool": observation.tool_name, "command": command, "path": observation.path, "content": content_hash, "policy": self.policy.policy_hash}))
        capture_id = "cap-" + sha256_bytes(canonical_json({"content": content_hash, "family": family, "command": sha256_bytes(command.encode()), "path": observation.path, "policy": self.policy.policy_hash}))[:32]
        if self.policy.deduplicate:
            existing = self._seen(observation.scope_key, identity)
            if existing and self.evidence.verify(existing["exact_handle"]):
                count = self._touch_seen(observation.scope_key, identity, capture_id)
                visible = f"[Syntavra dedup capture={capture_id} seen={count} exact={existing['exact_handle']}]"
                result = AdaptiveResult(capture_id, existing["family"], "dedup-reference", visible, int(existing["original_bytes"]), len(visible.encode()), 1 - len(visible.encode()) / max(1, int(existing["original_bytes"])), existing["exact_handle"], int(existing["chunk_count"]), existing["content_hash"], bool(existing["quality_gate_passed"]), True, count, json.loads(existing["metadata_json"]))
                self._record_session(session_runtime, session_id, observation, result); return result
        preview = self._redact(self._preview(family, text, observation.path))
        critical = self._critical(text, family, self.policy.visible_budget_bytes)
        header = f"[Syntavra AdaptiveContext v1 family={family} raw={len(raw)} capture={capture_id} exact=sc://sha256/{content_hash}]"
        candidate = header + ("\nCritical evidence:\n" + "\n".join(critical) if critical else "") + ("\nSummary:\n" + preview if preview else "")
        visible = self._bounded(candidate, self.policy.visible_budget_bytes)
        preliminary = 1 - len(visible.encode()) / max(1, len(raw))
        if len(raw) <= self.policy.passthrough_threshold_bytes or preliminary < self.policy.min_savings_ratio:
            visible = self._bounded(self._redact(text), self.policy.visible_budget_bytes); mode = "passthrough-captured"
        elif family in {"git-status", "diff", "git-log", "test-output", "search-list"}: mode = "command-compact"
        elif len(raw) >= self.policy.externalize_threshold_bytes: mode = "externalized"
        else: mode = "content-compact"
        quality = all(item in visible for item in critical)
        exact = self.evidence.put(raw, kind=f"adaptive-context:{family}", metadata={"capture_id": capture_id, "path": observation.path})
        chunks = self._chunks(raw, self.policy.chunk_size); chunk_rows = []
        for index, (chunk, start, end) in enumerate(chunks):
            handle = self.evidence.put(chunk, kind="adaptive-context-chunk", metadata={"capture_id": capture_id, "chunk_index": index})
            chunk_rows.append((capture_id, index, handle, len(chunk), start, end))
        metadata = {"schema_version": 1, "tool_name": observation.tool_name, "command": command, "path": observation.path, "scope_key": observation.scope_key, "critical_lines": len(critical), "policy": asdict(self.policy), **observation.metadata}
        with self.state.transaction(immediate=True) as db:
            db.execute("INSERT OR REPLACE INTO adaptive_captures VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (capture_id, content_hash, family, mode, visible, len(raw), len(visible.encode()), exact, len(chunks), self.policy.policy_hash, int(quality), json.dumps(metadata, ensure_ascii=False, sort_keys=True), time.time()))
            db.execute("DELETE FROM adaptive_chunks WHERE capture_id=?", (capture_id,)); db.executemany("INSERT INTO adaptive_chunks VALUES(?,?,?,?,?,?)", chunk_rows)
            if self._fts5:
                db.execute("DELETE FROM adaptive_search WHERE capture_id=?", (capture_id,))
                db.executemany("INSERT INTO adaptive_search VALUES(?,?,?,?,?)", [(capture_id, i, s, e, c.decode("utf-8", errors="replace")) for i, (c, s, e) in enumerate(chunks)])
        count = self._touch_seen(observation.scope_key, identity, capture_id); visible_bytes = len(visible.encode())
        result = AdaptiveResult(capture_id, family, mode, visible, len(raw), visible_bytes, 1 - visible_bytes / max(1, len(raw)), exact, len(chunks), content_hash, quality, False, count, metadata)
        self._record_session(session_runtime, session_id, observation, result); return result

    @staticmethod
    def _record_session(runtime: SessionLike | None, session_id: str | None, observation: ToolObservation, result: AdaptiveResult) -> None:
        if runtime and session_id:
            runtime.append(session_id, "adaptive-context", {"capture_id": result.capture_id, "exact_handle": result.exact_handle, "family": result.family, "mode": result.mode, "path": observation.path, "command": observation.command[:500], "summary": result.visible_text[:1200], "quality_gate_passed": result.quality_gate_passed, "original_bytes": result.original_bytes, "visible_bytes": result.visible_bytes})

    def capture(self, capture_id: str) -> dict[str, Any]:
        with self.state.read() as db:
            row = db.execute("SELECT * FROM adaptive_captures WHERE capture_id=?", (capture_id,)).fetchone(); chunks = db.execute("SELECT * FROM adaptive_chunks WHERE capture_id=? ORDER BY chunk_index", (capture_id,)).fetchall()
        if not row: raise KeyError(capture_id)
        value = dict(row); value["metadata"] = json.loads(value.pop("metadata_json")); value["chunks"] = [dict(chunk) for chunk in chunks]; return value

    def restore(self, capture_id: str, *, chunk_index: int | None = None) -> bytes:
        value = self.capture(capture_id)
        if chunk_index is None: return self.evidence.get(value["exact_handle"])
        if chunk_index < 0 or chunk_index >= len(value["chunks"]): raise IndexError(chunk_index)
        return self.evidence.get(value["chunks"][chunk_index]["exact_handle"])

    def verify(self, capture_id: str) -> dict[str, Any]:
        value = self.capture(capture_id); raw = self.evidence.get(value["exact_handle"]); rebuilt = b"".join(self.evidence.get(chunk["exact_handle"]) for chunk in value["chunks"]); reasons = []
        if sha256_bytes(raw) != value["content_hash"]: reasons.append("content-hash-mismatch")
        if raw != rebuilt: reasons.append("chunk-roundtrip-mismatch")
        if len(raw) != int(value["original_bytes"]): reasons.append("size-mismatch")
        if not bool(value["quality_gate_passed"]): reasons.append("quality-gate-failed")
        return {"ok": not reasons, "capture_id": capture_id, "bytes": len(raw), "chunks": len(value["chunks"]), "reasons": reasons}

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {token.casefold() for token in _WORD.findall(text) if len(token) > 1}

    def search(self, capture_id: str, query: str, *, limit: int = 8) -> list[SearchHit]:
        value = self.capture(capture_id); query_tokens = self._tokens(query)
        if not query_tokens: return []
        candidates = value["chunks"]
        if self._fts5:
            try:
                expression = " OR ".join(sorted(query_tokens))
                with self.state.read() as db: selected = {int(row[0]) for row in db.execute("SELECT chunk_index FROM adaptive_search WHERE adaptive_search MATCH ? AND capture_id=? ORDER BY bm25(adaptive_search) LIMIT 32", (expression, capture_id))}
                if selected: candidates = [chunk for chunk in candidates if int(chunk["chunk_index"]) in selected]
            except Exception: pass
        hits: list[SearchHit] = []
        for chunk in candidates:
            lines = self.evidence.get(chunk["exact_handle"]).decode("utf-8", errors="replace").splitlines()
            for index, line in enumerate(lines):
                overlap = query_tokens & self._tokens(line); score = len(overlap) * 3 + (4 if query.casefold() in line.casefold() else 0) + (1.5 if _ERROR.search(line) else 0)
                if score <= 0: continue
                start = max(0, index - self.policy.search_window_lines); end = min(len(lines), index + self.policy.search_window_lines + 1)
                hits.append(SearchHit(capture_id, int(chunk["chunk_index"]), int(chunk["start_line"]) + start, int(chunk["start_line"]) + end - 1, score, "\n".join(lines[start:end]), value["exact_handle"]))
        hits.sort(key=lambda item: (-item.score, item.chunk_index, item.start_line)); output: list[SearchHit] = []; seen = set()
        for hit in hits:
            key = (hit.chunk_index, hit.start_line, hit.end_line)
            if key in seen: continue
            seen.add(key); output.append(hit)
            if len(output) >= max(1, limit): break
        return output

    def recall(self, runtime: SessionLike, session_id: str, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
        tokens = self._tokens(query); rows = []
        for event in runtime.events(session_id, limit=1_000_000):
            if getattr(event, "event_type", "") != "adaptive-context": continue
            payload = getattr(event, "payload", {}); haystack = " ".join(str(payload.get(key, "")) for key in ("path", "command", "summary", "family", "mode")); score = len(tokens & self._tokens(haystack)) * 3 + (4 if query.casefold() in haystack.casefold() else 0)
            if score > 0: rows.append((score, int(getattr(event, "sequence", 0)), payload))
        rows.sort(key=lambda row: (-row[0], -row[1])); return [{"score": score, "sequence": sequence, **payload} for score, sequence, payload in rows[:max(1, limit)]]

    def stats(self) -> dict[str, Any]:
        with self.state.read() as db:
            row = db.execute("SELECT COUNT(*) captures,COALESCE(SUM(original_bytes),0) original,COALESCE(SUM(visible_bytes),0) visible,COALESCE(SUM(quality_gate_passed),0) quality FROM adaptive_captures").fetchone(); repeats = db.execute("SELECT COALESCE(SUM(seen_count-1),0) FROM adaptive_seen").fetchone()[0]; families = {item[0]: int(item[1]) for item in db.execute("SELECT family,COUNT(*) FROM adaptive_captures GROUP BY family")}
        original = int(row["original"]); visible = int(row["visible"])
        return {"captures": int(row["captures"]), "original_bytes": original, "visible_bytes": visible, "savings_ratio": 1 - visible / max(1, original), "quality_passes": int(row["quality"]), "repeat_reads_elided": int(repeats), "families": families, "fts5_enabled": self._fts5}
