from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterable


LANGUAGE_BY_SUFFIX: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".cs": "c_sharp",
    ".rb": "ruby",
    ".php": "php",
    ".lua": "lua",
    ".luau": "lua",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".swift": "swift",
    ".scala": "scala",
    ".sc": "scala",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hrl": "erlang",
    ".hs": "haskell",
    ".lhs": "haskell",
    ".ml": "ocaml",
    ".mli": "ocaml",
    ".fs": "fsharp",
    ".fsx": "fsharp",
    ".dart": "dart",
    ".r": "r",
    ".R": "r",
    ".jl": "julia",
    ".sol": "solidity",
    ".zig": "zig",
    ".vue": "vue",
    ".svelte": "svelte",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".fish": "fish",
    ".ps1": "powershell",
}


@dataclass(frozen=True)
class ParsedDeclaration:
    name: str
    kind: str
    line: int
    end_line: int
    calls: tuple[str, ...] = ()
    imports: tuple[str, ...] = ()
    bases: tuple[str, ...] = ()


_DEFINITION_TYPES = {
    "function_definition": "function",
    "function_declaration": "function",
    "method_definition": "method",
    "method_declaration": "method",
    "function_item": "function",
    "function_signature_item": "function",
    "class_definition": "class",
    "class_declaration": "class",
    "class_specifier": "class",
    "interface_declaration": "interface",
    "trait_item": "trait",
    "struct_item": "struct",
    "struct_specifier": "struct",
    "enum_item": "enum",
    "enum_declaration": "enum",
    "module": "module",
    "module_declaration": "module",
}
_CALL_TYPES = {"call", "call_expression", "invocation_expression", "method_invocation"}
_IMPORT_TYPES = {
    "import_statement",
    "import_declaration",
    "import_from_statement",
    "use_declaration",
    "using_directive",
    "require_call",
}
_NAME_TYPES = {"identifier", "type_identifier", "property_identifier", "field_identifier", "constant"}


class TreeSitterLanguageBackend:
    """Optional exact parser backend.

    The general language surface is provided by ``tree-sitter-language-pack``.
    Grammars that must remain available during offline certification can also
    be packaged directly; C# is currently pinned to that local path so the
    reference suite never depends on a first-use grammar download.
    """

    def __init__(self) -> None:
        try:
            from tree_sitter_language_pack import get_parser  # type: ignore
        except ImportError:
            get_parser = None
        try:
            import tree_sitter_c_sharp  # type: ignore
            from tree_sitter import Language, Parser  # type: ignore
        except ImportError:
            tree_sitter_c_sharp = None
            Language = None
            Parser = None
        self._get_parser = get_parser
        self._csharp_binding = tree_sitter_c_sharp
        self._tree_sitter_language = Language
        self._tree_sitter_parser = Parser

    @property
    def installed(self) -> bool:
        return self._get_parser is not None or self._csharp_binding is not None

    def _packaged_parser(self, language: str):
        if language not in {"c_sharp", "csharp"}:
            return None
        if self._csharp_binding is None:
            return None
        if self._tree_sitter_language is None or self._tree_sitter_parser is None:
            return None
        try:
            grammar = self._tree_sitter_language(self._csharp_binding.language())
            return self._tree_sitter_parser(grammar)
        except Exception:
            return None

    @lru_cache(maxsize=128)
    def _parser(self, language: str):
        packaged = self._packaged_parser(language)
        if packaged is not None:
            return packaged
        if self._get_parser is None:
            return None
        try:
            return self._get_parser(language)
        except Exception:
            return None

    def available(self, language: str) -> bool:
        return self._parser(language) is not None

    @staticmethod
    def _text(node: Any, source: bytes) -> str:
        return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def _name(self, node: Any, source: bytes) -> str:
        named = node.child_by_field_name("name") if hasattr(node, "child_by_field_name") else None
        if named is not None:
            return self._text(named, source).strip()
        stack = list(getattr(node, "named_children", ()))
        while stack:
            child = stack.pop(0)
            if getattr(child, "type", "") in _NAME_TYPES:
                return self._text(child, source).strip()
            stack[0:0] = list(getattr(child, "named_children", ()))
        return ""

    def parse(self, source_text: str, language: str) -> list[ParsedDeclaration] | None:
        parser = self._parser(language)
        if parser is None:
            return None
        source = source_text.encode("utf-8")
        try:
            tree = parser.parse(source)
        except Exception:
            return None
        root = tree.root_node
        declarations: list[ParsedDeclaration] = []
        global_imports: set[str] = set()

        def descendants(node: Any) -> Iterable[Any]:
            stack = list(getattr(node, "named_children", ()))
            while stack:
                child = stack.pop()
                yield child
                stack.extend(getattr(child, "named_children", ()))

        for node in descendants(root):
            node_type = getattr(node, "type", "")
            if node_type in _IMPORT_TYPES:
                value = self._text(node, source).strip()
                if value:
                    global_imports.add(value[:500])

        for node in descendants(root):
            node_type = getattr(node, "type", "")
            kind = _DEFINITION_TYPES.get(node_type)
            if kind is None:
                continue
            name = self._name(node, source)
            if not name:
                continue
            calls: set[str] = set()
            bases: set[str] = set()
            for child in descendants(node):
                child_type = getattr(child, "type", "")
                if child_type in _CALL_TYPES:
                    called = child.child_by_field_name("function") if hasattr(child, "child_by_field_name") else None
                    called_name = self._name(called or child, source)
                    if called_name and called_name != name:
                        calls.add(called_name)
                if child_type in {"superclass", "extends_clause", "implements_clause", "trait_bounds"}:
                    value = self._text(child, source).strip()
                    if value:
                        bases.add(value[:300])
            declarations.append(
                ParsedDeclaration(
                    name=name,
                    kind=kind,
                    line=int(node.start_point[0]) + 1,
                    end_line=int(node.end_point[0]) + 1,
                    calls=tuple(sorted(calls)),
                    imports=tuple(sorted(global_imports)),
                    bases=tuple(sorted(bases)),
                )
            )
        return declarations

    def manifest(self) -> dict[str, object]:
        languages = sorted(set(LANGUAGE_BY_SUFFIX.values()))
        available = [language for language in languages if self.available(language)] if self.installed else []
        return {
            "backend": "tree-sitter-language-pack",
            "installed": self.installed,
            "declared_languages": languages,
            "declared_language_count": len(languages),
            "available_languages": available,
            "available_language_count": len(available),
            "fallback": "deterministic-lexical",
        }