from __future__ import annotations

import json
import math
import re
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .state import StateDB
from .structural_parsers import ParseResult, ParserRegistry
from .util import canonical_json, sha256_bytes, sha256_file


IGNORE_PARTS = {
    ".git", ".syntavra", "node_modules", ".venv", "venv", "dist", "build",
    "target", "__pycache__", ".mypy_cache", ".pytest_cache", ".next", ".gradle",
    "vendor", "coverage", ".idea", ".vscode",
}
TEST_HINT_RE = re.compile(r"(?i)(?:^|/)(?:test|tests|spec|specs)(?:/|_)|(?:_test|\.spec|\.test)\.")


@dataclass(frozen=True)
class Symbol:
    path: str
    name: str
    qualified_name: str
    kind: str
    line: int
    end_line: int
    signature: str = ""
    confidence: float = 1.0
    parser: str = ""


@dataclass(frozen=True)
class Edge:
    source_path: str
    source_symbol: str
    edge_type: str
    target: str
    line: int
    confidence: float
    target_path: str = ""
    metadata: dict[str, Any] | None = None


class StructuralIndex:
    """Incremental multi-language semantic and lexical structural graph.

    v0.3 resolves optional LSP/compiler snapshots first, Python AST second, and
    language-specific adapters for JS/TS, Rust, Go, Java, C/C++, C#, Ruby, PHP,
    and Lua/Luau. Every indexed file is content-addressed; branch switches and
    timestamp-only changes do not force a full rebuild.
    """

    def __init__(self, path: Path, *, repository_root: Path, repository_id: str):
        self.root = repository_root.resolve(strict=True)
        self.repository_id = repository_id
        self.state = StateDB(path)
        self.parsers = ParserRegistry(self.root)
        self._initialize()

    def _initialize(self) -> None:
        with self.state.transaction(immediate=True) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS structural_files(
                    path TEXT PRIMARY KEY,
                    content_hash TEXT NOT NULL,
                    language TEXT NOT NULL,
                    parser TEXT NOT NULL DEFAULT '',
                    semantic INTEGER NOT NULL DEFAULT 0,
                    diagnostics_json TEXT NOT NULL DEFAULT '[]',
                    indexed_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS structural_symbols(
                    symbol_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    name TEXT NOT NULL,
                    qualified_name TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL,
                    line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL DEFAULT 0,
                    signature TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 1.0,
                    parser TEXT NOT NULL DEFAULT '',
                    UNIQUE(path,qualified_name,kind,line)
                );
                CREATE INDEX IF NOT EXISTS structural_symbol_name_idx
                    ON structural_symbols(name,qualified_name);
                CREATE INDEX IF NOT EXISTS structural_symbol_path_idx
                    ON structural_symbols(path,line);
                CREATE TABLE IF NOT EXISTS structural_edges(
                    source_path TEXT NOT NULL,
                    source_symbol TEXT NOT NULL,
                    edge_type TEXT NOT NULL,
                    target TEXT NOT NULL,
                    target_path TEXT NOT NULL DEFAULT '',
                    line INTEGER NOT NULL,
                    confidence REAL NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(source_path,source_symbol,edge_type,target,line)
                );
                CREATE INDEX IF NOT EXISTS structural_edge_target_idx
                    ON structural_edges(target,edge_type);
                CREATE INDEX IF NOT EXISTS structural_edge_source_idx
                    ON structural_edges(source_symbol,edge_type);
                CREATE INDEX IF NOT EXISTS structural_edge_path_idx
                    ON structural_edges(source_path,target_path);
                """
            )
            migrations = {
                "structural_files": {
                    "parser": "TEXT NOT NULL DEFAULT ''",
                    "semantic": "INTEGER NOT NULL DEFAULT 0",
                    "diagnostics_json": "TEXT NOT NULL DEFAULT '[]'",
                },
                "structural_symbols": {
                    "qualified_name": "TEXT NOT NULL DEFAULT ''",
                    "end_line": "INTEGER NOT NULL DEFAULT 0",
                    "signature": "TEXT NOT NULL DEFAULT ''",
                    "confidence": "REAL NOT NULL DEFAULT 1.0",
                    "parser": "TEXT NOT NULL DEFAULT ''",
                },
                "structural_edges": {
                    "target_path": "TEXT NOT NULL DEFAULT ''",
                    "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
                },
            }
            for table, columns in migrations.items():
                current = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
                for name, declaration in columns.items():
                    if name not in current:
                        db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    @staticmethod
    def _normalized(path: Path) -> str:
        return path.as_posix()

    def _paths(self) -> list[Path]:
        suffixes = self.parsers.suffixes
        output: list[Path] = []
        for path in self.root.rglob("*"):
            if not path.is_file() or path.suffix.casefold() not in suffixes:
                continue
            relative = path.relative_to(self.root)
            if any(part in IGNORE_PARTS for part in relative.parts):
                continue
            output.append(path)
        return sorted(output)

    def index(self) -> dict[str, Any]:
        current = {self._normalized(path.relative_to(self.root)): path for path in self._paths()}
        with self.state.read() as db:
            known = {row["path"]: row["content_hash"] for row in db.execute("SELECT path,content_hash FROM structural_files")}
        changed = 0
        reused = 0
        failed = 0
        semantic = 0
        parser_counts: dict[str, int] = defaultdict(int)
        for relative, path in current.items():
            digest = sha256_file(path)
            if known.get(relative) == digest:
                reused += 1
                continue
            result = self._index_file(relative, path, digest)
            parser_counts[result.parser] += 1
            semantic += int(result.semantic)
            failed += int(bool(result.diagnostics and not result.symbols))
            changed += 1
        removed = set(known) - set(current)
        if removed:
            with self.state.transaction(immediate=True) as db:
                for relative in removed:
                    db.execute("DELETE FROM structural_edges WHERE source_path=? OR target_path=?", (relative, relative))
                    db.execute("DELETE FROM structural_symbols WHERE path=?", (relative,))
                    db.execute("DELETE FROM structural_files WHERE path=?", (relative,))
        self.resolve_edges()
        return {
            "changed": changed,
            "reused": reused,
            "removed": len(removed),
            "total": len(current),
            "semantic_files": semantic,
            "failed_files": failed,
            "parsers": dict(sorted(parser_counts.items())),
            "capabilities": self.parsers.capabilities(),
        }


    def update_paths(
        self,
        changed_paths: Iterable[str | Path],
        *,
        deleted_paths: Iterable[str | Path] = (),
    ) -> dict[str, Any]:
        """Incrementally refresh an explicit path set in the canonical SQLite graph."""
        changed = 0
        reused = 0
        failed = 0
        semantic = 0
        parser_counts: dict[str, int] = defaultdict(int)
        normalized_deleted = {str(Path(path)).replace("\\", "/") for path in deleted_paths}
        with self.state.read() as db:
            known = {row["path"]: row["content_hash"] for row in db.execute("SELECT path,content_hash FROM structural_files")}
        for raw in changed_paths:
            candidate = Path(raw)
            path = candidate if candidate.is_absolute() else self.root / candidate
            try:
                relative = self._normalized(path.resolve().relative_to(self.root))
            except (OSError, ValueError):
                continue
            if not path.is_file() or path.suffix.casefold() not in self.parsers.suffixes or any(part in IGNORE_PARTS for part in Path(relative).parts):
                normalized_deleted.add(relative)
                continue
            digest = sha256_file(path)
            if known.get(relative) == digest:
                reused += 1
                continue
            result = self._index_file(relative, path, digest)
            parser_counts[result.parser] += 1
            semantic += int(result.semantic)
            failed += int(bool(result.diagnostics and not result.symbols))
            changed += 1
        if normalized_deleted:
            with self.state.transaction(immediate=True) as db:
                for relative in sorted(normalized_deleted):
                    db.execute("DELETE FROM structural_edges WHERE source_path=? OR target_path=?", (relative, relative))
                    db.execute("DELETE FROM structural_symbols WHERE path=?", (relative,))
                    db.execute("DELETE FROM structural_files WHERE path=?", (relative,))
        self.resolve_edges()
        return {
            "changed": changed,
            "reused": reused,
            "removed": len(normalized_deleted),
            "total": self.stats()["files"],
            "semantic_files": semantic,
            "failed_files": failed,
            "parsers": dict(sorted(parser_counts.items())),
            "capabilities": self.parsers.capabilities(),
            "backend": "sqlite-structural-index",
        }

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        """Return a deterministic read-only snapshot for higher-level analyses."""
        with self.state.read() as db:
            files = [dict(row) for row in db.execute("SELECT * FROM structural_files ORDER BY path")]
            symbols = [dict(row) for row in db.execute("SELECT * FROM structural_symbols ORDER BY path,line,qualified_name,kind")]
            edges = [dict(row) for row in db.execute("SELECT * FROM structural_edges ORDER BY source_path,line,source_symbol,edge_type,target")]
        for row in files:
            row["diagnostics"] = json.loads(row.pop("diagnostics_json", "[]") or "[]")
        for row in edges:
            row["metadata"] = json.loads(row.pop("metadata_json", "{}") or "{}")
        return {"files": files, "symbols": symbols, "edges": edges}

    def task_seeds(self, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
        """Resolve exact and natural-language task text to deterministic symbol seeds."""
        text = query.strip()
        if not text:
            return []
        tokens = [token.casefold() for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text)]
        stop = {"the","and","for","with","from","into","that","this","fix","add","remove","update","change","make","use","using","system","feature","code","file","test","tests"}
        wanted = [token for token in tokens if token not in stop]
        with self.state.read() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT path,name,qualified_name,kind,line,end_line,signature,confidence,parser FROM structural_symbols"
            )]
        scored: list[tuple[float, dict[str, Any]]] = []
        query_cf = text.casefold()
        for row in rows:
            name = str(row["name"]).casefold()
            qualified = str(row["qualified_name"]).casefold()
            haystack = f"{row['path']} {row['name']} {row['qualified_name']} {row['signature']}".casefold()
            exact = 20.0 if query_cf in {name, qualified} else 0.0
            contained = 8.0 if query_cf in haystack else 0.0
            lexical = sum(3.0 if token in {name, self._short(qualified).casefold()} else 1.0 for token in wanted if token in haystack)
            score = exact + contained + lexical + float(row.get("confidence", 0.0))
            if score > 0:
                scored.append((score, row))
        scored.sort(key=lambda item: (-item[0], item[1]["path"], item[1]["line"], item[1]["qualified_name"]))
        return [{**row, "seed_score": score} for score, row in scored[:max(1, limit)]]

    def _index_file(self, relative: str, path: Path, digest: str) -> ParseResult:
        text = path.read_text(encoding="utf-8", errors="replace")
        result = self.parsers.parse(relative, text)
        with self.state.transaction(immediate=True) as db:
            db.execute("DELETE FROM structural_edges WHERE source_path=?", (relative,))
            db.execute("DELETE FROM structural_symbols WHERE path=?", (relative,))
            db.executemany(
                """
                INSERT OR IGNORE INTO structural_symbols(
                    path,name,qualified_name,kind,line,end_line,signature,confidence,parser
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        relative,
                        item.name,
                        item.qualified_name,
                        item.kind,
                        item.line,
                        item.end_line,
                        item.signature,
                        item.confidence,
                        result.parser,
                    )
                    for item in result.symbols
                ],
            )
            db.executemany(
                """
                INSERT OR IGNORE INTO structural_edges(
                    source_path,source_symbol,edge_type,target,target_path,line,confidence,metadata_json
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        relative,
                        item.source_symbol,
                        item.edge_type,
                        item.target,
                        item.target_path,
                        item.line,
                        item.confidence,
                        json.dumps(item.metadata, ensure_ascii=False, sort_keys=True),
                    )
                    for item in result.edges
                ],
            )
            db.execute(
                """
                INSERT INTO structural_files(path,content_hash,language,parser,semantic,diagnostics_json,indexed_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(path) DO UPDATE SET
                    content_hash=excluded.content_hash,
                    language=excluded.language,
                    parser=excluded.parser,
                    semantic=excluded.semantic,
                    diagnostics_json=excluded.diagnostics_json,
                    indexed_at=excluded.indexed_at
                """,
                (
                    relative,
                    digest,
                    result.language,
                    result.parser,
                    int(result.semantic),
                    json.dumps(result.diagnostics, ensure_ascii=False),
                    time.time(),
                ),
            )
        return result

    @staticmethod
    def _short(value: str) -> str:
        normalized = value.replace("::", ".").replace(":", ".")
        return normalized.rsplit(".", 1)[-1]

    def resolve_edges(self) -> int:
        """Resolve target symbols to paths without inventing ambiguous identities."""
        with self.state.transaction(immediate=True) as db:
            symbols = [dict(row) for row in db.execute("SELECT path,name,qualified_name FROM structural_symbols")]
            by_exact: dict[str, set[str]] = defaultdict(set)
            by_short: dict[str, set[str]] = defaultdict(set)
            for symbol in symbols:
                by_exact[symbol["qualified_name"]].add(symbol["path"])
                by_short[symbol["name"]].add(symbol["path"])
            rows = [dict(row) for row in db.execute("SELECT rowid,target FROM structural_edges WHERE target_path='' OR target_path IS NULL")]
            updates: list[tuple[str, int]] = []
            for row in rows:
                candidates = by_exact.get(row["target"], set()) or by_short.get(self._short(row["target"]), set())
                if len(candidates) == 1:
                    updates.append((next(iter(candidates)), int(row["rowid"])))
            db.executemany("UPDATE structural_edges SET target_path=? WHERE rowid=?", updates)
        return len(updates)

    def inspect_symbol(self, query: str, *, limit: int = 20) -> dict[str, Any]:
        with self.state.read() as db:
            rows = [
                dict(row)
                for row in db.execute(
                    """
                    SELECT path,name,qualified_name,kind,line,end_line,signature,confidence,parser
                    FROM structural_symbols
                    WHERE name LIKE ? OR qualified_name LIKE ? OR signature LIKE ?
                    ORDER BY CASE WHEN name=? OR qualified_name=? THEN 0 ELSE 1 END,
                             confidence DESC,path,line
                    LIMIT ?
                    """,
                    (f"%{query}%", f"%{query}%", f"%{query}%", query, query, max(1, limit)),
                )
            ]
        return {"query": query, "symbols": rows}

    def _graph_snapshot(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        with self.state.read() as db:
            symbols = [dict(row) for row in db.execute("SELECT * FROM structural_symbols")]
            edges = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM structural_edges WHERE edge_type IN "
                    "('calls','calls-short','imports','inherits','implements','overrides','reads','writes','instantiates','test-covers')"
                )
            ]
        return symbols, edges

    @staticmethod
    def _personalized_rank(
        reverse: dict[str, list[tuple[str, float]]],
        seeds: set[str],
        *,
        iterations: int = 32,
        damping: float = 0.85,
    ) -> dict[str, float]:
        nodes = set(reverse) | {source for values in reverse.values() for source, _ in values} | seeds
        if not nodes:
            return {}
        actual_seeds = seeds & nodes or set(nodes)
        teleport = {node: (1.0 / len(actual_seeds) if node in actual_seeds else 0.0) for node in nodes}
        rank = dict(teleport)
        for _ in range(iterations):
            updated = {node: (1.0 - damping) * teleport[node] for node in nodes}
            for target, callers in reverse.items():
                total = sum(max(0.01, weight) for _, weight in callers) or 1.0
                for caller, weight in callers:
                    updated[caller] += damping * rank.get(target, 0.0) * max(0.01, weight) / total
            rank = updated
        return rank

    def inspect_impact(self, query: str, *, max_depth: int = 3) -> dict[str, Any]:
        max_depth = max(0, max_depth)
        symbols, edges = self._graph_snapshot()
        definitions = [
            {key: row[key] for key in ("path", "name", "qualified_name", "kind", "line", "end_line", "signature", "confidence", "parser")}
            for row in symbols
            if row["name"] == query or row["qualified_name"] == query or self._short(row["qualified_name"]) == self._short(query)
        ]
        seed_names = {query, self._short(query)}
        seed_names.update(row["qualified_name"] for row in definitions)
        seed_names.update(row["name"] for row in definitions)

        reverse: dict[str, list[tuple[str, float]]] = defaultdict(list)
        edge_rows_by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
        symbol_paths: dict[str, set[str]] = defaultdict(set)
        for row in symbols:
            symbol_paths[row["qualified_name"]].add(row["path"])
            symbol_paths[row["name"]].add(row["path"])
        edge_weight = {
            "calls": 1.0,
            "calls-short": 0.86,
            "imports": 0.62,
            "inherits": 0.94,
            "implements": 0.94,
            "overrides": 0.96,
            "instantiates": 0.88,
            "reads": 0.46,
            "writes": 0.58,
            "test-covers": 1.0,
        }
        for row in edges:
            target = row["target"]
            short_target = self._short(target)
            source = row["source_symbol"]
            confidence = float(row["confidence"]) * edge_weight.get(row["edge_type"], 0.5)
            reverse[target].append((source, confidence))
            reverse[short_target].append((source, confidence))
            edge_rows_by_target[target].append(row)
            if short_target != target:
                edge_rows_by_target[short_target].append(row)

        queue = deque((seed, 0) for seed in sorted(seed_names))
        best_depth: dict[str, int] = {seed: 0 for seed in seed_names}
        paths: set[str] = {row["path"] for row in definitions}
        traversed: list[dict[str, Any]] = []
        while queue:
            target, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for row in edge_rows_by_target.get(target, []):
                caller = row["source_symbol"]
                next_depth = depth + 1
                previous = best_depth.get(caller)
                if previous is not None and previous <= next_depth:
                    continue
                best_depth[caller] = next_depth
                paths.add(row["source_path"])
                if row.get("target_path"):
                    paths.add(row["target_path"])
                paths.update(symbol_paths.get(caller, set()))
                traversed.append({**row, "depth": next_depth})
                queue.append((caller, next_depth))

        direct = [row for row in traversed if row["depth"] == 1]
        rank = self._personalized_rank(reverse, seed_names)
        ranked_symbols = [
            {
                "symbol": symbol,
                "depth": depth,
                "rank": rank.get(symbol, 0.0),
                "paths": sorted(symbol_paths.get(symbol, set())),
            }
            for symbol, depth in best_depth.items()
            if depth > 0
        ]
        ranked_symbols.sort(key=lambda row: (row["depth"], -row["rank"], row["symbol"]))
        affected = sorted(paths)
        tests = [path for path in affected if TEST_HINT_RE.search(path)]
        confidence = 1.0 if definitions and all(float(row.get("confidence", 0)) >= 0.9 for row in definitions) else 0.75 if definitions else 0.4
        return {
            "query": query,
            "definitions": definitions,
            "direct_references": direct,
            "transitive_references": sorted(
                traversed,
                key=lambda row: (row["depth"], -float(row["confidence"]), row["source_path"], row["line"]),
            ),
            "ranked_symbols": ranked_symbols,
            "affected_paths": affected,
            "affected_tests": tests,
            "required_verifiers": self.required_verifiers(affected),
            "max_depth": max_depth,
            "confidence": confidence,
            "recall_boundary": "semantic-snapshot+python-ast+language-specific-static-graph",
        }

    @staticmethod
    def required_verifiers(paths: Iterable[str]) -> list[str]:
        suffixes = {Path(path).suffix.casefold() for path in paths}
        commands: list[str] = []
        if ".py" in suffixes:
            commands.append("python -m unittest discover -s tests -q")
        if suffixes & {".js", ".jsx", ".ts", ".tsx"}:
            commands.append("npm test -- --runInBand")
        if ".rs" in suffixes:
            commands.append("cargo test --all-targets")
        if ".go" in suffixes:
            commands.append("go test ./...")
        if ".java" in suffixes:
            commands.append("./gradlew test")
        if ".cs" in suffixes:
            commands.append("dotnet test")
        if suffixes & {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"}:
            commands.append("ctest --test-dir build --output-on-failure")
        if ".rb" in suffixes:
            commands.append("bundle exec rake test")
        if ".php" in suffixes:
            commands.append("vendor/bin/phpunit")
        return commands

    def impacted_by_paths(self, changed_paths: Iterable[str], *, max_depth: int = 3) -> dict[str, Any]:
        normalized = {str(Path(path)).replace("\\", "/") for path in changed_paths}
        with self.state.read() as db:
            symbols = [
                row["qualified_name"] or row["name"]
                for row in db.execute(
                    f"SELECT name,qualified_name FROM structural_symbols WHERE path IN ({','.join('?' for _ in normalized)})",
                    tuple(normalized),
                )
            ] if normalized else []
        impacts = [self.inspect_impact(symbol, max_depth=max_depth) for symbol in sorted(set(symbols))]
        paths = sorted({path for impact in impacts for path in impact["affected_paths"]} | normalized)
        return {
            "changed_paths": sorted(normalized),
            "seed_symbols": sorted(set(symbols)),
            "affected_paths": paths,
            "affected_tests": [path for path in paths if TEST_HINT_RE.search(path)],
            "required_verifiers": self.required_verifiers(paths),
        }

    def repository_map(self, query: str, *, token_budget: int = 2000, max_depth: int = 4) -> dict[str, Any]:
        """Build a deterministic query-conditioned symbol map under a token budget."""
        impact = self.inspect_impact(query, max_depth=max_depth)
        with self.state.read() as db:
            symbols = [dict(row) for row in db.execute("SELECT * FROM structural_symbols")]
        rank_by_symbol = {row["symbol"]: float(row["rank"]) for row in impact["ranked_symbols"]}
        seeds = {row["qualified_name"] for row in impact["definitions"]} | {row["name"] for row in impact["definitions"]}
        candidates: list[tuple[float, dict[str, Any], int]] = []
        query_lower = query.casefold()
        for row in symbols:
            text = f"{row['path']}:{row['line']} {row['kind']} {row['qualified_name']} {row['signature']}".strip()
            estimated = max(1, math.ceil(len(text) / 4))
            lexical = 1.0 if query_lower in row["qualified_name"].casefold() else 0.0
            graph = rank_by_symbol.get(row["qualified_name"], rank_by_symbol.get(row["name"], 0.0))
            affected = 0.4 if row["path"] in impact["affected_paths"] else 0.0
            semantic = 0.2 if row["parser"].startswith(("semantic", "python-ast")) else 0.0
            score = 5.0 * lexical + 3.0 * graph + affected + semantic + float(row["confidence"]) / max(1.0, math.log2(estimated + 2))
            if row["qualified_name"] in seeds or row["name"] in seeds:
                score += 10.0
            candidates.append((score, row, estimated))
        candidates.sort(key=lambda item: (-item[0], item[1]["path"], item[1]["line"]))
        used = 0
        selected: list[dict[str, Any]] = []
        for score, row, estimated in candidates:
            if used + estimated > token_budget:
                continue
            selected.append({
                "path": row["path"],
                "line": row["line"],
                "end_line": row["end_line"],
                "kind": row["kind"],
                "symbol": row["qualified_name"],
                "signature": row["signature"],
                "score": score,
                "estimated_tokens": estimated,
                "parser": row["parser"],
            })
            used += estimated
        payload = {
            "query": query,
            "budget": token_budget,
            "used": used,
            "selected": selected,
            "affected_paths": impact["affected_paths"],
            "affected_tests": impact["affected_tests"],
            "required_verifiers": impact["required_verifiers"],
        }
        payload["map_hash"] = sha256_bytes(canonical_json(payload))
        return payload

    def stats(self) -> dict[str, Any]:
        with self.state.read() as db:
            files = int(db.execute("SELECT COUNT(*) FROM structural_files").fetchone()[0])
            symbols = int(db.execute("SELECT COUNT(*) FROM structural_symbols").fetchone()[0])
            edges = int(db.execute("SELECT COUNT(*) FROM structural_edges").fetchone()[0])
            languages = {row["language"]: row["count"] for row in db.execute("SELECT language,COUNT(*) count FROM structural_files GROUP BY language")}
            parsers = {row["parser"]: row["count"] for row in db.execute("SELECT parser,COUNT(*) count FROM structural_files GROUP BY parser")}
            semantic = int(db.execute("SELECT COUNT(*) FROM structural_files WHERE semantic=1").fetchone()[0])
        return {
            "repository_id": self.repository_id,
            "files": files,
            "symbols": symbols,
            "edges": edges,
            "languages": languages,
            "parsers": parsers,
            "semantic_files": semantic,
            "graph_hash": sha256_bytes(canonical_json({"files": files, "symbols": symbols, "edges": edges, "languages": languages, "parsers": parsers})),
        }
