from __future__ import annotations

import ast
import math
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .state import StateDB
from .util import sha256_file


SOURCE_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go", ".java", ".cs",
    ".c", ".h", ".cpp", ".hpp", ".rb", ".php", ".lua", ".luau",
}
IGNORE_PARTS = {
    ".git", ".signalcore", "node_modules", ".venv", "venv", "dist", "build",
    "target", "__pycache__", ".mypy_cache", ".pytest_cache",
}
GENERIC_DEF = re.compile(
    r"(?m)^\s*(?:def|class|fn|function|func|interface|trait|struct|enum|local\s+function)\s+([A-Za-z_$][\w$]*)"
)
GENERIC_CALL = re.compile(r"\b([A-Za-z_$][\w$]*)\s*\(")
GENERIC_IMPORT = re.compile(
    r"(?m)^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+)|use\s+([\w:]+)|require\(['\"]([^'\"]+))"
)
TEST_HINT_RE = re.compile(r"(?i)(?:^|/)(?:test|tests|spec|specs)(?:/|_)|(?:_test|\.spec|\.test)\.")


@dataclass(frozen=True)
class Symbol:
    path: str
    name: str
    qualified_name: str
    kind: str
    line: int
    end_line: int


@dataclass(frozen=True)
class Edge:
    source_path: str
    source_symbol: str
    edge_type: str
    target: str
    line: int
    confidence: float


class _PythonVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.symbols: list[Symbol] = []
        self.edges: list[Edge] = []
        self.path = ""
        self.parents: list[str] = []
        self.import_aliases: dict[str, str] = {}

    def parse(self, path: str, text: str) -> tuple[list[Symbol], list[Edge]]:
        self.path = path
        tree = ast.parse(text, filename=path)
        self.visit(tree)
        return self.symbols, self.edges

    def _current(self) -> str:
        return ".".join(self.parents) if self.parents else "<module>"

    def _add_symbol(self, node: ast.AST, name: str, kind: str) -> None:
        qualified = ".".join((*self.parents, name)) if self.parents else name
        self.symbols.append(
            Symbol(
                self.path,
                name,
                qualified,
                kind,
                int(getattr(node, "lineno", 1)),
                int(getattr(node, "end_lineno", getattr(node, "lineno", 1))),
            )
        )

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._add_symbol(node, node.name, "class")
        self.parents.append(node.name)
        self.generic_visit(node)
        self.parents.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._add_symbol(node, node.name, "function")
        self.parents.append(node.name)
        self.generic_visit(node)
        self.parents.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[0]
            self.import_aliases[local] = alias.name
            self.edges.append(Edge(self.path, self._current(), "imports", alias.name, node.lineno, 1.0))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if module:
            self.edges.append(Edge(self.path, self._current(), "imports", module, node.lineno, 1.0))
        for alias in node.names:
            local = alias.asname or alias.name
            self.import_aliases[local] = f"{module}.{alias.name}" if module else alias.name

    @staticmethod
    def _call_name(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parts: list[str] = [node.attr]
            current = node.value
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            return ".".join(reversed(parts))
        return None

    def visit_Call(self, node: ast.Call) -> None:
        target = self._call_name(node.func)
        if target:
            head = target.split(".")[0]
            if head in self.import_aliases:
                target = self.import_aliases[head] + target[len(head):]
            self.edges.append(Edge(self.path, self._current(), "calls", target, node.lineno, 0.98))
            short = target.rsplit(".", 1)[-1]
            if short != target:
                self.edges.append(Edge(self.path, self._current(), "calls-short", short, node.lineno, 0.88))
        self.generic_visit(node)


class StructuralIndex:
    """Incremental multi-language symbol and reverse-impact graph.

    Python uses AST. Other languages use conservative lexical adapters. The
    reverse traversal follows callers transitively and ranks affected symbols by
    depth, confidence and personalized PageRank rather than returning only direct
    textual matches.
    """

    def __init__(self, path: Path, *, repository_root: Path, repository_id: str):
        self.root = repository_root.resolve(strict=True)
        self.repository_id = repository_id
        self.state = StateDB(path)
        with self.state.transaction(immediate=True) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS structural_files(
                    path TEXT PRIMARY KEY,
                    content_hash TEXT NOT NULL,
                    language TEXT NOT NULL,
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
                    UNIQUE(path,qualified_name,kind,line)
                );
                CREATE INDEX IF NOT EXISTS structural_symbol_name_idx
                    ON structural_symbols(name,qualified_name);
                CREATE TABLE IF NOT EXISTS structural_edges(
                    source_path TEXT NOT NULL,
                    source_symbol TEXT NOT NULL,
                    edge_type TEXT NOT NULL,
                    target TEXT NOT NULL,
                    line INTEGER NOT NULL,
                    confidence REAL NOT NULL,
                    UNIQUE(source_path,source_symbol,edge_type,target,line)
                );
                CREATE INDEX IF NOT EXISTS structural_edge_target_idx
                    ON structural_edges(target,edge_type);
                CREATE INDEX IF NOT EXISTS structural_edge_source_idx
                    ON structural_edges(source_symbol,edge_type);
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(structural_symbols)")}
            if "qualified_name" not in columns:
                db.execute("ALTER TABLE structural_symbols ADD COLUMN qualified_name TEXT NOT NULL DEFAULT ''")
            if "end_line" not in columns:
                db.execute("ALTER TABLE structural_symbols ADD COLUMN end_line INTEGER NOT NULL DEFAULT 0")

    def _paths(self) -> list[Path]:
        output: list[Path] = []
        for path in self.root.rglob("*"):
            if not path.is_file() or path.suffix.casefold() not in SOURCE_SUFFIXES:
                continue
            relative = path.relative_to(self.root)
            if any(part in IGNORE_PARTS for part in relative.parts):
                continue
            output.append(path)
        return sorted(output)

    def index(self) -> dict[str, int]:
        current = {str(path.relative_to(self.root)).replace("\\", "/"): path for path in self._paths()}
        changed = 0
        reused = 0
        with self.state.read() as db:
            known = {row["path"]: row["content_hash"] for row in db.execute("SELECT path,content_hash FROM structural_files")}
        for relative, path in current.items():
            digest = sha256_file(path)
            if known.get(relative) == digest:
                reused += 1
                continue
            self._index_file(relative, path, digest)
            changed += 1
        removed = set(known) - set(current)
        if removed:
            with self.state.transaction(immediate=True) as db:
                for relative in removed:
                    db.execute("DELETE FROM structural_edges WHERE source_path=?", (relative,))
                    db.execute("DELETE FROM structural_symbols WHERE path=?", (relative,))
                    db.execute("DELETE FROM structural_files WHERE path=?", (relative,))
        return {"changed": changed, "reused": reused, "removed": len(removed), "total": len(current)}

    @staticmethod
    def _lexical_symbols_edges(relative: str, text: str) -> tuple[list[Symbol], list[Edge]]:
        symbols: list[Symbol] = []
        edges: list[Edge] = []
        for match in GENERIC_DEF.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            name = match.group(1)
            symbols.append(Symbol(relative, name, name, "symbol", line, line))
        for match in GENERIC_CALL.finditer(text):
            target = match.group(1)
            if target in {"if", "for", "while", "switch", "return", "sizeof", "catch"}:
                continue
            line = text.count("\n", 0, match.start()) + 1
            edges.append(Edge(relative, "<file>", "calls", target, line, 0.55))
        for match in GENERIC_IMPORT.finditer(text):
            target = next((group for group in match.groups() if group), None)
            if target:
                line = text.count("\n", 0, match.start()) + 1
                edges.append(Edge(relative, "<file>", "imports", target, line, 0.72))
        return symbols, edges

    def _index_file(self, relative: str, path: Path, digest: str) -> None:
        text = path.read_text(encoding="utf-8", errors="replace")
        language = path.suffix.casefold().lstrip(".")
        symbols: list[Symbol] = []
        edges: list[Edge] = []
        if path.suffix.casefold() == ".py":
            try:
                symbols, edges = _PythonVisitor().parse(relative, text)
            except SyntaxError:
                symbols, edges = [], []
        lexical_symbols, lexical_edges = self._lexical_symbols_edges(relative, text)
        if not symbols:
            symbols = lexical_symbols
        # Keep lexical calls only when AST did not already produce the same line/target.
        existing = {(edge.line, edge.target.rsplit(".", 1)[-1]) for edge in edges if edge.edge_type.startswith("calls")}
        for edge in lexical_edges:
            if edge.edge_type != "calls" or (edge.line, edge.target) not in existing:
                edges.append(edge)
        with self.state.transaction(immediate=True) as db:
            db.execute("DELETE FROM structural_edges WHERE source_path=?", (relative,))
            db.execute("DELETE FROM structural_symbols WHERE path=?", (relative,))
            db.executemany(
                """
                INSERT OR IGNORE INTO structural_symbols(path,name,qualified_name,kind,line,end_line)
                VALUES(?,?,?,?,?,?)
                """,
                [(item.path, item.name, item.qualified_name, item.kind, item.line, item.end_line) for item in symbols],
            )
            db.executemany(
                """
                INSERT OR IGNORE INTO structural_edges(source_path,source_symbol,edge_type,target,line,confidence)
                VALUES(?,?,?,?,?,?)
                """,
                [(item.source_path, item.source_symbol, item.edge_type, item.target, item.line, item.confidence) for item in edges],
            )
            db.execute(
                """
                INSERT INTO structural_files(path,content_hash,language,indexed_at)
                VALUES(?,?,?,?)
                ON CONFLICT(path) DO UPDATE SET
                    content_hash=excluded.content_hash,
                    language=excluded.language,
                    indexed_at=excluded.indexed_at
                """,
                (relative, digest, language, time.time()),
            )

    def inspect_symbol(self, query: str, *, limit: int = 20) -> dict[str, Any]:
        with self.state.read() as db:
            rows = [
                dict(row)
                for row in db.execute(
                    """
                    SELECT path,name,qualified_name,kind,line,end_line
                    FROM structural_symbols
                    WHERE name LIKE ? OR qualified_name LIKE ?
                    ORDER BY CASE WHEN name=? OR qualified_name=? THEN 0 ELSE 1 END,path,line
                    LIMIT ?
                    """,
                    (f"%{query}%", f"%{query}%", query, query, limit),
                )
            ]
        return {"query": query, "symbols": rows}

    @staticmethod
    def _short(value: str) -> str:
        return value.rsplit(".", 1)[-1]

    def _graph_snapshot(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        with self.state.read() as db:
            symbols = [dict(row) for row in db.execute("SELECT * FROM structural_symbols")]
            edges = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM structural_edges WHERE edge_type IN ('calls','calls-short')"
                )
            ]
        return symbols, edges

    @staticmethod
    def _personalized_rank(
        reverse: dict[str, list[tuple[str, float]]],
        seeds: set[str],
        *,
        iterations: int = 24,
        damping: float = 0.82,
    ) -> dict[str, float]:
        nodes = set(reverse) | {source for values in reverse.values() for source, _ in values} | seeds
        if not nodes:
            return {}
        teleport = {node: (1.0 / len(seeds) if node in seeds else 0.0) for node in nodes}
        rank = dict(teleport)
        for _ in range(iterations):
            updated = {node: (1.0 - damping) * teleport[node] for node in nodes}
            for target, callers in reverse.items():
                total = sum(weight for _, weight in callers) or 1.0
                for caller, weight in callers:
                    updated[caller] += damping * rank.get(target, 0.0) * weight / total
            rank = updated
        return rank

    def inspect_impact(self, query: str, *, max_depth: int = 3) -> dict[str, Any]:
        max_depth = max(0, max_depth)
        symbols, edges = self._graph_snapshot()
        definitions = [
            {key: row[key] for key in ("path", "name", "qualified_name", "kind", "line", "end_line")}
            for row in symbols
            if row["name"] == query or row["qualified_name"] == query
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
        for row in edges:
            target = row["target"]
            short_target = self._short(target)
            source = row["source_symbol"]
            reverse[target].append((source, float(row["confidence"])))
            reverse[short_target].append((source, float(row["confidence"])))
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
            "max_depth": max_depth,
            "recall_boundary": "static-call-graph",
        }

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
        }
