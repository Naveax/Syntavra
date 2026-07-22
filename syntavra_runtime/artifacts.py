from .platform_common import *

@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    sha256: str
    media_type: str
    kind: str
    byte_count: int
    created_at: str
    object_path: str
    metadata: dict[str, Any] = field(default_factory=dict)


class ArtifactStore:
    """Exact, content-addressed storage with bounded query views."""

    def __init__(self, root: Path):
        self.root = root
        self.objects = root / "objects"
        self.objects.mkdir(parents=True, exist_ok=True)
        self.db_path = root / "artifacts.sqlite3"
        with _connect(self.db_path) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    sha256 TEXT NOT NULL UNIQUE,
                    media_type TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    byte_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    object_path TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_artifacts_kind ON artifacts(kind, created_at);
                """
            )

    def _object_path(self, digest: str) -> Path:
        return self.objects / digest[:2] / digest[2:4] / digest

    def put(
        self,
        value: bytes | str,
        *,
        media_type: str = "text/plain",
        kind: str = "generic",
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactRecord:
        data = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        digest = hashlib.sha256(data).hexdigest()
        artifact_id = f"sha256:{digest}"
        target = self._object_path(digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            temporary = target.with_name(f".{target.name}.{secrets.token_hex(6)}.tmp")
            temporary.write_bytes(data)
            os.replace(temporary, target)
        created = _now()
        metadata_json = canonical_json(dict(metadata or {})).decode("utf-8")
        with _connect(self.db_path) as db:
            db.execute(
                """INSERT OR IGNORE INTO artifacts
                   (artifact_id, sha256, media_type, kind, byte_count, created_at, object_path, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (artifact_id, digest, media_type, kind, len(data), created, str(target), metadata_json),
            )
            row = db.execute("SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone()
        if row is None:
            raise RuntimeError("artifact metadata write failed")
        return self._record(row)

    @staticmethod
    def _record(row: sqlite3.Row) -> ArtifactRecord:
        return ArtifactRecord(
            artifact_id=row["artifact_id"],
            sha256=row["sha256"],
            media_type=row["media_type"],
            kind=row["kind"],
            byte_count=int(row["byte_count"]),
            created_at=row["created_at"],
            object_path=row["object_path"],
            metadata=json.loads(row["metadata_json"] or "{}"),
        )

    def record(self, artifact_id: str) -> ArtifactRecord:
        with _connect(self.db_path) as db:
            row = db.execute("SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone()
        if row is None:
            raise KeyError(artifact_id)
        return self._record(row)

    def read(self, artifact_id: str) -> bytes:
        record = self.record(artifact_id)
        data = Path(record.object_path).read_bytes()
        if hashlib.sha256(data).hexdigest() != record.sha256:
            raise ValueError(f"artifact integrity failure: {artifact_id}")
        return data

    def query(
        self,
        artifact_id: str,
        *,
        mode: str = "head",
        expression: str = "",
        limit: int = 80,
    ) -> dict[str, Any]:
        record = self.record(artifact_id)
        data = self.read(artifact_id)
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        limit = max(1, min(limit, 1000))
        selected: list[str]
        if mode == "head":
            selected = lines[:limit]
        elif mode == "tail":
            selected = lines[-limit:]
        elif mode == "errors":
            selected = [line for line in lines if _ERROR_RE.search(line) or _LOCATION_RE.search(line)][:limit]
        elif mode == "regex":
            pattern = re.compile(expression)
            selected = [line for line in lines if pattern.search(line)][:limit]
        elif mode == "failures":
            selected = [line for line in lines if re.search(r"(?i)(fail|error|traceback|panic|assert)", line)][:limit]
        elif mode == "json":
            parsed = json.loads(text)
            current: Any = parsed
            for part in [item for item in expression.split(".") if item]:
                if isinstance(current, Mapping):
                    current = current[part]
                elif isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
                    current = current[int(part)]
                else:
                    raise KeyError(expression)
            selected = json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True).splitlines()[:limit]
        else:
            raise ValueError(f"unsupported artifact query mode: {mode}")
        rendered = _redact("\n".join(selected))
        return {
            "ok": True,
            "artifact": asdict(record),
            "mode": mode,
            "expression": expression,
            "matched_lines": len(selected),
            "view": rendered,
            "view_tokens": _estimate_tokens(rendered),
        }

    def verify(self, artifact_id: str | None = None) -> dict[str, Any]:
        with _connect(self.db_path) as db:
            rows = db.execute(
                "SELECT * FROM artifacts" + (" WHERE artifact_id = ?" if artifact_id else ""),
                ((artifact_id,) if artifact_id else ()),
            ).fetchall()
        failures: list[str] = []
        for row in rows:
            record = self._record(row)
            path = Path(record.object_path)
            if not path.is_file():
                failures.append(f"missing:{record.artifact_id}")
                continue
            if hashlib.sha256(path.read_bytes()).hexdigest() != record.sha256:
                failures.append(f"hash:{record.artifact_id}")
        return {"ok": not failures, "checked": len(rows), "failures": failures}

    def stats(self) -> dict[str, Any]:
        with _connect(self.db_path) as db:
            row = db.execute(
                "SELECT COUNT(*) count, COALESCE(SUM(byte_count), 0) bytes FROM artifacts"
            ).fetchone()
            kinds = db.execute(
                "SELECT kind, COUNT(*) count, SUM(byte_count) bytes FROM artifacts GROUP BY kind ORDER BY kind"
            ).fetchall()
        return {
            "artifacts": int(row["count"]),
            "exact_bytes": int(row["bytes"]),
            "kinds": [dict(item) for item in kinds],
        }


@dataclass(frozen=True)
class FirewallReceipt:
    kind: str
    artifact_id: str
    original_bytes: int
    visible_bytes: int
    estimated_original_tokens: int
    estimated_visible_tokens: int
    savings_ratio: float
    compact_view: str
    query_modes: tuple[str, ...]
    exact_recovery: bool
    critical_lines: tuple[str, ...]


class OutputFirewall:
    """Typed pre-context output interception with exact recovery."""

    def __init__(self, store: ArtifactStore):
        self.store = store

    @staticmethod
    def classify(tool: str, text: str, media_type: str = "") -> str:
        lower = tool.casefold()
        stripped = text.lstrip()
        if media_type.endswith("json") or stripped.startswith(("{", "[")):
            try:
                json.loads(text)
                return "json"
            except (json.JSONDecodeError, TypeError):
                pass
        if "diff" in lower or text.startswith("diff --git"):
            return "diff"
        if any(token in lower for token in ("pytest", "unittest", "jest", "vitest", "cargo test", "go test")):
            return "test"
        if any(token in lower for token in ("grep", "rg", "search", "find")):
            return "search"
        if any(token in lower for token in ("read", "cat", "source", "file")):
            return "source"
        return "shell"

    @staticmethod
    def _unique(lines: Iterable[str], limit: int) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for line in lines:
            normalized = line.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(line)
            if len(result) >= limit:
                break
        return result

    def _compact(self, kind: str, text: str, exit_code: int, max_lines: int) -> tuple[str, list[str]]:
        clean = _redact(text.replace("\r", ""))
        lines = clean.splitlines()
        critical = self._unique(
            (line for line in lines if _ERROR_RE.search(line) or _LOCATION_RE.search(line)),
            max(10, max_lines // 2),
        )
        if kind == "json":
            parsed = json.loads(text)
            if isinstance(parsed, Mapping):
                keys = sorted(str(key) for key in parsed)[:50]
                view = json.dumps({"type": "object", "keys": keys, "key_count": len(parsed)}, indent=2)
            elif isinstance(parsed, list):
                sample = parsed[:3]
                view = json.dumps({"type": "array", "items": len(parsed), "sample": sample}, ensure_ascii=False, indent=2)
            else:
                view = json.dumps(parsed, ensure_ascii=False)
            return view, critical
        if kind == "diff":
            headers = [line for line in lines if line.startswith(("diff --git", "--- ", "+++ ", "@@ "))]
            changes = [line for line in lines if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
            view_lines = self._unique(headers + changes[:max_lines], max_lines)
            return "\n".join(view_lines), critical
        if kind == "test":
            summaries = [
                line for line in lines
                if re.search(r"(?i)(\d+\s+(?:passed|failed|errors?|skipped)|test result:|tests?:)", line)
            ]
            view_lines = self._unique(critical + summaries + lines[-10:], max_lines)
            return "\n".join(view_lines), critical
        if kind == "search":
            grouped: dict[str, int] = defaultdict(int)
            examples: list[str] = []
            for line in lines:
                prefix = line.split(":", 1)[0]
                grouped[prefix] += 1
                if len(examples) < max_lines:
                    examples.append(line)
            header = [f"{path}: {count} matches" for path, count in sorted(grouped.items(), key=lambda item: (-item[1], item[0]))[:20]]
            return "\n".join(header + self._unique(examples, max_lines)), critical
        if kind == "source":
            symbols = [
                line.strip() for line in lines
                if re.match(r"\s*(?:async\s+def|def|class|function|export\s+(?:class|function)|fn|struct|interface)\s+", line)
            ]
            view_lines = self._unique(symbols + lines[:15] + critical, max_lines)
            return "\n".join(view_lines), critical
        # Shell/general output: preserve failures, fold repeated lines and keep tail.
        counts = Counter(line for line in lines if line.strip())
        repeated = [f"[{count}x] {line}" for line, count in counts.most_common(15) if count > 2]
        view_lines = self._unique(critical + repeated + lines[:8] + lines[-12:], max_lines)
        if exit_code != 0 and not critical:
            view_lines = self._unique(lines[: max_lines // 2] + lines[-max_lines // 2 :], max_lines)
        return "\n".join(view_lines), critical

    def capture(
        self,
        tool: str,
        output: bytes | str,
        *,
        exit_code: int = 0,
        duration_ms: float = 0.0,
        media_type: str = "text/plain",
        max_lines: int = 60,
    ) -> FirewallReceipt:
        data = output.encode("utf-8") if isinstance(output, str) else bytes(output)
        text = data.decode("utf-8", errors="replace")
        kind = self.classify(tool, text, media_type)
        record = self.store.put(
            data,
            media_type=media_type,
            kind=f"tool-output:{kind}",
            metadata={"tool": tool, "exit_code": exit_code, "duration_ms": duration_ms},
        )
        compact, critical = self._compact(kind, text, exit_code, max_lines)
        header = (
            f"Tool: {tool}\nParser: {kind}\nExit code: {exit_code}\n"
            f"Exact output: artifact://{record.artifact_id}\n"
        )
        view = header + compact
        original_tokens = _estimate_tokens(text)
        visible_tokens = _estimate_tokens(view)
        ratio = 1.0 - (len(view.encode("utf-8")) / max(1, len(data)))
        modes = {
            "json": ("head", "tail", "json", "regex"),
            "test": ("failures", "errors", "head", "tail", "regex"),
        }.get(kind, ("head", "tail", "errors", "regex"))
        return FirewallReceipt(
            kind=kind,
            artifact_id=record.artifact_id,
            original_bytes=len(data),
            visible_bytes=len(view.encode("utf-8")),
            estimated_original_tokens=original_tokens,
            estimated_visible_tokens=visible_tokens,
            savings_ratio=max(-1.0, min(1.0, ratio)),
            compact_view=view,
            query_modes=tuple(modes),
            exact_recovery=self.store.verify(record.artifact_id)["ok"],
            critical_lines=tuple(critical),
        )


@dataclass(frozen=True)
class ContextIRItem:
    item_id: str
    layer: str
    kind: str
    source: str
    content: str
    priority: float = 0.5
    stable: bool = False
    exact_required: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextPack:
    provider: str
    model: str
    budget_tokens: int
    used_tokens: int
    cache_prefix_hash: str
    items: tuple[dict[str, Any], ...]
    omitted: tuple[dict[str, Any], ...]
    artifacts: tuple[str, ...]
    pack_hash: str
    deterministic: bool


class ContextCompiler:
    """Typed, delta-aware context compiler with stable-prefix ordering."""

    LAYERS = {
        "system": 0,
        "repository": 1,
        "tools": 2,
        "memory": 3,
        "task": 4,
        "user": 5,
    }

    def __init__(self, store: ArtifactStore):
        self.store = store
        self.firewall = OutputFirewall(store)

    @staticmethod
    def _kind(content: str, source: str) -> str:
        lower = source.casefold()
        if lower.endswith(('.py', '.ts', '.tsx', '.js', '.rs', '.go', '.java', '.cs', '.cpp', '.c')):
            return "source"
        if lower.endswith(('.json', '.jsonl')):
            return "json"
        if "diff" in lower or content.startswith("diff --git"):
            return "diff"
        if _ERROR_RE.search(content):
            return "diagnostic"
        return "text"

    @staticmethod
    def _delta(previous: str, current: str) -> str:
        if not previous or previous == current:
            return current
        diff = list(difflib.unified_diff(previous.splitlines(), current.splitlines(), lineterm="", n=2))
        rendered = "\n".join(diff)
        return rendered if rendered and len(rendered) < len(current) * 0.75 else current

    def compile(
        self,
        items: Sequence[ContextIRItem | Mapping[str, Any]],
        *,
        provider: str = "generic",
        model: str = "unknown",
        budget_tokens: int = 32_000,
        previous: Mapping[str, str] | None = None,
        externalize_threshold_bytes: int = 8_192,
    ) -> ContextPack:
        previous = dict(previous or {})
        normalized: list[ContextIRItem] = []
        seen_content: set[str] = set()
        artifact_ids: list[str] = []
        for raw in items:
            item = raw if isinstance(raw, ContextIRItem) else ContextIRItem(**dict(raw))
            content = item.content.replace("\r\n", "\n")
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if digest in seen_content:
                continue
            seen_content.add(digest)
            kind = item.kind or self._kind(content, item.source)
            content = self._delta(previous.get(item.item_id, ""), content)
            if len(content.encode("utf-8")) > externalize_threshold_bytes:
                receipt = self.firewall.capture(item.source, content, media_type="text/plain")
                artifact_ids.append(receipt.artifact_id)
                content = receipt.compact_view
            normalized.append(
                ContextIRItem(
                    item_id=item.item_id,
                    layer=item.layer,
                    kind=kind,
                    source=item.source,
                    content=content,
                    priority=max(0.0, min(1.0, float(item.priority))),
                    stable=bool(item.stable),
                    exact_required=bool(item.exact_required),
                    metadata=dict(item.metadata),
                )
            )
        ordered = sorted(
            normalized,
            key=lambda item: (
                self.LAYERS.get(item.layer, 99),
                0 if item.stable else 1,
                -item.priority,
                item.source,
                item.item_id,
            ),
        )
        selected: list[dict[str, Any]] = []
        omitted: list[dict[str, Any]] = []
        used = 0
        stable_payload: list[dict[str, Any]] = []
        for item in ordered:
            tokens = _estimate_tokens(item.content, provider)
            row = {
                "item_id": item.item_id,
                "layer": item.layer,
                "kind": item.kind,
                "source": item.source,
                "content": item.content,
                "tokens": tokens,
                "priority": item.priority,
                "stable": item.stable,
                "exact_required": item.exact_required,
                "metadata": item.metadata,
            }
            if used + tokens <= max(1, budget_tokens):
                selected.append(row)
                used += tokens
                if item.stable:
                    stable_payload.append(row)
            else:
                omitted.append({key: value for key, value in row.items() if key != "content"} | {"reason": "budget"})
        prefix_hash = sha256_bytes(canonical_json(stable_payload))
        pack_body = {
            "version": VERSION,
            "channel": CHANNEL,
            "provider": provider,
            "model": model,
            "budget_tokens": budget_tokens,
            "items": selected,
            "artifacts": sorted(set(artifact_ids)),
        }
        return ContextPack(
            provider=provider,
            model=model,
            budget_tokens=budget_tokens,
            used_tokens=used,
            cache_prefix_hash=prefix_hash,
            items=tuple(selected),
            omitted=tuple(omitted),
            artifacts=tuple(sorted(set(artifact_ids))),
            pack_hash=sha256_bytes(canonical_json(pack_body)),
            deterministic=True,
        )
