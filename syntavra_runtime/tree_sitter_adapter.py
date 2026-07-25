from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any

from .language_parsers import LANGUAGE_BY_SUFFIX, TreeSitterLanguageBackend
from .language_platform import LanguageParseResult


class TreeSitterLanguageAdapter:
    """Canonical syntax adapter for the built-in multi-language parser backend.

    Tree-sitter proves syntax structure, not cross-file semantic identity. The
    adapter therefore emits exact declaration/containment facts and explicitly
    candidate call/import edges until LSP, LSIF or SCIP evidence confirms them.
    """

    language_ids = tuple(sorted((set(LANGUAGE_BY_SUFFIX.values()) | {"csharp", "fsharp"}) - {"python"}))
    capabilities = frozenset({"syntax", "definitions", "references"})

    def __init__(self, backend: TreeSitterLanguageBackend | None = None) -> None:
        self.backend = backend or TreeSitterLanguageBackend()

    @property
    def installed(self) -> bool:
        return self.backend.installed

    def available_languages(self) -> tuple[str, ...]:
        if not self.installed:
            return ()
        return tuple(language for language in self.language_ids if self.backend.available(language))

    @staticmethod
    def _node_id(path: str, kind: str, name: str, line: int) -> str:
        payload = f"tree-sitter\0{path}\0{kind}\0{name}\0{line}".encode("utf-8")
        return "tree-sitter:" + hashlib.sha256(payload).hexdigest()

    def parse(self, *, path: str, text: str, evidence_ref: str) -> LanguageParseResult:
        language = next(
            (language for suffix, language in LANGUAGE_BY_SUFFIX.items() if path.casefold().endswith(suffix.casefold())),
            "unknown",
        )
        language = {"csharp": "c_sharp"}.get(language, language)
        declarations = self.backend.parse(text, language)
        if declarations is None:
            raise ValueError(f"tree-sitter parser unavailable for language: {language}")

        module_id = self._node_id(path, "module", path, 1)
        nodes: list[dict[str, Any]] = [
            {
                "node_id": module_id,
                "kind": "module",
                "name": path.rsplit("/", 1)[-1],
                "qualified_name": path,
                "start_line": 1,
                "end_line": max(1, len(text.splitlines())),
                "evidence_ref": evidence_ref,
                "metadata": {
                    "source": "tree-sitter",
                    "exact_semantic": False,
                    "exact_syntax": True,
                    "capability_level": "syntax",
                },
            }
        ]
        edges: list[dict[str, Any]] = []
        by_name: dict[str, list[str]] = defaultdict(list)
        declaration_rows: list[tuple[Any, str]] = []

        for declaration in declarations:
            node_id = self._node_id(path, declaration.kind, declaration.name, declaration.line)
            declaration_rows.append((declaration, node_id))
            by_name[declaration.name].append(node_id)
            nodes.append(
                {
                    "node_id": node_id,
                    "kind": declaration.kind,
                    "name": declaration.name,
                    "qualified_name": f"{path}:{declaration.name}",
                    "start_line": declaration.line,
                    "end_line": declaration.end_line,
                    "evidence_ref": evidence_ref,
                    "metadata": {
                        "source": "tree-sitter",
                        "exact_semantic": False,
                        "exact_syntax": True,
                        "capability_level": "syntax",
                    },
                }
            )
            edges.append(
                {
                    "source": module_id,
                    "target": node_id,
                    "edge_type": "defines",
                    "confidence": 1.0,
                    "evidence_ref": evidence_ref,
                    "metadata": {
                        "source": "tree-sitter",
                        "exact_semantic": False,
                        "exact_syntax": True,
                    },
                }
            )

        for declaration, source_id in declaration_rows:
            for call in declaration.calls:
                short = call.replace("::", ".").rsplit(".", 1)[-1]
                targets = by_name.get(call) or by_name.get(short) or []
                for target_id in targets:
                    if target_id == source_id:
                        continue
                    edges.append(
                        {
                            "source": source_id,
                            "target": target_id,
                            "edge_type": "calls",
                            "confidence": 0.72,
                            "evidence_ref": evidence_ref,
                            "metadata": {
                                "source": "tree-sitter",
                                "exact_semantic": False,
                                "resolution": "same-file-candidate",
                            },
                        }
                    )
            for imported in declaration.imports:
                normalized = " ".join(imported.split())[:500]
                if not normalized:
                    continue
                edges.append(
                    {
                        "source": module_id,
                        "target": f"external:{normalized}",
                        "edge_type": "imports-candidate",
                        "confidence": 0.55,
                        "evidence_ref": evidence_ref,
                        "metadata": {
                            "source": "tree-sitter",
                            "exact_semantic": False,
                        },
                    }
                )
            for base in declaration.bases:
                short = base.replace("::", ".").rsplit(".", 1)[-1]
                for target_id in by_name.get(base) or by_name.get(short) or []:
                    if target_id == source_id:
                        continue
                    edges.append(
                        {
                            "source": source_id,
                            "target": target_id,
                            "edge_type": "inherits",
                            "confidence": 0.78,
                            "evidence_ref": evidence_ref,
                            "metadata": {
                                "source": "tree-sitter",
                                "exact_semantic": False,
                                "resolution": "same-file-candidate",
                            },
                        }
                    )

        return LanguageParseResult(
            nodes=tuple(nodes),
            edges=tuple(edges),
            capability_level="syntax",
            evidence_source="tree-sitter-language-pack",
            diagnostics=(),
        )

    def manifest(self) -> dict[str, Any]:
        return {
            "adapter": "tree-sitter-language-pack",
            "installed": self.installed,
            "available_languages": list(self.available_languages()),
            "capability_level": "syntax",
            "claim_boundary": "cross-file semantic identity requires LSP, LSIF or SCIP confirmation",
        }


__all__ = ["TreeSitterLanguageAdapter"]
