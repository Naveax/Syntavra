from .platform_common import *

_LANGUAGE_BY_SUFFIX = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".rs": "rust", ".go": "go", ".java": "java", ".cs": "csharp",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".rb": "ruby", ".php": "php",
    ".lua": "lua", ".luau": "luau", ".kt": "kotlin", ".swift": "swift",
}
_GENERIC_SYMBOL_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:class|interface|struct|enum|trait|function|fn|def|func)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE,
)
_GENERIC_IMPORT_RE = re.compile(
    r"(?m)^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w./-]+)|use\s+([\w:]+)|require\(['\"]([^'\"]+))"
)

class IncrementalCodeIntelligenceGraph:
    """Incremental syntax graph with confidence and evidence provenance."""

    def __init__(self, path: Path):
        self.path = path
        with _connect(path) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS files (
                    path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, language TEXT NOT NULL, indexed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS nodes (
                    node_id TEXT PRIMARY KEY, path TEXT NOT NULL, kind TEXT NOT NULL,
                    name TEXT NOT NULL, qualified_name TEXT NOT NULL, start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL, language TEXT NOT NULL, evidence_ref TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_nodes_path ON nodes(path);
                CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
                CREATE TABLE IF NOT EXISTS edges (
                    source TEXT NOT NULL, target TEXT NOT NULL, edge_type TEXT NOT NULL,
                    confidence REAL NOT NULL, evidence_ref TEXT NOT NULL, metadata_json TEXT NOT NULL,
                    PRIMARY KEY(source, target, edge_type, evidence_ref)
                );
                """
            )

    @staticmethod
    def _node_id(path: str, kind: str, name: str, line: int) -> str:
        return sha256_bytes(f"{path}\0{kind}\0{name}\0{line}".encode("utf-8"))

    def _python(self, relative: str, text: str, evidence: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        tree = ast.parse(text, filename=relative)
        module_id = self._node_id(relative, "module", relative, 1)
        nodes.append({
            "node_id": module_id, "path": relative, "kind": "module", "name": Path(relative).stem,
            "qualified_name": relative, "start_line": 1, "end_line": max(1, len(text.splitlines())),
            "language": "python", "evidence_ref": evidence, "metadata_json": "{}",
        })
        symbols: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                node_id = self._node_id(relative, kind, node.name, node.lineno)
                symbols[node.name] = node_id
                nodes.append({
                    "node_id": node_id, "path": relative, "kind": kind, "name": node.name,
                    "qualified_name": f"{relative}:{node.name}", "start_line": node.lineno,
                    "end_line": getattr(node, "end_lineno", node.lineno), "language": "python",
                    "evidence_ref": evidence, "metadata_json": "{}",
                })
                edges.append({
                    "source": module_id, "target": node_id, "edge_type": "defines", "confidence": 1.0,
                    "evidence_ref": evidence, "metadata_json": "{}",
                })
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names]
                if isinstance(node, ast.ImportFrom) and node.module:
                    names.append(node.module)
                for name in names:
                    target = f"external:{name}"
                    edges.append({
                        "source": module_id, "target": target, "edge_type": "imports", "confidence": 0.95,
                        "evidence_ref": evidence, "metadata_json": json.dumps({"external": True}),
                    })
        for parent in ast.walk(tree):
            parent_id = module_id
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                parent_id = symbols.get(parent.name, module_id)
            for child in ast.iter_child_nodes(parent):
                if isinstance(child, ast.Call):
                    name = child.func.id if isinstance(child.func, ast.Name) else child.func.attr if isinstance(child.func, ast.Attribute) else ""
                    if name in symbols:
                        edges.append({
                            "source": parent_id, "target": symbols[name], "edge_type": "calls", "confidence": 0.9,
                            "evidence_ref": evidence, "metadata_json": "{}",
                        })
        return nodes, edges

    def _generic(self, relative: str, text: str, language: str, evidence: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        module_id = self._node_id(relative, "module", relative, 1)
        nodes = [{
            "node_id": module_id, "path": relative, "kind": "module", "name": Path(relative).stem,
            "qualified_name": relative, "start_line": 1, "end_line": max(1, len(text.splitlines())),
            "language": language, "evidence_ref": evidence, "metadata_json": "{}",
        }]
        edges: list[dict[str, Any]] = []
        for match in _GENERIC_SYMBOL_RE.finditer(text):
            name = match.group(1)
            line = text.count("\n", 0, match.start()) + 1
            node_id = self._node_id(relative, "symbol", name, line)
            nodes.append({
                "node_id": node_id, "path": relative, "kind": "symbol", "name": name,
                "qualified_name": f"{relative}:{name}", "start_line": line, "end_line": line,
                "language": language, "evidence_ref": evidence, "metadata_json": json.dumps({"source": "regex"}),
            })
            edges.append({
                "source": module_id, "target": node_id, "edge_type": "defines", "confidence": 0.7,
                "evidence_ref": evidence, "metadata_json": json.dumps({"source": "regex"}),
            })
        for match in _GENERIC_IMPORT_RE.finditer(text):
            name = next((value for value in match.groups() if value), "")
            if name:
                edges.append({
                    "source": module_id, "target": f"external:{name}", "edge_type": "imports", "confidence": 0.65,
                    "evidence_ref": evidence, "metadata_json": json.dumps({"external": True, "source": "regex"}),
                })
        return nodes, edges

    def index_repository(self, root: Path, *, max_file_bytes: int = 2_000_000) -> dict[str, Any]:
        root = root.resolve(strict=True)
        changed = 0
        skipped = 0
        errors: list[dict[str, str]] = []
        discovered: set[str] = set()
        with _connect(self.path) as db:
            for path in sorted(root.rglob("*")):
                if not path.is_file() or any(part in {".git", ".syntavra", "node_modules", ".venv", "venv", "dist", "build"} for part in path.parts):
                    continue
                language = _LANGUAGE_BY_SUFFIX.get(path.suffix.casefold())
                if not language or path.stat().st_size > max_file_bytes:
                    continue
                relative = path.relative_to(root).as_posix()
                discovered.add(relative)
                data = path.read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                previous = db.execute("SELECT sha256 FROM files WHERE path = ?", (relative,)).fetchone()
                if previous and previous["sha256"] == digest:
                    skipped += 1
                    continue
                evidence = f"sha256:{digest}"
                try:
                    text = data.decode("utf-8", errors="replace")
                    nodes, edges = self._python(relative, text, evidence) if language == "python" else self._generic(relative, text, language, evidence)
                except (SyntaxError, ValueError) as error:
                    errors.append({"path": relative, "error": f"{type(error).__name__}: {error}"})
                    continue
                old_ids = [row["node_id"] for row in db.execute("SELECT node_id FROM nodes WHERE path = ?", (relative,))]
                if old_ids:
                    placeholders = ",".join("?" for _ in old_ids)
                    db.execute(f"DELETE FROM edges WHERE source IN ({placeholders}) OR target IN ({placeholders})", (*old_ids, *old_ids))
                db.execute("DELETE FROM nodes WHERE path = ?", (relative,))
                for node in nodes:
                    db.execute(
                        """INSERT OR REPLACE INTO nodes
                           (node_id,path,kind,name,qualified_name,start_line,end_line,language,evidence_ref,metadata_json)
                           VALUES (:node_id,:path,:kind,:name,:qualified_name,:start_line,:end_line,:language,:evidence_ref,:metadata_json)""",
                        node,
                    )
                for edge in edges:
                    if edge["target"].startswith("external:"):
                        external_id = edge["target"]
                        db.execute(
                            """INSERT OR IGNORE INTO nodes
                               (node_id,path,kind,name,qualified_name,start_line,end_line,language,evidence_ref,metadata_json)
                               VALUES (?, ?, 'external', ?, ?, 0, 0, 'external', ?, '{}')""",
                            (external_id, relative, external_id.split(":", 1)[1], external_id, evidence),
                        )
                    db.execute(
                        """INSERT OR REPLACE INTO edges
                           (source,target,edge_type,confidence,evidence_ref,metadata_json)
                           VALUES (:source,:target,:edge_type,:confidence,:evidence_ref,:metadata_json)""",
                        edge,
                    )
                db.execute(
                    "INSERT OR REPLACE INTO files(path,sha256,language,indexed_at) VALUES(?,?,?,?)",
                    (relative, digest, language, _now()),
                )
                changed += 1
            stale = [row["path"] for row in db.execute("SELECT path FROM files") if row["path"] not in discovered]
            for relative in stale:
                ids = [row["node_id"] for row in db.execute("SELECT node_id FROM nodes WHERE path = ?", (relative,))]
                if ids:
                    placeholders = ",".join("?" for _ in ids)
                    db.execute(f"DELETE FROM edges WHERE source IN ({placeholders}) OR target IN ({placeholders})", (*ids, *ids))
                db.execute("DELETE FROM nodes WHERE path = ?", (relative,))
                db.execute("DELETE FROM files WHERE path = ?", (relative,))
        return {"ok": not errors, "changed_files": changed, "unchanged_files": skipped, "removed_files": len(stale), "errors": errors, **self.stats()}

    def query(self, text: str, *, limit: int = 20) -> list[dict[str, Any]]:
        query_tokens = _tokens(text)
        with _connect(self.path) as db:
            rows = db.execute("SELECT * FROM nodes WHERE kind != 'external'").fetchall()
            degrees = {
                row["node_id"]: row["degree"]
                for row in db.execute(
                    "SELECT node_id, COUNT(*) degree FROM (SELECT source node_id FROM edges UNION ALL SELECT target node_id FROM edges) GROUP BY node_id"
                )
            }
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            corpus = _tokens(f"{row['name']} {row['qualified_name']} {row['path']} {row['kind']} {row['language']}")
            matched = query_tokens & corpus
            if query_tokens and not matched:
                continue
            lexical = len(matched) / max(1, len(query_tokens))
            exact = 1.0 if text.casefold() in row["qualified_name"].casefold() else 0.0
            score = lexical * 70 + exact * 20 + min(10.0, degrees.get(row["node_id"], 0) * 0.5)
            value = dict(row)
            value["metadata"] = json.loads(value.pop("metadata_json"))
            value["score"] = score
            value["matched_terms"] = sorted(matched)
            value["degree"] = degrees.get(row["node_id"], 0)
            scored.append((score, value))
        return [value for _, value in sorted(scored, key=lambda item: (-item[0], item[1]["path"], item[1]["start_line"]))[:max(1, limit)]]

    def impact(self, node_id: str, *, max_depth: int = 6) -> dict[str, Any]:
        with _connect(self.path) as db:
            if not db.execute("SELECT 1 FROM nodes WHERE node_id = ?", (node_id,)).fetchone():
                raise KeyError(node_id)
            queue = [(node_id, 0)]
            seen = {node_id}
            ordered = [node_id]
            while queue:
                current, depth = queue.pop(0)
                if depth >= max_depth:
                    continue
                rows = db.execute(
                    "SELECT source FROM edges WHERE target = ? AND edge_type IN ('calls','imports','depends-on','implements','overrides','tested-by')",
                    (current,),
                ).fetchall()
                for row in rows:
                    candidate = row["source"]
                    if candidate not in seen:
                        seen.add(candidate)
                        ordered.append(candidate)
                        queue.append((candidate, depth + 1))
            placeholders = ",".join("?" for _ in ordered)
            nodes = [dict(row) for row in db.execute(f"SELECT * FROM nodes WHERE node_id IN ({placeholders})", ordered)]
        tests = [node for node in nodes if "test" in node["path"].casefold() or node["kind"].startswith("test")]
        return {"root": node_id, "impacted": nodes, "affected_tests": tests, "exact_evidence": all(node["evidence_ref"] for node in nodes)}

    def stats(self) -> dict[str, Any]:
        with _connect(self.path) as db:
            files = db.execute("SELECT COUNT(*) value FROM files").fetchone()["value"]
            nodes = db.execute("SELECT COUNT(*) value FROM nodes").fetchone()["value"]
            edges = db.execute("SELECT COUNT(*) value FROM edges").fetchone()["value"]
            languages = [dict(row) for row in db.execute("SELECT language, COUNT(*) files FROM files GROUP BY language ORDER BY language")]
        return {"files": int(files), "nodes": int(nodes), "edges": int(edges), "languages": languages}

