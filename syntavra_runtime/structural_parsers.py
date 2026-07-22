from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol


@dataclass(frozen=True)
class ParsedSymbol:
    name: str
    qualified_name: str
    kind: str
    line: int
    end_line: int
    signature: str = ""
    confidence: float = 1.0


@dataclass(frozen=True)
class ParsedEdge:
    source_symbol: str
    edge_type: str
    target: str
    line: int
    confidence: float = 1.0
    target_path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParseResult:
    language: str
    parser: str
    symbols: tuple[ParsedSymbol, ...]
    edges: tuple[ParsedEdge, ...]
    diagnostics: tuple[str, ...] = ()
    semantic: bool = False


class LanguageParser(Protocol):
    parser_id: str

    def parse(self, path: str, text: str) -> ParseResult: ...


class _PythonVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.symbols: list[ParsedSymbol] = []
        self.edges: list[ParsedEdge] = []
        self.parents: list[str] = []
        self.import_aliases: dict[str, str] = {}

    @property
    def current(self) -> str:
        return ".".join(self.parents) if self.parents else "<module>"

    @staticmethod
    def call_name(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            values = [node.attr]
            current = node.value
            while isinstance(current, ast.Attribute):
                values.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                values.append(current.id)
            return ".".join(reversed(values))
        return None

    @staticmethod
    def annotation(node: ast.AST | None) -> str:
        if node is None:
            return ""
        try:
            return ast.unparse(node)
        except (AttributeError, ValueError):
            return ""

    def add_definition(self, node: ast.AST, name: str, kind: str, signature: str = "") -> None:
        qualified = ".".join((*self.parents, name)) if self.parents else name
        self.symbols.append(
            ParsedSymbol(
                name,
                qualified,
                kind,
                int(getattr(node, "lineno", 1)),
                int(getattr(node, "end_lineno", getattr(node, "lineno", 1))),
                signature,
                1.0,
            )
        )
        self.edges.append(ParsedEdge(self.current, "defines", qualified, int(getattr(node, "lineno", 1)), 1.0))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.add_definition(node, node.name, "class")
        qualified = ".".join((*self.parents, node.name)) if self.parents else node.name
        for base in node.bases:
            name = self.call_name(base)
            if name:
                self.edges.append(ParsedEdge(qualified, "inherits", name, node.lineno, 0.98))
        self.parents.append(node.name)
        self.generic_visit(node)
        self.parents.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        args = [argument.arg for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)]
        signature = f"({', '.join(args)})"
        return_type = self.annotation(node.returns)
        if return_type:
            signature += f" -> {return_type}"
        self.add_definition(node, node.name, "method" if self.parents else "function", signature)
        self.parents.append(node.name)
        self.generic_visit(node)
        self.parents.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[0]
            self.import_aliases[local] = alias.name
            self.edges.append(ParsedEdge(self.current, "imports", alias.name, node.lineno, 1.0))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if module:
            self.edges.append(ParsedEdge(self.current, "imports", module, node.lineno, 1.0))
        for alias in node.names:
            local = alias.asname or alias.name
            self.import_aliases[local] = f"{module}.{alias.name}" if module else alias.name

    def visit_Call(self, node: ast.Call) -> None:
        target = self.call_name(node.func)
        if target:
            head = target.split(".", 1)[0]
            if head in self.import_aliases:
                target = self.import_aliases[head] + target[len(head):]
            self.edges.append(ParsedEdge(self.current, "calls", target, node.lineno, 0.99))
            if target.rsplit(".", 1)[-1] != target:
                self.edges.append(ParsedEdge(self.current, "calls-short", target.rsplit(".", 1)[-1], node.lineno, 0.9))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.edges.append(ParsedEdge(self.current, "reads", node.id, node.lineno, 0.82))
        elif isinstance(node.ctx, (ast.Store, ast.Del)):
            self.edges.append(ParsedEdge(self.current, "writes", node.id, node.lineno, 0.82))


class PythonParser:
    parser_id = "python-ast-v3"

    def parse(self, path: str, text: str) -> ParseResult:
        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError as exc:
            return ParseResult("python", self.parser_id, (), (), (f"syntax-error:{exc.lineno}:{exc.msg}",), True)
        visitor = _PythonVisitor()
        visitor.visit(tree)
        return ParseResult("python", self.parser_id, tuple(visitor.symbols), tuple(visitor.edges), (), True)


@dataclass(frozen=True)
class RegexProfile:
    language: str
    suffixes: tuple[str, ...]
    definitions: tuple[tuple[str, str], ...]
    imports: tuple[str, ...]
    inheritance: tuple[str, ...] = ()
    calls: tuple[str, ...] = (r"\b([A-Za-z_$][\w$.:]*)\s*\(",)
    keywords: frozenset[str] = frozenset()


PROFILES: tuple[RegexProfile, ...] = (
    RegexProfile(
        "javascript",
        (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"),
        (
            ("class", r"\b(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)"),
            ("interface", r"\b(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)"),
            ("type", r"\b(?:export\s+)?type\s+([A-Za-z_$][\w$]*)\s*="),
            ("enum", r"\b(?:export\s+)?enum\s+([A-Za-z_$][\w$]*)"),
            ("function", r"\b(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\("),
            ("function", r"\b(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"),
            ("method", r"(?m)^\s*(?:public\s+|private\s+|protected\s+|static\s+|async\s+|readonly\s+)*([A-Za-z_$][\w$]*)\s*\([^;{}]*\)\s*(?::[^={]+)?\s*\{"),
        ),
        (
            r"\bimport(?:[^'\"]*?from\s*)?['\"]([^'\"]+)['\"]",
            r"\brequire\(\s*['\"]([^'\"]+)['\"]\s*\)",
            r"\bexport\s+\*\s+from\s+['\"]([^'\"]+)['\"]",
        ),
        (r"\bclass\s+\w+\s+extends\s+([A-Za-z_$][\w$.]*)", r"\bclass\s+\w+[^\{]*\bimplements\s+([^\{]+)"),
        keywords=frozenset({"if", "for", "while", "switch", "catch", "return", "typeof", "new", "function"}),
    ),
    RegexProfile(
        "rust",
        (".rs",),
        (
            ("function", r"\b(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+([A-Za-z_][\w]*)\s*(?:<[^>]+>)?\s*\("),
            ("struct", r"\b(?:pub\s+)?struct\s+([A-Za-z_][\w]*)"),
            ("enum", r"\b(?:pub\s+)?enum\s+([A-Za-z_][\w]*)"),
            ("trait", r"\b(?:pub\s+)?trait\s+([A-Za-z_][\w]*)"),
            ("type", r"\b(?:pub\s+)?type\s+([A-Za-z_][\w]*)\s*="),
            ("module", r"\b(?:pub\s+)?mod\s+([A-Za-z_][\w]*)"),
        ),
        (r"\buse\s+([^;]+);", r"\bextern\s+crate\s+([A-Za-z_][\w]*)"),
        (r"\bimpl(?:<[^>]+>)?\s+([^\s{]+)\s+for\s+([^\s{]+)",),
        (r"\b([A-Za-z_][\w:]*)\s*!?\s*\(",),
        frozenset({"if", "for", "while", "match", "loop", "return", "Some", "Ok", "Err"}),
    ),
    RegexProfile(
        "go",
        (".go",),
        (
            ("function", r"\bfunc\s+(?:\([^)]*\)\s*)?([A-Za-z_][\w]*)\s*\("),
            ("type", r"\btype\s+([A-Za-z_][\w]*)\s+(?:struct|interface|=)"),
            ("variable", r"\bvar\s+([A-Za-z_][\w]*)\s+"),
        ),
        (r"(?m)^\s*import\s+(?:[A-Za-z_.]+\s+)?\"([^\"]+)\"", r"(?s)\bimport\s*\((.*?)\)"),
        (),
        keywords=frozenset({"if", "for", "switch", "select", "return", "go", "defer", "make", "new", "append", "len", "cap"}),
    ),
    RegexProfile(
        "java",
        (".java",),
        (
            ("class", r"\b(?:public|protected|private|abstract|final|sealed|non-sealed|static|\s)*\bclass\s+([A-Za-z_][\w]*)"),
            ("interface", r"\b(?:public|protected|private|abstract|sealed|static|\s)*\binterface\s+([A-Za-z_][\w]*)"),
            ("enum", r"\benum\s+([A-Za-z_][\w]*)"),
            ("record", r"\brecord\s+([A-Za-z_][\w]*)\s*\("),
            ("method", r"(?m)^\s*(?:@[\w.]+(?:\([^)]*\))?\s*)*(?:(?:public|protected|private|static|final|abstract|synchronized|native|default|strictfp)\s+)*(?:<[^>]+>\s+)?[\w$<>,.?\[\]\s]+\s+([A-Za-z_$][\w$]*)\s*\([^;{}]*\)\s*(?:throws[^\{]+)?\{"),
        ),
        (r"(?m)^\s*import\s+(?:static\s+)?([\w.*]+)\s*;", r"(?m)^\s*package\s+([\w.]+)\s*;"),
        (r"\bclass\s+\w+\s+extends\s+([\w$.]+)", r"\b(?:class|record|enum)\s+\w+[^\{]*\bimplements\s+([^\{]+)"),
        keywords=frozenset({"if", "for", "while", "switch", "catch", "return", "new", "super", "this", "synchronized"}),
    ),
    RegexProfile(
        "csharp",
        (".cs",),
        (
            ("class", r"\b(?:public|internal|private|protected|abstract|sealed|static|partial|\s)*\bclass\s+([A-Za-z_][\w]*)"),
            ("interface", r"\b(?:public|internal|private|protected|partial|\s)*\binterface\s+([A-Za-z_][\w]*)"),
            ("struct", r"\b(?:public|internal|private|protected|readonly|ref|partial|\s)*\bstruct\s+([A-Za-z_][\w]*)"),
            ("record", r"\b(?:public|internal|private|protected|sealed|abstract|partial|\s)*\brecord(?:\s+class|\s+struct)?\s+([A-Za-z_][\w]*)"),
            ("method", r"(?m)^\s*(?:(?:public|private|protected|internal|static|virtual|override|abstract|sealed|async|extern|unsafe|new|partial)\s+)+[\w<>,.?\[\]\s]+\s+([A-Za-z_][\w]*)\s*\([^;{}]*\)\s*(?:where[^\{]+)?\{"),
        ),
        (r"(?m)^\s*using\s+(?:static\s+)?([\w.]+)\s*;", r"(?m)^\s*namespace\s+([\w.]+)"),
        (r"\b(?:class|record|struct)\s+\w+\s*:\s*([^\{]+)",),
        keywords=frozenset({"if", "for", "foreach", "while", "switch", "catch", "return", "new", "typeof", "nameof", "lock", "using"}),
    ),
    RegexProfile(
        "cpp",
        (".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh"),
        (
            ("class", r"\bclass\s+([A-Za-z_][\w]*)"),
            ("struct", r"\bstruct\s+([A-Za-z_][\w]*)"),
            ("enum", r"\benum(?:\s+class)?\s+([A-Za-z_][\w]*)"),
            ("function", r"(?m)^\s*(?:template\s*<[^;{]+>\s*)?(?:(?:static|inline|virtual|constexpr|consteval|constinit|extern|friend|explicit|mutable|signed|unsigned|long|short)\s+)*[A-Za-z_][\w:<>,*&\s]*\s+([A-Za-z_~][\w:]*)\s*\([^;{}]*\)\s*(?:const\s*)?(?:noexcept(?:\([^)]*\))?\s*)?(?:->[^\{]+)?\{"),
        ),
        (r"(?m)^\s*#\s*include\s*[<\"]([^>\"]+)[>\"]", r"(?m)^\s*using\s+namespace\s+([\w:]+)"),
        (r"\bclass\s+\w+\s*:\s*(?:public|protected|private)?\s*([^\{]+)",),
        keywords=frozenset({"if", "for", "while", "switch", "catch", "return", "sizeof", "alignof", "decltype", "static_cast", "dynamic_cast"}),
    ),
    RegexProfile(
        "ruby",
        (".rb",),
        (
            ("class", r"(?m)^\s*class\s+([A-Za-z_][\w:]*)"),
            ("module", r"(?m)^\s*module\s+([A-Za-z_][\w:]*)"),
            ("method", r"(?m)^\s*def\s+(?:self\.)?([A-Za-z_][\w!?=]*)"),
        ),
        (r"(?m)^\s*require(?:_relative)?\s*[('\"]([^)'\"]+)",),
        (r"(?m)^\s*class\s+\w+\s*<\s*([A-Za-z_][\w:]*)",),
        keywords=frozenset({"if", "unless", "while", "until", "for", "case", "return", "yield", "super", "puts", "raise"}),
    ),
    RegexProfile(
        "php",
        (".php",),
        (
            ("class", r"\b(?:abstract\s+|final\s+|readonly\s+)?class\s+([A-Za-z_][\w]*)"),
            ("interface", r"\binterface\s+([A-Za-z_][\w]*)"),
            ("trait", r"\btrait\s+([A-Za-z_][\w]*)"),
            ("enum", r"\benum\s+([A-Za-z_][\w]*)"),
            ("function", r"\bfunction\s+&?\s*([A-Za-z_][\w]*)\s*\("),
        ),
        (r"\b(?:require|require_once|include|include_once)\s*\(?\s*['\"]([^'\"]+)", r"(?m)^\s*use\s+([^;]+);", r"(?m)^\s*namespace\s+([^;]+);"),
        (r"\bclass\s+\w+\s+extends\s+([A-Za-z_\\][\w\\]*)", r"\bclass\s+\w+[^\{]*\bimplements\s+([^\{]+)"),
        keywords=frozenset({"if", "for", "foreach", "while", "switch", "catch", "return", "echo", "isset", "empty", "array"}),
    ),
    RegexProfile(
        "lua",
        (".lua", ".luau"),
        (
            ("function", r"(?m)^\s*(?:local\s+)?function\s+([A-Za-z_][\w.:]*)\s*\("),
            ("function", r"(?m)^\s*local\s+([A-Za-z_][\w]*)\s*=\s*function\s*\("),
            ("type", r"(?m)^\s*(?:export\s+)?type\s+([A-Za-z_][\w]*)\s*="),
        ),
        (r"\brequire\s*\(\s*([^\)]+)\)",),
        (),
        (r"\b([A-Za-z_][\w.:]*)\s*\(",),
        frozenset({"if", "for", "while", "repeat", "until", "return", "function", "type", "typeof", "assert", "error"}),
    ),
)


class RegexLanguageParser:
    parser_id = "language-lexical-v3"

    def __init__(self, profile: RegexProfile):
        self.profile = profile
        self._definitions = tuple((kind, re.compile(pattern)) for kind, pattern in profile.definitions)
        self._imports = tuple(re.compile(pattern) for pattern in profile.imports)
        self._inheritance = tuple(re.compile(pattern) for pattern in profile.inheritance)
        self._calls = tuple(re.compile(pattern) for pattern in profile.calls)

    @staticmethod
    def line(text: str, offset: int) -> int:
        return text.count("\n", 0, offset) + 1

    def parse(self, path: str, text: str) -> ParseResult:
        symbols: list[ParsedSymbol] = []
        edges: list[ParsedEdge] = []
        definition_positions: list[tuple[int, str]] = []
        for kind, pattern in self._definitions:
            for match in pattern.finditer(text):
                name = match.group(1).strip()
                if not name or name in self.profile.keywords:
                    continue
                line = self.line(text, match.start())
                signature = match.group(0).strip().split("{", 1)[0][:300]
                qualified = name.replace("::", ".").replace(":", ".")
                symbols.append(ParsedSymbol(name.rsplit(".", 1)[-1], qualified, kind, line, line, signature, 0.9))
                definition_positions.append((match.start(), qualified))
                edges.append(ParsedEdge("<file>", "defines", qualified, line, 0.94))
        definition_positions.sort()

        def source_for(offset: int) -> str:
            source = "<file>"
            for position, candidate in definition_positions:
                if position > offset:
                    break
                source = candidate
            return source

        for pattern in self._imports:
            for match in pattern.finditer(text):
                target = match.group(1).strip() if match.lastindex else match.group(0).strip()
                if target:
                    edges.append(ParsedEdge(source_for(match.start()), "imports", target, self.line(text, match.start()), 0.87))
        for pattern in self._inheritance:
            for match in pattern.finditer(text):
                groups = [value.strip() for value in match.groups() if value and value.strip()]
                for group in groups:
                    for target in re.split(r"\s*,\s*|\s+", group):
                        target = target.strip("{}:()")
                        if target and target not in {"public", "private", "protected", "implements", "extends", "for"}:
                            edges.append(ParsedEdge(source_for(match.start()), "inherits", target, self.line(text, match.start()), 0.82))
        seen_calls: set[tuple[str, str, int]] = set()
        for pattern in self._calls:
            for match in pattern.finditer(text):
                target = match.group(1).strip()
                short = target.rsplit(".", 1)[-1].rsplit("::", 1)[-1].rsplit(":", 1)[-1]
                if short in self.profile.keywords or not short:
                    continue
                line = self.line(text, match.start())
                source = source_for(match.start())
                key = (source, target, line)
                if key in seen_calls:
                    continue
                seen_calls.add(key)
                edges.append(ParsedEdge(source, "calls", target, line, 0.72))
                if short != target:
                    edges.append(ParsedEdge(source, "calls-short", short, line, 0.66))
        return ParseResult(self.profile.language, f"{self.parser_id}:{self.profile.language}", tuple(symbols), tuple(edges), (), False)


class SemanticSnapshotParser:
    """Consumes optional LSP/compiler snapshots without requiring a live server.

    Snapshot format is deliberately small and host-neutral. Each repository may
    provide `.syntavra/semantic/<relative-path>.json` with `symbols` and
    `edges`; this permits language servers or compiler adapters to populate
    exact identities while the runtime retains deterministic offline fallback.
    """

    parser_id = "semantic-snapshot-v1"

    def __init__(self, repository_root: Path):
        self.repository_root = repository_root

    def snapshot_path(self, relative: str) -> Path:
        safe = relative.replace("\\", "/").replace("/", "__")
        return self.repository_root / ".syntavra" / "semantic" / f"{safe}.json"

    def load(self, relative: str) -> ParseResult | None:
        path = self.snapshot_path(relative)
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            symbols = tuple(ParsedSymbol(**row) for row in value.get("symbols", []))
            edges = tuple(ParsedEdge(**row) for row in value.get("edges", []))
            return ParseResult(str(value.get("language", "semantic")), self.parser_id, symbols, edges, (), True)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return ParseResult("semantic", self.parser_id, (), (), (f"snapshot-error:{type(exc).__name__}:{exc}",), True)


class ParserRegistry:
    def __init__(self, repository_root: Path):
        self.repository_root = repository_root
        self.semantic = SemanticSnapshotParser(repository_root)
        self.python = PythonParser()
        self.by_suffix: dict[str, LanguageParser] = {".py": self.python}
        for profile in PROFILES:
            parser = RegexLanguageParser(profile)
            for suffix in profile.suffixes:
                self.by_suffix[suffix] = parser

    @property
    def suffixes(self) -> frozenset[str]:
        return frozenset(self.by_suffix)

    def parse(self, relative: str, text: str) -> ParseResult:
        semantic = self.semantic.load(relative)
        if semantic and semantic.symbols:
            return semantic
        parser = self.by_suffix.get(Path(relative).suffix.casefold())
        if parser is None:
            return ParseResult("unknown", "unsupported", (), (), ("unsupported-language",), False)
        parsed = parser.parse(relative, text)
        if semantic and semantic.diagnostics:
            return ParseResult(parsed.language, parsed.parser, parsed.symbols, parsed.edges, (*semantic.diagnostics, *parsed.diagnostics), parsed.semantic)
        return parsed

    def capabilities(self) -> dict[str, Any]:
        languages: dict[str, list[str]] = {}
        for suffix, parser in sorted(self.by_suffix.items()):
            language = "python" if parser is self.python else getattr(parser, "profile").language
            languages.setdefault(language, []).append(suffix)
        return {
            "languages": languages,
            "semantic_snapshot": True,
            "tree_sitter_optional": False,
            "fallback": "language-specific-lexical",
        }


def parser_fixtures() -> Iterable[tuple[str, str, str]]:
    yield "main.py", "def target():\n    return 1\ndef caller():\n    return target()\n", "target"
    yield "main.ts", "export function target(){ return 1 }\nexport function caller(){ return target() }\n", "target"
    yield "main.rs", "pub fn target() -> i32 { 1 }\npub fn caller() -> i32 { target() }\n", "target"
    yield "main.go", "package main\nfunc target() int { return 1 }\nfunc caller() int { return target() }\n", "target"
    yield "Main.java", "class Main { static int target(){return 1;} static int caller(){return target();} }", "target"
    yield "Main.cs", "class Main { static int target(){return 1;} static int caller(){return target();} }", "target"
    yield "main.cpp", "int target(){return 1;} int caller(){return target();}", "target"
    yield "main.rb", "def target; 1; end\ndef caller; target(); end\n", "target"
    yield "main.php", "<?php function target(){return 1;} function caller(){return target();}", "target"
    yield "main.luau", "local function target() return 1 end\nlocal function caller() return target() end\n", "target"
