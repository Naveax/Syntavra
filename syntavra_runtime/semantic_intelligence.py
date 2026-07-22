from .platform_common import *
from .language_platform import LanguageDetection, LanguageParseResult, LanguageRegistry
from .language_services import LanguageServiceRegistry, SandboxedLanguageServiceAdapter
from .language_lsp import GenericLSPAdapter, LSPServiceRegistry
from .semantic_index_store import SemanticIndexStore


_DECLARATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "type",
        re.compile(
            r"(?im)^\s*(?:export\s+|public\s+|private\s+|protected\s+|internal\s+|open\s+|abstract\s+|sealed\s+|partial\s+)*"
            r"(?:class|interface|struct|enum|trait|protocol|record|object|type|data|union|module|namespace|package)\s+"
            r"(?P<name>(?!\d)\w+(?:[.$:]\w+)*)"
        ),
    ),
    (
        "callable",
        re.compile(
            r"(?im)^\s*(?:export\s+|public\s+|private\s+|protected\s+|internal\s+|static\s+|async\s+|inline\s+|virtual\s+|override\s+|final\s+)*"
            r"(?:function|fn|def|func|fun|sub|proc|procedure|method|macro|task|rule)\s+"
            r"(?P<name>(?!\d)\w+(?:[.$:]\w+)*)"
        ),
    ),
    ("shell-function", re.compile(r"(?m)^\s*(?:function\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)\s*\{")),
    ("assigned-callable", re.compile(r"(?m)^\s*(?:export\s+)?(?:const|let|var|val|define)\s+(?P<name>(?!\d)\w+)\s*(?::[^=]+)?=\s*(?:async\s+)?(?:function\b|\([^\n]*\)\s*=>)")),
    ("lisp-definition", re.compile(r"(?im)^\s*\((?:defun|defmacro|define|defn|def|define-syntax)\s+\(?(?P<name>(?!\d)[^\s()]+)")),
    ("logic-rule", re.compile(r"(?m)^\s*(?P<name>[a-z][A-Za-z0-9_]*)\s*\([^\n]*\)\s*:-")),
    ("assembly-label", re.compile(r"(?m)^\s*(?P<name>[A-Za-z_.$][A-Za-z0-9_.$@]*)\s*:\s*(?:[#;].*)?$")),
)

_IMPORT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?m)^\s*(?:from\s+(?P<from>[\w./:@+-]+)\s+import|import\s+(?P<import>[\w./:@+-]+)|use\s+(?P<use>[\w:]+)|require\s*\(\s*['\"](?P<require>[^'\"]+))"),
    re.compile(r"(?m)^\s*(?:include|require_once|source|load|using|open)\s*[(' \t]+(?P<include>[\w./:@+-]+)"),
)

_IDENTIFIER_RE = re.compile(r"(?u)(?!\d)\w[\w.$:@-]{2,}")
_IGNORE_PARTS = frozenset({
    ".git", ".hg", ".svn", ".syntavra", "node_modules", ".venv", "venv", "dist", "build",
    "target", "vendor", "coverage", ".cache", "__pycache__", ".mypy_cache", ".pytest_cache",
})


class IncrementalCodeIntelligenceGraph:
    """Language-agnostic incremental graph with graded semantic evidence.

    Every decodable text file is indexable. Registered grammars/adapters raise
    precision; unknown and future languages fall back to conservative lexical
    structure and are never represented as exact semantic facts.
    """

    def __init__(
        self,
        path: Path,
        *,
        language_registry: LanguageRegistry | None = None,
        language_service_registry: LanguageServiceRegistry | None = None,
        lsp_service_registry: LSPServiceRegistry | None = None,
    ):
        self.path = path
        self.languages = language_registry or LanguageRegistry()
        self.language_services = language_service_registry or LanguageServiceRegistry()
        self.lsp_services = lsp_service_registry or LSPServiceRegistry()
        with _connect(path) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS files (
                    path TEXT PRIMARY KEY,
                    sha256 TEXT NOT NULL,
                    language TEXT NOT NULL,
                    indexed_at TEXT NOT NULL,
                    analysis_key TEXT NOT NULL DEFAULT '',
                    detector TEXT NOT NULL DEFAULT 'legacy',
                    confidence REAL NOT NULL DEFAULT 0.0,
                    capability_level TEXT NOT NULL DEFAULT 'lexical',
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS nodes (
                    node_id TEXT PRIMARY KEY, path TEXT NOT NULL, kind TEXT NOT NULL,
                    name TEXT NOT NULL, qualified_name TEXT NOT NULL, start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL, language TEXT NOT NULL, evidence_ref TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_nodes_path ON nodes(path);
                CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
                CREATE INDEX IF NOT EXISTS idx_nodes_language ON nodes(language);
                CREATE TABLE IF NOT EXISTS edges (
                    source TEXT NOT NULL, target TEXT NOT NULL, edge_type TEXT NOT NULL,
                    confidence REAL NOT NULL, evidence_ref TEXT NOT NULL, metadata_json TEXT NOT NULL,
                    PRIMARY KEY(source, target, edge_type, evidence_ref)
                );
                """
            )
            existing = {row[1] for row in db.execute("PRAGMA table_info(files)")}
            migrations = {
                "analysis_key": "ALTER TABLE files ADD COLUMN analysis_key TEXT NOT NULL DEFAULT ''",
                "detector": "ALTER TABLE files ADD COLUMN detector TEXT NOT NULL DEFAULT 'legacy'",
                "confidence": "ALTER TABLE files ADD COLUMN confidence REAL NOT NULL DEFAULT 0.0",
                "capability_level": "ALTER TABLE files ADD COLUMN capability_level TEXT NOT NULL DEFAULT 'lexical'",
                "metadata_json": "ALTER TABLE files ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'",
            }
            for column, statement in migrations.items():
                if column not in existing:
                    db.execute(statement)
        self.semantic_indexes = SemanticIndexStore(path)

    @staticmethod
    def _node_id(path: str, kind: str, name: str, line: int) -> str:
        return sha256_bytes(f"{path}\0{kind}\0{name}\0{line}".encode("utf-8"))

    @staticmethod
    def _metadata(**values: Any) -> str:
        return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _module_node(
        self,
        relative: str,
        text: str,
        language: str,
        evidence: str,
        detection: LanguageDetection,
        *,
        exact: bool,
        source: str,
    ) -> dict[str, Any]:
        return {
            "node_id": self._node_id(relative, "module", relative, 1),
            "path": relative,
            "kind": "module",
            "name": Path(relative).stem or Path(relative).name,
            "qualified_name": relative,
            "start_line": 1,
            "end_line": max(1, len(text.splitlines())),
            "language": language,
            "evidence_ref": evidence,
            "metadata_json": self._metadata(
                source=source,
                exact_semantic=exact,
                capability_level=detection.capability_level,
                detection_confidence=detection.confidence,
                detection_evidence=detection.evidence,
                generated=detection.generated,
                minified=detection.minified,
            ),
        }

    def _python(
        self,
        relative: str,
        text: str,
        evidence: str,
        detection: LanguageDetection,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        tree = ast.parse(text, filename=relative)
        module = self._module_node(relative, text, "python", evidence, detection, exact=True, source="python-ast")
        module_id = module["node_id"]
        nodes.append(module)
        symbols: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                node_id = self._node_id(relative, kind, node.name, node.lineno)
                symbols[node.name] = node_id
                nodes.append({
                    "node_id": node_id,
                    "path": relative,
                    "kind": kind,
                    "name": node.name,
                    "qualified_name": f"{relative}:{node.name}",
                    "start_line": node.lineno,
                    "end_line": getattr(node, "end_lineno", node.lineno),
                    "language": "python",
                    "evidence_ref": evidence,
                    "metadata_json": self._metadata(source="python-ast", exact_semantic=True, capability_level="syntax"),
                })
                edges.append({
                    "source": module_id,
                    "target": node_id,
                    "edge_type": "defines",
                    "confidence": 1.0,
                    "evidence_ref": evidence,
                    "metadata_json": self._metadata(source="python-ast", exact_semantic=True),
                })
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names]
                if isinstance(node, ast.ImportFrom) and node.module:
                    names.append(node.module)
                for name in names:
                    target = f"external:{name}"
                    edges.append({
                        "source": module_id,
                        "target": target,
                        "edge_type": "imports",
                        "confidence": 0.98,
                        "evidence_ref": evidence,
                        "metadata_json": self._metadata(external=True, source="python-ast", exact_semantic=True),
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
                            "source": parent_id,
                            "target": symbols[name],
                            "edge_type": "calls",
                            "confidence": 0.92,
                            "evidence_ref": evidence,
                            "metadata_json": self._metadata(source="python-ast", exact_semantic=False, resolution="same-file-name"),
                        })
        return nodes, edges, []

    def _generic(
        self,
        relative: str,
        text: str,
        language: str,
        evidence: str,
        detection: LanguageDetection,
        *,
        diagnostics: Iterable[str] = (),
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        module = self._module_node(relative, text, language, evidence, detection, exact=False, source="universal-fallback")
        module_id = module["node_id"]
        nodes: list[dict[str, Any]] = [module]
        edges: list[dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()

        for kind, pattern in _DECLARATION_PATTERNS:
            for match in pattern.finditer(text):
                name = match.group("name").strip()
                line = text.count("\n", 0, match.start()) + 1
                identity = (name, line)
                if not name or identity in seen:
                    continue
                seen.add(identity)
                node_id = self._node_id(relative, kind, name, line)
                nodes.append({
                    "node_id": node_id,
                    "path": relative,
                    "kind": "symbol-candidate",
                    "name": name,
                    "qualified_name": f"{relative}:{name}",
                    "start_line": line,
                    "end_line": line,
                    "language": language,
                    "evidence_ref": evidence,
                    "metadata_json": self._metadata(
                        source="universal-declaration-pattern",
                        candidate_kind=kind,
                        exact_semantic=False,
                        capability_level="lexical",
                    ),
                })
                edges.append({
                    "source": module_id,
                    "target": node_id,
                    "edge_type": "defines-candidate",
                    "confidence": 0.55,
                    "evidence_ref": evidence,
                    "metadata_json": self._metadata(source="universal-declaration-pattern", exact_semantic=False),
                })

        for pattern in _IMPORT_PATTERNS:
            for match in pattern.finditer(text):
                name = next((value for value in match.groupdict().values() if value), "").strip("'\"()")
                if not name:
                    continue
                edges.append({
                    "source": module_id,
                    "target": f"external:{name}",
                    "edge_type": "imports-candidate",
                    "confidence": 0.45,
                    "evidence_ref": evidence,
                    "metadata_json": self._metadata(external=True, source="universal-import-pattern", exact_semantic=False),
                })

        # A brand-new language may use declaration syntax Syntavra has never seen.
        # Keep a bounded set of identifier candidates so repository search and
        # navigation still work without pretending they are exact symbols.
        if len(nodes) == 1 and not detection.minified:
            occurrences: dict[str, tuple[int, int]] = {}
            for line_number, line in enumerate(text.splitlines(), 1):
                for match in _IDENTIFIER_RE.finditer(line):
                    value = match.group(0)
                    if value.casefold() in {"copyright", "generated", "license", "http", "https"}:
                        continue
                    count, first_line = occurrences.get(value, (0, line_number))
                    occurrences[value] = (count + 1, first_line)
            ranked = sorted(occurrences.items(), key=lambda item: (-item[1][0], item[1][1], item[0]))[:32]
            for name, (count, line) in ranked:
                if count < 2:
                    continue
                node_id = self._node_id(relative, "identifier-candidate", name, line)
                nodes.append({
                    "node_id": node_id,
                    "path": relative,
                    "kind": "identifier-candidate",
                    "name": name,
                    "qualified_name": f"{relative}:{name}",
                    "start_line": line,
                    "end_line": line,
                    "language": language,
                    "evidence_ref": evidence,
                    "metadata_json": self._metadata(
                        source="universal-lexical-frequency",
                        occurrences=count,
                        exact_semantic=False,
                        capability_level="lexical",
                    ),
                })
                edges.append({
                    "source": module_id,
                    "target": node_id,
                    "edge_type": "contains-identifier-candidate",
                    "confidence": 0.3,
                    "evidence_ref": evidence,
                    "metadata_json": self._metadata(source="universal-lexical-frequency", exact_semantic=False),
                })

        return nodes, edges, list(diagnostics) + list(detection.diagnostics)

    def _adapter_for_detection(self, detection: LanguageDetection) -> Any | None:
        direct = self.languages.adapter_for(detection.language_id)
        if direct is not None:
            return direct
        candidates = []
        seen: set[int] = set()
        for language_id in detection.candidates:
            adapter = self.languages.adapter_for(language_id)
            if adapter is not None and id(adapter) not in seen:
                seen.add(id(adapter))
                candidates.append(adapter)
        return candidates[0] if len(candidates) == 1 else None

    def _adapter_parse(
        self,
        relative: str,
        text: str,
        language: str,
        evidence: str,
        detection: LanguageDetection,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]] | None:
        adapter = self._adapter_for_detection(detection)
        if adapter is None:
            return None
        adapter_languages = tuple(str(item).casefold() for item in adapter.language_ids)
        resolved_language = next((item for item in detection.candidates if item.casefold() in adapter_languages), language)
        result = adapter.parse(path=relative, text=text, evidence_ref=evidence)
        if not isinstance(result, LanguageParseResult):
            raise TypeError("language adapter must return LanguageParseResult")
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        for raw in result.nodes:
            item = dict(raw)
            name = str(item.get("name") or Path(relative).stem or Path(relative).name)
            kind = str(item.get("kind") or "symbol")
            line = max(1, int(item.get("start_line", 1)))
            metadata_value = item.get("metadata", {})
            metadata_dict = dict(metadata_value) if isinstance(metadata_value, Mapping) else {}
            metadata_dict.setdefault("source", result.evidence_source)
            metadata_dict.setdefault("exact_semantic", result.capability_level == "semantic")
            metadata_dict.setdefault("capability_level", result.capability_level)
            nodes.append({
                "node_id": str(item.get("node_id") or self._node_id(relative, kind, name, line)),
                "path": relative,
                "kind": kind,
                "name": name,
                "qualified_name": str(item.get("qualified_name") or f"{relative}:{name}"),
                "start_line": line,
                "end_line": max(line, int(item.get("end_line", line))),
                "language": resolved_language,
                "evidence_ref": str(item.get("evidence_ref") or evidence),
                "metadata_json": self._metadata(**metadata_dict),
            })
        for raw in result.edges:
            item = dict(raw)
            metadata_value = item.get("metadata", {})
            metadata_dict = dict(metadata_value) if isinstance(metadata_value, Mapping) else {}
            metadata_dict.setdefault("source", result.evidence_source)
            metadata_dict.setdefault("exact_semantic", result.capability_level == "semantic")
            edges.append({
                "source": str(item["source"]),
                "target": str(item["target"]),
                "edge_type": str(item.get("edge_type") or "references"),
                "confidence": max(0.0, min(1.0, float(item.get("confidence", 0.9)))),
                "evidence_ref": str(item.get("evidence_ref") or evidence),
                "metadata_json": self._metadata(**metadata_dict),
            })
        if not nodes:
            nodes.append(self._module_node(relative, text, language, evidence, detection, exact=False, source=result.evidence_source))
        return nodes, edges, list(result.diagnostics)

    def _parse(
        self,
        relative: str,
        text: str,
        evidence: str,
        detection: LanguageDetection,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        adapter_result = self._adapter_parse(relative, text, detection.language_id, evidence, detection)
        if adapter_result is not None:
            return adapter_result
        if detection.language_id == "python":
            try:
                return self._python(relative, text, evidence, detection)
            except SyntaxError as error:
                return self._generic(
                    relative,
                    text,
                    detection.language_id,
                    evidence,
                    detection,
                    diagnostics=(f"python-ast-fallback: {error}",),
                )
        return self._generic(relative, text, detection.language_id, evidence, detection)

    @staticmethod
    def _analysis_key(digest: str, detection: LanguageDetection, adapter: Any) -> str:
        adapter_identity = "none" if adapter is None else f"{type(adapter).__module__}.{type(adapter).__qualname__}"
        adapter_manifest_hash = str(getattr(getattr(adapter, "manifest", None), "manifest_hash", ""))
        payload = json.dumps(
            {
                "content": digest,
                "language": detection.language_id,
                "detector": detection.evidence,
                "descriptor": detection.descriptor_source,
                "capability": detection.capability_level,
                "adapter": adapter_identity,
                "adapter_manifest_sha256": adapter_manifest_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _remove_file(self, db: sqlite3.Connection, relative: str) -> None:
        old_ids = [row["node_id"] for row in db.execute("SELECT node_id FROM nodes WHERE path = ?", (relative,))]
        if old_ids:
            placeholders = ",".join("?" for _ in old_ids)
            db.execute(
                f"DELETE FROM edges WHERE source IN ({placeholders}) OR target IN ({placeholders})",
                (*old_ids, *old_ids),
            )
        db.execute("DELETE FROM nodes WHERE path = ?", (relative,))
        db.execute("DELETE FROM files WHERE path = ?", (relative,))

    def index_repository(self, root: Path, *, max_file_bytes: int = 2_000_000) -> dict[str, Any]:
        root = root.resolve(strict=True)
        self.languages.discover_manifests(root)
        self.language_services.discover(root)
        self.lsp_services.discover(root)
        service_diagnostics: list[str] = list(self.language_services.diagnostics)
        lsp_diagnostics: list[str] = list(self.lsp_services.diagnostics)
        if os.environ.get("SYNTAVRA_ALLOW_LANGUAGE_SERVICES", "").casefold() in {"1", "true", "yes"}:
            for manifest in sorted(self.language_services.manifests.values(), key=lambda item: item.service_id):
                try:
                    adapter = SandboxedLanguageServiceAdapter(
                        manifest,
                        workspace=root,
                        state_root=self.path.parent / "language-service-state",
                    )
                    self.languages.register_adapter(adapter)
                except Exception as error:
                    service_diagnostics.append(f"service:{manifest.service_id}: {type(error).__name__}: {error}")
        if os.environ.get("SYNTAVRA_ALLOW_LSP_SERVICES", "").casefold() in {"1", "true", "yes"}:
            for manifest in sorted(self.lsp_services.manifests.values(), key=lambda item: item.service_id):
                occupied = [language for language in manifest.language_ids if self.languages.adapter_for(language) is not None]
                if occupied:
                    lsp_diagnostics.append(f"lsp:{manifest.service_id}: shadowed-by-higher-priority-adapter:{','.join(sorted(occupied))}")
                    continue
                try:
                    adapter = GenericLSPAdapter(
                        manifest,
                        workspace=root,
                        state_root=self.path.parent / "lsp-service-state",
                    )
                    self.languages.register_adapter(adapter)
                except Exception as error:
                    lsp_diagnostics.append(f"lsp:{manifest.service_id}: {type(error).__name__}: {error}")
        changed = 0
        skipped = 0
        binary_skipped = 0
        oversized_skipped = 0
        errors: list[dict[str, str]] = []
        warnings: list[dict[str, Any]] = []
        discovered: set[str] = set()
        with _connect(self.path) as db:
            for path in sorted(root.rglob("*")):
                if not path.is_file() or any(part in _IGNORE_PARTS for part in path.parts):
                    continue
                try:
                    size = path.stat().st_size
                except OSError as error:
                    errors.append({"path": str(path), "error": f"{type(error).__name__}: {error}"})
                    continue
                if size > max_file_bytes:
                    oversized_skipped += 1
                    continue
                relative = path.relative_to(root).as_posix()
                try:
                    data = path.read_bytes()
                except OSError as error:
                    errors.append({"path": relative, "error": f"{type(error).__name__}: {error}"})
                    continue
                detection = self.languages.detect(path, data)
                if detection.binary:
                    binary_skipped += 1
                    continue
                text, encoding, binary = self.languages.decode_text(data)
                if binary or text is None:
                    binary_skipped += 1
                    continue
                discovered.add(relative)
                digest = hashlib.sha256(data).hexdigest()
                adapter = self._adapter_for_detection(detection)
                analysis_key = self._analysis_key(digest, detection, adapter)
                previous = db.execute("SELECT analysis_key FROM files WHERE path = ?", (relative,)).fetchone()
                if previous and previous["analysis_key"] == analysis_key:
                    skipped += 1
                    continue
                evidence = f"sha256:{digest}"
                try:
                    nodes, edges, diagnostics = self._parse(relative, text, evidence, detection)
                except Exception as error:
                    errors.append({"path": relative, "error": f"{type(error).__name__}: {error}"})
                    continue
                self._remove_file(db, relative)
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
                               VALUES (?, ?, 'external', ?, ?, 0, 0, 'external', ?, ?)""",
                            (
                                external_id,
                                relative,
                                external_id.split(":", 1)[1],
                                external_id,
                                evidence,
                                self._metadata(source="external-reference", exact_semantic=False),
                            ),
                        )
                    db.execute(
                        """INSERT OR REPLACE INTO edges
                           (source,target,edge_type,confidence,evidence_ref,metadata_json)
                           VALUES (:source,:target,:edge_type,:confidence,:evidence_ref,:metadata_json)""",
                        edge,
                    )
                db.execute(
                    """INSERT OR REPLACE INTO files
                       (path,sha256,language,indexed_at,analysis_key,detector,confidence,capability_level,metadata_json)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        relative,
                        digest,
                        detection.language_id,
                        _now(),
                        analysis_key,
                        detection.evidence,
                        detection.confidence,
                        detection.capability_level,
                        self._metadata(
                            descriptor_source=detection.descriptor_source,
                            encoding=encoding,
                            generated=detection.generated,
                            minified=detection.minified,
                            adapter=adapter is not None,
                        ),
                    ),
                )
                if diagnostics:
                    warnings.append({"path": relative, "diagnostics": diagnostics})
                changed += 1
            stale = [row["path"] for row in db.execute("SELECT path FROM files") if row["path"] not in discovered]
            for relative in stale:
                self._remove_file(db, relative)
        return {
            "ok": not errors,
            "changed_files": changed,
            "unchanged_files": skipped,
            "removed_files": len(stale),
            "binary_skipped": binary_skipped,
            "oversized_skipped": oversized_skipped,
            "errors": errors,
            "warnings": warnings,
            "language_platform": self.languages.inventory(),
            "language_services": {
                **self.language_services.inventory(),
                "diagnostics": service_diagnostics,
            },
            "lsp_services": {
                **self.lsp_services.inventory(),
                "diagnostics": lsp_diagnostics,
            },
            **self.stats(),
        }

    def import_semantic_index(
        self,
        index_path: Path,
        *,
        repository_root: Path,
        format: str = "auto",
        repository_commit: str | None = None,
        current_commit: str | None = None,
        allow_stale: bool = False,
        source_name: str | None = None,
    ) -> dict[str, Any]:
        return self.semantic_indexes.import_path(
            index_path,
            repository_root=repository_root,
            format=format,
            repository_commit=repository_commit,
            current_commit=current_commit,
            allow_stale=allow_stale,
            source_name=source_name,
        )

    def remove_semantic_index(self, source_key: str) -> dict[str, Any]:
        return self.semantic_indexes.remove(source_key)

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
            exact_name = 1.0 if text.casefold() in row["qualified_name"].casefold() else 0.0
            metadata_value = json.loads(row["metadata_json"])
            evidence_bonus = 5.0 if metadata_value.get("exact_semantic") else 0.0
            score = lexical * 65 + exact_name * 20 + evidence_bonus + min(10.0, degrees.get(row["node_id"], 0) * 0.5)
            value = dict(row)
            value["metadata"] = value.pop("metadata_json")
            value["metadata"] = metadata_value
            value["score"] = score
            value["matched_terms"] = sorted(matched)
            value["degree"] = degrees.get(row["node_id"], 0)
            value["semantic_status"] = "exact" if metadata_value.get("exact_semantic") else "candidate"
            scored.append((score, value))
        return [value for _, value in sorted(scored, key=lambda item: (-item[0], item[1]["path"], item[1]["start_line"]))[:max(1, limit)]]

    def impact(self, node_id: str, *, max_depth: int = 6) -> dict[str, Any]:
        with _connect(self.path) as db:
            if not db.execute("SELECT 1 FROM nodes WHERE node_id = ?", (node_id,)).fetchone():
                raise KeyError(node_id)
            queue = [(node_id, 0)]
            seen = {node_id}
            ordered = [node_id]
            traversed_edges: list[dict[str, Any]] = []
            while queue:
                current, depth = queue.pop(0)
                if depth >= max_depth:
                    continue
                rows = db.execute(
                    """SELECT * FROM edges WHERE target = ? AND edge_type IN
                       ('calls','imports','depends-on','implements','overrides','tested-by',
                        'defines-candidate','imports-candidate','contains-identifier-candidate')""",
                    (current,),
                ).fetchall()
                for row in rows:
                    edge = dict(row)
                    edge["metadata"] = json.loads(edge.pop("metadata_json"))
                    traversed_edges.append(edge)
                    candidate = row["source"]
                    if candidate not in seen:
                        seen.add(candidate)
                        ordered.append(candidate)
                        queue.append((candidate, depth + 1))
            placeholders = ",".join("?" for _ in ordered)
            nodes = [dict(row) for row in db.execute(f"SELECT * FROM nodes WHERE node_id IN ({placeholders})", ordered)]
        exact_flags: list[bool] = []
        for node in nodes:
            metadata_value = json.loads(node.pop("metadata_json"))
            node["metadata"] = metadata_value
            exact_flags.append(bool(metadata_value.get("exact_semantic")))
        tests = [node for node in nodes if "test" in node["path"].casefold() or node["kind"].startswith("test")]
        return {
            "root": node_id,
            "impacted": nodes,
            "affected_tests": tests,
            "edges": traversed_edges,
            "exact_evidence": bool(exact_flags) and all(exact_flags) and all(edge["metadata"].get("exact_semantic") for edge in traversed_edges),
            "candidate_evidence_present": any(not flag for flag in exact_flags) or any(not edge["metadata"].get("exact_semantic") for edge in traversed_edges),
        }

    def stats(self) -> dict[str, Any]:
        with _connect(self.path) as db:
            files = db.execute("SELECT COUNT(*) value FROM files").fetchone()["value"]
            nodes = db.execute("SELECT COUNT(*) value FROM nodes").fetchone()["value"]
            edges = db.execute("SELECT COUNT(*) value FROM edges").fetchone()["value"]
            languages = [dict(row) for row in db.execute("SELECT language, COUNT(*) files FROM files GROUP BY language ORDER BY language")]
            capabilities = [dict(row) for row in db.execute("SELECT capability_level, COUNT(*) files FROM files GROUP BY capability_level ORDER BY capability_level")]
            detectors = [dict(row) for row in db.execute("SELECT detector, COUNT(*) files FROM files GROUP BY detector ORDER BY detector")]
            unknown = int(db.execute("SELECT COUNT(*) value FROM files WHERE language LIKE 'unknown:%'").fetchone()["value"])
        return {
            "files": int(files),
            "nodes": int(nodes),
            "edges": int(edges),
            "languages": languages,
            "capabilities": capabilities,
            "detectors": detectors,
            "unknown_language_files": unknown,
            "universal_text_fallback": True,
            **self.semantic_indexes.stats(),
        }


__all__ = ["IncrementalCodeIntelligenceGraph"]
